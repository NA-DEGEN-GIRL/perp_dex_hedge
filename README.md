# Hyperliquid Multi‑DEX Trader (TUI)

Hyperliquid 엔진을 사용하는 perp DEX들을 `ccxt` + `textual` 기반 한 화면에서 동시에 거래하는 터미널 앱입니다.  
여러 거래소에 같은 심볼의 포지션을 열거나, 방향을 달리해 헤지·파밍용으로 동시 주문을 넣을 수 있습니다.

---

## 기능 요약

- Hyperliquid 엔진 기반 perp DEX 동시 거래 (예: dexari, liquid, based 등)
- 심볼(기본: BTC) 현재가 표시, 각 거래소 포지션/담보(USDC) 실시간 표시
- Market/Limit, Long/Short, 개별 실행(EX), 전체 실행(EXECUTE ALL)
- OFF(비활성) 토글: EXECUTE ALL 대상에서 제외
- 반복 실행(REPEAT): 설정한 횟수만큼, 매 실행 간격을 a~b초 랜덤으로 반복
- 로그 패널(화면 하단) + 파일 로그(`debug.log`)

---

## 설치 요구사항

- Python 3.10 이상 권장
- macOS/Linux/WSL/Windows 터미널 환경
- Hyperliquid Agent Wallet API Key 또는 지갑 Private Key(보안상 API Key 권장)

---

## 빠른 시작

### 1) 저장소 준비 및 가상환경
```bash
cd /root/codes/perp_dex_hedge
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.in
```

### 2) 환경변수(.env) 작성
`.env.example`을 `.env`로 복사 후 거래소별 정보를 채웁니다.
```env
# 예시 (일부)
DEXARI_WALLET_ADDRESS=0x...
DEXARI_AGENT_API_KEY=...
DEXARI_PRIVATE_KEY=0x...

LIQUID_WALLET_ADDRESS=0x...
LIQUID_AGENT_API_KEY=...
LIQUID_PRIVATE_KEY=0x...
```
- 각 거래소 섹션명(예: `[dexari]`)을 대문자로 바꾼 이름이 `.env` 키 접두사가 됩니다.
  - `[dexari]` → `DEXARI_...`
  - `[liquid]` → `LIQUID_...`
  - `[based]` → `BASED_...`
- Agent API Key 발급을 권장합니다(지갑 Private Key 직접 사용 가능하나 보안상 비권장).

### 3) 빌더 설정(config.ini) 작성
```ini
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 20   # 빌더 feeInt (본인 tier에 따라 10~50)

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
```
- `builder_code`: 빌더 주소(0x…)
- `fee_rate`: 정수값(10~50). 본 앱은 `ccxt.hyperliquid`의 per‑order 빌더 값인 `feeInt`로 사용합니다.
- 어떤 거래소든 `builder_code` 또는 `.env`의 지갑 정보가 비어 있으면 앱에서 “설정 없음”으로 비활성 처리됩니다.

### 4) 실행
```bash
python main.py
```

---

## 화면 구성과 조작법

### 헤더 영역
- Ticker: 거래 심볼(기본 BTC, 대소문자 무관)
- Current Price: 현재가
- Total Collateral: 모든 거래소 USDC 담보 합계
- All Qty: 모든 거래소에 일괄 적용할 수량(입력 시 각 카드 Q에 반영)
- EXECUTE ALL: 활성+방향 선택된 거래소에 동시 주문 실행
- REPEAT: 반복 실행 토글(아래 “반복 실행” 참조)
- 종료: 앱 종료

### 거래소 카드(거래소별 1개씩)
- Q: 주문 수량
- P: 주문 가격 (Limit일 때만 활성)
- 주문 타입: Mkt(시장가) / Lmt(지정가)
- L/S/EX/OFF:
  - L: Long 선택(초록)
  - S: Short 선택(빨강)
  - EX: 해당 거래소만 즉시 주문 실행
  - OFF: 비활성(노랑) → EXECUTE ALL 대상에서 제외
- 📊 Position / 💰 Collateral: 포지션/담보 실시간 표시

### 활성/비활성(OFF) 로직
- 앱 시작 시: 모든 거래소는 비활성(OFF) 상태입니다.
- Long 또는 Short 선택 시: 자동 활성화(OFF 해제) → EXECUTE ALL 대상 포함
- OFF 버튼 클릭 시: 비활성화 + L/S 선택 해제 → EXECUTE ALL 대상 제외

