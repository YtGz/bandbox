"""Riff segmentation and feature extraction for death/black metal.

Segments a recording into riffs using self-similarity novelty detection,
then extracts per-riff fingerprints optimized for heavily distorted music:

- HPSS preprocessing to separate guitar from drums
- Beat-aligned 16-slot onset patterns (groove + drum fingerprints)
- Three-method contour cascade: spectral centroid → spectral rolloff → pYIN
- Zero-mean unit-variance normalization (tuning-independent)
- Fixed-length resampling (tempo-independent, enables DTW)
- Per-riff onset uniformity score (for adaptive weighting in match.py)
"""

import numpy as np
import librosa
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import find_peaks

# ════════════════════════════════════════════════════════════
#  CONSTANTS
# ════════════════════════════════════════════════════════════

SR = 22050
HOP_LENGTH = 512
N_FFT = 2048

# Riff segmentation
MIN_RIFF_DURATION = 3.0   # seconds — merge segments shorter than this
MAX_RIFF_DURATION = 60.0  # seconds — split segments longer than this
NOVELTY_THRESHOLD = 0.3   # minimum novelty peak height (0-1)
MIN_PEAK_DISTANCE_SEC = 4.0  # minimum seconds between riff boundaries

# Contour extraction
CONTOUR_FIXED_LENGTH = 200      # resample all contours to this length
CONTOUR_MEDIAN_SIZE = 15        # median filter window (kill outliers)
CONTOUR_SMOOTH_SIZE = 25        # uniform filter window (smooth tremolo ripple)
PYIN_CONFIDENCE_THRESHOLD = 0.5 # use pYIN when confidence exceeds this

# Beat-aligned patterns
SLOTS_PER_BEAT = 16             # subdivisions per beat for groove patterns
MIN_BEATS_FOR_GROOVE = 4        # need at least this many beats

# Beat tracking — madmom-modern is the primary beat tracker (neural RNN + DBN).
# If it fails to import (broken install, missing C extensions), we fall back
# to librosa's simpler beat tracker and warn the user via a system warning.
try:
    import madmom
    import madmom.features.beats
    HAS_MADMOM = True
except ImportError:
    import logging as _logging
    _logging.getLogger("bandbox-worker").warning(
        "⚠️  madmom-modern not available — falling back to librosa beat tracking. "
        "Beat detection accuracy will be significantly reduced, especially for "
        "fast tempos. Install madmom-modern to fix: pip install madmom-modern"
    )
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


def _detect_beats(
    y: np.ndarray, sr: int, *, audio_path: str | None = None,
) -> np.ndarray:
    """Detect beat positions in seconds, relative to the provided audio signal.

    Primary: madmom's neural beat tracker (RNN + DBN postprocessing).
    Robust up to 215 BPM. Writes the riff segment to a temporary WAV
    file because madmom's RNNBeatProcessor requires a file path.

    Fallback: librosa's beat tracker. Less accurate, especially at
    high tempos, but accepts numpy arrays directly and has no
    external dependencies.

    Args:
        y: Audio signal as numpy array (the riff segment).
        sr: Sample rate.
        audio_path: Original file path (reserved for future whole-file
            beat tracking cache).
    """
    if HAS_MADMOM:
        try:
            import tempfile
            import soundfile as sf

            # Write riff segment to temp file for madmom
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
                sf.write(tmp_path, y, sr)

            try:
                proc = madmom.features.beats.DBNBeatTrackingProcessor(
                    min_bpm=40, max_bpm=260, fps=100,
                )
                act = madmom.features.beats.RNNBeatProcessor()(tmp_path)
                beats = proc(act)
            finally:
                import os
                os.unlink(tmp_path)

            if len(beats) >= MIN_BEATS_FOR_GROOVE:
                return beats
        except Exception as e:
            import logging
            logging.getLogger("bandbox-worker").warning(
                "madmom beat tracking failed, falling back to librosa: %s", e,
            )

    # Librosa fallback — less accurate, especially at high tempos
    if not HAS_MADMOM and not _detect_beats._warned_no_madmom:
        _detect_beats._warned_no_madmom = True
        import logging
        logging.getLogger("bandbox-worker").warning(
            "Processing with librosa beat tracking (madmom-modern not installed). "
            "Results may be less accurate. Reprocess recordings after installing "
            "madmom-modern for better quality."
        )

    tempo, beat_frames = librosa.beat.beat_track(
        y=y, sr=sr, hop_length=HOP_LENGTH,
    )
    return librosa.frames_to_time(beat_frames, sr=sr, hop_length=HOP_LENGTH)

