#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hyperliquid WS 데모 (no SDK)
- Perp/Spot 가격 조회 (지속 출력)
- webData3 포지션/마진(전체 및 DEX별) 요약
- Spot 잔고 및 USDC 기준 포트폴리오 가치
사용 예:
  # Perp (DEX 포함)
  python -m hl_related.demo_market_and_user --perp xyz:XYZ100
  # Perp (HL 기본)
  python -m hl_related.demo_market_and_user --perp BTC
  # Spot (페어)
  python -m hl_related.demo_market_and_user --spot UBTC/USDC
  # Spot (베이스만 → USDC 가정)
  python -m hl_related.demo_market_and_user --spot UBTC
  # 주소 포함(포지션/마진/스팟)
  python -m hl_related.demo_market_and_user --address 0xYourAddr --perp BTC --spot UBTC --interval 3
"""

import argparse
import asyncio
import logging
import os
import sys
import signal
import time
from typing import Any, Dict, Optional, Tuple, List

from hl_ws_client_raw import (
    HLWSClientRaw,
    http_to_wss,
    DEFAULT_HTTP_BASE,
)

# -------------------- 출력 유틸 --------------------

def _fmt_num(v: Any, nd: int = 6, none: str = "-") -> str:
    try:
        f = float(v)
        if abs(f) >= 1:
            return f"{f:,.{max(2, min(nd, 8))}f}"
        return f"{f:.{nd}f}"
    except Exception:
        return none

def _fmt_pos_short(coin: str, p: Dict[str, Any]) -> str:
    side = p.get("side") or "flat"
    side_c = "L" if side == "long" else ("S" if side == "short" else "-")
    size = p.get("size") or 0.0
    upnl = p.get("upnl")
    try:
        upnl_s = f"{float(upnl):+.3f}" if upnl is not None else "+0.000"
    except Exception:
        upnl_s = "+0.000"
    return f"{coin} {side_c}{size:g}({upnl_s})"

# -------------------- 도우미 --------------------

def _parse_perp_arg(perp_arg: str) -> tuple[Optional[str], Optional[str]]:
    """
    perp_arg가 'xyz:XYZ100' 형태면 dex를 추출(lower), 심볼은 'xyz:XYZ100'로 유지.
    - 반환: (resolved_perp_symbol, resolved_dex)
    - 우선순위: perp_arg에 DEX가 있으면 dex_opt보다 perp_arg의 DEX를 우선
    """
    if not perp_arg:
        return None, None

    s = perp_arg.strip()
    if ":" in s:
        dex_from_perp, coin = s.split(":", 1)
        dex_final = dex_from_perp.strip().lower()
        perp_final = f"{dex_final}:{coin.strip().upper()}"
        return perp_final, dex_final
    else:
        return s.upper(), None

async def _wait_for_price(client: HLWSClientRaw, symbol: str, is_spot_pair: bool = False, base_only: bool = False,
                          timeout: float = 8.0, poll: float = 0.1) -> Optional[float]:
    """
    가격 대기:
      - is_spot_pair=True       → 'BASE/USDC' 등 페어 가격
      - base_only=True          → BASE 단가(USDC 쿼트 전제)
      - 둘 다 False             → Perp/일반 심볼
    """
    end = time.time() + timeout
    symbol_u = symbol.upper()
    while time.time() < end:
        try:
            if is_spot_pair:
                px = client.get_spot_pair_px(symbol_u)
                if px is not None:
                    return px
            elif base_only:
                px = client.get_spot_px_base(symbol_u)
                if px is not None:
                    return px
            else:
                px = client.get_price(symbol_u)
                if px is not None:
                    return px
        except Exception:
            pass
        await asyncio.sleep(poll)
    return None

async def _wait_for_webdata3(client: HLWSClientRaw, timeout: float = 10.0, poll: float = 0.2) -> bool:
    """webData3(DEX별 margin/positions)가 들어올 때까지 대기."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if client.web3_margin_by_dex:
                return True
        except Exception:
            pass
        await asyncio.sleep(poll)
    return False

# -------------------- 메인 로직 --------------------

