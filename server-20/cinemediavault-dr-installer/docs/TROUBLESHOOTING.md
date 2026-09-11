# CineMediaVault troubleshooting

Start here:

```bash
sudo cinevaultctl status
sudo cinevaultctl smoke-test
```

The smoke test observes real behaviour and names what failed. Most of what
follows is the fix for a specific line it can print.

---

## Installation

### The setup page will not load

```bash
sudo systemctl status cinemediavault-setup.service
sudo ss -lntp | grep 8099
```

- **Wrong address.** `install.sh` prints the address it bound. If your machine
  has several interfaces it may not be the one you expect. Try
  `sudo ./install.sh --host <the address you want>`.
- **Not on the same network.** The page is reachable only from private ranges.
- **Port busy.** `sudo ./install.sh --port 8100`.

### "That setup code is not correct"

The code is printed on the console where you ran `install.sh`, once. It is not
stored anywhere else. Stop the installer with Ctrl-C and run it again for a new
code.

After five wrong codes an address is locked out for five minutes.

### The install stopped partway

Nothing is half-written. Every file that was replaced was backed up first, and
the journal records what completed.

```bash
sudo cinevaultctl status         # shows the failed step
sudo ./install.sh                # carries on from where it stopped
```

To undo it instead:

```bash
sudo cinevaultctl rollback
```

### "preflight check(s) failed; nothing was changed"

Read the lines above it - each failure names what to do. The common ones:

| Failure | Fix |
|---|---|
| Administrator privileges | run with `sudo` |
| Port already in use | stop the other service, or choose another port |
| Media path does not exist | create or mount it, or enable "create missing folders" |
| Install directory not writable | run with `sudo` |
| Unsupported distribution | Ubuntu 22.04 or 24.04 |
| systemd is not running | install on a real VM, not a minimal container |

### "could not locate the default-administrator block"

The installer refuses to install an application it cannot make safe: upstream
creates a super-administrator with a publicly known password, and the patch that
removes it did not match. This means the payload does not match the version the
installer expects.

Re-stage the payload from a matching source, or check
`installer/steps/s050_payload.py` against
`payload/app/cinemediavault.py`.

---

## The server will not start

```bash
sudo systemctl status cinemediavault.service
sudo journalctl -u cinemediavault.service -n 50 --no-pager
sudo tail -50 /var/log/cinemediavault/cinemediavault.err
```

| Symptom | Cause | Fix |
|---|---|---|
| `Address already in use` | something else has the port | `sudo ss -lntp \| grep 5000` |
| `SSLError` / certificate errors | certificate or key missing or unreadable | `sudo cinevaultctl repair --only tls` |
| `Permission denied` on state | ownership drifted | `sudo chown -R cinevault:cinevault /var/lib/cinemediavault` |
| `database is locked` | a scan is holding it | wait, then check the disk is not full |
| exits immediately, no message | configuration error | `sudo cinevaultctl validate` |

### It restarts over and over

The health check gives up after two consecutive failures and then waits five
minutes, so this is a real failure rather than a loop:

```bash
sudo systemctl stop cinemediavault-health.timer     # stop the restarts
sudo journalctl -u cinemediavault.service -n 100
# fix, then
sudo systemctl start cinemediavault-health.timer
```

---

## A library is empty

The single most common cause is an unmounted share.

```bash
findmnt --target /srv/media/Movies
ls /srv/media/Movies | head
```

If the folder is genuinely empty, the mount is not there. Remount it:

```bash
sudo mount /srv/media/Movies
sudo systemctl start cinemediavault-refresh.service
```

**Your catalogue is safe meanwhile.** The refresh refuses to rebuild an index
from an empty directory, precisely so an unmounted share cannot erase it. You
will see this in `refresh.log`:

```
SKIP movies: /srv/media/Movies is empty. Refusing to rebuild the index from an
empty directory - an unmounted share would wipe the catalogue.
```

### The folder is mounted but titles are missing

- Check the service can read it: `sudo -u cinevault ls /srv/media/Movies`
- Check the layout. A folder per title, with the year, matches best.
- Force a refresh and watch: `sudo journalctl -u cinemediavault-refresh.service -f`

---

## Playback

### It buffers or will not start

Direct play needs no CPU; conversion needs a lot. Check which is happening:

```bash
ps aux | grep ffmpeg
```

If ffmpeg is running for every stream, the clients cannot play your files
directly. Either enable hardware acceleration, or transcode the library once to
a widely-compatible format.

```bash
ffmpeg -hide_banner -encoders | grep -E 'nvenc|qsv|vaapi'
sudo cinevaultctl config set transcode.hls_encoder nvenc
sudo cinevaultctl apply
```

