# Linux Suite

This is the portable Linux/WSL side of the CineVault software suite.

## Software

- `webex-notifier`: sends status/completion/failure messages.
- `transcode-orchestrator`: validates queues, starts jobs, encodes with
  HandBrake, archives originals, and deploys encoded assets.
- `cron-tools`: simple cron visibility helpers.

## Quick Start

```bash
cd /path/to/SOFTWARE/LINUX
./install_all.sh
```

Then edit:

```bash
webex-notifier/config/application.properties
transcode-orchestrator/config/application.properties
```

Enable hourly watchdog:

```bash
transcode-orchestrator/cron/install_transcode_cron.sh
```

Check status:

```bash
transcode-orchestrator/bin/status_transcode_orchestrator.sh
```

## Required Tools

- `python3`
- `ffmpeg` and `ffprobe`
- `HandBrakeCLI`
- `cron`

The installer attempts to install these with `apt`, `dnf`, or `yum`.
