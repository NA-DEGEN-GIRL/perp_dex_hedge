# trading_service.py
import time
from typing import Tuple, Optional, Dict, Any, Union
from hl_ws.hl_ws_client import HLWSClientRaw, http_to_wss
from core import ExchangeManager
import asyncio
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN
from superstack_payload import get_superstack_payload
import aiohttp  # comment: superstack 전송에 필요
from eth_account import Account
from hl_sign import sign_l1_action as hl_sign_l1_action
import os
import logging
from logging.handlers import RotatingFileHandler

# 모듈 전용 로거
logger = logging.getLogger(__name__)
def _ensure_ts_logger():
    """
    trading_service.py 전용 파일 핸들러 설정.
    - 기본 파일: ./ts.log (절대경로로 기록)
    - 기본 레벨: INFO
    - 기본 전파: False (루트 핸들러로 중복 기록 방지)
    환경변수:
      PDEX_TS_LOG_FILE=/path/to/ts.log
      PDEX_TS_LOG_LEVEL=DEBUG|INFO|...
      PDEX_TS_LOG_CONSOLE=0|1
      PDEX_TS_PROPAGATE=0|1
    """
    # 이미 붙어 있으면 중복 추가 금지
    if getattr(logger, "_ts_logger_attached", False):
        return

    lvl_name = os.getenv("PDEX_TS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, lvl_name, logging.INFO)
    log_file = os.path.abspath(os.getenv("PDEX_TS_LOG_FILE", "ts.log"))
    to_console = os.getenv("PDEX_TS_LOG_CONSOLE", "0") == "1"
    propagate = os.getenv("PDEX_TS_PROPAGATE", "0") == "1"

    # 포맷
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    # 기존에 동일 파일 핸들러가 붙어 있으면 제거(핫리로드 대비)
    to_remove = []
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(getattr(h, "baseFilename", "")) == log_file:
                    to_remove.append(h)
            except Exception:
                pass
    for h in to_remove:
        logger.removeHandler(h)

    # 파일 핸들러
    fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.NOTSET)  # 핸들러는 모듈 로거 레벨만 따르도록
    logger.addHandler(fh)

    # 콘솔 핸들러(옵션)
    if to_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.NOTSET)
        logger.addHandler(sh)

    # 모듈 로거 레벨/전파 설정
    logger.setLevel(level)
    logger.propagate = propagate

    # 중복 방지 플래그
    logger._ts_logger_attached = True

    # 1회 안내 로그(최초 설정 확인용)
    logger.info("[TS-LOG] attached ts logger level=%s file=%s console=%s propagate=%s",
                lvl_name, log_file, to_console, propagate)

# 모듈 import 시점에 전용 핸들러를 붙인다.
_ensure_ts_logger()
try:
    from exchange_factory import symbol_create
except Exception:
    symbol_create = None
    logger.warning("[mpdex] exchange_factory.symbol_create 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")

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

def _strip_0x(h: str | None) -> str:
    if not isinstance(h, str):
        return ""
    return h[2:] if h.startswith(("0x", "0X")) else h

