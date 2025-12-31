# mpdex - Multi Perpetual DEX Trading Library

A unified async Python interface for trading perpetual futures across multiple DEXs.

## Supported Exchanges

| Exchange | Platform Key | Symbol Example |
|----------|-------------|----------------|
| Paradex | `paradex` | `BTC-USD-PERP` |
| EdgeX | `edgex` | `BTCUSD` |
| GRVT | `grvt` | `BTC_USDT_Perp` |
| Backpack | `backpack` | `BTC_USDC_PERP` |
| Lighter | `lighter` | `BTC` |
| Hyperliquid | `hyperliquid` | `BTC` |
| Superstack | `superstack` | `BTC` |
| Pacifica | `pacifica` | `BTC` |
| TreadFi+Hyperliquid | `treadfi.hyperliquid` | `BTC:PERP-USDC` |
| TreadFi+Pacifica | `treadfi.pacifica` | `BTC:PERP-USDC` |
| Variational | `variational` | `BTC` |
| StandX | `standx` | `BTC-USD` |

## Quick Start

```python
import asyncio
from exchange_factory import create_exchange, symbol_create

async def main():
    # 1. Import your API keys
    from keys.pk_lighter import LIGHTER_KEY

    # 2. Create exchange instance
    ex = await create_exchange("lighter", LIGHTER_KEY)

    # 3. Create symbol (always use symbol_create for correct format)
    symbol = symbol_create("lighter", "BTC")

    # 4. Use the exchange
    collateral = await ex.get_collateral()
    price = await ex.get_mark_price(symbol)

    # 5. Always close when done
    await ex.close()

asyncio.run(main())
```

## Core Methods

All exchanges implement these async methods:

### Account Info
```python
# Get account collateral/balance
collateral = await ex.get_collateral()
# Returns: {"total": float, "free": float, ...}

# Get available trading symbols
symbols = ex.available_symbols
# Returns: {"perp": ["BTC", "ETH", ...], "spot": [...]}
```

### Market Data
```python
# Get mark price
price = await ex.get_mark_price(symbol)
# Returns: float (e.g., 95000.5)

# Get orderbook (ALWAYS uses WebSocket internally)
orderbook = await ex.get_orderbook(symbol)
# Returns: {"bids": [[price, size], ...], "asks": [[price, size], ...]}

# IMPORTANT: Unsubscribe orderbook when done to release WS resources
await ex.unsubscribe_orderbook(symbol)
```

**Note on Orderbook**:
- `get_orderbook()` always uses WebSocket subscription internally
- The first call subscribes and waits for data
- Subsequent calls return cached data (updated in real-time via WS)
- **You must call `unsubscribe_orderbook(symbol)` when done** to release WebSocket resources

### Position Management
```python
# Get current position
position = await ex.get_position(symbol)
# Returns: {"size": float, "side": "buy"|"sell", "entry_price": float, ...} or None

# Close position
result = await ex.close_position(symbol, position, is_reduce_only=True)
```

### Order Management
```python
# Market order (price=None)
result = await ex.create_order(symbol, "buy", amount=0.01)
result = await ex.create_order(symbol, "sell", amount=0.01)

# Limit order (specify price)
result = await ex.create_order(symbol, "buy", amount=0.01, price=94000.0)
result = await ex.create_order(symbol, "sell", amount=0.01, price=96000.0)

# Get open orders
open_orders = await ex.get_open_orders(symbol)
# Returns: [{"order_id": str, "side": str, "price": float, "size": float, ...}, ...]

# Cancel orders
result = await ex.cancel_orders(symbol, open_orders)
```

## Symbol System

### Overview

mpdex uses a **two-layer symbol system**:

1. **User Symbol** - What you pass to `symbol_create()` and exchange methods
2. **Internal Symbol** - What the wrapper converts to for different APIs (order vs fetch)

