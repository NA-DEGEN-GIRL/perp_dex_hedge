# core.py
import os
import asyncio
import configparser
import logging
from types import SimpleNamespace
import ccxt.async_support as ccxt
from dotenv import load_dotenv
try:
    from exchange_factory import create_exchange  # mpdex 팩토리
except Exception:
    create_exchange = None
    logging.warning("[lighter] mpdex(exchange_factory) 를 찾지 못했습니다. lighter 는 비활성화됩니다.")

# --- 설정 로드 ---
load_dotenv()
config = configparser.ConfigParser(interpolation=None)
def load_config_with_encodings(path: str) -> configparser.ConfigParser:
    """
    config.ini를 여러 인코딩으로 안전하게 로드합니다.
    우선순위: UTF-8 → UTF-8-SIG → CP949 → EUC-KR → MBCS(Windows 기본).
    """
    encodings = ("utf-8", "utf-8-sig", "cp949", "euc-kr", "mbcs")
    last_err = None
    cfg = configparser.ConfigParser(interpolation=None)

    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                cfg.read_file(f)
            logging.info(f"[config] loaded '{path}' with encoding='{enc}'")
            return cfg
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except FileNotFoundError:
            logging.critical(f"[config] file not found: {path}")
            raise
        except Exception as e:
            # 예기치 못한 에러는 바로 올립니다(잘못된 INI 문법 등)
            logging.exception(f"[config] load error with encoding='{enc}': {e}")
            raise

    # 모든 인코딩 시도 실패
    if last_err:
        logging.critical(f"[config] failed to decode '{path}' with tried encodings {encodings}")
        raise last_err
    else:
        # 이론상 도달하지 않지만 안전상
        raise RuntimeError(f"[config] unknown error while reading '{path}'")

# config.ini 경로(실행 위치와 무관하게 파일 위치 기준)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")
config = load_config_with_encodings(CONFIG_PATH)

EXCHANGES = sorted([section for section in config.sections()])


class ExchangeManager:
    """
    - exchanges[name] : ccxt 인스턴스 또는 None
    - meta[name]      : {'show': bool, 'hl': bool}
    - visible_names() : show=True 인 거래소 목록
    - first_hl_exchange(): hl=True 이면서 설정/연결된 첫 거래소 인스턴스
    """
    def __init__(self):
        self.exchanges = {}
        self.meta = {}
        for exchange_name in EXCHANGES:
            show = config.get(exchange_name, "show", fallback="True").strip().lower() == "true"
            hl = config.get(exchange_name, "hl", fallback="True").strip().lower() == "true"
            # FrontendMarket 플래그 로딩
            fm_raw = config.get(exchange_name, "FrontendMarket", fallback="False")
            frontend_market = (fm_raw or "").strip().lower() == "true"

            self.meta[exchange_name] = {"show": show, "hl": hl, "frontend_market": frontend_market}

            builder_code = config.get(exchange_name, "builder_code", fallback=None)
            wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")

            # 하이퍼리퀴드 엔진 거래소만 현재 인스턴스 생성 (hl=True + 키/설정 유효)
            if hl and builder_code and wallet_address:
                # feeInt(정수)만 사용
                fee_int = int(config.get(exchange_name, "fee_rate", fallback="0") or 0)

                self.exchanges[exchange_name] = ccxt.hyperliquid(
                    {
                        "apiKey": os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY"),
                        "privateKey": os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY"),
                        "walletAddress": wallet_address,
                        "options": {
                            "builder": builder_code,
                            "feeInt": fee_int,
                            "builderFee": True,
                            "approvedBuilderFee": True,
                        },
                    }
                )
            else:
                # non-HL(lighter 등)은 initialize_all에서 생성
                self.exchanges[exchange_name] = None

    async def initialize_all(self):
        # 각 거래소의 initialize_client()를 병렬로 1회 호출
        tasks = []
        # 1) HL 쪽 initialize_client
        for name, ex in self.exchanges.items():
            if ex and self.meta.get(name, {}).get("hl", False):
                tasks.append(ex.initialize_client())

        # 2) mpdx 생성
        for name in EXCHANGES:
            meta = self.meta.get(name, {})
            if meta.get("hl", False):
                continue
            # lighter만 처리 (필요 시 다른 non-HL도 유사하게 추가)
            if name.lower() == "lighter" and self.exchanges.get(name) is None:
                if create_exchange is None:
                    logging.warning("[lighter] exchange_factory.create_exchange 를 찾을 수 없습니다. mpdex 설치/경로 확인")
                    continue
                # .env에서 키 읽기
                try:
                    acc_id = int(os.getenv("LIGHTER_ACCOUNT_ID"))
                    pk = os.getenv("LIGHTER_PRIVATE_KEY")
                    api_key_id = int(os.getenv("LIGHTER_API_KEY_ID"))
                    l1_addr = os.getenv("LIGHTER_L1_ADDRESS")
                    key = SimpleNamespace(
                        account_id=acc_id,
                        private_key=pk,
                        api_key_id=api_key_id,
                        l1_address=l1_addr,
                    )
                    # 비동기 생성
                    lighter = await create_exchange("lighter", key)
                    self.exchanges[name] = lighter
                    logging.info("[lighter] client created")
                except Exception as e:
                    logging.warning(f"[lighter] client create failed: {e}")
                    self.exchanges[name] = None

        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logging.warning(f"initialize_all error: {e}")

    async def close_all(self):
        # ccxt/mpex 모두 close() 지원
        close_tasks = []
        for ex in self.exchanges.values():
            if ex and hasattr(ex, "close"):
                close_tasks.append(ex.close())
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

    def get_exchange(self, name: str):
        return self.exchanges.get(name)

    def get_meta(self, name: str):
        return self.meta.get(name, {"show": False, "hl": False, "frontend_market": False})

    def visible_names(self):
        return [n for n in EXCHANGES if self.meta.get(n, {}).get("show", False)]

    def all_names(self):
        return list(EXCHANGES)

    def first_hl_exchange(self):
        """hl=True 이고 설정된 첫 ccxt 인스턴스 반환"""
        for n in EXCHANGES:
            m = self.meta.get(n, {})
            if m.get("hl", False) and self.exchanges.get(n):
                return self.exchanges[n]
        return None