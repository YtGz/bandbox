# BandBox — Implementation Guide

---

## 1. System Architecture

### Services

Four containers managed by Docker Compose:

- **Caddy** — reverse proxy, auto-HTTPS, routes traffic to SvelteKit and Pocket-ID
- **SvelteKit** — frontend, upload endpoint, audio file serving. Runs on Bun.
- **Python Worker** — audio processing pipeline. Runs on uv. Watches for new files, processes them sequentially, updates Convex.
- **Pocket-ID** — self-hosted OIDC provider with passkey/WebAuthn authentication

### External Service

- **Convex Cloud** — realtime database. Stores all metadata, provides reactive subscriptions to the frontend. Keeps DB load off the SBC.

### Data Flow

1. The Pi uploads a WAV file and its SHA-256 hash to the SvelteKit upload endpoint.
2. SvelteKit saves the file to a shared volume, creates a recording document in Convex (state: `uploading`), and drops a JSON manifest file into a watched directory.
3. The Python worker picks up the manifest file, processes the audio through the pipeline (normalize → trim → encode → analyze → fingerprint), and updates the recording's state in Convex at each step via HTTP actions.
4. Once all recordings in a batch are analyzed, the worker runs DTW matching and LLM grouping, then assigns recordings to songs in Convex.
5. The SvelteKit frontend subscribes to Convex queries. All state changes — processing progress, grouping results — appear in real time without polling or SSE wiring.

### Shared Volume

A single Docker volume (`audio_data`) is mounted by both SvelteKit and the Python worker at `/data/audio`. SvelteKit writes incoming files; the worker reads, processes, and writes output files; SvelteKit serves the processed files to the browser.

---

## 2. Data Model (Convex Schema)

### `songs` table

- `title` — working title, either LLM-generated or manually set
- `notes` — optional free-text, may contain LLM reasoning or band notes
- `createdAt` — timestamp

### `recordings` table

- `filename` — original filename from the USB stick
- `fileHash` — SHA-256, used for deduplication
- `uploadedAt` — timestamp
- `state` — one of: `uploading`, `normalizing`, `trimming`, `analyzing`, `grouped`, `ungrouped`
- `songId` — optional reference to a song
- `pathFlac` — relative path to the full normalized FLAC file
- `pathSong` — relative path to the trimmed song segment (Opus)
- `pathPre` — relative path to the pre-song segment (Opus)
- `pathPost` — relative path to the post-song segment (Opus)
- `cutStartSec` — where the trim begins (seconds into the original)
- `cutEndSec` — where the trim ends
- `trimConfidence` — 0–1 score indicating how confident the trim detection was
- `trimMethod` — which detection method triggered (energy wall, rhythmic regularity, pitched content)
- `transcriptPre` — Whisper transcription of the pre-song segment
- `transcriptPost` — Whisper transcription of the post-song segment
- `tempo` — detected BPM
- `dominantKey` — detected key
- `durationSec` — duration of the trimmed song portion

Indexes: `by_hash` (deduplication), `by_state` (query processing queue), `by_song` (list takes per song)

### `riffs` table

- `recordingId` — reference to parent recording
- `riffIndex` — position within the recording (0, 1, 2...)
- `startSec`, `endSec` — boundaries within the recording
- `tempo` — per-riff tempo
- `fingerprint` — JSON blob containing groove pattern, drum pattern, spectral contrast, tempo
- `contour` — JSON array of the pitch contour (for DTW)

Index: `by_recording`

### `riffMatches` table

- `riffAId`, `riffBId` — the two riffs being compared
- `score` — overall similarity (0–1)
- `breakdown` — JSON object with per-feature scores (groove, drums, contour, spectral)

Indexes: `by_riff_a`, `by_riff_b`

### `corrections` table

- `recordingId` — which recording was manually reassigned
- `fromSongId` — previous song group (null if was ungrouped)
- `toSongId` — new song group
- `correctedAt` — timestamp

Used as training signal for the future custom ML model. Index: `by_recording`.

---

## 3. Authentication

### Pocket-ID Setup

