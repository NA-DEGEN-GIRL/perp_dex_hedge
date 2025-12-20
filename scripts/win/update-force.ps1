# scripts\win\update-force.ps1
# 충돌 무시 강제 업데이트: 설정 백업 → 원격 기본 브랜치 강제 동기화 → 설정 복원 → 새 .venv 설치 → 실행
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_lib.ps1"

if (-not (Test-Path ".git")) { throw "여기는 Git 저장소가 아닙니다. 프로젝트 루트에서 실행하세요." }
Write-Host "=== 강제 업데이트 시작 ==="

# 1) 설정 백업
$keep = @(".env",".env.local","config.ini","config.local.ini")
$bkdir = Backup-Configs -KeepList $keep
Write-Host "백업 완료: $bkdir"

# 2) 기존 가상환경 제거(충돌 예방)
if (Test-Path ".\.venv") {
  Write-Host "기존 .venv 제거..."
  Remove-Item -Recurse -Force .\.venv
}

# 3) 원격 최신으로 강제 동기화
$branch = Get-DefaultBranch
Write-Host "원격 가져오기..."
git fetch --all --prune
Write-Host "강제 리셋: origin/$branch"
git reset --hard ("origin/" + $branch)

# 4) 설정 복원
Restore-Configs -BackupDir $bkdir -KeepList $keep

# 5) 새 .venv 생성 및 의존성 재설치
New-Or-ActivateVenv
Pip-Setup

# 6) 실행
Write-Host "=== 실행 ==="
python main.py