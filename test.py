import ccxt.async_support as ccxt
import asyncio

hl = ccxt.hyperliquid()

async def test():
    res = await hl.fetch_markets()
    for i in res:
        print(i['symbol'],i['type'])

if __name__ == "__main__":
    asyncio.run(test())