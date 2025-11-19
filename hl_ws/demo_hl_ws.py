#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hyperliquid WS 데모 (no SDK)
- Perp/Spot 가격 조회 (지속 출력, DEX별)
- webData3 포지션/마진(DEX별) 요약
- Spot 잔고 및 USDC 기준 포트폴리오 가치

사용 예:
  # Perp (DEX 포함)
  python -m hl_ws.demo_hl_ws --perp xyz:XYZ100
  # Perp (HL 기본)
  python -m hl_ws.demo_hl_ws --perp BTC
  # Spot (페어)
  python -m hl_ws.demo_hl_ws --spot UBTC/USDC
  # Spot (베이스만 → USDC 가정)
  python -m hl_ws.demo_hl_ws --spot UBTC
  # 주소 포함(포지션/마진/스팟)
  python -m hl_ws.demo_hl_ws --address 0xYourAddr --perp BTC --spot UBTC --interval 3
"""

import argparse
import asyncio
import logging
import os
import sys
import signal
import time
from typing import Any, Dict, Optional, Tuple, List

from hl_ws_client import (
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

def _parse_perp_arg(perp_arg: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    perp_arg가 'xyz:XYZ100' 형태면 dex를 추출(lower), 심볼은 'xyz:XYZ100'로 유지.
    반환: (resolved_perp_symbol, resolved_dex)
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

async def _wait_for_webdata3_any(clients: Dict[str, HLWSClientRaw], timeout: float = 10.0, poll: float = 0.2) -> bool:
    """여러 client 중 하나라도 webData3(DEX별 margin/positions)를 받기까지 대기."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            for c in clients.values():
                if getattr(c, "web3_margin_by_dex", None):
                    if c.web3_margin_by_dex:
                        return True
        except Exception:
            pass
        await asyncio.sleep(poll)
    return False

