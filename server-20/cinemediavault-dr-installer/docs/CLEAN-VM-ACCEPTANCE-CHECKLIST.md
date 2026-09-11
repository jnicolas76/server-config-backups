# Clean-VM acceptance checklist

The end-to-end validation this package has **not** yet had. Everything here is
destructive to the VM it runs on and must be done on a throwaway machine - never
against an existing CineMediaVault, tuner, share or database.

Budget about 90 minutes for the full pass, plus whatever the guide collector and
any metadata fetch take in the background.

---

## Before you start

**Prepare**

- [ ] A fresh Ubuntu **22.04** VM: 4 vCPU, 4 GB RAM, 40 GB disk, on the same
      network segment as the HDHomeRun tuner if you are testing Live TV.
- [ ] A second fresh Ubuntu **24.04** VM, same shape, for the second pass.
- [ ] Snapshot both VMs while clean, so each pass can start from zero.
- [ ] A small sample library: 3-5 films and one TV show with two seasons, in
      `Title (Year)` folders. A few hundred megabytes is plenty.
- [ ] A TMDb API key.
- [ ] The installer package, transferred to the VM.

**Confirm you are not pointing at anything real**

- [ ] The VM has no route to the production CineMediaVault host.
- [ ] The media paths you will enter are the sample library, not a real share.
- [ ] If testing Live TV against the real tuner: nobody is watching or
      recording, and you will only *read* the guide and lineup. A recording test
      claims a tuner for two minutes.

---

## Pass 1 - Ubuntu 22.04, wizard, standard install

### 1. Package integrity

```bash
sha256sum -c CineMediaVault-Installer-*.zip.sha256
unzip -q CineMediaVault-Installer-*.zip && cd CineMediaVault-Complete-Installer
```

- [ ] Checksum matches.
- [ ] `install.sh` is executable.

### 2. The test suite runs on the target too

```bash
./tests/run-tests.sh
```

- [ ] All tests pass. (They need no root and no network.)

### 3. Dry run changes nothing

```bash
cp docs/examples/minimal.yaml /tmp/t.yaml
$EDITOR /tmp/t.yaml          # point it at the sample library
sudo ./install.sh --dry-run --config /tmp/t.yaml
```

- [ ] It completes and prints a plan.
- [ ] `ls /opt/cinemediavault` → does not exist.
- [ ] `ls /etc/cinemediavault` → does not exist.
- [ ] `systemctl list-units 'cinemediavault*'` → nothing.
- [ ] The sample library is untouched (`ls -la`, compare counts).

### 4. Bootstrap and the setup page

```bash
sudo ./install.sh
```

- [ ] Prerequisites install without error.
- [ ] It prints an address and a setup code.
- [ ] The page loads from **another machine** on the LAN.
- [ ] The page does **not** load from outside the trusted range (if you can test
      this).
- [ ] A wrong code is refused.
- [ ] Six wrong codes in a row lock that address out for five minutes.
- [ ] The correct code gets in.

### 5. Walk the wizard

Work through all fourteen steps, and specifically confirm:

- [ ] **Welcome** shows this machine's real cores, memory and graphics.
- [ ] **Administrator**: a 6-character password is refused with a clear reason.
- [ ] **Administrator**: `password123` is refused as a common password.
- [ ] **Administrator**: a good password is accepted.
- [ ] **Network**: `0.0.0.0/0` as a trusted network is refused.
- [ ] **Network**: a port already in use is reported as such.
- [ ] **Media**: `/etc` is refused as a library path.
- [ ] **Media**: `/mnt` is refused as too broad.
- [ ] **Media**: a valid path reports its item count and free space.
- [ ] **Media**: an empty folder warns about an unmounted share.
- [ ] **Metadata**: the TMDb key masks itself after entry.
- [ ] **Live TV** (if applicable): the tuner is found, or manual entry works.
- [ ] **Live TV**: the channel list loads. No channel scan is started on the
      tuner.
- [ ] **Guide**: choosing "extended" reveals the collector settings.
- [ ] **Recording**: retention defaults to "never".
- [ ] **Subtitles**: enabling Whisper on this CPU-only VM shows the honest
      warning about speed.
- [ ] **Modules**: selecting the bulk re-encoder shows the warning about
      rewriting source media.
- [ ] **Review** lists your choices in plain language plus every machine check.

### 6. Resumability

Partway through the wizard:

