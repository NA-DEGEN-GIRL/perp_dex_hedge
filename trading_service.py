# trading_service.py
import time
from typing import Tuple, Optional #, Dict, Any, Union
from core import ExchangeManager
import os
import logging
from logging.handlers import RotatingFileHandler
from decimal import Decimal, ROUND_HALF_UP #, ROUND_UP, ROUND_DOWN
#from ui_config import ui_print as print

STABLES = ["USDC", "USDH", "USDT0", "USDE"]
STABLES_DISPLAY = ["USDC", "USDH", "USDT", "USDE"]

# 모듈 전용 로거
logger = logging.getLogger(__name__)

def _ensure_ts_logger():
    """
    trading_service.py 전용 파일 핸들러 설정.
    - 기본 파일: ./ts.log (절대경로로 기록)
    - 기본 레벨: INFO
    - 기본 전파: False (루트 핸들러로 중복 기록 방지)
    환경변수:
      PDEX_TS_LOG_FILE=/path/to/ts.log
      PDEX_TS_LOG_LEVEL=DEBUG|INFO|...
      PDEX_TS_LOG_CONSOLE=0|1
      PDEX_TS_PROPAGATE=0|1
    """
    # 이미 붙어 있으면 중복 추가 금지
    if getattr(logger, "_ts_logger_attached", False):
        return

    lvl_name = os.getenv("PDEX_TS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, lvl_name, logging.INFO)
    log_file = os.path.abspath(os.getenv("PDEX_TS_LOG_FILE", "ts.log"))
    to_console = os.getenv("PDEX_TS_LOG_CONSOLE", "0") == "1"
    propagate = os.getenv("PDEX_TS_PROPAGATE", "0") == "1"

    # 포맷
    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

    # 기존에 동일 파일 핸들러가 붙어 있으면 제거(핫리로드 대비)
    to_remove = []
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(getattr(h, "baseFilename", "")) == log_file:
                    to_remove.append(h)
            except Exception:
                pass
    for h in to_remove:
        logger.removeHandler(h)

    # 파일 핸들러
    fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.NOTSET)  # 핸들러는 모듈 로거 레벨만 따르도록
    logger.addHandler(fh)

    # 콘솔 핸들러(옵션)
    if to_console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.NOTSET)
        logger.addHandler(sh)

    # 모듈 로거 레벨/전파 설정
    logger.setLevel(level)
    logger.propagate = propagate

    # 중복 방지 플래그
    logger._ts_logger_attached = True

    # 1회 안내 로그(최초 설정 확인용)
    logger.info("[TS-LOG] attached ts logger level=%s file=%s console=%s propagate=%s",
                lvl_name, log_file, to_console, propagate)
    print("[TS-LOG] attached ts logger level=%s file=%s console=%s propagate=%s",
                lvl_name, log_file, to_console, propagate)

# 모듈 import 시점에 전용 핸들러를 붙인다.
_ensure_ts_logger()

try:
    from exchange_factory import symbol_create
except Exception:
    symbol_create = None
    logger.warning("[mpdex] exchange_factory.symbol_create 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")
    print("[mpdex] exchange_factory.symbol_create 를 찾지 못했습니다. 비-HL 거래소는 비활성화됩니다.")

