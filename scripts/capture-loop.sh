#!/usr/bin/env bash
set -u

device=${1:-/dev/v4l/by-id/usb-MACROSILICON_USB_Video-video-index0}
output_dir=${2:-/tmp/hdmi-capture-loop}
segment_seconds=${HDMI_CAPTURE_SEGMENT_SECONDS:-60}
child_pid=
stop_requested=0

usage() {
    printf 'usage: %s [video-device] [output-directory]\n' "$0" >&2
}

case "$segment_seconds" in
    ''|*[!0-9]*|0)
        printf 'HDMI_CAPTURE_SEGMENT_SECONDS must be a positive integer\n' >&2
        exit 2
        ;;
esac

if [[ ${1:-} == --help ]]; then
    usage
    exit 0
fi

mkdir -p -- "$output_dir"

stop_capture() {
    stop_requested=1
    if [[ -n $child_pid ]]; then
        kill -INT "$child_pid" 2>/dev/null || true
    fi
}
trap stop_capture INT TERM

previous=
while IFS= read -r stale; do
    rm -f -- "$stale"
done < <(find "$output_dir" -maxdepth 1 -type f \
         -name '*.recording.mkv' -print)

while IFS= read -r candidate; do
    if [[ -z $previous ]]; then
        previous=$candidate
    else
        rm -f -- "$candidate"
    fi
done < <(find "$output_dir" -maxdepth 1 -type f -name '*.mkv' \
         ! -name '*.recording.mkv' -printf '%T@ %p\n' |
         sort -nr | cut -d' ' -f2-)

while (( ! stop_requested )); do
    start=$(date +%s)
    expected_end=$((start + segment_seconds))
    current="$output_dir/${start}-${expected_end}.recording.mkv"

    ffmpeg -hide_banner -loglevel warning -nostdin \
        -f v4l2 -thread_queue_size 1024 \
        -input_format mjpeg -framerate 60 -video_size 1280x720 \
        -i "$device" -t "$segment_seconds" -map 0:v:0 -c:v copy \
        -y "$current" &
    child_pid=$!
    wait "$child_pid"
    status=$?
    child_pid=

    if (( stop_requested )); then
        break
    fi
    if (( status != 0 )); then
        printf 'capture failed with status %d; retrying\n' "$status" >&2
        rm -f -- "$current"
        sleep 1
        continue
    fi

    actual_end=$(date +%s)
    complete="$output_dir/${start}-${actual_end}.mkv"
    mv -f -- "$current" "$complete"
    if [[ -n $previous && $previous != "$complete" ]]; then
        rm -f -- "$previous"
    fi
    previous=$complete
done

printf 'capture stopped; incomplete segment retained at %s\n' \
    "${current:-none}" >&2
