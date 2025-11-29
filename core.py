# core.py
import os
import asyncio
import configparser
from types import SimpleNamespace
import sys
import logging
from pathlib import Path
logger = logging.getLogger(__name__)  # 모듈 전용 로거

try:
    from exchange_factory import create_exchange  # mpdex 팩토리
except Exception:
    create_exchange = None
    logger.warning("[mpdex] exchange_factory.create_exchange 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")

def _resolve_config_path() -> str:
    """
    config.ini를 아래 우선순위로 찾습니다.
    현재 작업 디렉터리(CWD)/config.ini
    """
    p = (Path.cwd() / "config.ini").resolve()
    if p.exists():
        return str(p)

def _get_bool_env(key: str, fallback: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return fallback
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")

# --- 설정 로드 ---
config = configparser.ConfigParser(
    interpolation=None,
    inline_comment_prefixes=('#', ';')  # [ADD] 행 내 주석 자동 제거
)
CONFIG_PATH = _resolve_config_path()  # [CHG] 유연 경로 사용
_cfg_file = Path(CONFIG_PATH)
if not _cfg_file.exists():
    logger.critical("[config] file not found: %s", CONFIG_PATH)
    # 여기서 바로 raise 하면 exe가 즉시 종료되므로, 메시지를 명확히 남기고 예외 발생
    raise FileNotFoundError(f"config.ini not found. Put config.ini next to the exe or set PDEX_CONFIG. tried: {CONFIG_PATH}")

def load_config_with_encodings(path: str) -> configparser.ConfigParser:
    """
    config.ini를 여러 인코딩으로 안전하게 로드합니다.
    우선순위: UTF-8 → UTF-8-SIG → CP949 → EUC-KR → MBCS(Windows 기본).
    """
    encodings = ("utf-8", "utf-8-sig", "cp949", "euc-kr", "mbcs")
    last_err = None
    cfg = configparser.ConfigParser(
        interpolation=None,
        inline_comment_prefixes=('#', ';')  # [ADD]
    )

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
        # 호환 편의 속성 바로 노출
        self.walletAddress = inner.get("walletAddress")
        self.apiKey = inner.get("apiKey")
        self.secret = inner.get("privateKey") 

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
            hl_like = (exchange_platform in ("hyperliquid", "superstack","treadfi.hyperliquid"))

            if hl_like:
                if exchange_platform == 'treadfi.hyperliquid':
                    order_backend = "mpdex"
                else:
                    order_backend = "hl_native"
            else:
                order_backend = "mpdex"

            # FrontendMarket 플래그 로딩
            fm_raw = config.get(exchange_name, "FrontendMarket", fallback="False")
            frontend_market = (fm_raw or "").strip().lower() == "true"

            self.meta[exchange_name] = {
                "show": show,
                "hl": hl_like,
                "frontend_market": frontend_market,
                "exchange": exchange_platform,
                "order_backend": order_backend,  # [NEW] 주문 경로 판단용
            }

            # hl, but superstack needs its own signature method due to intrinsic wallet provider service
            if hl_like and order_backend == "hl_native":
                builder_code = config.get(exchange_name, "builder_code", fallback=None)
                wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")
                
                # [ADD] config.ini의 is_sub를 읽어 sub-account 여부 판정
                is_sub_env_key = f"{exchange_name.upper()}_IS_SUB"
                is_sub = _get_bool_env(is_sub_env_key, fallback=False)
                logger.info("[core] %s: is_sub(%s)=%s", exchange_name, is_sub_env_key, is_sub)

                if wallet_address:
                    # 수수료: (limit, market) 페어 + DEX별 페어 맵 구성
                    fee_pair, dex_fee_pair_default, dex_fee_pair_map = self._get_fee_rate(exchange_name)
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
                    if is_sub:
                        options["options"]["vaultAddress"] = wallet_address
                        logger.info("[core] %s: is_sub=True → vaultAddress set to walletAddress", exchange_name)

                    if exchange_platform == 'superstack':
                        # api key of its own wallet provider
                        options["apiKey"] = os.getenv(f"{exchange_name.upper()}_API_KEY")
                    else:
                        # normal hl
                        options["apiKey"] = os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY")
                        options["privateKey"] = os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY")

                    self.exchanges[exchange_name] = Hyperliquid(options=options)
                    
                    try:
                        self.exchanges[exchange_name].options["walletAddress"] = wallet_address
                        if is_sub:
                            self.exchanges[exchange_name].options["vaultAddress"] = wallet_address
                    except Exception:
                        pass
            else:
                # non-HL(lighter 등)은 initialize_all에서 생성
                self.exchanges[exchange_name] = None
    
    def _get_fee_rate(self, exchange_name):
        if config.has_option(exchange_name, "fee_rate"):
            fee_pair = _parse_fee_pair(config.get(exchange_name, "fee_rate"))
        else:
            fee_pair = (0,0)
            
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
        return fee_pair, dex_fee_pair_default, dex_fee_pair_map

    async def initialize_all(self):
        # 각 거래소의 initialize_client()를 병렬로 1회 호출
        tasks = []
        # 1) HL 쪽 initialize_client
        #for name, ex in self.exchanges.items():
        #    if ex and self.meta.get(name, {}).get("hl", False):
        #        tasks.append(ex.initialize_client())

        mpdex_ex = [n for n in EXCHANGES if self.meta.get(n, {}).get("order_backend", "") == "mpdex"]
        if create_exchange is None and mpdex_ex:
            logger.warning("[mpdex] 미설치/경로 오류로 비-HL 생성 스킵: %s", ",".join(mpdex_ex))

        for name in mpdex_ex:
            if self.exchanges.get(name):
                continue
            if create_exchange is None:
                continue

            # we know fn: name -> exchange (platform)
            # create client by exchange platform not by name
            # but .env given by exchange name
            exchange_platform = self.meta.get(name, {}).get("exchange")
                        
            try:
                key = self._build_mpdex_key(name, exchange_platform)

                if key is None:
                    print(f"[{name}] .env 키가 누락되어 생성 스킵")
                    logger.warning(f"[{name}] .env 키가 누락되어 생성 스킵")
                    continue

                client = await create_exchange(exchange_platform.lower(), key)
                
                if exchange_platform == "treadfi.hyperliquid":
                    # hl 처럼 position 및 가격 조회를 위해 필요
                    # sub account의 주소에 해당하는게 wallet address
                    client.walletAddress = client.sub_wallet_address
                    fee_pair, dex_fee_pair_default, dex_fee_pair_map =  self._get_fee_rate(name)
                    client.options = {
                        "feeIntPair": fee_pair,                                 # (limit, market)
                        "dexFeeIntPairDefault": dex_fee_pair_default or None,   # (limit, market) or None
                        "dexFeeIntPairMap": dex_fee_pair_map,                   # {dex: (limit, market)}
                    }
                    try:
                        res = await client.login()
                        print("User Name", res["username"])
                    except Exception as e:
                        print(f"Treadfi login problem {e}")
                        print(f"Treadfi cannot be used")
                if exchange_platform == "variational":
                    # 로그인 필요
                    try:
                        res = await client.initialize()
                        print("로그인 성공:", res.get('ok'))
                    except Exception as e:
                        print(f"베리나 로그인 문제 {e}")
                    
                self.exchanges[name] = client

                print(f"[{name}] mpdex client created")
                logger.info(f"[{name}] mpdex client created")

            except Exception as e:
                print(f"[{name}] mpdex client create failed: {e}")
                logger.warning(f"[{name}] mpdex client create failed: {e}")
                self.exchanges[name] = None

    def _build_mpdex_key(self, name: str, exchange_platform: str) -> SimpleNamespace | None:
        """mpdex 각 거래소별 키를 .env에서 읽어 SimpleNamespace로 생성"""
        u_name = name.upper()
        try:
            if exchange_platform.lower() == "lighter":
                return SimpleNamespace(
                    account_id=int(os.getenv(f"{u_name}_ACCOUNT_ID")),
                    private_key=os.getenv(f"{u_name}_PRIVATE_KEY"),
                    api_key_id=int(os.getenv(f"{u_name}_API_KEY_ID")),
                    l1_address=os.getenv(f"{u_name}_L1_ADDRESS"),
                )
            if exchange_platform.lower() == "paradex":
                return SimpleNamespace(
                    wallet_address=os.getenv(f"{u_name}_L1_ADDRESS"),
                    paradex_address=os.getenv(f"{u_name}_ADDRESS"),
                    paradex_private_key=os.getenv(f"{u_name}_PRIVATE_KEY"),
                )
            if exchange_platform.lower() == "edgex":
                return SimpleNamespace(
                    account_id=int(os.getenv(f"{u_name}_ACCOUNT_ID")),
                    private_key=os.getenv(f"{u_name}_PRIVATE_KEY"),
                )
            if exchange_platform.lower() == "grvt":
                return SimpleNamespace(
                    api_key=os.getenv(f"{u_name}_API_KEY"),
                    account_id=int(os.getenv(f"{u_name}_ACCOUNT_ID")),
                    secret_key=os.getenv(f"{u_name}_SECRET_KEY"),
                )
            if exchange_platform.lower() == "backpack":
                return SimpleNamespace(
                    api_key=os.getenv(f"{u_name}_API_KEY"),
                    secret_key=os.getenv(f"{u_name}_SECRET_KEY"),
                )
            if exchange_platform.lower() == "treadfi.hyperliquid":
                return SimpleNamespace(
                    session_cookies={"csrftoken":os.getenv(f"{u_name}_CSRF_TOKEN"),
                                     "sessionid":os.getenv(f"{u_name}_SESSION_ID")},
                    evm_private_key=os.getenv(f"{u_name}_PRIVATE_KEY"),
                    main_wallet_address=os.getenv(f"{u_name}_MAIN_WALLET_ADDRESS"),
                    sub_wallet_address=os.getenv(f"{u_name}_SUB_WALLET_ADDRESS"),
                    account_name=os.getenv(f"{u_name}_ACCOUNT_NAME"),
                )
            if exchange_platform.lower() == "variational":
                return SimpleNamespace(
                    evm_wallet_address=os.getenv(f"{u_name}_WALLET_ADDRESS"),
                    session_cookies={"vr_token":os.getenv(f"{u_name}_JWT_TOKEN")},
                    evm_private_key=os.getenv(f"{u_name}_PRIVATE_KEY"),
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
        return self.meta.get(name, 
                {
                    "show": False,
                    "hl": False,
                    "frontend_market": False,
                    "order_backend": "",
                    "exchange": ""
                })

    def is_hl_like(self, name:str):
        return self.get_meta(name).get("hl")
    
    def get_order_backend(self, name:str):
        return self.get_meta(name).get("order_backend")
    
    def get_exchange_platform(self, name:str):
        return self.get_meta(name).get("exchange")

    def visible_names(self):
        return [n for n in EXCHANGES if self.meta.get(n, {}).get("show", False)]

    def all_names(self):
        return list(EXCHANGES)

    def first_hl_exchange(self):
        """hl=True 이고 order_backend=hl_native"""
        for n in EXCHANGES:
            m = self.meta.get(n, {})
            if m.get("hl", False) and m.get("order_backend", "") == "hl_native" and self.exchanges.get(n):
                return self.exchanges[n]
        return None