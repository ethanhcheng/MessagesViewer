# Deploying on Proxmox VE with TrueNAS Core NFS

This guide sets up Messages Viewer as an unprivileged Debian LXC container on
Proxmox, with the Messages backup directory served from TrueNAS Core over NFS.

## Overview

```
[ Client browser ]  --http-->  [ Proxmox LXC ]  --bind-mount-->  [ NFS mount on Proxmox host ]  --NFS-->  [ TrueNAS Core ]
```

The NFS share is mounted on the **Proxmox host** and bind-mounted into the LXC
container. This is the standard pattern — NFS clients inside an unprivileged
LXC are blocked by the kernel, so we mount on the host and pass it through.

## 1. Export the backup folder from TrueNAS Core

On TrueNAS Core:

1. Move your Messages backup into a dataset, e.g. `tank/backups/messages`.
2. **Sharing → Unix Shares (NFS)** → **Add**:
   - **Path**: `/mnt/tank/backups/messages`
   - **Authorized networks**: e.g. `192.168.1.0/24` (your LAN)
   - **Hosts**: the Proxmox host IP, if you want to restrict further
   - **Maproot User / Group**: leave default, *or* set to `nobody`/`nogroup`
     and make the dataset world-readable for simplest access.
   - Tick **Read Only**.
3. **Services → NFS** → enable, set to start automatically.

Test from the Proxmox host:

```bash
showmount -e <truenas-ip>
# Should list /mnt/tank/backups/messages
```

## 2. Create the LXC container on Proxmox

Download a Debian 12 template if you don't have one (Proxmox UI → CT Templates,
or `pveam update && pveam download local debian-12-standard_*.tar.zst`).

```bash
# Pick a free VMID, e.g. 200
pct create 200 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname messagesviewer \
  --cores 2 \
  --memory 1024 \
  --swap 512 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --rootfs local-lvm:8 \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1
```

Adjust `--rootfs` storage name to match your setup.

## 3. NFS-mount the backup on the Proxmox host

```bash
apt-get install -y nfs-common
mkdir -p /mnt/messages-backup
# Add to /etc/fstab so it survives reboots
echo '<truenas-ip>:/mnt/tank/backups/messages /mnt/messages-backup nfs ro,soft,timeo=30,vers=4 0 0' >> /etc/fstab
mount /mnt/messages-backup
ls /mnt/messages-backup   # should show chat.db and Attachments/
```

## 4. Bind-mount the backup into the container

```bash
pct set 200 -mp0 /mnt/messages-backup,mp=/srv/messages,ro=1
```

If you used the unprivileged container above, UIDs on the host don't match
those in the container. The simplest fix for a read-only viewer:

- Ensure the NFS export maps to `nobody`/`nogroup` and the directory is
  world-readable (`chmod -R a+rX` on the NAS side), **or**
- Drop `--unprivileged 1` when creating the container (privileged LXC).
  For a read-only homelab viewer that's a reasonable trade-off.

## 5. Start the container and install the app

```bash
pct start 200
pct enter 200

# inside the container:
apt-get update && apt-get install -y git
git clone <your-fork-or-copy-of-this-repo> /opt/messagesviewer
cd /opt/messagesviewer
ADMIN_PASSWORD='choose-a-strong-pw' bash deploy/install.sh
```

The installer:
- Creates a `messagesviewer` system user.
- Builds the Python venv.
- Writes `/etc/messagesviewer.env` with your admin password.
- Installs and starts the `messagesviewer.service` systemd unit.

## 6. Use it

Find the container's IP (`pct exec 200 -- ip -4 addr show eth0`), then from
any machine on your LAN:

```
http://<container-ip>:8000/
```

1. Log in with the admin password you set.
2. On the setup screen, enter `/srv/messages` as the data directory.
3. Browse, search, filter.

## Updating

```bash
pct enter 200
cd /opt/messagesviewer
git pull
.venv/bin/pip install -r requirements.txt
systemctl restart messagesviewer
```

## Troubleshooting

- **`chat.db not found at /srv/messages/chat.db`** — check the bind mount with
  `pct config 200` and that `ls /srv/messages` works inside the container.
- **Permission denied reading `chat.db`** — the unprivileged container's
  mapped UID can't read the file. Either make the NFS export world-readable
  or recreate the container as privileged.
- **Browser can't connect** — check Proxmox firewall and that
  `ss -tlnp` inside the container shows port 8000.