Pocket-ID runs as a Docker container and provides a WebAuthn/passkey-based login flow over OIDC. Each band member registers a passkey on their phone or laptop. No passwords.

Caddy routes `/pocket-id/*` to the Pocket-ID container.

### SvelteKit Integration

- A server hook intercepts every request and checks for a valid session cookie.
- Unauthenticated users are redirected to `/login`, which initiates the OIDC flow with Pocket-ID.
- After successful passkey authentication, Pocket-ID redirects to `/callback` with an authorization code.
- The callback endpoint exchanges the code for tokens, creates a signed session cookie (HttpOnly, Secure, SameSite=Lax, 30-day expiry), and redirects to the dashboard.
- The Pi's upload endpoint uses a static API key in the `X-Api-Key` header instead of session auth.

### Protected Routes

| Path | Auth method |
|---|---|
| `/api/upload` | API key (Pi) |
| `/login`, `/callback` | Public |
| Everything else | Session cookie (Pocket-ID OIDC) |

---

## 4. Frontend (SvelteKit + TailwindCSS)

### Page Structure

```
/ Dashboard (all songs + unsorted)
/song/{id} Song detail (all takes)
/recording/{id} Recording detail (trim review, riff map)
/login Redirect to Pocket-ID
/callback OIDC callback handler
```

### Dashboard (`/`)

The main view. Three sections, top to bottom:

**Processing banner** — only visible when recordings are being processed. Shows a pulsing animation and text like "Analyzing... 3 of 8 recordings remaining". Disappears automatically when all recordings reach `grouped` or `ungrouped` state. This is reactive — driven by a Convex subscription to a query that filters recordings by processing states.

**Song groups** — each song is a collapsible card showing:
- Song title (editable inline)
- Take count and date of most recent take
- Inline audio player for the most recent take's `_song.opus`
- Expand to see all takes as a list

**Unsorted section** — appears at the bottom when recordings exist in `ungrouped` state. Each unsorted recording shows:
- Original filename
- If still processing: a pulsing state badge (`normalizing`, `trimming`, `analyzing`)
- If analysis complete but unmatched: an "Assign to song" dropdown listing all existing songs plus a "Create new song" option
- Inline audio player

All three sections update in real time via Convex subscriptions. When the Python worker groups a recording, it moves from "Unsorted" into its song group without the user doing anything.

### Song Page (`/song/{id}`)

Shows all takes for one song, ordered newest first. Header area:

- Back link to dashboard
- Song title with inline edit (pencil icon)
- LLM-generated notes displayed below the title in muted text
- A "Merge with..." button that opens a dropdown of other songs, for merging two song groups

Each take is a card containing:

- **Date and take number** (e.g., "Mar 11 — Take 3")
- **Audio player** — plays `_song.opus` (the trimmed version)
- **Metadata row** — tempo, key, duration, instrumentation guess
- **Transcript** — if the pre-song speech was transcribed, shown as a quote (e.g., *"okay from the top, doom riff"*)
- **Trim confidence indicator**:
  - High confidence (≥85%): green checkmark, no special treatment
  - Medium confidence (60–84%): amber warning icon with the text "Xsec trimmed"
  - Low confidence (<60%): red warning icon with the text "Xsec trimmed — review recommended"
- **Trim review controls** (see Section 5 below)
- **Actions**: Download FLAC, Move to different song

### Recording Detail (`/recording/{id}`)

A dedicated page (or large modal overlay from the song page) for deep inspection of a single recording. Contains:

- Full waveform visualization of the original recording with the trim region highlighted
- The three audio segments (pre, song, post) each with their own play button
- Riff segmentation map showing detected riff boundaries on a timeline
- Match details: which other recordings share riffs with this one, with similarity scores

This page is optional for v1. The trim review controls on the song page are more important.

### Components

