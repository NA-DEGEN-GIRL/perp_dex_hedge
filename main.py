# main.py - UI 완전 재설계 버전

import os
import asyncio
import configparser
from decimal import Decimal
import logging

import ccxt.async_support as ccxt
from dotenv import load_dotenv
from textual.message import Message
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Button, Footer, Header, Input, Label, Log, Static, RadioSet, RadioButton
)
from textual.reactive import reactive

# --- 설정 로드 ---
load_dotenv()
config = configparser.ConfigParser()
config.read('config.ini')

EXCHANGES = sorted([section for section in config.sections()])

# --- CCXT 거래소 인스턴스 생성 관리 ---
class ExchangeManager:
    def __init__(self):
        self.exchanges = {}
        for exchange_name in EXCHANGES:
            builder_code = config.get(exchange_name, 'builder_code', fallback=None)
            wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")
            
            if not builder_code or not wallet_address:
                self.exchanges[exchange_name] = None
                continue
            
            self.exchanges[exchange_name] = ccxt.hyperliquid({
                'apiKey': os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY"),
                'privateKey': os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY"),
                # no need secret
                'walletAddress': wallet_address,
                'options': {
                    'builder': builder_code,
                    'feeRate': config.get(exchange_name, 'fee_rate', fallback='')+"%",
                }
            })

    async def close_all(self):
        tasks = [
            ex.close() for ex in self.exchanges.values() if ex
        ]
        if tasks:
            await asyncio.gather(*tasks)

    def get_exchange(self, name: str):
        return self.exchanges.get(name)

# --- 메시지 클래스 ---
class InfoUpdate(Message):
    def __init__(self, exchange_name: str, collateral: str, position: str, collateral_val: float) -> None:
        self.exchange_name = exchange_name
        self.collateral_info = collateral
        self.position_info = position
        self.collateral_value = collateral_val
        super().__init__()

# --- 개별 거래소 UI 위젯 ---
class ExchangeControl(Container):
    def __init__(self, exchange_name: str, manager: ExchangeManager, **kwargs) -> None:
        super().__init__(**kwargs)
        self.exchange_name = exchange_name
        self.manager = manager
        self.exchange = self.manager.get_exchange(self.exchange_name)

    def compose(self) -> ComposeResult:
        is_configured = self.exchange is not None
        
        with Vertical(classes="exchange-box"):
            # 거래소 이름 헤더
            yield Label(f"━━━ {self.exchange_name.upper()} ━━━", classes="exchange-header")
            
            if not is_configured:
                yield Label("설정 없음", classes="error-text")
                return
            
            # 첫번째 줄: 수량과 주문 타입
            with Horizontal(classes="compact-row"):
                yield Label("Qty:", classes="small-label")
                yield Input(placeholder="0.001", id=f"qty_{self.exchange_name}", classes="small-input")
                yield RadioSet(
                    RadioButton("Market", value=True),
                    RadioButton("Limit"),
                    id=f"type_{self.exchange_name}",
                    classes="order-type"
                )
            
            # 두번째 줄: 가격 입력 (Limit 주문용)
            with Horizontal(classes="compact-row"):
                yield Label("Price:", classes="small-label")
                yield Input(placeholder="100000", id=f"price_{self.exchange_name}", classes="small-input", disabled=True)
            
            # 세번째 줄: 방향 선택 및 실행 버튼
            with Horizontal(classes="button-row"):
                yield Button("LONG", variant="success", id=f"long_{self.exchange_name}", classes="direction-btn")
                yield Button("SHORT", variant="error", id=f"short_{self.exchange_name}", classes="direction-btn")
                yield Button("실행", variant="primary", id=f"exec_{self.exchange_name}", classes="exec-btn")
            
            # 포지션 및 담보 정보
            yield Static("Position: Loading...", id=f"pos_{self.exchange_name}", classes="info-line")
            yield Static("Collateral: Loading...", id=f"col_{self.exchange_name}", classes="info-line")

    async def on_mount(self) -> None:
        if self.exchange:
            self.set_interval(10, self.update_info)
            await self.update_info()

    async def update_info(self) -> None:
        if not self.exchange:
            return
            
        symbol = self.app.symbol
        try:
            balance = await self.exchange.fetch_balance()
            total_collateral = balance.get('USDC', {}).get('total', 0)
            collateral_info_str = f"💰 Collateral: {total_collateral:,.2f} USDC"
            
            positions = await self.exchange.fetch_positions([f"{symbol}/USDC:USDC"])
            
            position_info_str = "📊 Position: No position"
            if positions and len(positions) > 0:
                position = positions[0]
                contracts_val = 0.0
                try: 
                    contracts_val = float(position.get('contracts', 0))
                except (ValueError, TypeError): 
                    pass
                
                if contracts_val != 0:
                    side = "LONG" if position.get('side') == 'long' else "SHORT"
                    pnl_val = 0.0
                    try: 
                        pnl_val = float(position.get('unrealizedPnl', 0))
                    except (ValueError, TypeError): 
                        pass
                    
                    side_emoji = "🟢" if side == "LONG" else "🔴"
                    pnl_emoji = "✅" if pnl_val >= 0 else "❌"
                    position_info_str = f"📊 {side_emoji} {side} {contracts_val:.5f} | PnL: {pnl_emoji} {pnl_val:,.2f}"

            self.post_message(InfoUpdate(
                exchange_name=self.exchange_name,
                collateral=collateral_info_str,
                position=position_info_str,
                collateral_val=total_collateral
            ))
            
            logging.info(f"[{self.exchange_name.upper()}] Updated - {position_info_str}")

        except Exception as e:
            self.post_message(InfoUpdate(
                exchange_name=self.exchange_name,
                collateral="💰 Collateral: Error",
                position="📊 Position: Error",
                collateral_val=0
            ))
            logging.error(f"[{self.exchange_name.upper()}] UPDATE_INFO ERROR: {e}", exc_info=True)

