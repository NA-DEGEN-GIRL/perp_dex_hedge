# Hyperliquid Multi‑DEX Trader (urwid TUI)
![스크린샷](screenshot.png)

여러 Hyperliquid 엔진 기반 Perp DEX + mpdex 기반 비‑HL DEX(Lighter/Paradex/Edgex/GRVT/Backpack)를 하나의 터미널 UI(urwid)에서 동시에 거래하는 앱입니다.

- GitHub: https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
- 기본 UI: urwid (경량·빠름·안정) — 앞으로도 urwid 중심으로 개발합니다.
- Textual: 더 이상 권장하지 않으며 레거시 옵션입니다. (`python main.py --ui textual`)

---

## 기능 요약

- 다수의 DEX(HL + 비‑HL) 동시 거래
- 현재가(공유·HL), 총 담보(USDC), 포지션/PNL 실시간 표시
- Market/Limit, Long/Short, 개별 실행(EX), 전체 실행(EXECUTE ALL), 방향 반전(REVERSE)
- OFF(비활성) 토글: EXECUTE ALL 대상 제외(기본 OFF)
- REPEAT: “횟수 × a~b초 랜덤 간격” 반복 실행(재클릭 시 즉시 중단)
- BURN: REPEAT 기반, 방향을 번갈아 2배 횟수로 반복 실행(파밍 보조)
- CLOSE ALL: 활성 거래소 포지션을 시장가 반대주문으로 0(청산)
- Exchanges 박스: show=False 거래소도 실행 중 표시/숨김 전환
- 키보드 중심 조작(Tab/Shift+Tab/영역 전환), 파일 로그(`debug.log`)

---

## 0. 사전 준비

- Python 3.10+ (Windows는 fastecdsa 의존성으로 3.10 권장)
- Git
- 지원 OS: Linux, macOS, WSL(Windows)

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

# 의존성 (HL용 ccxt + urwid + mpdex까지 포함)
pip install -r requirements.in
```

requirements.in(발췌):
```
ccxt
textual
python-dotenv
urwid
mpdex @ git+https://github.com/NA-DEGEN-GIRL/multi-perp-dex.git@master
```

---

## 2. 설정

### A) .env (지갑/키)
```bash
cp .env.example .env
```

아래 키를 거래소별로 채웁니다(섹션명을 대문자 접두사로).

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

# ===== Lighter (mpdex, hl=False) =====
# account_id 확인:
# 1) https://app.lighter.xyz/explorer → 본인 주소 → 거래 상세의 account_index
# 2) https://apidocs.lighter.xyz/reference/account-1 → by=l1_address, value=본인 EVM 주소 → "Try it!" → account_index
# api key: https://app.lighter.xyz/apikeys (api_key_id는 보통 2부터 사용)
LIGHTER_ACCOUNT_ID=transaction_에서_확인
LIGHTER_PRIVATE_KEY=api_생성시_확인
LIGHTER_API_KEY_ID=api_생성시_확인
LIGHTER_L1_ADDRESS=your_evm_address

# ===== Paradex (mpdex, hl=False) =====
PARADEX_L1_ADDRESS=your_evm_address
PARADEX_ADDRESS=paradex_접속시_표시
PARADEX_PRIVATE_KEY=paradex에서_확인

# ===== Edgex (mpdex, hl=False) =====
EDGEX_ACCOUNT_ID=your_account_id
EDGEX_PRIVATE_KEY=https://pro.edgex.exchange/keyManagement에서_확인(신청_필요)

# ===== GRVT (mpdex, hl=False) =====
GRVT_API_KEY=https://grvt.io/exchange/account/api-keys에서_발급
GRVT_ACCOUNT_ID=your_account_id
GRVT_SECRET_KEY=https://grvt.io/exchange/account/api-keys에서_발급

# ===== Backpack (mpdex, hl=False) =====
BACKPACK_API_KEY=https://backpack.exchange/portfolio/settings/api-keys에서_발급
BACKPACK_SECRET_KEY=https://backpack.exchange/portfolio/settings/api-keys에서_발급
```

- HL은 Agent API Key(또는 Private Key)를 사용합니다(Private Key 직접 사용은 비권장).
- 비‑HL(mpdex) 거래소는 .env만 맞으면 추가 설정 없이 동작합니다(내부에서 심볼 변환 symbol_create 사용).

