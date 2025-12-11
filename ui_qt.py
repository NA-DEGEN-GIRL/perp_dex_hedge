#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qt 기반 UI (PySide6) 구현.
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

# 색상 정의 (미니멀: 3가지만)
CLR_TEXT = "#e0e0e0"       # 기본 텍스트
CLR_MUTED = "#888888"      # 보조 텍스트 (라벨)
CLR_ACCENT = "#4fc3f7"     # 포인트 (가격/중요 값)
CLR_COLLATERAL = "rgba(139, 125, 77, 1)" # collaterals
        

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 전역 설정
# ---------------------------------------------------------------------------

# 기본적으로 이모지 사용을 꺼서 깨짐(□□□) 방지. 폰트 설치 후 True로 변경 가능.
# 환경변수 PDEX_UI_USE_EMOJI=1 로도 켤 수 있음.
USE_EMOJI = os.getenv("PDEX_UI_USE_EMOJI", "0") == "1"

UI_FONT_FAMILY = os.getenv("PDEX_UI_FONT_FAMILY", "")
UI_FONT_SIZE = int(os.getenv("PDEX_UI_FONT_SIZE", "16"))
UI_THEME = os.getenv("PDEX_UI_THEME", "dark").lower()

UI_WINDOW_WIDTH = int(os.getenv("PDEX_UI_WIDTH", "1400"))
UI_WINDOW_HEIGHT = int(os.getenv("PDEX_UI_HEIGHT", "1600"))

def _format_size(value: float) -> str:
    """
    사이즈 포맷팅 - 값 크기에 따라 적절한 소수점 자릿수 사용
    """
    abs_val = abs(value)
    if abs_val == 0:
        return "0"
    elif abs_val >= 10:
        result = f"{value:,.2f}"
    elif abs_val >= 1:
        result = f"{value:,.3f}"
    elif abs_val >= 0.1:
        result = f"{value:,.4f}"
    elif abs_val >= 0.01:
        result = f"{value:,.5f}"
    else:
        result = f"{value:,.6f}"
    
    # 뒤의 불필요한 0 제거 (소수점 있을 때만)
    if '.' in result:
        result = result.rstrip('0').rstrip('.')
    return result
    
    
def _format_collateral(value: float) -> str:
    """잔고 포맷팅 - 소수점 1자리"""
    return f"{value:,.1f}"

def _apply_app_style(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")

    # 기본 폰트 설정
    font = app.font()
    if UI_FONT_FAMILY:
        font.setFamily(UI_FONT_FAMILY)
    if UI_FONT_SIZE > 0:
        font.setPointSize(UI_FONT_SIZE)
    app.setFont(font)

    # 다크 테마 팔레트
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
        palette.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor(160, 160, 160))
        app.setPalette(palette)

    # 스타일시트 (폰트 리스트 fallback 포함)
    base_font_size = UI_FONT_SIZE
    log_font_size = max(UI_FONT_SIZE - 1, 9)

    font_families = []
    if UI_FONT_FAMILY:
        font_families.append(UI_FONT_FAMILY)
    font_families += [
        "Noto Sans CJK KR", "Malgun Gothic", "Segoe UI", 
        "Noto Color Emoji", "Segoe UI Emoji", "Apple Color Emoji", 
        "Sans"
    ]
    css_fonts = ", ".join(f'"{f}"' for f in font_families)

    style = f"""
    QWidget {{
        font-size: {base_font_size}pt;
        font-family: {css_fonts};
    }}
    QGroupBox {{
        font-weight: bold;
        border: 1px solid #777;
        border-radius: 6px;
        margin-top: 6px;
        padding-top: 10px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px;
    }}
    QPushButton {{
        border: 1px solid #555;
        border-radius: 4px;
        padding: 5px;
        background-color: #444;
    }}
    QPushButton:hover {{
        background-color: #555;
    }}
    QPushButton:disabled {{
        background-color: #333;
        color: #777;
        border: 1px solid #333;
    }}
    QLineEdit {{
        padding: 4px;
        border: 1px solid #555;
        border-radius: 3px;
        background-color: #2b2b2b;
        color: white;
    }}
    /* 콤보박스 본체 */
    QComboBox {{
        padding: 4px;
        border: 1px solid #555;
        border-radius: 3px;
        background-color: #2b2b2b;
        color: white;
    }}
    QComboBox::drop-down {{
        border: 0px;
        width: 20px;
    }}
    /* [추가] 콤보박스 펼쳤을 때 나오는 리스트 디자인 */
    QComboBox QAbstractItemView {{
        border: 1px solid #555;
        background-color: #2b2b2b;
        color: white;
        selection-background-color: #1976d2;
        outline: none;
        padding: 4px;
    }}
    QPlainTextEdit {{
        font-family: {css_fonts};
        font-size: {log_font_size}pt;
        background-color: #1e1e1e;
        border: 1px solid #555;
    }}
    QScrollBar:vertical {{
        width: 12px;
        background: #2b2b2b;
    }}
    QScrollBar::handle:vertical {{
        background: #555;
        border-radius: 4px;
    }}
    """
    app.setStyleSheet(style)


# ---------------------------------------------------------------------------
# 로깅 등 유틸
# ---------------------------------------------------------------------------

def _ensure_ts_logger() -> None:
    if getattr(logger, "_ts_logger_attached", False):
        return
    lvl_name = os.getenv("PDEX_TS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, lvl_name, logging.INFO)
    log_file = "ui.log"
    to_console = os.getenv("PDEX_TS_LOG_CONSOLE", "0") == "1"
    propagate = os.getenv("PDEX_TS_PROPAGATE", "0") == "1"

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    
    # 기존 파일 핸들러 제거
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler):
            if os.path.abspath(getattr(h, "baseFilename", "")) == os.path.abspath(log_file):
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


_ensure_ts_logger()


RATE = {
    "GAP_FOR_INF": 0.1,
    "STATUS_POS_INTERVAL": {"default": 0.5, "lighter": 2.0},
    "STATUS_COLLATERAL_INTERVAL": {"default": 0.5, "lighter": 5.0},
    "CARD_PRICE_INTERVAL": {"default": 1.0, "lighter": 5.0},
}

def _normalize_symbol_input(sym: str) -> str:
    if not sym: return ""
    s = sym.strip()
    return s.split(":", 1)[1].upper() if ":" in s else s.upper()

def _compose_symbol(dex: str, coin: str) -> str:
    c = (coin or "").upper()
    return f"{dex.lower()}:{c}" if dex and dex != "HL" else c

