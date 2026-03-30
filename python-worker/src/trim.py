"""Song boundary detection for heavily distorted recordings.

Uses three detection methods and picks the best result:
1. Energy wall — sudden sustained energy jump (works for hard starts)
2. Rhythmic regularity — onset regularity detection (works for count-ins)
3. Pitched content — sustained pitched content detection (works for ambient intros)
"""

from dataclasses import dataclass

import librosa
import numpy as np


@dataclass
class TrimResult:
    """Result of song boundary detection."""

    start_sec: float
    end_sec: float
    confidence: float
    method: str


# Minimum song duration in seconds — ignore detections shorter than this
MIN_SONG_DURATION = 15.0

# Analysis parameters
HOP_LENGTH = 512
FRAME_LENGTH = 2048


def _energy_wall(y: np.ndarray, sr: int) -> TrimResult | None:
    """Detect a sudden sustained energy increase (hard song start)."""
    rms = librosa.feature.rms(y=y, frame_length=FRAME_LENGTH, hop_length=HOP_LENGTH)[0]
    if len(rms) < 10:
        return None

    # Smooth RMS
    kernel_size = max(3, sr // HOP_LENGTH // 10)  # ~100ms window
    if kernel_size > len(rms):
        return None
    smoothed = np.convolve(rms, np.ones(kernel_size) / kernel_size, mode="same")

    # Find the biggest jump in energy
    diff = np.diff(smoothed)
    if len(diff) == 0:
        return None

    # Threshold: jump must be > 3x median positive diff
    positive_diffs = diff[diff > 0]
    if len(positive_diffs) == 0:
        return None
    threshold = np.median(positive_diffs) * 3

    candidates = np.where(diff > threshold)[0]
    if len(candidates) == 0:
        return None

    # Pick the first significant jump in the first half
    midpoint = len(diff) // 2
    early_candidates = candidates[candidates < midpoint]
    if len(early_candidates) == 0:
        return None

    start_frame = early_candidates[0]
    start_sec = librosa.frames_to_time(start_frame, sr=sr, hop_length=HOP_LENGTH)

    # Find song end: last frame where energy is above 20% of peak
    peak_energy = np.max(smoothed)
    end_threshold = peak_energy * 0.2
    above = np.where(smoothed > end_threshold)[0]
    if len(above) == 0:
        return None
    end_frame = above[-1]
    end_sec = librosa.frames_to_time(end_frame, sr=sr, hop_length=HOP_LENGTH)

    if end_sec - start_sec < MIN_SONG_DURATION:
        return None

    # Confidence based on how dramatic the jump is
    jump_magnitude = diff[start_frame]
    confidence = min(1.0, float(jump_magnitude / (threshold * 2)) * 0.9 + 0.1)

    return TrimResult(
        start_sec=float(start_sec),
        end_sec=float(end_sec),
        confidence=confidence,
        method="energy_wall",
    )


def _rhythmic_regularity(y: np.ndarray, sr: int) -> TrimResult | None:
    """Detect where rhythmic (regular onset) patterns begin."""
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP_LENGTH, units="time"
    )

    if len(onsets) < 8:
        return None

    # Sliding window: check onset regularity (low std of inter-onset intervals)
    window_size = 8
    best_regularity = float("inf")
    best_start_idx = 0

    for i in range(len(onsets) - window_size):
        intervals = np.diff(onsets[i : i + window_size])
        if len(intervals) == 0:
            continue
        # Coefficient of variation: std / mean
        mean_interval = np.mean(intervals)
        if mean_interval < 0.1:  # Too fast, probably noise
            continue
        cv = float(np.std(intervals) / mean_interval)
        if cv < best_regularity:
            best_regularity = cv
            best_start_idx = i

    if best_regularity > 0.4:  # Not regular enough
        return None

    start_sec = float(onsets[best_start_idx])

    # End: find where regularity drops off from the end
    duration = float(len(y) / sr)
    # Scan backward
    end_sec = duration
    for i in range(len(onsets) - 1, window_size, -1):
        intervals = np.diff(onsets[max(0, i - window_size) : i])
        if len(intervals) == 0:
            continue
        mean_interval = np.mean(intervals)
        if mean_interval < 0.1:
            continue
        cv = float(np.std(intervals) / mean_interval)
        if cv < 0.5:
            end_sec = float(onsets[min(i, len(onsets) - 1)])
            break

    if end_sec - start_sec < MIN_SONG_DURATION:
        return None

    confidence = max(0.0, min(1.0, 1.0 - best_regularity))

    return TrimResult(
        start_sec=start_sec,
        end_sec=end_sec,
        confidence=confidence * 0.85,  # Slightly less confident than energy wall
        method="rhythmic_regularity",
    )


def _pitched_content(y: np.ndarray, sr: int) -> TrimResult | None:
    """Detect where sustained pitched/harmonic content begins."""
    # Spectral flatness: low = tonal, high = noisy
    flatness = librosa.feature.spectral_flatness(
        y=y, n_fft=FRAME_LENGTH, hop_length=HOP_LENGTH
    )[0]

    if len(flatness) < 10:
        return None

    # Smooth
    kernel_size = max(3, sr // HOP_LENGTH // 5)  # ~200ms
    if kernel_size > len(flatness):
        return None
    smoothed = np.convolve(flatness, np.ones(kernel_size) / kernel_size, mode="same")

    # Tonal frames: flatness below median * 0.7
    threshold = float(np.median(smoothed)) * 0.7
    tonal = smoothed < threshold

    # Find first sustained tonal region (at least 2 seconds)
    min_frames = int(2.0 * sr / HOP_LENGTH)
    start_frame = None
    count = 0

    for i, is_tonal in enumerate(tonal):
        if is_tonal:
            if count == 0:
                start_frame = i
            count += 1
            if count >= min_frames:
                break
        else:
            count = 0
            start_frame = None

    if start_frame is None or count < min_frames:
        return None

    start_sec = librosa.frames_to_time(start_frame, sr=sr, hop_length=HOP_LENGTH)

    # End: last sustained tonal region
    end_frame = start_frame
    count = 0
    for i in range(len(tonal) - 1, -1, -1):
        if tonal[i]:
            if count == 0:
                end_frame = i
            count += 1
            if count >= min_frames:
                break
        else:
            count = 0

    end_sec = librosa.frames_to_time(end_frame, sr=sr, hop_length=HOP_LENGTH)

    if end_sec - start_sec < MIN_SONG_DURATION:
        return None

    # Confidence based on how clearly tonal the detected region is
    tonal_ratio = float(np.sum(tonal[start_frame:end_frame])) / max(
        1, end_frame - start_frame
    )
    confidence = tonal_ratio * 0.8  # Cap at 0.8 — least reliable method

    return TrimResult(
        start_sec=float(start_sec),
        end_sec=float(end_sec),
        confidence=confidence,
        method="pitched_content",
    )


def detect_boundaries(audio_path: str) -> TrimResult:
    """Run all detection methods and return the best result.

    Falls back to the full recording if no method succeeds.
    """
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration = float(len(y) / sr)

    results: list[TrimResult] = []

    for detector in [_energy_wall, _rhythmic_regularity, _pitched_content]:
        try:
            result = detector(y, sr)
            if result is not None:
                results.append(result)
        except Exception:
            continue

    if not results:
        # No detection succeeded — return full recording with zero confidence
        return TrimResult(
            start_sec=0.0,
            end_sec=duration,
            confidence=0.0,
            method="none",
        )

    # Pick the result with the highest confidence
    return max(results, key=lambda r: r.confidence)
