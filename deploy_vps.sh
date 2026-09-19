#!/usr/bin/env bash
# DabarStream - one-shot VPS deployment helper for Oracle Linux 10
# Run as root ON THE VPS with server.py, importer.py, bible.db and
# dabarstream.service in the current directory:
#   sudo bash deploy_vps.sh
#
# Dependencies are installed into a virtualenv, NOT the system python: Oracle
# Linux 9+/10 mark the system interpreter as "externally managed", so a plain
# `pip install` is refused outright (PEP 668).
set -euo pipefail

APP_DIR=/opt/dabarstream
SERVICE_NAME=dabarstream
SERVICE_USER=dabarstream
VENV="$APP_DIR/.venv"
PORT=5000

echo "[1/7] Installing system packages..."
dnf install -y python3 python3-pip firewalld

echo "[2/7] Checking required files are present..."
missing=0
for f in server.py importer.py "$SERVICE_NAME.service"; do
  if [ ! -f "$f" ]; then echo "  MISSING: $f" >&2; missing=1; fi
done
[ "$missing" -eq 0 ] || { echo "Copy the missing files here, then re-run." >&2; exit 1; }
if [ -f bible.db ]; then
  echo "  bible.db found ($(du -h bible.db | cut -f1)) - will be installed"
else
  echo "  NOTE: no bible.db in this directory."
  echo "        Build it locally (python import_bibles.py), copy it here, re-run."
  echo "        The service still starts, but /health will report 0 verses."
fi

echo "[3/7] Creating service user and app directory..."
id -u "$SERVICE_USER" &>/dev/null || useradd --system --no-create-home --shell /sbin/nologin "$SERVICE_USER"
mkdir -p "$APP_DIR"

echo "[4/7] Installing application files..."
cp -v server.py importer.py "$APP_DIR"/
if [ -f bible.db ]; then cp -v bible.db "$APP_DIR"/; fi

echo "[5/7] Creating virtualenv and installing dependencies..."
python3 -m venv "$VENV"
[ -x "$VENV/bin/python" ] || { echo "venv creation failed - is python3-venv installed?" >&2; exit 1; }
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install flask flask-socketio
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

echo "[6/7] Opening firewall port $PORT..."
systemctl enable --now firewalld 2>/dev/null \
  || echo "  firewalld unavailable - relying on the OCI VCN security list"
if firewall-cmd --state &>/dev/null; then
  firewall-cmd --zone=public --add-port="$PORT"/tcp --permanent
  firewall-cmd --reload
else
  echo "  firewalld not running - skipped (open TCP $PORT in the OCI VCN ingress rules)"
fi

echo "[7/7] Installing and starting systemd service..."
install -m 0644 "$SERVICE_NAME.service" /etc/systemd/system/
systemctl daemon-reload
# A start failure here is usually the DABARSTREAM_KEY placeholder guard; report
# it clearly instead of aborting with a bare non-zero exit from set -e.
systemctl enable --now "$SERVICE_NAME" \
  || echo "  service did NOT start - check: journalctl -u $SERVICE_NAME -n 20 (did you set a real DABARSTREAM_KEY?)"
sleep 2
systemctl status "$SERVICE_NAME" --no-pager || true

cat <<EOF

DONE. Verify ON the VPS first:
  curl -s http://127.0.0.1:$PORT/health && echo
Expected: {"status":"ok", ..., "db_exists":true, "verses":<big number>,
           "translations":33}
If "verses" is 0 or db_error is set, bible.db was not copied - fix and re-run.

Then from your PC once the ingress rule exists:
  curl -s http://<VPS_IP>:$PORT/health

Reminders (the script cannot do these):
 - OCI Console: VCN -> Subnet -> Security List -> Add Ingress Rule:
     Source: your home IP (recommended) or 0.0.0.0/0, Protocol TCP, Port $PORT
 - Set a real DABARSTREAM_KEY in /etc/systemd/system/$SERVICE_NAME.service
   (the service refuses to start while the placeholder is unchanged), then:
     systemctl daemon-reload && systemctl restart $SERVICE_NAME
 - Overlay for OBS:  http://<VPS_IP>:$PORT/overlay   (1920x1080, transparent)
 - Control panel:    http://<VPS_IP>:$PORT/control   (enter the same stream key)
 - Logs:             journalctl -u $SERVICE_NAME -f
EOF
