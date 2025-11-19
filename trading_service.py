# trading_service.py
import logging
import time
from typing import Tuple, Optional, Dict, Any
from hl_ws.hl_ws_client import HLWSClientRaw, http_to_wss
from core import ExchangeManager
import asyncio
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN 
try:
    from exchange_factory import symbol_create
except Exception:
    symbol_create = None
    logging.warning("[mpdex] exchange_factory.symbol_create 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")

DEBUG_FRONTEND = False
logger = logging.getLogger("trading_service")
logger.propagate = True                    # 루트로 전파해 main.py의 FileHandler만 사용
logger.setLevel(logging.DEBUG if DEBUG_FRONTEND else logging.INFO)

def _parse_hip3_symbol(sym: str) -> Tuple[Optional[str], str]:
    # 'xyz:XYZ100' → ('xyz', 'xyz:XYZ100') 로 표준화
    if ":" in sym:
        dex, coin = sym.split(":", 1)
        dex_l = dex.lower()
        coin_u = coin.upper()
        return dex_l, f"{dex_l}:{coin_u}"
    return None, sym

# [추가] 소수부의 0만 제거하는 안전 유틸
def _strip_decimal_trailing_zeros(s: str) -> str:
    """
    문자열 s가 '123.4500'이면 '123.45'로,
    '123.000'이면 '123'으로 변환한다.
    소수점이 없으면(예: '26350') 정수부의 0는 절대 제거하지 않는다.
    """
    if "." in s:
        return s.rstrip("0").rstrip(".")  # comment: 정수부는 건드리지 않음
    return s

