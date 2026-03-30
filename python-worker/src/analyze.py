"""Riff segmentation and feature extraction.

Segments a recording into riffs using spectral novelty detection,
then extracts per-riff fingerprints for matching.
"""

import numpy as np
import librosa


# Analysis parameters
SR = 22050  # Resample to this for consistent analysis
HOP_LENGTH = 512
N_FFT = 2048
N_MELS = 128

# Riff segmentation
MIN_RIFF_DURATION = 3.0  # seconds — ignore segments shorter than this
MAX_RIFF_DURATION = 60.0  # seconds — split segments longer than this


def segment_riffs(
    audio_path: str,
    start_sec: float,
    end_sec: float,
) -> list[dict]:
    """Segment the song portion of a recording into riffs.

    Uses spectral novelty detection to find structural boundaries.
    Returns a list of riff dicts with startSec, endSec relative to the
    trimmed recording.
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    # Extract the trimmed portion
    start_sample = int(start_sec * sr)
    end_sample = int(end_sec * sr)
    y_song = y[start_sample:end_sample]

    if len(y_song) < sr * MIN_RIFF_DURATION:
        # Too short to segment — treat as one riff
        return [{"startSec": 0.0, "endSec": float(len(y_song) / sr)}]

    # Compute mel spectrogram for novelty detection
    S = librosa.feature.melspectrogram(
        y=y_song, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH, n_mels=N_MELS
    )
    S_db = librosa.power_to_db(S, ref=np.max)

    # Spectral novelty via recurrence matrix
    # Use checkerboard kernel for structural boundary detection
    chroma = librosa.feature.chroma_cqt(y=y_song, sr=sr, hop_length=HOP_LENGTH)
    bounds = librosa.segment.agglomerative(chroma, k=None)

    # Convert to time
    bound_times = librosa.frames_to_time(bounds, sr=sr, hop_length=HOP_LENGTH)

    # Add start and end
    duration = float(len(y_song) / sr)
    bound_times = np.concatenate([[0.0], bound_times, [duration]])
    bound_times = np.unique(np.sort(bound_times))

    # Build riff segments, merging short ones
    riffs: list[dict] = []
    current_start = bound_times[0]

    for i in range(1, len(bound_times)):
        seg_duration = bound_times[i] - current_start

        if seg_duration < MIN_RIFF_DURATION and i < len(bound_times) - 1:
            # Too short — merge with next segment
            continue

        if seg_duration > MAX_RIFF_DURATION:
            # Too long — split into equal parts
            n_parts = int(np.ceil(seg_duration / MAX_RIFF_DURATION))
            part_duration = seg_duration / n_parts
            for j in range(n_parts):
                riffs.append(
                    {
                        "startSec": round(current_start + j * part_duration, 3),
                        "endSec": round(
                            current_start + (j + 1) * part_duration, 3
                        ),
                    }
                )
        else:
            riffs.append(
                {
                    "startSec": round(float(current_start), 3),
                    "endSec": round(float(bound_times[i]), 3),
                }
            )

        current_start = bound_times[i]

    # Ensure at least one riff
    if not riffs:
        riffs = [{"startSec": 0.0, "endSec": duration}]

    return riffs


def extract_fingerprint(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
) -> dict:
    """Extract a fingerprint for a single riff.

    Returns a dict with:
    - groove: inter-onset interval histogram (rhythmic pattern)
    - drums: low-frequency onset pattern (kick/snare pattern)
    - spectral: spectral contrast summary
    - tempo: estimated BPM for this riff
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    # Extract the riff audio (absolute position in file)
    abs_start = song_start_sec + riff_start_sec
    abs_end = song_start_sec + riff_end_sec
    start_sample = int(abs_start * sr)
    end_sample = int(abs_end * sr)
    y_riff = y[start_sample:end_sample]

    if len(y_riff) < sr:
        # Less than 1 second — can't extract meaningful features
        return {
            "groove": [],
            "drums": [],
            "spectral": [],
            "tempo": 0,
        }

    # --- Groove pattern: inter-onset interval histogram ---
    onset_env = librosa.onset.onset_strength(y=y_riff, sr=sr, hop_length=HOP_LENGTH)
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP_LENGTH, units="time"
    )

    groove: list[float] = []
    if len(onsets) > 2:
        ioi = np.diff(onsets)
        # Quantize to 50ms bins, histogram up to 2 seconds
        bins = np.arange(0, 2.0, 0.05)
        hist, _ = np.histogram(ioi, bins=bins, density=True)
        groove = [round(float(x), 4) for x in hist]

    # --- Drum pattern: low-frequency onset pattern ---
    # Filter to low frequencies (kick/bass: < 200Hz)
    y_low = librosa.effects.preemphasis(y_riff, coef=-0.97)  # Boost lows
    S_low = librosa.feature.melspectrogram(
        y=y_low, sr=sr, n_mels=16, fmax=200, hop_length=HOP_LENGTH
    )
    onset_low = librosa.onset.onset_strength(S=librosa.power_to_db(S_low), sr=sr)
    drums: list[float] = []
    if len(onset_low) > 4:
        # Autocorrelation of low-freq onsets = drum pattern periodicity
        autocorr = np.correlate(onset_low, onset_low, mode="full")
        autocorr = autocorr[len(autocorr) // 2 :]
        # Normalize
        if autocorr[0] > 0:
            autocorr = autocorr / autocorr[0]
        # Take first 40 lags (~1 second)
        drums = [round(float(x), 4) for x in autocorr[:40]]

    # --- Spectral contrast ---
    contrast = librosa.feature.spectral_contrast(
        y=y_riff, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH
    )
    # Mean across time for each frequency band
    spectral = [round(float(x), 4) for x in np.mean(contrast, axis=1)]

    # --- Tempo ---
    tempo_estimate = librosa.beat.tempo(y=y_riff, sr=sr, hop_length=HOP_LENGTH)
    tempo = round(float(tempo_estimate[0]), 1) if len(tempo_estimate) > 0 else 0.0

    return {
        "groove": groove,
        "drums": drums,
        "spectral": spectral,
        "tempo": tempo,
    }


def extract_contour(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
) -> list[float]:
    """Extract pitch contour for a riff using spectral centroid.

    For heavily distorted audio, spectral centroid tracks the perceived
    pitch better than pYIN (which assumes clean pitch). The contour is
    downsampled to ~10 points per second for efficient DTW matching.
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    abs_start = song_start_sec + riff_start_sec
    abs_end = song_start_sec + riff_end_sec
    start_sample = int(abs_start * sr)
    end_sample = int(abs_end * sr)
    y_riff = y[start_sample:end_sample]

    if len(y_riff) < sr:
        return []

    # Spectral centroid as pitch proxy
    centroid = librosa.feature.spectral_centroid(
        y=y_riff, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH
    )[0]

    if len(centroid) == 0:
        return []

    # Normalize to 0-1 range
    c_min, c_max = np.min(centroid), np.max(centroid)
    if c_max - c_min > 0:
        centroid_norm = (centroid - c_min) / (c_max - c_min)
    else:
        centroid_norm = np.zeros_like(centroid)

    # Downsample to ~10 points per second
    frames_per_sec = sr / HOP_LENGTH
    downsample_factor = max(1, int(frames_per_sec / 10))
    downsampled = centroid_norm[::downsample_factor]

    return [round(float(x), 4) for x in downsampled]
