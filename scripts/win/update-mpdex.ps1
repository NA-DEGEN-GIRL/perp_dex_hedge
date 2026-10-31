# scripts\win\update-mpdex.ps1
# mpdex(multi-perp-dex)만 최신 master로 재설치: .venv/설정 유지, 다른 패키지는 새 의존성만 추가
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_lib.ps1"

if (-not (Test-Path ".\.venv")) { throw ".venv가 없습니다. 먼저 setup.ps1로 설치하세요." }
New-Or-ActivateVenv

$spec = (Get-Content .\requirements.in | Where-Object { $_ -match '^\s*mpdex\s*@' } | Select-Object -First 1)
if (-not $spec) { throw "requirements.in에서 mpdex 항목을 찾지 못했습니다." }
$spec = $spec.Trim()

Write-Host "=== mpdex 업데이트: $spec ==="
if (Get-Command uv -ErrorAction SilentlyContinue) {
  # uv로 만든 .venv에는 pip이 없을 수 있음 → uv 우선
  uv pip install --python .\.venv\Scripts\python.exe --refresh-package mpdex --reinstall-package mpdex -r requirements.in
} else {
  python -m pip install --upgrade --force-reinstall --no-deps $spec
  python -m pip install -r requirements.in   # 새로 추가된 의존성 설치
}

python -c "import importlib.metadata as m, json; d = m.distribution('mpdex'); print('mpdex commit:', json.loads(d.read_text('direct_url.json'))['vcs_info']['commit_id'][:7])"
Write-Host "=== 완료. 실행: .venv\Scripts\python.exe main.py ==="
