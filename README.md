# Messages Viewer

A self-hosted web app for browsing an archived macOS Messages database (`chat.db` +
`Attachments/`). Runs on your NAS; access it from any machine with a browser.

## How it works

- Backend: FastAPI (Python). Reads the SQLite `chat.db` in read-only mode.
- UI: Plain HTML/CSS/JS served by the backend. Styled to look like macOS Messages.
- Auth: Single admin password (set via env var). Session cookie after login.
- Data location: Configured through a setup screen — point it at the directory
  containing `chat.db` and `Attachments/`.

## Backing up your Messages data on macOS

On the Mac whose messages you want to archive:

1. Quit Messages.app.
2. Copy the entire `~/Library/Messages/` folder (Finder → Go → Go to Folder → `~/Library/Messages`).
3. Move that copy onto your NAS, e.g. `/mnt/nas/messages-backup-2025/`. The folder
   must end up containing at least:
   - `chat.db`
   - `Attachments/`

> Catalina+ requires Full Disk Access for whatever tool you use to copy.

## Install on the NAS

Requires Python 3.10+.

```bash
git clone <this-repo> /opt/messagesviewer
cd /opt/messagesviewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
export MV_ADMIN_PASSWORD='choose-a-strong-password'
# Optional: where to store the config file (default ./config.json)
# export MV_CONFIG_PATH=/var/lib/messagesviewer/config.json

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then from any machine on the network, open:

```
http://<nas-hostname-or-ip>:8000/
```

1. Log in with your `MV_ADMIN_PASSWORD`.
2. On first run, enter the absolute path to the backup directory (e.g.
   `/mnt/nas/messages-backup-2025`). The app verifies `chat.db` exists.
3. Browse conversations, search, and filter.

You can change the data directory later via the **Change data directory** link
in the sidebar footer.

## Deploy as a Proxmox LXC container (recommended)

### One-line install (Proxmox host)

On your Proxmox VE host, as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ethanhcheng/MessagesViewer/main/deploy/proxmox-installer.sh)"
```

The installer creates a Debian 12 LXC, optionally configures an NFS mount
from your NAS, clones this repo into the CT, runs the in-container installer,
and prints the URL when done. It prompts for an app password and optional
NFS details; or pass them via env vars for fully unattended runs:

```bash
ADMIN_PASSWORD='choose-a-strong-pw' \
NFS_SERVER='192.168.1.10' \
NFS_EXPORT='/mnt/tank/backups/messages' \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ethanhcheng/MessagesViewer/main/deploy/proxmox-installer.sh)"
```

See the script header for all available env vars (`CTID`, `STORAGE`,
`UNPRIVILEGED`, etc.).

### Manual setup

For step-by-step manual installation, see **[deploy/PROXMOX.md](deploy/PROXMOX.md)**.

## Run as a service on any Linux host

Inside any Debian/Ubuntu system, after cloning to `/opt/messagesviewer`:

```bash
ADMIN_PASSWORD='your-password' bash deploy/install.sh
```

This installs `messagesviewer.service` (see `deploy/messagesviewer.service`)
and starts it on port 8000.

## Security notes

- The app exposes attachments under `/api/attachments/{id}`. Path traversal is
  blocked — files outside the configured data directory are rejected.
- `chat.db` is opened read-only via SQLite URI mode.
- Sessions are in-memory: restarting the server logs everyone out.
- Run behind a reverse proxy with TLS if exposing outside your LAN.

## Limitations

- Search currently matches the plaintext `text` column only. Messages on
  macOS Ventura+ store text in the `attributedBody` blob; those bodies are
  decoded for *display* but not yet indexed for search. A future indexer
  could materialize decoded text into an FTS table.
- No "send message" capability — viewer only.
- Reactions, tapbacks, and edited/unsent messages aren't rendered specially yet.
