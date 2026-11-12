#!/bin/bash
# main.py 看門狗：程序不在就重新拉起。
#
# 為什麼要這支：2026-11-12 00:26 main.py 因磁碟不足觸發自我保護「Exiting program」，
# 之後沒有任何機制把它拉起來，靜默停機 24 小時——期間所有直播都沒錄、投稿也停擺。
# 由 cron 每 10 分鐘檢查一次，磁碟不足時不硬啟（否則會立刻再退出一次）。
set -u
cd /home/ubuntu/DouyinLiveRecorder-main || exit 1

pattern='^python3 -u main\.py$'
pgrep -f "$pattern" >/dev/null 2>&1 && exit 0

avail_kb=$(df --output=avail / | tail -1 | tr -d ' ')
if [ -z "${avail_kb:-}" ] || [ "$avail_kb" -lt 1500000 ]; then
    echo "$(date '+%F %T') 磁碟不足（${avail_kb:-未知}KB），暫不啟動 main.py" >> watchdog.log
    exit 1
fi

echo "$(date '+%F %T') 偵測到 main.py 未運行，重新啟動" >> watchdog.log
bash tools/restart_main.sh >> watchdog.log 2>&1
