"""Main worker loop — watches for manifests and processes recordings."""

import json
import logging
import os
import time
from pathlib import Path

from .convex_client import ConvexWorkerClient
from .normalize import normalize
from .trim import detect_boundaries
from .encode import split_and_encode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bandbox-worker")

POLL_INTERVAL = 5  # seconds


def get_audio_path() -> str:
    return os.environ.get("AUDIO_DATA_PATH", "/data/audio")


def process_recording(manifest: dict, client: ConvexWorkerClient) -> None:
    """Process a single recording through the pipeline."""
    recording_id = manifest["recordingId"]
    wav_path = manifest["filePath"]
    audio_base = get_audio_path()
    processed_dir = os.path.join(audio_base, "processed")
    os.makedirs(processed_dir, exist_ok=True)

    flac_path = os.path.join(processed_dir, f"{recording_id}.flac")

    # Step 1: Normalize
    log.info("[%s] Normalizing...", recording_id)
    client.update_state(recording_id, "normalizing")
    normalize(wav_path, flac_path)
    client.update_state(recording_id, "normalizing", pathFlac=f"{recording_id}.flac")

    # Step 2: Trim — detect song boundaries
    log.info("[%s] Detecting song boundaries...", recording_id)
    client.update_state(recording_id, "trimming")
    trim_result = detect_boundaries(flac_path)
    log.info(
        "[%s] Trim: %.1fs–%.1fs (confidence=%.2f, method=%s)",
        recording_id,
        trim_result.start_sec,
        trim_result.end_sec,
        trim_result.confidence,
        trim_result.method,
    )

    # Step 3: Encode segments
    log.info("[%s] Encoding segments...", recording_id)
    segment_paths = split_and_encode(
        flac_path,
        processed_dir,
        recording_id,
        trim_result.start_sec,
        trim_result.end_sec,
    )

    # Update Convex with trim results and file paths
    client.update_state(
        recording_id,
        "analyzing",
        cutStartSec=trim_result.start_sec,
        cutEndSec=trim_result.end_sec,
        trimConfidence=trim_result.confidence,
        trimMethod=trim_result.method,
        durationSec=trim_result.end_sec - trim_result.start_sec,
        **segment_paths,
    )

    # Step 4: Analysis placeholder
    # Feature extraction, riff fingerprinting, etc. will be added in Phase 7.
    # For now, mark as ungrouped.
    log.info("[%s] Analysis complete (stub), marking as ungrouped", recording_id)
    client.update_state(recording_id, "ungrouped")

    # Step 5: Clean up original WAV
    try:
        os.unlink(wav_path)
        log.info("[%s] Cleaned up source WAV", recording_id)
    except OSError:
        log.warning("[%s] Could not delete source WAV: %s", recording_id, wav_path)

    log.info("[%s] ✓ Processing complete", recording_id)


def run() -> None:
    """Main worker loop."""
    audio_base = get_audio_path()
    manifests_dir = os.path.join(audio_base, "incoming", "manifests")

    log.info("BandBox Worker starting")
    log.info("Audio path: %s", audio_base)
    log.info("Manifests dir: %s", manifests_dir)

    client = ConvexWorkerClient()

    while True:
        # Ensure directory exists
        os.makedirs(manifests_dir, exist_ok=True)

        # Scan for manifest files
        manifest_files = sorted(Path(manifests_dir).glob("*.json"))

        for manifest_path in manifest_files:
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)

                log.info("Processing: %s", manifest.get("filename", manifest_path.name))
                process_recording(manifest, client)

                # Delete manifest after successful processing
                manifest_path.unlink()

            except Exception:
                log.exception("Error processing %s", manifest_path.name)
                # Move failed manifest to avoid retry loop
                failed_dir = Path(manifests_dir) / "failed"
                failed_dir.mkdir(exist_ok=True)
                manifest_path.rename(failed_dir / manifest_path.name)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
