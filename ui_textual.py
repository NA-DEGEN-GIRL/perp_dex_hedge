# ui_textual.py
import time, random, asyncio
import logging
import textwrap
from pathlib import Path

from textual.message import Message
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import (
    Button, Footer, Header, Input, Label, Static, RadioSet, RadioButton, Log
)
from textual.reactive import reactive

from core import ExchangeManager, EXCHANGES  # 핵심: 코어에서 가져옴
from trading_service import TradingService   # 공통 거래 서비스

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
        self._updating = False              # 중복 실행 가드
        self._last_pos = None              # 마지막 포지션 문자열 캐시
        self._last_col = None              # 마지막 담보 문자열 캐시

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
            # 거래소별 시작 시점 분산 (0~700ms 지터)
            await asyncio.sleep(random.uniform(0.0, 0.7))
            self.set_interval(1.0, self.update_info)  # 1초 주기 그대로
            await self.update_info()

    async def update_info(self) -> None:
        if not self.exchange or self._updating:
            return
        self._updating = True
        t0 = time.perf_counter()
        symbol = self.app.symbol
        try:
            # 서비스 호출
            pos_str, col_str, col_val = await self.app.service.fetch_status(self.exchange_name, symbol)

            # 변경된 경우에만 메시지 전송 → 불필요한 리렌더링/메시지 폭주 방지
            if (pos_str != self._last_pos) or (col_str != self._last_col):
                self._last_pos, self._last_col = pos_str, col_str
                self.post_message(InfoUpdate(
                    exchange_name=self.exchange_name,
                    collateral=col_str,
                    position=pos_str,
                    collateral_val=col_val
                ))

            # 성능 로깅(선택)
            dt = (time.perf_counter() - t0) * 1000
            if dt > 800:
                logging.info(f"[{self.exchange_name.upper()}] update_info took {dt:.0f} ms")

        except Exception:
            logging.error(f"[{self.exchange_name.upper()}] UPDATE_INFO ERROR", exc_info=True)
            # 에러 시에도 바뀌었을 때만 UI 갱신
            err_pos, err_col = "📊 Position: Error", "💰 Collateral: Error"
            if (err_pos != self._last_pos) or (err_col != self._last_col):
                self._last_pos, self._last_col = err_pos, err_col
                self.post_message(InfoUpdate(
                    exchange_name=self.exchange_name,
                    collateral=err_col,
                    position=err_pos,
                    collateral_val=0
                ))
        finally:
            self._updating = False

