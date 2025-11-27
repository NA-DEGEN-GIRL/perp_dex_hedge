import argparse
import asyncio
import json
import os
import random
import re
import signal
import sys
import time
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError
import json
import websockets  # type: ignore
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK  # type: ignore
import logging
from logging.handlers import RotatingFileHandler

ws_logger = logging.getLogger("ws")
def _ensure_ws_logger():
    """
    WebSocket 전용 파일 핸들러를 한 번만 부착.
    - 기본 파일: ./ws.log
    - 기본 레벨: INFO
    - 기본 전파: False (루트 로그와 중복 방지)
    환경변수:
      PDEX_WS_LOG_FILE=/path/to/ws.log
      PDEX_WS_LOG_LEVEL=DEBUG|INFO|...
      PDEX_WS_LOG_CONSOLE=0|1
      PDEX_WS_PROPAGATE=0|1
    """
    if getattr(ws_logger, "_ws_logger_attached", False):
        return

    lvl_name = os.getenv("PDEX_WS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, lvl_name, logging.INFO)
    log_file = os.path.abspath(os.getenv("PDEX_WS_LOG_FILE", "ws.log"))
    to_console = os.getenv("PDEX_WS_LOG_CONSOLE", "0") == "1"
    propagate = os.getenv("PDEX_WS_PROPAGATE", "0") == "1"

    # 포맷 + 중복 핸들러 제거
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    for h in list(ws_logger.handlers):
        ws_logger.removeHandler(h)

    # 파일 핸들러(회전)
    fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.NOTSET)  # 핸들러는 로거 레벨만 따름
    ws_logger.addHandler(fh)

    # 콘솔(옵션)
    if to_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.NOTSET)
        ws_logger.addHandler(sh)

    ws_logger.setLevel(level)
    ws_logger.propagate = propagate
    ws_logger._ws_logger_attached = True
    ws_logger.info("[WS-LOG] attached file=%s level=%s console=%s propagate=%s",
                   log_file, lvl_name, to_console, propagate)

# 모듈 import 시 한 번 설정
_ensure_ws_logger()

DEFAULT_HTTP_BASE = "https://api.hyperliquid.xyz"  # 메인넷
DEFAULT_WS_PATH = "/ws"                            # WS 엔드포인트
WS_CONNECT_TIMEOUT = 15
WS_READ_TIMEOUT = 60
PING_INTERVAL = 20
RECONNECT_MIN = 1.0
RECONNECT_MAX = 8.0

def json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def _sample_items(d: Dict, n: int = 5):
    try:
        return list(d.items())[:n]
    except Exception:
        return []

def _clean_coin_key_for_perp(key: str) -> Optional[str]:
    """
    Perp/일반 심볼 정규화:
    - '@...' 내부키 제외
    - 'AAA/USDC'처럼 슬래시 포함은 Spot 처리로 넘김
    - 그 외 upper()
    """
    if not key:
        return None
    k = str(key).strip()
    if k.startswith("@"):
        return None
    if "/" in k:
        return None
    return k.upper() or None

def _clean_spot_key_from_pair(key: str) -> Optional[str]:
    """
    'AAA/USDC' → 'AAA' (베이스 심볼만 사용)
    """
    if not key:
        return None
    if "/" not in key:
        return None
    base, _quote = key.split("/", 1)
    base = base.strip().upper()
    return base or None

def http_to_wss(url: str) -> str:
    """
    'https://api.hyperliquid.xyz' → 'wss://api.hyperliquid.xyz/ws'
    이미 wss면 그대로, /ws 미포함 시 자동 부가.
    """
    if url.startswith("wss://"):
        return url if re.search(r"/ws($|[\?/#])", url) else (url.rstrip("/") + DEFAULT_WS_PATH)
    if url.startswith("https://"):
        base = re.sub(r"^https://", "wss://", url.rstrip("/"))
        return base + DEFAULT_WS_PATH if not base.endswith("/ws") else base
    return "wss://api.hyperliquid.xyz/ws"

def _sub_key(sub: dict) -> str:
    """구독 payload를 정규화하여 키 문자열로 변환."""
    # type + 주요 파라미터만 안정적으로 정렬
    t = str(sub.get("type"))
    u = (sub.get("user") or "").lower()
    d = (sub.get("dex") or "").lower()
    c = (sub.get("coin") or "").upper()
    return f"{t}|u={u}|d={d}|c={c}"

