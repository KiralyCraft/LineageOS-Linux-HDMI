#!/usr/bin/env bash
set -Eeuo pipefail

device=${1:-/dev/v4l/by-id/usb-MACROSILICON_USB_Video-video-index0}
output_dir=${2:-/tmp/hdmi-capture-ring}
segment_seconds=${HDMI_CAPTURE_SEGMENT_SECONDS:-60}

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

# FFmpeg retains the V4L2 fd for the entire lifetime of this process.  The
# segment muxer alternates between two Matroska files without stopping UVC
# streaming, which is important because this capture card drops HDMI hotplug
# when the host closes and reopens its video endpoint.
exec ffmpeg -hide_banner -loglevel warning -nostdin \
    -f v4l2 -thread_queue_size 1024 \
    -input_format mjpeg -framerate 60 -video_size 1280x720 \
    -i "$device" -map 0:v:0 -c:v copy \
    -f segment -segment_format matroska \
    -segment_time "$segment_seconds" -segment_wrap 2 -reset_timestamps 1 \
    -y "$output_dir/capture-slot-%01d.mkv"
