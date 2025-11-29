> ⚠️ 중요 고지(필독) — 안전/책임/한계
>
> - 이 저장소의 코드는 “코딩 초보”가 학습/개인 용도로 만든 실험적 프로그램입니다. 상업적/전문적 품질을 보장하지 않으며, 모든 사용은 전적으로 본인 책임입니다.  
> - 본 프로젝트는 “있는 그대로(as‑is)” 제공되며, 명시적/묵시적 보증이 없습니다. 본 소프트웨어를 사용/배포/수정함으로써 발생하는 모든 손실(자산 손실, 기회 손실, 시스템 장애, 법적 분쟁 등)에 대해 작성자는 책임지지 않습니다.
> - 금융/투자 조언이 아닙니다. 거래는 반드시 “소액”으로 테스트 후 사용하세요. 시장가(MKT)·반복 실행(REPEAT/BURN)·일괄 실행(EXECUTE ALL) 기능은 특히 주의가 필요합니다.
> - 민감 정보(.env의 지갑/키/세션)는 절대 공개/커밋/공유 금지. 실행 파일(.exe) 안에 포함하지 마세요. 프로그램은 exe 옆/현재 폴더의 .env·config.ini를 “외부에서” 읽습니다.
> - 키 보관 원칙: 가능하면 “프라이빗키” 대신 에이전트/API 키 사용을 권장합니다. 프라이빗키를 사용할 경우 오프라인 백업/암호보호/권한관리(파일 권한, 사용자 계정) 필수입니다.
> - 로그 파일(debug.log, ws.log 등)에 민감 데이터(주소/오류 메시지)가 포함될 수 있습니다. 공유 전 반드시 확인/편집하세요.
> - 하이브리드(treadfi.hyperliquid)·서브계정(vaultAddress)·HIP‑3(DEX) 등 특수 경로는 거래소 정책/업데이트에 의해 언제든 실패/변경될 수 있습니다.
> - Windows 단일 실행 파일(.exe)은 서명되지 않았습니다. 백신/보안정책에 따라 경고가 뜰 수 있습니다. 신뢰할 수 있는 환경에서만 실행하세요.
>
> 사용 전 체크리스트  
> 1) .env에 “사용할 거래소만” 남기고 키/주소 입력(나머지는 지우기)  
> 2) config.ini에서 해당 섹션 `show=True`, `exchange=` 값 확인  
> 3) `python main.py` 또는 PerpDexHedge.exe로 실행 → debug.log 확인  
> 4) “아주 작은 수량”으로 시장가 번 테스트 후 사용
> 5) 큰 금액을 넣어서 하지마세요. 어떤 버그가 있을지 모르니 본인이 감당가능한 선에서 돌리세요.

# 초보 전용 사용 설명서 (완전 기초, 복붙 가이드)

이 문서는 “컴퓨터로 뭐 해본 적 거의 없는 사람”도 그대로 따라 하면 이 봇을 켜서 주문까지 할 수 있게 만든 안내서입니다.  
진짜로 하나하나 차근차근 씁니다. 어렵게 보이면 숨 크게 쉬고, 그대로 천천히 따라만 하세요. 모르면 그냥 복사/붙여넣기 하세요.

---

## 0. 이게 뭔가요?

- 여러 거래소(하이퍼리퀴드, 트레드파이, 라이터, 파라덱스 등)를 한 화면에서 거래하는 “터미널 프로그램(검은 화면)”입니다.
- 키보드/마우스로 가격 보면서 버튼 눌러 주문합니다.
- 안전하게 연습하려면 아주 작은 수량부터 해 보세요.

---

## 1. 준비물 3가지

1) 파이썬 설치(3.10 이상 권장)  
- 윈도우: https://www.python.org/downloads/windows/ 들어가서 Python 3.10.x “Windows installer (64-bit)” 받아 설치 (Add Python to PATH 꼭 체크)  
- 맥: 앱 스토어/홈브류(모르면 https://brew.sh → Terminal 열고 `brew install python@3.10`)  
- 리눅스: 배포판 패키지로 설치(예: `sudo apt install python3 python3-venv`)

2) Git 설치 (코드를 내려받는 도구)  
- https://git-scm.com/downloads → 자신의 OS에 맞게 설치.  
- 혹은 Git 안 깔아도 됩니다. “Code → Download ZIP”으로 받아도 됩니다.

3) 지갑 주소/키(또는 API 키)  
- 하이퍼리퀴드(HL) / 트레드파이 / 슈퍼스택 / 라이터 등 거래하려는 거래소의 지갑/키 준비  
- 없는 거래소는 “안 써도” 됩니다. (예: HL만 하면 HL 것만 채움)

---

## 2. 다운로드 & 설치 (처음 1번만)

터미널(윈도우: PowerShell) 열고 아래 순서대로 입력(복사→붙여넣기→엔터):

