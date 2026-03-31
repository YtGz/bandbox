"""LLM-assisted song grouping.

Takes riff match data, transcripts, transitions, and metadata, builds a
structured report, and asks an LLM to group recordings into songs with
working titles.
"""

import json
import logging
import os

import httpx

log = logging.getLogger("bandbox-worker")

SYSTEM_PROMPT = """\
You are BandBox, an audio analysis system for a band's practice recordings.
This band plays black metal and death metal — blast beats, tremolo picking,
guttural vocals, heavily distorted guitars. Tempos range from doom (~60 BPM)
to blast sections (180+ BPM). A single song may contain both.

You receive three independent signal types. Triangulate — each compensates
for the others' weaknesses.

─── SIGNAL 1: AUDIO SIMILARITY ───────────────────────────────────────────

Riff match scores (0–1) between recording pairs, broken down by feature:
  • Groove (rhythmic pattern)  — most reliable across takes
  • Drums (kick/snare pattern) — very consistent, anchors identity
  • Contour (pitch movement)   — good for melodic riffs, noisy for atonal parts
  • Spectral (tone/distortion) — least reliable, use as tiebreaker only

Interpretation guide (scores are weighted cosine/DTW similarity, 0–1):
  High scores (top quartile of reported pairs) — strong same-song signal
  Mid scores — ambiguous, rely on speech and tempo to decide
  Low scores (near the bottom of reported pairs) — likely different songs
  Missing pairs — no significant similarity detected, treat as different
Note: pairs below 0.3 are pre-filtered out, so absent pairs = low match.

─── SIGNAL 2: SPEECH (per recording) ─────────────────────────────────────

Pre-song speech: what was said before playing. Often contains:
  • Song names ("Gravecrawler from the top")
  • References ("that doom one again", "the fast one")
  • Take numbers ("take 3")
  • Musical instructions ("drop D, slower this time")

Post-song speech: what was said after playing. Often contains:
  • Satisfaction cues ("that was the one", "nailed it")
  • Continuation cues ("one more", "again", "let's do that again")
  • Transition cues ("okay different song", "now the fast one")

─── SIGNAL 3: TRANSITIONS ────────────────────────────────────────────────

Speech captured between consecutive recordings (end of one → start of next).
This is the overlap of recording N's post-speech and recording N+1's
pre-speech, presented as a single passage for context.

Transitions are directional — they refer to what just happened, what's
about to happen, or both. Examples:

  "that was good, one more"
   → next recording = same song as previous

  "alright we got it, now the blasting one"
   → next recording = different song, possibly named "The Blasting One"

  "okay from the top"
   → next recording = same song (retake from beginning)

  "let's try something new"
   → next recording = different song

CRITICAL: If the reference direction is unclear, IGNORE the transition.
Do not let ambiguous transitions override audio similarity evidence.
When speech and audio conflict, trust high audio similarity over speech.

─── YOUR TASK ────────────────────────────────────────────────────────────

1. GROUP recordings that are takes of the same song. Use all three signals:
   • Audio similarity is your foundation
   • Speech confirms, disambiguates, or overrides weak audio matches
   • Transitions reveal sequence — "one more" = same group continues

2. TITLE each group (2–4 words). In priority order:
   a) Use the band's own words. They named it? Use that name.
      "okay Gravecrawler from the top"      → Gravecrawler
      "let's do that doom one"              → The Doom One
      "tremolo part in D"                   → D Tremolo
      "that blasting thing"                 → The Blasting Thing
   b) If they reference it descriptively, capture the vibe:
      "the slow heavy one"                  → Slow Heavy One
   c) If nobody spoke, derive from musical features:
      Slow + minor key                      → Slow Burn
      Fast blast + tremolo                  → Frostbite Riff
      Mid-tempo chug in drop D              → D Chug
   d) Last resort — genre placeholder with number:
      Untitled Riff #3, Blast Passage #1
   Titles must be easy to yell across a practice room. Punchy > clever.

3. SINGLE RECORDINGS: If a recording has no strong match to anything else,
   it's probably a new song with only one take so far. Create a group of one
   and title it from speech or musical features. Only leave a recording
   ungrouped if you truly cannot determine anything about it.

4. LEAVE recordings ungrouped if you're not confident about a MULTI-RECORDING
   group. One wrong group is worse than ten ungrouped recordings. But don't
   leave a clearly distinct song ungrouped just because it has no siblings.

5. NOTES: Write 1–2 sentences explaining your reasoning per group.
   Mention which signals convinced you (audio match score, speech cue,
   transition). This helps the band understand and correct mistakes.

─── OUTPUT FORMAT ─────────────────────────────────────────────────────────

Respond with ONLY a JSON object. No markdown fences, no explanation.
{
  "groups": [
    {
      "title": "Song Title",
      "notes": "Reasoning: which signals led to this grouping",
      "recordingIds": ["id1", "id2"],
      "existingSongId": "optional — only if assigning to an existing song"
    }
  ],
  "ungrouped": ["id3", "id4"]
}

Rules:
• recordingIds must only contain IDs from the RECORDINGS section
• To assign to an existing song, include its existingSongId
• Do not fabricate IDs — use only what appears in the report
"""


