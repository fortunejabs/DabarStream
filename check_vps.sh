#!/usr/bin/env bash
# =============================================================================
# DabarStream - Oracle Linux 10 / Oracle Cloud (OCI) server diagnostic
#
# Answers two questions:
#   1. Is the firewall chain (OCI Security List -> OS firewalld -> listening
#      socket) really letting TCP 5000 through?
#   2. Which Engine.IO protocol version does the server speak? (This is what
#      leaves the OBS overlay blank when the CDN client version mismatches.)
#
# READ-ONLY - changes nothing. Run it ON the VPS:
#   scp -i <key> check_vps.sh opc@193.123.179.93:~/
#   ssh -i <key> opc@193.123.179.93 "sudo bash ~/check_vps.sh"
# =============================================================================

APP_PORT=5000
APP_PUBLIC_IP=193.123.179.93
SERVICE=dabarstream

hr() { printf '%.0s-' {1..72}; echo; }
sec() { echo; hr; echo " $*"; hr; }

sec "0. Host / OS"
. /etc/os-release 2>/dev/null && echo "OS     : $PRETTY_NAME"
echo "Kernel : $(uname -r)"
echo "Time   : $(date -Is)"

# ---------------------------------------------------------------------------
# LAYER 3 - is anything actually listening?
# ---------------------------------------------------------------------------
sec "LAYER 3a - listening sockets on TCP $APP_PORT"
if ss -tlnp 2>/dev/null | grep -q ":$APP_PORT "; then
  ss -tlnp | grep ":$APP_PORT "
  echo ">>> OK: something is listening on $APP_PORT (want 0.0.0.0:* or *:*)"
else
  echo ">>> FAIL: NOTHING is listening on $APP_PORT - no firewall rule can fix that"
fi

sec "LAYER 3b - systemd unit '$SERVICE'"
systemctl is-active "$SERVICE" 2>/dev/null || echo "(not active / unit missing)"
systemctl status "$SERVICE" --no-pager --lines=0 2>/dev/null | head -12
echo "--- last 40 journal lines ---"
journalctl -u "$SERVICE" --no-pager -n 40 2>/dev/null || echo "(no journal access)"

# ---------------------------------------------------------------------------
# LAYER 2 - the OS firewall (firewalld on Oracle Linux)
# ---------------------------------------------------------------------------
sec "LAYER 2a - firewalld"
if command -v firewall-cmd >/dev/null 2>&1; then
  echo "state        : $(firewall-cmd --state 2>/dev/null || echo 'NOT RUNNING')"
  echo "default zone : $(firewall-cmd --get-default-zone 2>/dev/null)"
  echo "active zones :"; firewall-cmd --get-active-zones 2>/dev/null
  echo; echo "--- firewall-cmd --list-all ---"; firewall-cmd --list-all 2>/dev/null
  echo; echo "--- $APP_PORT/tcp open at RUNTIME? ---"
  firewall-cmd --query-port="$APP_PORT/tcp" 2>/dev/null && echo "YES" || echo "NO"
  echo "--- $APP_PORT/tcp open PERMANENTLY (survives reboot)? ---"
  if firewall-cmd --permanent --query-port="$APP_PORT/tcp" 2>/dev/null; then
    echo "YES"
  else
    echo "NO  <-- lost on reboot. Fix with:"
    echo "    sudo firewall-cmd --permanent --add-port=$APP_PORT/tcp"
    echo "    sudo firewall-cmd --reload"
  fi
else
  echo "firewall-cmd not installed (unusual on Oracle Linux)"
fi

sec "LAYER 2b - nftables / iptables (firewalld's real backend)"
echo "--- nft: chains, policies, any 5000 mentions ---"
nft list ruleset 2>/dev/null | grep -nE "chain|policy|5000" | head -40 || echo "(nft unavailable)"
echo "--- iptables filter table ---"
iptables -S 2>/dev/null | head -30 || echo "(iptables unavailable)"

sec "LAYER 2c - SELinux"
if command -v getenforce >/dev/null 2>&1; then
  echo "getenforce : $(getenforce)"
  echo "--- is $APP_PORT in an SELinux port label? ---"
  semanage port -l 2>/dev/null | grep -E "^(http_port_t|.*_port_t)" | grep -E "\b$APP_PORT\b" || \
    echo "(no label - harmless for a plain systemd python service)"
  echo "--- recent AVC denials ---"
  ausearch -m avc -ts recent 2>/dev/null | tail -20 || echo "(none / auditd not running)"