def _strip_bracket_markup(s: str) -> str:
    # [green]...[/] 제거
    return re.sub(r"\[[a-zA-Z_\/]+\]", "", s)

def _inject_usdc_value_into_pos(price: Optional[float], pos_str: str) -> str:
    """
    urwid용 마크업 문자열에서 수량 추출 후 USDC 가치 병기.
    """
    clean_str = _strip_bracket_markup(pos_str)
    if price is None:
        return clean_str

    # "LONG 0.123 ..." 패턴 찾기
    # 단순하게 "LONG" 또는 "SHORT" 뒤의 숫자를 찾음
    m = re.search(r"(LONG|SHORT)\s+([+-]?\d+(?:\.\d+)?)", clean_str)
    if not m:
        return clean_str

    side_str = m.group(1)
    size_str = m.group(2)
    try:
        size = float(size_str)
        usdc_val = size * price
        # 가독성을 위해 포맷팅
        new_part = f"{side_str} {size_str} ({usdc_val:,.1f} $)"
        # 원본 문자열 치환
        return clean_str.replace(f"{side_str} {size_str}", new_part)
    except:
        return clean_str

@dataclass
class ExchangeState:
    symbol: str = "BTC"
    enabled: bool = False
    side: Optional[str] = None
    order_type: str = "market"
    collateral: float = 0.0
    last_price: Optional[float] = None
    dex: str = "HL"


# ---------------------------------------------------------------------------
# 커스텀 콤보박스 (클릭 시 닫힘 문제 해결)
# ---------------------------------------------------------------------------

class DexComboBox(QtWidgets.QComboBox):
    """
    팝업 열림/닫힘 시그널만 추가한 단순 콤보박스.
    마우스 클릭 선택은 Qt 기본 동작에 맡깁니다.
    """
    popupOpened = QtCore.Signal()
    popupClosed = QtCore.Signal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        # 기본 QComboBox의 activated 시그널을 사용
        # (항목이 선택되면 자동으로 발생)
        self.activated.connect(self._on_activated)
        # 스크롤 휠로 값 변경 방지
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def _on_activated(self, index: int) -> None:
        # 선택 후 명시적으로 팝업 닫기
        self.hidePopup()

    def showPopup(self) -> None:
        self.popupOpened.emit()
        super().showPopup()

    def hidePopup(self) -> None:
        self.popupClosed.emit()
        super().hidePopup()


# ---------------------------------------------------------------------------
# 콘솔 리다이렉터
# ---------------------------------------------------------------------------

class EmittingStream(QtCore.QObject):
    text_written = QtCore.Signal(str)
    def write(self, text: str):
        self.text_written.emit(str(text))
    def flush(self):
        pass


# ---------------------------------------------------------------------------
# 거래소 카드 위젯
# ---------------------------------------------------------------------------

