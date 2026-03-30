"""Loudness normalization to -14 LUFS, output as FLAC."""

import numpy as np
import pyloudnorm as pyln
import soundfile as sf


TARGET_LUFS = -14.0


def normalize(input_path: str, output_path: str) -> None:
    """Read a WAV, loudness-normalize to TARGET_LUFS, write as FLAC."""
    data, rate = sf.read(input_path)

    # Convert mono to stereo-compatible shape for pyloudnorm
    if data.ndim == 1:
        data = data[:, np.newaxis]

    meter = pyln.Meter(rate)
    loudness = meter.integrated_loudness(data)

    if np.isinf(loudness):
        # Silent file — write as-is
        sf.write(output_path, data, rate, format="FLAC")
        return

    normalized = pyln.normalize.loudness(data, loudness, TARGET_LUFS)

    # Clip to prevent overflow
    normalized = np.clip(normalized, -1.0, 1.0)

    sf.write(output_path, normalized, rate, format="FLAC")
