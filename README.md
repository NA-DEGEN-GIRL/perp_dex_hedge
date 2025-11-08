# Hyperliquid Multi‑DEX Trader (TUI) — 설치·설정 아주 쉬운 사용 가이드

여러 Hyperliquid 엔진 기반 Perp DEX를 한 화면(TUI)에서 동시에 거래하는 앱입니다.  
- GitHub: https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
- 핵심: “설치 → 설정(.env, config.ini) → 실행”만 하면 됩니다.
- 빌더 수수료는 `feeInt`(정수 10~50)만 사용합니다. (퍼센트 문자열 사용 안 함)

---

## 0. 준비물(필수 프로그램)

초보자도 아래 2가지만 설치되어 있으면 됩니다.

1) Python 3.10 이상  
   - Windows: https://www.python.org/downloads/ 에서 다운로드 → 설치 시 “Add Python to PATH” 체크
   - macOS: 이미 설치되어 있거나 Homebrew(`brew install python@3.11`)로 설치
   - Linux/WSL: 배포판 패키지 또는 python.org 설치 파일 사용

2) Git  
   - Windows: https://git-scm.com/download/win 설치
   - macOS: `xcode-select --install` 또는 https://git-scm.com/download/mac
   - Linux/WSL: 배포판 패키지로 설치(예: `sudo apt install git`)

설치 확인(터미널/명령 프롬프트에서):
```bash
python --version     # 또는 python3 --version
git --version
```
버전이 출력되면 준비 완료입니다.

---

## 1. 코드 내려받기(클론) + 폴더 이동

```bash
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge
```

---

## 2. 가상환경 만들기 + 활성화

프로젝트마다 독립된 파이썬 환경을 쓰면 충돌을 피할 수 있습니다.

- macOS/Linux/WSL
```bash
python3 -m venv .venv
source .venv/bin/activate
```

- Windows PowerShell
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

활성화되면 프롬프트 앞에 `(.venv)` 같은 표시가 붙습니다.

---

## 3. 필수 라이브러리 설치

```bash
pip install -r requirements.in
```

성공하면 `ccxt`, `textual`, `python-dotenv`가 설치됩니다.

---

## 4. 환경변수 파일(.env) 만들기

1) 템플릿 복사 → 편집
```bash
cp .env.example .env
# Windows: copy .env.example .env
```

2) `.env` 파일을 열어(메모장/VSCode 등) 각 거래소별 “지갑 주소/키”를 채웁니다.

예시(.env.example 기반):
```env
# Dexari
DEXARI_WALLET_ADDRESS=0x...             # EVM 지갑 주소
DEXARI_AGENT_API_KEY=...                # Agent API 생성 시 제공되는 API Key (지갑 Private Key 대신 권장)
DEXARI_PRIVATE_KEY=0x...                # 위 API 발급 시 제공되는 secret key (또는 지갑 Private Key 사용 가능)

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

# 새 거래소 추가 시 같은 형식으로 추가
# NEWDEX_WALLET_ADDRESS=0x...
# NEWDEX_AGENT_API_KEY=...
# NEWDEX_PRIVATE_KEY=0x...
```

중요:
- 각 섹션명(예: `[dexari]`)의 “대문자”가 `.env` 키 접두사가 됩니다.  
  예) `[dexari]` → `DEXARI_WALLET_ADDRESS`, `DEXARI_AGENT_API_KEY`, `DEXARI_PRIVATE_KEY`
- Agent API Key(발급형) 사용을 권장합니다. (지갑 Private Key 직접 사용도 가능하지만 보안상 비권장)
- 값이 비어 있거나 주소/키가 틀리면 해당 거래소 카드가 “설정 없음”으로 표시됩니다.

---

## 5. 빌더 설정(config.ini) 채우기

`config.ini`를 열어 각 거래소에 “빌더 주소”와 “feeInt(정수)”를 넣습니다.  
이 앱은 **feeInt만** 사용합니다. (퍼센트 문자열 X)

예시(config.ini):
```ini
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 20   # feeInt(정수). 보통 10~50 범위

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
```

설명:
- `builder_code`: 빌더 주소(0x… 형식)
- `fee_rate`: 정수값(예: 10, 20, 25, 50). 이 값이 Hyperliquid의 per‑order `feeInt`로 쓰입니다.
- 섹션을 추가해도 `.env`가 비어 있으면 해당 거래소는 앱에서 비활성(설정 없음)으로 나옵니다.

---

## 6. 실행

```bash
python main.py
```

정상 실행 시 터미널에 TUI 화면이 나타납니다.

---

## 7. 화면에서 무엇을 하나요?

상단(헤더):
- Ticker: 거래 심볼(기본 BTC) — 대소문자 무관
- Current Price / Total Collateral: 현재가 / 모든 거래소 담보 합계
- All Qty: 여기에 수량을 입력하면 카드의 Q(수량)에 일괄 적용
- EXECUTE ALL: “활성 상태 + 방향 선택(L/S)”인 거래소에 동시 주문
- REPEAT: 반복실행 토글(시작 시 REPEAT → 실행 중 STOP; Times/Interval은 아래 참고)
- 종료: 앱 종료