- **AudioPlayer** — wraps HTML5 `<audio>` with a custom seek bar, time display, and playback speed toggle. Supports range requests for seeking. Accepts a source URL pointing to the SvelteKit audio streaming endpoint.
- **RecordingCard** — a single take within a song group or the unsorted list. Contains the audio player, metadata, trim controls, and action buttons.
- **SongGroup** — collapsible card on the dashboard wrapping multiple RecordingCards.
- **ProcessingBadge** — small animated indicator showing the current state of a recording. Pulses while active. Shows a checkmark when done.
- **AssignDropdown** — dropdown menu listing all songs plus "New song". Triggers a Convex mutation to assign the recording.
- **TrimReview** — the trim review and undo interface described in Section 5.

---

## 5. Trim Review and Undo

This is the feature for listening to what was cut and optionally restoring it.

### How Trimming Works (Recap)

The Python worker detects where the actual music starts and ends in each recording. Everything before the music start becomes `_pre.opus` (usually chatter, tuning, count-in). Everything after becomes `_post.opus` (usually chatter, noodling). The music itself becomes `_song.opus`. The original full recording is preserved as the FLAC file.

### Trim Review UI

On every RecordingCard (within the song page), below the main audio player:

**Default state (trim confidence ≥ 85%)**:
A subtle, collapsed row that says "Xsec before · Ysec after" with a small expand chevron. Most users will never touch this.

**Expanded state (or default if confidence < 85%)**:
Three audio segments displayed as a horizontal timeline:

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  ┌──────────┐  ┌──────────────────────────┐  ┌──────────┐  │
│  │ ▶ Before  │  │ ▶ Song (playing)          │  │ ▶ After   │  │
│  │   0:34    │  │       3:42                │  │   0:12    │  │
│  │  dimmed   │  │    highlighted            │  │  dimmed   │  │
│  └──────────┘  └──────────────────────────┘  └──────────┘  │
│                                                             │
│  💬 "okay from the top, doom riff"                          │
│                                                             │
│  [▶ Play from 10s before cut]       [↩ Undo trim]          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**"Before" segment**: Plays `_pre.opus`. Shown dimmed/muted to indicate it's the cut portion. Displays the transcription underneath if available.

**"Song" segment**: The main trimmed audio. This is what the regular player already plays.

**"After" segment**: Plays `_post.opus`. Also dimmed.

**"Play from 10s before cut" button**: This is the key usability feature. It plays the last 10 seconds of the "Before" segment followed immediately by the first few seconds of the "Song" segment, so the user can hear the exact transition point and judge whether the trim was correct. Implemented by seeking the "Before" player to its final 10 seconds, playing it, and when it ends, immediately starting the "Song" player. No server-side stitching needed.

**"Undo trim" button**: Opens a confirmation dialog explaining that this will make the player use the full FLAC instead of the trimmed Opus. When confirmed:
- A Convex mutation sets `cutStartSec` and `cutEndSec` to null and clears `trimMethod`
- The main audio player for this recording switches to serving the full FLAC file instead of `_song.opus`
- The trim review section collapses and shows "Trim removed — playing full recording"
- The user can later re-trim by clicking "Restore trim", which simply restores the original values (stored before clearing)

### Adjustable Trim (v2 Enhancement)

A future improvement: instead of binary undo, allow the user to drag the trim points on a waveform. This would:
- Display a simplified waveform of the full FLAC
- Show draggable markers at the current cut points
- Preview playback from any point
- On save, update `cutStartSec` and `cutEndSec` in Convex and re-encode the Opus segments server-side

This is out of scope for v1 but the data model supports it — the FLAC is always preserved, and the cut points are just numbers.

---

## 6. Audio Streaming

SvelteKit serves audio files through a dedicated endpoint that reads from the shared volume. This endpoint:

- Resolves the requested path relative to the processed audio directory
- Validates against path traversal
- Supports HTTP Range requests (required for seeking in the audio player)
- Sets appropriate Content-Type headers (`audio/opus` for `.opus`, `audio/flac` for `.flac`)
- Sets `Accept-Ranges: bytes` header

The frontend constructs audio URLs like `/api/audio/{recordingId}_song.opus` and passes them to the HTML5 `<audio>` element.

---

## 7. Upload Flow

### From the Pi

The Pi's `bandbox.py` script (already designed) uploads each new WAV file to the SvelteKit upload endpoint as a multipart form request, including the file and its SHA-256 hash.

