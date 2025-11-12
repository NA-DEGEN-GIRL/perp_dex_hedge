# trading_service.py
import logging
import os
import time
from typing import Tuple, Optional
from core import ExchangeManager
import asyncio
try:
    from exchange_factory import symbol_create
except Exception:
    symbol_create = None
    logging.warning("[mpdex] exchange_factory.symbol_create 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")
    
DEBUG_FRONTEND = False
logger = logging.getLogger("trading_service")
logger.propagate = True                    # 루트로 전파해 main.py의 FileHandler만 사용
logger.setLevel(logging.DEBUG if DEBUG_FRONTEND else logging.INFO)

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
    

    async def fetch_hl_price(self, symbol: str) -> str:
        ex = self.manager.first_hl_exchange()
        if not ex:
            return "N/A"
        try:
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
        
        # 1) Lighter (hl=False) 처리
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
                # positions는 매번
                positions = await ex.fetch_positions([f"{symbol}/USDC:USDC"])

                # balance는 10초마다 or need_balance=True인 경우에만
                col_val = self._last_collateral.get(exchange_name, 0.0)
                if need_balance or (now - self._last_balance_at.get(exchange_name, 0.0) >= self._balance_every):
                    bal = await ex.fetch_balance()
                    col_val = float(bal.get("USDC", {}).get("total", 0) or 0)
                    self._last_collateral[exchange_name] = col_val
                    self._last_balance_at[exchange_name] = now

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
    
    # NEW: FrontendMarket 시장가 주문 raw 전송
    async def _create_frontend_market_order(self, ex, symbol: str, side: str,
                                            amount: float, price: float,
                                            reduce_only: bool = False,
                                            client_id: Optional[str] = None) -> dict:
        """
        ccxt 수정 없이 privatePostExchange로 tif='FrontendMarket'을 정확히 넣어 시장가 주문 전송.
        """
        await ex.load_markets()
        market_id = f"{symbol}/USDC:USDC"
        m = ex.market(market_id)

        # 1) 슬리피지(기본 5%) 확보
        try:
            # ccxt 옵션에 문자열로 있을 수 있음
            slip_str = ex.options.get('defaultSlippage', '0.05')
            slippage = float(slip_str)
        except Exception:
            slippage = 0.05

        # 2) 공격적 px로 보정: buy는 (1+slip), sell은 (1-slip)
        try:
            last = float(price)
        except Exception:
            # 혹시 price_hint가 숫자가 아니면 보조 조회
            t = await ex.fetch_ticker(market_id)
            last = float(t.get('last'))

        is_buy = (side == 'buy')
        aggressive_px = last * (1.0 + slippage) if is_buy else last * (1.0 - slippage)

        # 3) 정밀도 보정
        px = ex.price_to_precision(market_id, aggressive_px)
        sz = ex.amount_to_precision(market_id, amount)

        order_obj = {
            'a': ex.parse_to_int(m['baseId']),          # asset id
            'b': (side == 'buy'),                       # True=buy, False=sell
            'p': px,                                    # price (string)
            's': sz,                                    # size (string)
            'r': bool(reduce_only),                     # reduceOnly
            't': { 'limit': { 'tif': 'FrontendMarket' } }  # 핵심
        }
        if client_id:
            order_obj['c'] = client_id

        nonce = ex.milliseconds()
        order_action = {
            'type': 'order',
            'orders': [order_obj],
            'grouping': 'na',
        }

        # builder/feeInt 포함(승인 상태일 때)
        if ex.safe_bool(ex.options, 'approvedBuilderFee', False):
            wallet = ex.safe_string_lower(ex.options, 'builder', '0x6530512A6c89C7cfCEbC3BA7fcD9aDa5f30827a6')
            fee_int = ex.safe_integer(ex.options, 'feeInt', 10)
            order_action['builder'] = { 'b': wallet, 'f': fee_int }

        signature = ex.sign_l1_action(order_action, nonce, None)  # vaultAddress=None

        request = {
            'action': order_action,
            'nonce': nonce,
            'signature': signature,
        }

        if DEBUG_FRONTEND:
            logger.debug(f"[FRONTEND] raw order payload={request}")

        resp = await ex.privatePostExchange(request)
        # ccxt의 create_orders 반환을 간단하게 모방(상태 파싱)
        response_obj = ex.safe_dict(resp, 'response', {})
        data = ex.safe_dict(response_obj, 'data', {})
        statuses = ex.safe_list(data, 'statuses', [])
        orders_to_parse = []
        for st in statuses:
            if st == 'waitingForTrigger':
                orders_to_parse.append({'status': st})
            else:
                orders_to_parse.append(st)
        parsed = ex.parse_orders(orders_to_parse, None)
        return parsed[0] if parsed else {'info': resp}
    
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
        
        # [추가] HL은 주문 직전에 심볼별 레버리지/마진 모드를 보장(캐시되어 과호출 없음)
        if meta.get("hl", False):
            try:
                await self.ensure_hl_max_leverage_for_exchange(exchange_name, symbol)
            except Exception as e:
                logger.info("[LEVERAGE] ensure @order skipped: %s", e)
        
        # 1) Lighter
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
        
        else:
            want_frontend = bool(meta.get("frontend_market", False))

            # 디버깅 로그(주문 분기 직전 전체 상황)
            logger.info(
                "[ORDER] ex=%s sym=%s type=%s side=%s price=%s reduce_only=%s meta=%s want_frontend=%s",
                exchange_name, symbol, order_type, side, price, reduce_only, meta, want_frontend
            )

            # 시장가 + FrontendMarket=True → raw 전송(정확한 tif 마킹)
            if order_type == "market" and want_frontend:
                if price is None:
                    # price는 HL 시장가에서 필수(슬리피지 계산용); 호출부에서 last를 넣어줌
                    raise RuntimeError("market order requires price for FrontendMarket")
                logger.info("[FRONTEND] using privatePostExchange (FrontendMarket) for %s", exchange_name)
                return await self._create_frontend_market_order(
                    ex, symbol, side, amount, price, reduce_only=reduce_only, client_id=client_id
                )
            
            # 그 외 ccxt 표준 전송(reduceOnly는 params로 전달)
            params = {}
            if reduce_only:
                params["reduceOnly"] = True
            if client_id:
                params["clientOrderId"] = client_id

            # 그 외에는 표준 ccxt create_order 사용
            return await ex.create_order(
                symbol=f"{symbol}/USDC:USDC",
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=params
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

        # 1) Lighter: 라이브러리 close_position 사용
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
        else:
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

            # 가격 확보: hint → 실패 시 해당 거래소에서 last
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
            # 주문 실행: execute_order로 위임 (시장가 + reduceOnly=True)
            return await self.execute_order(
                exchange_name=exchange_name,
                symbol=symbol,
                amount=amount,
                order_type="market",
                side=close_side,
                price=px,
                reduce_only=True
            )