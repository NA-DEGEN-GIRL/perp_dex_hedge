#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qt 기반 UI (PySide6) 구현.

기존 `ui_urwid.UrwidApp` 을 대체하기 위한 GUI 버전입니다.
핵심 개념과 비즈니스 로직(ExchangeManager, TradingService 사용 방식)은
`ui_urwid.py` 를 최대한 그대로 따르되, TUI → GUI 로만 교체했습니다.

의존성:
    pip install PySide6 qasync
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, List

from logging.handlers import RotatingFileHandler

from PySide6 import QtCore, QtGui, QtWidgets
import qasync

from core import ExchangeManager
from trading_service import TradingService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 로깅 설정 (ui_urwid.py 의 _ensure_ts_logger 와 동일 패턴)
# ---------------------------------------------------------------------------

def _ensure_ts_logger() -> None:
    """
    UI 모듈 전용 파일 핸들러 설정.
    - 기본 파일: ./ui.log
    """
    if getattr(logger, "_ts_logger_attached", False):
        return

    lvl_name = os.getenv("PDEX_TS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, lvl_name, logging.INFO)
    log_file = "ui.log"
    to_console = os.getenv("PDEX_TS_LOG_CONSOLE", "0") == "1"
    propagate = os.getenv("PDEX_TS_PROPAGATE", "0") == "1"

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

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

    fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.NOTSET)
    logger.addHandler(fh)

    if to_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.NOTSET)
        logger.addHandler(sh)

    logger.setLevel(level)
    logger.propagate = propagate
    logger._ts_logger_attached = True
    logger.info("[UI-QT] attached ui logger level=%s file=%s console=%s propagate=%s",
                lvl_name, log_file, to_console, propagate)


_ensure_ts_logger()


# ---------------------------------------------------------------------------
# 공통 상수/유틸 (ui_urwid.py 에서 가져와 단순화)
# ---------------------------------------------------------------------------

CARD_HEIGHT = 5   # urwid 시절 카드 높이 개념 (Qt에선 참고용)
LOGS_ROWS = 6     # 레이아웃 설계용 참고값
SWITCHER_ROWS = 5

RATE = {
    "GAP_FOR_INF": 0.1,  # 무한 루프 gap

    "STATUS_POS_INTERVAL": {
        "default": 0.5,
        "lighter": 2.0,
    },
    "STATUS_COLLATERAL_INTERVAL": {
        "default": 0.5,
        "lighter": 5.0,
    },
    "CARD_PRICE_INTERVAL": {
        "default": 1.0,
        "lighter": 5.0,
    },
}