class HLWSClientRaw:
    """
    최소 WS 클라이언트:
    - 단건 구독 메시지: {"method":"subscribe","subscription": {...}}
    - ping: {"method":"ping"}
    - 자동 재연결/재구독
    - Spot 토큰 인덱스 맵을 REST로 1회 로드하여 '@{index}' 키를 Spot 심볼로 변환
    """

    def __init__(self, ws_url: str, dex: Optional[str], address: Optional[str], coins: List[str], http_base: str):
        self.ws_url = ws_url
        self.http_base = (http_base.rstrip("/") or DEFAULT_HTTP_BASE)
        self.address = address.lower() if address else None
        self.dex = dex.lower() if dex else None
        self.coins = [c.upper() for c in (coins or [])]

        self.conn: Optional[websockets.WebSocketClientProtocol] = None
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task] = []

        # 최신 스냅샷 캐시
        self.prices: Dict[str, float] = {}        # Perp 등 일반 심볼: 'BTC' -> 104000.0
        self.spot_prices: Dict[str, float] = {}         # BASE → px (QUOTE=USDC일 때만)
        self.spot_pair_prices: Dict[str, float] = {}    # 'BASE/QUOTE' → px
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.balances: Dict[str, float] = {}      # 'USDC'/'USDH' 등

        # 재연결 시 재구독용
        self._subscriptions: List[Dict[str, Any]] = []

        # Spot 토큰 인덱스 ↔ 이름 맵
        self.spot_index_to_name: Dict[int, str] = {}
        self.spot_name_to_index: Dict[str, int] = {}

        # [추가] Spot '페어 인덱스(spotInfo.index)' → 'BASE/QUOTE' & (BASE, QUOTE)
        self.spot_asset_index_to_pair: Dict[int, str] = {}
        self.spot_asset_index_to_bq: Dict[int, tuple[str, str]] = {}

        # [추가] 보류(펜딩) 큐를 '토큰 인덱스'와 '페어 인덱스'로 분리
        self._pending_spot_token_mids: Dict[int, float] = {}  # '@{tokenIdx}' 대기분
        self._pending_spot_pair_mids: Dict[int, float] = {}   # '@{pairIdx}'를 쓴 환경 대비(옵션)

        # 매핑 준비 전 수신된 '@{index}' 가격을 보류
        self._pending_spot_mids: Dict[int, float] = {}

        # [추가] 디버깅 히트 카운터(로그 스팸 완화)
        self._spot_log_hits_idx: Dict[int, int] = {}
        self._spot_log_hits_pair: Dict[str, int] = {}


        # [추가] 디버그 타깃(옵션으로 주입)
        self.debug_spot_indexes: set[int] = set()
        self.debug_spot_bases: set[str] = set()
        self.log_raw_allmids: bool = False
        self.spot_log_min_level: int = logging.INFO

        # webData3 기반 캐시
        self.web3_margin: Dict[str, float] = {}                       # {'accountValue': float, 'withdrawable': float, ...}
        self.web3_perp_meta: Dict[str, Dict[str, Any]] = {}           # coin -> {'szDecimals': int, 'maxLeverage': int|None, 'onlyIsolated': bool}
        self.web3_asset_ctxs: Dict[str, Dict[str, Any]] = {}          # coin -> assetCtx(dict)
        self.web3_positions: Dict[str, Dict[str, Any]] = {}           # coin -> position(dict)
        self.web3_open_orders: List[Dict[str, Any]] = []              # raw list
        self.web3_spot_balances: Dict[str, float] = {}                # token -> total
        self.web3_spot_pair_ctxs: Dict[str, Dict[str, Any]] = {}      # 'BASE/QUOTE' -> ctx(dict)
        self.web3_spot_base_px: Dict[str, float] = {}                 # BASE -> px (QUOTE=USDC일 때)
        self.web3_collateral_quote: Optional[str] = None              # 예: 'USDC'
        self.web3_server_time: Optional[int] = None                   # ms
        self.web3_agent: Dict[str, Any] = {}                          # {'address': .., 'validUntil': ..}
        self.web3_positions_norm: Dict[str, Dict[str, Any]] = {}  # coin -> normalized position
        
        # [추가] webData3 DEX별 캐시/순서
        self.web3_dex_keys: List[str] = ["hl", "xyz", "flx", "vntl"]  # 인덱스→DEX 키 매핑 우선순위
        self.web3_margin_by_dex: Dict[str, Dict[str, float]] = {}     # dex -> {'accountValue', 'withdrawable', ...}
        self.web3_positions_by_dex_norm: Dict[str, Dict[str, Dict[str, Any]]] = {}  # dex -> {coin -> norm pos}
        self.web3_positions_by_dex_raw: Dict[str, List[Dict[str, Any]]] = {}         # dex -> raw assetPositions[*].position 목록
        self.web3_asset_ctxs_by_dex: Dict[str, List[Dict[str, Any]]] = {}            # dex -> assetCtxs(raw list)
        self.web3_total_account_value: float = 0.0

        self._send_lock = asyncio.Lock()
        self._active_subs: set[str] = set()  # 이미 보낸 구독의 키 집합

    async def _send_subscribe(self, sub: dict) -> None:
        """subscribe 메시지 전송(중복 방지)."""
        key = _sub_key(sub)
        if key in self._active_subs:
            return
        async with self._send_lock:
            if key in self._active_subs:
                return
            payload = {"method": "subscribe", "subscription": sub}
            await self.conn.send(json.dumps(payload, separators=(",", ":")))
            self._active_subs.add(key)

    async def ensure_core_subs(self) -> None:
        """
        스코프별 필수 구독을 보장:
        - allMids: 가격(이 스코프 문맥)
        - webData3/spotState: 주소가 있을 때만
        """
        # 1) 가격(스코프별)
        if self.dex:
            await self._send_subscribe({"type": "allMids", "dex": self.dex})
        else:
            await self._send_subscribe({"type": "allMids"})
        # 2) 주소 구독(webData3/spotState)
        if self.address:
            await self._send_subscribe({"type": "webData3", "user": self.address})
            await self._send_subscribe({"type": "spotState", "user": self.address})

    async def ensure_subscribe_active_asset(self, coin: str) -> None:
        """
        필요 시 코인 단위 포지션 스트림까지 구독(선택).
        보통 webData3로 충분하므로 기본은 호출 필요 없음.
        """
        sub = {"type": "activeAssetData", "coin": coin}
        if self.address:
            sub["user"] = self.address
        await self._send_subscribe(sub)

    @staticmethod
    def discover_perp_dexs_http(http_base: str, timeout: float = 8.0) -> list[str]:
        """
        POST {http_base}/info {"type":"perpDexs"} → [{'name':'xyz'}, {'name':'flx'}, ...]
        반환: ['xyz','flx','vntl', ...] (소문자)
        """
        url = f"{http_base.rstrip('/')}/info"
        payload = {"type":"perpDexs"}
        headers = {"Content-Type": "application/json"}
        def _post():
            data = json_dumps(payload).encode("utf-8")
            req = urllib_request.Request(url, data=data, headers=headers, method="POST")
            with urllib_request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        try:
            resp = _post()
            out = []
            if isinstance(resp, list):
                for e in resp:
                    n = (e or {}).get("name")
                    if n:
                        out.append(str(n).lower())
            # 중복 제거/정렬
            return sorted(set(out))
        except (HTTPError, URLError):
            return []
        except Exception:
            return []
        
    def _dex_key_by_index(self, i: int) -> str:
        """perpDexStates 배열 인덱스를 DEX 키로 매핑. 부족하면 'dex{i}' 사용."""
        return self.web3_dex_keys[i] if 0 <= i < len(self.web3_dex_keys) else f"dex{i}"

    def set_web3_dex_order(self, order: List[str]) -> None:
        """DEX 표시 순서를 사용자 정의로 교체. 예: ['hl','xyz','flx','vntl']"""
        try:
            ks = [str(k).lower().strip() for k in order if str(k).strip()]
            if ks:
                self.web3_dex_keys = ks
        except Exception:
            pass

    def get_dex_keys(self) -> List[str]:
        """현재 스냅샷에 존재하는 DEX 키(순서 보장)를 반환."""
        present = [k for k in self.web3_dex_keys if k in self.web3_margin_by_dex]
        # web3_dex_keys 외의 임시 dex{i}가 있을 수 있으므로 뒤에 덧붙임
        extras = [k for k in self.web3_margin_by_dex.keys() if k not in present]
        return present + sorted(extras)

    def get_total_account_value_web3(self) -> float:
        """webData3 기준 전체 AV 합계."""
        try:
            return float(sum(float((v or {}).get("accountValue", 0.0)) for v in self.web3_margin_by_dex.values()))
        except Exception:
            return 0.0

    def get_account_value_by_dex(self, dex: Optional[str] = None) -> Optional[float]:
        d = self.web3_margin_by_dex.get((dex or "hl").lower())
        if not d: return None
        try: return float(d.get("accountValue"))
        except Exception: return None

    def get_withdrawable_by_dex(self, dex: Optional[str] = None) -> Optional[float]:
        d = self.web3_margin_by_dex.get((dex or "hl").lower())
        if not d: return None
        try: return float(d.get("withdrawable"))
        except Exception: return None

    def get_margin_summary_by_dex(self, dex: Optional[str] = None) -> Dict[str, float]:
        return dict(self.web3_margin_by_dex.get((dex or "hl").lower(), {}))

    def get_positions_by_dex(self, dex: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        return dict(self.web3_positions_by_dex_norm.get((dex or "hl").lower(), {}))

    def get_asset_ctxs_by_dex(self, dex: Optional[str] = None) -> List[Dict[str, Any]]:
        return list(self.web3_asset_ctxs_by_dex.get((dex or "hl").lower(), []))

    def _update_from_webData3(self, data: Dict[str, Any]) -> None:
        """
        webData3 포맷을 DEX별로 분리 파싱해 내부 캐시에 반영.
        data 구조:
        - userState: {...}
        - perpDexStates: [ { clearinghouseState, assetCtxs, ...}, ... ]  # HL, xyz, flx, vntl 순
        """
        try:
            # userState(참고/보조)
            user_state = data.get("userState") or {}
            
            self.web3_server_time = user_state.get("serverTime") or self.web3_server_time
            if user_state.get("user"):
                self.web3_agent["user"] = user_state.get("user")
            if user_state.get("agentAddress"):
                self.web3_agent["agentAddress"] = user_state["agentAddress"]
            if user_state.get("agentValidUntil"):
                self.web3_agent["agentValidUntil"] = user_state["agentValidUntil"]
            
            dex_states = data.get("perpDexStates") or []
            
            # 누적 합계 재계산
            self.web3_total_account_value = 0.0


            for i, st in enumerate(dex_states):
                dex_key = self._dex_key_by_index(i)
                ch = (st or {}).get("clearinghouseState") or {}
                ms = ch.get("marginSummary") or {}

                # 숫자 변환
                def fnum(x, default=0.0):
                    try: return float(x)
                    except Exception: return default

                margin = {
                    "accountValue": fnum(ms.get("accountValue")),
                    "totalNtlPos":  fnum(ms.get("totalNtlPos")),
                    "totalRawUsd":  fnum(ms.get("totalRawUsd")),
                    "totalMarginUsed": fnum(ms.get("totalMarginUsed")),
                    "crossMaintenanceMarginUsed": fnum(ch.get("crossMaintenanceMarginUsed")),
                    "withdrawable": fnum(ch.get("withdrawable")),
                    "time": ch.get("time"),
                }
                self.web3_margin_by_dex[dex_key] = margin
                self.web3_total_account_value += float(margin["accountValue"])

                # 포지션(정규화/원본)
                norm_map: Dict[str, Dict[str, Any]] = {}
                raw_list: List[Dict[str, Any]] = []
                for ap in ch.get("assetPositions") or []:
                    pos = (ap or {}).get("position") or {}
                    if not pos:
                        continue
                    raw_list.append(pos)
                    coin_raw = str(pos.get("coin") or "")
                    coin_upper = coin_raw.upper()
                    if coin_upper:
                        try:
                            norm = self._normalize_position(pos)
                            # [ADD] 기존 대문자 키
                            norm_map[coin_upper] = norm  # comment: 기존 동작 유지
                            # [ADD] HIP-3 호환: 원문 키도 함께 저장해 조회 경로 다양성 보장
                            if ":" in coin_raw:
                                norm_map[coin_raw] = norm  # comment: 'xyz:XYZ100' 같은 원문 키 추가
                        except Exception:
                            continue
                self.web3_positions_by_dex_raw[dex_key] = raw_list
                self.web3_positions_by_dex_norm[dex_key] = norm_map

                # 자산 컨텍스트(raw 리스트 그대로 저장)
                asset_ctxs = st.get("assetCtxs") or []
                if isinstance(asset_ctxs, list):
                    self.web3_asset_ctxs_by_dex[dex_key] = asset_ctxs

        except Exception as e:
            ws_logger.debug(f"[webData3] update error: {e}", exc_info=True)

    def _normalize_position(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        """
        webData3.clearinghouseState.assetPositions[*].position → 표준화 dict
        반환 키:
        - coin: str
        - size: float(절대값), side: 'long'|'short'
        - entry_px, position_value, upnl, roe, liq_px, margin_used: float|None
        - lev_type: 'cross'|'isolated'|..., lev_value: int|None, max_leverage: int|None
        """
        def f(x, default=None):
            try:
                return float(x)
            except Exception:
                return default
        coin = str(pos.get("coin") or "").upper()
        szi = f(pos.get("szi"), 0.0) or 0.0
        side = "long" if szi > 0 else ("short" if szi < 0 else "flat")
        lev = pos.get("leverage") or {}
        lev_type = str(lev.get("type") or "").lower() or None
        try:
            lev_value = int(float(lev.get("value"))) if lev.get("value") is not None else None
        except Exception:
            lev_value = None
        return {
            "coin": coin,
            "size": abs(float(szi)),
            "side": side,
            "entry_px": f(pos.get("entryPx"), None),
            "position_value": f(pos.get("positionValue"), None),
            "upnl": f(pos.get("unrealizedPnl"), None),
            "roe": f(pos.get("returnOnEquity"), None),
            "liq_px": f(pos.get("liquidationPx"), None),
            "margin_used": f(pos.get("marginUsed"), None),
            "lev_type": lev_type,
            "lev_value": lev_value,
            "max_leverage": (int(float(pos.get("maxLeverage"))) if pos.get("maxLeverage") is not None else None),
            "raw": pos,  # 원본도 보관(디버깅/확장용)
        }

    # [추가] 정규화 포지션 전체 반환(사본)
    def get_positions(self) -> Dict[str, Dict[str, Any]]:
        return dict(self.web3_positions_norm)

    # [추가] 단일 코인의 핵심 요약 반환(사이즈=0 이면 None)
    def get_position_simple(self, coin: str) -> Optional[tuple]:
        """
        반환: (side, size, entry_px, upnl, roe, lev_type, lev_value)
        없거나 size=0이면 None
        """
        p = self.web3_positions_norm.get(coin.upper())
        if not p or not p.get("size"):
            return None
        return (
            p.get("side"),
            float(p.get("size") or 0.0),
            p.get("entry_px"),
            p.get("upnl"),
            p.get("roe"),
            p.get("lev_type"),
            p.get("lev_value"),
        )
    
    def get_account_value(self) -> Optional[float]:
        return self.web3_margin.get("accountValue")

    def get_withdrawable(self) -> Optional[float]:
        return self.web3_margin.get("withdrawable")

    def get_collateral_quote(self) -> Optional[str]:
        return self.web3_collateral_quote

    def get_perp_ctx(self, coin: str) -> Optional[Dict[str, Any]]:
        return self.web3_asset_ctxs.get(coin.upper())

    def get_perp_sz_decimals(self, coin: str) -> Optional[int]:
        meta = self.web3_perp_meta.get(coin.upper())
        return meta.get("szDecimals") if meta else None

    def get_perp_max_leverage(self, coin: str) -> Optional[int]:
        meta = self.web3_perp_meta.get(coin.upper())
        return meta.get("maxLeverage") if meta else None

    def get_position(self, coin: str) -> Optional[Dict[str, Any]]:
        return self.web3_positions.get(coin.upper())

    def get_spot_balance(self, token: str) -> float:
        return float(self.web3_spot_balances.get(token.upper(), 0.0))

    def get_spot_pair_px(self, pair: str) -> Optional[float]:
        """
        스팟 페어 가격 조회(내부 캐시 기반, 우선순위):
        1) web3_spot_pair_ctxs['BASE/QUOTE']의 midPx → markPx → prevDayPx
        2) spot_pair_prices['BASE/QUOTE'] (allMids로부터 받은 숫자)
        3) 페어가 BASE/USDC이면 spot_prices['BASE'] (allMids에서 받은 BASE 단가)
        """
        if not pair:
            return None
        p = str(pair).strip().upper()

        # 1) webData2/3에서 온 페어 컨텍스트가 있으면 거기서 mid/mark/prev 순으로 사용
        ctx = self.web3_spot_pair_ctxs.get(p)
        if isinstance(ctx, dict):
            for k in ("midPx", "markPx", "prevDayPx"):
                v = ctx.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except Exception:
                        continue

        # 2) allMids에서 유지하는 페어 가격 맵(숫자) 사용
        v = self.spot_pair_prices.get(p)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass

        # 3) BASE/USDC인 경우 BASE 단가(spot_prices['BASE'])를 사용
        if p.endswith("/USDC") and "/" in p:
            base = p.split("/", 1)[0].strip().upper()
            v2 = self.spot_prices.get(base)
            if v2 is not None:
                try:
                    return float(v2)
                except Exception:
                    pass

        return None

    def get_spot_px_base(self, base: str) -> Optional[float]:
        return self.web3_spot_base_px.get(base.upper())

    def get_open_orders(self) -> List[Dict[str, Any]]:
        return list(self.web3_open_orders)

    def set_spot_log_level(self, level: int | str) -> None:
        """
        level 예:
          - 숫자: logging.WARNING, ws_logger.ERROR 등
          - 문자열: 'WARNING', 'ERROR', 'INFO', 'DEBUG'
        """
        if isinstance(level, str):
            try:
                lvl = getattr(logger, level.upper())
            except Exception:
                lvl = logging.INFO
        else:
            lvl = int(level)
        self.spot_log_min_level = lvl

    # [추가] 디버깅 로그 헬퍼
    def _spot_log_idx(self, idx: int, level: int, msg: str):
        # 최소 레벨 미만이면 즉시 차단
        if level < self.spot_log_min_level:
            return
        c = self._spot_log_hits_idx.get(idx, 0) + 1
        self._spot_log_hits_idx[idx] = c
        if idx in self.debug_spot_indexes or c in (1, 10, 50) or (c % 100 == 0):
            ws_logger.log(level, f"[spot:@{idx}] {msg}")

    def _spot_log_pair(self, pair: str, level: int, msg: str):
        # 최소 레벨 미만이면 즉시 차단
        if level < self.spot_log_min_level:
            return
        c = self._spot_log_hits_pair.get(pair, 0) + 1
        self._spot_log_hits_pair[pair] = c
        base = pair.split("/", 1)[0] if "/" in pair else pair
        if base in self.debug_spot_bases or c in (1, 10, 50) or (c % 100 == 0):
            ws_logger.log(level, f"[spot:{pair}] {msg}")

    def _log_spot_map_state(self, stage: str):
        ws_logger.info(
            f"[spotMeta/{stage}] tokenMap={len(self.spot_index_to_name)} "
            f"pairMap={len(self.spot_asset_index_to_pair)} "
            f"pending_token_idx={len(self._pending_spot_token_mids)} "
            f"pending_pair_idx={len(self._pending_spot_pair_mids)}"
        )
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            ws_logger.debug(f"[spotMeta/{stage}] token idx->name sample={_sample_items(self.spot_index_to_name,8)}")
            ws_logger.debug(f"[spotMeta/{stage}] pair  idx->name sample={_sample_items(self.spot_asset_index_to_pair,8)}")
            if self._pending_spot_token_mids:
                ws_logger.debug(f"[spotMeta/{stage}] pending token mids sample={_sample_items(self._pending_spot_token_mids,8)}")
            if self._pending_spot_pair_mids:
                ws_logger.debug(f"[spotMeta/{stage}] pending pair mids  sample={_sample_items(self._pending_spot_pair_mids,8)}")

    # ---------------------- REST: spotMeta ----------------------
    async def ensure_spot_token_map_http(self) -> None:
        """
        REST info(spotMeta)를 통해
        - 토큰 인덱스 <-> 이름(USDC, PURR, ...) 맵
        - 스팟 페어 인덱스(spotInfo.index) <-> 'BASE/QUOTE' 및 (BASE, QUOTE) 맵
        을 1회 로드/갱신한다.
        """
        if self.spot_index_to_name and self.spot_asset_index_to_pair:
            self._log_spot_map_state("cached")
            return

        url = f"{self.http_base}/info"
        payload = {"type": "spotMeta"}
        headers = {"Content-Type": "application/json"}

        def _post():
            data = json_dumps(payload).encode("utf-8")
            req = urllib_request.Request(url, data=data, headers=headers, method="POST")
            with urllib_request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            resp = await asyncio.to_thread(_post)
        except (HTTPError, URLError) as e:
            ws_logger.warning(f"[spotMeta] http error: {e}")
            return
        except Exception as e:
            ws_logger.warning(f"[spotMeta] error: {e}")
            return

        try:
            tokens = (resp or {}).get("tokens") or []
            universe = (resp or {}).get("universe") or (resp or {}).get("spotInfos") or []

            # 1) 토큰 맵(spotMeta.tokens[].index -> name)
            idx2name: Dict[int, str] = {}
            name2idx: Dict[str, int] = {}
            for t in tokens:
                if isinstance(t, dict) and "index" in t and "name" in t:
                    try:
                        idx = int(t["index"])
                        name = str(t["name"]).upper().strip()
                        if not name:
                            continue
                        if idx in idx2name and idx2name[idx] != name:
                            ws_logger.warning(f"[spotMeta] duplicate token index {idx}: {idx2name[idx]} -> {name}")
                        idx2name[idx] = name
                        name2idx[name] = idx
                    except Exception as ex:
                        ws_logger.debug(f"[spotMeta] skip token={t} err={ex}")
            self.spot_index_to_name = idx2name
            self.spot_name_to_index = name2idx
            ws_logger.info(f"[spotMeta] loaded tokens={len(idx2name)} (e.g. USDC idx={name2idx.get('USDC')})")

            # 2) 페어 맵(spotInfo.index -> 'BASE/QUOTE' 및 (BASE, QUOTE))
            pair_by_index: Dict[int, str] = {}
            bq_by_index: Dict[int, tuple[str, str]] = {}
            ok = 0
            fail = 0
            for si in universe:
                if not isinstance(si, dict):
                    continue
                # 필수: spotInfo.index
                try:
                    s_idx = int(si.get("index"))
                except Exception:
                    fail += 1
                    continue

                # 우선 'tokens': [baseIdx, quoteIdx] 배열 처리
                base_idx = None
                quote_idx = None
                toks = si.get("tokens")
                if isinstance(toks, (list, tuple)) and len(toks) >= 2:
                    try:
                        base_idx = int(toks[0])
                        quote_idx = int(toks[1])
                    except Exception:
                        base_idx, quote_idx = None, None

                # 보조: 환경별 키(base/baseToken/baseTokenIndex, quote/...)
                if base_idx is None:
                    bi = si.get("base") or si.get("baseToken") or si.get("baseTokenIndex")
                    try:
                        base_idx = int(bi) if bi is not None else None
                    except Exception:
                        base_idx = None
                if quote_idx is None:
                    qi = si.get("quote") or si.get("quoteToken") or si.get("quoteTokenIndex")
                    try:
                        quote_idx = int(qi) if qi is not None else None
                    except Exception:
                        quote_idx = None

                base_name = idx2name.get(base_idx) if base_idx is not None else None
                quote_name = idx2name.get(quote_idx) if quote_idx is not None else None

                # name 필드가 'BASE/QUOTE'면 그대로, '@N' 등인 경우 토큰명으로 합성
                name_field = si.get("name")
                pair_name = None
                if isinstance(name_field, str) and "/" in name_field:
                    pair_name = name_field.strip().upper()
                    # base/quote 이름 보완
                    try:
                        b, q = pair_name.split("/", 1)
                        base_name = base_name or b
                        quote_name = quote_name or q
                    except Exception:
                        pass
                else:
                    if base_name and quote_name:
                        pair_name = f"{base_name}/{quote_name}"

                if pair_name and base_name and quote_name:
                    pair_by_index[s_idx] = pair_name
                    bq_by_index[s_idx] = (base_name, quote_name)
                    ok += 1
                    # 처음 몇 개 샘플 디버깅
                    if logging.getLogger().isEnabledFor(logging.DEBUG) and ok <= 5:
                        ws_logger.debug(f"[spotMeta] pair idx={s_idx} tokens=({base_idx},{quote_idx}) "
                                    f"names=({base_name},{quote_name}) nameField={name_field!r} -> {pair_name}")
                else:
                    fail += 1
                    if logging.getLogger().isEnabledFor(logging.DEBUG) and fail <= 5:
                        ws_logger.debug(f"[spotMeta] FAIL idx={s_idx} raw={si}")

            self.spot_asset_index_to_pair = pair_by_index
            self.spot_asset_index_to_bq = bq_by_index
            ws_logger.info(f"[spotMeta] loaded spot pairs={ok} (fail={fail})")

            if logging.getLogger().isEnabledFor(logging.DEBUG):
                sample_pairs = list(pair_by_index.items())[:10]
                sample_bq = [(k, bq_by_index[k]) for k, _ in sample_pairs if k in bq_by_index]
                ws_logger.debug(f"[spotMeta] pair idx->name sample={sample_pairs}")
                ws_logger.debug(f"[spotMeta] pair idx->(base,quote) sample={sample_bq}")

            # 3) 보류분 소급 적용 — 페어 인덱스(@{pairIdx})
            if self._pending_spot_pair_mids:
                applied = 0
                for s_idx, px in list(self._pending_spot_pair_mids.items()):
                    pair = pair_by_index.get(s_idx)
                    bq = bq_by_index.get(s_idx)
                    if pair and bq:
                        self.spot_pair_prices[pair] = float(px)
                        base, quote = bq
                        if quote == "USDC":
                            self.spot_prices[base] = float(px)
                        if logging.getLogger().isEnabledFor(logging.DEBUG):
                            ws_logger.debug(f"[spotMeta] apply pending @{s_idx} -> {pair} ({base}/{quote}) px={px}")
                        self._pending_spot_pair_mids.pop(s_idx, None)
                        applied += 1
                if applied:
                    ws_logger.info(f"[spotMeta] applied pending pair mids: {applied}")

            # (참고) 토큰 인덱스 보류분이 있을 경우 보조 적용
            if self._pending_spot_token_mids:
                applied_t = 0
                for t_idx, px in list(self._pending_spot_token_mids.items()):
                    name = idx2name.get(t_idx)
                    if name:
                        old = self.spot_prices.get(name)
                        self.spot_prices[name] = float(px)
                        ws_logger.info(f"[spotMeta] apply pending token @{t_idx} -> {name} {old} -> {px}")
                        self._pending_spot_token_mids.pop(t_idx, None)
                        applied_t += 1
                if applied_t:
                    ws_logger.info(f"[spotMeta] applied pending token mids: {applied_t}")

            self._log_spot_map_state("ready")

        except Exception as e:
            ws_logger.warning(f"[spotMeta] parse error: {e}", exc_info=True)
    # ---------------------- 연결/구독 ----------------------

    async def connect(self) -> None:
        ws_logger.info(f"WS connect: {self.ws_url}")
        self.conn = await websockets.connect(self.ws_url, ping_interval=None, open_timeout=WS_CONNECT_TIMEOUT)
        # keepalive task (JSON ping)
        self._tasks.append(asyncio.create_task(self._ping_loop(), name="ping"))
        # listen task
        self._tasks.append(asyncio.create_task(self._listen_loop(), name="listen"))

    async def close(self) -> None:
        self._stop.set()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks.clear()
        if self.conn:
            try:
                await self.conn.close()
            except Exception:
                pass
        self.conn = None

    def build_subscriptions(self) -> List[Dict[str, Any]]:
        subs: list[dict] = []
        # 1) scope별 allMids
        if self.dex:
            subs.append({"type":"allMids","dex": self.dex})
        else:
            subs.append({"type":"allMids"})  # HL(메인)
        # 2) 주소가 있으면 user 스트림(webData3/spotState)도 구독
        if self.address:
            subs.append({"type":"webData3","user": self.address})
            subs.append({"type":"spotState","user": self.address})
        return subs
    
    def _update_spot_balances(self, balances_list: Optional[List[Dict[str, Any]]]) -> None:
        """
        balances_list: [{'coin':'USDC','token':0,'total':'88.2969',...}, ...]
        - web3_spot_balances[token_name] 갱신
        - 레거시 self.balances도 동일 키로 동기화
        """
        if not isinstance(balances_list, list):
            return
        updated = 0
        for b in balances_list:
            try:
                token_name = str(b.get("coin") or b.get("tokenName") or b.get("token")).upper()
                if not token_name:
                    continue
                total = float(b.get("total") or 0.0)
                self.web3_spot_balances[token_name] = total
                # 레거시 캐시도 함께 유지(하위 호환)
                self.balances[token_name] = total
                updated += 1
            except Exception:
                continue

    async def subscribe(self) -> None:
        """
        단건 구독 전송(중복 방지): build_subscriptions() 결과를 _send_subscribe로 보냅니다.
        """
        if not self.conn:
            raise RuntimeError("WebSocket is not connected")

        subs = self.build_subscriptions()
        self._subscriptions = subs  # 재연결 시 재사용

        for sub in subs:
            await self._send_subscribe(sub)
            ws_logger.info(f"SUB -> {json_dumps({'method':'subscribe','subscription':sub})}")

    async def resubscribe(self) -> None:
        if not self.conn or not self._subscriptions:
            return
        # 재연결 시 서버는 이전 구독 상태를 잊었으므로 클라이언트 dedup도 비웁니다.
        self._active_subs.clear()
        for sub in self._subscriptions:
            await self._send_subscribe(sub)
            ws_logger.info(f"RESUB -> {json_dumps({'method':'subscribe','subscription':sub})}")

    # ---------------------- 루프/콜백 ----------------------

    async def _ping_loop(self) -> None:
        """
        WebSocket 프레임 ping이 아니라, 서버 스펙에 맞춘 JSON ping 전송.
        """
        try:
            while not self._stop.is_set():
                await asyncio.sleep(PING_INTERVAL)
                if not self.conn:
                    continue
                try:
                    await self.conn.send(json_dumps({"method": "ping"}))
                    ws_logger.debug("ping sent (json)")
                except Exception as e:
                    ws_logger.warning(f"ping error: {e}")
        except asyncio.CancelledError:
            return

    async def _listen_loop(self) -> None:
        assert self.conn is not None
        ws = self.conn
        while not self._stop.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=WS_READ_TIMEOUT)
            except asyncio.TimeoutError:
                ws_logger.warning("recv timeout; forcing reconnect")
                await self._handle_disconnect()
                break
            except (ConnectionClosed, ConnectionClosedOK):
                ws_logger.warning("ws closed; reconnecting")
                await self._handle_disconnect()
                break
            except Exception as e:
                ws_logger.error(f"recv error: {e}", exc_info=True)
                await self._handle_disconnect()
                break

            # 서버 초기 문자열 핸드셰이크 처리
            if isinstance(raw, str) and raw == "Websocket connection established.":
                ws_logger.debug(raw)
                continue

            try:
                msg = json.loads(raw)
            except Exception:
                ws_logger.debug(f"non-json message: {str(raw)[:200]}")
                continue

            try:
                self._dispatch(msg)
            except Exception:
                ws_logger.exception("dispatch error")

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        """
        서버 메시지 처리:
        - allMids: data = {'mids': { '<symbol or @pairIdx>': '<px_str>', ... } }
        - '@{pairIdx}'는 spotMeta.universe의 spotInfo.index로 매핑
        """
        ch = str(msg.get("channel") or msg.get("type") or "")
        if not ch:
            ws_logger.debug(f"no channel key in message: {msg}")
            return

        if ch == "error":
            data_str = str(msg.get("data") or "")
            if "Already subscribed" in data_str:
                ws_logger.debug(f"[WS info] {data_str}")
            else:
                ws_logger.error(f"[WS error] {data_str}")
            return
        if ch == "pong":
            ws_logger.debug("received pong")
            return

        if ch == "allMids":
            data = msg.get("data") or {}
            if self.log_raw_allmids and logging.getLogger().isEnabledFor(logging.DEBUG):
                ws_logger.debug(f"[allMids/raw] keys={len((data.get('mids') or {}).keys())}")

            if isinstance(data, dict) and isinstance(data.get("mids"), dict):
                mids: Dict[str, Any] = data["mids"]
                n_pair = n_pair_text = n_perp = 0

                for raw_key, raw_mid in mids.items():
                    # 1) '@{pairIdx}' → spotInfo.index
                    if isinstance(raw_key, str) and raw_key.startswith("@"):
                        try:
                            pair_idx = int(raw_key[1:])
                            px = float(raw_mid)
                        except Exception:
                            continue

                        pair_name = self.spot_asset_index_to_pair.get(pair_idx)   # 'BASE/QUOTE'
                        bq_tuple  = self.spot_asset_index_to_bq.get(pair_idx)     # (BASE, QUOTE)
                        asset_id  = 10000 + pair_idx                               # 공식: spot asset id

                        if not pair_name or not bq_tuple:
                            # 페어 맵 미준비 → 보류
                            self._pending_spot_pair_mids[pair_idx] = px
                            self._spot_log_idx(pair_idx, logging.DEBUG, f"pending pair map (px={px})")
                            continue

                        base, quote = bq_tuple

                        # 1-1) 페어 가격 캐시
                        self.spot_pair_prices[pair_name] = px
                        
                        # 1-2) 쿼트가 USDC인 경우 base 단일 가격도 채움
                        if quote == "USDC":
                            self.spot_prices[base] = px
                            

                        n_pair += 1
                        continue

                    # 2) 텍스트 페어 'AAA/USDC' → pair 캐시, USDC 쿼트면 base 캐시
                    maybe_spot_base = _clean_spot_key_from_pair(raw_key)
                    if maybe_spot_base:
                        try:
                            px = float(raw_mid)
                        except Exception:
                            px = None
                        if px is not None:
                            pair_name = raw_key.strip().upper()
                            self.spot_pair_prices[pair_name] = px
                            
                            if pair_name.endswith("/USDC"):
                                self.spot_prices[maybe_spot_base] = px
                                
                        n_pair_text += 1
                        continue

                    # 3) Perp/기타 심볼
                    perp_key = _clean_coin_key_for_perp(raw_key)
                    if not perp_key:
                        continue
                    try:
                        px = float(raw_mid)
                    except Exception:
                        continue
                    self.prices[perp_key] = px
                    n_perp += 1

                if logging.getLogger().isEnabledFor(logging.DEBUG):
                    ws_logger.debug(f"[allMids] counts: pairIdx={n_pair}, spot_text={n_pair_text}, perp={n_perp}")
            else:
                ws_logger.debug(f"[allMids] unexpected payload shape: {type(data)}")
            return

        # 포지션(코인별)
        elif ch == "spotState":
            # 예시 구조: {'channel':'spotState','data':{'user': '0x...','spotState': {'balances': [...]}}}
            data_body = msg.get("data") or {}
            spot = data_body.get("spotState") or {}
            balances_list = spot.get("balances") or []
            self._update_spot_balances(balances_list)

            # 짧은 요약 로그
            if logging.getLogger().isEnabledFor(logging.INFO):
                n = len(balances_list) if isinstance(balances_list, list) else 0
                sample = None
                try:
                    for b in balances_list:
                        if str(b.get("coin") or "").upper() in ("USDC","USDH"):
                            sample = (b.get("coin"), b.get("total")); break
                    if not sample and n:
                        sample = (balances_list[0].get("coin"), balances_list[0].get("total"))
                except Exception:
                    pass
                
            return
            
        # 유저 스냅샷(잔고 등)
        elif ch == "webData3":
            
            data_body = msg.get("data") or {}
            
            self._update_from_webData3(data_body)

            if logging.getLogger().isEnabledFor(logging.NOTSET):
                def _fmt_num(v, nd=4):
                    try:
                        f = float(v)
                        return f"{f:.{nd}f}"
                    except Exception:
                        return "N/A"

                def _fmt_pos_short(coin: str, p: Dict[str, Any]) -> str:
                    side = p.get("side") or "flat"
                    side_c = "L" if side == "long" else ("S" if side == "short" else "-")
                    size = p.get("size") or 0.0
                    upnl = p.get("upnl")
                    try:
                        upnl_s = f"{float(upnl):+.3f}" if upnl is not None else "+0.000"
                    except Exception:
                        upnl_s = "+0.000"
                    # 예: BTC L0.001(+0.359)
                    return f"{coin} {side_c}{size:g}({upnl_s})"

                total_av = self.get_total_account_value_web3()
                dex_logs = []
                for dex_key in self.get_dex_keys():
                    av_k = self.get_account_value_by_dex(dex_key)
                    wd_k = self.get_withdrawable_by_dex(dex_key)

                    # 포지션 요약(상위 최대 2개, 나머지는 +N 표기)
                    pos_map = self.get_positions_by_dex(dex_key) or {}
                    if pos_map:
                        items = list(pos_map.items())
                        show = []
                        for coin, pos_norm in items[:2]:
                            show.append(_fmt_pos_short(coin, pos_norm))
                        if len(items) > 2:
                            show.append(f"+{len(items)-2}")
                        pos_str = ", ".join(show)
                    else:
                        pos_str = "-"

                    dex_logs.append(
                        f"\n\t\t{dex_key}:{_fmt_num(av_k)} pos={pos_str}"
                        if (av_k is not None and wd_k is not None)
                        else f"{dex_key}:N/A pos={pos_str}"
                    )

                ws_logger.info(f"[webData3] totalAV={_fmt_num(total_av, nd=6)} | " + " | ".join(dex_logs))

        else:
            ws_logger.debug(f"[{ch}] {str(msg)[:300]}")

    async def _handle_disconnect(self) -> None:
        await self._safe_close_only()
        await self._reconnect_with_backoff()

    async def _safe_close_only(self) -> None:
        if self.conn:
            try:
                await self.conn.close()
            except Exception:
                pass
        self.conn = None

    async def _reconnect_with_backoff(self) -> None:
        delay = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                await asyncio.sleep(delay)
                await self.connect()
                await self.resubscribe()
                return
            except Exception as e:
                ws_logger.warning(f"reconnect failed: {e}")
                delay = min(RECONNECT_MAX, delay * 2.0) + random.uniform(0.0, 0.5)

    # ---------------------- 유틸/스냅샷/쿼리 ----------------------

    def snapshot(self) -> Dict[str, Any]:
        return {
            "prices": dict(self.prices),
            "spot_prices": dict(self.spot_prices),
            "spot_pair_prices": dict(self.spot_pair_prices),
            "positions": dict(self.positions),
            "balances": dict(self.balances),              # 레거시
            "spot_balances": dict(self.web3_spot_balances),  # [추가] 권장
            "pending_spot_token_mids": dict(self._pending_spot_token_mids),
            "pending_spot_pair_mids": dict(self._pending_spot_pair_mids),
        }

    def print_snapshot(self) -> None:
        snap = self.snapshot()
        ws_logger.info(
            f"[snapshot] perp={len(snap['prices'])} "
            f"spot_base={len(snap['spot_prices'])} "
            f"spot_pair={len(snap['spot_pair_prices'])} "
            f"pending_token_idx={len(snap['pending_spot_token_mids'])} "
            f"pending_pair_idx={len(snap['pending_spot_pair_mids'])}"
        )
        ws_logger.info(f"[snapshot] perp(sample)={_sample_items(snap['prices'])}")
        ws_logger.info(f"[snapshot] spot_base(sample)={_sample_items(snap['spot_prices'])}")
        ws_logger.info(f"[snapshot] spot_pair(sample)={_sample_items(snap['spot_pair_prices'])}")

    def get_price(self, symbol: str) -> Optional[float]:
        """Perp/일반 심볼 가격 조회(캐시)."""
        return self.prices.get(symbol.upper())

    def get_spot_price(self, symbol: str) -> Optional[float]:
        """Spot 심볼 가격 조회(캐시)."""
        return self.spot_prices.get(symbol.upper())

    def get_all_spot_balances(self) -> Dict[str, float]:
        return dict(self.web3_spot_balances)

    def get_spot_portfolio_value_usdc(self) -> float:
        """
        USDC 기준 추정 총가치:
        - USDC = 1.0
        - 기타 토큰은 BASE/USDC 단가(self.spot_prices 또는 spot_pair_ctxs의 mid/mark/prev) 사용
        - 가격을 알 수 없는 토큰은 0으로 계산
        """
        total = 0.0
        for token, amt in self.web3_spot_balances.items():
            try:
                if token == "USDC":
                    px = 1.0
                else:
                    # 우선 캐시된 BASE/USDC mid/mark/prev 기반
                    px = self.spot_prices.get(token)
                    if px is None:
                        # spot_pair_ctxs에 'TOKEN/USDC'가 있으면 그 값을 사용
                        pair = f"{token}/USDC"
                        ctx = self.web3_spot_pair_ctxs.get(pair)
                        if isinstance(ctx, dict):
                            for k in ("midPx","markPx","prevDayPx"):
                                v = ctx.get(k)
                                if v is not None:
                                    try:
                                        px = float(v); break
                                    except Exception:
                                        continue
                if px is None:
                    continue
                total += float(amt) * float(px)
            except Exception:
                continue
        return float(total)

# ---------------------- CLI/메인 ----------------------

def _parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hyperliquid WS raw client (no SDK)")
    p.add_argument("--base", type=str, default=DEFAULT_HTTP_BASE,
                   help="API base URL (https를 wss로 자동 변환). 기본: https://api.hyperliquid.xyz")
    p.add_argument("--dex", type=str, default=os.environ.get("HL_DEX", ""),
                   help="dex (xyz, flx, vntl) 필요시, 없으면 생략")
    p.add_argument("--address", type=str, default=os.environ.get("HL_ADDRESS", ""),
                   help="지갑 주소(0x...). 없으면 유저 전용 구독은 생략")
    p.add_argument("--coins", type=str, default=os.environ.get("HL_COINS", "BTC"),
                   help="activeAssetData 구독할 코인 목록 CSV (기본: BTC)")
    p.add_argument("--log", type=str, default=os.environ.get("HL_LOG", "INFO"),
                   help="로그 레벨 (DEBUG/INFO/WARNING/ERROR)")
    p.add_argument("--duration", type=int, default=int(os.environ.get("HL_DURATION", "0")),
                   help="N초 동안만 실행 후 종료(0=무한)")
    p.add_argument("--debug-spot-indexes", type=str, default="", help="집중 로그할 spotInfo index CSV (예: 0,142)")
    p.add_argument("--debug-spot-bases", type=str, default="", help="집중 로그할 BASE 심볼 CSV (예: BTC,ETH,PURR)")
    p.add_argument("--log-raw-allmids", action="store_true", help="allMids 원시 키 개수 DEBUG 출력")
    # [추가] spot 전용 로그 최소 레벨(기본은 코드에서 WARNING으로 셋)
    p.add_argument("--spot-log-level", type=str, default=os.environ.get("HL_SPOT_LOG_LEVEL", ""),
                   help="spot 로그 최소 레벨 설정 (DEBUG/INFO/WARNING/ERROR). 기본: WARNING")
    return p.parse_args(argv)

async def _amain(args: argparse.Namespace) -> int:
    # 1) 원하는 최소 출력 레벨 지정(여기서는 WARNING 이상만)
    target_level = logging.INFO

    # 2) 루트 로거 강제 재구성(force=True). (Py>=3.8)
    try:
        ws_logger.basicConfig(
            level=target_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[ws_logger.StreamHandler(sys.stdout)],
            force=True,  # 기존 핸들러/설정을 모두 무시하고 강제 재설정
        )
    except TypeError:
        # Py<3.8 fallback: 기존 핸들러 제거 후 재설정
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        ws_logger.basicConfig(
            level=target_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[ws_logger.StreamHandler(sys.stdout)],
        )

    # 3) 전역적으로 INFO/DEBUG 비활성화 (WARNING 이상만 출력)
    #    disable는 "이 레벨 이하"를 전부 막습니다. INFO(20) → INFO/DEBUG 차단.
    #ws_logger.disable(logging.INFO)
    

    # 4) 서드파티 로거(있다면)도 WARNING 이상으로 격상
    for name in ("websockets", "asyncio"):
        try:
            lg = logging.getLogger(name)
            lg.setLevel(target_level)
            for h in lg.handlers:
                h.setLevel(target_level)
        except Exception:
            pass

    # 5) 나머지 부트스트랩
    ws_url = http_to_wss(args.base)
    addr = args.address.strip() or None
    dex = args.dex.strip().lower() or None
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]

    # 아래 INFO 로그는 disable(INFO) 때문에 출력되지 않습니다.
    ws_logger.info(f"Start WS (url={ws_url}, dex={dex} addr={addr}, coins={coins})")

    client = HLWSClientRaw(ws_url=ws_url, dex=dex, address=addr, coins=coins, http_base=args.base)


    if getattr(args, "spot_log_level", None):
        client.set_spot_log_level(args.spot_log_level)
    else:
        client.set_spot_log_level(logging.WARNING)

    # 선택 디버그 타깃 주입(필요 없으면 유지해도 무관)
    if getattr(args, "debug_spot_indexes", None):
        try:
            client.debug_spot_indexes = {int(x.strip()) for x in args.debug_spot_indexes.split(",") if x.strip()}
        except Exception:
            pass
    if getattr(args, "debug_spot_bases", None):
        client.debug_spot_bases = {x.strip().upper() for x in args.debug_spot_bases.split(",") if x.strip()}
    client.log_raw_allmids = bool(getattr(args, "log_raw_allmids", False))

    stop_event = asyncio.Event()

    def _on_signal():
        # WARNING 이상이므로 출력됨
        ws_logger.warning("Signal received; shutting down...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            pass

    try:
        await client.ensure_spot_token_map_http()
        await client.connect()
        await client.subscribe()

        t0 = time.time()
        while not stop_event.is_set():
            # INFO 레벨 → disable(INFO)로 인해 출력되지 않음
            client.print_snapshot()
            await asyncio.sleep(5.0)
            if getattr(args, "duration", 0) and (time.time() - t0) >= args.duration:
                ws_logger.info("Duration reached, exiting.")  # 이 로그도 보이지 않음
                break
    finally:
        await client.close()

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())