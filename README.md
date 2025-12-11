# 다슬기 PERP DEX 봇

> 여러 Perp DEX를 하나의 화면에서 동시에 거래할 수 있는 프로그램입니다.

![스크린샷](screenshot.png)

GitHub: https://github.com/NA-DEGEN-GIRL/perp_dex_hedge

---

## ⚠️ 중요 고지 (반드시 읽어주세요)

### 면책 조항
- 이 프로그램은 **코딩 초보자가 학습/개인 용도로 만든 실험적 프로젝트**입니다
- **모든 사용에 대한 책임은 전적으로 본인에게 있습니다**
- 금융/투자 조언이 아닙니다
- 버그가 있을 수 있으니 **감당 가능한 소액으로만** 사용하세요

### 보안 주의사항
- `.env` 파일은 **절대 공개/공유 금지** (거래에 필요한 개인 지갑 정보가 담겨있음)
- Hyperliquid의 경우 **프라이빗 키 대신 Agent API 키** 사용을 강제 합니다
- 로그 파일에 민감 정보가 포함될 수 있으니 공유 전 확인하세요

### 사용 전 체크리스트
1. `.env`에 사용할 거래소 정보만 입력 (나머지는 삭제)
2. `config.ini`에서 사용할 거래소 `show = True` 설정
3. **아주 작은 수량**으로 테스트 후 사용
4. 큰 금액 넣지 마세요!

---

## 📌 지원 거래소

### Hyperliquid 기반
- MASS, Lit, Dexari, Liquid, BasedOne, Supercexy, Bullpen, Dreamcash, HyEna
- 특수 케이스: Superstack, Tread.fi (with Hyperliquid)

### 비-Hyperliquid 거래소
- Lighter, EdgeX, Paradex, GRVT, Backpack, Variational, Pacifica

---

## 🚀 빠른 시작 가이드

### 1단계: 프로그램 설치

#### Windows 사용자 (초보자용 스크립트)

**사전 준비: Python 3.10 설치**
```powershell
# PowerShell을 관리자 권한으로 실행 후:
winget install -e --id Python.Python.3.10
winget install -e --id Git.Git

# 설치 확인
python --version
git --version
```
**스크립트 실행**
```powershell
# 1. 프로젝트 다운로드
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge

# 2. 실행 정책 설정 (최초 1회만)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

# 3. 설치 및 실행
.\scripts\win\setup.ps1
```

#### 수동 설치 (Windows / Linux / Mac 공통)
```bash
# 프로젝트 다운로드
git clone https://github.com/NA-DEGEN-GIRL/perp_dex_hedge
cd perp_dex_hedge

# 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 의존성 설치
pip install -r requirements.in
```

### 2단계: 설정 파일 만들기

#### .env 파일 (API 키 설정)
```bash
# 예제 파일 복사
cp .env.example .env

# .env 파일 편집 (사용할 거래소만 남기고 나머지 삭제)
```

#### .env 작성 규칙 (중요!)
```env
# ✅ 올바른 예시
LIT_WALLET_ADDRESS=0xabc123...
LIT_AGENT_API_KEY=0xdef456...

# ❌ 잘못된 예시
LIT_WALLET_ADDRESS = 0xabc123...    # 등호 양옆에 공백 금지
LIT_WALLET_ADDRESS="0xabc123..."    # 따옴표 금지
LIT_WALLET_ADDRESS=0xabc... # 메모  # 주석 금지
```

### 3단계: 실행

#### 3.1 기본 실행 방법
```bash
# 기본 실행 (Qt UI)
python main.py

# 또는 초보 다슬기용 Windows:
.\scripts\win\run.ps1
```

#### 3.2 과거 URWID ui에 익숙한 경우 아래의 방법으로 실행
```bash
python main.py --ui urwid
```

---

## 📁 설정 파일 상세 설명

### .env 파일 (거래소별 API 키)

사용할 거래소만 설정하고 **나머지는 삭제**하세요.

#### Hyperliquid 기반 거래소 (공통 형식)
```env
# 거래소명_WALLET_ADDRESS = 거래 지갑 주소
# 거래소명_AGENT_API_KEY = Agent API 주소 (HL에서 생성)
# 거래소명_AGENT_PRIVATE_KEY = Agent 프라이빗 키

# 예시: Lit 거래소
LIT_WALLET_ADDRESS=0x...
LIT_AGENT_API_KEY=0x...
LIT_AGENT_PRIVATE_KEY=0x...

# 예시: Bullpen 거래소
BULLPEN_WALLET_ADDRESS=0x...
BULLPEN_AGENT_API_KEY=0x...
BULLPEN_AGENT_PRIVATE_KEY=0x...
```