async def run_demo(base: str, address: Optional[str],
                   perp_symbol: Optional[str], spot_symbol: Optional[str],
                   interval: float, duration: int, log_level: str) -> int:

    # 로깅 설정
    lvl = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # --perp 에서 DEX 자동 추출
    perp_sym_resolved, dex_resolved = _parse_perp_arg(perp_symbol)

    # 구독할 코인 후보(Perp)
    coins: List[str] = []
    if perp_sym_resolved:
        coins.append(perp_sym_resolved)

    ws_url = http_to_wss(base)

    # HLWSClientRaw가 dex를 필수 인자로 받는 시그니처이므로 반드시 전달
    # 주석: hl_ws_client_raw.py __init__(..., dex: Optional[str], address: Optional[str], coins: List[str], http_base: str)
    client = HLWSClientRaw(ws_url=ws_url, dex=dex_resolved, address=address, coins=coins, http_base=base)
    try:
        client.set_spot_log_level(logging.WARNING)
    except Exception:
        pass

    # spot 메타 선행 로드(페어/토큰 매핑)
    await client.ensure_spot_token_map_http()

    # 연결 및 구독
    await client.connect()
    await client.subscribe()

    # 종료 시그널
    stop_event = asyncio.Event()
    def _on_signal():
        logging.warning("Signal received; shutting down...")
        stop_event.set()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass

    # webData3 준비(주소가 있으면 포지션/마진 출력용)
    if address:
        await _wait_for_webdata3(client, timeout=10.0)

    # 최초 워밍업: 질문된 가격을 한 번 대기(없으면 N/A로 시작)
    async def _warmup():
        tasks = []
        if perp_sym_resolved:
            tasks.append(_wait_for_price(client, symbol=perp_sym_resolved))
        if spot_symbol:
            s = spot_symbol.strip().upper()
            if "/" in s:
                tasks.append(_wait_for_price(client, symbol=s, is_spot_pair=True))
            else:
                tasks.append(_wait_for_price(client, symbol=f"{s}/USDC", is_spot_pair=True))
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=8.0)
            except Exception:
                pass
    await _warmup()

    # 지속 출력 루프
    t0 = time.time()
    try:
        while not stop_event.is_set():
            # 1) Perp
            if perp_sym_resolved:
                sym = perp_sym_resolved
                px = client.get_price(sym)
                print(f"[Perp] {sym}: {_fmt_num(px, 6)}")
            
            # 2) Spot
            if spot_symbol:
                s_in = spot_symbol.strip().upper()
                if "/" in s_in:
                    px = client.get_spot_pair_px(s_in)
                    print(f"[Spot] {s_in}: {_fmt_num(px, 8)}")
                else:
                    pair = f"{s_in}/USDC"
                    px = client.get_spot_pair_px(pair)
                    if px is not None:
                        print(f"[Spot] {pair}: {_fmt_num(px, 8)}")
                    else:
                        # 대체 쿼트 후보 안내
                        found = None
                        try:
                            for k, v in client.spot_pair_prices.items():
                                if k.startswith(f"{s_in}/"):
                                    found = (k, float(v)); break
                        except Exception:
                            pass
                        if found:
                            print(f"[Spot] {pair}: USDC 페어 미발견 → 대체 {found[0]}={_fmt_num(found[1],8)}")
                        else:
                            print(f"[Spot] {pair}: N/A")
            

            # 3) webData3: 마진/포지션(주소가 있을 때)
            if address:
                total_av = client.get_total_account_value_web3()
                print(f"[webData3] Total AV: {_fmt_num(total_av, 6)}")
                for k in client.get_dex_keys():
                    av_k = client.get_account_value_by_dex(k)
                    wd_k = client.get_withdrawable_by_dex(k)
                    print(f"  - {k}: AV={_fmt_num(av_k,6)}  WD={_fmt_num(wd_k,6)}")

                    pos_map = client.get_positions_by_dex(k) or {}
                    if not pos_map:
                        print("    Positions: -")
                    else:
                        items = list(pos_map.items())
                        show = []
                        for coin, pos in items[:5]:
                            show.append(_fmt_pos_short(coin, pos))
                        if len(items) > 5:
                            show.append(f"... +{len(items) - 5} more")
                        print("    " + "; ".join(show))

                # 4) Spot 잔고/포트폴리오
                bals = client.get_all_spot_balances()
                if bals:
                    try:
                        pv = client.get_spot_portfolio_value_usdc()
                        print(f"Spot Portfolio Value (≈USDC): {_fmt_num(pv, 6)}")
                    except Exception:
                        pass
                    print(f"USDC: {bals['USDC']} | USDH: {bals['USDH']}")

            # 줄바꿈 및 대기
            print()
            await asyncio.sleep(max(0.3, float(interval)))
            if duration and (time.time() - t0) >= duration:
                break

    finally:
        await client.close()

    return 0

# -------------------- CLI --------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HL WS Demo - Market & User (no SDK)")
    p.add_argument("--base", type=str, default=DEFAULT_HTTP_BASE, help="API base URL (https→wss 자동). 기본: https://api.hyperliquid.xyz")
    p.add_argument("--address", type=str, default=os.environ.get("HL_ADDRESS", ""), help="지갑 주소(0x...). 포지션/마진/스팟 잔고 표시")
    p.add_argument("--perp", type=str, default=os.environ.get("HL_PERP", ""), help="Perp 심볼 (예: BTC 또는 xyz:XYZ100). 'dex:COIN'이면 dex 자동 추출")
    p.add_argument("--spot", type=str, default=os.environ.get("HL_SPOT", ""), help="Spot 심볼 (예: UBTC 또는 UBTC/USDC)")
    p.add_argument("--interval", type=float, default=3.0, help="지속 출력 주기(초)")
    p.add_argument("--duration", type=int, default=0, help="N초 뒤 종료(0=무한)")
    p.add_argument("--log", type=str, default=os.environ.get("HL_LOG", "INFO"), help="로그 레벨 (DEBUG/INFO/WARNING/ERROR)")
    return p.parse_args(argv or sys.argv[1:])

def main() -> int:
    args = _parse_args()
    return asyncio.run(run_demo(
        base=args.base,
        address=(args.address.strip() or None),
        perp_symbol=(args.perp.strip() or None),
        spot_symbol=(args.spot.strip() or None),
        interval=float(args.interval),
        duration=int(args.duration),
        log_level=args.log,
    ))

if __name__ == "__main__":
    raise SystemExit(main())