"""LLM-assisted song grouping.

Takes riff match data, transcripts, and metadata, builds a structured
report, and asks an LLM to group recordings into songs with working titles.
"""

import json
import logging
import os

import httpx

log = logging.getLogger("bandbox-worker")

SYSTEM_PROMPT = """\
You are BandBox, an audio analysis system for a band's practice recordings.

You will receive a similarity report containing:
- Recording metadata (filename, tempo, key, duration)
- Pre-song transcripts (what the band said before playing)
- Riff match scores between recordings (0-1 similarity with feature breakdowns)

Context: this band plays black metal and death metal. Expect blast beats, tremolo
picking, guttural vocals, and heavily distorted guitars. Tempos range from slow
doom passages (~60 BPM) to blast sections (180+ BPM). A single song may contain
both.

Your job:
1. Group recordings that are takes of the SAME SONG based on:
   - High riff similarity scores (especially groove and contour)
   - Similar tempo (within ~15% is normal variation between takes)
   - Similar key
   - Verbal cues in transcripts ("again", "from the top", "that one", song names)
2. For each group, suggest a short working title (2-4 words). Priority order:
   - Use something the band actually said if possible.
     "let's do that doom one" → "The Doom One"
     "tremolo part in D" → "D Tremolo"
     "that blasting thing" → "The Blasting Thing"
   - If they reference a name, even casually, use it.
     "okay Gravecrawler from the top" → "Gravecrawler"
   - If nothing is spoken, describe the vibe using the musical features.
     Slow + minor key → "Slow Burn", fast blast + tremolo → "Frostbite Riff"
   - Last resort: use a genre-appropriate placeholder with a number.
     "Untitled Riff #3", "Blast Passage #1"
   - Titles should be easy to yell across a practice room. Keep them punchy.
3. Leave recordings ungrouped if you're not confident they belong together.
   It's better to leave something ungrouped than to group it incorrectly.

Respond with ONLY a JSON object, no markdown, no explanation:
{
  "groups": [
    {
      "title": "Song Title",
      "notes": "Brief reasoning or musical description",
      "recordingIds": ["id1", "id2", ...],
      "existingSongId": "optional — if assigning to an existing song, put its Song ID here"
    }
  ],
  "ungrouped": ["id3", "id4", ...]
}

If assigning to an existing song, use its title and include existingSongId.
Only include recording IDs from the RECORDINGS section — never include Song IDs in recordingIds.
"""


def _build_report(
    recordings: list[dict],
    riff_matches: list[dict],
    riffs_by_recording: dict[str, list[dict]],
) -> str:
    """Build a structured similarity report for the LLM."""
    lines: list[str] = []

    lines.append("=== RECORDINGS ===\n")
    for rec in recordings:
        lines.append(f"ID: {rec['_id']}")
        lines.append(f"  Filename: {rec.get('filename', 'unknown')}")
        lines.append(f"  Tempo: {rec.get('tempo', 'unknown')} BPM")
        lines.append(f"  Key: {rec.get('dominantKey', 'unknown')}")
        lines.append(f"  Duration: {rec.get('durationSec', 'unknown')}s")
        if rec.get("transcriptPre"):
            lines.append(f'  Transcript (before): "{rec["transcriptPre"]}"')
        if rec.get("transcriptPost"):
            lines.append(f'  Transcript (after): "{rec["transcriptPost"]}"')
        lines.append(f"  Riffs: {len(riffs_by_recording.get(rec['_id'], []))}")
        lines.append("")

    # Build best match per recording pair
    pair_scores: dict[tuple[str, str], dict] = {}
    for match in riff_matches:
        # Find which recordings these riffs belong to
        riff_a_rec = None
        riff_b_rec = None
        for rec_id, riffs in riffs_by_recording.items():
            for riff in riffs:
                if riff["_id"] == match["riffAId"]:
                    riff_a_rec = rec_id
                if riff["_id"] == match["riffBId"]:
                    riff_b_rec = rec_id

        if not riff_a_rec or not riff_b_rec or riff_a_rec == riff_b_rec:
            continue

        # Canonical pair ordering
        pair = (min(riff_a_rec, riff_b_rec), max(riff_a_rec, riff_b_rec))
        if pair not in pair_scores or match["score"] > pair_scores[pair]["score"]:
            pair_scores[pair] = match

    if pair_scores:
        lines.append("=== BEST RIFF MATCHES (per recording pair) ===\n")
        for (rec_a, rec_b), match in sorted(
            pair_scores.items(), key=lambda x: -x[1]["score"]
        ):
            # Find filenames for readability
            name_a = next(
                (r.get("filename", rec_a) for r in recordings if r["_id"] == rec_a),
                rec_a,
            )
            name_b = next(
                (r.get("filename", rec_b) for r in recordings if r["_id"] == rec_b),
                rec_b,
            )
            bd = match.get("breakdown", {})
            lines.append(f"{name_a} <-> {name_b}")
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
        ungrouped_recordings: Recordings in 'ungrouped' state from the current batch.
        existing_songs: All existing songs (for context — LLM might assign to existing).
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
        song_context = "\n=== EXISTING SONGS ===\n"
        for song in existing_songs:
            song_context += f"\nSong ID: {song['_id']}"
            song_context += f"\n  Title: {song['title']}"
            if song.get("notes"):
                song_context += f"\n  Notes: {song['notes']}"
            rec_count = len(song.get("recordings", []))
            if rec_count:
                song_context += f"\n  Existing takes: {rec_count}"
        song_context += (
            "\n\nYou may assign recordings to existing songs using their Song ID. "
            "Put the Song ID (not recording IDs) in an 'existingSongId' field.\n"
        )
        report = song_context + "\n" + report

    log.info("Calling LLM for grouping...")
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
                # Assign to existing song
                log.info(
                    "Assigning %d recordings to existing song '%s'",
                    len(recording_ids),
                    title,
                )
                for rec_id in recording_ids:
                    client.assign_to_song(rec_id, existing_song_id)
            else:
                # Create new song and assign all recordings
                log.info(
                    "Creating new song '%s' with %d recordings",
                    title,
                    len(recording_ids),
                )
                client.create_song_and_assign(title, notes, recording_ids)
        except Exception:
            log.exception("Failed to apply grouping for '%s'", title)

    log.info("Grouping complete")
