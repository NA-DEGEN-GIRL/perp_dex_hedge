import os
import logging
import argparse

from core import ExchangeManager
from ui_urwid import UrwidApp  # urwid UI도 선택 가능하게 추가

def _setup_logging():
    # 파일 핸들러만 사용, 기존 핸들러 싹 정리
    log_level = os.getenv("PDEX_LOG_LEVEL", "INFO").upper()
    log_to_console = os.getenv("PDEX_LOG_CONSOLE", "0") == "1"  # 필요할 때만 콘솔 출력

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    fh = logging.FileHandler("debug.log", mode="a", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    if log_to_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    # 서드파티/비동기 로거 소음 억제
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # 필요 시 ccxt 로깅도 낮춰줍니다
    logging.getLogger("ccxt").setLevel(logging.ERROR)

def main():
    _setup_logging()
    logging.info("Application starting...")

    manager = ExchangeManager()

    try:
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