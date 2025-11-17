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
    logging.warning("[mpdex] exchange_factory.create_exchange 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")

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

            # 하이퍼리퀴드 엔진 거래소만 현재 인스턴스 생성 (hl=True + 키/설정 유효)
            if hl:
                builder_code = config.get(exchange_name, "builder_code", fallback=None)
                wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")
                
                if wallet_address:
                    fee_int = int(config.get(exchange_name, "fee_rate", fallback="0") or 0)
                    dex_fee_map = {}
                    for k, v in config.items(exchange_name):
                        if k.endswith("_fee_rate"):
                            # 예: 'xyz_fee_rate' → 'xyz'
                            dex = k[:-len("_fee_rate")].lower()
                            try:
                                dex_fee_map[dex] = int(v)
                            except Exception:
                                pass

                    self.exchanges[exchange_name] = ccxt.hyperliquid(
                        {
                            "apiKey": os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY"),
                            "privateKey": os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY"),
                            "walletAddress": wallet_address,
                            "options": {
                                "feeInt": fee_int,
                                "dexFeeInt": dex_fee_map,
                                # builder는 있을 때만 주입
                                **({"builder": builder_code} if builder_code else {}),
                                #"builderFee": True,
                                #"approvedBuilderFee": True,
                            },
                        }
                    )
                    # 주소 options로 복제
                    try:
                        self.exchanges[exchange_name].options["walletAddress"] = wallet_address
                    except Exception:
                        pass
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

        non_hl = [n for n in EXCHANGES if not self.meta.get(n, {}).get("hl", False)]
        if create_exchange is None and non_hl:
            logging.warning("[mpdex] 미설치/경로 오류로 비-HL 생성 스킵: %s", ",".join(non_hl))
        for name in non_hl:
            if self.exchanges.get(name):
                continue
            if create_exchange is None:
                continue
            try:
                key = self._build_mpdex_key(name)
                if key is None:
                    logging.warning(f"[{name}] .env 키가 누락되어 생성 스킵")
                    continue
                client = await create_exchange(name.lower(), key)
                self.exchanges[name] = client
                logging.info(f"[{name}] mpdex client created")
            except Exception as e:
                logging.warning(f"[{name}] mpdex client create failed: {e}")
                self.exchanges[name] = None

        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logging.warning(f"initialize_all error: {e}")

    def _build_mpdex_key(self, name: str) -> SimpleNamespace | None:
        """mpdex 각 거래소별 키를 .env에서 읽어 SimpleNamespace로 생성"""
        u = name.upper()
        try:
            if name.lower() == "lighter":
                return SimpleNamespace(
                    account_id=int(os.getenv("LIGHTER_ACCOUNT_ID")),
                    private_key=os.getenv("LIGHTER_PRIVATE_KEY"),
                    api_key_id=int(os.getenv("LIGHTER_API_KEY_ID")),
                    l1_address=os.getenv("LIGHTER_L1_ADDRESS"),
                )
            if name.lower() == "paradex":
                return SimpleNamespace(
                    wallet_address=os.getenv("PARADEX_L1_ADDRESS"),
                    paradex_address=os.getenv("PARADEX_ADDRESS"),
                    paradex_private_key=os.getenv("PARADEX_PRIVATE_KEY"),
                )
            if name.lower() == "edgex":
                return SimpleNamespace(
                    account_id=int(os.getenv("EDGEX_ACCOUNT_ID")),
                    private_key=os.getenv("EDGEX_PRIVATE_KEY"),
                )
            if name.lower() == "grvt":
                return SimpleNamespace(
                    api_key=os.getenv("GRVT_API_KEY"),
                    account_id=int(os.getenv("GRVT_ACCOUNT_ID")),
                    secret_key=os.getenv("GRVT_SECRET_KEY"),
                )
            if name.lower() == "backpack":
                return SimpleNamespace(
                    api_key=os.getenv("BACKPACK_API_KEY"),
                    secret_key=os.getenv("BACKPACK_SECRET_KEY"),
                )
        except Exception as e:
            logging.warning(f"[{name}] env key parse failed: {e}")
            return None
        return None

    async def close_all(self):
        # ccxt/mpex 모두 close() 지원
        close_tasks = []
        for ex in self.exchanges.values():
            if ex and hasattr(ex, "close"):
                try:
                    close_tasks.append(ex.close())
                except Exception:
                    pass
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