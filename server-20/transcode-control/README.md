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

## Safety

Queue changes are serialized through the agent, written atomically, and copied
to timestamped backups before modification. Stop operations target only the
known orchestrator and HandBrake process tree.

