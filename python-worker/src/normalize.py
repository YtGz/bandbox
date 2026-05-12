"""Loudness normalization to -14 LUFS with true-peak ceiling, output as FLAC."""

import numpy as np
import pyloudnorm as pyln
import soundfile as sf


TARGET_LUFS = -14.0
PEAK_CEILING_DB = -1.0   # don't push true peaks above this
PEAK_CEILING = 10 ** (PEAK_CEILING_DB / 20)


def normalize(input_path: str, output_path: str) -> None:
    """
    Read a WAV, loudness-normalize toward TARGET_LUFS while keeping the
    true peak below PEAK_CEILING_DB, write as FLAC.

    Band-practice recordings have a much higher crest factor than
    mastered music — naive LUFS normalization would push transient
    peaks well past 0 dBFS and a downstream `np.clip` would turn them
    into audible distortion. Instead, compute the LUFS gain and the
    peak-headroom gain, then apply whichever is *smaller* so we never
    clip.
    """
    data, rate = sf.read(input_path)

    # pyloudnorm needs a 2D array even for mono input.
    if data.ndim == 1:
        data = data[:, np.newaxis]

    meter = pyln.Meter(rate)
    loudness = meter.integrated_loudness(data)

    peak = float(np.max(np.abs(data)))

    # Silent or near-silent file → just transcode.
    if peak < 1e-8 or np.isinf(loudness) or np.isnan(loudness):
        sf.write(output_path, data, rate, format="FLAC")
        return

    # Linear gain factors for each goal.
    lufs_gain = 10 ** ((TARGET_LUFS - loudness) / 20)
    peak_gain = PEAK_CEILING / peak

    # Honour the tighter of the two constraints.
    gain = min(lufs_gain, peak_gain)

    sf.write(output_path, data * gain, rate, format="FLAC")
