# Hyperliquid Multi‑DEX Trader (TUI)

여러 Hyperliquid 엔진 기반 Perp DEX를 `ccxt` + `textual`로 한 화면에서 동시에 거래하는 터미널 앱입니다.  
- GitHub: https://github.com/NA-DEGEN-GIRL/perp_dex_hedge

본 프로젝트는 빌더 수수료를 Hyperliquid ccxt의 `feeInt`(정수)로만 사용합니다. (`feeRate` 문자열/퍼센트는 사용하지 않음)

---

## 주요 기능

- 여러 Hyperliquid 엔진 기반 DEX 동시 거래
- 심볼 현재가/총 담보(USDC) 표시, 거래소별 포지션/담보 실시간 표시
- 주문 타입(Mkt/Lmt) + Long/Short + 개별 실행(EX) + 전체 실행(EXECUTE ALL)
- OFF(비활성) 토글: EXECUTE ALL 대상 제외 (기본 비활성)
- REPEAT: “횟수 × a~b초 랜덤 간격”으로 EXECUTE ALL 반복 실행 (재클릭으로 즉시 중단)
- 로그 패널(하단) + 파일 로그(`debug.log`)

---

## 1) 설치

### A. Linux / macOS / WSL
```bash
# 0) 저장소 클론
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge

# 1) 가상환경
python3 -m venv .venv
source .venv/bin/activate

# 2) 의존성 설치
pip install -r requirements.in

# 3) 환경 변수 템플릿 복사 후 편집
cp .env.example .env
# .env 내용 편집 (거래소별 지갑/키 입력)

# 4) 빌더 설정(config.ini) 편집
# builder_code / fee_rate(feeInt 정수) 입력

# 5) 실행
python main.py
```

### B. Windows PowerShell
```powershell
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge

py -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -r requirements.in

copy .env.example .env
# 메모장 등으로 .env, config.ini 편집

py main.py
```

---

## 2) 설정

### .env (지갑/키)
`.env.example`을 `.env`로 복사한 후, 각 거래소별 값을 채웁니다:
```env
# Dexari
DEXARI_WALLET_ADDRESS=evm주소
DEXARI_AGENT_API_KEY=api key 발급시 나오는 api key에 해당하는 주소
DEXARI_PRIVATE_KEY=api key 발급시 나오는 private key에 해당 (지갑 private key도 대응가능)

# Liquid
LIQUID_WALLET_ADDRESS=0x...
LIQUID_AGENT_API_KEY=
LIQUID_PRIVATE_KEY=0x...

# Supercexy
SUPERCEXY_WALLET_ADDRESS=0x...
SUPERCEXY_AGENT_API_KEY=
SUPERCEXY_PRIVATE_KEY=0x...

# BasedOne
BASEDONE_WALLET_ADDRESS=0x...
BASEDONE_AGENT_API_KEY=
BASEDONE_PRIVATE_KEY=0x...

# Superstack
SUPERSTACK_WALLET_ADDRESS=0x...
SUPERSTACK_AGENT_API_KEY=
SUPERSTACK_PRIVATE_KEY=0x...

# 새 거래소 추가 시 동일 패턴으로 작성
# NEWDEX_WALLET_ADDRESS=...
# NEWDEX_AGENT_API_KEY=...
# NEWDEX_PRIVATE_KEY=...
```
- 섹션명(예: `[dexari]`)을 대문자화한 접두사(`DEXARI_...`)가 키 이름에 사용됩니다.
- 보안상 Private Key 대신 Agent API Key 생성시 나오는 private key 사용을 권장합니다 (둘 중 하나만 쓰면 됨).

### config.ini (빌더/수수료)
이 앱은 Hyperliquid ccxt `options.feeInt`만 사용합니다.  
`fee_rate` 항목은 “정수(10~50)”로 입력하세요. (퍼센트/문자열 아님)

```ini
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 20
# fee_rate = feeInt (권장 범위 10~50, 본인 tier에 따라 상이)

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
```

> 참고: 코드에서 `options = {'builder': <주소>, 'feeInt': <정수>, 'builderFee': True, 'approvedBuilderFee': True}` 로 설정합니다.  
> 앱 시작 시 초기화(`initialize_all`)가 수행되며, 승인 로직은 `approvedBuilderFee=True`로 간주해 주문 시 빌더가 포함됩니다.

---

## 3) 실행 및 조작

