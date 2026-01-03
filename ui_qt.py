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
#from ui_config import set_ui_type, ui_print as print

GROUP_MIN = 0
GROUP_MAX = 5
GROUP_COUNT = GROUP_MAX - GROUP_MIN + 1

# 그룹별 색상 팔레트 (선택 시 배경색, 테두리색, 텍스트색)
GROUP_COLORS = {
    0: {"bg": "#1b5e20", "border": "#81c784", "text": "#81c784"},  # 초록
    1: {"bg": "#0d47a1", "border": "#64b5f6", "text": "#64b5f6"},  # 파랑
    2: {"bg": "#e65100", "border": "#ffb74d", "text": "#ffb74d"},  # 주황
    3: {"bg": "#6a1b9a", "border": "#ce93d8", "text": "#ce93d8"},  # 보라
    4: {"bg": "#00838f", "border": "#4dd0e1", "text": "#4dd0e1"},  # 청록
    5: {"bg": "#c62828", "border": "#ef9a9a", "text": "#ef9a9a"},  # 빨강
}

def _get_group_btn_style(g: int, is_card: bool = False) -> str:
    """
    그룹 버튼의 스타일시트 생성.
    - g: 그룹 번호 (0~5)
    - is_card: 카드용(작은 사이즈) 여부
    """
    colors = GROUP_COLORS.get(g, GROUP_COLORS[0])
    
    if is_card:
        # 카드용 (작은 버튼)
        return f"""
            QPushButton {{
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px 6px;
                min-width: 20px;
                font-size: 10pt;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border-color: {colors['border']};
            }}
            QPushButton:checked {{
                background-color: {colors['bg']};
                border: 1px solid {colors['border']};
                color: {colors['text']};
            }}
        """
    else:
        # 헤더용 (일반 버튼)
        return f"""
            QPushButton {{
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 8px;
                min-width: 24px;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border-color: {colors['border']};
            }}
            QPushButton:checked {{
                background-color: {colors['bg']};
                border: 2px solid {colors['border']};
                color: {colors['text']};
            }}
        """

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


# standx는 rest api 부르면 ws 끊기는 현상이 있어서 폴링 간격을 길게 잡았음.
# mpdex의 문제가 아닌 standx 서버 문제로 보임.
RATE = {
    "GAP_FOR_INF": 0.1,
    "GAP_FOR_ORDERBOOK": 0.1,
    "STATUS_POS_INTERVAL": {"default": 0.5, "standx":10.0},
    "STATUS_OO_INTERVAL": {"default": 0.5, "standx":10.0},
    "STATUS_COLLATERAL_INTERVAL": {"default": 0.5, "standx":10.0},
    "CARD_PRICE_INTERVAL": {"default": 0.2},
}

# HL 거래소 주문 실행 옵션 (.env의 HL_ORDER_DELAY로 설정 가능)
# - 0: 모든 거래소 완전 병렬 실행
# - 양수(예: 0.15): HL 거래소 간 해당 초만큼 딜레이 (미세 순차)
# - 음수(예: -1): HL 거래소 완전 순차 실행 (하나 끝나면 다음)
HL_ORDER_DELAY = float(os.environ.get("HL_ORDER_DELAY", "0.15"))

def _normalize_symbol_input(sym: str) -> str:
    if not sym: return ""
    s = sym.strip()
    return s.split(":", 1)[1].upper() if ":" in s else s.upper()

def _compose_symbol(dex: str, coin: str, is_spot: bool = False) -> str:
    c = (coin or "").upper()
    if is_spot:
        return c
    return f"{dex.lower()}:{c}" if dex and dex != "HL" else c

def _ws_supported(ex, operation: str) -> bool:
    """
    거래소가 특정 operation에 대해 WS를 지원하는지 확인.
    """
    ws_dict = getattr(ex, "ws_supported", None)
    return ws_dict.get(operation, False)

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
# 검색 가능한 콤보박스 (Symbol 선택용)
# ---------------------------------------------------------------------------
def _extract_base_symbol(sym: str) -> str:
    """
    심볼에서 base 부분만 추출.
    예: "BTC-USDC" → "BTC", "ETH-USD" → "ETH", "SOL" → "SOL"
    """
    if not sym:
        return ""
    s = sym.strip().upper()
    # "-" 또는 "/" 로 분리 (BTC-USDC, BTC/USDC 등)
    for sep in ("-", "/", "_"):
        if sep in s:
            return s.split(sep)[0]
    return s

class SearchableComboBox(QtWidgets.QComboBox):
    """
    검색 가능한 콤보박스 (QComboBox + QCompleter 기반)
    - 직접 입력 + 실시간 검색 + 드롭다운
    
    Signals:
        text_confirmed(str): Enter/포커스이탈 시 확정된 텍스트
    """
    text_confirmed = QtCore.Signal(str)
    
    def __init__(self, items: list = None, parent=None):
        super().__init__(parent)
        
        self._is_spot = False  # Spot 모드 여부

        # 편집 가능 + 삽입 금지
        self.setEditable(True)
        self.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        
        # 아이템 추가
        if items:
            self.addItems(items)
        
        # Completer 설정 (substring 검색)
        self.setCompleter(self._create_completer())
        
        # 스타일
        self.setStyleSheet("""
            QComboBox {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 8px;
            }
            QComboBox:focus {
                border-color: #81c784;
            }
            QComboBox QAbstractItemView {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #555;
                selection-background-color: #1b5e20;
                selection-color: #81c784;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                padding: 6px 10px;
                min-height: 24px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #3a3a3a;
            }
        """)
        
        # 시그널 연결
        self.lineEdit().editingFinished.connect(self._on_editing_finished)
        self.activated.connect(self._on_activated)
    
    def _create_completer(self) -> QtWidgets.QCompleter:
        """Substring 검색이 가능한 Completer 생성"""
        completer = QtWidgets.QCompleter(self.model(), self)
        completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.CompletionMode.PopupCompletion)
        
        
        # 팝업 스타일
        popup = completer.popup()
        popup.setStyleSheet("""
            QListView {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #555;
                outline: none;
            }
            QListView::item {
                padding: 6px 10px;
            }
            QListView::item:hover {
                background-color: #3a3a3a;
            }
            QListView::item:selected {
                background-color: #1b5e20;
                color: #81c784;
            }
        """)
        
        return completer
    
    def set_spot_mode(self, is_spot: bool):
        """[ADD] Spot 모드 설정 - Spot일 때는 심볼 변환하지 않음"""
        self._is_spot = is_spot

    def _normalize_symbol(self, raw: str) -> str:
        """[ADD] 심볼 정규화 - Spot은 그대로, Perp는 base만 추출"""
        if not raw:
            return ""
        s = raw.strip().upper()
        if self._is_spot:
            return s  # Spot: 그대로 (예: "HYPE/USDC")
        else:
            return _extract_base_symbol(s)  # Perp: base만 (예: "BTC-USDC" → "BTC")

    def _on_editing_finished(self):
        """Enter/포커스이탈 시 확정"""
        raw = self.currentText().strip().upper()
        # BTC-USDC → BTC 변환
        text = self._normalize_symbol(raw)
        if text:
            self.setEditText(text)
            self.text_confirmed.emit(text)
    
    def _on_activated(self, index: int):
        """드롭다운에서 항목 선택 시 확정"""
        raw = self.itemText(index).strip().upper()
        text = self._normalize_symbol(raw)
        if text:
            self.setEditText(text)
            self.text_confirmed.emit(text)
    
    def set_items(self, items: list):
        """목록 설정"""
        current_text = self.currentText()
        self.clear()
        if items:
            self.addItems(items)
        # Completer 모델도 갱신
        self.completer().setModel(self.model())
        # 기존 텍스트 복원
        if current_text:
            self.setEditText(current_text)
    
    def text(self) -> str:
        """현재 텍스트"""
        return self.currentText().strip().upper()
    
    def setText(self, text: str):
        """텍스트 설정 (외부에서 호출용)"""
        self.setEditText(text)
    
    def keyPressEvent(self, event: QtGui.QKeyEvent):
        """Enter 키 처리"""
        if event.key() in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            completer = self.completer()
            if completer and completer.popup().isVisible():
                current = completer.popup().currentIndex()
                if current.isValid():
                    raw = current.data()
                    text = self._normalize_symbol(raw)
                    self.setCurrentText(text)
                    completer.popup().hide()
                    self.text_confirmed.emit(text.upper())
                    return
            self._on_editing_finished()
            return
        super().keyPressEvent(event)

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
    
    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        # FIX: 콤보가 닫혀 있을 때 휠로 값이 바뀌는 걸 방지
        # - 닫혀있으면 ignore -> 부모(카드 스크롤 영역)가 휠을 받도록
        # - 열려있으면 super -> 드롭다운 목록 자체는 휠로 스크롤 가능
        view = self.view()
        popup_open = False
        try:
            popup_open = bool(view) and view.isVisible()
        except Exception:
            popup_open = False

        if popup_open:
            super().wheelEvent(event)
        else:
            event.ignore()

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
# 오더북 패널 위젯
# ---------------------------------------------------------------------------

