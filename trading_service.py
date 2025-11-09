# trading_service.py
import logging
from typing import Tuple, Optional

from core import ExchangeManager


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

    async def execute_order(
        self,
        exchange_name: str,
        symbol: str,
        amount: float,
        order_type: str,  # 'market' or 'limit'
        side: str,        # 'buy' or 'sell'
        price: Optional[float] = None,
    ) -> dict:
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")

        px = price
        if order_type == "market" and px is None:
            try:
                t = await ex.fetch_ticker(f"{symbol}/USDC:USDC")
                px = t.get("last")
            except Exception:
                px = None

        order = await ex.create_order(
            symbol=f"{symbol}/USDC:USDC",
            type=order_type,
            side=side,
            amount=amount,
            price=px,
        )
        return order