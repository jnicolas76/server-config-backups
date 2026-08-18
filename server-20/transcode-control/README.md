# Transcode Control

Transcode Control is a two-part management application for the WSL HandBrake
orchestrator:

- `server.py` runs the dashboard on the always-on Linux server.
- `agent.py` runs beside the WSL queues and workers. It reports status and
  executes authenticated commands received from the dashboard.

No inbound connection to WSL is required. The agent polls the server, which
continues to work when WSL receives a different virtual IP.

## Configuration

Copy the example environment files and change the shared secret and dashboard
password. Both components must use the same `AGENT_SECRET`.

```bash
cp server.env.example server.env
cp agent.env.example agent.env
```

## Services

The deployment installs user-level systemd services:

- `transcode-control.service` on the dashboard server
- `transcode-control-agent.service` in WSL

The dashboard defaults to `http://SERVER_IP:8126`.

## Configuration model

The dashboard exposes four configuration areas. Changes are audited on the
server, delivered to the WSL agent, and written atomically to
`/mnt/c/DATA/transcode-control-config.json`.

- **Libraries** sets movie/TV source roots, local staging folders, and archive
  locations.
- **Profiles** selects H.264 or H.265, MP4 or MKV, encoder preset, target size
  mode, and audio bitrate.
- **Rules** defines extensions, source-size limits, filename inclusion and
  exclusion expressions, and completed-ledger handling.
- **Workers** records concurrency and retry policy. The current HandBrake
  orchestrator remains single-worker; values above one are retained for the
  multi-worker scheduler and are not silently launched today.

Per-job settings override a library default. Existing jobs keep their former
behavior until a profile is saved, so installing an updated dashboard does not
change an encode already in progress.

## Safety

Queue changes are serialized through the agent, written atomically, and copied
to timestamped backups before modification. Stop operations target only the
known orchestrator and HandBrake process tree.
