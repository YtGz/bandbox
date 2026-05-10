# BandBox Pi — Setup Guide

Turn a [Pwnagotchi](https://www.pisugar.com/products/pwnagotchi-complete-pack-pi02w-pisugar3-eink-case) into a band practice recording uploader. Plug in a USB stick after rehearsal, and BandBox copies, hashes, and uploads your recordings to the server — with a cute e-ink face keeping you informed.

## What You Need

- **[Pwnagotchi Complete Pack](https://www.pisugar.com/products/pwnagotchi-complete-pack-pi02w-pisugar3-eink-case)** — Pi Zero 2 W, PiSugar 3 battery, Waveshare 2.13" e-ink V4, case, 32 GB SD card
- **USB OTG adapter** — the Pi Zero has micro-USB, your USB stick is Type-A (included in the pack)
- **Mini-HDMI adapter** — for initial boot debugging (included in the pack)
- **A running BandBox server** — see the [main README](../README.md)
- **Wi-Fi access** — at the rehearsal space, at home, or both

## Step 1: Flash the SD Card (from Windows via WSL2)

We use Arch Linux ARM (aarch64) instead of Raspberry Pi OS for a leaner system with rolling updates.

> **Important:** Use the [official aarch64 image](https://archlinuxarm.org/platforms/armv8/broadcom/raspberry-pi-zero-2), **not** the armv7 image. The armv7 image (used by [jalbersdorfer's guide](https://github.com/jalbersdorfer/archlinux-arm-aarch64-on-rpi-zero-2-w) and [ladvien's walkthrough](https://ladvien.com/installing-arch-linux-raspberry-pi-zero-w/)) fails to boot on the Pi Zero 2 W.

### Prerequisites (Windows side)

Install [usbipd-win](https://github.com/dorssel/usbipd-win) to pass USB devices into WSL:

```powershell
scoop install usbipd
# Reboot after first install
```

You need an Arch (or any Linux) WSL2 distro with these packages:

```bash
sudo pacman -S --needed wget libarchive dosfstools e2fsprogs util-linux
```

### Attach the SD card to WSL

Insert the SD card, then in an **admin PowerShell**:

```powershell
usbipd list                          # find your card reader's BUSID
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

> If attach fails with "Device busy", eject the SD card from Windows Explorer first, then retry with `--force`.

### Partition, format, and flash

In WSL, find the SD card device name:

```bash
lsblk   # look for a device matching your SD card's size, e.g. /dev/sde
```

> **Warning:** Triple-check the device name. Selecting the wrong device **will erase data**.

Replace `sdX` with your device in every command below.

```bash
# --- Partition (1 GB boot + root fills the rest) ---
# fdisk hangs in WSL2 over USB passthrough — use sfdisk instead
echo -e ',1G,c\n,,L' | sudo sfdisk --force /dev/sdX

# --- Format ---
sudo mkfs.vfat /dev/sdX1
sudo mkfs.ext4 /dev/sdX2

# --- Mount ---
sudo mkdir -p /mnt/{boot,root}
sudo mount /dev/sdX1 /mnt/boot
sudo mount /dev/sdX2 /mnt/root
```

Download the **aarch64** tarball. `wget` can be slow from WSL — downloading from Windows is faster:

```powershell
# In PowerShell
curl -L -o $env:USERPROFILE\Downloads\ArchLinuxARM-rpi-aarch64-latest.tar.gz http://os.archlinuxarm.org/os/ArchLinuxARM-rpi-aarch64-latest.tar.gz
```

Then extract in WSL:

```bash
cd /tmp
cp /mnt/c/Users/$(whoami)/Downloads/ArchLinuxARM-rpi-aarch64-latest.tar.gz .
sudo bsdtar -xpf ArchLinuxARM-rpi-aarch64-latest.tar.gz -C /mnt/root
sync

# --- Move boot files ---
sudo mv /mnt/root/boot/* /mnt/boot/
```

### Enable Wi-Fi on first boot (headless)

So you can SSH in immediately without HDMI + keyboard. The aarch64 image uses `systemd-networkd` + `wpa_supplicant` (no NetworkManager, no iwd):

```bash
# --- Tell systemd-networkd to request DHCP on wlan0 ---
cat << 'EOF' | sudo tee /mnt/root/etc/systemd/network/25-wlan0.network
[Match]
Name=wlan0

[Network]
DHCP=yes
MulticastDNS=yes
LLMNR=yes

[DHCPv4]
RouteMetric=20
EOF

# --- WPA supplicant config (wpa_passphrase is not available in WSL) ---
sudo mkdir -p /mnt/root/etc/wpa_supplicant
sudo tee /mnt/root/etc/wpa_supplicant/wpa_supplicant-wlan0.conf << 'EOF'
ctrl_interface=/run/wpa_supplicant
update_config=1
country=US

network={
	ssid="YourSSID"
	psk="YourPassword"
}
EOF

# --- Enable the three services needed for Wi-Fi + DHCP + DNS ---
sudo ln -s \
  /usr/lib/systemd/system/wpa_supplicant@.service \
  /mnt/root/etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service

sudo ln -s \
  /usr/lib/systemd/system/systemd-networkd.service \
  /mnt/root/etc/systemd/system/multi-user.target.wants/systemd-networkd.service

sudo ln -s \
  /usr/lib/systemd/system/systemd-resolved.service \
  /mnt/root/etc/systemd/system/multi-user.target.wants/systemd-resolved.service

# --- Link resolv.conf so DNS works via systemd-resolved ---
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /mnt/root/etc/resolv.conf

# --- Fix brcmfmac firmware bug (Pi Zero 2 W) ---
# The BCM43436 firmware offloads the WPA 4-way handshake, which fails with
# many modern routers (especially WPA2/WPA3 transition mode). Disable it so
# wpa_supplicant handles the handshake in userspace instead.
# See: https://blog.wijman.net/make-raspberry-pi-zero-2w-wifi-work-correctly/
sudo mkdir -p /mnt/root/etc/modprobe.d
echo 'options brcmfmac roamoff=1 feature_disable=0x82000' | \
  sudo tee /mnt/root/etc/modprobe.d/brcmfmac.conf
```

> **Set your country code** in `wpa_supplicant-wlan0.conf` — without it, the Wi-Fi radio may stay disabled. Use your [ISO 3166-1 alpha-2 code](https://en.wikipedia.org/wiki/ISO_3166-1_alpha-2) (e.g. `US`, `DE`, `GB`).

> **Why `MulticastDNS=yes` and `LLMNR=yes`?** These let `systemd-resolved` publish the Pi's hostname over mDNS (Apple/Linux) and LLMNR (Windows), so you can reach it as `bandbox.local` on any network — home, rehearsal Wi-Fi, phone hotspot — without ever knowing the IP. Without them, mDNS is enabled globally but **off per-link**, and `bandbox.local` won't resolve.

### Create the bandbox user and SSH access

Create the user now so you never need root password login over SSH:

```bash
# Create user with home directory and sudo group
sudo useradd --root /mnt/root -m -G wheel -s /bin/bash bandbox

# Set a password — chroot won't work here (aarch64 binaries on an x86 host),
# so we write the password hash directly into /etc/shadow:
HASH=$(openssl passwd -6)   # you'll be prompted twice
sudo sed -i 's|^bandbox:!:|bandbox:'"${HASH}"':|' /mnt/root/etc/shadow

# Allow sudo for wheel group
sudo mkdir -p /mnt/root/etc/sudoers.d
echo '%wheel ALL=(ALL:ALL) ALL' | sudo tee /mnt/root/etc/sudoers.d/wheel
sudo chmod 440 /mnt/root/etc/sudoers.d/wheel
```

Enable SSH with key-only authentication:

```bash
sudo ln -s /usr/lib/systemd/system/sshd.service \
  /mnt/root/etc/systemd/system/multi-user.target.wants/sshd.service

# Allow root password login temporarily (needed to install sudo on first boot)
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /mnt/root/etc/ssh/sshd_config

# Copy your SSH public key (adjust path to your key)
sudo mkdir -p /mnt/root/home/bandbox/.ssh
sudo cp ~/.ssh/id_ed25519_sk.pub /mnt/root/home/bandbox/.ssh/authorized_keys
sudo chmod 700 /mnt/root/home/bandbox/.ssh
sudo chmod 600 /mnt/root/home/bandbox/.ssh/authorized_keys

# Fix ownership (bandbox is uid 1000 — first user created)
sudo chown -R 1000:1000 /mnt/root/home/bandbox/.ssh
```

> If you don't have an SSH key yet, generate one first: `ssh-keygen -t ed25519-sk -C "bandbox-pi"` (FIDO2) or `ssh-keygen -t ed25519` (standard).

### Set hostname

```bash
echo 'bandbox' | sudo tee /mnt/root/etc/hostname
```

### Unmount and detach

```bash
sudo umount /mnt/boot /mnt/root
```

Back in admin PowerShell:

```powershell
usbipd detach --busid <BUSID>
```

Eject the SD card from Windows.

## Step 2: First Boot & Initial System Setup

Insert the SD card into the Pi Zero 2 W and apply power. Wait 1–2 minutes, then find the Pi on your network (check your router's client list — it may show as `bandbox` or by MAC address).

```bash
ssh root@<pi-ip>
```

If headless Wi-Fi didn't work, connect HDMI + keyboard (**before** powering on — the Pi Zero has no hotplug) and log in as `root` (default password: `root`).

### Connect to Wi-Fi (if not pre-configured)

The aarch64 image has `wpa_supplicant` and `systemd-networkd` — **not** `iwctl` or NetworkManager.

```bash
# Unblock the radio (sometimes soft-blocked by default)
sudo rfkill unblock wifi

# Make sure the required services are running
sudo systemctl enable --now systemd-networkd systemd-resolved wpa_supplicant@wlan0

# Link resolv.conf if DNS doesn't work
sudo ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf

# Create/edit the WPA config
sudo tee /etc/wpa_supplicant/wpa_supplicant-wlan0.conf << 'EOF'
ctrl_interface=/run/wpa_supplicant
update_config=1
country=US

network={
	ssid="YourSSID"
	psk="YourPassword"
}
EOF

# Create the network file if missing
sudo tee /etc/systemd/network/25-wlan0.network << 'EOF'
[Match]
Name=wlan0

[Network]
DHCP=yes
MulticastDNS=yes
LLMNR=yes
EOF

# Restart to pick up config
sudo systemctl restart wpa_supplicant@wlan0 systemd-networkd

# Verify — wait a few seconds for DHCP
ip addr show wlan0
```

> **Tip:** If `wpa_supplicant@wlan0` fails to start with "interface busy", another process may be holding the interface. Run `sudo ip link set wlan0 down` first, then retry.

### Bootstrap as root

The base aarch64 image doesn't include `sudo`. SSH in as root (password `root`) to bootstrap:

```bash
ssh root@<pi-ip>
pacman-key --init
pacman-key --populate archlinuxarm
```

Before the first full upgrade, drop the GPU firmware blobs and disable the fallback initramfs — otherwise `mkinitcpio` will run out of memory or `/boot` space generating a fallback image the Pi will never use:

```bash
# Remove NVIDIA + AMD GPU firmware (irrelevant on a Pi Zero 2 W).
# -Rdd skips dependency checks so the linux-firmware meta-package
# doesn't block the removal.
pacman -Rdd --noconfirm linux-firmware-nvidia linux-firmware-amdgpu

# Disable the fallback initramfs preset.
sed -i \
  -e "s/^PRESETS=.*/PRESETS=('default')/" \
  -e 's/^fallback_image=/# fallback_image=/' \
  -e 's/^fallback_options=/# fallback_options=/' \
  /etc/mkinitcpio.d/linux-aarch64.preset

# Clean up the now-orphaned fallback image, if one already exists.
rm -f /boot/initramfs-linux-fallback.img
```

Now upgrade and install the basics:

```bash
pacman -Syu
pacman -S sudo fish
chsh -s /usr/bin/fish bandbox
```

Now lock down SSH and log out:

```bash
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd
exit
```

From now on, SSH in as `bandbox` (key-only):

```bash
ssh bandbox@<pi-ip>
```

### Update the system

```bash
sudo pacman -Syu
```

### Install paru (AUR helper)

Paru is written in Rust, and `rustc` will OOM-kill itself partway through linking on the Pi Zero 2 W's 512 MB of RAM with default settings. Two preparations are needed: give the kernel more virtual memory via zram, and tell Cargo to compile single-threaded with one codegen unit.

#### Set up zram swap

zram exposes a compressed block device backed by RAM — "swapping" to it is just in-place compression, not disk I/O. lz4 has trivial CPU cost even on the Pi Zero 2 W and effectively stretches 512 MB to ~800 MB of usable memory without touching the SD card.

```bash
sudo pacman -S zram-generator

sudo tee /etc/systemd/zram-generator.conf << 'EOF'
[zram0]
zram-size = ram * 2
compression-algorithm = lz4
EOF

sudo systemctl daemon-reload
sudo systemctl start systemd-zram-setup@zram0.service
```

Verify: `swapon --show` should list `/dev/zram0`.

#### Tune swappiness for zram

`vm.swappiness` controls how eagerly the kernel moves inactive pages to swap. With disk-based swap you want it low to avoid slow I/O and SD-card wear; with zram, "swap" is just compressed RAM, so you want it **high** — tell the kernel to aggressively reclaim anonymous pages instead of dropping file cache. Values up to 200 are valid and specifically intended for zram setups.

```bash
echo 'vm.swappiness=150' | sudo tee /etc/sysctl.d/99-swappiness.conf
sudo sysctl --system
```

#### Build paru

```bash
sudo pacman -S --needed base-devel git

cd /tmp
git clone https://aur.archlinux.org/paru.git
cd paru
CARGO_BUILD_JOBS=1 RUSTFLAGS="-C codegen-units=1" makepkg -si
cd /tmp && rm -rf paru   # clean up the build tree
```

> `CARGO_BUILD_JOBS=1` serializes compilation (no parallel rustc processes competing for RAM) and `codegen-units=1` makes each crate emit a single LLVM module, which lowers peak memory at the cost of longer link times. Expect 30–45 min on the Pi Zero 2 W — but it will actually finish.

## Step 3: Install and Configure PiSugar

### Install the power manager

The official `pisugar-power-manager.sh` installer only supports Debian/RPM and aborts on Arch Linux ARM. Instead, use the prebuilt aarch64-musl tarball from the [pisugar-power-manager-rs releases](https://github.com/PiSugar/pisugar-power-manager-rs/releases) — it ships a distro-agnostic `install.sh` that just drops binaries, configs, and systemd units in place.

First, enable I2C (required for the PiSugar 3 to talk to the Pi):

```bash
echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt
echo "i2c-dev" | sudo tee /etc/modules-load.d/i2c.conf
```

Then download and run the bundled installer with model `PiSugar 3`:

```bash
cd /tmp
VERSION=$(curl -fsSL https://api.github.com/repos/PiSugar/pisugar-power-manager-rs/releases/latest \
  | grep -Po '"tag_name":\s*"\K[^"]+')
curl -fLO "https://github.com/PiSugar/pisugar-power-manager-rs/releases/download/${VERSION}/pisugar_aarch64-unknown-linux-musl.tar.gz"
tar -xf pisugar_aarch64-unknown-linux-musl.tar.gz
cd aarch64-unknown-linux-musl
sudo bash install.sh -m 'PiSugar 3' server
sudo bash install.sh -m 'PiSugar 3' poweroff
cd /tmp && rm -rf aarch64-unknown-linux-musl pisugar_aarch64-unknown-linux-musl.tar.gz
```

> The bundled `install.sh` only accepts **one** positional arg (e.g. `server`, `poweroff`, or `all`) — passing two silently installs only the first. Don't use `all` either: it pulls in `programmer`, which has a broken path (`target/release/pisugar-programmer`) that doesn't exist in the release tarball, causing the script to abort midway.

Reboot to load the I2C kernel module and the dtparam, then enable + start the services:

```bash
sudo reboot
# after reconnect:
sudo systemctl enable --now pisugar-server pisugar-poweroff
sudo systemctl status pisugar-server
```

Verify the battery is detected: `curl http://localhost:8421/exec?cmd=get%20battery` (after logging in via the web UI to get a token — see [PiSugar HTTP API](https://github.com/PiSugar/pisugar-power-manager-rs#http-api)). Default web UI credentials are `admin/admin` — change them at `http://<pi-ip>:8421`.

### Tune the configuration

The default `/etc/pisugar-server/config.json` leaves nearly everything `null` — no auth, no buttons, no auto-shutdown, no battery protection. Replace it with a config tuned for portable battery use:

```bash
sudo tee /etc/pisugar-server/config.json > /dev/null << 'EOF'
{
    "auth_user": "admin",
    "auth_password": "CHANGE_ME",
    "session_timeout": 3600,
    "i2c_bus": 1,
    "i2c_addr": null,

    "single_tap_enable": true,
    "single_tap_shell": "systemctl restart bandbox",
    "double_tap_enable": false,
    "double_tap_shell": "",
    "long_tap_enable": true,
    "long_tap_shell": "shutdown -h now",

    "auto_shutdown_level": 3,
    "auto_shutdown_delay": 30,
    "auto_charging_range": [80, 95],
    "full_charge_duration": 3600,

    "auto_power_on": false,
    "soft_poweroff": true,
    "soft_poweroff_shell": "shutdown -h now",

    "anti_mistouch": true,
    "bat_protect": true,

    "auto_rtc_sync": true,
    "auto_wake_time": null,
    "auto_wake_repeat": 0,
    "adj_comm": null,
    "adj_diff": null,
    "rtc_adj_ppm": null
}
EOF
sudo systemctl restart pisugar-server
```

Why each non-default matters:

| Setting | Value | Why |
| --- | --- | --- |
| `auth_user` / `auth_password` | set | Default is `admin/admin` (or no auth) — anyone on the rehearsal Wi-Fi can shut your Pi down |
| `single_tap_shell` | `systemctl restart bandbox` | Force a screen refresh / reset state without rebooting |
| `long_tap_shell` | `shutdown -h now` | Triggers a clean shutdown (instead of a hardware power-cut that risks SD corruption) |
| `auto_shutdown_level` | `3` | Auto-shutdown at 3 % battery — prevents deep discharge that damages LiPo cells |
| `auto_shutdown_delay` | `30` | Grace period (seconds) — gives an in-flight upload time to finish |
| `auto_charging_range` | `[80, 95]` | Stops charging at 95 %, resumes at 80 %. LiPo cells age fastest at 100 % — this trades ~15 % capacity for **2–3× battery lifespan** |
| `full_charge_duration` | `3600` | Stop after 1 h at "full" even if the chip's gauge drifts — extra safety against overcharge |
| `auto_power_on` | `false` | Don't auto-boot when USB is plugged in — keeps the chip in deep sleep, saves microamps × hours |
| `soft_poweroff` | `true` | Enables the tap-then-long-press sequence to do a clean shutdown via systemd |
| `soft_poweroff_shell` | `shutdown -h now` | What the soft-poweroff sequence runs |
| `anti_mistouch` | `true` | Ignores stray button taps (e.g. in a backpack) — prevents accidental shutdowns |
| `bat_protect` | `true` | Enables the chip's hardware over/under-voltage and over-current protection |
| `auto_rtc_sync` | `true` | Keep the PiSugar's RTC in sync with system time — useful since the Pi has no built-in RTC |

> **Always change `auth_password`** before exposing the Pi on any shared network. The web UI on port 8421 lets anyone with credentials shut down or reconfigure your device.

You can also tweak any of these later through the web UI at `http://<pi-ip>:8421`.

## Step 4: Configure Wi-Fi Networks

Add all networks the Pi might encounter — rehearsal space, home, phone hotspot. Edit the wpa_supplicant config to include multiple networks:

```bash
sudo tee /etc/wpa_supplicant/wpa_supplicant-wlan0.conf << 'EOF'
ctrl_interface=/run/wpa_supplicant
update_config=1
country=US

network={
	ssid="RehearsalWiFi"
	psk="password1"
	priority=2
}

network={
	ssid="HomeWiFi"
	psk="password2"
	priority=1
}
EOF

sudo systemctl restart wpa_supplicant@wlan0
```

Higher `priority` values are tried first. The Pi will auto-connect to whichever network is available.

BandBox works offline — it buffers recordings locally and uploads when Wi-Fi is available.

## Step 5: Install BandBox

### Clone the repo

```bash
cd ~
git clone https://github.com/YtGz/bandbox.git
```

### Install Python dependencies with uv

```bash
# uv plus build tools needed for spidev / lgpio C extensions
sudo pacman -S uv base-devel swig unzip

# liblgpio (the C library lgpio's pip wheel links against). It's not in
# Arch's repos or the AUR, but it's a tiny single-Makefile build.
cd /tmp
curl -fLO http://abyz.me.uk/lg/lg.zip
unzip lg.zip && cd lg
make && sudo make install   # installs to /usr/local/lib + runs ldconfig
cd /tmp && rm -rf lg lg.zip  # clean up the build tree

# Enable SPI (needed for the e-ink display)
echo "dtparam=spi=on" | sudo tee -a /boot/config.txt

# Create venv and install everything from pyproject.toml
cd ~/bandbox/pi
uv sync
```

`uv sync` reads `pyproject.toml`, creates `.venv/`, and installs Pillow, NumPy, gpiozero, lgpio, spidev, and the Waveshare e-Paper driver from GitHub. The lock file (`uv.lock`) pins exact versions so re-flashing the SD card gives identical dependencies.

> **First sync is slow** — `lgpio` and `spidev` compile from source on the Pi Zero 2 W (~2–3 min). Subsequent syncs are fast.

> **Why `gpiozero` + `lgpio` and not `RPi.GPIO`?** The Waveshare driver's `RaspberryPi` backend uses `gpiozero`, which auto-picks a pin factory. `RPi.GPIO` has been unmaintained since 2019, mmaps `/dev/gpiomem` directly, and doesn't work on the Pi 5; `lgpio` talks to the modern kernel `gpio-cdev` interface (`/dev/gpiochipN`), which is where Linux is moving everyone. We also patch `waveshare_epd.epdconfig` at startup because its `grep Raspberry /proc/cpuinfo` autodetect fails on the aarch64 Arch kernel and falls through to a JetsonNano backend that needs a `sysfs_software_spi.so` shim pip never ships.

### Configure server connection

```bash
mkdir -p ~/.bandbox

cat > ~/.bandbox/env << 'EOF'
BANDBOX_SERVER_URL=https://your-server.example.com
BANDBOX_API_KEY=your-secret-api-key
EOF

chmod 600 ~/.bandbox/env
```

The API key must match the `PI_API_KEY` in your server's `.env` file.

### Create mount point and staging directory

```bash
mkdir -p ~/staging
sudo mkdir -p /mnt/bandbox-usb
```

### Install and start the service

```bash
sudo cp ~/bandbox/pi/bandbox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bandbox
```

### Check it's running

```bash
sudo systemctl status bandbox
journalctl -u bandbox -f
```

You should see the e-ink display wake up with "BandBox v0.1.0" and a happy face.

## Step 6: PiSugar Button Cheat Sheet

After the config above, the buttons behave like this:

| Gesture | Action |
| --- | --- |
| Single tap | Restart `bandbox.service` (refresh the screen) |
| Single tap → long press | Soft shutdown via systemd (safe) |
| Long press alone | Hardware power-cut (emergency only — risks SD corruption) |

## Usage

1. **Record** your practice session to a USB stick (from your mixer, interface, or portable recorder)
2. **Plug** the USB stick into the Pi via the OTG adapter
3. **Watch** the e-ink display — it shows hashing, copying, and upload progress
4. **Unplug** when you see "Safe to unplug!" (files are copied to staging)
5. **Uploads happen automatically** when Wi-Fi is available
6. **Shut it down** when you're done — single-tap then long-press the PiSugar button (or `sudo shutdown -h now`)

The USB stick is never modified. Re-inserting the same stick is harmless — duplicates are skipped instantly via the local hash journal.

> **Always power off between sessions.** An idle Pi Zero 2 W still pulls ~120–180 mA and drains the 1200 mAh battery in 6–10 hours. In deep sleep (after a clean shutdown via the `pisugar-poweroff` service) the PiSugar 3 draws microamps — months of standby. Booting back up costs only ~5–8 mAh, so even daily use is dramatically cheaper than leaving it on. **Don't use a bare long-press for shutdown** — that's a hardware power-cut and risks SD card corruption. The single-tap → long-press sequence triggers a clean shutdown via `pisugar-server`'s soft-poweroff hook.

## Troubleshooting

### Pi won't boot (solid green LED)

- Use the **aarch64** image, not armv7 — the armv7 image fails to boot on the Pi Zero 2 W
- Connect HDMI **before** powering on (no hotplug support)
- Try a minimal `config.txt` — the default one has Pi 4/CM4/CM5 settings that can hang the Pi Zero 2 W
- Do **not** change `mmcblk0` to `mmcblk1` in fstab — despite what some guides say, the aarch64 kernel on the Pi Zero 2 W uses `mmcblk0`

### Display shows nothing

- Check SPI is enabled: `ls /dev/spidev*` should show devices
- Check the display version matches `DISPLAY_VERSION` in `bandbox.py` (V3 vs V4)
- Check the ribbon cable is seated properly
- **Logs show `Cannot find sysfs_software_spi.so`?** That means waveshare-epd's autodetect fell through to its JetsonNano backend. `bandbox.py` already monkey-patches `epdconfig` to force the RaspberryPi backend; if you see this anyway, you're probably running an old checkout — `git pull` and `uv sync` again.

### USB stick not detected

- Use the **data** micro-USB port (the one closer to the center), not the power port
- Try a different OTG adapter
- Check `lsblk` to see if the device appears

### Wi-Fi won't connect

- **Check rfkill:** `rfkill list` — if wlan0 is soft-blocked, run `rfkill unblock wifi`
- **Check services:** all three must be active:
  ```bash
  systemctl status wpa_supplicant@wlan0 systemd-networkd systemd-resolved
  ```
- **"Interface busy" from wifi-menu:** another process (usually wpa_supplicant) holds the interface. Don't use `wifi-menu` — use `wpa_supplicant@wlan0` as described above
- **Check config syntax:** `wpa_supplicant -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant-wlan0.conf -d` (run manually with debug output)
- **Authentication timeout / 4-way handshake fails:** the Pi Zero 2 W's `brcmfmac` firmware offloads the WPA handshake and often fails with modern WPA2/WPA3 routers. Fix:
  ```bash
  echo 'options brcmfmac roamoff=1 feature_disable=0x82000' > /etc/modprobe.d/brcmfmac.conf
  reboot
  ```
  This disables firmware-based handshake (SWSUP) and SAE offload, letting wpa_supplicant handle it in userspace. See [blog.wijman.net](https://blog.wijman.net/make-raspberry-pi-zero-2w-wifi-work-correctly/).
- **No IP address:** verify the `.network` file exists at `/etc/systemd/network/25-wlan0.network` and contains `DHCP=yes`
- **DNS not working:** ensure resolv.conf is linked: `ls -l /etc/resolv.conf` should point to `/run/systemd/resolve/stub-resolv.conf`

### Uploads failing

- Verify server URL and API key: `cat ~/.bandbox/env`
- Test connectivity: `ping your-server.example.com`
- Check logs: `journalctl -u bandbox -n 50`

### Low disk space warning

- Check staging: `ls -lh ~/staging/`
- Files here are waiting for upload — ensure Wi-Fi is available
- Once uploaded, staged files are deleted automatically
