import asyncio
import random
import logging
import urwid
from typing import Dict, Optional

from core import ExchangeManager, EXCHANGES


class UrwidApp:
    def __init__(self, manager: ExchangeManager):
        self.mgr = manager

        # 상태
        self.symbol: str = "BTC"
        self.current_price: str = "..."
        self.enabled: Dict[str, bool] = {name: False for name in EXCHANGES}      # OFF/ON
        self.side: Dict[str, Optional[str]] = {name: None for name in EXCHANGES}  # 'buy'/'sell'/None
        self.order_type: Dict[str, str] = {name: "market" for name in EXCHANGES}  # 'market'/'limit'
        self.collateral: Dict[str, float] = {name: 0.0 for name in EXCHANGES}

        # UI 레퍼런스
        self.loop = None
        self.header = None
        self.body_list: urwid.ListBox = None
        self.footer = None

        self.ticker_edit = None
        self.price_text = None
        self.total_text = None              # 총 담보 표시
        self.allqty_edit = None
        self.repeat_times = None
        self.repeat_min = None
        self.repeat_max = None

        # 거래소별 위젯
        self.qty_edit: Dict[str, urwid.Edit] = {}
        self.price_edit: Dict[str, urwid.Edit] = {}
        self.type_btn: Dict[str, urwid.Button] = {}
        self.type_btn_wrap: Dict[str, urwid.Widget] = {}
        self.long_btn: Dict[str, urwid.Button] = {}
        self.long_btn_wrap: Dict[str, urwid.Widget] = {}
        self.short_btn: Dict[str, urwid.Button] = {}
        self.short_btn_wrap: Dict[str, urwid.Widget] = {}
        self.off_btn: Dict[str, urwid.Button] = {}
        self.off_btn_wrap: Dict[str, urwid.Widget] = {}
        self.ex_btn: Dict[str, urwid.Button] = {}
        self.ex_btn_wrap: Dict[str, urwid.Widget] = {}
        self.info_text: Dict[str, urwid.Text] = {}

        # 로그
        self.log_list = urwid.SimpleListWalker([])
        self.log_box: urwid.ListBox | None = None

        # REPEAT
        self.repeat_task = None
        self.repeat_cancel = asyncio.Event()

    # ---------------------- 유틸/로그 ----------------------
    def _log(self, msg: str):
        self.log_list.append(urwid.Text(msg))
        # 자동 스크롤(맨 아래로 포커스 이동)
        if self.log_box is not None and len(self.log_list) > 0:
            self.log_box.set_focus(len(self.log_list) - 1)
        # 화면 다시 그리기 요청
        self._request_redraw()

    def _collateral_sum(self) -> float:
        return sum(self.collateral.values())


    def _request_redraw(self):
        """다음 틱에 화면을 다시 그리도록 스케줄합니다."""
        if self.loop:
            try:
                # 0초 뒤 알람 → urwid idle 진입 시 redraw
                self.loop.set_alarm_in(0, lambda loop, data: None)
            except Exception:
                pass

    # ---------------------- 헤더(3행) ----------------------
    def _hdr_widgets(self):
        # 1행: Ticker / Price / Total / QUIT
        self.ticker_edit = urwid.Edit(("label", "Ticker: "), self.symbol)
        self.price_text = urwid.Text(("info", f"Price: {self.current_price}"))
        self.total_text = urwid.Text(("info", "Total: 0.00 USDC"))
        quit_btn = urwid.AttrMap(urwid.Button("QUIT", on_press=self._on_quit), "btn_warn", "btn_focus")

        row1 = urwid.Columns(
            [
                (18, self.ticker_edit),
                (20, self.price_text),
                (22, self.total_text),
                (8, quit_btn),
            ],
            dividechars=1,
        )

        # 2행: All Qty / EXECUTE ALL / REVERSE
        self.allqty_edit = urwid.Edit(("label", "All Qty: "), "")
        exec_btn = urwid.AttrMap(urwid.Button("EXECUTE ALL", on_press=self._on_exec_all), "btn", "btn_focus")
        reverse_btn = urwid.AttrMap(urwid.Button("REVERSE", on_press=self._on_reverse), "btn", "btn_focus")

        row2 = urwid.Columns(
            [
                (18, self.allqty_edit),
                (15, exec_btn),
                (11, reverse_btn),
            ],
            dividechars=1,
        )

        # 3행: REPEAT (Times / a(s) / b(s) / REPEAT)
        self.repeat_times = urwid.Edit(("label", "Times: "))
        self.repeat_min = urwid.Edit(("label", "a(s): "))
        self.repeat_max = urwid.Edit(("label", "b(s): "))
        repeat_btn = urwid.AttrMap(urwid.Button("REPEAT", on_press=self._on_repeat_toggle), "btn", "btn_focus")

        row3 = urwid.Columns(
            [
                (14, self.repeat_times),
                (10, self.repeat_min),
                (10, self.repeat_max),
                (10, repeat_btn),
            ],
            dividechars=1,
        )

        # 헤더 전체는 Pile로 3행 구성
        return urwid.Pile([('pack', row1), ('pack', row2), ('pack', row3)])

    # ---------------------- 거래소 카드 ----------------------
    def _row(self, name: str):
        # 입력칸: 포커스 시 배경으로 구분
        qty = urwid.AttrMap(urwid.Edit(("label", "Q:"), ""), "edit", "edit_focus")
        price = urwid.AttrMap(urwid.Edit(("label", "P:"), ""), "edit", "edit_focus")
        self.qty_edit[name] = qty.base_widget
        self.price_edit[name] = price.base_widget

        # Type (MKT/LMT) 토글
        def on_type(btn, n=name):
            self.order_type[n] = "limit" if self.order_type[n] == "market" else "market"
            self._refresh_type_label(n)

        type_btn = urwid.Button("MKT", on_press=on_type)
        type_wrap = urwid.AttrMap(type_btn, "btn_type", "btn_focus")
        self.type_btn[name] = type_btn
        self.type_btn_wrap[name] = type_wrap

        # L / S / OFF / EX
        def on_long(btn, n=name):
            self.side[n] = "buy"; self.enabled[n] = True; self._refresh_side(n)

        def on_short(btn, n=name):
            self.side[n] = "sell"; self.enabled[n] = True; self._refresh_side(n)

        def on_off(btn, n=name):
            self.enabled[n] = False; self.side[n] = None; self._refresh_side(n)

        async def ex_async(n=name): await self._exec_one(n)
        def on_ex(btn, n=name): asyncio.get_event_loop().create_task(ex_async(n))

        long_b = urwid.Button("L", on_press=on_long)
        short_b = urwid.Button("S", on_press=on_short)
        off_b = urwid.Button("OFF", on_press=on_off)
        ex_b = urwid.Button("EX", on_press=on_ex)

        long_wrap  = urwid.AttrMap(long_b,  "btn_long",         "btn_focus")
        short_wrap = urwid.AttrMap(short_b, "btn_short",        "btn_focus")
        off_wrap   = urwid.AttrMap(off_b,   "btn_off",          "btn_focus")
        ex_wrap    = urwid.AttrMap(ex_b,    "btn_exec",         "btn_focus")

        self.long_btn[name],  self.long_btn_wrap[name]   = long_b,  long_wrap
        self.short_btn[name], self.short_btn_wrap[name]  = short_b, short_wrap
        self.off_btn[name],   self.off_btn_wrap[name]    = off_b,   off_wrap
        self.ex_btn[name],    self.ex_btn_wrap[name]     = ex_b,    ex_wrap

        # 상태 표시
        info = urwid.Text(("info", "📊 Position: N/A | 💰 Collateral: N/A"))
        self.info_text[name] = info

        # 컨트롤 열 폭을 넉넉히(라벨 줄바꿈 방지)
        controls = urwid.Columns(
            [
                (12, urwid.Text(("title", f"[{name.upper()}]"))),
                (14, qty),        # Q
                (14, price),      # P
                (7,  type_wrap),  # <MKT>/<LMT> 한 줄
                (5,  long_wrap),  # <L>
                (5,  short_wrap), # <S>
                (7,  off_wrap),   # <OFF>
                (6,  ex_wrap),    # <EX>
            ],
            dividechars=1,
        )

        # 거래소 카드: FLOW로 2줄(controls + info), 카드 사이 Divider는 build()에서 추가
        return urwid.Pile([controls, info])

    def _refresh_type_label(self, name: str):
        self.type_btn[name].set_label("LMT" if self.order_type[name] == "limit" else "MKT")

    def _refresh_side(self, name: str):
        if self.side[name] == "buy":
            self.long_btn_wrap[name].set_attr_map({None: "btn_long_on"})
            self.short_btn_wrap[name].set_attr_map({None: "btn_short"})
        elif self.side[name] == "sell":
            self.long_btn_wrap[name].set_attr_map({None: "btn_long"})
            self.short_btn_wrap[name].set_attr_map({None: "btn_short_on"})
        else:
            self.long_btn_wrap[name].set_attr_map({None: "btn_long"})
            self.short_btn_wrap[name].set_attr_map({None: "btn_short"})
        self.off_btn_wrap[name].set_attr_map({None: "btn_off"})

    # ---------------------- 화면 구성 ----------------------
    def build(self):
        self.header = self._hdr_widgets()

        # 각 거래소 행을 FLOW로 구성하고, 사이에 Divider(색 적용)로 구분
        rows = []
        for i, n in enumerate(EXCHANGES):
            rows.append(self._row(n))
            if i != len(EXCHANGES) - 1:
                rows.append(urwid.AttrMap(urwid.Divider("─"), "sep"))

        self.body_list = urwid.ListBox(urwid.SimpleListWalker(rows))

        # Logs
        self.log_box = urwid.ListBox(self.log_list)
        self.footer = urwid.Pile([
            ('pack',  urwid.AttrMap(urwid.Text("Logs"), 'title')),
            ('fixed', 10, urwid.LineBox(self.log_box)),   # 고정 10줄 + 자동 스크롤
        ])

        frame = urwid.Frame(
            header=urwid.LineBox(self.header),
            body=self.body_list,  # 거래소가 많아지면 자동 세로 스크롤 가능
            footer=self.footer,
        )
        return frame

    # ---------------------- 주기 작업 ----------------------
    async def _price_loop(self):
        while True:
            try:
                self.symbol = (self.ticker_edit.edit_text or "BTC").upper()
                ex = next((self.mgr.get_exchange(n) for n in EXCHANGES if self.mgr.get_exchange(n)), None)
                if not ex:
                    self.current_price = "N/A"
                else:
                    try:
                        t = await ex.fetch_ticker(f"{self.symbol}/USDC:USDC")
                        self.current_price = f"{t['last']:,.2f}"
                    except Exception:
                        self.current_price = "Error"

                # 헤더 가격/총 담보 업데이트
                self.price_text.set_text(("info", f"Price: {self.current_price}"))
                total = self._collateral_sum()
                self.total_text.set_text(("info", f"Total: {total:,.2f} USDC"))

                # 화면 다시 그리기 요청 (입력 없이도 즉시 반영)
                self._request_redraw()

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"price loop: {e}")
                await asyncio.sleep(1.0)

    async def _status_loop(self, name: str):
        await asyncio.sleep(random.uniform(0.0, 0.7))
        while True:
            try:
                ex = self.mgr.get_exchange(name)
                if not ex:
                    self.info_text[name].set_text(("info", "📘 Position: N/A  |  💰 Collateral: N/A"))
                    self._request_redraw()  # ← 추가
                    await asyncio.sleep(1.0)
                    continue

                bal_coro = ex.fetch_balance()
                pos_coro = ex.fetch_positions([f"{self.symbol}/USDC:USDC"])
                balance, positions = await asyncio.gather(bal_coro, pos_coro, return_exceptions=False)

                total_collateral = balance.get("USDC", {}).get("total", 0) or 0
                self.collateral[name] = float(total_collateral)

                if positions and positions[0]:
                    p = positions[0]
                    sz = 0.0
                    try: sz = float(p.get("contracts") or 0)
                    except: sz = 0.0
                    if sz:
                        side = "LONG" if p.get("side") == "long" else "SHORT"
                        pnl = 0.0
                        try: pnl = float(p.get("unrealizedPnl") or 0)
                        except: pnl = 0.0
                        parts = [
                            (None, "📘 "), ("long_col" if side == "LONG" else "short_col", side),
                            (None, f" {sz:.5f}  |  PnL: "),
                            ("pnl_pos" if pnl >= 0 else "pnl_neg", f"{pnl:,.2f}"),
                            (None, f"  |  💰 Collateral: {total_collateral:,.2f} USDC"),
                            ]
                    else:
                        parts = [(None, f"📘 Position: N/A  |  💰 Collateral: {total_collateral:,.2f} USDC")]
                else:
                    parts = [(None, f"📘 Position: N/A  |  💰 Collateral: {total_collateral:,.2f} USDC")]

                self.info_text[name].set_text(parts)

                # 헤더 Total 갱신
                total = self._collateral_sum()
                self.total_text.set_text(("info", f"Total: {total:,.2f} USDC"))

                # 화면 다시 그리기 요청
                self._request_redraw()

                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"status loop {name}: {e}")
                await asyncio.sleep(1.0)

    # ---------------------- 버튼 핸들러 ----------------------
    def _on_exec_all(self, btn):
        asyncio.get_event_loop().create_task(self._exec_all())

    def _on_reverse(self, btn):
        cnt = 0
        for n in EXCHANGES:
            if not self.enabled.get(n, False):
                continue
            if self.side.get(n) == "buy":
                self.side[n] = "sell"; cnt += 1
            elif self.side.get(n) == "sell":
                self.side[n] = "buy";  cnt += 1
            self._refresh_side(n)
        self._log(f"[ALL] REVERSE 완료: {cnt}개")

    def _on_repeat_toggle(self, btn):
        loop = asyncio.get_event_loop()
        if self.repeat_task and not self.repeat_task.done():
            self.repeat_cancel.set()
            self._log("[REPEAT] 중지 요청")
        else:
            try:
                times = int(self.repeat_times.edit_text or "0")
                a = float(self.repeat_min.edit_text or "0")
                b = float(self.repeat_max.edit_text or "0")
            except Exception:
                self._log("[REPEAT] 입력 파싱 실패"); return
            if times <= 0 or a < 0 or b < 0:
                self._log("[REPEAT] Times>=1, Interval>=0 필요"); return
            if b < a: a, b = b, a
            self.repeat_cancel.clear()
            self.repeat_task = loop.create_task(self._repeat_runner(times, a, b))

    def _on_quit(self, btn):
        raise urwid.ExitMainLoop()

    # ---------------------- 주문 실행 ----------------------
    async def _exec_one(self, name: str):
        ex = self.mgr.get_exchange(name)
        if not ex:
            self._log(f"[{name.upper()}] 설정 없음"); return
        if not self.enabled.get(name, False):
            self._log(f"[{name.upper()}] 비활성 상태"); return
        side = self.side.get(name)
        if not side:
            self._log(f"[{name.upper()}] LONG/SHORT 미선택"); return

        try:
            qty_text = (self.qty_edit[name].edit_text or "").strip()
            if not qty_text:
                self._log(f"[{name.upper()}] 수량 없음"); return
            amount = float(qty_text)

            otype = self.order_type[name]
            price = None
            if otype == "limit":
                p_txt = (self.price_edit[name].edit_text or "").strip()
                if not p_txt:
                    self._log(f"[{name.upper()}] 가격 없음"); return
                price = float(p_txt)
            else:
                try:
                    t = await ex.fetch_ticker(f"{self.symbol}/USDC:USDC")
                    price = t.get("last")
                except Exception:
                    price = None

            self._log(f"[{name.upper()}] {side.upper()} {amount} {self.symbol} @ {otype}")
            order = await ex.create_order(
                symbol=f"{self.symbol}/USDC:USDC",
                type=otype,
                side=side,
                amount=amount,
                price=price,
            )
            self._log(f"[{name.upper()}] 주문 성공: #{order['id']}")
        except Exception as e:
            self._log(f"[{name.upper()}] 주문 실패: {e}")

    async def _exec_all(self):
        self._log("[ALL] 동시 주문 시작")
        tasks = []
        for n in EXCHANGES:
            if not self.mgr.get_exchange(n): continue
            if not self.enabled.get(n, False):
                self._log(f"[ALL] {n.upper()} 건너뜀: 비활성"); continue
            if not self.side.get(n):
                self._log(f"[ALL] {n.upper()} 건너뜀: 방향 미선택"); continue
            tasks.append(self._exec_one(n))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._log("[ALL] 완료")
        else:
            self._log("[ALL] 실행할 거래소가 없습니다.")

    async def _repeat_runner(self, times: int, a: float, b: float):
        self._log(f"[REPEAT] 시작: {times}회, 간격 {a:.2f}~{b:.2f}s 랜덤")
        try:
            for i in range(1, times + 1):
                if self.repeat_cancel.is_set():
                    self._log(f"[REPEAT] 취소됨 (진행 {i-1}/{times})"); break
                self._log(f"[REPEAT] 실행 {i}/{times}")
                await self._exec_all()
                if i < times:
                    delay = random.uniform(a, b)
                    self._log(f"[REPEAT] 대기 {delay:.2f}s ...")
                    try:
                        await asyncio.wait_for(self.repeat_cancel.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                    if self.repeat_cancel.is_set():
                        self._log("[REPEAT] 취소됨 (대기 중)"); break
            self._log("[REPEAT] 완료")
        finally:
            self.repeat_task = None
            self.repeat_cancel.clear()

    # ---------------------- 실행/루프 ----------------------
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        event_loop = urwid.AsyncioEventLoop(loop=loop)

        palette = [
            ("label",       "light cyan",     ""),
            ("info",        "light gray",     ""),
            ("title",       "light magenta",  ""),
            ("sep",         "dark gray",      ""),

            # 입력(Edit) 스타일
            ("edit",        "white",          ""),
            ("edit_focus",  "black",          "light gray"),

            # 버튼
            ("btn",         "black",          "light gray"),
            ("btn_focus",   "black",          "light blue"),
            ("btn_warn",    "black",          "yellow"),
            ("btn_type",    "black",          "dark cyan"),
            ("btn_exec",    "black",          "dark magenta"),

            ("btn_long",    "light green",    ""),
            ("btn_long_on", "black",          "light green"),
            ("btn_short",   "light red",      ""),
            ("btn_short_on","black",          "light red"),
            ("btn_off",     "yellow",         ""),

            # 정보 색
            ("long_col",    "light green",    ""),
            ("short_col",   "light red",      ""),
            ("pnl_pos",     "light green",    ""),
            ("pnl_neg",     "light red",      ""),
        ]

        root = self.build()
        self.loop = urwid.MainLoop(root, palette=palette, event_loop=event_loop)

        async def _bootstrap():
            try:
                await self.mgr.initialize_all()
            except Exception as e:
                logging.warning(f"initialize_all failed: {e}")
            loop.create_task(self._price_loop())
            for n in EXCHANGES:
                loop.create_task(self._status_loop(n))

            # All Qty → 각 카드 Q 동기화
            def allqty_changed(edit, new):
                for n in EXCHANGES:
                    if n in self.qty_edit:
                        self.qty_edit[n].set_edit_text(new)
            urwid.connect_signal(self.allqty_edit, "change", allqty_changed)

            # Ticker 변경 즉시 반영
            def ticker_changed(edit, new):
                self.symbol = (new or "BTC").upper()
            urwid.connect_signal(self.ticker_edit, "change", ticker_changed)

            self._request_redraw()

        loop.run_until_complete(_bootstrap())

        try:
            self.loop.run()
        finally:
            try:
                loop.run_until_complete(self.mgr.close_all())
            except Exception:
                pass
            loop.stop()
            loop.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename="debug.log",
        filemode="w",
    )
    try:
        app = UrwidApp(ExchangeManager())
        app.run()
    except KeyboardInterrupt:
        pass