#!/bin/bash

# --- 配置区 ---
MY_EMAIL="你的邮箱@qq.com"  # TODO: 修改为你的邮箱
DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOG_FILE="/home/ubuntu/DouyinLiveRecorder-main/upload.log"

# 1. 防重运行锁
if pidof -x $(basename "$0") -o %PPID >/dev/null; then
    exit 1
fi

cd "$DOWNLOAD_DIR" || exit 1
echo "--- 任务开始: $(date) ---"

# 查找所有 .ts 和 .mp3 文件（支持4层深度：平台/主播/批次/文件）
find . -path ./converted -prune -o -mindepth 4 -maxdepth 4 -type f \( -name "*.ts" -o -name "*.mp3" \) -print | while read -r file; do
    if lsof "$file" > /dev/null 2>&1; then
        continue
    fi

    # 路径解析: ./<平台>/<主播>/<批次时间>/<文件名>
    file_dir=$(dirname "$file")         # ./<平台>/<主播>/<批次时间>
    batch_dir=$(basename "$file_dir")   # 批次时间
    anchor_dir=$(dirname "$file_dir")   # ./<平台>/<主播>
    anchor_name=$(basename "$anchor_dir") # 主播名

    base_name=$(basename "$file")
    base_name_noext="${base_name%.*}"

    mkdir -p "./converted/$anchor_name/$batch_dir"

    if [[ "$file" == *.ts ]]; then
        mp3_file="./converted/$anchor_name/$batch_dir/${base_name_noext}.mp3"
        ffmpeg -i "$file" -vn -acodec libmp3lame -q:a 2 "$mp3_file" -y -loglevel error
        if [ $? -eq 0 ]; then
            rm -f "$file"
        fi
    else
        if cp "$file" "./converted/$anchor_name/$batch_dir/"; then
            rm -f "$file"
        fi
    fi
done

# 按主播+批次目录分别上传
if [ -d "./converted" ]; then
    for anchor_dir in ./converted/*/; do
        [ -d "$anchor_dir" ] || continue
        anchor_name=$(basename "$anchor_dir")
        for batch_dir in "$anchor_dir"*/; do
            [ -d "$batch_dir" ] || continue
            batch_time=$(basename "$batch_dir")
            python3 -m bypy --retry 5 --timeout 120 -s 500M syncup "$batch_dir" "/live_audio/$anchor_name/$batch_time" --on-dup overwrite 2>&1
        done
    done
    rm -rf ./converted
fi

# 清理空文件夹
find . -type d -empty -delete 2>/dev/null
echo "--- 任务结束: $(date) ---"
