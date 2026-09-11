# CineMediaVault backup, restore and migration

## What is worth backing up

| | Backed up | Why |
|---|---|---|
| Accounts, watch history, DVR schedule, playlists | **yes** | irreplaceable |
| Configuration | **yes** | rebuilding it by hand is tedious |
| API keys | **yes**, optionally | a restore without them is not a restore |
| Manual match corrections, custom posters | **yes** | your work |
| Generated metadata and artwork | **yes** | slow to refetch, and rate-limited |
| Your media | **no** | far too large, and not ours to copy |
| HLS and download caches | **no** | regenerate on demand |
| TLS private key | **no** | a backup travels; a private key should not |

---

## Taking a backup

```bash
sudo cinevaultctl backup
sudo cinevaultctl backup --destination /mnt/nas/backups --label weekly
sudo cinevaultctl backup --no-secrets            # safe to hand to someone else
```

Backups land in `/var/lib/cinemediavault/backups/` as
`cinemediavault-backup-<timestamp>-<label>.tar.gz`, and are pruned to
`ops.db_backup_keep` (10 by default).

A backup containing secrets is written mode 0600.

### What happens automatically

- a database backup every night at 01:00, verified and pruned;
- a full backup before every upgrade;
- a full backup before every restore;
- a snapshot of every file the installer replaces, kept in
  `/var/lib/cinemediavault/change-backups/` for rollback.

### Databases are copied consistently

Through SQLite's own backup API, not by copying the file, then verified with
`PRAGMA integrity_check`. A backup taken while someone is watching is still
sound.

---

## Getting backups off the machine

A backup on the same disk as the thing it backs up is not a backup.

```bash
# to a NAS
sudo cinevaultctl backup --destination /mnt/nas/cinemediavault

# to another host
sudo cinevaultctl backup --label offsite
rsync -av /var/lib/cinemediavault/backups/ backups@nas:/vault/cinemediavault/
```

A weekly copy, kept somewhere else, is enough for a home server.

---

## Inspecting a backup

```bash
tar -tzf cinemediavault-backup-20260901-120000-manual.tar.gz | head -30
tar -xzOf cinemediavault-backup-...tar.gz cinemediavault-backup/MANIFEST.json
```

The manifest records when it was taken, the version that took it, what is in it,
and what was deliberately left out.

---

## Restoring

```bash
sudo cinevaultctl restore /var/lib/cinemediavault/backups/cinemediavault-backup-....tar.gz
```

It shows what the archive contains and asks before proceeding. Then it:

1. backs up the current state (so the restore is reversible),
2. stops the service,
3. restores the databases, configuration and metadata,
4. fixes ownership,
5. starts the service.

Options:

```bash
--no-secrets      keep the API keys currently on this machine
--no-config       restore only data, keep this machine's configuration
--dry-run         show what would be restored
```

Afterwards:

```bash
sudo cinevaultctl smoke-test
```

### Restoring only the database

```bash
sudo systemctl stop cinemediavault.service
tar -xzf backup.tar.gz -C /tmp cinemediavault-backup/db/cinemediavault.db
sudo cp /tmp/cinemediavault-backup/db/cinemediavault.db \
        /var/lib/cinemediavault/db/cinemediavault.db
sudo chown cinevault:cinevault /var/lib/cinemediavault/db/cinemediavault.db
sudo systemctl start cinemediavault.service
```

---

## Migrating to another machine

1. **On the old machine**

   ```bash
   sudo cinevaultctl backup --label migration
   sudo cinevaultctl config show > cinemediavault-settings.yaml
   ```

2. **Move the media**, or point the new machine at the same shares. Keeping the
   same paths makes everything else trivial. If the paths change, edit them in
   the configuration before restoring - watch history is keyed on stable
   identities, not absolute paths, so it survives either way.

3. **On the new machine**

   ```bash
   sudo ./install.sh --config cinemediavault-settings.yaml
   sudo cinevaultctl restore cinemediavault-backup-...-migration.tar.gz
   sudo cinevaultctl smoke-test
   ```

4. **Check**: sign in, play a film, confirm continue-watching is intact, confirm
   the DVR schedule survived.

5. Leave the old machine intact for a week before reusing it.

---

## Upgrading

```bash
cd /path/to/new/CineMediaVault-Complete-Installer
sudo ./install.sh --config /etc/cinemediavault/cinemediavault.yaml
```

An upgrade migrates the configuration forward one schema version at a time,
takes a backup, then re-runs the same idempotent steps. Anything unchanged skips
itself.

To see what it would do first:

```bash
sudo cinevaultctl --config /etc/cinemediavault/cinemediavault.yaml plan
```

If an upgrade goes wrong:

```bash
sudo cinevaultctl rollback
```

---

## Disaster recovery, from nothing

You need: the installer package, a backup archive, and your media.

```bash
# 1. Fresh Ubuntu 22.04 or 24.04
# 2. Mount the media at the same paths as before
# 3. Install
sudo ./install.sh --config cinemediavault-settings.yaml
# 4. Restore
sudo cinevaultctl restore cinemediavault-backup-....tar.gz
# 5. Verify
sudo cinevaultctl smoke-test --deep
```

Without a backup you can still reinstall and rescan; what is lost is accounts,
watch history, DVR schedules and manual match corrections. The media itself was
never at risk.

---

## Testing your backups

An untested backup is a hope, not a backup. Once, on a spare VM or a container:

```bash
sudo ./install.sh --config cinemediavault-settings.yaml
sudo cinevaultctl restore <your latest backup>
sudo cinevaultctl smoke-test
```

Confirm accounts, watch history and the DVR schedule are all there.
