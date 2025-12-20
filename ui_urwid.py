import asyncio
import random
import warnings
from typing import Dict, Optional, List
import math

import urwid
from urwid.widget.pile import PileWarning  # urwid 레이아웃 경고 제거용
from ui_scroll import ScrollBar, ScrollableListBox, hook_global_mouse_events
from ui_config import set_ui_type

from core import ExchangeManager
from trading_service import TradingService
import sys
import os
import contextlib
import re
import time
from types import SimpleNamespace
import logging
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)
def _ensure_ts_logger():
    """
    trading_service.py 전용 파일 핸들러 설정.
    - 기본 파일: ./ts.log (절대경로로 기록)
    - 기본 레벨: INFO
    - 기본 전파: False (루트 핸들러로 중복 기록 방지)
    환경변수:
      PDEX_TS_LOG_FILE=/path/to/ts.log
      PDEX_TS_LOG_LEVEL=DEBUG|INFO|...
      PDEX_TS_LOG_CONSOLE=0|1
      PDEX_TS_PROPAGATE=0|1
    """
    # 이미 붙어 있으면 중복 추가 금지
    if getattr(logger, "_ts_logger_attached", False):
        return

    lvl_name = os.getenv("PDEX_TS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, lvl_name, logging.INFO)
    log_file = "ui.log"
    to_console = os.getenv("PDEX_TS_LOG_CONSOLE", "0") == "1"
    propagate = os.getenv("PDEX_TS_PROPAGATE", "0") == "1"

    # 포맷
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    # 기존에 동일 파일 핸들러가 붙어 있으면 제거(핫리로드 대비)
    to_remove = []
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(getattr(h, "baseFilename", "")) == log_file:
                    to_remove.append(h)
            except Exception:
                pass
    for h in to_remove:
        logger.removeHandler(h)

    # 파일 핸들러
    fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.NOTSET)  # 핸들러는 모듈 로거 레벨만 따르도록
    logger.addHandler(fh)

    # 콘솔 핸들러(옵션)
    if to_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.NOTSET)
        logger.addHandler(sh)

    # 모듈 로거 레벨/전파 설정
    logger.setLevel(level)
    logger.propagate = propagate

    # 중복 방지 플래그
    logger._ts_logger_attached = True

    # 1회 안내 로그(최초 설정 확인용)
    logger.info("[TS-LOG] attached ts logger level=%s file=%s console=%s propagate=%s",
                lvl_name, log_file, to_console, propagate)

# 모듈 import 시점에 전용 핸들러를 붙인다.
_ensure_ts_logger()

CARD_HEIGHT = 5
LOGS_ROWS = 6
SWITCHER_ROWS = 5

