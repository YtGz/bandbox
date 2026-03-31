"""Riff segmentation and feature extraction for death/black metal.

Segments a recording into riffs using spectral novelty detection,
then extracts per-riff fingerprints optimized for heavily distorted music:

- HPSS preprocessing to separate guitar from drums
- Beat-aligned 16-slot onset patterns (groove + drum fingerprints)
- Spectral centroid contour with pYIN fallback for tremolo riff tracking
- Zero-mean unit-variance normalization (tuning-independent)
- Fixed-length resampling (tempo-independent, enables DTW)
- Per-riff onset uniformity score (for adaptive weighting in match.py)
"""

import numpy as np
import librosa
from scipy.ndimage import median_filter, uniform_filter1d

# ════════════════════════════════════════════════════════════
#  CONSTANTS
# ════════════════════════════════════════════════════════════

SR = 22050
HOP_LENGTH = 512
N_FFT = 2048

# Riff segmentation
MIN_RIFF_DURATION = 3.0   # seconds — merge segments shorter than this
MAX_RIFF_DURATION = 60.0  # seconds — split segments longer than this

# Contour extraction
CONTOUR_FIXED_LENGTH = 200      # resample all contours to this length
CONTOUR_MEDIAN_SIZE = 15        # median filter window (kill outliers)
CONTOUR_SMOOTH_SIZE = 25        # uniform filter window (smooth tremolo ripple)
PYIN_CONFIDENCE_THRESHOLD = 0.5 # use pYIN when confidence exceeds this

# Beat-aligned patterns
SLOTS_PER_BEAT = 16             # subdivisions per beat for groove patterns
MIN_BEATS_FOR_GROOVE = 4        # need at least this many beats

# Beat tracking — use librosa as default, madmom when available
try:
    import madmom
    import madmom.features.beats
    HAS_MADMOM = True
except ImportError:
    HAS_MADMOM = False


# ════════════════════════════════════════════════════════════
#  HPSS — HARMONIC-PERCUSSIVE SOURCE SEPARATION
# ════════════════════════════════════════════════════════════


