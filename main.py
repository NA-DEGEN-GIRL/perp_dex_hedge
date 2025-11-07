import os
import asyncio
import configparser
import logging
import textwrap  # 줄바꿈용

import ccxt.async_support as ccxt
from dotenv import load_dotenv
from textual.message import Message
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Button, Footer, Header, Input, Label, Static, RadioSet, RadioButton, Log
)
from textual.reactive import reactive

# --- 설정 로드 ---
load_dotenv()
config = configparser.ConfigParser()
config.read("config.ini")

EXCHANGES = sorted([section for section in config.sections()])

# --- CCXT 거래소 인스턴스 생성 ---
class ExchangeManager:
    def __init__(self):
        self.exchanges = {}
        for exchange_name in EXCHANGES:
            builder_code = config.get(exchange_name, "builder_code", fallback=None)
            wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")
            if not builder_code or not wallet_address:
                self.exchanges[exchange_name] = None
                continue
            self.exchanges[exchange_name] = ccxt.hyperliquid(
                {
                    "apiKey": os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY"),
                    # 환경과 ccxt 구현에 맞춰 privateKey 사용
                    "privateKey": os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY"),
                    "walletAddress": wallet_address,
                    "options": {
                        "builder": builder_code,
                        "feeRate": config.get(exchange_name, "fee_rate", fallback="") + "%",  # 문자열 퍼센트
                    },
                }
            )

    async def close_all(self):
        tasks = [ex.close() for ex in self.exchanges.values() if ex]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_exchange(self, name: str):
        return self.exchanges.get(name)

# --- 메시지 ---
class InfoUpdate(Message):
    def __init__(self, exchange_name: str, collateral: str, position: str, collateral_val: float) -> None:
        self.exchange_name = exchange_name
        self.collateral_info = collateral
        self.position_info = position
        self.collateral_value = collateral_val
        super().__init__()

# --- 거래소 UI 위젯 ---
class ExchangeControl(Container):
    def __init__(self, exchange_name: str, manager: ExchangeManager, **kwargs) -> None:
        super().__init__(**kwargs)
        self.exchange_name = exchange_name
        self.manager = manager
        self.exchange = self.manager.get_exchange(self.exchange_name)

    def compose(self) -> ComposeResult:
        is_configured = self.exchange is not None
        with Vertical(classes="exchange-box"):
            yield Label(f"[b]{self.exchange_name.upper()}[/b]", classes="exchange-header")

            if not is_configured:
                yield Static("해당 거래소 설정이 없음", classes="error-text")
                return

            # 한 줄: Q/P + 주문타입 + 버튼들
            with Horizontal(classes="row-compact"):
                yield Label("Q:", classes="tiny-label")
                yield Input(id=f"qty_{self.exchange_name}", classes="ipt-qty")
                yield Label("P:", classes="tiny-label")
                yield Input(id=f"price_{self.exchange_name}", classes="ipt-price", disabled=True)

                with RadioSet(id=f"type_{self.exchange_name}", classes="radio-inline"):
                    yield RadioButton("Mkt", value=True)
                    yield RadioButton("Lmt")

                yield Button("L", variant="success", id=f"long_{self.exchange_name}", classes="btn-mini")
                yield Button("S", variant="error", id=f"short_{self.exchange_name}", classes="btn-mini")
                yield Button("EX", variant="primary", id=f"exec_{self.exchange_name}", classes="btn-mini exec")
                # 비활성 버튼
                yield Button("OFF", variant="warning", id=f"disable_{self.exchange_name}", classes="btn-off")

            yield Static("📊 Position: N/A", id=f"pos_{self.exchange_name}", classes="info-line")
            yield Static("💰 Collateral: N/A", id=f"col_{self.exchange_name}", classes="info-line")

    async def on_mount(self) -> None:
        if self.exchange:
            self.set_interval(1, self.update_info)
            await self.update_info()

    async def update_info(self) -> None:
        if not self.exchange:
            return
        symbol = self.app.symbol
        try:
            balance = await self.exchange.fetch_balance()
            total_collateral = balance.get("USDC", {}).get("total", 0)
            collateral_info_str = f"💰 Collateral: {total_collateral:,.2f} USDC"

            positions = await self.exchange.fetch_positions([f"{symbol}/USDC:USDC"])
            position_info_str = "📊 Position: N/A"
            if positions and positions[0]:
                p = positions[0]
                size = 0.0
                try:
                    size = float(p.get("contracts", 0) or 0)
                except Exception:
                    size = 0.0
                if size != 0:
                    side = "LONG" if p.get("side") == "long" else "SHORT"
                    pnl = 0.0
                    try:
                        pnl = float(p.get("unrealizedPnl", 0) or 0)
                    except Exception:
                        pnl = 0.0
                    side_color = "green" if side == "LONG" else "red"
                    pnl_color = "green" if pnl >= 0 else "red"
                    position_info_str = f"📊 [{side_color}]{side}[/] {size:.5f} | PnL: [{pnl_color}]{pnl:,.2f}[/]"

            self.post_message(
                InfoUpdate(
                    exchange_name=self.exchange_name,
                    collateral=collateral_info_str,
                    position=position_info_str,
                    collateral_val=total_collateral,
                )
            )
        except Exception:
            logging.error(f"[{self.exchange_name.upper()}] UPDATE_INFO ERROR", exc_info=True)
            self.post_message(
                InfoUpdate(
                    exchange_name=self.exchange_name,
                    collateral="💰 Collateral: Error",
                    position="📊 Position: Error",
                    collateral_val=0,
                )
            )

