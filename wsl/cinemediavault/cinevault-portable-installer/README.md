# CineMediaVault Portable Installer

This bundle installs CineMediaVault with optional Movies, TV Shows, Comics, NES, SEGA, DOS, and MAME modules.

The bundle can also carry the Android companion APK under `android/`. The APK is optional; it connects to whatever
CineVault/CineMediaVault server URL you enter on first launch and stores offline downloads on the phone.

It does not copy your media library. It stores paths in:

```bash
/etc/cinevault/cinevault.conf
```

Installed app files go under:

```bash
/opt/cinevault
```

## Install

Interactive install:

```bash
sudo ./install.sh
```

## Android Companion APK

If `android/CineMediaVault-*.apk` is present, copy it to the phone and install it manually.

The companion app:

- Prompts for the server URL on first launch and lets you change it later from the `CmV` menu.
- Downloads movies and TV episodes for offline playback.
- Saves movie files under the selected offline folder, avoiding repeated nested `CineVault/Movies` paths.
- Caches the title, summary, and poster into the app offline index when a movie, episode, or season ZIP is downloaded.
- Shows download progress in 10% steps for APK-managed downloads.
- Keeps full-season downloads as `.zip`, extracts season ZIPs automatically, and indexes extracted video files for offline playback.
- Shows only local/offline items when the server is unavailable.

To change the offline download folder after first setup, open the app menu and choose `Change Offline Save Folder`, or use the same button on the offline library screen.

Install only Movies and TV:

```bash
sudo ./install.sh --movies --tv
```

Install only Comics later:

```bash
sudo ./install.sh --comics
```

Install only one emulator later:

```bash
sudo ./install.sh --nes
sudo ./install.sh --sega
sudo ./install.sh --dos
sudo ./install.sh --mame
```

Use an existing config without prompts:

```bash
sudo ./install.sh --all --non-interactive --config ./my-cinevault.conf
```

Skip OS package installation:

```bash
sudo ./install.sh --skip-packages
```

Copy/build only, without enabling systemd services:

```bash
sudo ./install.sh --no-services
```

## Current Media Mounts

Production now expects the old Raspberry Pi shares to be served from `.20` over Samba/CIFS:

```text
//192.168.1.20/Movies   -> /mnt/nfs-share-movies
//192.168.1.20/TV_Shows -> /mnt/nfs-share-tvshows
```

On the `.20` server those shares currently map to:

```text
/media/jnicolas/Expansion -> Movies, Books, Comics, comic-library
/media/jnicolas/Elements  -> TV Shows
```

The old `.121` defaults should not be used for new installs unless the Raspberry Pi is restored and explicitly chosen.

Example Linux client `/etc/fstab` entries:

```fstab
//192.168.1.20/Movies   /mnt/nfs-share-movies  cifs  credentials=/root/.smbcredentials,vers=3.0,sec=ntlmssp,iocharset=utf8,_netdev,nofail,x-systemd.automount,x-systemd.requires=network-online.target,x-systemd.after=network-online.target  0  0
//192.168.1.20/TV_Shows /mnt/nfs-share-tvshows cifs  credentials=/root/.smbcredentials,vers=3.0,sec=ntlmssp,iocharset=utf8,_netdev,nofail,x-systemd.automount,x-systemd.requires=network-online.target,x-systemd.after=network-online.target  0  0
```

The installer bundle includes a helper that creates those mount points and fstab lines:

```bash
sudo ./mount-media-shares.sh
```

Override defaults if needed:

```bash
sudo MOVIES_SHARE='//server/Movies' TV_SHARE='//server/TV_Shows' ./mount-media-shares.sh
```

## Required Paths

Set only the paths you want to use:

```bash
MOVIE_ROOT="/mnt/nfs-share-movies/Movies"
TV_ROOT="/mnt/nfs-share-tvshows/TV Shows"
COMICS_ROOT="/mnt/nfs-share-movies/Comics"
COMIC_LIBRARY_ROOT="/mnt/nfs-share-movies/comic-library"
NES_ROOT="/mnt/roms/nes"
SEGA_ROOT="/mnt/roms/sega"
DOS_ROOT="/mnt/roms/dos"
MAME_ROOT="/mnt/roms/mame"
```