else
  echo "SELinux tooling not present"
fi

# ---------------------------------------------------------------------------
# LAYER 1 - OCI cloud layer (not readable from inside the VM)
# ---------------------------------------------------------------------------
sec "LAYER 1 - OCI Security List / Network Security Group"
cat <<'EON'
Cannot be read from inside the instance. Check in the OCI Console:
  Networking -> Virtual Cloud Networks -> <VCN> -> Subnets -> <subnet>
      -> Security Lists -> Ingress Rules
  ...and also: Network Security Groups (NSGs) attached to the VNIC.
Required Ingress rule:
    Source Type       : CIDR
    Source CIDR       : 0.0.0.0/0   (or just your church's public IP, safer)
    IP Protocol       : TCP
    Source Port Range : (LEAVE EMPTY)
    Destination Port  : 5000
Classic mistake: typing 5000 into SOURCE Port Range. That is the client's
ephemeral port; the server port belongs in DESTINATION Port Range.
EON

# ---------------------------------------------------------------------------
# END-TO-END PROBES
# ---------------------------------------------------------------------------
sec "PROBE 1 - loopback (is the app alive?)"
curl -s -o /dev/null -w "GET /                  -> %{http_code}\n" --max-time 8 \
  "http://127.0.0.1:$APP_PORT/" || echo "loopback FAILED"

sec "PROBE 2 - own public IP (exercises the full local netfilter chain)"
curl -s -o /dev/null -w "GET /socket.io.js      -> %{http_code}\n" --max-time 8 \
  "http://$APP_PUBLIC_IP:$APP_PORT/socket.io/socket.io.js"
echo "NOTE: 400 here is EXPECTED on python-engineio 4.x - it no longer serves"
echo "      the client .js file. A 400 still proves the request REACHED the app."

sec "PROBE 3 - Engine.IO protocol version   <<< THE DECISIVE TEST"
echo "  EIO=4 -> Socket.IO JS client v3.x / v4.x  (modern Flask-SocketIO)"
echo "  EIO=3 -> Socket.IO JS client v1.x / v2.x  (python-engineio 3.x)"
echo "  EIO=2 -> Socket.IO JS client v0.9"
echo
for v in 4 3 2; do
  url="http://$APP_PUBLIC_IP:$APP_PORT/socket.io/?EIO=$v&transport=polling&t=$RANDOM"
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$url")
  body=$(curl -s --max-time 8 "$url" | head -c 150)
  printf "  EIO=%s -> HTTP %-3s  %s\n" "$v" "$code" "$body"
done
echo
cat <<'EON'
HOW TO READ IT:
  * Exactly ONE version answers 200 with JSON containing a "sid". The others
    answer 400 "The client is using an unsupported version of the Socket.IO or
    Engine.IO protocols".
  * The version that returns 200 is what the SERVER speaks. The <script> tag in
    the OBS overlay MUST use a matching JS client:
        200 on EIO=4 ->  cdnjs socket.io 4.0.1 (or 4.7.5)   <- modern default
        200 on EIO=3 ->  cdnjs socket.io 2.5.0
        200 on EIO=2 ->  cdnjs socket.io 0.9.x
  * Mismatch = every browser handshake gets 400, `io` never connects, and the
    overlay stays blank. That is Flask-SocketIO issue #1424 exactly.
  * If NO version returns 200, the app itself is broken - read the journal.
EON

sec "Installed Socket.IO stack versions"
for p in flask-socketio python-socketio python-engineio eventlet gevent simple-websocket; do
  printf '  %-18s %s\n' "$p" "$(python3 -m pip show "$p" 2>/dev/null | awk '/^Version:/{print $2}')"
done

sec "How the app is running"
ps -eo pid,user,etime,cmd 2>/dev/null | grep -Ei "server\.py|gunicorn|eventlet|werkzeug" | grep -v grep || \
  echo "(no obvious python web process found)"

sec "Socket.IO client <script> tag in the served source"
if [ -r /opt/dabarstream/server.py ]; then
  grep -nE "socket\.io[^\"']*socket\.io\.js" /opt/dabarstream/server.py | head -20 || \
    echo "(no socket.io client <script> tag found)"
else
  echo "(cannot read /opt/dabarstream/server.py)"
fi

sec "DONE"

