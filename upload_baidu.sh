#!/bin/bash

# Production upload policy:
#   1. Upload one file at a time.
#   2. Verify the exact remote file size.
#   3. Delete only that verified local file.
#   4. Keep raw success and failure logs permanently.

DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOCK_FILE="/tmp/douyin_live_recorder_upload.lock"
REMOTE_ROOT="/live_audio"
BYPY_SLICE_SIZE="1536M"
BYPY_RETRY_COUNT=1
BYPY_TIMEOUT=120
# Files at or above 1.5 GiB would enter bypy's currently denied tmpfile API.
NORMAL_UPLOAD_LIMIT_BYTES=1610612736
# Target size per split part when an oversized file must be divided (~1.2 GiB headroom).
SPLIT_TARGET_BYTES=1258291200

SCRIPT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &&
    pwd
)"
UPLOAD_LOG_DIR="$SCRIPT_DIR/upload_logs"
UPLOAD_DB="$UPLOAD_LOG_DIR/upload_history.sqlite3"
DB_ERROR_LOG="$UPLOAD_LOG_DIR/sqlite_errors.log"
RUN_ID="$(date '+%Y%m%dT%H%M%S%z')-$$"
RUN_STARTED_EPOCH="$(date +%s)"
AI_RUN_REPORT="$UPLOAD_LOG_DIR/ai_${RUN_ID}.json"
AI_LATEST_REPORT="$UPLOAD_LOG_DIR/ai_latest.json"

log_message() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

db_command() {
    python3 "$SCRIPT_DIR/upload_log_db.py" --db "$UPLOAD_DB" "$@" \
        2>>"$DB_ERROR_LOG"
}

notify_upload() {
    local message="$1"
    local summary="${message%%$'\n'*}"

    if python3 "$SCRIPT_DIR/upload_notify.py" "$message"; then
        log_message "[通知成功] $summary"
    else
        log_message "[通知失败] $summary"
    fi
    # Notification is best effort and never changes upload results.
    return 0
}

format_notification_files() {
    local max_display_count=10
    local display_count=$#
    local result=""
    local index
    local files=("$@")

    if [ "$display_count" -gt "$max_display_count" ]; then
        display_count=$max_display_count
    fi
    for ((index = 0; index < display_count; index++)); do
        result+=$'\n- '"${files[$index]}"
    done
    if [ "${#files[@]}" -gt "$max_display_count" ]; then
        result+=$'\n- ...另有 '"$((${#files[@]} - max_display_count))"$' 个文件'
    fi
    printf '%s' "$result"
}

collect_split_parts() {
    # Fill SPLIT_PARTS with existing part files beside $1 (glob order = sorted).
    local src="$1"
    local dir base stem p
    dir="$(dirname -- "$src")"
    base="$(basename -- "$src")"
    stem="${base%.*}"
    SPLIT_PARTS=()
    for p in "$dir/$stem".part[0-9][0-9][0-9].*; do
        [ -f "$p" ] && SPLIT_PARTS+=("$p")
    done
}

split_marker_path() {
    # Marker recording a completed, verified split for $1.
    local dir base stem
    dir="$(dirname -- "$1")"
    base="$(basename -- "$1")"
    stem="${base%.*}"
    printf '%s/%s.split.done' "$dir" "$stem"
}

