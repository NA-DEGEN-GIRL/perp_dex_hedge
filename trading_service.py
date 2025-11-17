# trading_service.py
import logging
import os
import time
from typing import Tuple, Optional, Dict, Any
from core import ExchangeManager
import asyncio
try:
    from exchange_factory import symbol_create
except Exception:
    symbol_create = None
    logging.warning("[mpdex] exchange_factory.symbol_create 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")
    
DEBUG_FRONTEND = True
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
        # [추가] 상태/쿨다운 캐시
        self._last_collateral: dict[str, float] = {}
        self._last_status: dict[str, Tuple[str, str, float]] = {}  # (pos_str, col_str, col_val)
        self._cooldown_until: dict[str, float] = {}                # 429 쿨다운 끝나는 시각
        self._balance_every: float = 5.0                           # balance 최소 간격(초)
        self._last_balance_at: dict[str, float] = {}               # balance 최근 호출 시각
        self._backoff_sec: dict[str, float] = {}                   # per-ex 백오프(초)
        # (추가) HL 마켓 레버리지/모드 캐시: (exchange, market_id) -> dict
        self._hl_lev_cache: dict[tuple[str, str], dict] = {}
        # (추가) 심볼별 레버리지/모드 적용 여부 캐시
        self._lev_mode_applied: dict[tuple[str, str], bool] = {}
        self._lev_mode_last_at: dict[tuple[str, str], float] = {}
        
        # ex_name -> { 'vaults': [universe...], 'map': {coin -> asset_index}}
        self._hip3_cache: Dict[str, Dict[str, Any]] = {} 
        # [추가] HIP-3 코인별 최대 레버리지 캐시: (dex, hip3_coin) -> int
        self._hip3_maxlev_cache: Dict[tuple[str, str], int] = {}
        # [추가] HIP-3 레버리지 적용 여부 캐시: (exchange_name, hip3_coin) -> bool
        self._hip3_lev_applied: Dict[tuple[str, str], bool] = {}

    async def fetch_perp_dexs(self) -> list[str]:
        """
        HL 첫 거래소에서 publicPostInfo({"type":"perpDexs"}) 호출 → dex 이름 목록(lowercase) 반환.
        기본 'HL'은 UI에서 추가합니다.
        """
        ex = self.manager.first_hl_exchange()
        if not ex:
            return []
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
            # 중복 제거 + 정렬
            return sorted(set(names))
        except Exception as e:
            logger.info("[HIP3] fetch_perp_dexs failed: %s", e)
            return []
        
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

    async def _hip3_pick_price(self, ex, dex: str, hip3_coin: str, price_hint: Optional[float]) -> float:
        """
        HIP-3 가격 소스:
        - price_hint가 있으면 우선 사용
        - 없으면 metaAndAssetCtxs(dex)에서 해당 코인의 markPx → midPx → oraclePx → prevDayPx 순
        """
        if price_hint is not None:
            return float(price_hint)
        px = await self._hl_price_from_meta_asset_ctxs(ex, dex, hip3_coin)
        if px is None:
            raise RuntimeError(f"HIP3 price not found for {hip3_coin}")
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
    
    # [추가] HL Info API로 user 상태 가져오기 (clearinghouseState)
    async def _hl_get_user_state(self, ex, dex: str, user_addr: str) -> Optional[dict]:
        """
        HIP-3: clearinghouseState(user, dex)를 Info API로 조회.
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
            payload = {"type": "clearinghouseState", "user": user_addr.lower(), "dex": dex}
            state = await ex.publicPostInfo(payload)
            if isinstance(state, dict):
                logger.debug("[HIP3] state ok: dex=%s user=%s keys=%s", dex, user_addr, list(state.keys()))
                return state
            # 일부 구현이 리스트 등으로 줄 수 있어 대비
            if isinstance(state, list) and state and isinstance(state[0], dict):
                return state[0]
            logger.info("[HIP3] unexpected state type: %s", type(state))
            return None
        except Exception as e:
            logger.info("[HIP3] clearinghouseState failed: %s", e)
            return None

    def _hl_parse_position_from_state(self, state: dict, hip3_coin: str) -> Optional[dict]:
        """
        clearinghouseState에서 특정 코인(예: 'xyz:XYZ100')의 포지션만 추출해 표준화.
        디버깅 강화를 위해 매칭/스킵/파싱 과정을 상세 로깅합니다.
        PDEX_HIP3_DEBUG=1 이면 state 전체를 한 번 덤프(길이 제한)합니다.
        """
        try:
            hip3_debug = True

            if not isinstance(state, dict):
                logger.debug("[HIP3] state not dict: %s", type(state))
                return None

            if hip3_debug:
                # 너무 큰 로그를 방지하기 위해 앞부분만 출력
                try:
                    import json
                    raw = json.dumps(state)[:2000]  # 2KB 제한
                    logger.debug("[HIP3] raw state(head): %s...", raw)
                except Exception:
                    logger.debug("[HIP3] raw state(head): %s...", str(state)[:1000])

            aps = state.get("assetPositions", []) or []
            logger.debug("[HIP3] parse start: target=%s, assetPositions.len=%d",
                        hip3_coin, len(aps))

            # 코인 목록 수집(최대 50개만)
            coins = []
            for ap in aps[:50]:
                pos0 = ap.get("position") or {}
                coins.append(str(pos0.get("coin") or ""))
            logger.debug("[HIP3] coins in positions(head): %s", coins[:20])

            for idx, ap in enumerate(aps):
                pos = ap.get("position") or {}
                coin = str(pos.get("coin") or "")
                if coin != f"{hip3_coin}":
                    logger.debug("[HIP3] skip idx=%d coin=%s != %s", idx, coin, hip3_coin)
                    continue

                # 안전 파싱 함수
                def f(x, default=0.0):
                    try:
                        return float(x)
                    except Exception:
                        return default

                szi = f(pos.get("szi"), 0.0)
                entry_px = f(pos.get("entryPx"), 0.0)
                u_pnl = f(pos.get("unrealizedPnl"), 0.0)
                liq_px = f(pos.get("liquidationPx"), 0.0)
                pval = f(pos.get("positionValue"), 0.0)
                m_used = f(pos.get("marginUsed"), 0.0)
                lev_info = pos.get("leverage", {}) or {}
                lev_type = str(lev_info.get("type") or "").lower()
                try:
                    lev_val = int(float(lev_info.get("value"))) if lev_info.get("value") is not None else None
                except Exception:
                    lev_val = None

                logger.debug(
                    "[HIP3] matched idx=%d coin=%s szi=%s entryPx=%s uPnl=%s lev=(%s,%s) liqPx=%s pVal=%s mUsed=%s",
                    idx, coin, pos.get("szi"), pos.get("entryPx"), pos.get("unrealizedPnl"),
                    lev_type, lev_info.get("value"), pos.get("liquidationPx"),
                    pos.get("positionValue"), pos.get("marginUsed")
                )

                if abs(szi) <= 0.0:
                    logger.debug("[HIP3] matched but zero size: szi=%s", szi)
                    return None

                side = "long" if szi > 0 else "short"

                result = {
                    "coin": coin,
                    "size": abs(szi),               # 표시는 절대값
                    "entry_price": entry_px,
                    "unrealized_pnl": u_pnl,
                    "side": side,
                    "leverage": lev_val,
                    "leverage_type": lev_type,
                    "liquidation_price": liq_px,
                    "position_value": pval,
                    "margin_used": m_used,
                }

                # marginSummary.accountValue도 참고해 보고 싶다면 여기에 추가 가능
                try:
                    ms = state.get("marginSummary", {}) or {}
                    if ms.get("accountValue") is not None:
                        result["collateral"] = float(ms.get("accountValue"))
                        logger.debug("[HIP3] marginSummary.accountValue=%s", ms.get("accountValue"))
                except Exception:
                    pass

                logger.debug("[HIP3] parse result: %s", result)
                return result

            logger.debug("[HIP3] no matching position for %s (coins=%s)", hip3_coin, coins[:20])
            return None

        except Exception as e:
            logger.debug("[HIP3] parse exception: %s", e, exc_info=True)
            return None
        
    async def _hip3_build_asset_map(self, ex, ex_name: str):
        """
        allPerpMetas를 로드해, 모든 vault(universe)를 평탄화하여
        'coin' -> asset_id 맵을 만든다.
        공식:
        - 메인 퍼프(meta_idx=0): asset = index_in_meta
        - 빌더 퍼프(meta_idx>=1): asset = 100000 + meta_idx * 10000 + index_in_meta
        """
        # 이미 빌드된 경우 캐시 사용
        if ex_name in self._hip3_cache:
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

            self._hip3_cache[ex_name] = {"vaults": vaults, "map": mapping}
            logger.info("[HIP3] %s: %d vault(s), %d coins cached (assetID built by spec)",
                        ex_name, len(vaults), len(mapping))
        except Exception as e:
            logger.info("[HIP3] %s allPerpMetas build failed: %s", ex_name, e)
            self._hip3_cache[ex_name] = {"vaults": [], "map": {}}

    async def _hip3_resolve_asset_index(self, ex, ex_name: str, hip3_coin: str) -> Optional[int]:
        """
        'xyz:XYZ100' 같은 코인의 전역 asset_index를 캐시에서 꺼내거나 allPerpMetas로 빌드 후 반환.
        """
        if ex_name not in self._hip3_cache:
            await self._hip3_build_asset_map(ex, ex_name)
        mp = self._hip3_cache.get(ex_name, {}).get("map", {})
        return mp.get(hip3_coin)

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
            aidx = await self._hip3_resolve_asset_index(ex, exchange_name, hip3_coin)
            if aidx is None:
                raise RuntimeError(f"HIP3 asset index not found for {hip3_coin} on {exchange_name}")
            # HIP‑3 가격 소스(metaAndAssetCtxs)
            px_base = await self._hip3_pick_price(ex, dex, hip3_coin, price)
        else:
            # 메인 퍼프: ccxt 마켓에서 baseId 사용
            await ex.load_markets()
            market_id = f"{symbol}/USDC:USDC"
            m = ex.market(market_id)
            aidx = ex.parse_to_int(m["baseId"])
            # 메인 퍼프 가격 소스(fetch_ticker or hint)
            if price is None:
                t = await ex.fetch_ticker(market_id)
                px_base = float(t.get("last"))
            else:
                px_base = float(price)

        # 3) 주문 가격(px_str) & TIF 결정
        if order_type == "market":
            px_eff = px_base * (1.0 + slippage) if is_buy else px_base * (1.0 - slippage)
            if want_frontend:
                tif = "FrontendMarket"
            else:
                tif = "Gtc"
            # HIP‑3는 정수 가격이 체결 안정적, 메인은 프리시전 준수
            if dex:
                price_str = str(int(px_eff))
            else:
                price_str = ex.price_to_precision(f"{symbol}/USDC:USDC", px_eff)
        else:
            # 지정가: 가격 필수
            if price is None:
                raise RuntimeError("limit order requires price")
            tif = self._tif_capitalize(time_in_force, default="Gtc")
            price_str = str(px_base) if dex else ex.price_to_precision(f"{symbol}/USDC:USDC", px_base)

        # 4) 수량 문자열
        size_str = str(amount).rstrip("0").rstrip(".") if dex else ex.amount_to_precision(f"{symbol}/USDC:USDC", amount)

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

        action = {"type": "order", "orders": [order_obj], "grouping": "na"}

        opt = getattr(ex, "options", {}) or {}
        builder_addr = opt.get("builder")                      # 사용자 설정 builder_code
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

    async def _hip3_get_max_leverage(self, ex, dex: str, hip3_coin: str) -> Optional[int]:
        """
        metaAndAssetCtxs(dex)에서 해당 코인의 maxLeverage(int)를 반환.
        캐시(_hip3_maxlev_cache)를 우선 사용.
        """
        key = (dex, hip3_coin)
        if key in self._hip3_maxlev_cache:
            return self._hip3_maxlev_cache[key]
        try:
            resp = await ex.publicPostInfo({"type": "metaAndAssetCtxs", "dex": dex})
            if not isinstance(resp, list) or len(resp) < 2:
                return None
            universe = (resp[0] or {}).get("universe", []) or []
            for a in universe:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if name != hip3_coin:
                    continue
                if a.get("isDelisted", False):
                    continue
                val = a.get("maxLeverage")
                if val is None:
                    continue
                max_lev = int(float(val))
                self._hip3_maxlev_cache[key] = max_lev
                return max_lev
            return None
        except Exception as e:
            logger.info("[HIP3] get_max_leverage failed: %s", e)
            return None

    # ------------- HIP-3 레버리지 설정(updateLeverage, Isolated 권장) -------------
    async def _hip3_update_leverage(self, ex, ex_name: str, hip3_coin: str, leverage: int, isolated: bool=True):
        aidx = await self._hip3_resolve_asset_index(ex, ex_name, hip3_coin)
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
    
    def _hl_market_id(self, symbol: str) -> str:
        # 본 프로젝트는 HL perp의 쿼트가 USDC:USDC로 고정
        return f"{symbol}/USDC:USDC"

    async def _hl_get_max_lev_info(self, ex, market_id: str) -> tuple[Optional[int], bool]:
        """
        HL 마켓 정보에서 (maxLeverage, onlyIsolated)를 관용적으로 추출.
        (limits.leverage.max) -> (maxLeverage) -> (info.maxLeverage) 순으로 시도.
        """
        try:
            # ccxt 마켓 캐시가 있으면 우선 사용
            if getattr(ex, "markets", None) and market_id in ex.markets:
                m = ex.markets[market_id]
            else:
                await ex.load_markets()
                m = ex.markets.get(market_id, None)
            if not m:
                # fetch_markets로 강제 로드
                await ex.fetch_markets()
                m = ex.markets.get(market_id, None)
            if not m:
                return None, False

            # onlyIsolated 추출(기본 False)
            only_isolated = bool(m.get("onlyIsolated", False) or m.get("info", {}).get("onlyIsolated", False))

            # maxLeverage 추출
            max_lev = None
            try:
                limits = m.get("limits", {})
                lev = limits.get("leverage", {})
                val = lev.get("max", None)
                if val is not None:
                    max_lev = int(float(val))
            except Exception:
                pass
            if max_lev is None:
                try:
                    if "maxLeverage" in m and m["maxLeverage"] is not None:
                        max_lev = int(float(m["maxLeverage"]))
                except Exception:
                    pass
            if max_lev is None:
                try:
                    info = m.get("info", {})
                    if "maxLeverage" in info and info["maxLeverage"] is not None:
                        max_lev = int(float(info["maxLeverage"]))
                except Exception:
                    pass

            return max_lev, only_isolated
        except Exception as e:
            logger.info("[LEVERAGE] market info read failed: %s", e)
            return None, False

    async def ensure_hl_max_leverage_for_exchange(self, exchange_name: str, symbol: str):
        """
        HL 거래소에 대해: 해당 심볼의 maxLeverage를 읽어 cross/isolated 설정 및 레버리지 설정을 1회만 적용.
        """
        ex = self.manager.get_exchange(exchange_name)
        meta = self.manager.get_meta(exchange_name) or {}
        if not ex or not meta.get("hl", False):
            return

        market_id = self._hl_market_id(symbol)
        key = (exchange_name, market_id)
        if self._lev_mode_applied.get(key):
            return  # 이미 설정됨

        # 캐시: 먼저 조회
        cached = self._hl_lev_cache.get(key)
        if cached is None:
            max_lev, only_iso = await self._hl_get_max_lev_info(ex, market_id)
            self._hl_lev_cache[key] = {"maxLeverage": max_lev, "onlyIsolated": only_iso}
        else:
            max_lev = cached.get("maxLeverage")
            only_iso = cached.get("onlyIsolated", False)

        # config leverage가 있으면 max와 비교해 더 작은 값 사용
        cfg_lev = meta.get("leverage")
        if cfg_lev:
            try:
                cfg_lev = int(cfg_lev)
            except Exception:
                cfg_lev = None

        if max_lev is None and cfg_lev is None:
            logger.info("[LEVERAGE] %s: %s no leverage info (skip)", exchange_name, market_id)
            self._lev_mode_applied[key] = True  # 중복 호출 방지
            return

        use_lev = cfg_lev if (cfg_lev and max_lev is None) else (max_lev if (cfg_lev is None) else min(cfg_lev, max_lev))

        # 1) 마진 모드: onlyIsolated True면 isolated, 아니면 cross
        try:
            mode = "isolated" if only_iso else "cross"
            await ex.set_margin_mode(mode, market_id, params={})
            logger.info("[LEVERAGE] %s: set_margin_mode(%s, %s) OK", exchange_name, mode, market_id)
        except Exception as e:
            logger.info("[LEVERAGE] %s: set_margin_mode unsupported/failed: %s", exchange_name, e)

        # 2) 레버리지 설정
        if use_lev:
            try:
                await ex.set_leverage(int(use_lev), market_id, params={})
                logger.info("[LEVERAGE] %s: set_leverage(%s, %s) OK", exchange_name, use_lev, market_id)
            except Exception as e:
                logger.info("[LEVERAGE] %s: set_leverage(%s, %s) failed: %s", exchange_name, use_lev, market_id, e)

        self._lev_mode_applied[key] = True
        self._lev_mode_last_at[key] = time.monotonic()

    async def ensure_hl_max_leverage_for_all(self, symbol: str):
        """설정된 모든 HL 거래소에 대해 ensure_hl_max_leverage_for_exchange 실행."""
        tasks = []
        for name in self.manager.all_names():
            if self.manager.get_exchange(name) and self.manager.get_meta(name).get("hl", False):
                tasks.append(self.ensure_hl_max_leverage_for_exchange(name, symbol))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _is_rate_limited(self, err: Exception | str) -> bool:
        s = str(err).lower()
        return ("429" in s) or ("too many" in s) or ("rate limit" in s)
    
    def is_configured(self, name: str) -> bool:
        return self.manager.get_exchange(name) is not None

    def is_hl(self, name: str) -> bool:
        return bool(self.manager.get_meta(name).get("hl", False))

    async def _hl_price_from_meta_asset_ctxs(self, ex, dex: str, hip3_coin: str) -> Optional[float]:
        """
        HIP-3 가격 조회: publicPostInfo({"type":"metaAndAssetCtxs","dex": dex})
        응답은 [ { "universe": [...] }, [ assetCtxs... ] ] 형태이며,
        universe[i].name과 assetCtxs[i]가 같은 인덱스로 매칭됩니다.
        """
        try:
            payload = {"type": "metaAndAssetCtxs", "dex": dex}
            resp = await ex.publicPostInfo(payload)
            if not isinstance(resp, list) or len(resp) < 2:
                logger.debug("[HIP3] metaAndAssetCtxs unexpected resp type=%s", type(resp))
                return None

            meta0 = resp[0] or {}
            universe = meta0.get("universe", []) or []
            asset_ctxs = resp[1] or []

            # 방어: 길이 차이 존재 가능 → 이름 매칭 우선
            # 1) 우선 인덱스 정렬 가정(universe[i] ↔ asset_ctxs[i])
            # 2) 그래도 못 찾으면 이름 기반으로 탐색
            idx = None
            for i, a in enumerate(universe):
                if not isinstance(a, dict):
                    continue
                name = str(a.get("name") or "")
                if name == hip3_coin and not a.get("isDelisted", False):
                    idx = i
                    break

            px = None
            if idx is not None and idx < len(asset_ctxs) and isinstance(asset_ctxs[idx], dict):
                ctx = asset_ctxs[idx]
                # 우선순위: markPx → midPx → oraclePx → prevDayPx
                for k in ("markPx", "midPx", "oraclePx", "prevDayPx"):
                    v = ctx.get(k)
                    if v is not None:
                        try:
                            px = float(v)
                            break
                        except Exception:
                            continue

            if px is None:
                # 이름 기반 탐색(혹시 인덱스 불일치 대비)
                for a, ctx in zip(universe, asset_ctxs):
                    try:
                        if not isinstance(a, dict) or not isinstance(ctx, dict):
                            continue
                        if str(a.get("name") or "") != hip3_coin:
                            continue
                        if a.get("isDelisted", False):
                            continue
                        for k in ("markPx", "midPx", "oraclePx", "prevDayPx"):
                            v = ctx.get(k)
                            if v is not None:
                                px = float(v)
                                break
                        if px is not None:
                            break
                    except Exception:
                        continue

            return px
        except Exception as e:
            logger.info("[HIP3] metaAndAssetCtxs failed: %s", e)
            return None

    async def fetch_hl_price(self, symbol: str) -> str:
        ex = self.manager.first_hl_exchange()
        if not ex:
            return "N/A"
        # HIP-3 여부 파싱
        dex, hip3_coin = _parse_hip3_symbol(symbol)
        try:
            # 간단 캐시(3초): (dex, hip3_coin) 키
            now = time.monotonic()
            if not hasattr(self, "_hip3_px_cache"):
                self._hip3_px_cache = {}  # type: ignore[attr-defined]
            cache = getattr(self, "_hip3_px_cache")  # type: ignore[attr-defined]

            if dex:
                key = (dex, hip3_coin)
                ent = cache.get(key) if isinstance(cache, dict) else None
                if ent and (now - ent.get("ts", 0.0) < 3.0):
                    return f"{ent['px']:,.2f}"

                px = await self._hl_price_from_meta_asset_ctxs(ex, dex, hip3_coin)
                if px is None:
                    logger.debug("[HIP3] price not found for %s, fallback=Error", hip3_coin)
                    return "Error"
                # 캐시
                cache[key] = {"px": px, "ts": now}
                return f"{px:,.2f}"

            # 일반 HL 페어
            t = await ex.fetch_ticker(f"{symbol}/USDC:USDC")
            return f"{t['last']:,.2f}"
        
        except Exception as e:
            logger.error(f"HL price fetch error: {e}", exc_info=True)
            return "Error"

    async def fetch_status(
        self,
        exchange_name: str,
        symbol: str,
        need_balance: bool = True  # [변경] balance 스킵 가능
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
        
        # 1) mpdex (hl=False) 처리
        if not meta.get("hl", False):
            try:
                col_val = self._last_collateral.get(exchange_name, 0.0)
                if need_balance:
                    c = await ex.get_collateral()
                    col_val = float(c.get("total_collateral") or 0.0)
                    self._last_collateral[exchange_name] = col_val
                    self._last_balance_at[exchange_name] = time.monotonic()
                
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
                cached = self._last_status.get(exchange_name)
                return cached if cached else ("📊 Position: Error", "💰 Collateral: Error", 0.0)
            
        else:
            now = time.monotonic()
            # 429 쿨다운이면 캐시 반환
            if now < self._cooldown_until.get(exchange_name, 0.0):
                cached = self._last_status.get(exchange_name)
                if cached:
                    return cached
                # 캐시 없으면 N/A
                return "📊 Position: N/A", f"💰 Collateral: {self._last_collateral.get(exchange_name, 0.0):,.2f} USDC", self._last_collateral.get(exchange_name, 0.0)

            try:
                # balance는 10초마다 or need_balance=True인 경우에만
                col_val = self._last_collateral.get(exchange_name, 0.0)
                if need_balance or (now - self._last_balance_at.get(exchange_name, 0.0) >= self._balance_every):
                    bal = await ex.fetch_balance()
                    col_val = float(bal.get("USDC", {}).get("total", 0) or 0)
                    self._last_collateral[exchange_name] = col_val
                    self._last_balance_at[exchange_name] = now


                # 2) 포지션: HIP‑3면 clearinghouseState(user+dex), 아니면 fetch_positions
                dex, hip3_coin = _parse_hip3_symbol(symbol)
                pos_str = "📊 Position: N/A"
                # 디버깅: HIP-3 파싱 결과 출력 (여기가 문제였음)
                
                if dex:
                    user_addr = self._hl_user_address(ex)

                    logger.debug("fetch_status(HL): hip3 dex=%s coin=%s address=%s", dex, hip3_coin, user_addr)
                    
                    state = await self._hl_get_user_state(ex, dex, user_addr)
                    
                    hip3_pos = self._hl_parse_position_from_state(state or {}, hip3_coin)
                    logger.debug(str(hip3_pos))
                    if hip3_pos:
                        side = "LONG" if hip3_pos["side"] == "long" else "SHORT"
                        size = float(hip3_pos["size"])
                        pnl = float(hip3_pos["unrealized_pnl"])
                        side_color = "green" if side == "LONG" else "red"
                        pnl_color = "green" if pnl >= 0 else "red"
                        pos_str = f"📊 [{side_color}]{side}[/] {size:.5f} | PnL: [{pnl_color}]{pnl:,.2f}[/]"
                else:
                    # positions는 매번
                    positions = await ex.fetch_positions([f"{symbol}/USDC:USDC"])
                    # 포지션 문자열 구성(이전과 동일)
                    pos_str = "📊 Position: N/A"
                    if positions and positions[0]:
                        p = positions[0]
                        try:
                            sz = float(p.get("contracts") or 0.0)
                        except Exception:
                            sz = 0.0
                        if sz:
                            side = "LONG" if p.get("side") == "long" else "SHORT"
                            try:
                                pnl = float(p.get("unrealizedPnl") or 0.0)
                            except Exception:
                                pnl = 0.0
                            side_color = "green" if side == "LONG" else "red"
                            pnl_color = "green" if pnl >= 0 else "red"
                            pos_str = f"📊 [{side_color}]{side}[/] {sz:.5f} | PnL: [{pnl_color}]{pnl:,.2f}[/]"

                col_str = f"💰 Collateral: {col_val:,.2f} USDC"
                # 캐시 갱신
                self._last_status[exchange_name] = (pos_str, col_str, col_val)
                # 성공하면 백오프 초기화
                self._backoff_sec[exchange_name] = 0.0
                return pos_str, col_str, col_val

            except Exception as e:
                logging.error(f"[{exchange_name}] fetch_status error: {e}", exc_info=True)
                # 429면 백오프/쿨다운 설정
                if self._is_rate_limited(e):
                    current = self._backoff_sec.get(exchange_name, 2.0) or 2.0
                    new_backoff = min(current * 2.0, 15.0)
                    self._backoff_sec[exchange_name] = new_backoff
                    self._cooldown_until[exchange_name] = now + new_backoff
                # 캐시 반환
                cached = self._last_status.get(exchange_name)
                if cached:
                    return cached
                return "📊 Position: Error", "💰 Collateral: Error", 0.0
    
    
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

        # (선택) 메인 퍼프는 주문 전 심볼별 레버리지/마진모드 보장(캐시로 과호출 방지)
        dex, hip3_coin = _parse_hip3_symbol(symbol)
        if dex:
            try:
                apply_key = (exchange_name, hip3_coin)
                if not self._hip3_lev_applied.get(apply_key):
                    max_lev = await self._hip3_get_max_leverage(ex, dex, hip3_coin)
                    if max_lev:
                        await self._hip3_update_leverage(ex, exchange_name, hip3_coin, leverage=max_lev, isolated=True)
                        logger.info("[HIP3] %s %s leverage set to max=%s (isolated)", exchange_name, hip3_coin, max_lev)
                    else:
                        logger.info("[HIP3] %s %s maxLeverage not found, skip set", exchange_name, hip3_coin)
                    self._hip3_lev_applied[apply_key] = True
            except Exception as e:
                logger.info("[HIP3] auto set max leverage skipped: %s", e)

        # (B) 메인 HL(자체 퍼프): 기존 보장(심볼별 max/cross/iso)
        else:
            try:
                await self.ensure_hl_max_leverage_for_exchange(exchange_name, symbol)
            except Exception as e:
                logger.info("[LEVERAGE] ensure @order skipped: %s", e)

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
                px_base = await self._hip3_pick_price(ex, dex, hip3_coin, price_hint)
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