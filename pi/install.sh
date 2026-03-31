#!/bin/bash
set -e

echo ""
echo "  ♪ ♪ ♪  Installing BandBox  ♪ ♪ ♪"
echo ""

# Enable SPI (needed for e-ink display)
sudo raspi-config nonint do_spi 0

# System packages
sudo apt-get update
sudo apt-get install -y python3-pip python3-pil python3-numpy git

# Waveshare e-Paper driver
if [ ! -d "$HOME/e-Paper" ]; then
    cd ~
    git clone https://github.com/waveshareteam/e-Paper.git
fi
cd ~/e-Paper/RaspberryPi_JetsonNano/python
pip3 install . --break-system-packages 2>/dev/null || pip3 install .

# Create directories
mkdir -p ~/staging
mkdir -p ~/.bandbox
sudo mkdir -p /mnt/bandbox-usb

# Install systemd service
sudo cp ~/bandbox/pi/bandbox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bandbox

echo ""
echo "  ✓ BandBox installed!"
echo ""
echo "  Next steps:"
echo "  1. Set your server URL and API key:"
echo "     echo 'BANDBOX_SERVER_URL=https://your-server.example.com' >> ~/.bandbox/env"
echo "     echo 'BANDBOX_API_KEY=your-secret-key' >> ~/.bandbox/env"
echo "  2. Start the service:"
echo "     sudo systemctl start bandbox"
echo "  3. Plug in your USB stick and watch the magic!"
echo ""
