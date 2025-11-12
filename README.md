# Hyperliquid Multi-DEX Trader (urwid TUI)
![스크린샷](screenshot.png)
여러 Hyperliquid 엔진 기반 Perp DEX + Lighter(비-HL)를 터미널 UI(urwid)에서 동시에 거래하는 앱입니다.

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

## 0. 사전 준비

1) Python 3.10+  
2) Git
3) 지원 OS: linux, 맥, WSL
* 일반 windows는 lighter 사용시 사용불가, WSL을 쓰세요

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

`.env`를 열어 각 거래소 키를 채웁니다(섹션명을 대문자 접두사로).
```env
# Dexari (HL)
DEXARI_WALLET_ADDRESS=0x...
DEXARI_AGENT_API_KEY=
DEXARI_PRIVATE_KEY=0x...

# Liquid (HL)
LIQUID_WALLET_ADDRESS=0x...
LIQUID_AGENT_API_KEY=
LIQUID_PRIVATE_KEY=0x...

# Supercexy (HL)
SUPERCEXY_WALLET_ADDRESS=0x...
SUPERCEXY_AGENT_API_KEY=
SUPERCEXY_PRIVATE_KEY=0x...

# BasedOne (HL)
BASEDONE_WALLET_ADDRESS=0x...
BASEDONE_AGENT_API_KEY=
BASEDONE_PRIVATE_KEY=0x...

# Lighter (비-HL, mpdex 사용)
# account_id 얻는 법:
# - https://app.lighter.xyz/explorer 에서 본인 주소 → 트랜잭션 상세 → account_index 확인
#   또는 https://apidocs.lighter.xyz/reference/account-1 에서
#   by=l1_address, value=본인 EVM 주소 → Try it! → account_index 확인
#
# api key 생성: https://app.lighter.xyz/apikeys
# - api_key_id: 처음 생성한 키는 보통 2부터 사용(0,1은 예약)
LIGHTER_ACCOUNT_ID=transaction_에서_확인한_account_index
LIGHTER_PRIVATE_KEY=api_생성시_확인(secret)
LIGHTER_API_KEY_ID=api_생성시_설정(index)
LIGHTER_L1_ADDRESS=your_evm_address
```
- Agent API Key(또는 Private Key) 사용 권장(Private Key 직접 사용은 비권장).
- 값이 비어 있으면 해당 거래소는 “설정 없음”으로 표시됩니다.

### B) config.ini (표시/엔진/수수료)
```ini
# 예시(HL)
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 20       # feeInt(정수, 보통 10~50)
show = True
hl = True
FrontendMarket = True

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50
show = True
hl = True

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
show = False
hl = True

# Lighter(비-HL)
[lighter]
show = True
hl = False
```
- show=True: 기본 표시, False: 기본 숨김(OFF 간주)
- hl=True: Hyperliquid 엔진, hl=False: Lighter(mpdex) 사용

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
2) All Qty / EXECUTE ALL / REVERSE / CLOSE ALL  
3) REPEAT: Times / min(s) / max(s) / [REPEAT]  
4) BURN: Burn 횟수 / min(s) / max(s) / [BURN]

- Ticker: 거래 심볼(기본 BTC)
- Price: HL 엔진 공유 현재가(여러 HL 거래소가 한 번 조회값 공유)
- Total: 모든 거래소 담보 합계
- All Qty: 입력 시 모든 카드 Q(수량)에 일괄 반영
- EXECUTE ALL: 활성+방향 선택된 거래소에 동시 주문
- REVERSE: 활성 거래소의 LONG↔SHORT 반전
- CLOSE ALL: 활성 거래소 포지션을 시장가 반대주문으로 0으로
- REPEAT/BURN: 반복 실행(재클릭 시 즉시 중단)

### 거래소 카드(본문)
- Q: 수량
- P: 가격(지정가일 때만 사용)
- MKT/LMT: 시장가 ↔ 지정가
- L: 롱 선택(초록)
- S: 숏 선택(빨강)
- OFF: 비활성(노랑, EXECUTE ALL 대상 제외)
- EX: 개별 실행
- 상태: 📘 Position / 💰 Collateral(실시간: Lighter도 지원)

### Exchanges 박스(하단)
- 모든 거래소 체크박스(show 토글)
- ON → 카드 표시 + 기능 사용
- OFF → 카드 숨김 + OFF 상태

---

## 5. 키보드 단축키

영역 전환(헤더 ⇄ 본문 ⇄ 푸터):
- Shift+Down/Up

내부 이동(입력·버튼만 순회):
- Tab: 다음 입력/버튼  
- Shift+Tab: 역방향  
- 방향키(←/→/↑/↓): 세부 이동

---

## 6. HL 가격 공유 (최적화)

- `hl=True` 거래소들은 **현재가를 공유**합니다.
- 대표 HL 거래소 1곳에서만 ticker 조회 → 전체 HL 거래소가 사용
- Lighter는 현재가 공유에 포함하지 않습니다(추후 확장 가능)

---

## 7. 표시/숨김 (show 옵션)

- `config.ini`의 show=True/False로 기본 표시 여부 결정
- urwid UI에서는 Exchanges 박스에서 ON/OFF로 실시간 토글 가능

---

## 8. 로그/디버깅

- 화면 하단 Logs
- 파일 로그: `debug.log`(앱 루트)

---

## 9. Lighter(비-HL) 요약

- .env에 아래 4개만 채우면 됩니다:
```env
LIGHTER_ACCOUNT_ID=transaction_에서_확인한_account_index
LIGHTER_PRIVATE_KEY=api_생성시_확인(secret)
LIGHTER_API_KEY_ID=api_생성시_확인(id)
LIGHTER_L1_ADDRESS=your_evm_address
```
- config.ini:
```ini
[lighter]
show = True
hl = False
```
---

## 10. 보안 주의

- `.env`는 절대 커밋/공유 금지
- Agent API Key 권장(Private Key 직접 사용 지양)
- 서버/CI 배포 시 파일 권한/접근 제어 철저

---

## 11. 기술 스택

- UI: urwid(기본), Textual(레거시)
- 거래소 API: ccxt(Hyperliquid), mpdex(Lighter)
- 설정: python-dotenv, configparser

`requirements.in`
```
ccxt
urwid
textual
python-dotenv
```
(mpdex는 별도 설치)

---

## 12. 로드맵

- ✅ urwid UI 안정화 / HL 가격 공유 / Exchanges 토글
- ✅ REPEAT 즉시 중단 / Tab·Shift+Tab 탐색 안정화
- ✅ CLOSE ALL / BURN 기능 추가
- ✅ Lighter(비-HL) 연동
- 🔜 limit 오더 취소 관리