거래소 카드(거래소마다 한 장씩):
- Q: 수량
- P: 가격(주문 타입이 Lmt(지정가)일 때만 입력/활성)
- 주문 타입: Mkt(시장가) / Lmt(지정가)
- 버튼:
  - L: 롱 선택(초록)
  - S: 숏 선택(빨강)
  - EX: 해당 거래소만 지금 주문 실행
  - OFF: 비활성(노랑) — EXECUTE ALL 대상에서 제외
- 상태 표시:
  - 📊 Position: 포지션 방향/사이즈/PNL
  - 💰 Collateral: 해당 거래소 USDC 담보

중요(“활성/비활성” 규칙):
- 앱 시작 시: 모든 거래소는 OFF(비활성) 상태입니다. → EXECUTE ALL 대상이 아님
- L 또는 S를 누르면 자동으로 “활성”이 되고 EXECUTE ALL 대상에 포함됩니다.
- OFF를 누르면 다시 비활성 + L/S 선택 해제 → EXECUTE ALL 대상에서 제외

---

## 8. EXECUTE ALL & 반복 실행(REPEAT)

EXECUTE ALL:
- “활성(OFF 아님)” + “방향(L/S) 선택된” 거래소만 주문을 보냅니다.
- Market 주문은 Hyperliquid 특성상 가격이 필요(슬리피지 계산)하여 앱의 현재가를 사용합니다.
- Limit 주문은 P(가격)를 직접 입력하세요.

REPEAT(반복 실행):
- 헤더의 입력칸에서 설정:
  - Times: 반복 횟수(정수, 1 이상)
  - Interval(s): a ~ b (각 실행 사이 대기시간을 a~b초 랜덤으로 대기)
- 동작:
  1) REPEAT 누르면 설정한 Times만큼 EXECUTE ALL을 반복합니다.
  2) 매 실행 사이 `random(a, b)`초 대기합니다.
  3) 동작 중 REPEAT(=STOP)을 다시 누르면 즉시 중단됩니다.
  4) 진행상황은 하단 로그와 `debug.log`에 기록됩니다.

---

## 9. 로그/디버깅

- 화면 하단 Log: 실행/오류/건너뜀 사유 등 표시(자동 줄바꿈)
- 파일 로그: 프로젝트 루트의 `debug.log`에 기록됩니다.

---

## 10. 문제 해결(자주 묻는 질문)

Q1) 실행했는데 카드에 “설정 없음”이 보여요.  
A1) 해당 거래소 섹션의 `.env`(지갑/키) 또는 `config.ini`(builder_code/feeInt)가 비어 있거나 잘못되면 비활성 처리됩니다. 키 이름(대문자 접두사)과 값(0x…)을 다시 확인하세요.

Q2) Market 주문이 실패(가격 관련 메시지)합니다.  
A2) Hyperliquid는 시장가도 슬리피지 계산용 가격이 필요합니다. 앱은 현재가를 사용하지만, 네트워크/레이트리밋/잔고 부족 등으로 실패할 수 있습니다. 수량·잔고와 네트워크 상황을 확인하세요.

Q3) 버튼을 누를 때 텍스트가 드래그 선택되는 것처럼 하이라이트돼요.  
A3) 터미널/멀티플렉서의 선택 설정 영향입니다.  
- tmux: `set -g mouse on`  
- VSCode: `"terminal.integrated.enableMouseReporting": true`, `"copyOnSelection": false`  
- Windows Terminal: `"copyOnSelect": false`

Q4) 느려요.  
A4) 1초마다 모든 거래소 잔고/포지션을 조회하면 네트워크/레이트리밋으로 지연이 느껴질 수 있습니다. 거래소가 많다면 실행 주기를 2~5초로 늘리거나(코드 수정 필요), 표시 거래소를 줄이세요. 앱에는 중복 실행 가드/지터/변경분만 갱신 등 최적화가 적용되어 있습니다.

Q5) set_interval 단위가 무엇인가요?  
A5) 초(Seconds)입니다. `1` 또는 `1.0`은 1초 주기입니다.

---

## 11. 새 거래소 추가

1) `.env`에 해당 거래소 지갑/키 추가(섹션명 대문자 접두사 사용):
```env
NEWDEX_WALLET_ADDRESS=0x...
NEWDEX_AGENT_API_KEY=...
NEWDEX_PRIVATE_KEY=0x...
```

2) `config.ini`에 섹션 추가:
```ini
[newdex]
builder_code = 0xYourBuilder
fee_rate = 20    # feeInt(정수)
```

3) `python main.py` 재실행 → 카드가 자동으로 생깁니다.  
(`builder_code` 또는 `.env` 정보가 비어 있으면 “설정 없음”으로 표시됩니다)

---

## 12. 보안 안내(매우 중요)

- `.env`에는 **민감한 키**가 들어갑니다. 절대 Git에 커밋하거나 외부에 공유하지 마세요.
- 가능하다면 지갑 Private Key 대신 “Agent API 발급 키”를 사용하세요.
- 서버/CI에 배포할 때는 파일 권한과 접근 제어를 철저히 관리하세요.

---

## 13. 의존 라이브러리

`requirements.in`
```
ccxt
textual
python-dotenv
```