prepare_split_parts() {
    # Prepare parts below NORMAL_UPLOAD_LIMIT_BYTES for an oversized $1 ($2 = size).
    # bypy output/diagnostics go to $3. Fills SPLIT_PARTS; on failure sets
    # split_error_category / split_error_message and returns non-zero.
    local src="$1" src_size="$2" up_log="$3"
    split_error_category=""
    split_error_message=""
    SPLIT_PARTS=()

    local dir base stem ext marker expected_parts
    dir="$(dirname -- "$src")"
    base="$(basename -- "$src")"
    stem="${base%.*}"
    ext="${base##*.}"
    marker="$(split_marker_path "$src")"

    # Reuse a completed previous split; discard stale leftovers from interrupted runs.
    collect_split_parts "$src"
    if [ "${#SPLIT_PARTS[@]}" -gt 0 ]; then
        expected_parts="$(cat -- "$marker" 2>/dev/null || true)"
        if [ -n "$expected_parts" ] && [ "$expected_parts" = "${#SPLIT_PARTS[@]}" ]; then
            printf '\n===== REUSING %s PARTS FROM PREVIOUS RUN =====\n' "${#SPLIT_PARTS[@]}" >>"$up_log"
            return 0
        fi
        rm -f -- "${SPLIT_PARTS[@]}" "$marker"
        SPLIT_PARTS=()
    fi

    local avail
    avail="$(df -B1 --output=avail -- "$src" | tail -n 1)"
    if [ -z "$avail" ] || [ "$avail" -lt "$src_size" ]; then
        split_error_category="insufficient_disk_space"
        split_error_message="磁盘剩余空间不足，无法切分（需要约 ${src_size} 字节，可用 ${avail:-未知} 字节）"
        return 1
    fi

    local parts=$(( (src_size + SPLIT_TARGET_BYTES - 1) / SPLIT_TARGET_BYTES ))
    local split_ok=1

    case "$ext" in
        ts|flv|mkv|mp4)
            local duration segment_time
            duration="$(ffprobe -v error -show_entries format=duration \
                -of default=noprint_wrappers=1:nokey=1 -- "$src" 2>>"$up_log" | \
                awk '{printf "%d", $1}')"
            if ! [[ "$duration" =~ ^[1-9][0-9]*$ ]]; then
                split_error_category="probe_failed"
                split_error_message="ffprobe 无法读取媒体时长，无法按时间切分；文件已保留"
                return 1
            fi
            segment_time=$(( duration / parts ))
            [ "$segment_time" -lt 1 ] && segment_time=1
            {
                printf '\n===== OVERSIZE SPLIT: ffmpeg copy segment_time=%ss parts=%s =====\n' \
                    "$segment_time" "$parts"
            } >>"$up_log"
            ffmpeg -nostdin -hide_banner -loglevel warning -y \
                -i "$src" -c copy -f segment \
                -segment_time "$segment_time" -reset_timestamps 1 \
                "$dir/$stem.part%03d.$ext" >>"$up_log" 2>&1 || split_ok=0
            ;;
        *)
            local chunk_bytes=$(( (src_size + parts - 1) / parts ))
            {
                printf '\n===== OVERSIZE BINARY SPLIT: chunk=%s bytes parts=%s =====\n' \
                    "$chunk_bytes" "$parts"
            } >>"$up_log"
            split -b "$chunk_bytes" -d -a 3 --additional-suffix ".$ext" \
                -- "$src" "$dir/$stem.part" >>"$up_log" 2>&1 || split_ok=0
            ;;
    esac

    if [ "$split_ok" -ne 1 ]; then
        collect_split_parts "$src"
        [ "${#SPLIT_PARTS[@]}" -gt 0 ] && rm -f -- "${SPLIT_PARTS[@]}"
        rm -f -- "$marker"
        SPLIT_PARTS=()
        split_error_category="split_failed"
        split_error_message="切分命令执行失败（详见 $up_log）；原文件已保留"
        return 1
    fi

    collect_split_parts "$src"
    if [ "${#SPLIT_PARTS[@]}" -eq 0 ]; then
        rm -f -- "$marker"
        split_error_category="split_failed"
        split_error_message="切分后未产生任何分片；原文件已保留"
        return 1
    fi

    # Integrity gate before anything is uploaded or deleted.
    local p psz max_part=0 total_bytes=0 too_big=0
    for p in "${SPLIT_PARTS[@]}"; do
        psz="$(stat -c %s -- "$p")"
        total_bytes=$((total_bytes + psz))
        [ "$psz" -gt "$max_part" ] && max_part=$psz
        [ "$psz" -ge "$NORMAL_UPLOAD_LIMIT_BYTES" ] && too_big=1
    done
    if [ "$too_big" -ne 0 ] || [ "$total_bytes" -lt $(( src_size * 99 / 100 )) ]; then
        rm -f -- "${SPLIT_PARTS[@]}"
        SPLIT_PARTS=()
        split_error_category="split_failed"
        split_error_message="切分结果校验失败（最大分片=$max_part 字节，合计=$total_bytes 字节，原文件=$src_size 字节），已清理分片并保留原文件"
        return 1
    fi

    printf '%s\n' "${#SPLIT_PARTS[@]}" >"$marker" || true
    return 0
}

