# trading_service.py
import logging
from typing import Tuple, Optional

from core import ExchangeManager  # 타입 힌트 목적 (선택)


class TradingService:
    """
    UI에서 직접 ccxt를 다루지 않도록 감싸는 공통 서비스.
    - fetch_current_price(symbol) -> str
    - fetch_status(exchange_name, symbol) -> (pos_str, col_str, col_val)
    - execute_order(exchange_name, symbol, amount, order_type, side, price) -> order(dict)
    - is_configured(name) -> bool
    """

    def __init__(self, manager: ExchangeManager):
        self.manager = manager

    def is_configured(self, name: str) -> bool:
        ex = self.manager.get_exchange(name)
        return ex is not None

    async def fetch_current_price(self, symbol: str) -> str:
        ex = next((e for e in self.manager.exchanges.values() if e), None)
        if not ex:
            return "N/A"
        try:
            t = await ex.fetch_ticker(f"{symbol}/USDC:USDC")
            return f"{t['last']:,.2f}"
        except Exception as e:
            logging.error(f"Price fetch error: {e}", exc_info=True)
            # just pass to use previous price
            #return "Error"

    async def fetch_status(self, exchange_name: str, symbol: str) -> Tuple[str, str, float]:
        """
        returns:
          pos_str: "📊 ...", col_str: "💰 Collateral: ...", col_val: float
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "📊 Position: N/A", "💰 Collateral: N/A", 0.0
        try:
            # 동시 호출
            bal_coro = ex.fetch_balance()
            pos_coro = ex.fetch_positions([f"{symbol}/USDC:USDC"])
            balance, positions = await bal_coro, await pos_coro  # 순차보다 명확한 예외 전파를 위해 분리
            total_collateral = balance.get("USDC", {}).get("total", 0) or 0
            col_str = f"💰 Collateral: {total_collateral:,.2f} USDC"

            pos_str = "📊 Position: N/A"
            if positions and positions[0]:
                p = positions[0]
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
        """
        ccxt create_order 감싸기
        - market 주문이고 price가 None이면 ticker last를 price로 시도
        """
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