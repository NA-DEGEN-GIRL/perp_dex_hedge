# core.py
import os
import asyncio
import configparser
from types import SimpleNamespace
import logging
logger = logging.getLogger(__name__)  # 모듈 전용 로거

try:
    from exchange_factory import create_exchange  # mpdex 팩토리
except Exception:
    create_exchange = None
    logger.warning("[mpdex] exchange_factory.create_exchange 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")

# --- 설정 로드 ---
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
            logger.info(f"[config] loaded '{path}' with encoding='{enc}'")
            return cfg
        except UnicodeDecodeError as e:
            last_err = e
            continue
        except FileNotFoundError:
            logger.critical(f"[config] file not found: {path}")
            raise
        except Exception as e:
            # 예기치 못한 에러는 바로 올립니다(잘못된 INI 문법 등)
            logger.exception(f"[config] load error with encoding='{enc}': {e}")
            raise

    # 모든 인코딩 시도 실패
    if last_err:
        logger.critical(f"[config] failed to decode '{path}' with tried encodings {encodings}")
        raise last_err
    else:
        # 이론상 도달하지 않지만 안전상
        raise RuntimeError(f"[config] unknown error while reading '{path}'")

# config.ini 경로(실행 위치와 무관하게 파일 위치 기준)
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.ini")
config = load_config_with_encodings(CONFIG_PATH)

EXCHANGES = sorted([section for section in config.sections()])

# [ADD] 공통 유틸: "a b" / "a" 형태를 (limit, market) 튜플로 파싱
def _parse_fee_pair(raw: str | tuple | list | None) -> tuple[int, int]:
    """
    "20 25" -> (20, 25), "20" -> (20, 20)
    "20/25" "20,25" "20|25" 도 허용
    tuple/list 입력도 허용: (20,25) -> (20,25), [20,25] -> (20,25)
    """
    if raw is None:
        return (0, 0)
    # tuple/list 면 그대로 정수 변환해서 반환
    if isinstance(raw, (tuple, list)):
        try:
            if len(raw) == 1:
                v = int(float(raw[0]))
                return (v, v)
            a = int(float(raw[0])); b = int(float(raw[1]))
            return (a, b)
        except Exception:
            return (0, 0)

    s = str(raw).strip()
    if not s:
        return (0, 0)
    # 구분자 통일
    for sep in [",", "/", "|"]:
        s = s.replace(sep, " ")
    toks = [t for t in s.split() if t]
    if len(toks) == 1:
        try:
            v = int(float(toks[0])); return (v, v)
        except Exception:
            return (0, 0)
    try:
        a = int(float(toks[0])); b = int(float(toks[1]))
        return (a, b)
    except Exception:
        return (0, 0)

class Hyperliquid:
    """
    trading_service가 기대하는 최소 표면:
    - .options: dict (walletAddress/privateKey/apiKey 등)
    - async initialize_client(): no-op
    - async close(): no-op
    - (선택) sign_l1_action(): 미구현이어도 trading_service가 fallback 서명 사용
    """
    def __init__(self, options: dict):
        inner = dict(options.get("options", {}) or {})
        for k in ("walletAddress", "apiKey", "privateKey"):
            v = options.get(k)
            if v is not None:
                inner[k] = v
        self.options = inner
        # 호환 편의: walletAddress 속성을 바로 노출
        self.walletAddress = inner.get("walletAddress")

    async def initialize_client(self):  # ccxt 대체 no-op
        return

    async def close(self):
        return
    
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
            
            exchange_platform = config.get(exchange_name, "exchange", fallback='hyperliquid')
            hl = (exchange_platform == 'hyperliquid')

            # FrontendMarket 플래그 로딩
            fm_raw = config.get(exchange_name, "FrontendMarket", fallback="False")
            frontend_market = (fm_raw or "").strip().lower() == "true"
            self.meta[exchange_name] = {"show": show, "hl": hl, "frontend_market": frontend_market, "exchange": exchange_platform}

            # hl, but superstack needs its own signature method due to intrinsic wallet provider service
            if hl or exchange_platform == 'superstack':
                builder_code = config.get(exchange_name, "builder_code", fallback=None)
                wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")
                
                if wallet_address:
                    # [CHG] 수수료: (limit, market) 페어 + DEX별 페어 맵 구성
                    if config.has_option(exchange_name, "fee_rate"):
                        fee_pair = _parse_fee_pair(config.get(exchange_name, "fee_rate"))
                    else:
                        fee_pair = "0,0"
                        
                    if config.has_option(exchange_name, "dex_fee_rate"):
                        dex_fee_pair_default = _parse_fee_pair(config.get(exchange_name, "dex_fee_rate"))
                    else:
                        dex_fee_pair_default = None  # comment: 없을 때는 None → DEX 주문에서 fee_rate로 폴백

                    dex_fee_pair_map = {}
                    for k, v in config.items(exchange_name):
                        if not k.endswith("_fee_rate"):
                            continue
                        k_l = k.lower()
                        if k_l == "dex_fee_rate" or k_l == "fee_rate":
                            # 공통 키는 개별 dex 맵에 넣지 않음
                            continue
                        dex_name = k_l[:-len("_fee_rate")].strip()
                        if not dex_name:
                            continue
                        dex_fee_pair_map[dex_name] = _parse_fee_pair(v)

                    options = {
                        "walletAddress": wallet_address,
                        "options": {
                            "feeIntPair": fee_pair,                                 # (limit, market)
                            "dexFeeIntPairDefault": dex_fee_pair_default or None,   # (limit, market) or None
                            "dexFeeIntPairMap": dex_fee_pair_map,                   # {dex: (limit, market)}
                            # builder는 있을 때만 주입
                            **({"builder": builder_code} if builder_code else {}),
                        },
                    }
                    if exchange_platform == 'superstack':
                        # api key of its own wallet provider
                        options["apiKey"] = os.getenv(f"{exchange_name.upper()}_API_KEY")
                    else:
                        # normal hl
                        options["apiKey"] = os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY")
                        options["privateKey"] = os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY")

                    self.exchanges[exchange_name] = Hyperliquid(options)
                    
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
            logger.warning("[mpdex] 미설치/경로 오류로 비-HL 생성 스킵: %s", ",".join(non_hl))
        for name in non_hl:
            if self.exchanges.get(name):
                continue
            if create_exchange is None:
                continue
            try:
                key = self._build_mpdex_key(name)
                if key is None:
                    logger.warning(f"[{name}] .env 키가 누락되어 생성 스킵")
                    continue
                client = await create_exchange(name.lower(), key)
                self.exchanges[name] = client
                logger.info(f"[{name}] mpdex client created")
            except Exception as e:
                logger.warning(f"[{name}] mpdex client create failed: {e}")
                self.exchanges[name] = None

        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.warning(f"initialize_all error: {e}")

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
            logger.warning(f"[{name}] env key parse failed: {e}")
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