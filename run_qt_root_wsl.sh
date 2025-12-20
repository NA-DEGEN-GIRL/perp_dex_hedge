#!/usr/bin/env bash
set -euo pipefail

# --- WSLg 감지 ---
if [ ! -d /mnt/wslg ]; then
  echo "[ERR] /mnt/wslg not found. This looks not like WSLg environment."
  exit 1
fi

# --- root 전용 런타임 디렉토리 생성 ---
mkdir -p /run/user/0
chmod 700 /run/user/0

# --- WSLg 소켓을 root 런타임으로 연결 ---
if [ -S /mnt/wslg/runtime-dir/wayland-0 ]; then
  ln -sf /mnt/wslg/runtime-dir/wayland-0 /run/user/0/wayland-0
else
  echo "[WARN] wayland socket not found: /mnt/wslg/runtime-dir/wayland-0"
fi

if [ -S /mnt/wslg/PulseServer ]; then
  ln -sf /mnt/wslg/PulseServer /run/user/0/PulseServer
fi

# --- 환경변수 설정 ---
export DISPLAY=:0
export WAYLAND_DISPLAY=wayland-0
export XDG_RUNTIME_DIR=/run/user/0
export PULSE_SERVER=/run/user/0/PulseServer
export QT_QPA_PLATFORM=wayland

echo "[OK] WSLg root env prepared:"
echo "     DISPLAY=$DISPLAY"
echo "     WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
echo "     XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
echo "     QT_QPA_PLATFORM=$QT_QPA_PLATFORM"

exec python main.py