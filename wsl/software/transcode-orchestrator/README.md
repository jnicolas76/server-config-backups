# Transcode Orchestrator

This app keeps the movie and TV transcode queues healthy, starts one encode at
a time, copies the source locally, runs HandBrake, validates the result, archives
the original under the configured `COMPLETED` folder, and moves the finished
asset back to the original media folder.

## Main Files

- `config/application.properties`: all paths and tuning settings.
- `bin/combined_transcode_orchestrator.py`: chooses the next job.
- `bin/handbrake_transcode_worker.py`: copy, encode, verify, archive, replace.
- `bin/transcode_queue_watchdog.py`: hourly queue validator and self-healer.
- `bin/start_combined_transcode_orchestrator.sh`: starts orchestrator in the background.
- `bin/status_transcode_orchestrator.sh`: shows current encode and progress.
- `cron/install_transcode_cron.sh`: installs reboot and hourly cron.
- `cron/remove_transcode_cron.sh`: removes the watchdog cron.

## Configure

Edit:

```bash
LINUX/transcode-orchestrator/config/application.properties
```

Important settings:

```properties
movies.root=/mnt/nfs-share-movies/Movies
movies.completed.dir=/mnt/nfs-share-movies/COMPLETED
tv.root=/mnt/nfs-share-tvshows/TV Shows
tv.completed.dir=/mnt/nfs-share-tvshows/COMPLETED
movies.gb.per.hour=0.5
movies.encoder=x265
movies.preset=slow
tv.target.gb=0.7
tv.encoder=x265
tv.preset=slow
watchdog.cron.minute=7
```

For faster but slightly less efficient movie encodes, change:

```properties
movies.preset=medium
```

## Install

```bash
cd /path/to/SOFTWARE/LINUX/transcode-orchestrator
./install.sh
./cron/install_transcode_cron.sh
```

## Run Manually

```bash
./bin/start_combined_transcode_orchestrator.sh
```

## Status

```bash
./bin/status_transcode_orchestrator.sh
```

## Stop

```bash
./bin/stop_transcode_orchestrator.sh
```

## Hints

- Keep `work.dir` on a fast local disk, not on NFS.
- Keep `completed.dir` on the same NFS share as the media root when possible.
- The watchdog is designed to run hourly and exit. It is not a permanent loop.
- If a job fails, the next watchdog run validates queues and restarts the
  orchestrator if nothing is actively encoding.
- Copa can be disabled with `orchestrator.skip.copa=true`.