_detect_beats._warned_no_madmom = False  # type: ignore[attr-defined]


# ════════════════════════════════════════════════════════════
#  RIFF SEGMENTATION — SSM + CHECKERBOARD NOVELTY
# ════════════════════════════════════════════════════════════


def _checkerboard_kernel(size: int) -> np.ndarray:
    """Build a checkerboard kernel for novelty detection.

    The kernel has +1 in the top-left and bottom-right quadrants,
    and -1 in the off-diagonal quadrants. When convolved along the
    diagonal of a self-similarity matrix, it fires at structural
    boundaries — where the music changes character.
    """
    kernel = np.ones((size, size))
    half = size // 2
    kernel[:half, half:] = -1
    kernel[half:, :half] = -1
    return kernel


def _compute_novelty_curve(
    y: np.ndarray, sr: int, hop: int,
) -> np.ndarray:
    """Compute a novelty curve from a self-similarity matrix.

    Uses spectral contrast + onset strength as features (more robust
    than chroma for heavily distorted music). The SSM reveals repeating
    sections as bright blocks; the checkerboard kernel detects transitions
    between blocks.
    """
    # Feature extraction — spectral contrast captures tonal character,
    # onset strength captures rhythmic character
    contrast = librosa.feature.spectral_contrast(
        y=y, sr=sr, hop_length=hop, n_bands=6,
    )
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onset_2d = onset_env.reshape(1, -1)

    features = np.vstack([contrast, onset_2d])

    # L2-normalize feature vectors
    norms = np.linalg.norm(features, axis=0, keepdims=True)
    norms[norms < 1e-10] = 1.0
    features = features / norms

    # Self-similarity matrix (cosine similarity)
    ssm = features.T @ features  # (n_frames, n_frames)

    # Checkerboard kernel — size scales with tempo (~1.5 seconds)
    kernel_size = max(8, min(128, int(1.5 * sr / hop)))
    # Ensure even size
    kernel_size = kernel_size + (kernel_size % 2)
    kernel = _checkerboard_kernel(kernel_size)

    # Convolve kernel along the diagonal
    half_k = kernel_size // 2
    n_frames = ssm.shape[0]
    novelty = np.zeros(n_frames)

    for i in range(half_k, n_frames - half_k):
        patch = ssm[i - half_k:i + half_k, i - half_k:i + half_k]
        if patch.shape == kernel.shape:
            novelty[i] = np.sum(patch * kernel)

    # Rectify and normalize
    novelty = np.maximum(novelty, 0)
    peak = novelty.max()
    if peak > 0:
        novelty /= peak

    return novelty


def segment_riffs(
    audio_path: str,
    start_sec: float,
    end_sec: float,
) -> list[dict]:
    """Segment the song portion into riffs using SSM novelty detection.

    Builds a self-similarity matrix from spectral contrast + onset features,
    runs a checkerboard kernel along the diagonal to detect structural
    boundaries, and splits at novelty peaks.

    Short segments (<3s) are merged; long segments (>60s) are split.

    Returns list of dicts with startSec/endSec relative to the trimmed region.
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    start_sample = int(start_sec * sr)
    end_sample = int(end_sec * sr)
    y_song = y[start_sample:end_sample]

    duration = float(len(y_song) / sr)

    if duration < MIN_RIFF_DURATION:
        return [{"startSec": 0.0, "endSec": duration}]

    # Compute novelty curve
    novelty = _compute_novelty_curve(y_song, sr, HOP_LENGTH)

    # Find peaks in novelty = riff boundaries
    min_distance = int(MIN_PEAK_DISTANCE_SEC * sr / HOP_LENGTH)
    peaks, _ = find_peaks(
        novelty,
        height=NOVELTY_THRESHOLD,
        distance=min_distance,
    )

    # Convert to time
    bound_times = librosa.frames_to_time(peaks, sr=sr, hop_length=HOP_LENGTH)
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

    This is the most robust method and the primary default.
    """
    centroid = librosa.feature.spectral_centroid(
        y=y_harm, sr=sr, n_fft=N_FFT, hop_length=HOP_LENGTH,
    )[0]

    if len(centroid) == 0:
        return np.array([])

    smoothed = median_filter(centroid, size=CONTOUR_MEDIAN_SIZE)
    smoothed = uniform_filter1d(smoothed, size=CONTOUR_SMOOTH_SIZE)

    return smoothed


