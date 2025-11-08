import os
import logging
import argparse

from core import ExchangeManager
from ui_textual import KimbapHeaven
from ui_urwid import UrwidApp  # urwid UI도 선택 가능하게 추가

# 기본 UI를 손쉽게 바꾸고 싶다면, 아래 환경변수로도 제어할 수 있습니다.
#   export PDEX_UI_DEFAULT=urwid   # Linux/macOS/WSL
#   set PDEX_UI_DEFAULT=urwid      # Windows PowerShell
DEFAULT_UI = os.getenv("PDEX_UI_DEFAULT", "textual")  # textual / urwid

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        filename="debug.log",
        filemode="w",
    )
    logging.info("Application starting...")

    parser = argparse.ArgumentParser(description="Hyperliquid Multi-DEX Trader")
    parser.add_argument(
        "--ui",
        choices=["textual", "urwid"],
        default=DEFAULT_UI,
        help=f"사용할 UI 프레임워크 선택 (기본: {DEFAULT_UI})",
    )
    args = parser.parse_args()

    manager = ExchangeManager()

    try:
        if args.ui == "textual":
            # Textual: 기존과 동일하게 실행
            app = KimbapHeaven(manager=manager)
            app.run()
        else:
            # urwid: 별도 UI 실행
            app = UrwidApp(manager)
            app.run()

    except KeyboardInterrupt:
        pass
    except Exception:
        logging.critical("CRITICAL APP ERROR", exc_info=True)
    finally:
        logging.info("Application finished.")

if __name__ == "__main__":
    main()