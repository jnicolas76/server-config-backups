#!/usr/bin/env bash
set -euo pipefail

# CineMediaVault client mount helper.
# Creates the stable client mount points expected by CineMediaVault and the
# transcode orchestrator:
#   /mnt/nfs-share-movies
#   /mnt/nfs-share-tvshows
#
# Current production shares are served from 192.168.1.20:
#   //192.168.1.20/Movies
#   //192.168.1.20/TV_Shows

MOVIES_SHARE="${MOVIES_SHARE:-//192.168.1.20/Movies}"
TV_SHARE="${TV_SHARE:-//192.168.1.20/TV_Shows}"
MOVIES_MOUNT="${MOVIES_MOUNT:-/mnt/nfs-share-movies}"
TV_MOUNT="${TV_MOUNT:-/mnt/nfs-share-tvshows}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-/root/.smbcredentials}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

if [[ ! -f "$CREDENTIALS_FILE" ]]; then
  cat >&2 <<EOF
Missing credentials file: $CREDENTIALS_FILE

Create it with:
  sudo install -m 600 /dev/null $CREDENTIALS_FILE
  sudo sh -c 'printf "username=jnicolas\\npassword=YOUR_PASSWORD\\n" > $CREDENTIALS_FILE'
EOF
  exit 1
fi

mkdir -p "$MOVIES_MOUNT" "$TV_MOUNT"

movies_line="${MOVIES_SHARE} ${MOVIES_MOUNT} cifs credentials=${CREDENTIALS_FILE},vers=3.0,sec=ntlmssp,iocharset=utf8,_netdev,nofail,x-systemd.automount,x-systemd.requires=network-online.target,x-systemd.after=network-online.target 0 0"
tv_line="${TV_SHARE} ${TV_MOUNT} cifs credentials=${CREDENTIALS_FILE},vers=3.0,sec=ntlmssp,iocharset=utf8,_netdev,nofail,x-systemd.automount,x-systemd.requires=network-online.target,x-systemd.after=network-online.target 0 0"

touch /etc/fstab
grep -Fq "$MOVIES_MOUNT" /etc/fstab || echo "$movies_line" >> /etc/fstab
grep -Fq "$TV_MOUNT" /etc/fstab || echo "$tv_line" >> /etc/fstab

systemctl daemon-reload 2>/dev/null || true
mount "$MOVIES_MOUNT" || true
mount "$TV_MOUNT" || true

echo "Movies mount:"
findmnt "$MOVIES_MOUNT" || true
echo "TV mount:"
findmnt "$TV_MOUNT" || true
