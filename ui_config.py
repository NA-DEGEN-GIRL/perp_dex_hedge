# ui_config.py
UI_TYPE = "urwid"  # 기본값

def set_ui_type(ui_type: str):
    global UI_TYPE
    UI_TYPE = ui_type

def is_qt_ui() -> bool:
    return UI_TYPE == "qt"

def ui_print(*args, **kwargs):
    """Qt UI일 때만 print (콘솔 박스에 표시)"""
    if is_qt_ui():
        print(*args, **kwargs)