### Subtitles do not appear

- Sidecar files must sit beside the video with a matching name:
  `Film (2020).mkv` and `Film (2020).en.srt`.
- Image-based subtitles (PGS, VobSub) cannot be shown in a browser; only text
  subtitles can.
- Check the cache: `ls /var/cache/cinemediavault/subtitles/`

### The browser warns about the certificate

Expected with a self-signed certificate. Accept it once per device, or install
it as trusted:

```bash
sudo cp /etc/cinemediavault/certs/cinemediavault.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

---

## Live TV and recording

### No tuner found

```bash
curl -s http://<tuner-ip>/discover.json
```

Automatic discovery needs either internet access or a flat network path to the
tuner; both are commonly blocked. Entering the address manually always works:

```bash
sudo cinevaultctl config set livetv.device_address 192.168.1.50
sudo cinevaultctl config set livetv.discovery manual
sudo cinevaultctl apply
```

### The channel list is empty

The tuner has not scanned. Run a channel scan from the **tuner's own** web page.
CineMediaVault deliberately never starts one, because a scan takes the tuner
offline for everyone.

### A recording failed

Check `/dvr/conflicts` in the interface, then:

```bash
sudo journalctl -u cinemediavault.service | grep -i dvr | tail -30
df -h /srv/media/Recordings
```

| Cause | Fix |
|---|---|
| all tuners busy | reduce reserved tuners, or accept the conflict |
| below the free-space floor | free space, or lower `dvr.min_free_gb` |
| tuner unreachable | check the tuner and the network |
| destination not writable | `sudo -u cinevault touch /srv/media/Recordings/x` |

### The guide is short or empty

The tuner's own guide is about two days; that is normal. If you enabled the
extended collector:

```bash
sudo cinevaultctl epg status
docker logs cinemediavault-epg --tail 50
```

| Symptom | Cause |
|---|---|
| container absent | not installed; `sudo cinevaultctl repair --only epg` |
| container restarting | usually out of memory; raise `epg.memory_limit_mb` |
| guide present but tiny | the channel map matches nothing in your lineup |
| guide never updates | check the daily schedule and the container logs |

**The bundled channel map was verified for one specific lineup and will not
match yours.** Without a correct map the collector produces an empty guide -
which is safe, because your tuner's guide is used unchanged.

---

## Whisper

### It never starts

```bash
sudo systemctl status cinemediavault-subtitles.service
ls /var/lib/cinemediavault/subtitles/venv/bin/python
```

If the virtual environment is missing, the install was skipped - usually for
lack of disk space:

```bash
sudo cinevaultctl repair --only subtitles.whisper
```

### It is extremely slow

Expected on CPU. Use a smaller model, or add an NVIDIA GPU:

```bash
sudo cinevaultctl config set subtitles.whisper_model small
sudo cinevaultctl apply
```

### It is slowing everything down

It should not - it is niced to 19 and at idle I/O priority. If it still is,
lower its CPU quota:

```bash
sudo cinevaultctl config set subtitles.whisper_cpu_quota_percent 100
sudo cinevaultctl apply
```

---

## Disk space

```bash
df -h
du -sh /var/lib/cinemediavault/* /var/cache/cinemediavault/*
```

Safe to remove, in order of preference:

1. `/var/cache/cinemediavault/hls/*` - regenerates on demand
2. `/var/cache/cinemediavault/mobile-downloads/*` - regenerates on demand
3. old files in `/var/lib/cinemediavault/backups/` - keep at least one
4. old `/var/lib/cinemediavault/change-backups/*` - only after you are sure you
   will not roll back

Never remove `/var/lib/cinemediavault/db/` - that is your accounts, watch
history and DVR schedule.

---

## Recovering from a bad change

```bash
sudo cinevaultctl rollback                    # undo the last installer run
sudo cinevaultctl restore <backup.tar.gz>     # restore a full backup
```

Both take a safety copy of the current state first, so recovery is itself
reversible.

---

## Collecting information for help

```bash
sudo cinevaultctl status --json               > /tmp/cmv-status.json
sudo cinevaultctl smoke-test --json           > /tmp/cmv-smoke.json
sudo cinevaultctl config show                 > /tmp/cmv-config.yaml
sudo journalctl -u cinemediavault.service -n 200 --no-pager > /tmp/cmv-journal.txt
```

`config show` masks every secret, and every log line has already been through
the redactor. Read them before sharing anyway.

**Never share** `/etc/cinemediavault/secrets.env` or anything from
`/etc/cinemediavault/certs/`.
