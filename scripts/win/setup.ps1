# scripts\win\setup.ps1
# 최초 설치: .venv 생성 → 의존성 설치 → 기본 설정 준비 → 실행
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_lib.ps1"   # 공통 유틸 로드

Write-Host "=== 최초 설치 시작 ==="
New-Or-ActivateVenv
Pip-Setup
Prepare-Configs
Write-Host "=== 실행 ==="
python main.py