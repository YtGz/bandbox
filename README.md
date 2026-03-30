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
- **Pocket-ID** — Self-hosted OIDC authentication with passkeys
- **Caddy** — Reverse proxy with automatic HTTPS

All services run as Docker containers on a single-board computer.

## Audio Intelligence

**Riff Segmentation** — agglomerative clustering on chroma features. Finds structural boundaries, merges segments shorter than 3s, splits anything over 60s.

**Fingerprinting** — four features per riff:

- **Groove** (35% weight) — inter-onset interval histogram. Captures the rhythmic pattern regardless of what instruments play it.
- **Drums** (25%) — low-frequency onset autocorrelation. Isolates kick/snare patterns, which are the most consistent element across takes.
- **Contour** (25%) — spectral centroid as pitch proxy, because pYIN chokes on heavy distortion. Downsampled to 10pts/sec for efficient DTW.
- **Spectral contrast** (15%) — tonal character per frequency band. Captures the distortion/tone signature.

**Matching** — brute-force DTW on contours, cosine similarity on everything else. Tempo penalty kicks in when BPM differs >15% (catches the "same riff, different tempo" case without over-penalizing natural drift between takes).

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

## Documentation

- [Implementation Guide](docs/IMPLEMENTATION.md) — Full technical spec and architecture details

## License

[MIT-0](LICENSE.md)
