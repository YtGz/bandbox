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

### `sets` table

Stores metadata for set recordings (full rehearsal run-throughs, multi-song recordings).

- `title` — auto-generated from date (e.g., "April 7, 2026"), editable
- `notes` — optional free-text
- `recordedAt` — timestamp of when the set was recorded (used for date grouping and ordering)
- `createdAt` — timestamp

Index: `by_recorded_at` (date-ordered listing)

### `setMarkers` table

Timestamped markers within a set recording, linking to recognized songs. Empty in v1 — the data model is ready for future auto-detection once the fingerprint library is rich enough.

- `setId` — reference to parent set
- `songId` — optional reference to a matched song
- `label` — display name (matched song title, or manual label like "Doom Riff")
- `startSec` — timestamp within the set recording
- `endSec` — optional end timestamp
- `confidence` — 0–1 if auto-detected, null if manual
- `source` — `"manual"` or `"auto"`

Index: `by_set`

### `recordings` table

Uses a discriminated union on the `kind` field. A recording is either a **song recording** (single song/riff take, goes through the full analysis pipeline) or a **set recording** (multi-song run-through, normalized and encoded only).

#### Common fields (both kinds)

- `kind` — `"song"` or `"set"` (discriminator)
- `filename` — original filename from the USB stick
- `fileHash` — SHA-256, used for deduplication
- `uploadedAt` — timestamp
- `state` — processing state (see below)
- `pathFlac` — relative path to the full normalized FLAC file
- `durationSec` — total duration in seconds
- `processingFlags` — optional array of processing flags

#### Song recording fields (`kind: "song"`)

- `state` — one of: `uploading`, `normalizing`, `trimming`, `analyzing`, `grouped`, `ungrouped`, `reprocess`
- `songId` — optional reference to a song
- `pathSong` — relative path to the trimmed song segment (Opus)
- `pathPre` — relative path to the pre-song segment (Opus)
- `pathPost` — relative path to the post-song segment (Opus)
- `cutStartSec` — where the trim begins (seconds into the original)
- `cutEndSec` — where the trim ends
- `savedCutStartSec` — stored original cut start for trim undo/restore
- `savedCutEndSec` — stored original cut end for trim undo/restore
- `trimConfidence` — 0–1 score indicating how confident the trim detection was
- `trimMethod` — which detection method triggered (energy wall, rhythmic regularity, pitched content)
- `transcriptPre` — Whisper transcription of the pre-song segment
- `transcriptPost` — Whisper transcription of the post-song segment
- `tempo` — detected BPM
- `dominantKey` — detected key

#### Set recording fields (`kind: "set"`)

- `state` — one of: `uploading`, `normalizing`, `ready`
- `setId` — optional reference to a set
- `pathOpus` — relative path to the full Opus encode (no trimming)

Indexes: `by_hash` (deduplication), `by_kind_and_state` (query processing queue, scoped by kind), `by_song` (list takes per song), `by_set` (list recordings per set)

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