### SvelteKit Upload Endpoint

1. Verify the `X-Api-Key` header matches the configured Pi API key
2. Call a Convex mutation to create a recording document. The mutation checks the hash index for duplicates and returns early if found.
3. If not a duplicate, save the WAV file to `/data/audio/incoming/{recordingId}.wav`
4. Write a JSON manifest to `/data/audio/incoming/manifests/{recordingId}.json` containing the recording ID and file path
5. Return a response to the Pi indicating accepted or duplicate

### Python Worker Pickup

The worker polls the manifests directory every 5 seconds. When it finds manifest files, it processes them sequentially (one recording at a time to stay within RAM), then deletes each manifest after processing.

---

## 8. Python Worker Pipeline

The worker is the only Python component. It is kept isolated in its own container with its own dependencies managed by uv. It communicates with Convex exclusively through HTTP actions (POST requests to Convex HTTP endpoints, authenticated with a worker API key).

### Processing Steps (per recording)

1. **Normalize** — loudness normalize to -14 LUFS, encode as FLAC
2. **Trim** — detect song start/end using multi-stage detection (energy wall, rhythmic regularity, pitched content). Compute confidence score.
3. **Encode segments** — encode trimmed song as `_song.opus` at 128k, pre-song as `_pre.opus`, post-song as `_post.opus`
4. **Transcribe** — run Whisper on `_pre.opus` and `_post.opus` to extract verbal cues
5. **Analyze** — extract features from the trimmed portion of the FLAC:
   - Riff segmentation via novelty detection
   - Per-riff fingerprinting (groove pattern, drum pattern, spectral contrast, tempo)
   - Pitch contour extraction (spectral centroid method, with pYIN fallback)
6. **Store riffs** — send riff data to Convex via HTTP action
7. **Clean up** — delete the original WAV

After each step, the worker calls a Convex HTTP action to update the recording's state. This triggers reactive updates in every connected browser.

### Grouping (per batch)

After all recordings in a batch are analyzed:

1. Load all riff fingerprints from Convex (or from a local cache)
2. Run brute-force DTW matching: compare every new riff against every existing riff
3. Store match scores in the `riffMatches` table
4. Build a structured similarity report for the LLM, including:
   - Best riff matches per recording pair, with scores and feature breakdowns
   - Pre/post transcripts
   - Tempo and key information
5. Call the LLM with the report and ask it to group recordings into songs and suggest working titles
6. Apply the LLM's grouping decisions by assigning recordings to songs in Convex

Recordings that the LLM cannot confidently group are set to `ungrouped` for manual assignment.

---

## 9. Manual Corrections

### Reassign Recording

The AssignDropdown on each RecordingCard allows moving a recording to a different song or creating a new song. This triggers a Convex mutation that:

- Updates the recording's `songId`
- Sets state to `grouped`
- Logs the correction in the `corrections` table (from-song, to-song, timestamp)

### Rename Song

Inline edit on the song title. Triggers a Convex mutation on the `songs` table.

### Merge Songs

On the song page, a "Merge with..." action opens a dropdown of other songs. Selecting one triggers a Convex mutation that:

- Moves all recordings from the selected song to the current song
- Deletes the now-empty song document

### Dissolve Song

Deleting a song group sets all its recordings to `ungrouped` and removes the song document.

### Create Song

From the unsorted section, "Create new song" prompts for a title, creates a song document, and assigns the recording to it.

All corrections are logged for future model training.

---

## 10. Convex Queries and Mutations

### Queries (reactive subscriptions)

- **Dashboard query** — returns three lists: song groups with their takes, ungrouped recordings, and currently-processing recordings. The frontend subscribes to this; it updates live as the worker progresses.
- **Song detail query** — returns one song with all its takes, ordered by date. Subscribed when viewing a song page.
- **Processing status query** — returns all recordings in processing states. Used by the processing banner.

### Mutations (triggered by user actions)

