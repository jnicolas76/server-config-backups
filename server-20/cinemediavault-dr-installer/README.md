# CineMediaVault

A self-hosted media server for a household: movies, television, music, books,
comics and retro games, plus live television and recording from an HDHomeRun
tuner. It runs on one Ubuntu machine, keeps your media exactly where it already
is, and is set up through a web page rather than a configuration file.

This repository is the **installer**. It takes a clean Ubuntu 22.04 or 24.04
machine and produces a working CineMediaVault.

```bash
sudo ./install.sh
```

That prints an address and a one-time code. Open the address, enter the code,
and answer about a dozen plain-language questions.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Hardware and VM sizing](#hardware-and-vm-sizing)
- [Supported systems and prerequisites](#supported-systems-and-prerequisites)
- [Quick start](#quick-start)
- [The setup wizard, step by step](#the-setup-wizard-step-by-step)
- [Unattended installation](#unattended-installation)
- [Storage and folder layout](#storage-and-folder-layout)
- [Network ports and firewall](#network-ports-and-firewall)
- [HTTPS and local domain names](#https-and-local-domain-names)
- [Live TV, the guide, and the DVR](#live-tv-the-guide-and-the-dvr)
- [Metadata, subtitles and Whisper](#metadata-subtitles-and-whisper)
- [Transcoding](#transcoding)
- [The libraries](#the-libraries)
- [Creators and generators](#creators-and-generators)
- [The Android companion](#the-android-companion)
- [Backup, restore, upgrade and migration](#backup-restore-upgrade-and-migration)
- [Security and secrets](#security-and-secrets)
- [Monitoring, logs and troubleshooting](#monitoring-logs-and-troubleshooting)
- [Configuration reference](#configuration-reference)
- [Testing and clean-VM acceptance](#testing-and-clean-vm-acceptance)
- [Known limitations](#known-limitations)
- [Licences and third-party notices](#licences-and-third-party-notices)

---

## What it does

**Movies and television.** Scans your existing folders, matches titles against
The Movie Database for posters, summaries, cast and genres, and remembers where
everyone stopped watching. A manual **Fix Match** sticks: corrections are stored
against a stable identity, so a rescan - or re-encoding a file from `.mkv` to
`.mp4` - does not lose them.

**Music.** Artists, albums, tracks and genres from your own files, with
gapless-feeling browser playback, a full-screen now-playing view, queue, shuffle
and repeat, per-user playlists, and a resume position that survives closing the
tab. On Android the companion app keeps playing with lock-screen controls.

**Book Vault.** EPUB and PDF with covers and metadata, and a reader in the
browser.

**Comics.** CBZ and CBR archives turned into a browsable, readable library.

**Games.** Browser-playable retro libraries for NES, SEGA, DOS, arcade and more.
You supply the game files.

**Live TV and recording.** With an HDHomeRun tuner: a six-hour scrolling guide
grid, watch live, record one showing or a whole series, and optionally a guide
that reaches about two weeks instead of the tuner's own two days.

**Video Wall.** Four things at once on one screen, each remembering its own
position.

**Downloads.** Take a film or a season with you, optionally compressed to a size
budget for a phone.

**Subtitles.** Uses the subtitle files and embedded tracks you already have.
Optionally looks up missing ones online, or generates them locally from the
audio. Nothing you already have is ever overwritten.

**Color themes.** Gold (the default), royal blue, or green, picked from
Account settings. It is saved to the account in SQLite, not the browser, so
it follows a user to any device or new session, and it is applied
server-side before the page is sent, so there is no flash of the default
theme. See [docs/ACCOUNT-THEMES.md](docs/ACCOUNT-THEMES.md).

**Usage analytics.** Admin → Active & Historical Usage shows real measured
bandwidth (not inferred from source bitrate), CineVault vs. system CPU, and
per-user/per-session breakdowns, live and over a selectable time range (15
minutes to 30 days, or custom), with dependency-free charts and configurable
retention. The Video Wall's compact bandwidth line links straight to it. See
[docs/USAGE-ANALYTICS.md](docs/USAGE-ANALYTICS.md).

**Genre discovery and virtual channels.** Genre tile/poster browsing for
Movies and TV, plus ten clock-driven virtual movie channels and ten virtual
TV channels with a 14-day cable-style guide, generated from your own library
and always on - no installer switch. See
[docs/VIRTUAL-CHANNELS.md](docs/VIRTUAL-CHANNELS.md).

**Promo/barker channel (optional).** A spoken-narration preview reel of
upcoming virtual-channel programming, rendered on a timer with Kokoro
text-to-speech and ffmpeg. Off by default: it needs a private Python
environment and two model files you supply yourself. See
[docs/PROMO-BARKER-CHANNEL.md](docs/PROMO-BARKER-CHANNEL.md).

---

## Architecture

```
                        Browser / phone / TV
                                 |
                          HTTPS (port 5000)
                                 |
      +--------------------------v---------------------------+
      |  cinemediavault.service      (systemd, user cinevault)|
      |                                                       |
      |  Python standard library HTTP server                  |
      |    movies · TV · music · live TV · DVR · video wall   |
      |    downloads · subtitles · admin · modules page       |
      +--+-------------+--------------+-------------+---------+
         |             |              |             |
    +----v----+   +----v-----+   +----v----+   +----v---------+
    | SQLite  |   | metadata |   | ffmpeg  |   | HDHomeRun    |
    | WAL     |   | posters  |   | HLS/DVR |   | tuner (LAN)  |
    +---------+   +----------+   +---------+   +--------------+

    Media libraries are mounted read-only into the service.
    Recordings and generated libraries are the only writable media paths.

      +-------------------------------------------------------+
      |  Auxiliary services, each its own hardened unit        |
      |    cinemediavault-bookvault.service                    |
      |    cinemediavault-module@comics / @nes / @sega / ...   |
      |    cinemediavault-subtitles.service   (optional)       |
      +-------------------------------------------------------+

      +-------------------------------------------------------+
      |  Docker Compose, isolated project  (optional)          |
      |    cinemediavault-epg   iptv-org/epg grabber           |
      |    bound to a private address only, never 0.0.0.0      |
      +-------------------------------------------------------+

      Timers:  health (1 min) · refresh (15 min) · metadata (daily)
               thumbnails (daily) · database backup (nightly)
```

### Why systemd for the application and Docker only for the guide collector

The application needs three things a container makes harder: the media mounts
exactly as the host sees them, LAN-level access to the tuner for discovery and
streaming, and the GPU render node for hardware transcoding. It also has no
third-party Python dependencies at all, so the isolation a container buys is
mostly isolation from a problem it does not have. Running it as a systemd
service with `ProtectSystem=strict`, an empty capability set, a read-only view
of your libraries and an explicit writable list gives *stronger* practical
confinement than a container run with the device and mount access it would need
anyway.

The guide collector is the opposite case. It is a large Node application, rebuilt
from upstream source, that legitimately needs a couple of gigabytes of heap for a
full collection. Keeping it in its own image means an upstream change cannot
disturb the media server, and its memory ceiling is enforced by the runtime
rather than by hope. It publishes one read-only XMLTV file on a private address.

### Data flow: how a guide entry becomes a recording

1. The tuner's own guide is fetched and cached (`hdhr-guide-cache.json`).
2. If the extended guide is enabled, the collector's XMLTV is read and merged
   **behind** the tuner's horizon - and only where overlapping programme names
   and times still agree. A missing, stale or shifted feed changes nothing.
3. You press **Record** or **Record Series**. A row goes into `dvr_recordings`
   or `dvr_series_rules`.
4. The scheduler wakes every few seconds, checks its own sessions *and* the
   tuner's live status, and refuses to oversubscribe the tuners.
5. At the start time minus the padding, `ffmpeg -map 0 -c copy` writes the
   broadcast transport stream straight to disk. No re-encoding, so recording
   costs almost no CPU.
6. On completion the file is filed under Movies, TV Shows, Sports or News, and
   appears in **Recordings** with Play, Download and Delete.

---

## Hardware and VM sizing

| | Minimum | Comfortable | Everything on |
|---|---|---|---|
| vCPU | 2 | 4 | 8 |
| RAM | 2 GB | 4 GB | 12 GB |
| System disk | 8 GB free | 20 GB | 40 GB |
| Media | your own | your own | your own |

What actually consumes the resources:

- **Direct play costs almost nothing.** A dozen people streaming files their
  devices can already play will barely register.
- **HLS conversion costs a lot.** Budget roughly two modern cores per concurrent
  1080p stream on CPU, or a handful of streams on one hardware encoder.
- **Recording costs almost nothing** - it is a stream copy. Recordings need about
  **7 GB per hour** of HD broadcast.
- **The guide collector wants ~3 GB** while it runs, once a day.
- **Whisper wants a lot.** With a GPU it is comfortable; on CPU it runs several
  times slower than real time, and a first pass over a large library takes days.
- **Metadata and thumbnails** are bursty and scheduled overnight, niced to the
  lowest priority.

For a first install, four vCPUs and 4 GB is a good place to start. The wizard
shows what it detects and warns before you select something the machine will
struggle with.

---

## Supported systems and prerequisites

- **Ubuntu 22.04 LTS or 24.04 LTS**, x86-64 or ARM64. Debian usually works but
  is not tested.
- **systemd.** Not optional: the services, timers and self-healing depend on it.
- **Root access**, via `sudo`.
- **Python 3.10 or newer** - installed automatically if missing.
- **Internet access** for metadata and any optional component that downloads at
  install time. A purely local library installs and plays fine without it; pass
  `--offline` to skip every outbound request.

Everything else the installer works out from what you select, and installs only
that.

---

## Quick start

```bash
# 1. Copy the installer onto the machine and unpack it
unzip CineMediaVault-Installer-2.1.0-*.zip
cd CineMediaVault-Complete-Installer

# 2. Look before you leap (optional, changes nothing)
sudo ./install.sh --dry-run --config docs/examples/minimal.yaml

# 3. Run it
sudo ./install.sh
```

`install.sh` prints something like:

```
  Open the setup page in a browser

      http://192.168.1.42:8099/

  and enter this setup code:

      kP3nQ7xW-2mF9tR4vL8s
```

Open that address from any machine on your network, enter the code, and work
through the wizard. When it finishes it gives you the address of your new
server.

Afterwards:

```bash
sudo cinevaultctl status        # what is installed and running
sudo cinevaultctl smoke-test    # confirm it actually works
sudo cinevaultctl backup        # take the first backup
```

---

## The setup wizard, step by step

The wizard is **resumable**. Refresh the page, close the browser, or reboot the
machine mid-installation: reopening the page picks up where you left off, and
completed work is never repeated.

### 1. Welcome
Names the server, picks a time zone, and shows what it found on this machine -
cores, memory, graphics, whether Docker is present. Choose **Standard**,
**Minimal** or **Everything** as a starting point; every individual choice is
still yours later.

You are also asked to accept the third-party licences for the optional
components that download their own software.

### 2. Administrator
Your account. The password must be at least 12 characters and mix at least three
of lower case, upper case, digits and symbols.

The password is hashed the instant you submit it. It is never written to a
configuration file, never reaches a log, and cannot be recovered from the server
- only reset.

### 3. Network and HTTPS
The address people will type, which ports to use, and the certificate.

- **Generate one for me** is right for a home network. Browsers warn once per
  device; accept it and the connection is properly encrypted thereafter.
- **I already have a certificate** references yours in place. The private key is
  never copied, so it never lands in a backup.
- **Use my Let's Encrypt certificate** additionally installs a renewal hook so
  the service reloads after each renewal.

**Trusted networks** decides who may reach the setup page and the guide
collector. Only private ranges are accepted - `0.0.0.0/0` is rejected outright.

### 4. Media locations
Where your libraries are. Each path is checked as you type: does it exist, is it
readable, is it writable where it needs to be, how much is in it, how much space
is free.

Two refusals worth knowing about:

- **A path that is too broad is rejected.** `/mnt` is refused; `/mnt/media/Movies`
  is accepted. A scanner pointed at a whole mount tree is slow and matches badly.
- **A system directory is rejected.** `/etc`, `/usr`, `/var` and their kind can
  never be a library.

If a folder is empty, you are warned rather than blocked - an empty folder is
either a new library or an unmounted share, and only you know which. The
scheduled scan protects you regardless: it refuses to rebuild an index from an
empty directory, so an unmounted share cannot erase your catalogue.

**Nothing here modifies your media.** These folders are read.

### 5. Network shares
If your media is on a NAS that is not mounted yet, describe it and the installer
will mount it and, if you ask, add an `/etc/fstab` entry - with `nofail` and
automount, so a NAS that is switched off cannot stop the machine booting.

SMB credentials go in a file you create with mode 0600. The installer never
writes or reads share passwords itself.

### 6. Metadata providers
A free TMDb key gets posters, summaries, cast and genres. Optional; add it later
if you prefer. Keys are stored in a root-only file and masked everywhere in the
interface.

Fetching artwork during installation is **off** by default: a large library takes
hours and will hit the provider's rate limits. It runs on a schedule instead.

### 7. Live TV
Searches your network for an HDHomeRun tuner, or takes its address directly.
Shows what it found - model, tuners, firmware - and loads the channel list so you
can untick what you do not want.

**Reserved tuners** keeps some free for live viewing so recording can never take
over every tuner.

The installer never starts a channel scan on your tuner: a scan takes it offline.
If the lineup is empty, run a scan from the tuner's own web page first.

### 8. Guide
Your tuner provides about two days. Optionally run a private collector that
extends that to about two weeks.

The tuner's guide always wins. Collected data is appended only beyond the tuner's
horizon, and only where overlapping programmes still agree. If the collector is
unavailable, stale, malformed or time-shifted, nothing changes - you keep the
tuner's guide.

Request pacing defaults to one request at a time, 2.5 seconds apart. A full
collection takes roughly an hour. Being unhurried is what keeps the collector
welcome.

### 9. Recording
Padding before and after (broadcasts overrun), retention, what to do when more
shows overlap than you have tuners, and the free-space floor below which
recording is refused.

**Automatic deletion is off by default.** Nothing you record is ever removed
without you choosing it.

### 10. Subtitles
Existing subtitle files and embedded tracks are always used. Optionally look up
missing ones on SubDL, or generate them locally from the audio with Whisper.

Whisper is honestly labelled: on a machine without a GPU you are told it will run
several times slower than real time and that a full library takes days. It is
installed **not started** unless you explicitly ask, and it runs CPU-capped,
memory-capped, niced and at the lowest I/O priority, so it never interrupts
playback.

**Existing subtitles are never overwritten.** That is not configurable.

### 11. Integrations
Optional notifications and links to other systems you may already run.

### 12. Modules and sizing
Which libraries, which game platforms, which creator toolchains, how playback
converts, and how much of the machine CineMediaVault may use.

The bulk library re-encoder is here too. It **rewrites your source files**, so it
is installed stopped, with an empty queue, and the installer refuses to start it
even if the configuration asks. Arming it is a separate, deliberate command.

### 13. Review
Everything you chose, in plain language, plus every machine check with its
result and, where something failed, what to do about it. Nothing has been
changed at this point. **Dry run** proves the whole plan without touching
anything.

### 14. Install
Live progress: which step is running, what each one did, and a full log with
every password, key and token stripped out.

If something fails, it says which step and why. Nothing is left half-written:
every replaced file was backed up first, and running the installer again carries
on from where it stopped rather than starting over.

---

## Unattended installation

```bash
cinevaultctl config example > my-vault.yaml
$EDITOR my-vault.yaml
cinevaultctl --config my-vault.yaml validate
sudo ./install.sh --dry-run --config my-vault.yaml
sudo ./install.sh --config my-vault.yaml
```

The wizard and this path run **the same engine with the same validation**, so
they cannot disagree.

A minimal file:

```yaml
deployment:
  timezone: America/Denver
  accept_licenses: true
admin:
  username: jane
  password: "a-strong-passphrase-you-choose"
media:
  movies_root: /srv/media/Movies
  tv_root: /srv/media/TV Shows
network:
  hostname: vault.home.arpa
  https_port: 5000
```

Any setting can also come from the environment as `CMV_<SECTION>_<KEY>`:

```bash
sudo CMV_NETWORK_HTTPS_PORT=5443 ./install.sh --config my-vault.yaml
```

A name that matches no setting is an error, not a silent no-op.

The password in the file is hashed on first read and blanked; the installer never
writes it back.

---

## Storage and folder layout

### What the installer creates

```
/opt/cinemediavault/          application (root-owned; the service cannot write here)
  app/                          the CineMediaVault application
  scripts/                      helpers: health, refresh, backup, index rebuild
  creators/                     comic, book and game generators
  modules/                      game library pages and runtimes
  compose/epg/                  the guide collector project
  bin/                          cinevault-create
  VERSION.json, payload-manifest.json

/etc/cinemediavault/          configuration  (0750)
  cinemediavault.yaml           your settings          (0640 root:cinevault)
  cinevault.env                 derived service environment (0640)
  secrets.env                   API keys               (0640 root:cinevault)
  certs/                        certificate and key
  modules/                      per-module environment

/var/lib/cinemediavault/      state  (owned by cinevault)
  db/                           SQLite databases
  metadata/movies|tv/           indexes, posters, thumbnails, manual overrides
  music-art/, module-logos/
  backups/                      database and full backups
  change-backups/               every file the installer replaced, for rollback
  install-journal.json          what has been installed, for resume and rollback

/var/log/cinemediavault/      logs, rotated daily
/var/cache/cinemediavault/    HLS, subtitles, mobile downloads - safe to delete
```

Generated metadata lives in `/var/lib`, **not** beside your media. Your library
folders stay exactly as they were.

### What your media should look like

CineMediaVault reads common layouts. What matches best:

```
Movies/
  The Long, Long Trailer (1954)/
    The Long, Long Trailer (1954).mp4
    The Long, Long Trailer (1954).en.srt

TV Shows/
  Scooby-Doo, Where Are You! (1969)/
    Season 01/
      S01E01 - What a Night for a Knight.mp4

Music/
  Artist/
    Album (Year)/
      01 - Track.flac

Books/       Comics/            Games/           Recordings/
  *.epub       *.cbz, *.cbr       nes/, sega/      Movies/, TV Shows/,
  *.pdf                           dos/, mame/      Sports/, News/
```

A year in the folder name is the single biggest help to matching.

---

## Network ports and firewall

| Port | Service | Default | Exposure |
|---|---|---|---|
| 5000 | CineMediaVault HTTPS | on | your trusted networks |
| 8080 | CineMediaVault HTTP | off | your trusted networks |
| 8110 | Comics | with the module | your trusted networks |
| 8112 | Book Vault | with the module | your trusted networks |
| 8090+ | Game modules | with each module | your trusted networks |
| 3010 | Guide collector | with extended EPG | **a private address only** |
| 8099 | Setup wizard | during setup only | **never opened in the firewall** |

`network.configure_firewall` adds `ufw` rules for exactly the ports this
installation uses, limited to your trusted networks. The wizard and the guide
collector are never given a rule.

The firewall is **not enabled for you**. Enabling `ufw` over SSH can cut the very
session running the installer. Check your SSH rule is present, then:

```bash
sudo ufw status
sudo ufw enable
```

### Do not port-forward this to the internet

If you want access from outside, use a VPN (WireGuard or Tailscale) into your
home network. Exposing a media server directly means exposing your accounts,
your library and every optional module to the whole internet.

---

## HTTPS and local domain names

The self-signed certificate covers your chosen hostname, `localhost`, and every
local IP address the machine has, so it matches whichever address a device uses.

To stop the browser warning, install the certificate as trusted on each device:

```bash
sudo cp /etc/cinemediavault/certs/cinemediavault.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

For a real name without warnings, the cleanest home setup is a DNS name in a
domain you own, pointed at the private address, with a Let's Encrypt certificate
issued by DNS challenge. Then choose **Use my existing Let's Encrypt
certificate**; the installer adds a renewal hook so the service reloads
automatically.

`.local` names are mDNS and can be unreliable across VLANs and some phones.
`.home.arpa` is the reserved choice for home networks.

---

## Live TV, the guide, and the DVR

### The tuner

Discovery tries SiliconDust's service and a UDP broadcast, then probes directly.
Both can be blocked; typing the address always works.

The installer records only the device ID, model, address and tuner count. It
never copies device authorisation tokens.

### The guide

The tuner's free guide covers about two days and is always authoritative. In
extended mode, CineMediaVault combines that local guide with approximately 12
additional days into one continuous guide. Both parts run on the CineVault host;
no second server is required.

The optional collector runs the upstream `iptv-org/epg` grabber in its own
container and publishes XMLTV on a private address. The merge is deliberately
conservative:

- collected data is used **only beyond** the tuner's horizon;
- the local tuner cache refreshes after 12 hours by default;
- every refresh revalidates overlapping programme titles and times;
- an unavailable, stale, malformed, mismatched or time-shifted feed leaves the
  tuner's guide untouched.

The collector needs a **verified channel map** matching your channels to the
upstream source. The one included was verified for one specific lineup and will
not match yours. Without a map the collector produces an empty guide and your
tuner’s guide is used unchanged - safe, just not extended.

Collection runs once each night at 03:20 by default. A seeded or previously
completed XMLTV file remains available while the next collection is running, so
the guide does not disappear during an update. The collector binds to
`127.0.0.1:3010` by default and is not exposed to the LAN or internet.

### Recording

- Records the broadcast stream with `ffmpeg -map 0 -c copy`. No re-encoding.
- 60 seconds early and 120 seconds late by default.
- Filed under Movies, TV Shows, Sports or News, then a title folder.
- Series rules cover all episodes or new episodes only, with durable duplicate
  suppression.
- The scheduler checks its own sessions **and** the tuner's live status, so it
  cannot oversubscribe. Reserved tuners are never claimed.
- Recording is refused below the free-space floor.
- Interrupted recordings are recovered or marked failed after a restart.
- **Automatic deletion is off by default.**

---

## Metadata, subtitles and Whisper

### Metadata

A free TMDb key fetches posters, summaries, cast and genres on a schedule. A
manual **Fix Match** is stored against a stable identity, so it survives rescans
and even re-encoding a file to a different container.

### Subtitles

Order of preference, always:

1. subtitle files you already have,
2. embedded text subtitle tracks,
3. SubDL lookup, if enabled,
4. local transcription, if enabled.

Generated files are written alongside yours as `.en.whisper.srt` and never over
them.

### Whisper

A private virtual environment with a pinned `faster-whisper`. The model
downloads on first use.

Be realistic about cost. With an NVIDIA GPU, `large-v3` is comfortable. On CPU,
use `small` or `base`, and expect a first pass over a large library to take days.
The worker is CPU-capped, memory-capped, niced to 19 and at idle I/O priority, so
it yields to playback - but it is still doing real work.

It is installed **stopped** unless you asked otherwise. Start it when you are
ready:

```bash
sudo systemctl start cinemediavault-subtitles.service
```

or from the **Whisper Subtitles** card on the Modules page.

---

## Transcoding

Two different things share the word.

**On-demand HLS** converts a stream a client cannot play directly. It writes only
to the cache and never touches your files. On by default. `auto` picks NVENC,
Quick Sync or VA-API if the hardware has it, and CPU otherwise.

**The bulk library queue** re-encodes your library to save space. It **rewrites
source media**. It is therefore:

- off unless you select it,
- installed stopped with an empty queue,
- never started by the installer, even if the configuration asks,
- armed only by a separate deliberate command:

```bash
sudo cinevaultctl transcode status
sudo cinevaultctl transcode enable     # asks for confirmation
```

Before arming it: confirm you have backups, and try a handful of titles first.
An encoder exiting zero is not proof the output is good; the pipeline verifies
duration, streams, decodability and size before replacing anything, and
quarantines repeated failures rather than looping.

---

## The libraries

**Movies** - catalogue, detail pages, cast, related titles, continue watching,
Fix Match, custom posters.

**TV** - shows, seasons, episodes, per-episode thumbnails, next-up.

**Music** - artists, albums, tracks, genres, recently added, continue listening,
full-screen now playing with seek, queue, shuffle, repeat, per-user playlists,
track and playlist downloads. Playback position is per user and survives a
restart; restored state never autoplays at you.

**Book Vault** - EPUB and PDF with covers, metadata and a browser reader.

**Comics** - CBZ and CBR turned into a readable web library.

**Games** - browser-playable retro libraries. You supply your own game files;
none are downloaded or bundled. After adding files:

```bash
sudo cinevaultctl module rebuild nes
```

---

## Creators and generators

Offline tools that turn raw material into a browsable library. All of them read a
source folder and write into a separate output folder; none modifies or deletes
source material.

```bash
cinevault-create list

cinevault-create comics new              # rebuild every comic collection
cinevault-create comics recent           # only recently added
cinevault-create comics hub              # the index page
cinevault-create comics import-magazine  # import a scanned run
cinevault-create books hub               # the book/magazine hub
cinevault-create games build nes         # rebuild a platform catalogue
cinevault-create games bundle GAME.zip   # build a playable DOS bundle
cinevault-create games world-paths       # regenerate strategy-game map data
```

The command runs each generator as the service account with your library paths
already set, so nothing needs editing for this machine.

---

## The Android companion

The APK is **not** bundled. A signed binary inside an installer is a binary whose
provenance you cannot check. Two supported routes:

1. **Use an existing build.** Copy `CineMediaVault-1.7.0-music-player.apk` to the
   phone and install it. It prompts for the server URL on first launch.
2. **Build it yourself** from `CineMediaVault-1.7.0-source.zip` with Android
   Studio or Gradle.

The app offers offline downloads of films, episodes and whole seasons, a local
index with artwork and summaries, and background music playback with
lock-screen controls.

Server-side support for all of it is installed with `modules.downloads`.

---

## Backup, restore, upgrade and migration

### What a backup contains

Configuration (including secrets, so a restore is actually usable), the SQLite
databases taken consistently through the SQLite backup API, generated metadata
and artwork, and module state.

It never contains media, caches, or the TLS private key.

```bash
sudo cinevaultctl backup
sudo cinevaultctl backup --destination /mnt/nas/backups --label weekly
sudo cinevaultctl backup --no-secrets          # safe to share
```

A backup runs nightly on its own. A database backup is also taken automatically
before any upgrade or restore.

### Restore

```bash
sudo cinevaultctl restore /var/lib/cinemediavault/backups/cinemediavault-backup-....tar.gz
```

The current state is backed up first, so a restore is itself reversible.

### Upgrade

```bash
cd /path/to/new/CineMediaVault-Complete-Installer
sudo ./install.sh --config /etc/cinemediavault/cinemediavault.yaml
```

An upgrade migrates the configuration forward, takes a backup, then re-runs the
same idempotent steps. Steps whose inputs have not changed skip themselves, so
only what actually differs is touched.

### Migration to another machine

```bash
# old machine
sudo cinevaultctl backup --label migration
# copy the archive across, then on the new machine
sudo ./install.sh --config <same config>
sudo cinevaultctl restore cinemediavault-backup-...-migration.tar.gz
```

Mount your media at the same paths, or edit the paths in the configuration
before restoring.

### Uninstall

```bash
sudo cinevaultctl uninstall                    # keeps database and configuration
sudo cinevaultctl uninstall --remove-state     # removes them too
```

**Your media is never removed**, by either form. Even
`--remove-generated-media` only removes libraries CineMediaVault generated
itself, never a source library and never your recordings.

### Rollback

```bash
sudo cinevaultctl rollback
```

Restores every file the last run replaced, from the snapshots taken before each
change.

---

## Security and secrets

**The service is unprivileged.** It runs as `cinevault` with no shell, an empty
capability set, `ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges`,
and a system-call filter. It can read its own code but never write it.

**Your libraries are read-only to it.** Only recordings, generated libraries and
the ROM directory are writable, and they are listed explicitly.

**Passwords are never stored.** PBKDF2-SHA256, 260,000 iterations, fresh salt.
The plaintext is discarded the moment it is hashed and never reaches a
configuration file, a log, or the wizard's state file.

**Secrets are separated.** API keys live in `secrets.env`, mode 0640,
`root:cinevault`. They never appear in `cinemediavault.yaml`.

**Nothing leaks into a log.** Every message passes through a redactor that masks
both registered secret values and credential *shapes* - `api_key=`,
`Authorization:`, `user:pass@host`, PEM blocks, JWTs - so even a third-party
error message cannot print a key.

**The setup wizard is not a back door.** It binds a private address only and
refuses a public one, requires a one-time code printed on the console, checks a
CSRF token on every state change, rejects cross-origin requests, rate-limits
wrong codes, and stops itself when installation finishes. It is never enabled at
boot.

**No shell string is ever built from configuration.** Every command is an
argument list; `shell=True` appears nowhere, and the test suite parses the source
to prove it.

**The default administrator is removed.** The upstream application creates a
super-administrator with a publicly known password on every start. The installer
patches that out and substitutes your account - and refuses to install if it
cannot.

### What this installer never collects

Media, databases, user accounts, watch history, TLS private keys, API tokens,
Webex tokens, share passwords, or HDHomeRun device authorisation tokens.

---

## Monitoring, logs and troubleshooting

```bash
sudo cinevaultctl status        # what is installed and running
sudo cinevaultctl smoke-test    # does it actually work
sudo systemctl status cinemediavault.service
sudo journalctl -u cinemediavault.service -f
```

| Log | Where |
|---|---|
| Application | `/var/log/cinemediavault/cinemediavault.log`, `.err` |
| Health check | `/var/log/cinemediavault/health.log` |
| Library refresh | `/var/log/cinemediavault/refresh.log` |
| Installation | `/var/log/cinemediavault/install-*.jsonl` |
| Guide collector | `docker logs cinemediavault-epg` |

The health check probes every minute and restarts the service after **two**
consecutive failures, with a five-minute cooldown so a bad configuration cannot
cause a restart storm.

Common situations are covered in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## Configuration reference

Every setting, its type, default, constraints and meaning:
**[docs/CONFIGURATION-REFERENCE.md](docs/CONFIGURATION-REFERENCE.md)**

```bash
cinevaultctl config example          # commented example, every setting
cinevaultctl config show             # what is in effect, secrets masked
cinevaultctl config get network.https_port
sudo cinevaultctl config set network.https_port 5443
sudo cinevaultctl apply              # regenerate services after an edit
```

---

## Testing and clean-VM acceptance

```bash
./tests/run-tests.sh                 # the full suite; needs no root, no network
sudo ./tools/smoke-test.sh           # verify a real installation
sudo ./tools/smoke-test.sh --deep    # also probe ffmpeg and the tuner
```

The suite covers configuration validation, path safety, secret redaction,
password policy, idempotency (a second run must change nothing), generated
systemd and Compose configuration, rollback, backup and restore, migration, the
wizard API, and a mocked full installation.

The acceptance procedure for a fresh VM is
**[docs/CLEAN-VM-ACCEPTANCE-CHECKLIST.md](docs/CLEAN-VM-ACCEPTANCE-CHECKLIST.md)**.

---

## Known limitations

- **The bundled EPG channel map matches one specific lineup.** You will need to
  build your own for the extended guide. Without one, the guide still works - it
  just stops at the tuner's horizon.
- **Whisper on CPU is slow.** Hours per film, days for a library. The wizard says
  so rather than letting you find out.
- **`unrar-free` does not handle every RAR5 comic archive.** The non-free `unrar`
  does, but its licence prevents this installer from shipping or installing it.
- **The Android APK is not bundled** and must be built or copied separately.
- **One tuner device.** Multiple HDHomeRuns are not aggregated.
- **HDHomeRun only.** Other tuner types are not supported.
- **No clustering.** One machine.
- **HLS on CPU is expensive.** Plan for direct play, or add a hardware encoder.
- **The wizard runs as root.** Unavoidable for something that installs an
  operating system's worth of software; it is bound to a private address, needs a
  console-printed code, and stops when finished.
- **Debian and non-Ubuntu derivatives are untested**, as is anything other than
  x86-64 and ARM64.
- **`.local` hostnames** can be unreliable across VLANs and on some phones.

---

## Licences and third-party notices

CineMediaVault's own code is the property of its author. Third-party components,
what they are licensed under, and how each is obtained are recorded in
**[docs/THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md)**.

The short version: nothing under a copyleft licence is redistributed inside this
package. EmulatorJS (GPL-3.0) and js-dos (GPL-2.0) are downloaded from their
official sources at install time. hls.js (Apache-2.0) is bundled, with its notice
alongside. No ROMs, BIOS images, media or other content are included.

---

## Further documentation

| Document | For |
|---|---|
| [docs/OPERATOR-GUIDE.md](docs/OPERATOR-GUIDE.md) | Running it day to day |
| [docs/DEVELOPER-GUIDE.md](docs/DEVELOPER-GUIDE.md) | Changing the installer |
| [docs/RESPONSIVE-DESIGN.md](docs/RESPONSIVE-DESIGN.md) | Responsive/mobile-safe conventions for the app's UI templates |
| [docs/ACCOUNT-THEMES.md](docs/ACCOUNT-THEMES.md) | The per-account color theme system (default/royal-blue/green) |
| [docs/USAGE-ANALYTICS.md](docs/USAGE-ANALYTICS.md) | Bandwidth/CPU usage tracking, the Active & Historical Usage admin page, and its APIs |
| [docs/VIRTUAL-CHANNELS.md](docs/VIRTUAL-CHANNELS.md) | Genre discovery and the ten+ten virtual channel guide |
| [docs/PROMO-BARKER-CHANNEL.md](docs/PROMO-BARKER-CHANNEL.md) | The optional spoken-preview promo/barker channel |
| [docs/BACKUP-RESTORE.md](docs/BACKUP-RESTORE.md) | Backup, restore, migration |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | When something is wrong |
| [docs/MODULE-INVENTORY.md](docs/MODULE-INVENTORY.md) | Every component, and its disposition |
| [docs/CONFIGURATION-REFERENCE.md](docs/CONFIGURATION-REFERENCE.md) | Every setting |
| [docs/CLEAN-VM-ACCEPTANCE-CHECKLIST.md](docs/CLEAN-VM-ACCEPTANCE-CHECKLIST.md) | Validating a fresh install |
| [docs/THIRD-PARTY-NOTICES.md](docs/THIRD-PARTY-NOTICES.md) | Licences |
| [BUILD-REPORT.md](BUILD-REPORT.md) | How this package was built |