If a path is blank, that module is skipped unless you explicitly install it later.

## Services

Main CineVault:

```bash
sudo systemctl status cinevault
sudo systemctl restart cinevault
```

Lab CineVault helper commands:

```bash
sudo -u cinevault /opt/cinevault/bin/start-cinevault-lab
sudo /opt/cinevault/bin/stop-cinevault-lab
```

The lab helper uses `CINEVAULT_LAB_PORT` from `/etc/cinevault/cinevault.conf` and keeps a separate SQLite database, HLS cache, and mobile-download cache from production.

Lab compressed downloads default to `MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO=0.40`, meaning the target output is 40% of the original source size. The server retries at lower bitrates if ffmpeg overshoots and refuses to mark a compressed download ready if it exceeds the selected target budget or grows larger than the source file.

SQLite database backups:

```bash
sudo systemctl status cinevault-db-backup.timer
sudo systemctl start cinevault-db-backup.service
ls -lh /opt/cinevault/backups
```

Backups run daily at 1:00 AM, are saved under `/opt/cinevault/backups`, and keep the latest 10 by default. Change `CINEVAULT_BACKUP_KEEP` in `/etc/cinevault/cinevault.conf` to adjust retention. Admin users can also run a manual backup from the CineVault user management page.

## Users, Approvals, and Play History

CineVault stores users, sessions, watch state, play history, pending account requests, and metadata state in SQLite:

```bash
/opt/cinevault/state/cinevault.db
```

On first run the installer creates the default protected super-admin user you configured during installation. The super-admin account cannot be deleted from the UI.

Admins can:

- approve or deny account requests submitted from the login page
- create users manually
- change user passwords
- delete non-super-admin users, which also purges that user's sessions, watch state, ratings, watchlist, and play history
- view movie and TV library totals
- view per-user play history
- clear play history for one user or all users

Play history is stored indefinitely until an admin clears it.

Optional static modules:

```bash
sudo systemctl status cinevault-static@comics
sudo systemctl status cinevault-static@nes
sudo systemctl status cinevault-static@sega
sudo systemctl status cinevault-static@dos
sudo systemctl status cinevault-static@mame
```

## Ports

Defaults:

```text
CineVault: 8093
Lab:       5000
Comics:    8110
NES:       8092
SEGA:      8094
DOS:       8091
MAME:      8101
```

Change these in `/etc/cinevault/cinevault.conf`, then restart services.

## Rebuild Modules

After adding new comics or ROMs:

```bash
sudo -u cinevault /opt/cinevault/bin/rebuild-module comics
sudo -u cinevault /opt/cinevault/bin/rebuild-module nes
sudo -u cinevault /opt/cinevault/bin/rebuild-module sega
sudo -u cinevault /opt/cinevault/bin/rebuild-module dos
sudo -u cinevault /opt/cinevault/bin/rebuild-module mame
```

## Android Offline Downloads

The Android APK in `android/` supports offline playback. The first time it connects, choose one parent save folder; the app creates `CineVault/Movies` and `CineVault/TV_Shows` under it.

Movie and individual episode downloads save the playable media plus poster/summary metadata into the APK offline index. Full-season TV downloads are served as real ZIP files with `cinemediavault-season.json` metadata inside the archive; the APK downloads the ZIP, shows progress, extracts the episodes, removes the temporary ZIP entry, and lists the extracted episodes offline with title, summary, and poster/still artwork when available.

Offline cards include a `Delete offline` control that removes the Android-side file and its offline index entry. Fix Match, poster/artwork uploads, and Admin pages are regular web actions and are not intercepted as offline downloads.

APK `1.6.1` or newer includes a stricter download click handler so tapping Admin, Fix Match, or Update Poster cannot accidentally grab a nearby Download link. TV episode Admin pages link Fix Match and Update Poster to the parent show id, which prevents `Show id not found` errors.

## RPM

On a system with `rpmbuild` installed:

```bash
./build-rpm.sh
```

The RPM installs this installer payload under:

```bash
/opt/cinevault-installer
```

Then run:

```bash
sudo /opt/cinevault-installer/install.sh
```
