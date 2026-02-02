# PySide6 Dark Theme System

PySide6(Qt6) 기반 다크 테마 구현 가이드. 다른 프로젝트에서 재사용 가능.

## 환경 변수

```python
import os

# 테마 선택: "dark" (기본값) 또는 다른 값 (라이트 테마)
UI_THEME = os.getenv("PDEX_UI_THEME", "dark").lower()

# 폰트 설정
UI_FONT_FAMILY = os.getenv("PDEX_UI_FONT_FAMILY", "")  # 빈 문자열이면 시스템 기본
UI_FONT_SIZE = int(os.getenv("PDEX_UI_FONT_SIZE", "16"))

# 창 크기
UI_WINDOW_WIDTH = int(os.getenv("PDEX_UI_WIDTH", "1400"))
UI_WINDOW_HEIGHT = int(os.getenv("PDEX_UI_HEIGHT", "1600"))

# 모니터 선택: "cursor" (마우스 위치) 또는 "primary" (메인 모니터)
UI_MONITOR = os.getenv("PDEX_UI_MONITOR", "cursor").lower()

# 이모지 사용 여부 (일부 시스템에서 깨짐 방지)
USE_EMOJI = os.getenv("PDEX_UI_USE_EMOJI", "0") == "1"
```

---

## 색상 팔레트

### 기본 색상 상수

```python
# 텍스트 색상
CLR_TEXT = "#e0e0e0"           # 기본 텍스트 (밝은 회색)
CLR_MUTED = "#888888"          # 보조/비활성 텍스트 (중간 회색)
CLR_ACCENT = "#4fc3f7"         # 강조 색상 (하늘색)

# 상태 색상
CLR_LONG = "#81c784"           # 롱/상승 (녹색 계열)
CLR_SHORT = "#ef9a9a"          # 숏/하락 (빨간색 계열)
CLR_NEUTRAL = "#e0e0e0"        # 중립 (기본 텍스트와 동일)

# 특수 색상
CLR_COLLATERAL = "rgba(139, 125, 77, 1)"  # 잔고/담보 (골드 계열)
CLR_DANGER = "#ef5350"         # 위험/삭제 (빨간색)
CLR_INFO = "#90caf9"           # 정보 (파란색)
CLR_DETAIL = "#ce93d8"         # 상세/보조 (보라색)
```

### 그룹별 색상 (6개 그룹)

```python
GROUP_COLORS = {
    0: {"bg": "#1b5e20", "border": "#81c784", "text": "#81c784"},  # 녹색
    1: {"bg": "#0d47a1", "border": "#64b5f6", "text": "#64b5f6"},  # 파랑
    2: {"bg": "#e65100", "border": "#ffb74d", "text": "#ffb74d"},  # 주황
    3: {"bg": "#6a1b9a", "border": "#ce93d8", "text": "#ce93d8"},  # 보라
    4: {"bg": "#00838f", "border": "#4dd0e1", "text": "#4dd0e1"},  # 청록
    5: {"bg": "#c62828", "border": "#ef9a9a", "text": "#ef9a9a"},  # 빨강
}
```

### 배경색 시스템

```python
# 배경색 레이어 (어두운 순)
BG_DARKEST = "#1e1e1e"     # 가장 어두움 (에디터, 입력 필드 내부)
BG_DARKER = "#232323"      # 약간 밝음 (입력 필드 배경)
BG_BASE = "#2b2b2b"        # 기본 배경 (입력 요소)
BG_WINDOW = "#353535"      # 윈도우 배경 (RGB: 53, 53, 53)
BG_HOVER = "#3a3a3a"       # 버튼 기본 / 호버 전
BG_ACTIVE = "#4a4a4a"      # 호버 상태
BG_PRESSED = "#2a2a2a"     # 눌림 상태
BG_DISABLED = "#2a2a2a"    # 비활성화 상태

# 테두리 색상
BORDER_DEFAULT = "#555555"
BORDER_HOVER = "#666666"
BORDER_DISABLED = "#333333"
```

---

## QPalette 설정 (다크 테마)

