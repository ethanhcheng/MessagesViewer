#!/usr/bin/env bash
# Messages Viewer — Proxmox VE one-shot installer.
#
# Run on the Proxmox host:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/<you>/messagesviewer/main/deploy/proxmox-installer.sh)"
#
# Creates an unprivileged Debian 12 LXC, optionally mounts an NFS share from
# your NAS, installs the app inside, and prints the URL.
#
# Env-var overrides (all optional — script will prompt if missing in TTY mode):
#   CTID                 e.g. 200                    (default: next free >=200)
#   HOSTNAME             container hostname          (default: messagesviewer)
#   STORAGE              Proxmox storage for rootfs  (default: prompt / auto-detect)
#   TEMPLATE_STORAGE     where templates live        (default: prompt / auto-detect)
#   BRIDGE               network bridge              (default: vmbr0)
#   DISK_GB              rootfs size in GB           (default: 8)
#   MEMORY_MB            RAM in MB                   (default: 1024)
#   CORES                CPU cores                   (default: 2)
#   UNPRIVILEGED         1 or 0                      (default: 0 — privileged, simpler NFS perms)
#   ADMIN_USER           app login username          (default: admin)
#   ADMIN_PASSWORD       app login password          (REQUIRED)
#   CT_ROOT_PASSWORD     root password for the LXC   (REQUIRED — used for console/SSH login)
#   NFS_SERVER           e.g. 192.168.1.10           (skip mount setup if empty)
#   NFS_EXPORT           e.g. /mnt/tank/backups/messages
#   NFS_VERS             3, 4, 4.1, 4.2              (default: auto-negotiate)
#   HOST_MOUNT           where to mount on host      (default: /mnt/messages-backup)
#   CT_MOUNT             where to expose inside CT   (default: /srv/messages)
#   REPO_URL             git repo to clone in CT     (default: $REPO_URL_DEFAULT below)
#   REPO_REF             branch/tag to check out     (default: main)
#
set -euo pipefail

REPO_URL_DEFAULT="https://github.com/ethanhcheng/MessagesViewer.git"

# ---------- ui helpers ----------
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info()  { printf "${BLUE}[i]${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}[✓]${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}[!]${NC} %s\n" "$*"; }
die()   { printf "${RED}[x]${NC} %s\n" "$*" >&2; exit 1; }

prompt() {
  local var_name="$1" default="$2" prompt_text="$3" silent="${4:-0}"
  local current="${!var_name:-}"
  if [[ -n "$current" ]]; then return; fi
  if [[ ! -t 0 ]]; then
    # Non-interactive — fall back to default if provided
    if [[ -n "$default" ]]; then
      printf -v "$var_name" '%s' "$default"
      return
    fi
    die "$var_name is required (non-interactive); set it as env var."
  fi
  local val=""
  if [[ "$silent" == "1" ]]; then
    read -rsp "$prompt_text${default:+ [$default]}: " val; echo
  else
    read -rp "$prompt_text${default:+ [$default]}: " val
  fi
  printf -v "$var_name" '%s' "${val:-$default}"
}

# Prompt for a password twice and confirm they match. Skips if already set via env.
prompt_password_confirm() {
  local var_name="$1" label="$2"
  local current="${!var_name:-}"
  if [[ -n "$current" ]]; then return; fi
  if [[ ! -t 0 ]]; then
    die "$var_name is required (non-interactive); set it as env var."
  fi
  local p1="" p2=""
  while :; do
    read -rsp "$label: " p1; echo
    if [[ -z "$p1" ]]; then echo "Password cannot be empty."; continue; fi
    read -rsp "$label (confirm): " p2; echo
    if [[ "$p1" == "$p2" ]]; then
      printf -v "$var_name" '%s' "$p1"
      return
    fi
    warn "Passwords do not match. Try again."
  done
}