run_upload_and_verify() {
    # Upload one local file via bypy, then verify the exact remote size.
    # Sets UPLOAD_EXIT_CODE REMOTE_SIZE ERROR_CODE ERROR_CATEGORY ERROR_MESSAGE
    # RAPIDUPLOAD_FALLBACK. Returns 0 only when fully verified.
    local up_local="$1" up_remote="$2" up_log="$3"
    local tmp_log="${up_log}.bypy.tmp"

    UPLOAD_EXIT_CODE=0
    REMOTE_SIZE=""
    ERROR_CODE=""
    ERROR_CATEGORY=""
    ERROR_MESSAGE=""
    RAPIDUPLOAD_FALLBACK=0
    : >"$tmp_log"

    python3 -m bypy -v \
        --retry "$BYPY_RETRY_COUNT" \
        --timeout "$BYPY_TIMEOUT" \
        -s "$BYPY_SLICE_SIZE" \
        upload "$up_local" "$up_remote" overwrite \
        >>"$tmp_log" 2>&1
    UPLOAD_EXIT_CODE=$?

    # 31023 from _rapidupload_file_act is a rejected rapid-upload attempt.
    # If bypy subsequently uploads normally, the file may still succeed.
    if grep -qE '_rapidupload_file_act|method[^[:alnum:]]+rapidupload' "$tmp_log" \
        && grep -qE 'Error code:[[:space:]]*31023|error_code[^0-9]+31023' "$tmp_log"; then
        RAPIDUPLOAD_FALLBACK=1
    fi

    local terminal_error
    terminal_error="$(grep -E '^Error [0-9]+[[:space:]]*$' "$tmp_log" | tail -n 1 || true)"
    if [ -n "$terminal_error" ]; then
        ERROR_CODE="${terminal_error#Error }"
    elif grep -qE 'Error code:[[:space:]]*31064|Error 31064' "$tmp_log"; then
        ERROR_CODE="31064"
    fi

    if [ "$ERROR_CODE" = "31064" ]; then
        ERROR_CATEGORY="authorization_31064"
        ERROR_MESSAGE="百度 tmpfile 分片接口拒绝文件上传"
    elif [ "$UPLOAD_EXIT_CODE" -ne 0 ]; then
        ERROR_CATEGORY="upload_process_failed"
        ERROR_MESSAGE="bypy 进程退出码为 $UPLOAD_EXIT_CODE"
    elif [ -n "$terminal_error" ]; then
        ERROR_CATEGORY="bypy_terminal_error"
        ERROR_MESSAGE="$terminal_error"
    elif ! grep -qE ' OK\.[[:space:]]*$' "$tmp_log"; then
        ERROR_CATEGORY="missing_success_marker"
        ERROR_MESSAGE="bypy 未输出最终成功标记"
    else
        local meta_output meta_exit_code local_bytes
        local_bytes="$(stat -c %s -- "$up_local")"
        meta_output="$(python3 -m bypy --retry "$BYPY_RETRY_COUNT" \
            --timeout "$BYPY_TIMEOUT" meta "$up_remote" '$s' 2>&1)"
        meta_exit_code=$?
        {
            printf '\n===== REMOTE SIZE VERIFICATION =====\n'
            printf '%s\n' "$meta_output"
        } >>"$tmp_log"
        REMOTE_SIZE="$(printf '%s\n' "$meta_output" | \
            awk '/^[[:space:]]*[0-9]+[[:space:]]*$/ {value=$1} END {print value}')"

        if [ "$meta_exit_code" -ne 0 ] || [ -z "$REMOTE_SIZE" ]; then
            ERROR_CATEGORY="remote_meta_failed"
            ERROR_MESSAGE="无法读取远端文件大小"
        elif [ "$REMOTE_SIZE" != "$local_bytes" ]; then
            ERROR_CATEGORY="remote_size_mismatch"
            ERROR_MESSAGE="远端大小不一致：本地=$local_bytes 远端=$REMOTE_SIZE"
        fi
    fi

    { printf '\n===== BYPY RAW OUTPUT =====\n'; cat -- "$tmp_log"; } >>"$up_log"
    rm -f -- "$tmp_log"
    [ -z "$ERROR_CATEGORY" ]
}