# --- 메인 앱 ---
class KimbapHeaven(App):
    CSS_PATH = Path(__file__).with_name("app.tcss")
    symbol = reactive("BTC")
    current_price = reactive("...")
    total_collateral = reactive(0.0)

    def __init__(self, manager: ExchangeManager):
        super().__init__()
        self.manager = manager
        self.service = TradingService(manager)  # 공통 서비스
        self._collateral_by_exchange = {name: 0 for name in EXCHANGES}
        self.exchange_enabled = {name: False for name in EXCHANGES}
        self._updating_price = False
        self._repeat_task: asyncio.Task | None = None
        self._repeat_cancel = asyncio.Event()

    def compose(self) -> ComposeResult:
        yield Header(name="Hyperliquid Multi-DEX Trader")

        with Container(id="main-controls"):
            with Horizontal(classes="hdr-row hdr-gap"):
                yield Label("Ticker:", classes='tiny-label')
                yield Input(value="BTC", placeholder="BTC", id="symbol-input")
                yield Static(id="current-price-display")
                yield Static(id="total-collateral-display")

            with Horizontal(classes="hdr-row hdr-gap"):
                yield Label("All Qty:", classes='tiny-label')
                yield Input(id="all-qty-input")
                yield Button("EXECUTE ALL", variant="warning", id="exec-all")
                yield Button("REVERSE", variant="primary", id="reverse-all")
                yield Button("종료", variant="error", id="quit-button")
            
            with Horizontal(classes="hdr-row"):
                yield Button("REPEAT", variant="warning", id="repeat-all")
                yield Label("Times:", classes='tiny-label')
                yield Input(id="repeat-count")
                yield Label("Interval(s):", classes='tiny-label')
                yield Input(id="repeat-min")
                yield Label("~", classes='tiny-label')
                yield Input(id="repeat-max")

        with Container(id="body-scroll"):
            with ScrollableContainer(id="exchanges-container"):
                for name in EXCHANGES:
                    yield ExchangeControl(name, self.manager, id=name)

        yield Log(id="log", highlight=True)
        yield Footer()

    # Log 줄바꿈 유틸
    def log_write(self, msg: str) -> None:
        try:
            log = self.query_one("#log", Log)
            w = log.size.width or 80
            wrap_width = max(20, w - 4)
            for line in textwrap.wrap(msg, width=wrap_width, replace_whitespace=False, drop_whitespace=False):
                log.write_line(line)
        except Exception:
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
        try:
            await self.manager.initialize_all()
        except Exception as e:
            logging.warning(f"initialize_all failed: {e}")
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
        if bid.startswith("disable_"):
            ex_name = bid.split("_", 1)[1]
            self.exchange_enabled[ex_name] = False
            try:
                long_b = self.query_one(f"#{ex_name} #long_{ex_name}", Button)
                short_b = self.query_one(f"#{ex_name} #short_{ex_name}", Button)
                long_b.variant, short_b.variant = "default", "default"
            except Exception:
                pass
            try:
                off_b = self.query_one(f"#{ex_name} #disable_{ex_name}", Button)
                off_b.variant = "warning"
            except Exception:
                pass
            self.log_write(f"[{ex_name.upper()}] 비활성화됨 (EXECUTE ALL 대상 제외)")
            return

        if bid == "repeat-all":
            await self._toggle_repeat()
            return

        if bid == "reverse-all":
            reversed_count = 0
            for name in EXCHANGES:
                if not self.exchange_enabled.get(name, False):
                    continue
                side = self.get_selected_side(name)
                if not side:
                    continue
                try:
                    long_b = self.query_one(f"#{name} #long_{name}", Button)
                    short_b = self.query_one(f"#{name} #short_{name}", Button)
                    if side == "buy":
                        long_b.variant = "default"
                        short_b.variant = "error"
                        reversed_count += 1
                    elif side == "sell":
                        long_b.variant = "success"
                        short_b.variant = "default"
                        reversed_count += 1
                except Exception:
                    pass
            self.log_write(f"[ALL] REVERSE 완료: {reversed_count}개 거래소 방향 반전")
            return

        if bid.startswith(("long_", "short_")):
            action, ex_name = bid.split("_", 1)
            try:
                long_b = self.query_one(f"#{ex_name} #long_{ex_name}", Button)
                short_b = self.query_one(f"#{ex_name} #short_{ex_name}", Button)
                if action == "long":
                    long_b.variant, short_b.variant = "success", "default"
                else:
                    long_b.variant, short_b.variant = "default", "error"
                self.exchange_enabled[ex_name] = True
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
        if not self.manager.get_exchange(exchange_name):
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
            price = float(price_input.value) if (order_type == "limit" and price_input.value) else None
            if not side:
                self.log_write(f"[{exchange_name.upper()}] LONG/SHORT 선택을 하세요.")
                return
            self.log_write(f"[{exchange_name.upper()}] {side.upper()} {amount} {self.symbol} @ {order_type}")
            # 서비스 호출
            order = await self.service.execute_order(exchange_name, self.symbol, amount, order_type, side, price)
            self.log_write(f"[{exchange_name.upper()}] 주문 성공: #{order['id']}")
            await self.query_one(f"#{exchange_name}", ExchangeControl).update_info()
        except Exception as e:
            self.log_write(f"[{exchange_name.upper()}] 주문 실패: {e}")
            logging.error(f"[{exchange_name}] Order execution error", exc_info=True)

    async def execute_all_orders(self):
        self.log_write("[ALL] 모든 거래소 동시 주문 실행...")
        tasks = []
        for name in EXCHANGES:
            if not self.manager.get_exchange(name):
                continue
            if not self.exchange_enabled.get(name, False):
                self.log_write(f"[ALL] {name.upper()} 건너뜀: 비활성")
                continue
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
        if self._updating_price:
            return
        self._updating_price = True
        try:
            self.current_price = await self.service.fetch_current_price(self.symbol)
        finally:
            self._updating_price = False

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

    async def _toggle_repeat(self):
        btn = self.query_one("#repeat-all", Button)
        exec_btn = self.query_one("#exec-all", Button)
        if self._repeat_task and not self._repeat_task.done():
            self._repeat_cancel.set()
            self.log_write("[REPEAT] 중지 요청...")
            try:
                await self._repeat_task
            except Exception:
                pass
            self._repeat_task = None
            self._repeat_cancel.clear()
            btn.label = "REPEAT"; btn.variant = "warning"; exec_btn.disabled = False
            self.log_write("[REPEAT] 중지 완료")
            return
        try:
            n_input = self.query_one("#repeat-count", Input).value or "0"
            a_input = self.query_one("#repeat-min", Input).value or "0"
            b_input = self.query_one("#repeat-max", Input).value or "0"
            times = int(float(n_input)); a = float(a_input); b = float(b_input)
        except Exception:
            self.log_write("[REPEAT] 입력값 파싱 실패 (Times/Interval)"); return
        if times <= 0: self.log_write("[REPEAT] Times는 1 이상이어야 합니다."); return
        if a < 0 or b < 0: self.log_write("[REPEAT] Interval은 0 이상이어야 합니다."); return
        if b < a: a, b = b, a
        btn.label = "STOP"; btn.variant = "error"; exec_btn.disabled = True
        self._repeat_cancel.clear()
        self._repeat_task = asyncio.create_task(self._repeat_runner(times, a, b))

    async def _repeat_runner(self, times: int, a: float, b: float):
        self.log_write(f"[REPEAT] 시작: {times}회, 간격 {a:.2f}~{b:.2f}s 랜덤")
        try:
            for i in range(1, times + 1):
                if self._repeat_cancel.is_set():
                    self.log_write(f"[REPEAT] 취소됨 (진행 {i-1}/{times})"); break
                self.log_write(f"[REPEAT] 실행 {i}/{times}")
                await self.execute_all_orders()
                if i < times:
                    delay = random.uniform(a, b)
                    self.log_write(f"[REPEAT] 대기 {delay:.2f}s ...")
                    try:
                        await asyncio.wait_for(self._repeat_cancel.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    if self._repeat_cancel.is_set():
                        self.log_write(f"[REPEAT] 취소됨 (대기 중)"); break
            self.log_write("[REPEAT] 완료")
        finally:
            try:
                btn = self.query_one("#repeat-all", Button)
                exec_btn = self.query_one("#exec-all", Button)
                btn.label = "REPEAT"; btn.variant = "warning"; exec_btn.disabled = False
            except Exception:
                pass
            self._repeat_task = None; self._repeat_cancel.clear()

    async def on_quit(self) -> None:
        if self._repeat_task and not self._repeat_task.done():
            self._repeat_cancel.set()
            try: await self._repeat_task
            except Exception: pass
        await self.manager.close_all()

    async def action_quit(self) -> None:
        await self.on_quit()
        self.exit()