class TradingService:
    def __init__(self, manager: ExchangeManager):
        self.manager = manager
        # [WS] HL WebSocket Client
        self.hl_ws: Optional[HLWSClientRaw] = None
        self._ws_lock = asyncio.Lock()
        self._ws_started = False
        # [추가] 스코프별(hl/xyz/...) WS 풀
        self._ws_by_scope: Dict[str, HLWSClientRaw] = {}
        self._ws_scope_locks: Dict[str, asyncio.Lock] = {}

        #  상태/쿨다운 캐시
        self._last_collateral: dict[str, float] = {}
        self._last_status: dict[str, Tuple[str, str, float]] = {}  # (pos_str, col_str, col_val)
        self._cooldown_until: dict[str, float] = {}                # 429 쿨다운 끝나는 시각
        self._balance_every: float = 5.0                           # balance 최소 간격(초)
        self._last_balance_at: dict[str, float] = {}               # balance 최근 호출 시각
        self._backoff_sec: dict[str, float] = {}                   # per-ex 백오프(초)
        
        # ex_name -> { 'vaults': [universe...], 'map': {coin -> asset_index}}
        self._asset_index_cache_by_ex: Dict[str, Dict[str, Any]] = {} 
        #  HIP-3 레버리지 적용 여부 캐시: (exchange_name, hip3_coin) -> bool
        self._leverage_applied: Dict[tuple[str, str], bool] = {}
        self._hl_px_cache_by_dex: Dict[str, Dict[str, Any]] = {}  # {'HL'|'xyz': {'ts': float, 'map': {...}}}
        # HL 빌더 DEX 목록 캐시(앱 시작 시 1회)
        self._perp_dex_list: Optional[list[str]] = None 
        # [추가/정리] (dex_or_HL, coin_key) -> decimals
        self._hl_px_dec_cache: Dict[tuple[str, str], int] = {}
        # (dex_or_HL, coin_key) -> szDecimals 
        self._hl_sz_dec_cache: Dict[tuple[str, str], int] = {}

        # dex별 quote 화폐 캐시
        self._spot_token_map: Optional[Dict[str, str]] = None  # 1회성: '0' -> 'USDC'
        self._dex_quote_map: Dict[str, str] = {}               # 'xyz' -> 'USDH'
        
        self._leverage_inflight: set[tuple[str, str]] = set()          # (exchange_name, coin_key) in-flight 가드
        self._leverage_last_check: dict[tuple[str, str], float] = {}   # 마지막 체크 시각(스로틀)
        self._leverage_check_interval: float = 5.0                     # 스로틀 간격(초) - 필요시 조정
        self._spot_usdh_by_ex: dict[str, float] = {}  # HL: 거래소별 마지막 USDH 잔고

        # [추가] 주소별 합산 AV 캐시: key="address" (또는 exchange fallback)
        #  값: {"ts": monotonic, "av": float, "usdh": float}
        self._agg_av_cache: Dict[str, Dict[str, float]] = {}
        self._agg_refresh_secs: float = 1.0  # comment: 합산 재계산 최소 주기(초)
        # [ADD] 표시 자리수 설정 (환경변수 오버라이드 가능)
        self._disp_dec_max = 8
        self._disp_sig_max = 7

    def format_price_simple(self, px: float) -> str:
        """
        간단 표시 규칙(고정 자릿수 표기, 소수부 0도 유지):
          - abs(px) >= 10      → 소수 2자리
          - 1 <= abs(px) < 10  → 소수 3자리
          - 0.1 <= abs(px) < 1 → 소수 4자리
          - 0.01 <= abs(px) < 0.1  → 소수 5자리
          - 0.001 <= abs(px) < 0.01 → 소수 6자리
          - 그 미만(아주 작은 값) → 소수 6자리(최대)
        """
        try:
            v = float(px)
        except Exception:
            return str(px)

        a = abs(v)
        if a >= 10:
            dec = 2
        elif a >= 1:
            dec = 3
        elif a >= 0.1:
            dec = 4
        elif a >= 0.01:
            dec = 5
        elif a >= 0.001:
            dec = 6
        else:
            dec = 6  # 최대 소수 자리

        q = Decimal(f"1e-{dec}") if dec > 0 else Decimal("1")
        d = Decimal(str(v)).quantize(q, rounding=ROUND_HALF_UP)
        s = format(d, "f")  # comment: 소수부 0 제거하지 않음(고정 자릿수 유지)  <-- FIX

        # 천단위 구분
        return self._format_with_grouping(s)

    def _format_with_grouping(self, s: str) -> str:
        """
        '12345.6700' → '12,345.67', '0.0001200' → '0.00012'
        s는 소수부 0 제거가 이미 반영된 문자열이라고 가정.
        """
        if not s:
            return s
        neg = s.startswith("-")
        if neg:
            s = s[1:]
        if "." in s:
            ip, fp = s.split(".", 1)
        else:
            ip, fp = s, None
        try:
            ip_g = f"{int(ip or '0'):,}"
        except Exception:
            # int 변환 실패 시 안전 폴백
            ip_g = ip or "0"
        out = ip_g if fp is None else f"{ip_g}.{fp}"
        return f"-{out}" if neg else out

    # [ADD] 일반(spot/비-HL) 표시용 포맷터
    def _format_generic_price(self, px: float, dec_max: int | None = None, sig_max: int | None = None) -> str:
        """
        - dec_max(최대 소수), sig_max(최대 유효숫자) 기준으로 표시 문자열 생성.
        - 기본값: dec_max=self._disp_dec_max(기본 8), sig_max=self._disp_sig_max(기본 7)
        - 소수부 0은 제거, 정수부 0은 보존. 천단위 구분기호 적용.
        """
        dec_max = self._disp_dec_max if dec_max is None else max(0, int(dec_max))
        sig_max = self._disp_sig_max if sig_max is None else max(1, int(sig_max))

        d = Decimal(str(px))
        # 1) 우선 dec_max로 반올림
        q = Decimal(f"1e-{dec_max}") if dec_max > 0 else Decimal("1")
        d1 = d.quantize(q, rounding=ROUND_HALF_UP)
        s1 = format(d1, "f")

        # 유효숫자 계산
        if "." in s1:
            ip, fp = s1.split(".", 1)
        else:
            ip, fp = s1, ""
        int_digits = len(ip.lstrip("-").lstrip("0")) if ip not in ("", "0", "-0") else 0
        frac_digits = len(fp)
        sig_digits = int_digits + (len(fp.lstrip("0")) if int_digits == 0 else frac_digits)

        if sig_digits <= sig_max:
            return self._format_with_grouping(_strip_decimal_trailing_zeros(s1))

        # 2) 유효숫자 제한에 맞춰 소수부 축소
        allow_frac = max(0, sig_max - int_digits)
        allow_frac = min(allow_frac, dec_max)
        q2 = Decimal(f"1e-{allow_frac}") if allow_frac > 0 else Decimal("1")
        d2 = d.quantize(q2, rounding=ROUND_HALF_UP)
        s2 = format(d2, "f")
        return self._format_with_grouping(_strip_decimal_trailing_zeros(s2))

    # [ADD] HL 표시용 포맷터(Perp은 tick_decimals 준수)
    async def format_price_for_display(
        self,
        ex,
        dex: Optional[str],
        coin_key: str,
        px_val: float,
        is_spot: bool = False
    ) -> str:
        """
        - HL Perp: szDecimals → tick_decimals(=6 - sz)로 _format_perp_price 사용 후 천단위 적용
        - Spot/기타: _format_generic_price 사용
        coin_key:
          - 메인 HL Perp: 'BTC' 같은 UPPER
          - HIP-3 Perp: 'xyz:XYZ100' 원문
          - Spot: 'BASE/QUOTE' 등 (is_spot=True로 호출 권장)
        """
        if is_spot:
            return self._format_generic_price(px_val)

        # Perp(HL 메인/HIP-3)
        try:
            sz_dec = await self._hl_sz_decimals(ex, dex, coin_key)
            tick_decimals = max(0, 6 - int(sz_dec))
        except Exception:
            # 정보 실패 시 일반 포맷터로 폴백
            return self._format_generic_price(px_val)

        core = self._format_perp_price(float(px_val), tick_decimals)  # comment: tick 규칙 + 유효숫자(5) 포함
        return self._format_with_grouping(core)

    def _sanitize_http_base(self, ex: Any, url_template: Optional[str]) -> str:
        """
        ccxt 인스턴스(ex)의 hostname 속성을 읽어 '{hostname}' 템플릿을 치환하고,
        올바른 base URL을 반환합니다.
        
        예시:
        - ex.hostname = 'hyperliquid.xyz', url_template = 'https://api.{hostname}' -> 'https://api.hyperliquid.xyz'
        - url_template이 없으면 'https://api.hyperliquid.xyz'로 폴백
        """
        try:
            from urllib.parse import urlparse

            # 1. 템플릿 URL이 없으면 기본값
            if not url_template:
                return "https://api.hyperliquid.xyz"

            # 2. '{hostname}' 템플릿이 있는지 확인
            if '{hostname}' in url_template:
                # ex 객체에서 hostname 가져오기
                hostname = getattr(ex, 'hostname', None)
                if hostname:
                    # hostname으로 치환
                    final_url = url_template.format(hostname=hostname)
                else:
                    # ex에 hostname 없으면 기본값으로 치환
                    final_url = url_template.format(hostname='hyperliquid.xyz')
                
                u = urlparse(final_url)
                if u.scheme and u.netloc:
                    return f"{u.scheme}://{u.netloc}"
            
            # 3. 템플릿이 없는 일반 URL이면 기존 로직으로 정규화
            u = urlparse(url_template)
            if u.scheme and u.netloc:
                return f"{u.scheme}://{u.netloc}"
            
            # 4. 모든 시도 실패 시 최종 폴백
            return "https://api.hyperliquid.xyz"

        except Exception as e:
            logging.debug(f"[_sanitize_http_base] fallback due to error: {e}")
            return "https://api.hyperliquid.xyz"
    
    def _get_px_decimals(self, dex: Optional[str], coin_key: str, fallback_by_sz: Optional[int] = None) -> int:
        """
        _hl_price_map 호출 시 저장된 (dex_or_HL, coin_key) → px_decimals 캐시를 우선 사용.
        없으면 (옵션) szDecimals 기반 보정값(6 - sz) 또는 2로 폴백.
        """
        scope = dex if dex else "HL"
        d = self._hl_px_dec_cache.get((scope, coin_key))
        if isinstance(d, int) and d >= 0:
            return d
        if isinstance(fallback_by_sz, int) and fallback_by_sz >= 0:
            return max(0, fallback_by_sz)  # comment: sz 기반 추정값
        return 2  # comment: 최후 폴백

    def _round_to_tick(self, value: float, decimals: int, up: bool) -> Decimal:
        # comment: tick_decimals(= 6 - szDecimals)에 맞춰 BUY=상향, SELL=하향 정렬
        q = Decimal(f"1e-{decimals}") if decimals > 0 else Decimal("1")
        d = Decimal(str(value))
        return d.quantize(q, rounding=(ROUND_UP if up else ROUND_DOWN))

    async def fetch_perp_dexs(self) -> list[str]:
        """
        HL 첫 거래소에서 publicPostInfo({"type":"perpDexs"}) 호출 → dex 이름 목록(lowercase) 반환.
        앱 생애주기에서 최초 1회만 네트워크 호출하고, 이후에는 캐시를 반환합니다.
        """
        # 캐시가 있으면 즉시 반환
        if self._perp_dex_list is not None:
            return self._perp_dex_list

        ex = self.manager.first_hl_exchange()
        if not ex:
            self._perp_dex_list = []
            return self._perp_dex_list

        try:
            resp = await ex.publicPostInfo({"type": "perpDexs"})
            names: list[str] = []

            # [수정] 다양한 응답 포맷을 수용
            # 1) 리스트인 경우: ["xyz","abc"] 또는 [{"name":"xyz"}, ...]
            if isinstance(resp, list):
                for e in resp:
                    if isinstance(e, str):
                        names.append(e.lower())
                    elif isinstance(e, dict):
                        # name / dex / id 후보 키
                        for k in ("name", "dex", "id"):
                            v = e.get(k)
                            if isinstance(v, str) and v.strip():
                                names.append(v.strip().lower())
                                break
            # 2) 딕셔너리인 경우: {"dexes":[...]} | {"names":[...]} | {"list":[...]}
            elif isinstance(resp, dict):
                for key in ("dexes", "names", "list"):
                    lst = resp.get(key)
                    if isinstance(lst, list):
                        for e in lst:
                            if isinstance(e, str):
                                names.append(e.lower())
                            elif isinstance(e, dict):
                                v = e.get("name") or e.get("dex") or e.get("id")
                                if isinstance(v, str) and v.strip():
                                    names.append(v.strip().lower())

            # HL 자체는 버튼 리스트에서 제외, 중복 제거 + 정렬
            names = [n for n in names if n and n != "hl"]
            self._perp_dex_list = sorted(set(names))
            logger.info("[HIP3] perpDexs loaded: %s", self._perp_dex_list)
            return self._perp_dex_list
        except Exception as e:
            logger.info("[HIP3] fetch_perp_dexs failed: %s", e)
            self._perp_dex_list = []
            return self._perp_dex_list

    def set_perp_dexs(self, dex_list: list[str]) -> None:
        """
        UI 등 외부에서 이미 구한 perpDex 목록을 서비스 캐시에 주입할 때 사용.
        """
        try:
            self._perp_dex_list = sorted(set([str(x).lower() for x in dex_list]))
        except Exception:
            self._perp_dex_list = []

    def _tif_capitalize(self, tif: str | None, default: str = "Gtc") -> str:
        """ccxt가 사용하는 스타일과 동일하게 timeInForce를 Capitalize."""
        if not tif:
            return default
        t = tif.strip().lower()
        if t == "alo":
            return "Alo"
        if t == "ioc":
            return "Ioc"
        if t == "gtc":
            return "Gtc"
        return t.capitalize()

    async def _hl_pick_price(self, ex, dex: str, coin: str, price_hint: Optional[float]) -> float:
        """HIP‑3 시장가용 가격: 힌트 우선, 없으면 _hl_price_map(dex)에서 해당 코인 가격."""
        if price_hint is not None:
            return float(price_hint)
        px_map = await self._hl_price_map(ex, dex)
        px = px_map.get(coin)
        if px is None:
            raise RuntimeError(f"Price not found for {coin}")
        return float(px)

    def _hl_user_address(self, ex) -> Optional[str]:
        try:
            addr = getattr(ex, "walletAddress", None)
        except Exception:
            addr = None
        if not addr:
            try:
                # ccxt 옵션 하위에 들어있는 환경을 위해 보조 조회
                addr = (getattr(ex, "options", {}) or {}).get("walletAddress") \
                       or (getattr(ex, "options", {}) or {}).get("walletaddress")
            except Exception:
                addr = None
        if addr:
            return str(addr).lower()
        return None

    def _ws_key(self, scope: str, ex) -> str:
        """
        WS 풀을 'scope|address'로 분리해 계정(주소)별로 독립적인 WS를 유지.
        scope: 'hl' 또는 'dex명'
        """
        scope_l = (scope or "hl").lower()
        addr = self._hl_user_address(ex) or "noaddr"
        return f"{scope_l}|{addr}"

    async def _get_ws_for_scope(self, scope: str, ex) -> Optional[HLWSClientRaw]:
        """
        DEX 스코프별 + 주소별 WS 클라이언트를 관리/생성합니다.
        - scope: 'hl', 'xyz', 'flx' 등
        - ex: ccxt HL 인스턴스 (URL 및 지갑 주소 참조용)
        """
        if HLWSClientRaw is None or not ex:
            return None

        scope_l = (scope or "hl").lower()
        address = self._hl_user_address(ex)
        key = self._ws_key(scope_l, ex)  # comment: "scope|address"

        # 1) 이미 생성된 클라이언트가 있으면 반환
        if key in self._ws_by_scope:
            return self._ws_by_scope[key]

        # 2) 키별 락 생성 및 획득
        if key not in self._ws_scope_locks:
            self._ws_scope_locks[key] = asyncio.Lock()
        async with self._ws_scope_locks[key]:
            # 더블 체크
            if key in self._ws_by_scope:
                return self._ws_by_scope[key]

            try:
                http_base = self._sanitize_http_base(ex, getattr(ex, "urls", {}).get("api", {}).get("public"))
                ws_url = http_to_wss(http_base)
                dex_arg = None if scope_l == "hl" else scope_l

                logger.info(
                    "[WS] Creating client for key='%s' (scope='%s', addr=%s, http=%s)",
                    key, scope_l, address, http_base
                )

                ws = HLWSClientRaw(
                    ws_url=ws_url,
                    dex=dex_arg,
                    address=address,   # comment: 주소별로 독립
                    coins=[],
                    http_base=http_base
                )
                await ws.ensure_spot_token_map_http()
                await ws.connect()
                await ws.ensure_core_subs()
                await ws.subscribe()

                self._ws_by_scope[key] = ws
                return ws
            except Exception as e:
                logger.error(f"[WS] key='{key}' start failed: {e}", exc_info=True)
                return None

    async def _hl_sz_decimals(self, ex, dex: Optional[str], coin_key: str) -> int:
        """
        metaAndAssetCtxs(dex?)에서 코인(메인: 'BTC', HIP‑3: 'xyz:XYZ100')의 szDecimals를 1회 캐시 후 반환.
        """
        cache_key = (dex if dex else "HL", coin_key)
        if cache_key in self._hl_sz_dec_cache:
            return self._hl_sz_dec_cache[cache_key]

        payload = {"type": "metaAndAssetCtxs"}
        if dex:
            payload["dex"] = dex
        try:
            resp = await ex.publicPostInfo(payload)
            if not isinstance(resp, list) or len(resp) < 2:
                self._hl_sz_dec_cache[cache_key] = 0
                return 0
            universe = (resp[0] or {}).get("universe", []) or []
            for a in universe:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if not name or a.get("isDelisted", False):
                    continue
                key = name.upper() if not dex else name
                if key != coin_key:
                    continue
                try:
                    szd = int(a.get("szDecimals"))
                except Exception:
                    szd = 0
                self._hl_sz_dec_cache[cache_key] = szd
                return szd
            self._hl_sz_dec_cache[cache_key] = 0
            return 0
        except Exception:
            self._hl_sz_dec_cache[cache_key] = 0
            return 0

    def _format_perp_price(self, px: float, decimals_max: int) -> str:
        """
        Perp 가격 포맷:
        - tick_decimals(=decimals_max)로 반올림
        - 유효숫자 최대 5 자리 제한
        - 소수부의 0만 제거(정수부 0는 보존)
        """
        d = Decimal(str(px))
        # 1) 소수자릿수 제한으로 반올림
        quant = Decimal(f"1e-{decimals_max}") if decimals_max > 0 else Decimal("1")
        d = d.quantize(quant, rounding=ROUND_HALF_UP)

        s = format(d, "f")
        if "." not in s:
            # 정수 가격은 그대로 반환 (예: '26350' → '26350')
            return s

        int_part, frac_part = s.split(".", 1)
        # 현재 유효숫자 계산
        if int_part == "" or int_part == "0":
            sig_digits = len(frac_part.lstrip("0"))
            int_digits = 0
        else:
            int_digits = len(int_part.lstrip("0"))
            sig_digits = int_digits + len(frac_part)

        if sig_digits <= 5:
            # 소수부 0만 제거
            return _strip_decimal_trailing_zeros(s)

        # 2) 유효숫자 5로 축소(소수부만 축소)
        allow_frac = max(0, 5 - int_digits)
        allow_frac = min(allow_frac, decimals_max)
        quant2 = Decimal(f"1e-{allow_frac}") if allow_frac > 0 else Decimal("1")
        d2 = d.quantize(quant2, rounding=ROUND_HALF_UP)

        s2 = format(d2, "f")
        # [중요 수정] 정수부의 끝자리 0가 잘리지 않도록, 소수부가 있을 때만 0 제거
        return _strip_decimal_trailing_zeros(s2)

    async def _hl_get_user_state(self, ex, dex: Optional[str], user_addr: str) -> Optional[dict]:
        """
        clearinghouseState(user, dex)를 Info API로 조회.
        예시 응답(요약):
        {
            "marginSummary": {...},
            "assetPositions": [
            { "type": "oneWay",
                "position": {
                "coin": "xyz:XYZ100",
                "szi": "0.0004",
                "leverage": {"type": "isolated","value": "20","rawUsd": "-9.538334"},
                "entryPx": "25075.0",
                "positionValue": "10.0296",
                "unrealizedPnl": "-0.0004",
                "returnOnEquity": "-0.00079760",
                "liquidationPx": "24457.2666",
                "marginUsed": "0.491266",
                "maxLeverage": "20",
                "cumFunding": {...}
                }
            }
            ],
            "time": "1763270235843"
        }
        """
        try:
            if not user_addr:
                return None
            payload = {"type": "clearinghouseState", "user": user_addr.lower()}
            if dex:
                payload["dex"] = dex
            state = await ex.publicPostInfo(payload)
            if isinstance(state, dict):
                logger.debug("[HL] state ok: dex=%s user=%s keys=%s", dex or "HL", user_addr, list(state.keys()))
                return state
            if isinstance(state, list) and state and isinstance(state[0], dict):
                return state[0]
            logger.info("[HL] unexpected state type: %s", type(state))
            return None
        except Exception as e:
            logger.info("[HL] clearinghouseState failed: %s", e)
            return None

    def _agg_key(self, ex) -> str:
        addr = self._hl_user_address(ex)
        if addr:
            return addr.lower()
        # 주소 미확인 시 exchange 인스턴스 id로 대체(동일 프로세스 내 유효)
        return f"ex:{id(ex)}"

    async def _get_hl_total_account_value_and_usdh(self, ex) -> Tuple[float, float, float]:
        """
        반환: (total_perp_av, usdh_spot, usdc_spot)
        - total_perp_av: 'hl' + 모든 HIP-3 dex의 accountValue 합(Perp 마진 계정 USDC 기준)
        - usdh_spot: HL spot의 USDH 잔고
        - usdc_spot: HL spot의 USDC 잔고
        캐시 만료(_agg_refresh_secs) 전에는 캐시 반환(깜빡임 방지).
        """
        if not ex:
            return 0.0, 0.0, 0.0  # comment: [ADD] 기본값에 usdc 포함

        key = self._agg_key(ex)
        now = time.monotonic()
        cached = self._agg_av_cache.get(key)
        if cached and (now - float(cached.get("ts", 0.0)) < self._agg_refresh_secs):
            return float(cached.get("av", 0.0)), float(cached.get("usdh", 0.0)), float(cached.get("usdc", 0.0))

        total_av = 0.0
        usdh = 0.0
        usdc = 0.0  # comment: [ADD] SPOT USDC 합산 대상(현 사양상 HL 스코프만)

        # 스코프 목록: 'hl' + 알려진 HIP-3 DEX들
        dex_list = (self._perp_dex_list or [])
        scopes: list[str] = ["hl"] + [d for d in dex_list if d and d != "hl"]

        # WS들을 병렬 확보
        ws_tasks = [self._get_ws_for_scope(sc, ex) for sc in scopes]
        results = await asyncio.gather(*ws_tasks, return_exceptions=True)

        for sc, ws in zip(scopes, results):
            if isinstance(ws, Exception) or ws is None:
                continue
            try:
                av = ws.get_account_value_by_dex("hl" if sc == "hl" else sc)
                if av is not None:
                    total_av += float(av)
                if sc == "hl":
                    # USDH는 HL spot에만 존재한다는 전제
                    u_h = ws.get_spot_balance("USDH")
                    u_c = ws.get_spot_balance("USDC")
                    if u_h is not None:
                        usdh = float(u_h)
                    if u_c is not None:
                        usdc = float(u_c)
            except Exception as e:
                logging.warning(f"_get_hl_total_account_value_and_usdh: {e}")
                # 개별 스코프 실패는 무시하고 계속 합산
                continue

        # 캐시 저장
        self._agg_av_cache[key] = {"ts": now, "av": total_av, "usdh": usdh, "usdc": usdc}
        return total_av, usdh, usdc

    def _hl_parse_position_from_state(self, state: dict, coin_key: str) -> Optional[dict]:
        """
        clearinghouseState에서 특정 코인(메인: 'BTC', HIP‑3: 'xyz:XYZ100') 포지션 추출.
        """
        try:
            hip3_debug = True

            if not isinstance(state, dict):
                logger.debug("[HL] state not dict: %s", type(state))
                return None

            if hip3_debug:
                try:
                    import json
                    logger.debug("[HL] raw state(head): %s...", json.dumps(state)[:2000])
                except Exception:
                    logger.debug("[HL] raw state(head): %s...", str(state)[:1000])

            aps = state.get("assetPositions", []) or []
            logger.debug("[HL] parse start: target=%s, assetPositions.len=%d", coin_key, len(aps))

            # 코인 이름 헤드 로그
            coins = []
            for ap in aps[:50]:
                pos0 = (ap or {}).get("position") or {}
                coins.append(str(pos0.get("coin") or ""))
            logger.debug("[HL] coins in positions(head): %s", coins[:20])

            for idx, ap in enumerate(aps):
                pos = (ap or {}).get("position") or {}
                coin = str(pos.get("coin") or "")
                if coin != coin_key:
                    logger.debug("[HL] skip idx=%d coin=%s != %s", idx, coin, coin_key)
                    continue

                def f(x, default=0.0):
                    try: return float(x)
                    except Exception: return default

                szi    = f(pos.get("szi"), 0.0)
                epx    = f(pos.get("entryPx"), 0.0)
                upnl   = f(pos.get("unrealizedPnl"), 0.0)
                liq    = f(pos.get("liquidationPx"), 0.0)
                pval   = f(pos.get("positionValue"), 0.0)
                mused  = f(pos.get("marginUsed"), 0.0)
                lev_i  = pos.get("leverage", {}) or {}
                lev_ty = str(lev_i.get("type") or "").lower()
                try:
                    lev_v = int(float(lev_i.get("value"))) if lev_i.get("value") is not None else None
                except Exception:
                    lev_v = None

                logger.debug("[HL] matched idx=%d coin=%s szi=%s entryPx=%s uPnl=%s lev=(%s,%s) liqPx=%s pVal=%s mUsed=%s",
                            idx, coin, pos.get("szi"), pos.get("entryPx"), pos.get("unrealizedPnl"),
                            lev_ty, lev_i.get("value"), pos.get("liquidationPx"),
                            pos.get("positionValue"), pos.get("marginUsed"))

                if abs(szi) <= 0.0:
                    logger.debug("[HL] matched but zero size: szi=%s", szi)
                    return None

                side = "long" if szi > 0 else "short"

                result = {
                    "coin": coin,
                    "size": abs(szi),
                    "entry_price": epx,
                    "unrealized_pnl": upnl,
                    "side": side,
                    "leverage": lev_v,
                    "leverage_type": lev_ty,
                    "liquidation_price": liq,
                    "position_value": pval,
                    "margin_used": mused,
                }
                try:
                    ms = state.get("marginSummary", {}) or {}
                    if ms.get("accountValue") is not None:
                        result["collateral"] = float(ms.get("accountValue"))
                        logger.debug("[HL] marginSummary.accountValue=%s", ms.get("accountValue"))
                except Exception:
                    pass

                logger.debug("[HL] parse result: %s", result)
                return result

            logger.debug("[HL] no matching position for %s (coins=%s)", coin_key, coins[:20])
            return None
        except Exception as e:
            logger.debug("[HL] parse exception: %s", e, exc_info=True)
            return None
        
    async def _hl_build_asset_map(self, ex, ex_name: str):
        """
        allPerpMetas를 로드해, 모든 vault(universe)를 평탄화하여
        'coin' -> asset_id 맵을 만든다.
        공식:
        - 메인 퍼프(meta_idx=0): asset = index_in_meta
        - 빌더 퍼프(meta_idx>=1): asset = 100000 + meta_idx * 10000 + index_in_meta
        """
        # 이미 빌드된 경우 캐시 사용
        if ex_name in self._asset_index_cache_by_ex:
            return

        try:
            resp = await ex.publicPostInfo({"type": "allPerpMetas"})
            vaults = []
            mapping: Dict[str, int] = {}
            # resp는 vault 메타의 리스트(각 항목에 universe 배열)
            for meta_idx, meta in enumerate(resp or []):
                uni = meta.get("universe") if isinstance(meta, dict) else None
                if not uni:
                    continue
                # 공식 오프셋
                if meta_idx == 0:
                    offset = 0
                else:
                    offset = 100000 + meta_idx * 10000

                for local_idx, asset in enumerate(uni):
                    if not isinstance(asset, dict):
                        continue
                    coin = asset.get("name")
                    if not coin or asset.get("isDelisted"):
                        continue
                    # 예: 메인 BTC → 0, 빌더 1번째 xyz:XYZ100 → 110000 + local_idx
                    mapping[coin] = offset + local_idx

                vaults.append(uni)

            self._asset_index_cache_by_ex[ex_name] = {"vaults": vaults, "map": mapping}
            logger.info("[HIP3] %s: %d vault(s), %d coins cached (assetID built by spec)",
                        ex_name, len(vaults), len(mapping))
        except Exception as e:
            logger.info("[HIP3] %s allPerpMetas build failed: %s", ex_name, e)
            self._asset_index_cache_by_ex[ex_name] = {"vaults": [], "map": {}}

    async def _resolve_asset_index(self, ex, ex_name: str, hip3_coin: str) -> Optional[int]:
        """
        'xyz:XYZ100' 같은 코인의 전역 asset_index를 캐시에서 꺼내거나 allPerpMetas로 빌드 후 반환.
        """
        if ex_name not in self._asset_index_cache_by_ex:
            await self._hl_build_asset_map(ex, ex_name)
        mp = self._asset_index_cache_by_ex.get(ex_name, {}).get("map", {})
        return mp.get(hip3_coin)

    async def _get_max_leverage_unified(self, ex, dex: Optional[str], coin_key: str) -> tuple[Optional[int], bool]:
        """
        metaAndAssetCtxs(dex?)에서 coin_key(name) 항목을 찾아
        (maxLeverage, isolated_flag) 반환.
        - coin_key: 메인 → 'BTC' 같은 UPPER, HIP‑3 → 'xyz:XYZ100' 원문
        - isolated_flag: onlyIsolated=True 또는 marginMode in {'isolated', 'strictIsolated'}
        """
        try:
            payload = {"type": "metaAndAssetCtxs"}
            if dex:
                payload["dex"] = dex
            resp = await ex.publicPostInfo(payload)
            if not isinstance(resp, list) or len(resp) < 2:
                return None, False
            universe = (resp[0] or {}).get("universe", []) or []
            for a in universe:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if not name or a.get("isDelisted", False):
                    continue
                key = name.upper() if not dex else name
                if key != coin_key:
                    continue
                max_lev = a.get("maxLeverage")
                try:
                    max_lev = int(float(max_lev)) if max_lev is not None else None
                except Exception:
                    max_lev = None
                mmode = str(a.get("marginMode") or "").lower()
                only_iso = bool(a.get("onlyIsolated", False) or mmode in ("isolated", "strictisolated"))
                return max_lev, only_iso
            return None, False
        except Exception:
            return None, False
        
    async def ensure_hl_max_leverage_auto(self, exchange_name: str, symbol: str) -> None:
        """
        HL 전용 통합 레버리지 보장:
        - 자산ID/레버리지는 모두 메타 기반으로 처리(메인/HIP‑3 동일)
        - 메인: coin_key='BTC' 등 UPPER, HIP‑3: 'xyz:XYZ100' 원문
        - max 레버리지를 1회만 updateLeverage로 적용(격리 여부: 메타 기준)
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex or not self.manager.get_meta(exchange_name).get("hl", False):
            return

        dex, hip3_coin = _parse_hip3_symbol(symbol)
        coin_key = hip3_coin if dex else symbol.upper()
        # 이미 적용했다면 스킵
        key = (exchange_name, coin_key)

        # 0) 이미 적용되었으면 즉시 반환
        if self._leverage_applied.get(key):
            return

        # 1) in-flight 가드(동시 중복 호출 차단)
        if key in self._leverage_inflight:
            return

        # 2) 최근 체크 스로틀(기본 5초)
        now = time.monotonic()
        last = self._leverage_last_check.get(key, 0.0)
        if (now - last) < self._leverage_check_interval:
            return
        self._leverage_last_check[key] = now
        self._leverage_inflight.add(key)

        try:
            
            # 3) maxLeverage/isolated 여부 (메타)
            max_lev, only_iso = await self._get_max_leverage_unified(ex, dex, coin_key)
            if not max_lev:
                # 없으면 굳이 재시도하지 않도록 적용 완료로 간주(원하면 스로틀만 갱신하고 미적용으로 둘 수도 있음)
                self._leverage_applied[key] = True
                return

            # 4) 자산ID(메타 캐시 기반) → updateLeverage 1회 적용
            try:
                await self._hl_update_leverage(ex, exchange_name, coin_key, leverage=int(max_lev), isolated=bool(only_iso))
                logger.info("[LEVERAGE] %s %s set to max=%s (isolated=%s)", exchange_name, coin_key, max_lev, only_iso)
            except Exception as e:
                # 실패해도 과호출 방지를 위해 일정 시간 스로틀 상태만 유지(필요시 재시도 정책 도입)
                logger.info("[LEVERAGE] %s %s updateLeverage failed: %s", exchange_name, coin_key, e)
                return
            finally:
                # 성공/실패 관계없이 너무 잦은 호출은 방지. 성공 시에는 멱등 보장을 위해 적용 완료로 마킹
                self._leverage_applied[key] = True
        finally:
            # in-flight 해제
            self._leverage_inflight.discard(key)

    async def _hl_create_order_unified(
        self,
        ex,
        exchange_name: str,
        symbol: str,              # 'BTC' 또는 'xyz:XYZ100'
        side: str,                # 'buy' | 'sell'
        amount: float,
        order_type: str,          # 'market' | 'limit'
        price: Optional[float],   # limit price or market price hint
        reduce_only: bool,
        want_frontend: bool,      # 시장가(Front‑end) 옵션
        time_in_force: Optional[str] = None,  # limit일 때 기본 Gtc
        client_id: Optional[str] = None,
    ) -> dict:
        """
        HL 주문을 '한 함수'로 처리:
        - 메인 퍼프: a = ccxt.market(baseId)
        - HIP‑3: a = 100000 + dex_idx*10000 + index_in_meta (allPerpMetas 기반)
        - 시장가: 가격 힌트 or 현재가에 슬리피지 적용, tif=FrontendMarket(옵션 ON) 또는 Ioc/Gtc
        - 지정가: 입력 가격 사용, tif 기본 Gtc
        - builder/fee, reduceOnly, client_id 모두 raw payload로 반영
        """
        # 0) 공통 파라미터
        try:
            slip_str = ex.options.get("defaultSlippage", "0.05")
            slippage = float(slip_str)
        except Exception:
            slippage = 0.05
        is_buy = (side == "buy")

        # 1) HIP‑3 여부 판별(+ 정규화)
        dex, hip3_coin = _parse_hip3_symbol(symbol)

        # 2) 자산 ID(a) & 가격 원본(px_base) 결정
        if dex:
            # HIP‑3: 자산 ID는 빌더 퍼프 규약
            aidx = await self._resolve_asset_index(ex, exchange_name, hip3_coin)
            if aidx is None:
                raise RuntimeError(f"HIP3 asset index not found for {hip3_coin} on {exchange_name}")
            # HIP‑3 가격 소스(metaAndAssetCtxs)
            px_base = await self._hl_pick_price(ex, dex, hip3_coin, price)
        else:
            # 메인 퍼프: 자산 ID도 allPerpMetas 캐시로(메타_idx=0)
            coin_key = symbol.upper()
            aidx = await self._resolve_asset_index(ex, exchange_name, coin_key)
            if aidx is None:
                raise RuntimeError(f"Main asset index not found for {coin_key} on {exchange_name}")
            # 가격도 메타(무 dex)에서
            if price is None:
                px_map = await self._hl_price_map(ex, None)
                px = px_map.get(coin_key)
                if px is None:
                    raise RuntimeError(f"Main price not found for {coin_key}")
                px_base = float(px)
            else:
                px_base = float(price)

        coin_key = (hip3_coin if dex else symbol.upper())
        # szDecimals 조회(1회 캐시) → Perp 허용 price 소수자릿수 = 6 - szDecimals
        sz_dec = await self._hl_sz_decimals(ex, dex, coin_key)
        tick_decimals = max(0, 6 - int(sz_dec))  # perp MAX_DECIMALS = 6

        # [참고] px_decimals는 오직 '로그/보조'용으로만 사용
        px_decimals = self._get_px_decimals(dex, coin_key, fallback_by_sz=tick_decimals)

        # 3) 주문 가격(px_str) & TIF 결정
        if order_type == "market":
            
            if want_frontend:
                tif = "FrontendMarket"
            else:
                tif = "Gtc"
            px_eff = px_base * (1.0 + slippage) if is_buy else px_base * (1.0 - slippage)
            
            # [안전 가드] px_eff가 px_base의 0.5x~1.5x를 벗어나면 클램프 및 경고
            lo, hi = px_base * 0.5, px_base * 1.5
            if px_eff < lo or px_eff > hi:
                logger.warning("[ORDER][GUARD] px_eff out of range: base=%.8f eff=%.8f → clamp[%.8f, %.8f]",
                               px_base, px_eff, lo, hi)
                px_eff = min(max(px_eff, lo), hi)

            d_tick = self._round_to_tick(px_eff, tick_decimals, up=is_buy)
            # [변경] 최종 문자열 생성 시 정수부 0 보존
            price_str = self._format_perp_price(float(d_tick), tick_decimals)
            if not price_str:
                price_str = "0"

        else:
            # 지정가: 가격 필수
            if price is None:
                raise RuntimeError("limit order requires price")
            tif = self._tif_capitalize(time_in_force, default="Gtc")
            price_str = self._format_perp_price(float(price), tick_decimals)

        # 4) 수량 문자열
        if int(sz_dec) > 0:
            q = Decimal(f"1e-{int(sz_dec)}")
            sz_d = Decimal(str(amount)).quantize(q, rounding=ROUND_HALF_UP)
        else:
            sz_d = Decimal(int(round(amount)))
        size_str = format(sz_d, "f")
        # [중요 수정] size도 정수부 0가 잘리지 않도록 소수부가 있을 때만 제거
        size_str = _strip_decimal_trailing_zeros(size_str)

        # 5) raw payload 구성
        order_obj = {
            "a": aidx,
            "b": is_buy,
            "p": price_str,
            "s": size_str,
            "r": bool(reduce_only),
            "t": {"limit": {"tif": tif}},
        }
        if client_id:
            order_obj["c"] = str(client_id)
        
        try:
            logger.info(
                "[ORDER] %s %s %s a=%s px_base=%.10f tick_dec=%d(px_dec=%d) price_str=%s tif=%s reduceOnly=%s",
                exchange_name, order_type.upper(), coin_key, aidx, px_base, tick_decimals, px_decimals,
                price_str, tif, reduce_only
            )
        except Exception:
            pass

        action = {"type": "order", "orders": [order_obj], "grouping": "na"}

        opt = getattr(ex, "options", {}) or {}
        builder_addr = opt.get("builder",None)                      # 사용자 설정 builder_code
        if builder_addr:                                       # 빌더가 있을 때만 builder/fee 추가
            fee_int = None
            dex, _ = _parse_hip3_symbol(symbol)
            if dex:
                fee_map = opt.get("dexFeeInt", {}) or {}
                if dex in fee_map:
                    fee_int = int(fee_map[dex])
            if fee_int is None:
                base_fee = opt.get("feeInt", None)
                if base_fee is not None:
                    fee_int = int(base_fee)
            if fee_int is not None:
                action["builder"] = {"b": str(builder_addr).lower(), "f": int(fee_int)}

        nonce = ex.milliseconds()
        signature = ex.sign_l1_action(action, nonce, None)
        req = {"action": action, "nonce": nonce, "signature": signature}

        if DEBUG_FRONTEND:
            logger.debug("[HL-RAW] payload=%s", req)

        # 6) 전송 및 파싱
        resp = await ex.privatePostExchange(req)
        response_obj = ex.safe_dict(resp, "response", {})
        data = ex.safe_dict(response_obj, "data", {})
        statuses = ex.safe_list(data, "statuses", [])
        orders_to_parse = []
        for st in statuses:
            orders_to_parse.append({"status": st} if st == "waitingForTrigger" else st)
        parsed = ex.parse_orders(orders_to_parse, None)
        return parsed[0] if parsed else {"info": resp}

    # ------------- HIP-3 레버리지 설정(updateLeverage, Isolated 권장) -------------
    async def _hl_update_leverage(self, ex, ex_name: str, hip3_coin: str, leverage: int, isolated: bool=True):
        aidx = await self._resolve_asset_index(ex, ex_name, hip3_coin)
        if aidx is None:
            raise RuntimeError(f"HIP3 asset index not found for {hip3_coin} on {ex_name}")

        action = {"type": "updateLeverage", "asset": aidx, "isCross": (not isolated), "leverage": int(leverage)}
        nonce = ex.milliseconds()
        signature = ex.sign_l1_action(action, nonce, None)
        req = {"action": action, "nonce": nonce, "signature": signature}
        resp = await ex.privatePostExchange(req)
        logger.info("[HIP3] %s leverage set: %s (isolated=%s) -> %s", ex_name, leverage, isolated, resp.get("status"))

    def _to_native_symbol(self, exchange_name: str, coin: str) -> str:
        meta = self.manager.get_meta(exchange_name) or {}
        if meta.get("hl", False):
            return coin
        return symbol_create(exchange_name, coin)
    
    def _extract_order_id(self, res: dict) -> Optional[str]:
        if not isinstance(res, dict):
            return str(res)
        for k in ("tx_hash", "order_id", "id", "hash"):
            v = res.get(k)
            if v:
                return str(v)
        return str(res)
    
    def _is_rate_limited(self, err: Exception | str) -> bool:
        s = str(err).lower()
        return ("429" in s) or ("too many" in s) or ("rate limit" in s)
    
    async def _ensure_spot_token_map(self, ex) -> None:
        """
        publicPostInfo({"type": "spotMeta"})를 호출하여
        토큰 인덱스와 이름의 매핑을 1회 빌드하고 캐시합니다.
        """
        if self._spot_token_map is not None:
            return

        try:
            resp = await ex.publicPostInfo({"type": "spotMeta"})
            if not isinstance(resp, dict) or "tokens" not in resp:
                self._spot_token_map = {}
                return

            mapping = {}
            for token in resp.get("tokens", []):
                if isinstance(token, dict) and "index" in token and "name" in token:
                    mapping[str(token["index"])] = str(token["name"])

            self._spot_token_map = mapping
            logger.info("[QUOTE] Spot token map built: %d items", len(mapping))
        except Exception as e:
            logger.warning("[QUOTE] Failed to build spot token map: %s", e)
            self._spot_token_map = {}  # 실패 시 빈 딕셔너리로 설정하여 재시도 방지
    
    async def _fetch_dex_quote(self, ex, dex: Optional[str]) -> str:
        """
        주어진 dex의 quote 화폐를 조회하고 캐시합니다. (e.g., 'USDC', 'USDH')
        실패 시 'USDC'를 기본값으로 사용하고 캐시하여 반복적인 실패를 방지합니다.
        """
        cache_key = dex if dex else "HL"
        if cache_key in self._dex_quote_map:
            return self._dex_quote_map[cache_key]

        # 스팟 토큰 맵이 없으면 빌드 (최초 1회)
        if self._spot_token_map is None:
            await self._ensure_spot_token_map(ex)

        # 맵 빌드에 실패했거나 비어있으면 기본값으로 진행
        if not self._spot_token_map:
            self._dex_quote_map[cache_key] = "USDC"
            return "USDC"

        try:
            payload = {"type": "meta"}
            if dex:
                payload["dex"] = dex

            meta_info = await ex.publicPostInfo(payload)
            if not isinstance(meta_info, dict) or "collateralToken" not in meta_info:
                raise ValueError("Invalid meta response")

            collateral_idx = str(meta_info.get("collateralToken"))
            quote_currency = self._spot_token_map.get(collateral_idx, "USDC")  # 못찾으면 기본값

            self._dex_quote_map[cache_key] = quote_currency
            logger.info("[QUOTE] Fetched quote for dex '%s': %s", cache_key, quote_currency)
            return quote_currency
        except Exception as e:
            logger.warning("[QUOTE] Failed to fetch quote for dex '%s', defaulting to USDC. Error: %s", cache_key, e)
            self._dex_quote_map[cache_key] = "USDC"  # 실패 시 기본값 캐시
            return "USDC"
        
    def is_configured(self, name: str) -> bool:
        return self.manager.get_exchange(name) is not None

    def is_hl(self, name: str) -> bool:
        return bool(self.manager.get_meta(name).get("hl", False))

    async def _hl_price_map(self, ex, dex: Optional[str] = None) -> Dict[str, float]:
        """
        metaAndAssetCtxs 호출로 전체 페어 가격 맵을 생성.
        - dex=None/'': 메인 HL
        - dex='xyz' 등: HIP‑3
        반환:
        - 메인 HL: {'BTC': 104000.0, 'ETH': 3000.0, ...} (name upper)
        - HIP‑3 : {'xyz:XYZ100': 25075.0, ...} (원본 name 그대로)
        가격과 함께 각 페어의 decimals(소숫점 자리수)도 1회 캐시에 저장합니다.
        """
        try:
            payload = {"type": "metaAndAssetCtxs"}
            if dex:
                payload["dex"] = dex
            resp = await ex.publicPostInfo(payload)
            if not isinstance(resp, list) or len(resp) < 2:
                logger.debug("[HL] metaAndAssetCtxs unexpected resp: %s", type(resp))
                return {}

            universe = (resp[0] or {}).get("universe", []) or []
            asset_ctxs = resp[1] or []
            px_map: Dict[str, float] = {}

            # 1) 인덱스 매칭 우선
            for i, a in enumerate(universe):
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if not name or a.get("isDelisted", False):
                    continue
                ctx = asset_ctxs[i] if (i < len(asset_ctxs) and isinstance(asset_ctxs[i], dict)) else {}
                # 우선순위: markPx → midPx → oraclePx → prevDayPx
                px = None
                src_val = None
                for k in ("markPx", "midPx", "oraclePx", "prevDayPx"):
                    v = ctx.get(k)
                    if v is not None:
                        try:
                            px = float(v)
                            src_val = v
                            break
                        except Exception:
                            continue
                if px is None:
                    continue
                key = name.upper() if not dex else name
                px_map[key] = px

                # decimals 1회 저장
                dec_key = (dex if dex else "HL", key)
                if dec_key not in self._hl_px_dec_cache:
                    s = str(src_val)
                    self._hl_px_dec_cache[dec_key] = int(len(s.split(".", 1)[1]) if "." in s else 0)

            # 2) 이름 기반 보완(인덱스 불일치 대비)
            valid_cnt = sum(1 for a in universe if isinstance(a, dict) and a.get("name"))
            if len(px_map) < valid_cnt:
                for a, ctx in zip(universe, asset_ctxs):
                    try:
                        if not isinstance(a, dict) or not isinstance(ctx, dict):
                            continue
                        name = str(a.get("name") or "")
                        if not name or a.get("isDelisted", False):
                            continue
                        key = name.upper() if not dex else name
                        if key in px_map:
                            continue
                        # 우선순위 동일
                        for k in ("markPx", "midPx", "oraclePx", "prevDayPx"):
                            v = ctx.get(k)
                            if v is not None:
                                px_map[key] = float(v)
                                # decimals도 저장(조기 return 없음)
                                dec_key = (dex if dex else "HL", key)
                                if dec_key not in self._hl_px_dec_cache:
                                    s = str(v)
                                    self._hl_px_dec_cache[dec_key] = int(len(s.split(".", 1)[1]) if "." in s else 0)
                                break
                    except Exception:
                        continue

            return px_map
        except Exception as e:
            logger.info("[HL] metaAndAssetCtxs payload=%s failed: %s",
                        {"type": "metaAndAssetCtxs", **({"dex": dex} if dex else {})}, e)
            return {}
    
    async def fetch_price(self, exchange_name: str, symbol: str, dex_hint: Optional[str] = None) -> str:
        """
        가격 조회:
        - HL: WS 캐시 우선 사용
        - 비-HL: REST API
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "N/A"
        meta = self.manager.get_meta(exchange_name) or {}

        try:
            if not meta.get("hl", False):
                native = self._to_native_symbol(exchange_name, symbol)
                px = await ex.get_mark_price(native)
                return self.format_price_simple(float(px))

            # HL: 스코프 결정
            dex, hip3_coin = _parse_hip3_symbol(symbol)
            scope = dex if dex else "hl"
            ws = await self._get_ws_for_scope(scope, ex)
            if not ws:
                return "WS Error"

            # Perp / Spot 구분
            if dex:  # HIP-3 perp
                px = ws.get_price(hip3_coin)  # 'xyz:COIN'
                return self.format_price_simple(float(px)) if px is not None else "..."
            else:
                if "/" in symbol:            # Spot pair
                    px = ws.get_spot_pair_px(symbol.upper())
                    return self.format_price_simple(float(px)) if px is not None else "..."
                else:                         # Perp(HL) → 'BTC'
                    px = ws.get_price(symbol.upper())
                    if px is not None:
                        return self.format_price_simple(float(px))
                    # 보조: base spot
                    px = ws.get_spot_px_base(symbol.upper())
                    return self.format_price_simple(float(px)) if px is not None else "..."

        except Exception as e:
            logger.info("[PRICE] %s fetch_price failed: %s", exchange_name, e)
            return "Error"

    async def fetch_status(
        self,
        exchange_name: str,
        symbol: str,
        need_balance: bool = True,  # [변경] balance 스킵 가능
        need_position: bool = True,    # 포지션 갱신 여부
    ) -> Tuple[str, str, float]:
        """
        - HL 카드의 collateral을 'HL+모든 DEX 합산 AV'로 표기
        - USDH는 항상 함께 표기(0일 때도)
        - 데이터 미수신 시 직전 캐시 유지로 깜빡임 방지
        """
        meta = self.manager.get_meta(exchange_name) or {}
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "📊 Position: N/A", "💰 Collateral: N/A", 0.0
        
        # 직전 캐시 불러오기 (없으면 기본값)
        last_pos_str, last_col_str, last_col_val = self._last_status.get(
            exchange_name,
            ("📊 Position: N/A", "💰 Collateral: N/A", self._last_collateral.get(exchange_name, 0.0)),
        )

        # 거래소 객체 없음 → 이전 값 유지(깜빡임 방지)
        if not ex:
            return last_pos_str, last_col_str, last_col_val

        # 1) mpdex (hl=False) 처리
        if not meta.get("hl", False):
            try:
                col_val = self._last_collateral.get(exchange_name, 0.0)
                if need_balance:
                    c = await ex.get_collateral()
                    col_val = float(c.get("total_collateral") or 0.0)
                    self._last_collateral[exchange_name] = col_val
                    self._last_balance_at[exchange_name] = time.monotonic()

                pos_str = last_pos_str
                if need_position:
                    native = self._to_native_symbol(exchange_name, symbol)
                    pos = await ex.get_position(native)
                    pos_str = "📊 Position: N/A"
                    if pos and float(pos.get("size") or 0.0) != 0.0:
                        side_raw = str(pos.get("side") or "").lower()
                        side = "LONG" if side_raw == "long" else "SHORT"
                        size = float(pos.get("size") or 0.0)
                        pnl = float(pos.get("unrealized_pnl") or 0.0)
                        side_color = "green" if side == "LONG" else "red"
                        pnl_color = "green" if pnl >= 0 else "red"
                        pos_str = f"📊 [{side_color}]{side}[/] {size:.5f} | PnL: [{pnl_color}]{pnl:,.5f}[/]"

                col_str = f"💰 Collateral: {col_val:,.2f} USDC"
                self._last_status[exchange_name] = (pos_str, col_str, col_val)
                return pos_str, col_str, col_val
            
            except Exception as e:
                logger.info(f"[{exchange_name}] non-HL fetch_status error: {e}")
                # 실패 시에도 이전 값을 그대로 반환(깜빡임 방지)
                return last_pos_str, last_col_str, last_col_val
            
        # 2) Hyperliquid (WebSocket 기반)
        dex, hip3_coin = _parse_hip3_symbol(symbol)
        scope = dex if dex else "hl"
        try:
            ws = await self._get_ws_for_scope(scope, ex)
        except Exception:
            ws = None

         # [핵심 변경] 기본은 '직전 캐시 그대로'
        pos_str: str = last_pos_str
        col_val: float = last_col_val
        col_str: str = (
            last_col_str
            if last_col_str
            else "💰 Account Value: [red]PERP[/] "
                 f"{last_col_val:,.1f} USDC | [cyan]SPOT[/] 0.0 USDH, 0.0 USDC"
        )
        
        if need_position and ws:
            key = "hl" if scope == "hl" else scope
            positions = ws.get_positions_by_dex(key) or {}
            search = hip3_coin.upper() if dex else symbol.upper()
            p = positions.get(search)
            if p and p.get("size", 0) > 0:
                size = float(p["size"])
                side = "LONG" if p["side"] == "long" else "SHORT"
                upnl = float(p.get("upnl") or 0.0)
                side_color = "green" if side == "LONG" else "red"
                pnl_color = "green" if upnl >= 0 else "red"
                pos_str = f"📊 [{side_color}]{side}[/] {size:g} | PnL: [{pnl_color}]{upnl:,.1f}[/]"
            else:
                # 포지션이 진짜 0일 때만 N/A로 갱신. (데이터 미도착으로 None인 경우는 위에서 캐시 유지)
                pos_str = "📊 Position: N/A"

         # (b) 담보(계정가치) 갱신이 필요한 틱에만 담보 재계산
        if need_balance:
            try:
                total_av, usdh, usdc = await self._get_hl_total_account_value_and_usdh(ex)
                col_val = float(total_av)  # comment: 헤더 합계용(여전히 PERP AV)
                # comment: [CHG] 표시 포맷: PERP(USDC) | SPOT USDH, USDC
                col_str = (
                    f"💰 Account Value: [red]PERP[/] {col_val:,.1f} USDC | "
                    f"[cyan]SPOT[/] {float(usdh):,.1f} USDH, {float(usdc):,.1f} USDC"
                )
            except Exception as e:
                logger.info(f"[{exchange_name}] HL agg collateral failed: {e}")
                # 실패: 캐시 유지

        # 캐시 갱신(이번 틱에서 실제로 갱신된 값만 반영됨)
        self._last_status[exchange_name] = (pos_str, col_str, col_val)
        self._last_collateral[exchange_name] = col_val

        return pos_str, col_str, col_val
    
    async def execute_order(
        self,
        exchange_name: str,
        symbol: str,
        amount: float,
        order_type: str,  # 'market' or 'limit'
        side: str,        # 'buy' or 'sell'
        price: Optional[float] = None,
        reduce_only: bool = False,  # NEW: reduceOnly 플래그
        client_id: Optional[str] = None,
    ) -> dict:
        meta = self.manager.get_meta(exchange_name) or {}
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")
        
        # 1) mpdex
        if not meta.get("hl", False):
            native = self._to_native_symbol(exchange_name, symbol)
            if order_type == "limit":
                if price is None:
                    raise RuntimeError(f"{exchange_name} limit order requires price")
                res = await ex.create_order(native, side, amount, price=price)
            else:
                res = await ex.create_order(native, side, amount)
            oid = self._extract_order_id(res)
            return {"id": oid, "info": res}
        
        # HL: 통합 raw 경로로 일원화
        want_frontend = bool(meta.get("frontend_market", False))
        logger.info("[ORDER] ex=%s sym=%s type=%s side=%s price=%s reduce_only=%s want_frontend=%s",
                    exchange_name, symbol, order_type, side, price, reduce_only, want_frontend)

        await self.ensure_hl_max_leverage_auto(exchange_name, symbol)

        # 통합 raw 호출(메인/HIP‑3 자동 분기)
        return await self._hl_create_order_unified(
            ex=ex,
            exchange_name=exchange_name,
            symbol=symbol,
            side=side,
            amount=amount,
            order_type=order_type,
            price=price,
            reduce_only=reduce_only,
            want_frontend=want_frontend,
            time_in_force=None,
            client_id=client_id,
        )
    
    async def close_position(
        self,
        exchange_name: str,
        symbol: str,
        price_hint: Optional[float] = None
    ) -> Optional[dict]:
        """
        현재 포지션을 반대 방향 시장가(reduceOnly=True)로 청산합니다.
        price_hint가 없으면 해당 거래소에서 last를 보조조회합니다.
        포지션이 없으면 None 반환.
        """
        meta = self.manager.get_meta(exchange_name) or {}
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")

        # 1) mpdex: 라이브러리 close_position 사용
        if not meta.get("hl", False):
            try:
                native = self._to_native_symbol(exchange_name, symbol)
                pos = await ex.get_position(native)
                if not pos or float(pos.get("size") or 0.0) == 0.0:
                    logger.info("[CLOSE] %s non-HL: no position", exchange_name)
                    return None
                res = await ex.close_position(native, pos)
                oid = self._extract_order_id(res)
                return {"id": oid, "info": res}
            except Exception as e:
                logger.info(f"[CLOSE] non-HL {exchange_name} failed: {e}")
                raise
        

        # 2) HL: HIP-3(dex:COIN) 여부로 분기
        dex, hip3_coin = _parse_hip3_symbol(symbol)
        want_frontend = bool(meta.get("frontend_market", False))

        if dex:
            # HIP-3: clearinghouseState(user+dex)로 포지션 조회
            user_addr = self._hl_user_address(ex)
            state = await self._hl_get_user_state(ex, dex, user_addr)
            hip3_pos = self._hl_parse_position_from_state(state or {}, hip3_coin)
            if not hip3_pos or float(hip3_pos.get("size") or 0.0) == 0.0:
                logger.info("[CLOSE] %s HIP3 %s: no position", exchange_name, hip3_coin)
                return None

            size = float(hip3_pos["size"])
            side_now = str(hip3_pos.get("side") or "long").lower()
            close_side = "sell" if side_now == "long" else "buy"
            amount = abs(size)

            # 가격 확보: hint → 없으면 metaAndAssetCtxs(dex)에서 markPx 기반
            try:
                px_base = await self._hl_pick_price(ex, dex, hip3_coin, price_hint)
            except Exception as e:
                logger.error("[CLOSE] %s HIP3 %s price fetch failed: %s", exchange_name, hip3_coin, e)
                raise

            logger.info("[CLOSE] %s HIP3 %s: %s %.10f → %s %.10f @ market",
                        exchange_name, hip3_coin, side_now.upper(), size, close_side.upper(), amount)

            # 통합 raw 호출(시장가 + reduceOnly=True)
            order = await self._hl_create_order_unified(
                ex=ex,
                exchange_name=exchange_name,
                symbol=hip3_coin,              # 'dex_lower:COIN_UPPER'
                side=close_side,
                amount=amount,
                order_type="market",
                price=px_base,                 # 힌트 전달(내부에서 슬리피지 적용)
                reduce_only=True,
                want_frontend=want_frontend,
                time_in_force=None,
                client_id=None,
            )
            return order
        
        # 3) 일반 HL(자체 퍼프): 기존 로직(positions → reduceOnly 시장가)
        # 포지션 조회
        pos = await ex.fetch_positions([f"{symbol}/USDC:USDC"])
        if not pos or not pos[0]:
            logger.info("[CLOSE] %s: no position", exchange_name)
            return None

        p = pos[0]
        try:
            size = float(p.get("contracts") or 0)
        except Exception:
            size = 0.0
        if size == 0:
            logger.info("[CLOSE] %s: already zero", exchange_name)
            return None

        cur_side = "long" if p.get("side") == "long" else "short"
        close_side = "sell" if cur_side == "long" else "buy"
        amount = abs(size)

        # 가격 확보: hint → 실패 시 fetch_ticker last
        px: Optional[float] = None
        if price_hint is not None:
            try:
                px = float(price_hint)
            except Exception:
                px = None
        if px is None:
            try:
                t = await ex.fetch_ticker(f"{symbol}/USDC:USDC")
                px = float(t.get("last"))
            except Exception as e:
                logger.error(f"[CLOSE] {exchange_name} price fetch failed: {e}")
                raise

        logger.info("[CLOSE] %s: %s %.10f → %s %.10f @ market",
                    exchange_name, cur_side.upper(), size, close_side.upper(), amount)
        # 통합 raw 호출(시장가 + reduceOnly=True)
        order = await self._hl_create_order_unified(
            ex=ex,
            exchange_name=exchange_name,
            symbol=symbol,
            side=close_side,
            amount=amount,
            order_type="market",
            price=px,
            reduce_only=True,
            want_frontend=want_frontend,
            time_in_force=None,
            client_id=None,
        )
        return order