def _build_report(
    recordings: list[dict],
    riff_matches: list[dict],
    riffs_by_recording: dict[str, list[dict]],
) -> str:
    """Build a structured report with recordings, transitions, and matches."""
    lines: list[str] = []

    # ── Recordings ──────────────────────────────────────────

    lines.append("=== RECORDINGS ===")
    lines.append("(Ordered chronologically by upload time)\n")

    for rec in recordings:
        lines.append(f"ID: {rec['_id']}")
        lines.append(f"  Filename: {rec.get('filename', 'unknown')}")
        lines.append(f"  Tempo: {rec.get('tempo', 'unknown')} BPM")
        lines.append(f"  Key: {rec.get('dominantKey', 'unknown')}")
        lines.append(f"  Duration: {rec.get('durationSec', 'unknown')}s")
        lines.append(f"  Riffs: {len(riffs_by_recording.get(rec['_id'], []))}")

        if rec.get("transcriptPre"):
            lines.append(f'  Speech before: "{rec["transcriptPre"]}"')
        if rec.get("transcriptPost"):
            lines.append(f'  Speech after: "{rec["transcriptPost"]}"')

        lines.append("")

    # ── Transitions ─────────────────────────────────────────
    # Build transitions from consecutive recording pairs.
    # A transition combines recording N's post-speech with N+1's pre-speech
    # into a single contextual passage.

    transitions = []
    for i in range(len(recordings) - 1):
        rec_a = recordings[i]
        rec_b = recordings[i + 1]

        post = rec_a.get("transcriptPost", "").strip()
        pre = rec_b.get("transcriptPre", "").strip()

        if not post and not pre:
            continue

        parts = []
        if post:
            parts.append(post)
        if pre:
            parts.append(pre)

        name_a = rec_a.get("filename", rec_a["_id"])
        name_b = rec_b.get("filename", rec_b["_id"])

        transitions.append({
            "id_a": rec_a["_id"],
            "id_b": rec_b["_id"],
            "name_a": name_a,
            "name_b": name_b,
            "speech": " … ".join(parts),
        })

    if transitions:
        lines.append("=== TRANSITIONS ===")
        lines.append("(Speech between consecutive recordings)\n")

        for t in transitions:
            lines.append(f'{t["name_a"]} → {t["name_b"]}')
            lines.append(f'  "{t["speech"]}"')
            lines.append("")

    # ── Riff matches ────────────────────────────────────────
    # Best match per recording pair, sorted by score descending.

    pair_scores: dict[tuple[str, str], dict] = {}

    # Build a fast riff→recording lookup
    riff_to_rec: dict[str, str] = {}
    for rec_id, riffs in riffs_by_recording.items():
        for riff in riffs:
            riff_to_rec[riff["_id"]] = rec_id

    for match in riff_matches:
        riff_a_rec = riff_to_rec.get(match["riffAId"])
        riff_b_rec = riff_to_rec.get(match["riffBId"])

        if not riff_a_rec or not riff_b_rec or riff_a_rec == riff_b_rec:
            continue

        pair = (min(riff_a_rec, riff_b_rec), max(riff_a_rec, riff_b_rec))
        if pair not in pair_scores or match["score"] > pair_scores[pair]["score"]:
            pair_scores[pair] = match

    if pair_scores:
        lines.append("=== AUDIO SIMILARITY ===")
        lines.append("(Best riff match per recording pair, descending)\n")

        rec_name = {r["_id"]: r.get("filename", r["_id"]) for r in recordings}

        for (rec_a, rec_b), match in sorted(
            pair_scores.items(), key=lambda x: -x[1]["score"]
        ):
            bd = match.get("breakdown", {})
            lines.append(
                f"{rec_name.get(rec_a, rec_a)} ↔ {rec_name.get(rec_b, rec_b)}"
            )
            lines.append(f"  Overall: {match['score']:.3f}")
            lines.append(
                f"  Groove: {bd.get('groove', 0):.3f}  "
                f"Drums: {bd.get('drums', 0):.3f}  "
                f"Contour: {bd.get('contour', 0):.3f}  "
                f"Spectral: {bd.get('spectral', 0):.3f}"
            )
            lines.append("")

    return "\n".join(lines)


