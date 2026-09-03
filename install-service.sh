#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/pandamonium.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: pandamonium.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Pandamonium UI service..."
echo "Make sure you've edited pandamonium.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pandamonium
sudo systemctl start pandamonium
sudo systemctl status pandamonium
