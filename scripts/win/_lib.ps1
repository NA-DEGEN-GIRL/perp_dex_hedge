# scripts\win\_lib.ps1
# 공통 유틸(3개 스크립트에서 dot-source로 불러옵니다)
$ErrorActionPreference = "Stop"

function Use-Python {
  param([switch]$ForVenv)
  # 우선순위: py -3.10 → py → python
  if ($ForVenv) {
    $candidates = @("py -3.10", "py", "python")
  } else {
    $candidates = @("py -3.10", "py", "python")
  }
  foreach ($cmd in $candidates) {
    try {
      $v = & $cmd -V 2>$null
      if ($LASTEXITCODE -eq 0 -and $v) { return $cmd }
    } catch {}
  }
  throw "Python 실행파일(py 또는 python)을 찾지 못했습니다. Python 설치 또는 PATH 설정을 확인하세요."
}

function New-Or-ActivateVenv {
  # .venv 없으면 생성, 있으면 활성화
  if (-not (Test-Path ".\.venv")) {
    Write-Host "▶ 가상환경(.venv) 생성 중..."
    $py = Use-Python -ForVenv
    & $py -m venv .venv
  }
  Write-Host "▶ 가상환경 활성화..."
  . .\.venv\Scripts\Activate.ps1
}

function Pip-Setup {
  Write-Host "▶ pip 업그레이드..."
  python -m pip install --upgrade pip | Out-Null
  Write-Host "▶ 의존성 설치..."
  if (Test-Path ".\requirements.in") {
    pip install -r requirements.in
  } elseif (Test-Path ".\requirements.txt") {
    pip install -r requirements.txt
  } else {
    Write-Host "requirements.in/requirements.txt가 없어 의존성 설치를 건너뜁니다."
  }
}

function Prepare-Configs {
  # .env/.ini 기본 파일 준비(있으면 건너뜀)
  if (-not (Test-Path ".\.env") -and (Test-Path ".\.env.example")) {
    Copy-Item ".\.env.example" ".\.env"
    Write-Host "▶ .env 파일을 생성했습니다. 값을 채워주세요(.env는 UTF-8, # 주석 허용)."
  }
  if (-not (Test-Path ".\config.local.ini") -and (Test-Path ".\config.ini")) {
    Copy-Item ".\config.ini" ".\config.local.ini"
    Write-Host "▶ config.local.ini 백업본을 만들었습니다(참고/비교용)."
  }
}

function Get-DefaultBranch {
  try {
    $line = git remote show origin | Select-String "HEAD branch" | ForEach-Object { $_.ToString() }
    if ($line) { return ($line -split ":\s*")[-1].Trim() }
  } catch {}
  return "main"
}

function Backup-Configs {
  param([string[]]$KeepList = @(".env",".env.local","config.ini","config.local.ini"))
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $bkdir = Join-Path "backup" $stamp
  New-Item -ItemType Directory -Force -Path $bkdir | Out-Null
  foreach ($f in $KeepList) {
    if (Test-Path $f) {
      $dest = Join-Path $bkdir $f
      $destDir = Split-Path $dest -Parent
      if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
      Copy-Item $f $dest -Force
      Write-Host "백업: $f -> $dest"
    }
  }
  return $bkdir
}

function Restore-Configs {
  param(
    [string]$BackupDir,
    [string[]]$KeepList = @(".env",".env.local","config.ini","config.local.ini")
  )
  foreach ($f in $KeepList) {
    $src = Join-Path $BackupDir $f
    if (Test-Path $src) {
      Copy-Item $src $f -Force
      Write-Host "복원: $f"
    }
  }
}