```bash
# 프로그램 내려받기
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge

# 가상환경(프로그램 전용 상자 같은 것) 만들고 켜기
python -m venv .venv
# 윈도우 PowerShell
.\.venv\Scripts\Activate.ps1
# 맥/리눅스
# source .venv/bin/activate

# 필요한 것들 설치
pip install -r requirements.in
```

ZIP으로 받은 경우: 압축 푼 폴더에서 터미널 열고 위 “가상환경~설치” 부분만 하면 됩니다.

---

## 3. 설정 파일 만들기 (딱 2개: .env, config.ini)

이 프로그램은 “어떤 거래소를 쓸지”와 “어떤 지갑을 쓸지”만 알면 돌아갑니다.  
그 정보가 2개 파일에 들어갑니다.

### 3-1) .env (지갑/키 넣는 곳)

“.env.example” 파일을 복사해서 “.env” 파일을 만듭니다.

```bash
cp .env.example .env
```

복사한 “.env” 파일을 메모장으로 열고, **쓸 거래소 부분만 남기고 나머지는 지워도 됩니다.**  
중요 규칙: `키=값` 사이에 공백 절대 금지 (예: `A=B` (O), `A = B` (X)).

아래 중 하나만 고르면 됩니다. (여러 개 해도 됩니다)

- (A) 일반 Hyperliquid(HL)만 할 거다
  ```
  HL_WALLET_ADDRESS=0x내지갑주소
  HL_AGENT_API_KEY=
  HL_PRIVATE_KEY=0x내개인지갑프라이빗키
  HL_IS_SUB=0    # 서브계정이면 1 (true)
  ```
  - HL_IS_SUB=1 이면 내부에서 vaultAddress=HL_WALLET_ADDRESS 로 자동 설정됩니다(서브계정용 서명 규칙).

- (B) Tread.fi (hyperliquid 하이브리드: 가격/포지션=HL, 주문=트레드파이)
  ```
  TREADFI_HL_MAIN_WALLET_ADDRESS=메인지갑주소   # 로그인/서명 주체
  TREADFI_HL_SUB_WALLET_ADDRESS=서브지갑주소   # 실제 주문/포지션 지갑
  TREADFI_HL_PRIVATE_KEY=메인지갑프키         # (선택) 자동 로그인
  TREADFI_HL_ACCOUNT_NAME=트레드파이계정명     # 필요
  TREADFI_HL_CSRF_TOKEN=세션쿠키(선택)
  TREADFI_HL_SESSION_ID=세션쿠키(선택)
  ```
  - 프라이빗키를 빼고 **세션 쿠키**만 넣어도 됩니다(로그인만 하면 됨).
  - 편한 방법: `TREADFI_HL_MAIN_...`/`SUB_...` 두 줄만 우선 넣고 **프로그램 실행** →  
    브라우저에서 http://127.0.0.1:6974/ 열고 로그인(텍스트 서명) → 자동 세션 사용.

- (C) Superstack (HL + 지갑 provider)
  ```
  SUPERSTACK_WALLET_ADDRESS=0x...
  SUPERSTACK_API_KEY=sk_...
  ```

- (D) 비‑HL(mpdex: Lighter/Paradex/Edgex/GRVT/Backpack)
  - .env.example 내부 주석을 읽고 해당 거래소 부분만 채워 넣으세요.
  - 예) Lighter
    ```
    LIGHTER_ACCOUNT_ID=...
    LIGHTER_PRIVATE_KEY=...
    LIGHTER_API_KEY_ID=...
    LIGHTER_L1_ADDRESS=0x...
    ```

힌트
- 안 쓰는 거래소는 통째로 지워도 됩니다.
- 지갑 주소는 0x로 시작, 대소문자 아무거나 OK(내부에서 정리합니다).

### 3-2) config.ini (어떤 거래소를 쓸지 표시하는 곳)

**중요:** `[섹션명]`은 그냥 “이름/라벨”입니다.  
**실제 엔진/백엔드 종류는 섹션 안의 `exchange=` 값으로 결정됩니다.**

- 일반 HL(하이퍼리퀴드)  
  → 섹션 안에 `exchange=` 없으면 기본이 HL(native)입니다.
  ```
  [hl]
  show = True
  # exchange 키가 없으면 hyperliquid(native)로 취급
  ```

- Superstack  
  ```
  [superstack]
  show = True
  exchange = superstack
  ```

- Tread.fi (하이브리드: 조회=HL, 주문=mpdex)  
  ```
  [treadfi_hl]
  show = True
  exchange = treadfi.hyperliquid
  fee_rate = 20   ; 표시용
  ```

- 비‑HL(mpdex)  
  ```
  [lighter]
  show = False
  exchange = lighter

  [paradex]
  show = False
  exchange = paradex
  ...
  ```

**딱 기억할 것:**  
- `.env`는 “지갑/키” → **섹션명 대문자 접두사**로 작성 (예: `[treadfi_hl]` → `TREADFI_HL_*`)  
- `config.ini`는 “어떤 거래소인지” → **exchange=… 값으로 결정**

---

## 4. 실행하기 (처음 주문까지)

