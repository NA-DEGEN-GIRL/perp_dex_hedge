# trading_service.py
import logging
import os
from typing import Tuple, Optional
from core import ExchangeManager

def _build_file_only_logger(name: str, filename: str = "debug.log", level: int = logging.INFO) -> logging.Logger:
    lg = logging.getLogger(name)
    # 전용 핸들러만 쓰고, 루트로 전파 금지 → 콘솔로 안 나감
    lg.propagate = False
    # 중복 추가 방지
    if not lg.handlers:
        fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
        fh = logging.FileHandler(filename, mode="a", encoding="utf-8")
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    lg.setLevel(level)
    return lg

DEBUG_FRONTEND = False
logger = _build_file_only_logger(
    "trading_service",
    filename="debug.log",
    level=logging.DEBUG if DEBUG_FRONTEND else logging.INFO
)

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
            logging.error(f"HL price fetch error: {e}", exc_info=True)
            return "Error"

    async def fetch_status(self, exchange_name: str, symbol: str) -> Tuple[str, str, float]:
        """
        returns:
          pos_str: "📊 ...", col_str: "💰 Collateral: ...", col_val: float
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "📊 Position: N/A", "💰 Collateral: N/A", 0.0
        try:
            bal = await ex.fetch_balance()
            pos = await ex.fetch_positions([f"{symbol}/USDC:USDC"])
            total_collateral = bal.get("USDC", {}).get("total", 0) or 0
            col_str = f"💰 Collateral: {total_collateral:,.2f} USDC"

            pos_str = "📊 Position: N/A"
            if pos and pos[0]:
                p = pos[0]
                sz = 0.0
                try:
                    sz = float(p.get("contracts") or 0)
                except Exception:
                    sz = 0.0
                if sz:
                    side = "LONG" if p.get("side") == "long" else "SHORT"
                    pnl = 0.0
                    try:
                        pnl = float(p.get("unrealizedPnl") or 0)
                    except Exception:
                        pnl = 0.0
                    side_color = "green" if side == "LONG" else "red"
                    pnl_color = "green" if pnl >= 0 else "red"
                    pos_str = f"📊 [{side_color}]{side}[/] {sz:.5f} | PnL: [{pnl_color}]{pnl:,.2f}[/]"

            return pos_str, col_str, float(total_collateral)
        except Exception as e:
            logging.error(f"[{exchange_name}] fetch_status error: {e}", exc_info=True)
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
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")
        
        meta = self.manager.get_meta(exchange_name)
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
                ex, symbol, side, amount, price, reduce_only=False, client_id=None
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
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")

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