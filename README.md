# Hyperliquid Multi-DEX Trader (urwid TUI)

여러 Hyperliquid 엔진 기반 Perp DEX를 터미널 UI(urwid)에서 동시에 거래하는 앱입니다.

- **GitHub**: https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
- **기본 UI**: urwid (경량·빠름·안정) — 앞으로도 urwid 중심으로 개발됩니다.
- **Textual**: 더 이상 권장하지 않으며 레거시로만 남아 있습니다. (`--ui textual`로 실행 가능하지만 비권장)

---

## 특징

- 여러 Hyperliquid 엔진 기반 DEX 동시 거래
- 심볼(BTC 등) 현재가·총 담보(USDC)·포지션·PNL 실시간 표시
- Market/Limit, Long/Short, 개별 실행(EX), 전체 실행(EXECUTE ALL), 방향 일괄 반전(REVERSE)
- OFF(비활성) 토글: EXECUTE ALL 대상 제외 (기본 비활성)
- REPEAT: "횟수 × a~b초 랜덤 간격" 반복 실행 (재클릭으로 즉시 중단)
- Exchanges 토글: show=False 거래소도 실행 중 표시/숨김 전환 가능
- 로그 패널(자동 스크롤) + 파일 로그(`debug.log`)
- 키보드 중심 조작: Tab/Shift+Tab(입력·버튼 간 이동), Shift+Up/Down(영역 전환), 방향키(세부 이동)

## 미개발된 부분
- 지정가 주문 취소 불가 (앱에 들어가서 해야함), 현재는 시장가 주문만 사용하길 권장함
- 현재 hyperliquid 엔진 외의 타 거래소에 대해 미지원

---

## 0. 준비물(필수)

1. **Python 3.10 이상**
   - Windows: https://www.python.org/downloads/ 설치 시 "Add Python to PATH" 체크
   - macOS: 기본 설치되어 있거나 `brew install python@3.11`
   - Linux/WSL: `sudo apt install python3 python3-pip`

2. **Git**
   - Windows: https://git-scm.com/download/win
   - macOS: `xcode-select --install`
   - Linux/WSL: `sudo apt install git`

설치 확인:
```bash
python --version   # 또는 python3 --version
git --version
```

---

## 1. 설치

### A. 저장소 클론
```bash
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge
```

### B. 가상환경 생성·활성화
- **macOS/Linux/WSL**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

- **Windows PowerShell**
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

활성화되면 프롬프트 앞에 `(.venv)` 표시가 붙습니다.

### C. 의존성 설치
```bash
pip install -r requirements.in
```

성공하면 `ccxt`, `urwid`, `textual`, `python-dotenv`가 설치됩니다.

---

## 2. 설정

### A. 환경변수(.env) — 지갑·키
1. 템플릿 복사
```bash
cp .env.example .env
# Windows: copy .env.example .env
```

2. `.env` 파일을 편집기(메모장/VSCode/nano 등)로 열어 각 거래소 정보 입력:
```env
# Dexari
DEXARI_WALLET_ADDRESS=0x...             # EVM 지갑 주소
DEXARI_AGENT_API_KEY=...                # Agent API Key (권장)
DEXARI_PRIVATE_KEY=0x...                # Agent secret key 또는 지갑 Private Key

# Liquid
LIQUID_WALLET_ADDRESS=0x...
LIQUID_AGENT_API_KEY=
LIQUID_PRIVATE_KEY=0x...

# 필요한 거래소만 채우고, 나머지는 비워도 됩니다.
```

**중요**:
- 섹션명(예: `[dexari]`)을 **대문자**화한 접두사가 `.env` 키 이름이 됩니다.  
  `[dexari]` → `DEXARI_WALLET_ADDRESS`
- **Agent API Key 사용 권장** (지갑 Private Key 직접 사용은 보안상 비권장)
- 값이 비어 있으면 해당 거래소는 "설정 없음"으로 표시됩니다.

### B. 빌더 설정(config.ini) — 빌더·수수료
`config.ini`를 열어 편집:
```ini
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 20     # feeInt(정수). 본인 tier에 따라 10~50
show = True       # 화면 표시 여부 (True/False)
hl = True         # Hyperliquid 엔진 여부 (True/False)

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50
show = True
hl = True

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
show = False      # 기본 숨김, Exchanges 박스에서 토글 가능
hl = True
```

