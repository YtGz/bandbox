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
from .analyze import segment_riffs, extract_features
from .match import find_matches
from .group import group_recordings

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

    # Step 4: Riff segmentation and feature extraction
    log.info("[%s] Segmenting riffs...", recording_id)
    riff_segments = segment_riffs(
        flac_path, trim_result.start_sec, trim_result.end_sec
    )
    log.info("[%s] Found %d riff segments", recording_id, len(riff_segments))

    riff_data = []
    for i, seg in enumerate(riff_segments):
        log.info(
            "[%s] Extracting features for riff %d (%.1fs–%.1fs)",
            recording_id,
            i,
            seg["startSec"],
            seg["endSec"],
        )
        # Single pass: loads audio once, runs HPSS once
        fingerprint, contour_data = extract_features(
            flac_path,
            trim_result.start_sec,
            seg["startSec"],
            seg["endSec"],
        )

        log.info(
            "[%s] Riff %d: tempo=%.1f, uniformity=%.2f, contour=%s (%d pts)",
            recording_id,
            i,
            fingerprint.get("tempo", 0),
            fingerprint.get("onsetUniformity", 0),
            contour_data.get("method", "none"),
            len(contour_data.get("contour", [])),
        )

        riff_data.append(
            {
                "riffIndex": i,
                "startSec": seg["startSec"],
                "endSec": seg["endSec"],
                "tempo": fingerprint.get("tempo"),
                "fingerprint": fingerprint,
                "contour": contour_data,
            }
        )

    # Store riffs in Convex
    log.info("[%s] Storing %d riffs in Convex", recording_id, len(riff_data))
    client.store_riffs(recording_id, riff_data)

    # Extract overall tempo from riffs
    tempos = [r["fingerprint"]["tempo"] for r in riff_data if r["fingerprint"].get("tempo")]
    overall_tempo = round(sum(tempos) / len(tempos), 1) if tempos else None

    client.update_state(recording_id, "ungrouped", tempo=overall_tempo)

    # Step 5: Clean up original WAV
    try:
        os.unlink(wav_path)
        log.info("[%s] Cleaned up source WAV", recording_id)
    except OSError:
        log.warning("[%s] Could not delete source WAV: %s", recording_id, wav_path)

    log.info("[%s] ✓ Processing complete", recording_id)


def run_batch_matching(processed_ids: list[str], client: ConvexWorkerClient) -> None:
    """After a batch of recordings are analyzed, run riff matching.

    Compares riffs from newly processed recordings against all existing riffs.
    Stores match results in Convex for the LLM grouping step (Phase 8).
    """
    log.info("Running batch matching for %d recordings", len(processed_ids))

    # Fetch all riffs
    all_riffs = client.get_all_riffs()
    total_riffs = len(all_riffs)
    log.info("Total riffs in database: %d", total_riffs)

    # Performance scaling warnings
    if total_riffs > 1000:
        log.warning(
            "⚠️  PERFORMANCE: %d riffs in library. Brute-force matching is O(n²) "
            "and will be slow. Consider implementing approximate nearest neighbor "
            "indexing (faiss/annoy) for the contour vectors. "
            "See: https://github.com/facebookresearch/faiss",
            total_riffs,
        )
        # Notify via Convex so the frontend can show a banner
        try:
            client.set_system_warning(
                "matching_performance",
                f"Riff library has grown to {total_riffs} riffs. "
                f"Matching is getting slow — time to implement vector indexing. "
                f"See AUDIO_ANALYSIS.md § Future.",
            )
        except Exception:
            pass  # non-critical, don't break matching
    elif total_riffs > 500:
        log.info(
            "📊 Riff library at %d — matching still fast, but approaching the "
            "threshold (~1000) where vector indexing would help.",
            total_riffs,
        )

    if len(all_riffs) < 2:
        log.info("Not enough riffs to match, skipping")
        return

    # Split into new (from this batch) and existing
    new_riffs = [r for r in all_riffs if r.get("recordingId") in processed_ids]
    existing_riffs = [r for r in all_riffs if r.get("recordingId") not in processed_ids]

    # Also compare new riffs against each other (within the batch)
    # by including them in both lists
    all_for_matching = all_riffs

    log.info(
        "Matching %d new riffs against %d total riffs",
        len(new_riffs),
        len(all_for_matching),
    )

    matches = find_matches(new_riffs, all_for_matching)
    log.info("Found %d matches above threshold", len(matches))

    for match in matches:
        try:
            client.store_match(
                match["riffAId"],
                match["riffBId"],
                match["score"],
                match["breakdown"],
            )
        except Exception:
            log.exception(
                "Failed to store match %s <-> %s",
                match["riffAId"],
                match["riffBId"],
            )

    log.info("Batch matching complete")

    # Run LLM grouping on ungrouped recordings
    try:
        ungrouped = client.list_ungrouped()
        if ungrouped:
            existing_songs = client.list_songs()
            all_riffs = client.get_all_riffs()

            # Build riffs-by-recording lookup
            riffs_by_recording: dict[str, list[dict]] = {}
            for riff in all_riffs:
                rec_id = riff.get("recordingId", "")
                riffs_by_recording.setdefault(rec_id, []).append(riff)

            # Get all riff matches
            # (we already stored them above, re-fetch for completeness)
            group_recordings(
                ungrouped_recordings=ungrouped,
                existing_songs=existing_songs,
                riff_matches=matches,
                riffs_by_recording=riffs_by_recording,
                convex_client=client,
            )
    except Exception:
        log.exception("Error during LLM grouping")


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

        processed_ids: list[str] = []

        for manifest_path in manifest_files:
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)

                log.info("Processing: %s", manifest.get("filename", manifest_path.name))
                process_recording(manifest, client)
                processed_ids.append(manifest["recordingId"])

                # Delete manifest after successful processing
                manifest_path.unlink()

            except Exception:
                log.exception("Error processing %s", manifest_path.name)
                # Move failed manifest to avoid retry loop
                failed_dir = Path(manifests_dir) / "failed"
                failed_dir.mkdir(exist_ok=True)
                manifest_path.rename(failed_dir / manifest_path.name)

        # After processing a batch, run riff matching
        if processed_ids:
            try:
                run_batch_matching(processed_ids, client)
            except Exception:
                log.exception("Error during batch matching")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