# --- 메인 앱 ---
class TradingApp(App):
    CSS = """
    Screen { 
        layout: vertical; 
        overflow-y: hidden;          /* 화면 자체는 세로 고정 */
        overflow-x: auto;            /* 가로 스크롤 허용(압축 방지) */
    }

    #main-controls {
        height: auto;
        padding: 0 1;
        border: round $primary;
        background: $panel;
        margin-bottom: 1;
        overflow-x: auto;            /* 헤더 가로 스크롤 허용 */
    }
    .hdr-row { 
        height: auto; 
        align: left middle;          /* 수직 중앙 정렬 */
        content-align: left middle;
    }
    .hdr-row > * { margin-right: 1; }
    .hdr-gap { margin-bottom: 1; }  /* 첫 번째 헤더 행 아래에 1칸 여백 */

    #symbol-input { width: 10; min-height: 3;}     /* BTC 기본값 보이도록 폭 고정 */
    #all-qty-input { width: 13; min-height: 3;}    /* All Qty 축소 */
    #current-price-display { height: 3; width: 18; content-align: left middle;}
    #total-collateral-display { height: 3; width: 22; content-align: left middle;}
    #exec-all { width: 12; height: 3; content-align: left middle;}
    #quit-button { width: 5; height: 3; content-align: left middle;}

    #body-scroll {
        overflow-y: auto;              /* 세로 스크롤: 창을 줄여도 내용이 스크롤됨 */
        overflow-x: auto;              /* 가로 스크롤: 폭이 부족할 때 찌그러짐 방지 */
        padding: 0 1;
    }

    #exchanges-container { height: auto; }

    .exchange-box {
        height: auto;
        border: round $panel;
        padding: 0 0;
        margin: 0 0 0 0;
    }
    .exchange-header { 
        min-height: 1;
        height: 1; 
        color: $primary; 
        content-align: left middle; 
        padding: 0;
        margin: 0;
    }
    
    .btn-off { min-height: 3; height: 3; min-width: 6; content-align: center middle; }

    .row-compact {
        height: auto;
        align: left middle;
        content-align: left middle;
        padding: 0;
        margin: 0 0 0 0;
        overflow-x: auto;            /* 가로 스크롤(버튼/라디오 압축 방지) */
    }

    .tiny-label { min-height: 3; content-align: right middle;}
    .ipt-qty { width: 13; }
    .ipt-price { width: 14; }

    .radio-inline { layout: horizontal; width: auto; }
    .radio-inline RadioButton { width: 7; padding: 0; margin: 0; content-align: center middle; }

    .btn-mini { min-height: 3; height: 3; min-width: 8; margin-left: 1; content-align: center middle; }  /* 더 좁게 */
    .btn-mini.exec { min-height: 3; height: 3; min-width: 8;  }

    .info-line { margin: 0 0 0 0; color: $text-muted; content-align: left middle;}
    .error-text { color: $error; }

    #log { 
        height: 8; 
        border: round $primary; 
        margin: 0 1; 
        overflow-x: hidden;          /* 가로 스크롤 숨김: 줄바꿈으로 해결 */
    }
    """

    symbol = reactive("BTC")
    current_price = reactive("...")
    total_collateral = reactive(0.0)

    def __init__(self, manager: ExchangeManager):
        super().__init__()
        self.manager = manager
        self._collateral_by_exchange = {name: 0 for name in EXCHANGES}
        self.exchange_enabled = {name: False for name in EXCHANGES}

    def compose(self) -> ComposeResult:
        yield Header(name="Hyperliquid Multi-DEX Trader")

        with Container(id="main-controls"):
            with Horizontal(classes="hdr-row hdr-gap"):
                yield Label("Ticker:", classes='tiny-label')
                yield Input(value="BTC", placeholder="BTC", id="symbol-input")
                yield Static(id="current-price-display")
                yield Static(id="total-collateral-display")

            with Horizontal(classes="hdr-row"):
                yield Label("All Qty:", classes='tiny-label')
                yield Input(id="all-qty-input")
                yield Button("EXECUTE ALL", variant="warning", id="exec-all")
                yield Button("종료", variant="error", id="quit-button")

        with Container(id="body-scroll"):  # 추가: 바디 전체 스크롤 래퍼
            with ScrollableContainer(id="exchanges-container"):
                for name in EXCHANGES:
                    yield ExchangeControl(name, self.manager, id=name)

        yield Log(id="log", highlight=True)
        yield Footer()

    # Log 줄바꿈 유틸 (가로 스크롤 없이 출력)
    def log_write(self, msg: str) -> None:
        try:
            log = self.query_one("#log", Log)
            w = log.size.width or 80
            # 테두리/여백 감안
            wrap_width = max(20, w - 4)
            for line in textwrap.wrap(msg, width=wrap_width, replace_whitespace=False, drop_whitespace=False):
                log.write_line(line)
        except Exception:
            # Log 준비 전일 수 있으므로 콘솔에도 남김
            print(msg)

    def on_info_update(self, message: InfoUpdate) -> None:
        try:
            pos = self.query_one(f"#{message.exchange_name} #pos_{message.exchange_name}", Static)
            col = self.query_one(f"#{message.exchange_name} #col_{message.exchange_name}", Static)
            pos.update(message.position_info)
            col.update(message.collateral_info)
            self.update_total_collateral(message.exchange_name, message.collateral_value)
        except Exception:
            logging.error(f"UI update failed for {message.exchange_name}", exc_info=True)

    async def on_mount(self) -> None:
        self.set_interval(1, self.update_current_price)
        await self.update_all_exchange_info()

    def watch_current_price(self, price: str) -> None:
        self.query_one("#current-price-display").update(f"📈 [b yellow]{price}[/b yellow]")

    def watch_total_collateral(self, total: float) -> None:
        self.query_one("#total-collateral-display").update(f"💵 [b green]Total: {total:,.2f} USDC[/b green]")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "symbol-input":
            if event.value:
                self.symbol = event.value.upper()
        elif event.input.id == "all-qty-input":
            for name in EXCHANGES:
                try:
                    self.query_one(f"#{name} #qty_{name}", Input).value = event.value
                except Exception:
                    pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        # [추가] OFF 토글
        if bid.startswith("disable_"):
            ex_name = bid.split("_", 1)[1]
            self.exchange_enabled[ex_name] = False  # 명시적 비활성
            # Long/Short 시각 상태 해제
            try:
                long_b = self.query_one(f"#{ex_name} #long_{ex_name}", Button)
                short_b = self.query_one(f"#{ex_name} #short_{ex_name}", Button)
                long_b.variant, short_b.variant = "default", "default"
            except Exception:
                pass
            # OFF 버튼은 경고색 유지
            try:
                off_b = self.query_one(f"#{ex_name} #disable_{ex_name}", Button)
                off_b.variant = "warning"
            except Exception:
                pass
            self.log_write(f"[{ex_name.upper()}] 비활성화됨 (EXECUTE ALL 대상 제외)")
            return

        # Long/Short 누르면 자동 활성화
        if bid.startswith(("long_", "short_")):
            action, ex_name = bid.split("_", 1)
            try:
                long_b = self.query_one(f"#{ex_name} #long_{ex_name}", Button)
                short_b = self.query_one(f"#{ex_name} #short_{ex_name}", Button)
                if action == "long":
                    long_b.variant, short_b.variant = "success", "default"
                else:
                    long_b.variant, short_b.variant = "default", "error"
                # [추가] 자동 활성화
                self.exchange_enabled[ex_name] = True
                # OFF 버튼은 일반색(활성 표시)
                off_b = self.query_one(f"#{ex_name} #disable_{ex_name}", Button)
                off_b.variant = "default"
            except Exception:
                pass
            return

        if bid.startswith("exec_"):
            await self.execute_order(bid.split("_", 1)[1])
            return

        if bid == "exec-all":
            await self.execute_all_orders()
            return

        if bid == "quit-button":
            await self.action_quit()
            return

    # 라디오 변경: pressed_index만 사용(버전 호환)
    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id.startswith("type_"):
            ex_name = event.radio_set.id.split("_", 1)[1]
            try:
                idx = getattr(event, "pressed_index", None)
                if idx is None:
                    idx = getattr(event, "index", None)
                if idx is None:
                    idx = event.radio_set.pressed_index
                price_input = self.query_one(f"#{ex_name} #price_{ex_name}", Input)
                price_input.disabled = (idx == 0)  # 0=Mkt, 1=Lmt
            except Exception:
                pass

    async def execute_order(self, exchange_name: str) -> None:
        exchange = self.manager.get_exchange(exchange_name)
        if not exchange:
            self.log_write(f"[{exchange_name.upper()}] 주문 불가: 설정 없음")
            return

        try:
            qty_input = self.query_one(f"#{exchange_name} #qty_{exchange_name}", Input)
            type_set = self.query_one(f"#{exchange_name} #type_{exchange_name}", RadioSet)
            price_input = self.query_one(f"#{exchange_name} #price_{exchange_name}", Input)
            side = self.get_selected_side(exchange_name)

            if not qty_input.value:
                self.log_write(f"[{exchange_name.upper()}] 수량을 입력하세요.")
                return

            amount = float(qty_input.value)
            order_type = "market" if (type_set.pressed_index in (None, 0)) else "limit"
            price = float(price_input.value) if (order_type == "limit" and price_input.value) else float(self.current_price.replace(",",""))

            if not side:
                self.log_write(f"[{exchange_name.upper()}] LONG/SHORT 선택을 하세요.")
                return

            self.log_write(f"[{exchange_name.upper()}] {side.upper()} {amount} {self.symbol} @ {order_type}")
            
            order = await exchange.create_order(
                symbol=f"{self.symbol}/USDC:USDC",
                type=order_type,
                side=side,  # 'buy' or 'sell'
                amount=amount,
                price=price,
            )
            self.log_write(f"[{exchange_name.upper()}] 주문 성공: #{order['id']}")
            await self.query_one(f"#{exchange_name}", ExchangeControl).update_info()

        except Exception as e:
            self.log_write(f"[{exchange_name.upper()}] 주문 실패: {e}")
            logging.error(f"[{exchange_name}] Order execution error", exc_info=True)

    async def execute_all_orders(self):
        self.log_write("[ALL] 모든 거래소 동시 주문 실행...")
        tasks = []

        for name in EXCHANGES:
            # 설정/연결 확인
            if not self.manager.get_exchange(name):
                continue
            # [추가] 활성 상태 확인
            if not self.exchange_enabled.get(name, False):
                self.log_write(f"[ALL] {name.upper()} 건너뜀: 비활성")
                continue
            # [추가] 방향 선택 확인
            side = self.get_selected_side(name)
            if not side:
                self.log_write(f"[ALL] {name.upper()} 건너뜀: LONG/SHORT 미선택")
                continue

            tasks.append(self.execute_order(name))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            self.log_write("[ALL] 실행할 거래소가 없습니다. (모두 비활성 또는 미선택)")

    async def update_current_price(self):
        repr_ex = next((ex for ex in self.manager.exchanges.values() if ex), None)
        if not repr_ex:
            self.current_price = "N/A"
            return
        try:
            t = await repr_ex.fetch_ticker(f"{self.symbol}/USDC:USDC")
            self.current_price = f"{t['last']:,.2f}"
        except Exception:
            self.current_price = "Error"
            logging.error("Price fetch error", exc_info=True)

    async def update_all_exchange_info(self):
        tasks = [c.update_info() for c in self.query(ExchangeControl) if c.exchange]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def update_total_collateral(self, exchange_name: str, value: float):
        self._collateral_by_exchange[exchange_name] = value
        self.total_collateral = sum(self._collateral_by_exchange.values())

    def get_selected_side(self, exchange_name: str) -> str | None:
        try:
            if self.query_one(f"#{exchange_name} #long_{exchange_name}", Button).variant == "success":
                return "buy"
            if self.query_one(f"#{exchange_name} #short_{exchange_name}", Button).variant == "error":
                return "sell"
        except Exception:
            pass
        return None

    async def on_quit(self) -> None:
        await self.manager.close_all()

    async def action_quit(self) -> None:
        await self.on_quit()
        self.exit()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename="debug.log",
        filemode="w",
    )
    logging.info("Application starting...")
    try:
        app = TradingApp(manager=ExchangeManager())
        app.run()
    except Exception:
        logging.critical("CRITICAL APP ERROR", exc_info=True)
    finally:
        logging.info("Application finished.")