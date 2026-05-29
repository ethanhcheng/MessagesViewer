#!/usr/bin/env bash
# Update Messages Viewer in place: git pull, refresh Python deps, restart service.
# Run inside the LXC as root, or from the Proxmox host via:
#   pct exec <CTID> -- bash /opt/messagesviewer/deploy/update.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/messagesviewer}"

echo "==> Pulling latest from $(git -C "$APP_DIR" remote get-url origin 2>/dev/null || echo 'origin')"
git -c safe.directory="$APP_DIR" -C "$APP_DIR" pull --ff-only

echo "==> Refreshing Python deps"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Restarting service"
systemctl restart messagesviewer

echo "==> Recent logs:"
journalctl -u messagesviewer -n 15 --no-pager
