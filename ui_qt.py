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
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional, List
from logging.handlers import RotatingFileHandler

from PySide6 import QtCore, QtGui, QtWidgets
import qasync

from core import ExchangeManager
from trading_service import TradingService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 전역 UI 설정 (폰트/테마)
# ---------------------------------------------------------------------------

# 코드/환경에서 쉽게 조절할 수 있도록 상수 + ENV 제공
UI_FONT_FAMILY = os.getenv("PDEX_UI_FONT_FAMILY", "Noto Sans CJK KR")        # 예: "Noto Sans CJK KR"
UI_FONT_SIZE = int(os.getenv("PDEX_UI_FONT_SIZE", "18"))     # 전체 기본 폰트 크기(pt)
UI_THEME = os.getenv("PDEX_UI_THEME", "dark").lower()        # "light" 또는 "dark"


def _apply_app_style(app: QtWidgets.QApplication) -> None:
    """
    앱 전체에 폰트/테마 적용.
    - 폰트는 환경 변수 또는 기본 값 사용
    - 테마는 Fusion 기반 light/dark 두 가지
    """
    # 스타일은 Fusion 으로 통일
    app.setStyle("Fusion")

    # 폰트 설정
    font = app.font()
    if UI_FONT_FAMILY:
        font.setFamily(UI_FONT_FAMILY)
    if UI_FONT_SIZE > 0:
        font.setPointSize(UI_FONT_SIZE)
    app.setFont(font)

    # 색상 테마 (라이트/다크)
    if UI_THEME == "dark":
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(35, 35, 35))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.ToolTipText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor(53, 53, 53))
        palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.BrightText, QtCore.Qt.red)
        palette.setColor(QtGui.QPalette.Link, QtGui.QColor(42, 130, 218))
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(42, 130, 218))
        palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.black)
        # FIX: placeholder 색도 어두운 배경에서 보이도록
        palette.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor(160, 160, 160))
        app.setPalette(palette)

    # 전체 스타일시트
    base_font_size = UI_FONT_SIZE
    log_font_size = max(UI_FONT_SIZE - 1, 8)

    # FIX: 한글 + 이모지까지 고려한 폰트 fallback 체인
    font_families: List[str] = []
    if UI_FONT_FAMILY:
        font_families.append(UI_FONT_FAMILY)
    # 자주 쓰이는 이모지/시스템 폰트들 (있으면 사용, 없으면 무시됨)
    font_families += [
        "Noto Color Emoji",
        "Segoe UI Emoji",
        "Apple Color Emoji",
        "Noto Emoji",
        "EmojiOne Color",
        "Sans"
    ]
    css_font_families = ", ".join(f'"{f}"' for f in font_families)

    style = f"""
    QWidget {{
        font-size: {base_font_size}pt;
        font-family: {css_font_families};
    }}
    QGroupBox {{
        font-weight: bold;
        border: 1px solid #999;
        border-radius: 4px;
        margin-top: 8px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 8px;
        padding: 0px 4px;
    }}
    QPushButton {{
        padding: 4px 10px;
    }}
    QPushButton:disabled {{
        color: #777;
    }}
    QPlainTextEdit, QTextEdit {{
        font-size: {log_font_size}pt;
        font-family: {css_font_families};
    }}
    """
    app.setStyleSheet(style)


# ---------------------------------------------------------------------------
# 로깅 설정
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
# 공통 상수/유틸
# ---------------------------------------------------------------------------

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
    """사용자 입력 심볼 정규화."""
    if not sym:
        return ""
    s = sym.strip()
    if ":" in s:
        _, coin = s.split(":", 1)
        return coin.upper()
    return s.upper()


def _compose_symbol(dex: str, coin: str) -> str:
    """dex가 'HL'이면 coin(upper)만, HIP-3이면 'dex:COIN'으로 합성."""
    coin_u = (coin or "").upper()
    if dex and dex != "HL":
        return f"{dex.lower()}:{coin_u}"
    return coin_u


