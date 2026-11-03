#!/bin/bash
# 等錄製收播後再重啟 main.py。
#
# 為什麼要這支：改了 main.py 需要重啟才生效，但直接重啟會把正在跑的 ffmpeg
# 一起砍掉，那一場就被當成「斷流」收尾——分段不完整，還可能觸發一次投稿。
# 斷流重連時 ffmpeg 也會短暫消失，所以不能只看「這一刻沒有 ffmpeg」，要連續
# 安靜 10 分鐘才算真的收播。
#
# 用法：setsid nohup bash tools/deferred_restart.sh >> deferred_restart.log 2>&1 &
set -u
cd /home/ubuntu/DouyinLiveRecorder-main || exit 1

QUIET_CHECKS=10          # 連續 10 次檢查都安靜
INTERVAL=60              # 每次間隔 60 秒 → 安靜 10 分鐘

busy() {
    pgrep -f 'ffmpeg ' >/dev/null 2>&1 && return 0      # 錄製或轉碼
    pgrep -f 'biliup ' >/dev/null 2>&1 && return 0      # 投稿中
    return 1
}

echo "$(date '+%F %T') 等待錄製空檔…"
quiet=0
while [ "$quiet" -lt "$QUIET_CHECKS" ]; do
    if busy; then
        [ "$quiet" -gt 0 ] && echo "$(date '+%F %T') 又有 ffmpeg 活動，計時歸零"
        quiet=0
    else
        quiet=$((quiet + 1))
    fi
    sleep "$INTERVAL"
done

# 真正重啟前再確認一次，避免剛好撞上新一輪錄製
if busy; then
    echo "$(date '+%F %T') 最後檢查發現仍在錄製，放棄本次重啟"
    exit 1
fi

echo "$(date '+%F %T') 已安靜 ${QUIET_CHECKS} 分鐘，開始重啟"
bash tools/restart_main.sh
echo "$(date '+%F %T') 重啟結束，退出碼 $?"