def _hpss(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split audio into harmonic (guitar sustain) and percussive (drums/attacks).

    Uses a wide margin for cleaner separation at the cost of some bleed.
    This is THE critical preprocessing step — everything downstream benefits.
    """
    return librosa.effects.hpss(y, margin=3.0)


# ════════════════════════════════════════════════════════════
#  BEAT TRACKING
# ════════════════════════════════════════════════════════════


def _detect_beats(y: np.ndarray, sr: int) -> np.ndarray:
    """Detect beat positions in seconds.

    Uses madmom's neural beat tracker when available (robust up to 260 BPM),
    falls back to librosa's beat tracker.
    """
    if HAS_MADMOM:
        try:
            proc = madmom.features.beats.DBNBeatTrackingProcessor(
                min_bpm=40, max_bpm=260, fps=100,
            )
            act = madmom.features.beats.RNNBeatProcessor()(y)
            beats = proc(act)
            if len(beats) >= MIN_BEATS_FOR_GROOVE:
                return beats
        except Exception:
            pass  # fall through to librosa

    # Librosa fallback
    tempo, beat_frames = librosa.beat.beat_track(
        y=y, sr=sr, hop_length=HOP_LENGTH,
    )
    return librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP_LENGTH)


# ════════════════════════════════════════════════════════════
#  RIFF SEGMENTATION
# ════════════════════════════════════════════════════════════


def segment_riffs(
    audio_path: str,
    start_sec: float,
    end_sec: float,
) -> list[dict]:
    """Segment the song portion into riffs using spectral novelty detection.

    Uses agglomerative clustering on chroma features to find structural
    boundaries. Short segments are merged; long segments are split.

    Returns list of dicts with startSec/endSec relative to the trimmed region.
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    start_sample = int(start_sec * sr)
    end_sample = int(end_sec * sr)
    y_song = y[start_sample:end_sample]

    duration = float(len(y_song) / sr)

    if duration < MIN_RIFF_DURATION:
        return [{"startSec": 0.0, "endSec": duration}]

    # Chroma-based segmentation (works well even for distorted music
    # because power chord root movement is visible in chroma)
    chroma = librosa.feature.chroma_cqt(y=y_song, sr=sr, hop_length=HOP_LENGTH)
    bounds = librosa.segment.agglomerative(chroma, k=None)

    bound_times = librosa.frames_to_time(bounds, sr=sr, hop_length=HOP_LENGTH)
    bound_times = np.concatenate([[0.0], bound_times, [duration]])
    bound_times = np.unique(np.sort(bound_times))

    # Build riff segments, merging short ones and splitting long ones
    riffs: list[dict] = []
    current_start = bound_times[0]

    for i in range(1, len(bound_times)):
        seg_duration = bound_times[i] - current_start

        if seg_duration < MIN_RIFF_DURATION and i < len(bound_times) - 1:
            continue

        if seg_duration > MAX_RIFF_DURATION:
            n_parts = int(np.ceil(seg_duration / MAX_RIFF_DURATION))
            part_duration = seg_duration / n_parts
            for j in range(n_parts):
                riffs.append({
                    "startSec": round(current_start + j * part_duration, 3),
                    "endSec": round(current_start + (j + 1) * part_duration, 3),
                })
        else:
            riffs.append({
                "startSec": round(float(current_start), 3),
                "endSec": round(float(bound_times[i]), 3),
            })

        current_start = bound_times[i]

    if not riffs:
        riffs = [{"startSec": 0.0, "endSec": duration}]

    return riffs


# ════════════════════════════════════════════════════════════
#  CONTOUR EXTRACTION — MELODIC SHAPE
# ════════════════════════════════════════════════════════════


def _extract_contour_centroid(
    y_harm: np.ndarray, sr: int,
) -> np.ndarray:
    """Extract pitch contour via spectral centroid on harmonic layer.

    The centroid tracks the "center of mass" of the spectrum. When the
    guitar moves to a higher note, the centroid rises — even through
    heavy distortion. Heavy smoothing removes tremolo ripple.
    """
    centroid = librosa.feature.spectral_centroid(
        y=y_harm, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH,
    )[0]

    if len(centroid) == 0:
        return np.array([])

    # Median filter kills outlier spikes, uniform filter smooths the wave
    smoothed = median_filter(centroid, size=CONTOUR_MEDIAN_SIZE)
    smoothed = uniform_filter1d(smoothed, size=CONTOUR_SMOOTH_SIZE)

    return smoothed


def _extract_contour_pyin(
    y_harm: np.ndarray, sr: int,
) -> tuple[np.ndarray | None, float]:
    """Attempt pitch tracking via pYIN on harmonic layer.

    Returns (contour_or_None, confidence). pYIN works well on cleaner
    passages and isolated instruments, fails on heavily distorted full-band.
    """
    try:
        f0, _, voiced_prob = librosa.pyin(
            y_harm, sr=sr,
            fmin=librosa.note_to_hz("A0"),   # drop A territory
            fmax=librosa.note_to_hz("E4"),
            hop_length=HOP_LENGTH,
        )
    except Exception:
        return None, 0.0

    confidence = float(np.nanmean(voiced_prob))

    if confidence < PYIN_CONFIDENCE_THRESHOLD:
        return None, confidence

    f0_midi = librosa.hz_to_midi(f0)
    valid = ~np.isnan(f0_midi)

    if np.sum(valid) < len(f0_midi) * 0.3:
        return None, confidence

    # Interpolate through unvoiced gaps
    x = np.arange(len(f0_midi))
    f0_interp = np.interp(x, x[valid], f0_midi[valid])
    smoothed = uniform_filter1d(f0_interp, size=15)

    return smoothed, confidence


def extract_contour(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
) -> dict:
    """Extract melodic contour for a riff.

    Uses pYIN when it's confident, falls back to spectral centroid.
    Returns a dict with:
    - contour: fixed-length normalized curve (for DTW)
    - intervals: quantized up/down/flat sequence
    - method: "pyin" or "centroid"
    - pyinConfidence: how confident pYIN was (useful for debugging)
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    abs_start = song_start_sec + riff_start_sec
    abs_end = song_start_sec + riff_end_sec
    y_riff = y[int(abs_start * sr):int(abs_end * sr)]

    if len(y_riff) < sr:
        return {
            "contour": [],
            "intervals": [],
            "method": "none",
            "pyinConfidence": 0.0,
        }

    # HPSS — analyze harmonic layer only
    y_harm, _ = _hpss(y_riff)

    # Try pYIN first (more precise when it works)
    pyin_contour, pyin_conf = _extract_contour_pyin(y_harm, sr)

    if pyin_contour is not None:
        pitch_curve = pyin_contour
        method = "pyin"
    else:
        pitch_curve = _extract_contour_centroid(y_harm, sr)
        method = "centroid"

    if len(pitch_curve) == 0:
        return {
            "contour": [],
            "intervals": [],
            "method": "none",
            "pyinConfidence": round(pyin_conf, 3),
        }

    # ── Resample to fixed length (tempo-independent) ──
    resampled = np.interp(
        np.linspace(0, len(pitch_curve), CONTOUR_FIXED_LENGTH),
        np.arange(len(pitch_curve)),
        pitch_curve,
    )

    # ── Normalize: zero-mean, unit-variance (tuning-independent) ──
    std = np.std(resampled)
    if std > 1e-8:
        normalized = (resampled - np.mean(resampled)) / std
    else:
        normalized = np.zeros_like(resampled)

    # ── Quantize to interval sequence (up/down/flat) ──
    delta = np.diff(normalized)
    threshold = np.std(delta) * 0.25
    intervals = np.zeros_like(delta)
    intervals[delta > threshold] = 1
    intervals[delta < -threshold] = -1

    return {
        "contour": [round(float(x), 4) for x in normalized],
        "intervals": [int(x) for x in intervals],
        "method": method,
        "pyinConfidence": round(pyin_conf, 3),
    }


# ════════════════════════════════════════════════════════════
#  RHYTHM FINGERPRINTING — BEAT-ALIGNED PATTERNS
# ════════════════════════════════════════════════════════════


def _onset_to_beat_pattern(
    onset_env: np.ndarray,
    onset_times: np.ndarray,
    beats: np.ndarray,
) -> tuple[list[float], float]:
    """Align onset strengths to a beat grid and build a per-beat pattern.

    Returns (groove_pattern, onset_uniformity).
    groove_pattern: averaged 16-slot pattern across all beats.
    onset_uniformity: 0=very sparse/syncopated, 1=uniform (blast beat).
    """
    beat_patterns = []

    for i in range(len(beats) - 1):
        mask = (onset_times >= beats[i]) & (onset_times < beats[i + 1])
        segment = onset_env[mask]

        if len(segment) == 0:
            continue

        # Resample to SLOTS_PER_BEAT fixed slots
        resampled = np.interp(
            np.linspace(0, len(segment), SLOTS_PER_BEAT),
            np.arange(len(segment)),
            segment,
        )

        # Normalize per beat (relative pattern, not absolute volume)
        peak = np.max(resampled)
        if peak > 1e-8:
            resampled /= peak
        beat_patterns.append(resampled)

    if not beat_patterns:
        return [0.0] * SLOTS_PER_BEAT, 0.5

    groove = np.mean(beat_patterns, axis=0)

    # Onset uniformity: how evenly distributed are the onsets?
    # High = blast beat / constant 16ths. Low = syncopated groove.
    mean_g = np.mean(groove)
    if mean_g > 1e-8:
        uniformity = 1.0 - float(np.std(groove) / mean_g)
    else:
        uniformity = 0.5

    uniformity = max(0.0, min(1.0, uniformity))

    return [round(float(x), 4) for x in groove], round(uniformity, 4)


def extract_fingerprint(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
) -> dict:
    """Extract a complete fingerprint for a single riff.

    Returns a dict with:
    - groove: 16-slot beat-aligned onset pattern (full signal)
    - drums: 16-slot beat-aligned onset pattern (percussive layer only)
    - spectral: spectral contrast per frequency band
    - tempo: estimated BPM
    - onsetUniformity: 0-1 score (high = blast, low = groove)
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    abs_start = song_start_sec + riff_start_sec
    abs_end = song_start_sec + riff_end_sec
    y_riff = y[int(abs_start * sr):int(abs_end * sr)]

    if len(y_riff) < sr:
        return {
            "groove": [],
            "drums": [],
            "spectral": [],
            "tempo": 0,
            "onsetUniformity": 0.5,
        }

    # ── HPSS ──
    y_harm, y_perc = _hpss(y_riff)

    # ── Beat tracking ──
    beats = _detect_beats(y_riff, sr)

    if len(beats) < MIN_BEATS_FOR_GROOVE:
        # Not enough beats — fall back to basic features
        return _fallback_fingerprint(y_riff, y_perc, sr)

    # ── Full-signal onset pattern → groove ──
    onset_env = librosa.onset.onset_strength(
        y=y_riff, sr=sr, hop_length=HOP_LENGTH,
    )
    onset_times = librosa.frames_to_time(
        range(len(onset_env)), sr=sr, hop_length=HOP_LENGTH,
    )

    groove, uniformity = _onset_to_beat_pattern(onset_env, onset_times, beats)

    # ── Percussive onset pattern → drums ──
    onset_perc = librosa.onset.onset_strength(
        y=y_perc, sr=sr, hop_length=HOP_LENGTH,
    )
    onset_perc_times = librosa.frames_to_time(
        range(len(onset_perc)), sr=sr, hop_length=HOP_LENGTH,
    )

    drums, _ = _onset_to_beat_pattern(onset_perc, onset_perc_times, beats)

    # ── Spectral contrast (from full signal) ──
    contrast = librosa.feature.spectral_contrast(
        y=y_riff, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH,
    )
    spectral = [round(float(x), 4) for x in np.mean(contrast, axis=1)]

    # ── Tempo from beat positions ──
    if len(beats) >= 2:
        intervals = np.diff(beats)
        tempo = round(60.0 / float(np.median(intervals)), 1)
    else:
        tempo = 0.0

    return {
        "groove": groove,
        "drums": drums,
        "spectral": spectral,
        "tempo": tempo,
        "onsetUniformity": uniformity,
    }


def _fallback_fingerprint(
    y_riff: np.ndarray, y_perc: np.ndarray, sr: int,
) -> dict:
    """Fallback when beat tracking fails (very short or chaotic riffs).

    Uses IOI histogram (old approach) instead of beat-aligned patterns.
    """
    onset_env = librosa.onset.onset_strength(
        y=y_riff, sr=sr, hop_length=HOP_LENGTH,
    )
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP_LENGTH, units="time",
    )

    groove: list[float] = []
    if len(onsets) > 2:
        ioi = np.diff(onsets)
        bins = np.arange(0, 2.0, 0.05)
        hist, _ = np.histogram(ioi, bins=bins, density=True)
        groove = [round(float(x), 4) for x in hist]

    # Drum pattern via autocorrelation of percussive onsets
    onset_perc = librosa.onset.onset_strength(
        y=y_perc, sr=sr, hop_length=HOP_LENGTH,
    )
    drums: list[float] = []
    if len(onset_perc) > 4:
        autocorr = np.correlate(onset_perc, onset_perc, mode="full")
        autocorr = autocorr[len(autocorr) // 2:]
        if autocorr[0] > 0:
            autocorr = autocorr / autocorr[0]
        drums = [round(float(x), 4) for x in autocorr[:40]]

    # Spectral contrast
    contrast = librosa.feature.spectral_contrast(
        y=y_riff, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH,
    )
    spectral = [round(float(x), 4) for x in np.mean(contrast, axis=1)]

    # Tempo
    tempo_est = librosa.beat.tempo(y=y_riff, sr=sr, hop_length=HOP_LENGTH)
    tempo = round(float(tempo_est[0]), 1) if len(tempo_est) > 0 else 0.0

    return {
        "groove": groove,
        "drums": drums,
        "spectral": spectral,
        "tempo": tempo,
        "onsetUniformity": 0.5,  # unknown — use balanced weights
    }
