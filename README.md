# BandBox

Turn hours of raw practice recordings into an organized library of songs and takes. Automatic silence trimming, riff-based song identification, and a web UI for your whole band. Built for heavily distorted genres where standard music analysis tools fail.

## How It Works

1. **Record** — Plug a USB stick into the Raspberry Pi after practice
2. **Process** — BandBox transfers, normalizes, trims silence, and extracts riff fingerprints
3. **Identify** — Recordings are automatically grouped into songs using audio fingerprinting and LLM-assisted matching
4. **Review** — Browse songs, listen to takes, audit trim decisions, and correct groupings through a reactive web interface

## Architecture

- **SvelteKit** — Frontend and upload API, running on Bun
- **Convex** — Realtime cloud database with reactive subscriptions
- **Python Worker** — Audio processing pipeline (normalize, trim, encode, fingerprint)
- **Pocket-ID** — Self-hosted OIDC provider with passkey/WebAuthn authentication
- **oauth2-proxy** — Authentication gate in front of SvelteKit (no auth code in the app)
- **Caddy** — Reverse proxy with automatic HTTPS

All services run as Docker containers on a single-board computer. The Pi upload endpoint (`/api/upload`) uses API key auth and bypasses oauth2-proxy.

## Audio Intelligence

**Riff Segmentation** — agglomerative clustering on chroma features. Finds structural boundaries, merges segments shorter than 3s, splits anything over 60s.

**Fingerprinting** — four features per riff:

- **Groove** (35% weight) — inter-onset interval histogram. Captures the rhythmic pattern regardless of what instruments play it.
- **Drums** (25%) — low-frequency onset autocorrelation. Isolates kick/snare patterns, which are the most consistent element across takes.
- **Contour** (25%) — spectral centroid as pitch proxy, because pYIN chokes on heavy distortion. Downsampled to 10pts/sec for efficient DTW.
- **Spectral contrast** (15%) — tonal character per frequency band. Captures the distortion/tone signature.

**Matching** — brute-force DTW on contours, cosine similarity on everything else. Tempo penalty kicks in when BPM differs >15% (catches the "same riff, different tempo" case without over-penalizing natural drift between takes).

## Data Lifecycle

Recordings flow through four stages. Each stage has clear ownership and retention rules.

```
USB Stick ──copy──▸ Pi Staging ──upload──▸ Server ──process──▸ Convex + Processed Files
 (keep)           (temporary)           (incoming)            (permanent)
```

### 1. USB Stick (band's portable archive)

The USB stick is never modified by BandBox. Files are only read and copied off. The band decides when to format or reuse a stick. Re-inserting a previously copied stick is harmless — the server deduplicates by SHA-256 hash, so nothing gets uploaded twice.

**Retention:** Forever. This is the band's responsibility and their only pre-upload backup.

### 2. Pi Staging (`~/staging/`)

The Pi copies new files from the USB stick to a local staging directory. Files wait here until they've been successfully uploaded to the server. Once the server confirms receipt (returns `accepted`), the local copy is deleted to free up space.

**Retention:** Temporary. Deleted automatically after confirmed upload. The Pi can buffer ~29 GB (roughly 140 songs at 200 MB each) while offline.

### 3. Server Incoming (`/data/audio/incoming/`)

The SvelteKit upload endpoint receives WAV files via HTTP, creates a recording document in Convex, and drops a manifest for the Python worker. The original WAV is deleted after processing.

**Retention:** Temporary. Deleted after the Python worker finishes processing.

### 4. Server Processed (`/data/audio/processed/`)

The Python worker produces four files per recording:

| File | Format | Purpose |
| --- | --- | --- |
| `{id}.flac` | FLAC | Full normalized lossless master — never deleted |
| `{id}_song.opus` | Opus 128k | Trimmed song segment for playback |
| `{id}_pre.opus` | Opus 128k | Pre-song segment (chatter, tuning, count-in) |
| `{id}_post.opus` | Opus 128k | Post-song segment (chatter, noodling) |

The FLAC is the permanent source of truth. Opus files are derived and can be regenerated from the FLAC at any time. All metadata (trim points, riff fingerprints, song groupings) lives in Convex.

**Retention:** Permanent. The FLAC is never deleted.

### What this means in practice

- **Lost your Pi?** Plug the USB stick into a new one. Everything re-uploads, server skips duplicates.
- **USB stick died?** No problem if the server already has the files. The FLAC masters are safe.
- **Server disk full?** The Opus files can be regenerated from FLACs. Only the FLACs are essential.
- **Want to reprocess?** The FLAC is always there. Re-trim, re-analyze, re-fingerprint at any time.

## Development

### Prerequisites

- [Bun](https://bun.sh/) (v1.3+)
- [Convex](https://www.convex.dev/) account

### Setup

```bash
bun install
```

### Dev Server

```bash
# Terminal 1: SvelteKit
bun run dev

# Terminal 2: Convex
bunx convex dev
```

### Commands

| Command          | Description              |
| ---------------- | ------------------------ |
| `bun run dev`    | Start development server |
| `bun run build`  | Build for production     |
| `bun run check`  | Type-check the project   |
| `bun run lint`   | Lint and format check    |
| `bun run format` | Auto-format all files    |
| `bun run test`   | Run unit tests           |

## Deployment

### Prerequisites

- A server with [Docker](https://docs.docker.com/engine/install/) and [Docker Compose](https://docs.docker.com/compose/install/)
- A domain name pointed at your server (for Caddy's automatic HTTPS)
- A [Convex](https://www.convex.dev/) account and deployment

### 1. Configure environment

```bash
cp .env.example .env
```

Fill in the values — at minimum: `DOMAIN`, `PUBLIC_CONVEX_URL`, `PI_API_KEY`, `WORKER_API_KEY`, and `COOKIE_SECRET`. Leave the OIDC fields empty for now.

Generate a cookie secret:

```bash
openssl rand -base64 32
```

### 2. Start the stack

```bash
docker compose up -d
```

### 3. Set up Pocket-ID

Open `https://your-domain/pocket-id` in your browser. On first launch:

1. Create your admin account (register a passkey)
2. Go to OIDC Clients → Create a new client
3. Set the redirect URI to `https://your-domain/oauth2/callback`
4. Copy the **Client ID** and **Client Secret**

### 4. Connect oauth2-proxy to Pocket-ID

Paste the OIDC credentials into your `.env` file:

```bash
OIDC_CLIENT_ID=your-client-id
OIDC_CLIENT_SECRET=your-client-secret
```

Restart oauth2-proxy to pick up the new config:

```bash
docker compose restart oauth2-proxy
```

### 5. Register band members

Each band member opens `https://your-domain/pocket-id` and registers a passkey on their phone or laptop. No passwords — just biometrics or a hardware key.

After registering, they can access the BandBox dashboard at `https://your-domain/`.

## Documentation

- [Pi Setup Guide](pi/README.md) — How to set up the Pwnagotchi as a recording uploader
- [Implementation Guide](docs/IMPLEMENTATION.md) — Full technical spec and architecture details

## License

[MIT-0](LICENSE.md)