- **Create recording** — inserts a new recording, deduplicates by hash
- **Update state** — patches a recording's state and optional metadata fields (called by the Python worker via HTTP actions)
- **Assign to song** — sets songId and state, logs correction if reassignment
- **Create song** — inserts a new song
- **Rename song** — patches song title
- **Merge songs** — moves recordings, deletes source song
- **Delete song** — ungroups recordings, deletes song
- **Store riffs** — batch insert riff documents for a recording
- **Undo trim** — clears cut points on a recording, stores original values in a separate field for potential restoration

### HTTP Actions (called by Python worker)

Two HTTP routes authenticated by a worker API key:

- **Update state** — receives recording ID, new state, and optional metadata. Runs the updateState mutation internally.
- **Store riffs** — receives recording ID and array of riff data. Runs the storeBatch mutation internally.

---

## 11. Deployment

### Docker Compose Services

| Service | Image/Build | Ports | Volumes |
|---|---|---|---|
| `caddy` | `caddy:2-alpine` | 80, 443 | Caddyfile, caddy_data |
| `sveltekit` | Built from `./sveltekit` | 3000 (internal) | audio_data |
| `python-worker` | Built from `./python-worker` | None | audio_data |
| `pocket-id` | `stonith404/pocket-id` | 8080 (internal) | pocket_id_data |

### Volumes

- `audio_data` — shared between SvelteKit and Python worker. Contains incoming and processed audio files.
- `caddy_data` — Caddy's TLS certificates and state
- `pocket_id_data` — Pocket-ID's database and configuration

### Environment Variables

Managed via a `.env` file:

- `DOMAIN` — public domain for Caddy
- `CONVEX_URL` / `PUBLIC_CONVEX_URL` — Convex deployment URLs
- `CONVEX_HTTP_URL` — Convex HTTP actions URL (for Python worker)
- `PI_API_KEY` — shared secret for Pi uploads
- `WORKER_API_KEY` — shared secret for Python worker to Convex HTTP actions
- `POCKET_ID_CLIENT_ID` / `POCKET_ID_CLIENT_SECRET` — OIDC credentials
- `SESSION_SECRET` — signing key for session cookies
- `LLM_API_KEY` / `LLM_BASE_URL` — for the grouping LLM

### Caddy Routing

- `/*` → SvelteKit (frontend + API)
- `/pocket-id/*` → Pocket-ID (strip prefix)

---

## 12. File Storage Layout

```
/data/audio/
  incoming/
    manifests/             ← JSON manifests for the Python worker
    {recordingId}.wav      ← raw uploads, deleted after processing
  processed/
    {recordingId}.flac         ← full normalized lossless (permanent)
    {recordingId}_pre.opus     ← pre-song segment
    {recordingId}_song.opus    ← trimmed song
    {recordingId}_post.opus    ← post-song segment
```

The FLAC is never deleted. It serves as the source of truth for re-trimming, full-quality downloads, and future reprocessing. The Opus files are derived artifacts that could be regenerated from the FLAC if needed.

---

## 13. Implementation Phases

| Phase | Deliverable | What it enables |
|---|---|---|
| **1** | Docker Compose skeleton with Caddy, SvelteKit shell, Pocket-ID | Working auth flow, empty dashboard |
| **2** | Convex schema, basic queries/mutations | Data layer ready |
| **3** | Upload endpoint, Pi integration | Files arrive on the server |
| **4** | Dashboard page with song groups, unsorted section, audio player | Browse and play recordings (manually added to Convex for testing) |
| **5** | Python worker: normalize → trim → encode | Processed audio files appear, state updates flow to frontend |
| **6** | Trim review and undo UI | Users can audit and correct trim decisions |
| **7** | Python worker: feature extraction, riff fingerprinting, DTW matching | Riff data populates in Convex |
| **8** | LLM grouping, song creation, auto-assignment | Recordings sort themselves into songs |
| **9** | Manual corrections: reassign, rename, merge, dissolve | Users can fix grouping errors |
| **10** | Pi upload script update for new endpoint | Full end-to-end flow from practice to webapp |

**Phases 1–4** produce a usable app for uploading and listening. **Phases 5–6** add automated processing with trim review. **Phases 7–8** add the intelligence. **Phase 9** adds the human-in-the-loop corrections. **Phase 10** closes the loop with the Pi.
