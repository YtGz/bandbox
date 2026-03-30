"""Encode audio segments as Opus using ffmpeg (called via subprocess)."""

import subprocess
from pathlib import Path

import soundfile as sf


def encode_opus(input_path: str, output_path: str, bitrate: str = "128k") -> None:
    """Encode an audio file to Opus via ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-c:a",
            "libopus",
            "-b:a",
            bitrate,
            "-vbr",
            "on",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def split_and_encode(
    flac_path: str,
    output_dir: str,
    recording_id: str,
    start_sec: float,
    end_sec: float,
) -> dict[str, str]:
    """Split a FLAC into pre/song/post segments and encode to Opus.

    Returns a dict with keys: pathSong, pathPre, pathPost (relative paths).
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Read the full FLAC
    data, rate = sf.read(flac_path)

    start_sample = int(start_sec * rate)
    end_sample = int(end_sec * rate)
    total_samples = len(data)

    # Clamp
    start_sample = max(0, min(start_sample, total_samples))
    end_sample = max(start_sample, min(end_sample, total_samples))

    paths: dict[str, str] = {}

    # Pre-song segment
    if start_sample > rate:  # Only if > 1 second of pre content
        pre_path = out / f"{recording_id}_pre.wav"
        pre_opus = out / f"{recording_id}_pre.opus"
        sf.write(str(pre_path), data[:start_sample], rate)
        encode_opus(str(pre_path), str(pre_opus))
        pre_path.unlink()  # Clean up temp WAV
        paths["pathPre"] = f"{recording_id}_pre.opus"

    # Song segment
    song_path = out / f"{recording_id}_song.wav"
    song_opus = out / f"{recording_id}_song.opus"
    sf.write(str(song_path), data[start_sample:end_sample], rate)
    encode_opus(str(song_path), str(song_opus))
    song_path.unlink()
    paths["pathSong"] = f"{recording_id}_song.opus"

    # Post-song segment
    if total_samples - end_sample > rate:  # Only if > 1 second of post content
        post_path = out / f"{recording_id}_post.wav"
        post_opus = out / f"{recording_id}_post.opus"
        sf.write(str(post_path), data[end_sample:], rate)
        encode_opus(str(post_path), str(post_opus))
        post_path.unlink()
        paths["pathPost"] = f"{recording_id}_post.opus"

    return paths
