"""Riff matching via DTW and multi-feature similarity.

Compares riffs using four features:
1. Groove pattern (inter-onset interval histogram) — cosine similarity
2. Drum pattern (low-freq autocorrelation) — cosine similarity
3. Pitch contour — DTW distance, normalized
4. Spectral contrast — cosine similarity

Features are weighted to prioritize what matters for heavily distorted music:
groove and drums are most stable across takes, spectral contrast captures
tone/distortion character, and pitch contour catches melodic shape even
through heavy distortion.
"""

import numpy as np
from scipy.spatial.distance import cosine as cosine_dist


# Feature weights — tuned for death/black metal
WEIGHTS = {
    "groove": 0.35,
    "drums": 0.25,
    "contour": 0.25,
    "spectral": 0.15,
}

# Minimum similarity to consider a match
MIN_MATCH_SCORE = 0.3


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0-1."""
    if not a or not b:
        return 0.0

    # Pad shorter vector with zeros
    max_len = max(len(a), len(b))
    va = np.array(a + [0.0] * (max_len - len(a)))
    vb = np.array(b + [0.0] * (max_len - len(b)))

    # Handle zero vectors
    if np.linalg.norm(va) == 0 or np.linalg.norm(vb) == 0:
        return 0.0

    return float(1.0 - cosine_dist(va, vb))


def _dtw_distance(a: list[float], b: list[float]) -> float:
    """Simple DTW distance between two sequences. Returns 0-1 similarity."""
    if not a or not b:
        return 0.0

    n, m = len(a), len(b)

    # Limit computation for very long sequences
    if n > 500 or m > 500:
        # Downsample
        factor = max(n, m) // 500 + 1
        a = a[::factor]
        b = b[::factor]
        n, m = len(a), len(b)

    # DTW cost matrix
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0.0

    aa = np.array(a)
    bb = np.array(b)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(aa[i - 1] - bb[j - 1])
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

    # Normalize by path length
    path_length = n + m
    raw_distance = dtw[n, m] / path_length if path_length > 0 else 0.0

    # Convert to similarity (0-1). Max distance per step is 1.0 (normalized contour).
    similarity = max(0.0, 1.0 - raw_distance * 2)
    return similarity


def compare_riffs(riff_a: dict, riff_b: dict) -> dict:
    """Compare two riffs and return a similarity score with breakdown.

    Each riff should have: fingerprint (with groove, drums, spectral, tempo)
    and optionally contour.

    Returns:
        {
            "score": float (0-1),
            "breakdown": {"groove": float, "drums": float, "contour": float, "spectral": float}
        }
    """
    fp_a = riff_a.get("fingerprint", {})
    fp_b = riff_b.get("fingerprint", {})

    # Individual feature similarities
    groove_sim = _cosine_similarity(
        fp_a.get("groove", []), fp_b.get("groove", [])
    )
    drums_sim = _cosine_similarity(
        fp_a.get("drums", []), fp_b.get("drums", [])
    )
    spectral_sim = _cosine_similarity(
        fp_a.get("spectral", []), fp_b.get("spectral", [])
    )
    contour_sim = _dtw_distance(
        riff_a.get("contour", []), riff_b.get("contour", [])
    )

    # Tempo penalty: if tempos differ by more than 15%, reduce score
    tempo_a = fp_a.get("tempo", 0)
    tempo_b = fp_b.get("tempo", 0)
    tempo_penalty = 1.0
    if tempo_a > 0 and tempo_b > 0:
        ratio = min(tempo_a, tempo_b) / max(tempo_a, tempo_b)
        if ratio < 0.85:
            tempo_penalty = ratio  # Proportional penalty

    breakdown = {
        "groove": round(groove_sim, 4),
        "drums": round(drums_sim, 4),
        "contour": round(contour_sim, 4),
        "spectral": round(spectral_sim, 4),
    }

    # Weighted sum
    raw_score = (
        WEIGHTS["groove"] * groove_sim
        + WEIGHTS["drums"] * drums_sim
        + WEIGHTS["contour"] * contour_sim
        + WEIGHTS["spectral"] * spectral_sim
    )

    score = round(raw_score * tempo_penalty, 4)

    return {"score": score, "breakdown": breakdown}


def find_matches(
    new_riffs: list[dict],
    existing_riffs: list[dict],
) -> list[dict]:
    """Compare every new riff against every existing riff.

    Returns a list of match results above MIN_MATCH_SCORE:
    [{"riffAId": str, "riffBId": str, "score": float, "breakdown": dict}, ...]
    """
    matches = []

    for new_riff in new_riffs:
        for existing_riff in existing_riffs:
            # Don't compare a riff to itself or to riffs from the same recording
            if new_riff.get("_id") == existing_riff.get("_id"):
                continue
            if new_riff.get("recordingId") == existing_riff.get("recordingId"):
                continue

            result = compare_riffs(new_riff, existing_riff)

            if result["score"] >= MIN_MATCH_SCORE:
                matches.append(
                    {
                        "riffAId": new_riff["_id"],
                        "riffBId": existing_riff["_id"],
                        "score": result["score"],
                        "breakdown": result["breakdown"],
                    }
                )

    return matches