def _extract_contour_rolloff(
    y_harm: np.ndarray, sr: int,
) -> np.ndarray:
    """Extract pitch contour via spectral rolloff on harmonic layer.

    The rolloff is the frequency below which 50% of spectral energy sits.
    Less precise than centroid but more resistant to certain distortion
    artifacts — when distortion generates strong high-frequency content
    that pulls the centroid up, the rolloff (at 50%) stays anchored to
    the fundamental region.

    Used as a backup when centroid quality is poor.
    """
    rolloff = librosa.feature.spectral_rolloff(
        y=y_harm, sr=sr, hop_length=HOP_LENGTH, roll_percent=0.5,
    )[0]

    if len(rolloff) == 0:
        return np.array([])

    smoothed = median_filter(rolloff, size=CONTOUR_MEDIAN_SIZE)
    smoothed = uniform_filter1d(rolloff, size=CONTOUR_SMOOTH_SIZE)

    return smoothed


def _extract_contour_pyin(
    y_harm: np.ndarray, sr: int,
) -> tuple[np.ndarray | None, float]:
    """Attempt pitch tracking via pYIN on harmonic layer.

    Returns (contour_or_None, confidence). pYIN extracts actual fundamental
    frequencies — the most precise method when it works. Fails on heavily
    distorted full-band recordings but works well on cleaner passages,
    isolated instruments, or bass-heavy sections.
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


def _contour_quality(contour: np.ndarray) -> float:
    """Score a contour's quality — higher means more melodic movement.

    A flat contour (single chugged note) has low quality.
    A contour with clear directional movement (tremolo melody) has high quality.
    Used to pick the best contour method.
    """
    if len(contour) < 10:
        return 0.0

    # Normalize to compare fairly
    std = np.std(contour)
    if std < 1e-8:
        return 0.0

    normalized = (contour - np.mean(contour)) / std

    # Quality = how much directional movement exists
    # High-quality: smooth waves (tremolo melody)
    # Low-quality: flat line (single note chug) or random noise
    delta = np.diff(normalized)
    # Autocorrelation of deltas — smooth waves have high autocorrelation
    if len(delta) < 20:
        return float(np.std(normalized))

    autocorr = np.correlate(delta, delta, mode="full")
    autocorr = autocorr[len(autocorr) // 2:]
    if autocorr[0] > 0:
        autocorr = autocorr / autocorr[0]

    # Sum of first few lags — high for smooth contours, low for noise
    quality = float(np.mean(autocorr[1:min(20, len(autocorr))]))
    return max(0.0, quality)


def extract_contour(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
    *,
    _preloaded: tuple[np.ndarray, np.ndarray] | None = None,
) -> dict:
    """Extract melodic contour for a riff.

    Three-method cascade on the harmonic layer:
    1. Spectral centroid (most robust, primary default)
    2. Spectral rolloff (backup, resistant to certain distortion artifacts)
    3. pYIN (most precise when it works — used if confidence is high)

    If pYIN is confident, it wins regardless. Otherwise, the better of
    centroid and rolloff is selected by contour quality score.

    Args:
        _preloaded: Optional (y_harm, y_perc) tuple from extract_fingerprint
            to avoid redundant audio loading and HPSS computation.

    Returns a dict with:
    - contour: fixed-length normalized curve (for DTW)
    - intervals: quantized multi-level sequence (-2, -1, 0, +1, +2)
    - method: "pyin", "centroid", or "rolloff"
    - pyinConfidence: how confident pYIN was
    """
    if _preloaded is not None:
        y_harm = _preloaded[0]
        sr = SR
    else:
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

        y_harm, _ = _hpss(y_riff)

    if len(y_harm) < sr:
        return {
            "contour": [],
            "intervals": [],
            "method": "none",
            "pyinConfidence": 0.0,
        }

    # ── Method 3: pYIN (most precise when confident) ──
    pyin_contour, pyin_conf = _extract_contour_pyin(y_harm, sr)

    if pyin_contour is not None:
        pitch_curve = pyin_contour
        method = "pyin"
    else:
        # ── Method 1: Spectral centroid (primary default) ──
        centroid_contour = _extract_contour_centroid(y_harm, sr)

        # ── Method 2: Spectral rolloff (backup) ──
        rolloff_contour = _extract_contour_rolloff(y_harm, sr)

        # Pick the one with better melodic movement
        centroid_q = _contour_quality(centroid_contour)
        rolloff_q = _contour_quality(rolloff_contour)

        if len(centroid_contour) > 0 and centroid_q >= rolloff_q:
            pitch_curve = centroid_contour
            method = "centroid"
        elif len(rolloff_contour) > 0:
            pitch_curve = rolloff_contour
            method = "rolloff"
        elif len(centroid_contour) > 0:
            pitch_curve = centroid_contour
            method = "centroid"
        else:
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

    # ── Quantize to multi-level interval sequence ──
    # Five levels capture the SIZE of pitch jumps, not just direction:
    #   -2 = big drop, -1 = small drop, 0 = flat, +1 = small rise, +2 = big rise
    # The threshold between "small" and "big" is 1 standard deviation of deltas.
    delta = np.diff(normalized)
    delta_std = np.std(delta)
    small_threshold = delta_std * 0.25
    big_threshold = delta_std * 1.0

    intervals = np.zeros(len(delta), dtype=int)
    intervals[(delta > small_threshold) & (delta <= big_threshold)] = 1
    intervals[delta > big_threshold] = 2
    intervals[(delta < -small_threshold) & (delta >= -big_threshold)] = -1
    intervals[delta < -big_threshold] = -2

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


def extract_features(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
) -> tuple[dict, dict]:
    """Extract both fingerprint and contour in a single pass.

    Loads audio once, runs HPSS once, reuses the results for both
    fingerprint extraction and contour extraction.

    Returns (fingerprint_dict, contour_dict).
    """
    y, sr = librosa.load(audio_path, sr=SR, mono=True)

    abs_start = song_start_sec + riff_start_sec
    abs_end = song_start_sec + riff_end_sec
    y_riff = y[int(abs_start * sr):int(abs_end * sr)]

    empty_fp = {
        "groove": [], "drums": [], "spectral": [],
        "tempo": 0, "onsetUniformity": 0.5,
    }
    empty_ct = {
        "contour": [], "intervals": [],
        "method": "none", "pyinConfidence": 0.0,
    }

    if len(y_riff) < sr:
        return empty_fp, empty_ct

    # Single HPSS pass — reused by both fingerprint and contour
    y_harm, y_perc = _hpss(y_riff)

    # Extract fingerprint (uses y_riff, y_harm, y_perc)
    fingerprint = _extract_fingerprint_from_audio(
        y_riff, y_harm, y_perc, sr, audio_path=audio_path,
    )

    # Extract contour (reuses y_harm — no redundant load or HPSS)
    contour = extract_contour(
        audio_path, song_start_sec, riff_start_sec, riff_end_sec,
        _preloaded=(y_harm, y_perc),
    )

    return fingerprint, contour


def extract_fingerprint(
    audio_path: str,
    song_start_sec: float,
    riff_start_sec: float,
    riff_end_sec: float,
) -> dict:
    """Extract a complete fingerprint for a single riff.

    Prefer extract_features() when you also need the contour — it avoids
    redundant audio loading and HPSS computation.

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

    y_harm, y_perc = _hpss(y_riff)
    return _extract_fingerprint_from_audio(
        y_riff, y_harm, y_perc, sr, audio_path=audio_path,
    )


def _extract_fingerprint_from_audio(
    y_riff: np.ndarray,
    y_harm: np.ndarray,
    y_perc: np.ndarray,
    sr: int,
    *,
    audio_path: str | None = None,
) -> dict:
    """Core fingerprint extraction from pre-loaded, pre-separated audio."""

    # ── Beat tracking ──
    beats = _detect_beats(y_riff, sr, audio_path=audio_path)

    if len(beats) < MIN_BEATS_FOR_GROOVE:
        fp = _fallback_fingerprint(y_riff, y_perc, sr)
        fp["_degraded"] = "fallback_fingerprint"
        return fp

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

    Uses IOI histogram (simpler approach) instead of beat-aligned patterns.
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
