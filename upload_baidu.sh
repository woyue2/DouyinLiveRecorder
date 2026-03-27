#!/bin/bash

# --- 配置区 ---
MY_EMAIL="你的邮箱@qq.com"
DOWNLOAD_DIR="/root/DouyinLiveRecorder/downloads"
LOG_FILE="/root/DouyinLiveRecorder/upload.log"

# --- 发信函数 ---
send_mail() {
    local subject="$1"
    local body="$2"
    echo -e "Subject: ${subject}\n\n${body}" | msmtp "${MY_EMAIL}"
}

# 1. 防重运行锁
if pidof -x $(basename "$0") -o %PPID >/dev/null; then
    exit 1
fi

cd "$DOWNLOAD_DIR" || exit 1
echo "--- 任务开始: $(date) ---"

# --- 检查直播状态 & Cookie 是否过期 ---
# 检查 Docker 日志里是否有 Cookie 过期关键字
if docker logs --since 1h douyinliverecorder-app-1 2>&1 | grep -q "Cookie已失效"; then
    send_mail "🚨 警告：抖音 Cookie 已过期" "服务器检测到录制器 Cookie 失效，请及时更新 URL_config.ini 或 config.ini 中的 Cookie。"
fi

# --- 逻辑处理 ---
HAS_ACTIVE_LIVE=false
PROCESSED_FILES=""

# 查找所有 .ts 文件
find . -type f -name "*.ts" | while read -r ts_file; do
    if lsof "$ts_file" > /dev/null 2>&1; then
        # 发现正在写入的文件，说明正在直播
        anchor_name=$(basename $(dirname "$ts_file"))
        if [ "$HAS_ACTIVE_LIVE" = false ]; then
            send_mail "🎙️ 主播正在直播中" "检测到主播 [${anchor_name}] 正在直播，服务器已开始抓取并分段..."
            HAS_ACTIVE_LIVE=true
        fi
        continue
    fi

    # 逻辑转换
    dir_name=$(dirname "$ts_file")
    base_name=$(basename "$ts_file" .ts)
    convert_dir="$dir_name/converted"
    mkdir -p "$convert_dir"
    mp3_file="$convert_dir/${base_name}.mp3"

    ffmpeg -i "$ts_file" -vn -acodec libmp3lame -q:a 2 "$mp3_file" -y -loglevel error
    
    if [ $? -eq 0 ]; then
        rm -f "$ts_file"
        PROCESSED_FILES="${PROCESSED_FILES}\n- ${base_name}.mp3"
    fi
done

# --- 上传与最终通知 ---
BYPY_PATH=$(which bypy)
SYNC_OUT=$($BYPY_PATH --retry 5 --timeout 120 -s 500M syncup "./converted" /live_audio --on-dup overwrite 2>&1)

if [[ $SYNC_OUT == *"OK"* ]]; then
    if [ ! -z "$PROCESSED_FILES" ]; then
        send_mail "✅ 文件上传成功通知" "以下文件已成功转码并上传至百度网盘：${PROCESSED_FILES}"
    fi
elif [[ $SYNC_OUT == *"Error"* ]]; then
    send_mail "❌ 上传过程遇到异常" "bypy 同步时出错，请查看日志文件 upload.log。错误摘要：\n${SYNC_OUT}"
fi

# 清理空文件夹
find . -type d -empty -delete 2>/dev/null
echo "--- 任务结束: $(date) ---"
