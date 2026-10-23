#!/bin/bash
# 小紅書真瀏覽器登入環境：Xvfb + x11vnc + noVNC + Chromium
# 由 systemd (xhs-browser.service) 拉起；使用者透過 nginx /vnc/ 操作真實頁面，
# 滑塊驗證直接用滑鼠拖。登入後由 xhs_harvest_cookies.py 收 cookie。
set -u
export DISPLAY=:99

Xvfb :99 -screen 0 1280x900x24 &
sleep 1

x11vnc -display :99 -passwd xhs8888 -forever -shared -rfbport 5900 -quiet &
sleep 1

websockify --web=/usr/share/novnc 6080 localhost:5900 &
sleep 1

exec /home/ubuntu/chromium/chrome-linux/chrome \
    --user-data-dir=/home/ubuntu/.xhs-chrome \
    --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --remote-allow-origins=* \
    --no-first-run --no-default-browser-check --disable-gpu --disable-dev-shm-usage \
    --disable-features=Translate --lang=zh-CN \
    --window-size=1280,900 --start-maximized \
    "https://www.xiaohongshu.com"
