# BandBox Pi — Setup Guide

Turn a [Pwnagotchi](https://www.tindie.com/products/pisugar/pwnagotchi-complete-pack-pi0w-eink-battery-case/) into a band practice recording uploader. Plug in a USB stick after rehearsal, and BandBox copies, hashes, and uploads your recordings to the server — with a cute e-ink face keeping you informed.

## What You Need

- **[Pwnagotchi Complete Pack](https://www.tindie.com/products/pisugar/pwnagotchi-complete-pack-pi0w-eink-battery-case/)** — Pi Zero 2 W, PiSugar 3 battery, e-ink display, case, 32 GB SD card
- **USB OTG adapter** — the Pi Zero has micro-USB, your USB stick is Type-A
- **A running BandBox server** — see the [main README](../README.md)
- **Wi-Fi access** — at the rehearsal space, at home, or both

## Step 1: Install Arch Linux ARM

We use Arch Linux ARM (aarch64) instead of Raspberry Pi OS for a leaner system with rolling updates.

### Flash the SD card

Follow the aarch64 installation for the Pi Zero 2 W:

> **Guide:** [Arch Linux ARM aarch64 on RPi Zero 2 W](https://github.com/jalbersdorfer/archlinux-arm-aarch64-on-rpi-zero-2-w)
>
> **Background:** [Installing Arch Linux on Raspberry Pi Zero W](https://ladvien.com/installing-arch-linux-raspberry-pi-zero-w/)

The key steps:

1. Partition the SD card (boot + root)
2. Extract the aarch64 root filesystem
3. Move the boot files into place
4. Configure `config.txt` for the Pi Zero 2 W

### Enable SSH on first boot

Before ejecting the SD card, ensure SSH starts automatically:

```bash
# On the mounted root partition
ln -s /usr/lib/systemd/system/sshd.service \
  /path/to/root/etc/systemd/system/multi-user.target.wants/sshd.service
```

### First boot

Insert the SD card, power on, and find the Pi on your network:

```bash
# Default credentials: alarm/alarm (user), root/root
ssh alarm@<pi-ip>
```

## Step 2: Initial System Setup

### Switch to root and set passwords

```bash
su - root
# Change root password
passwd
# Change alarm user password
passwd alarm
```

### Connect to Wi-Fi

```bash
# List available networks
iwctl station wlan0 scan
iwctl station wlan0 get-networks

# Connect
iwctl station wlan0 connect "YourNetwork"
```

### Update the system

```bash
pacman-key --init
pacman-key --populate archlinuxarm
pacman -Syu
```

### Install paru (AUR helper)

```bash
# As the alarm user (not root)
sudo pacman -S --needed base-devel git

cd /tmp
git clone https://aur.archlinux.org/paru.git
cd paru
makepkg -si
```

## Step 3: Secure SSH Access

Disable password authentication and use a FIDO2 hardware key for SSH.

### On your workstation

Generate a FIDO2-backed SSH key (requires a security key like YubiKey):

```bash
ssh-keygen -t ed25519-sk -C "bandbox-pi"
```

Copy the public key to the Pi:

```bash
ssh-copy-id -i ~/.ssh/id_ed25519_sk.pub alarm@<pi-ip>
```

### On the Pi

Lock down SSH — edit `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
```

Restart SSH:

```bash
sudo systemctl restart sshd
```

> **Test from another terminal before closing your session!** If something is misconfigured, you'll lock yourself out.

## Step 4: Install BandBox Dependencies

```bash
# System packages
sudo pacman -S python python-pillow python-numpy

# Enable SPI (needed for the e-ink display)
# Add to /boot/config.txt:
echo "dtparam=spi=on" | sudo tee -a /boot/config.txt

# Waveshare e-Paper driver
cd ~
git clone https://github.com/waveshareteam/e-Paper.git
cd e-Paper/RaspberryPi_JetsonNano/python
pip install . --break-system-packages
```

### Install PiSugar power manager

```bash
curl https://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash
```

Verify it's running:

```bash
sudo systemctl status pisugar-server
```

## Step 5: Install BandBox

### Clone the repo

```bash
cd ~
git clone https://github.com/YtGz/bandbox.git
```

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
# Update the service file paths if your username isn't "pi"
# Edit bandbox.service: replace /home/pi with /home/alarm (or your user)

sudo cp ~/bandbox/pi/bandbox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bandbox
```

### Check it's running

```bash
sudo systemctl status bandbox
journalctl -u bandbox -f
```

You should see the e-ink display wake up with "BandBox v1.0" and a happy face.

## Step 6: Configure Wi-Fi Networks

Add all networks the Pi might encounter — rehearsal space, home, phone hotspot:

```bash
# Each network
iwctl station wlan0 connect "RehearsalWiFi"
iwctl station wlan0 connect "HomeWiFi"
```

iwd remembers networks automatically. The Pi will connect to whichever is available.

BandBox works offline — it buffers recordings locally and uploads when Wi-Fi is available.

## Step 7: PiSugar Button Config (Optional)

Open the PiSugar web UI at `http://<pi-ip>:8421` and configure button gestures:

| Gesture | Command | Purpose |
| --- | --- | --- |
| Single tap | `systemctl restart bandbox` | Force screen refresh |
| Long press | `shutdown -h now` | Safe shutdown |

## Usage

1. **Record** your practice session to a USB stick (from your mixer, interface, or portable recorder)
2. **Plug** the USB stick into the Pi via the OTG adapter
3. **Watch** the e-ink display — it shows hashing, copying, and upload progress
4. **Unplug** when you see "Safe to unplug!" (files are copied to staging)
5. **Uploads happen automatically** when Wi-Fi is available

The USB stick is never modified. Re-inserting the same stick is harmless — duplicates are skipped instantly via the local hash journal.

## Troubleshooting

### Display shows nothing

- Check SPI is enabled: `ls /dev/spidev*` should show devices
- Check the display version matches `DISPLAY_VERSION` in `bandbox.py` (V3 vs V4)
- Check the ribbon cable is seated properly

### USB stick not detected

- Use the **data** micro-USB port (the one closer to the center), not the power port
- Try a different OTG adapter
- Check `lsblk` to see if the device appears

### Uploads failing

- Verify server URL and API key: `cat ~/.bandbox/env`
- Test connectivity: `ping your-server.example.com`
- Check logs: `journalctl -u bandbox -n 50`

### Low disk space warning

- Check staging: `ls -lh ~/staging/`
- Files here are waiting for upload — ensure Wi-Fi is available
- Once uploaded, staged files are deleted automatically
