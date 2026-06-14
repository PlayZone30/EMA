#!/bin/bash
# Install the live-trader systemd service on the EC2 instance.
# ===========================================================
# Run ONCE on the instance (with sudo privileges):
#     bash install_service.sh
#
# Afterwards the trader + dashboard auto-start on every boot. Because the EC2
# instance is stopped/started (not terminated), the EBS volume — and therefore
# live_dashboard.db + live_5min_trades.csv — persists across days. The collector
# resumes its running capital from the DB on each start.
#
# Useful commands after install:
#     sudo systemctl status  live-trader
#     sudo journalctl -u live-trader -f
#     tail -f service.log
#     sudo systemctl restart live-trader
#     sudo systemctl disable live-trader   # stop auto-start on boot

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="$(whoami)"
SERVICE_NAME="live-trader"
TEMPLATE="$SCRIPT_DIR/live-trader.service"
TARGET="/etc/systemd/system/${SERVICE_NAME}.service"

if [ ! -f "$TEMPLATE" ]; then
    echo "❌ Template not found: $TEMPLATE"
    exit 1
fi

echo "Installing $SERVICE_NAME"
echo "  User: $RUN_USER"
echo "  Dir : $SCRIPT_DIR"

chmod +x "$SCRIPT_DIR/run.sh"

# Substitute placeholders and install the unit.
sed -e "s|__USER__|${RUN_USER}|g" \
    -e "s|__DIR__|${SCRIPT_DIR}|g" \
    "$TEMPLATE" | sudo tee "$TARGET" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo ""
echo "✅ Installed and enabled. It will auto-start on every boot."
echo "   Start now:   sudo systemctl start $SERVICE_NAME"
echo "   Status:      sudo systemctl status $SERVICE_NAME"
echo "   Live logs:   sudo journalctl -u $SERVICE_NAME -f"