### B) config.ini (표시/엔진/수수료)
```ini
# HL 예시
[dexari]
builder_code = 0x7975cafdff839ed5047244ed3a0dd82a89866081
fee_rate = 10
hl = True
show = True
FrontendMarket = False      # (선택) 시장가를 FrontendMarket로 보낼 때 True

[liquid]
builder_code = 0x6D4E7F472e6A491B98CBEeD327417e310Ae8ce48
fee_rate = 50
hl = True
show = True

[based]
builder_code = 0x1924b8561eef20e70ede628a296175d358be80e5
fee_rate = 25
hl = True
show = False
FrontendMarket = True

[supercexy]
builder_code = 0x0000000bfbf4c62c43c2e71ef0093f382bf7a7b4
fee_rate = 16
hl = True
show = False
FrontendMarket = True

# 비‑HL(mpdex): show=True/False와 hl=False만 지정하면 됩니다.
[lighter]
hl = False
show = False

[paradex]
hl = False
show = False

[edgex]
hl = False
show = False

[grvt]
hl = False
show = False

[backpack]
hl = False
show = True
```

- show=True: 기본 표시, False: 기본 숨김(OFF 간주)
- hl=True: Hyperliquid(ccxt), hl=False: mpdex 클라이언트 사용
- fee_rate: HL per‑order builder feeInt(정수, 10~50)

---

## 3. 실행

```bash
python main.py
```

- 기본 UI는 urwid입니다(권장).
- Textual(레거시): `python main.py --ui textual`

---

## 4. 사용법(버튼 상세)

### 4-1. 헤더(4행) 버튼/입력
1) Ticker / Price / Total / QUIT
- Ticker: 거래 심볼(기본 BTC). 입력 시 0.4초 뒤 자동 반영되며, HL 거래소는 심볼별 최대 레버리지/마진 모드(크로스/아이솔레이트)를 한 번만 자동 적용합니다.
- Price: HL 엔진 공유 현재가.
- Total: 모든 거래소 담보 합계(USDC or USDT).
- QUIT: 앱 종료

2) All Qty / EXECUTE ALL / REVERSE / CLOSE ALL
- All Qty: 입력 시 현재 화면에 보이는 모든 거래소 카드의 Q(수량)에 일괄 적용.
- EXECUTE ALL: 활성화된 (L 혹은 S가 선택된) 거래소들 한 번에 주문.
  - OFF인 거래소는 대상에서 제외.
- REVERSE: 현재 활성 + 방향 선택된 거래소에 한해 LONG↔SHORT 일괄 반전. OFF/방향 미선택 거래소는 영향 없음.
- CLOSE ALL: 활성 거래소의 포지션을 0으로 만드는 청산(반대 방향 “시장가” 주문).
  - OFF인 거래소는 대상에서 제외.

3) REPEAT: Times / min(s) / max(s) / [REPEAT]
- Times: 반복 횟수(정수 ≥ 1).
- min(s)/max(s): 각 반복 사이 대기 시간의 범위(초). 매 회차 random(min~max)로 대기.
- REPEAT 버튼:
  - 시작: Times 회만큼 EXECUTE ALL을 반복(중간에 취소 가능).
  - 중지: 수행 중 다시 누르면 즉시 중단(다음 주문을 더 이상 시작하지 않음).
  - 동작 중에는 중복 실행을 방지합니다.

4) BURN: Burn 횟수 / min(s) / max(s) / [BURN]
- Burn 횟수:
  - 1회: REPEAT와 동일.
  - ≥2회: REPEAT(Times) → burn interval(min~max) 대기 → 방향 반전 → REPEAT(2×Times) → … (횟수만큼 반복)
  - 예: Times=5, Burn=3, 처음 LONG이면 “LONG×5 → 대기 → SHORT×10 → 대기 → LONG×10”.
  - **-1로 설정히 무한 반복**
- min(s)/max(s): 각 burn 라운드 사이 대기 범위(초).
- BURN 버튼:
  - 시작: 위 알고리즘으로 반복 실행(중간에 취소 가능).
  - 중지: 수행 중 다시 누르면 즉시 중단(다음 라운드/주문 시작 전 정지).

참고
- 헤더에서 Ticker를 바꾸면(예: BTC→ETH), HL 거래소의 해당 심볼에 대해 최대 허용 레버리지와 마진 모드(cross/isolated)가 자동 적용됩니다(거래소/계정이 지원하는 범위 내).
- HL + FrontendMarket=True이고 유형이 시장가인 주문은 HL의 raw 경로(privatePostExchange)로 전송되어 tif='FrontendMarket'으로 마킹됩니다(슬리피지 적용).

