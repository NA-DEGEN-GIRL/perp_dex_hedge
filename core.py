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
    """
    - exchanges[name] : ccxt 인스턴스 또는 None
    - meta[name]      : {'show': bool, 'hl': bool}
    - visible_names() : show=True 인 거래소 목록
    - first_hl_exchange(): hl=True 이면서 설정/연결된 첫 거래소 인스턴스
    """
    def __init__(self):
        self.exchanges = {}
        self.meta = {}
        for exchange_name in EXCHANGES:
            show = config.get(exchange_name, "show", fallback="True").strip().lower() == "true"
            hl = config.get(exchange_name, "hl", fallback="True").strip().lower() == "true"
            self.meta[exchange_name] = {"show": show, "hl": hl}

            builder_code = config.get(exchange_name, "builder_code", fallback=None)
            wallet_address = os.getenv(f"{exchange_name.upper()}_WALLET_ADDRESS")

            # 하이퍼리퀴드 엔진 거래소만 현재 인스턴스 생성 (hl=True + 키/설정 유효)
            if not hl or not builder_code or not wallet_address:
                self.exchanges[exchange_name] = None
                continue

            # feeInt(정수)만 사용
            fee_int = int(config.get(exchange_name, "fee_rate", fallback="0") or 0)

            self.exchanges[exchange_name] = ccxt.hyperliquid(
                {
                    "apiKey": os.getenv(f"{exchange_name.upper()}_AGENT_API_KEY"),
                    "privateKey": os.getenv(f"{exchange_name.upper()}_PRIVATE_KEY"),
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
                logging.warning(f"initialize_all error: {e}")

    async def close_all(self):
        tasks = [ex.close() for ex in self.exchanges.values() if ex]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_exchange(self, name: str):
        return self.exchanges.get(name)

    def get_meta(self, name: str):
        return self.meta.get(name, {"show": False, "hl": False})

    def visible_names(self):
        return [n for n in EXCHANGES if self.meta.get(n, {}).get("show", False)]

    def all_names(self):
        return list(EXCHANGES)

    def first_hl_exchange(self):
        """hl=True 이고 설정된 첫 ccxt 인스턴스 반환"""
        for n in EXCHANGES:
            m = self.meta.get(n, {})
            if m.get("hl", False) and self.exchanges.get(n):
                return self.exchanges[n]
        return None