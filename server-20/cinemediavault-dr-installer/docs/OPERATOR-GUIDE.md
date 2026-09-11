# CineMediaVault operator guide

Running the server day to day. Assumes it is already installed; if it is not,
start with the [README](../README.md).

---

## The one command to know

```bash
sudo cinevaultctl status
```

Shows the installed version, the address, which services are up, and how the
last installation run ended. When something feels wrong, start here.

---

## Daily and weekly rhythm

Nothing needs doing daily. The scheduled work runs itself:

| When | What |
|---|---|
| every minute | health probe, restart after two consecutive failures |
| every 15 minutes | library index refresh |
| nightly 01:00 | database backup, pruned to the last 10 |
| nightly 03:20 | extended guide collection, if enabled |
| nightly 04:40 | metadata and artwork fetch |
| nightly 05:20 | TV episode thumbnails |
| daily | log rotation |

Worth doing occasionally:

```bash
sudo cinevaultctl smoke-test        # monthly, and after any change
sudo cinevaultctl backup            # before you change anything yourself
```

---

## Services

| Unit | What it is |
|---|---|
| `cinemediavault.service` | the media server |
| `cinemediavault-health.timer` | health probe and self-heal |
| `cinemediavault-refresh.timer` | library index refresh |
| `cinemediavault-backup.timer` | nightly database backup |
| `cinemediavault-metadata.timer` | artwork and descriptions |
| `cinemediavault-thumbnails.timer` | TV episode stills |
| `cinemediavault-bookvault.service` | Book Vault |
| `cinemediavault-module@comics.service` | comics library |
| `cinemediavault-module@nes.service` etc. | game libraries |
| `cinemediavault-subtitles.service` | Whisper worker (optional) |

```bash
sudo systemctl restart cinemediavault.service
sudo systemctl status cinemediavault-module@nes.service
systemctl list-timers 'cinemediavault*'
```

Restarting the main service interrupts playback for anyone watching. The health
check deliberately waits for two consecutive failures before doing it for
exactly that reason.

---

## Adding media

Copy files into the library folder. The index refresh picks them up within
about 15 minutes.

To not wait:

```bash
sudo systemctl start cinemediavault-refresh.service
```

Artwork arrives on the nightly metadata run, or:

```bash
sudo systemctl start cinemediavault-metadata.service
```

### A title matched wrongly

Use **Fix Match** on the title's page. The correction is stored against a stable
identity, so it survives rescans, refreshes, and even re-encoding the file to a
different container. You should not have to do it twice.

### A library shows nothing

Almost always an unmounted share:

```bash
findmnt --target /srv/media/Movies
ls /srv/media/Movies | head
```

The refresh **refuses** to rebuild an index from an empty directory, so an
unmounted share cannot erase your catalogue. Remount, then run the refresh.

---

## Users

Users are managed inside CineMediaVault, on the admin pages: create accounts,
approve requests, reset passwords, view or clear play history.

The super-administrator cannot be deleted from the interface. To reset its
password from the console:

```bash
sudo cinevaultctl admin set-password
```

---

## Live TV and recording

```bash
sudo cinevaultctl smoke-test --deep      # confirms the tuner answers
```

Scheduling is done in the web interface: **Record** for one showing, **Record
Series** for a rule, and **New episodes only** where you want it.

Things worth knowing:

- The scheduler checks both its own sessions and the tuner's live status, so it
  cannot oversubscribe your tuners.
- Reserved tuners are never claimed by recording.
- Recording stops being scheduled below the free-space floor.
- Automatic deletion is off unless you turned it on.
- An interrupted recording is recovered or marked failed after a restart.

### The guide only covers two days

That is the tuner's free guide. To build one unified guide with approximately
two authoritative local days plus 12 appended days, enable the collector and
supply a channel map matching your lineup. The collector runs privately on the
same CineVault host:

```bash
sudo cinevaultctl epg status
sudo cinevaultctl epg rebuild
```

The first collection takes 45-90 minutes at the default pacing. The guide keeps
working from the tuner's data throughout.

The local tuner cache refreshes every 12 hours by default. The extended
collector runs nightly at 03:20 and keeps serving the last completed guide while
it builds the next one.

---

## Games

Copy game files into the platform folder under your games root, then:

```bash
sudo cinevaultctl module rebuild nes
```

That rebuilds the catalogue and restarts the module.

---

## Comics and books

After adding archives:

```bash
cinevault-create comics new       # rebuild every collection
cinevault-create comics recent    # only recently added - much faster
cinevault-create comics hub       # the index page
cinevault-create books hub
```

These read your source folder and write into the generated library folder. They
never modify or delete the originals.

---

## Subtitles

Existing subtitle files and embedded tracks are used automatically.

If you enabled Whisper:

```bash
sudo systemctl start cinemediavault-subtitles.service
sudo systemctl stop cinemediavault-subtitles.service
sudo journalctl -u cinemediavault-subtitles.service -f
```

or use the **Whisper Subtitles** card on the Modules page.

The first pass over a large library takes days. It is CPU-capped, niced and at
idle I/O priority, so it yields to playback - but check the first several
results before leaving it unattended.

---

## Transcoding

On-demand conversion needs no attention.

The bulk library queue **rewrites your source files**. It is installed stopped.
Before arming it: have backups, and run a handful of titles first.

```bash
sudo cinevaultctl transcode status
sudo cinevaultctl transcode enable      # asks for confirmation
sudo cinevaultctl transcode disable
```

---

## Changing settings

```bash
sudo cinevaultctl config set network.https_port 5443
sudo cinevaultctl apply
```

`apply` regenerates the service environment, the systemd units and the container
settings together, then restarts what changed. Editing
`/etc/cinemediavault/cinemediavault.yaml` by hand works too - follow it with
`apply`.

Do not edit `/etc/cinemediavault/cinevault.env`. It is generated, and `apply`
overwrites it.

---

## Repairing

```bash
sudo cinevaultctl repair                       # re-converge everything
sudo cinevaultctl repair --only systemd,timers # just those
sudo cinevaultctl repair --force config        # rewrite even if unchanged
```

Repair is the same idempotent engine as install. Steps whose inputs have not
changed skip themselves, so it is cheap and safe to run.

---

## Disk space

```bash
df -h /var/lib/cinemediavault /srv/media/Recordings
du -sh /var/cache/cinemediavault/*
```

The cache directory is entirely regenerable. With the service stopped it is safe
to empty:

```bash
sudo systemctl stop cinemediavault.service
sudo rm -rf /var/cache/cinemediavault/hls/*
sudo systemctl start cinemediavault.service
```

Recordings are about 7 GB per hour of HD broadcast. The health check warns in
`health.log` at 85% and again at 93%.

---

## Notes carried over from the original deployment

These are not enforced by the installer; they are hard-won operational habits
worth keeping.

- Validate mounts before any scan, move or transcode. An unavailable mount must
  never be treated as an empty library.
- Never delete an original because an encoder exited zero. Verify duration,
  streams, decodability and size first.
- Quarantine repeat transcode failures rather than looping on them.
- Never enqueue the same asset in two transcode pipelines.
- Back up the database before a schema migration.
- Do not interrupt an active encode merely to update a dashboard.
- Restore to a staging directory and compare, rather than writing straight over
  a live service.
