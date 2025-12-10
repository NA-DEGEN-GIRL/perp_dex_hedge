import os
import logging
from logging.handlers import RotatingFileHandler
from core import ExchangeManager
#from ui_urwid import UrwidApp
from dotenv import load_dotenv
from pathlib import Path
import sys
import argparse

def _load_env_flexible():
    """
    .env를 아래 우선순위로 1회 로드:
    현재 작업 디렉터리(CWD)/.env
    """
    tried = []
    # CWD
    p = (Path.cwd() / ".env").resolve()
    tried.append(str(p))
    if p.exists():
        load_dotenv(p, override=False)
        return str(p)
    
    return None  # 못 찾았음
    
def _setup_logging():
    # 1) 환경 변수 읽기
    level_name = os.getenv("PDEX_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = os.path.abspath(os.getenv("PDEX_LOG_FILE", "debug.log"))
    to_console = os.getenv("PDEX_LOG_CONSOLE", "0") == "1"

    # 2) 강제 재설정(force): 기존 basicConfig/핸들러 덮어쓰기
    #    (파이썬 3.8+)
    fmt = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    handlers = [RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=3, encoding="utf-8")]
    if to_console:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=handlers,
        force=True,   # ← 핵심: 기존 설정/핸들러를 모두 무시하고 재설정
    )

    # 3) 핸들러 레벨은 NOTSET로(루트 레벨만 따르게)
    root = logging.getLogger()
    for h in root.handlers:
        h.setLevel(logging.NOTSET)

    # 4) 소음 로거 억제(원하면 환경변수로)
    for name in [s.strip() for s in os.getenv("PDEX_LOG_SUPPRESS", "asyncio,urllib3").split(",") if s.strip()]:
        logging.getLogger(name).setLevel(logging.CRITICAL)

    # 5) 모듈별 레벨(옵션)
    mapping_env = os.getenv("PDEX_MODULE_LEVELS", "")
    if mapping_env:
        for token in mapping_env.split(","):
            token = token.strip()
            if "=" not in token:
                continue
            mod, lev = token.split("=", 1)
            try:
                logging.getLogger(mod.strip()).setLevel(getattr(logging, lev.strip().upper()))
            except Exception:
                pass

    logging.info("Logging initialized: level=%s file=%s console=%s", level_name, log_file, to_console)

def _dump_logging_state():
    lg = logging.getLogger(__name__)
    root = logging.getLogger()
    L = logging.getLevelName
    lg.info("[LOG] root level=%s handlers=%d", L(root.level), len(root.handlers))
    for i, h in enumerate(root.handlers, 1):
        lg.info("[LOG] handler[%d]=%s level=%s target=%s",
                i, h.__class__.__name__, L(getattr(h, "level", logging.NOTSET)),
                getattr(h, "baseFilename", None) or "stream")
    # 관심 모듈들 확인(패키지 경로/짧은 이름 모두 찍어보면 좋습니다)
    for name in ("trading_service", "perp_dex_hedge.trading_service"):
        t = logging.getLogger(name)
        lg.info("[LOG] logger %-32s effective=%s setLevel=%s propagate=%s",
                name, L(t.getEffectiveLevel()), L(t.level), t.propagate)

# (옵션) 이후 basicConfig를 다른 모듈이 또 호출하는 것을 무력화
def _guard_basicConfig(enable: bool = True):
    import logging as _logging
    if not enable:
        return
    _orig = _logging.basicConfig
    def _noop(*args, **kwargs):
        return  # 앞으로의 basicConfig 호출을 전부 무시
    _logging.basicConfig = _noop

def _parse_args():  # [ADD]
    parser = argparse.ArgumentParser(description="Perp DEX Hedge")
    parser.add_argument(
        "--ui", "-u",
        choices=["urwid", "qt"],
        default=os.getenv("PDEX_UI", "urwid"),
        help="UI 선택: urwid(기본) 또는 qt(PySide6)"
    )
    return parser.parse_args()

def main():
    args = _parse_args()
    _load_env_flexible()

    _setup_logging()     # 강제 재설정
    _guard_basicConfig(enable=True)  # (옵션) 이후 타 모듈의 basicConfig 무력화
    _dump_logging_state()            # 상태 확인 1회
    logging.info("Application starting...")

    manager = ExchangeManager()

    try:
        if args.ui == "urwid":
            # [ADD] 지연 임포트
            from ui_urwid import UrwidApp
            app = UrwidApp(manager)
            app.run()
        else:
            # [ADD] PySide6 GUI 실행
            try:
                from ui_qt import run_qt_app
            except ImportError as e:
                logging.critical("PySide6(UI=qt) 미설치 또는 ui_qt 모듈 누락: %s", e)
                print("PySide6가 설치되어 있지 않습니다. 아래를 실행해 설치하세요:\n  pip install PySide6")
                sys.exit(2)
            run_qt_app(manager)

    except KeyboardInterrupt:
        pass
    except Exception:
        logging.critical("CRITICAL APP ERROR", exc_info=True)
    finally:
        logging.info("Application finished.")

if __name__ == "__main__":
    main()