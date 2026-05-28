#!/usr/bin/env bash
# Install Messages Viewer inside a Debian/Ubuntu LXC container.
# Run as root inside the container after cloning the repo to /opt/messagesviewer.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/messagesviewer}"
APP_USER="${APP_USER:-messagesviewer}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

if [[ -z "$ADMIN_PASSWORD" ]]; then
  echo "ERROR: set ADMIN_PASSWORD env var before running this script." >&2
  echo "Example: ADMIN_USER='admin' ADMIN_PASSWORD='choose-a-strong-pw' bash deploy/install.sh" >&2
  exit 1
fi

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip git ca-certificates

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "==> Creating service user: $APP_USER"
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> Setting up virtualenv at $APP_DIR/.venv"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

mkdir -p /var/lib/messagesviewer
chown -R "$APP_USER:$APP_USER" "$APP_DIR" /var/lib/messagesviewer

echo "==> Writing environment file /etc/messagesviewer.env"
umask 077
cat > /etc/messagesviewer.env <<EOF
MV_ADMIN_USER=$ADMIN_USER
MV_ADMIN_PASSWORD=$ADMIN_PASSWORD
MV_CONFIG_PATH=/var/lib/messagesviewer/config.json
EOF
chmod 600 /etc/messagesviewer.env
chown root:"$APP_USER" /etc/messagesviewer.env

echo "==> Installing systemd unit"
install -m 0644 deploy/messagesviewer.service /etc/systemd/system/messagesviewer.service
systemctl daemon-reload
systemctl enable --now messagesviewer.service

echo
echo "Done. Status:"
systemctl --no-pager --lines=5 status messagesviewer.service || true
echo
echo "The viewer is listening on port 8000."
echo "Find the container IP with: ip -4 addr show eth0"
