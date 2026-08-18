# CineMediaVault Transcode Control

Portable controller and Linux/WSL agent for managing the existing movie and TV
transcode queues. The package intentionally contains no database, passwords,
tokens, cache, queue data, or media.

## Features

- Queue ordering, removal, additions, and per-job profiles.
- H.264/H.265 and MP4/MKV output choices supported by the current worker.
- Target file size or GB-per-hour sizing.
- Movie and TV default profiles, library/work/archive paths, eligibility rules,
  retry policy, and worker-slot settings.
- ffprobe details: runtime, container, codecs, resolution, and audio channels.
- Live title, progress, ETA, process ID, CPU, memory, disks, history, and savings.

## Install the Controller

On the web/dashboard server:

```bash
cp controller.env.example controller.env
nano controller.env
./install-controller.sh
```

Open `http://SERVER_IP:8126`.

## Install the Agent

On the machine that owns the queues and runs HandBrake:

```bash
cp agent.env.example agent.env
nano agent.env
./install-agent.sh
```

Use the same `AGENT_SECRET` in both environment files. Adjust queue, ledger,
profile, orchestrator, and media paths in `agent.env` for the target machine.

## Configuration

The UI stores controller settings in SQLite and sends changes to the agent. The
agent writes `transcode-control-config.json` atomically beside its data files.
The orchestrator reads that file at job boundaries, so an active encode is not
interrupted by a settings change.

`max_concurrent`, CPU slots, and GPU slots are retained as policy settings. The
current combined orchestrator is deliberately single-worker; this package does
not silently launch unsafe parallel jobs. Multi-worker scheduling requires a
separate dispatcher and per-worker scratch directories.

## Service Commands

```bash
systemctl --user status transcode-control.service
systemctl --user status transcode-control-agent.service
journalctl --user -u transcode-control.service -f
journalctl --user -u transcode-control-agent.service -f
```