---

## EXECUTE ALL (동시 주문)

- “활성(OFF 아님)”이면서 “방향(L/S) 선택된” 거래소만 주문을 전송합니다.
- Market 주문 시, Hyperliquid 라이브러리 특성상 slippage 계산용 가격이 필요해 현재가를 내부적으로 사용합니다.
- Limit 주문 시에는 P(가격)를 입력해야 합니다.

---

## 반복 실행(REPEAT)

헤더에서 다음을 설정합니다.
- REPEAT: 시작/중지 토글(동작 중 라벨이 STOP으로 바뀜)
- Times: 반복 횟수 (정수, 1 이상)
- Interval(s): a ~ b → 매 실행 후 a~b초 사이에서 랜덤 대기 후 다음 실행

동작 규칙:
1. REPEAT 누르면 Times 횟수만큼 `EXECUTE ALL`을 반복 실행합니다.
2. 각 회차 사이 대기시간은 `random(a, b)`초입니다.
3. 동작 중 REPEAT(=STOP)을 다시 누르면 즉시 중단합니다.
4. `EXECUTE ALL` 버튼은 REPEAT 동작 중 비활성화되며, 종료 시 복구됩니다.

---

## 로그

- 화면 하단 Log 패널: 실행/오류/스킵 사유 등을 실시간 출력(자동 줄바꿈)
- 파일 로그: `debug.log`

---

## 빌더/수수료(중요)

- 본 앱은 `options.feeInt`(정수)로 per‑order 빌더 값을 사용합니다. `config.ini`의 `fee_rate`가 곧 `feeInt`입니다.
- Hyperliquid ccxt 내부 로직상 최초 승인(approve) 절차가 필요할 수 있어 앱 시작 시 자동 초기화(`initialize_all`)를 수행합니다.
  - 본 템플릿은 승인 마킹을 `approvedBuilderFee=True`로 둡니다. 필요 시 승인 흐름에 맞게 옵션을 조정하세요.

---

## 새로운 거래소 추가

1) `.env`에 지갑/에이전트 키 추가 (섹션명 대문자 접두사)
```env
NEWDEX_WALLET_ADDRESS=0x...
NEWDEX_AGENT_API_KEY=...
NEWDEX_PRIVATE_KEY=0x...
```

2) `config.ini`에 섹션 추가
```ini
[newdex]
builder_code = 0xYourBuilder
fee_rate = 20
```

3) 앱 재실행  
섹션명이 곧 내부 식별자이며 `.env` 키 접두사는 섹션명의 대문자 형태를 사용합니다.

---

## 자주 묻는 질문(FAQ)

- Q. set_interval의 단위는?  
  A. 초(Seconds)입니다. `1`은 1초, `2.5`도 가능합니다.

- Q. Market 주문이 실패해요(가격 관련 메시지).  
  A. Hyperliquid 라이브러리는 시장가에서 slippage 계산용 가격이 필요합니다. 본 앱은 현재가를 사용해 전달합니다. 여전히 실패 시 네트워크/잔고/레이트리밋을 확인하세요.

- Q. 터미널에서 버튼 문자가 드래그로 선택돼요.  
  A. 터미널/멀티플렉서의 “copy on select / QuickEdit / mouse reporting” 설정을 조정해야 합니다. tmux `set -g mouse on`, VS Code `enableMouseReporting=true` 등 환경 설정을 권장합니다.

- Q. 느린가요?  
  A. 1초 주기로 모든 거래소의 잔고/포지션을 동시에 조회하면 네트워크/레이트리밋 영향으로 지연이 체감될 수 있습니다. 앱은 중복 실행 가드/지터 분산/변경분만 갱신 등을 적용했습니다. 필요 시 주기를 늘리세요.

---

## 주의사항(보안)

- `.env`는 절대 저장소에 커밋하지 마세요.
- 가능하면 Agent API Key를 사용하고, 지갑 Private Key 사용은 피하세요.
- 서버/CI에 배포 시 권한/접근 제어를 엄격하게 관리하세요.

---

## 요구 라이브러리

`requirements.in`
```
ccxt
textual
python-dotenv
```

---

## 실행 스냅샷(예시)

- 헤더: Ticker/현재가/총담보/All Qty/EXECUTE ALL/REPEAT/종료
- 거래소 카드: Q/P/Mkt·Lmt/L/S/EX/OFF + Position/Collateral
- 하단 Log: 동작/오류/반복 상태 등