class TradingService:
    def __init__(self, manager: ExchangeManager):
        self.manager = manager

        #  상태/쿨다운 캐시
        self._last_collateral: dict[str, float] = {}
        self._last_status: dict[str, Tuple[str, str, float]] = {}  # (pos_str, col_str, col_val)
        self._last_balance_at: dict[str, float] = {}               # balance 최근 호출 시각
        logger.info("[TS] init (effective=%s handlers=%d)",
                    logging.getLevelName(logger.getEffectiveLevel()),
                    len(logging.getLogger().handlers))
    
    def format_price_simple(self, px: float) -> str:
        """
        간단 표시 규칙(고정 자릿수 표기, 소수부 0도 유지):
          - abs(px) >= 10      → 소수 2자리
          - 1 <= abs(px) < 10  → 소수 3자리
          - 0.1 <= abs(px) < 1 → 소수 4자리
          - 0.01 <= abs(px) < 0.1  → 소수 5자리
          - 0.001 <= abs(px) < 0.01 → 소수 6자리
          - 그 미만(아주 작은 값) → 소수 6자리(최대)
        """
        try:
            v = float(px)
        except Exception:
            return str(px)

        a = abs(v)
        if a >= 10:
            dec = 2
        elif a >= 1:
            dec = 3
        elif a >= 0.1:
            dec = 4
        elif a >= 0.01:
            dec = 5
        elif a >= 0.001:
            dec = 6
        else:
            dec = 6  # 최대 소수 자리

        q = Decimal(f"1e-{dec}") if dec > 0 else Decimal("1")
        d = Decimal(str(v)).quantize(q, rounding=ROUND_HALF_UP)
        s = format(d, "f")  # comment: 소수부 0 제거하지 않음(고정 자릿수 유지)  <-- FIX

        # 천단위 구분
        return self._format_with_grouping(s)

    def _format_with_grouping(self, s: str) -> str:
        """
        '12345.6700' → '12,345.67', '0.0001200' → '0.00012'
        s는 소수부 0 제거가 이미 반영된 문자열이라고 가정.
        """
        if not s:
            return s
        neg = s.startswith("-")
        if neg:
            s = s[1:]
        if "." in s:
            ip, fp = s.split(".", 1)
        else:
            ip, fp = s, None
        try:
            ip_g = f"{int(ip or '0'):,}"
        except Exception:
            # int 변환 실패 시 안전 폴백
            ip_g = ip or "0"
        out = ip_g if fp is None else f"{ip_g}.{fp}"
        return f"-{out}" if neg else out

    def get_display_builder_fee(self, exchange_name: str, dex: Optional[str], order_type: str) -> Optional[int]:
        """
        HL 카드 우상단 'FEE:' 표기를 위한 표시용 수수료 선택.
        - dex: 'xyz' | 'flx' | 'vntl' | None(HL)
        - order_type: 'market' | 'limit'
        반환: feeInt(int) 또는 None(설정 없음)
        """
        try:
            ex = self.manager.get_exchange(exchange_name)
            if not ex:
                return None
            fee_int, _src, _pair = self._pick_fee_with_reason(ex, dex, order_type)
            return int(fee_int) if fee_int is not None else None
        except Exception:
            return None

    def _pick_fee_with_reason(
        self, ex, dex: Optional[str], order_type: str
    ) -> tuple[Optional[int], str, Optional[tuple[int, int]]]:
        """
        반환: (feeInt 또는 None, source 설명 문자열, 선택된 (limit,market) 페어 또는 None)

        정책(정정):
        - 메인 HL(dex is None): fee_rate만 적용
          * options.feeIntPair -> 사용
        - HIP-3 DEX(dex is not None): dex_fee_rate / xyz_fee_rate 등만 적용
          * options.dexFeeIntPairMap[dex]        -> 사용 (개별 DEX: xyz_fee_rate 등)
          * options.dexFeeIntPairDefault         -> 사용 (공통 DEX: dex_fee_rate)
          * (폴백 허용) options.feeIntPair       -> 사용 (설정 누락 시 마지막 보조)

        order_type: 'limit' → index=0, 'market' → index=1
        """
        try:
            opt = getattr(ex, "builder_fee_pair", {}) or {}
            if not opt:
                opt = getattr(ex, "options", {})
                opt = opt.get("builder_fee_pair",{}) or {}

            idx = 0 if str(order_type).lower() == "limit" else 1

            #logger.info(f"Fee info dex {dex}, idx {idx}, opt {opt}, ")
            #logger.info(opt.get("base"))
            #try:
            #    logger.info(opt.get(dex.lower()) or {})
            #except:
            #    pass
            #logger.info(opt.get("dex") or {})

            # 메인 HL: fee_rate만 사용
            if not dex:
                base_pair = opt.get("base")
                if isinstance(base_pair, (list, tuple)) and len(base_pair) >= 2:
                    return int(base_pair[idx]), "hl:feeIntPair", (int(base_pair[0]), int(base_pair[1]))
                return None, "hl:none", None

            # 1) 개별 DEX 페어 (xyz_fee_rate 등)
            pairs_map = opt.get(dex.lower()) or {}
            if isinstance(pairs_map, (list, tuple)) and len(pairs_map) >= 2:
                return int(pairs_map[idx]), f"dex:{dex.lower()}_fee_rate", (int(pairs_map[0]), int(pairs_map[1]))

            # 2) 공통 DEX 페어 (dex_fee_rate)
            pair_def = opt.get("dex") or {}
            if isinstance(pair_def, (list, tuple)) and len(pair_def) >= 2:
                return int(pair_def[idx]), "dex:dex_fee_rate", (int(pair_def[0]), int(pair_def[1]))

            # 3) (폴백 허용) 기본 페어 (fee_rate) - 설정 누락 보조용
            base_pair = opt.get("base")
            if isinstance(base_pair, (list, tuple)) and len(base_pair) >= 2:
                return int(base_pair[idx]), "fallback:base", (int(base_pair[0]), int(base_pair[1]))

        except Exception as e:
            logger.debug("[FEE] pick reason error: %s", e)
            print("[FEE] pick reason error: %s", e)

        return None, "none", None

    def _to_native_symbol(self, exchange_name: str, coin: str, is_spot: bool = False) -> str:
        exchange_platform = self.manager.get_exchange_platform(exchange_name)
        return symbol_create(exchange_platform, coin, is_spot=is_spot)
        
    def _extract_order_id(self, res) -> Optional[str]:
        if isinstance(res, list):
            res = res[0]
        try:
            oid = self._extract_oid(res)
            if oid:
                return oid
        except Exception:
            pass
        try:
            if not isinstance(res, dict):
                return str(res)
            for k in ("tx_hash", "order_id", "id", "hash"):
                v = res.get(k)
                if v:
                    return str(v)
            return str(res)
        except Exception:
            return str(res)
    
    def _extract_oid(self, raw: dict) -> int | None:
        # for hl
        resp = (raw or {}).get("response") or {}
        data = resp.get("data") or {}
        sts = data.get("statuses") or []
        if sts and isinstance(sts[0], dict):
            # 구현에 따라 key가 다를 수 있어 dict 전체 탐색
            def _find(d: dict, k: str):
                if k in d and isinstance(d[k], int):
                    return d[k]
                for v in d.values():
                    if isinstance(v, dict):
                        r = _find(v, k)
                        if r is not None: return r
                    elif isinstance(v, list):
                        for it in v:
                            if isinstance(it, dict):
                                r = _find(it, k)
                                if r is not None: return r
                return None
            return _find(sts[0], "oid")
        return None
    
    async def fetch_price(self, exchange_name: str, symbol: str, is_spot: bool=False) -> Optional[float]:
        """
        가격 조회:
        - HL: WS 캐시 우선 사용
        - 비-HL: REST API
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            return "N/A"
        
        try:
            native = self._to_native_symbol(exchange_name, symbol, is_spot=is_spot)
            px = await ex.get_mark_price(native)
            return self.format_price_simple(float(px))

        except Exception as e:
            logger.info("[PRICE] %s fetch_price failed: %s", exchange_name, e)
            print("[PRICE] %s fetch_price failed: %s", exchange_name, e)
            return "Error"
    
    async def _fetch_collateral(self, exchange_name: str, ex, symbol: str) -> Tuple[float, dict]:
        """
        [ADD] 공통 collateral 조회 헬퍼.
        
        Returns:
            (col_val, collateral_dict)
            - col_val: perp total collateral 값
            - collateral_dict: {"perp": {...}, "spot": {...}}
        """
        collateral = {"perp": {}, "spot": {}}
        
        try:
            perp_quote = ex.get_perp_quote(symbol)
        except Exception:
            perp_quote = "USD"
        
        try:
            c = await ex.get_collateral()
            col_val = float(c.get("total_collateral") or 0.0)
            collateral["perp"][perp_quote] = col_val
            
            self._last_collateral[exchange_name] = col_val
            self._last_balance_at[exchange_name] = time.monotonic()
            
            # spot collateral
            if "spot" in c:
                spot_map = c.get("spot", {})
                for i, stable in enumerate(STABLES):
                    val = float(spot_map.get(stable, 0) or 0.0)
                    stable_display = STABLES_DISPLAY[i]
                    collateral["spot"][stable_display] = val
            
            return col_val, collateral
            
        except Exception as e:
            logger.info(f"[{exchange_name}] _fetch_collateral error: {e}")
            col_val = self._last_collateral.get(exchange_name, 0.0)
            collateral["perp"][perp_quote] = col_val
            return col_val, collateral
        
    async def fetch_status(
        self,
        exchange_name: str,
        symbol: str,
        need_balance: bool = True,  # [변경] balance 스킵 가능
        need_position: bool = True,    # 포지션 갱신 여부
        is_spot: bool = False,
    ) -> Tuple[str, str, float, dict]:
        """
        - is_spot=True: 선택된 코인의 spot 잔고 조회
        - is_spot=False: 기존 perp 포지션/담보 조회
        """
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            default_json = {"position": None, "collateral": {"perp": {}, "spot": {}}}
            return "📊 포지션: N/A", "💰 잔고: N/A", 0.0, default_json

        is_ws = hasattr(ex, "fetch_by_ws") and getattr(ex, "fetch_by_ws", False)
        
        # 직전 캐시 불러오기
        default_json = {"position": None, "collateral": {"perp": {}, "spot": {}}}
        last = self._last_status.get(
            exchange_name,
            ("📊 포지션: N/A", "💰 잔고: N/A", self._last_collateral.get(exchange_name, 0.0), default_json),
        )

        # === 공통: collateral 조회 ===
        json_data = {
            "position": None,
            "collateral": {"perp": {}, "spot": {}},
            "coin_balance": None,
        }
        
        col_val = self._last_collateral.get(exchange_name, 0.0)
        
        if need_balance or is_ws:
            col_val, json_data["collateral"] = await self._fetch_collateral(exchange_name, ex, symbol)

        # === Spot 모드 ===
        if is_spot:
            coin = symbol.split("/")[0] if "/" in symbol else symbol
            coin_upper = coin.upper()
            
            if need_balance or is_ws:
                try:
                    if hasattr(ex, "get_spot_balance"):
                        spot_balance = await ex.get_spot_balance(coin_upper)
                    else:
                        print(f"{exchange_name} get_spot_balance 없음")
                        spot_balance = {}
                    
                    coin_data = spot_balance.get(coin_upper, {})
                    available = float(coin_data.get("available", 0))
                    total = float(coin_data.get("total", 0))
                    
                    json_data["coin_balance"] = {
                        "coin": coin_upper,
                        "available": available,
                        "locked": float(coin_data.get("locked", 0)),
                        "total": total,
                    }
                    
                    result = ("📊 포지션: -", f"💰 {coin_upper}: {total}", col_val, json_data)
                    self._last_status[exchange_name] = result
                    return result
                    
                except Exception as e:
                    logger.info(f"[{exchange_name}] spot fetch_status error: {e}")
                    print(f"[{exchange_name}] spot fetch_status error: {e}")
                    return "📊 포지션: -", "💰 잔고: Error", 0.0, json_data
            else:
                return last

        # === Perp 모드 ===
        try:
            perp_quote = ex.get_perp_quote(symbol)
        except Exception:
            perp_quote = "USD"
        
        # col_str 생성
        spot_data = json_data["collateral"].get("spot", {})
        has_spot = any(v != 0 for v in spot_data.values())
        
        if has_spot:
            spot_parts = [f"{v:,.1f} {k}" for k, v in spot_data.items() if v != 0]
            spot_str = ", ".join(spot_parts) if spot_parts else "—"
            col_str = f"💰 잔고: [red]PERP[/] {col_val:,.1f} {perp_quote} | [cyan]SPOT[/] {spot_str}"
        else:
            col_str = f"💰 잔고: {col_val:,.1f} {perp_quote}"

        # 포지션 조회
        pos_str = last[0]
        if need_position or is_ws:
            try:
                native = self._to_native_symbol(exchange_name, symbol)
                pos = await ex.get_position(native)
                json_data["position"] = None
                pos_str = "📊 포지션: N/A"
                
                if pos and float(pos.get("size") or 0.0) != 0.0:
                    side_raw = str(pos.get("side") or "").lower()
                    side = "LONG" if side_raw == "long" else "SHORT"
                    size = float(pos.get("size") or 0.0)
                    pnl = float(pos.get("unrealized_pnl") or 0.0)
                    side_color = "green" if side == "LONG" else "red"
                    pnl_color = "green" if pnl >= 0 else "red"
                    pos_str = f"📊 [{side_color}]{side}[/] {size:.5f} PnL: [{pnl_color}]{pnl:,.1f}[/]"
                    
                    json_data["position"] = {
                        "side": side,
                        "size": size,
                        "unrealized_pnl": pnl,
                    }
            except Exception as e:
                logger.info(f"[{exchange_name}] position fetch error: {e}")
                print(f"[{exchange_name}] position fetch error: {e}")

        result = (pos_str, col_str, col_val, json_data)
        self._last_status[exchange_name] = result
        return result
    
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
        is_spot: bool = False,
    ) -> dict:
        logger.info(f"[EXECUTE] start: ex={exchange_name} sym={symbol} side={side} amt={amount} type={order_type}")
        print(f"[EXECUTE] start: ex={exchange_name} sym={symbol} side={side} amt={amount} type={order_type}")
        ex = self.manager.get_exchange(exchange_name)
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")
        
        native = self._to_native_symbol(exchange_name, symbol, is_spot=is_spot)
        if order_type == "limit":
            if price is None:
                raise RuntimeError(f"{exchange_name} limit order requires price")
            res = await ex.create_order(native, side, amount, price=price)
        else:
            res = await ex.create_order(native, side, amount)
        oid = self._extract_order_id(res)
        return {"id": oid, "info": res}
    
    async def close_position(
        self,
        exchange_name: str,
        symbol: str,
        price_hint: Optional[float] = None
    ) -> Optional[dict]:
        """
        현재 포지션을 반대 방향 시장가(reduceOnly=True)로 청산합니다.
        - HIP‑3: WS(webData3)로만 포지션 조회(기존과 동일)
        - 메인 HL: REST(ccxt.fetch_positions/fetch_ticker) 제거, WS(webData3)로만 포지션/가격 조회
        """
        ex = self.manager.get_exchange(exchange_name)
        
        if not ex:
            raise RuntimeError(f"{exchange_name} not configured")

        # 1) mpdex: 라이브러리 close_position 사용
        # get position 때문에 mpdex를 쓰는 hl의 경우는 hl쪽으로
        #if not is_hl_like:
        try:
            native = self._to_native_symbol(exchange_name, symbol)
            pos = await ex.get_position(native)
            if not pos or float(pos.get("size") or 0.0) == 0.0:
                logger.info("[CLOSE] %s non-HL: no position", exchange_name)
                print("[CLOSE] %s non-HL: no position", exchange_name)
                return None
            res = await ex.close_position(native, pos)
            oid = self._extract_order_id(res)
            return {"id": oid, "info": res}
        except Exception as e:
            logger.info(f"[CLOSE] non-HL {exchange_name} failed: {e}")
            print(f"[CLOSE] non-HL {exchange_name} failed: {e}")
            raise