- [ ] Refresh the browser → it returns to the same step with your answers intact.
- [ ] Close the tab, reopen → same.
- [ ] `sudo reboot`, then `sudo ./install.sh` again and reopen the page → it
      resumes at the step you had reached.
- [ ] The password does **not** have to be re-entered (the hash was kept).
- [ ] `sudo grep -r '<your password>' /var/lib/cinemediavault/` → **no match**.

### 7. Dry run from the wizard

- [ ] Press **Dry run** on the review screen. It completes.
- [ ] Nothing was created (`/opt/cinemediavault` still absent).

### 8. Install

- [ ] Progress shows each step with a result.
- [ ] The log streams, and contains **no** password, TMDb key or token.
- [ ] It finishes with a success screen and a working link.
- [ ] The setup service stops when you press **Close setup**:
      `systemctl status cinemediavault-setup.service` → inactive.
- [ ] `systemctl is-enabled cinemediavault-setup.service` → **not** enabled.

### 9. First look

- [ ] The server loads at the printed address.
- [ ] The certificate warning appears once, then the site works.
- [ ] Your administrator account signs in.
- [ ] The old default account does **not** exist and `admin1` does not work
      anywhere.
- [ ] Films appear (allow a few minutes for the first scan).
- [ ] A film plays.
- [ ] Seeking works; the position is remembered after reload.
- [ ] TV shows, seasons and episodes appear.
- [ ] Search returns results.

### 10. Smoke test

```bash
sudo cinevaultctl smoke-test
sudo cinevaultctl smoke-test --deep
sudo cinevaultctl status
```

- [ ] `RESULT: PASS`.
- [ ] Deep test confirms ffmpeg and, if applicable, the tuner.

### 11. Permissions and secrets

```bash
sudo ls -l /etc/cinemediavault/
sudo ls -l /etc/cinemediavault/certs/
ps -o user= -C python3 | sort -u
```

- [ ] `secrets.env` is `0640 root:cinevault`.
- [ ] `cinevault.env` is `0640`.
- [ ] The TLS key is not world-readable.
- [ ] `admin-bootstrap.json` has been **removed** (verification deletes it).
- [ ] The service runs as `cinevault`, not root.
- [ ] `sudo grep -rl '<your password>' /etc /var/lib /var/log 2>/dev/null` →
      **no match**.
- [ ] `sudo grep -rl '<your TMDb key>' /var/log` → **no match**.

### 12. Idempotency - the important one

```bash
sudo ./install.sh --config /etc/cinemediavault/cinemediavault.yaml
```

- [ ] It completes.
- [ ] It reports steps as already done rather than redoing them.
- [ ] No duplicate users: `getent passwd cinevault | wc -l` → 1.
- [ ] No duplicate fstab lines: `grep -c cinemediavault /etc/fstab`.
- [ ] The service is still up and still works.
- [ ] Watch history from step 9 survived.

### 13. Reboot survival

```bash
sudo reboot
```

- [ ] The service comes back on its own.
- [ ] Timers are active: `systemctl list-timers 'cinemediavault*'`.
- [ ] The site works, sign-in works, playback works.
- [ ] Module services are back.

### 14. Self-healing

```bash
sudo pkill -f cinemediavault.py
# wait up to three minutes
systemctl status cinemediavault.service
sudo tail /var/log/cinemediavault/health.log
```

- [ ] The service was restarted automatically.
- [ ] The health log records the failures and the restart.
- [ ] It did **not** restart after a single failed probe.

### 15. Backup and restore

```bash
sudo cinevaultctl backup --label acceptance
# make a visible change: create a user, or mark something watched
sudo cinevaultctl restore /var/lib/cinemediavault/backups/*acceptance.tar.gz
```

- [ ] The archive is created and `tar -tzf` lists databases and configuration.
- [ ] `tar -tzf ... | grep -c '\.key'` → **0**.
- [ ] `tar -tzf ...` contains no media file.
- [ ] The restore succeeds and the change is reverted.
- [ ] A pre-restore backup was taken automatically.

### 16. Live TV and DVR (if a tuner is available)

- [ ] `/live-tv` shows the grid with logos and a current-time marker.
- [ ] Earlier/later navigation works.
- [ ] A channel plays live.
- [ ] **Record** on a programme starting within a few minutes creates a schedule
      entry.