finish_run_record() {
    local status="$1"
    local elapsed
    elapsed=$(($(date +%s) - RUN_STARTED_EPOCH))
    db_command run-finish \
        --run-id "$RUN_ID" \
        --status "$status" \
        --success-count "$success_count" \
        --failed-count "$failed_count" \
        --elapsed-seconds "$elapsed" >/dev/null || true
}

write_ai_report() {
    local temporary_report="${AI_RUN_REPORT}.tmp"
    if db_command ai-report --limit 100 >"$temporary_report"; then
        mv -- "$temporary_report" "$AI_RUN_REPORT"
        cp -- "$AI_RUN_REPORT" "$AI_LATEST_REPORT"
    else
        rm -f -- "$temporary_report"
        log_message "[SQLite警告] AI 报告生成失败，详见 $DB_ERROR_LOG"
    fi
}

mkdir -p "$UPLOAD_LOG_DIR"
db_command init >/dev/null || log_message "[SQLite警告] 初始化失败，详见 $DB_ERROR_LOG"

# Prevent overlapping cron jobs.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log_message "[任务跳过] 已有上传任务运行"
    exit 0
fi

if ! cd "$DOWNLOAD_DIR"; then
    log_message "[任务失败] 无法进入下载目录: $DOWNLOAD_DIR"
    exit 1
fi

log_message "[任务开始] run_id=$RUN_ID 模式=逐文件上传/核验/删除"

# Stage completed recordings while preserving the source directory structure.
found_count=0
staged_count=0
occupied_count=0
failed_stage_count=0

