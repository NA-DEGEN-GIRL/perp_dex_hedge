# Hyperliquid Multi-DEX Trader (urwid TUI)
![스크린샷](screenshot.png)
여러 Hyperliquid 엔진 기반 Perp DEX를 터미널 UI(urwid)에서 동시에 거래하는 앱입니다.

- GitHub: https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
- 기본 UI: urwid (경량·빠름·안정) — 앞으로도 urwid 중심으로 개발합니다.
- Textual: 더 이상 권장하지 않으며 레거시 옵션입니다. (`python main.py --ui textual`)

---

## 무엇을 할 수 있나요?

- 다수의 DEX(HL 및 Lighter)에 동일 심볼로 동시 주문
- 현재가(공유, HL), 총 담보(USDC), 포지션/PNL 실시간 표시
- Market/Limit, Long/Short, 개별 실행(EX), 전체 실행(EXECUTE ALL), 방향 반전(REVERSE)
- OFF(비활성) 토글: EXECUTE ALL 대상 제외(기본 OFF)
- REPEAT: “횟수 × a~b초 랜덤 간격” 반복 실행(다시 누르면 즉시 중단)
- BURN: REPEAT 기반, 방향 번갈아 2배 횟수로 반복 실행(에어드랍 파밍 보조)
- CLOSE ALL: 활성 거래소 포지션을 시장가 반대주문으로 0(청산)
- 화면 하단 Exchanges 박스: show=False 거래소도 실행 중 표시/숨김 전환
- 로그 패널(자동 스크롤) + 파일 로그(`debug.log`)
- 키보드 중심 조작: Tab/Shift+Tab(입력·버튼만 순회), 영역 전환, 방향키

---

## 미개발/제약

- HL 엔진 이외는 미지원(hl=False인 거래소는 향후 추가 예정)
- 지정가 주문 취소는 앱 내 제공하지 않음(시장가 주문 권장)
- 네트워크/레이트리밋 상태에 따라 응답 지연 가능

---

## 0. 사전 준비

1) Python 3.10+  
2) Git

확인:
```bash
python --version   # 또는 python3 --version
git --version
```

---

## 1. 설치

```bash
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge

# 가상환경
python3 -m venv .venv
source .venv/bin/activate    # Windows PowerShell: .\.venv\Scripts\Activate.ps1

# 의존성
pip install -r requirements.in
```

---

## 2. 설정

### A) .env (지갑/키)
```bash
cp .env.example .env
```

`.env`를 열어 각 거래소 키를 채웁니다(섹션명을 대문자로 한 접두사 사용).
```env
DEXARI_WALLET_ADDRESS=0x...
DEXARI_AGENT_API_KEY=...
DEXARI_PRIVATE_KEY=0x...

LIQUID_WALLET_ADDRESS=0x...
LIQUID_AGENT_API_KEY=
LIQUID_PRIVATE_KEY=
```
- Agent API Key 사용 권장(Private Key 직접 사용은 비권장).
- 값이 비어 있으면 해당 거래소는 “설정 없음”으로 표시됩니다.

### B) config.ini (빌더/수수료/표시/엔진)
```ini
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 20     # feeInt(정수, 보통 10~50)
show = True       # 앱 시작 시 화면 표시
hl = True         # Hyperliquid 엔진

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50
show = True
hl = True

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
show = False      # 기본 숨김(Exchanges 박스에서 ON 가능)
hl = True
```
- show=True: 기본 표시, False: 기본 숨김(OFF 간주)
- hl=True: HL 엔진(현재는 hl=True만 동작)

---

## 3. 실행

```bash
python main.py
```

- 기본 UI는 urwid입니다(권장).  
- Textual(레거시): `python main.py --ui textual` (비권장)

---

## 4. 화면 구성 & 조작

### 헤더(4행)
1) Ticker / Price / Total / QUIT  
2) All Qty / EXECUTE ALL / REVERSE / CLOSE ALL(신규)  
3) REPEAT: Times / min(s) / max(s) / [REPEAT 버튼]  
4) BURN(신규): Burn 횟수 / min(s) / max(s) / [BURN 버튼]

- Ticker: 거래 심볼(기본 BTC)
- Price: HL 엔진 공유 현재가(여러 HL 거래소가 한 번 조회값 공유)
- Total: 모든 거래소 담보 합계
- All Qty: 입력 시 모든 카드의 Q(수량)에 일괄 반영
- EXECUTE ALL: 활성+방향 선택된 거래소에 동시 주문
- REVERSE: 활성 거래소의 LONG↔SHORT 반전
- CLOSE ALL: 활성 거래소의 포지션을 시장가 반대주문으로 0으로
- REPEAT: 횟수/간격 설정 후 반복 실행(다시 누르면 즉시 중단)
- BURN: 아래 알고리즘으로 반복 실행(다시 누르면 즉시 중단)

BURN 알고리즘(요약)
- Burn 횟수 = 1 → REPEAT와 동일(기설정 Times로 1회)
- Burn 횟수 ≥ 2 →  
  (1) REPEAT(Times) 실행 →  
  (2) burn interval(c~d초) 대기 → 방향 반전 → REPEAT(2×Times) 실행 →  
  (3) 다시 interval 대기 → 방향 반전 → REPEAT(2×Times) 실행 … (Burn 횟수만큼)  