def _strip_bracket_markup(s: str) -> str:
    """'[green]LONG[/] ...' 같은 색 태그 제거."""
    return re.sub(r"\[[a-zA-Z_\/]+\]", "", s)


def _inject_usdc_value_into_pos(price: Optional[float], pos_str: str) -> str:
    """
    pos_str 예: '📊 [green]LONG[/] 0.12345 | PnL: [red]-1.23[/]'
    → '📊 LONG 0.12345 (3,456.8 USDC) | PnL: -1.23'
    """
    if price is None:
        return _strip_bracket_markup(pos_str)

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
# 보조 클래스: DEX 콤보박스
# ---------------------------------------------------------------------------

class DexComboBox(QtWidgets.QComboBox):
    """
    DEX 선택용 콤보박스.
    기본 QComboBox 동작은 그대로 두고,
    팝업 열림/닫힘 시그널만 추가로 발행한다.
    """
    popupOpened = QtCore.Signal()
    popupClosed = QtCore.Signal()

    def showPopup(self) -> None:
        self.popupOpened.emit()
        super().showPopup()

    def hidePopup(self) -> None:
        self.popupClosed.emit()
        super().hidePopup()


# ---------------------------------------------------------------------------
# stdout/stderr → UI 콘솔 리다이렉터
# ---------------------------------------------------------------------------

class EmittingStream(QtCore.QObject):
    text_written = QtCore.Signal(str)

    def write(self, text: str) -> int:  # type: ignore[override]
        if text:
            self.text_written.emit(str(text))
        return len(text)

    def flush(self) -> None:  # type: ignore[override]
        pass


# ---------------------------------------------------------------------------
# Qt 위젯: 거래소 카드 (한 거래소당 한 장)
# ---------------------------------------------------------------------------

