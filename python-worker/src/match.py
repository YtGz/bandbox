"""Riff matching with adaptive weighting for death/black metal.

Compares riffs using four features with weights that shift based on
riff type:
- Blast beat / tremolo → lean on melodic CONTOUR (rhythm is uniform)
- Groove / breakdown   → lean on RHYTHM (pitch is static chugs)
- Mixed / unclear      → balanced

Features:
1. Groove pattern — cosine similarity of beat-aligned onset patterns
2. Drum pattern — cosine similarity of percussive onset patterns (after HPSS)
3. Pitch contour — DTW on normalized melodic shape (tuning-independent)
4. Spectral contrast — cosine similarity (tiebreaker, least reliable)

DTW uses open begin/end for subsequence matching — a partial take of
one riff can match against a longer recording containing that riff.
"""

import numpy as np
from scipy.spatial.distance import cosine as cosine_dist

# Minimum similarity to store a match (pairs below this are dropped)
MIN_MATCH_SCORE = 0.3

# ════════════════════════════════════════════════════════════
#  ADAPTIVE WEIGHTS
# ════════════════════════════════════════════════════════════

# Onset uniformity thresholds (from analyze.py's onsetUniformity field):
#   High (>0.7) = blast beat / tremolo — nearly every slot is active
#   Low  (<0.4) = groove / breakdown — sparse, syncopated hits
UNIFORMITY_BLAST = 0.7
UNIFORMITY_GROOVE = 0.4

WEIGHTS_BLAST = {
    "contour": 0.55,
    "groove": 0.10,
    "drums": 0.10,
    "spectral": 0.05,
    "tempo": 0.20,
}

WEIGHTS_GROOVE = {
    "contour": 0.15,
    "groove": 0.35,
    "drums": 0.20,
    "spectral": 0.10,
    "tempo": 0.20,
}

WEIGHTS_BALANCED = {
    "contour": 0.30,
    "groove": 0.25,
    "drums": 0.15,
    "spectral": 0.10,
    "tempo": 0.20,
}


def _select_weights(uniformity_a: float, uniformity_b: float) -> dict[str, float]:
    """Pick feature weights based on both riffs' onset uniformity."""
    if uniformity_a > UNIFORMITY_BLAST and uniformity_b > UNIFORMITY_BLAST:
        return WEIGHTS_BLAST
    elif uniformity_a < UNIFORMITY_GROOVE and uniformity_b < UNIFORMITY_GROOVE:
        return WEIGHTS_GROOVE
    else:
        return WEIGHTS_BALANCED


