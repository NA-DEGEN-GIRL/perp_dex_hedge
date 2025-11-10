# core.py
import os
import asyncio
import configparser
import logging

import ccxt.async_support as ccxt
from dotenv import load_dotenv

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
            if not hl or not builder_code or not wallet_address:
                self.exchanges[exchange_name] = None
                continue

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

    async def initialize_all(self):
        # 각 거래소의 initialize_client()를 병렬로 1회 호출
        tasks = []
        for ex in self.exchanges.values():
            if ex:
                tasks.append(ex.initialize_client())
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logging.warning(f"initialize_all error: {e}")

    async def close_all(self):
        tasks = [ex.close() for ex in self.exchanges.values() if ex]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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