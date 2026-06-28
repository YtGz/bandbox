#!/usr/bin/env python3
"""
 ╔═══════════════════════════════════════╗
 ║          ♪ BandBox v0.1.0 ♪          ║
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
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Vaduz")
except Exception:  # pragma: no cover — fallback if tzdata missing
    TZ = timezone.utc
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# ════════════════════════════════════════════════════════════
#  GPIOZERO PI-REVISION PATCH
# ════════════════════════════════════════════════════════════
#
# gpiozero discovers the Pi model by reading either
# /proc/device-tree/system/linux,revision (binary, 4 bytes) or the
# `Revision:` line in /proc/cpuinfo. The aarch64 Arch Linux ARM kernel
# exposes neither, so every pin factory raises PinUnknownPi at import
# and gpiozero raises BadPinFactory. Hard-code the revision code for
# the Pi Zero 2 W (0x902120) — this is just used to pick GPIO chip
# numbers, not for any runtime behaviour we care about. Must run before
# anything imports gpiozero (incl. waveshare_epd.epdconfig).
def _patch_gpiozero_revision() -> None:
    from gpiozero.pins import local as _local

    _local.get_pi_revision = lambda: 0x902120  # Pi Zero 2 W rev 1.0


# ════════════════════════════════════════════════════════════
#  WAVESHARE EPDCONFIG PATCH
# ════════════════════════════════════════════════════════════
#
# waveshare-epd picks its hardware backend at import time by running
# `grep Raspberry /proc/cpuinfo`. The Pi Zero 2 W's aarch64 kernel on
# Arch Linux ARM doesn't emit that string (only "Hardware: BCM2835" and
# "Model: Raspberry Pi Zero 2 W"), so the package falls through to its
# JetsonNano class — which needs a `sysfs_software_spi.so` shim that
# isn't shipped via pip. Pre-load the module with the platform check
# forced to RaspberryPi so the gpiozero + spidev path is used instead.
def _patch_waveshare_epdconfig() -> None:
    import importlib.util

    if "waveshare_epd.epdconfig" in sys.modules:
        return
    spec = importlib.util.find_spec("waveshare_epd.epdconfig")
    if spec is None or spec.origin is None:
        return
    with open(spec.origin) as fh:
        src = fh.read()
    src = src.replace(
        '\nif "Raspberry" in output:\n',
        '\nif True:  # patched: aarch64 Arch /proc/cpuinfo omits "Raspberry"\n',
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["waveshare_epd.epdconfig"] = mod
    exec(compile(src, spec.origin, "exec"), mod.__dict__)


_patch_gpiozero_revision()
_patch_waveshare_epdconfig()


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
UPLOAD_TIMEOUT = 3600          # seconds per file (4 GB over 10 Mbit/s ≈ 55 min)
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


def wifi_strength():
    """Return (ssid:str|None, bars_0_to_3:int)."""
    ssid = wifi_name()
    if not ssid:
        return None, 0
    try:
        lines = Path("/proc/net/wireless").read_text().strip().splitlines()
        if len(lines) < 3:
            return ssid, 0
        # line 2 is the header, line 3+ are interfaces
        for line in lines[2:]:
            line = line.strip()
            if not line.startswith("wlan"):
                continue
            parts = line.split()
            # parts: [iface, status, link_quality, level_dBm, noise_dBm, ...]
            level = int(float(parts[3]))
            break
        else:
            return ssid, 0
    except Exception:
        return ssid, 0

    # dBm thresholds: >= -50 = 3 bars, >= -60 = 2, >= -70 = 1, < -70 = 0
    if level >= -50:
        return ssid, 3
    elif level >= -60:
        return ssid, 2
    elif level >= -70:
        return ssid, 1
    else:
        return ssid, 0


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
    # We run as the unprivileged `bandbox` user, so mount(8) needs root.
    # The sudoers drop-in only permits the bandbox-mount-usb wrapper —
    # see pi/README.md §5 "Allow the bandbox user to mount the USB stick".
    r = subprocess.run(
        ["sudo", "-n", "/usr/local/sbin/bandbox-mount-usb", device],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.error(
            "mount %s -> %s failed (rc=%d): %s",
            device, MOUNT_POINT, r.returncode,
            (r.stderr or r.stdout or "").strip(),
        )
    return r.returncode == 0


def unmount_usb():
    subprocess.run(["sync"], capture_output=True)
    subprocess.run(
        ["sudo", "-n", "/usr/local/sbin/bandbox-umount-usb"],
        capture_output=True,
    )


def find_audio_files():
    """Return list of audio files on the mounted USB."""
    return [
        f for f in MOUNT_POINT.rglob("*")
        if f.is_file() and f.suffix in AUDIO_EXTENSIONS
    ]


# ════════════════════════════════════════════════════════════
#  SERVER UPLOAD
# ════════════════════════════════════════════════════════════


def upload_file(filepath, file_hash, filename):
    """
    Upload one file to the BandBox server, streaming chunks straight
    from disk (the WAVs can be 2–4 GB and the Pi only has 512 MB RAM).

    Returns 'accepted', 'duplicate', or 'error'.
    """
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(SERVER_URL.rstrip("/") + "/api/upload")
    if parts.scheme not in ("http", "https"):
        log.error("Bad server URL scheme: %s", parts.scheme)
        return "error"

    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    size = filepath.stat().st_size
    mtime = filepath.stat().st_mtime
    conn_cls = (
        http.client.HTTPSConnection
        if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(host, port, timeout=UPLOAD_TIMEOUT)

    try:
        # putrequest/putheader/endheaders lets us stream the body in
        # 64 KB chunks. http.client.request() with a file-like body
        # already does this internally, but we want explicit control
        # over the buffer size and error handling around partial writes.
        #
        # Note: putrequest() sends `Host:` itself — adding our own would
        # produce a duplicate header that some reverse proxies reject
        # with a connection reset.
        conn.putrequest("POST", path)
        conn.putheader("X-Api-Key", API_KEY)
        conn.putheader("X-File-Hash", file_hash)
        conn.putheader("X-Filename", filename)
        conn.putheader("X-File-Modified", str(int(mtime * 1000)))
        conn.putheader("Content-Type", "audio/wav")
        conn.putheader("Content-Length", str(size))
        conn.endheaders()

        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                conn.send(chunk)

        resp = conn.getresponse()
        body = resp.read()
        if 200 <= resp.status < 300:
            try:
                data = json.loads(body.decode())
                return data.get("status", "accepted")
            except (ValueError, UnicodeDecodeError):
                return "accepted"
        if resp.status == 409:
            return "duplicate"
        log.error(
            "Upload HTTP %d: %s",
            resp.status, body[:200].decode("utf-8", "replace").strip(),
        )
        return "error"
    except (OSError, http.client.HTTPException) as e:
        log.error("Upload failed: %s", e)
        return "error"
    finally:
        conn.close()


def upload_with_retry(filepath, file_hash, filename):
    """Upload with exponential backoff. Returns 'accepted', 'duplicate', or 'error'."""
    for attempt in range(1, UPLOAD_RETRIES + 1):
        result = upload_file(filepath, file_hash, filename)
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
            epd = drv.EPD()
            epd.init()
            epd.Clear(0xFF)
            self.epd = epd
            log.info("E-ink display ready (%s)", DISPLAY_VERSION)
        except Exception as e:
            log.warning("Display not available: %s (saving PNGs instead)", e)
            self.epd = None

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

    def draw_header(self, battery_pct, charging, wifi, wifi_bars):
        d = self.draw

        # Align everything in the header bar to a common bottom edge: the
        # bottom of the battery icon (y=14, 4 px above the separator line).
        bottom_y = 14

        # title
        tb = d.textbbox((0, 0), "BandBox", font=font_title)
        d.text((4, bottom_y - tb[3]), "BandBox", font=font_title, fill=0)

        # battery icon (right side)
        bx = WIDTH - 48
        d.rectangle([bx, 4, bx + 25, 14], outline=0, width=1)
        d.rectangle([bx + 25, 7, bx + 27, 11], fill=0)  # nub
        if battery_pct > 0:
            fw = int(23 * battery_pct / 100)
            if fw > 0:
                d.rectangle([bx + 1, 5, bx + 1 + fw, 13], fill=0)
        # percentage text — placed immediately to the LEFT of the icon
        txt = f"{battery_pct}%" if battery_pct >= 0 else "?"
        if charging:
            txt = "⚡" + txt
        tb = d.textbbox((0, 0), txt, font=font_sm)
        tw = tb[2] - tb[0]
        d.text((bx - tw - 1, bottom_y - tb[3]), txt, font=font_sm, fill=0)

        # wifi icon — to the RIGHT of the battery icon, in the gap to the edge
        if wifi:
            wx, wy = WIDTH - 8, 13
            for i, r in enumerate((3, 6, 9), 1):
                if i <= wifi_bars:
                    d.arc(
                        [wx - r, wy - r, wx + r, wy + r], 200, 340,
                        fill=0, width=1,
                    )
            d.ellipse([wx - 1, wy - 1, wx + 1, wy + 1], fill=0)

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
        wi, wifi_bars = wifi_strength()

        dp = self.display
        dp.clear()
        dp.draw_header(pct, chg, wi, wifi_bars)
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
        # Partial refresh — base image already set by idle screen, so this
        # takes ~300 ms instead of ~4 s. Puts the final frame on screen
        # before the PiSugar cuts power.
        self.screen("sleeping", "Shutting down...", sub="z z z", full=False)
        time.sleep(2)  # safety margin for the partial refresh + sleep cmd
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
            # Sidecar with the original filename — the server requires
            # it in the upload form. Re-written every time so we always
            # have the freshest name even if the same hash shows up on
            # a stick with a renamed file.
            meta_path = STAGING_DIR / f"{file_hash}.meta.json"
            meta_path.write_text(json.dumps({"filename": src.name}))

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
            meta_path = STAGING_DIR / f"{file_hash}.meta.json"

            # Load the original USB filename from the sidecar written
            # at staging time; fall back to the staging name (which is
            # just `{hash}.wav`) if the sidecar got nuked somehow.
            try:
                filename = json.loads(meta_path.read_text())["filename"]
            except (OSError, ValueError, KeyError):
                filename = filepath.name

            self.screen(
                "uploading", msg("uploading"),
                detail=f"File {i}/{total}",
                progress=(i, total),
                full=False,
            )

            result = upload_with_retry(filepath, file_hash, filename)

            if result in ("accepted", "duplicate"):
                self.journal.add(file_hash)
                filepath.unlink()
                meta_path.unlink(missing_ok=True)
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
        now_str = datetime.now(TZ).strftime("%H:%M")

        # Count only the audio files — `.meta.json` sidecars share the
        # staging dir but aren't uploads in their own right.
        def _pending():
            return sum(
                1 for f in STAGING_DIR.iterdir()
                if f.is_file() and f.suffix in AUDIO_EXTENSIONS
            )

        if 0 <= pct < 15:
            mood, status = "error", msg("low_battery")
        elif free_space_mb() < MIN_FREE_SPACE_MB:
            mood, status = "error", msg("low_space")
        else:
            pending = _pending()
            if pending > 0 and not has_internet():
                mood, status = "chill", msg("no_wifi")
            else:
                mood, status = "happy", msg("idle")

        detail = ""
        pending = _pending()
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