**설명**:
- `builder_code`: 빌더 주소 (0x…)
- `fee_rate`: 정수 (10~50). Hyperliquid per-order `feeInt`로 사용
- `show`: True/False — 앱 시작 시 화면에 표시 여부
- `hl`: True/False — Hyperliquid 엔진 사용 여부 (현재는 hl=True만 지원, 추후 다른 엔진 확장 예정)

---

## 3. 실행

```bash
python main.py
```

기본은 **urwid** UI입니다. (Textual을 쓰려면 `python main.py --ui textual`, 단 비권장)

---

## 4. 화면 구성 및 조작

### 헤더(상단 3행)
**1행**: Ticker / Price / Total / QUIT  
**2행**: All Qty / EXECUTE ALL / REVERSE  
**3행**: REPEAT / Times / min(s) / max(s)

- **Ticker**: 거래 심볼 (기본 BTC, 대소문자 무관)
- **Price**: HL 엔진 공유 현재가
- **Total**: 모든 거래소 담보 합계
- **All Qty**: 입력 시 모든 카드의 Q(수량)에 일괄 반영
- **EXECUTE ALL**: 활성 + 방향 선택된 거래소에 동시 주문
- **REVERSE**: 활성 거래소의 LONG↔SHORT 일괄 반전
- **REPEAT**: 반복 실행 토글 (Times회, min~max초 랜덤 간격)
- **QUIT**: 앱 종료

### 거래소 카드(본문, 각 거래소별)
- **Q**: 수량
- **P**: 가격 (Limit일 때만 활성)
- **MKT/LMT**: 시장가 ↔ 지정가 토글
- **L**: 롱 선택 (초록)
- **S**: 숏 선택 (빨강)
- **OFF**: 비활성화 (노랑) — EXECUTE ALL 대상 제외
- **EX**: 개별 주문 실행
- **상태**: 📘 Position / 💰 Collateral (실시간 갱신)

### Exchanges 박스(하단)
- 모든 거래소 체크박스 (show=True/False 토글)
- 체크 ON → 카드 표시 + 활성화 가능
- 체크 OFF → 카드 숨김 + OFF 간주

### Logs(맨 아래)
- 주문/오류/건너뛴 사유 표시
- 자동 스크롤 + 파일 로그(`debug.log`)

---

## 5. 키보드 단축키

### 영역 전환
- **Ctrl+Down/Up** (또는 Alt/Shift+Down/Up, PageDown/Up, Ctrl+J/K, F6): 헤더 ⇄ 본문(거래소) ⇄ 푸터(Exchanges/Logs) 순환

### 내부 이동
- **Tab**: 현재 영역 내 다음 입력·버튼으로 (본문에서는 Q→P→MKT→L→S→OFF→EX→다음 카드 Q)
- **Shift+Tab**: 역방향 (EX→OFF→S→L→MKT→P→Q→이전 카드 EX)
- **방향키(←/→/↑/↓)**: 세부 이동 (urwid 기본)

### 래핑(순환)
- 마지막 카드의 EX에서 Tab → 첫 카드의 Q
- 첫 카드의 Q에서 Shift+Tab → 마지막 카드의 EX

---

## 6. 활성/비활성 규칙

- **앱 시작 시**: 모든 거래소는 OFF (비활성)
- **L/S 클릭 시**: 자동 활성화 → EXECUTE ALL 대상 포함
- **OFF 클릭 시**: 비활성화 + L/S 선택 해제 → EXECUTE ALL 대상 제외

---

## 7. EXECUTE ALL & REVERSE & REPEAT

### EXECUTE ALL
- "활성(OFF 아님)" + "방향(L/S) 선택된" 거래소만 주문
- Market 주문: HL 공유 현재가 사용 (슬리피지 계산용)
- Limit 주문: P(가격) 입력 필수

### REVERSE
- 활성 거래소의 선택 방향을 일괄 반전 (LONG→SHORT, SHORT→LONG)
- OFF·미선택 거래소는 그대로

### REPEAT
- Times, min(s), max(s) 입력 후 REPEAT 클릭
- Times회 반복, 매 실행 사이 min~max초 랜덤 대기
- 실행 중 REPEAT 재클릭 → 즉시 중단
- 로그와 `debug.log`에 진행 상황 기록

---

## 8. Hyperliquid 엔진 가격 공유 (최적화)

- `hl=True` 거래소들은 **현재가를 공유**합니다.
- 대표 HL 거래소 1곳에서만 ticker를 조회하여 전체 HL 거래소가 사용 → 네트워크 요청 절약
- 향후 `hl=False` (다른 엔진) 거래소 추가 시, 엔진별로 가격 조회를 분리 예정

