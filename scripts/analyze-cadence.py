#!/usr/bin/env python3
"""Decode the frame IDs emitted by native/probes/sdl-frame-pacing.c."""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


VIEWPORT_RE = re.compile(r"^(\d+)x(\d+)\+(\d+)\+(\d+)$")


@dataclass(frozen=True)
class DecodedFrame:
    pts_ms: float
    top: int | None
    bottom: int | None


def parse_viewport(value: str) -> tuple[int, int, int, int]:
    match = VIEWPORT_RE.fullmatch(value)
    if not match:
        raise argparse.ArgumentTypeError("expected WIDTHxHEIGHT+X+Y")
    width, height, x, y = map(int, match.groups())
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("viewport dimensions must be positive")
    return width, height, x, y


def gray_to_binary(gray: int) -> int:
    value = 0
    while gray:
        value ^= gray
        gray >>= 1
    return value


def decode_band(gray: np.ndarray, width: int, x: int, y: int,
                band_height: int) -> int | None:
    height, frame_width = gray.shape
    if x < 0 or y < 0 or x + width > frame_width or y + band_height > height:
        return None

    values: list[float] = []
    margin_y = max(1, band_height // 4)
    for bar in range(20):
        left = x + bar * width // 20
        right = x + (bar + 1) * width // 20
        margin_x = max(1, (right - left) // 4)
        patch = gray[y + margin_y:y + band_height - margin_y,
                     left + margin_x:right - margin_x]
        if patch.size == 0:
            return None
        values.append(float(np.median(patch)))

    white = (values[0] + values[2]) / 2.0
    black = (values[1] + values[3]) / 2.0
    if white - black < 100.0:
        return None
    threshold = (white + black) / 2.0
    if not (values[0] > threshold and values[1] < threshold and
            values[2] > threshold and values[3] < threshold):
        return None

    encoded = 0
    for bit, value in enumerate(values[4:]):
        if value > threshold:
            encoded |= 1 << bit
    return gray_to_binary(encoded)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def longest_valid_run(frames: list[DecodedFrame]) -> list[DecodedFrame]:
    runs: list[list[DecodedFrame]] = []
    current: list[DecodedFrame] = []
    for frame in frames:
        if frame.top is not None and frame.bottom is not None:
            current.append(frame)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return max(runs, key=len, default=[])


def decode_video(path: Path, viewport: tuple[int, int, int, int]) -> tuple[
        list[DecodedFrame], float]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {path}")

    capture_fps = capture.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(capture_fps) or capture_fps <= 0:
        capture_fps = 60.0
    width, height, x, y = viewport
    band_height = max(1, height // 8)
    top_y = y
    bottom_y = y + height - band_height
    frames: list[DecodedFrame] = []

    while True:
        ok, image = capture.read()
        if not ok:
            break
        pts_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        frames.append(DecodedFrame(
            pts_ms=pts_ms,
            top=decode_band(gray, width, x, top_y, band_height),
            bottom=decode_band(gray, width, x, bottom_y, band_height),
        ))
    capture.release()
    return frames, capture_fps


def report(frames: list[DecodedFrame], capture_fps: float) -> None:
    valid = longest_valid_run(frames)
    if not valid:
        raise RuntimeError("no valid cadence-probe frame run found")

    nominal_ms = 1000.0 / capture_fps
    duration_ms = max(nominal_ms,
                      valid[-1].pts_ms - valid[0].pts_ms + nominal_ms)
    torn = sum(frame.top != frame.bottom for frame in valid)

    holds: list[tuple[int, float, int]] = []
    hold_id = valid[0].top
    hold_started_ms = valid[0].pts_ms
    hold_frames = 1
    transitions: list[tuple[int, int]] = []
    previous_id = hold_id
    for frame in valid[1:]:
        current_id = frame.top
        if current_id == hold_id:
            hold_frames += 1
            continue
        holds.append((hold_id, frame.pts_ms - hold_started_ms, hold_frames))
        if previous_id is not None and current_id is not None:
            transitions.append((previous_id, current_id))
        previous_id = current_id
        hold_id = current_id
        hold_started_ms = frame.pts_ms
        hold_frames = 1
    terminal_duration_ms = valid[-1].pts_ms - hold_started_ms + nominal_ms
    holds.append((hold_id, terminal_duration_ms, hold_frames))

    source_skips = 0
    discontinuities = 0
    for previous, current in transitions:
        delta = (current - previous) & 0xFFFF
        if 1 <= delta < 0x8000:
            source_skips += delta - 1
        else:
            discontinuities += 1

    completed_hold_ms = [duration for _, duration, _ in holds[:-1]]
    completed_hold_frames = Counter(frames for _, _, frames in holds[:-1])
    all_hold_ms = [duration for _, duration, _ in holds]
    updates = max(0, len(holds) - 1)
    update_hz = updates * 1000.0 / duration_ms
    active_duration_ms = sum(completed_hold_ms)
    active_update_hz = (updates * 1000.0 / active_duration_ms
                        if active_duration_ms > 0 else math.nan)
    capture_duplicates = len(valid) - len(holds)

    print(f"capture_frames={len(frames)} capture_fps={capture_fps:.3f}")
    print(f"valid_run_frames={len(valid)} valid_seconds={duration_ms / 1000:.3f} "
          f"first_id={valid[0].top} last_id={valid[-1].top}")
    print(f"display_updates={updates} whole_run_update_hz={update_hz:.3f} "
          f"active_update_hz={active_update_hz:.3f} "
          f"capture_duplicates={capture_duplicates} source_skips={source_skips} "
          f"discontinuities={discontinuities} torn_frames={torn}")
    if completed_hold_ms:
        print("completed_hold_ms "
              f"median={statistics.median(completed_hold_ms):.3f} "
              f"p95={percentile(completed_hold_ms, 0.95):.3f} "
              f"p99={percentile(completed_hold_ms, 0.99):.3f} "
              f"max={max(completed_hold_ms):.3f}")
        histogram = ",".join(
            f"{sample_count}:{occurrences}"
            for sample_count, occurrences in sorted(completed_hold_frames.items())
        )
        print(f"completed_hold_capture_frames={histogram}")
    print(f"terminal_hold id={holds[-1][0]} frames={holds[-1][2]} "
          f"duration_ms={holds[-1][1]:.3f} "
          f"all_holds_max_ms={max(all_hold_ms):.3f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--viewport", type=parse_viewport,
        help="probe viewport as WIDTHxHEIGHT+X+Y; defaults to the full video")
    args = parser.parse_args()

    try:
        capture = cv2.VideoCapture(str(args.video))
        if not capture.isOpened():
            raise RuntimeError(f"cannot open {args.video}")
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.release()
        viewport = args.viewport or (frame_width, frame_height, 0, 0)
        frames, capture_fps = decode_video(args.video, viewport)
        report(frames, capture_fps)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