# Pick a Proxmox storage that supports a given content type (rootdir | vztmpl).
# Validates an env var if set, auto-picks if only one option, otherwise prompts.
pick_storage() {
  local var_name="$1" content="$2" label="$3"
  local current="${!var_name:-}"
  local options=()
  while IFS= read -r line; do
    [[ -n "$line" ]] && options+=("$line")
  done < <(pvesm status --content "$content" 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')

  if [[ ${#options[@]} -eq 0 ]]; then
    die "No active Proxmox storage supports content type '$content'. Configure one in Datacenter → Storage."
  fi

  if [[ -n "$current" ]]; then
    local match=0
    for s in "${options[@]}"; do [[ "$s" == "$current" ]] && match=1; done
    if [[ $match -eq 0 ]]; then
      die "$var_name='$current' not found. Available for $content: ${options[*]}"
    fi
    info "$label: $current"
    return
  fi

  if [[ ${#options[@]} -eq 1 ]]; then
    printf -v "$var_name" '%s' "${options[0]}"
    info "$label: ${options[0]} (only option)"
    return
  fi

  if [[ ! -t 0 ]]; then
    printf -v "$var_name" '%s' "${options[0]}"
    warn "$label: defaulting to ${options[0]} (non-interactive). Override with $var_name=…"
    return
  fi

  echo
  echo "Available storages for $label (content=$content):"
  local i=1
  for s in "${options[@]}"; do
    printf "  %d) %s\n" "$i" "$s"
    i=$((i + 1))
  done
  local choice=""
  while :; do
    read -rp "Select [1-${#options[@]}]: " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#options[@]} )); then
      printf -v "$var_name" '%s' "${options[$((choice - 1))]}"
      info "$label: ${options[$((choice - 1))]}"
      return
    fi
    echo "Invalid choice."
  done
}

# ---------- preflight ----------
[[ $EUID -eq 0 ]] || die "Run as root on the Proxmox host."
command -v pveversion >/dev/null || die "pveversion not found — this script must run on a Proxmox VE host."
command -v pct >/dev/null || die "pct not found."
command -v pveam >/dev/null || die "pveam not found."
command -v pvesm >/dev/null || die "pvesm not found."

info "Detected $(pveversion | head -n1)"

# ---------- gather config ----------
echo
echo "== App login (used to sign in to the Messages Viewer web UI) =="
prompt ADMIN_USER "admin" "App login username"
prompt_password_confirm ADMIN_PASSWORD "App login password"
[[ -n "$ADMIN_PASSWORD" ]] || die "ADMIN_PASSWORD required."

echo
echo "== LXC root password (used to log into the container console / SSH as root) =="
prompt_password_confirm CT_ROOT_PASSWORD "LXC root password"
[[ -n "$CT_ROOT_PASSWORD" ]] || die "CT_ROOT_PASSWORD required."

: "${HOSTNAME:=messagesviewer}"
pick_storage STORAGE rootdir "Rootfs storage"
pick_storage TEMPLATE_STORAGE vztmpl "Template storage"
: "${BRIDGE:=vmbr0}"
: "${DISK_GB:=8}"
: "${MEMORY_MB:=1024}"
: "${CORES:=2}"
: "${UNPRIVILEGED:=0}"
: "${HOST_MOUNT:=/mnt/messages-backup}"
: "${CT_MOUNT:=/srv/messages}"
: "${REPO_URL:=$REPO_URL_DEFAULT}"
: "${REPO_REF:=main}"

if [[ -z "${CTID:-}" ]]; then
  CTID=200
  while pct status "$CTID" >/dev/null 2>&1; do CTID=$((CTID + 1)); done
fi
info "Using CTID=$CTID, HOSTNAME=$HOSTNAME, STORAGE=$STORAGE"

prompt NFS_SERVER "" "NFS server IP (blank to skip mount setup)"
if [[ -n "${NFS_SERVER}" ]]; then
  prompt NFS_EXPORT "" "NFS export path (e.g. /mnt/tank/backups/messages)"
  [[ -n "$NFS_EXPORT" ]] || die "NFS_EXPORT required when NFS_SERVER is set."
fi

if [[ "$REPO_URL" == *REPLACE_ME* ]]; then
  warn "REPO_URL is still the placeholder. Set REPO_URL env var to your fork URL."
  prompt REPO_URL "" "Git repo URL to clone in the container"
  [[ "$REPO_URL" != *REPLACE_ME* ]] || die "REPO_URL still has REPLACE_ME — aborting."
fi

# ---------- template ----------
TEMPLATE_PATTERN="debian-12-standard"
info "Updating template catalog…"
pveam update >/dev/null
TEMPLATE_NAME="$(pveam available --section system | awk -v p="$TEMPLATE_PATTERN" '$2 ~ p {print $2}' | sort -V | tail -n1)"
[[ -n "$TEMPLATE_NAME" ]] || die "No $TEMPLATE_PATTERN template found in pveam catalog."

TEMPLATE_PATH="$TEMPLATE_STORAGE:vztmpl/$TEMPLATE_NAME"
if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE_NAME"; then
  info "Downloading template $TEMPLATE_NAME to $TEMPLATE_STORAGE…"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE_NAME"
fi
ok "Template ready: $TEMPLATE_PATH"

# ---------- nfs mount on host ----------
if [[ -n "${NFS_SERVER:-}" ]]; then
  info "Setting up NFS mount on Proxmox host: $NFS_SERVER:$NFS_EXPORT → $HOST_MOUNT"
  command -v mount.nfs >/dev/null || apt-get install -y nfs-common
  mkdir -p "$HOST_MOUNT"

  # Unmount and rewrite any stale fstab line for this mountpoint before retrying.
  mountpoint -q "$HOST_MOUNT" && umount "$HOST_MOUNT" 2>/dev/null || true
  sed -i "\|[[:space:]]${HOST_MOUNT}[[:space:]]|d" /etc/fstab

  attempt_mount() {
    local opts="$1"
    local fstab_line="$NFS_SERVER:$NFS_EXPORT $HOST_MOUNT nfs $opts 0 0"
    echo "$fstab_line" >> /etc/fstab
    systemctl daemon-reload >/dev/null 2>&1 || true
    if mount "$HOST_MOUNT" 2>/tmp/nfs_mount_err; then
      ok "Mounted with options: $opts"
      return 0
    fi
    warn "Mount failed with '$opts': $(cat /tmp/nfs_mount_err)"
    sed -i "\|[[:space:]]${HOST_MOUNT}[[:space:]]|d" /etc/fstab
    return 1
  }

  mounted=0
  if [[ -n "${NFS_VERS:-}" ]]; then
    attempt_mount "ro,soft,timeo=30,vers=${NFS_VERS}" && mounted=1
  else
    # Auto-negotiate first (mount.nfs tries v4.2 → v4.1 → v4 → v3).
    attempt_mount "ro,soft,timeo=30" && mounted=1
    # Some servers (e.g. TrueNAS Core out of the box) only enable v3 and refuse the v4 probe.
    if [[ $mounted -eq 0 ]]; then
      info "Retrying with vers=3 (typical for TrueNAS Core)…"
      attempt_mount "ro,soft,timeo=30,vers=3" && mounted=1
    fi
  fi

  if [[ $mounted -eq 0 ]]; then
    die "NFS mount failed. Verify the export with: showmount -e $NFS_SERVER"
  fi

  [[ -f "$HOST_MOUNT/chat.db" ]] \
    && ok "Found chat.db at $HOST_MOUNT" \
    || warn "chat.db not found in $HOST_MOUNT (you can still set the data dir later in the UI)."
fi

# ---------- create ct ----------
info "Creating LXC $CTID…"
pct create "$CTID" "$TEMPLATE_PATH" \
  --hostname "$HOSTNAME" \
  --cores "$CORES" \
  --memory "$MEMORY_MB" \
  --swap 512 \
  --net0 "name=eth0,bridge=$BRIDGE,ip=dhcp" \
  --rootfs "$STORAGE:$DISK_GB" \
  --unprivileged "$UNPRIVILEGED" \
  --features nesting=1 \
  --onboot 1 \
  --password "$CT_ROOT_PASSWORD"

if [[ -n "${NFS_SERVER:-}" ]]; then
  info "Bind-mounting $HOST_MOUNT → $CT_MOUNT (ro)"
  pct set "$CTID" -mp0 "$HOST_MOUNT,mp=$CT_MOUNT,ro=1"
fi

info "Starting CT…"
pct start "$CTID"
# Wait for network
for _ in {1..30}; do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then break; fi
  sleep 1
done

# ---------- install inside ct ----------
info "Installing dependencies inside CT…"
pct exec "$CTID" -- bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends git ca-certificates
'

info "Cloning $REPO_URL (ref: $REPO_REF) into /opt/messagesviewer…"
pct exec "$CTID" -- bash -c "
  set -e
  git clone --depth 1 --branch '$REPO_REF' '$REPO_URL' /opt/messagesviewer
"

info "Running in-container installer…"
pct exec "$CTID" -- bash -c "
  set -e
  cd /opt/messagesviewer
  ADMIN_USER='$ADMIN_USER' ADMIN_PASSWORD='$ADMIN_PASSWORD' bash deploy/install.sh
"

# ---------- summary ----------
CT_IP="$(pct exec "$CTID" -- ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
echo
ok "Messages Viewer is installed."
echo
echo "  Container:    $CTID ($HOSTNAME)"
echo "  IP:           ${CT_IP:-<not yet assigned, run \`pct exec $CTID -- ip a\`>}"
echo "  URL:          http://${CT_IP:-<ct-ip>}:8000/"
echo "  Login:        with the password you supplied"
if [[ -n "${NFS_SERVER:-}" ]]; then
  echo "  Data dir:     $CT_MOUNT  (already bind-mounted from $NFS_SERVER:$NFS_EXPORT)"
  echo "                Enter that path on the setup screen."
else
  echo "  Data dir:     not configured — mount your backup into the CT and set the path in the UI."
fi
echo
