# scripts\win\run.ps1
# 평소 실행: .venv이 없으면 자동 생성/설치, 있으면 바로 실행
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_lib.ps1"

Write-Host "=== 실행 준비 ==="
New-Or-ActivateVenv
# 의존성 누락 시 자동 설치(선택적으로 유지). 주기적으로 최신 유지하려면 update-force.ps1 사용.
try {
  python -c "import pkgutil,sys; sys.exit(0)" | Out-Null
} catch {}
Write-Host "=== 실행 ==="
python main.py