while IFS= read -r -d '' file; do
    found_count=$((found_count + 1))

    case "$file" in
        ./converted/*) continue ;;
    esac

    if lsof "$file" >/dev/null 2>&1; then
        occupied_count=$((occupied_count + 1))
        continue
    fi

    # 转写占位：mp3 正在被转写（main.py 创建 .transcribing），跳过避免抢先上传删除
    if [ -e "${file}.transcribing" ]; then
        continue
    fi

    file_dir="$(dirname "$file")"
    target_dir="./converted/${file_dir#./}"
    target_file="$target_dir/$(basename "$file")"
    mkdir -p "$target_dir"

    if [ -e "$target_file" ]; then
        failed_stage_count=$((failed_stage_count + 1))
        log_message "[暂存冲突] 保留源文件: $file"
        continue
    fi

    if mv -- "$file" "$target_file"; then
        staged_count=$((staged_count + 1))
    else
        failed_stage_count=$((failed_stage_count + 1))
        log_message "[暂存失败] 保留源文件: $file"
    fi
done < <(find . -path ./converted -prune -o -type f \( \
    -name "*.ts" -o -name "*.mkv" -o -name "*.flv" -o \
    -name "*.mp4" -o -name "*.mp3" -o -name "*.m4a" -o -name "*.md" \
\) -print0)

log_message "[扫描汇总] 发现=$found_count 暂存=$staged_count 占用跳过=$occupied_count 暂存失败=$failed_stage_count"

pending_local_files=()
pending_relative_files=()
pending_count=0
pending_bytes=0
if [ -d ./converted ]; then
    while IFS= read -r -d '' pending_file; do
        relative_file="${pending_file#./converted/}"
        file_size="$(stat -c %s "$pending_file")"
        pending_local_files+=("$pending_file")
        pending_relative_files+=("$relative_file")
        pending_count=$((pending_count + 1))
        pending_bytes=$((pending_bytes + file_size))
    done < <(find ./converted -type f \( \
        -name "*.ts" -o -name "*.mkv" -o -name "*.flv" -o \
        -name "*.mp4" -o -name "*.mp3" -o -name "*.m4a" -o -name "*.md" \
    \) ! -name '*.part[0-9][0-9][0-9].*' ! -name '*.split.done' -print0 | sort -z)
fi

db_command run-start \
    --run-id "$RUN_ID" \
    --mode per-file \
    --slice-size "$BYPY_SLICE_SIZE" \
    --retry-count "$BYPY_RETRY_COUNT" \
    --pending-count "$pending_count" \
    --pending-bytes "$pending_bytes" >/dev/null || true
db_command event \
    --run-id "$RUN_ID" \
    --level info \
    --type scan_summary \
    --message "录制目录扫描完成" \
    --details-json "{\"found\":$found_count,\"staged\":$staged_count,\"occupied\":$occupied_count,\"stage_failed\":$failed_stage_count}" \
    >/dev/null || true
if [ "$failed_stage_count" -gt 0 ]; then
    db_command event \
        --run-id "$RUN_ID" \
        --level warning \
        --type staging_problem \
        --message "$failed_stage_count 个源文件暂存失败或冲突，均已保留" \
        >/dev/null || true
fi

success_count=0
failed_count=0
success_files=()
failed_files=()

if [ "$pending_count" -eq 0 ]; then
    log_message "[任务完成] 无待上传文件"
    finish_run_record success
    write_ai_report
    exit 0
fi

log_message "[上传队列] 文件=$pending_count 总字节=$pending_bytes 分片阈值=$BYPY_SLICE_SIZE"
pending_summary="$(format_notification_files "${pending_relative_files[@]}")"
notify_upload "[直播录制] 开始逐文件上传：$pending_count 个文件，总计 $pending_bytes 字节
待上传文件：${pending_summary}"

for ((index = 0; index < pending_count; index++)); do
    sequence=$((index + 1))
    local_file="${pending_local_files[$index]}"
    relative_file="${pending_relative_files[$index]}"
    remote_file="$REMOTE_ROOT/$relative_file"
    local_size="$(stat -c %s "$local_file")"
    extension="${relative_file##*.}"
    file_started_epoch="$(date +%s)"
    detail_base="$UPLOAD_LOG_DIR/bypy_${RUN_ID}_$(printf '%04d' "$sequence")"
    running_log="${detail_base}.running.log"
    success_log="${detail_base}.success.log"
    failed_log="${detail_base}.failed.log"

    file_id="$(db_command file-start \
        --run-id "$RUN_ID" \
        --sequence "$sequence" \
        --relative-path "$relative_file" \
        --local-path "$DOWNLOAD_DIR/${local_file#./}" \
        --remote-path "$remote_file" \
        --extension "$extension" \
        --local-size "$local_size" || true)"

    log_message "[文件开始 $sequence/$pending_count] $relative_file ($local_size 字节)"
    process_exit_code=0
    remote_size=""
    error_code=""
    error_category=""
    error_message=""
    rapidupload_fallback=0
    split_part_count=0
    route_note="普通上传"

    if [ "$local_size" -ge "$NORMAL_UPLOAD_LIMIT_BYTES" ]; then
        # Oversized: bypy would enter the denied tmpfile slice API (31064).
        # Split locally into < limit parts, upload and verify each part, and
        # delete the original only after every part is verified remotely.
        if ! prepare_split_parts "$local_file" "$local_size" "$running_log"; then
            error_category="$split_error_category"
            error_message="$split_error_message"
        else
            split_part_count="${#SPLIT_PARTS[@]}"
            route_note="已切分为 $split_part_count 段上传"
            if [ -n "$file_id" ]; then
                db_command event \
                    --run-id "$RUN_ID" \
                    --file-id "$file_id" \
                    --level info \
                    --type oversize_split \
                    --message "文件 $local_size 字节超过单文件上限，已切分为 $split_part_count 段逐段上传核验" \
                    >/dev/null || true
            fi
            part_index=0
            all_parts_ok=1
            for part_file in "${SPLIT_PARTS[@]}"; do
                part_index=$((part_index + 1))
                part_base="$(basename -- "$part_file")"
                part_rel_dir="$(dirname -- "$relative_file")"
                if [ "$part_rel_dir" = "." ]; then
                    part_remote="$REMOTE_ROOT/$part_base"
                else
                    part_remote="$REMOTE_ROOT/$part_rel_dir/$part_base"
                fi

                printf '\n===== PART %s/%s: %s =====\n' \
                    "$part_index" "$split_part_count" "$part_base" >>"$running_log"

                run_upload_and_verify "$part_file" "$part_remote" "$running_log"
                [ "$RAPIDUPLOAD_FALLBACK" -eq 1 ] && rapidupload_fallback=1

                if [ -n "$ERROR_CATEGORY" ]; then
                    all_parts_ok=0
                    process_exit_code=$UPLOAD_EXIT_CODE
                    error_code=$ERROR_CODE
                    error_category="oversize_part_failed"
                    error_message="切分为 $split_part_count 段后第 $part_index 段（$part_base）上传失败：$ERROR_MESSAGE"
                    log_message "[分片失败 $sequence/$pending_count] 保留分片与原文件: $part_base；$ERROR_MESSAGE"
                    break
                fi

                if ! rm -f -- "$part_file"; then
                    all_parts_ok=0
                    error_category="local_delete_failed"
                    error_message="第 $part_index 段远端核验成功，但删除本地分片失败；本地文件保留"
                    log_message "[分片警告 $sequence/$pending_count] $error_message: $part_base"
                    break
                fi
                log_message "[分片成功 $sequence/$pending_count] $part_index/$split_part_count 已核验并删除本地: $part_base ($REMOTE_SIZE 字节)"
            done
            if [ "$all_parts_ok" -eq 1 ] && [ "$rapidupload_fallback" -eq 1 ]; then
                route_note="$route_note；发生秒传回退"
            fi
        fi
    else
        run_upload_and_verify "$local_file" "$remote_file" "$running_log"
        process_exit_code=$UPLOAD_EXIT_CODE
        remote_size=$REMOTE_SIZE
        error_code=$ERROR_CODE
        error_category=$ERROR_CATEGORY
        error_message=$ERROR_MESSAGE
        rapidupload_fallback=$RAPIDUPLOAD_FALLBACK

        if [ "$rapidupload_fallback" -eq 1 ]; then
            route_note="秒传失败后回退普通上传"
            if [ -n "$file_id" ]; then
                db_command event \
                    --run-id "$RUN_ID" \
                    --file-id "$file_id" \
                    --level info \
                    --type rapidupload_fallback \
                    --message "秒传返回 31023，bypy 开始回退普通上传" \
                    >/dev/null || true
            fi
        fi
    fi

    if [ -z "$error_category" ]; then
        delete_also="$(split_marker_path "$local_file")"
        if rm -f -- "$local_file" "$delete_also"; then
            mv -- "$running_log" "$success_log"
            success_count=$((success_count + 1))
            success_files+=("$relative_file")
            elapsed=$(($(date +%s) - file_started_epoch))
            log_message "[文件成功 $sequence/$pending_count] $route_note；已核验并删除本地文件: $relative_file (${elapsed}s)"
            if [ -n "$file_id" ]; then
                finish_args=(file-finish
                    --file-id "$file_id" --status success
                    --elapsed-seconds "$elapsed"
                    --process-exit-code "$process_exit_code"
                    --detail-log-path "$success_log"
                    --rapidupload-fallback "$rapidupload_fallback"
                    --raw-output-file "$success_log"
                    --local-deleted 1)
                [ -n "$remote_size" ] && finish_args+=(--remote-size "$remote_size")
                db_command "${finish_args[@]}" >/dev/null || true
            fi
            continue
        fi
        error_category="local_delete_failed"
        error_message="远端核验成功，但删除本地文件失败；本地文件保留"
    fi

    mv -- "$running_log" "$failed_log"
    failed_count=$((failed_count + 1))
    failed_files+=("$relative_file：$error_message")
    elapsed=$(($(date +%s) - file_started_epoch))
    route_note=""
    [ "$rapidupload_fallback" -eq 1 ] && route_note="；发生秒传回退"
    log_message "[文件失败 $sequence/$pending_count] 保留本地文件: $relative_file；$error_message$route_note"
    if [ -n "$file_id" ]; then
        finish_args=(file-finish
            --file-id "$file_id" --status failed
            --elapsed-seconds "$elapsed"
            --process-exit-code "$process_exit_code"
            --error-category "$error_category"
            --error-message "$error_message"
            --detail-log-path "$failed_log"
            --rapidupload-fallback "$rapidupload_fallback"
            --raw-output-file "$failed_log"
            --local-deleted 0)
        [ -n "$remote_size" ] && finish_args+=(--remote-size "$remote_size")
        [ -n "$error_code" ] && finish_args+=(--error-code "$error_code")
        db_command "${finish_args[@]}" >/dev/null || true
    fi
done

# Remove empty staging directories only. Never bulk-delete converted or files.
if [ -d ./converted ]; then
    find ./converted -depth -type d -empty -delete 2>/dev/null || true
fi

elapsed_total=$(($(date +%s) - RUN_STARTED_EPOCH))
if [ "$failed_count" -eq 0 ]; then
    run_status="success"
    success_summary="$(format_notification_files "${success_files[@]}")"
    log_message "[任务完成] 成功=$success_count 失败=0 耗时=${elapsed_total}s"
    notify_upload "[直播录制] 上传完成：成功 $success_count，失败 0，耗时 ${elapsed_total}s
成功文件：${success_summary}"
else
    if [ "$success_count" -gt 0 ]; then
        run_status="partial"
    else
        run_status="failed"
    fi
    failure_summary="$(format_notification_files "${failed_files[@]}")"
    if [ "$success_count" -gt 0 ]; then
        success_summary="$(format_notification_files "${success_files[@]}")"
        success_section=$'\n成功文件：'"$success_summary"
    else
        success_section=$'\n成功文件：无'
    fi
    log_message "[任务完成] 状态=$run_status 成功=$success_count 失败=$failed_count 耗时=${elapsed_total}s；失败文件均已保留"
    notify_upload "[直播录制] 上传异常：成功 $success_count，失败 $failed_count，失败文件已保留${success_section}
失败文件：${failure_summary}"
fi

finish_run_record "$run_status"
write_ai_report
log_message "[排查入口] 人工明细=$UPLOAD_LOG_DIR；AI报告=$AI_LATEST_REPORT；SQLite=$UPLOAD_DB"

[ "$failed_count" -eq 0 ]
