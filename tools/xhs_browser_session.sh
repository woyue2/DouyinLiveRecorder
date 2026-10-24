#!/bin/bash
# 小紅書真瀏覽器登入環境：Xvfb + x11vnc + noVNC + Chromium
# 由 systemd (xhs-browser.service) 拉起；使用者透過 nginx /vnc/ 操作真實頁面，
# 滑塊驗證直接用滑鼠拖。登入後由 xhs_harvest_cookies.py 收 cookie。
set -u
export DISPLAY=:99

Xvfb :99 -screen 0 900x1500x24 &
sleep 1

# -localhost：只綁 127.0.0.1，對外一律走 nginx /vnc/ 代理。
# 不綁的話 RFB 直接裸奔在 0.0.0.0:5900，弱密碼被爆破等於交出已登入的帳號。
x11vnc -display :99 -passwd "${VNC_PASSWORD:-xhs8888}" -localhost \
       -forever -shared -rfbport 5900 -quiet &
sleep 1

websockify --web=/usr/share/novnc 6080 localhost:5900 &
sleep 1

exec /home/ubuntu/chromium/chrome-linux/chrome \
    --user-data-dir=/home/ubuntu/.xhs-chrome \
    --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --remote-allow-origins=* \
    --no-first-run --no-default-browser-check --disable-gpu --disable-dev-shm-usage \
    --disable-features=Translate,LocalNetworkAccessChecks --lang=zh-CN \
    --window-size=900,1500 --start-maximized \
    "https://www.xiaohongshu.com"