Most exchanges use the same symbol for both ordering and fetching. However, **TreadFi exchanges are special** - they use different symbols for orders vs data fetching.

### Symbol Creation

Always use `symbol_create()` to get the correct user symbol:

```python
from exchange_factory import symbol_create

# Perpetual (default)
symbol = symbol_create("lighter", "BTC")        # "BTC"
symbol = symbol_create("paradex", "ETH")        # "ETH-USD-PERP"
symbol = symbol_create("backpack", "SOL")       # "SOL_USDC_PERP"

# Spot trading (where supported)
symbol = symbol_create("lighter", "BTC/USDC", is_spot=True)   # "BTC/USDC"
symbol = symbol_create("backpack", "ETH/USDC", is_spot=True)  # "ETH_USDC"
```

### Symbol Conversion by Exchange Type

#### Standard Exchanges (Same symbol for Order & Fetch)

| Exchange | User Symbol | Order API | Fetch API |
|----------|-------------|-----------|-----------|
| Hyperliquid | `BTC` | `BTC` | `BTC` |
| Hyperliquid DEX | `hyna:BTC` | `hyna:BTC` | `hyna:BTC` |
| Hyperliquid Spot | `BTC/USDC` | `@{pairIdx}` | `@{pairIdx}` |
| Pacifica | `BTC` | `BTC` | `BTC` |
| Lighter | `BTC` | `BTC` | `BTC` |
| StandX | `BTC-USD` | `BTC-USD` | `BTC-USD` |
| Paradex | `BTC-USD-PERP` | `BTC-USD-PERP` | `BTC-USD-PERP` |

#### TreadFi Exchanges (Different symbols for Order vs Fetch)

TreadFi is a **hybrid exchange** - it routes orders to TreadFi API but fetches data from underlying exchanges (Hyperliquid or Pacifica).

| Exchange | User Symbol | Order API (TreadFi) | Fetch API (Underlying) |
|----------|-------------|---------------------|------------------------|
| treadfi.hyperliquid | `BTC:PERP-USDC` | `BTC:PERP-USDC` | `BTC` (via Hyperliquid) |
| treadfi.hyperliquid (DEX) | `hyna_HYNA:PERP-USDC` | `hyna_HYNA:PERP-USDC` | `hyna:HYNA` (via Hyperliquid) |
| treadfi.hyperliquid (Spot) | `BTC-USDC` | `BTC-USDC` | `BTC/USDC` (via Hyperliquid) |
| treadfi.pacifica | `BTC:PERP-USDC` | `BTC:PERP-USDC` | `BTC` (via Pacifica) |

**Important**: The wrapper handles these conversions internally. You only need to use the **User Symbol** from `symbol_create()`.

### Symbol Conversion Functions (Internal)

```python
# treadfi.hyperliquid: _symbol_convert_for_ws()
"BTC:PERP-USDC"      → "BTC"        # Perp
"hyna_HYNA:PERP-USDC" → "hyna:HYNA"  # DEX Perp
"BTC-USDC"           → "BTC/USDC"   # Spot

# treadfi.pacifica: _symbol_to_pacifica()
"BTC:PERP-USDC"      → "BTC"        # Always extracts base coin
```

### available_symbols Property

After `init()`, you can check available symbols:

```python
ex = await create_exchange("hyperliquid", KEY)

# Perp symbols (by DEX for Hyperliquid)
ex.available_symbols['perp']
# {"hl": ["BTC-USDC", "ETH-USDC", ...], "hyna": ["HYNA-USDC", ...]}

# Spot symbols
ex.available_symbols['spot']
# ["BTC/USDC", "ETH/USDC", ...]

# For simpler exchanges (Pacifica, Lighter, etc.)
ex.available_symbols['perp']
# ["BTC-USDC", "ETH-USDC", ...]  # Just a list
```

## Exchange-Specific Key Templates

Each exchange requires different credentials. Copy the template and fill in:

