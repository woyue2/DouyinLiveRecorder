#!/usr/bin/env bash

set -u

if [[ $# -ne 1 ]]; then
    echo "Usage: bash tools/diagnose_ffmpeg.sh /path/to/recording.ffmpeg.log"
    exit 2
fi

source_log=$1
if [[ ! -f "$source_log" ]]; then
    echo "Log file not found: $source_log"
    exit 2
fi

input_url=$(sed -n 's/^input_url: //p' "$source_log" | head -n 1)
if [[ -z "$input_url" ]]; then
    echo "No input_url entry found in: $source_log"
    exit 2
fi

diagnostic_dir=$(mktemp -d "${TMPDIR:-/tmp}/douyin-ffmpeg-diagnose.XXXXXX")
echo "Diagnostic logs: $diagnostic_dir"

run_test() {
    local name=$1
    shift
    echo
    echo "Running: $name"
    "$@" >"$diagnostic_dir/$name.log" 2>&1
    local return_code=$?
    echo "$name return code: $return_code"
    if (( return_code > 128 )); then
        echo "$name signal: $((return_code - 128))"
    fi
}

run_test basic \
    ffmpeg -hide_banner -loglevel trace \
    -f lavfi -i "sine=frequency=1000:sample_rate=44100" \
    -t 1 -f null -

run_test minimal_hls \
    ffmpeg -y -hide_banner -loglevel trace \
    -rw_timeout 15000000 \
    -i "$input_url" \
    -t 5 -map 0 -c copy \
    -f mpegts "$diagnostic_dir/minimal.ts"

run_test segmented_hls \
    ffmpeg -y -hide_banner -loglevel trace \
    -rw_timeout 15000000 \
    -i "$input_url" \
    -t 5 -map 0 -c copy \
    -f segment -segment_time 2 -segment_format mpegts \
    -reset_timestamps 1 "$diagnostic_dir/segment_%03d.ts.part"

echo
echo "Summary:"
for result_log in "$diagnostic_dir"/*.log; do
    echo "----- $(basename "$result_log") -----"
    tail -n 12 "$result_log"
done