```python
from PySide6 import QtCore, QtGui, QtWidgets

def apply_dark_palette(app: QtWidgets.QApplication) -> None:
    """다크 테마 팔레트 적용"""
    palette = QtGui.QPalette()

    # 윈도우/배경
    palette.setColor(QtGui.QPalette.Window, QtGui.QColor(53, 53, 53))
    palette.setColor(QtGui.QPalette.Base, QtGui.QColor(35, 35, 35))
    palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(53, 53, 53))

    # 텍스트
    palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.BrightText, QtCore.Qt.red)
    palette.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor(160, 160, 160))

    # 버튼
    palette.setColor(QtGui.QPalette.Button, QtGui.QColor(53, 53, 53))

    # 툴팁
    palette.setColor(QtGui.QPalette.ToolTipBase, QtCore.Qt.white)
    palette.setColor(QtGui.QPalette.ToolTipText, QtCore.Qt.white)

    # 선택/하이라이트
    palette.setColor(QtGui.QPalette.Link, QtGui.QColor(42, 130, 218))
    palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(42, 130, 218))
    palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.black)

    app.setPalette(palette)
```

---

## 글로벌 스타일시트

```python
def get_global_stylesheet(font_size: int = 16, font_family: str = "") -> str:
    """전역 스타일시트 생성"""
    log_font_size = max(font_size - 1, 9)

    # 폰트 fallback 체인 (한글 + 이모지 지원)
    font_families = []
    if font_family:
        font_families.append(font_family)
    font_families += [
        "Noto Sans CJK KR",      # 한글 (Linux)
        "Malgun Gothic",          # 한글 (Windows)
        "Segoe UI",               # 영문 (Windows)
        "Noto Color Emoji",       # 이모지 (Linux)
        "Segoe UI Emoji",         # 이모지 (Windows)
        "Apple Color Emoji",      # 이모지 (macOS)
        "Sans"                    # Fallback
    ]
    css_fonts = ", ".join(f'"{f}"' for f in font_families)

    return f"""
    /* 전역 기본 */
    QWidget {{
        font-size: {font_size}pt;
        font-family: {css_fonts};
    }}

    /* 그룹 박스 */
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

    /* 버튼 */
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

    /* 입력 필드 */
    QLineEdit {{
        padding: 4px;
        border: 1px solid #555;
        border-radius: 3px;
        background-color: #2b2b2b;
        color: white;
    }}

    /* 콤보박스 */
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
    QComboBox QAbstractItemView {{
        border: 1px solid #555;
        background-color: #2b2b2b;
        color: white;
        selection-background-color: #1976d2;
        outline: none;
        padding: 4px;
    }}

    /* 텍스트 에디터 */
    QPlainTextEdit {{
        font-family: {css_fonts};
        font-size: {log_font_size}pt;
        background-color: #1e1e1e;
        border: 1px solid #555;
    }}

    /* 스크롤바 */
    QScrollBar:vertical {{
        width: 12px;
        background: #2b2b2b;
    }}
    QScrollBar::handle:vertical {{
        background: #555;
        border-radius: 4px;
    }}
    """
```

---

## 버튼 스타일 템플릿

### 기본 버튼

```python
BTN_BASE = """
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
    QPushButton:disabled {
        background-color: #2a2a2a;
        color: #555;
        border-color: #333;
    }
"""
```

### 위험/삭제 버튼

```python
BTN_DANGER = """
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
```

### 롱(매수) 버튼

```python
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
    QPushButton:checked {
        border: 2px solid #81c784;
        background-color: #2e3d2e;
    }
    QPushButton:disabled {
        background-color: #2a2a2a;
        color: #555;
        border-color: #333;
    }
"""
```

### 숏(매도) 버튼

```python
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
    QPushButton:checked {
        border: 2px solid #ef9a9a;
        background-color: #3d2e2e;
    }
    QPushButton:disabled {
        background-color: #2a2a2a;
        color: #555;
        border-color: #333;
    }
"""
```

### 토글 버튼 (Order Type 등)

```python
BTN_TOGGLE = """
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
```

### 그룹 선택 버튼 (동적 색상)