- [ ] The recording starts at the padded time.
- [ ] It appears in **Recordings** and plays.
- [ ] Download gives the original `.ts`.
- [ ] Delete removes it.
- [ ] With two tuners: two concurrent recordings work, a third is refused rather
      than failing messily.
- [ ] `dvr.min_free_gb` set absurdly high causes recording to be refused with a
      clear message.

### 17. Extended guide (if enabled)

- [ ] `docker ps` shows `cinemediavault-epg` running.
- [ ] `sudo ss -lntp | grep 3010` shows it bound to a **private address**, not
      `0.0.0.0`.
- [ ] `sudo cinevaultctl epg status` reports the container and guide.
- [ ] After the first collection, the grid reaches beyond the tuner's horizon.
- [ ] Stop the container: the guide still works from the tuner's data.

### 18. Optional modules

For each you installed:

- [ ] Music: library appears, a track plays, seeking works, the position
      survives a reload.
- [ ] Book Vault: reachable on its port, a book opens.
- [ ] Comics: reachable, a comic opens.
- [ ] Games: the page loads; with a ROM present, a game runs.
- [ ] Video Wall: four streams play, fullscreen works, positions persist.
- [ ] Downloads: a film downloads and plays locally.

### 19. Rollback

```bash
sudo cinevaultctl config set meta.instance_name "Renamed"
sudo cinevaultctl apply
sudo cinevaultctl rollback
```

- [ ] The name reverts.
- [ ] The service still works.

### 20. Uninstall preserves media

```bash
ls -la /srv/media/Movies | tee /tmp/before.txt
sudo cinevaultctl uninstall --remove-state --remove-generated-media --yes
ls -la /srv/media/Movies | tee /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt
```

- [ ] **The media is identical.** This is the single most important check here.
- [ ] Recordings survive.
- [ ] `/opt/cinemediavault` is gone.
- [ ] No `cinemediavault*` units remain.
- [ ] A final backup was written to `/var/backups/`.
- [ ] `grep cinemediavault /etc/fstab` → nothing managed remains.

---

## Pass 2 - Ubuntu 24.04, unattended install

Restore the clean 24.04 snapshot.

```bash
cinevaultctl config example > /tmp/full.yaml     # or reuse pass 1's config
sudo ./install.sh --dry-run --config /tmp/full.yaml
sudo ./install.sh --config /tmp/full.yaml
sudo cinevaultctl smoke-test
```

- [ ] Validation catches a deliberately broken value (set `network.https_port`
      to `99999`).
- [ ] Dry run changes nothing.
- [ ] The install succeeds with no interaction.
- [ ] The result is equivalent to pass 1.
- [ ] `CMV_NETWORK_HTTPS_PORT=5443 sudo -E ./install.sh --config /tmp/full.yaml`
      applies the override.
- [ ] An invalid override name is rejected with a clear message.
- [ ] Smoke test passes.
- [ ] A second run changes nothing.

---

## Pass 3 - upgrade and migration

- [ ] On the pass-1 VM, run the installer again from the same package: it
      converges and reports no changes.
- [ ] Take a backup on pass 1, restore it onto pass 2, and confirm accounts,
      watch history and DVR schedules transfer.
- [ ] Smoke test passes on the migrated machine.

---

## Pass 4 - failure handling

Deliberately break things and confirm the installer behaves:

- [ ] Install with a media path that does not exist and "create missing" off →
      refused at preflight, nothing changed.
- [ ] Install with a port already occupied → refused, nothing changed.
- [ ] Interrupt an install with Ctrl-C partway → `cinevaultctl status` shows
      where it stopped; running again resumes correctly.
- [ ] Power-cycle the VM during an install → the same.
- [ ] Point at an unmounted share, let the refresh run → the catalogue is **not**
      emptied, and `refresh.log` explains why.
- [ ] Fill the recordings volume → recording is refused rather than corrupting a
      file.

---

## Sign-off

| | |
|---|---|
| Tester | |
| Date | |
| Package version / checksum | |
| Ubuntu 22.04 pass | ☐ pass ☐ fail |
| Ubuntu 24.04 pass | ☐ pass ☐ fail |
| Upgrade / migration | ☐ pass ☐ fail |
| Failure handling | ☐ pass ☐ fail |
| **Media never modified** | ☐ confirmed |
| **No secret found on disk or in logs** | ☐ confirmed |
| Issues found | |

Anything that fails should be recorded with the exact command, the output, and
the contents of `/var/log/cinemediavault/install-*.jsonl` for that run.
