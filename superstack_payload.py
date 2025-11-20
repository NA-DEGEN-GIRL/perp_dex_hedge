from __future__ import annotations

from typing import Any, Dict
import aiohttp

# --- 상수 정의 ---
DEFAULT_BASE_URL = "https://wallet-service.superstack.xyz"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "superstack-aiohttp/0.1",
}

# --- 공개 함수 (Public Function) ---
async def get_superstack_payload(
    api_key: str,
    action: Dict[str, Any],
    base_url: str = DEFAULT_BASE_URL,
) -> Dict[str, Any]:
    async with aiohttp.ClientSession(headers=DEFAULT_HEADERS) as session:
        return await _perform_payload_request(api_key, action, base_url, session)

# --- 내부 헬퍼 함수 (Internal Helper Functions) ---
async def _perform_payload_request(
    api_key: str,
    action: Dict[str, Any],
    base_url: str,
    session: aiohttp.ClientSession
) -> Dict[str, Any]:
    """get_superstack_payload의 핵심 로직을 수행합니다."""
    url = f"{base_url.rstrip('/')}/api/exchange"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    req = {"action": action}

    async with session.post(url, headers=headers, json=req) as response:
        await _raise_if_bad_response(response)
        exchange_resp = await response.json()
    
    payload = exchange_resp.get("payload")
    if payload is None:
        raise ValueError("superstack API 응답에서 'payload'를 찾을 수 없습니다.")

    return payload

async def _raise_if_bad_response(resp: aiohttp.ClientResponse) -> None:
    """HTTP 응답 상태 코드가 2xx가 아닐 경우 예외를 발생시킵니다."""
    if 200 <= resp.status < 300:
        return
    
    ctype = resp.headers.get("content-type", "")
    text = await resp.text()

    if "text/html" in ctype.lower():
        # HTML 응답은 보통 WAF나 IP 차단 문제일 가능성이 높음
        raise RuntimeError(f"Request blocked (HTTP {resp.status} HTML). Likely WAF/IP whitelist issue. Body preview: {text[:300]}...")
    
    # JSON 에러 포맷이 일정치 않으므로 원문을 그대로 노출
    raise RuntimeError(f"HTTP {resp.status}: {text[:400]}...")