### 화면 구성
- 헤더
  - Ticker: 거래 심볼(기본 BTC, 대소문자 무관)
  - Current Price / Total Collateral: 현재가 / 모든 거래소 담보 합
  - All Qty: 입력 시 각 거래소 카드의 Q(수량)에 일괄 반영
  - EXECUTE ALL: “활성 + 방향(L/S) 선택된” 거래소에 동시 주문
  - REPEAT: 반복 실행 토글(시작 시 REPEAT → 실행 중 STOP)
  - 종료: 앱 종료
- 거래소 카드(각 거래소별)
  - Q: 수량, P: 가격(지정가일 때만 활성)
  - 주문 타입: Mkt(시장가) / Lmt(지정가)
  - 버튼: L(롱), S(숏), EX(개별 실행), OFF(비활성)
  - 상태: 📊 Position / 💰 Collateral

### 활성/비활성(OFF) 규칙
- 앱 시작 시 전 거래소는 OFF(비활성).
- L 또는 S를 누르면 자동 활성화(OFF 해제) → EXECUTE ALL 대상 포함.
- OFF를 누르면 다시 비활성화되며 L/S 선택도 초기화 → EXECUTE ALL 대상 제외.

### EXECUTE ALL
- “활성(OFF 아님)”이며 “L/S가 선택된” 거래소만 주문 전송.
- Market 주문은 Hyperliquid 특성상 가격이 필요(슬리피지 계산). 본 앱은 현재가를 사용합니다.
- Limit 주문은 P(가격) 입력 필수.

### REPEAT(반복 실행)
- 헤더에서 입력:
  - Times: 반복 횟수(정수, 1 이상)
  - Interval(s): a ~ b  (각 실행 사이 랜덤 대기)
- 동작:
  1) REPEAT 클릭 → Times만큼 EXECUTE ALL 반복 실행
  2) 매 회차 사이 `random(a, b)`초 대기
  3) 동작 중 REPEAT(=STOP) 재클릭 → 즉시 중단
  4) 진행 상황은 하단 로그/`debug.log`에 기록

---

## 4) 로그/디버깅

- 화면 하단 Log 패널: 실행/오류/스킵 사유 출력(자동 줄바꿈)
- 파일 로그: `debug.log` (앱 루트 디렉터리)

---

## 5) 성능/안정성 팁

- 기본 갱신 주기: 1초(set_interval 초 단위). 거래소가 많다면 2~5초로 늘리면 안정적일 수 있습니다.
- 앱은 거래소별 시작 지터, 중복 실행 가드, 변경분만 UI 갱신 등으로 부하를 낮추도록 구성되었습니다.
- 터미널에서 버튼 글자가 드래그로 선택되는 현상은 터미널 설정 영향입니다.  
  - tmux: `set -g mouse on`  
  - VSCode: `"terminal.integrated.enableMouseReporting": true`, `"copyOnSelection": false`  
  - Windows Terminal: `"copyOnSelect": false`

---

## 6) 새 거래소 추가

1) `.env`에 지갑/키 추가 (섹션명 대문자 접두사)
```env
NEWDEX_WALLET_ADDRESS=0x...
NEWDEX_AGENT_API_KEY=...
NEWDEX_PRIVATE_KEY=0x...
```
2) `config.ini`에 섹션 추가
```ini
[newdex]
builder_code = 0xYourBuilder
fee_rate = 20    # feeInt(정수)
```
3) `python main.py` 재실행 → 카드가 자동 생성됩니다.  
(builder_code 또는 .env 정보가 비어 있으면 카드가 “설정 없음”으로 비활성 표기됩니다)

---

## 7) FAQ

- Q. set_interval 단위는?  
  A. 초(Seconds)입니다. `1` 또는 `1.0`은 1초 주기를 의미합니다.

- Q. feeInt 범위는?  
  A. 일반적으로 10~50 범위를 사용합니다(본인 tier/정책에 따름). 정수만 입력하세요.

- Q. Market 주문이 실패합니다(가격 관련).  
  A. Hyperliquid는 시장가에도 슬리피지 계산을 위해 기준 가격이 필요합니다. 앱은 현재가를 사용해 전달합니다. 여전히 실패 시 잔고/네트워크/레이트리밋을 확인하세요.

- Q. 버튼 글자가 드래그로 선택됩니다.  
  A. 터미널/멀티플렉서 설정 문제입니다(위 “성능/안정성 팁” 참고).

---

## 8) 의존성

`requirements.in`
```
ccxt
textual
python-dotenv
```