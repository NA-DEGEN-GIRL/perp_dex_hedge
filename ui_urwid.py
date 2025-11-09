# ui_urwid.py
import asyncio
import random
import logging
import warnings
from typing import Dict, Optional, List

import urwid
from urwid.widget.pile import PileWarning  # urwid 레이아웃 경고 제거용

from core import ExchangeManager


# urwid의 레이아웃 경고(PileWarning)를 화면에 출력하지 않도록 억제
warnings.simplefilter("ignore", PileWarning)


class UrwidApp:
    def __init__(self, manager: ExchangeManager):
        self.mgr = manager

        # 상태
        self.symbol: str = "BTC"
        self.current_price: str = "..."
        self.enabled: Dict[str, bool] = {name: False for name in self.mgr.all_names()}      # OFF/ON
        self.side: Dict[str, Optional[str]] = {name: None for name in self.mgr.all_names()}  # 'buy'/'sell'/None
        self.order_type: Dict[str, str] = {name: "market" for name in self.mgr.all_names()}  # 'market'/'limit'
        self.collateral: Dict[str, float] = {name: 0.0 for name in self.mgr.all_names()}

        # UI 레퍼런스
        self.loop: urwid.MainLoop | None = None
        self.header = None
        self.body_list: urwid.ListBox = None
        self.footer = None

        # 헤더 위젯
        self.ticker_edit = None
        self.price_text = None
        self.total_text = None
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

        # “Exchanges” 토글 박스
        self.switcher_list_walker: urwid.SimpleListWalker | None = None
        self.switch_checks: Dict[str, urwid.CheckBox] = {}

        # 로그
        self.log_list = urwid.SimpleListWalker([])
        self.log_box: urwid.ListBox | None = None

        # REPEAT
        self.repeat_task = None
        self.repeat_cancel = asyncio.Event()
    
    def _set_initial_focus(self, loop=None, data=None):
        """앱 시작 후 즉시 'All Qty' 입력칸에 포커스를 맞춘다."""
        try:
            if not self.loop:
                return
            frame: urwid.Frame = self.loop.widget
            # 1) 전체 포커스 영역을 헤더로
            frame.focus_part = "header"

            # 2) 헤더(LineBox → Pile)에서 2번째 행(row2 = All Qty/EXEC/REVERSE)로 포커스
            header_widget = frame.header
            header_pile = header_widget.original_widget if isinstance(header_widget, urwid.LineBox) else header_widget
            if isinstance(header_pile, urwid.Pile):
                header_pile.focus_position = 1  # row2

                # 3) row2는 Columns: 첫 컬럼(All Qty)로 포커스
                row2 = header_pile.contents[1][0]
                if isinstance(row2, urwid.Columns):
                    row2.focus_position = 0  # All Qty Edit

            # 4) 커서를 All Qty 텍스트 끝으로 이동(선택사항)
            if self.allqty_edit is not None:
                self.allqty_edit.set_edit_pos(len(self.allqty_edit.edit_text or ""))

            # 즉시 다시 그리기
            self._request_redraw()
        except Exception:
            pass

    # --------- 유틸/화면 갱신 ----------
    def _request_redraw(self):
        """다음 틱에 화면을 다시 그리도록 스케줄"""
        if self.loop:
            try:
                self.loop.set_alarm_in(0, lambda loop, data: None)
            except Exception:
                pass

    def _log(self, msg: str):
        self.log_list.append(urwid.Text(msg))
        if self.log_box is not None and len(self.log_list) > 0:
            self.log_box.set_focus(len(self.log_list) - 1)  # 자동 스크롤
        self._request_redraw()

    def _collateral_sum(self) -> float:
        return sum(self.collateral.values())

    # --------- 헤더(3행) ----------
    def _hdr_widgets(self):
        # 1행
        self.ticker_edit = urwid.Edit(("label", "Ticker: "), self.symbol)
        self.price_text = urwid.Text(("info", f"Price: {self.current_price}"))
        self.total_text = urwid.Text(("info", "Total: 0.00 USDC"))
        quit_btn = urwid.AttrMap(urwid.Button("QUIT", on_press=self._on_quit), "btn_warn", "btn_focus")

        row1 = urwid.Columns(
            [
                (18, self.ticker_edit),
                (20, self.price_text),
                (28, self.total_text),
                (8, quit_btn),
            ],
            dividechars=1,
        )
        # 2행
        self.allqty_edit = urwid.Edit(("label", "All Qty: "), "")
        exec_btn = urwid.AttrMap(urwid.Button("EXECUTE ALL", on_press=self._on_exec_all), "btn_exec", "btn_focus")
        reverse_btn = urwid.AttrMap(urwid.Button("REVERSE", on_press=self._on_reverse), "btn", "btn_focus")

        row2 = urwid.Columns(
            [
                (18, self.allqty_edit),
                (15, exec_btn),
                (11, reverse_btn),
            ],
            dividechars=1,
        )
        # 3행
        self.repeat_times = urwid.Edit(("label", "Times: "))
        self.repeat_min = urwid.Edit(("label", "min(s): "))
        self.repeat_max = urwid.Edit(("label", "max(s): "))
        repeat_btn = urwid.AttrMap(urwid.Button("REPEAT", on_press=self._on_repeat_toggle), "btn_exec", "btn_focus")

        row3 = urwid.Columns(
            [
                (14, self.repeat_times),
                (13, self.repeat_min),
                (13, self.repeat_max),
                (10, repeat_btn),
            ],
            dividechars=1,
        )
        # pack 대신 기본(FLOW)로 두어 경고 제거
        return urwid.Pile([row1, row2, row3])

    # --------- 거래소 카드 ----------
    def _row(self, name: str):
        # 입력
        qty = urwid.AttrMap(urwid.Edit(("label", "Q:"), ""), "edit", "edit_focus")
        price = urwid.AttrMap(urwid.Edit(("label", "P:"), ""), "edit", "edit_focus")
        self.qty_edit[name] = qty.base_widget
        self.price_edit[name] = price.base_widget

        # 타입 토글
        def on_type(btn, n=name):
            self.order_type[n] = "limit" if self.order_type[n] == "market" else "market"
            self._refresh_type_label(n)
        type_btn = urwid.Button("MKT", on_press=on_type)
        type_wrap = urwid.AttrMap(type_btn, "btn_type", "btn_focus")
        self.type_btn[name] = type_btn
        self.type_btn_wrap[name] = type_wrap

        # L/S/OFF/EX
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

        # 상태
        info = urwid.Text(("info", "📊 Position: N/A | 💰 Collateral: N/A"))
        self.info_text[name] = info

        controls = urwid.Columns(
            [
                (12, urwid.Text(("title", f"[{name.upper()}]"))),
                (14, qty),
                (14, price),
                (7,  type_wrap),
                (5,  long_wrap),
                (5,  short_wrap),
                (7,  off_wrap),
                (6,  ex_wrap),
            ],
            dividechars=1,
        )
        # 전부 FLOW로(자동 높이); 고정 높이 강제 X
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

    # --------- Exchanges 토글 박스 (GridFlow로 가로 나열) ----------
    def _build_switcher(self):
        # 체크박스 만들기
        self.switch_checks = {}
        cells = []
        for name in self.mgr.all_names():
            show = self.mgr.get_meta(name).get("show", False)
            chk = urwid.CheckBox(name.upper(), state=show, on_state_change=self._on_toggle_show)
            self.switch_checks[name] = chk
            # 폭이 들쭉날쭉하지 않게 Padding으로 약간 여유
            cells.append(urwid.Padding(chk, width=('relative', 100)))  # 나중에 Columns에 넣을 것

        # 2줄로 고정: 상단 절반, 하단 절반
        half = (len(cells) + 1) // 2
        row1_cells = cells[:half]
        row2_cells = cells[half:]

        # 가로로 쭉 나열 (여백 2칸)
        row1 = urwid.Columns(row1_cells, dividechars=2)
        row2 = urwid.Columns(row2_cells, dividechars=2) if row2_cells else urwid.Text("")

        # 2줄을 Pile로 묶고 박스로 감싸 시각적 구분
        box_body = urwid.Pile([row1, row2])
        box = urwid.LineBox(box_body, title="Exchanges")
        return box

    def _on_toggle_show(self, chk: urwid.CheckBox, state: bool):
        # meta 갱신
        for n, c in self.switch_checks.items():
            if c is chk:
                self.mgr.meta[n]["show"] = bool(state)
                if not state:
                    # OFF 간주
                    self.enabled[n] = False
                    self.side[n] = None
                break
        # 바디 재구성
        self._rebuild_body_rows()
        self._request_redraw()

    def _rebuild_body_rows(self):
        rows = []
        visible = self.mgr.visible_names()
        for i, n in enumerate(visible):
            rows.append(self._row(n))
            if i != len(visible) - 1:
                rows.append(urwid.AttrMap(urwid.Divider("─"), "sep"))
        self.body_list.body = urwid.SimpleListWalker(rows)

    # --------- 화면 구성 ----------
    def build(self):
        self.header = self._hdr_widgets()

        # body: show=True 거래소만 표시
        rows = []
        visible = self.mgr.visible_names()
        for i, n in enumerate(visible):
            rows.append(self._row(n))
            if i != len(visible) - 1:
                rows.append(urwid.AttrMap(urwid.Divider("─"), "sep"))
        self.body_list = urwid.ListBox(urwid.SimpleListWalker(rows))

        # switcher + logs (여기 수정)
        switcher = self._build_switcher()
        self.log_box = urwid.ListBox(self.log_list)

        # Logs 제목은 pack(1줄), 로그 박스는 fixed(10줄)
        logs_panel = urwid.Pile([
            ('pack',  urwid.AttrMap(urwid.Text("Logs"), 'title')),
            ('fixed', 10, urwid.LineBox(self.log_box)),
        ])

        # Footer는 Exchanges 박스(고정 높이 4줄: 콘텐츠 2 + 테두리 2), Logs 패널은 pack
        self.footer = urwid.Pile([
            ('fixed', 4, switcher),   # 2줄 고정 박스
            ('pack',  logs_panel),    # Logs는 내부에서 고정 높이를 이미 줌
        ])

        frame = urwid.Frame(
            header=urwid.LineBox(self.header),
            body=self.body_list,
            footer=self.footer,
        )
        return frame

    # --------- 주기 작업 ----------
    async def _price_loop(self):
        while True:
            try:
                self.symbol = (self.ticker_edit.edit_text or "BTC").upper()
                # HL 가격 공유: hl=True + 설정된 첫 거래소에서만 조회
                ex = self.mgr.first_hl_exchange()
                if not ex:
                    self.current_price = "N/A"
                else:
                    try:
                        t = await ex.fetch_ticker(f"{self.symbol}/USDC:USDC")
                        self.current_price = f"{t['last']:,.2f}"
                    except Exception:
                        self.current_price = "Error"

                self.price_text.set_text(("info", f"Price: {self.current_price}"))
                self.total_text.set_text(("info", f"Total: {self._collateral_sum():,.2f} USDC"))
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
                    self.info_text.get(name, urwid.Text("")).set_text(("info", "📘 Position: N/A  |  💰 Collateral: N/A"))
                    self._request_redraw()
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

                if name in self.info_text:
                    self.info_text[name].set_text(parts)
                self.total_text.set_text(("info", f"Total: {self._collateral_sum():,.2f} USDC"))
                self._request_redraw()
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"status loop {name}: {e}")
                await asyncio.sleep(1.0)

    # --------- 버튼 핸들러 ----------
    def _on_exec_all(self, btn):
        asyncio.get_event_loop().create_task(self._exec_all())

    def _on_reverse(self, btn):
        cnt = 0
        for n in self.mgr.visible_names():
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

    # --------- 주문 실행 ----------
    async def _exec_one(self, name: str):
        max_retry = 3
        ex = self.mgr.get_exchange(name)
        if not ex:
            self._log(f"[{name.upper()}] 설정 없음"); return
        if not self.enabled.get(name, False):
            self._log(f"[{name.upper()}] 비활성 상태"); return
        side = self.side.get(name)
        if not side:
            self._log(f"[{name.upper()}] LONG/SHORT 미선택"); return

        for attempt in range(1,max_retry+1):
            try:
                qty_text = (self.qty_edit[name].edit_text or "").strip()
                if not qty_text:
                    self._log(f"[{name.upper()}] 수량 없음"); return
                amount = float(qty_text)

                otype = (self.order_type[name] or "").lower()

                if otype == "limit":
                    # [수정] 지정가: 입력된 가격을 사용
                    p_txt = (self.price_edit[name].edit_text or "").strip()
                    if not p_txt:
                        self._log(f"[{name.upper()}] 지정가(Price) 없음")
                        return
                    price = float(p_txt)
                else:
                    # 시장가: 캐시된 현재가 사용
                    price = float(str(self.current_price).replace(",", ""))
                
                self._log(f"[{name.upper()}] {side.upper()} {amount} {self.symbol} @ {otype}")
                order = await ex.create_order(
                    symbol=f"{self.symbol}/USDC:USDC",
                    type=otype,
                    side=side,
                    amount=amount,
                    price=price,
                )
                self._log(f"[{name.upper()}] 주문 성공: #{order['id']}")
                break
            except Exception as e:
                self._log(f"[{name.upper()}] 주문 실패: {e}")
                self._log(f"[{name.upper()}] 주문 재시도...{attempt} | {max_retry}")
                if attempt >= max_retry:
                    self._log(f"[{name.upper()}] 재시도 한도 초과, 중단")
                    return
                await asyncio.sleep(0.5)

    async def _exec_all(self):
        self._log("[ALL] 동시 주문 시작")
        tasks = []
        for n in self.mgr.visible_names():
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

    def _focus_header(self):
        if self.loop:
            frame: urwid.Frame = self.loop.widget
            frame.focus_part = "header"

    def _focus_body_first(self):
        if self.loop and self.body_list:
            frame: urwid.Frame = self.loop.widget
            frame.focus_part = "body"
            try:
                # 첫 가시 거래소 카드로 이동
                if len(self.body_list.body) > 0:
                    self.body_list.set_focus(0)
            except Exception:
                pass

    def _focus_footer(self):
        if self.loop:
            frame: urwid.Frame = self.loop.widget
            frame.focus_part = "footer"

    # ---------- 키 핸들러 ----------
    def _tab_body_next(self):
        """본문(거래소) 영역에서 현재 줄(controls Columns) 내부 포커스를 오른쪽으로 이동"""
        try:
            # 현재 바디 포커스: Pile([controls, info])
            try:
                focus_widget = self.body_list.focus              # 새 표준 속성
            except AttributeError:
                focus_widget, _ = self.body_list.get_focus()     # 구식 API(예외적 폴백)

            if not isinstance(focus_widget, urwid.Pile):
                return

            controls = focus_widget.contents[0][0]
            if not isinstance(controls, urwid.Columns):
                return

            try:
                idx = controls.focus_position                    # 새 표준 속성
            except AttributeError:
                _, idx = controls.get_focus()                    # 폴백

            n = len(controls.contents)
            controls.focus_position = (idx + 1) % n              # 새 표준 속성으로 설정
        except Exception:
            pass


    def _tab_body_prev(self):
        """본문(거래소) 영역에서 현재 줄(controls Columns) 내부 포커스를 왼쪽으로 이동"""
        try:
            try:
                focus_widget = self.body_list.focus
            except AttributeError:
                focus_widget, _ = self.body_list.get_focus()

            if not isinstance(focus_widget, urwid.Pile):
                return

            controls = focus_widget.contents[0][0]
            if not isinstance(controls, urwid.Columns):
                return

            try:
                idx = controls.focus_position
            except AttributeError:
                _, idx = controls.get_focus()

            n = len(controls.contents)
            controls.focus_position = (idx - 1) % n
        except Exception:
            pass

    def _on_key(self, key):
        """
        탭/시프트탭 + Ctrl/Alt/Shift+위·아래 + PageUp/Down + F6 + Ctrl+J/K.
        마우스 이벤트(tuple)는 무시.
        """
        # 0) 마우스/비문자 입력(urwid는 mouse press 등을 tuple로 전달) → 무시
        if not isinstance(key, str):
            return

        k = key.lower().strip()

        # 현재 포커스 영역
        try:
            frame: urwid.Frame = self.loop.widget
            part = frame.focus_part  # 'header' | 'body' | 'footer'
        except Exception:
            part = None

        # 영역 전환 유틸
        def to_next_region():
            if part == 'header':
                self._focus_body_first()
            elif part == 'body':
                self._focus_footer()
            else:  # footer or None
                self._focus_header()

        def to_prev_region():
            if part == 'footer':
                self._focus_body_first()
            elif part == 'body':
                self._focus_header()
            else:  # header or None
                self._focus_footer()

        # 1) 영역 전환: 여러 조합 허용 (Ctrl/Alt/Shift+Arrow, PageUp/Down, Ctrl+J/K, F6)
        next_keys = {'ctrl down', 'meta down', 'shift down', 'page down', 'ctrl j', 'f6'}
        prev_keys = {'ctrl up',   'meta up',   'shift up',   'page up',   'ctrl k'}

        if k in next_keys:
            to_next_region()
            return
        if k in prev_keys:
            to_prev_region()
            return

        # 2) Tab / Shift+Tab: 본문(거래소 카드 1행) 내부 칸 이동만 처리
        if k in {'tab', '\t'}:
            if part == 'body':
                self._tab_body_next()
                return
        if k in {'shift tab', 'backtab'}:
            if part == 'body':
                self._tab_body_prev()
                return

        # 그 외 키는 urwid 기본 동작(방향키/엔터 등)에 맡김
        return
    
    # --------- 실행/루프 ----------
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        event_loop = urwid.AsyncioEventLoop(loop=loop)

        palette = [
            ("label",       "light cyan",     ""),
            ("info",        "light gray",     ""),
            ("title",       "light magenta",  ""),
            ("sep",         "dark gray",      ""),

            ("edit",        "white",          ""),
            ("edit_focus",  "black",          "light gray"),

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

            ("long_col",    "light green",    ""),
            ("short_col",   "light red",      ""),
            ("pnl_pos",     "light green",    ""),
            ("pnl_neg",     "light red",      ""),
        ]

        root = self.build()
        self.loop = urwid.MainLoop(root,
            palette=palette,
            event_loop=event_loop,
            unhandled_input=self._on_key  # [추가] 키 핸들러 연결
        )
        
        async def _bootstrap():
            try:
                await self.mgr.initialize_all()
            except Exception as e:
                logging.warning(f"initialize_all failed: {e}")

            # 가격/상태 주기 작업 시작 (표시 중인 거래소만 상태 루프)
            loop.create_task(self._price_loop())
            for n in self.mgr.visible_names():
                loop.create_task(self._status_loop(n))

            # All Qty → 각 카드 Q 동기화
            def allqty_changed(edit, new):
                for n in self.mgr.visible_names():
                    if n in self.qty_edit:
                        self.qty_edit[n].set_edit_text(new)
            urwid.connect_signal(self.allqty_edit, "change", allqty_changed)

            # Ticker 변경 즉시 반영
            def ticker_changed(edit, new):
                self.symbol = (new or "BTC").upper()
            urwid.connect_signal(self.ticker_edit, "change", ticker_changed)

            self._request_redraw()

        loop.run_until_complete(_bootstrap())
        self.loop.set_alarm_in(0, self._set_initial_focus)

        try:
            self.loop.run()
        finally:
            try:
                loop.run_until_complete(self.mgr.close_all())
            except Exception:
                pass
            loop.stop()
            loop.close()

'''
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
'''    