class ExchangesGrid(urwid.WidgetWrap):
    """
    한 줄에 여러 개(그리드)로 Exchanges 체크박스를 배치하고,
    줄 수가 넘치면 내부 스크롤로 탐색하는 위젯.
    - per_row(한 줄 개수)는 렌더 시 size[0]에 따라 동적으로 계산
    - visible_rows(가시 줄 수)는 항목 수에 맞춰 자동 증가(최대 max_rows)
    """
    def __init__(self, items: list[tuple[str, bool]], on_toggle, *,
                 min_cell_w: int = 14,      # 셀 최소폭(라벨+여백). 너무 긴 이름은 clip.
                 gap: int = 2,              # 셀 사이 간격(Columns dividechars)
                 per_row_min: int = 2,
                 per_row_max: int = 5,
                 min_rows: int = 2,
                 max_rows: int = 10):
        self.items_meta = items[:]          # [(name, show)]
        self.on_toggle = on_toggle
        self.min_cell_w = max(10, int(min_cell_w))
        self.gap = max(0, int(gap))
        self.per_row_min = max(1, int(per_row_min))
        self.per_row_max = max(self.per_row_min, int(per_row_max))
        self.min_rows = max(1, int(min_rows))
        self.max_rows = max(self.min_rows, int(max_rows))

        # 체크박스 생성(+ 콜백 연결)
        self._checks: dict[str, urwid.CheckBox] = {}
        row_items = []
        for name, show in self.items_meta:
            cb = urwid.CheckBox(name.upper(), state=bool(show),
                                on_state_change=lambda c, st, n=name: self.on_toggle(n, st))
            # 포커스 색상
            row_items.append(urwid.AttrMap(cb, None, 'btn_focus'))
            self._checks[name] = cb

        # ListWalker + ScrollBar + ListBox
        self._walker = urwid.SimpleListWalker([])  # 행(Columns) 위젯들이 들어감
        self._scroll = ScrollBar(width=1)
        self._listbox = ScrollableListBox(self._walker, scrollbar=self._scroll, enable_selection=True, page_overlap=1)
        self._scroll.attach(self._listbox)

        # 상태
        self._row_items = row_items  # 셀 위젯들
        self._last_cols = None
        self.per_row = self.per_row_min
        self.rows_total = 1
        self.visible_rows = self.min_rows

        content = urwid.Columns([
            ('weight', 1, self._listbox),
            ('fixed', self._scroll.width, self._scroll),
        ], dividechars=0)
        super().__init__(urwid.LineBox(content, title="Exchanges"))

        # 최초 그리드 구성(대략적인 per_row 가정)
        self._rebuild_rows(terminal_cols=120)  # 임시 값, render에서 다시 계산됨

    def _compute_per_row(self, cols: int) -> int:
        # 안전한 per_row 계산: 셀 최소폭 + gap을 가정해 몇 개 들어가는지 산출
        # columns = cells*min_cell_w + (cells-1)*gap  → 근사 역산
        if cols <= 0:
            return self.per_row_min
        # 후보 max 개수
        max_fit = max(1, (cols + self.gap) // (self.min_cell_w + self.gap))
        return max(self.per_row_min, min(self.per_row_max, max_fit))

    def _rebuild_rows(self, terminal_cols: int):
        self.per_row = self._compute_per_row(terminal_cols)
        total = len(self._row_items)
        self.rows_total = max(1, math.ceil(total / self.per_row))
        # 표시할 줄 수: 항목 수에 따라 자동 증가(최대 self.max_rows)
        self.visible_rows = max(self.min_rows, min(self.max_rows, self.rows_total))

        rows = []
        # per_row 개씩 잘라 Columns로 묶음
        for r in range(self.rows_total):
            start = r * self.per_row
            chunk = self._row_items[start:start + self.per_row]
            # 개수가 부족하면 빈 칸 채우기(레이아웃 안정)
            if len(chunk) < self.per_row:
                chunk = chunk + [urwid.Text("")] * (self.per_row - len(chunk))
            row = urwid.Columns([('weight', 1, w) for w in chunk], dividechars=self.gap)
            rows.append(row)

        # Walker 교체
        self._walker[:] = rows

        # ScrollBar에 총 항목 수/현재 표시 줄 수를 반영(가상 모드 없이 단순 스크롤)
        # ScrollableListBox가 자체적으로 first/height를 반영해 스크롤 업데이트

    def render(self, size, focus=False):
        # 렌더 시점 크기 측정
        if isinstance(size, tuple) and len(size) >= 1:
            cols = int(size[0])
        else:
            cols = self._last_cols or 120
        if cols != self._last_cols:
            self._rebuild_rows(cols)
            self._last_cols = cols
        return super().render(size, focus)

    # 외부에서 현재 체크 상태를 읽고 싶을 때
    def get_states(self) -> dict[str, bool]:
        return {name: bool(cb.get_state()) for name, cb in self._checks.items()}

# Logs/Body 상호작용으로 '팔로우 모드'를 제어하기 위한 래퍼
class FollowableListBox(ScrollableListBox):
    def __init__(self, *args, role: str = "", app_ref=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._role = role  # 'logs' 또는 'body'
        if app_ref is not None:
            self.set_app_ref(app_ref)

    def mouse_event(self, size, event, button, col, row, focus):
        # Logs를 사용자가 조작하면 팔로우 중지
        if self._role == 'logs':
            if (event == 'mouse press' and button in (1, 4, 5)) or (event == 'mouse drag' and button == 1):
                try:
                    if self._app_ref is not None:
                        setattr(self._app_ref, "_logs_follow", False)
                except Exception:
                    pass

        # Body 클릭하면 팔로우 재개 + 최신으로 점프
        if self._role == 'body':
            if event == 'mouse press' and button in (1, 4, 5):
                if self._app_ref is not None and hasattr(self._app_ref, "logs_follow_latest"):
                    self._app_ref.logs_follow_latest(redraw=False)
            elif event == 'mouse drag' and button == 1:
                if self._app_ref is not None and hasattr(self._app_ref, "logs_follow_latest"):
                    self._app_ref.logs_follow_latest(redraw=False)
            elif event == 'mouse press' and button == 1:
                if self._app_ref is not None and hasattr(self._app_ref, "logs_follow_latest"):
                    self._app_ref.logs_follow_latest(redraw=False)

        return super().mouse_event(size, event, button, col, row, focus)

RATE = SimpleNamespace(
    GAP_FOR_INF=0.1, # need small gap for infinite loop
    # all for non hl
    STATUS_POS_INTERVAL={"default":0.5, "lighter":2.0},
    STATUS_COLLATERAL_INTERVAL={"default":0.5, "lighter":5.0},
    CARD_PRICE_INTERVAL={"default":1.0, "lighter":5.0},
)

# urwid의 레이아웃 경고(PileWarning)를 화면에 출력하지 않도록 억제
warnings.simplefilter("ignore", PileWarning)

def _normalize_symbol_input(sym: str) -> str:
        """
        사용자 입력 심볼 정규화:
        - HIP-3 'dex:coin' → 'dex_lower:COIN_UPPER' (입력은 보통 coin만 받지만, 방어)
        - 일반 HL        → 'SYMBOL_UPPER'
        """
        if not sym:
            return ""
        s = sym.strip()
        if ":" in s:
            _, coin = s.split(":", 1)
            return coin.upper()
        return s.upper()

def _compose_symbol(dex: str, coin: str) -> str:
    """
    dex가 'HL'이면 coin(upper)만, HIP-3이면 'dex:COIN'으로 합성.
    """
    coin_u = (coin or "").upper()
    if dex and dex != "HL":
        return f"{dex.lower()}:{coin_u}"
    return coin_u

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
    
    def mouse_event(self, size, event, button, col, row, focus):
        # size = (maxcol, maxrow)  row는 프레임 최상단 기준
        try:
            maxcol, maxrow = (size + (0, 0))[:2]
        except Exception:
            maxcol, maxrow = 0, 0

        # [핵심] logs 박스 영역인지 계산
        is_logs_area = False
        try:
            app = self.app_ref
            logs_rows     = LOGS_ROWS
            switcher_rows = SWITCHER_ROWS
            footer_rows   = logs_rows + switcher_rows

            # footer 전체의 시작 row (프레임 하단에서 위로 footer_rows)
            # row 가 footer 안이고, 그중 'logs' 영역이면 예외
            if footer_rows > 0 and maxrow > 0:
                in_footer = (row >= maxrow - footer_rows)
                if in_footer:
                    # footer 내부에서 logs 박스의 시작 기준
                    # footer 구조: [switcher (위)] [logs (아래)]
                    footer_row = row - (maxrow - footer_rows)
                    # logs 박스는 footer 내부의 하단 영역
                    if footer_row >= switcher_rows:
                        is_logs_area = True
        except Exception:
            is_logs_area = False

        # logs 영역이 아니고, 마우스 이벤트(press/drag/release)면 → 최신으로 강제
        if not is_logs_area and event.startswith("mouse"):
            try:
                if self.app_ref and hasattr(self.app_ref, "logs_follow_latest"):
                    # 즉시 최신으로 (원하시면 redraw=False로 바꿔도 됨)
                    self.app_ref.logs_follow_latest(redraw=True)
            except Exception:
                pass

        # 원래 이벤트 처리
        return super().mouse_event(size, event, button, col, row, focus)

class UrwidApp:
    def __init__(self, manager: ExchangeManager):
        set_ui_type("urwid")
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
        self.body_scroll: ScrollBar | None = None   # [ADD]
        self.footer = None
        self.log_scroll: ScrollBar | None = None    # [ADD]

        self._dragging_scrollbar = None     # [추가] 전역 드래그 중인 스크롤바
        self._pending_logs: list[str] = []  # [추가] 드래그 중 로그 버퍼
        self._logs_follow = True         # 기본은 최신 로그 자동 팔로우

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
        self.switcher_listbox: ScrollableListBox | None = None
        self.switcher_scroll: ScrollBar | None = None
        self._switcher_rows: int = 5  # footer에 넣을 'fixed' 높이(라인박스 테두리 

        # trading service
        self.service = TradingService(self.mgr)

        # 로그
        self.log_list = urwid.SimpleListWalker([])

        self.body_walker = None  # build()에서 생성

        # REPEAT/BURN 태스크
        self.repeat_task = None
        self.repeat_cancel = asyncio.Event()
        self.burn_task = None
        self.burn_cancel = asyncio.Event() 

        # 거래소별 status 루프 태스크 관리
        self._status_tasks: Dict[str, asyncio.Task] = {}
        self._price_task: asyncio.Task | None = None      # 가격 루프 태스크 보관
        
        self._last_balance_at: Dict[str, float] = {}  # [추가]
        self._last_pos_at: Dict[str, float] = {}       # [추가] 포지션 마지막 업데이트
        self.card_price_text: Dict[str, urwid.Text] = {}  # 거래소별 가격 라인 위젯
        self.card_quote_text: Dict[str, urwid.Text] = {}  # [추가] 거래소별 quote 텍스트 위젯
        self._last_card_price_at: Dict[str, float] = {} # 카드별 최근 가격 갱신 시각(스로틀링 용)
        self.card_last_price: Dict[str, float] = {} # 카드별 마지막 숫자 가격

        self._ticker_lev_alarm = None  # 디바운스 핸들

        self.symbol_by_ex: Dict[str, str] = {name: self.symbol for name in self.mgr.all_names()}  # 거래소별 심볼
        self.ticker_edit_by_ex: Dict[str, urwid.Edit] = {}                                        # 거래소별 Ticker 입력 위젯
        self._lev_alarm_by_ex: Dict[str, object] = {} 
        self._bulk_updating_tickers: bool = False

        self.dex_names: List[str] = ["HL"]                  # 헤더/카드에서 선택 가능한 dex 명단
        self.header_dex: str = "HL"                         # 헤더에서 선택된 dex
        self.dex_by_ex: Dict[str, str] = {n: "HL" for n in self.mgr.all_names()}  # 카드별 dex
        self.dex_btns_header: Dict[str, urwid.AttrMap] = {}                      # 헤더 버튼 래퍼
        self.dex_btns_by_ex: Dict[str, Dict[str, urwid.AttrMap]] = {}            # 카드별 dex 
        self._status_locks: Dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in self.mgr.all_names()}
        self.fee_text: Dict[str, urwid.Text] = {}  # [ADD] 카드별 FEE 라벨 위젯

        # [ADD] 거래소별 초기값(카드 입력값) 상태 저장용
        self.qty_by_ex: Dict[str, str] = {name: "" for name in self.mgr.all_names()}
        self._seeded_side_done: Dict[str, bool] = {name: False for name in self.mgr.all_names()}
        self.trade_type_by_ex: Dict[str, str] = {name: "perp" for name in self.mgr.all_names()}  # 아직 기능 X, 저장만

        # [ADD] meta.initial_setup를 상태 dict에 1회 반영
        self._seed_initial_setup_defaults()

    # [ADD] initial_setup 파싱/적용 유틸
    def _parse_initial_setup(self, raw, is_hl_like: bool) -> dict:
        """
        core.py가 meta['initial_setup']을 dict로 넣어준 케이스 + 문자열로 남겨둔 케이스 둘 다 처리.
        반환: {symbol, amount, trade_type, dex}
        - symbol: 카드 입력창에 들어갈 coin (HL-like면 'XYZ100' 형태)
        - dex: HL-like일 때만 의미(HL/XYZ/FLX...)
        """
        out = {"symbol": None, "amount": None, "side": None, "trade_type": None, "dex": None}

        if not raw:
            return out

        # 1) dict 형태면 그대로 흡수
        if isinstance(raw, dict):
            out["symbol"] = raw.get("symbol")
            out["amount"] = raw.get("amount")
            out["side"] = raw.get("side") or raw.get("long_short")
            out["trade_type"] = raw.get("trade_type") or raw.get("spot_or_perp") or raw.get("mode")
            out["dex"] = raw.get("dex")
        else:
            # 2) 문자열 "xyz:XYZ100, 0.0002, long, perp" 파싱
            try:
                parts = [p.strip() for p in str(raw).split(",")]
            except Exception:
                parts = []
            if len(parts) >= 1:
                out["symbol"] = parts[0]
            if len(parts) >= 2:
                out["amount"] = parts[1]
            if len(parts) >= 3:
                out["side"] = parts[2].lower()
            if len(parts) >= 4:
                out["trade_type"] = parts[3].lower()

        # 3) HL-like일 때 dex:coin 처리
        sym = (out["symbol"] or "").strip()
        if sym and ":" in sym:
            dex_part, coin_part = sym.split(":", 1)
            if is_hl_like:
                out["dex"] = (out["dex"] or dex_part).strip().upper()
                out["symbol"] = coin_part.strip().upper()
            else:
                out["symbol"] = coin_part.strip().upper()
                out["dex"] = None
        else:
            if sym:
                out["symbol"] = sym.strip().upper()

        # 4) trade_type 기본값
        if out["trade_type"]:
            out["trade_type"] = str(out["trade_type"]).strip().lower()
        else:
            out["trade_type"] = "perp"

        # 5) side 매핑: long/short/off → buy/sell/None
        s = (out["side"] or "").strip().lower()
        if s in ("long", "l", "buy"):
            out["side"] = "buy"
        elif s in ("short", "s", "sell"):
            out["side"] = "sell"
        elif s in ("off", "none", "", "null"):
            out["side"] = None
        else:
            # 알 수 없는 값이면 None(미선택)
            out["side"] = None

        # 6) dex 기본값
        if is_hl_like:
            out["dex"] = (out["dex"] or "HL").strip().upper()

        # amount는 string 그대로 저장
        if out["amount"] is not None:
            out["amount"] = str(out["amount"]).strip()

        return out

    def _seed_initial_setup_defaults(self):
        """
        meta['initial_setup']을 읽어, 카드 상태 dict(symbol_by_ex/dex_by_ex/qty_by_ex/trade_type_by_ex)에 초기값을 1회 주입.
        - 이미 사용자가 바꾼 값이 있을 수 있으니, '기본값 상태'일 때만 덮어쓰기.
        """
        for name in self.mgr.all_names():
            meta = self.mgr.get_meta(name) or {}
            raw = meta.get("initial_setup")
            if not raw:
                continue

            is_hl_like = self.mgr.is_hl_like(name)
            setup = self._parse_initial_setup(raw, is_hl_like=is_hl_like)

            # 심볼 초기값 주입: 현재가 기본 심볼(self.symbol)인 경우에만 덮어쓰기(사용자 변경 보호)
            if setup.get("symbol") and (self.symbol_by_ex.get(name) in (None, "", self.symbol)):
                self.symbol_by_ex[name] = setup["symbol"]

            # 수량 초기값 주입: 비어 있을 때만
            if setup.get("amount") and not (self.qty_by_ex.get(name) or "").strip():
                self.qty_by_ex[name] = setup["amount"]

            # dex 초기값 주입: HL-like일 때, 기본 HL일 때만 덮어쓰기
            if is_hl_like and setup.get("dex"):
                if (self.dex_by_ex.get(name) or "HL").upper() == "HL":
                    self.dex_by_ex[name] = setup["dex"]

            # trade_type 저장(아직 기능 X)
            if setup.get("trade_type"):
                self.trade_type_by_ex[name] = setup["trade_type"]
            
            if not self._seeded_side_done.get(name, False):
                if setup.get("side") in ("buy", "sell", None):
                    # 사용자가 아직 선택 안 했을 때만 초기 side 적용
                    if (not self.enabled.get(name, False)) and (self.side.get(name) is None):
                        if setup["side"] == "buy":
                            self.enabled[name] = True
                            self.side[name] = "buy"
                        elif setup["side"] == "sell":
                            self.enabled[name] = True
                            self.side[name] = "sell"
                        else:
                            # off/none
                            self.enabled[name] = False
                            self.side[name] = None

                    self._seeded_side_done[name] = True

    # [ADD] Logs 맨 아래로 안전하게 스크롤하는 헬퍼 (UI 루프에서 실행)
    def _scroll_logs_to_bottom(self, redraw=True):
        # comment: UI 루프에서 set_focus가 실행되도록 알람으로 예약
        def _do_scroll(loop, data):
            try:
                total = len(self.log_list)
                if total > 0:
                    # comment: 실제 ListBox에 포커스를 이동
                    self.log_listbox.set_focus(total - 1, coming_from='below')
            except Exception:
                pass
            if redraw:
                self._request_redraw()
        try:
            # 즉시가 아닌 다음 틱에 실행 → 렌더 경합/비동기 갱신 충돌 방지
            self.loop.set_alarm_in(0, _do_scroll)
        except Exception:
            # loop 초기화 전이라면 직접 시도 (예외는 조용히 무시)
            _do_scroll(None, None)

    def _update_card_fee(self, name: str):
        """
        HL-like 카드에서 현재 DEX/주문타입에 맞는 feeInt를 표시.
        비‑HL은 표기하지 않음.
        """

        try:
            if not self.mgr.is_hl_like(name):
                # 비‑HL은 FEE 위젯이 없거나 무의미 → 무시
                return
            dex = self.dex_by_ex.get(name, "HL")
            dex_key = None if dex == "HL" else dex.lower()
            order_type = (self.order_type.get(name) or "market").lower()
            fee = self.service.get_display_builder_fee(name, dex_key, order_type)
            lbl = f"Builder Fee: {fee}" if isinstance(fee, int) else "Builder Fee: -"
            w = self.fee_text.get(name)
            if w:
                w.set_text(("label", lbl))
        except Exception:
            # 조용히 무시
            pass

    # [ADD] 브래킷 마크업 파서(urwid용)
    def _parse_bracket_markup(self, s: str) -> list[tuple[Optional[str], str]]:
        """
        '[red]PERP[/] 123 | [cyan]SPOT[/] ...' 형태의 문자열을
        urwid Text.set_text가 받는 (attr, text) 튜플 리스트로 변환합니다.
        지원 태그: [red], [green], [cyan], [/]
        색 매핑:
          - red   -> 'pnl_neg'   (팔레트: light red)
          - green -> 'pnl_pos'   (팔레트: light green)
          - cyan  -> 'label'     (팔레트: light cyan)
        """
        color_map = {
            "red": "pnl_neg",
            "green": "pnl_pos",
            "cyan": "label",
        }
        # 토큰으로 분할: [tag] / [/]
        tokens = re.split(r'(\[[a-zA-Z_]+\]|\[/\])', s)
        parts: list[tuple[Optional[str], str]] = []
        cur_attr: Optional[str] = None

        for tok in tokens:
            if not tok:
                continue
            if tok == "[/]":
                cur_attr = None
                continue
            m = re.fullmatch(r"\[([a-zA-Z_]+)\]", tok)
            if m:
                cur_attr = color_map.get(m.group(1).lower())
                continue
            # 일반 텍스트
            parts.append((cur_attr, tok))
        return parts

    def _status_bracket_to_urwid(self, pos_str: str, col_str: str):
        """
        trading_service.fetch_status가 주는 문자열을 urwid 마크업 리스트로 변환.
        - pos_str: 첫 번째 [green]/[red] 블록은 LONG/SHORT 색(long_col/short_col),
                   두 번째 [green]/[red] 블록은 PnL 색(pnl_pos/pnl_neg)로 처리(기존 동작 유지)
        - col_str: [red] / [cyan] 등 마크업을 실제 색으로 파싱(신규)
        """
        # 1) pos_str: 기존 규칙 유지(1번째=side, 2번째=PNL)
        tokens = re.split(r'(\[green\]|\[red\]|\[/\])', pos_str)
        pos_parts: list[tuple[Optional[str], str]] = []
        attr = None
        seen_colored_blocks = 0

        for tok in tokens:
            if tok in ('[green]', '[red]'):
                seen_colored_blocks += 1
                if seen_colored_blocks == 1:
                    attr = 'long_col' if tok == '[green]' else 'short_col'
                else:
                    attr = 'pnl_pos' if tok == '[green]' else 'pnl_neg'
            elif tok == '[/]':
                attr = None
            elif tok:
                pos_parts.append((attr, tok))

        # 2) col_str: 색 마크업 파싱(신규)
        col_parts = self._parse_bracket_markup(col_str)

        # 3) 결합: ' | ' 구분자 뒤에 collateral 파트 연결
        return pos_parts + [(None, "\n")] + col_parts

    def _inject_usdc_value_into_pos(self, ex_name: str, pos_str: str) -> str:
        """
        pos_str 예: '📊 [green]LONG[/] 0.12345 | PnL: [red]-1.23[/]'
        → '📊 [green]LONG[/] 0.12345 (3,456.78 USDC) | PnL: [red]-1.23[/]'
        카드별 최신 가격(self.card_last_price[ex_name])이 있을 때만 주입.
        """
        price = self.card_last_price.get(ex_name)
        if price is None:
            return pos_str  # 가격이 아직 없으면 원문 유지

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
        injected = f"{size_str} ({usdc_value:,.1f} USDC)"

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
    

    def _build_header_dex_row(self) -> urwid.Widget:
        """
        헤더용 HIP3-DEX 선택 행을 GridFlow 형태로 구성.
        self.dex_names에 있는 dex들을 버튼화하고, 선택된 dex는 btn_dex_on으로 표시.
        """
        buttons = []
        self.dex_btns_header.clear()

        # 'HL' + 나머지 dex들
        for dex in self.dex_names:
            label = dex.upper() if dex != "HL" else "HL"
            b = urwid.Button(label)
            def on_sel(btn, d=dex):
                self._on_header_dex_select(d)
            urwid.connect_signal(b, "click", on_sel)
            wrap = urwid.AttrMap(b, "btn_dex_on" if dex == self.header_dex else "btn_dex", "btn_focus")
            self.dex_btns_header[dex] = wrap
            buttons.append(('given', max(6, len(label)+4), wrap))  # 고정 폭

        row = urwid.Columns(buttons, dividechars=1)
        return urwid.Columns([(12, urwid.Text(("label", "HIP3-DEX:"))), row], dividechars=1)

    def _on_header_dex_select(self, dex: str):
        """
        헤더에서 dex 하나를 선택 → 전체 카드에 dex 일괄 적용 + 버튼 스타일 동기화.
        """
        self.header_dex = dex
        # 헤더 버튼 스타일 반영
        for d, w in self.dex_btns_header.items():
            w.set_attr_map({None: "btn_dex_on" if d == dex else "btn_dex"})
        # 모든 카드 dex 동기화
        self._bulk_updating_tickers = True
        try:
            for n in self.mgr.all_names():
                self.dex_by_ex[n] = dex
            # 화면에 보이는 카드 버튼 스타일 갱신
            for n in self.mgr.visible_names():
                self._update_card_dex_styles(n)
                self._update_card_fee(n)
        finally:
            self._bulk_updating_tickers = False

    def _update_card_dex_styles(self, name: str):
        """
        카드의 dex 버튼 스타일을 현재 self.dex_by_ex[name]에 맞게 갱신.
        """
        cur = self.dex_by_ex.get(name, "HL")
        row_btns = self.dex_btns_by_ex.get(name, {})
        for d, w in row_btns.items():
            w.set_attr_map({None: "btn_dex_on" if d == cur else "btn_dex"})

    def _build_card_dex_row(self, name: str) -> urwid.Widget:
        """
        카드 한 장의 HIP3-DEX 선택 행.
        """
        row_btns: Dict[str, urwid.AttrMap] = {}
        buttons = []
        cur = self.dex_by_ex.get(name, "HL")

        for dex in self.dex_names:
            label = dex.upper() if dex != "HL" else "HL"
            b = urwid.Button(label)
            def on_sel(btn, d=dex, ex_name=name):
                self._on_card_dex_select(ex_name, d)
            urwid.connect_signal(b, "click", on_sel)
            wrap = urwid.AttrMap(b, "btn_dex_on" if dex == cur else "btn_dex", "btn_focus")
            row_btns[dex] = wrap
            buttons.append(('given', max(6, len(label)+4), wrap))

        self.dex_btns_by_ex[name] = row_btns
        dex_row = urwid.Columns(buttons, dividechars=1)

        # [ADD] 우측 FEE 라벨
        fee_label = urwid.Text(("label", "Builder Fee: -"))
        self.fee_text[name] = fee_label

        # DEX 행을 왼쪽 가변폭으로, FEE 라벨은 오른쪽 고정 폭으로 배치
        return urwid.Columns(
            [
                ('weight', 1, urwid.Padding(dex_row, left=0, right=1)),
                ('weight', 1,    urwid.Padding(fee_label, left=0)),
            ],
            dividechars=1
        )

    def _on_card_dex_select(self, name: str, dex: str):
        """
        해당 카드만 dex 설정을 변경.
        """
        self.dex_by_ex[name] = dex
        self._update_card_dex_styles(name)
        self._update_card_fee(name)

    def _request_redraw(self):
        """다음 틱에 화면을 다시 그리도록 스케줄"""
        if self.loop:
            try:
                self.loop.set_alarm_in(0, lambda loop, data: None)
            except Exception:
                pass

    def logs_follow_latest(self, redraw=True):
        self._logs_follow = True
        # comment: at_bottom 여부와 상관없이 무조건 최신으로 이동
        self._scroll_logs_to_bottom(redraw=redraw)

    def _log(self, msg: str):
        # 드래그 중이면 버퍼에 쌓기(기존)
        if self._dragging_scrollbar == self.log_scroll:
            self._pending_logs.append(msg)
            return

        if self._pending_logs:
            for pending in self._pending_logs:
                self.log_list.append(urwid.Text(pending))
            self._pending_logs.clear()

        self.log_list.append(urwid.Text(msg))

        # 그 외에는 플래그에 따름
        if self._logs_follow:
            self._scroll_logs_to_bottom(redraw=True)
        else:
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

        # 2.5행 HIP3‑DEX (처음엔 HL만, _bootstrap에서 갱신)
        self.header_dex_row = self._build_header_dex_row()

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
        return urwid.Pile([row1, row2, self.header_dex_row, row3, row4])

    # --------- 거래소 카드 ----------
    def _row(self, name: str):
        meta = self.mgr.get_meta(name) or {}

        # [ADD] 안전망: __init__에서 시딩을 했어도, 혹시 누락되면 여기서 한 번 더 시딩
        try:
            if meta.get("initial_setup"):
                is_hl_like = self.mgr.is_hl_like(name)
                setup = self._parse_initial_setup(meta.get("initial_setup"), is_hl_like=is_hl_like)
                if setup.get("symbol") and (self.symbol_by_ex.get(name) in (None, "", self.symbol)):
                    self.symbol_by_ex[name] = setup["symbol"]
                if setup.get("amount") and not (self.qty_by_ex.get(name) or "").strip():
                    self.qty_by_ex[name] = setup["amount"]
                if is_hl_like and setup.get("dex") and (self.dex_by_ex.get(name) or "HL").upper() == "HL":
                    self.dex_by_ex[name] = setup["dex"]
                if setup.get("trade_type"):
                    self.trade_type_by_ex[name] = setup["trade_type"]
        except Exception:
            pass

        # [CHG] 상태 dict 기반으로 기본 텍스트를 넣기
        init_ticker = (self.symbol_by_ex.get(name) or self.symbol or "BTC")
        init_qty = (self.qty_by_ex.get(name) or "")

        qty = urwid.AttrMap(urwid.Edit(("label", "Q:"), init_qty), "edit", "edit_focus")
        price = urwid.AttrMap(urwid.Edit(("label", "P:"), ""), "edit", "edit_focus")
        t_edit = urwid.AttrMap(urwid.Edit(("label", "T:"), init_ticker), "edit", "edit_focus")

        self.qty_edit[name] = qty.base_widget
        self.price_edit[name] = price.base_widget
        self.ticker_edit_by_ex[name] = t_edit.base_widget

        # [ADD] qty 변경 시 상태 dict도 업데이트(재빌드 시 값 유지에 도움)
        def on_qty_changed(edit, new, n=name):
            self.qty_by_ex[n] = (new or "").strip()
        urwid.connect_signal(qty.base_widget, "change", on_qty_changed)

        def on_ticker_changed(edit, new, n=name):
            # 대문자로 정규화하여 저장
            coin = _normalize_symbol_input(new or self.symbol)
            self.symbol_by_ex[n] = coin

            # [추가] 헤더에서 일괄 동기화 중에는 per‑card 레버리지 예약을 건너뜁니다.
            if self._bulk_updating_tickers:
                return
            
            dex = self.dex_by_ex.get(n, "HL")
            sym = _compose_symbol(dex, coin)

            try:
                if self._lev_alarm_by_ex.get(n):
                    self.loop.remove_alarm(self._lev_alarm_by_ex[n])
            except Exception:
                pass

        urwid.connect_signal(t_edit.base_widget, "change", on_ticker_changed)

        # 타입 토글
        def on_type(btn, n=name):
            self.order_type[n] = "limit" if self.order_type[n] == "market" else "market"
            self._refresh_type_label(n)
            self._update_card_fee(n)
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

        card_dex_row = self._build_card_dex_row(name)  # NEW
        controls = urwid.Columns(
            [
                (14, urwid.Text(("title", f"[{name.upper()}]"))),
                (10, t_edit),          # ← NEW: 거래소별 Ticker
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
        is_hl_like = self.mgr.is_hl_like(name)
        
        price_line = urwid.Text(("info", "Price: ..."))
        self.card_price_text[name] = price_line

        if is_hl_like:
            quote_line = urwid.Text(("quote_color", "")) # 초기값은 비워둠
            self.card_quote_text[name] = quote_line
            price_and_dex = urwid.Columns(
                [
                    ('pack', price_line),                    # Price: 25,180.00 형태 길이만 차지
                    ('pack', urwid.Padding(quote_line, left=0, right=1)), # quote_line을 오른쪽에 붙이고, 좌우에 1칸씩 패딩을 줍니다.
                    ('weight', 1, urwid.Padding(card_dex_row, left=1)),  # DEX 행이 남은 폭 전체
                ],
                dividechars=1,
            )
            card = urwid.Pile([controls, price_and_dex, info])
        else:
            card = urwid.Pile([controls, price_line, info])

        # 초기 FEE 표기 1회 갱신(해당 카드가 HL-like일 경우)
        if is_hl_like:
            self._update_card_fee(name)

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
        Exchanges 토글 박스(그리드 + 스크롤).
        - 한 줄에 여러 개(per_row는 렌더 시 동적 계산)
        - 줄 수는 항목 수에 따라 자동 증가(최대 max_rows)
        """
        names = self.mgr.all_names()
        if not names:
            grid = urwid.LineBox(urwid.Text("no exchanges"), title="Exchanges")
            self._switcher_rows = 3
            return grid

        items = []
        for name in names:
            show = bool(self.mgr.get_meta(name).get("show", False))
            items.append((name, show))

        # 그리드 생성(콜백: 기존 토글 핸들러 재사용)
        grid = ExchangesGrid(
            items,
            on_toggle=lambda n, st: self._on_toggle_show(self.switch_checks.get(n, None) or urwid.CheckBox("", state=st), st),
            min_cell_w=15, gap=1, per_row_min=2, per_row_max=6, min_rows=2, max_rows=10
        )

        # 체크박스 인스턴스 매핑(토글 콜백에서 상태 반영 필요하면 사용)
        self.switch_checks = {}
        for name, _ in items:
            # ExchangesGrid 내부 체크박스 접근은 private이라 여기선 더미 매핑 유지(필요 시 grid.get_states 사용)
            self.switch_checks[name] = urwid.CheckBox(name, state=self.mgr.get_meta(name).get("show", False))

        # footer 고정 높이: visible_rows + LineBox 테두리(2)
        self._switcher_rows = grid.visible_rows + 2
        return grid

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
            #if i != len(visible) - 1:
            rows.append(urwid.Text(("sep", "─" * 88)))

        if self.body_walker is not None:
            self.body_walker.clear()
            self.body_walker.extend(rows)
            try:
                if len(self.body_walker) > 0:
                    self.body_list.set_focus(0)
            except Exception:
                pass

        # 대신 가볍게 다시 그리기만 예약
        self._request_redraw()

    # --------- 화면 구성 ----------
    def build(self):
        self.header = self._hdr_widgets()

        # 1) 본문(카드) rows 구성
        rows = []
        visible = self.mgr.visible_names()
        for i, n in enumerate(visible):
            rows.append(self._row(n))
            #if i != len(visible) - 1:
            divider = urwid.Text(("sep", "─" * 88))
            rows.append(divider)

        # [FIX] 카드: '하드코딩 5줄' + '카드(Pile)만 아이템' 모드 켜기
        self.body_walker = urwid.SimpleListWalker(rows)
        self.body_scroll = ScrollBar(width=1)
        self.body_list = ScrollableListBox(
            self.body_walker,
            scrollbar=self.body_scroll,
            enable_selection=True,
            page_overlap=1,
            use_visual_total=True,
            fixed_lines_per_item=CARD_HEIGHT,
            count_only_pile_as_item=True
        )
        self.body_scroll.attach(self.body_list)
        self.body_list.set_app_ref(self)
        self.body_list.set_selection_lock(True)
        setattr(self.body_list, "_role", "cards")  # [DBG] 태그
        
        body_with_scroll = urwid.Columns(
            [
                ('weight', 1, self.body_list),   # ← 원본 ListBox
                ('fixed', self.body_scroll.width, self.body_scroll),  # ← 원본 ScrollBar
            ],
            dividechars=0
        )

        # 2) Logs (아이템 개수 기반 유지)
        self.log_scroll = ScrollBar(width=1)  # 테스트와 동일 폭 1
        self.log_listbox = FollowableListBox(   # ← FollowableListBox 사용
            self.log_list,
            scrollbar=self.log_scroll,
            enable_selection=False,
            page_overlap=1,
            role='logs',
            app_ref=self
        )
        self.log_scroll.attach(self.log_listbox)
        logs_columns = urwid.Columns(
            [
                ('weight', 1, self.log_listbox),
                ('fixed', self.log_scroll.width, self.log_scroll)
            ],
            dividechars=0
        )
        logs_frame = urwid.LineBox(logs_columns, title="Logs")  # [FIX] LineBox는 한 번만

        # [선택] 기존과 같은 4줄 표시를 원하시면 'fixed, 6'로 넣으십시오(테두리 2줄 포함)
        # footer 구성은 기존 구조를 따르되 logs_frame만 넣도록 변경
        switcher = self._build_switcher()
        self.footer = urwid.Pile([
            ('fixed', SWITCHER_ROWS, switcher),
            ('fixed', LOGS_ROWS, logs_frame),  # 내부 표시 4줄(6 - 테두리 2)
        ])

        # 본문은 기존 body_with_scroll 사용
        frame = CustomFrame(
            header=urwid.LineBox(self.header),
            body=body_with_scroll,
            footer=self.footer,
            app_ref=self
        )
        return frame

    # --------- 주기 작업 ----------
    async def _price_loop(self):
        while True:
            try:
                self.symbol = (self.ticker_edit.edit_text or "BTC").upper()
                raw = self.ticker_edit.edit_text or "BTC"
                coin = _normalize_symbol_input(raw)

                px_str = self.current_price or "..."
                dex = self.header_dex
                #scope = "hl" if dex == "HL" else dex
                
                # HL 우선 선택(없으면 가시 HL로 폴백)
                ex = self.mgr.first_hl_exchange()
                if not ex:
                    try:
                        for nm in self.mgr.visible_names():
                            if self.mgr.get_meta(nm).get("hl", False) and self.mgr.get_exchange(nm):
                                ex = self.mgr.get_exchange(nm)
                                break
                    except Exception:
                        ex = None

                if ex:
                    # HL: 키 생성
                    sym = _compose_symbol(dex, coin)  # HL → 'BTC', HIP-3 → 'dex:COIN'
                    px_val = await ex.get_mark_price(sym)
                    if px_val is not None:
                        px_str = self.service.format_price_simple(float(px_val))
                
                self.current_price = px_str
                self.price_text.set_text(("info", f"Price: {self.current_price}"))
                self.total_text.set_text(("info", f"Total: {self._collateral_sum():,.1f} USDC"))
                self._request_redraw()

                await asyncio.sleep(RATE.GAP_FOR_INF)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"price loop: {e}")
                await asyncio.sleep(RATE.GAP_FOR_INF)

    async def _status_loop(self, name: str):
        if not hasattr(self, '_debug_logged'):
            self._debug_logged = True
            try:
                h = self.body_list._last_h
                total = len(self.body_walker)
                logger.info(f"[DEBUG] Screen height: {h}, Total items: {total}, Needs scroll: {total > h}")
            except Exception as e:
                logger.error(f"[DEBUG] Failed to get render info: {e}")

        await asyncio.sleep(random.uniform(0.0, 0.7))

        lock = self._status_locks.get(name)
        if not lock:
            return

        while True:
            await asyncio.sleep(RATE.GAP_FOR_INF)
            try:
                await lock.acquire()

                now = time.monotonic()
                exchange_platform = self.mgr.get_meta(name).get("exchange", "hyperliquid")
                try:
                    STATUS_COLLATERAL_INTERVAL = RATE.STATUS_COLLATERAL_INTERVAL[exchange_platform]
                    STATUS_POS_INTERVAL = RATE.STATUS_POS_INTERVAL[exchange_platform]
                    CARD_PRICE_INTERVAL = RATE.CARD_PRICE_INTERVAL[exchange_platform]
                except Exception:
                    STATUS_COLLATERAL_INTERVAL = RATE.STATUS_COLLATERAL_INTERVAL["default"]
                    STATUS_POS_INTERVAL = RATE.STATUS_POS_INTERVAL["default"]
                    CARD_PRICE_INTERVAL = RATE.CARD_PRICE_INTERVAL["default"]
                
                need_collat = (now - self._last_balance_at.get(name, 0.0) >= STATUS_COLLATERAL_INTERVAL)
                need_pos = (now - self._last_pos_at.get(name, 0.0) >= STATUS_POS_INTERVAL)
                need_price  = (now - self._last_card_price_at.get(name, 0.0) >= CARD_PRICE_INTERVAL)

                sym_coin = _normalize_symbol_input(self.symbol_by_ex.get(name) or self.symbol)
                dex = self.dex_by_ex.get(name, "HL")
                sym = _compose_symbol(dex, sym_coin)
                is_hl_like = self.mgr.is_hl_like(name)  # <-- 변경
                
                ex = self.mgr.get_exchange(name)
                is_ws = hasattr(ex,"fetch_by_ws") and getattr(ex, "fetch_by_ws",False)

                if need_price or is_ws:
                    try:
                        px_str = await self.service.fetch_price(name, sym)
                        self.card_price_text[name].set_text(("info", f"Price: {px_str}"))
                        # 주입용 숫자 캐시
                        try:
                            self.card_last_price[name] = float(str(px_str).replace(",", ""))
                        except Exception:
                            pass
                        self._last_card_price_at[name] = now  # [추가] 타임스탬프 갱신
                    except Exception as e:
                        logger.info(f"[UI] price update for {name} failed: {e}")
                        self.card_price_text[name].set_text(("pnl_neg", "Price: Error"))

                if is_hl_like:
                    # 여길 업데이트 해야함 how?
                    try:
                        if name in self.card_quote_text:
                            #logger.info(f"{name}")
                            #logger.info(f"{sym}")
                            quote_str = ex.get_perp_quote(sym)
                            #logger.info(f"{quote_str}")
                            self.card_quote_text[name].set_text(("quote_color", quote_str))
                            
                    except Exception as px_e:
                        logger.info(f"[UI] Price update for {name} failed: {px_e}")
                        self.card_price_text[name].set_text(("pnl_neg", "Price: Error???"))

                pos_str, col_str, col_val, _ = await self.service.fetch_status(name, sym, need_balance=need_collat, need_position=need_pos)

                if need_collat or is_ws:
                    self.collateral[name] = float(col_val)
                    self._last_balance_at[name] = now
                    self.total_text.set_text(("info", f"Total: {self._collateral_sum():,.1f} USDC"))
                
                if need_pos:
                    self._last_pos_at[name] = now

                pos_str = self._inject_usdc_value_into_pos(name, pos_str)

                if name in self.info_text:
                    markup_parts = self._status_bracket_to_urwid(pos_str, col_str)
                    self.info_text[name].set_text(markup_parts)
                
                self._request_redraw()

                await asyncio.sleep(RATE.GAP_FOR_INF)

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error(f"[CRITICAL] Unhandled error in status_loop for '{name}'", exc_info=True)
                if name in self.info_text:
                    self.info_text[name].set_text([('pnl_neg', "Status Error: Check logs")])
                    self._request_redraw()
                await asyncio.sleep(1.0) # 에러 시 잠시 대기

            finally:
                # [수정] 무조건 락 해제
                if lock.locked():
                    lock.release()
    
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

        max_retry = 5
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
                    # 시장가: 헤더 Price를 쓰지 않음 → 서비스가 심볼별로 안전하게 산출
                    price = None
                
                sym_coin = _normalize_symbol_input(self.symbol_by_ex.get(name) or self.symbol)
                dex = self.dex_by_ex.get(name, self.header_dex)
                sym = _compose_symbol(dex, sym_coin)

                # 로그도 실제 주문 심볼을 표시
                self._log(f"[{name.upper()}] {side.upper()} {amount} {sym} @ {otype}")

                order = await self.service.execute_order(
                    exchange_name=name,
                    symbol=sym,
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
                await asyncio.sleep(1.0)

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

                sym_coin = _normalize_symbol_input(self.symbol_by_ex.get(name) or self.symbol)
                dex = self.dex_by_ex.get(name, self.header_dex)
                sym = _compose_symbol(dex, sym_coin)
                order = await self.service.close_position(
                    exchange_name=name,
                    symbol=sym,
                    price_hint=None,
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
                    logger.error(f"Tab next finalize error: {e}")

            # 0.01초 후 finalize (위젯 렌더 완료 대기)
            self.loop.set_alarm_in(0.05, _finalize_focus_to_q)

        except Exception as e:
            logger.error(f"Tab next exception: {e}", exc_info=True)

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
            logger.info(f"Tab prev: moving from card {k} to card {k_prev}, row {row_prev}")

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
                    logger.error(f"Tab prev finalize error: {e}")

            # 0.01초 후 finalize (위젯 렌더 완료 대기)
            self.loop.set_alarm_in(0.05, _finalize_focus_to_ex)

        except Exception as e:
            logger.error(f"Tab prev exception: {e}", exc_info=True)

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

        if part in ('header', 'body'):
            # 너무 자주 그리진 않게 redraw=False
            self.logs_follow_latest(redraw=False)

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

    def _apply_to_all_qty(self, new_text):
        """
        헤더의 All Qty 입력칸이 변경될 때 모든 카드에 반영합니다.
        """
        for name in self.mgr.all_names():
            if name in self.qty_edit:
                self.qty_edit[name].set_edit_text(new_text or "")

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

            ("btn_dex",    "white",       ""),
            ("btn_dex_on", "black",       "light green"),
            
            ("quote_color", "light green",      "", "bold"),
            
            ("scroll_bar",   "dark gray",   ""),
            ("scroll_thumb", "light cyan",  ""),
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
        
        hook_global_mouse_events(self.loop, self)
        
        async def _bootstrap():
            try:
                await self.mgr.initialize_all()
            except Exception as e:
                logger.warning(f"initialize_all failed: {e}")
            
            # DEX 목록 가져와 헤더/카드 UI 동적 구성 (비동기)
            try:
                #dexs = await self.service.fetch_perp_dexs()
                first_hl = self.mgr.first_hl_exchange()
                dexs = [x.upper() for x in first_hl.dex_list]
                self.dex_names = dexs #["HL"] + dexs
                # Frame.header(LineBox)의 original_widget을 교체해야 실제로 헤더가 재그려집니다.
                # 기존 코드는 self.header(original_widget 아님)에 새 Pile을 할당해 효과가 없었습니다.
                new_header_pile = self._hdr_widgets()  # 새 헤더 Pile 생성
                frame = self.loop.widget
                if isinstance(frame, urwid.Frame):
                    lb = frame.header
                    if isinstance(lb, urwid.LineBox):
                        lb.original_widget = new_header_pile  # LineBox 내부 교체
                    else:
                        frame.header = urwid.LineBox(new_header_pile)
                # 내부 참조도 최신으로 갱신(신규 위젯 핸들 유지)
                self.header = new_header_pile

                # 바디 카드 재구성(카드의 DEX 버튼들도 새 목록 반영)
                self._rebuild_body_rows()
            except Exception as e:
                self._log(f"Error fetching DEX list: {e}")

            # 3) 보이는 카드 리스트 재구성 + 초기 포커스 설정
            self.loop.set_alarm_in(0.1, self._set_initial_focus)

            # 4) 가격/상태 주기 작업 시작
            self._price_task = asyncio.get_event_loop().create_task(self._price_loop())
            for n in self.mgr.visible_names():
                if self.mgr.is_hl_like(n):
                    self._update_card_fee(n)
                if n not in self._status_tasks or self._status_tasks[n].done():
                    self._status_tasks[n] = asyncio.get_event_loop().create_task(self._status_loop(n))
            
            # Ticker 변경 즉시 반영
            def ticker_changed(edit, new):
                coin = _normalize_symbol_input(new or "BTC")
                self.symbol = coin
                self._bulk_updating_tickers = True

                try:
                    # 모든 거래소(표시/비표시 포함)의 심볼 상태를 먼저 갱신
                    for ex_name in self.mgr.all_names():
                        self.symbol_by_ex[ex_name] = coin

                    # 화면에 보이는 카드의 T 입력칸 텍스트를 갱신 (체인지 시그널은 발생해도 레버리지 예약은 벌크 플래그로 억제됨)
                    for ex_name in self.mgr.visible_names():
                        try:
                            edit_w = self.ticker_edit_by_ex.get(ex_name)
                            if edit_w:
                                edit_w.set_edit_text(coin)
                        except Exception:
                            pass
                finally:
                    # 벌크 모드 해제
                    self._bulk_updating_tickers = False

                # 직전 예약 취소(디바운스)
                try:
                    if self._ticker_lev_alarm:
                        self.loop.remove_alarm(self._ticker_lev_alarm)
                except Exception:
                    pass

            urwid.connect_signal(self.ticker_edit, "change", ticker_changed)
            urwid.connect_signal(self.allqty_edit, 'change', lambda _, new: self._apply_to_all_qty(new))

            self._request_redraw()

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