def _call_llm(report: str) -> dict:
    """Call the LLM API with the similarity report."""
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        log.warning("LLM_API_KEY not set, skipping LLM grouping")
        return {"groups": [], "ungrouped": []}

    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    resp = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": report},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        },
        timeout=60,
    )
    resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]

    # Strip markdown fences if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON: %s", content[:500])
        return {"groups": [], "ungrouped": []}


def group_recordings(
    ungrouped_recordings: list[dict],
    existing_songs: list[dict],
    riff_matches: list[dict],
    riffs_by_recording: dict[str, list[dict]],
    convex_client: object,
) -> None:
    """Run LLM grouping on ungrouped recordings.

    Args:
        ungrouped_recordings: Recordings in 'ungrouped' state from the current
            batch, ordered chronologically by upload time.
        existing_songs: All existing songs (for context — LLM might assign to
            existing).
        riff_matches: All riff match results involving the ungrouped recordings.
        riffs_by_recording: Dict mapping recording ID to its riffs.
        convex_client: ConvexWorkerClient instance.
    """
    from .convex_client import ConvexWorkerClient

    client: ConvexWorkerClient = convex_client  # type: ignore

    if not ungrouped_recordings:
        log.info("No ungrouped recordings to process")
        return

    log.info("Building similarity report for %d recordings", len(ungrouped_recordings))
    report = _build_report(ungrouped_recordings, riff_matches, riffs_by_recording)

    # Add existing songs context
    if existing_songs:
        song_lines = ["\n=== EXISTING SONGS ==="]
        song_lines.append("(These songs already exist — you may assign new takes to them)\n")

        for song in existing_songs:
            song_lines.append(f"Song ID: {song['_id']}")
            song_lines.append(f"  Title: {song['title']}")
            if song.get("notes"):
                song_lines.append(f"  Notes: {song['notes']}")
            rec_count = len(song.get("recordings", []))
            if rec_count:
                song_lines.append(f"  Existing takes: {rec_count}")
            song_lines.append("")

        report = "\n".join(song_lines) + "\n" + report

    log.info("Calling LLM for grouping...")
    log.debug("Report:\n%s", report)
    result = _call_llm(report)
    log.info("LLM result: %s", json.dumps(result, indent=2))

    # Apply grouping decisions
    for group in result.get("groups", []):
        title = group.get("title", "Untitled")
        notes = group.get("notes", "")
        recording_ids = group.get("recordingIds", [])
        existing_song_id = group.get("existingSongId")

        if not recording_ids:
            continue

        # Filter to only IDs that are actually ungrouped recordings
        valid_ids = [r["_id"] for r in ungrouped_recordings]
        recording_ids = [rid for rid in recording_ids if rid in valid_ids]

        if not recording_ids:
            continue

        try:
            if existing_song_id:
                log.info(
                    "Assigning %d recordings to existing song '%s'",
                    len(recording_ids),
                    title,
                )
                for rec_id in recording_ids:
                    client.assign_to_song(rec_id, existing_song_id)
            else:
                log.info(
                    "Creating new song '%s' with %d recordings",
                    title,
                    len(recording_ids),
                )
                client.create_song_and_assign(title, notes, recording_ids)
        except Exception:
            log.exception("Failed to apply grouping for '%s'", title)

    log.info("Grouping complete")