---

## 9. 거래소 표시/숨김 (show 옵션)

- `config.ini`의 `show=True/False`로 기본 표시 여부 결정
- **urwid**: Exchanges 박스에서 체크박스 ON/OFF로 실시간 토글 가능
  - 체크 ON → 카드 표시 + 기능 사용 가능
  - 체크 OFF → 카드 숨김 + OFF 상태
- **Textual**: 현재는 show=True인 거래소만 표시 (향후 토글 기능 추가 예정)

---

## 10. 로그/디버깅

- **화면 하단 Logs**: 실행/오류/건너뜀 사유 표시 (자동 스크롤)
- **파일 로그**: 프로젝트 루트의 `debug.log`에 상세 기록

---

## 11. 새 거래소 추가

1. `.env`에 지갑/키 추가 (섹션명 대문자 접두사):
```env
NEWDEX_WALLET_ADDRESS=0x...
NEWDEX_AGENT_API_KEY=...
NEWDEX_PRIVATE_KEY=0x...
```

2. `config.ini`에 섹션 추가:
```ini
[newdex]
builder_code = 0xYourBuilder
fee_rate = 20
show = True
hl = True
```

3. `python main.py` 재실행 → 자동으로 카드 생성

---

## 12. 문제 해결(FAQ)

**Q1) "설정 없음"이 표시됩니다.**  
→ `.env`(지갑/키) 또는 `config.ini`(builder_code/fee_rate) 값 확인. 대문자 접두사 규칙 준수 필요.

**Q2) Market 주문이 실패합니다(가격 관련).**  
→ Hyperliquid는 시장가도 슬리피지 계산용 가격 필요. 앱은 현재가 사용. 네트워크/잔고/레이트리밋 확인.

**Q3) 버튼 클릭 시 텍스트가 드래그 선택됩니다.**  
→ 터미널 설정 문제입니다.  
- tmux: `set -g mouse on`
- VSCode: `"terminal.integrated.enableMouseReporting": true`, `"copyOnSelection": false`
- Windows Terminal: `"copyOnSelect": false`

**Q4) 느립니다.**  
→ 1초마다 잔고/포지션 조회 중. 거래소가 많으면 주기를 늘리거나(코드 수정 필요) show=False로 일부 숨기세요.

**Q5) 실행 직후 화면이 안 보입니다(urwid).**  
→ 키를 한 번 누르면 나타납니다. 자동 갱신 중이지만 터미널에 따라 첫 렌더가 지연될 수 있습니다.

**Q6) Tab/Shift+Tab이 이상하게 동작합니다.**  
→ urwid의 Tab 동작은 위젯 렌더링 타이밍에 민감합니다. 0.05초 지연이 적용되어 안정화되어 있으나, 환경에 따라 다를 수 있습니다.

**Q7) 기본 UI를 바꾸고 싶습니다.**  
→ `main.py`의 `DEFAULT_UI = "urwid"` 또는 환경변수 `PDEX_UI_DEFAULT=textual`로 변경 가능.

---

## 13. 키보드 조작 가이드

### 영역 전환(헤더 ⇄ 본문 ⇄ 푸터)
- **Ctrl+Down/Up** (또는 Alt+Down/Up, Shift+Down/Up)
- **PageDown/Up**
- **Ctrl+J/K**
- **F6**

### 내부 이동(입력·버튼 간)
- **Tab**: 다음 입력/버튼
- **Shift+Tab**: 이전 입력/버튼
- **방향키(←/→/↑/↓)**: 세부 이동

### 본문(거래소 카드)
- **Tab**: Q → P → MKT/LMT → L → S → OFF → EX → 다음 카드 Q (순환)
- **Shift+Tab**: 역방향 (EX → … → Q → 이전 카드 EX)

### 주문 실행
- **Enter**: 포커스된 버튼 실행
- **Space**: 체크박스/라디오 토글

---

## 14. 보안 주의(필수)

- `.env`는 **절대 Git에 커밋하거나 공유하지 마세요**.
- 가능하면 **Agent API Key** 사용 (Private Key 직접 사용 지양)
- 서버 배포 시 파일 권한(chmod 600 .env) 및 접근 제어 철저히 관리

---

## 15. 기술 스택

- **UI**: urwid (기본), Textual (레거시)
- **거래소 API**: ccxt (Hyperliquid)
- **설정**: python-dotenv, configparser

`requirements.in`: