#!/usr/bin/env python3
"""
 ╔═══════════════════════════════════════╗
 ║           ♪ BandBox v1.0 ♪           ║
 ║   Band practice recording uploader   ║
 ║   Pi Zero 2W + PiSugar 3 + E-ink    ║
 ╚═══════════════════════════════════════╝

 Plug in USB → copies new files → uploads to BandBox server
"""

import hashlib
import json
import logging
import os
import random
import signal
import socket
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# ════════════════════════════════════════════════════════════
#  CONFIGURATION — edit these to match your setup
# ════════════════════════════════════════════════════════════

STAGING_DIR = Path.home() / "staging"
MOUNT_POINT = Path("/mnt/bandbox-usb")
STATE_DIR = Path.home() / ".bandbox"
JOURNAL_PATH = STATE_DIR / "uploaded.json"

SERVER_URL = os.environ.get("BANDBOX_SERVER_URL", "https://bandbox.example.com")
API_KEY = os.environ.get("BANDBOX_API_KEY", "change-me")

UPLOAD_INTERVAL = 120          # seconds between upload sweeps
UPLOAD_TIMEOUT = 300           # seconds per file (200 MB over decent Wi-Fi)
UPLOAD_RETRIES = 3             # attempts per file
MIN_FREE_SPACE_MB = 5000       # warn when SD card drops below this
PISUGAR_SOCKET = "/tmp/pisugar-server.sock"
LOG_FILE = Path.home() / "bandbox.log"

# Display model: "V4" or "V3" — check your Waveshare version
DISPLAY_VERSION = "V4"

# File extensions to pick up from USB
AUDIO_EXTENSIONS = {".wav", ".WAV"}


# ════════════════════════════════════════════════════════════
#  LOGGING
# ════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bandbox")


# ════════════════════════════════════════════════════════════
#  FONTS
# ════════════════════════════════════════════════════════════

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(name, size):
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except OSError:
        return ImageFont.load_default()


font_sm = _font("DejaVuSans.ttf", 10)
font_md = _font("DejaVuSans.ttf", 13)
font_lg = _font("DejaVuSans-Bold.ttf", 14)
font_title = _font("DejaVuSans-Bold.ttf", 12)


# ════════════════════════════════════════════════════════════
#  PERSONALITY — messages for each state
# ════════════════════════════════════════════════════════════

MESSAGES = {
    "idle": [
        "Waiting for the drop...",
        "Ready to jam!",
        "Plug in some tunes!",
        "Gimme those tracks!",
        "Standing by...",
    ],
    "usb_found": [
        "Ooh, fresh tracks!",
        "New music incoming!",
        "Let's hear what you got!",
    ],
    "hashing": [
        "Checking signatures...",
        "Reading the liner notes...",
        "Scanning the setlist...",
    ],
    "copying": [
        "Dubbing the tapes...",
        "Sampling your jams...",
        "Ripping tracks...",
    ],
    "copy_done": [
        "Tracks secured!",
        "Got the goods!",
        "Nailed it!",
    ],
    "copy_none": [
        "Already got these.",
        "Nothing new here.",
        "All caught up!",
    ],
    "uploading": [
        "Beaming to the server...",
        "Sharing the love...",
        "Sending to the band...",
    ],
    "upload_done": [
        "Band has the tracks!",
        "Shared with the crew!",
        "Mission complete!",
    ],
    "upload_partial": [
        "Some tracks sent!",
        "Partially synced.",
    ],
    "no_wifi": [
        "No signal, vibing...",
        "Will upload later.",
        "Offline & chill.",
    ],
    "error": [
        "Hit a sour note...",
        "Something's off-key.",
    ],
    "low_battery": [
        "Running on fumes...",
        "Feed me electrons!",
    ],
    "low_space": [
        "Getting cramped...",
        "SD card filling up!",
    ],
}


def msg(key):
    return random.choice(MESSAGES.get(key, ["..."]))


# ════════════════════════════════════════════════════════════
#  HASH JOURNAL — remember what's been uploaded
# ════════════════════════════════════════════════════════════


