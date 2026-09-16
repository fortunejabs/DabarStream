#!/usr/bin/env bash
# DabarStream - one-shot VPS deployment helper for Oracle Linux 10
# Run as root ON THE VPS after copying server.py, importer.py, bible.db (optional):
#   sudo bash deploy_vps.sh
set -euo pipefail

APP_DIR=/opt/dabarstream
SERVICE_NAME=dabarstream
SERVICE_USER=dabarstream

echo "[1/6] Installing system packages..."
dnf install -y python3 python3-pip firewalld

echo "[2/6] Creating service user and app directory..."
id -u "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /sbin/nologin "$SERVICE_USER"
mkdir -p "$APP_DIR"

echo "[3/6] Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install flask flask-socketio

echo "[4/6] Installing application files..."
# Assumes server.py / importer.py / bible.db are in the current directory
cp -v server.py importer.py "$APP_DIR"/
[ -f bible.db ] && cp -v bible.db "$APP_DIR"/ || echo "  NOTE: no bible.db found - run importer.py first (see below)"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

echo "[5/6] Opening firewall port 5000..."
firewall-cmd --zone=public --add-port=5000/tcp --permanent
firewall-cmd --reload

echo "[6/6] Installing and starting systemd service..."
cp "$SERVICE_NAME.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager

cat <<'EOF'

DONE. Reminders:
 - If bible.db is missing: python3 importer.py (after uncommenting an import call)
 - OCI Console: VCN -> Subnet -> Security List -> Add Ingress Rule:
     Source 0.0.0.0/0 (or your IP), TCP, Dest Port 5000
 - Overlay URL for OBS: http://<VPS_IP>:5000/overlay  (1920x1080, transparent)
 - Control panel:      http://<VPS_IP>:5000/control
 - Logs: journalctl -u dabarstream -f
EOF
