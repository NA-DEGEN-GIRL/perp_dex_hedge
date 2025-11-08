# core.py
import os
import asyncio
import configparser
import logging

import ccxt.async_support as ccxt
from dotenv import load_dotenv

# --- 설정 로드 ---
load_dotenv()
config = configparser.ConfigParser(interpolation=None)
config.read("config.ini")

EXCHANGES = sorted([section for section in config.sections()])

class ExchangeManager:
    def __init__(self):
        self.exchanges = {}
        for exchange_name in EXCHANGES:
            builder_code = config.get(exchange_name, "builder_code", fallback=None)
            wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")
            if not builder_code or not wallet_address:
                self.exchanges[exchange_name] = None
                continue

            # feeInt(정수)만 사용
            fee_int = int(config.get(exchange_name, "fee_rate", fallback="0") or 0)

            self.exchanges[exchange_name] = ccxt.hyperliquid(
                {
                    # api키 발급 받을때 나오는 주소
                    "apiKey": os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY"),
                    # 아래는 api키 발급받을때 나오는 secret key를 의미함.
                    # 지갑 자체 private key 사용해도 상관은 없으나, 보안상 api key 발급형태로 사용하길 바람
                    "privateKey": os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY"),
                    # 계정 주소
                    "walletAddress": wallet_address,
                    "options": {
                        "builder": builder_code,
                        "feeInt": fee_int,
                        "builderFee": True,
                        "approvedBuilderFee": True,
                    },
                }
            )

    async def initialize_all(self):
        # 각 거래소의 initialize_client()를 병렬로 1회 호출
        tasks = []
        for ex in self.exchanges.values():
            if ex:
                tasks.append(ex.initialize_client())
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                # 초기화 실패 시에도 앱은 계속 동작하게 하고 경고만 남김
                logging.warning(f"initialize_all error: {e}")

    async def close_all(self):
        tasks = [ex.close() for ex in self.exchanges.values() if ex]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_exchange(self, name: str):
        return self.exchanges.get(name)