1) 터미널에서 프로젝트 폴더로 이동(처음 설치 때 만든 폴더)
```bash
cd perp_dex_hedge
```

2) 가상환경 켜기
```bash
# 윈도우 PowerShell
.\.venv\Scripts\Activate.ps1
# 맥/리눅스
# source .venv/bin/activate
```

3) 프로그램 실행
```bash
python main.py
```

4) (Tread.fi만) 처음 실행 시 브라우저에서 http://127.0.0.1:6974/ 열어 로그인(텍스트 서명)  
   - 세션 쿠키를 입력한 경우 이 단계 생략됩니다.

5) 화면 사용법(진짜 핵심만)
- 맨 위에 Ticker/BTC, Price/Total/QUIT, Qty/EXECUTE ALL 등 버튼이 쭉 나옵니다.
- 아래 “거래소 카드(박스)”에서 **거래소 이름** 보이고, 그 줄에 “코인(T), 수량(Q), 가격(P), 주문유형(MKT/LMT), LONG(L)/SHORT(S), OFF, EX 버튼”이 있습니다.
  - **가장 쉬운 주문(시장가):**  
    1) T(코인)에 BTC 입력  
    2) Q(수량)에 0.001 같은 아주 작은 수 입력  
    3) MKT 선택(시장가)  
    4) 방향(L 또는 S) 누르기 → 초록/빨강으로 활성 표시  
    5) EX 누르기 → 해당 거래소에 “그 주문만” 즉시 전송

- **CLOSE ALL**: 활성 카드(L/S 선택된 것)만 시장가 반대주문으로 0 만들기(조심!)

- **주의:** 처음엔 무조건 **아주 작은 수량**으로 테스트하세요.

---

## 5. 자주 묻는 질문(FAQ)

Q1) debug.log는 어디 있나요?  
A) 프로그램 폴더에 `debug.log`가 생깁니다. 실행 중 뜨는 모든 INFO가 들어갑니다.  
   콘솔에도 보고 싶으면 실행 전에 `PDEX_LOG_CONSOLE=1` 환경변수 켜고 실행하세요.

Q2) “Already subscribed” 같은 메시지가 많이 떠요. 문제인가요?  
A) 아니요. 이미 구독된 채널이라는 정보 메시지입니다. 동작에는 지장 없습니다.

Q3) `.env`에서 왜 `A=B` 형태로 붙여 쓰라 하나요?  
A) `A = B`처럼 띄우면 값에 공백이 포함돼 인식 실패합니다. 무조건 붙여 쓰세요: `A=B`.

Q4) HL_IS_SUB=1이 뭔가요?  
A) HL 서브계정을 쓸 때 **마스터 지갑**으로 서명하고 `vaultAddress=서브지갑` 규칙을 따르기 위해 자동 설정합니다.  
   일반 계정이면 0/비워둠.

Q5) Tread.fi는 왜 메인/서브 지갑을 나눠 쓰나요?  
A) 주문/포지션은 서브 지갑에 반영됩니다. 메인 지갑은 로그인/서명 주체입니다.

Q6) 주문이 안 나가요  
- 지갑/키(.env)가 비었는지, 해당 섹션이 config.ini에서 `show=True`인지 확인하세요.  
- 수량(Q)이 0이면 주문이 나가지 않습니다. 아주 작은 수량이라도 입력해야 합니다.

---

## 6. 안전·보안

- `.env`는 절대 인터넷에 올리지 마세요(지갑/키가 다 들어있습니다).  
- 프라이빗키가 부담되면 HL은 Agent API Key, mpdex는 해당 거래소 API 키 사용을 권장합니다.  
- 윈도우/공용PC는 특히 조심(폴더 권한/암호 관리 철저).

---

## 7. 고급 설정(선택)

- 콘솔 로그 켜기: 실행 전에 `PDEX_LOG_CONSOLE=1`  
- 모듈별 로그 레벨 바꾸기: `PDEX_MODULE_LEVELS="perp_dex_hedge.trading_service=DEBUG"`  
- WebSocket만 따로 파일로(관리자가 켜둔 경우): `ws.log` 참고

---

## 8. 문제 생기면

1) `debug.log`에서 에러 줄을 찾아 그대로 검색(또는 이슈에 붙여 주세요).  
2) `.env`에서 **쓴 거래소만** 남았는지 확인(혼란 줄이기).  
3) `config.ini`에서 그 섹션 `show=True`인지, `exchange=` 값이 맞는지 확인.  
4) 여전히 안 되면… **아주 작은 수량**으로 다시 천천히 시도.

---

## 9. 마지막 한줄 요약

- `.env`에 **내 지갑/키**만 넣고, `config.ini`에서 **쓸 거래소 섹션만 show=True**로 켜고, 터미널에서 `python main.py` → 거래소 카드에서 **코인/수량/시장가/방향 선택 후 EX**.  
- 모르면 그냥 복사→붙여넣기 하고 그대로 따라 하기. 이 문서대로 하면 됩니다. 😎