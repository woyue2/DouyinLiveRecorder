#!/bin/bash

# --- 配置区 ---
MY_EMAIL="你的邮箱@qq.com"  # TODO: 修改为你的邮箱
DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOG_FILE="/home/ubuntu/DouyinLiveRecorder-main/upload.log"
LOCK_FILE="/tmp/douyin_live_recorder_upload.lock"

log_message() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

# 使用内核文件锁保证定时任务不会重叠运行
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log_message "[跳过任务] 已有上传任务运行"
    exit 0
fi

if ! cd "$DOWNLOAD_DIR"; then
    log_message "[任务失败] 无法进入下载目录: $DOWNLOAD_DIR"
    exit 1
fi

log_message "-------------------- 任务开始 --------------------"

# 查找所有已经正式发布的录制文件；*.part 不会匹配这些扩展名
found_count=0
staged_count=0
occupied_count=0
failed_stage_count=0

while IFS= read -r -d '' file; do
    found_count=$((found_count + 1))
    log_message "[发现文件] $file"

    # 双重防护：即使以后调整 find 表达式，也绝不重新处理 converted。
    case "$file" in
        ./converted/*)
            log_message "[跳过内部文件] $file"
            continue
            ;;
    esac

    if lsof "$file" > /dev/null 2>&1; then
        occupied_count=$((occupied_count + 1))
        log_message "[跳过占用文件] $file"
        continue
    fi

    # 保留平台、主播、批次以及可选日期/标题目录，避免不同来源互相覆盖
    file_dir=$(dirname "$file")
    target_dir="./converted/${file_dir#./}"
    target_file="$target_dir/$(basename "$file")"
    mkdir -p "$target_dir"

    if [ -e "$target_file" ]; then
        if cmp -s -- "$file" "$target_file"; then
            rm -f -- "$file"
            staged_count=$((staged_count + 1))
            log_message "[合并重复文件] $file -> $target_file"
        else
            failed_stage_count=$((failed_stage_count + 1))
            log_message "[暂存冲突，保留源文件] $file -> $target_file"
        fi
        continue
    fi

    if mv -- "$file" "$target_file"; then
        staged_count=$((staged_count + 1))
        log_message "[移入待上传] $file -> $target_file"
    else
        failed_stage_count=$((failed_stage_count + 1))
        log_message "[暂存失败，保留源文件] $file"
    fi
done < <(find . -path ./converted -prune -o -type f \( \
    -name "*.ts" -o \
    -name "*.mkv" -o \
    -name "*.flv" -o \
    -name "*.mp4" -o \
    -name "*.mp3" -o \
    -name "*.m4a" \
\) -print0)

log_message \
    "[扫描汇总] 发现=$found_count 暂存=$staged_count 占用跳过=$occupied_count 暂存失败=$failed_stage_count"

# 单个排他任务同步整个待上传树，成功后统一清理
pending_count=0
if [ -d "./converted" ]; then
    while IFS= read -r -d '' pending_file; do
        pending_count=$((pending_count + 1))
        log_message "[待上传文件] $pending_file"
    done < <(find ./converted -type f \( \
        -name "*.ts" -o \
        -name "*.mkv" -o \
        -name "*.flv" -o \
        -name "*.mp4" -o \
        -name "*.mp3" -o \
        -name "*.m4a" \
    \) -print0)
fi

if [ "$pending_count" -eq 0 ]; then
    log_message "[本轮无待上传文件]"
else
    log_message "[开始上传] 共 $pending_count 个文件"
    if python3 -m bypy --retry 5 --timeout 120 -s 500M \
        syncup "./converted" "/live_audio" --on-dup overwrite 2>&1; then
        while IFS= read -r -d '' uploaded_file; do
            log_message "[上传成功] $uploaded_file"
        done < <(find ./converted -type f -print0)
        rm -rf -- "./converted"
        log_message "[上传完成] 已清理本轮 $pending_count 个待上传文件"
    else
        upload_return_code=$?
        log_message \
            "[上传失败] 返回码=$upload_return_code，$pending_count 个文件保留在 ./converted，等待下次重试"
        while IFS= read -r -d '' retained_file; do
            log_message "[失败保留] $retained_file"
        done < <(find ./converted -type f -print0)
    fi
fi

# 清理空文件夹
find . -type d -empty -delete 2>/dev/null
log_message "-------------------- 任务结束 --------------------"