Authentication uses [oauth2-proxy](https://github.com/oauth2-proxy/oauth2-proxy) in front of SvelteKit, with any OIDC-compatible provider (e.g. [Pocket-ID](https://github.com/stonith404/pocket-id), Keycloak, Authentik). This keeps all auth logic out of the application — SvelteKit serves pages, oauth2-proxy decides who gets in.

### Why oauth2-proxy?

BandBox doesn't need per-user features. Every band member sees the same dashboard, edits the same songs, reviews the same recordings. The only question is: "Are you in the band?" The OIDC provider controls who can register. oauth2-proxy enforces the login gate. SvelteKit stays auth-free.

### How It Works

1. The reverse proxy forwards all traffic to oauth2-proxy
2. oauth2-proxy checks for a valid session cookie
3. If missing or expired, the user is redirected to the OIDC provider's login
4. After authentication, oauth2-proxy sets a secure cookie (30-day expiry) and proxies the request to SvelteKit
5. SvelteKit never sees unauthenticated requests
6. `/api/upload` is excluded from auth via `--skip-auth-route` (Pi uses API key authentication)

### OIDC Provider Setup

The standalone Docker Compose profile includes Pocket-ID, a self-hosted passkey/WebAuthn provider. Alternatively, point `OIDC_ISSUER_URL` at any existing OIDC provider.

Whichever provider you use:
1. Create an OIDC client with redirect URI `https://your-domain/oauth2/callback`
2. Set `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` in `.env`
3. If using an external provider, set `OIDC_ISSUER_URL` in `.env`

---

## 4. Frontend (SvelteKit + TailwindCSS)

### Page Structure

```
/ Dashboard (all songs + unsorted + sets)
/song/{id} Song detail (all takes)
/set/{id} Set detail (all recordings for a set)
/recording/{id} Recording detail (trim review, riff map)
/callback OIDC callback handler
```

### Dashboard (`/`)

The main view. Four sections, top to bottom:

**Processing banner** — only visible when recordings are being processed. Shows a pulsing animation and text like "Analyzing... 3 of 8 recordings remaining". Disappears automatically when all recordings reach a terminal state (`grouped`, `ungrouped`, or `ready`). This is reactive — driven by a Convex subscription to a query that filters recordings by processing states.

**Song groups** — each song is a collapsible card showing:

- Song title (editable inline)
- Take count and date of most recent take
- Inline audio player for the most recent take's `_song.opus`
- Expand to see all takes as a list

**Sets section** — set recordings grouped by date. Each date heading shows the recordings from that day:

- If one set that day: just the date as heading (e.g., "April 7, 2026"), with editable title
- If multiple sets that day: "April 7, 2026 — Set 1", "Set 2", etc., ordered by timestamp
- Each set card shows: title, duration, inline audio player for the full Opus encode
- Clicking a set opens the set detail page

**Unsorted section** — appears at the bottom when recordings exist in `ungrouped` state. Each unsorted recording shows:

- Original filename
- If still processing: a pulsing state badge (`normalizing`, `trimming`, `analyzing`)
- If analysis complete but unmatched: an "Assign to song" dropdown listing all existing songs plus a "Create new song" option
- Inline audio player

All four sections update in real time via Convex subscriptions. When the Python worker groups a recording, it moves from "Unsorted" into its song group without the user doing anything. Set recordings appear in the Sets section as soon as they reach `ready` state.

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
- **Transcript** — if the pre-song speech was transcribed, shown as a quote (e.g., _"okay from the top, doom riff"_)
- **Trim confidence indicator**:
  - High confidence (≥85%): green checkmark, no special treatment
  - Medium confidence (60–84%): amber warning icon with the text "Xsec trimmed"
  - Low confidence (<60%): red warning icon with the text "Xsec trimmed — review recommended"
- **Trim review controls** (see Section 5 below)
- **Actions**: Download FLAC, Move to different song

### Set Page (`/set/{id}`)

Shows all recordings for one set (typically one, but could be multiple if the band recorded the same set twice in a day). Header area:

- Back link to dashboard
- Set title with inline edit (e.g., "April 7, 2026" → "Pre-show warmup")
- Notes field (editable)

Each recording is a card containing:

- **Audio player** — plays the full Opus encode
- **Duration** — total length
- **Download FLAC** button

**Song markers (v2)** — when `setMarkers` are present, the audio player shows clickable timestamp markers on the seek bar. Each marker displays the matched song title (or manual label). Clicking a marker seeks to that timestamp. Markers with `source: "auto"` show a confidence indicator; markers with `source: "manual"` show a pin icon. A "+" button allows adding manual markers at the current playback position.

In v1, this section is absent — sets are simply playable recordings with no internal structure.

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
- **SetCard** — a set recording on the dashboard or set page. Contains the audio player, duration, title, and download button. Simpler than RecordingCard — no trim controls or riff maps.
- **SetDateGroup** — groups SetCards under a date heading on the dashboard. Handles the "Set 1, Set 2" numbering when multiple sets share a date.

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

### Set Classification

Before any processing, the worker checks the recording's duration. If the raw WAV exceeds the **set threshold** (default: 17 minutes, configurable), the recording is classified as `kind: "set"` and follows the simplified set pipeline. Otherwise it follows the full song pipeline.

The duration check happens on the raw WAV file before normalization — it's a cheap `ffprobe` call.

### Set Pipeline (kind: "set")

1. **Normalize** — loudness normalize to -14 LUFS, encode as FLAC
2. **Encode** — encode the full recording as a single Opus file (128k). No trimming, no segmentation.
3. **Assign to set** — find or create a `sets` document for the recording date. If a set already exists for that date, the recording is added to it. Title is auto-generated from the date (e.g., "April 7, 2026").
4. **Clean up** — delete the original WAV, set state to `ready`

Set recordings skip the entire trim → segment → fingerprint → match pipeline. They exist for playback, not analysis.

### Song Pipeline (kind: "song")

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

### Reclassify Recording

If the duration threshold misclassifies a recording (e.g., a 20-minute epic that's actually one song, or a 15-minute set that slipped under the threshold), the user can reclassify it from the UI. This triggers a Convex mutation that changes the `kind` field and either:

- **Song → Set**: clears song-specific fields, creates/assigns a set, sets state to `ready`
- **Set → Song**: clears set-specific fields, sets state to `reprocess` so the worker picks it up for the full song pipeline

### Rename Set

Inline edit on the set title. Triggers a Convex mutation on the `sets` table.

---

## 10. Convex Queries and Mutations

### Queries (reactive subscriptions)

- **Dashboard query** — returns four lists: song groups with their takes, sets grouped by date, ungrouped recordings, and currently-processing recordings. The frontend subscribes to this; it updates live as the worker progresses.
- **Song detail query** — returns one song with all its takes, ordered by date. Subscribed when viewing a song page.
- **Set detail query** — returns one set with all its recordings and any set markers, ordered by timestamp. Subscribed when viewing a set page.
- **Processing status query** — returns all recordings in processing states (both song and set pipelines). Used by the processing banner.

### Mutations (triggered by user actions)

- **Create recording** — inserts a new recording, deduplicates by hash. The `kind` field is set by the worker after duration classification.
- **Update state** — patches a recording's state and optional metadata fields (called by the Python worker via HTTP actions)
- **Assign to song** — sets songId and state, logs correction if reassignment
- **Create song** — inserts a new song
- **Rename song** — patches song title
- **Merge songs** — moves recordings, deletes source song
- **Delete song** — ungroups recordings, deletes song
- **Store riffs** — batch insert riff documents for a recording
- **Undo trim** — clears cut points on a recording, stores original values in a separate field for potential restoration
- **Create set** — inserts a new set with auto-generated title from date
- **Rename set** — patches set title
- **Assign to set** — sets setId on a set recording, creates set if needed
- **Reclassify recording** — changes `kind` between `"song"` and `"set"`, clears/resets fields accordingly
- **Add set marker** — inserts a manual marker into `setMarkers` (v2)
- **Remove set marker** — deletes a marker from `setMarkers` (v2)

### HTTP Actions (called by Python worker)

Three HTTP routes authenticated by a worker API key:

- **Update state** — receives recording ID, new state, and optional metadata. Runs the updateState mutation internally.
- **Store riffs** — receives recording ID and array of riff data. Runs the storeBatch mutation internally.
- **Assign to set** — receives recording ID and recording date. Finds or creates a set for that date, assigns the recording. Runs the assignToSet mutation internally.

---

## 11. Deployment

BandBox supports two deployment modes via Docker Compose profiles:

**Standalone** — includes everything (Caddy reverse proxy + Pocket-ID for auth):
```bash
docker compose --profile standalone up -d
```

**Bring-your-own infrastructure** — only the core services (you provide your own reverse proxy and OIDC provider):
```bash
docker compose up -d
```

This is useful when you already have Caddy (or nginx/Traefik) running on the host, and/or an existing Pocket-ID (or Keycloak/Authentik) instance with users you want to keep.

### Docker Compose Services

| Service | Profile | Image/Build | Ports | Volumes |
|---|---|---|---|---|
| `sveltekit` | *core* | Built from `./` | 3000 (internal) | audio_data |
| `python-worker` | *core* | Built from `./python-worker` | None | audio_data |
| `oauth2-proxy` | *core* | `oauth2-proxy:v7` | 4180 (internal) | None |
| `caddy` | `standalone` | `caddy:2-alpine` | 80, 443 | Caddyfile, caddy_data, caddy_config |
| `pocket-id` | `standalone` | `stonith404/pocket-id` | 8080 (internal) | pocket_id_data |

### Volumes

- `audio_data` — shared between SvelteKit and Python worker. Contains incoming and processed audio files.
- `caddy_data` — Caddy's TLS certificates and state (standalone only)
- `caddy_config` — Caddy's runtime configuration (standalone only)
- `pocket_id_data` — Pocket-ID's database and configuration (standalone only)

### Environment Variables

Managed via a `.env` file (see `.env.example`):

- `DOMAIN` — public domain for the deployment
- `CONVEX_URL` / `PUBLIC_CONVEX_URL` — Convex deployment URLs
- `PI_API_KEY` — shared secret for Pi uploads
- `WORKER_API_KEY` — shared secret for Python worker → Convex HTTP actions
- `OIDC_ISSUER_URL` — OIDC issuer URL (defaults to bundled Pocket-ID; set when using an external provider)
- `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` — OIDC client credentials (for oauth2-proxy)
- `COOKIE_SECRET` — 32-byte base64 string for oauth2-proxy cookie encryption
- `LLM_API_KEY` / `LLM_BASE_URL` — for the grouping LLM

### Reverse Proxy Routing

oauth2-proxy acts as both the auth gate *and* the upstream proxy to SvelteKit. This simplifies reverse proxy configuration — everything goes to oauth2-proxy, which handles authentication and forwards to SvelteKit internally.

**BYO reverse proxy** — the minimal config is a single `reverse_proxy` to oauth2-proxy:

```
bandbox.example.com {
    reverse_proxy oauth2-proxy:4180
}
```

oauth2-proxy is configured with `--skip-auth-route=/api/upload` so the Pi's API-key-authenticated uploads bypass OIDC login. All other requests require a valid session cookie.

**Standalone profile** — the bundled Caddyfile adds a route for `/pocket-id/*` to the Pocket-ID container. Everything else goes to oauth2-proxy.

If your existing OIDC provider runs on a separate domain (not behind the same reverse proxy), no special routing is needed — oauth2-proxy talks to it directly via `OIDC_ISSUER_URL`.

---

## 12. File Storage Layout

```
/data/audio/
  incoming/
    manifests/             ← JSON manifests for the Python worker
    {recordingId}.wav      ← raw uploads, deleted after processing
  processed/
    songs/
      {recordingId}.flac         ← full normalized lossless (permanent)
      {recordingId}_pre.opus     ← pre-song segment
      {recordingId}_song.opus    ← trimmed song
      {recordingId}_post.opus    ← post-song segment
    sets/
      {recordingId}.flac         ← full normalized lossless (permanent)
      {recordingId}.opus         ← full recording in Opus (no trimming)
```

Song and set recordings are stored in separate subdirectories for clarity. The FLAC is never deleted. It serves as the source of truth for re-trimming, full-quality downloads, and future reprocessing. The Opus files are derived artifacts that could be regenerated from the FLAC if needed.

---

## 13. Implementation Phases

| Phase  | Deliverable                                                                | What it enables                                                   |
| ------ | -------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **1**  | Docker Compose with Caddy, SvelteKit, Pocket-ID, oauth2-proxy              | Working auth flow, empty dashboard                                |
| **2**  | Convex schema, basic queries/mutations                                     | Data layer ready                                                  |
| **3**  | Upload endpoint, Pi integration                                            | Files arrive on the server                                        |
| **4**  | Dashboard page with song groups, sets section, unsorted, audio player      | Browse and play recordings (manually added to Convex for testing) |
| **5**  | Python worker: classify → normalize → trim → encode (song + set pipelines) | Processed audio files appear, sets auto-grouped by date           |
| **6**  | Trim review and undo UI                                                    | Users can audit and correct trim decisions                        |
| **7**  | Python worker: feature extraction, riff fingerprinting, DTW matching       | Riff data populates in Convex                                     |
| **8**  | LLM grouping, song creation, auto-assignment                               | Recordings sort themselves into songs                             |
| **9**  | Manual corrections: reassign, rename, merge, dissolve, reclassify          | Users can fix grouping errors and song/set misclassification      |
| **10** | Pi upload script update for new endpoint                                   | Full end-to-end flow from practice to webapp                      |
| **11** | Set marker UI + auto-detection from fingerprint library (v2)               | Timestamped song navigation within set recordings                 |

**Phases 1–4** produce a usable app for uploading and listening. **Phase 5** adds automated processing with set classification. **Phase 6** adds trim review. **Phases 7–8** add the intelligence. **Phase 9** adds the human-in-the-loop corrections. **Phase 10** closes the loop with the Pi. **Phase 11** is a future enhancement once the fingerprint library is mature.