**Agent API 키 만드는 방법:**
1. Hyperliquid 사이트 접속
2. Portfolio → API → Generate API Key
3. API 주소와 프라이빗 키 복사

#### 서브 계정 사용 시
```env
# 서브 계정 사용하려면 _IS_SUB=1 추가
LIT_WALLET_ADDRESS=0x서브계정주소...
LIT_AGENT_API_KEY=0x...
LIT_AGENT_PRIVATE_KEY=0x...
LIT_IS_SUB=1
```

#### Tread.fi 설정
```env
# 보안을 위해 프로그램 실행시 지시하는 로그인 절차를 따르는것을 추천
TREADFI_HL_LOGIN_WALLET_ADDRESS=로그인지갑주소
TREADFI_HL_TRADING_WALLET_ADDRESS=거래계정주소
TREADFI_HL_ACCOUNT_NAME=treadfi에서만든계정이름
# 선택: 자동 로그인 원하면 아래값 설정
TREADFI_HL_LOGIN_WALLET_PRIVATE_KEY=로그인지갑프라이빗키
TREADFI_HL_CSRF_TOKEN=쿠키에서복사
TREADFI_HL_SESSION_ID=쿠키에서복사
```

#### Superstack 설정
```env
SUPERSTACK_WALLET_ADDRESS=0x...
SUPERSTACK_API_KEY=sk_...
```

#### 비-Hyperliquid 거래소

**Backpack:**
```env
BACKPACK_API_KEY=api키
BACKPACK_SECRET_KEY=시크릿키
# https://backpack.exchange/portfolio/settings/api-keys 에서 발급
```

**Lighter:**
```env
LIGHTER_ACCOUNT_ID=계정번호
LIGHTER_PRIVATE_KEY=api프라이빗키
LIGHTER_API_KEY_ID=2
LIGHTER_L1_ADDRESS=EVM지갑주소
# https://app.lighter.xyz/apikeys 에서 발급
```

**Variational:**
```env
# 보안을 위해 자동 로그인 절차를 따르는것을 권고
VARIATIONAL_WALLET_ADDRESS=지갑주소
VARIATIONAL_JWT_TOKEN=vr-token쿠키값
# 자동 로그인을 원할시 아래의 값 설정
VARIATIONAL_PRIVATE_KEY=지갑프라이빗키
```

**기타 거래소:**
```env
# Paradex
PARADEX_L1_ADDRESS=EVM주소
PARADEX_ADDRESS=paradex주소
PARADEX_PRIVATE_KEY=paradex프라이빗키

# EdgeX
EDGEX_ACCOUNT_ID=계정ID
EDGEX_PRIVATE_KEY=프라이빗키

# GRVT
GRVT_API_KEY=api키
GRVT_ACCOUNT_ID=계정ID
GRVT_SECRET_KEY=시크릿키

# Pacifica
PACIFICA_PUBLIC_KEY=지갑주소
PACIFICA_AGENT_PUBLIC_KEY=API주소
PACIFICA_AGENT_PRIVATE_KEY=API프라이빗키
```

---

### config.ini 파일 (거래소 표시/수수료 설정)

#### 기본 구조
```ini
[거래소이름]
show = True              # UI에 표시할지 여부
exchange = hyperliquid   # 거래소 엔진 종류
builder_code = 0x...     # HL 빌더 코드 (HL 기반만)
fee_rate = 20 / 25       # 수수료 (limit / market)
```

#### exchange 값 종류
| 값 | 설명 |
|----|------|
| `hyperliquid` (또는 생략) | 일반 Hyperliquid |
| `superstack` | Superstack |
| `treadfi.hyperliquid` | Tread.fi (HL 계정 사용) |
| `lighter` | Lighter |
| `backpack` | Backpack |
| `variational` | Variational |
| `paradex` | Paradex |
| `edgex` | EdgeX |
| `grvt` | GRVT |
| `pacifica` | Pacifica |

#### 예시
```ini
# 사용할 거래소: show = True
[lit]
builder_code = 0x24a747628494231347f4f6aead2ec14f50bcc8b7
fee_rate = 35 / 50
show = True

# 사용 안 할 거래소: show = False
[paradex]
exchange = paradex
show = False

# 비-HL 거래소는 exchange 필수
[backpack]
exchange = backpack
show = True
```

---

## 사용 방법

버튼 잘 보고 누르면됨

### 주요 기능

#### 기본 거래
1. **심볼 입력**: 거래할 코인 (예: BTC, ETH)
2. **수량 입력**: 거래할 양
3. **Long/Short 선택**: 방향 선택 (미선택 = 비활성)
4. **Market/Limit**: 시장가 또는 지정가
5. **주문 실행**: 해당 거래소만 주문