class OrderBookPanel(QtWidgets.QWidget):
    """오더북 + 오픈 오더 표시 패널"""
    close_clicked = QtCore.Signal()
    cancel_all_clicked = QtCore.Signal()
    cancel_selected_clicked = QtCore.Signal(list)  # 선택된 오더 목록 전달
    price_clicked = QtCore.Signal(float)  # 가격 클릭 시 해당 가격 전달

    ORDERBOOK_DEPTH = 10  # 오더북 호가 수

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exchange_name = ""
        self._symbol = ""
        # 소숫점 자릿수 (기본값, 심볼별로 조정 가능)
        self._price_decimals = 2
        self._size_decimals = 4
        # 오더북 행-가격 매핑 (오픈오더 인디케이터용)
        self._asks_row_prices: list[tuple[int, float]] = []
        self._bids_row_prices: list[tuple[int, float]] = []
        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 스크롤 영역 생성
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")

        # 스크롤 내부 컨텐츠 위젯
        content_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # 헤더: 거래소명 + 심볼 + 닫기 버튼
        header = QtWidgets.QHBoxLayout()
        self.title_label = QtWidgets.QLabel("[거래소] 심볼")
        self.title_label.setStyleSheet(f"color: #baa055; font-weight: bold; font-size: {UI_FONT_SIZE}pt;")
        header.addWidget(self.title_label)
        header.addStretch()

        self.close_btn = QtWidgets.QPushButton("X")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                color: #ef5350;
                border: 1px solid #555;
                border-radius: 3px;
                font-weight: bold;
                font-size: {UI_FONT_SIZE}pt;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border-color: #ef5350;
            }}
        """)
        self.close_btn.clicked.connect(self.close_clicked.emit)
        header.addWidget(self.close_btn)
        layout.addLayout(header)

        # 오더북 섹션 제목 + Spread (같은 줄)
        orderbook_header = QtWidgets.QHBoxLayout()
        ob_title = QtWidgets.QLabel("오더북")
        ob_title.setStyleSheet(f"color: #888; font-size: {UI_FONT_SIZE}pt;")
        orderbook_header.addWidget(ob_title)
        orderbook_header.addStretch()
        self.spread_label = QtWidgets.QLabel("Spread: -")
        self.spread_label.setStyleSheet(f"color: #90caf9; font-size: {UI_FONT_SIZE}pt;")
        orderbook_header.addWidget(self.spread_label)
        layout.addLayout(orderbook_header)

        # 오더북 컬럼 헤더 (한 번만) - 테이블과 바로 붙어야 함
        col_header = QtWidgets.QHBoxLayout()
        col_header.setSpacing(0)
        col_header.setContentsMargins(4, 0, 4, 0)
        for col_name in ["Price", "Size", "Total"]:
            lbl = QtWidgets.QLabel(col_name)
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet(f"color: #666; font-size: {UI_FONT_SIZE - 1}pt; padding: 2px 4px;")
            col_header.addWidget(lbl, stretch=1)
        layout.addLayout(col_header)

        # Asks 테이블 (빨간색) - 헤더와 바로 붙음
        self.asks_table = self._create_orderbook_table("#ef9a9a", show_header=False)
        self.asks_table.cellClicked.connect(self._on_orderbook_clicked)
        layout.addWidget(self.asks_table)

        # Bids 테이블 (초록색) - asks와 바로 붙음
        self.bids_table = self._create_orderbook_table("#81c784", show_header=False)
        self.bids_table.cellClicked.connect(self._on_orderbook_clicked)
        layout.addWidget(self.bids_table)

        # 오픈 오더 섹션 제목
        orders_header = QtWidgets.QHBoxLayout()
        orders_title = QtWidgets.QLabel("오픈 오더")
        orders_title.setStyleSheet(f"color: #888; font-size: {UI_FONT_SIZE}pt; margin-top: 8px;")
        orders_header.addWidget(orders_title)
        orders_header.addStretch()
        layout.addLayout(orders_header)

        # 오픈 오더 테이블
        self.orders_table = QtWidgets.QTableWidget()
        self.orders_table.setColumnCount(5)
        self.orders_table.setHorizontalHeaderLabels(["", "Side", "Price", "Size", "Order ID"])
        # 컬럼 크기 조절 가능하게 (Interactive), 마지막 열은 남은 공간 채움
        self.orders_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.orders_table.horizontalHeader().setStretchLastSection(True)
        # 기본 컬럼 너비 설정
        self.orders_table.setColumnWidth(0, 40)   # 체크박스
        self.orders_table.setColumnWidth(1, 50)   # Side
        self.orders_table.setColumnWidth(2, 80)   # Price
        self.orders_table.setColumnWidth(3, 70)   # Size
        self.orders_table.verticalHeader().setVisible(False)
        self.orders_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.orders_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.orders_table.verticalHeader().setDefaultSectionSize(26)
        self.orders_table.setStyleSheet(f"""
            QTableWidget {{
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #444;
                gridline-color: #444;
                font-size: {UI_FONT_SIZE}pt;
            }}
            QHeaderView::section {{
                background-color: #454545;
                color: #aaa;
                padding: 4px;
                border: none;
                font-size: {UI_FONT_SIZE - 1}pt;
            }}
            QHeaderView::section:first {{
                padding: 0px;
            }}
        """)

        # 헤더의 첫 번째 열에 체크박스 오버레이
        self._select_all_checkbox = QtWidgets.QCheckBox()
        self._select_all_checkbox.setParent(self.orders_table)
        self._select_all_checkbox.stateChanged.connect(self._on_select_all_changed)
        # 초기 위치는 _update_select_all_checkbox_pos에서 설정
        self._select_all_checkbox.raise_()

        # 오픈오더는 최대 8개 정도 보이도록
        self.orders_table.setMinimumHeight(8 * 26 + 28)
        layout.addWidget(self.orders_table)

        # 오더 데이터 저장 (취소 시 사용)
        self._open_orders_data: list = []
        # 현재 표시 중인 오더 ID 목록 (변경 감지용)
        self._current_order_ids: list = []
        # 행별 체크박스 참조 저장
        self._row_checkboxes: dict = {}

        # 취소 버튼 레이아웃
        cancel_btn_layout = QtWidgets.QHBoxLayout()
        cancel_btn_layout.setSpacing(8)

        # 선택 취소 버튼
        self.cancel_selected_btn = QtWidgets.QPushButton("선택 취소")
        self.cancel_selected_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                color: #ffab91;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: {UI_FONT_SIZE}pt;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border-color: #ffab91;
            }}
            QPushButton:pressed {{
                background-color: #2a2a2a;
            }}
        """)
        self.cancel_selected_btn.clicked.connect(self._on_cancel_selected)
        cancel_btn_layout.addWidget(self.cancel_selected_btn)

        # 전체 취소 버튼
        self.cancel_all_btn = QtWidgets.QPushButton("전체 취소")
        self.cancel_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #3a3a3a;
                color: #ef5350;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: {UI_FONT_SIZE}pt;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border-color: #ef5350;
            }}
            QPushButton:pressed {{
                background-color: #2a2a2a;
            }}
        """)
        self.cancel_all_btn.clicked.connect(self.cancel_all_clicked.emit)
        cancel_btn_layout.addWidget(self.cancel_all_btn)

        layout.addLayout(cancel_btn_layout)

        # 남는 공간은 맨 아래로
        layout.addStretch(1)

        # 스크롤 영역에 컨텐츠 위젯 설정
        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area)

        # 패널 스타일
        self.setStyleSheet(f"""
            OrderBookPanel {{
                background-color: #2d2d2d;
                border: 1px solid #555;
                border-radius: 4px;
                font-size: {UI_FONT_SIZE}pt;
            }}
        """)
        self.setMinimumWidth(300)
        # bid/ask 10행씩 모두 보이도록 최소 높이 설정
        # 오더북: (10행 * 28px + 2) * 2 = 564px
        # 오픈오더: 135px
        # 헤더/제목/버튼 등: ~180px
        # 총: 약 880px
        self.setMinimumHeight(880)

    def _create_orderbook_table(self, color: str, show_header: bool = True) -> QtWidgets.QTableWidget:
        """오더북 테이블 생성 (Price + Size + Total)"""
        table = QtWidgets.QTableWidget()
        table.setColumnCount(3)
        if show_header:
            table.setHorizontalHeaderLabels(["Price", "Size", "Total"])
        else:
            table.horizontalHeader().setVisible(False)
        table.setRowCount(self.ORDERBOOK_DEPTH)
        table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        # 스크롤바 숨기기
        table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setStyleSheet(f"""
            QTableWidget {{
                background-color: #2b2b2b;
                color: {color};
                border: 1px solid #444;
                font-size: {UI_FONT_SIZE}pt;
            }}
            QTableWidget::item {{
                padding: 1px 4px;
            }}
        """)
        # 행 높이 - 강제 고정
        row_height = 28
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Fixed)
        for i in range(self.ORDERBOOK_DEPTH):
            table.setRowHeight(i, row_height)
        # 테이블 높이: 10행 + border
        table.setFixedHeight( (self.ORDERBOOK_DEPTH) * row_height + 2)
        return table

    def set_exchange_info(self, exchange_name: str, symbol: str):
        """거래소/심볼 정보 설정"""
        self._exchange_name = exchange_name
        self._symbol = symbol
        self.title_label.setText(f"[{exchange_name.upper()}] {symbol}")
        # 심볼에 따라 소숫점 자릿수 자동 조정
        self._auto_detect_decimals(symbol)

    def _auto_detect_decimals(self, symbol: str):
        """심볼에 따라 소숫점 자릿수 자동 결정"""
        symbol_upper = symbol.upper()
        # BTC, ETH 등 고가 코인
        if any(x in symbol_upper for x in ["BTC", "ETH"]):
            self._price_decimals = 2
            self._size_decimals = 4
        # SOL, AVAX 등 중가 코인
        elif any(x in symbol_upper for x in ["SOL", "AVAX", "BNB", "AAVE"]):
            self._price_decimals = 3
            self._size_decimals = 3
        # 저가 코인
        elif any(x in symbol_upper for x in ["DOGE", "SHIB", "PEPE", "FLOKI", "WIF", "BONK"]):
            self._price_decimals = 6
            self._size_decimals = 0
        # 기본값
        else:
            self._price_decimals = 4
            self._size_decimals = 2

    def _on_orderbook_clicked(self, row: int, col: int):
        """오더북 가격 클릭 시 해당 가격을 시그널로 전달"""
        # 어느 테이블에서 클릭했는지 확인
        sender = self.sender()
        if sender is None:
            return

        item = sender.item(row, 0)  # 첫 번째 열이 가격
        if item and item.text():
            try:
                # 인디케이터(•)와 콤마 제거 후 float 변환
                price_str = item.text().replace("•", "").replace(",", "").strip()
                price = float(price_str)
                self.price_clicked.emit(price)
            except ValueError:
                pass

    def update_orderbook(self, orderbook: dict):
        """오더북 데이터 업데이트"""
        if not orderbook:
            return

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        # 행 -> 가격 매핑 저장 (오픈오더 인디케이터용)
        self._asks_row_prices: list[tuple[int, float]] = []  # [(row, price), ...]
        self._bids_row_prices: list[tuple[int, float]] = []

        # Asks 테이블 업데이트 (역순: 높은 가격이 아래로, 아래 정렬)
        asks_display = asks[:self.ORDERBOOK_DEPTH]
        asks_display = list(reversed(asks_display))  # 역순
        total = 0.0
        totals = []
        for ask in reversed(asks_display):
            total += float(ask[1]) if len(ask) > 1 else 0
            totals.insert(0, total)

        # 아래 정렬: 빈 행은 위쪽에, 데이터는 아래쪽에
        empty_rows = self.ORDERBOOK_DEPTH - len(asks_display)
        for i in range(self.ORDERBOOK_DEPTH):
            if i < empty_rows:
                self._clear_table_row(self.asks_table, i)
            else:
                data_idx = i - empty_rows
                price = float(asks_display[data_idx][0])
                size = float(asks_display[data_idx][1]) if len(asks_display[data_idx]) > 1 else 0
                total_size = totals[data_idx]
                self._set_table_row(self.asks_table, i, price, size, total_size)
                self._asks_row_prices.append((i, price))

        # Bids 테이블 업데이트 (정순: 높은 가격이 위로)
        bids_display = bids[:self.ORDERBOOK_DEPTH]
        total = 0.0
        for i in range(self.ORDERBOOK_DEPTH):
            if i < len(bids_display):
                price = float(bids_display[i][0])
                size = float(bids_display[i][1]) if len(bids_display[i]) > 1 else 0
                total += size
                self._set_table_row(self.bids_table, i, price, size, total)
                self._bids_row_prices.append((i, price))
            else:
                self._clear_table_row(self.bids_table, i)

        # Spread 계산
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            spread = best_ask - best_bid
            spread_pct = (spread / best_bid * 100) if best_bid > 0 else 0
            self.spread_label.setText(f"Spread: {spread:.{self._price_decimals}f} ({spread_pct:.3f}%)")
        else:
            self.spread_label.setText("Spread: -")

        # 오픈오더 위치 인디케이터 표시
        self._mark_order_indicators()

    def _set_table_row(self, table: QtWidgets.QTableWidget, row: int, price: float, size: float, total: float):
        """테이블 행 설정 (고정 소숫점 자릿수)"""
        price_str = f"{price:,.{self._price_decimals}f}"
        size_str = f"{size:,.{self._size_decimals}f}"
        total_str = f"{total:,.{self._size_decimals}f}"

        for col, text in enumerate([price_str, size_str, total_str]):
            item = table.item(row, col)
            if not item:
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row, col, item)
            else:
                item.setText(text)

    def _clear_table_row(self, table: QtWidgets.QTableWidget, row: int):
        """테이블 행 비우기"""
        for col in range(3):
            item = table.item(row, col)
            if item:
                item.setText("")

    def _mark_order_indicators(self):
        """오픈오더 위치에 인디케이터(•) 표시 - 가격 앞에 • 추가"""
        if not self._open_orders_data:
            return
        if not hasattr(self, "_asks_row_prices") or not hasattr(self, "_bids_row_prices"):
            return

        # SELL/SHORT -> asks, BUY/LONG -> bids
        sell_prices = []
        buy_prices = []
        for order in self._open_orders_data:
            side = str(order.get("side", "")).upper()
            price = float(order.get("price", 0))
            if side in ("SELL", "SHORT"):
                sell_prices.append(price)
            elif side in ("BUY", "LONG"):
                buy_prices.append(price)

        # asks 테이블에 SELL 오더 표시
        marked_ask_rows = set()
        for order_price in sell_prices:
            closest_row = self._find_closest_row(self._asks_row_prices, order_price)
            if closest_row is not None and closest_row not in marked_ask_rows:
                marked_ask_rows.add(closest_row)
                item = self.asks_table.item(closest_row, 0)  # 가격 열
                if item and not item.text().startswith("•"):
                    item.setText("• " + item.text())

        # bids 테이블에 BUY 오더 표시
        marked_bid_rows = set()
        for order_price in buy_prices:
            closest_row = self._find_closest_row(self._bids_row_prices, order_price)
            if closest_row is not None and closest_row not in marked_bid_rows:
                marked_bid_rows.add(closest_row)
                item = self.bids_table.item(closest_row, 0)  # 가격 열
                if item and not item.text().startswith("•"):
                    item.setText("• " + item.text())

    def _find_closest_row(self, row_prices: list[tuple[int, float]], target_price: float) -> int | None:
        """주어진 가격에 가장 가까운 행 번호 반환"""
        if not row_prices:
            return None

        closest_row = None
        min_diff = float("inf")
        for row, price in row_prices:
            diff = abs(price - target_price)
            if diff < min_diff:
                min_diff = diff
                closest_row = row
        return closest_row

    def showEvent(self, event):
        """패널 표시 시 체크박스 위치 업데이트"""
        super().showEvent(event)
        QtCore.QTimer.singleShot(10, self._update_select_all_checkbox_pos)

    def resizeEvent(self, event):
        """리사이즈 시 체크박스 위치 업데이트"""
        super().resizeEvent(event)
        self._update_select_all_checkbox_pos()

    def _update_select_all_checkbox_pos(self):
        """전체 선택 체크박스를 헤더 첫 번째 셀 중앙에 위치"""
        header = self.orders_table.horizontalHeader()
        if not header.isVisible():
            return
        # 첫 번째 열의 폭과 헤더 높이
        col_width = self.orders_table.columnWidth(0)
        header_height = header.height()
        cb_w, cb_h = 18, 18
        x = (col_width - cb_w) // 2 + 3  # 오른쪽으로 약간 이동
        y = (header_height - cb_h) // 2
        self._select_all_checkbox.setGeometry(x, y, cb_w, cb_h)
        self._select_all_checkbox.show()

    def _on_select_all_changed(self, state):
        """전체 선택 체크박스 상태 변경"""
        is_checked = (state == QtCore.Qt.CheckState.Checked.value)
        for cb in self._row_checkboxes.values():
            if cb:
                cb.setChecked(is_checked)

    def _create_row_checkbox(self, order_id: str) -> QtWidgets.QWidget:
        """행용 체크박스 위젯 생성"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        checkbox = QtWidgets.QCheckBox()
        layout.addWidget(checkbox)

        # 체크박스 참조 저장
        self._row_checkboxes[order_id] = checkbox
        return container

    def update_open_orders(self, orders: list):
        """오픈 오더 목록 업데이트"""
        if orders is None:
            orders = []

        # 오더 ID 목록 추출
        new_order_ids = [str(o.get("order_id", o.get("id", ""))) for o in orders]

        # 오더 목록이 변경된 경우에만 테이블 재구성
        if new_order_ids != self._current_order_ids:
            self._current_order_ids = new_order_ids
            self._open_orders_data = orders
            self._row_checkboxes.clear()

            self.orders_table.setRowCount(len(orders))

            for row, order in enumerate(orders):
                side = str(order.get("side", "")).upper()
                price = order.get("price", 0)
                size = order.get("size", order.get("quantity", 0))
                order_id = str(order.get("order_id", order.get("id", "")))

                # 체크박스 위젯 (열 0)
                checkbox_widget = self._create_row_checkbox(order_id)
                self.orders_table.setCellWidget(row, 0, checkbox_widget)

                # Side 색상 (열 1) - BUY/LONG은 초록, SELL/SHORT는 빨강
                side_item = QtWidgets.QTableWidgetItem(side)
                side_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                if side in ("BUY", "LONG"):
                    side_item.setForeground(QtGui.QColor("#81c784"))
                else:
                    side_item.setForeground(QtGui.QColor("#ef9a9a"))
                self.orders_table.setItem(row, 1, side_item)

                # Price (열 2)
                price_item = QtWidgets.QTableWidgetItem(f"{float(price):,.{self._price_decimals}f}")
                price_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.orders_table.setItem(row, 2, price_item)

                # Size (열 3)
                size_item = QtWidgets.QTableWidgetItem(f"{float(size):,.{self._size_decimals}f}")
                size_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.orders_table.setItem(row, 3, size_item)

                # Order ID (열 4)
                id_item = QtWidgets.QTableWidgetItem(order_id[:12])
                id_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.orders_table.setItem(row, 4, id_item)

            # 오더북 인디케이터 업데이트 (오픈오더 변경 시 즉시 반영)
            self._mark_order_indicators()

    def _on_cancel_selected(self):
        """선택된 오더 취소"""
        selected_orders = []
        for idx, order_id in enumerate(self._current_order_ids):
            cb = self._row_checkboxes.get(order_id)
            if cb and cb.isChecked():
                if idx < len(self._open_orders_data):
                    selected_orders.append(self._open_orders_data[idx])

        if selected_orders:
            self.cancel_selected_clicked.emit(selected_orders)
            # 선택 취소 후 체크 해제
            for order_id in list(self._row_checkboxes.keys()):
                cb = self._row_checkboxes.get(order_id)
                if cb:
                    cb.setChecked(False)
            self._select_all_checkbox.setChecked(False)

    def clear(self):
        """패널 초기화"""
        for i in range(self.ORDERBOOK_DEPTH):
            self._clear_table_row(self.asks_table, i)
            self._clear_table_row(self.bids_table, i)
        self.spread_label.setText("Spread: -")
        self.orders_table.setRowCount(0)
        self._open_orders_data = []
        self._current_order_ids = []
        self._row_checkboxes.clear()
        self._select_all_checkbox.setChecked(False)
        self._asks_row_prices = []
        self._bids_row_prices = []


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
    group_changed = QtCore.Signal(str, int)  # (ex_name, group)
    market_type_changed = QtCore.Signal(str, str)  # (ex_name, "perp" or "spot")
    transfer_execute = QtCore.Signal(str, dict)  # [ADD] (ex_name, transfer_info)
    detail_order_clicked = QtCore.Signal(str, str)  # [ADD] 상세 주문 버튼 클릭 (ex_name, direction: "left" or "right")
    close_position_clicked = QtCore.Signal(str)  # 포지션 종료 버튼 클릭 (ex_name)

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
        #self.ticker_edit = QtWidgets.QLineEdit()
        self.ticker_edit = SearchableComboBox()

        # [CHANGED] 수량 입력 + 내부 USD 라벨 오버레이
        self.qty_edit = QtWidgets.QLineEdit()
        
        # USD 가치 라벨 (qty_edit 내부 오버레이)
        self.qty_value_label = QtWidgets.QLabel("", self.qty_edit)
        self.qty_value_label.setStyleSheet(f"color: {CLR_MUTED}; background: transparent;")
        self.qty_value_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.qty_value_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        
        # 수량 변경 시 USD 가치 업데이트
        self.qty_edit.textChanged.connect(self._update_qty_value)

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

        # Perp/Spot 선택 버튼
        self.perp_btn = QtWidgets.QPushButton("Perp")
        self.spot_btn = QtWidgets.QPushButton("Spot")
        self.perp_btn.setCheckable(True)
        self.spot_btn.setCheckable(True)
        self.perp_btn.setChecked(True)  # 기본값: Perp
        self._has_spot = False  # 초기값, 나중에 set_has_spot으로 변경

        BTN_MARKET_TYPE = """
            QPushButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
            QPushButton:checked {
                background-color: #1b3146;
                border: 2px solid #64b5f6;
                color: #64b5f6;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }
        """
        self.perp_btn.setStyleSheet(BTN_MARKET_TYPE)
        self.spot_btn.setStyleSheet(BTN_MARKET_TYPE)

        # 버튼
        self.long_btn = QtWidgets.QPushButton("Long")
        self.short_btn = QtWidgets.QPushButton("Short")
        self.off_btn = QtWidgets.QPushButton("미선택")
        self.exec_btn = QtWidgets.QPushButton("주문 실행")
        self.close_pos_btn = QtWidgets.QPushButton("포지션 종료")  # 포지션 종료 버튼

        # 상세 버튼 + 방향 선택 버튼
        self.detail_left_btn = QtWidgets.QPushButton("◀")  # 왼쪽 방향
        self.detail_right_btn = QtWidgets.QPushButton("▶")  # 오른쪽 방향 (기본)
        self.detail_btn = QtWidgets.QPushButton("상세")

        self.detail_left_btn.setCheckable(True)
        self.detail_right_btn.setCheckable(True)
        self.detail_right_btn.setChecked(True)  # 기본: 오른쪽

        self.exec_btn.setAutoDefault(False)
        self.exec_btn.setDefault(False)
        self.close_pos_btn.setAutoDefault(False)
        self.close_pos_btn.setDefault(False)
        self.detail_btn.setAutoDefault(False)
        self.detail_btn.setDefault(False)
        self.detail_left_btn.setAutoDefault(False)
        self.detail_right_btn.setAutoDefault(False)

        self.group_buttons: Dict[int, QtWidgets.QPushButton] = {}
        self.current_group = 0

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

        BTN_DETAIL = """
            QPushButton {
                background-color: #3a3a3a;
                color: #ce93d8;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #ce93d8;
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

        BTN_ARROW = """
            QPushButton {
                background-color: #3a3a3a;
                color: #888;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 6px;
                min-width: 20px;
                max-width: 24px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                color: #ce93d8;
            }
            QPushButton:checked {
                background-color: #4a3a4a;
                color: #ce93d8;
                border-color: #ce93d8;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #444;
                border-color: #333;
            }
        """

        BTN_CLOSE_POS = """
            QPushButton {
                background-color: #3a3a3a;
                color: #ffab91;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #ffab91;
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

        for g in range(GROUP_COUNT):
            btn = QtWidgets.QPushButton(str(g))
            btn.setCheckable(True)
            btn.setChecked(g == 0)
            btn.setStyleSheet(_get_group_btn_style(g, is_card=True))  # [CHANGED]
            btn.setFixedWidth(24)
            btn.clicked.connect(lambda checked, gg=g: self._on_card_group_clicked(gg))
            self.group_buttons[g] = btn

        self.long_btn.setStyleSheet(BTN_LONG)
        self.short_btn.setStyleSheet(BTN_SHORT)
        self.off_btn.setStyleSheet(BTN_BASE)
        self.exec_btn.setStyleSheet(BTN_EXEC)
        self.close_pos_btn.setStyleSheet(BTN_CLOSE_POS)
        self.detail_btn.setStyleSheet(BTN_DETAIL)
        self.detail_left_btn.setStyleSheet(BTN_ARROW)
        self.detail_right_btn.setStyleSheet(BTN_ARROW)

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

        # Spot 관련 위젯들 (숨김 처리용)
        self.spot_sep_label = None
        self.spot_title_label = None
        
        # Collateral 전송 위젯
        self._has_transfer = False  # transfer 기능 지원 여부
        self._perp_collateral_coin = "USDC"  # Perp collateral 코인
        self._perp_collateral_amount = 0.0  # Perp collateral 수량
        self._spot_collateral_amount = 0.0  # Spot collateral 수량 (해당 코인)
        
        self.transfer_to_perp_btn = QtWidgets.QPushButton("◀")
        self.transfer_to_spot_btn = QtWidgets.QPushButton("▶")
        self.transfer_amount_edit = QtWidgets.QLineEdit()
        self.transfer_max_btn = QtWidgets.QPushButton("MAX")
        self.transfer_exec_btn = QtWidgets.QPushButton("전송")
        
        # 전송 방향 상태: None(미선택), "to_perp", "to_spot"
        self._transfer_direction: Optional[str] = None
        
        self._setup_transfer_widgets()

        self._build_layout()
        self._connect_signals()

    def _setup_transfer_widgets(self):
        """Collateral 전송 위젯 초기 설정"""
        # 버튼 스타일
        BTN_TRANSFER = """
            QPushButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px 8px;
                min-width: 24px;
                font-size: 12pt;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #888;
            }
            QPushButton:checked {
                background-color: #1b5e20;
                border: 2px solid #81c784;
                color: #81c784;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }
        """

        BTN_TRANSFER_EXEC = f"""
            QPushButton {{
                background-color: #3a3a3a;
                color: #90caf9;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px 12px;
                font-size: {UI_FONT_SIZE}pt;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border-color: #90caf9;
            }}
            QPushButton:pressed {{
                background-color: #1b3a5c;
            }}
            QPushButton:disabled {{
                background-color: #2a2a2a;
                color: #555;
                border-color: #333;
            }}
        """
        
        self.transfer_to_perp_btn.setStyleSheet(BTN_TRANSFER)
        self.transfer_to_spot_btn.setStyleSheet(BTN_TRANSFER)
        self.transfer_exec_btn.setStyleSheet(BTN_TRANSFER_EXEC)
        
        self.transfer_to_perp_btn.setCheckable(True)
        self.transfer_to_spot_btn.setCheckable(True)
        self.transfer_to_perp_btn.setChecked(False)
        self.transfer_to_spot_btn.setChecked(False)
        
        # [CHANGED] 수량 입력 필드 설정 (내부 MAX 버튼 포함)
        self.transfer_amount_edit.setFixedWidth(200)
        self.transfer_amount_edit.setPlaceholderText("전송수량")
        self.transfer_amount_edit.setStyleSheet("""
            QLineEdit {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 2px 40px 2px 6px;
            }
        """)
        
        # MAX 버튼을 QLineEdit 내부에 오버레이로 배치
        self.transfer_max_btn.setParent(self.transfer_amount_edit)
        self.transfer_max_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #404040;
                color: #aaa;
                border: none;
                border-radius: 3px;
                padding: 2px 6px;
                font-size: {UI_FONT_SIZE}pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #505050;
                color: #e0e0e0;
            }}
            QPushButton:pressed {{
                background-color: #1b5e20;
                color: #81c784;
            }}
        """)
        self.transfer_max_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        
        # 시그널 연결
        self.transfer_to_perp_btn.clicked.connect(self._on_transfer_to_perp_clicked)
        self.transfer_to_spot_btn.clicked.connect(self._on_transfer_to_spot_clicked)
        self.transfer_max_btn.clicked.connect(self._on_transfer_max_clicked)
        self.transfer_exec_btn.clicked.connect(self._on_transfer_exec_clicked)

        # 초기에는 숨김
        self._set_transfer_visible(False)

    def _update_transfer_max_btn_pos(self):
        """[ADD] MAX 버튼을 QLineEdit 내부 오른쪽에 배치"""
        if not hasattr(self, 'transfer_max_btn') or not hasattr(self, 'transfer_amount_edit'):
            return
        
        # 버튼 크기 계산
        btn_width = self.transfer_max_btn.sizeHint().width()
        btn_height = self.transfer_amount_edit.height() - 4  # 약간의 여백
        
        # 오른쪽 정렬 위치 계산
        x = self.transfer_amount_edit.width() - btn_width - 2
        y = (self.transfer_amount_edit.height() - btn_height) // 2
        
        self.transfer_max_btn.setGeometry(x, y, btn_width, btn_height)

    def _set_transfer_visible(self, visible: bool):
        """[ADD] 전송 위젯 표시/숨김"""
        self.transfer_to_perp_btn.setVisible(visible)
        self.transfer_to_spot_btn.setVisible(visible)
        self.transfer_amount_edit.setVisible(visible)
        self.transfer_max_btn.setVisible(visible)
        self.transfer_exec_btn.setVisible(visible)

    def set_has_transfer(self, has_transfer: bool):
        """[ADD] 전송 기능 지원 여부 설정"""
        self._has_transfer = has_transfer
        self._set_transfer_visible(has_transfer)

    def set_collateral_info(self, perp_coin: str, perp_amount: float, spot_amount: float):
        """[ADD] Collateral 정보 업데이트 (MAX 계산용)"""
        self._perp_collateral_coin = perp_coin
        self._perp_collateral_amount = perp_amount
        self._spot_collateral_amount = spot_amount

    def _on_transfer_to_perp_clicked(self):
        """[ADD] ◀ 버튼 클릭 (Spot → Perp)"""
        if self.transfer_to_perp_btn.isChecked():
            self._transfer_direction = "to_perp"
            self.transfer_to_spot_btn.setChecked(False)
        else:
            self._transfer_direction = None

    def _on_transfer_to_spot_clicked(self):
        """[ADD] ▶ 버튼 클릭 (Perp → Spot)"""
        if self.transfer_to_spot_btn.isChecked():
            self._transfer_direction = "to_spot"
            self.transfer_to_perp_btn.setChecked(False)
        else:
            self._transfer_direction = None

    def _on_transfer_max_clicked(self):
        """[ADD] MAX 버튼 클릭 - 방향에 따라 최대값 설정"""
        if self._transfer_direction == "to_perp":
            # Spot → Perp: Spot의 해당 코인 잔고
            max_val = self._spot_collateral_amount
        elif self._transfer_direction == "to_spot":
            # Perp → Spot: Perp collateral 잔고
            max_val = self._perp_collateral_amount
        else:
            # 방향 미선택: 아무것도 안 함
            return
        
        # 소수점 1자리까지 버림
        truncated = int(max_val * 10) / 10.0
        self.transfer_amount_edit.setText(f"{truncated:.1f}")

    def get_transfer_info(self) -> Optional[dict]:
        """
        [ADD] 현재 전송 설정 반환.
        Returns:
            {"direction": "to_perp" or "to_spot", "amount": float, "coin": str}
            또는 None (방향 미선택 또는 수량 없음)
        """
        if not self._transfer_direction:
            return None
        
        try:
            amount = float(self.transfer_amount_edit.text().strip())
            if amount <= 0:
                return None
        except ValueError:
            return None
        
        return {
            "direction": self._transfer_direction,
            "amount": amount,
            "coin": self._perp_collateral_coin
        }

    def _on_transfer_exec_clicked(self):
        """[ADD] 전송 버튼 클릭"""
        info = self.get_transfer_info()
        if info:
            self.transfer_execute.emit(self.ex_name, info)
        else:
            # 방향 미선택 또는 수량 없음
            print(f"[{self.ex_name}] 전송 방향을 선택하고 수량을 입력하세요")

    def is_valid(self) -> bool:
        """[ADD] 위젯이 아직 유효한지 (삭제되지 않았는지) 확인"""
        try:
            # C++ 객체가 삭제되었으면 접근 시 RuntimeError 발생
            _ = self.price_label.text()
            return True
        except RuntimeError:
            return False

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

        # Perp/Spot 버튼
        header_row.addWidget(self.perp_btn, stretch=1)
        header_row.addWidget(self.spot_btn, stretch=1)
        
        for b in (self.long_btn, self.short_btn, self.off_btn, self.exec_btn, self.close_pos_btn):
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
        input_row.addWidget(self.detail_left_btn)
        input_row.addWidget(self.detail_right_btn)
        input_row.addWidget(self.detail_btn, stretch=1)
        
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

        pos_row.addWidget(QtWidgets.QLabel("그룹"))
        for g in range(GROUP_COUNT):
            pos_row.addWidget(self.group_buttons[g])

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
        
        
        # 전송 컨트롤:  [수량+MAX] [◀][▶] [전송]
        collat_row.addSpacing(10)
        collat_row.addWidget(self.transfer_amount_edit)  # MAX 버튼은 내부에 포함됨
        collat_row.addWidget(self.transfer_to_perp_btn)
        collat_row.addWidget(self.transfer_to_spot_btn)
        collat_row.addWidget(self.transfer_exec_btn)
        collat_row.addSpacing(10)

        self.spot_title_label = QtWidgets.QLabel("Spot:")
        self.spot_title_label.setStyleSheet(f"color: {CLR_MUTED};")
        collat_row.addWidget(self.spot_title_label)
        collat_row.addWidget(self.collat_spot_label)
        
        collat_row.addStretch()
        
        main_layout.addLayout(collat_row)

    def _auto_select_symbol(self, symbols: list):
        """
        심볼 목록에서 자동 선택.
        1. 현재 심볼이 목록에 있으면 유지
        2. 없으면 BTC → ETH 순서로 검색
        3. 둘 다 없으면 첫 번째 선택
        
        Args:
            symbols: 새로 적용될 심볼 목록
        
        Returns:
            선택된 심볼 (없으면 None)
        """
        if not symbols:
            return None
        
        # 현재 심볼에서 base 부분만 추출 (BTC/USDC → BTC, BTC-USDC → BTC)
        current_raw = self.ticker_edit.currentText().strip().upper()
        current = _extract_base_symbol(current_raw)

        def find_match(target: str) -> str | None:
            """target에 대해 exact 매칭 우선, contains 매칭 fallback"""
            if not target:
                return None
            contains_match = None
            for sym in symbols:
                sym_base = _extract_base_symbol(sym.upper())
                if sym_base == target:
                    return sym  # exact 매칭 즉시 반환
                if contains_match is None and target in sym_base:
                    contains_match = sym  # 첫 번째 contains 매칭 저장
            return contains_match
        
        # 1. 현재 심볼 검색
        result = find_match(current)
        if result:
            return result
        
        # 2. BTC → ETH 순서로 검색
        for base in ["BTC", "ETH"]:
            result = find_match(base)
            if result:
                return result
        
        # 3. 첫 번째 선택
        return symbols[0]

    def _update_qty_value(self):
        """수량 변경 시 USD 가치 업데이트 (입력칸 내부 오버레이)"""
        try:
            qty_text = self.qty_edit.text().strip()
            if not qty_text:
                self.qty_value_label.setText("")
                return
            
            qty = float(qty_text)
            if self._current_price and self._current_price > 0:
                usd_value = qty * self._current_price
                self.qty_value_label.setText(f"≈{usd_value:,.1f}$  ")  # 오른쪽 여백
            else:
                self.qty_value_label.setText("")
        except ValueError:
            self.qty_value_label.setText("")

    def showEvent(self, event):
        """[ADD] 위젯 표시 시 오버레이 위치 초기화"""
        super().showEvent(event)
        self._update_transfer_max_btn_pos()

    def resizeEvent(self, event):
        """[ADD] 리사이즈 시 USD 라벨 위치 조정"""
        super().resizeEvent(event)
        # qty_edit 내부에서 오른쪽 전체 영역 차지
        if hasattr(self, 'qty_value_label'):
            self.qty_value_label.setGeometry(
                0, 0,
                self.qty_edit.width(),
                self.qty_edit.height()
            )
        self._update_transfer_max_btn_pos()

    def _on_perp_clicked(self):
        """Perp 버튼 클릭"""
        self.perp_btn.setChecked(True)
        self.spot_btn.setChecked(False)
        # DEX 콤보 활성화 (HL-like만)
        if self.dex_combo:
            self.dex_combo.setEnabled(True)
        self.ticker_edit.set_spot_mode(False)
        self._adjust_pos_label_width(is_spot=False)
        self.clear_position_display()
        self.market_type_changed.emit(self.ex_name, "perp")

    def _on_spot_clicked(self):
        """Spot 버튼 클릭"""
        if not self._has_spot:
            # Spot 없으면 클릭 무시하고 Perp 유지
            self.spot_btn.setChecked(False)
            self.perp_btn.setChecked(True)
            return
        self.perp_btn.setChecked(False)
        self.spot_btn.setChecked(True)
        # DEX 콤보 비활성화 (Spot은 DEX 선택 무시)
        if self.dex_combo:
            self.dex_combo.setEnabled(False)
        self.ticker_edit.set_spot_mode(True)
        self._adjust_pos_label_width(is_spot=True)
        self.clear_position_display()
        self.market_type_changed.emit(self.ex_name, "spot")

    def set_has_spot(self, has_spot: bool):
        """Spot 지원 여부 설정 - 비활성화/활성화"""
        self._has_spot = has_spot
        self.spot_btn.setEnabled(has_spot)
        
        # Spot 지원 안 하면 Perp로 강제 전환
        if not has_spot and self.spot_btn.isChecked():
            self.perp_btn.setChecked(True)
            self.spot_btn.setChecked(False)
            if self.dex_combo:
                self.dex_combo.setEnabled(True)

    def set_market_type(self, market_type: str):
        """외부에서 market type 설정"""
        is_perp = (market_type.lower() != "spot")
        self.perp_btn.setChecked(is_perp)
        self.spot_btn.setChecked(not is_perp)
        # DEX 콤보 상태 업데이트
        if self.dex_combo:
            self.dex_combo.setEnabled(is_perp)

    def get_market_type(self) -> str:
        """현재 market type 반환"""
        return "spot" if self.spot_btn.isChecked() else "perp"

    def _on_card_group_clicked(self, g: int):
        """[ADD] 카드 그룹 버튼 클릭"""
        self.current_group = g
        for gg, btn in self.group_buttons.items():
            btn.setChecked(gg == g)
        self.group_changed.emit(self.ex_name, g)

    def set_group(self, g: int):
        """[ADD] 외부에서 그룹 설정"""
        self.current_group = g
        for gg, btn in self.group_buttons.items():
            btn.setChecked(gg == g)

    def _on_market_clicked(self):
        self.market_btn.setChecked(True)
        self.limit_btn.setChecked(False)
        self.price_edit.clear()  # 가격 입력란 비우기
        self.price_edit.setEnabled(False)
        self.price_edit.setPlaceholderText("auto")
        self.order_type_changed.emit(self.ex_name, "market")

    def _on_limit_clicked(self):
        self.market_btn.setChecked(False)
        self.limit_btn.setChecked(True)
        self.price_edit.setEnabled(True)
        self.price_edit.setPlaceholderText("")
        self.order_type_changed.emit(self.ex_name, "limit")

    def _on_detail_left_clicked(self):
        self.detail_left_btn.setChecked(True)
        self.detail_right_btn.setChecked(False)

    def _on_detail_right_clicked(self):
        self.detail_left_btn.setChecked(False)
        self.detail_right_btn.setChecked(True)

    def _on_detail_clicked(self):
        direction = "left" if self.detail_left_btn.isChecked() else "right"
        self.detail_order_clicked.emit(self.ex_name, direction)

    def get_detail_direction(self) -> str:
        """현재 선택된 상세 방향 반환"""
        return "left" if self.detail_left_btn.isChecked() else "right"

    def _connect_signals(self) -> None:
        self.exec_btn.clicked.connect(lambda: self.execute_clicked.emit(self.ex_name))
        self.long_btn.clicked.connect(lambda: self.long_clicked.emit(self.ex_name))
        self.short_btn.clicked.connect(lambda: self.short_clicked.emit(self.ex_name))
        self.off_btn.clicked.connect(lambda: self.off_clicked.emit(self.ex_name))
        self.detail_btn.clicked.connect(self._on_detail_clicked)
        self.close_pos_btn.clicked.connect(lambda: self.close_position_clicked.emit(self.ex_name))

        # 방향 버튼 토글 (라디오 버튼처럼 동작)
        self.detail_left_btn.clicked.connect(self._on_detail_left_clicked)
        self.detail_right_btn.clicked.connect(self._on_detail_right_clicked)

        self.market_btn.clicked.connect(self._on_market_clicked)
        self.limit_btn.clicked.connect(self._on_limit_clicked)

        self.perp_btn.clicked.connect(self._on_perp_clicked)
        self.spot_btn.clicked.connect(self._on_spot_clicked)

        #self.ticker_edit.editingFinished.connect(
        #    lambda: self.ticker_changed.emit(self.ex_name, self.ticker_edit.text())
        #)
        # [CHANGED] SearchableComboBox의 text_confirmed 시그널 사용
        self.ticker_edit.text_confirmed.connect(
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
        """ticker 설정"""
        current = self.ticker_edit.currentText()
        if current != t:
            self.ticker_edit.setEditText(t)
        #if self.ticker_edit.text() != t: self.ticker_edit.setText(t)

    def set_symbol_list(self, symbols: list):
        """
        심볼 자동완성 목록 설정.
        symbols: ["BTC", "ETH", "SOL", ...] 형태
        """
        self.ticker_edit.set_items(symbols)

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
        self._update_qty_value()

    def set_quote_label(self, txt): self.quote_label.setText(txt or "")
    
    def set_fee_label(self, txt):
        if self.fee_label:
            self.fee_label.setText(txt)

    def set_has_orderbook(self, has_orderbook: bool):
        """오더북 기능 지원 여부에 따라 상세 버튼 활성화/비활성화"""
        self.detail_btn.setEnabled(has_orderbook)
        self.detail_left_btn.setEnabled(has_orderbook)
        self.detail_right_btn.setEnabled(has_orderbook)

    def _adjust_pos_label_width(self, is_spot: bool):
        """[ADD] 포지션 라벨 너비를 모드에 따라 조정"""
        if is_spot:
            # Spot: 더 긴 텍스트 (수량 + $ + 주문가능)
            self.pos_side_label.setFixedWidth(300)
        else:
            # Perp: LONG/SHORT만
            self.pos_side_label.setFixedWidth(80)

    def clear_position_display(self):
        """[ADD] 포지션 표시 초기화 (로딩 상태)"""
        self.pos_side_label.setText("")
        self.pos_side_label.setStyleSheet(f"color: {CLR_MUTED};")
        self.pos_size_label.setText("")
        self.pos_size_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.pos_size_label.setStyleSheet(f"color: {CLR_MUTED};")
        self.pos_pnl_label.setText("")
        self.pos_pnl_label.setStyleSheet(f"color: {CLR_MUTED};")

    def set_status_info(self, json_data: dict):
        """
        json_data format:
        {
            # old"collateral": {
                "perp": {"USDC": 12.1},  # or None
                "spot": {"USDT": 10.2, "USDC": 15.0, ...}  # or None
            },
            "collateral": {
                "perp": {"USDC": {
                    "total":12.1,
                    "available":10.0
                }
                },  # or None
                "spot": {"USDT": {
                    "total":10.1,
                    "available":8.0
                }, # or None
                "USDC": {}, ...
                }  # or None
            },
            "position": {
                "size": 0.002,
                "side": "short",  # "long" or "short"
                "unrealized_pnl": 1.2
            },  # or None
            "coin_balance": {  # Spot일 때만 존재
                "coin": "HYPE",
                "available": 90.0,
                "locked": 10.0,
                #"staked": 0.0,
                "total": 100.0
            }
        }
        """
        CLR_LONG = "#81c784"
        CLR_SHORT = "#ef9a9a"
        CLR_NEUTRAL = "#e0e0e0"
        CLR_PNL_POS = "#4caf50"
        CLR_PNL_NEG = "#f44336"

        # [ADD] json_data가 없거나 비어있으면 포지션만 초기화하고 collateral은 유지
        if not json_data:
            return
        
        coin_balance = json_data.get("coin_balance") if json_data else None
        if coin_balance:
            coin = coin_balance.get("coin", "")
            total = coin_balance.get("total", 0)
            available = coin_balance.get("available", 0)
            
            # 포지션 행: Spot은 코인 잔고 표시
            #self.pos_side_label.setText("")
            #self.pos_side_label.setStyleSheet(f"color: {CLR_MUTED};")
            
            # 수량 + USD 가치 표시
            size_text = f"{_format_size(total)} <span style='color: {CLR_COLLATERAL};'>{coin}</span>"
            if self._current_price and self._current_price > 0:
                usd_value = total * self._current_price
                size_text += f" <span style='color: {CLR_MUTED};'>(≈{usd_value:,.1f}$)</span>"
            
            # total != available 이면 주문가능 수량 표시
            if total != available and total > 0:
                size_text += f" <span style='color: {CLR_MUTED};'>[주문가능: {_format_size(available)}]</span>"
            
            self.pos_side_label.setText(f"{size_text}")
            self.pos_side_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.pos_side_label.setStyleSheet(f"color: {CLR_NEUTRAL};")
            
            # [ADD] Spot 모드: Perp용 라벨 초기화 (이전 상태 제거)
            self.pos_size_label.setText("")
            self.pos_size_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self.pos_size_label.setStyleSheet(f"color: {CLR_MUTED};")
            
            self.pos_pnl_label.setText("")
            self.pos_pnl_label.setStyleSheet(f"color: {CLR_MUTED};")
            
            # 잔고 행: 기존 perp/spot collateral 처리
            collateral = json_data.get("collateral")
            if collateral and (collateral.get("perp") or collateral.get("spot")):
                self._render_collateral(collateral, CLR_NEUTRAL)
            return
        
        # === Perp 모드 (기존 코드) ===
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
        collateral = json_data.get("collateral")
        if collateral and (collateral.get("perp") or collateral.get("spot")):
            self._render_collateral(collateral, CLR_NEUTRAL)

    def _render_collateral(self, collateral: dict, CLR_NEUTRAL: str):
        """[ADD] 잔고 렌더링 헬퍼 (Perp/Spot 공용)"""
        # Perp 잔고
        perp_data = collateral.get("perp") if collateral else None
        perp_coin = ""
        perp_amount = 0.0

        if perp_data and any(v != 0 for v in perp_data.values()):
            perp_parts = []
            for k, v in perp_data.items():
                if v != 0:
                    perp_parts.append(f"{_format_collateral(v)} <span style='color:{CLR_COLLATERAL};'>{k}</span>")
                    # 첫 번째 perp collateral 정보 저장
                    if perp_amount == 0:
                        perp_coin = k
                        perp_amount = float(v)
            self.collat_perp_label.setText(", ".join(perp_parts) if perp_parts else "")
            self.collat_perp_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.collat_perp_label.setStyleSheet(f"color: {CLR_NEUTRAL};")
        else:
            self.collat_perp_label.setText("")
            self.collat_perp_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            self.collat_perp_label.setStyleSheet(f"color: {CLR_MUTED};")
        
        # Spot 잔고
        spot_data = collateral.get("spot") if collateral else {}
        spot_nonzero = {k: v for k, v in spot_data.items() if v and float(v) != 0}
        has_spot_collateral = len(spot_nonzero) > 0

        # [ADD] Spot에서 perp_coin과 같은 코인의 잔고 찾기
        spot_amount = float(spot_data.get(perp_coin, 0) or 0)

        if has_spot_collateral:
            spot_parts = []
            for k, v in spot_data.items():
                if v != 0:
                    spot_parts.append(
                        f"<span style='background-color:#333; padding:3px 8px; border-radius:3px;'>"
                        f"{_format_collateral(v)} <span style='color:{CLR_MUTED};'>{k}</span></span>"
                    )
            self.collat_spot_label.setText("&nbsp;&nbsp;&nbsp;&nbsp;".join(spot_parts))
            self.collat_spot_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
            self.collat_spot_label.setStyleSheet(f"color: {CLR_NEUTRAL};")
        else:
            self.collat_spot_label.setText("")
        
        # 전송용 collateral 정보 업데이트
        self.set_collateral_info(perp_coin, perp_amount, spot_amount)

        # Spot 위젯들 보이기/숨기기
        if self.spot_sep_label:
            self.spot_sep_label.setVisible(has_spot_collateral)
        if self.spot_title_label:
            self.spot_title_label.setVisible(has_spot_collateral)
        self.collat_spot_label.setVisible(has_spot_collateral)

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
    group_changed = QtCore.Signal(int)

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

        BTN_GROUP_STYLE = """
            QPushButton {
                background-color: #3a3a3a;
                color: #e0e0e0;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 4px 8px;
                min-width: 24px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
                border-color: #666;
            }
            QPushButton:checked {
                background-color: #1b5e20;
                border: 2px solid #81c784;
                color: #81c784;
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

        self.group_buttons: Dict[int, QtWidgets.QPushButton] = {}
        self.current_group = 0
        
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
        rows[row_id].addStretch(20)
        
        rows[row_id].addWidget(self._label("그룹", CLR_MUTED))
        for g in range(GROUP_COUNT):
            btn = QtWidgets.QPushButton(str(g))
            btn.setCheckable(True)
            btn.setChecked(g == 0)
            btn.setStyleSheet(_get_group_btn_style(g, is_card=False))  # [CHANGED]
            btn.setFixedWidth(32)
            btn.clicked.connect(lambda checked, gg=g: self._on_group_clicked(gg))
            self.group_buttons[g] = btn
            rows[row_id].addWidget(btn)
        
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

    def _on_group_clicked(self, g: int):
        """[ADD] 그룹 버튼 클릭 시 호출"""
        self.current_group = g
        for gg, btn in self.group_buttons.items():
            btn.setChecked(gg == g)
        self.group_changed.emit(g)

    def _label(self, text, color):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(f"color: {color};")
        return lbl

    def _connect_signals(self):
        self.ticker_edit.editingFinished.connect(
            lambda: self.ticker_changed.emit(self.ticker_edit.text())
        )
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

        # 아이콘 설정 (icon.png 우선, 없으면 이모지 fallback)
        import os
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))
        else:
            pixmap = QtGui.QPixmap(64, 64)
            pixmap.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setFont(QtGui.QFont("Segoe UI Emoji", 48))
            painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, "🐌")
            painter.end()
            self.setWindowIcon(QtGui.QIcon(pixmap))

        self.mgr = manager
        self.service = TradingService(self.mgr)

        # State
        names = self.mgr.all_names()
        self.symbol = "BTC"
        # 형식: {
        #     "hl_ex": {"perp": {"hl": [...], "xyz": [...]}, "spot": [...]},
        #     "non_hl_ex": {"perp": [...], "spot": [...]},
        #     ...
        # }
        self._symbol_cache_by_ex: Dict[str, Dict[str, any]] = {}

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
        self.market_type_by_ex = {n: "perp" for n in names}

        # Tasks state
        self._stopping = False
        self._price_task = None
        self._status_task = None
        self._last_balance_at: dict[str, float] = {}
        self._last_pos_at: dict[str, float] = {}
        self._last_price_at: dict[str, float] = {}
        self._force_status_update: set[str] = set()  # 잔고/포지션 즉시 업데이트용
        self._force_open_orders_update: set[str] = set()  # 오픈오더 즉시 업데이트용
        self._initial_load_done: bool = False  # 초기 로딩 완료 여부

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

        # REPEAT / BURN 태스크
        #self._repeat_task: Optional[asyncio.Task] = None
        #self._repeat_cancel = asyncio.Event()
        #self._burn_task: Optional[asyncio.Task] = None
        #self._burn_cancel = asyncio.Event()

        # 그룹 관련 상태
        self.current_group = 0
        self.group_by_ex = {n: 0 for n in names}

        # 그룹별 헤더 캐시
        self.group_symbol: Dict[int, str] = {g: "BTC" for g in range(GROUP_COUNT)}
        self.group_qty: Dict[int, str] = {g: "" for g in range(GROUP_COUNT)}
        self.group_dex: Dict[int, str] = {g: "HL" for g in range(GROUP_COUNT)}

        # 그룹별 repeat/burn 입력값 캐시
        self.group_repeat_cfg: Dict[int, Dict[str, str]] = {
            g: {"times": "", "min": "", "max": ""} for g in range(GROUP_COUNT)
        }
        self.group_burn_cfg: Dict[int, Dict[str, str]] = {
            g: {"burn": "", "min": "", "max": ""} for g in range(GROUP_COUNT)
        }

        # 그룹별 repeat/burn 태스크
        self.repeat_task_by_group: Dict[int, Optional[asyncio.Task]] = {g: None for g in range(GROUP_COUNT)}
        self.repeat_cancel_by_group: Dict[int, asyncio.Event] = {g: asyncio.Event() for g in range(GROUP_COUNT)}
        self.burn_task_by_group: Dict[int, Optional[asyncio.Task]] = {g: None for g in range(GROUP_COUNT)}
        self.burn_cancel_by_group: Dict[int, asyncio.Event] = {g: asyncio.Event() for g in range(GROUP_COUNT)}

        self._switching_group = False

        # 오더북 패널 상태 (왼쪽/오른쪽 각각)
        self._orderbook_panel_exchange_left: Optional[str] = None
        self._orderbook_panel_symbol_left: Optional[str] = None
        self._orderbook_task_left: Optional[asyncio.Task] = None
        self._last_open_orders_at_left: float = 0.0

        self._orderbook_panel_exchange_right: Optional[str] = None
        self._orderbook_panel_symbol_right: Optional[str] = None
        self._orderbook_task_right: Optional[asyncio.Task] = None
        self._last_open_orders_at_right: float = 0.0

        self._build_main_layout()
        self._connect_header_signals()

    def _update_card_symbols(self, card_name: str, dex: str = "HL", market_type: str = "perp"):
        """
        카드의 심볼 목록을 DEX/마켓타입에 맞게 업데이트.
        
        캐시 구조 (거래소별):
            HL-like:  {"perp": {"hl": [...], "xyz": [...]}, "spot": [...] or None}
            비-HL:    {"perp": [...], "spot": [...] or None}
        
        Args:
            card_name: 거래소 이름
            dex: DEX 이름 (HL-like만 해당)
            market_type: "perp" 또는 "spot"
        """
        if card_name not in self.cards:
            return
        
        card = self.cards[card_name]
        is_hl = self.mgr.is_hl_like(card_name)
        
        # 해당 거래소의 캐시 가져오기
        ex_cache = self._symbol_cache_by_ex.get(card_name, {})
        if not ex_cache:
            return
        
        symbols = []
        
        if market_type == "spot":
            # spot은 HL/비-HL 모두 단순 리스트 (또는 None/없음)
            spot_data = ex_cache.get("spot")
            if spot_data and isinstance(spot_data, list):
                symbols = spot_data
        else:
            # perp
            perp_data = ex_cache.get("perp", {})
            
            if is_hl:
                # HL-like: perp는 dict {"hl": [...], "xyz": [...], ...}
                if isinstance(perp_data, dict):
                    dex_key = dex.lower() if dex and dex != "HL" else "hl"
                    symbols = perp_data.get(dex_key, [])
                    # 해당 DEX 목록 없으면 HL 기본 목록 사용
                    if not symbols:
                        symbols = perp_data.get("hl", [])
            else:
                # 비-HL: perp는 단순 리스트
                if isinstance(perp_data, list):
                    symbols = perp_data
        
        card.set_symbol_list(symbols)

    async def refresh_symbol_list(self):
        """
        모든 거래소에서 심볼 목록을 가져와 캐시 업데이트.
        
        각 거래소의 get_available_symbols() 반환 형식:
            HL-like:  {"perp": {"hl": [...], "xyz": [...]}, "spot": [...] or None}
            비-HL:    {"perp": [...], "spot": [...] or None}
        """
        for name in self.mgr.available_names():
            try:
                ex = self.mgr.get_exchange(name)
                if not ex:
                    continue
                
                if hasattr(ex, "get_available_symbols"):
                    data = await ex.get_available_symbols()
                    if data:
                        self._symbol_cache_by_ex[name] = data
                        
                        # 해당 거래소 카드가 있으면 즉시 적용
                        if name in self.cards:
                            dex = self.dex_by_ex.get(name, "HL")
                            market_type = self.market_type_by_ex.get(name, "perp")
                            self._update_card_symbols(name, dex, market_type)
                            
            except Exception as e:
                logger.debug(f"[UI] Symbol list refresh failed for {name}: {e}")

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

        # 중앙 영역: Left Panel + Cards + Right Panel (QSplitter로 크기 조절 가능)
        self.center_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.center_splitter.setHandleWidth(4)
        self.center_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #555;
            }
            QSplitter::handle:hover {
                background-color: #777;
            }
        """)

        # 왼쪽 OrderBook Panel (초기에는 숨김)
        self.orderbook_panel_left = OrderBookPanel()
        self.orderbook_panel_left.setMinimumWidth(300)
        self.orderbook_panel_left.setVisible(False)
        self.orderbook_panel_left.close_clicked.connect(lambda: self._on_orderbook_panel_close("left"))
        self.orderbook_panel_left.cancel_all_clicked.connect(lambda: self._on_orderbook_cancel_all("left"))
        self.orderbook_panel_left.cancel_selected_clicked.connect(lambda orders: self._on_orderbook_cancel_selected(orders, "left"))
        self.orderbook_panel_left.price_clicked.connect(self._on_orderbook_price_clicked)
        self.center_splitter.addWidget(self.orderbook_panel_left)

        # Cards Scroll (중앙)
        cards_scroll = QtWidgets.QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setWidget(self.cards_container)
        self.center_splitter.addWidget(cards_scroll)

        # 오른쪽 OrderBook Panel (초기에는 숨김)
        self.orderbook_panel_right = OrderBookPanel()
        self.orderbook_panel_right.setMinimumWidth(300)
        self.orderbook_panel_right.setVisible(False)
        self.orderbook_panel_right.close_clicked.connect(lambda: self._on_orderbook_panel_close("right"))
        self.orderbook_panel_right.cancel_all_clicked.connect(lambda: self._on_orderbook_cancel_all("right"))
        self.orderbook_panel_right.cancel_selected_clicked.connect(lambda orders: self._on_orderbook_cancel_selected(orders, "right"))
        self.orderbook_panel_right.price_clicked.connect(self._on_orderbook_price_clicked)
        self.center_splitter.addWidget(self.orderbook_panel_right)

        # 오더북 패널 기본 너비
        self._orderbook_panel_width_left = 400
        self._orderbook_panel_width_right = 400

        # 초기 splitter 비율 설정 (left:cards:right)
        self.center_splitter.setSizes([0, 1000, 0])

        main_vbox.addWidget(self.center_splitter, stretch=2)

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
        logs_layout.addWidget(self.log_edit,stretch=3)
        system_output = QtWidgets.QLabel("온갖 로그:")
        system_output.setStyleSheet(f"color: rgba(109, 109, 109, 1);")
        logs_layout.addWidget(system_output)
        logs_layout.addWidget(self.console_edit,stretch=2)
        
        logs_gb = create_section("", logs_container)
        bottom_splitter.addWidget(logs_gb)
        
        bottom_splitter.setStretchFactor(0, 1)
        bottom_splitter.setStretchFactor(1, 2)
        main_vbox.addWidget(bottom_splitter, stretch=1)

        self.setCentralWidget(central)
        
        self.resize(UI_WINDOW_WIDTH, UI_WINDOW_HEIGHT)
        
        self.setStatusBar(QtWidgets.QStatusBar())
        self.statusBar().setSizeGripEnabled(True)

    def _on_header_group(self, g: int):
        """헤더 그룹 변경"""
        # 현재 그룹 값 저장
        cur = self.current_group
        self.group_symbol[cur] = _normalize_symbol_input(self.header.ticker_edit.text() or "BTC")
        self.group_qty[cur] = self.header.allqty_edit.text().strip()
        self.group_dex[cur] = self.header.dex_combo.currentText() or "HL"
        
        self.group_repeat_cfg[cur] = {
            "times": self.header.repeat_times.text().strip(),
            "min": self.header.repeat_min.text().strip(),
            "max": self.header.repeat_max.text().strip(),
        }
        self.group_burn_cfg[cur] = {
            "burn": self.header.burn_count.text().strip(),
            "min": self.header.burn_min.text().strip(),
            "max": self.header.burn_max.text().strip(),
        }
        
        # 그룹 변경
        self.current_group = g
        
        # 새 그룹 값 복원 (전파하지 않음)
        self._switching_group = True
        try:
            ng = self.current_group
            self.header.ticker_edit.setText(self.group_symbol.get(ng, "BTC"))
            self.header.allqty_edit.setText(self.group_qty.get(ng, ""))
            
            dex = self.group_dex.get(ng, "HL")
            idx = self.header.dex_combo.findText(dex, QtCore.Qt.MatchFlag.MatchFixedString)
            if idx >= 0:
                self.header.dex_combo.setCurrentIndex(idx)
            
            self.header.repeat_times.setText(self.group_repeat_cfg[ng]["times"])
            self.header.repeat_min.setText(self.group_repeat_cfg[ng]["min"])
            self.header.repeat_max.setText(self.group_repeat_cfg[ng]["max"])
            self.header.burn_count.setText(self.group_burn_cfg[ng]["burn"])
            self.header.burn_min.setText(self.group_burn_cfg[ng]["min"])
            self.header.burn_max.setText(self.group_burn_cfg[ng]["max"])
        finally:
            self._switching_group = False

    def _on_card_group(self, ex_name: str, g: int):
        """카드 그룹 변경"""
        self.group_by_ex[ex_name] = g

    def _on_market_type_change(self, n: str, market_type: str):
        """카드의 Perp/Spot 변경 처리"""
        self.market_type_by_ex[n] = market_type

        # 심볼 목록 업데이트
        dex = self.dex_by_ex.get(n, "HL")
        self._update_card_symbols(n, dex, market_type)

        # 심볼 자동 선택
        if n in self.cards:
            card = self.cards[n]
            ex_cache = self._symbol_cache_by_ex.get(n, {})

            # 새 목록 가져오기
            if market_type == "spot":
                symbols = ex_cache.get("spot", [])
            else:
                perp_data = ex_cache.get("perp", {})
                if self.mgr.is_hl_like(n) and isinstance(perp_data, dict):
                    dex_key = dex.lower() if dex and dex != "HL" else "hl"
                    symbols = perp_data.get(dex_key, perp_data.get("hl", []))
                elif isinstance(perp_data, list):
                    symbols = perp_data
                else:
                    symbols = []

            # 자동 선택 및 적용
            if symbols:
                selected = card._auto_select_symbol(symbols)
                if selected:
                    # 심볼 정규화 후 설정
                    normalized = card.ticker_edit._normalize_symbol(selected)
                    card.ticker_edit.setEditText(normalized)
                    # 상태 업데이트
                    self.symbol_by_ex[n] = normalized
                    self.exchange_state[n].symbol = normalized

        # 오더북 패널이 열려있으면 새 심볼로 다시 열기
        for direction in ["left", "right"]:
            if self._get_panel_exchange(direction) == n:
                asyncio.get_event_loop().create_task(
                    self._open_orderbook_panel(n, direction)
                )

    def _is_group_cancelled(self, g: int) -> bool:
        """그룹별 취소 여부"""
        return (self.repeat_cancel_by_group[g].is_set() or 
                self.burn_cancel_by_group[g].is_set())

    def _connect_header_signals(self):
        h = self.header
        h.ticker_changed.connect(self._on_header_ticker)
        h.allqty_changed.connect(self._on_allqty)
        h.exec_all_clicked.connect(self._on_exec_all)
        h.reverse_clicked.connect(self._on_reverse)
        h.close_all_clicked.connect(self._on_close_all)
        h.repeat_clicked.connect(self._on_repeat_toggle)
        h.burn_clicked.connect(self._on_burn_toggle)
        h.quit_clicked.connect(self.close)
        h.dex_changed.connect(self._on_header_dex)
        h.group_changed.connect(self._on_header_group)

    @QtCore.Slot(str)
    def _append_console_text(self, text: str):
        text = text.replace("\r\n", "\n")
        if text.strip():
            # 현재 스크롤바가 맨 아래에 있는지 확인
            sb = self.console_edit.verticalScrollBar()
            at_bottom = (sb.value() >= sb.maximum() - 10)  # 약간의 여유
            
            self.console_edit.appendPlainText(text.rstrip())
            
            # 맨 아래에 있었을 때만 자동 스크롤
            if at_bottom:
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

        # [ADD] 심볼 목록 초기화 (비동기로 백그라운드에서)
        asyncio.get_running_loop().create_task(self.refresh_symbol_list())

        loop = asyncio.get_running_loop()
        self._price_task = loop.create_task(self._price_loop())
        self._status_task = loop.create_task(self._status_loop())

    def _build_switches(self):
        while self.exchange_switch_layout.count():
            w = self.exchange_switch_layout.takeAt(0).widget()
            if w: w.deleteLater()
        self.exchange_switches.clear()

        # show=never인 거래소는 선택지에서 제외
        names = self.mgr.available_names()
        if not names: return

        row, col = 0, 0
        for name in names:
            meta = self.mgr.get_meta(name)
            cb = QtWidgets.QCheckBox(name.upper())
            cb.setChecked(meta.get("show") is True)
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
                    meta = self.mgr.get_meta(name)
                    setup = meta.get("initial_setup", {}) # [ADD] 초기값 가져오기

                    card = ExchangeCardWidget(name, self.dex_names, is_hl_like=is_hl_like)
                    
                    st = self.exchange_state[name]
                    card.set_ticker(setup.get("symbol",st.symbol))

                    if is_hl_like:
                        init_dex = setup.get("dex", "HL").upper()
                        self.dex_by_ex[name] = init_dex
                        st.dex = init_dex
                        card.set_dex(init_dex)
                    
                    init_symbol = setup.get("symbol", st.symbol)
                    self.symbol_by_ex[name] = init_symbol
                    st.symbol = init_symbol

                    setup_qty = setup.get("amount",None)
                    if setup_qty:
                        card.set_qty(setup_qty)
                        
                    # 2) (신규) initial_setup의 long/short/off 반영
                    #    - setup에서 side 값을 가져와 buy/sell/None으로 변환
                    raw_side = (setup.get("side") or setup.get("long_short") or "").strip().lower()
                    setup_side = None
                    if raw_side in ("long", "l", "buy"):
                        setup_side = "buy"
                    elif raw_side in ("short", "s", "sell"):
                        setup_side = "sell"
                    elif raw_side in ("off", "none", "", "null"):
                        setup_side = None

                    if setup.get("group") is not None:
                        try:
                            init_group = int(setup.get("group", 0))
                            if init_group < GROUP_MIN: init_group = GROUP_MIN
                            if init_group > GROUP_MAX: init_group = GROUP_MAX
                            self.group_by_ex[name] = init_group
                            card.set_group(init_group)
                        except:
                            pass

                    # "초기값"일 때만 적용(사용자가 이미 눌러둔 상태 보호)
                    #    - st.enabled=False & st.side=None 상태면 초기값 적용
                    if (not st.enabled) and (st.side is None) and raw_side:
                        if setup_side is None:
                            st.enabled = False
                            st.side = None
                        else:
                            st.enabled = True
                            st.side = setup_side
                        # UI 상태도 동기화
                        self.enabled[name] = st.enabled
                        self.side[name] = st.side

                    # 4) 카드에 최종 상태 반영
                    card.set_order_type(st.order_type)
                    card.set_side_enabled(st.enabled, st.side)
                    
                    if is_hl_like:
                        card.set_dex(setup.get("dex",st.dex))
                    
                    # Signals 연결
                    card.execute_clicked.connect(self._on_exec_one)
                    card.long_clicked.connect(self._on_long)
                    card.short_clicked.connect(self._on_short)
                    card.off_clicked.connect(self._on_off)
                    card.order_type_changed.connect(self._on_otype_change)
                    card.dex_changed.connect(self._on_card_dex)
                    card.ticker_changed.connect(self._on_card_ticker)
                    card.group_changed.connect(self._on_card_group)
                    card.market_type_changed.connect(self._on_market_type_change)
                    card.transfer_execute.connect(self._on_transfer_execute)
                    card.detail_order_clicked.connect(self._on_detail_order)
                    card.close_position_clicked.connect(self._on_close_position)

                    self.cards[name] = card
                    '''
                    if name in self._symbol_cache_by_ex:
                        dex = self.dex_by_ex.get(name, "HL")
                        market_type = self.market_type_by_ex.get(name, "perp")
                        self._update_card_symbols(name, dex, market_type)
                        
                        # has_spot 설정
                        ex_cache = self._symbol_cache_by_ex[name]
                        spot_data = ex_cache.get("spot")
                        has_spot = bool(spot_data and isinstance(spot_data, list) and len(spot_data) > 0)
                        card.set_has_spot(has_spot)
                    else:
                    '''
                    # exchange instance에서 확인, get_available_symbols가 준비 안됐을수도 있기때문.
                    ex = self.mgr.get_exchange(name)
                    if ex and hasattr(ex, "has_spot"):
                        card.set_has_spot(ex.has_spot)

                    if ex and hasattr(ex, "transfer_to_perp") and hasattr(ex, "transfer_to_spot"):
                        card.set_has_transfer(True)
                    else:
                        card.set_has_transfer(False)

                    # 오더북 지원 여부 확인
                    has_orderbook = ex and hasattr(ex, "get_orderbook")
                    card.set_has_orderbook(has_orderbook)

                    if name in self._symbol_cache_by_ex:
                        dex = self.dex_by_ex.get(name, "HL")
                        self._update_card_symbols(name, dex)
                
                # 카드를 레이아웃에 추가
                self.cards_layout.addWidget(self.cards[name])
            
            # 마지막에 stretch 추가
            self.cards_layout.addStretch(1)
        
        # All Qty 동기화: 현재 그룹만
        aq = self.header.allqty_edit.text()
        if aq:
            g = self.current_group
            for n, c in self.cards.items():
                if self.group_by_ex.get(n, 0) == g:
                    c.set_qty(aq)
        
        # HL-like만 fee 업데이트
        for n in visible_names:
            if self.mgr.is_hl_like(n):
                self._update_fee(n)

    # --- Handlers ---
    def _on_header_ticker(self, t):
        """[CHANGED] 현재 그룹의 카드에만 ticker 전파"""
        if self._switching_group:
            return
        
        s = _normalize_symbol_input(t)
        self.symbol = s
        g = self.current_group
        
        for n in self.mgr.visible_names():
            # [ADD] 그룹 필터: 현재 그룹만
            if self.group_by_ex.get(n, 0) != g:
                continue
            
            self.symbol_by_ex[n] = s
            self.exchange_state[n].symbol = s
            if n in self.cards:
                self.cards[n].set_ticker(s)

    def _on_allqty(self, t):
        """[CHANGED] 현재 그룹의 카드에만 수량 전파"""
        if self._switching_group:
            return
        
        g = self.current_group
        
        for n in self.mgr.visible_names():
            # [ADD] 그룹 필터: 현재 그룹만
            if self.group_by_ex.get(n, 0) != g:
                continue
            
            if n in self.cards:
                self.cards[n].set_qty(t)

    def _on_header_dex(self, d):
        """[CHANGED] 현재 그룹의 HL-like 카드에만 DEX 전파"""
        if self._switching_group:
            return

        if not d:  # None 또는 빈 문자열 방지
            d = "HL"

        self.header_dex = d
        g = self.current_group

        for n in self.mgr.visible_names():
            # [ADD] 그룹 필터: 현재 그룹만
            if self.group_by_ex.get(n, 0) != g:
                continue

            if self.mgr.is_hl_like(n):
                self.dex_by_ex[n] = d
                self.exchange_state[n].dex = d
                if n in self.cards:
                    self.cards[n].set_dex(d)
                    self._update_fee(n)
            
    def _on_card_ticker(self, n, t):
        s = _normalize_symbol_input(t or self.symbol)
        self.symbol_by_ex[n] = s
        self.exchange_state[n].symbol = s
        # 오더북 패널이 열려있으면 심볼 변경 시 갱신 (왼쪽/오른쪽 모두 체크)
        if self._orderbook_panel_exchange_left == n:
            asyncio.get_event_loop().create_task(self._refresh_orderbook_for_symbol(n, s, "left"))
        if self._orderbook_panel_exchange_right == n:
            asyncio.get_event_loop().create_task(self._refresh_orderbook_for_symbol(n, s, "right"))

    async def _refresh_orderbook_for_symbol(self, ex_name: str, symbol: str, direction: str = "right"):
        """심볼 변경 시 오더북 갱신 (WS 재구독)"""
        panel = self._get_panel_by_direction(direction)
        panel_exchange = self._get_panel_exchange(direction)
        panel_symbol = self._get_panel_symbol(direction)

        if panel_exchange != ex_name:
            return

        # 기존 구독 해제
        try:
            ex = self.mgr.get_exchange(ex_name)
            if ex and panel_symbol and hasattr(ex, "unsubscribe_orderbook"):
                await ex.unsubscribe_orderbook(panel_symbol)
        except Exception as e:
            self._log(f"[ORDERBOOK] unsubscribe 실패: {e}")

        # 새 심볼로 다시 열기 (_do_exec와 동일한 심볼 생성 방식)
        is_spot = self.market_type_by_ex.get(ex_name, "perp") == "spot"
        is_hl_like = self.mgr.is_hl_like(ex_name)
        if is_hl_like:
            sym = _compose_symbol(self.dex_by_ex.get(ex_name, "HL"), symbol, is_spot)
        else:
            sym = symbol.upper()

        quote = ex.get_perp_quote(sym)
        native_symbol = self.service._to_native_symbol(ex_name, sym, is_spot, quote=quote)

        if direction == "left":
            self._orderbook_panel_symbol_left = native_symbol
        else:
            self._orderbook_panel_symbol_right = native_symbol

        panel.set_exchange_info(ex_name, native_symbol)
        panel.clear()

        # 업데이트 태스크 재시작
        if direction == "left":
            if self._orderbook_task_left:
                self._orderbook_task_left.cancel()
            self._orderbook_task_left = asyncio.get_event_loop().create_task(
                self._orderbook_update_loop(ex_name, native_symbol, direction)
            )
        else:
            if self._orderbook_task_right:
                self._orderbook_task_right.cancel()
            self._orderbook_task_right = asyncio.get_event_loop().create_task(
                self._orderbook_update_loop(ex_name, native_symbol, direction)
            )

    def _on_card_dex(self, n, d):
        """카드의 DEX 변경 처리 (perp에서만 DEX 선택 가능)"""
        if not d:  # None 또는 빈 문자열 방지
            d = "HL"
        self.dex_by_ex[n] = d
        self.exchange_state[n].dex = d
        self._update_fee(n)

        # 심볼 목록 업데이트 (DEX 변경은 perp에서만 발생)
        market_type = self.market_type_by_ex.get(n, "perp")
        self._update_card_symbols(n, d, market_type)

        # 심볼 자동 선택 (perp인 경우만)
        if n in self.cards and market_type == "perp":
            card = self.cards[n]
            ex_cache = self._symbol_cache_by_ex.get(n, {})

            # 새 DEX의 perp 심볼 목록 가져오기
            perp_data = ex_cache.get("perp", {})
            if self.mgr.is_hl_like(n) and isinstance(perp_data, dict):
                dex_key = d.lower() if d and d != "HL" else "hl"
                symbols = perp_data.get(dex_key, perp_data.get("hl", []))
            elif isinstance(perp_data, list):
                symbols = perp_data
            else:
                symbols = []

            # 자동 선택 및 적용
            if symbols:
                selected = card._auto_select_symbol(symbols)
                if selected:
                    normalized = card.ticker_edit._normalize_symbol(selected)
                    card.ticker_edit.setEditText(normalized)
                    self.symbol_by_ex[n] = normalized
                    self.exchange_state[n].symbol = normalized

                    # 오더북 패널이 열려있으면 새 심볼로 갱신 (왼쪽/오른쪽 모두 체크)
                    if self._orderbook_panel_exchange_left == n:
                        asyncio.get_event_loop().create_task(
                            self._refresh_orderbook_for_symbol(n, normalized, "left")
                        )
                    if self._orderbook_panel_exchange_right == n:
                        asyncio.get_event_loop().create_task(
                            self._refresh_orderbook_for_symbol(n, normalized, "right")
                        )

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
        """[CHANGED] 현재 그룹만 reverse"""
        self._reverse_enabled(self.current_group)

    def _on_close_all(self):
        asyncio.get_running_loop().create_task(self._do_close_all())

    def _on_close_position(self, n: str):
        """개별 거래소 포지션 종료 핸들러"""
        asyncio.get_running_loop().create_task(self._do_close_position(n))

    async def _do_close_position(self, n: str):
        """개별 거래소 포지션 종료"""
        try:
            hint = float(self.current_price.replace(",", ""))
        except:
            hint = None

        is_hl_like = self.mgr.is_hl_like(n)
        is_spot = self.market_type_by_ex.get(n, "perp") == "spot"

        if is_hl_like:
            sym = _compose_symbol(self.dex_by_ex.get(n, "HL"), self.symbol_by_ex.get(n, "BTC"), is_spot)
        else:
            sym = self.symbol_by_ex.get(n, "BTC").upper()

        self._log(f"[{n.upper()}] 포지션 종료 시작... ({sym})")

        try:
            await self.service.close_position(n, sym, hint)
            self._log(f"[{n.upper()}] 포지션 종료 완료")
            self._force_status_update.add(n)
        except Exception as e:
            self._log(f"[{n.upper()}] 포지션 종료 실패: {e}")

    def _on_transfer_execute(self, n: str, info: dict):
        """[ADD] 전송 실행 핸들러"""
        asyncio.get_running_loop().create_task(self._do_transfer(n, info))

    async def _do_transfer(self, n: str, info: dict):
        """[ADD] 실제 전송 실행"""
        direction = info.get("direction")
        amount = info.get("amount")
        coin = info.get("coin", "USDC")
        
        self._log(f"[{n.upper()}] 전송 시작: {direction} {amount} {coin}")
        
        try:
            ex = self.mgr.get_exchange(n)
            if not ex:
                self._log(f"[{n.upper()}] 거래소 없음")
                return
            
            if direction == "to_perp":
                if hasattr(ex, "transfer_to_perp"):
                    result = await ex.transfer_to_perp(amount)
                    status = result.get('status', 'error')
                    if status == 'ok':
                        self._log(f"[{n.upper()}] Spot → Perp 전송 완료: {amount} {coin}")
                        self._force_status_update.add(n)
                    else:
                        self._log(f"[{n.upper()}] Spot → Perp 에러 : {str(result)}")
                else:
                    self._log(f"[{n.upper()}] transfer_to_perp 미지원")
            elif direction == "to_spot":
                if hasattr(ex, "transfer_to_spot"):
                    result = await ex.transfer_to_spot(amount)
                    status = result.get('status', 'error')
                    if status == 'ok':
                        self._log(f"[{n.upper()}] Perp → Spot 전송 완료: {amount} {coin}")
                        self._force_status_update.add(n)
                    else:
                        self._log(f"[{n.upper()}] Perp → Spot 에러 : {str(result)}")
                else:
                    self._log(f"[{n.upper()}] transfer_to_spot 미지원")
            else:
                self._log(f"[{n.upper()}] 알 수 없는 방향: {direction}")
                
        except Exception as e:
            self._log(f"[{n.upper()}] 전송 실패: {e}")

    # --- Actions ---
    async def _do_exec(self, n, silent=False) -> bool:
        """
        단일 거래소 주문 실행
        silent=True: 간단 결과만 반환 (EXEC ALL용)
        silent=False: 상세 로그 출력 (개별 버튼용)
        """
        c = self.cards.get(n)
        if not c:
            return False
        try:
            qty = float(c.get_qty())
            otype = self.order_type[n]
            price = float(c.get_price_text()) if otype == "limit" else None
            side = self.side[n]

            is_hl_like = self.mgr.is_hl_like(n)
            is_spot = self.market_type_by_ex.get(n, "perp") == "spot"

            if is_hl_like:
                dex = self.dex_by_ex.get(n) or "HL"  # None 방지
                sym = _compose_symbol(dex, self.symbol_by_ex[n], is_spot)
            else:
                sym = self.symbol_by_ex[n].upper()

            if not silent:
                self._log(f"[{n.upper()}] {side} {qty} {sym} @ {otype}")
            
            res = await self.service.execute_order(n, sym, qty, otype, side, price, is_spot=is_spot)

            if not silent:
                self._log(f"[{n.upper()}] OK: {res['id']}")

            # 주문 성공 시 즉시 업데이트 요청
            self._force_status_update.add(n)  # 잔고/포지션
            self._force_open_orders_update.add(n)  # 오픈오더 (limit 주문 시)

            return True
        except Exception as e:
            if not silent:
                self._log(f"[{n.upper()}] FAIL: {e}")
            raise e

    async def _do_exec_all(self, g: Optional[int] = None):
        """[CHANGED] 현재 그룹만 실행"""
        if g is None:
            g = self.current_group

        exec_items = []
        for n in self.mgr.visible_names():
            # [ADD] 그룹 필터
            if self.group_by_ex.get(n, 0) != g:
                continue
            if self.enabled.get(n) and self.side.get(n):
                exec_items.append(n)

        if not exec_items:
            self._log(f"[EXEC ALL:G{g}] 실행할 거래소 없음")
            return

        self._log(f"[EXEC ALL:G{g}] {len(exec_items)}개 거래소 주문 시작...")

        success = 0
        failed = 0

        # HL 거래소와 비-HL 거래소 분리
        hl_items = [n for n in exec_items if self.mgr.is_hl_like(n)]
        non_hl_items = [n for n in exec_items if not self.mgr.is_hl_like(n)]

        # 비-HL 거래소는 항상 병렬 실행
        if non_hl_items:
            tasks = [self._do_exec(n, silent=True) for n in non_hl_items]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for n, res in zip(non_hl_items, results):
                if isinstance(res, Exception):
                    self._log(f"  ✗ {n.upper()}: {res}")
                    failed += 1
                elif res:
                    self._log(f"  ✓ {n.upper()}: 주문 완료")
                    success += 1
                else:
                    failed += 1

        # HL 거래소 처리
        if hl_items:
            if HL_ORDER_DELAY == 0:
                # 완전 병렬 실행
                tasks = [self._do_exec(n, silent=True) for n in hl_items]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for n, res in zip(hl_items, results):
                    if isinstance(res, Exception):
                        self._log(f"  ✗ {n.upper()}: {res}")
                        failed += 1
                    elif res:
                        self._log(f"  ✓ {n.upper()}: 주문 완료")
                        success += 1
                    else:
                        failed += 1
            elif HL_ORDER_DELAY < 0:
                # 완전 순차 실행 (하나 끝나면 다음)
                for n in hl_items:
                    try:
                        res = await self._do_exec(n, silent=True)
                        if res:
                            self._log(f"  ✓ {n.upper()}: 주문 완료")
                            success += 1
                        else:
                            failed += 1
                    except Exception as e:
                        self._log(f"  ✗ {n.upper()}: {e}")
                        failed += 1
            else:
                # 미세 순차 실행 (딜레이 후 다음 시작, 결과는 나중에 취합)
                tasks = []
                for i, n in enumerate(hl_items):
                    if i > 0:
                        await asyncio.sleep(HL_ORDER_DELAY)
                    tasks.append(asyncio.create_task(self._do_exec(n, silent=True)))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for n, res in zip(hl_items, results):
                    if isinstance(res, Exception):
                        self._log(f"  ✗ {n.upper()}: {res}")
                        failed += 1
                    elif res:
                        self._log(f"  ✓ {n.upper()}: 주문 완료")
                        success += 1
                    else:
                        failed += 1

        self._log(f"[EXEC ALL:G{g}] 완료 (성공: {success}, 실패: {failed})")

    async def _do_close_all(self, g: Optional[int] = None):
        """[CHANGED] 현재 그룹만 close"""
        if g is None:
            g = self.current_group

        close_items = []
        for n in self.mgr.visible_names():
            if self.group_by_ex.get(n, 0) != g:
                continue

            if self.enabled.get(n):
                try:
                    hint = float(self.current_price.replace(",", ""))
                except:
                    hint = None

                is_hl_like = self.mgr.is_hl_like(n)
                is_spot = self.market_type_by_ex.get(n, "perp") == "spot"
                if is_hl_like:
                    dex = self.dex_by_ex.get(n) or "HL"  # None 방지
                    sym = _compose_symbol(dex, self.symbol_by_ex[n], is_spot)
                else:
                    sym = self.symbol_by_ex[n].upper()

                close_items.append((n, sym, hint, is_hl_like))

        if not close_items:
            self._log("[CLOSE ALL] 종료할 포지션 없음")
            return

        self._log(f"[CLOSE ALL] {len(close_items)}개 포지션 종료 시작...")

        success = 0
        failed = 0

        # HL 거래소와 비-HL 거래소 분리
        hl_items = [(n, sym, hint) for n, sym, hint, is_hl in close_items if is_hl]
        non_hl_items = [(n, sym, hint) for n, sym, hint, is_hl in close_items if not is_hl]
        
        # 비-HL 거래소는 항상 병렬 실행
        if non_hl_items:
            tasks = [self.service.close_position(n, sym, hint) for n, sym, hint in non_hl_items]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (n, sym, _), res in zip(non_hl_items, results):
                if isinstance(res, Exception):
                    self._log(f"  ✗ {n.upper()}: {res}")
                    failed += 1
                else:
                    self._log(f"  ✓ {n.upper()}: 종료 완료")
                    self._force_status_update.add(n)
                    success += 1

        # HL 거래소 처리
        if hl_items:
            if HL_ORDER_DELAY == 0:
                # 완전 병렬 실행
                tasks = [self.service.close_position(n, sym, hint) for n, sym, hint in hl_items]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for (n, sym, _), res in zip(hl_items, results):
                    if isinstance(res, Exception):
                        self._log(f"  ✗ {n.upper()}: {res}")
                        failed += 1
                    else:
                        self._log(f"  ✓ {n.upper()}: 종료 완료")
                        self._force_status_update.add(n)
                        success += 1
            elif HL_ORDER_DELAY < 0:
                # 완전 순차 실행 (하나 끝나면 다음)
                for n, sym, hint in hl_items:
                    try:
                        await self.service.close_position(n, sym, hint)
                        self._log(f"  ✓ {n.upper()}: 종료 완료")
                        self._force_status_update.add(n)
                        success += 1
                    except Exception as e:
                        self._log(f"  ✗ {n.upper()}: {e}")
                        failed += 1
            else:
                # 미세 순차 실행 (딜레이 후 다음 시작, 결과는 나중에 취합)
                tasks = []
                for i, (n, sym, hint) in enumerate(hl_items):
                    if i > 0:
                        await asyncio.sleep(HL_ORDER_DELAY)
                    tasks.append(asyncio.create_task(self.service.close_position(n, sym, hint)))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for (n, sym, _), res in zip(hl_items, results):
                    if isinstance(res, Exception):
                        self._log(f"  ✗ {n.upper()}: {res}")
                        failed += 1
                    else:
                        self._log(f"  ✓ {n.upper()}: 종료 완료")
                        self._force_status_update.add(n)
                        success += 1

        self._log(f"[CLOSE ALL] 완료 (성공: {success}, 실패: {failed})")

    def _on_repeat_toggle(self):
        """[CHANGED] 그룹별 독립 repeat 실행/중지"""
        loop = asyncio.get_running_loop()
        g = self.current_group

        # 이 그룹의 burn이 돌고 있으면 먼저 중지
        bt = self.burn_task_by_group.get(g)
        if bt and not bt.done():
            self.burn_cancel_by_group[g].set()
            self._log(f"[BURN:G{g}] 중지 요청")
            return

        # 이 그룹의 repeat 토글
        rt = self.repeat_task_by_group.get(g)
        if rt and not rt.done():
            self.repeat_cancel_by_group[g].set()
            self._log(f"[REPEAT:G{g}] 중지 요청")
            return

        # 시작
        try:
            times = int(self.header.repeat_times.text() or "0")
            a = float(self.header.repeat_min.text() or "0")
            b = float(self.header.repeat_max.text() or "0")
        except Exception:
            self._log(f"[REPEAT:G{g}] 입력 파싱 실패")
            return

        if times <= 0 or a < 0 or b < 0:
            self._log(f"[REPEAT:G{g}] Times>=1, Interval>=0 필요")
            return
        if b < a:
            a, b = b, a

        # 그룹별 cancel 초기화 및 task 저장
        self.repeat_cancel_by_group[g].clear()
        self.repeat_task_by_group[g] = loop.create_task(self._repeat_runner(g, times, a, b))
        self._log(f"[REPEAT:G{g}] 시작")

    def _on_burn_toggle(self):
        """[CHANGED] 그룹별 독립 burn 실행/중지"""
        loop = asyncio.get_running_loop()
        g = self.current_group

        # 이 그룹의 repeat가 돌고 있으면 먼저 중지
        rt = self.repeat_task_by_group.get(g)
        if rt and not rt.done():
            self.repeat_cancel_by_group[g].set()
            self._log(f"[REPEAT:G{g}] 중지 요청")

        # burn 토글
        bt = self.burn_task_by_group.get(g)
        if bt and not bt.done():
            self.burn_cancel_by_group[g].set()
            self._log(f"[BURN:G{g}] 중지 요청")
            return

        # 입력값 파싱
        try:
            base_times = int(self.header.repeat_times.text() or "0")
            rep_min = float(self.header.repeat_min.text() or "0")
            rep_max = float(self.header.repeat_max.text() or "0")
            burn_times = int(self.header.burn_count.text() or "0")
            burn_min = float(self.header.burn_min.text() or "0")
            burn_max = float(self.header.burn_max.text() or "0")
        except Exception:
            self._log(f"[BURN:G{g}] 입력 파싱 실패")
            return

        if base_times <= 0 or rep_min < 0 or rep_max < 0 or burn_min < 0 or burn_max < 0:
            self._log(f"[BURN:G{g}] Times>=1, Interval>=0 필요")
            return

        if rep_max < rep_min:
            rep_min, rep_max = rep_max, rep_min
        if burn_max < burn_min:
            burn_min, burn_max = burn_max, burn_min

        # 그룹별 cancel 초기화 및 task 저장
        self.burn_cancel_by_group[g].clear()
        self.burn_task_by_group[g] = loop.create_task(
            self._burn_runner(g, burn_times, base_times, rep_min, rep_max, burn_min, burn_max)
        )
        self._log(f"[BURN:G{g}] 시작")

    async def _repeat_runner(self, g: int, times: int, a: float, b: float):
        """
        [CHANGED] 그룹별 독립 repeat runner.
        - g: 그룹 번호
        - 최소 간격 0.5초 보장 (rate limit 고려)
        """
        import random
        MIN_INTERVAL = 0.5

        self._log(f"[REPEAT:G{g}] 시작: {times}회, 간격 {a:.2f}~{b:.2f}s 랜덤")
        try:
            for i in range(1, times + 1):
                if self._is_group_cancelled(g):
                    self._log(f"[REPEAT:G{g}] 취소됨 (진행 {i-1}/{times})")
                    break

                self._log(f"[REPEAT:G{g}] 실행 {i}/{times}")
                await self._do_exec_all(g)

                if i >= times:
                    break

                # 최소 간격 보장
                delay = max(MIN_INTERVAL, random.uniform(a, b))
                self._log(f"[REPEAT:G{g}] 대기 {delay:.2f}s ...")
                try:
                    await asyncio.wait_for(self._wait_cancel_any(g), timeout=delay)
                except asyncio.TimeoutError:
                    pass

                if self._is_group_cancelled(g):
                    self._log(f"[REPEAT:G{g}] 취소됨 (대기 중)")
                    break

            self._log(f"[REPEAT:G{g}] 완료")
        finally:
            self.repeat_task_by_group[g] = None
            self.repeat_cancel_by_group[g].clear()

    async def _burn_runner(self, g: int, burn_times: int, base_times: int,
                       rep_min: float, rep_max: float, burn_min: float, burn_max: float):
        """
        [CHANGED] 그룹별 독립 burn runner.
        burn_times=1 → repeat(base_times) 한 번만
        burn_times>=2 → repeat(base_times) → (sleep → reverse → repeat(2*base_times)) × (burn_times-1)
        burn_times<0  → 무한 루프
        """
        import random

        self._log(f"[BURN:G{g}] 시작: burn_times={burn_times}, base={base_times}, "
                f"repeat_interval={rep_min}~{rep_max}, burn_interval={burn_min}~{burn_max}")
        try:
            if self._is_group_cancelled(g):
                return

            # 1) 첫 라운드: repeat(base_times)
            await self._repeat_runner(g, base_times, rep_min, rep_max)
            if self._is_group_cancelled(g):
                return

            round_idx = 2
            while True:
                if burn_times > 0 and round_idx > burn_times:
                    break

                delay = random.uniform(burn_min, burn_max)
                self._log(f"[BURN:G{g}] interval 대기 {delay:.2f}s ... (round {round_idx}/{burn_times if burn_times>0 else '∞'})")
                try:
                    await asyncio.wait_for(self._wait_cancel_any(g), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                if self._is_group_cancelled(g):
                    break

                # reverse (그룹만)
                self._reverse_enabled(g)
                if self._is_group_cancelled(g):
                    break

                # repeat 2×base_times
                await self._repeat_runner(g, 2 * base_times, rep_min, rep_max)
                if self._is_group_cancelled(g):
                    break

                round_idx += 1

            self._log(f"[BURN:G{g}] 완료")
        finally:
            self.burn_task_by_group[g] = None
            self.burn_cancel_by_group[g].clear()

    async def _wait_cancel_any(self, g: int):
        """그룹별 cancel 이벤트 대기"""
        while not self._is_group_cancelled(g):
            await asyncio.sleep(0.05)

    def _reverse_enabled(self, g: Optional[int] = None):
        """
        활성(enabled=True) + 방향 선택된 거래소만 LONG↔SHORT 토글.
        그룹 지정 시 해당 그룹만 reverse.
        """
        if g is None:
            g = self.current_group

        cnt = 0
        for n in self.mgr.visible_names():
            if self.group_by_ex.get(n, 0) != g:
                continue
            if not self.enabled.get(n, False):
                continue

            cur = self.side.get(n)
            if cur == "buy":
                self._set_side(n, "sell")
                cnt += 1
            elif cur == "sell":
                self._set_side(n, "buy")
                cnt += 1

        self._log(f"[G{g}] REVERSE 완료: {cnt}개")

    # --- Loops ---
    async def _price_loop(self):
        while not self._stopping:
            try:
                # 간단화: 첫 번째 HL 거래소 or visible 첫번째
                ex = self.mgr.first_hl_exchange()
                # header.ticker_edit.text() 대신 확정된 self.symbol 사용
                coin = _normalize_symbol_input(self.symbol or "BTC")
                if ex:
                    sym = _compose_symbol(self.header_dex, coin)
                    p = await ex.get_mark_price(sym)
                    if p: 
                        self.current_price = f"{p:,.2f}"
                        self.header.set_price(self.current_price)
                
                # [CHANGED] Total Collateral: 선택된(enabled) 거래소만 합산
                tot = sum(
                    self.collateral.get(n, 0.0)
                    for n in self.mgr.visible_names()
                    if self.enabled.get(n, False)
                )
                self.header.set_total(tot)
            except: pass
            await asyncio.sleep(RATE["GAP_FOR_INF"])

    async def _update_single_card(self, n: str, now: float):
        """단일 카드 상태 업데이트 (병렬 처리용)"""
        try:
            if n not in self.cards:
                return
            c = self.cards[n]

            # 카드가 삭제 예정이거나 이미 삭제됐으면 스킵
            if not c.is_valid():
                return

            # 거래소 플랫폼별 업데이트 주기 결정
            meta = self.mgr.get_meta(n)
            exchange_platform = meta.get("exchange", "hyperliquid")

            try:
                col_interval = RATE["STATUS_COLLATERAL_INTERVAL"].get(
                    exchange_platform,
                    RATE["STATUS_COLLATERAL_INTERVAL"]["default"]
                )
                pos_interval = RATE["STATUS_POS_INTERVAL"].get(
                    exchange_platform,
                    RATE["STATUS_POS_INTERVAL"]["default"]
                )
                price_interval = RATE["CARD_PRICE_INTERVAL"].get(
                    exchange_platform,
                    RATE["CARD_PRICE_INTERVAL"]["default"]
                )
            except Exception:
                col_interval = RATE["STATUS_COLLATERAL_INTERVAL"]["default"]
                pos_interval = RATE["STATUS_POS_INTERVAL"]["default"]
                price_interval = RATE["CARD_PRICE_INTERVAL"]["default"]

            # 업데이트 필요 여부 판단 (force_update 시 즉시 업데이트)
            force_update = n in self._force_status_update
            need_collat = force_update or (now - self._last_balance_at.get(n, 0.0) >= col_interval)
            need_pos = force_update or (now - self._last_pos_at.get(n, 0.0) >= pos_interval)
            need_price = force_update or (now - self._last_price_at.get(n, 0.0) >= price_interval)

            # WS 지원 여부 확인 (operation별)
            ex = self.mgr.get_exchange(n)
            if not ex:
                return
            ws_price = _ws_supported(ex, "get_mark_price")
            ws_position = _ws_supported(ex, "get_position")
            ws_collateral = _ws_supported(ex, "get_collateral")
            is_hl_like = self.mgr.is_hl_like(n)
            is_spot = self.market_type_by_ex.get(n, "perp") == "spot"

            # [수정] 비-HL은 DEX 무시, HL-like만 DEX 적용
            if is_hl_like:
                dex = self.dex_by_ex.get(n) or "HL"  # None 방지
                sym = _compose_symbol(dex, self.symbol_by_ex[n], is_spot)
            else:
                sym = self.symbol_by_ex[n].upper()

            # 가격 업데이트
            if need_price or ws_price:
                try:
                    p = await self.service.fetch_price(n, sym, is_spot=is_spot)
                    c.set_price_label(p)
                    self._last_price_at[n] = now
                except RuntimeError:
                    return
                except Exception:
                    try:
                        c.set_price_label("Err")
                    except RuntimeError:
                        return

            # Quote 라벨 업데이트
            try:
                quote_str = ex.get_perp_quote(sym)
                c.set_quote_label(quote_str)
            except RuntimeError:
                return
            except Exception as e:
                logger.debug(f"[UI] quote update failed for {n}: {e}", exc_info=True)
                try:
                    c.set_quote_label("")
                except RuntimeError:
                    return

            # Builder Fee 업데이트 (HL-like만)
            if is_hl_like:
                self._update_fee(n)

            # 포지션/잔고 업데이트
            if need_pos or need_collat or ws_position or ws_collateral:
                try:
                    is_spot = self.market_type_by_ex.get(n, "perp") == "spot"
                    _pos, _col, total_col_val, json_data = await self.service.fetch_status(
                        n, sym,
                        need_balance=need_collat or ws_collateral,
                        need_position=need_pos or ws_position,
                        is_spot=is_spot
                    )

                    c.set_status_info(json_data)

                    if need_collat or ws_collateral:
                        if total_col_val:
                            self.collateral[n] = float(total_col_val)
                        self._last_balance_at[n] = now

                    if need_pos or ws_position:
                        self._last_pos_at[n] = now

                    # force update 플래그 해제
                    self._force_status_update.discard(n)

                except RuntimeError:
                    return
                except Exception as e:
                    logger.debug(f"[UI] Status update for {n} failed: {e}")

        except RuntimeError:
            # 카드가 삭제된 경우
            pass
        except Exception as e:
            logger.debug(f"[UI] Card update error for {n}: {e}")

    async def _status_loop(self):
        """
        거래소별 상태(가격/포지션/잔고) 업데이트 루프.
        - 초기 로딩: 순차 업데이트 (rate limit 방지)
        - 이후: 병렬 동시 업데이트
        - WS 거래소: 매 틱마다 업데이트
        - REST 거래소: RATE에 정의된 주기에 따라 업데이트
        """
        while not self._stopping:
            try:
                now = time.monotonic()
                visible_names = self.mgr.visible_names()
                
                # 병렬 업데이트
                tasks = [
                    self._update_single_card(n, now)
                    for n in visible_names
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                """
                for n in visible_names:
                    await self._update_single_card(n, now)
                self._initial_load_done = True
                
                
                if not self._initial_load_done:
                    # 초기 로딩: 순차 업데이트 (rate limit 방지)
                    for n in visible_names:
                        await self._update_single_card(n, now)
                    self._initial_load_done = True
                else:
                    # 이후: 병렬 업데이트
                    tasks = [
                        self._update_single_card(n, now)
                        for n in visible_names
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                """
                

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[UI] Status loop error: {e}")

            # 루프 간격
            await asyncio.sleep(RATE["GAP_FOR_INF"])

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
            is_spot = self.market_type_by_ex.get(n, "perp") == "spot"
            fee = self.service.get_display_builder_fee(n, dex_key, order_type, is_spot)
            
            if isinstance(fee, int):
                card.set_fee_label(f"Builder Fee: {fee}")
            else:
                card.set_fee_label("Builder Fee: -")
                
        except Exception as e:
            # 에러 시 조용히 무시 (로그만 남김)
            logger.debug(f"[UI] Fee update for {n} failed: {e}")
            print(f"[UI] Fee update for {n} failed: {e}")

    def _log(self, m):
        logger.info(m)
        
        # 현재 스크롤바가 맨 아래에 있는지 확인
        sb = self.log_edit.verticalScrollBar()
        at_bottom = (sb.value() >= sb.maximum() - 10)
        
        self.log_edit.appendPlainText(m)
        
        # 맨 아래에 있었을 때만 자동 스크롤
        if at_bottom:
            sb.setValue(sb.maximum())

    # ============================
    # 오더북 패널 핸들러
    # ============================
    def _on_detail_order(self, ex_name: str, direction: str = "right"):
        """상세 주문 버튼 클릭 핸들러"""
        asyncio.get_event_loop().create_task(self._toggle_orderbook_panel(ex_name, direction))

    def _on_orderbook_panel_close(self, direction: str = "right"):
        """오더북 패널 닫기 버튼 클릭"""
        asyncio.get_event_loop().create_task(self._close_orderbook_panel(direction))

    def _on_orderbook_cancel_all(self, direction: str = "right"):
        """오더북 패널 전체 취소 버튼 클릭"""
        asyncio.get_event_loop().create_task(self._do_cancel_all_orders(direction))

    def _on_orderbook_cancel_selected(self, selected_orders: list, direction: str = "right"):
        """오더북 패널 선택 취소 버튼 클릭"""
        asyncio.get_event_loop().create_task(self._do_cancel_selected_orders(selected_orders, direction))

    def _get_panel_by_direction(self, direction: str) -> OrderBookPanel:
        """방향에 따른 패널 반환"""
        return self.orderbook_panel_left if direction == "left" else self.orderbook_panel_right

    def _get_panel_exchange(self, direction: str) -> Optional[str]:
        """방향에 따른 거래소 이름 반환"""
        if direction == "left":
            return self._orderbook_panel_exchange_left
        return self._orderbook_panel_exchange_right

    def _get_panel_symbol(self, direction: str) -> Optional[str]:
        """방향에 따른 심볼 반환"""
        if direction == "left":
            return self._orderbook_panel_symbol_left
        return self._orderbook_panel_symbol_right

    async def _do_cancel_selected_orders(self, selected_orders: list, direction: str = "right"):
        """선택된 오픈 오더 취소"""
        if not selected_orders:
            self._log("[ORDERBOOK] 선택된 주문 없음")
            return

        ex_name = self._get_panel_exchange(direction)
        if not ex_name:
            return

        ex = self.mgr.get_exchange(ex_name)
        if not ex:
            self._log(f"[{ex_name}] 거래소 없음")
            return

        symbol = self._get_panel_symbol(direction)
        if not symbol:
            self._log(f"[{ex_name}] 심볼 없음")
            return

        try:
            if hasattr(ex, "cancel_orders"):
                await ex.cancel_orders(symbol, selected_orders)
                self._log(f"[{ex_name}] {len(selected_orders)}개 선택 주문 취소 완료")
                self._force_open_orders_update.add(ex_name)  # 오픈오더만
            else:
                self._log(f"[{ex_name}] cancel_orders 미지원")
        except Exception as e:
            self._log(f"[{ex_name}] 선택 주문 취소 실패: {e}")

    def _on_orderbook_price_clicked(self, price: float):
        """오더북 가격 클릭 시 해당 카드의 limit 가격으로 설정"""
        # 어느 패널에서 클릭했는지 확인
        sender = self.sender()
        ex_name = None
        if sender == self.orderbook_panel_left:
            ex_name = self._orderbook_panel_exchange_left
        elif sender == self.orderbook_panel_right:
            ex_name = self._orderbook_panel_exchange_right

        if not ex_name:
            return

        card = self.cards.get(ex_name)
        if card:
            # 주문 타입을 limit으로 변경
            card.set_order_type("limit")
            self.order_type[ex_name] = "limit"
            self.exchange_state[ex_name].order_type = "limit"
            # 가격 설정
            card.price_edit.setText(str(price))
            self._log(f"[{ex_name}] Limit 가격 설정: {price}")

    async def _toggle_orderbook_panel(self, ex_name: str, direction: str = "right"):
        """오더북 패널 토글"""
        panel = self._get_panel_by_direction(direction)
        panel_exchange = self._get_panel_exchange(direction)

        if panel_exchange == ex_name and panel.isVisible():
            # 같은 거래소면 토글 (닫기)
            await self._close_orderbook_panel(direction)
        else:
            # 다른 거래소면 열기
            await self._open_orderbook_panel(ex_name, direction)

    async def _open_orderbook_panel(self, ex_name: str, direction: str = "right"):
        """오더북 패널 열기"""
        panel = self._get_panel_by_direction(direction)
        opposite = "right" if direction == "left" else "left"

        # 같은 거래소가 반대쪽에 이미 열려있으면 그쪽을 닫음
        if self._get_panel_exchange(opposite) == ex_name:
            await self._close_orderbook_panel(opposite)

        # 해당 방향에 이미 열린 패널이 있으면 먼저 닫기 (WS 구독 해제)
        if self._get_panel_exchange(direction):
            await self._close_orderbook_panel(direction)

        # 거래소/심볼 설정
        if direction == "left":
            self._orderbook_panel_exchange_left = ex_name
        else:
            self._orderbook_panel_exchange_right = ex_name

        coin = self.symbol_by_ex.get(ex_name, "BTC")
        is_spot = self.market_type_by_ex.get(ex_name, "perp") == "spot"

        # _do_exec와 동일한 심볼 생성 방식 사용
        is_hl_like = self.mgr.is_hl_like(ex_name)
        if is_hl_like:
            sym = _compose_symbol(self.dex_by_ex.get(ex_name, "HL"), coin, is_spot)
        else:
            sym = coin.upper()

        ex = self.mgr.get_exchange(ex_name)
        quote = ex.get_perp_quote(sym)
        native_symbol = self.service._to_native_symbol(ex_name, sym, is_spot, quote=quote)

        if direction == "left":
            self._orderbook_panel_symbol_left = native_symbol
        else:
            self._orderbook_panel_symbol_right = native_symbol

        panel.set_exchange_info(ex_name, native_symbol)
        panel.setVisible(True)

        # 창 너비 확장 (카드 영역 유지 + 오더북 패널 추가)
        sizes = self.center_splitter.sizes()
        panel_width = self._orderbook_panel_width_left if direction == "left" else self._orderbook_panel_width_right

        # 왼쪽 확장 시 창 위치 이동
        if direction == "left":
            self.move(self.x() - panel_width, self.y())

        # 창 너비 확장
        self.resize(self.width() + panel_width, self.height())

        # Splitter 크기 설정 (left:cards:right)
        if direction == "left":
            self.center_splitter.setSizes([panel_width, sizes[1], sizes[2]])
        else:
            self.center_splitter.setSizes([sizes[0], sizes[1], panel_width])

        # 오더북 업데이트 루프 시작
        if direction == "left":
            if self._orderbook_task_left:
                self._orderbook_task_left.cancel()
            self._orderbook_task_left = asyncio.get_event_loop().create_task(
                self._orderbook_update_loop(ex_name, native_symbol, direction)
            )
        else:
            if self._orderbook_task_right:
                self._orderbook_task_right.cancel()
            self._orderbook_task_right = asyncio.get_event_loop().create_task(
                self._orderbook_update_loop(ex_name, native_symbol, direction)
            )

    async def _close_orderbook_panel(self, direction: str = "right"):
        """오더북 패널 닫기 + WS 구독 해제"""
        panel = self._get_panel_by_direction(direction)

        # 태스크 취소
        if direction == "left":
            if self._orderbook_task_left:
                self._orderbook_task_left.cancel()
                self._orderbook_task_left = None
        else:
            if self._orderbook_task_right:
                self._orderbook_task_right.cancel()
                self._orderbook_task_right = None

        # WS 구독 해제
        panel_exchange = self._get_panel_exchange(direction)
        panel_symbol = self._get_panel_symbol(direction)
        if panel_exchange:
            try:
                ex = self.mgr.get_exchange(panel_exchange)
                if ex and panel_symbol and hasattr(ex, "unsubscribe_orderbook"):
                    await ex.unsubscribe_orderbook(panel_symbol)
            except Exception as e:
                self._log(f"[ORDERBOOK] unsubscribe 실패: {e}")

        # 창 너비 축소 + Splitter 정리
        if panel.isVisible():
            sizes = self.center_splitter.sizes()
            idx = 0 if direction == "left" else 2

            # 현재 오더북 패널 너비 저장 (다음에 열 때 사용)
            if sizes[idx] > 0:
                if direction == "left":
                    self._orderbook_panel_width_left = sizes[idx]
                else:
                    self._orderbook_panel_width_right = sizes[idx]

            # Splitter 크기 조정
            if direction == "left":
                self.center_splitter.setSizes([0, sizes[1], sizes[2]])
            else:
                self.center_splitter.setSizes([sizes[0], sizes[1], 0])

            # 창 너비 축소
            panel_width = sizes[idx]

            # 왼쪽 축소 시 창 위치 이동
            if direction == "left":
                self.move(self.x() + panel_width, self.y())

            # 창 너비 축소
            self.resize(self.width() - panel_width, self.height())

        panel.setVisible(False)
        panel.clear()

        if direction == "left":
            self._orderbook_panel_exchange_left = None
            self._orderbook_panel_symbol_left = None
        else:
            self._orderbook_panel_exchange_right = None
            self._orderbook_panel_symbol_right = None

    async def _orderbook_update_loop(self, ex_name: str, symbol: str, direction: str = "right"):
        """오더북/오픈오더 주기적 업데이트"""
        error_count = 0
        max_errors = 5

        panel = self._get_panel_by_direction(direction)

        # 거래소별 오픈오더 조회 주기 설정
        meta = self.mgr.get_meta(ex_name)
        exchange_platform = meta.get("exchange", "hyperliquid") if meta else "hyperliquid"
        open_orders_interval = RATE["STATUS_OO_INTERVAL"].get(
            exchange_platform,
            RATE["STATUS_OO_INTERVAL"]["default"]
        )

        while not self._stopping:
            # 거래소가 변경되었거나 패널이 닫혔으면 종료
            if self._get_panel_exchange(direction) != ex_name:
                break
            if self._get_panel_symbol(direction) != symbol:
                break

            try:
                ex = self.mgr.get_exchange(ex_name)
                if not ex:
                    break

                now = time.time()
                ws_open_orders = _ws_supported(ex, "get_open_orders")
                force_update = ex_name in self._force_open_orders_update

                # 오더북 조회 (항상)
                if hasattr(ex, "get_orderbook"):
                    try:
                        orderbook = await ex.get_orderbook(symbol)
                        if orderbook and (orderbook.get("bids") or orderbook.get("asks")):
                            panel.update_orderbook(orderbook)
                            error_count = 0
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        error_count += 1
                        if error_count <= 2:
                            self._log(f"[ORDERBOOK] {ex_name} 오더북 조회 실패: {e}")

                # 오픈 오더 조회 (주기 제한 적용)
                last_open_orders_at = (
                    self._last_open_orders_at_left if direction == "left"
                    else self._last_open_orders_at_right
                )
                need_open_orders = ws_open_orders or force_update or (now - last_open_orders_at >= open_orders_interval)

                if need_open_orders and hasattr(ex, "get_open_orders"):
                    try:
                        open_orders = await ex.get_open_orders(symbol)
                        panel.update_open_orders(open_orders or [])
                        # 마지막 조회 시간 업데이트
                        if direction == "left":
                            self._last_open_orders_at_left = now
                        else:
                            self._last_open_orders_at_right = now
                        # force update 플래그 해제
                        self._force_open_orders_update.discard(ex_name)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if error_count <= 2:
                            self._log(f"[ORDERBOOK] {ex_name} 오픈오더 조회 실패: {e}")

                if error_count >= max_errors:
                    self._log(f"[ORDERBOOK] {ex_name} 연속 에러 {error_count}회, 5초 대기")
                    await asyncio.sleep(5.0)
                    error_count = 0
                    continue

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log(f"[ORDERBOOK] {ex_name} 업데이트 실패: {e}")

            await asyncio.sleep(RATE["GAP_FOR_ORDERBOOK"])

    async def _do_cancel_all_orders(self, direction: str = "right"):
        """오픈 오더 전체 취소"""
        ex_name = self._get_panel_exchange(direction)
        if not ex_name:
            return

        ex = self.mgr.get_exchange(ex_name)
        if not ex:
            self._log(f"[{ex_name}] 거래소 없음")
            return

        symbol = self._get_panel_symbol(direction)
        if not symbol:
            self._log(f"[{ex_name}] 심볼 없음")
            return

        try:
            if hasattr(ex, "cancel_orders"):
                open_orders = []
                if hasattr(ex, "get_open_orders"):
                    open_orders = await ex.get_open_orders(symbol)

                if not open_orders:
                    self._log(f"[{ex_name}] 취소할 주문 없음")
                    return

                await ex.cancel_orders(symbol, open_orders)
                self._log(f"[{ex_name}] {len(open_orders)}개 주문 취소 완료")
                self._force_open_orders_update.add(ex_name)  # 오픈오더만
            else:
                self._log(f"[{ex_name}] cancel_orders 미지원")
        except Exception as e:
            self._log(f"[{ex_name}] 주문 취소 실패: {e}")

    async def shutdown(self):
        self._stopping = True
        if self._console_redirect_installed:
            sys.stdout = self._stdout_orig
            sys.stderr = self._stderr_orig
        # Cancel tasks...
        if self._price_task: self._price_task.cancel()
        if self._status_task: self._status_task.cancel()
        # 오더북 패널 정리 (왼쪽/오른쪽 모두)
        if self._orderbook_task_left: self._orderbook_task_left.cancel()
        if self._orderbook_task_right: self._orderbook_task_right.cancel()
        if self._orderbook_panel_exchange_left:
            try:
                await self._close_orderbook_panel("left")
            except:
                pass
        if self._orderbook_panel_exchange_right:
            try:
                await self._close_orderbook_panel("right")
            except:
                pass
        await self.mgr.close_all()
        self._shutdown_done = True

    def closeEvent(self, e):
        if getattr(self, "_shutdown_done", False):
            # shutdown 완료 후 실제 종료
            e.accept()
        else:
            # shutdown 먼저 실행, 완료 후 다시 close 호출
            e.ignore()
            asyncio.get_event_loop().create_task(self._shutdown_and_close())

    async def _shutdown_and_close(self):
        """shutdown 완료 후 창 닫기"""
        await self.shutdown()
        self.close()  # _shutdown_done=True 상태로 다시 closeEvent 호출

def run_qt_app(mgr):
    #set_ui_type("qt")
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