# Ollama GPU Host and Open WebUI

This is the sanitized recovery bundle for the home-network AI deployment.

## Architecture

- `192.168.1.20:8080`: Open WebUI 0.11.1 and the read-only home-network tool gateway.
- `192.168.1.232:11434`: Ollama 0.33.2 with GTX 1080 Ti acceleration.
- Models: `qwen3:4b` and `llama3.2:3b`.
- Transcoding remains on `.232`; Ollama runs with low CPU and I/O priority and one request/model at a time.

Open WebUI's persisted `ollama.base_urls` value and its container environment both point to
`http://192.168.1.232:11434`. RAG/embedding settings were not moved.

## Model tools

Open WebUI exposes the `Current Time, Internet & Home Network` tool connection to models hosted
on `.232`. Its OpenAPI gateway runs on `.20` and provides:

- Exact current time in `America/Denver` and UTC.
- Public internet search through Bing's RSS search results.
- Text extraction from public HTTP/HTTPS pages.
- The existing allowlisted, read-only home-network diagnostics.

Internet page requests resolve and validate the destination before connecting. Private, loopback,
link-local, and reserved IP addresses are blocked to prevent the internet reader from becoming a
route into the LAN. Responses have time and size limits. The bearer token remains only in its
runtime file and is excluded from this backup.

## Local development agent

Server `.232` also hosts OpenCode 1.18.25 and a dedicated `dev-agent:14b` model based on Qwen3
14B Q4_K_M. The agent has an 8K context window and runs fully on the GTX 1080 Ti. The original
`qwen2.5-coder:14b` model remains installed for pure code-generation tasks.

Launch the terminal agent from a project directory with:

```bash
/home/jnicolas/.local/bin/dev-agent /path/to/project
```

All OpenCode tool permissions are enabled without interactive approval, including file edits, shell
commands, web access, and paths outside the starting workspace. Processes still run as Linux user
`jnicolas`; the browser agent does not receive passwordless root or sudo access. The agent is
intentionally not exposed as an unauthenticated network service.

OpenCode's child-process sandbox strips the file capability used by `/usr/bin/ping`. A local
`~/.local/bin/ping` shim uses a dedicated self-SSH key to execute the system ping under the same
unprivileged `jnicolas` account. The private key is generated on `.232` and is excluded from backups.

The password-protected web interface runs as a user service at `http://192.168.1.232:4096`.
Its username is `jnicolas`; the generated password is stored outside the repository in
`/mnt/c/Data/opencode-232.pass.txt`. The runtime environment file is mode `0600` and is excluded
from all source-control backups.

The web service starts in `/home/jnicolas/dev-agent-workspace`. Starting it at the home-directory
root prevents OpenCode's file picker from initializing and leaves the browser without a usable
project. A `Welcome to Dev Agent` starter session is created in the dedicated workspace.

The 14B model is the strongest practical local tier for this VM. Its approximately 9.7 GB GPU
footprint fits beside the small NVENC allocation, but initial model loading and long agent turns are
noticeably slower during active transcoding. Frontier cloud coding models remain materially stronger.

## Resource and safety settings

Ollama is configured with one loaded model and one parallel request. Flash Attention and q8 KV
cache are enabled. The launcher uses `nice -n 15` and idle-class I/O priority so HandBrake remains
the priority workload.

VM 100 has a pending Proxmox change from 4 vCPU/4 GiB to 6 vCPU/6 GiB. It deliberately was not
rebooted while a transcode was active. The change applies at the next normal VM reboot.

## Restore

1. Install the official Ollama Linux bundle under `/home/jnicolas/.local` on `.232`.
2. Copy `server-232/start-ollama-gpu.sh` to `/home/jnicolas/.local/bin/`.
3. Copy `server-232/ollama-gpu.service` to `/home/jnicolas/.config/systemd/user/`.
4. Run `systemctl --user daemon-reload`, then enable and start `ollama-gpu.service`.
5. Preserve the existing user crontab and append the `@reboot` entry in `server-232/crontab-entry.txt`.
6. Pull `qwen3:4b` and `llama3.2:3b`.
7. Point Open WebUI at `http://192.168.1.232:11434` using the updater in `server-20/`.

Before changing Open WebUI, copy `/app/backend/data/webui.db` out of the container and retain the
old stopped container as a rollback. Do not commit that database: it contains private application data.

## Validation

- `curl http://192.168.1.232:11434/api/tags` lists both models.
- Open WebUI reports healthy and `GET /api/version` returns 0.11.1.
- A qwen request completed in about 3.8 seconds during the initial test.
- `ollama ps` reported 100% GPU offload.
- HandBrake continued running during installation, model loading, and validation.

## Secrets excluded

This bundle intentionally excludes passwords, API tokens, gateway tokens, SSH keys, Open WebUI
databases, chat data, model weights, and media. Tokens are read from their runtime files and are not
embedded in the source.