def _normalize_symbol_input(sym: str) -> str:
    """
    사용자 입력 심볼 정규화:
    - HIP-3 'dex:coin' → 'COIN_UPPER'
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


def _strip_bracket_markup(s: str) -> str:
    """
    '[green]LONG[/] 0.1 | PnL: [red]-1.23[/]' 형태에서 색 태그 제거.
    (Qt 텍스트는 일단 색 없이 plain text 로 표시)
    """
    return re.sub(r"\[[a-zA-Z_\/]+\]", "", s)


def _inject_usdc_value_into_pos(price: Optional[float], pos_str: str) -> str:
    """
    pos_str 예: '📊 [green]LONG[/] 0.12345 | PnL: [red]-1.23[/]'
    → '📊 LONG 0.12345 (3,456.8 USDC) | PnL: -1.23'
    price 가 없으면 원문 유지.
    """
    if price is None:
        return _strip_bracket_markup(pos_str)

    # 닫는 브래킷 뒤 숫자만 캡처 (ui_urwid.py 의 로직 단순화 버전)
    m = re.search(r"\]\s*([+-]?\d+(?:\.\d+)?)(?=\s*\|\s*PnL:)", pos_str)
    if not m:
        return _strip_bracket_markup(pos_str)

    size_str = m.group(1)
    try:
        size = float(size_str)
    except Exception:
        return _strip_bracket_markup(pos_str)

    usdc_value = size * price
    injected = f"{size_str} ({usdc_value:,.1f} USDC)"

    start, end = m.span(1)
    new_pos = pos_str[:start] + injected + pos_str[end:]
    return _strip_bracket_markup(new_pos)


# ---------------------------------------------------------------------------
# 데이터/상태 구조
# ---------------------------------------------------------------------------

@dataclass
class ExchangeState:
    """단일 거래소 상태 (UI 비즈니스 로직용)"""
    symbol: str = "BTC"
    enabled: bool = False          # OFF/ON
    side: Optional[str] = None     # 'buy' | 'sell' | None
    order_type: str = "market"     # 'market' | 'limit'
    collateral: float = 0.0
    last_price: Optional[float] = None
    last_pos_text: str = "Position: N/A"
    last_col_text: str = "Collateral: N/A"
    dex: str = "HL"                # HL / HIP3 등


# ---------------------------------------------------------------------------
# Qt 위젯: 거래소 카드 (한 거래소당 한 장)
# ---------------------------------------------------------------------------

class ExchangeCardWidget(QtWidgets.QGroupBox):
    """
    한 거래소 카드 위젯.
    - [EXCHANGE]  T/Q/P, MKT/LMT, L/S/OFF, EX
    - Price, Quote, Builder Fee, Position/Collateral 정보 표시
    """
    execute_clicked = QtCore.Signal(str)        # ex_name
    long_clicked = QtCore.Signal(str)
    short_clicked = QtCore.Signal(str)
    off_clicked = QtCore.Signal(str)
    order_type_toggled = QtCore.Signal(str)     # ex_name
    dex_changed = QtCore.Signal(str, str)       # ex_name, dex
    ticker_changed = QtCore.Signal(str, str)    # ex_name, new_ticker

    def __init__(self, ex_name: str, dex_choices: List[str], parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.ex_name = ex_name
        self.setTitle(f"[{ex_name.upper()}]")

        self._dex_choices = dex_choices[:] or ["HL"]

        # --- 위젯 생성 ---
        self.ticker_edit = QtWidgets.QLineEdit()
        self.qty_edit = QtWidgets.QLineEdit()
        self.price_edit = QtWidgets.QLineEdit()

        self.order_type_btn = QtWidgets.QPushButton("MKT")
        self.order_type_btn.setCheckable(True)

        self.long_btn = QtWidgets.QPushButton("L")
        self.short_btn = QtWidgets.QPushButton("S")
        self.off_btn = QtWidgets.QPushButton("OFF")
        self.exec_btn = QtWidgets.QPushButton("EX")

        self.price_label = QtWidgets.QLabel("Price: ...")
        self.quote_label = QtWidgets.QLabel("")  # HL-like 일 때만 사용
        self.fee_label = QtWidgets.QLabel("Builder Fee: -")
        self.info_label = QtWidgets.QLabel("📊 Position: N/A\n💰 Collateral: N/A")

        self.dex_combo = QtWidgets.QComboBox()
        self.dex_combo.addItems(self._dex_choices)

        self._build_layout()
        self._connect_signals()

    # comment: 레이아웃 구성
    def _build_layout(self) -> None:
        form_layout = QtWidgets.QGridLayout()

        # 1행: T/Q/P
        form_layout.addWidget(QtWidgets.QLabel("T:"), 0, 0)
        form_layout.addWidget(self.ticker_edit,        0, 1)

        form_layout.addWidget(QtWidgets.QLabel("Q:"),  0, 2)
        form_layout.addWidget(self.qty_edit,           0, 3)

        form_layout.addWidget(QtWidgets.QLabel("P:"),  0, 4)
        form_layout.addWidget(self.price_edit,         0, 5)

        # 2행: MKT/LMT + L/S/OFF/EX
        form_layout.addWidget(self.order_type_btn, 1, 0)
        form_layout.addWidget(self.long_btn,       1, 2)
        form_layout.addWidget(self.short_btn,      1, 3)
        form_layout.addWidget(self.off_btn,        1, 4)
        form_layout.addWidget(self.exec_btn,       1, 5)

        # 3행: Price / Quote / DEX / Fee
        hbox_price = QtWidgets.QHBoxLayout()
        hbox_price.addWidget(self.price_label)
        hbox_price.addWidget(self.quote_label)
        hbox_price.addStretch(1)
        hbox_price.addWidget(QtWidgets.QLabel("DEX:"))
        hbox_price.addWidget(self.dex_combo)
        hbox_price.addWidget(self.fee_label)

        # 메인 레이아웃
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.addLayout(form_layout)
        vbox.addLayout(hbox_price)
        vbox.addWidget(self.info_label)

    def _connect_signals(self) -> None:
        self.exec_btn.clicked.connect(lambda: self.execute_clicked.emit(self.ex_name))
        self.long_btn.clicked.connect(lambda: self.long_clicked.emit(self.ex_name))
        self.short_btn.clicked.connect(lambda: self.short_clicked.emit(self.ex_name))
        self.off_btn.clicked.connect(lambda: self.off_clicked.emit(self.ex_name))
        self.order_type_btn.clicked.connect(lambda: self.order_type_toggled.emit(self.ex_name))
        self.dex_combo.currentTextChanged.connect(
            lambda text: self.dex_changed.emit(self.ex_name, text)
        )
        self.ticker_edit.textChanged.connect(
            lambda text: self.ticker_changed.emit(self.ex_name, text)
        )

    # --- 상태/뷰 업데이트 메서드 ---

    def set_ticker(self, ticker: str) -> None:
        if self.ticker_edit.text() != ticker:
            self.ticker_edit.setText(ticker)

    def set_qty(self, qty: str) -> None:
        if self.qty_edit.text() != qty:
            self.qty_edit.setText(qty)

    def get_qty(self) -> str:
        return self.qty_edit.text().strip()

    def get_price_text(self) -> str:
        return self.price_edit.text().strip()

    def set_price_label(self, px_str: str) -> None:
        self.price_label.setText(f"Price: {px_str}")

    def set_quote_label(self, text: str) -> None:
        self.quote_label.setText(text or "")

    def set_fee_label(self, text: str) -> None:
        self.fee_label.setText(text)

    def set_info_text(self, pos_text: str, col_text: str) -> None:
        self.info_label.setText(f"{pos_text}\n{col_text}")

    def set_order_type(self, order_type: str) -> None:
        order_type = (order_type or "market").lower()
        is_limit = (order_type == "limit")
        self.order_type_btn.setChecked(is_limit)
        self.order_type_btn.setText("LMT" if is_limit else "MKT")

    def set_side_enabled(self, enabled: bool, side: Optional[str]) -> None:
        """
        버튼의 on/off 스타일은 Qt 기본 스타일로, 체크 여부만 표현.
        """
        self.long_btn.setCheckable(True)
        self.short_btn.setCheckable(True)
        self.off_btn.setCheckable(True)

        self.long_btn.setChecked(False)
        self.short_btn.setChecked(False)
        self.off_btn.setChecked(False)

        if not enabled:
            self.off_btn.setChecked(True)
            return

        if side == "buy":
            self.long_btn.setChecked(True)
        elif side == "sell":
            self.short_btn.setChecked(True)

    def set_dex(self, dex: str) -> None:
        idx = self.dex_combo.findText(dex, QtCore.Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.dex_combo.setCurrentIndex(idx)


# ---------------------------------------------------------------------------
# Qt 위젯: 헤더 (심볼/가격/All Qty/Repeat/Burn 등)
# ---------------------------------------------------------------------------

class HeaderWidget(QtWidgets.QWidget):
    ticker_changed = QtCore.Signal(str)
    allqty_changed = QtCore.Signal(str)
    exec_all_clicked = QtCore.Signal()
    reverse_clicked = QtCore.Signal()
    close_all_clicked = QtCore.Signal()
    repeat_clicked = QtCore.Signal()
    burn_clicked = QtCore.Signal()
    quit_clicked = QtCore.Signal()
    dex_changed = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)

        # comment: 위젯들
        self.ticker_edit = QtWidgets.QLineEdit("BTC")
        self.price_label = QtWidgets.QLabel("Price: ...")
        self.total_label = QtWidgets.QLabel("Total: 0.0 USDC")

        self.allqty_edit = QtWidgets.QLineEdit("")
        self.exec_all_btn = QtWidgets.QPushButton("EXECUTE ALL")
        self.reverse_btn = QtWidgets.QPushButton("REVERSE")
        self.close_all_btn = QtWidgets.QPushButton("CLOSE ALL")
        self.quit_btn = QtWidgets.QPushButton("QUIT")

        # REPEAT
        self.repeat_times = QtWidgets.QLineEdit("")
        self.repeat_min = QtWidgets.QLineEdit("")
        self.repeat_max = QtWidgets.QLineEdit("")
        self.repeat_btn = QtWidgets.QPushButton("REPEAT")

        # BURN
        self.burn_count = QtWidgets.QLineEdit("")
        self.burn_min = QtWidgets.QLineEdit("")
        self.burn_max = QtWidgets.QLineEdit("")
        self.burn_btn = QtWidgets.QPushButton("BURN")

        # DEX
        self.dex_combo = QtWidgets.QComboBox()

        self._build_layout()
        self._connect_signals()

    def _build_layout(self) -> None:
        grid = QtWidgets.QGridLayout(self)

        # 1행
        grid.addWidget(QtWidgets.QLabel("Ticker:"), 0, 0)
        grid.addWidget(self.ticker_edit,           0, 1)
        grid.addWidget(self.price_label,           0, 2)
        grid.addWidget(self.total_label,           0, 3)
        grid.addWidget(self.quit_btn,              0, 4)

        # 2행
        grid.addWidget(QtWidgets.QLabel("All Qty:"), 1, 0)
        grid.addWidget(self.allqty_edit,             1, 1)
        grid.addWidget(self.exec_all_btn,            1, 2)
        grid.addWidget(self.reverse_btn,             1, 3)
        grid.addWidget(self.close_all_btn,           1, 4)

        # 3행: DEX
        grid.addWidget(QtWidgets.QLabel("HIP3-DEX:"), 2, 0)
        grid.addWidget(self.dex_combo,               2, 1, 1, 2)

        # 4행: REPEAT
        grid.addWidget(QtWidgets.QLabel("Times:"), 3, 0)
        grid.addWidget(self.repeat_times,           3, 1)
        grid.addWidget(QtWidgets.QLabel("min(s):"), 3, 2)
        grid.addWidget(self.repeat_min,             3, 3)
        grid.addWidget(QtWidgets.QLabel("max(s):"), 3, 4)
        grid.addWidget(self.repeat_max,             3, 5)
        grid.addWidget(self.repeat_btn,             3, 6)

        # 5행: BURN
        grid.addWidget(QtWidgets.QLabel("Burn:"),   4, 0)
        grid.addWidget(self.burn_count,             4, 1)
        grid.addWidget(QtWidgets.QLabel("min(s):"), 4, 2)
        grid.addWidget(self.burn_min,               4, 3)
        grid.addWidget(QtWidgets.QLabel("max(s):"), 4, 4)
        grid.addWidget(self.burn_max,               4, 5)
        grid.addWidget(self.burn_btn,               4, 6)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(3, 1)

    def _connect_signals(self) -> None:
        self.ticker_edit.textChanged.connect(self.ticker_changed)
        self.allqty_edit.textChanged.connect(self.allqty_changed)
        self.exec_all_btn.clicked.connect(self.exec_all_clicked)
        self.reverse_btn.clicked.connect(self.reverse_clicked)
        self.close_all_btn.clicked.connect(self.close_all_clicked)
        self.repeat_btn.clicked.connect(self.repeat_clicked)
        self.burn_btn.clicked.connect(self.burn_clicked)
        self.quit_btn.clicked.connect(self.quit_clicked)
        self.dex_combo.currentTextChanged.connect(self.dex_changed)

    # --- 외부에서 쓰기 쉬운 헬퍼 ---

    def set_price(self, price_str: str) -> None:
        self.price_label.setText(f"Price: {price_str}")

    def set_total(self, total_usdc: float) -> None:
        self.total_label.setText(f"Total: {total_usdc:,.1f} USDC")

    def set_dex_choices(self, dexs: List[str], current: str) -> None:
        self.dex_combo.blockSignals(True)
        self.dex_combo.clear()
        self.dex_combo.addItems(dexs)
        idx = self.dex_combo.findText(current, QtCore.Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.dex_combo.setCurrentIndex(idx)
        self.dex_combo.blockSignals(False)


# ---------------------------------------------------------------------------
# 메인 윈도우 (UiQtApp)
# ---------------------------------------------------------------------------

class UiQtApp(QtWidgets.QMainWindow):
    """
    urwid 기반 UrwidApp 을 Qt 기반으로 옮긴 버전.
    - ExchangeManager, TradingService 사용 방식은 최대한 동일하게 유지.
    - UI 레이아웃만 Qt 위젯으로 교체.
    """

    def __init__(self, manager: ExchangeManager):
        super().__init__()
        self.setWindowTitle("Perp DEX Hedge (Qt)")

        self.mgr = manager
        self.service = TradingService(self.mgr)

        # 상태
        self.symbol: str = "BTC"
        self.current_price: str = "..."
        self.enabled: Dict[str, bool] = {n: False for n in self.mgr.all_names()}
        self.side: Dict[str, Optional[str]] = {n: None for n in self.mgr.all_names()}
        self.order_type: Dict[str, str] = {n: "market" for n in self.mgr.all_names()}
        self.collateral: Dict[str, float] = {n: 0.0 for n in self.mgr.all_names()}

        self.symbol_by_ex: Dict[str, str] = {n: self.symbol for n in self.mgr.all_names()}
        self.dex_by_ex: Dict[str, str] = {n: "HL" for n in self.mgr.all_names()}
        self.dex_names: List[str] = ["HL"]
        self.header_dex: str = "HL"

        self.exchange_state: Dict[str, ExchangeState] = {
            n: ExchangeState(symbol=self.symbol, dex="HL") for n in self.mgr.all_names()
        }

        self._bulk_updating_tickers: bool = False
        self._stopping: bool = False
        self._price_task: Optional[asyncio.Task] = None
        self._status_task: Optional[asyncio.Task] = None

        # 상태 루프용 타임스탬프
        self._last_balance_at: Dict[str, float] = {}
        self._last_pos_at: Dict[str, float] = {}
        self._last_price_at: Dict[str, float] = {}

        # UI 구성 요소
        self.header = HeaderWidget()
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)

        self.exchange_switch_container = QtWidgets.QWidget()
        self.exchange_switch_layout = QtWidgets.QGridLayout(self.exchange_switch_container)
        self.exchange_switches: Dict[str, QtWidgets.QCheckBox] = {}

        self.cards_container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.addStretch(1)
        self.cards: Dict[str, ExchangeCardWidget] = {}

        self._build_main_layout()
        self._connect_header_signals()

    # ------------------------------------------------------------------
    # UI 레이아웃 구성
    # ------------------------------------------------------------------

    def _build_main_layout(self) -> None:
        """
        메인 레이아웃:
            [Header]
            [Cards (ScrollArea)]
            [Exchanges Switch Grid]  [Logs]
        """
        central = QtWidgets.QWidget()
        main_vbox = QtWidgets.QVBoxLayout(central)

        # Header
        header_box = QtWidgets.QGroupBox("Header")
        header_layout = QtWidgets.QVBoxLayout(header_box)
        header_layout.addWidget(self.header)

        # Cards (ScrollArea)
        cards_scroll = QtWidgets.QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setWidget(self.cards_container)

        # Exchanges Switch
        switch_box = QtWidgets.QGroupBox("Exchanges")
        switch_layout = QtWidgets.QVBoxLayout(switch_box)
        switch_layout.addWidget(self.exchange_switch_container)

        # Logs
        logs_box = QtWidgets.QGroupBox("Logs")
        logs_layout = QtWidgets.QVBoxLayout(logs_box)
        logs_layout.addWidget(self.log_edit)

        bottom_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        bottom_splitter.addWidget(switch_box)
        bottom_splitter.addWidget(logs_box)
        bottom_splitter.setStretchFactor(0, 1)
        bottom_splitter.setStretchFactor(1, 2)

        main_vbox.addWidget(header_box)
        main_vbox.addWidget(cards_scroll, 2)
        main_vbox.addWidget(bottom_splitter, 1)

        self.setCentralWidget(central)
        self.resize(1200, 800)

    def _connect_header_signals(self) -> None:
        self.header.ticker_changed.connect(self._on_header_ticker_changed)
        self.header.allqty_changed.connect(self._on_allqty_changed)
        self.header.exec_all_clicked.connect(self._on_exec_all)
        self.header.reverse_clicked.connect(self._on_reverse)
        self.header.close_all_clicked.connect(self._on_close_all_clicked)

        # TODO: Qt 버전 repeat/burn 은 단순 로그만 남깁니다.
        self.header.repeat_clicked.connect(
            lambda: self._log("[REPEAT] Qt UI에서는 아직 미구현입니다.")
        )
        self.header.burn_clicked.connect(
            lambda: self._log("[BURN] Qt UI에서는 아직 미구현입니다.")
        )
        self.header.quit_clicked.connect(self.close)
        self.header.dex_changed.connect(self._on_header_dex_changed)

    # ------------------------------------------------------------------
    # 초기 비동기 설정
    # ------------------------------------------------------------------

    async def async_init(self) -> None:
        """
        ExchangeManager 초기화, DEX 리스트/카드/스위치 구성, 가격/상태 루프 시작.
        """
        try:
            await self.mgr.initialize_all()
        except Exception as e:
            self._log(f"[INIT] initialize_all failed: {e}")

        # DEX 목록 로딩 (HL 우선)
        try:
            first_hl = self.mgr.first_hl_exchange()
            if first_hl and getattr(first_hl, "dex_list", None):
                dexs = [x.upper() for x in first_hl.dex_list]
                if "HL" not in dexs:
                    dexs.insert(0, "HL")
                self.dex_names = dexs
        except Exception as e:
            self._log(f"[INIT] fetch DEX list failed: {e}")
            self.dex_names = ["HL"]

        self.header_dex = "HL"
        self.header.set_dex_choices(self.dex_names, self.header_dex)

        # 스위치/카드 구성
        self._build_exchange_switches()
        self._rebuild_cards()

        # 가격/상태 루프 시작
        loop = asyncio.get_running_loop()
        self._price_task = loop.create_task(self._price_loop())
        self._status_task = loop.create_task(self._status_loop())

    # ------------------------------------------------------------------
    # 스위치 / 카드 구성
    # ------------------------------------------------------------------

    def _build_exchange_switches(self) -> None:
        """
        footer 의 Exchanges Grid 에 해당하는 Qt 체크박스 생성.
        """
        # 기존 위젯 제거
        while self.exchange_switch_layout.count():
            item = self.exchange_switch_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        self.exchange_switches.clear()

        names = self.mgr.all_names()
        if not names:
            self.exchange_switch_layout.addWidget(QtWidgets.QLabel("no exchanges"))
            return

        cols = 4
        row = 0
        col = 0
        for name in names:
            meta = self.mgr.get_meta(name)
            show = bool(meta.get("show", False))

            cb = QtWidgets.QCheckBox(name.upper())
            cb.setChecked(show)
            cb.toggled.connect(lambda state, n=name: self._on_toggle_show(n, state))

            self.exchange_switches[name] = cb
            self.exchange_switch_layout.addWidget(cb, row, col)

            col += 1
            if col >= cols:
                col = 0
                row += 1

    def _rebuild_cards(self) -> None:
        """
        visible_names 기준으로 카드 생성/삭제.
        """
        # 기존 카드 제거
        for name, card in list(self.cards.items()):
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.cards_layout.addStretch(1)

        visible = self.mgr.visible_names()
        for name in visible:
            card = ExchangeCardWidget(name, dex_choices=self.dex_names)
            self.cards[name] = card

            # 초기 상태 반영
            ex_state = self.exchange_state[name]
            card.set_ticker(ex_state.symbol)
            card.set_order_type(ex_state.order_type)
            card.set_side_enabled(ex_state.enabled, ex_state.side)
            card.set_dex(ex_state.dex)
            card.set_fee_label("Builder Fee: -")

            # 시그널 연결
            card.execute_clicked.connect(self._on_exec_one_clicked)
            card.long_clicked.connect(self._on_long_clicked)
            card.short_clicked.connect(self._on_short_clicked)
            card.off_clicked.connect(self._on_off_clicked)
            card.order_type_toggled.connect(self._on_order_type_toggled)
            card.dex_changed.connect(self._on_card_dex_changed)
            card.ticker_changed.connect(self._on_card_ticker_changed)

            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        # All Qty 가 이미 입력돼 있으면 카드에도 반영
        all_qty = self.header.allqty_edit.text()
        if all_qty:
            for c in self.cards.values():
                c.set_qty(all_qty)

        # DEX / Fee 초기 갱신
        for name in visible:
            self._update_card_fee(name)

    # ------------------------------------------------------------------
    # 헤더 이벤트 핸들러
    # ------------------------------------------------------------------

    def _on_header_ticker_changed(self, text: str) -> None:
        coin = _normalize_symbol_input(text or "BTC")
        self.symbol = coin

        self._bulk_updating_tickers = True
        try:
            # 내부 상태
            for ex_name in self.mgr.all_names():
                self.symbol_by_ex[ex_name] = coin
                st = self.exchange_state[ex_name]
                st.symbol = coin

            # 화면 카드
            for card in self.cards.values():
                card.set_ticker(coin)
        finally:
            self._bulk_updating_tickers = False

    def _on_allqty_changed(self, text: str) -> None:
        for card in self.cards.values():
            card.set_qty(text or "")

    def _on_header_dex_changed(self, dex: str) -> None:
        self.header_dex = dex
        # 전체 카드에 일괄 적용
        for n in self.mgr.all_names():
            self.dex_by_ex[n] = dex
            self.exchange_state[n].dex = dex

        for name, card in self.cards.items():
            card.set_dex(dex)
            self._update_card_fee(name)

    # ------------------------------------------------------------------
    # 카드 이벤트 핸들러
    # ------------------------------------------------------------------

    def _on_card_ticker_changed(self, ex_name: str, text: str) -> None:
        coin = _normalize_symbol_input(text or self.symbol)
        self.symbol_by_ex[ex_name] = coin
        self.exchange_state[ex_name].symbol = coin
        # urwid 버전처럼 레버리지 예약은 생략 (Qt 버전 단순화)

    def _on_card_dex_changed(self, ex_name: str, dex: str) -> None:
        self.dex_by_ex[ex_name] = dex
        self.exchange_state[ex_name].dex = dex
        self._update_card_fee(ex_name)

    def _on_long_clicked(self, ex_name: str) -> None:
        self.enabled[ex_name] = True
        self.side[ex_name] = "buy"
        self.exchange_state[ex_name].enabled = True
        self.exchange_state[ex_name].side = "buy"
        self._refresh_side(ex_name)

    def _on_short_clicked(self, ex_name: str) -> None:
        self.enabled[ex_name] = True
        self.side[ex_name] = "sell"
        self.exchange_state[ex_name].enabled = True
        self.exchange_state[ex_name].side = "sell"
        self._refresh_side(ex_name)

    def _on_off_clicked(self, ex_name: str) -> None:
        self.enabled[ex_name] = False
        self.side[ex_name] = None
        self.exchange_state[ex_name].enabled = False
        self.exchange_state[ex_name].side = None
        self._refresh_side(ex_name)

    def _on_order_type_toggled(self, ex_name: str) -> None:
        cur = (self.order_type.get(ex_name) or "market").lower()
        new_type = "limit" if cur == "market" else "market"
        self.order_type[ex_name] = new_type
        self.exchange_state[ex_name].order_type = new_type

        card = self.cards.get(ex_name)
        if card:
            card.set_order_type(new_type)
        self._update_card_fee(ex_name)

    def _on_exec_one_clicked(self, ex_name: str) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self._exec_one(ex_name))

    # ------------------------------------------------------------------
    # Exchanges ON/OFF 토글
    # ------------------------------------------------------------------

    def _on_toggle_show(self, ex_name: str, state: bool) -> None:
        meta = self.mgr.get_meta(ex_name)
        meta["show"] = bool(state)
        if not state:
            # OFF 로 내려가면 enabled/side 초기화
            self.enabled[ex_name] = False
            self.side[ex_name] = None
            self.exchange_state[ex_name].enabled = False
            self.exchange_state[ex_name].side = None
        self._rebuild_cards()

    # ------------------------------------------------------------------
    # 로그 / 합계 / FEE
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        logger.info(msg)
        self.log_edit.appendPlainText(msg)
        # 항상 맨 아래로 스크롤
        scrollbar = self.log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _collateral_sum(self) -> float:
        return sum(self.collateral.values())

    def _update_header_total(self) -> None:
        self.header.set_total(self._collateral_sum())

    def _update_card_fee(self, ex_name: str) -> None:
        """
        HL-like 거래소에서만 Builder Fee 표시.
        """
        try:
            if not self.mgr.is_hl_like(ex_name):
                return
            card = self.cards.get(ex_name)
            if not card:
                return
            dex = self.dex_by_ex.get(ex_name, "HL")
            dex_key = None if dex == "HL" else dex.lower()
            otype = (self.order_type.get(ex_name) or "market").lower()
            fee = self.service.get_display_builder_fee(ex_name, dex_key, otype)
            if isinstance(fee, int):
                card.set_fee_label(f"Builder Fee: {fee}")
            else:
                card.set_fee_label("Builder Fee: -")
        except Exception:
            # 조용히 무시
            pass

    def _refresh_side(self, ex_name: str) -> None:
        card = self.cards.get(ex_name)
        if not card:
            return
        enabled = self.enabled.get(ex_name, False)
        side = self.side.get(ex_name)
        card.set_side_enabled(enabled, side)

    # ------------------------------------------------------------------
    # 가격/상태 루프
    # ------------------------------------------------------------------

    async def _price_loop(self) -> None:
        """
        헤더에 공통 심볼 가격 / 총 콜래터럴 표시.
        """
        while not self._stopping:
            try:
                raw = self.header.ticker_edit.text() or "BTC"
                coin = _normalize_symbol_input(raw)
                self.symbol = coin

                px_str = self.current_price or "..."

                # HL 우선
                ex = self.mgr.first_hl_exchange()
                if not ex:
                    for nm in self.mgr.visible_names():
                        meta = self.mgr.get_meta(nm)
                        if meta.get("hl", False) and self.mgr.get_exchange(nm):
                            ex = self.mgr.get_exchange(nm)
                            break

                if ex:
                    sym = _compose_symbol(self.header_dex, coin)
                    try:
                        px_val = await ex.get_mark_price(sym)
                        if px_val is not None:
                            px_str = self.service.format_price_simple(float(px_val))
                    except Exception as e:
                        logger.debug(f"price loop: mark_price failed for {sym}: {e}")

                self.current_price = px_str
                self.header.set_price(self.current_price)
                self._update_header_total()

                await asyncio.sleep(RATE["GAP_FOR_INF"])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"price loop error: {e}")
                await asyncio.sleep(RATE["GAP_FOR_INF"])

    async def _status_loop(self) -> None:
        """
        거래소별 상태/가격/콜래터럴 업데이트 (단일 루프에서 순회).
        """
        await asyncio.sleep(0.3)

        while not self._stopping:
            try:
                visible = self.mgr.visible_names()
                now = time.monotonic()

                for name in visible:
                    card = self.cards.get(name)
                    if not card:
                        continue

                    meta = self.mgr.get_meta(name)
                    exchange_platform = meta.get("exchange", "hyperliquid")
                    try:
                        col_itv = RATE["STATUS_COLLATERAL_INTERVAL"][exchange_platform]
                        pos_itv = RATE["STATUS_POS_INTERVAL"][exchange_platform]
                        px_itv = RATE["CARD_PRICE_INTERVAL"][exchange_platform]
                    except Exception:
                        col_itv = RATE["STATUS_COLLATERAL_INTERVAL"]["default"]
                        pos_itv = RATE["STATUS_POS_INTERVAL"]["default"]
                        px_itv = RATE["CARD_PRICE_INTERVAL"]["default"]

                    need_collat = (now - self._last_balance_at.get(name, 0.0) >= col_itv)
                    need_pos = (now - self._last_pos_at.get(name, 0.0) >= pos_itv)
                    need_price = (now - self._last_price_at.get(name, 0.0) >= px_itv)

                    ex = self.mgr.get_exchange(name)
                    if not ex:
                        continue

                    sym_coin = _normalize_symbol_input(self.symbol_by_ex.get(name) or self.symbol)
                    dex = self.dex_by_ex.get(name, "HL")
                    sym = _compose_symbol(dex, sym_coin)
                    is_hl_like = self.mgr.is_hl_like(name)

                    # 가격
                    if need_price:
                        try:
                            px_str = await self.service.fetch_price(name, sym)
                            card.set_price_label(px_str)
                            try:
                                self.exchange_state[name].last_price = float(str(px_str).replace(",", ""))
                            except Exception:
                                self.exchange_state[name].last_price = None
                            self._last_price_at[name] = now
                        except Exception as e:
                            logger.info(f"[UI] price update for {name} failed: {e}")
                            card.set_price_label("Error")

                    # Quote (HL-like)
                    if is_hl_like:
                        try:
                            quote_str = ex.get_perp_quote(sym)
                            card.set_quote_label(quote_str)
                        except Exception as e:
                            logger.info(f"[UI] quote update for {name} failed: {e}")
                            card.set_quote_label("")

                    # 포지션/콜래터럴
                    try:
                        pos_str, col_str, col_val = await self.service.fetch_status(
                            name, sym, need_balance=need_collat, need_position=need_pos
                        )
                    except Exception as e:
                        logger.error(f"[UI] status update for {name} failed: {e}")
                        continue

                    # collateral
                    if need_collat:
                        try:
                            self.collateral[name] = float(col_val)
                        except Exception:
                            pass
                        self._last_balance_at[name] = now
                        self._update_header_total()

                    if need_pos:
                        self._last_pos_at[name] = now

                    # 문자열 가공 (USDC 값 주입 + 색 태그 제거)
                    last_px = self.exchange_state[name].last_price
                    pos_pretty = _inject_usdc_value_into_pos(last_px, pos_str)
                    col_pretty = _strip_bracket_markup(col_str)
                    card.set_info_text(pos_pretty, col_pretty)

                await asyncio.sleep(RATE["GAP_FOR_INF"])
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CRITICAL] Unhandled error in status_loop", exc_info=True)
                await asyncio.sleep(1.0)

    # ------------------------------------------------------------------
    # 주문 관련 메서드
    # ------------------------------------------------------------------

    async def _exec_one(self, name: str) -> None:
        ex = self.mgr.get_exchange(name)
        if not ex:
            self._log(f"[{name.upper()}] 설정 없음")
            return
        if not self.enabled.get(name, False):
            self._log(f"[{name.upper()}] 비활성 상태")
            return
        side = self.side.get(name)
        if not side:
            self._log(f"[{name.upper()}] LONG/SHORT 미선택")
            return

        card = self.cards.get(name)
        if not card:
            self._log(f"[{name.upper()}] UI 카드 없음")
            return

        max_retry = 5
        for attempt in range(1, max_retry + 1):
            try:
                qty_text = card.get_qty()
                if not qty_text:
                    self._log(f"[{name.upper()}] 수량 없음")
                    return
                amount = float(qty_text)

                otype = (self.order_type.get(name) or "market").lower()

                if otype == "limit":
                    price_text = card.get_price_text()
                    if not price_text:
                        self._log(f"[{name.upper()}] 지정가(Price) 없음")
                        return
                    price = float(price_text)
                else:
                    price = None

                sym_coin = _normalize_symbol_input(self.symbol_by_ex.get(name) or self.symbol)
                dex = self.dex_by_ex.get(name, self.header_dex)
                sym = _compose_symbol(dex, sym_coin)

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

    async def _exec_all_async(self) -> None:
        self._log("[ALL] 동시 주문 시작")
        tasks = []
        for n in self.mgr.visible_names():
            if not self.mgr.get_exchange(n):
                continue
            if not self.enabled.get(n, False):
                self._log(f"[ALL] {n.upper()} 건너뜀: 비활성")
                continue
            if not self.side.get(n):
                self._log(f"[ALL] {n.upper()} 건너뜀: 방향 미선택")
                continue
            tasks.append(self._exec_one(n))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._log("[ALL] 완료")
        else:
            self._log("[ALL] 실행할 거래소가 없습니다.")

    def _on_exec_all(self) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self._exec_all_async())

    def _on_reverse(self) -> None:
        cnt = 0
        for n in self.mgr.visible_names():
            if not self.enabled.get(n, False):
                continue
            if self.side.get(n) == "buy":
                self.side[n] = "sell"
                self.exchange_state[n].side = "sell"
                cnt += 1
            elif self.side.get(n) == "sell":
                self.side[n] = "buy"
                self.exchange_state[n].side = "buy"
                cnt += 1
            self._refresh_side(n)
        self._log(f"[ALL] REVERSE 완료: {cnt}개")

    def _on_close_all_clicked(self) -> None:
        loop = asyncio.get_running_loop()
        loop.create_task(self._close_all_positions())

    async def _close_all_positions(self) -> None:
        self._log("[CLOSE] CLOSE ALL 시작")
        tasks = []
        for n in self.mgr.visible_names():
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

    async def _close_one_position(self, name: str, ex) -> None:
        max_retry = 3
        for attempt in range(1, max_retry + 1):
            try:
                # 가격 힌트는 현재 헤더가 들고 있는 가격 사용(필요 시 None 허용)
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
                    price_hint=hint,
                )
                if order is None:
                    # 포지션 없음
                    return
                self._log(f"[{name.upper()}] CLOSE 성공: #{order.get('id', '?')}")
                return
            except Exception as e:
                self._log(f"[{name.upper()}] CLOSE 실패: {e}")
                self._log(f"[{name.upper()}] CLOSE 재시도...{attempt} | {max_retry}")
                if attempt >= max_retry:
                    self._log(f"[{name.upper()}] 재시도 한도 초과, 중단")
                    return
                await asyncio.sleep(0.5)

    # ------------------------------------------------------------------
    # 종료/정리
    # ------------------------------------------------------------------

    async def _kill_ccxt_throttlers(self) -> None:
        """
        ui_urwid.py 의 _kill_ccxt_throttlers 를 거의 그대로 사용.
        Throttler.looper 태스크 강제 정리.
        """
        try:
            current = asyncio.current_task()
        except Exception:
            current = None

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

        await asyncio.sleep(0)

    async def shutdown(self) -> None:
        """
        Qt 종료 시 비동기 리소스 정리.
        """
        self._stopping = True

        # 가격/상태 루프 취소
        tasks: List[asyncio.Task] = []
        if self._price_task and not self._price_task.done():
            self._price_task.cancel()
            tasks.append(self._price_task)
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
            tasks.append(self._status_task)

        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass

        # Manager 정리
        try:
            await self.mgr.close_all()
        except Exception:
            pass

        # ccxt Throttler 정리
        try:
            await self._kill_ccxt_throttlers()
        except Exception:
            pass

        # 남은 태스크도 전수 cancel (가능한 깔끔한 종료)
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

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """
        창 닫기 → 비동기 정리 → 이벤트 루프 중단.
        """
        loop = asyncio.get_event_loop()

        async def _shutdown_and_stop() -> None:
            try:
                await self.shutdown()
            finally:
                loop.stop()

        loop.create_task(_shutdown_and_stop())
        event.accept()


# ---------------------------------------------------------------------------
# 엔트리 포인트
# ---------------------------------------------------------------------------

def run_qt_app(manager: ExchangeManager) -> None:
    """
    기존 `UrwidApp(manager).run()` 대신 쓸 수 있는 Qt 진입 함수.

    예:
        from core import ExchangeManager
        from ui_qt import run_qt_app

        mgr = ExchangeManager(...)
        run_qt_app(mgr)
    """
    app = QtWidgets.QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = UiQtApp(manager)

    async def _startup():
        await window.async_init()
        window.show()

    loop.create_task(_startup())

    with loop:
        loop.run_forever()

if __name__ == "__main__":
    # comment: 직접 실행 시 Manager 초기화는 프로젝트 구조에 맞게 수정 필요
    print("This module is intended to be imported and used with an ExchangeManager.")
    print("예: run_qt_app(ExchangeManager(...))")