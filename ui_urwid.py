# ui_urwid.py
import asyncio
import random
import logging
import warnings
from typing import Dict, Optional, List

import urwid
from urwid.widget.pile import PileWarning  # urwid 레이아웃 경고 제거용

from core import ExchangeManager
from trading_service import TradingService
import sys
import os
import contextlib
import re
import time

# urwid의 레이아웃 경고(PileWarning)를 화면에 출력하지 않도록 억제
warnings.simplefilter("ignore", PileWarning)

class CustomFrame(urwid.Frame):
    """Tab/Shift+Tab을 앱 핸들러로만 보내고 기본 동작 차단"""
    def __init__(self, *args, app_ref=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.app_ref = app_ref

    def keypress(self, size, key):
        # Tab/Shift+Tab은 우리 앱 핸들러로만 보내고 여기서 차단
        if key in ('tab', 'shift tab'):
            if self.app_ref and self.app_ref._on_key:
                result = self.app_ref._on_key(key)
                # 처리됐으면(True) None 반환 → urwid가 더 이상 처리 안 함
                if result:
                    return None
        # 그 외 키는 부모(기본 Frame)에 위임
        return super().keypress(size, key)

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
        self.burn_count = None           # burn 횟수 (1이면 repeat와 동일)
        self.burn_min = None             # burn interval min(s)
        self.burn_max = None             # burn interval max(s)

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

        # trading service
        self.service = TradingService(self.mgr)

        # 로그
        self.log_list = urwid.SimpleListWalker([])
        self.log_box: urwid.ListBox | None = None

        # REPEAT/BURN 태스크
        self.repeat_task = None
        self.repeat_cancel = asyncio.Event()
        self.burn_task = None
        self.burn_cancel = asyncio.Event() 

        # 거래소별 status 루프 태스크 관리
        self._status_tasks: Dict[str, asyncio.Task] = {}
        self._price_task: asyncio.Task | None = None      # 가격 루프 태스크 보관
        
        self._last_balance_at: Dict[str, float] = {}  # [추가]

        self._ticker_lev_alarm = None  # 디바운스 핸들

    def _status_bracket_to_urwid(self, pos_str: str, col_str: str):
        """
        trading_service.fetch_status가 주는 문자열(예: '[green]LONG[/] 0.1 | PnL: [red]-0.02[/]')
        을 urwid 마크업 리스트로 변환.
        규칙:
        - 첫 번째 [green]/[red] 블록 → side 색(long_col/short_col)
        - 두 번째 [green]/[red] 블록 → pnl 색(pnl_pos/pnl_neg)
        """
        # 태그 토큰화
        tokens = re.split(r'(\[green\]|\[red\]|\[/\])', pos_str)
        parts = []
        attr = None
        seen_colored_blocks = 0

        for tok in tokens:
            if tok in ('[green]', '[red]'):
                # 어떤 블록인지에 따라 attr 결정
                seen_colored_blocks += 1
                if seen_colored_blocks == 1:
                    attr = 'long_col' if tok == '[green]' else 'short_col'
                else:
                    attr = 'pnl_pos' if tok == '[green]' else 'pnl_neg'
            elif tok == '[/]':
                attr = None
            elif tok:
                # 실제 텍스트
                if attr:
                    parts.append((attr, tok))
                else:
                    parts.append((None, tok))

        # 앞에 아이콘 + 뒤에 collateral 문자열 붙이기
        # pos_str 앞의 "📊 "은 서비스에서 이미 포함되어 있을 수 있음. 일관성을 위해 교체.
        # pos_str에서 '📊 '이 없는 경우도 있으니, 아이콘은 여기서 추가하지 않고 그대로 parts만 사용.
        # col_str은 색상 없이 붙입니다.
        parts.append((None, f" | {col_str}"))
        return parts
    
    def _inject_usdc_value_into_pos(self, pos_str: str) -> str:
        """
        pos_str 예: '📊 [green]LONG[/] 0.12345 | PnL: [red]-1.23[/]'
        → '📊 [green]LONG[/] 0.12345 (3,456.78 USDC) | PnL: [red]-1.23[/]'
        현재가(self.current_price)가 숫자일 때만 삽입. 실패 시 원문 유지.
        """
        try:
            price = float(str(self.current_price).replace(",", ""))
        except Exception:
            return pos_str  # 현재가가 숫자가 아니면 원문 유지

        # 사이즈를 캡처: 닫는 괄호 ']' 뒤의 공백들 다음에 오는 숫자, 그리고 뒤에 ' | PnL:'이 이어지는 패턴
        m = re.search(r"\]\s*([+-]?\d+(?:\.\d+)?)(?=\s*\|\s*PnL:)", pos_str)
        if not m:
            return pos_str

        size_str = m.group(1)
        try:
            size = float(size_str)
        except Exception:
            return pos_str

        usdc_value = size * price
        injected = f"{size_str} ({usdc_value:,.2f} USDC)"

        # 캡처된 사이즈 부분만 교체
        start, end = m.span(1)
        new_pos = pos_str[:start] + injected + pos_str[end:]
        return new_pos
    
    def _enable_win_vt(self):
        """Windows 콘솔에서 VT 입력/출력을 가능한 한 활성화."""
        if os.name != "nt":
            return
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 핸들: 입력/출력
            STD_INPUT_HANDLE  = -10
            STD_OUTPUT_HANDLE = -11
            ENABLE_VIRTUAL_TERMINAL_INPUT       = 0x0200
            ENABLE_VIRTUAL_TERMINAL_PROCESSING  = 0x0004

            # 입력 모드
            hIn = kernel32.GetStdHandle(STD_INPUT_HANDLE)
            in_mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(hIn, ctypes.byref(in_mode)):
                kernel32.SetConsoleMode(hIn, in_mode.value | ENABLE_VIRTUAL_TERMINAL_INPUT)

            # 출력 모드
            hOut = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            out_mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(hOut, ctypes.byref(out_mode)):
                kernel32.SetConsoleMode(hOut, out_mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            # 실패해도 조용히 넘어감(하위 콘솔이면 어차피 mouse off 처리됨)
            pass

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
        reverse_btn = urwid.AttrMap(urwid.Button("REVERSE", on_press=self._on_reverse), "btn_reverse", "btn_focus")
        close_positions_btn = urwid.AttrMap(urwid.Button("CLOSE ALL", on_press=self._on_close_positions), "btn_reverse", "btn_focus")

        row2 = urwid.Columns(
            [
                (18, self.allqty_edit),
                (15, exec_btn),
                (11, reverse_btn),
                (13, close_positions_btn),
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

        self.burn_count = urwid.Edit(("label", "Burn: "))
        self.burn_min   = urwid.Edit(("label", "min(s): ")) 
        self.burn_max   = urwid.Edit(("label", "max(s): "))
        burn_btn = urwid.AttrMap(urwid.Button("BURN", on_press=self._on_burn_toggle), "btn_short_on", "btn_focus")
        row4 = urwid.Columns(
            [
                (14, self.burn_count),
                (13, self.burn_min),
                (13, self.burn_max),
                (8, burn_btn),
            ],
            dividechars=1,
        )

        # pack 대신 기본(FLOW)로 두어 경고 제거
        return urwid.Pile([row1, row2, row3, row4])

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
        card = urwid.Pile([controls, info])  # ← 기존에는 바로 return 하던 부분

        # [추가] 카드 생성 직후 현재 상태에 맞게 버튼 강조 반영
        # 초기 enabled[name]은 False이므로 OFF 버튼이 강조(btn_off_on)로 보입니다.
        self._refresh_side(name)

        return card

    def _refresh_type_label(self, name: str):
        self.type_btn[name].set_label("LMT" if self.order_type[name] == "limit" else "MKT")

    def _refresh_side(self, name: str):
        """
        버튼 스타일 반영:
        - enabled=False → OFF 강조(btn_off_on), L/S 기본색
        - enabled=True & side=='buy' → L 강조, S 기본, OFF 기본
        - enabled=True & side=='sell' → S 강조, L 기본, OFF 기본
        - enabled=True & side=None → L/S/ OFF 모두 기본
        """
        off_wrap = self.off_btn_wrap.get(name)
        long_wrap = self.long_btn_wrap.get(name)
        short_wrap = self.short_btn_wrap.get(name)

        # 방어
        if not (off_wrap and long_wrap and short_wrap):
            return

        if not self.enabled.get(name, False):
            # OFF 상태(비활성) → OFF 강조
            long_wrap.set_attr_map({None: "btn_long"})
            short_wrap.set_attr_map({None: "btn_short"})
            off_wrap.set_attr_map({None: "btn_off_on"})
            return

        # enabled=True
        side = self.side.get(name)
        if side == "buy":
            long_wrap.set_attr_map({None: "btn_long_on"})
            short_wrap.set_attr_map({None: "btn_short"})
            off_wrap.set_attr_map({None: "btn_off"})
        elif side == "sell":
            long_wrap.set_attr_map({None: "btn_long"})
            short_wrap.set_attr_map({None: "btn_short_on"})
            off_wrap.set_attr_map({None: "btn_off"})
        else:
            # 방향 미선택이지만 enabled=True인 경우 (드문 케이스)
            long_wrap.set_attr_map({None: "btn_long"})
            short_wrap.set_attr_map({None: "btn_short"})
            off_wrap.set_attr_map({None: "btn_off"})

    # --------- Exchanges 토글 박스 (GridFlow로 가로 나열) ----------
    def _build_switcher(self):
        """
        Exchanges 토글 박스: 두 줄, 균일 폭 셀.
        - 각 체크박스를 ('given', cell_w, widget)으로 전달해 고정 폭으로 배치
        - 이름 길이에 따라 cell_w를 동적으로 산정(최소 12)
        """
        self.switch_checks = {}

        names = self.mgr.all_names()
        if not names:
            return urwid.LineBox(urwid.Text("no exchanges"), title="Exchanges")

        # 라벨 최대 길이에 여유분(브래킷·공백 등) 더해 셀 폭 산정
        max_label = max(len(n.upper()) for n in names)
        cell_w = max(12, max_label + 6)  # 최소 12칸, 보통 [ ] + 공백 여유 6

        # 두 줄로 균등 분할
        half = (len(names) + 1) // 2
        row1_widgets, row2_widgets = [], []

        for idx, name in enumerate(names):
            show = self.mgr.get_meta(name).get("show", False)
            chk = urwid.CheckBox(name.upper(), state=show, on_state_change=self._on_toggle_show)
            self.switch_checks[name] = chk
            # Columns에 'given' 폭으로 직접 넘깁니다 (Padding 불필요)
            if idx < half:
                row1_widgets.append(('given', cell_w, chk))
            else:
                row2_widgets.append(('given', cell_w, chk))

        def to_columns(cells):
            if not cells:
                return urwid.Text("")
            # dividechars로 셀 간 간격만 주면 모두 같은 폭으로 정렬됩니다.
            return urwid.Columns(cells, dividechars=2)

        row1 = to_columns(row1_widgets)
        row2 = to_columns(row2_widgets)

        return urwid.LineBox(urwid.Pile([row1, row2]), title="Exchanges")

    def _on_toggle_show(self, chk: urwid.CheckBox, state: bool):
        # meta 갱신
        toggled_name = None
        for n, c in self.switch_checks.items():
            if c is chk:
                self.mgr.meta[n]["show"] = bool(state)
                toggled_name = n
                if not state:
                    # OFF 간주
                    self.enabled[n] = False
                    self.side[n] = None
                break

        # 바디 재구성 (위젯 생성/제거)
        self._rebuild_body_rows()

        # NEW: 토글된 거래소의 status 루프 동적 관리
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if toggled_name:
            # ON → status 루프 시작 (exchange가 있는 경우에만)
            if state and self.mgr.get_exchange(toggled_name):
                t = self._status_tasks.get(toggled_name)
                if not t or t.done():
                    if loop:
                        self._status_tasks[toggled_name] = loop.create_task(self._status_loop(toggled_name))
            # OFF → status 루프 취소
            if not state:
                t = self._status_tasks.pop(toggled_name, None)
                if t and not t.done():
                    try:
                        t.cancel()
                    except Exception:
                        pass

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
            ('fixed', 6, urwid.LineBox(self.log_box)),
        ])

        # Footer는 Exchanges 박스(고정 높이 4줄: 콘텐츠 2 + 테두리 2), Logs 패널은 pack
        self.footer = urwid.Pile([
            ('fixed', 4, switcher),   # 2줄 + 테두리 2줄 = 4
            ('pack',  logs_panel),    # Logs는 내부에서 고정 높이를 이미 줌
        ])

        frame = CustomFrame(
            header=urwid.LineBox(self.header),
            body=self.body_list,
            footer=self.footer,
            app_ref=self  # self 참조 전달
        )
        return frame

    # --------- 주기 작업 ----------
    async def _price_loop(self):
        while True:
            try:
                self.symbol = (self.ticker_edit.edit_text or "BTC").upper()
                # 서비스 사용: HL 가격 공유 1회 조회
                self.current_price = await self.service.fetch_hl_price(self.symbol)
                self.price_text.set_text(("info", f"Price: {self.current_price}"))
                self.total_text.set_text(("info", f"Total: {self._collateral_sum():,.2f} USDC"))
                self._request_redraw()
                await asyncio.sleep(2.5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"price loop: {e}")
                await asyncio.sleep(2.5)

    async def _status_loop(self, name: str):
        await asyncio.sleep(random.uniform(0.0, 0.7))
        while True:
            try:
                # balance는 거래소별 2.5 초마다만 요청
                now = time.monotonic()
                need_balance = (now - self._last_balance_at.get(name, 0.0) >= 2.5)

                pos_str, col_str, col_val = await self.service.fetch_status(
                    name, self.symbol, need_balance=need_balance
                )
                if need_balance:
                    self._last_balance_at[name] = now

                pos_str = self._inject_usdc_value_into_pos(pos_str)

                self.collateral[name] = float(col_val)
                if name in self.info_text:
                    markup_parts = self._status_bracket_to_urwid(pos_str, col_str)
                    self.info_text[name].set_text(markup_parts)
                self.total_text.set_text(("info", f"Total: {self._collateral_sum():,.2f} USDC"))
                self._request_redraw()

                # 0.5~1.7초 사이 랜덤 지터로 분산
                await asyncio.sleep(0.5 + random.uniform(0.0, 0.7))

            except asyncio.CancelledError:
                break

            except Exception as e:
                logging.error(f"status loop {name}: {e}")
                await asyncio.sleep(2.5 + random.uniform(0.0, 0.7))

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
        
        # burn 돌고 있으면 먼저 멈춤
        if self.burn_task and not self.burn_task.done():
            self.burn_cancel.set()
            self._log("[BURN] 중지 요청")
        
        # repeat 돌고 있으면 먼저 멈춤
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

    def _on_burn_toggle(self, btn):
        loop = asyncio.get_event_loop()
        # 먼저 기존 repeat/burn 정리
        if self.repeat_task and not self.repeat_task.done():
            self.repeat_cancel.set()
            self._log("[REPEAT] 중지 요청")

        if self.burn_task and not self.burn_task.done():
            self.burn_cancel.set()
            self._log("[BURN] 중지 요청")
            return  # 누르면 중지 동작으로 동작

        # 입력값 파싱
        try:
            base_times = int(self.repeat_times.edit_text or "0")
            rep_min = float(self.repeat_min.edit_text or "0")
            rep_max = float(self.repeat_max.edit_text or "0")
            burn_times = int(self.burn_count.edit_text or "0")
            burn_min = float(self.burn_min.edit_text or "0")
            burn_max = float(self.burn_max.edit_text or "0")
        except Exception:
            self._log("[BURN] 입력 파싱 실패"); return
        if base_times <= 0 or rep_min < 0 or rep_max < 0 or burn_min < 0 or burn_max < 0:
            self._log("[BURN] Times>=1, Interval>=0 필요"); return
        if rep_max < rep_min:
            rep_min, rep_max = rep_max, rep_min
        if burn_max < burn_min:
            burn_min, burn_max = burn_max, burn_min

        # 태스크 시작
        self.burn_cancel.clear()
        self.burn_task = loop.create_task(
            self._burn_runner(burn_times, base_times, rep_min, rep_max, burn_min, burn_max)
        )
    
    def _on_close_positions(self, btn):
        asyncio.get_event_loop().create_task(self._close_all_positions())

    def _on_quit(self, btn):
        raise urwid.ExitMainLoop()

    # --------- 주문 실행 ----------
    async def _exec_one(self, name: str):
        # 반복/번 해제 신호가 이미 켜져 있으면 즉시 반환
        if self.repeat_cancel.is_set() or self.burn_cancel.is_set():
            return
        
        ex = self.mgr.get_exchange(name)
        if not ex:
            self._log(f"[{name.upper()}] 설정 없음"); return
        if not self.enabled.get(name, False):
            self._log(f"[{name.upper()}] 비활성 상태"); return
        side = self.side.get(name)
        if not side:
            self._log(f"[{name.upper()}] LONG/SHORT 미선택"); return

        max_retry = 3
        for attempt in range(1,max_retry+1):
            # 루프 중에도 즉시 중단
            if self.repeat_cancel.is_set() or self.burn_cancel.is_set():
                return
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
                order = await self.service.execute_order(
                    exchange_name=name,
                    symbol=self.symbol,
                    amount=amount,
                    order_type=otype,
                    side=side,
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
        # 즉시 중단 체크
        if self.repeat_cancel.is_set() or self.burn_cancel.is_set():
            self._log("[ALL] 취소됨")
            return
        
        self._log("[ALL] 동시 주문 시작")
        tasks = []
        for n in self.mgr.visible_names():
            if self.repeat_cancel.is_set() or self.burn_cancel.is_set():
                self._log("[ALL] 취소됨(준비 중)")
                break

            if not self.mgr.get_exchange(n): 
                continue
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
            i = 1
            while i <= times:
                # 즉시 중단 체크 (BURN 취소 또는 REPEAT 취소)
                if self.repeat_cancel.is_set() or self.burn_cancel.is_set():
                    self._log(f"[REPEAT] 취소됨 (진행 {i-1}/{times})")
                    break

                self._log(f"[REPEAT] 실행 {i}/{times}")
                await self._exec_all()

                if i >= times:
                    break

                # sleep도 cancel 즉시 반영
                delay = random.uniform(a, b)
                self._log(f"[REPEAT] 대기 {delay:.2f}s ...")
                try:
                    # 둘 중 하나라도 켜지면 즉시 리턴
                    await asyncio.wait_for(self._wait_cancel_any(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

                if self.repeat_cancel.is_set() or self.burn_cancel.is_set():
                    self._log(f"[REPEAT] 취소됨 (대기 중)")
                    break

                i += 1

            self._log("[REPEAT] 완료")
        finally:
            self.repeat_task = None
            self.repeat_cancel.clear()

    async def _burn_runner(self, burn_times: int, base_times: int, rep_min: float, rep_max: float, burn_min: float, burn_max: float):
        """
        burn_times=1 → repeat(base_times) 한 번만
        burn_times>=2 → repeat(base_times) → (sleep c~d → reverse → repeat(2*base_times)) × (burn_times-1)
        burn_times<0  → repeat(base_times) → 이후 무한 루프 [sleep c~d → reverse → repeat(2*base_times)]
        """
        self._log(f"[BURN] 시작: burn_times={burn_times}, base={base_times}, repeat_interval={rep_min}~{rep_max}, burn_interval={burn_min}~{burn_max}")
        try:
            # 1) 첫 라운드: repeat(base_times)
            if self.burn_cancel.is_set(): return
            await self._repeat_runner(base_times, rep_min, rep_max)
            if self.burn_cancel.is_set(): return

            # 2) 이후 라운드: 2*base_times, 방향 반전, burn interval 휴식
            round_idx = 2
            while True:
                if burn_times > 0 and round_idx > burn_times:
                    break
                # burn interval 대기
                delay = random.uniform(burn_min, burn_max)
                self._log(f"[BURN] interval 대기 {delay:.2f}s ... (round {round_idx}/{burn_times if burn_times>0 else '∞'})")
                try:
                    await asyncio.wait_for(asyncio.shield(self._wait_cancel_any()), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                if self.burn_cancel.is_set(): break

                # reverse
                self._reverse_enabled()
                if self.burn_cancel.is_set(): break

                # repeat 2×base_times
                await self._repeat_runner(2 * base_times, rep_min, rep_max)
                if self.burn_cancel.is_set(): break

                if burn_times > 0:
                    round_idx += 1
                else:
                    # 무한 반복
                    round_idx += 1
                    continue

            self._log("[BURN] 완료")

        finally:
            self.burn_task = None
            self.burn_cancel.clear()

    async def _wait_cancel_any(self):
        # 단순 event wait (실제 wait_for의 timeout과 함께 사용)
        # cancel 이벤트가 켜지면 즉시 반환
        while not (self.repeat_cancel.is_set() or self.burn_cancel.is_set()):
            await asyncio.sleep(0.05)

    def _reverse_enabled(self):
        """활성(enabled=True) + 방향 선택된 거래소만 LONG↔SHORT 토글."""
        cnt = 0
        for n in self.mgr.visible_names():
            if not self.enabled.get(n, False):
                continue
            cur = self.side.get(n)
            if cur == "buy":
                self.side[n] = "sell"
                cnt += 1
            elif cur == "sell":
                self.side[n] = "buy"
                cnt += 1
            # 버튼 색/상태 갱신
            try:
                if n in self.long_btn_wrap and n in self.short_btn_wrap:
                    if self.side[n] == "buy":
                        self.long_btn_wrap[n].set_attr_map({None: "btn_long_on"})
                        self.short_btn_wrap[n].set_attr_map({None: "btn_short"})
                    elif self.side[n] == "sell":
                        self.long_btn_wrap[n].set_attr_map({None: "btn_long"})
                        self.short_btn_wrap[n].set_attr_map({None: "btn_short_on"})
                    else:
                        self.long_btn_wrap[n].set_attr_map({None: "btn_long"})
                        self.short_btn_wrap[n].set_attr_map({None: "btn_short"})
            except Exception:
                pass
        self._log(f"[ALL] REVERSE 완료: {cnt}개")

    async def _close_all_positions(self):
        """
        show=True & enabled=True 거래소만 대상으로,
        현재 포지션의 반대 방향으로 '시장가' 주문을 넣어 포지션을 0으로 만든다.
        - 포지션 없으면 건너뜀
        - 지정가/가격 입력과 무관하게 항상 시장가(price=현재가) 사용
        """
        self._log("[CLOSE] CLOSE ALL 시작")
        tasks = []
        for n in self.mgr.visible_names():
            # OFF는 건너뜀
            if not self.enabled.get(n, False):
                self._log(f"[CLOSE] {n.upper()} 건너뜀: 비활성(OFF)")
                continue
            ex = self.mgr.get_exchange(n)
            if not ex:
                self._log(f"[CLOSE] {n.upper()} 건너뜀: 설정 없음")
                continue
            tasks.append(self._close_one_position(n, ex))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ok = sum(1 for r in results if not isinstance(r, Exception))
            self._log(f"[CLOSE] 완료: 성공 {ok}/{len(tasks)}")
        else:
            self._log("[CLOSE] 실행할 거래소가 없습니다.")

    async def _close_one_position(self, name: str, ex):
        """단일 거래소 청산(시장가) 헬퍼."""
        max_retry = 3
        for attempt in range(1,max_retry+1):
            try:
                # 현재가를 price_hint로 전달(서비스에서 실패 시 보조 조회)
                try:
                    hint = float(str(self.current_price).replace(",", ""))
                except Exception:
                    hint = None

                order = await self.service.close_position(
                    exchange_name=name,
                    symbol=self.symbol,
                    price_hint=hint,
                )
                if order is None:
                    # 포지션 없음/이미 0
                    return
                self._log(f"[{name.upper()}] CLOSE 성공: #{order.get('id','?')}")
                return
            except Exception as e:
                self._log(f"[{name.upper()}] CLOSE 실패: {e}")
                self._log(f"[{name.upper()}] CLOSE 재시도...{attempt} | {max_retry}")
                if attempt >= max_retry:
                    self._log(f"[{name.upper()}] 재시도 한도 초과, 중단")
                    return
                await asyncio.sleep(0.5)

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
        if not self.loop:
            return
        frame: urwid.Frame = self.loop.widget
        frame.focus_part = "footer"
        # Exchanges 박스(LineBox→Pile→row1 Columns)의 첫 칸으로
        switcher_pile = self._get_switcher_pile()
        if switcher_pile:
            try:
                switcher_pile.focus_position = 0  # row1
                row1 = switcher_pile.contents[0][0]
                if isinstance(row1, urwid.Columns):
                    row1.focus_position = 0
            except Exception:
                pass

    # ---------- 키 핸들러 ----------
   # ====================== 선택 가능 판정/언랩 유틸 ======================
    def _unwrap(self, w):
        try:
            while True:
                if isinstance(w, urwid.AttrMap):   w = w.original_widget
                elif isinstance(w, urwid.Padding): w = w.original_widget
                elif isinstance(w, urwid.LineBox): w = w.original_widget
                elif isinstance(w, urwid.BoxAdapter): w = w._original_widget
                elif isinstance(w, urwid.Filler):  w = w.body
                else: break
        except Exception:
            pass
        return w

    def _is_selectable_widget(self, w) -> bool:
        base = self._unwrap(w)
        try:
            return bool(base.selectable())
        except Exception:
            return False
        
    # ====================== Columns 내부 탐색 헬퍼 ======================
    def _first_selectable_index(self, columns: urwid.Columns):
        for i, (w, _) in enumerate(columns.contents):
            if self._is_selectable_widget(w):
                return i
        return None

    def _last_selectable_index(self, columns: urwid.Columns):
        for i in range(len(columns.contents) - 1, -1, -1):
            if self._is_selectable_widget(columns.contents[i][0]):
                return i
        return None

    def _current_col_index(self, columns: urwid.Columns):
        try:
            return columns.focus_position
        except Exception:
            _, idx = columns.get_focus()
            return 0 if idx is None else idx

    def _next_selectable_index(self, columns: urwid.Columns, idx: int):
        n = len(columns.contents)
        for j in range(idx + 1, n):
            if self._is_selectable_widget(columns.contents[j][0]):
                return j
        return None

    def _prev_selectable_index(self, columns: urwid.Columns, idx: int):
        for j in range(idx - 1, -1, -1):
            if self._is_selectable_widget(columns.contents[j][0]):
                return j
        return None

    def _get_header_pile(self):
        try:
            frame: urwid.Frame = self.loop.widget
            header_widget = frame.header
            header_pile = header_widget.original_widget if isinstance(header_widget, urwid.LineBox) else header_widget
            return header_pile if isinstance(header_pile, urwid.Pile) else None
        except Exception:
            return None

    # 2) Columns 내부 포커스 한 칸 이동(선택 가능한 칸만) ---------

    def _columns_focus_step(self, columns: urwid.Columns, forward: bool = True) -> bool:
        """Columns에서 다음/이전 '선택 가능한' 칸으로 이동. 이동하면 True."""
        try:
            try:
                idx = columns.focus_position
            except Exception:
                _, idx = columns.get_focus()
                if idx is None:
                    idx = 0

            n = len(columns.contents)
            if n == 0:
                return False

            # 현재 위치 기준으로 앞/뒤로 순회하며 selectable()인 칸을 찾는다
            for step in range(1, n + 1):
                j = (idx + step) % n if forward else (idx - step) % n
                w = columns.contents[j][0]
                if self._is_selectable_widget(w):
                    columns.focus_position = j
                    return True
            return False
        except Exception:
            return False

    # 3) 헤더 내부 Tab 이동(행은 유지, 입력/버튼만 순회) ------------

    def _tab_header_next(self):
        pile = self._get_header_pile()
        if not pile: return
        r = pile.focus_position
        row = pile.contents[r][0]
        if not isinstance(row, urwid.Columns): return
        idx = self._current_col_index(row)
        nxt = self._next_selectable_index(row, idx)
        if nxt is not None:
            row.focus_position = nxt
            return
        # 행 끝 → 다음 행 첫 선택항목
        r_next = (r + 1) % len(pile.contents)
        pile.focus_position = r_next
        next_row = pile.contents[r_next][0]
        if isinstance(next_row, urwid.Columns):
            f = self._first_selectable_index(next_row)
            if f is not None:
                next_row.focus_position = f

    def _tab_header_prev(self):
        pile = self._get_header_pile()
        if not pile: return
        r = pile.focus_position
        row = pile.contents[r][0]
        if not isinstance(row, urwid.Columns): return
        idx = self._current_col_index(row)
        prv = self._prev_selectable_index(row, idx)
        if prv is not None:
            row.focus_position = prv
            return
        # 행 처음 → 이전 행 마지막 선택항목
        r_prev = (r - 1) % len(pile.contents)
        pile.focus_position = r_prev
        prev_row = pile.contents[r_prev][0]
        if isinstance(prev_row, urwid.Columns):
            l = self._last_selectable_index(prev_row)
            if l is not None:
                prev_row.focus_position = l

    # 1) 카드 행(구분선 제외) 인덱스 목록/현재 카드 위치 얻기 ------------------

    def _card_row_indices(self) -> list[int]:
        """body_list 안에서 '카드(Pile)'가 있는 행 인덱스만 추려서 반환(구분선/텍스트 제외)."""
        rows = []
        if not self.body_list or not getattr(self.body_list, "body", None):
            return rows
        for i, w in enumerate(self.body_list.body):
            base = getattr(w, "base_widget", w)
            if isinstance(base, urwid.Pile):
                # 카드 Pile: 첫 콘텐츠가 Columns(controls) 인지 확인
                try:
                    if isinstance(base.contents[0][0], urwid.Columns):
                        rows.append(i)
                except Exception:
                    pass
        return rows

    def _current_card_info(self):
        """(현재카드행인덱스, 카드행순번(0..n-1), 전체카드행인덱스리스트, 현재카드의 controls Columns) 반환."""
        focus_widget, pos = self.body_list.get_focus()
        indices = self._card_row_indices()
        if pos not in indices:
            # 만약 포커스가 구분선에 있으면 가장 가까운 카드로 보정
            try:
                # 위쪽으로
                up = max([i for i in indices if i <= pos], default=None)
                if up is None:
                    up = min(indices) if indices else None
                if up is not None:
                    self.body_list.set_focus(up)
                    focus_widget, pos = self.body_list.get_focus()
            except Exception:
                pass
        if pos not in indices:
            return None, None, indices, None
        k = indices.index(pos)  # 현재 카드의 순번
        base = getattr(focus_widget, "base_widget", focus_widget)
        controls = base.contents[0][0] if isinstance(base, urwid.Pile) else None
        return pos, k, indices, controls

    # 2) 본문에서 Tab → 다음 카드의 Q 로 래핑 이동 -----------------------------
    def _tab_body_next(self):
        """본문(거래소 카드)에서 Tab → 줄 끝이면 다음 카드의 Q로 이동"""
        try:
            focus_widget, pos = self.body_list.get_focus()
            if not isinstance(focus_widget, urwid.Pile):
                return

            controls = focus_widget.contents[0][0]
            if not isinstance(controls, urwid.Columns):
                return

            # 1) 같은 줄 내에서 다음 selectable 칸으로 이동 시도
            idx = self._current_col_index(controls)
            nxt = self._next_selectable_index(controls, idx)
            if nxt is not None:
                controls.focus_position = nxt
                return

            # 2) 줄 끝 → 다음 카드로 이동
            indices = self._card_row_indices()
            if pos not in indices:
                return
            k = indices.index(pos)
            k_next = (k + 1) % len(indices)
            row_next = indices[k_next]

            # 다음 카드로 포커스 이동
            self.body_list.set_focus(row_next)

            # [핵심] 위젯 렌더링 완료 후 Q로 포커스를 설정하도록 지연 예약
            def _finalize_focus_to_q(loop, data):
                try:
                    # 지금 포커스된 카드 다시 가져오기
                    current_widget, _ = self.body_list.get_focus()
                    base = getattr(current_widget, "base_widget", current_widget)
                    if isinstance(base, urwid.Pile):
                        base.focus_position = 0  # controls 확정
                        cols = base.contents[0][0]
                        if isinstance(cols, urwid.Columns):
                            # Q=1로 강제
                            cols.focus_position = 1
                            self._request_redraw()
                except Exception as e:
                    logging.error(f"Tab next finalize error: {e}")

            # 0.01초 후 finalize (위젯 렌더 완료 대기)
            self.loop.set_alarm_in(0.05, _finalize_focus_to_q)

        except Exception as e:
            logging.error(f"Tab next exception: {e}", exc_info=True)

    # 3) 본문에서 Shift+Tab → 이전 카드의 EX(마지막 selectable)로 래핑 이동 ----
    def _tab_body_prev(self):
        """본문(거래소 카드)에서 Shift+Tab: 줄 처음이면 이전 카드의 EX(마지막 selectable)로 래핑 이동."""
        try:
            pos, k, indices, controls = self._current_card_info()
            if controls is None:
                return

            # 1) 같은 카드 내 이전 selectable 칸으로 이동 시도
            idx = self._current_col_index(controls)
            prv = self._prev_selectable_index(controls, idx)
            if prv is not None:
                controls.focus_position = prv
                return

            # 2) 줄 처음 → 이전 카드로 (래핑)
            if not indices:
                return
            k_prev = (k - 1) % len(indices)
            row_prev = indices[k_prev]

            # 이전 카드로 포커스 이동
            self.body_list.set_focus(row_prev)
            logging.info(f"Tab prev: moving from card {k} to card {k_prev}, row {row_prev}")

            # [핵심] 위젯 렌더링 완료 후 EX(마지막 selectable)로 포커스를 설정하도록 지연 예약
            def _finalize_focus_to_ex(loop, data):
                try:
                    # 지금 포커스된 카드 다시 가져오기
                    current_widget, _ = self.body_list.get_focus()
                    base = getattr(current_widget, "base_widget", current_widget)
                    if isinstance(base, urwid.Pile):
                        base.focus_position = 0  # controls 확정
                        cols = base.contents[0][0]
                        if isinstance(cols, urwid.Columns):
                            # 마지막 selectable(EX)로 강제
                            last_idx = self._last_selectable_index(cols)
                            if last_idx is not None:
                                cols.focus_position = last_idx
                                self._request_redraw()
                except Exception as e:
                    logging.error(f"Tab prev finalize error: {e}")

            # 0.01초 후 finalize (위젯 렌더 완료 대기)
            self.loop.set_alarm_in(0.05, _finalize_focus_to_ex)

        except Exception as e:
            logging.error(f"Tab prev exception: {e}", exc_info=True)
    # ====================== Exchanges(푸터) Tab 이동 ======================
    def _get_switcher_pile(self):
        try:
            frame: urwid.Frame = self.loop.widget
            footer_pile = frame.footer if isinstance(frame.footer, urwid.Pile) else None
            if not footer_pile: return None
            switcher = footer_pile.contents[0][0]          # ('fixed', 4, LineBox)
            inner = switcher.original_widget if isinstance(switcher, urwid.LineBox) else switcher  # Pile([row1,row2])
            return inner if isinstance(inner, urwid.Pile) else None
        except Exception:
            return None

    def _tab_switcher_next(self):
        pile = self._get_switcher_pile()
        if not pile: return
        r = pile.focus_position  # 0 or 1
        row = pile.contents[r][0]
        if isinstance(row, urwid.Columns):
            idx = self._current_col_index(row)
            nxt = self._next_selectable_index(row, idx)
            if nxt is not None:
                row.focus_position = nxt
                return
            # 행 끝 → 다음 행 첫 칸
            r_next = (r + 1) % len(pile.contents)
            pile.focus_position = r_next
            next_row = pile.contents[r_next][0]
            if isinstance(next_row, urwid.Columns):
                f = self._first_selectable_index(next_row)
                if f is not None:
                    next_row.focus_position = f

    def _tab_switcher_prev(self):
        pile = self._get_switcher_pile()
        if not pile: return
        r = pile.focus_position
        row = pile.contents[r][0]
        if isinstance(row, urwid.Columns):
            idx = self._current_col_index(row)
            prv = self._prev_selectable_index(row, idx)
            if prv is not None:
                row.focus_position = prv
                return
            # 행 처음 → 이전 행 마지막 칸
            r_prev = (r - 1) % len(pile.contents)
            pile.focus_position = r_prev
            prev_row = pile.contents[r_prev][0]
            if isinstance(prev_row, urwid.Columns):
                l = self._last_selectable_index(prev_row)
                if l is not None:
                    prev_row.focus_position = l

    def _on_key(self, key):
        """
        탭/시프트탭 + Ctrl/Alt/Shift+위·아래 + PageUp/Down + F6 + Ctrl+J/K.
        마우스 이벤트(tuple)는 무시.
        """
        # 0) 마우스/비문자 입력(urwid는 mouse press 등을 tuple로 전달) → 무시
        if not isinstance(key, str):
            return
        k = key.lower().strip()

        try:
            frame: urwid.Frame = self.loop.widget
            part = frame.focus_part  # 'header' | 'body' | 'footer'
        except Exception:
            part = None

        # 영역 순환 유틸
        def to_next_region():
            if part == 'header':
                self._focus_body_first()
            elif part == 'body':
                self._focus_footer()
            else:
                self._focus_header()

        def to_prev_region():
            if part == 'footer':
                self._focus_body_first()
            elif part == 'body':
                self._focus_header()
            else:
                self._focus_footer()

        # 1) 영역 전환
        next_keys = {'ctrl down', 'meta down', 'shift down', 'page down', 'ctrl j', 'f6'}
        prev_keys = {'ctrl up',   'meta up',   'shift up',   'page up',   'ctrl k'}
        if k in next_keys:
            to_next_region()
            return True
        if k in prev_keys:
            to_prev_region()
            return True

        # 2) Tab / Shift+Tab: 포커스 영역별 내부 이동 (처리 시 True 반환)
        if k in {'tab', '\t'}:
            if part == 'header':
                self._tab_header_next()
                return True
            if part == 'body':
                self._tab_body_next()
                return True
            if part == 'footer':
                if self._get_switcher_pile():
                    self._tab_switcher_next()
                    return True
            return None  # footer에 switcher 없음 등 → 기본 처리 허용

        if k in {'shift tab', 'backtab'}:
            if part == 'header':
                self._tab_header_prev()
                return True
            if part == 'body':
                self._tab_body_prev()
                return True
            if part == 'footer':
                if self._get_switcher_pile():
                    self._tab_switcher_prev()
                    return True
            return None

        # 그 외는 urwid 기본 동작에 맡김
        return None
    
    def _supports_vt(self) -> bool:
        """
        Windows에서 VT(ANSI) 입력/출력 지원을 최대한 보수적이되 실용적으로 감지.
        - 환경변수 오버라이드(PDEX_FORCE_MOUSE / PDEX_DISABLE_MOUSE)
        - VS Code / Windows Terminal / ConEmu / ANSICON / TERM=xterm-*
        - 기본적으로 Linux/WSL/macOS는 True
        """
        # 환경변수 오버라이드
        if os.environ.get("PDEX_DISABLE_MOUSE") == "1":
            return False
        if os.environ.get("PDEX_FORCE_MOUSE") == "1":
            return True

        if os.name != "nt":
            return True  # 비 Windows는 기본 OK

        env = os.environ
        # Windows Terminal
        if env.get("WT_SESSION"):
            return True
        # VS Code(내장 터미널)
        if env.get("TERM_PROGRAM") == "vscode" or env.get("VSCODE_PID"):
            return True
        # ConEmu/ANSICON(ANSI on)
        if env.get("ConEmuANSI") == "ON" or env.get("ANSICON"):
            return True
        # msys/git bash 등 xterm 류
        term = (env.get("TERM") or "").lower()
        if term.startswith("xterm") or "vt100" in term:
            return True

        return False
    
    async def _kill_ccxt_throttlers(self):
        """
        ccxt async_support가 띄운 Throttler.looper 태스크를 강제로 정리.
        close_all() 이후에도 간헐적으로 남는 경우가 있어 전수 검사해 취소/대기합니다.
        """
        try:
            current = asyncio.current_task()
        except Exception:
            current = None

        # 현재 루프의 모든 태스크 중에서 Throttler.looper만 추려서 취소
        throttlers = []
        for t in asyncio.all_tasks():
            if t is current:
                continue
            try:
                cr = t.get_coro()
                qn = getattr(cr, "__qualname__", "")
                rn = repr(cr)
                if "Throttler.looper" in qn or "Throttler.looper" in rn:
                    if not t.done():
                        try:
                            t.cancel()
                        except Exception:
                            pass
                        throttlers.append(t)
            except Exception:
                continue

        if throttlers:
            try:
                await asyncio.gather(*throttlers, return_exceptions=True)
            except Exception:
                pass

        # 한 틱 흘려보내기(취소 전파)
        await asyncio.sleep(0)
        
    async def _shutdown_tasks(self):
        """백그라운드 태스크를 모두 정리(cancel & await)해 'pending task' 경고 제거."""
        # (1) 반복/번 태스크 중단 신호
        self.repeat_cancel.set()
        self.burn_cancel.set()

        # (2) 실행 중 태스크 목록 수집
        tasks: list[asyncio.Task] = []

        if self.repeat_task and not self.repeat_task.done():
            tasks.append(self.repeat_task)
        if self.burn_task and not self.burn_task.done():
            tasks.append(self.burn_task)

        # 상태 루프들
        for name, t in list(self._status_tasks.items()):
            if t and not t.done():
                t.cancel()
                tasks.append(t)
        self._status_tasks.clear()

        # 가격 루프
        if self._price_task and not self._price_task.done():
            self._price_task.cancel()
            tasks.append(self._price_task)
        self._price_task = None

        # (3) 실제 취소 대기 (CancelledError 억제)
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass

        # (4) 추가로 Manager도 닫기(이미 run() finally에서 호출해도 좋음)
        try:
            await self.mgr.close_all()
        except Exception:
            pass
        
        # (4) 한 틱 흘려보내고, ccxt Throttler.looper를 한 번 더 강제 수거
        await asyncio.sleep(0)
        try:
            await self._kill_ccxt_throttlers()
        except Exception:
            pass

        # (6) 남은 모든 태스크(특히 ccxt Throttler)를 전수 cancel+await
        try:
            current = asyncio.current_task()
        except Exception:
            current = None

        pending = [t for t in asyncio.all_tasks() if t is not current]
        if pending:
            for t in pending:
                try:
                    t.cancel()
                except Exception:
                    pass
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass

    # --------- 실행/루프 ----------
    def run(self):
        if os.name == 'nt':
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            except Exception:
                pass

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        event_loop = urwid.AsyncioEventLoop(loop=loop)

        # VT 모드 활성 시도 (Windows)
        self._enable_win_vt()

        palette = [
            ("label",       "light cyan",     ""),
            ("info",        "light gray",     ""),
            ("title",       "light magenta",  ""),
            ("sep",         "dark gray",      ""),

            ("edit",        "white",          ""),
            ("edit_focus",  "black",          "light gray"),

            ("btn",         "black",          "light gray"),
            ("btn_reverse", "white",          ""),
            ("btn_focus",   "black",          "light blue"),
            ("btn_warn",    "black",          "yellow"),
            ("btn_type",    "black",          "dark cyan"),
            ("btn_exec",    "black",          "dark magenta"),

            ("btn_long",    "light green",    ""),
            ("btn_long_on", "black",          "light green"),
            ("btn_short",   "light red",      ""),
            ("btn_short_on","black",          "light red"),
            ("btn_off",     "yellow",         ""),
            ("btn_off_on",  "black",          "yellow"),

            ("long_col",    "light green",    ""),
            ("short_col",   "light red",      ""),
            ("pnl_pos",     "light green",    ""),
            ("pnl_neg",     "light red",      ""),
        ]

        root = self.build()

        handle_mouse = True
        if not self._supports_vt():
            handle_mouse = False

        self.loop = urwid.MainLoop(
            root, palette=palette,
            event_loop=event_loop,
            unhandled_input=self._on_key,
            handle_mouse=handle_mouse   # ← 여기서 제어
        )
        
        async def _bootstrap():
            try:
                await self.mgr.initialize_all()
            except Exception as e:
                logging.warning(f"initialize_all failed: {e}")

            # 가격/상태 주기 작업 시작 (표시 중인 거래소만 상태 루프)
            self._price_task = loop.create_task(self._price_loop())
            for n in self.mgr.visible_names():
                # 태스크 딕셔너리에 관리 (중복 방지)
                if n not in self._status_tasks or self._status_tasks[n].done():
                    self._status_tasks[n] = loop.create_task(self._status_loop(n))

            # All Qty → 각 카드 Q 동기화
            def allqty_changed(edit, new):
                for n in self.mgr.visible_names():
                    if n in self.qty_edit:
                        self.qty_edit[n].set_edit_text(new)
            urwid.connect_signal(self.allqty_edit, "change", allqty_changed)

            # Ticker 변경 즉시 반영
            def ticker_changed(edit, new):
                self.symbol = (new or "BTC").upper()

                # 직전 예약 취소(디바운스)
                try:
                    if self._ticker_lev_alarm:
                        self.loop.remove_alarm(self._ticker_lev_alarm)
                except Exception:
                    pass

                def _apply_max_lev(loop_, data):
                    try:
                        asyncio.get_event_loop().create_task(
                            self.service.ensure_hl_max_leverage_for_all(self.symbol)
                        )
                    except Exception as e:
                        logging.info(f"[LEVERAGE] ensure_hl_max_leverage_for_all failed: {e}")

                # 0.4초 뒤 한 번만 호출(빠른 타이핑 방지)
                self._ticker_lev_alarm = self.loop.set_alarm_in(0.4, _apply_max_lev)

            urwid.connect_signal(self.ticker_edit, "change", ticker_changed)

            self._request_redraw()

            try:
                await self.service.ensure_hl_max_leverage_for_all(self.symbol)
            except Exception as e:
                logging.info(f"[LEVERAGE] initial ensure max leverage skipped: {e}")

        loop.run_until_complete(_bootstrap())
        self.loop.set_alarm_in(0, self._set_initial_focus)

        try:
            with open(os.devnull, "w") as devnull, contextlib.redirect_stderr(devnull):
                self.loop.run()
        finally:
            # 마우스 트래킹/커서/색 복구
            try:
                # SGR mouse off, 커서 보이기, 스타일 리셋
                sys.stdout.write('\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?25h\x1b[0m')
                sys.stdout.flush()
                # Windows 콘솔 VT 모드 원복(실패해도 무시)
                if os.name == "nt":
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    STD_INPUT_HANDLE  = -10
                    STD_OUTPUT_HANDLE = -11
                    hIn = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                    hOut = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
                    mode = ctypes.c_uint()
            except Exception:
                pass
            
            # (A) 모든 백그라운드 태스크 정리(우리 태스크 + ccxt Throttler)
            try:
                loop.run_until_complete(self._shutdown_tasks())
            except Exception:
                pass
            
            # (B) async generator 정리
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
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
