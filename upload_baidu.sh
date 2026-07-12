#!/bin/bash

# --- 配置区 ---
MY_EMAIL="你的邮箱@qq.com"  # TODO: 修改为你的邮箱
DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOG_FILE="/home/ubuntu/DouyinLiveRecorder-main/upload.log"

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

# 查找所有 .ts 和 .mp3 文件
find . -not -path "*/converted/*" -type f \( -name "*.ts" -o -name "*.mp3" \) | while read -r file; do
    if lsof "$file" > /dev/null 2>&1; then
        # 发现正在写入的文件，说明正在直播
        anchor_name=$(basename $(dirname "$file"))
        if [ "$HAS_ACTIVE_LIVE" = false ]; then
            send_mail "🎙️ 主播正在直播中" "检测到主播 [${anchor_name}] 正在直播，服务器已开始抓取并分段..."
            HAS_ACTIVE_LIVE=true
        fi
        continue
    fi

    base_name=$(basename "$file")
    base_name_noext="${base_name%.*}"
    convert_dir="./converted"
    mkdir -p "$convert_dir"

    if [[ "$file" == *.ts ]]; then
        # .ts → 转 mp3 再上传
        mp3_file="$convert_dir/${base_name_noext}.mp3"
        ffmpeg -i "$file" -vn -acodec libmp3lame -q:a 2 "$mp3_file" -y -loglevel error
        if [ $? -eq 0 ]; then
            rm -f "$file"
            PROCESSED_FILES="${PROCESSED_FILES}\n- ${base_name_noext}.mp3"
        fi
    else
        # .mp3 → 直接拷贝到 converted 目录统一上传
        cp "$file" "$convert_dir/"
        PROCESSED_FILES="${PROCESSED_FILES}\n- ${base_name}"
    fi
done

# --- 上传与最终通知 ---
BYPY_PATH=$(which bypy)
SYNC_OUT=$($BYPY_PATH --retry 5 --timeout 120 -s 500M syncup "./converted" /live_audio --on-dup overwrite 2>&1)

if [[ $SYNC_OUT == *"OK"* ]]; then
    if [ ! -z "$PROCESSED_FILES" ]; then
        send_mail "✅ 文件上传成功通知" "以下文件已成功转码并上传至百度网盘：${PROCESSED_FILES}"
    fi
    rm -rf ./converted
elif [[ $SYNC_OUT == *"Error"* ]]; then
    send_mail "❌ 上传过程遇到异常" "bypy 同步时出错，请查看日志文件 upload.log。错误摘要：\n${SYNC_OUT}"
fi

# 清理空文件夹
find . -type d -empty -delete 2>/dev/null
echo "--- 任务结束: $(date) ---"