class HashJournal:
    """Tracks SHA-256 hashes of files confirmed by the server."""

    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError):
                log.warning("Corrupt journal, starting fresh")
                self._data = {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.rename(self.path)

    def contains(self, file_hash):
        return file_hash in self._data

    def add(self, file_hash):
        self._data[file_hash] = datetime.now(timezone.utc).isoformat()
        self._save()

    def __len__(self):
        return len(self._data)


# ════════════════════════════════════════════════════════════
#  PISUGAR 3 BATTERY
# ════════════════════════════════════════════════════════════


def _pisugar(command):
    """Send a command to pisugar-server via unix socket."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(PISUGAR_SOCKET)
        s.sendall(f"{command}\n".encode())
        resp = s.recv(256).decode().strip()
        s.close()
        return resp.split(":", 1)[1].strip() if ":" in resp else resp
    except Exception:
        return None


def get_battery():
    """Return (percentage:int, charging:bool)."""
    raw_pct = _pisugar("get battery")
    raw_chg = _pisugar("get battery_charging")
    try:
        pct = max(0, min(100, int(float(raw_pct))))
    except (TypeError, ValueError):
        pct = -1
    charging = str(raw_chg).lower() == "true"
    return pct, charging


# ════════════════════════════════════════════════════════════
#  NETWORK HELPERS
# ════════════════════════════════════════════════════════════


def has_internet():
    try:
        subprocess.run(
            ["ping", "-c", "1", "-W", "2", "8.8.8.8"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def wifi_name():
    try:
        r = subprocess.run(
            ["iwgetid", "-r"], capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
#  HASHING
# ════════════════════════════════════════════════════════════


def sha256_file(filepath, buf_size=65536):
    """Stream SHA-256 of a file without loading it into memory."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(buf_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ════════════════════════════════════════════════════════════
#  DISK SPACE
# ════════════════════════════════════════════════════════════


def free_space_mb():
    """Free space on the partition containing STAGING_DIR, in MB."""
    try:
        st = os.statvfs(STAGING_DIR)
        return (st.f_bavail * st.f_frsize) // (1024 * 1024)
    except OSError:
        return -1


# ════════════════════════════════════════════════════════════
#  USB DETECTION & FILE COPY
# ════════════════════════════════════════════════════════════


def find_usb_partition():
    """Return first USB mass-storage partition like /dev/sda1, or None."""
    devs = sorted(Path("/dev").glob("sd[a-z][0-9]"))
    return str(devs[0]) if devs else None


def mount_usb(device):
    MOUNT_POINT.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["mount", "-o", "ro", device, str(MOUNT_POINT)], capture_output=True,
    )
    return r.returncode == 0


def unmount_usb():
    subprocess.run(["sync"], capture_output=True)
    subprocess.run(["umount", str(MOUNT_POINT)], capture_output=True)


def find_audio_files():
    """Return list of audio files on the mounted USB."""
    return [
        f for f in MOUNT_POINT.rglob("*")
        if f.is_file() and f.suffix in AUDIO_EXTENSIONS
    ]


# ════════════════════════════════════════════════════════════
#  SERVER UPLOAD
# ════════════════════════════════════════════════════════════


def upload_file(filepath, file_hash):
    """
    Upload one file to the BandBox server.
    Returns 'accepted', 'duplicate', or 'error'.
    """
    import urllib.request
    import urllib.error

    url = f"{SERVER_URL.rstrip('/')}/api/upload"

    # Build multipart form data manually (no requests dependency)
    boundary = f"----BandBox{int(time.time()*1000)}"
    body_parts = []

    # hash field
    body_parts.append(f"--{boundary}".encode())
    body_parts.append(b'Content-Disposition: form-data; name="hash"')
    body_parts.append(b"")
    body_parts.append(file_hash.encode())

    # file field
    body_parts.append(f"--{boundary}".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"'
        .encode()
    )
    body_parts.append(b"Content-Type: audio/wav")
    body_parts.append(b"")
    body_parts.append(filepath.read_bytes())

    body_parts.append(f"--{boundary}--".encode())
    body_parts.append(b"")

    body = b"\r\n".join(body_parts)

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Api-Key": API_KEY,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            return data.get("status", "accepted")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return "duplicate"
        log.error("Upload HTTP error %d: %s", e.code, e.reason)
        return "error"
    except Exception as e:
        log.error("Upload failed: %s", e)
        return "error"


def upload_with_retry(filepath, file_hash):
    """Upload with exponential backoff. Returns 'accepted', 'duplicate', or 'error'."""
    for attempt in range(1, UPLOAD_RETRIES + 1):
        result = upload_file(filepath, file_hash)
        if result in ("accepted", "duplicate"):
            return result
        if attempt < UPLOAD_RETRIES:
            wait = 2 ** attempt
            log.warning("Upload attempt %d failed, retrying in %ds", attempt, wait)
            time.sleep(wait)
    return "error"


