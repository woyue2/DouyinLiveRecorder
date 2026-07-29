#!/bin/bash

# --- 配置区 ---
MY_EMAIL="你的邮箱@qq.com"  # TODO: 修改为你的邮箱
DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOG_FILE="/home/ubuntu/DouyinLiveRecorder-main/upload.log"
LOCK_FILE="/tmp/douyin_live_recorder_upload.lock"

# 使用内核文件锁保证定时任务不会重叠运行
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "--- 已有上传任务运行，本次跳过: $(date) ---"
    exit 1
fi

cd "$DOWNLOAD_DIR" || exit 1
echo "--- 任务开始: $(date) ---"

# 查找所有已经正式发布的录制文件；*.part 不会匹配这些扩展名
find . -path ./converted -prune -o -type f \( \
    -name "*.ts" -o \
    -name "*.mkv" -o \
    -name "*.flv" -o \
    -name "*.mp4" -o \
    -name "*.mp3" -o \
    -name "*.m4a" \
\) -print0 | while IFS= read -r -d '' file; do
    if lsof "$file" > /dev/null 2>&1; then
        continue
    fi

    # 保留平台、主播、批次以及可选日期/标题目录，避免不同来源互相覆盖
    file_dir=$(dirname "$file")
    target_dir="./converted/${file_dir#./}"
    mkdir -p "$target_dir"

    if cp "$file" "$target_dir/"; then
        rm -f "$file"
    fi
done

# 单个排他任务同步整个待上传树，成功后统一清理
if [ -d "./converted" ]; then
    if python3 -m bypy --retry 5 --timeout 120 -s 500M \
        syncup "./converted" "/live_audio" --on-dup overwrite 2>&1; then
        rm -rf "./converted"
    fi
fi

# 清理空文件夹
find . -type d -empty -delete 2>/dev/null
echo "--- 任务结束: $(date) ---"
