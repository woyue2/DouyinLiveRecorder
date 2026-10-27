#!/bin/bash
# 重啟 main.py（錄播主程式）。
#
# 為什麼要這支：`pgrep -f "python3 -u main.py"` 也會命中包著這串字的
# bash 包裝指令，取第一個 pid 時可能殺到包裝層而留下舊的 python，
# 結果新舊兩個實例同時跑（2026-10-27 實際發生，重複監測 12 小時）。
# 這裡改用錨定整條命令列，並在啟動後確認只有一個實例。
set -u
cd /home/ubuntu/DouyinLiveRecorder-main || exit 1

pattern='^python3 -u main\.py$'
pids=$(pgrep -f "$pattern" || true)
if [ -n "$pids" ]; then
    echo "停止既有實例: $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 20); do
        [ -z "$(pgrep -f "$pattern" || true)" ] && break
        sleep 1
    done
    pids=$(pgrep -f "$pattern" || true)
    if [ -n "$pids" ]; then
        echo "仍未結束，強制終止: $pids"
        # shellcheck disable=SC2086
        kill -9 $pids 2>/dev/null || true
        sleep 2
    fi
fi

setsid nohup python3 -u main.py >> output.log 2>&1 < /dev/null &
disown
sleep 8

count=$(pgrep -f "$pattern" | wc -l)
echo "目前 main.py 實例數: $count"
pgrep -af "$pattern" | head -3
[ "$count" -eq 1 ] || { echo "警告：實例數不是 1，請檢查"; exit 1; }
exit 0
