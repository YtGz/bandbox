"""Loudness normalization to -14 LUFS via ffmpeg's two-pass loudnorm filter."""

import json
import logging
import re
import subprocess


log = logging.getLogger("bandbox.normalize")


TARGET_I = -14.0    # integrated loudness, LUFS
TARGET_TP = -1.0    # true peak ceiling, dBTP
TARGET_LRA = 11.0   # loudness range, LU


def _loudnorm_filter(measured=None):
    """Build the ffmpeg `loudnorm` filter string, optionally with the
    first-pass measurements baked in for a deterministic second pass."""
    base = (
        f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
        ":print_format=json"
    )
    if measured is None:
        return base
    return (
        base
        + f":measured_I={measured['input_i']}"
        + f":measured_TP={measured['input_tp']}"
        + f":measured_LRA={measured['input_lra']}"
        + f":measured_thresh={measured['input_thresh']}"
        + f":offset={measured['target_offset']}"
        + ":linear=true"
    )


def _parse_loudnorm_json(stderr: str) -> dict:
    """ffmpeg prints the loudnorm JSON report at the tail of stderr."""
    match = re.search(r"\{[^{}]*\"input_i\".*?\}", stderr, re.DOTALL)
    if not match:
        raise RuntimeError("loudnorm did not emit a JSON report")
    return json.loads(match.group(0))


def normalize(input_path: str, output_path: str) -> None:
    """
    Read a WAV, run ffmpeg loudnorm (two-pass, with a true-peak
    limiter at -1 dBTP), write as FLAC.

    Why two passes?
    `loudnorm` in single-pass mode does dynamic normalization (a
    look-ahead compressor) which can pump on transient-heavy material
     — fine for podcasts, ugly on drums. With measurements from a
    first analysis pass and `linear=true`, the second pass just
    applies a constant gain and engages the limiter only on samples
    that would otherwise exceed -1 dBTP. Result: phone-friendly
    loudness without distorted snare hits.
    """
    # `loudnorm` upsamples to 192 kHz internally; if we don't pin
    # output sample rate, ffmpeg's encoder writes the file at 192 kHz
    # which inflates FLAC size 4× with no audible benefit. Detect the
    # source rate and force the second pass back down to it.
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path,
        ],
        capture_output=True, text=True, check=True,
    )
    src_rate = probe.stdout.strip() or "48000"

    # ── pass 1: measure ──────────────────────────────────────
    pass1 = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", input_path,
            "-af", _loudnorm_filter(),
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=True,
    )
    measured = _parse_loudnorm_json(pass1.stderr)
    log.debug(
        "loudnorm pass 1: I=%s LUFS, TP=%s dBTP, LRA=%s LU",
        measured.get("input_i"),
        measured.get("input_tp"),
        measured.get("input_lra"),
    )

    # If the file is effectively silent, loudnorm reports input_i as
    # "-inf" and the second pass refuses to run. Just transcode.
    if measured.get("input_i") in ("-inf", "-70.0") or \
       float(measured.get("input_i", -70.0)) <= -70.0:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", input_path, "-c:a", "flac", output_path],
            check=True,
        )
        return

    # ── pass 2: apply ────────────────────────────────────────
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            "-af", _loudnorm_filter(measured),
            "-ar", src_rate,
            "-c:a", "flac",
            output_path,
        ],
        check=True,
    )