class TradingService:
    def __init__(self, manager: ExchangeManager):
        self.manager = manager
        
        self._ws_by_scope: Dict[str, HLWSClientRaw] = {}
        self._ws_scope_locks: Dict[str, asyncio.Lock] = {}

        #  상태/쿨다운 캐시
        self._last_collateral: dict[str, float] = {}
        self._last_status: dict[str, Tuple[str, str, float]] = {}  # (pos_str, col_str, col_val)
        self._last_balance_at: dict[str, float] = {}               # balance 최근 호출 시각
        
        self._leverage_applied: Dict[tuple[str, str], bool] = {} #  레버리지 적용 여부 캐시: (exchange_name, coin) -> bool
        self._perp_dex_list: Optional[list[str]] = None # HL 빌더 DEX 목록 캐시(앱 시작 시 1회)
        self._hl_sz_dec_cache: Dict[tuple[str, str], int] = {} # (dex_or_HL, coin_key) -> szDecimals 

        self._leverage_inflight: set[tuple[str, str]] = set()          # (exchange_name, coin_key) in-flight 가드
        self._leverage_last_check: dict[tuple[str, str], float] = {}   # 마지막 체크 시각(스로틀)
        self._leverage_check_interval: float = 5.0                     # 스로틀 간격(초) - 필요시 조정
        
        # [추가] 주소별 합산 AV 캐시: key="address" (또는 exchange fallback)
        #  값: {"ts": monotonic, "av": float, "usdh": float}
        self._agg_av_cache: Dict[str, Dict[str, float]] = {}
        self._agg_refresh_secs: float = 1.0  # comment: 합산 재계산 최소 주기(초)
        
        self._info_ex_cached = None              # comment: 최초 접근 시 first_hl_exchange()로 채움
        self._asset_idx_cache: Dict[str, int] = {}     # key = f"{scope}|{coin_upper}"
        
        self._hl_asset_meta_cache: Dict[tuple[str, str], Dict[str, Any]] = {}
        #self._hl_asset_meta_ttl: float = 30.0  # comment: 캐시 TTL(초). 필요 시 조정
        logger.info("[TS] init (effective=%s handlers=%d)",
                    logging.getLevelName(logger.getEffectiveLevel()),
                    len(logging.getLogger().handlers))

    def _hl_vault_address(self, ex) -> Optional[str]:
        """
        core.py에서 is_sub=True인 HL 카드에 한해 ex.options에 저장된 vaultAddress를 읽는다.
        - ex.options['vaultAddress'] (평탄화된 위치, 권장)
        - (fallback) ex.options['options']['vaultAddress']
        반환: '0x..'(소문자) 또는 None
        """
        try:
            opts: dict = getattr(ex, "options", {}) or {}
            v = opts.get("vaultAddress")
            if not v:
                inner = opts.get("options")
                if isinstance(inner, dict):
                    v = inner.get("vaultAddress")
            return str(v).lower() if v else None
        except Exception:
            return None
    
    def get_display_builder_fee(self, exchange_name: str, dex: Optional[str], order_type: str) -> Optional[int]:
        """
        HL 카드 우상단 'FEE:' 표기를 위한 표시용 수수료 선택.
        - dex: 'xyz' | 'flx' | 'vntl' | None(HL)
        - order_type: 'market' | 'limit'
        반환: feeInt(int) 또는 None(설정 없음)
        """
        try:
            ex = self.manager.get_exchange(exchange_name)
            if not ex:
                return None
            fee_int, _src, _pair = self._pick_fee_with_reason(ex, dex, order_type)
            return int(fee_int) if fee_int is not None else None
        except Exception:
            return None

    def _hl_now_ms(self) -> int:
        """ex.milliseconds() 대체."""
        return int(time.time() * 1000)

    def _hl_sign_l1_action(self, ex: Any, action: Dict[str, Any], nonce: int, vault_address: Optional[str]) -> dict:
        """
        공식 SDK 서명 모듈 사용.
        반환: {'r':'0x..','s':'0x..','v':27/28}
        """
        opt = getattr(ex, "options", {}) or {}
        priv = opt.get("privateKey") or opt.get("private_key")
        if not priv:
            raise RuntimeError("HL signing requires privateKey in ex.options.")
        # wallet 객체 생성
        wallet = Account.from_key(bytes.fromhex(_strip_0x(priv)))
        is_mainnet = not bool(opt.get("sandboxMode", False))
        # 공식 SDK sign_l1_action 호출 (expires_after는 사용 안하면 None)
        signature = hl_sign_l1_action(wallet, action, vault_address, nonce, None, is_mainnet)
        return signature

    async def _send_hl_exchange(self, ex: Any, action: Dict[str, Any], *, platform: str) -> Dict[str, Any]:
        """
        platform == 'superstack' → provider 서명
        else → _hl_now_ms + _hl_sign_l1_action 로 payload 생성
        """
        if platform == "superstack":
            opt = getattr(ex, "options", {}) or {}
            api_key = opt.get("apiKey")
            if not api_key:
                raise RuntimeError("superstack apiKey is missing in options.")
            payload: Union[Dict[str, Any], str] = await get_superstack_payload(api_key=api_key, action=action)
        else:
            try:
                nonce = self._hl_now_ms()
                vault_addr = self._hl_vault_address(ex)   # ← 추가
                signature = self._hl_sign_l1_action(ex, action, nonce, vault_address=vault_addr)
                payload = {"action": action, "nonce": nonce, "signature": signature}
                if vault_addr:
                    payload["vaultAddress"] = vault_addr
            except Exception as e:
                logger.info(f"{e}")
                raise RuntimeError("payload error")


        # 전송 (UA 제거 상태 유지)
        base = "https://api.hyperliquid.xyz"
        url = f"{base}/exchange"
        async with aiohttp.ClientSession() as session:
            if isinstance(payload, (str, bytes)):
                async with session.post(url, headers={"Content-Type": "application/json"}, data=payload) as res:
                    await self._raise_if_bad_response(res)
                    return await res.json()
            else:
                async with session.post(url, headers={"Content-Type": "application/json"}, json=payload) as res:
                    await self._raise_if_bad_response(res)
                    return await res.json()

    async def _raise_if_bad_response(self, resp: aiohttp.ClientResponse) -> None:
        """HTTP 응답 상태 코드가 2xx가 아닐 경우 예외를 발생시킵니다."""
        if 200 <= resp.status < 300:
            return
        
        ctype = resp.headers.get("content-type", "")
        text = await resp.text()

        if "text/html" in ctype.lower():
            # HTML 응답은 보통 WAF나 IP 차단 문제일 가능성이 높음
            raise RuntimeError(f"Request blocked (HTTP {resp.status} HTML). Likely WAF/IP whitelist issue. Body preview: {text[:300]}...")
        
        # JSON 에러 포맷이 일정치 않으므로 원문을 그대로 노출
        raise RuntimeError(f"HTTP {resp.status}: {text[:400]}...")
        
    async def _info_post_http(self, payload: dict, timeout_sec: float = 6.0) -> dict | list | None:
        try:
            http_base = self._sanitize_http_base()
            url = f"{http_base}/info"
            to = aiohttp.ClientTimeout(total=max(1.0, float(timeout_sec)))
            async with aiohttp.ClientSession(timeout=to) as sess:
                async with sess.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except Exception as e:
            logger.debug(f"[_info_post_http] {payload.get('type')} request failed: {e}")
            return None

    def _normalize_meta_and_asset_ctxs(self, obj: dict | list | None) -> list[dict]:
        """
        반환: list[dict], 각 dict는 적어도 'universe': list[...]를 포함.
        metaAndAssetCtxs/allPerpMetas 응답의 다양한 포맷을 수용.
        """
        if obj is None:
            return []
        if isinstance(obj, list):
            # 이미 [{...}, {...}] 형태일 수 있음
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            # ccxt 래핑형: {"response":{"data":[...]}}
            data = obj.get("response", {}).get("data")
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
            # 직접형: {"data":[...]}
            d2 = obj.get("data")
            if isinstance(d2, list):
                return [x for x in d2 if isinstance(x, dict)]
            # 키 명시형: {"metaAndAssetCtxs":[...]}, {"allPerpMetas":[...]}
            for k in ("metaAndAssetCtxs", "allPerpMetas", "list"):
                v = obj.get(k)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        return []
    
    async def _hl_asset_meta(
        self,
        dex: Optional[str],
        coin_key: str
    ) -> tuple[int, Optional[int], bool]:
        """
        metaAndAssetCtxs에서 coin_key에 해당하는
        - sz_decimals(int)
        - max_leverage(Optional[int])
        - only_isolated(bool)
        를 한 번에 반환. 내부 TTL 캐시 사용.
        coin_key:
          - 메인 HL: 'BTC' (UPPER)
          - HIP-3:   'xyz:XYZ100' (원문)
        """
        scope = (dex if dex else "HL")
        ckey = (scope, coin_key)
        now = time.monotonic()

        # 1) 캐시 히트(유효 TTL 내)
        meta_cached = self._hl_asset_meta_cache.get(ckey)
        if meta_cached: # and (now - float(meta_cached.get("ts", 0.0)) < self._hl_asset_meta_ttl):
            return (
                int(meta_cached.get("sz", 0)),
                meta_cached.get("max_lev"),
                bool(meta_cached.get("only_iso", False)),
            )

        # 2) HTTP 조회(metaAndAssetCtxs)
        payload = {"type": "metaAndAssetCtxs"}
        if dex:
            payload["dex"] = dex
        raw = await self._info_post_http(payload)
        lst = self._normalize_meta_and_asset_ctxs(raw)
        # 기본값
        szd: int = 0
        max_lev: Optional[int] = None
        only_iso: bool = False

        if lst:
            universe = (lst[0] or {}).get("universe", []) or []
            for a in universe:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if not name or a.get("isDelisted", False):
                    continue
                # 메인: UPPER 비교, HIP-3: 원문 비교
                key_cmp = name.upper() if not dex else name
                if key_cmp != coin_key:
                    continue

                # szDecimals
                try:
                    szd = int(a.get("szDecimals"))
                except Exception:
                    szd = 0

                # maxLeverage
                try:
                    max_lev_raw = a.get("maxLeverage")
                    max_lev = int(float(max_lev_raw)) if max_lev_raw is not None else None
                except Exception:
                    max_lev = None

                # margin mode → only_isolated
                mmode = str(a.get("marginMode") or "").lower()
                only_iso = bool(a.get("onlyIsolated", False) or mmode in ("isolated", "strictisolated"))
                break

        # 3) 캐시 저장(역호환 캐시도 함께 갱신)
        self._hl_asset_meta_cache[ckey] = {"ts": now, "sz": szd, "max_lev": max_lev, "only_iso": only_iso}
        self._hl_sz_dec_cache[ckey] = szd  # comment: 기존 경로에서 참조할 수 있으므로 같이 갱신

        return szd, max_lev, only_iso

    async def _hl_sz_decimals(self, dex: Optional[str], coin_key: str) -> int:
        cache_key = (dex if dex else "HL", coin_key)
        # 기존 캐시 우선
        if cache_key in self._hl_sz_dec_cache:
            return self._hl_sz_dec_cache[cache_key]

        szd, _, _ = await self._hl_asset_meta(dex, coin_key)
        # _hl_asset_meta에서 _hl_sz_dec_cache도 갱신되지만, 명시적으로 반환값 사용
        return szd

    async def _get_max_leverage_unified(self, dex: Optional[str], coin_key: str) -> tuple[Optional[int], bool]:
        _, max_lev, only_iso = await self._hl_asset_meta(dex, coin_key)
        return max_lev, only_iso

    async def _fetch_all_perp_metas_http(self) -> list:
        """
        allPerpMetas를 /info(type='allPerpMetas')로 가져와
        list[dict(universe=...)] 형태로 반환합니다.
        """
        payload = {"type": "allPerpMetas"}  # <-- 수정: 'perpDexs' → 'allPerpMetas'
        raw = await self._info_post_http(payload)
        lst = self._normalize_meta_and_asset_ctxs(raw)  # <-- 수정: DEX 리스트 정규화가 아니라 메타 정규화 사용
        try:
            u0 = (lst[0] or {}).get("universe") if lst else None
            head = [str((a or {}).get("name") or "") for a in (u0 or [])[:10]]
            logger.info(f"[allPerpMetas] metas={len(lst)} universe0_len={len(u0 or [])} head={head}")
        except Exception as e:
            logger.info(f"[allPerpMetas] {e}")
                
        return lst

    def _is_hl_like(self, meta: dict) -> bool:
        try:
            if meta.get("hl", False):
                return True
            # core.py가 meta["exchange"]에 'superstack'을 넣어줌
            return str(meta.get("exchange", "")).lower() == "superstack"
        except Exception:
            return False

    def _info_ex(self):
        """
        meta/assetCtx 등 '조회 전용'은 전역 동일하므로 첫 번째 HL(ccxt)을 공용으로 사용.
        없으면 None 반환(호출부에서 처리).
        """
        try:
            if self._info_ex_cached and hasattr(self._info_ex_cached, "publicPostInfo"):
                return self._info_ex_cached
        except Exception:
            pass
        ex = self.manager.first_hl_exchange()
        self._info_ex_cached = ex if ex and hasattr(ex, "publicPostInfo") else None
        return self._info_ex_cached

    def _pick_fee_with_reason(
        self, ex, dex: Optional[str], order_type: str
    ) -> tuple[Optional[int], str, Optional[tuple[int, int]]]:
        """
        반환: (feeInt 또는 None, source 설명 문자열, 선택된 (limit,market) 페어 또는 None)

        정책(정정):
        - 메인 HL(dex is None): fee_rate만 적용
          * options.feeIntPair -> 사용
        - HIP-3 DEX(dex is not None): dex_fee_rate / xyz_fee_rate 등만 적용
          * options.dexFeeIntPairMap[dex]        -> 사용 (개별 DEX: xyz_fee_rate 등)
          * options.dexFeeIntPairDefault         -> 사용 (공통 DEX: dex_fee_rate)
          * (폴백 허용) options.feeIntPair       -> 사용 (설정 누락 시 마지막 보조)

        order_type: 'limit' → index=0, 'market' → index=1
        """
        try:
            opt = getattr(ex, "options", {}) or {}
            idx = 0 if str(order_type).lower() == "limit" else 1

            # 메인 HL: fee_rate만 사용
            if not dex:
                base_pair = opt.get("feeIntPair")
                if isinstance(base_pair, (list, tuple)) and len(base_pair) >= 2:
                    return int(base_pair[idx]), "hl:feeIntPair", (int(base_pair[0]), int(base_pair[1]))
                # 레거시 단일값 폴백
                if "feeInt" in opt:
                    v = int(opt.get("feeInt"))
                    return v, "hl:legacy:feeInt", (v, v)
                return None, "hl:none", None

            # HIP-3 DEX: 개별 → 공통 → (폴백) 기본 → 레거시
            # 1) 개별 DEX 페어 (xyz_fee_rate 등)
            pairs_map = opt.get("dexFeeIntPairMap") or {}
            if isinstance(pairs_map, dict):
                p = pairs_map.get(dex.lower())
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    return int(p[idx]), f"dex:{dex.lower()}_fee_rate", (int(p[0]), int(p[1]))

            # 2) 공통 DEX 페어 (dex_fee_rate)
            pair_def = opt.get("dexFeeIntPairDefault")
            if isinstance(pair_def, (list, tuple)) and len(pair_def) >= 2:
                return int(pair_def[idx]), "dex:dex_fee_rate", (int(pair_def[0]), int(pair_def[1]))

            # 3) (폴백 허용) 기본 페어 (fee_rate) - 설정 누락 보조용
            base_pair = opt.get("feeIntPair")
            if isinstance(base_pair, (list, tuple)) and len(base_pair) >= 2:
                return int(base_pair[idx]), "fallback:feeIntPair", (int(base_pair[0]), int(base_pair[1]))

        except Exception as e:
            logger.debug("[FEE] pick reason error: %s", e)

        return None, "none", None

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
    
    def _sanitize_http_base(self) -> str:
        # 강제
        http_base = "https://api.hyperliquid.xyz"
        return http_base

    def _round_to_tick(self, value: float, decimals: int, up: bool) -> Decimal:
        # comment: tick_decimals(= 6 - szDecimals)에 맞춰 BUY=상향, SELL=하향 정렬
        q = Decimal(f"1e-{decimals}") if decimals > 0 else Decimal("1")
        d = Decimal(str(value))
        return d.quantize(q, rounding=(ROUND_UP if up else ROUND_DOWN))

    async def fetch_perp_dexs(self) -> list[str]:
        if self._perp_dex_list is not None:
            return self._perp_dex_list

        try:
            raw = await self._info_post_http({"type": "perpDexs"})  # <-- 수정됨

            names: list[str] = []

            # 최상위가 리스트인 경우
            if isinstance(raw, list):
                for e in raw:
                    if isinstance(e, str):
                        names.append(e.strip().lower())
                    elif isinstance(e, dict):
                        # dict 항목이면 대표 키를 문자열로 뽑아 사용
                        v = e.get("name") or e.get("dex") or e.get("id")
                        if isinstance(v, str) and v.strip():
                            names.append(v.strip().lower())

            # dict인 경우: response.data / data / 최상위 키에서 리스트 탐색
            elif isinstance(raw, dict):
                # 우선 response.data 또는 data 찾기
                data = raw.get("response", {}).get("data", raw.get("data", raw)) or {}
                src_list = None
                for key in ("dexes", "names", "list", "perpDexs"):
                    v = (data or {}).get(key)
                    if isinstance(v, list):
                        src_list = v
                        break
                if src_list is None:
                    for key in ("dexes", "names", "list", "perpDexs"):
                        v = raw.get(key)
                        if isinstance(v, list):
                            src_list = v
                            break
                if src_list:
                    for e in src_list:
                        if isinstance(e, str):
                            names.append(e.strip().lower())
                        elif isinstance(e, dict):
                            v = e.get("name") or e.get("dex") or e.get("id")
                            if isinstance(v, str) and v.strip():
                                names.append(v.strip().lower())

            # [HARDEN] 최종적으로 문자열만 남기고 정규화
            names = [n for n in names if isinstance(n, str) and n]
            names = [n for n in names if n != "hl"]
            self._perp_dex_list = sorted(set(names))
            logger.info("[HIP3] perpDexs loaded: %s", self._perp_dex_list)
            return self._perp_dex_list

        except Exception as e:
            logger.info("[HIP3] fetch_perp_dexs (HTTP) failed: %s", e)
            self._perp_dex_list = []
            return self._perp_dex_list

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

    async def _hl_pick_price(self, ex, dex: Optional[str], coin_key: str, price_hint: Optional[float]) -> float:
        """
        우선순위: price_hint → WS 가격 → 0.0
        - dex가 None이면 HL(메인), 있으면 HIP-3
        - 메인(HL)에서는 perp 가격 없을 때 base spot 가격으로 보조
        """
        try:
            if price_hint is not None:
                return float(price_hint)
        except Exception:
            pass

        scope = dex if dex else "hl"
        ws = await self._get_ws_for_scope(scope, ex)
        if not ws:
            return 0.0

        try:
            if dex:
                # HIP-3: 'dex:COIN' 그대로
                px = ws.get_price(coin_key)
                return float(px) if px is not None else 0.0
            else:
                # 메인 HL: 'BTC'
                key = coin_key.upper()
                px = ws.get_price(key)
                if px is not None:
                    return float(px)
                # 보조: base spot
                px = ws.get_spot_px_base(key)
                return float(px) if px is not None else 0.0
        except Exception:
            return 0.0

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
                http_base = self._sanitize_http_base()
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
                logger.warning(f"_get_hl_total_account_value_and_usdh: {e}")
                # 개별 스코프 실패는 무시하고 계속 합산
                continue

        # 캐시 저장
        self._agg_av_cache[key] = {"ts": now, "av": total_av, "usdh": usdh, "usdc": usdc}
        return total_av, usdh, usdc

    async def _resolve_asset_index(self, coin_key: str) -> Optional[int]:
        """
        coin_key: 'BTC' 또는 'xyz:XYZ100'(원문/HIP-3)
        공식:
        - 메인 퍼프(meta_idx=0): asset = local_idx
        - 빌더 퍼프(meta_idx>=1): asset = 100000 + meta_idx*10000 + local_idx
        """
        dex, hip3 = _parse_hip3_symbol(coin_key) if ":" in coin_key else (None, coin_key)
        scope = (dex or "hl").lower()
        norm_coin = (hip3 if dex else coin_key.upper())
        cache_key = f"{scope}|{norm_coin}"
        if cache_key in self._asset_idx_cache:
            return self._asset_idx_cache[cache_key]

        metas_list = await self._fetch_all_perp_metas_http()
        if not metas_list:
            logger.info("[AIDX] allPerpMetas unavailable")
            return None

        try:
            for meta_idx, meta in enumerate(metas_list):
                uni = (meta or {}).get("universe")
                if not isinstance(uni, list):
                    continue
                offset = 0 if meta_idx == 0 else 100000 + meta_idx * 10000
                for local_idx, asset in enumerate(uni):
                    if not isinstance(asset, dict):
                        continue
                    name = str(asset.get("name") or "")
                    if not name or asset.get("isDelisted", False):
                        continue
                    cmp_key = name.upper() if scope == "hl" else name
                    if cmp_key != norm_coin:
                        continue
                    aidx = int(offset + local_idx)
                    self._asset_idx_cache[cache_key] = aidx
                    logger.debug("[AIDX] %s -> %d (meta_idx=%d local=%d name=%s)", cache_key, aidx, meta_idx, local_idx, name)
                    return aidx
            logger.info("[AIDX] not found for %s (scope=%s)", norm_coin, scope)
            return None
        except Exception as e:
            logger.debug("[AIDX] parse error: %s", e, exc_info=True)
            return None

    async def ensure_hl_max_leverage_auto(self, exchange_name: str, symbol: str) -> None:
        """
        HL 전용 통합 레버리지 보장:
        - 자산ID/레버리지는 모두 메타 기반으로 처리(메인/HIP‑3 동일)
        - 메인: coin_key='BTC' 등 UPPER, HIP‑3: 'xyz:XYZ100' 원문
        - max 레버리지를 1회만 updateLeverage로 적용(격리 여부: 메타 기준)
        """
        ex = self.manager.get_exchange(exchange_name)
        meta = self.manager.get_meta(exchange_name) or {}  # [CHG] meta 캐싱
        
        # [CHG] is_hl_like 사용: superstack 포함
        if not ex or not self._is_hl_like(meta):
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
            max_lev, only_iso = await self._get_max_leverage_unified(dex, coin_key)
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
        logger.info("test")
        meta = self.manager.get_meta(exchange_name) or {}

        try:
            slip_str = "0.05" # 강제, 추후 수정 #ex.options.get("defaultSlippage", "0.05")
            slippage = float(slip_str)
        except Exception:
            slippage = 0.05
        is_buy = (side == "buy")

        # 1) HIP‑3 여부 판별(+ 정규화)
        dex, hip3_coin = _parse_hip3_symbol(symbol)
        dex_key = dex # comment: 항상 정의해 NameError 방지(이전 FIX 유지)

        # 2) 자산 ID(a) & 가격 원본(px_base) 결정
        if dex:
            # HIP‑3: 자산 ID는 REST(allPerpMetas) 기반 캐시(그대로), 가격은 WS 기반(_hl_pick_price)
            aidx = await self._resolve_asset_index(hip3_coin)
            if aidx is None:
                raise RuntimeError(f"HIP3 asset index not found for {hip3_coin} on {exchange_name}")
            px_base = await self._hl_pick_price(ex, dex, hip3_coin, price)
        else:
            # 메인 HL: 자산 ID는 REST(allPerpMetas) 기반 캐시(그대로), 가격은 WS 기반(_hl_pick_price)
            coin_key = symbol.upper()
            aidx = await self._resolve_asset_index(coin_key)
            if aidx is None:
                raise RuntimeError(f"Main asset index not found for {coin_key} on {exchange_name}")
            px_base = await self._hl_pick_price(ex, None, coin_key, price)

        coin_key = (hip3_coin if dex else symbol.upper())
        # szDecimals 조회(1회 캐시) → Perp 허용 price 소수자릿수 = 6 - szDecimals
        sz_dec = await self._hl_sz_decimals(dex, coin_key)
        tick_decimals = max(0, 6 - int(sz_dec))  # perp MAX_DECIMALS = 6

        # 3) 주문 가격(px_str) & TIF 결정
        if order_type == "market":
            if want_frontend:
                tif = "FrontendMarket"
            else:
                tif = "Gtc"
                if exchange_name.lower() in ['liquid','mass','treadfi']: # hard coding
                    tif = "Ioc"

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
                "[ORDER] %s %s %s a=%s px_base=%.10f tick_dec=%d price_str=%s tif=%s reduceOnly=%s",
                exchange_name, order_type.upper(), coin_key, aidx, px_base, tick_decimals,
                price_str, tif, reduce_only
            )
        except Exception:
            pass

        action = {"type": "order", "orders": [order_obj], "grouping": "na"}

        opt = getattr(ex, "options", {}) or {}
        builder_addr = opt.get("builder",None)                      # 사용자 설정 builder_code
        if builder_addr:                                       # 빌더가 있을 때만 builder/fee 추가
            #fee_int = None
            #dex_key, _ = _parse_hip3_symbol(symbol)
            fee_int, fee_src, fee_pair = self._pick_fee_with_reason(ex, dex_key, order_type)

            # 최종 주입: fee_int가 None이면 builder만 주입(수수료는 생략)
            builder_payload = {"b": str(builder_addr).lower()}
            if isinstance(fee_int, int):
                builder_payload["f"] = int(fee_int)
            action["builder"] = builder_payload  # comment: 기존 action["builder"] = {"b","f"} 대체
            try:
                pair_str = f"{fee_pair[0]},{fee_pair[1]}" if fee_pair else "None"
                logger.info(
                    "[FEE] ex=%s sym=%s type=%s dex=%s builder=%s feeInt=%s source=%s pair=(%s)",
                    exchange_name, coin_key, order_type.lower(), dex_key or "hl",
                    str(builder_addr).lower(),
                    str(fee_int) if fee_int is not None else "None",
                    fee_src, pair_str
                )
            except Exception:
                pass
        else:
            # [ADD] 빌더 미설정 로그
            logger.info(
                "[FEE] ex=%s sym=%s type=%s dex=%s builder=None (no fee applied in payload)",
                exchange_name, coin_key, order_type.lower(), dex_key or "hl"
            )

        platform = str(meta.get("exchange", "")).lower() if isinstance(meta, dict) else ""
        resp = await self._send_hl_exchange(ex, action, platform=platform)
        oid = self._extract_order_id(resp) or ""
        return {"id": oid, "info": resp}
        
    async def _hl_update_leverage(self, ex, ex_name: str, hip3_coin: str, leverage: int, isolated: bool=True):
        meta = self.manager.get_meta(ex_name) or {}

        aidx = await self._resolve_asset_index(hip3_coin)
        if aidx is None:
            raise RuntimeError(f"HIP3 asset index not found for {hip3_coin} on {ex_name}")

        action = {
            "type": "updateLeverage",
            "asset": aidx,
            "isCross": (not isolated),
            "leverage": int(leverage),
        }
        platform = str(self.manager.get_meta(ex_name).get("exchange", "")).lower()
        resp = await self._send_hl_exchange(ex, action, platform=platform)
        logger.info("[HIP3][%s] %s leverage set: %s (isolated=%s) -> %s",
                    platform or "hl-raw", ex_name, leverage, isolated, str(resp)[:200])
        return resp
        
    def _to_native_symbol(self, exchange_name: str, coin: str) -> str:
        meta = self.manager.get_meta(exchange_name) or {}
        # [CHG] is_hl_like 사용: HL(superstack 포함)은 그대로, 비‑HL만 변환
        if self._is_hl_like(meta):
            return coin
        # 비‑HL: 헤더/카드 DEX 선택의 영향을 제거한다.
        # 'xyz:COIN' → 'COIN' 으로 정규화 (HIP‑3 접두사 제거)
        sym = coin
        try:
            if isinstance(sym, str) and ":" in sym:
                sym = sym.split(":", 1)[1]
        except Exception:
            pass

        # mpdex 심볼 생성기 필요
        if symbol_create is None:
            raise RuntimeError("[mpdex] symbol_create 가 없어 비‑HL 심볼을 생성할 수 없습니다.")
        return symbol_create(exchange_name, sym)
    
    def _extract_order_id(self, res) -> Optional[str]:
        if isinstance(res, list):
            res = res[0]
        try:
            oid = self._extract_oid(res)
            if oid:
                return oid
        except Exception:
            pass
        try:
            if not isinstance(res, dict):
                return str(res)
            for k in ("tx_hash", "order_id", "id", "hash"):
                v = res.get(k)
                if v:
                    return str(v)
            return str(res)
        except Exception:
            return str(res)
    
    def _extract_oid(self, raw: dict) -> int | None:
        # for hl
        resp = (raw or {}).get("response") or {}
        data = resp.get("data") or {}
        sts = data.get("statuses") or []
        if sts and isinstance(sts[0], dict):
            # 구현에 따라 key가 다를 수 있어 dict 전체 탐색
            def _find(d: dict, k: str):
                if k in d and isinstance(d[k], int):
                    return d[k]
                for v in d.values():
                    if isinstance(v, dict):
                        r = _find(v, k)
                        if r is not None: return r
                    elif isinstance(v, list):
                        for it in v:
                            if isinstance(it, dict):
                                r = _find(it, k)
                                if r is not None: return r
                return None
            return _find(sts[0], "oid")
        return None
    
    async def fetch_price(self, exchange_name: str, symbol: str) -> str:
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
            if not self._is_hl_like(meta):
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
            return "📊 Position: N/A", "💰 Account Value: N/A", 0.0
        
        # 직전 캐시 불러오기 (없으면 기본값)
        last_pos_str, last_col_str, last_col_val = self._last_status.get(
            exchange_name,
            ("📊 Position: N/A", "💰 Account Value: N/A", self._last_collateral.get(exchange_name, 0.0)),
        )

        # 1) mpdex (hl=False) 처리
        if not self._is_hl_like(meta):
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
                        pos_str = f"📊 [{side_color}]{side}[/] {size:.5f} | PnL: [{pnl_color}]{pnl:,.1f}[/]"

                col_str = f"💰 Account Value: {col_val:,.1f} USDC"
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
        logger.info(f"[EXECUTE] start: ex={exchange_name} sym={symbol} side={side} amt={amount} type={order_type}")
        meta = self.manager.get_meta(exchange_name) or {}
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")
        
        # 1) mpdex
        if not self._is_hl_like(meta):
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
        - HIP‑3: WS(webData3)로만 포지션 조회(기존과 동일)
        - 메인 HL: REST(ccxt.fetch_positions/fetch_ticker) 제거, WS(webData3)로만 포지션/가격 조회
        """
        meta = self.manager.get_meta(exchange_name) or {}
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")

        # 1) mpdex: 라이브러리 close_position 사용
        if not self._is_hl_like(meta):
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
            # [CHG] HIP‑3 포지션: WS(webData3)에서 우선 조회
            try:
                ws = await self._get_ws_for_scope(dex, ex)
            except Exception:
                ws = None

            hip3_pos = None  # {'size': float, 'side': 'long'|'short'} 기대
            if ws:
                try:
                    pos_map = ws.get_positions_by_dex(dex) or {}
                    # HL WS 캐시는 보통 '대문자 키'로 저장됨. 두 형태 모두 시도
                    p = pos_map.get(hip3_coin.upper()) or pos_map.get(hip3_coin)
                    if p and float(p.get("size") or 0.0) != 0.0:
                        hip3_pos = {
                            "size": float(p["size"]),
                            "side": str(p.get("side") or "long").lower(),
                        }
                except Exception as e:
                    logger.debug(f"[CLOSE] WS position parse failed: {e}")

            if not hip3_pos or float(hip3_pos.get("size") or 0.0) == 0.0:
                logger.info("[CLOSE] %s HIP3 %s: no position", exchange_name, hip3_coin)
                return None

            size = float(hip3_pos["size"])
            side_now = str(hip3_pos.get("side") or "long").lower()
            close_side = "sell" if side_now == "long" else "buy"
            amount = abs(size)

            # 가격 확보: price_hint → WS 가격 → Info API 가격
            px_base: Optional[float] = None
            if price_hint is not None:
                try:
                    px_base = float(price_hint)
                except Exception:
                    px_base = None
            if px_base is None and ws:
                try:
                    ws_px = ws.get_price(hip3_coin)
                    if ws_px is not None:
                        px_base = float(ws_px)
                except Exception:
                    px_base = None
            if px_base is None:
                ex_info = self._info_ex()
                px_base = await self._hl_pick_price(ex_info, dex, hip3_coin, None)

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
        
        # 3) 메인 HL: WS로 포지션/가격 조회
        ws = await self._get_ws_for_scope("hl", ex)
        if not ws:
            logger.info("[CLOSE] %s: no WS", exchange_name)
            return None

        pos_map = {}
        try:
            pos_map = ws.get_positions_by_dex("hl") or {}
        except Exception as e:
            logger.debug(f"[CLOSE] WS positions fetch failed: {e}")

        key = symbol.upper()
        p = pos_map.get(key)
        if not p or float(p.get("size") or 0.0) == 0.0:
            logger.info("[CLOSE] %s: no position for %s", exchange_name, key)
            return None

        size = float(p["size"])
        side_now = str(p.get("side") or "long").lower()
        close_side = "sell" if side_now == "long" else "buy"
        amount = abs(size)

        # 가격: hint → WS 가격 → WS base spot 가격
        px_base: Optional[float] = None
        if price_hint is not None:
            try:
                px_base = float(price_hint)
            except Exception:
                px_base = None
        if px_base is None:
            px = ws.get_price(key)
            if px is None:
                px = ws.get_spot_px_base(key)
            px_base = float(px) if px is not None else None

        logger.info("[CLOSE] %s: %s %.10f → %s %.10f @ market",
                    exchange_name, side_now.upper(), size, close_side.upper(), amount)

        order = await self._hl_create_order_unified(
            ex=ex,
            exchange_name=exchange_name,
            symbol=symbol,
            side=close_side,
            amount=amount,
            order_type="market",
            price=px_base,
            reduce_only=True,
            want_frontend=want_frontend,
            time_in_force=None,
            client_id=None,
        )
        return order