```python
def get_group_button_style(group: int, is_small: bool = False) -> str:
    """그룹 번호에 따른 버튼 스타일 생성"""
    colors = GROUP_COLORS.get(group, GROUP_COLORS[0])

    padding = "2px 6px" if is_small else "4px 8px"
    min_width = "20px" if is_small else "24px"
    font_size = "10pt" if is_small else "inherit"
    border_width = "1px" if is_small else "2px"

    return f"""
        QPushButton {{
            background-color: #3a3a3a;
            color: #e0e0e0;
            border: 1px solid #555;
            border-radius: 3px;
            padding: {padding};
            min-width: {min_width};
            font-size: {font_size};
        }}
        QPushButton:hover {{
            background-color: #4a4a4a;
            border-color: {colors['border']};
        }}
        QPushButton:checked {{
            background-color: {colors['bg']};
            border: {border_width} solid {colors['border']};
            color: {colors['text']};
        }}
    """
```

---

## 전체 적용 예시

```python
from PySide6 import QtWidgets, QtGui, QtCore
import sys

def apply_app_style(app: QtWidgets.QApplication) -> None:
    """앱에 다크 테마 적용"""
    # 1. Fusion 스타일 사용 (크로스 플랫폼 일관성)
    app.setStyle("Fusion")

    # 2. 폰트 설정
    font = app.font()
    if UI_FONT_FAMILY:
        font.setFamily(UI_FONT_FAMILY)
    if UI_FONT_SIZE > 0:
        font.setPointSize(UI_FONT_SIZE)
    app.setFont(font)

    # 3. 다크 팔레트 적용
    if UI_THEME == "dark":
        apply_dark_palette(app)

    # 4. 스타일시트 적용
    app.setStyleSheet(get_global_stylesheet(UI_FONT_SIZE, UI_FONT_FAMILY))


def main():
    app = QtWidgets.QApplication(sys.argv)
    apply_app_style(app)

    # 창 생성 및 표시
    window = QtWidgets.QMainWindow()
    window.setWindowTitle("Dark Theme Example")
    window.resize(UI_WINDOW_WIDTH, UI_WINDOW_HEIGHT)

    # 모니터 선택 로직
    if UI_MONITOR == "primary":
        screen = app.primaryScreen()
    else:
        cursor_pos = QtGui.QCursor.pos()
        screen = None
        for s in app.screens():
            if s.geometry().contains(cursor_pos):
                screen = s
                break
        if screen is None:
            screen = app.primaryScreen()

    if screen:
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - window.width()) // 2
        y = geo.y() + (geo.height() - window.height()) // 2
        window.move(x, y)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

---

## QPlainTextEdit 메모리 관리

장시간 실행 시 메모리 누수 방지:

```python
# 최대 라인 수 제한 (오래된 로그 자동 삭제)
log_edit = QtWidgets.QPlainTextEdit()
log_edit.setReadOnly(True)
log_edit.setMaximumBlockCount(5000)  # 5000줄 제한

console_edit = QtWidgets.QPlainTextEdit()
console_edit.setReadOnly(True)
console_edit.setMaximumBlockCount(3000)  # 3000줄 제한
```

---

## 테이블 스타일

```python
TABLE_STYLE = """
    QTableWidget {
        background-color: #2b2b2b;
        border: 1px solid #555;
        gridline-color: #444;
        color: white;
    }
    QTableWidget::item {
        padding: 4px;
    }
    QTableWidget::item:selected {
        background-color: #1976d2;
    }
    QHeaderView::section {
        background-color: #3a3a3a;
        color: #e0e0e0;
        padding: 4px;
        border: 1px solid #555;
    }
"""
```

---

## 색상 참조 (Material Design 기반)

| 용도 | 색상 코드 | 설명 |
|------|-----------|------|
| 녹색 (Long) | `#81c784` | Material Green 300 |
| 빨강 (Short) | `#ef9a9a` | Material Red 200 |
| 파랑 (Info) | `#90caf9` | Material Blue 200 |
| 하늘 (Accent) | `#4fc3f7` | Material Light Blue 300 |
| 주황 (Warning) | `#ffb74d` | Material Orange 300 |
| 보라 (Detail) | `#ce93d8` | Material Purple 200 |
| 청록 (Teal) | `#4dd0e1` | Material Cyan 300 |
| 골드 (Gold) | `rgba(139, 125, 77, 1)` | 커스텀 |