# --- 메인 TUI 앱 ---
class TradingApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    
    /* 메인 컨트롤 영역 */
    #main-header {
        height: 7;
        background: $boost;
        padding: 0 1;
        margin: 0;
    }
    
    .header-row {
        height: 2;
        align: center middle;
        margin: 0;
    }
    
    .ticker-label {
        width: 8;
        content-align: right middle;
        margin-right: 1;
    }
    
    #symbol-input {
        width: 10;
        margin-right: 2;
    }
    
    #all-qty-input {
        width: 15;
        margin-right: 2;
    }
    
    .price-display {
        color: $warning;
        text-style: bold;
    }
    
    .total-display {
        color: $success;
        text-style: bold;
    }
    
    /* 거래소 컨테이너 */
    #exchanges-scroll {
        height: 100%;
        margin: 0 1;
    }
    
    .exchange-box {
        border: solid $panel;
        padding: 0 1;
        margin-bottom: 1;
        height: 9;
        background: $panel;
    }
    
    .exchange-header {
        text-align: center;
        color: $primary;
        text-style: bold;
        margin: 0;
        height: 1;
    }
    
    .compact-row {
        height: 1;
        margin: 0;
        align: left middle;
    }
    
    .button-row {
        height: 3;
        margin: 0;
        align: center middle;
    }
    
    .small-label {
        width: 6;
        content-align: right middle;
        margin-right: 1;
    }
    
    .small-input {
        width: 12;
        height: 1;
    }
    
    .order-type {
        width: 20;
        height: 1;
        layout: horizontal;
        margin-left: 1;
    }
    
    .direction-btn {
        width: 8;
        margin: 0 1;
    }
    
    .exec-btn {
        width: 6;
    }
    
    RadioButton {
        width: 10;
        height: 1;
        padding: 0;
        margin: 0;
    }
    
    .info-line {
        height: 1;
        margin: 0;
        color: $text-muted;
    }
    
    .error-text {
        color: $error;
        text-align: center;
    }
    
    /* 로그 영역 */
    #log {
        height: 10;
        border: solid $primary;
        margin: 0 1 1 1;
    }
    
    /* 버튼 스타일 */
    Button {
        height: 3;
    }
    
    #exec-all {
        margin-right: 1;
    }
    """

    symbol = reactive("BTC")
    current_price = reactive("Loading...")
    total_collateral = reactive(0.0)

    def __init__(self, manager: ExchangeManager):
        super().__init__()
        self.manager = manager
        self._collateral_by_exchange = {name: 0 for name in EXCHANGES}

    def compose(self) -> ComposeResult:
        yield Header(name="🚀 Hyperliquid Multi-DEX Trader")
        
        # 컴팩트한 상단 컨트롤
        with Container(id="main-header"):
            # 첫번째 줄: 티커와 현재가
            with Horizontal(classes="header-row"):
                yield Label("Ticker:", classes="ticker-label")
                yield Input(value="BTC", id="symbol-input")
                yield Static("", id="current-price-display", classes="price-display")
                yield Static("", id="total-collateral-display", classes="total-display")
            
            # 두번째 줄: 전체 수량 설정과 버튼들
            with Horizontal(classes="header-row"):
                yield Label("All Qty:", classes="ticker-label")
                yield Input(placeholder="0.001", id="all-qty-input")
                yield Button("ALL LONG", variant="success", id="all-long")
                yield Button("ALL SHORT", variant="error", id="all-short")
                yield Button("EXECUTE ALL", variant="warning", id="exec-all")
                yield Button("EXIT", variant="error", id="quit-button")

        # 거래소 리스트 (스크롤 가능)
        with ScrollableContainer(id="exchanges-scroll"):
            for exchange_name in EXCHANGES:
                yield ExchangeControl(exchange_name, self.manager)

        # 로그
        yield Log(id="log", highlight=True)
        yield Footer()

    def on_info_update(self, message: InfoUpdate) -> None:
        """거래소 정보 업데이트"""
        try:
            pos_widget = self.query_one(f"#pos_{message.exchange_name}", Static)
            col_widget = self.query_one(f"#col_{message.exchange_name}", Static)
            
            pos_widget.update(message.position_info)
            col_widget.update(message.collateral_info)
            
            self.update_total_collateral(message.exchange_name, message.collateral_value)
        except Exception as e:
            logging.error(f"Failed to update UI for {message.exchange_name}: {e}")

    async def on_mount(self) -> None:
        self.set_interval(5, self.update_current_price)
        await self.update_current_price()

    def watch_symbol(self, new_symbol: str) -> None:
        asyncio.create_task(self.update_all_exchange_info())

    def watch_current_price(self, price: str) -> None:
        self.query_one("#current-price-display").update(f"📈 ${price}")

    def watch_total_collateral(self, total: float) -> None:
        self.query_one("#total-collateral-display").update(f"💵 Total: ${total:,.2f}")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "symbol-input":
            self.symbol = event.value.upper()
        elif event.input.id == "all-qty-input":
            qty = event.value
            for name in EXCHANGES:
                try:
                    self.query_one(f"#qty_{name}", Input).value = qty
                except:
                    pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        log = self.query_one("#log", Log)
        
        if button_id == "all-long":
            # 모든 거래소 LONG 선택
            for name in EXCHANGES:
                try:
                    long_btn = self.query_one(f"#long_{name}", Button)
                    short_btn = self.query_one(f"#short_{name}", Button)
                    long_btn.variant = "success"
                    short_btn.variant = "default"
                except:
                    pass
            log.write_line("[ALL] All exchanges set to LONG")
            
        elif button_id == "all-short":
            # 모든 거래소 SHORT 선택
            for name in EXCHANGES:
                try:
                    long_btn = self.query_one(f"#long_{name}", Button)
                    short_btn = self.query_one(f"#short_{name}", Button)
                    long_btn.variant = "default"
                    short_btn.variant = "error"
                except:
                    pass
            log.write_line("[ALL] All exchanges set to SHORT")
        
        elif button_id.startswith(("long_", "short_")):
            action, exchange_name = button_id.split("_", 1)
            try:
                long_button = self.query_one(f"#long_{exchange_name}", Button)
                short_button = self.query_one(f"#short_{exchange_name}", Button)
                if action == "long":
                    long_button.variant = "success"
                    short_button.variant = "default"
                else:
                    long_button.variant = "default"
                    short_button.variant = "error"
            except Exception as e:
                logging.error(f"Button selection error: {e}")
                
        elif button_id.startswith("exec_"):
            exchange_name = button_id.split("_", 1)[1]
            await self.execute_order(exchange_name)
            
        elif button_id == "exec-all":
            await self.execute_all_orders()
            
        elif button_id == "quit-button":
            self.exit()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id.startswith("type_"):
            exchange_name = event.radio_set.id.split("_", 1)[1]
            try:
                price_input = self.query_one(f"#price_{exchange_name}", Input)
                price_input.disabled = event.index == 0
            except Exception as e:
                logging.error(f"Radio set change error: {e}")

    async def execute_order(self, exchange_name: str, side_override: str = None) -> None:
        exchange = self.manager.get_exchange(exchange_name)
        log = self.query_one("#log", Log)
        
        if not exchange:
            log.write_line(f"❌ [{exchange_name.upper()}] No configuration")
            return

        try:
            qty_input = self.query_one(f"#qty_{exchange_name}", Input)
            type_set = self.query_one(f"#type_{exchange_name}", RadioSet)
            price_input = self.query_one(f"#price_{exchange_name}", Input)
            
            side = side_override if side_override else self.get_selected_side(exchange_name)
            
            if not qty_input.value:
                log.write_line(f"⚠️ [{exchange_name.upper()}] Please enter quantity")
                return
                
            amount = float(qty_input.value)
            order_type = 'market' if type_set.pressed_index == 0 else 'limit'
            price = float(price_input.value) if order_type == 'limit' and price_input.value else None

            if not side:
                log.write_line(f"⚠️ [{exchange_name.upper()}] Please select LONG or SHORT")
                return

            log.write_line(f"🔄 [{exchange_name.upper()}] Executing {side.upper()} {amount} {self.symbol} @ {order_type}")
            
            order = await exchange.create_order(
                symbol=f"{self.symbol}/USDC:USDC",
                type=order_type,
                side=side,
                amount=amount,
                price=price
            )
            
            log.write_line(f"✅ [{exchange_name.upper()}] Order success: #{order['id']}")
            
            # 주문 후 정보 갱신
            controls = self.query("ExchangeControl").results()
            for control in controls:
                if isinstance(control, ExchangeControl) and control.exchange_name == exchange_name:
                    await control.update_info()
                    break

        except Exception as e:
            log.write_line(f"❌ [{exchange_name.upper()}] Order failed: {e}")
            logging.error(f"Order execution error: {e}", exc_info=True)

    async def execute_all_orders(self):
        log = self.query_one("#log", Log)
        log.write_line("🚀 [ALL] Executing orders on all exchanges...")
        
        tasks = []
        for name in EXCHANGES:
            if self.manager.get_exchange(name):
                tasks.append(self.execute_order(name))
        
        if tasks:
            await asyncio.gather(*tasks)
        else:
            log.write_line("⚠️ [ALL] No configured exchanges")

    async def update_current_price(self):
        repr_exchange = next((ex for ex in self.manager.exchanges.values() if ex), None)
        if not repr_exchange:
            self.current_price = "N/A"
            return
        
        try:
            ticker = await repr_exchange.fetch_ticker(f"{self.symbol}/USDC:USDC")
            self.current_price = f"{ticker['last']:,.2f}"
        except Exception as e:
            self.current_price = "Error"
            logging.error(f"Price fetch error: {e}")

    async def update_all_exchange_info(self):
        controls = self.query("ExchangeControl").results()
        tasks = []
        for control in controls:
            if isinstance(control, ExchangeControl) and control.exchange:
                tasks.append(control.update_info())
        
        if tasks:
            await asyncio.gather(*tasks)

    def update_total_collateral(self, exchange_name: str, value: float):
        self._collateral_by_exchange[exchange_name] = value
        self.total_collateral = sum(self._collateral_by_exchange.values())

    def get_selected_side(self, exchange_name: str) -> str | None:
        try:
            long_button = self.query_one(f"#long_{exchange_name}", Button)
            short_button = self.query_one(f"#short_{exchange_name}", Button)
            
            if long_button.variant == "success":
                return "buy"
            if short_button.variant == "error":
                return "sell"
            return None
        except Exception as e:
            logging.error(f"Get side error: {e}")
            return None
        
    async def action_quit(self) -> None:
        await self.manager.close_all()
        self.exit()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', filename='debug.log', filemode='w')
    logging.info("Application starting...")
    
    try:
        exchange_manager = ExchangeManager()
        app = TradingApp(manager=exchange_manager)
        app.run()
    except Exception as e:
        logging.critical("CRITICAL ERROR: %s", e, exc_info=True)
    finally:
        logging.info("Application finished.")