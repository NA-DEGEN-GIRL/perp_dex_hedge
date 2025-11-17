# trading_service.py
import logging
import time
from typing import Tuple, Optional, Dict, Any
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
    """
    UI에서 거래소(ccxt) 호출을 공통 처리:
    - fetch_hl_price(symbol) : hl=True 거래소 중 하나에서 현재가 1회 조회
    - fetch_status(name, symbol) : 포지션/담보 조회 문자열 + 수치 반환
    - execute_order(...)     : 주문 실행(시장가 price None이면 last로 보정 시도)
    - is_configured(name)    : 연결/설정 여부
    - is_hl(name)            : hl 엔진 여부
    """
    def __init__(self, manager: ExchangeManager):
        self.manager = manager
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



    # [추가] 가격 소수자릿수(px decimals) 조회 유틸: metaAndAssetCtxs 캐시 기반
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

    # [추가] HL 가격맵 캐시를 특정 dex(또는 메인 HL)에 대해 1회 갱신
    async def refresh_hl_cache_for_dex(self, dex: Optional[str] = None, ttl: float = 3.0) -> None:
        """
        첫 번째 HL 거래소에서만 metaAndAssetCtxs(dex?)를 호출하여
        self._hl_px_cache_by_dex[dex or 'HL'] = {'ts': now, 'map': {...}} 형태로 갱신.
        """
        ex = self.manager.first_hl_exchange()
        if not ex:
            return

        cache_key = dex if dex else "HL"
        ent = self._hl_px_cache_by_dex.get(cache_key, {})
        now = time.monotonic()
        # 너무 잦은 호출 방지(절반 TTL 안에서는 스킵)
        if ent and (now - float(ent.get("ts", 0.0))) < (ttl * 0.5):
            return

        px_map = await self._hl_price_map(ex, dex)
        if px_map:
            self._hl_px_cache_by_dex[cache_key] = {"ts": now, "map": px_map}

    # [추가] 캐시에서만 가격 문자열 조회(네트워크 호출 없음)
    def get_cached_hl_price(self, symbol: str, dex_hint: Optional[str] = None) -> Optional[str]:
        """
        - 메인: symbol='BTC' → cache['HL']['map']['BTC']
        - HIP-3: dex_hint='xyz', symbol='BTC' → cache['xyz']['map']['xyz:BTC']
        캐시에 없으면 None 리턴(호출측에서 refresh_hl_cache_for_dex로 보강).
        """
        dex, hip3_coin = _parse_hip3_symbol(symbol)
        if dex is None and dex_hint and dex_hint != "HL":
            dex = dex_hint.lower()
            hip3_coin = f"{dex}:{symbol.upper()}"

        cache_key = dex if dex else "HL"
        ent = self._hl_px_cache_by_dex.get(cache_key)
        if not ent:
            return None

        px_map = ent.get("map", {}) or {}
        key = hip3_coin if dex else symbol.upper()
        px = px_map.get(key)
        if px is None:
            return None
        try:
            return f"{float(px):,.2f}"
        except Exception:
            return None

    # [추가] 외부에서 dex 별 quote를 보장적으로 가져올 수 있는 래퍼(최초 1회 네트워크)
    async def ensure_quote_for_dex(self, dex: Optional[str]) -> str:
        """
        - dex=None → 'HL' 범위
        - 이미 캐시에 있으면 캐시 리턴, 없으면 첫 HL 거래소를 통해 1회 조회 후 캐시.
        """
        ex = self.manager.first_hl_exchange()
        if not ex:
            return "USDC"
        return await self._fetch_dex_quote(ex, dex)

    async def _hl_get_spot_usdh(self, ex) -> float:
        """
        spotClearinghouseState(user)에서 USDH 잔고(total)를 찾아 반환.
        실패/없음이면 0.0
        """
        user = self._hl_user_address(ex)
        if not user:
            return 0.0
        try:
            state = await ex.publicPostInfo({"type": "spotClearinghouseState", "user": user})
            if not isinstance(state, dict):
                return 0.0
            balances = state.get("balances") or []
            for b in balances:
                try:
                    if isinstance(b, dict) and str(b.get("coin", "")).upper() == "USDH":
                        return float(b.get("total") or 0.0)
                except Exception:
                    continue
            return 0.0
        except Exception as e:
            logger.info("[HL] spotClearinghouseState failed: %s", e)
            return 0.0

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
            if isinstance(resp, list):
                for e in resp:
                    if isinstance(e, dict) and e.get("name"):
                        try:
                            names.append(str(e["name"]).lower())
                        except Exception:
                            continue
            # 중복 제거 + 정렬 + 캐시
            self._perp_dex_list = sorted(set(names))
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

    # HL Info API로 user 상태 가져오기 (clearinghouseState)
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

    async def _hl_sum_account_value(self, ex) -> float:
        """
        HL 전체(메인 + 모든 HIP-3 dex)의 accountValue 합계를 반환.
        - user 주소: _hl_user_address(ex)
        - dex 목록: 캐시(self._perp_dex_list) 사용. 없을 경우 최초 1회 fetch 후 캐시.
        """
        user = self._hl_user_address(ex)
        if not user:
            return 0.0

        # perpDexs 캐시 준비(최초 1회만 네트워크 호출)
        if self._perp_dex_list is None:
            try:
                await self.fetch_perp_dexs()
            except Exception:
                self._perp_dex_list = []

        total = 0.0
        try:
            # 메인(HL) + 캐시된 dex
            all_scopes = [None] + (self._perp_dex_list or [])
            for d in all_scopes:
                st = await self._hl_get_user_state(ex, d, user)
                await asyncio.sleep(0.25)
                if not st or not isinstance(st, dict):
                    continue
                ms = st.get("marginSummary", {}) or {}
                av = ms.get("accountValue")
                try:
                    if av is not None:
                        total += float(av)
                except Exception:
                    continue
        except Exception:
            pass
        return total

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

    # [추가] 통합 가격 API: HL은 기존 fetch_hl_price, 비-HL은 mpdex.get_mark_price 사용
    async def fetch_price(self, exchange_name: str, symbol: str, dex_hint: Optional[str] = None) -> str:
        """
        카드별 가격 조회(통합):
        - HL: fetch_hl_price(symbol, dex_hint) 사용(내부 metaAndAssetCtxs 캐시)
        - 비-HL(mpdex): native 심볼로 변환 후 exchange.get_mark_price(native)
        반환은 "12,345.67" 형태 문자열 또는 "Error"/"N/A".
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "N/A"
        meta = self.manager.get_meta(exchange_name) or {}

        try:
            if meta.get("hl", False):
                # HL: dex_hint가 있으면 HIP‑3, 없으면 메인
                return await self.fetch_hl_price(symbol, dex_hint=dex_hint)
            else:
                # 비-HL: mpdex 클라이언트 get_mark_price(native)
                native = self._to_native_symbol(exchange_name, symbol)
                px = await ex.get_mark_price(native)
                return f"{float(px):,.2f}"
        except Exception as e:
            logger.info("[PRICE] %s fetch_price failed: %s", exchange_name, e)
            return "Error"

    async def fetch_hl_price(self, symbol: str, dex_hint: Optional[str] = None) -> str:
        """
        HL 가격 조회(캐시 3초):
        - HIP‑3: symbol='xyz:XYZ100' 또는 dex_hint='xyz' + symbol='XYZ100'
        - 메인: symbol='BTC'
        """
        ex = self.manager.first_hl_exchange()
        if not ex:
            return "N/A"
        try:
            dex, hip3_coin = _parse_hip3_symbol(symbol)
            if dex is None and dex_hint and dex_hint != "HL":
                dex = dex_hint.lower()
                hip3_coin = f"{dex}:{symbol.upper()}"

            # 캐시 키: 'HL' 또는 dex
            cache_key = dex if dex else "HL"
            ent = self._hl_px_cache_by_dex.get(cache_key, {})
            now = time.monotonic()
            ttl = 3.0

            if not ent or (now - ent.get("ts", 0.0) >= ttl):
                px_map = await self._hl_price_map(ex, dex)
                if px_map:
                    self._hl_px_cache_by_dex[cache_key] = {"ts": now, "map": px_map}
                    ent = self._hl_px_cache_by_dex[cache_key]

            px_map = ent.get("map", {}) if ent else {}
            if dex:  # HIP‑3
                px = px_map.get(hip3_coin)
            else:    # 메인
                px = px_map.get(symbol.upper())

            if px is not None:
                return f"{px:,.2f}"

            # 한 번 더 즉시 갱신 시도(신규/갱신 지연 대비)
            px_map2 = await self._hl_price_map(ex, dex)
            if px_map2:
                self._hl_px_cache_by_dex[cache_key] = {"ts": time.monotonic(), "map": px_map2}
                if dex:
                    px = px_map2.get(hip3_coin)
                else:
                    px = px_map2.get(symbol.upper())
                if px is not None:
                    return f"{px:,.2f}"

            return "Error"
        except Exception as e:
            logger.error("HL price fetch error: %s", e, exc_info=True)
            return "Error"

    async def fetch_status(
        self,
        exchange_name: str,
        symbol: str,
        need_balance: bool = True,  # [변경] balance 스킵 가능
        need_position: bool = True,    # 포지션 갱신 여부
    ) -> Tuple[str, str, float]:
        """
        returns: (pos_str, col_str, col_val)
        - need_balance=False면 balance를 건너뛰고 캐시 last_collateral을 사용
        - 429 백오프 중이면 캐시를 즉시 반환
        """
        meta = self.manager.get_meta(exchange_name) or {}
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "📊 Position: N/A", "💰 Collateral: N/A", 0.0
        
        # [공통] 직전 캐시
        last_pos_str, last_col_str, last_col_val = self._last_status.get(
            exchange_name, ("📊 Position: N/A", "💰 Collateral: N/A", self._last_collateral.get(exchange_name, 0.0))
        )

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
                # 실패 시 캐시 반환(표시에는 Stale 명시)
                last_col_val = self._last_collateral.get(exchange_name, 0.0)
                pos_str = last_pos_str
                col_str = f"💰 Collateral: {last_col_val:,.2f} USDC (Stale)"
                return pos_str, col_str, last_col_val
            
        # 2) HL
        now = time.monotonic()
        if now < self._cooldown_until.get(exchange_name, 0.0):
            cached = self._last_status.get(exchange_name)
            if cached:
                return cached
            last_col_val = self._last_collateral.get(exchange_name, 0.0)
            return "📊 Position: N/A", f"💰 Collateral: {last_col_val:,.2f} USDC (Cooldown)", last_col_val

        try:
            # 담보(USDC 합계 + USDH spot) — need_balance일 때만 네트워크 호출
            col_val = self._last_collateral.get(exchange_name, 0.0)
            if need_balance:
                av_sum = await self._hl_sum_account_value(ex)
                col_val = float(av_sum)
                self._last_collateral[exchange_name] = col_val
                self._last_balance_at[exchange_name] = now

            usdh_val = self._spot_usdh_by_ex.get(exchange_name, 0.0)
            if need_balance:
                usdh_val = await self._hl_get_spot_usdh(ex)
                self._spot_usdh_by_ex[exchange_name] = usdh_val

            # 포지션 — need_position일 때만 네트워크 호출
            pos_str = last_pos_str
            if need_position:
                dex, hip3_coin = _parse_hip3_symbol(symbol)
                coin_key = hip3_coin if dex else symbol.upper()
                user_addr = self._hl_user_address(ex)
                state = await self._hl_get_user_state(ex, dex, user_addr)
                pos_data = self._hl_parse_position_from_state(state or {}, coin_key)

                pos_str = "📊 Position: N/A"
                if pos_data:
                    side = "LONG" if pos_data["side"] == "long" else "SHORT"
                    size = float(pos_data["size"])
                    pnl  = float(pos_data["unrealized_pnl"])
                    side_color = "green" if side == "LONG" else "red"
                    pnl_color  = "green" if pnl >= 0 else "red"
                    pos_str = f"📊 [{side_color}]{side}[/] {size:.5f} | PnL: [{pnl_color}]{pnl:,.2f}[/]"

            col_str = f"💰 Collateral: {col_val:,.2f} USDC"
            col_str += f" | USDH {usdh_val:,.2f}"

            self._last_status[exchange_name] = (pos_str, col_str, col_val)
            self._backoff_sec[exchange_name] = 0.0
            return pos_str, col_str, col_val

        except Exception as e:
            logger.error(f"[{exchange_name}] fetch_status error: {e}", exc_info=True)
            if self._is_rate_limited(e):
                current = self._backoff_sec.get(exchange_name, 2.0) or 2.0
                new_backoff = min(current * 2.0, 15.0)
                self._backoff_sec[exchange_name] = new_backoff
                self._cooldown_until[exchange_name] = now + new_backoff

            # 실패 시 캐시 반환
            last_col_val = self._last_collateral.get(exchange_name, 0.0)
            last_usdh_val = self._spot_usdh_by_ex.get(exchange_name, 0.0)
            pos_str = last_pos_str
            col_str = f"💰 Collateral: {last_col_val:,.2f} USDC (Stale)"
            if last_usdh_val > 0:
                col_str += f" | USDH {last_usdh_val:,.2f}"
            return pos_str, col_str, last_col_val
    
    
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