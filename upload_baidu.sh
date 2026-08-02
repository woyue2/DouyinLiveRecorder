#!/bin/bash

# --- 配置区 ---
MY_EMAIL="你的邮箱@qq.com"  # TODO: 修改为你的邮箱
DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOG_FILE="/home/ubuntu/DouyinLiveRecorder-main/upload.log"
LOCK_FILE="/tmp/douyin_live_recorder_upload.lock"
SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"

log_message() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

notify_upload() {
    local message="$1"

    if python3 "$SCRIPT_DIR/upload_notify.py" "$message"; then
        log_message "[通知成功] $message"
    else
        log_message "[通知失败] $message"
    fi

    # 通知属于尽力而为的附加功能，不能改变上传任务的结果
    return 0
}

format_pending_files() {
    local max_display_count=10
    local display_count=${#pending_files[@]}
    local file_summary=""
    local index

    if [ "$display_count" -gt "$max_display_count" ]; then
        display_count=$max_display_count
    fi

    for ((index = 0; index < display_count; index++)); do
        file_summary+=$'\n- '"${pending_files[$index]}"
    done

    if [ "${#pending_files[@]}" -gt "$max_display_count" ]; then
        file_summary+=$'\n- ...另有 '"$((${#pending_files[@]} - max_display_count))"$' 个文件'
    fi

    printf '%s' "$file_summary"
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
pending_files=()
if [ -d "./converted" ]; then
    while IFS= read -r -d '' pending_file; do
        pending_count=$((pending_count + 1))
        pending_files+=("${pending_file#./converted/}")
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

# bypy 在部分分片上传失败时退出码仍可能为 0（曾导致误删本地副本），
# 所以成败不能只看退出码：还要检查输出中的失败标记，并在清理前逐目录核对远端文件大小。
# 核对通过后才删除本地 ./converted，防止再次出现静默丢数据。
verify_remote_sizes() {
    local remote_root="/live_audio"
    local list_output=""
    local last_dir=""
    local local_file rel remote_dir fname local_size remote_size
    local ok=0

    while IFS= read -r -d '' local_file; do
        rel="${local_file#./converted/}"
        remote_dir="$remote_root/$(dirname "$rel")"
        fname="$(basename "$rel")"
        local_size="$(stat -c %s "$local_file")"

        if [ "$remote_dir" != "$last_dir" ]; then
            last_dir="$remote_dir"
            list_output="$(python3 -m bypy list "$remote_dir" 2>/dev/null)"
        fi

        remote_size="$(printf '%s\n' "$list_output" |
            awk -v n="$fname" '$1=="F" && $2==n {print $3; exit}')"

        if [ -z "$remote_size" ] || [ "$remote_size" != "$local_size" ]; then
            log_message \
                "[核对失败] $rel 远端缺失或大小不符 (本地=$local_size 远端=${remote_size:-缺失})"
            ok=1
        fi
    done < <(find ./converted -type f -print0)

    return "$ok"
}

if [ "$pending_count" -eq 0 ]; then
    log_message "[本轮无待上传文件]"
else
    pending_file_summary="$(format_pending_files)"
    log_message "[开始上传] 共 $pending_count 个文件"
    notify_upload \
        "[直播录制] 开始上传，共 $pending_count 个文件
文件：$pending_file_summary"
    upload_log_tmp="$(mktemp)"
    # 分片降到 20M：单个分片失败只需重传 20M，秒级失败，不再每次重传 500M 浪费约 18 分钟
    if python3 -m bypy --retry 5 --timeout 120 -s 20M \
        syncup "./converted" "/live_audio" --on-dup overwrite >"$upload_log_tmp" 2>&1 \
        && ! grep -qE "Maximum number|Error [0-9]" "$upload_log_tmp" \
        && verify_remote_sizes; then
        while IFS= read -r -d '' uploaded_file; do
            log_message "[上传成功] $uploaded_file"
        done < <(find ./converted -type f -print0)
        rm -rf -- "./converted"
        log_message "[上传完成] 已清理本轮 $pending_count 个待上传文件"
        notify_upload \
            "[直播录制] 上传成功，共 $pending_count 个文件
文件：$pending_file_summary"
    else
        upload_return_code=$?
        log_message \
            "[上传失败] 返回码=$upload_return_code，$pending_count 个文件保留在 ./converted，等待下次重试"
        while IFS= read -r -d '' retained_file; do
            log_message "[失败保留] $retained_file"
        done < <(find ./converted -type f -print0)
        notify_upload \
            "[直播录制] 上传失败，返回码 $upload_return_code；$pending_count 个文件已保留，稍后自动重试
文件：$pending_file_summary"
    fi
    rm -f -- "$upload_log_tmp"
fi

# 清理空文件夹
find . -type d -empty -delete 2>/dev/null
log_message "-------------------- 任务结束 --------------------"
