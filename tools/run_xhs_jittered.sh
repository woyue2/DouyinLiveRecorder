#!/bin/bash
# 小紅書巡檢防風控包裝腳本
# 由 cron 在固定基準時間觸發，實際執行前隨機抖動延遲，
# 避免每天在完全相同的時刻發起請求而形成可識別的抓取指紋。
set -uo pipefail

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." &&
    pwd
)"
cd "$SCRIPT_DIR"

# 抖動區間（分鐘）。基準時間 09:00 + 0~240 分鐘 => 實際執行落在 09:00 ~ 13:00
MIN_DELAY_MIN=0
MAX_DELAY_MIN=240

RANGE=$(( MAX_DELAY_MIN - MIN_DELAY_MIN + 1 ))
JITTER_MIN=$(( RANDOM % RANGE + MIN_DELAY_MIN ))
JITTER_SEC=$(( JITTER_MIN * 60 ))

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 小紅書巡檢：本次隨機抖動 ${JITTER_MIN} 分鐘（${JITTER_SEC} 秒）後執行"
sleep "$JITTER_SEC"

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 小紅書巡檢：開始執行"
python3 xhs_note_monitor.py --count 10
EXIT_CODE=$?
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] 小紅書巡檢：結束（退出碼 ${EXIT_CODE}）"
exit "$EXIT_CODE"