#### 전체 동작
- **전체 주문 수행**: 활성화된(Long/Short 선택된) 모든 거래소 동시 주문
- **롱/숏 전환**: 활성화된 거래소 방향 반전
- **모든 포지션 종료**: 활성화된 거래소 포지션 시장가 청산

#### 반복 실행
- **반복 수행횟수**: 몇 번 실행할지
- **반복 대기시간**: 실행 사이 대기 (랜덤)
- **반복 실행 버튼**: 시작/중지

#### 태우기 실행

**"태우기"는 파밍/볼륨 쌓기용 기능입니다.**  
롱↔숏을 번갈아가며 자동으로 반복 거래합니다.

| 설정 | 설명 |
|------|------|
| 횟수 | 총 몇 라운드 할지 (-1 로 설정시 무한) |
| min(s) | 라운드 사이 최소 대기 시간(초) |
| max(s) | 라운드 사이 최대 대기 시간(초) |

**작동 방식:**
1. [반복 실행]을 [반복 횟수]만큼 실행
2. [태우기 대기시간] 동안 쉼
3. 방향 반전 (롱→숏 또는 숏→롱)
4. [반복 실행]을 2×[반복 횟수]만큼 실행
5. 2~4를 [태우기 횟수]만큼 반복

**예시 1:** 반복 횟수=5, 태우기 횟수=3, 시작=LONG

| 라운드 | 방향 | 횟수 | 비고 |
|:------:|:----:|:----:|------|
| 1 | LONG | 5회 | 첫 실행 |
| 2 | SHORT | 10회 | 방향 반전, 2배 |
| 3 | LONG | 10회 | 방향 반전, 종료 |

**예시 2:** 반복 횟수=3, 태우기 횟수=5, 시작=SHORT

| 라운드 | 방향 | 횟수 | 비고 |
|:------:|:----:|:----:|------|
| 1 | SHORT | 3회 | 첫 실행 |
| 2 | LONG | 6회 | 방향 반전, 2배 |
| 3 | SHORT | 6회 | 방향 반전 |
| 4 | LONG | 6회 | 방향 반전 |
| 5 | SHORT | 6회 | 종료 |

> 💡 대기 시간은 min~max 사이에서 랜덤으로 결정됩니다.

**주의사항:**
- [태우기 실행] 버튼을 다시 누르면 중지됩니다 (다음 라운드부터 멈춤)
- [반복 실행]의 횟수/min/max 설정도 함께 사용됩니다
- 시작 방향은 현재 선택된 Long/Short에 따릅니다

### HIP-3 DEX 선택 (Hyperliquid 전용)

헤더의 DEX 드롭다운에서 선택:
- **HL**: 기본 Hyperliquid
- **XYZ**: Unit에서 운영
- **FLX**: Felix에서 운영 (USDH 필요)
- **VNTL**: Ventuals에서 운영 (USDH 필요)
- **HYNA**: HyEna에서 운영 (USDE 필요)

> ⚠️ FLX/VNTL 거래 시 **Spot에 USDH**가 있어야 합니다  
> ⚠️ HYNA 거래 시 **Spot에 USDE**가 있어야 합니다

---

## 🔧 Windows 스크립트 정리

| 스크립트 | 용도 |
|----------|------|
| `.\scripts\win\setup.ps1` | 최초 설치 |
| `.\scripts\win\run.ps1` | 평소 실행 |
| `.\scripts\win\update-force.ps1` | 강제 업데이트 (설정 백업됨) |

---

## ❓ 자주 묻는 질문

### Q: "python을 찾을 수 없습니다" 오류
A: Python 설치 시 "Add to PATH" 옵션을 체크했는지 확인하세요.

### Q: 실행이 차단됩니다
A: PowerShell 관리자 권한으로:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
```

### Q: 거래소가 안 보여요
A: `config.ini`에서 해당 거래소 `show = True` 확인

### Q: "API key not found" 오류
A: `.env` 파일에 해당 거래소 키가 올바르게 입력되었는지 확인

### Q: 한글/이모지가 깨져요
A: 폰트 문제입니다. 시스템에 한글 폰트가 설치되어 있는지 확인하세요.

---

## 📋 로드맵

- ✅ Qt UI
- ✅ 다중 DEX 동시 거래
- ✅ REPEAT / BURN 기능
- ✅ HIP-3 DEX 지원 (XYZ, FLX, VNTL, HYNA)
- ✅ 비-HL 거래소 연동
- 🔜 USDC ↔ USDH 스왑 기능
- 🔜 지정가 주문 관리
- 🔜 더 많은 거래소 지원

---

## 📞 문의

문의 하지마