class ExchangeCardWidget(QtWidgets.QGroupBox):
    execute_clicked = QtCore.Signal(str)
    long_clicked = QtCore.Signal(str)
    short_clicked = QtCore.Signal(str)
    off_clicked = QtCore.Signal(str)
    order_type_changed = QtCore.Signal(str, str)
    dex_changed = QtCore.Signal(str, str)
    ticker_changed = QtCore.Signal(str, str)

    def __init__(self, ex_name: str, dex_choices: List[str], is_hl_like: bool = True, parent=None):
        super().__init__(parent)
        self.ex_name = ex_name
        self._is_hl_like = is_hl_like
        
        # GroupBox 타이틀 대신 안쪽 라벨 사용
        self.setTitle("") 

        self._dex_choices = dex_choices[:] or ["HL"]

        # 카드 제목
        self.title_label = QtWidgets.QLabel(f"[{ex_name.upper()}]")
        self.title_label.setStyleSheet(f"color: rgba(186, 160, 85, 1); font-size: {UI_FONT_SIZE}pt;")
        
        self._current_price: Optional[float] = None

        # 포지션 행
        self.pos_side_label = QtWidgets.QLabel("")
        self.pos_size_label = QtWidgets.QLabel("")
        self.pos_pnl_label = QtWidgets.QLabel("")
        
        # 잔고 행 (Perp | Spot)
        self.collat_perp_label = QtWidgets.QLabel("")
        self.collat_spot_label = QtWidgets.QLabel("")
        
        # 입력 위젯
        self.ticker_edit = QtWidgets.QLineEdit()
        self.qty_edit = QtWidgets.QLineEdit()
        self.price_edit = QtWidgets.QLineEdit()

        # Type: DexComboBox 사용
        self.market_btn = QtWidgets.QPushButton("Market")
        self.limit_btn = QtWidgets.QPushButton("Limit")
        
        self.market_btn.setCheckable(True)
        self.limit_btn.setCheckable(True)
        self.market_btn.setChecked(True)  # 기본값: Market
        BTN_ORDER_TYPE = """
            QPushButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 12px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
            QPushButton:checked {
                background-color: #1b3146ff;
                border: 2px solid #93b4c4ff;
                color: #93b4c4ff;
            }
        """
        self.market_btn.setStyleSheet(BTN_ORDER_TYPE)
        self.limit_btn.setStyleSheet(BTN_ORDER_TYPE)

        # 버튼
        self.long_btn = QtWidgets.QPushButton("Long")
        self.short_btn = QtWidgets.QPushButton("Short")
        self.off_btn = QtWidgets.QPushButton("미선택")
        self.exec_btn = QtWidgets.QPushButton("주문 실행")

        self.exec_btn.setAutoDefault(False)
        self.exec_btn.setDefault(False)

        BTN_BASE = """
            QPushButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }
            QPushButton:checked {
                border: 2px solid #888;
            }
        """
        
        BTN_LONG = """
            QPushButton {
                background-color: #3a3a3a;
                color: #81c784;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #81c784;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }
            QPushButton:checked {
                border: 2px solid #81c784;
                background-color: #2e3d2e;
            }
        """
        
        BTN_SHORT = """
            QPushButton {
                background-color: #3a3a3a;
                color: #ef9a9a;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #ef9a9a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }
            QPushButton:checked {
                border: 2px solid #ef9a9a;
                background-color: #3d2e2e;
            }
        """
        
        BTN_EXEC = """
            QPushButton {
                background-color: #3a3a3a;
                color: #90caf9;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #90caf9;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }
        """

        self.long_btn.setStyleSheet(BTN_LONG)
        self.short_btn.setStyleSheet(BTN_SHORT)
        self.off_btn.setStyleSheet(BTN_BASE)
        self.exec_btn.setStyleSheet(BTN_EXEC)

        # 정보 라벨
        self.price_title = QtWidgets.QLabel("가격: ")
        self.price_title.setStyleSheet(f"color: {CLR_MUTED};")
        self.price_label = QtWidgets.QLabel("...")
        self.price_label.setStyleSheet("color: #81d4fa;")
        
        self.quote_label = QtWidgets.QLabel("")
        self.quote_label.setStyleSheet(f"color: {CLR_COLLATERAL};")
        if self._is_hl_like:
            self.fee_label = QtWidgets.QLabel("Builder Fee: -")
            self.fee_label.setStyleSheet("color: #aaaaaa;")
            self.dex_combo = DexComboBox()
            self.dex_combo.addItems(self._dex_choices)
            self.dex_label = QtWidgets.QLabel("DEX:")
        else:
            self.fee_label = None
            self.dex_combo = None
            self.dex_label = None

        # Position / Account Info
        #self.info_pos_label = QtWidgets.QLabel("포지션: N/A")
        #self.info_acc_label = QtWidgets.QLabel("잔고: N/A")
        # 가독성을 위해 약간의 마진과 폰트 조정
        #self.info_pos_label.setStyleSheet("margin-top: 4px; color: #e0e0e0;")
        #self.info_acc_label.setStyleSheet("margin-bottom: 4px; color: #bdbdbd;")

        self._build_layout()
        self._connect_signals()

    def _build_layout(self) -> None:
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(6)

        # 1. 헤더 행: 비율로 정렬
        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(10)
        
        # 거래소 이름 (비율 2)
        header_row.addWidget(self.title_label, stretch=2)
        
        # 가격 + Quote (비율 2)
        price_container = QtWidgets.QWidget()
        price_layout = QtWidgets.QHBoxLayout(price_container)
        price_layout.setContentsMargins(0, 0, 0, 0)
        price_layout.setSpacing(4)
        price_layout.addWidget(self.price_label)
        price_layout.addWidget(self.quote_label)
        header_row.addWidget(price_container, stretch=2)
        
        if self._is_hl_like and self.fee_label:
            header_row.addWidget(self.fee_label, stretch=2)
        else:
            header_row.addWidget(QtWidgets.QWidget(), stretch=2)
        
        header_row.addStretch(4)
        
        for b in (self.long_btn, self.short_btn, self.off_btn, self.exec_btn):
            header_row.addWidget(b, stretch=1)
        
        main_layout.addLayout(header_row)

        # 2. 입력 행: 비율로 정렬
        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(10)

        def add_field(label_txt, widget, stretch=1):
            lbl = QtWidgets.QLabel(label_txt)
            lbl.setStyleSheet(f"color: {CLR_MUTED};")
            input_row.addWidget(lbl)
            input_row.addWidget(widget, stretch=stretch)

        
        if self._is_hl_like:
            add_field("심볼:", self.ticker_edit, stretch=2)
            input_row.addWidget(self.dex_combo, stretch=1)
        else:
            add_field("심볼:", self.ticker_edit, stretch=3)

        add_field("수량:", self.qty_edit, stretch=2)
        
        # 주문 타입 버튼 (각 비율 1)
        input_row.addWidget(self.market_btn, stretch=1)
        input_row.addWidget(self.limit_btn, stretch=1)
        
        add_field("주문 가격:", self.price_edit, stretch=2)
        input_row.addStretch(1)
        
        main_layout.addLayout(input_row)

        # 3. 포지션 행: [포지션] [LONG/SHORT] [Size: xxx] [PnL: xxx]
        pos_row = QtWidgets.QHBoxLayout()
        pos_row.setSpacing(6)
        
        pos_title = QtWidgets.QLabel("포지션")
        pos_title.setStyleSheet(f"color: {CLR_MUTED};")
        pos_title.setFixedWidth(80)
        pos_row.addWidget(pos_title)
        
        self.pos_side_label.setFixedWidth(80)
        pos_row.addWidget(self.pos_side_label)
        
        pos_row.addWidget(self.pos_size_label)
        pos_row.addSpacing(20)
        pos_row.addWidget(self.pos_pnl_label)
        pos_row.addStretch()
        
        main_layout.addLayout(pos_row)

        # 4. 잔고 행: [잔고] [Perp: xxx USDC] [|] [Spot: xx USDC | xx USDH | ...]
        collat_row = QtWidgets.QHBoxLayout()
        collat_row.setSpacing(6)
        
        collat_title = QtWidgets.QLabel("잔고")
        collat_title.setStyleSheet(f"color: {CLR_MUTED};")
        collat_title.setFixedWidth(80)
        collat_row.addWidget(collat_title)
        
        perp_lbl = QtWidgets.QLabel("Perp:")
        perp_lbl.setStyleSheet(f"color: {CLR_MUTED};")
        collat_row.addWidget(perp_lbl)
        collat_row.addWidget(self.collat_perp_label)
        
        collat_row.addSpacing(15)
        
        sep_lbl = QtWidgets.QLabel("|")
        sep_lbl.setStyleSheet("color: #444;")
        collat_row.addWidget(sep_lbl)
        
        collat_row.addSpacing(15)
        
        spot_lbl = QtWidgets.QLabel("Spot:")
        spot_lbl.setStyleSheet(f"color: {CLR_MUTED};")
        collat_row.addWidget(spot_lbl)
        collat_row.addWidget(self.collat_spot_label)
        
        collat_row.addStretch()
        
        main_layout.addLayout(collat_row)

    def _on_market_clicked(self):
        self.market_btn.setChecked(True)
        self.limit_btn.setChecked(False)
        self.price_edit.setEnabled(False)
        self.price_edit.setPlaceholderText("auto")
        self.order_type_changed.emit(self.ex_name, "market")

    def _on_limit_clicked(self):
        self.market_btn.setChecked(False)
        self.limit_btn.setChecked(True)
        self.price_edit.setEnabled(True)
        self.price_edit.setPlaceholderText("")
        self.order_type_changed.emit(self.ex_name, "limit")

    def _connect_signals(self) -> None:
        self.exec_btn.clicked.connect(lambda: self.execute_clicked.emit(self.ex_name))
        self.long_btn.clicked.connect(lambda: self.long_clicked.emit(self.ex_name))
        self.short_btn.clicked.connect(lambda: self.short_clicked.emit(self.ex_name))
        self.off_btn.clicked.connect(lambda: self.off_clicked.emit(self.ex_name))
        
        self.market_btn.clicked.connect(self._on_market_clicked)
        self.limit_btn.clicked.connect(self._on_limit_clicked)

        self.ticker_edit.textChanged.connect(
            lambda text: self.ticker_changed.emit(self.ex_name, text)
        )

        if self._is_hl_like and self.dex_combo:
            self.dex_combo.currentTextChanged.connect(
                lambda text: self.dex_changed.emit(self.ex_name, text)
            )
            # DEX 팝업 열림 동안 Exec 버튼 막기
            self.dex_combo.popupOpened.connect(lambda: self.exec_btn.setEnabled(False))
            self.dex_combo.popupClosed.connect(lambda: self.exec_btn.setEnabled(True))
        
    def set_ticker(self, t): 
        if self.ticker_edit.text() != t: self.ticker_edit.setText(t)
    def set_qty(self, q):
        if self.qty_edit.text() != q: self.qty_edit.setText(q)
    def get_qty(self): return self.qty_edit.text().strip()
    def get_price_text(self): return self.price_edit.text().strip()
    
    def set_price_label(self, px): 
        self.price_label.setText(f"{px}")
        try:
            self._current_price = float(str(px).replace(",", ""))
        except:
            self._current_price = None

    def set_quote_label(self, txt): self.quote_label.setText(txt or "")
    
    def set_fee_label(self, txt):
        if self.fee_label:
            self.fee_label.setText(txt)
    
    def set_status_info(self, json_data: dict):
        """
        json_data format:
        {
            "collateral": {
                "perp": {"USDC": 12.1},  # or None
                "spot": {"USDT": 10.2, "USDC": 15.0, ...}  # or None
            },
            "position": {
                "size": 0.002,
                "side": "short",  # "long" or "short"
                "unrealized_pnl": 1.2
            }  # or None
        }
        """
        CLR_LONG = "#81c784"
        CLR_SHORT = "#ef9a9a"
        CLR_NEUTRAL = "#e0e0e0"
        CLR_PNL_POS = "#4caf50"
        CLR_PNL_NEG = "#f44336"
        
        # 포지션 처리
        position = json_data.get("position") if json_data else None
        if position and position.get("size", 0) != 0:
            side = position.get("side", "").upper()
            size = abs(position.get("size", 0))
            pnl = position.get("unrealized_pnl", 0)
            
            # 방향 표시
            if side == "LONG":
                self.pos_side_label.setText("LONG")
                self.pos_side_label.setStyleSheet(f"color: {CLR_LONG};")
            elif side == "SHORT":
                self.pos_side_label.setText("SHORT")
                self.pos_side_label.setStyleSheet(f"color: {CLR_SHORT};")
            else:
                self.pos_side_label.setText("")
                self.pos_side_label.setStyleSheet(f"color: {CLR_MUTED};")
            
            # 사이즈 표시 + USD 값
            size_text = _format_size(size)
            if self._current_price and self._current_price > 0:
                usd_value = size * self._current_price
                size_text += f" <span style='color: {CLR_MUTED};'>({usd_value:,.1f}$)</span>"
            self.pos_size_label.setText(size_text)
            self.pos_size_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.pos_size_label.setStyleSheet(f"color: {CLR_NEUTRAL};")
            
            # PnL 표시
            pnl_color = CLR_PNL_POS if pnl >= 0 else CLR_PNL_NEG
            pnl_sign = "+" if pnl >= 0 else ""
            self.pos_pnl_label.setText(f"PNL: {pnl_sign}{pnl:,.1f}")
            self.pos_pnl_label.setStyleSheet(f"color: {pnl_color};")
        else:
            self.pos_side_label.setText("")
            self.pos_side_label.setStyleSheet(f"color: {CLR_MUTED};")
            self.pos_size_label.setText("")
            self.pos_size_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self.pos_size_label.setStyleSheet(f"color: {CLR_MUTED};")
            self.pos_pnl_label.setText("")
            self.pos_pnl_label.setStyleSheet(f"color: {CLR_MUTED};")
        
        # 잔고 처리
        collateral = json_data.get("collateral") if json_data else None
        
        # Perp 잔고 - key도 muted 색상으로
        perp_data = collateral.get("perp") if collateral else None
        if perp_data and any(v != 0 for v in perp_data.values()):
            perp_parts = []
            for k, v in perp_data.items():
                if v != 0:
                    perp_parts.append(f"{_format_collateral(v)} <span style='color:{CLR_COLLATERAL};'>{k}</span>")
            self.collat_perp_label.setText(", ".join(perp_parts) if perp_parts else "")
            self.collat_perp_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.collat_perp_label.setStyleSheet(f"color: {CLR_NEUTRAL};")
        else:
            self.collat_perp_label.setText("")
            self.collat_perp_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self.collat_perp_label.setStyleSheet(f"color: {CLR_MUTED};")
        
        # Spot 잔고 - 간격 넓히고 깔끔하게
        spot_data = collateral.get("spot") if collateral else None
        if spot_data and any(v != 0 for v in spot_data.values()):
            spot_parts = []
            for k, v in spot_data.items():
                if v != 0:
                    spot_parts.append(
                        f"<span style='background-color:#333; padding:3px 8px; border-radius:3px;'>"
                        f"{_format_collateral(v)} <span style='color:{CLR_COLLATERAL};'>{k}</span></span>"
                    )
            # 간격을 위해 &nbsp; 4개 사용
            self.collat_spot_label.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(spot_parts) if spot_parts else "")
            self.collat_spot_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.collat_spot_label.setStyleSheet(f"color: {CLR_NEUTRAL};")
        else:
            self.collat_spot_label.setText("")
            self.collat_spot_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self.collat_spot_label.setStyleSheet(f"color: {CLR_MUTED};")

    def set_order_type(self, otype):
        otype = (otype or "market").lower()
        is_market = (otype == "market")
        
        self.market_btn.setChecked(is_market)
        self.limit_btn.setChecked(not is_market)
        
        self.price_edit.setEnabled(not is_market)
        self.price_edit.setPlaceholderText("auto" if is_market else "")

    def set_side_enabled(self, enabled, side):
        for b in (self.long_btn, self.short_btn, self.off_btn):
            b.setCheckable(True)
            b.setChecked(False)
        
        if not enabled:
            self.off_btn.setChecked(True)
        else:
            if side == "buy": self.long_btn.setChecked(True)
            elif side == "sell": self.short_btn.setChecked(True)

    def set_dex(self, dex):
        if self.dex_combo:
            idx = self.dex_combo.findText(dex, QtCore.Qt.MatchFlag.MatchFixedString)
            if idx >= 0: self.dex_combo.setCurrentIndex(idx)


