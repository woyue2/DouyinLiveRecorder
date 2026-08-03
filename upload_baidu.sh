#!/bin/bash

# Production upload policy:
#   1. Upload one file at a time.
#   2. Verify the exact remote file size.
#   3. Delete only that verified local file.
#   4. Keep raw success and failure logs permanently.

DOWNLOAD_DIR="/home/ubuntu/DouyinLiveRecorder-main/downloads"
LOCK_FILE="/tmp/douyin_live_recorder_upload.lock"
REMOTE_ROOT="/live_audio"
BYPY_SLICE_SIZE="1G"
BYPY_RETRY_COUNT=1
BYPY_TIMEOUT=120
# Files at or above this limit would enter bypy's currently denied tmpfile API.
NORMAL_UPLOAD_LIMIT_BYTES=1073741824

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

format_failure_files() {
    local max_display_count=10
    local display_count=${#failed_files[@]}
    local result=""
    local index

    if [ "$display_count" -gt "$max_display_count" ]; then
        display_count=$max_display_count
    fi
    for ((index = 0; index < display_count; index++)); do
        result+=$'\n- '"${failed_files[$index]}"
    done
    if [ "${#failed_files[@]}" -gt "$max_display_count" ]; then
        result+=$'\n- ...另有 '"$((${#failed_files[@]} - max_display_count))"$' 个文件'
    fi
    printf '%s' "$result"
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
    -name "*.mp4" -o -name "*.mp3" -o -name "*.m4a" \
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
        -name "*.mp4" -o -name "*.mp3" -o -name "*.m4a" \
    \) -print0 | sort -z)
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
failed_files=()

if [ "$pending_count" -eq 0 ]; then
    log_message "[任务完成] 无待上传文件"
    finish_run_record success
    write_ai_report
    exit 0
fi

log_message "[上传队列] 文件=$pending_count 总字节=$pending_bytes 分片阈值=$BYPY_SLICE_SIZE"
notify_upload "[直播录制] 开始逐文件上传：$pending_count 个文件，总计 $pending_bytes 字节"

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

    if [ "$local_size" -ge "$NORMAL_UPLOAD_LIMIT_BYTES" ]; then
        process_exit_code=2
        error_category="requires_denied_slice_api"
        error_message="文件达到 1GiB，会进入已确认返回 31064 的 tmpfile 分片接口；已跳过并保留"
        printf '%s\n' "$error_message" >"$running_log"
    else
        python3 -m bypy -v \
            --retry "$BYPY_RETRY_COUNT" \
            --timeout "$BYPY_TIMEOUT" \
            -s "$BYPY_SLICE_SIZE" \
            upload "$local_file" "$remote_file" overwrite \
            >"$running_log" 2>&1
        process_exit_code=$?

        # 31023 from _rapidupload_file_act is a rejected rapid-upload attempt.
        # If bypy subsequently uploads normally, the file may still succeed.
        if grep -qE '_rapidupload_file_act|method[^[:alnum:]]+rapidupload' "$running_log" \
            && grep -qE 'Error code:[[:space:]]*31023|error_code[^0-9]+31023' "$running_log"; then
            rapidupload_fallback=1
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

        terminal_error="$(grep -E '^Error [0-9]+[[:space:]]*$' "$running_log" | tail -n 1 || true)"
        if [ -n "$terminal_error" ]; then
            error_code="${terminal_error#Error }"
        elif grep -qE 'Error code:[[:space:]]*31064|Error 31064' "$running_log"; then
            error_code="31064"
        fi

        if [ "$error_code" = "31064" ]; then
            error_category="authorization_31064"
            error_message="百度 tmpfile 分片接口拒绝文件上传"
        elif [ "$process_exit_code" -ne 0 ]; then
            error_category="upload_process_failed"
            error_message="bypy 进程退出码为 $process_exit_code"
        elif [ -n "$terminal_error" ]; then
            error_category="bypy_terminal_error"
            error_message="$terminal_error"
        elif ! grep -qE ' OK\.[[:space:]]*$' "$running_log"; then
            error_category="missing_success_marker"
            error_message="bypy 未输出最终成功标记"
        else
            meta_output="$(python3 -m bypy --retry "$BYPY_RETRY_COUNT" \
                --timeout "$BYPY_TIMEOUT" meta "$remote_file" '$s' 2>&1)"
            meta_exit_code=$?
            {
                printf '\n===== REMOTE SIZE VERIFICATION =====\n'
                printf '%s\n' "$meta_output"
            } >>"$running_log"
            remote_size="$(printf '%s\n' "$meta_output" | \
                awk '/^[[:space:]]*[0-9]+[[:space:]]*$/ {value=$1} END {print value}')"

            if [ "$meta_exit_code" -ne 0 ] || [ -z "$remote_size" ]; then
                error_category="remote_meta_failed"
                error_message="无法读取远端文件大小"
            elif [ "$remote_size" != "$local_size" ]; then
                error_category="remote_size_mismatch"
                error_message="远端大小不一致：本地=$local_size 远端=$remote_size"
            fi
        fi
    fi

    if [ -z "$error_category" ]; then
        if rm -f -- "$local_file"; then
            mv -- "$running_log" "$success_log"
            success_count=$((success_count + 1))
            elapsed=$(($(date +%s) - file_started_epoch))
            route_note="普通上传"
            [ "$rapidupload_fallback" -eq 1 ] && route_note="秒传失败后回退普通上传"
            log_message "[文件成功 $sequence/$pending_count] $route_note；已核验并删除本地文件: $relative_file (${elapsed}s)"
            if [ -n "$file_id" ]; then
                db_command file-finish \
                    --file-id "$file_id" --status success \
                    --elapsed-seconds "$elapsed" \
                    --process-exit-code "$process_exit_code" \
                    --remote-size "$remote_size" \
                    --detail-log-path "$success_log" \
                    --rapidupload-fallback "$rapidupload_fallback" \
                    --raw-output-file "$success_log" \
                    --local-deleted 1 >/dev/null || true
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
    log_message "[任务完成] 成功=$success_count 失败=0 耗时=${elapsed_total}s"
    notify_upload "[直播录制] 上传完成：成功 $success_count，失败 0，耗时 ${elapsed_total}s"
else
    if [ "$success_count" -gt 0 ]; then
        run_status="partial"
    else
        run_status="failed"
    fi
    failure_summary="$(format_failure_files)"
    log_message "[任务完成] 状态=$run_status 成功=$success_count 失败=$failed_count 耗时=${elapsed_total}s；失败文件均已保留"
    notify_upload "[直播录制] 上传异常：成功 $success_count，失败 $failed_count，失败文件已保留${failure_summary}"
fi

finish_run_record "$run_status"
write_ai_report
log_message "[排查入口] 人工明细=$UPLOAD_LOG_DIR；AI报告=$AI_LATEST_REPORT；SQLite=$UPLOAD_DB"

[ "$failed_count" -eq 0 ]
