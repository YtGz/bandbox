# 🎸 BandBox

> Plug in a USB stick after practice. Get an organized library of songs and takes.

BandBox turns hours of raw rehearsal recordings into a searchable, playable collection — automatically. Silence trimming, riff-based song identification, and a real-time web UI for the whole band. Built for heavily distorted genres where standard music analysis tools give up.

## ✨ How It Works

```
🎤 Record → 🔌 Plug in USB → 🧠 Auto-process → 🎵 Browse your songs
```

1. **Record** your practice session to a USB stick (from your mixer, interface, or portable recorder)
2. **Plug** the stick into a Raspberry Pi running BandBox
3. **Relax** — BandBox copies, uploads, normalizes, trims silence, extracts riff fingerprints, and groups recordings into songs
4. **Review** — open the web UI, listen to takes, audit trim decisions, and fix any groupings

Everything updates in real time. When the worker finishes processing a recording, it appears in your browser without refreshing.

## 🏗️ Architecture

```
                  ┌─────────────────────────────────────────────┐
                  │              Docker Compose                  │
USB Stick         │                                             │
  │               │  Caddy ──┬── /api/upload ──→ SvelteKit ◄──┤──── Convex Cloud
  ▼               │          │                     ▲    │      │      (database)
Raspberry Pi ─────┼── Wi-Fi ─┤── /pocket-id/* ──→ Pocket-ID   │
(bandbox.py)      │          │                                 │
                  │          ├── /oauth2/* ──→ oauth2-proxy    │
                  │          │                                 │
                  │          └── /* ──→ oauth2-proxy ──→ SvelteKit
                  │                                     │      │
                  │                          Python Worker      │
                  │                     (audio processing)      │
                  └─────────────────────────────────────────────┘
```

| Service | Role |
| --- | --- |
| 🌐 **Caddy** | Reverse proxy, automatic HTTPS |
| 🎨 **SvelteKit** | Frontend + upload API (Bun) |
| 🐍 **Python Worker** | Normalize, trim, encode, fingerprint, match |
| 🔑 **Pocket-ID** | Passkey/WebAuthn login (OIDC provider) |
| 🛡️ **oauth2-proxy** | Auth gate — keeps SvelteKit auth-free |
| ☁️ **Convex** | Real-time cloud database |

## 🧠 Audio Intelligence

Standard music analysis chokes on heavy distortion. BandBox uses features that survive it.

**Riff Segmentation** — agglomerative clustering on chroma features. Finds structural boundaries, merges short segments (<3s), splits long ones (>60s).

**Fingerprinting** — four features per riff, weighted by reliability:

| Feature | Weight | What it captures |
| --- | ---: | --- |
| 🥁 **Groove** | 35% | Rhythmic pattern via inter-onset intervals — instrument-agnostic |
| 🎯 **Drums** | 25% | Kick/snare patterns via low-frequency autocorrelation — most consistent across takes |
| 🎵 **Contour** | 25% | Spectral centroid as pitch proxy (pYIN fails on distortion) — 10 pts/sec for DTW |
| 🎸 **Spectral contrast** | 15% | Tonal character per frequency band — captures the distortion signature |

**Matching** — DTW on contours, cosine similarity on the rest. Tempo penalty >15% BPM difference (catches "same riff, different tempo" without over-penalizing natural drift).

## 📦 Data Lifecycle

Recordings flow through four stages. Each has clear ownership and retention rules.

```
💾 USB Stick ──→ 📂 Pi Staging ──→ 📥 Server Incoming ──→ 🎵 Processed Files
   (keep)         (temporary)        (temporary)            (permanent)
```

| Stage | What happens | Retention |
| --- | --- | --- |
| **💾 USB Stick** | Never modified by BandBox. Read-only mount. Band decides when to format. | ♾️ Band's responsibility |
| **📂 Pi Staging** | New files copied here, deleted after server confirms upload. ~29 GB buffer. | 🗑️ Auto-deleted after upload |
| **📥 Server Incoming** | WAV received via HTTP, manifest created for worker. | 🗑️ Deleted after processing |
| **🎵 Processed** | FLAC master + Opus segments (song, pre, post). Metadata in Convex. | ♾️ FLAC never deleted |

**Resilience:**

- 🔌 **Lost your Pi?** Plug the USB into a new one. Duplicates are skipped by SHA-256 hash.
- 💥 **USB stick died?** FLAC masters are safe on the server.
- 💽 **Server disk full?** Opus files can be regenerated from FLACs.
- 🔄 **Want to reprocess?** The FLAC is always there.

## 🚀 Deployment

### Prerequisites

- A server with [Docker](https://docs.docker.com/engine/install/) and [Docker Compose](https://docs.docker.com/compose/install/)
- A domain name pointed at your server
- A [Convex](https://www.convex.dev/) account

### 1. Configure

```bash
cp .env.example .env
# Fill in: DOMAIN, PUBLIC_CONVEX_URL, PI_API_KEY, WORKER_API_KEY
# Generate a cookie secret:
openssl rand -base64 32
# Leave OIDC fields empty for now
```

### 2. Launch

```bash
docker compose up -d
```

### 3. Set up authentication

Open `https://your-domain/pocket-id` and create your admin account (passkey). Then:

1. Go to **OIDC Clients** → **Create new client**
2. Set redirect URI to `https://your-domain/oauth2/callback`
3. Copy the **Client ID** and **Client Secret** into `.env`
4. `docker compose restart oauth2-proxy`

### 4. Invite the band

Each member opens `https://your-domain/pocket-id` and registers a passkey. No passwords — just biometrics or a hardware key. Done.

### 5. Set up the Pi

Follow the [Pi Setup Guide](pi/README.md) to turn a Pwnagotchi into your recording uploader.

## 🛠️ Development

```bash
# Install
bun install

# Dev server (two terminals)
bun run dev          # SvelteKit
bunx convex dev      # Convex
```

| Command | Description |
| --- | --- |
| `bun run dev` | Start development server |
| `bun run build` | Build for production |
| `bun run check` | Type-check the project |
| `bun run lint` | Lint and format check |
| `bun run format` | Auto-format all files |
| `bun run test` | Run unit tests |

## 📖 Documentation

- **[Audio Analysis Deep Dive](docs/AUDIO_ANALYSIS.md)** — How BandBox recognizes riffs through distortion, blast beats, and alternate tunings
- **[Pi Setup Guide](pi/README.md)** — Pwnagotchi setup, Arch Linux ARM, e-ink display
- **[Implementation Guide](docs/IMPLEMENTATION.md)** — Full technical spec, schema, audio pipeline details

## 📄 License

[MIT-0](LICENSE.md)