# ---------------------------------------------------------------------------
# 헤더 위젯
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        # 버튼 스타일 (단일 스타일)
        BTN_STYLE = """
            QPushButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
        """
        
        BTN_DANGER_STYLE = """
            QPushButton {
                background-color: #3a3a3a;
                color: #ef5350;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #ef5350;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
        """

        # ===== 위젯 생성 =====
        
        # Row 1 위젯들
        self.ticker_edit = QtWidgets.QLineEdit("BTC")
        self.ticker_edit.setFixedWidth(120)
        
        self.price_label = QtWidgets.QLabel("...")
        self.price_label.setStyleSheet(f"color: {CLR_ACCENT};")
        
        self.total_label = QtWidgets.QLabel("$0.00")
        self.total_label.setStyleSheet(f"color: {CLR_ACCENT};")
        
        self.allqty_edit = QtWidgets.QLineEdit()
        self.allqty_edit.setFixedWidth(100)
        
        self.dex_combo = DexComboBox()
        self.dex_combo.setFixedWidth(100)
        
        # Row 1 버튼들
        self.exec_all_btn = QtWidgets.QPushButton("전체 주문 수행")
        self.exec_all_btn.setStyleSheet(BTN_STYLE)
        
        self.reverse_btn = QtWidgets.QPushButton("롱/숏 전환")
        self.reverse_btn.setStyleSheet(BTN_STYLE)
        
        self.close_all_btn = QtWidgets.QPushButton("모든 포지션 종료")
        self.close_all_btn.setStyleSheet(BTN_DANGER_STYLE)
        
        self.quit_btn = QtWidgets.QPushButton("프로그램 종료")
        self.quit_btn.setStyleSheet(BTN_DANGER_STYLE)
        
        # Row 2 위젯들 (REPEAT)
        self.repeat_times = QtWidgets.QLineEdit()
        self.repeat_times.setFixedWidth(60)
        self.repeat_min = QtWidgets.QLineEdit()
        self.repeat_min.setFixedWidth(80)
        self.repeat_max = QtWidgets.QLineEdit()
        self.repeat_max.setFixedWidth(80)
        self.repeat_btn = QtWidgets.QPushButton("반복 실행")
        self.repeat_btn.setStyleSheet(BTN_STYLE)
        
        # Row 2 위젯들 (BURN)
        self.burn_count = QtWidgets.QLineEdit()
        self.burn_count.setFixedWidth(60)
        self.burn_min = QtWidgets.QLineEdit()
        self.burn_min.setFixedWidth(80)
        self.burn_max = QtWidgets.QLineEdit()
        self.burn_max.setFixedWidth(80)
        self.burn_btn = QtWidgets.QPushButton("태우기 실행")
        self.burn_btn.setStyleSheet(BTN_STYLE)

        # ===== 레이아웃 =====
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(8)

        rows = []

        row_id = 0
        rows.append(QtWidgets.QHBoxLayout())
        rows[row_id].addWidget(self._label("가격 ($)", CLR_MUTED))
        rows[row_id].addWidget(self.price_label)
        rows[row_id].addSpacing(20)
        rows[row_id].addWidget(self._label("총 잔고($)", CLR_MUTED))
        rows[row_id].addWidget(self.total_label)
        rows[row_id].addStretch()
        rows[row_id].addWidget(self.close_all_btn)
        rows[row_id].addWidget(self.quit_btn)
        
        #main_layout.addLayout(rows[row_id])

        # --- Row 1: 메인 컨트롤 ---
        row_id += 1
        rows.append(QtWidgets.QHBoxLayout())
        rows[row_id].setSpacing(8)
        
        rows[row_id].addWidget(self._label("거래 심볼", CLR_MUTED))
        rows[row_id].addWidget(self.ticker_edit)
        
        rows[row_id].addSpacing(20)
        
        rows[row_id].addWidget(self._label("수량", CLR_MUTED))
        rows[row_id].addWidget(self.allqty_edit)
        
        rows[row_id].addSpacing(20)
        
        rows[row_id].addWidget(self._label("DEX", CLR_MUTED))
        rows[row_id].addWidget(self.dex_combo)
        
        #rows[row_id].addStretch()
        rows[row_id].setSpacing(20)
        
        rows[row_id].addWidget(self.exec_all_btn)
        rows[row_id].addWidget(self.reverse_btn)
        rows[row_id].addStretch()
        
        
        #main_layout.addLayout(rows[row_id])

        # REPEAT
        row_id += 1
        rows.append(QtWidgets.QHBoxLayout())
        rows[row_id].setSpacing(8)
        rows[row_id].addWidget(self._label("반복 수행횟수", CLR_MUTED))
        rows[row_id].addWidget(self.repeat_times)
        rows[row_id].addWidget(self._label("반복 대기시간(초)", CLR_MUTED))
        rows[row_id].addWidget(self.repeat_min)
        rows[row_id].addWidget(self._label("~", CLR_MUTED))
        rows[row_id].addWidget(self.repeat_max)
        rows[row_id].addWidget(self.repeat_btn)
        rows[row_id].addStretch()
        
        # BURN
        row_id += 1
        rows.append(QtWidgets.QHBoxLayout())
        rows[row_id].setSpacing(8)
        rows[row_id].addWidget(self._label("태우기 횟수", CLR_MUTED))
        rows[row_id].addWidget(self.burn_count)
        rows[row_id].addWidget(self._label("태우기 대기시간(초)", CLR_MUTED))
        rows[row_id].addWidget(self.burn_min)
        rows[row_id].addWidget(self._label("~", CLR_MUTED))
        rows[row_id].addWidget(self.burn_max)
        rows[row_id].addWidget(self.burn_btn)
        rows[row_id].addStretch()
        
        for row in rows:
            main_layout.addLayout(row)

    def _label(self, text, color):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(f"color: {color};")
        return lbl

    def _connect_signals(self):
        self.ticker_edit.textChanged.connect(self.ticker_changed)
        self.allqty_edit.textChanged.connect(self.allqty_changed)
        self.exec_all_btn.clicked.connect(self.exec_all_clicked)
        self.reverse_btn.clicked.connect(self.reverse_clicked)
        self.close_all_btn.clicked.connect(self.close_all_clicked)
        self.repeat_btn.clicked.connect(self.repeat_clicked)
        self.burn_btn.clicked.connect(self.burn_clicked)
        self.quit_btn.clicked.connect(self.quit_clicked)
        self.dex_combo.currentTextChanged.connect(self.dex_changed)

    def set_price(self, p):
        self.price_label.setText(str(p))
    
    def set_total(self, t):
        self.total_label.setText(f"{t:,.1f}")
    
    def set_dex_choices(self, dexs, cur):
        self.dex_combo.blockSignals(True)
        self.dex_combo.clear()
        self.dex_combo.addItems(dexs)
        idx = self.dex_combo.findText(cur, QtCore.Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            self.dex_combo.setCurrentIndex(idx)
        self.dex_combo.blockSignals(False)

# ---------------------------------------------------------------------------
# 메인 앱
# ---------------------------------------------------------------------------

class UiQtApp(QtWidgets.QMainWindow):
    def __init__(self, manager: ExchangeManager):
        super().__init__()
        self.setWindowTitle("Perp DEX Hedge (Qt)")
        self.mgr = manager
        self.service = TradingService(self.mgr)

        # State
        names = self.mgr.all_names()
        self.symbol = "BTC"
        self.current_price = "..."
        self.enabled = {n: False for n in names}
        self.side = {n: None for n in names}
        self.order_type = {n: "market" for n in names}
        self.collateral = {n: 0.0 for n in names}
        self.symbol_by_ex = {n: "BTC" for n in names}
        self.dex_by_ex = {n: "HL" for n in names}
        self.dex_names = ["HL"]
        self.header_dex = "HL"
        self.exchange_state = {n: ExchangeState(symbol="BTC") for n in names}

        # Tasks state
        self._stopping = False
        self._price_task = None
        self._status_task = None
        self._last_balance_at = {}
        self._last_pos_at = {}
        self._last_price_at = {}

        # Components
        self.header = HeaderWidget()
        self.log_edit = QtWidgets.QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.console_edit = QtWidgets.QPlainTextEdit()
        self.console_edit.setReadOnly(True)

        self.exchange_switch_container = QtWidgets.QWidget()
        self.exchange_switch_layout = QtWidgets.QGridLayout(self.exchange_switch_container)
        self.exchange_switches = {}

        self.cards_container = QtWidgets.QWidget()
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.addStretch(1)
        self.cards = {}

        # Console redirect setup
        self._stdout_orig = None
        self._stderr_orig = None
        self._stdout_stream = None
        self._stderr_stream = None
        self._console_redirect_installed = False

        self._build_main_layout()
        self._connect_header_signals()

    def install_console_redirect(self):
        if self._console_redirect_installed: return
        self._stdout_orig = sys.stdout
        self._stderr_orig = sys.stderr
        self._stdout_stream = EmittingStream()
        self._stderr_stream = EmittingStream()
        self._stdout_stream.text_written.connect(self._append_console_text)
        self._stderr_stream.text_written.connect(self._append_console_text)
        sys.stdout = self._stdout_stream
        sys.stderr = self._stderr_stream
        self._console_redirect_installed = True

    def _build_main_layout(self):
        central = QtWidgets.QWidget()
        main_vbox = QtWidgets.QVBoxLayout(central)

        # Helper to create titled section
        def create_section(title, widget, layout_type=QtWidgets.QVBoxLayout):
            gb = QtWidgets.QGroupBox()
            # GroupBox 타이틀 대신 내부 라벨 사용
            gb_layout = layout_type(gb)
            gb_layout.setContentsMargins(5, 5, 5, 5)
            gb_layout.setSpacing(2)
            
            if title:
                lbl = QtWidgets.QLabel(title)
                lbl.setStyleSheet(f"color: rgba(139, 152, 103, 1); font-size: {UI_FONT_SIZE}pt;")
                gb_layout.addWidget(lbl, stretch=0)
            
            gb_layout.addWidget(widget, stretch=1)
            return gb

        # Header
        header_gb = create_section("", self.header)
        main_vbox.addWidget(header_gb)

        # Cards Scroll
        cards_scroll = QtWidgets.QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setWidget(self.cards_container)
        main_vbox.addWidget(cards_scroll, stretch=2)

        # Bottom Area
        bottom_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        
        # Exchanges Switch
        sw_gb = create_section("거래소 선택", self.exchange_switch_container)
        bottom_splitter.addWidget(sw_gb)

        # Logs
        logs_container = QtWidgets.QWidget()
        logs_layout = QtWidgets.QVBoxLayout(logs_container)
        logs_layout.setContentsMargins(0,0,0,0)
        trading_log = QtWidgets.QLabel("기본 로그:")
        trading_log.setStyleSheet(f"color: rgba(109, 109, 109, 1);")
        logs_layout.addWidget(trading_log)
        logs_layout.addWidget(self.log_edit)
        system_output = QtWidgets.QLabel("온갖 로그:")
        system_output.setStyleSheet(f"color: rgba(109, 109, 109, 1);")
        logs_layout.addWidget(system_output)
        logs_layout.addWidget(self.console_edit)
        
        logs_gb = create_section("", logs_container)
        bottom_splitter.addWidget(logs_gb)
        
        bottom_splitter.setStretchFactor(0, 1)
        bottom_splitter.setStretchFactor(1, 2)
        main_vbox.addWidget(bottom_splitter, stretch=1)

        self.setCentralWidget(central)
        
        self.resize(UI_WINDOW_WIDTH, UI_WINDOW_HEIGHT)
        
        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().setSizeGripEnabled(True)

    def _connect_header_signals(self):
        h = self.header
        h.ticker_changed.connect(self._on_header_ticker)
        h.allqty_changed.connect(self._on_allqty)
        h.exec_all_clicked.connect(self._on_exec_all)
        h.reverse_clicked.connect(self._on_reverse)
        h.close_all_clicked.connect(self._on_close_all)
        h.repeat_clicked.connect(lambda: self._log("[REPEAT] Not implemented"))
        h.burn_clicked.connect(lambda: self._log("[BURN] Not implemented"))
        h.quit_clicked.connect(self.close)
        h.dex_changed.connect(self._on_header_dex)

    @QtCore.Slot(str)
    def _append_console_text(self, text: str):
        text = text.replace("\r\n", "\n")
        if text.strip():
            self.console_edit.appendPlainText(text.rstrip())
            sb = self.console_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

    # --- Async Init & Loops ---
    async def async_init(self):
        try: await self.mgr.initialize_all()
        except Exception as e: self._log(f"Init Error: {e}")

        # DEX list
        try:
            first_hl = self.mgr.first_hl_exchange()
            dexs = ["HL"]
            if first_hl and getattr(first_hl, "dex_list", None):
                for d in first_hl.dex_list:
                    if d.upper() != "HL": dexs.append(d.upper())
            self.dex_names = dexs
        except: self.dex_names = ["HL"]

        self.header.set_dex_choices(self.dex_names, "HL")
        self._build_switches()
        self._rebuild_cards()

        loop = asyncio.get_running_loop()
        self._price_task = loop.create_task(self._price_loop())
        self._status_task = loop.create_task(self._status_loop())

    def _build_switches(self):
        while self.exchange_switch_layout.count():
            w = self.exchange_switch_layout.takeAt(0).widget()
            if w: w.deleteLater()
        self.exchange_switches.clear()
        
        names = self.mgr.all_names()
        if not names: return

        row, col = 0, 0
        for name in names:
            meta = self.mgr.get_meta(name)
            cb = QtWidgets.QCheckBox(name.upper())
            cb.setChecked(bool(meta.get("show", False)))
            cb.toggled.connect(lambda s, n=name: self._on_toggle_show(n, s))
            self.exchange_switches[name] = cb
            self.exchange_switch_layout.addWidget(cb, row, col)
            col += 1
            if col >= 3:
                col = 0
                row += 1

    def _rebuild_cards(self):
        # [최적화] 기존 카드 중 여전히 visible한 것은 재사용
        visible_names = set(self.mgr.visible_names())
        current_names = set(self.cards.keys())
        
        # 제거할 카드
        to_remove = current_names - visible_names
        for name in to_remove:
            card = self.cards.pop(name, None)
            if card:
                card.setParent(None)
                card.deleteLater()
        
        # 새로 추가할 카드
        to_add = visible_names - current_names
        
        # 레이아웃 재구성이 필요한 경우에만
        if to_remove or to_add:
            # 레이아웃 아이템 정리 (stretch 포함)
            while self.cards_layout.count():
                item = self.cards_layout.takeAt(0)
                # 카드는 이미 처리했으므로 stretch만 정리
                if item.widget() and item.widget() not in self.cards.values():
                    item.widget().deleteLater()
            
            # visible 순서대로 카드 추가
            for name in self.mgr.visible_names():
                if name in to_add:
                    # 새 카드 생성
                    is_hl_like = self.mgr.is_hl_like(name)
                    card = ExchangeCardWidget(name, self.dex_names, is_hl_like=is_hl_like)
                    
                    st = self.exchange_state[name]
                    card.set_ticker(st.symbol)
                    card.set_order_type(st.order_type)
                    card.set_side_enabled(st.enabled, st.side)
                    
                    if is_hl_like:
                        card.set_dex(st.dex)
                    
                    # Signals 연결
                    card.execute_clicked.connect(self._on_exec_one)
                    card.long_clicked.connect(self._on_long)
                    card.short_clicked.connect(self._on_short)
                    card.off_clicked.connect(self._on_off)
                    card.order_type_changed.connect(self._on_otype_change)
                    card.dex_changed.connect(self._on_card_dex)
                    card.ticker_changed.connect(self._on_card_ticker)
                    
                    self.cards[name] = card
                
                # 카드를 레이아웃에 추가
                self.cards_layout.addWidget(self.cards[name])
            
            # 마지막에 stretch 추가
            self.cards_layout.addStretch(1)
        
        # All Qty 동기화
        aq = self.header.allqty_edit.text()
        if aq:
            for c in self.cards.values():
                c.set_qty(aq)
        
        # HL-like만 fee 업데이트
        for n in visible_names:
            if self.mgr.is_hl_like(n):
                self._update_fee(n)

    # --- Handlers ---
    def _on_header_ticker(self, t):
        s = _normalize_symbol_input(t)
        self.symbol = s
        for n in self.mgr.all_names():
            self.symbol_by_ex[n] = s
            self.exchange_state[n].symbol = s
        for c in self.cards.values(): c.set_ticker(s)

    def _on_allqty(self, t):
        for c in self.cards.values(): c.set_qty(t)

    def _on_header_dex(self, d):
        self.header_dex = d
        for n in self.mgr.all_names():
            self.dex_by_ex[n] = d
            self.exchange_state[n].dex = d
        for n, c in self.cards.items():
            c.set_dex(d)
            self._update_fee(n)
    
    def _on_card_ticker(self, n, t):
        s = _normalize_symbol_input(t or self.symbol)
        self.symbol_by_ex[n] = s
        self.exchange_state[n].symbol = s

    def _on_card_dex(self, n, d):
        self.dex_by_ex[n] = d
        self.exchange_state[n].dex = d
        self._update_fee(n)

    def _on_long(self, n): self._set_side(n, "buy")
    def _on_short(self, n): self._set_side(n, "sell")
    def _on_off(self, n): self._set_side(n, None)
    
    def _set_side(self, n, side):
        self.enabled[n] = (side is not None)
        self.side[n] = side
        self.exchange_state[n].enabled = (side is not None)
        self.exchange_state[n].side = side
        if n in self.cards:
            self.cards[n].set_side_enabled(self.enabled[n], side)

    def _on_otype_change(self, n, t):
        self.order_type[n] = t
        self.exchange_state[n].order_type = t
        if n in self.cards: 
            self.cards[n].set_order_type(t)
        self._update_fee(n)

    def _on_toggle_show(self, n, state):
        self.mgr.get_meta(n)["show"] = state
        if not state: 
            self._set_side(n, None)
        
        # [수정] 비동기로 카드 재구성하여 UI 블로킹 방지
        QtCore.QTimer.singleShot(0, self._rebuild_cards)

    def _on_exec_one(self, n):
        asyncio.get_running_loop().create_task(self._do_exec(n))
    
    def _on_exec_all(self):
        asyncio.get_running_loop().create_task(self._do_exec_all())
    
    def _on_reverse(self):
        cnt = 0
        for n in self.mgr.visible_names():
            if not self.enabled.get(n): continue
            s = self.side.get(n)
            new_s = "sell" if s == "buy" else "buy" if s == "sell" else None
            if new_s:
                self._set_side(n, new_s)
                cnt += 1
        self._log(f"Reversed {cnt} exchanges")

    def _on_close_all(self):
        asyncio.get_running_loop().create_task(self._do_close_all())

    # --- Actions ---
    async def _do_exec(self, n):
        c = self.cards.get(n)
        if not c: return
        try:
            qty = float(c.get_qty())
            otype = self.order_type[n]
            price = float(c.get_price_text()) if otype == "limit" else None
            side = self.side[n]
            
            sym = _compose_symbol(self.dex_by_ex[n], self.symbol_by_ex[n])
            self._log(f"[{n}] {side} {qty} {sym} @ {otype}")
            res = await self.service.execute_order(n, sym, qty, otype, side, price)
            self._log(f"[{n}] OK: {res['id']}")
        except Exception as e:
            self._log(f"[{n}] FAIL: {e}")

    async def _do_exec_all(self):
        tasks = []
        for n in self.mgr.visible_names():
            if self.enabled.get(n) and self.side.get(n):
                tasks.append(self._do_exec(n))
        if tasks: await asyncio.gather(*tasks)

    async def _do_close_all(self):
        tasks = []
        for n in self.mgr.visible_names():
            if self.enabled.get(n):
                try: hint = float(self.current_price.replace(",",""))
                except: hint = None
                sym = _compose_symbol(self.dex_by_ex[n], self.symbol_by_ex[n])
                tasks.append(self.service.close_position(n, sym, hint))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            self._log("Closed all positions")

    # --- Loops ---
    async def _price_loop(self):
        while not self._stopping:
            try:
                # 간단화: 첫 번째 HL 거래소 or visible 첫번째
                ex = self.mgr.first_hl_exchange()
                coin = _normalize_symbol_input(self.header.ticker_edit.text() or "BTC")
                if ex:
                    sym = _compose_symbol(self.header_dex, coin)
                    p = await ex.get_mark_price(sym)
                    if p: 
                        self.current_price = f"{p:,.2f}"
                        self.header.set_price(self.current_price)
                
                # Total Collateral
                tot = sum(self.collateral.values())
                self.header.set_total(tot)
            except: pass
            await asyncio.sleep(RATE["GAP_FOR_INF"])

    async def _status_loop(self):
        while not self._stopping:
            try:
                now = time.monotonic()
                for n in self.mgr.visible_names():
                    if n not in self.cards: continue
                    c = self.cards[n]
                    
                    # Update Intervals (Skip logic omitted for brevity, assume check)
                    sym = _compose_symbol(self.dex_by_ex[n], self.symbol_by_ex[n])
                    
                    # Price & Quote
                    try:
                        p = await self.service.fetch_price(n, sym)
                        c.set_price_label(p)
                    except: c.set_price_label("Err")
                    
                    if self.mgr.is_hl_like(n):
                         ex = self.mgr.get_exchange(n)
                         if ex: c.set_quote_label(ex.get_perp_quote(sym))

                         self._update_fee(n)

                    # Pos / Col
                    try:
                        pos, col, col_val, json_data = await self.service.fetch_status(n, sym, True, True)
                        
                        # json_data format
                        """
                        { "collateral": {
                            "perp":{"USDC":12.1},  # or None
                            "spot":{"USDT":10.2,"USDC":15.0,...} # or None, optional
                            },
                          "position": {
                          "size":0.002,"side":"short","unrealized_pnl":1.2
                          } # or None
                        }
                        """
                        c.set_status_info(json_data)
                        
                        if col_val: 
                            self.collateral[n] = float(col_val)
                        """
                        try:
                            px_val = float(str(p).replace(",",""))
                        except: px_val = None
                        
                        pos_fmt = _inject_usdc_value_into_pos(px_val, pos)
                        col_fmt = _strip_bracket_markup(col)
                        c.set_info_text(pos_fmt, col_fmt)
                        
                        if col_val: self.collateral[n] = float(col_val)
                        """
                    except: pass
                    
            except: pass
            await asyncio.sleep(1.0) # Reduce load

    def _update_fee(self, n):
        """
        HL-like 거래소의 Builder Fee를 업데이트.
        - DEX가 'HL'이면 dex_key=None
        - DEX가 HIP3(예: 'BULLPEN')이면 dex_key='bullpen'
        - order_type에 따라 market/limit fee 표시
        """
        try:
            # HL-like 거래소만 표시
            if not self.mgr.is_hl_like(n):
                return
            
            card = self.cards.get(n)
            if not card:
                return
            
            dex = self.dex_by_ex.get(n, "HL")
            dex_key = None if dex == "HL" else dex.lower()
            order_type = (self.order_type.get(n) or "market").lower()
            
            # TradingService에서 fee 가져오기
            fee = self.service.get_display_builder_fee(n, dex_key, order_type)
            
            if isinstance(fee, int):
                card.set_fee_label(f"Builder Fee: {fee}")
            else:
                card.set_fee_label("Builder Fee: -")
                
        except Exception as e:
            # 에러 시 조용히 무시 (로그만 남김)
            logger.debug(f"[UI] Fee update for {n} failed: {e}")

    def _log(self, m):
        logger.info(m)
        self.log_edit.appendPlainText(m)

    async def shutdown(self):
        self._stopping = True
        if self._console_redirect_installed:
            sys.stdout = self._stdout_orig
            sys.stderr = self._stderr_orig
        # Cancel tasks...
        if self._price_task: self._price_task.cancel()
        if self._status_task: self._status_task.cancel()
        await self.mgr.close_all()

    def closeEvent(self, e):
        asyncio.get_event_loop().create_task(self.shutdown())
        e.accept()

def run_qt_app(mgr):
    # WSL/X11 환경 감지 및 플랫폼 설정
    try:
        release = os.uname().release
        if "WSL" in release or "microsoft" in release.lower():
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    except:
        pass

    app = QtWidgets.QApplication(sys.argv)
    _apply_app_style(app)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    win = UiQtApp(mgr)
    def position_window_on_cursor_screen():
        cursor_pos = QtGui.QCursor.pos()
        
        # 커서가 있는 스크린 찾기
        target_screen = None
        for screen in app.screens():
            if screen.geometry().contains(cursor_pos):
                target_screen = screen
                break
        
        # 못 찾으면 기본 스크린 사용
        if target_screen is None:
            target_screen = app.primaryScreen()
        
        if target_screen:
            screen_geo = target_screen.availableGeometry()
            
            # 창을 해당 스크린 중앙에 배치
            x = screen_geo.x() + (screen_geo.width() - win.width()) // 2
            y = screen_geo.y() + (screen_geo.height() - win.height()) // 2
            
            win.move(x, y)
    
    async def starter():
        await win.async_init()
        position_window_on_cursor_screen()  # 위치 설정
        win.show()
        win.install_console_redirect()
    
    loop.create_task(starter())
    
    with loop:
        loop.run_forever()

if __name__ == "__main__":
    print("Import this module")