class ExchangeCardWidget(QtWidgets.QGroupBox):
    """
    한 거래소 카드 위젯.
    - [EXCHANGE]  T/Q/P, Order Type, Long/Short/OFF, Execute
    - Price, Quote, Builder Fee, Position/Collateral 정보 표시
    """
    execute_clicked = QtCore.Signal(str)           # ex_name
    long_clicked = QtCore.Signal(str)
    short_clicked = QtCore.Signal(str)
    off_clicked = QtCore.Signal(str)
    order_type_changed = QtCore.Signal(str, str)   # ex_name, "market"/"limit"
    dex_changed = QtCore.Signal(str, str)          # ex_name, dex
    ticker_changed = QtCore.Signal(str, str)       # ex_name, new_ticker

    def __init__(self, ex_name: str, dex_choices: List[str],
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.ex_name = ex_name

        self.setTitle(f"")

        self._dex_choices = dex_choices[:] or ["HL"]

        # NEW: 카드 안쪽 제목 라벨
        self.title_label = QtWidgets.QLabel(f"[{ex_name.upper()}]")
        self.title_label.setStyleSheet("""
            QLabel {
                color: #ffeb3b;
                font-weight: bold;
            }
        """)

        # --- 위젯 생성 ---
        self.ticker_edit = QtWidgets.QLineEdit()
        self.qty_edit = QtWidgets.QLineEdit()
        self.price_edit = QtWidgets.QLineEdit()
        # FIX: placeholder 는 set_order_type 에서 제어 (여기선 설정하지 않음)

        # Order Type: Market / Limit
        self.order_type_combo = QtWidgets.QComboBox()
        self.order_type_combo.addItems(["Market", "Limit"])

        # FIX: Type 콤보도 리스트 뷰 클릭 시 자동 닫힘
        self.order_type_combo.view().pressed.connect(
            lambda idx: QtCore.QTimer.singleShot(0, self.order_type_combo.hidePopup)
        )

        # FIX: 버튼 텍스트/컬러/크기 정리
        self.long_btn = QtWidgets.QPushButton("Long")
        self.short_btn = QtWidgets.QPushButton("Short")
        self.off_btn = QtWidgets.QPushButton("Off")
        self.exec_btn = QtWidgets.QPushButton("Execute")

        self.exec_btn.setAutoDefault(False)
        self.exec_btn.setDefault(False)

        # 버튼 색상 스타일 (개별 위젯에 적용)
        self.long_btn.setStyleSheet("""
            QPushButton {
                background-color: #2e7d32;
                color: white;
                font-weight: bold;
            }
            QPushButton:disabled {
                background-color: #4b4b4b;
                color: #aaaaaa;
            }
        """)
        self.short_btn.setStyleSheet("""
            QPushButton {
                background-color: #c62828;
                color: white;
                font-weight: bold;
            }
            QPushButton:disabled {
                background-color: #4b4b4b;
                color: #aaaaaa;
            }
        """)
        self.off_btn.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                color: white;
            }
            QPushButton:disabled {
                background-color: #4b4b4b;
                color: #aaaaaa;
            }
        """)
        self.exec_btn.setStyleSheet("""
            QPushButton {
                background-color: #1565c0;
                color: white;
                font-weight: bold;
            }
            QPushButton:disabled {
                background-color: #4b4b4b;
                color: #aaaaaa;
            }
        """)

        # FIX: 버튼 폭을 일정하게 맞추기
        for b in (self.long_btn, self.short_btn, self.off_btn, self.exec_btn):
            b.setMinimumWidth(90)

        self.price_label = QtWidgets.QLabel("Price: ...")
        self.quote_label = QtWidgets.QLabel("")
        self.fee_label = QtWidgets.QLabel("Builder Fee: -")
        self.info_label = QtWidgets.QLabel("📊 Position: N/A\n💰 Collateral: N/A")

        self.dex_combo = DexComboBox()
        self.dex_combo.addItems(self._dex_choices)

        self._build_layout()
        self._connect_signals()

    def _build_layout(self) -> None:
        """
        레이아웃을 2개의 행(HBox)로 나누어
        1행(T/Q/P)과 2행(Type/Long/Short/Off/Execute)이 서로 간섭하지 않도록 한다.
        """
        vbox = QtWidgets.QVBoxLayout(self)

        # NEW: 헤더 줄 (카드 제목)
        header_row = QtWidgets.QHBoxLayout()
        header_row.addWidget(self.title_label)
        header_row.addStretch(1)
        vbox.addLayout(header_row)

        # --- 1행: T / Q / P -------------------------------------------
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(8)

        # T: [ticker]
        row1.addWidget(QtWidgets.QLabel("T:"))
        row1.addWidget(self.ticker_edit, stretch=4)

        row1.addSpacing(8)

        # Q: [qty]
        row1.addWidget(QtWidgets.QLabel("Q:"))
        row1.addWidget(self.qty_edit, stretch=2)

        row1.addSpacing(8)

        # Type: [Market/Limit]
        row1.addWidget(QtWidgets.QLabel("Type:"))
        row1.addWidget(self.order_type_combo, stretch=3)

        row1.addSpacing(8)

        # P: [price]
        row1.addWidget(QtWidgets.QLabel("P:"))
        row1.addWidget(self.price_edit, stretch=3)

        vbox.addLayout(row1)

        # --- 2행: Long / Short / Off / Execute ------------------------
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(8)

        row2.addStretch(1)

        # 버튼들은 전부 Expanding 으로, 동일한 비율로 늘어나도록 설정
        for b in (self.long_btn, self.short_btn, self.off_btn, self.exec_btn):
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                            QtWidgets.QSizePolicy.Preferred)
            row2.addWidget(b)

        row2.addStretch(1)

        vbox.addLayout(row2)

        # --- 3행: Price / Quote / DEX / Fee ----------------------------
        hbox_price = QtWidgets.QHBoxLayout()
        hbox_price.addWidget(self.price_label)
        hbox_price.addWidget(self.quote_label)
        hbox_price.addStretch(1)
        hbox_price.addWidget(QtWidgets.QLabel("DEX:"))
        hbox_price.addWidget(self.dex_combo)
        hbox_price.addWidget(self.fee_label)

        vbox.addLayout(hbox_price)
        vbox.addWidget(self.info_label)

    def _connect_signals(self) -> None:
        self.exec_btn.clicked.connect(
            lambda: self.execute_clicked.emit(self.ex_name)
        )
        self.long_btn.clicked.connect(
            lambda: self.long_clicked.emit(self.ex_name)
        )
        self.short_btn.clicked.connect(
            lambda: self.short_clicked.emit(self.ex_name)
        )
        self.off_btn.clicked.connect(
            lambda: self.off_clicked.emit(self.ex_name)
        )

        # FIX: order_type_changed 신호 (market/limit)
        self.order_type_combo.currentTextChanged.connect(
            lambda text: self.order_type_changed.emit(self.ex_name, text.lower())
        )
        
        self.dex_combo.currentTextChanged.connect(
            lambda text: self.dex_changed.emit(self.ex_name, text)
        )
        self.ticker_edit.textChanged.connect(
            lambda text: self.ticker_changed.emit(self.ex_name, text)
        )

        # 팝업 열리는 동안 EX 버튼 비활성화 → 클릭 스루 방지
        self.dex_combo.popupOpened.connect(lambda: self.exec_btn.setEnabled(False))
        self.dex_combo.popupClosed.connect(lambda: self.exec_btn.setEnabled(True))

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
        """
        order_type: "market" | "limit"
        - Market 이면 P 입력칸 비활성화 + placeholder "auto"
        - Limit 이면 P 활성화 + placeholder 제거
        """
        order_type = (order_type or "market").lower()
        idx = 0 if order_type == "market" else 1
        if self.order_type_combo.currentIndex() != idx:
            self.order_type_combo.setCurrentIndex(idx)

        is_limit = (order_type == "limit")
        self.price_edit.setEnabled(is_limit)

        if is_limit:
            self.price_edit.setPlaceholderText("")      # FIX
        else:
            self.price_edit.setPlaceholderText("auto")  # FIX

    def set_side_enabled(self, enabled: bool, side: Optional[str]) -> None:
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
# Qt 위젯: 헤더
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
        self.dex_combo = DexComboBox()

        self._build_layout()
        self._connect_signals()

    def _build_layout(self) -> None:
        grid = QtWidgets.QGridLayout(self)

        # 1행: Ticker / Price / Total / Quit
        grid.addWidget(QtWidgets.QLabel("Ticker:"), 0, 0)
        grid.addWidget(self.ticker_edit,           0, 1)
        grid.addWidget(self.price_label,           0, 2)
        grid.addWidget(self.total_label,           0, 3)
        grid.addWidget(self.quit_btn,              0, 4)

        # 2행: All Qty / EXECUTE ALL / REVERSE / CLOSE ALL
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

        # 시스템 stdout/stderr 를 보여줄 콘솔 영역
        self.console_edit = QtWidgets.QPlainTextEdit()
        self.console_edit.setReadOnly(True)

        self.exchange_switch_container = QtWidgets.QWidget()
        self.exchange_switch_layout = QtWidgets.QGridLayout(self.exchange_switch_container)
        self.exchange_switches: Dict[str, QtWidgets.QCheckBox] = {}

        self.cards_container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.addStretch(1)
        self.cards: Dict[str, ExchangeCardWidget] = {}

        # FIX: stdout/stderr 리다이렉트는 나중에 install_console_redirect()에서 함
        self._stdout_orig = None
        self._stderr_orig = None
        self._stdout_stream: Optional[EmittingStream] = None
        self._stderr_stream: Optional[EmittingStream] = None
        self._console_redirect_installed: bool = False

        self._build_main_layout()
        self._connect_header_signals()

    def install_console_redirect(self) -> None:
        """
        UI가 뜬 뒤에만 stdout/stderr 를 콘솔로 리다이렉트.
        (UI 뜨기 전 print 는 터미널에 그대로 출력되도록)
        """
        if self._console_redirect_installed:
            return
        self._stdout_orig = sys.stdout
        self._stderr_orig = sys.stderr

        self._stdout_stream = EmittingStream()
        self._stderr_stream = EmittingStream()
        self._stdout_stream.text_written.connect(self._append_console_text)
        self._stderr_stream.text_written.connect(self._append_console_text)

        sys.stdout = self._stdout_stream
        sys.stderr = self._stderr_stream
        self._console_redirect_installed = True

    # UI 레이아웃 구성
    def _build_main_layout(self) -> None:
        """
        메인 레이아웃:
            [Header]
            [Cards (ScrollArea)]
            [Exchanges Switch Grid]  [Logs + Console]
        """
        central = QtWidgets.QWidget()
        main_vbox = QtWidgets.QVBoxLayout(central)

        # Header
        header_box = QtWidgets.QGroupBox()   # 기존: QtWidgets.QGroupBox("Header")
        header_layout = QtWidgets.QVBoxLayout(header_box)

        header_title = QtWidgets.QLabel("Header")
        header_title.setStyleSheet("color: #ffeb3b; font-weight: bold;")
        header_layout.addWidget(header_title)
        header_layout.addWidget(self.header)

        # Cards (ScrollArea)
        cards_scroll = QtWidgets.QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setWidget(self.cards_container)

        # Exchanges Switch
        switch_box = QtWidgets.QGroupBox()
        switch_layout = QtWidgets.QVBoxLayout(switch_box)
        ex_title = QtWidgets.QLabel("Exchanges")
        ex_title.setStyleSheet("color: #ffeb3b; font-weight: bold;")
        switch_layout.addWidget(ex_title)
        switch_layout.addWidget(self.exchange_switch_container)

        # Logs + Console
        logs_box = QtWidgets.QGroupBox()
        logs_layout = QtWidgets.QVBoxLayout(logs_box)

        logs_title = QtWidgets.QLabel("Logs")
        logs_title.setStyleSheet("color: #ffeb3b; font-weight: bold;")
        logs_layout.addWidget(logs_title)

        logs_layout.addWidget(QtWidgets.QLabel("Trading / App Log:"))
        logs_layout.addWidget(self.log_edit, stretch=3)

        logs_layout.addWidget(QtWidgets.QLabel("System stdout / stderr:"))
        logs_layout.addWidget(self.console_edit, stretch=2)

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

        # 창 우하단 크기 조절 핸들
        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().setSizeGripEnabled(True)

    def _connect_header_signals(self) -> None:
        self.header.ticker_changed.connect(self._on_header_ticker_changed)
        self.header.allqty_changed.connect(self._on_allqty_changed)
        self.header.exec_all_clicked.connect(self._on_exec_all)
        self.header.reverse_clicked.connect(self._on_reverse)
        self.header.close_all_clicked.connect(self._on_close_all_clicked)

        self.header.repeat_clicked.connect(
            lambda: self._log("[REPEAT] Qt UI에서는 아직 미구현입니다.")
        )
        self.header.burn_clicked.connect(
            lambda: self._log("[BURN] Qt UI에서는 아직 미구현입니다.")
        )
        self.header.quit_clicked.connect(self.close)
        self.header.dex_changed.connect(self._on_header_dex_changed)

    @QtCore.Slot(str)
    def _append_console_text(self, text: str) -> None:
        """print() 등에서 넘어온 텍스트를 콘솔 창에 표시."""
        text = text.replace("\r\n", "\n")
        if text.strip():
            self.console_edit.appendPlainText(text.rstrip("\n"))
            sb = self.console_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    # 초기 비동기 설정
    # ------------------------------------------------------------------

    async def async_init(self) -> None:
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

        self._build_exchange_switches()
        self._rebuild_cards()

        loop = asyncio.get_running_loop()
        self._price_task = loop.create_task(self._price_loop())
        self._status_task = loop.create_task(self._status_loop())

    # ------------------------------------------------------------------
    # 스위치 / 카드 구성
    # ------------------------------------------------------------------

    def _build_exchange_switches(self) -> None:
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

            ex_state = self.exchange_state[name]
            card.set_ticker(ex_state.symbol)
            card.set_order_type(ex_state.order_type)
            card.set_side_enabled(ex_state.enabled, ex_state.side)
            card.set_dex(ex_state.dex)
            card.set_fee_label("Builder Fee: -")

            card.execute_clicked.connect(self._on_exec_one_clicked)
            card.long_clicked.connect(self._on_long_clicked)
            card.short_clicked.connect(self._on_short_clicked)
            card.off_clicked.connect(self._on_off_clicked)
            card.order_type_changed.connect(self._on_order_type_changed)
            card.dex_changed.connect(self._on_card_dex_changed)
            card.ticker_changed.connect(self._on_card_ticker_changed)

            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        all_qty = self.header.allqty_edit.text()
        if all_qty:
            for c in self.cards.values():
                c.set_qty(all_qty)

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
            for ex_name in self.mgr.all_names():
                self.symbol_by_ex[ex_name] = coin
                st = self.exchange_state[ex_name]
                st.symbol = coin

            for card in self.cards.values():
                card.set_ticker(coin)
        finally:
            self._bulk_updating_tickers = False

    def _on_allqty_changed(self, text: str) -> None:
        for card in self.cards.values():
            card.set_qty(text or "")

    def _on_header_dex_changed(self, dex: str) -> None:
        self.header_dex = dex
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

    def _on_order_type_changed(self, ex_name: str, order_type: str) -> None:
        order_type = (order_type or "market").lower()
        self.order_type[ex_name] = order_type
        self.exchange_state[ex_name].order_type = order_type

        card = self.cards.get(ex_name)
        if card:
            card.set_order_type(order_type)
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
        scrollbar = self.log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _collateral_sum(self) -> float:
        return sum(self.collateral.values())

    def _update_header_total(self) -> None:
        self.header.set_total(self._collateral_sum())

    def _update_card_fee(self, ex_name: str) -> None:
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
        while not self._stopping:
            try:
                raw = self.header.ticker_edit.text() or "BTC"
                coin = _normalize_symbol_input(raw)
                self.symbol = coin

                px_str = self.current_price or "..."

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

                    if is_hl_like:
                        try:
                            quote_str = ex.get_perp_quote(sym)
                            card.set_quote_label(quote_str)
                        except Exception as e:
                            logger.info(f"[UI] quote update for {name} failed: {e}")
                            card.set_quote_label("")

                    try:
                        pos_str, col_str, col_val = await self.service.fetch_status(
                            name, sym, need_balance=need_collat, need_position=need_pos
                        )
                    except Exception as e:
                        logger.error(f"[UI] status update for {name} failed: {e}")
                        continue

                    if need_collat:
                        try:
                            self.collateral[name] = float(col_val)
                        except Exception:
                            pass
                        self._last_balance_at[name] = now
                        self._update_header_total()

                    if need_pos:
                        self._last_pos_at[name] = now

                    last_px = self.exchange_state[name].last_price
                    pos_pretty = _inject_usdc_value_into_pos(last_px, pos_str)
                    col_pretty = _strip_bracket_markup(col_str)
                    card.set_info_text(pos_pretty, col_pretty)

                await asyncio.sleep(RATE["GAP_FOR_INF"])
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("[CRITICAL] Unhandled error in status_loop", exc_info=True)
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
        self._stopping = True

        # stdout/stderr 원복
        if self._console_redirect_installed:
            sys.stdout = self._stdout_orig or sys.__stdout__
            sys.stderr = self._stderr_orig or sys.__stderr__

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

        try:
            await self.mgr.close_all()
        except Exception:
            pass

        try:
            await self._kill_ccxt_throttlers()
        except Exception:
            pass

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
    """
    app = QtWidgets.QApplication(sys.argv)
    _apply_app_style(app)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = UiQtApp(manager)

    async def _startup():
        # FIX: 초기화(로그인 등) 동안의 print 는 터미널에 출력되도록
        await window.async_init()
        window.show()
        # UI 가 뜬 이후부터는 stdout/stderr 를 UI 콘솔로 리다이렉트
        window.install_console_redirect()

    loop.create_task(_startup())

    with loop:
        loop.run_forever()


if __name__ == "__main__":
    print("This module is intended to be imported and used with an ExchangeManager.")
    print("예: run_qt_app(ExchangeManager(...))")