---

### 4-2. 거래소 카드(한 거래소당 1장)
행 구성: [거래소명]  Q  P  MKT/LMT  L  S  OFF  EX  + 상태(아래 줄)

- Q(수량): 주문 수량. 헤더의 All Qty를 입력하면 일괄 반영됩니다.
- P(가격): 지정가(LMT)일 때만 의미가 있으며, 미입력 시 해당 거래소는 EXECUTE ALL 대상에서 건너뜁니다.
- MKT/LMT: 주문 유형 토글.
  - MKT(시장가): 시장가 거래 설정.
  - LMT(지정가): P(가격) 필수.
- L(롱): 선택 시 방향을 LONG으로 지정, 카드가 활성(OFF 해제). 버튼이 초록(선택)으로 표시.
- S(숏): 선택 시 방향을 SHORT으로 지정, 카드가 활성(OFF 해제). 버튼이 빨강(선택)으로 표시.
- OFF: 비활성 토글. 누르면 L/S 선택이 해제되고, OFF가 노란색 강조(선택 상태)로 표시됩니다. EXECUTE ALL/CLOSE ALL 대상에서 제외됩니다.
- EX: 해당 거래소만 즉시 선택된 주문 실행.

상태(두 번째 줄)
- 📘 Position / 💰 Collateral: 포지션과 총 담보(USDC).  
  - 포지션 크기 옆에 “(크기×현재가)” USDC 값을 함께 표기합니다. 예: `0.01000 (1,234.56 USDC)`  
  - PnL과 방향은 색상으로 강조됩니다(롱/숏/PNL±).

---

### 4-3. Exchanges 박스(하단)
- 모든 거래소를 체크박스로 가로 배열(2줄)합니다.
- ON(체크): 해당 거래소 카드가 화면에 생성되고, 상태 갱신 루프가 시작됩니다.
- OFF(해제): 카드가 숨겨지고, 상태 갱신 루프가 취소됩니다(네트워크 요청 감소).
- config.ini의 show=True/False 기본값과 무관하게 실시간 토글 가능합니다.

---

### 4-4. 키보드(요약)
- 영역 전환: Shift+Up/Down
- 내부 이동: Tab/Shift+Tab(입력·버튼만 순회, 텍스트 칸 건너뜀), 방향키
- 래핑:
  - 본문에서 EX → Tab → 다음 거래소의 Q로 이동
  - 본문에서 Q → Shift+Tab → 이전 거래소의 EX로 이동

---

## 5. 동작 참고

- HL 가격 공유: `hl=True` 거래소는 대표 1곳에서 조회한 ticker를 공유해 API 호출량을 절약합니다.
- 비‑HL 주문:
  - Market: price 없이 실행
  - Limit: price 필수
  - 내부에서 거래소 고유 심볼(symbol_create)을 사용하므로 Ticker 입력은 일반 코인 기호(BTC/ETH 등)만 넣으면 됩니다.
- CLOSE ALL: reduceOnly 시장가로 청산(HL), mpdex는 close_position 사용
- FrontendMarket(HL): `FrontendMarket=True` + order_type='market'일 때 raw 경로로 tif=FrontendMarket 주문(슬리피지 적용)

---

## 6. 로그/디버깅

- 파일 로그: `debug.log`(UTF‑8 텍스트)
- 콘솔 로그는 기본 비활성(urwid 화면 보전). 필요 시 `PDEX_LOG_CONSOLE=1`로 임시 활성화.

---

## 7. 보안 주의

- `.env`는 절대 커밋/공유 금지
- 가능한 Agent API Key(또는 mpdex API 키) 사용(Private Key 직접 사용 지양)
- 서버/CI 배포 시 파일 권한/접근 제어 철저

---

## 8. 기술 스택

- UI: urwid(기본), Textual(레거시)
- 거래소 API: ccxt(Hyperliquid), mpdex(Lighter/Paradex/Edgex/GRVT/Backpack)
- 설정: python‑dotenv, configparser

---

## 9. 로드맵

- ✅ urwid UI 안정화 / HL 가격 공유 / Exchanges 토글
- ✅ REPEAT 즉시 중단 / Tab·Shift+Tab 탐색 안정화
- ✅ CLOSE ALL / BURN 기능
- ✅ 비‑HL(mpdex) 거래소: Lighter/Paradex/Edgex/GRVT/Backpack 연동
- 🔜 비‑HL(mpdex) 거래소: Pacifica/Variational 연동
- 🔜 limit 오더 관리