- 예) Times=5, Burn=2, 시작이 LONG이면: LONG×5 → 대기 → SHORT×10  
  Burn=3이면: LONG×5 → 대기 → SHORT×10 → 대기 → LONG×10

즉시 중단(Repeat/Burn)
- REPEAT/BURN 버튼을 다시 누르면 즉시 중단 신호가 적용되어 “다음 주문을 더 이상 시작하지 않습니다”.  
  (이미 전송된 주문은 취소할 수 없습니다)

### 거래소 카드(본문, 거래소별)
- Q: 수량
- P: 가격(지정가일 때만 사용)
- MKT/LMT: 시장가 ↔ 지정가
- L: 롱 선택(초록)
- S: 숏 선택(빨강)
- OFF: 비활성(노랑, EXECUTE ALL 대상 제외)
- EX: 개별 실행
- 상태: 📘 Position / 💰 Collateral(실시간 갱신)

### Exchanges 박스(하단)
- 모든 거래소 체크박스(show 토글)
- ON → 카드 표시 + 활성화 가능
- OFF → 카드 숨김 + OFF 상태(대상 제외)

### Logs(맨 아래)
- 실행/오류/건너뜀 사유 표시(자동 스크롤)
- 상세 로그는 `debug.log`에 기록

---

## 5. 키보드 단축키

영역 전환(헤더 ⇄ 본문 ⇄ 푸터):
- Ctrl+Down/Up (또는 Alt/Shift+Down/Up, PageDown/Up, Ctrl+J/K, F6)

내부 이동(입력·버튼만 순회, 텍스트 칸은 건너뜀):
- Tab: 다음 입력/버튼  
  (본문: Q → P → MKT/LMT → L → S → OFF → EX → 다음 카드 Q(래핑))
- Shift+Tab: 이전 입력/버튼  
  (본문: EX → OFF → S → L → MKT/LMT → P → Q → 이전 카드 EX(래핑))
- 방향키(←/→/↑/↓): 세부 이동(urwid 기본)

---

## 6. HL 가격 공유 (최적화)

- `hl=True` 거래소들은 **현재가를 공유**합니다.
- 대표 HL 거래소 1곳에서만 ticker를 조회하여 전체 HL 거래소가 사용 → 네트워크 요청 절약
- 추후 `hl=False`(타 엔진) 지원 시 엔진별 조회로 분리 예정

---

## 7. 표시/숨김 (show 옵션)

- `config.ini`의 show=True/False로 기본 표시 여부 결정
- urwid UI에서는 Exchanges 박스에서 ON/OFF로 실시간 토글 가능
  - ON → 카드 표시 + 기능 사용
  - OFF → 카드 숨김 + OFF 상태

---

## 8. 로그/디버깅

- 화면 하단 Logs: 즉시 확인
- 파일 로그: `debug.log`(앱 루트)

---

## 9. 문제 해결(FAQ)

Q1) “설정 없음”이 뜹니다.  
→ `.env`(지갑/키)와 `config.ini`(builder_code/fee_rate, show/hl)를 확인하세요. 접두사는 섹션을 대문자로 쓴 것과 일치해야 합니다.

Q2) Market 주문이 실패(가격 관련)합니다.  
→ Hyperliquid는 시장가도 가격이 필요합니다(슬리피지 계산). 앱은 현재가를 사용합니다. 네트워크/잔고/레이트리밋을 확인하세요.

Q3) 버튼 클릭 시 텍스트가 드래그 선택됩니다.  
→ 터미널 설정 문제입니다.  
tmux: `set -g mouse on`  
VSCode: `"terminal.integrated.enableMouseReporting": true`, `"copyOnSelection": false`  
Windows Terminal: `"copyOnSelect": false`

Q4) 느립니다.  
→ 1초 주기 잔고/포지션 조회. 거래소가 많으면 주기를 늘리거나(show=False) 일부 숨기세요.

Q5) Tab/Shift+Tab이 이상합니다.  
→ urwid는 렌더 타이밍에 민감합니다. 본 앱은 지연 알람을 사용해 안정화했지만 터미널/폰트에 따라 체감이 다를 수 있습니다.

Q6) 기본 UI를 바꾸고 싶어요.  
→ `main.py`의 `DEFAULT_UI = "urwid"` 또는 `PDEX_UI_DEFAULT=textual` 환경변수로 변경 가능.

---

## 10. 보안 주의

- `.env`는 절대 커밋/공유 금지
- 가능하면 Agent API Key 사용(Private Key 직접 사용 지양)
- 서버/CI 배포 시 파일 권한/접근 제어 철저

---

## 11. 기술 스택

- UI: urwid(기본), Textual(레거시)
- 거래소 API: ccxt (Hyperliquid)
- 설정: python-dotenv, configparser

`requirements.in`
```
ccxt
urwid
textual
python-dotenv
```

---

## 12. 로드맵

- ✅ urwid UI 안정화 / HL 가격 공유 / Exchanges 토글
- ✅ REPEAT 즉시 중단 / Tab·Shift+Tab 탐색 안정화
- ✅ CLOSE ALL / BURN 기능 추가
- 🔜 타 엔진(hl=False) DEX 지원 / limit 오더 주문 취소 기능(굳이?)