def _resolve_perp_for_scope(scope: str, input_perp: str) -> str:
    """
    입력 perp 심볼(예: 'BTC' 또는 'xyz:XYZ100')을 scope별 쿼리 심볼로 변환.
    - scope == 'hl' → 'COIN'
    - scope != 'hl' → 'scope:COIN'
    """
    s = input_perp.strip().upper()
    if ":" in s:
        _, coin = s.split(":", 1)
        base = coin.strip().upper()
    else:
        base = s
    return base if scope == "hl" else f"{scope}:{base}"

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

    # --perp 에서 DEX 자동 추출(출력 포맷 보조에만 사용)
    perp_sym_resolved, dex_resolved = _parse_perp_arg(perp_symbol)

    http_base = base.rstrip("/") if base else DEFAULT_HTTP_BASE
    ws_host = http_to_wss(http_base)

    # 1) 시작 시 DEX 목록 조회 → scope 리스트 생성
    #dex_list = HLWSClientRaw.discover_perp_dexs_http(http_base)  # ex: ['xyz','flx','vntl']
    scopes = [dex_resolved] if dex_resolved else ["hl"]  # 선택한 스코프만 WS 생성

    # 2) scope별 WS 인스턴스 생성/구독
    clients: Dict[str, HLWSClientRaw] = {}
    for sc in scopes:
        dex_sc = None if sc == "hl" else sc
        c = HLWSClientRaw(
            ws_url=ws_host,
            dex=dex_sc,
            address=address,   # 주소가 있으면 webData3/spotState 동시 구독
            coins=[],          # activeAssetData는 생략
            http_base=http_base
        )
        # spot meta 선행
        await c.ensure_spot_token_map_http()
        await c.connect()
        await c.subscribe()
        clients[sc] = c

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
        await _wait_for_webdata3_any(clients, timeout=10.0)

    # 최초 워밍업: 질문된 가격(Perp/Spot)을 한번 대기(있으면)
    async def _warmup():
        tasks: List[asyncio.Task] = []
        if perp_sym_resolved:
            #for sc in scopes:
            sc = dex_resolved if dex_resolved else "hl"
            sym_sc = _resolve_perp_for_scope(sc, perp_sym_resolved)
            tasks.append(asyncio.create_task(_wait_for_price(clients[sc], symbol=sym_sc)))
        if spot_symbol:
            s = spot_symbol.strip().upper()
            #for sc in scopes:
            sc = "hl"
            if "/" in s:
                tasks.append(asyncio.create_task(_wait_for_price(clients[sc], symbol=s, is_spot_pair=True)))
            else:
                tasks.append(asyncio.create_task(_wait_for_price(clients[sc], symbol=f"{s}/USDC", is_spot_pair=True)))
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
            # 1) Perp 가격 (DEX별)
            if perp_sym_resolved:
                print("Perp Prices by DEX:")
                #for sc in scopes:
                #dex_resolved
                sc = dex_resolved if dex_resolved else 'hl'
                sym_sc = _resolve_perp_for_scope(sc, perp_sym_resolved)
                px = clients[sc].get_price(sym_sc)
                print(f"  [{sc}] {sym_sc}: {_fmt_num(px, 6)}")

            # 2) Spot 가격 (DEX별)
            if spot_symbol:
                s_in = spot_symbol.strip().upper()
                shown_label = s_in if "/" in s_in else f"{s_in}/USDC"
                print("Spot Prices by DEX:")
                sc = 'hl'
                #for sc in scopes:
                if "/" in s_in:
                    px = clients[sc].get_spot_pair_px(s_in)
                else:
                    px = clients[sc].get_spot_pair_px(f"{s_in}/USDC")
                    if px is None:
                        # 대체 쿼트 후보 안내(해당 scope 캐시에서 BASE/ANY)
                        found = None
                        try:
                            for k, v in clients[sc].spot_pair_prices.items():
                                if k.startswith(f"{s_in}/"):
                                    found = (k, float(v)); break
                        except Exception:
                            pass
                        if found:
                            print(f"  [{sc}] {shown_label}: USDC 페어 미발견 → 대체 {found[0]}={_fmt_num(found[1],8)}")
                            continue
                print(f"  [{sc}] {shown_label}: {_fmt_num(px, 8)}")

            # 3) webData3: 마진/포지션(주소가 있을 때)
            if address:
                total_av = 0.0
                print("Account Value by DEX:")
                for sc in scopes:
                    # 동일 주소로 각 WS가 webData3를 받으므로, 같은 값을 읽게 되지만
                    # scope별 client에서 get_account_value_by_dex(sc)로 명시적으로 분리
                    av_sc = clients[sc].get_account_value_by_dex(sc if sc != "hl" else "hl")
                    total_av += float(av_sc or 0.0)
                    print(f"  [{sc}] AV={_fmt_num(av_sc,6)}")

                    pos_map = clients[sc].get_positions_by_dex(sc if sc != "hl" else "hl") or {}
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
                print(f"Total AV (sum): {_fmt_num(total_av, 6)}")

                # 4) Spot 잔고/포트폴리오
                # (모든 scope가 동일 주소의 spotState를 구독하므로 어느 client에서 읽어도 동일)
                any_client = next(iter(clients.values()))
                bals = any_client.get_all_spot_balances()
                if bals:
                    # 상위 10개만 간단히 표시
                    rows = sorted(bals.items(), key=lambda kv: kv[1], reverse=True)[:10]
                    print("Spot Balances (Top by Amount): " + ", ".join([f"{t}={_fmt_num(a, 8)}" for t, a in rows]))
                    try:
                        pv = any_client.get_spot_portfolio_value_usdc()
                        print(f"Spot Portfolio Value (≈USDC): {_fmt_num(pv, 6)}")
                    except Exception:
                        pass

            print()
            await asyncio.sleep(max(0.3, float(interval)))
            if duration and (time.time() - t0) >= duration:
                break

    finally:
        # 모든 scope client를 정리
        for c in clients.values():
            try:
                await c.close()
            except Exception:
                pass

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