```python
# Lighter example (keys/pk_lighter.py)
from dataclasses import dataclass

@dataclass
class LighterKey:
    account_id: str
    private_key: str
    api_key_id: str
    l1_address: str

LIGHTER_KEY = LighterKey(
    account_id="your_account_id",
    private_key="your_private_key",
    api_key_id="your_api_key_id",
    l1_address="your_l1_address"
)
```

## Common Patterns

### Check Collateral and Position
```python
async def check_status(ex, symbol):
    coll = await ex.get_collateral()
    print(f"Collateral: {coll.get('total', coll)}")

    pos = await ex.get_position(symbol)
    if pos and float(pos.get('size', 0)) != 0:
        print(f"Position: {pos['side']} {pos['size']} @ {pos.get('entry_price')}")
    else:
        print("No position")
```

### Place Order with Price Check
```python
async def place_limit_order(ex, symbol, side, amount, offset_pct=0.03):
    price = await ex.get_mark_price(symbol)

    if side == "buy":
        limit_price = price * (1 - offset_pct)  # 3% below mark
    else:
        limit_price = price * (1 + offset_pct)  # 3% above mark

    result = await ex.create_order(symbol, side, amount, price=limit_price)
    return result
```

### Cancel All and Close Position
```python
async def close_all(ex, symbol):
    # Cancel open orders first
    open_orders = await ex.get_open_orders(symbol)
    if open_orders:
        await ex.cancel_orders(symbol, open_orders)

    # Close position
    position = await ex.get_position(symbol)
    if position and float(position.get('size', 0)) != 0:
        await ex.close_position(symbol, position, is_reduce_only=True)
```

### Safe Orderbook Usage
```python
async def get_best_prices(ex, symbol):
    """Get best bid/ask with proper cleanup"""
    try:
        ob = await ex.get_orderbook(symbol)
        best_bid = ob['bids'][0][0] if ob.get('bids') else None
        best_ask = ob['asks'][0][0] if ob.get('asks') else None
        return best_bid, best_ask
    finally:
        # Always unsubscribe to release WS resources
        await ex.unsubscribe_orderbook(symbol)

# For continuous orderbook monitoring (keep subscribed)
async def monitor_orderbook(ex, symbol, duration_sec=60):
    """Monitor orderbook for a duration, then cleanup"""
    import asyncio
    try:
        ob = await ex.get_orderbook(symbol)  # Subscribe
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < duration_sec:
            # Subsequent calls return cached real-time data
            ob = await ex.get_orderbook(symbol)
            print(f"Bid: {ob['bids'][0]}, Ask: {ob['asks'][0]}")
            await asyncio.sleep(1)
    finally:
        await ex.unsubscribe_orderbook(symbol)  # Cleanup
```

## WebSocket Support

Some exchanges support WebSocket for faster data:

```python
# Exchanges with fetch_by_ws option
from keys.pk_hyperliquid import HYPERLIQUID_KEY
HYPERLIQUID_KEY.fetch_by_ws = True  # Enable WS data fetching

ex = await create_exchange("hyperliquid", HYPERLIQUID_KEY)
# Now get_mark_price, get_position, etc. use WebSocket
```

Supported: `hyperliquid`, `lighter`, `pacifica`, `treadfi.hyperliquid`, `treadfi.pacifica`, `standx`, `superstack`

## Error Handling

```python
async def safe_order(ex, symbol, side, amount, price=None):
    try:
        result = await ex.create_order(symbol, side, amount, price=price)
        if result.get("ok"):
            return {"success": True, "order_id": result.get("order_id")}
        else:
            return {"success": False, "error": result.get("error")}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

## Important Notes

1. **Always await** - All methods are async
2. **Always close** - Call `await ex.close()` when done
3. **Use symbol_create** - Don't hardcode symbol formats
4. **Check return values** - Most methods return dict with `ok` or `error` keys
5. **Position size** - Can be negative (short) or positive (long) depending on exchange