# ════════════════════════════════════════════════════════════
#  E-INK DISPLAY
# ════════════════════════════════════════════════════════════

WIDTH, HEIGHT = 250, 122  # landscape


class Display:
    """Manages the Waveshare 2.13" e-ink display."""

    def __init__(self):
        self.epd = None
        self.image = Image.new("1", (WIDTH, HEIGHT), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.partial_ready = False
        self._init_hw()

    # ── hardware init ──────────────────────────────────────

    def _init_hw(self):
        try:
            if DISPLAY_VERSION == "V4":
                from waveshare_epd import epd2in13_V4 as drv
            else:
                from waveshare_epd import epd2in13_V3 as drv
            self.epd = drv.EPD()
            self.epd.init()
            self.epd.Clear(0xFF)
            log.info("E-ink display ready (%s)", DISPLAY_VERSION)
        except Exception as e:
            log.warning("Display not available: %s (saving PNGs instead)", e)

    # ── low-level refresh ──────────────────────────────────

    def refresh(self, full=True):
        if self.epd is None:
            self.image.save("/tmp/bandbox-screen.png")
            return
        try:
            buf = self.epd.getbuffer(self.image)
            if full or not self.partial_ready:
                self.epd.display(buf)
                self.epd.displayPartBaseImage(buf)
                self.partial_ready = True
            else:
                self.epd.displayPartial(buf)
        except Exception as e:
            log.error("Display refresh failed: %s", e)

    def off(self):
        if self.epd:
            try:
                self.epd.sleep()
            except Exception:
                pass

    # ── drawing primitives ─────────────────────────────────

    def clear(self):
        self.draw.rectangle([0, 0, WIDTH, HEIGHT], fill=255)

    def draw_note(self, x, y, size=7):
        """Draw a cute ♪ musical note glyph."""
        d = self.draw
        # note head
        nh = int(size * 0.6)
        d.ellipse([x, y + size, x + size, y + size + nh], fill=0)
        # stem
        sx = x + size - 1
        d.line([sx, y, sx, y + size + nh // 2], fill=0, width=2)
        # flag
        d.arc(
            [sx - 1, y, sx + size, y + int(size * 0.8)],
            270, 30, fill=0, width=2,
        )

    def draw_star(self, cx, cy, size=5):
        """Draw a filled 4-pointed star (for ★ eyes)."""
        s, h = size, size // 2
        pts = [
            (cx, cy - s), (cx + h, cy - h),
            (cx + s, cy), (cx + h, cy + h),
            (cx, cy + s), (cx - h, cy + h),
            (cx - s, cy), (cx - h, cy - h),
        ]
        self.draw.polygon(pts, fill=0)

    # ── header bar ─────────────────────────────────────────

    def draw_header(self, battery_pct, charging, wifi):
        d = self.draw

        # title
        d.text((4, 2), "BandBox", font=font_title, fill=0)

        # battery icon (right side)
        bx = WIDTH - 48
        d.rectangle([bx, 4, bx + 25, 14], outline=0, width=1)
        d.rectangle([bx + 25, 7, bx + 27, 11], fill=0)  # nub
        if battery_pct > 0:
            fw = int(23 * battery_pct / 100)
            if fw > 0:
                d.rectangle([bx + 1, 5, bx + 1 + fw, 13], fill=0)
        # percentage text
        txt = f"{battery_pct}%" if battery_pct >= 0 else "?"
        if charging:
            txt = "⚡" + txt
        d.text((bx - 30, 2), txt, font=font_sm, fill=0)

        # wifi icon
        if wifi:
            wx = WIDTH - 58
            for r in (3, 6, 9):
                d.arc(
                    [wx - r, 14 - r, wx + r, 14 + r], 200, 340, fill=0, width=1,
                )
            d.ellipse([wx - 1, 13, wx + 1, 15], fill=0)

        # separator
        d.line([0, 18, WIDTH, 18], fill=0, width=1)

    # ── face drawing ───────────────────────────────────────

    def draw_face(self, mood):
        d = self.draw
        cx, cy = WIDTH // 2, 40
        lx, ly = cx - 18, cy - 4  # left eye center
        rx, ry = cx + 18, cy - 4  # right eye center
        er = 4  # eye radius

        if mood == "happy":
            d.ellipse([lx - er, ly - er, lx + er, ly + er], fill=0)
            d.ellipse([rx - er, ry - er, rx + er, ry + er], fill=0)
            d.arc([cx - 14, cy - 2, cx + 14, cy + 12], 10, 170, fill=0, width=2)

        elif mood == "excited":
            br = er + 3
            d.ellipse([lx - br, ly - br, lx + br, ly + br], outline=0, width=2)
            d.ellipse([lx - 2, ly - 2, lx + 2, ly + 2], fill=0)
            d.ellipse([rx - br, ry - br, rx + br, ry + br], outline=0, width=2)
            d.ellipse([rx - 2, ry - 2, rx + 2, ry + 2], fill=0)
            d.ellipse([cx - 5, cy + 3, cx + 5, cy + 13], outline=0, width=2)

        elif mood == "working":
            # > < squinting eyes
            d.line([lx - er, ly - er, lx + er, ly], fill=0, width=2)
            d.line([lx + er, ly, lx - er, ly + er], fill=0, width=2)
            d.line([rx + er, ry - er, rx - er, ry], fill=0, width=2)
            d.line([rx - er, ry, rx + er, ry + er], fill=0, width=2)
            d.arc([cx - 10, cy, cx + 10, cy + 10], 10, 170, fill=0, width=2)

        elif mood == "proud":
            self.draw_star(lx, ly, 6)
            self.draw_star(rx, ry, 6)
            d.arc(
                [cx - 16, cy - 4, cx + 16, cy + 14], 10, 170, fill=0, width=2,
            )
            d.line([cx - 12, cy + 5, cx + 12, cy + 5], fill=0, width=1)

        elif mood == "chill":
            d.line([lx - er, ly, lx + er, ly], fill=0, width=2)
            d.line([rx - er, ry, rx + er, ry], fill=0, width=2)
            d.line([cx - 8, cy + 6, cx + 8, cy + 6], fill=0, width=2)

        elif mood == "uploading":
            d.ellipse(
                [lx - er, ly - er - 3, lx + er, ly + er - 3], fill=0,
            )
            d.ellipse(
                [rx - er, ry - er - 3, rx + er, ry + er - 3], fill=0,
            )
            d.arc([cx - 8, cy, cx + 8, cy + 8], 10, 170, fill=0, width=2)

        elif mood == "done":
            d.ellipse([lx - er, ly - er, lx + er, ly + er], fill=0)
            d.ellipse([rx - er, ry - er, rx + er, ry + er], fill=0)
            d.chord([cx - 14, cy + 2, cx + 14, cy + 14], 0, 180, fill=0)

        elif mood == "error":
            for ex, ey in [(lx, ly), (rx, ry)]:
                d.line(
                    [ex - er, ey - er, ex + er, ey + er], fill=0, width=2,
                )
                d.line(
                    [ex - er, ey + er, ex + er, ey - er], fill=0, width=2,
                )
            d.arc(
                [cx - 10, cy + 10, cx + 10, cy + 20], 190, 350, fill=0, width=2,
            )

        elif mood == "sleeping":
            d.arc(
                [lx - er, ly - 2, lx + er, ly + er], 200, 340, fill=0, width=2,
            )
            d.arc(
                [rx - er, ry - 2, rx + er, ry + er], 200, 340, fill=0, width=2,
            )
            d.line([cx - 5, cy + 6, cx + 5, cy + 6], fill=0, width=1)
            d.text((cx + 28, cy - 14), "z z z", font=font_sm, fill=0)

        # musical notes flanking the face
        self.draw_note(cx - 48, 28, size=7)
        self.draw_note(cx + 38, 24, size=7)

    # ── text areas ─────────────────────────────────────────

    def _centered(self, y, text, font):
        bb = self.draw.textbbox((0, 0), text, font=font)
        x = (WIDTH - (bb[2] - bb[0])) // 2
        self.draw.text((x, y), text, font=font, fill=0)

    def draw_status(self, text):
        self._centered(62, text, font_md)

    def draw_detail(self, text):
        self._centered(80, text, font_sm)

    def draw_sub(self, text):
        self._centered(108, text, font_sm)

    # ── progress bar ───────────────────────────────────────

    def draw_progress(self, current, total):
        if total <= 0:
            return
        bx, by, bw, bh = 24, 92, 168, 12
        pct = current / total

        self.draw.rectangle([bx, by, bx + bw, by + bh], outline=0, width=1)
        fw = int(bw * pct)
        if fw > 0:
            self.draw.rectangle(
                [bx + 1, by + 1, bx + fw, by + bh - 1], fill=0,
            )
        self.draw.text(
            (bx + bw + 6, by - 1), f"{int(pct * 100)}%", font=font_sm, fill=0,
        )


# ════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ════════════════════════════════════════════════════════════


class BandBox:
    def __init__(self):
        self.display = Display()
        self.journal = HashJournal(JOURNAL_PATH)
        self.running = True
        self.usb_present = False
        self.last_upload_sweep = 0
        self.tracks_staged = 0  # new tracks copied this session

        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    # ── screen composer ────────────────────────────────────

    def screen(self, mood, status, detail="", sub="",
               progress=None, full=True):
        """Compose and push a full screen."""
        pct, chg = get_battery()
        wi = wifi_name()

        dp = self.display
        dp.clear()
        dp.draw_header(pct, chg, wi)
        dp.draw_face(mood)
        dp.draw_status(status)
        if detail:
            dp.draw_detail(detail)
        if progress:
            dp.draw_progress(*progress)
        if sub:
            dp.draw_sub(sub)
        dp.refresh(full=full)

    # ── startup / shutdown animations ──────────────────────

    def startup(self):
        log.info("BandBox starting up")
        self.screen("sleeping", "Waking up...", sub="z z z")
        time.sleep(1.5)
        self.screen(
            "happy", "BandBox v1.0",
            sub=f"{len(self.journal)} tracks in journal",
        )
        time.sleep(2)

    def shutdown(self):
        log.info("BandBox shutting down")
        self.screen("happy", "See ya!", sub="Rock on!")
        time.sleep(2)
        self.screen("sleeping", "z z z")
        time.sleep(1)
        self.display.off()

    # ── USB handling ───────────────────────────────────────

    def handle_usb(self):
        dev = find_usb_partition()
        if not dev:
            return

        log.info("USB detected: %s", dev)
        self.screen("excited", msg("usb_found"), detail="Checking files...")
        time.sleep(1)

        if not mount_usb(dev):
            log.error("Mount failed for %s", dev)
            self.screen("error", msg("error"), detail="Can't read USB")
            time.sleep(4)
            return

        try:
            self._process_usb()
        finally:
            unmount_usb()

    def _process_usb(self):
        audio_files = find_audio_files()

        if not audio_files:
            self.screen("chill", "No audio files.", sub="Safe to unplug!")
            time.sleep(5)
            return

        # Phase 1: hash files on USB, skip known ones
        self.screen("working", msg("hashing"),
                     detail=f"Checking {len(audio_files)} files...")

        new_files = []  # list of (path, hash)
        for i, f in enumerate(audio_files, 1):
            file_hash = sha256_file(f)
            if not self.journal.contains(file_hash):
                new_files.append((f, file_hash))
            if i % 5 == 0 or i == len(audio_files):
                self.screen(
                    "working", msg("hashing"),
                    detail=f"Checked {i}/{len(audio_files)}",
                    progress=(i, len(audio_files)),
                    full=False,
                )

        if not new_files:
            log.info("No new files (all %d already in journal)", len(audio_files))
            self.screen("chill", msg("copy_none"), sub="Safe to unplug!")
            time.sleep(5)
            return

        # Phase 2: check disk space
        needed_mb = sum(f.stat().st_size for f, _ in new_files) // (1024 * 1024)
        available = free_space_mb()
        if 0 < available < needed_mb + MIN_FREE_SPACE_MB:
            log.warning(
                "Low space: need %d MB, have %d MB (min free: %d MB)",
                needed_mb, available, MIN_FREE_SPACE_MB,
            )
            self.screen(
                "error", msg("low_space"),
                detail=f"Need {needed_mb} MB, {available} MB free",
            )
            time.sleep(5)
            return

        # Phase 3: copy new files to staging
        total = len(new_files)
        log.info("Copying %d new files to staging", total)
        last_refresh = 0

        for i, (src, file_hash) in enumerate(new_files, 1):
            dest = STAGING_DIR / f"{file_hash}{src.suffix}"
            if not dest.exists():
                shutil.copy2(src, dest)

            now = time.time()
            if now - last_refresh > 0.8 or i == total:
                last_refresh = now
                self.screen(
                    "working", msg("copying"),
                    detail=f"File {i}/{total}",
                    progress=(i, total),
                    full=False,
                )

        self.tracks_staged += total
        log.info("Copied %d files to staging", total)

        s = "s" if total != 1 else ""
        self.screen(
            "proud", msg("copy_done"),
            detail=f"{total} new track{s}!",
            sub="Safe to unplug!",
        )

        # wait for USB removal
        time.sleep(5)
        self._wait_usb_removal()

    def _wait_usb_removal(self):
        """Stay on screen until USB is gone."""
        while self.running and find_usb_partition():
            time.sleep(1)

    # ── upload sweep ───────────────────────────────────────

    def try_upload_sweep(self):
        """Upload all staged files to the server."""
        now = time.time()
        if now - self.last_upload_sweep < UPLOAD_INTERVAL:
            return
        self.last_upload_sweep = now

        # find staged files
        staged = sorted(STAGING_DIR.glob("*"))
        staged = [f for f in staged if f.is_file() and f.suffix in AUDIO_EXTENSIONS]
        if not staged:
            return

        if not has_internet():
            return

        log.info("Upload sweep: %d files staged", len(staged))
        total = len(staged)
        uploaded = 0
        failed = 0

        for i, filepath in enumerate(staged, 1):
            # extract hash from filename (we named them {hash}.wav)
            file_hash = filepath.stem

            self.screen(
                "uploading", msg("uploading"),
                detail=f"File {i}/{total}",
                progress=(i, total),
                full=False,
            )

            result = upload_with_retry(filepath, file_hash)

            if result in ("accepted", "duplicate"):
                self.journal.add(file_hash)
                filepath.unlink()
                uploaded += 1
                if result == "accepted":
                    log.info("Uploaded %s", filepath.name)
                else:
                    log.info("Duplicate %s (server already has it)", filepath.name)
            else:
                failed += 1
                log.error("Failed to upload %s after %d retries",
                          filepath.name, UPLOAD_RETRIES)

        # show result
        if failed == 0:
            self.screen(
                "done", msg("upload_done"),
                detail=f"{uploaded} synced!",
                sub=wifi_name() or "",
            )
        elif uploaded > 0:
            self.screen(
                "working", msg("upload_partial"),
                detail=f"{uploaded} sent, {failed} failed",
                sub="Will retry later",
            )
        else:
            self.screen(
                "error", msg("error"),
                detail=f"{failed} uploads failed",
                sub="Will retry later",
            )

        time.sleep(5)

    # ── idle screen ────────────────────────────────────────

    def show_idle(self):
        pct, _ = get_battery()
        now_str = datetime.now().strftime("%I:%M %p")

        if 0 <= pct < 15:
            mood, status = "error", msg("low_battery")
        elif free_space_mb() < MIN_FREE_SPACE_MB:
            mood, status = "error", msg("low_space")
        else:
            # count pending uploads
            pending = len(list(STAGING_DIR.glob("*")))
            if pending > 0 and not has_internet():
                mood, status = "chill", msg("no_wifi")
            else:
                mood, status = "happy", msg("idle")

        detail = ""
        pending = len(list(STAGING_DIR.glob("*")))
        if pending > 0:
            s = "s" if pending != 1 else ""
            detail = f"{pending} track{s} awaiting upload"

        self.screen(mood, status, detail=detail, sub=now_str)

    # ── main loop ──────────────────────────────────────────

    def run(self):
        self.startup()
        self.show_idle()
        tick = 0

        while self.running:
            try:
                # ── check for USB ──
                dev = find_usb_partition()
                if dev and not self.usb_present:
                    self.usb_present = True
                    self.handle_usb()
                    self.show_idle()
                    tick = 0
                elif not dev:
                    self.usb_present = False

                # ── periodic upload sweep ──
                self.try_upload_sweep()

                # ── refresh idle screen once a minute ──
                tick += 1
                if tick >= 12:  # 12 × 5s = 60s
                    self.show_idle()
                    tick = 0

                time.sleep(5)

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.exception("Unexpected error")
                self.screen("error", "Error!", detail=str(e)[:30])
                time.sleep(5)

        self.shutdown()


# ════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════


def main():
    box = BandBox()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: setattr(box, "running", False))

    box.run()


if __name__ == "__main__":
    main()