# ════════════════════════════════════════════════════════════
#  SIMILARITY FUNCTIONS
# ════════════════════════════════════════════════════════════


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0-1.

    Handles length mismatches by padding the shorter vector with zeros.
    """
    if not a or not b:
        return 0.0

    max_len = max(len(a), len(b))
    va = np.array(a + [0.0] * (max_len - len(a)))
    vb = np.array(b + [0.0] * (max_len - len(b)))

    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0

    return float(max(0.0, 1.0 - cosine_dist(va, vb)))


def _dtw_subsequence(a: list[float], b: list[float]) -> float:
    """DTW with open begin/end for subsequence matching.

    Returns 0-1 similarity. Allows a short sequence to match against
    part of a longer one — critical for partial take matching.
    """
    if not a or not b:
        return 0.0

    aa = np.array(a, dtype=np.float64)
    bb = np.array(b, dtype=np.float64)
    n, m = len(aa), len(bb)

    # Downsample if needed (keep DTW tractable)
    max_len = 500
    if n > max_len:
        aa = np.interp(np.linspace(0, n, max_len), np.arange(n), aa)
        n = max_len
    if m > max_len:
        bb = np.interp(np.linspace(0, m, max_len), np.arange(m), bb)
        m = max_len

    # DTW cost matrix — open begin: first row is 0 (can start anywhere in b)
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, :] = 0.0  # open begin — matching can start at any position in b

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = (aa[i - 1] - bb[j - 1]) ** 2
            dtw[i, j] = cost + min(
                dtw[i - 1, j],      # insertion
                dtw[i, j - 1],      # deletion
                dtw[i - 1, j - 1],  # match
            )

    # Open end — take minimum of last row (can end anywhere in b)
    end_cost = float(np.min(dtw[n, :]))

    # Normalize by the length of the shorter sequence
    path_len = min(n, m)
    if path_len == 0:
        return 0.0

    normalized = end_cost / path_len

    # Convert distance to similarity.
    # For zero-mean unit-variance signals, expected per-step distance
    # for identical signals ≈ 0, for uncorrelated ≈ 2.0
    similarity = max(0.0, 1.0 - normalized / 2.0)

    return similarity


def _tempo_similarity(tempo_a: float, tempo_b: float) -> float:
    """Tempo similarity with penalty for >15% difference.

    Returns 0-1. Handles double/half tempo by checking both.
    """
    if tempo_a <= 0 or tempo_b <= 0:
        return 0.5  # unknown tempo — neutral

    # Check direct ratio and double/half
    ratios = [
        min(tempo_a, tempo_b) / max(tempo_a, tempo_b),
        min(tempo_a, tempo_b * 2) / max(tempo_a, tempo_b * 2),
        min(tempo_a * 2, tempo_b) / max(tempo_a * 2, tempo_b),
    ]
    best_ratio = max(ratios)

    if best_ratio >= 0.85:
        return 1.0  # within normal drift
    else:
        return best_ratio  # proportional penalty


# ════════════════════════════════════════════════════════════
#  RIFF COMPARISON
# ════════════════════════════════════════════════════════════


def compare_riffs(riff_a: dict, riff_b: dict) -> dict:
    """Compare two riffs with adaptive weighting.

    Each riff should have:
    - fingerprint: {groove, drums, spectral, tempo, onsetUniformity}
    - contour: {contour, intervals, method, ...}

    Returns:
        {
            "score": float (0-1),
            "breakdown": {
                "groove": float, "drums": float,
                "contour": float, "spectral": float,
                "tempo": float
            },
            "weights": str ("blast" | "groove" | "balanced")
        }
    """
    fp_a = riff_a.get("fingerprint", {})
    fp_b = riff_b.get("fingerprint", {})
    ct_a = riff_a.get("contour", {})
    ct_b = riff_b.get("contour", {})

    # Handle legacy format: contour might be a plain list (old schema)
    if isinstance(ct_a, list):
        ct_a = {"contour": ct_a, "intervals": []}
    if isinstance(ct_b, list):
        ct_b = {"contour": ct_b, "intervals": []}

    # ── Per-feature similarities ──

    groove_sim = _cosine_similarity(
        fp_a.get("groove", []),
        fp_b.get("groove", []),
    )

    drums_sim = _cosine_similarity(
        fp_a.get("drums", []),
        fp_b.get("drums", []),
    )

    spectral_sim = _cosine_similarity(
        fp_a.get("spectral", []),
        fp_b.get("spectral", []),
    )

    # Contour: use the smooth normalized curve for DTW
    contour_a = ct_a.get("contour", [])
    contour_b = ct_b.get("contour", [])
    contour_sim = _dtw_subsequence(contour_a, contour_b)

    tempo_sim = _tempo_similarity(
        fp_a.get("tempo", 0),
        fp_b.get("tempo", 0),
    )

    # ── Adaptive weighting ──
    uniformity_a = fp_a.get("onsetUniformity", 0.5)
    uniformity_b = fp_b.get("onsetUniformity", 0.5)
    weights = _select_weights(uniformity_a, uniformity_b)

    # Label for debugging
    if weights is WEIGHTS_BLAST:
        weight_label = "blast"
    elif weights is WEIGHTS_GROOVE:
        weight_label = "groove"
    else:
        weight_label = "balanced"

    # ── Weighted sum ──
    score = (
        weights["contour"] * contour_sim
        + weights["groove"] * groove_sim
        + weights["drums"] * drums_sim
        + weights["spectral"] * spectral_sim
        + weights["tempo"] * tempo_sim
    )

    breakdown = {
        "groove": round(groove_sim, 4),
        "drums": round(drums_sim, 4),
        "contour": round(contour_sim, 4),
        "spectral": round(spectral_sim, 4),
        "tempo": round(tempo_sim, 4),
    }

    return {
        "score": round(score, 4),
        "breakdown": breakdown,
        "weights": weight_label,
    }


# ════════════════════════════════════════════════════════════
#  BATCH MATCHING
# ════════════════════════════════════════════════════════════


def find_matches(
    new_riffs: list[dict],
    existing_riffs: list[dict],
) -> list[dict]:
    """Compare every new riff against every existing riff.

    Returns matches above MIN_MATCH_SCORE:
    [{"riffAId": str, "riffBId": str, "score": float,
      "breakdown": dict, "weights": str}, ...]
    """
    matches = []

    for new_riff in new_riffs:
        new_id = new_riff.get("_id")
        new_rec = new_riff.get("recordingId")

        for existing_riff in existing_riffs:
            ex_id = existing_riff.get("_id")
            ex_rec = existing_riff.get("recordingId")

            # Skip self and same-recording comparisons
            if new_id == ex_id:
                continue
            if new_rec == ex_rec:
                continue

            result = compare_riffs(new_riff, existing_riff)

            if result["score"] >= MIN_MATCH_SCORE:
                matches.append({
                    "riffAId": new_id,
                    "riffBId": ex_id,
                    "score": result["score"],
                    "breakdown": result["breakdown"],
                    "weights": result["weights"],
                })

    return matches
