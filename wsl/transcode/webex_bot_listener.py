#!/usr/bin/env python3
"""Direct-message WebEx command listener for local WSL media scripts.

This is intentionally polling-based so it works from WSL without exposing a
public webhook endpoint. Only ALLOWED_EMAIL is allowed to control it.
"""

from __future__ import annotations

import csv
import json
import os
import re
import secrets
import shlex
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TOKEN_FILE = Path(os.environ.get("WEBEX_TOKEN_FILE", str(Path.home() / ".config" / "webex" / "token")))
ALLOWED_EMAIL = os.environ.get("WEBEX_ALLOWED_EMAIL", "jonathan.nicolas@gmail.com").lower()
DATA_DIR = Path(os.environ.get("CINEVAULT_DATA_DIR", "/mnt/c/DATA"))
STATE_FILE = Path(os.environ.get("WEBEX_LISTENER_STATE", str(DATA_DIR / "webex-bot-listener-state.json")))
LOG_FILE = Path(os.environ.get("WEBEX_LISTENER_LOG", str(DATA_DIR / "webex-bot-listener.log")))
POLL_SECONDS = int(os.environ.get("WEBEX_LISTENER_POLL_SECONDS", "8"))
SECURITY_CODE_TTL_SECONDS = int(os.environ.get("WEBEX_SECURITY_CODE_TTL_SECONDS", "300"))
CHATGPT_AGENT_RESTART_SCRIPT = Path(os.environ.get("CHATGPT_AGENT_RESTART_SCRIPT", str(DATA_DIR / "restart_chatgpt_agent.sh")))
UNAUTHORIZED_ALERT_COOLDOWN_SECONDS = int(os.environ.get("WEBEX_UNAUTHORIZED_ALERT_COOLDOWN_SECONDS", "300"))

API_BASE = "https://webexapis.com/v1"

CINEVAULT_HOST = os.environ.get("CINEVAULT_HOST", "192.168.1.20")
CINEVAULT_SSH_USER = os.environ.get("CINEVAULT_SSH_USER", "jnicolas")
CINEVAULT_PROD_PORT = int(os.environ.get("CINEVAULT_PROD_PORT", "8093"))
CINEVAULT_LAB_PORT = int(os.environ.get("CINEVAULT_LAB_PORT", "5000"))
CINEVAULT_PROD_SCRIPT = os.environ.get("CINEVAULT_PROD_SCRIPT", "/home/jnicolas/cinevault-watch-8093.py")
CINEVAULT_LAB_SCRIPT = os.environ.get("CINEVAULT_LAB_SCRIPT", "/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py")
CINEVAULT_PROD_LOG = os.environ.get("CINEVAULT_PROD_LOG", "/home/jnicolas/cinevault-8093.log")
CINEVAULT_LAB_LOG = os.environ.get("CINEVAULT_LAB_LOG", "/home/jnicolas/cinemediavault-lab/lab-5000.log")
CINEVAULT_LAB_START_SCRIPT = os.environ.get("CINEVAULT_LAB_START_SCRIPT", "/home/jnicolas/cinemediavault-lab/start_lab_5000.sh")
CINEVAULT_LAB_STOP_SCRIPT = os.environ.get("CINEVAULT_LAB_STOP_SCRIPT", "/home/jnicolas/cinemediavault-lab/stop_lab_5000.sh")

MOVIE_ROOT = Path(os.environ.get("MOVIE_ROOT", "/mnt/nfs-share-movies/Movies"))
TV_ROOT = Path(os.environ.get("TV_ROOT", "/mnt/nfs-share-tvshows/TV Shows"))
MOVIE_COMPLETED = Path(os.environ.get("MOVIE_COMPLETED", "/mnt/nfs-share-movies/COMPLETED"))
TV_COMPLETED = Path(os.environ.get("TV_COMPLETED", "/mnt/nfs-share-tvshows/COMPLETED"))
MOVIE_QUEUE = Path(os.environ.get("MOVIE_QUEUE", str(DATA_DIR / "orchestrator-movies-h265-queue.csv")))
TV_QUEUE = Path(os.environ.get("TV_QUEUE", str(DATA_DIR / "orchestrator-tv-h265-queue.csv")))
WATCHDOG = Path(os.environ.get("TRANSCODE_WATCHDOG", str(DATA_DIR / "transcode_queue_watchdog.py")))
ORCHESTRATOR_PID = Path(os.environ.get("ORCHESTRATOR_PID", str(DATA_DIR / "combined-transcode-orchestrator.pid")))
MOVIE_INDEX = Path(os.environ.get("MOVIE_LIVE_INDEX", str(DATA_DIR / "cinevault/home/media-download-library/movie-live-index.json")))
TV_INDEX = Path(os.environ.get("TV_LIVE_INDEX", str(DATA_DIR / "cinevault/home/tv-download-library/tv-live-index.json")))
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".m4v", ".avi", ".mov", ".mpeg", ".mpg", ".m2ts", ".ts", ".webm"}


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def load_token() -> str:
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"WebEx token is empty: {TOKEN_FILE}")
    return token


def api_request(token: str, method: str, path: str, payload: dict | None = None, query: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status == 204:
                return {}
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"WebEx HTTP {error.code}: {detail}") from None


def send_message(token: str, room_id: str, markdown: str) -> None:
    api_request(token, "POST", "/messages", {"roomId": room_id, "markdown": markdown})


def send_direct_message(token: str, email: str, markdown: str) -> None:
    api_request(token, "POST", "/messages", {"toPersonEmail": email, "markdown": markdown})


def shell(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)


def shell_text(command: str, timeout: int = 30) -> str:
    result = shell(["bash", "-lc", command], timeout=timeout)
    text = (result.stdout + result.stderr).strip()
    return text if text else "(no output)"


def remote_cinevault_shell(command: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    target = f"{CINEVAULT_SSH_USER}@{CINEVAULT_HOST}"
    return shell(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target, command], timeout=timeout)


def queue_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except Exception as exc:
        log(f"failed reading queue {path}: {exc}")
        return []


def queue_fieldnames(path: Path) -> list[str]:
    if not path.is_file():
        return ["file_path"]
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            return reader.fieldnames or ["file_path"]
    except Exception:
        return ["file_path"]


def queue_item_names(path: Path, limit: int = 8) -> list[str]:
    names: list[str] = []
    for row in queue_rows(path)[:limit]:
        raw = row.get("file_path") or row.get("full_path") or row.get("path") or ""
        if raw:
            names.append(Path(raw).name)
    return names


def row_path(row: dict) -> str:
    return row.get("file_path") or row.get("full_path") or row.get("path") or ""


def human_size(size: int | float | str | None) -> str:
    try:
        value = float(size or 0)
    except (TypeError, ValueError):
        return "unknown"
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024


def item_path(kind: str, item: dict) -> str:
    raw = item.get("path") or ""
    rel = item.get("rel_path") or ""
    if kind == "movies" and rel:
        return str(MOVIE_ROOT / rel)
    if kind == "tv" and rel:
        return str(TV_ROOT / rel)
    return raw


def pid_running(pid_file: Path) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    return Path(f"/proc/{pid}").exists()


def active_transcodes() -> list[str]:
    result = shell(["bash", "-lc", "ps -eo pid,ppid,stat,etime,cmd | grep -Ei 'HandBrakeCLI|handbrake_transcode_worker|combined_transcode_orchestrator' | grep -v grep"], timeout=20)
    return [line for line in result.stdout.splitlines() if line.strip()]


def current_job() -> str:
    lines = active_transcodes()
    if not lines:
        return "none"
    for line in lines:
        match = re.search(r"--file\s+(.+?)\s+--work-dir", line)
        if match:
            return Path(match.group(1)).name
    for line in lines:
        match = re.search(r"-i\s+(.+?)\s+-o", line)
        if match:
            return Path(match.group(1)).name
    return lines[0].split(None, 4)[-1][:180]


def latest_progress() -> str:
    logs = [DATA_DIR / "combined-transcode-movies-h265.log", DATA_DIR / "combined-transcode-tv-h265.log"]
    logs = [path for path in logs if path.exists()]
    logs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in logs:
        try:
            tail = shell(["tail", "-n", "250", str(path)], timeout=20).stdout
        except Exception:
            continue
        for line in reversed(tail.splitlines()):
            if "Encoding:" in line and "%" in line:
                return line.strip()[-180:]
    return "not reported yet"


def df_line(label: str, path: Path) -> str:
    result = shell(["df", "-h", str(path)], timeout=20)
    if result.returncode != 0:
        return f"{label}: unavailable"
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return f"{label}: unavailable"
    parts = lines[1].split()
    if len(parts) < 5:
        return f"{label}: {lines[1]}"
    return f"{label}: {parts[3]} free of {parts[1]} ({parts[4]} used)"


def completed_line(label: str, path: Path) -> str:
    if not path.exists():
        return f"{label}: unavailable"
    size = shell(["du", "-sh", str(path)], timeout=60).stdout.split()
    count = shell(["bash", "-lc", f"find {shlex.quote(str(path))} -type f 2>/dev/null | wc -l"], timeout=60).stdout.strip()
    return f"{label}: {count or '?'} files, {size[0] if size else '?'}"


def status_summary() -> str:
    movies = queue_rows(MOVIE_QUEUE)
    tv = queue_rows(TV_QUEUE)
    lines = [
        "**WSL Transcode Status**",
        "",
        f"- Current job: `{current_job()}`",
        f"- Progress: `{latest_progress()}`",
        f"- Orchestrator PID healthy: {'yes' if pid_running(ORCHESTRATOR_PID) else 'no'}",
        f"- Movie queue: {len(movies)} pending",
        f"- TV queue: {len(tv)} pending",
        f"- {df_line('Movies disk', MOVIE_ROOT)}",
        f"- {df_line('TV Shows disk', TV_ROOT)}",
        f"- {completed_line('Movies COMPLETED', MOVIE_COMPLETED)}",
        f"- {completed_line('TV COMPLETED', TV_COMPLETED)}",
    ]
    return "\n".join(lines)


def queue_summary(kind: str) -> str:
    if kind == "movies":
        path = MOVIE_QUEUE
        title = "Movie Queue"
    else:
        path = TV_QUEUE
        title = "TV Shows Queue"
    rows = queue_rows(path)
    names = queue_item_names(path, 10)
    body = "\n".join(f"{idx}. `{name}`" for idx, name in enumerate(names, 1)) or "none"
    return f"**{title}**\n\n- Pending: {len(rows)}\n- Current job: `{current_job()}`\n\n**Next items**\n{body}"


def load_index_items(kind: str) -> list[dict]:
    index = MOVIE_INDEX if kind == "movies" else TV_INDEX
    key = "movies" if kind == "movies" else "episodes"
    if index.is_file():
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
            return [item for item in data.get(key, []) if item.get("modified")]
        except Exception as exc:
            log(f"failed reading live index {index}: {exc}")
    root = MOVIE_ROOT if kind == "movies" else TV_ROOT
    items: list[dict] = []
    if not root.exists():
        return items
    command = (
        f"find {shlex.quote(str(root))} -type f "
        + r"\( "
        + " -o ".join(f"-iname '*{ext}'" for ext in sorted(VIDEO_EXTENSIONS))
        + r" \) -printf '%T@\t%s\t%p\n' 2>/dev/null | sort -rn | head -20"
    )
    result = shell(["bash", "-lc", command], timeout=120)
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        modified, size, path = parts
        items.append({
            "title": Path(path).stem,
            "path": path,
            "size": int(size),
            "modified": float(modified),
        })
    return items


def latest_items(kind: str, limit: int = 10) -> list[dict]:
    items = load_index_items(kind)
    items.sort(key=lambda item: float(item.get("modified") or 0), reverse=True)
    normalized: list[dict] = []
    for item in items[:limit]:
        path = item_path(kind, item)
        if kind == "movies":
            title = item.get("title") or Path(path).stem
            label = title
        else:
            show = item.get("show") or Path(path).parents[1].name if len(Path(path).parents) > 1 else "TV Show"
            season = item.get("season") or Path(path).parent.name
            title = item.get("title") or Path(path).stem
            label = f"{show} / {season} / {title}"
        normalized.append({
            "label": label,
            "path": path,
            "size": int(item.get("size") or 0),
            "modified": float(item.get("modified") or 0),
        })
    return normalized


def latest_menu(kind: str, state: dict) -> str:
    items = latest_items(kind)
    state[f"latest_{kind}"] = items
    title = "Latest Movies" if kind == "movies" else "Latest TV Episodes"
    disk = df_line("Movies disk" if kind == "movies" else "TV Shows disk", MOVIE_ROOT if kind == "movies" else TV_ROOT)
    lines = [f"**{title}**", "", f"- {disk}", ""]
    if not items:
        lines.append("No recent items found.")
        return "\n".join(lines)
    for idx, item in enumerate(items, 1):
        when = datetime.fromtimestamp(item["modified"]).strftime("%Y-%m-%d %H:%M")
        lines.append(f"{idx}. `{item['label']}` - {human_size(item['size'])} - {when}")
    noun = "movie" if kind == "movies" else "tv"
    lines.extend(["", f"To bump one to the top of the queue, reply `{noun} 1` or `bump {noun} 1`."])
    return "\n".join(lines)


def bump_queue(kind: str, selection: int, state: dict) -> str:
    cache_key = "latest_movies" if kind == "movies" else "latest_tv"
    queue = MOVIE_QUEUE if kind == "movies" else TV_QUEUE
    items = state.get(cache_key) or []
    if selection < 1 or selection > len(items):
        return f"No cached selection {selection}. Run `latest movies` or `latest tv` first."
    selected = items[selection - 1]
    target = selected["path"]
    fieldnames = queue_fieldnames(queue)
    rows = queue_rows(queue)
    path_field = next((name for name in ("file_path", "full_path", "path") if name in fieldnames), fieldnames[0])
    target_norm = os.path.normpath(target)
    matching = [row for row in rows if os.path.normpath(row_path(row)) == target_norm]
    remaining = [row for row in rows if os.path.normpath(row_path(row)) != target_norm]
    top_row = matching[0] if matching else {name: "" for name in fieldnames}
    top_row[path_field] = target
    backup = queue.with_suffix(queue.suffix + f".bak-webex-bump-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    if queue.exists():
        backup.write_bytes(queue.read_bytes())
    temp = queue.with_suffix(queue.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(top_row)
        writer.writerows(remaining)
    temp.replace(queue)
    return (
        f"**Queue bumped**\n\n"
        f"- Type: {'Movies' if kind == 'movies' else 'TV Shows'}\n"
        f"- New top item: `{selected['label']}`\n"
        f"- Size: {human_size(selected.get('size'))}\n"
        f"- Queue file: `{queue}`\n"
        f"- Backup: `{backup.name}`\n\n"
        "Running transcodes were not stopped; this changes what runs next."
    )


def start_queues() -> str:
    if not WATCHDOG.is_file():
        return f"Watchdog missing: `{WATCHDOG}`"
    result = shell(["python3", str(WATCHDOG)], timeout=300)
    text = (result.stdout + result.stderr).strip()
    return "**Start/Validate Queues**\n\nWatchdog run completed.\n\n" + f"```text\n{text[-1600:] or '(no output)'}\n```"


def restart_chatgpt_agent(confirm: bool) -> str:
    if not confirm:
        return "**Restart WSL Agent**\n\nReply `restart agent confirm` to restart the WSL Webex bot listener / configured ChatGPT agent."
    if not CHATGPT_AGENT_RESTART_SCRIPT.is_file():
        return f"Restart script missing: `{CHATGPT_AGENT_RESTART_SCRIPT}`"
    command = f"nohup bash -lc 'sleep 3; {shlex.quote(str(CHATGPT_AGENT_RESTART_SCRIPT))}' >> {shlex.quote(str(LOG_FILE))} 2>&1 &"
    result = shell(["bash", "-lc", command], timeout=20)
    if result.returncode != 0:
        return "**Restart WSL Agent**\n\nFailed to schedule restart.\n\n" + f"```text\n{(result.stdout + result.stderr).strip()[-1200:]}\n```"
    return "**Restart WSL Agent**\n\nRestart scheduled. The listener should go quiet briefly and come back within a few seconds."


def stop_queues(confirm: bool) -> str:
    if not confirm:
        return "**Stop Queues**\n\nReply `stop confirm` to stop the WSL transcode orchestrator, HandBrake workers, and ffmpeg transcode jobs."
    script = DATA_DIR / "stop_wsl_transcode_queues.sh"
    if script.is_file():
        result = shell(["bash", str(script)], timeout=120)
        return "**Stop Queues**\n\n" + f"```text\n{(result.stdout + result.stderr).strip()[-1800:]}\n```"
    command = (
        "pkill -f 'combined_transcode_orchestrator.py' || true; "
        "pkill -f 'handbrake_transcode_worker.py' || true; "
        "pkill -f 'HandBrakeCLI' || true; "
        "pkill -f 'ffmpeg.*mobile-download|ffmpeg.*HANDBRAKE|ffmpeg.*transcode' || true; "
        "rm -f /mnt/c/DATA/combined-transcode-orchestrator.pid"
    )
    result = shell(["bash", "-lc", command], timeout=120)
    return "**Stop Queues**\n\nStop commands executed.\n\n" + f"```text\n{(result.stdout + result.stderr).strip() or '(no output)'}\n```"


def cinevault_instance(which: str) -> dict[str, str | int]:
    if which == "lab":
        return {
            "key": "lab",
            "label": "Lab 5000",
            "port": CINEVAULT_LAB_PORT,
            "script": CINEVAULT_LAB_SCRIPT,
            "log": CINEVAULT_LAB_LOG,
            "start_script": CINEVAULT_LAB_START_SCRIPT,
            "stop_script": CINEVAULT_LAB_STOP_SCRIPT,
            "pattern": "^python3 /home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py",
        }
    return {
        "key": "prod",
        "label": "Production 8093",
        "port": CINEVAULT_PROD_PORT,
        "script": CINEVAULT_PROD_SCRIPT,
        "log": CINEVAULT_PROD_LOG,
        "pattern": "^python3 /home/jnicolas/cinevault-watch-8093.py",
    }


def parse_cinevault_action(normalized: str) -> tuple[str, str] | None:
    words = normalized.split()
    if not words:
        return None
    action = next((word for word in words if word in {"start", "stop"}), "")
    if not action:
        return None
    if any(word in {"lab", "5000"} for word in words):
        return action, "lab"
    if any(word in {"prod", "production", "8093", "cine", "cinevault", "cinemediavault"} for word in words):
        return action, "prod"
    return None


def is_cinevault_secure_command(normalized: str) -> bool:
    return parse_cinevault_action(normalized) is not None


def cinevault_status_one(which: str) -> str:
    cfg = cinevault_instance(which)
    port = int(cfg["port"])
    pattern = str(cfg["pattern"])
    label = str(cfg["label"])
    command = (
        f"tmp=/tmp/cinemediavault_status_{port}.txt; "
        f"echo '== {shlex.quote(label)} =='; "
        f"if pgrep -af {shlex.quote(pattern)} >$tmp; then "
        f"  sed -n '1,3p' $tmp; "
        f"else "
        f"  echo 'process: stopped'; "
        f"fi; "
        f"if ss -ltnp 2>/dev/null | grep -q ':{port} '; then "
        f"  echo 'port: listening {port}'; "
        f"else "
        f"  echo 'port: not listening {port}'; "
        f"fi; "
        f"code=$(curl -k -s -o /dev/null -w '%{{http_code}}' --max-time 8 https://127.0.0.1:{port}/api/homepage/status || true); "
        f"if [ \"$code\" = '000' ]; then "
        f"  code=$(curl -s -o /dev/null -w '%{{http_code}}' --max-time 8 http://127.0.0.1:{port}/api/homepage/status || true); "
        f"fi; "
        f"echo \"api: HTTP $code\""
    )
    result = remote_cinevault_shell(command, timeout=30)
    text = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        text = text or f"ssh failed with exit code {result.returncode}"
    return text


def cinevault_services_summary() -> str:
    prod = cinevault_status_one("prod")
    lab = cinevault_status_one("lab")
    return (
        "**CineMediaVault Services**\n\n"
        f"Host: `{CINEVAULT_SSH_USER}@{CINEVAULT_HOST}`\n\n"
        f"```text\n{prod}\n\n{lab}\n```\n\n"
        "Secure commands:\n"
        "- `start prod` / `stop prod`\n"
        "- `start lab` / `stop lab`\n"
        "- `start 8093` / `stop 8093`\n"
        "- `start 5000` / `stop 5000`"
    )


def start_cinevault(which: str) -> str:
    cfg = cinevault_instance(which)
    port = int(cfg["port"])
    script = str(cfg["script"])
    log_path = str(cfg["log"])
    label = str(cfg["label"])
    script_dir = str(Path(script).parent)
    log_dir = str(Path(log_path).parent)
    if which == "lab":
        start_script = str(cfg["start_script"])
        command = (
            f"cd {shlex.quote(str(Path(start_script).parent))}; "
            f"{shlex.quote(start_script)}; "
            f"sleep 5; "
            f"tail -n 8 {shlex.quote(str(Path(start_script).parent / 'logs' / 'cinemediavault-lab-5000.log'))} 2>/dev/null || true; "
            f"tail -n 8 {shlex.quote(str(Path(start_script).parent / 'logs' / 'cinemediavault-lab-5000.err'))} 2>/dev/null || true"
        )
    else:
        command = (
            f"if ss -ltnp 2>/dev/null | grep -q ':{port} '; then "
            f"  echo '{label} already has port {port} listening.'; "
            f"else "
            f"  mkdir -p {shlex.quote(log_dir)}; "
            f"  cd {shlex.quote(script_dir)}; "
            f"  nohup python3 {shlex.quote(script)} --host 0.0.0.0 --port {port} > {shlex.quote(log_path)} 2>&1 & "
            f"  echo 'started {label} on port {port}'; "
            f"fi; "
            f"sleep 5; "
            f"tail -n 8 {shlex.quote(log_path)} 2>/dev/null || true"
        )
    result = remote_cinevault_shell(command, timeout=45)
    detail = (result.stdout + result.stderr).strip()[-1800:] or "(no output)"
    return f"**Start CineMediaVault {label}**\n\n```text\n{detail}\n\n{cinevault_status_one(str(cfg['key']))}\n```"


def stop_cinevault(which: str) -> str:
    cfg = cinevault_instance(which)
    pattern = str(cfg["pattern"])
    label = str(cfg["label"])
    if which == "lab":
        stop_script = str(cfg["stop_script"])
        command = f"cd {shlex.quote(str(Path(stop_script).parent))}; {shlex.quote(stop_script)}"
    else:
        command = (
            f"pids=$(pgrep -f {shlex.quote(pattern)} || true); "
            f"if [ -z \"$pids\" ]; then "
            f"  echo '{label} process already stopped.'; "
            f"else "
            f"  echo \"Stopping {label}: $pids\"; "
            f"  printf '%s\\n' \"$pids\" | xargs -r kill; "
            f"  sleep 2; "
            f"  still=$(pgrep -f {shlex.quote(pattern)} || true); "
            f"  if [ -n \"$still\" ]; then printf '%s\\n' \"$still\" | xargs -r kill -9; fi; "
            f"fi"
        )
    result = remote_cinevault_shell(command, timeout=45)
    detail = (result.stdout + result.stderr).strip()[-1800:] or "(no output)"
    return f"**Stop CineMediaVault {label}**\n\n```text\n{detail}\n\n{cinevault_status_one(str(cfg['key']))}\n```"


def menu() -> str:
    return (
        "**CineMediaVault WSL Bot**\n\n"
        "Reply with one option:\n\n"
        "1. Movies queue\n"
        "2. TV Shows queue\n"
        "3. Full status\n"
        "4. Start / validate queues\n"
        "5. Stop queues\n"
        "6. Disk + completed folders\n"
        "7. Latest movies / TV\n"
        "8. CineMediaVault services\n\n"
        "You can also type: `movies`, `tv`, `status`, `latest`, `latest movies`, `latest tv`, `movie 1`, `tv 1`, `start`, `stop confirm`, `cine`, `start prod`, `stop prod`, `start lab`, `stop lab`, `space`, `help`."
    )


def space_summary() -> str:
    return "\n".join([
        "**Disk / Completed**",
        "",
        f"- {df_line('Movies disk', MOVIE_ROOT)}",
        f"- {df_line('TV Shows disk', TV_ROOT)}",
        f"- {completed_line('Movies COMPLETED', MOVIE_COMPLETED)}",
        f"- {completed_line('TV COMPLETED', TV_COMPLETED)}",
    ])


def is_secure_command(normalized: str) -> bool:
    if normalized in {"4", "start", "resume", "validate", "stop confirm", "5 confirm", "restart agent confirm", "restart chatgpt confirm", "restart bot confirm", "8 confirm"}:
        return True
    if is_cinevault_secure_command(normalized):
        return True
    if re.fullmatch(r"(?:bump\s+)?(?:movie|movies|m)\s*#?\s*(\d+)", normalized):
        return True
    if re.fullmatch(r"(?:bump\s+)?(?:tv|show|shows|t)\s*#?\s*(\d+)", normalized):
        return True
    if re.fullmatch(r"m(\d+)", normalized):
        return True
    if re.fullmatch(r"t(\d+)", normalized):
        return True
    return False


def security_prompt(normalized: str, state: dict) -> str:
    code = f"{secrets.randbelow(100000):05d}"
    expires = time.time() + SECURITY_CODE_TTL_SECONDS
    state["pending_secure_action"] = {
        "code": code,
        "command": normalized,
        "expires": expires,
        "created": time.time(),
    }
    minutes = max(1, SECURITY_CODE_TTL_SECONDS // 60)
    return (
        "**Security confirmation required**\n\n"
        f"Command waiting: `{normalized}`\n\n"
        f"Reply with this 5-digit code within {minutes} minutes:\n\n"
        f"`{code}`\n\n"
        "No queue or process changes will happen until the code is confirmed."
    )


def consume_security_code(normalized: str, state: dict) -> str | None:
    match = re.fullmatch(r"(?:code\s+)?(\d{5})", normalized)
    if not match:
        return None
    pending = state.get("pending_secure_action") or {}
    if not pending:
        return "No command is waiting for confirmation."
    if time.time() > float(pending.get("expires") or 0):
        state.pop("pending_secure_action", None)
        return "The confirmation code expired. Run the command again to get a new code."
    if match.group(1) != pending.get("code"):
        return "Incorrect confirmation code. No action was taken."
    command = pending.get("command", "")
    state.pop("pending_secure_action", None)
    if not command:
        return "The saved command was empty. No action was taken."
    return execute_command(command, state, confirmed=True)


def execute_command(normalized: str, state: dict, confirmed: bool = False) -> str:
    if is_secure_command(normalized) and not confirmed:
        return security_prompt(normalized, state)
    if not normalized or normalized in {"help", "menu", "hi", "hello"}:
        return menu()
    cinevault_action = parse_cinevault_action(normalized)
    if cinevault_action:
        action, which = cinevault_action
        if action == "start":
            return start_cinevault(which)
        return stop_cinevault(which)
    if normalized in {"8", "cine", "cinevault", "cinemediavault", "cine status", "cinevault status", "cinemediavault status", "services", "service status"}:
        return cinevault_services_summary()
    if normalized in {"1", "movie", "movies", "movie queue", "queue movies"}:
        return queue_summary("movies")
    if normalized in {"2", "tv", "tv shows", "tv queue", "shows"}:
        return queue_summary("tv")
    if normalized in {"3", "status", "current", "job", "jobs"}:
        return status_summary()
    if normalized in {"4", "start", "resume", "validate"}:
        return start_queues()
    if normalized in {"5", "stop", "stop queues"}:
        return stop_queues(False)
    if normalized in {"stop confirm", "5 confirm"}:
        return stop_queues(True)
    if normalized in {"8", "restart", "restart agent", "restart chatgpt", "restart bot"}:
        return restart_chatgpt_agent(False)
    if normalized in {"restart agent confirm", "restart chatgpt confirm", "restart bot confirm", "8 confirm"}:
        return restart_chatgpt_agent(True)
    if normalized in {"6", "space", "disk", "completed"}:
        return space_summary()
    if normalized in {"7", "latest", "latest items", "new", "newest"}:
        return latest_menu("movies", state) + "\n\n" + latest_menu("tv", state)
    if normalized in {"latest movies", "new movies", "newest movies"}:
        return latest_menu("movies", state)
    if normalized in {"latest tv", "latest shows", "new tv", "new shows", "newest tv"}:
        return latest_menu("tv", state)
    match = re.fullmatch(r"(?:bump\s+)?(?:movie|movies|m)\s*#?\s*(\d+)", normalized)
    if match:
        return bump_queue("movies", int(match.group(1)), state)
    match = re.fullmatch(r"(?:bump\s+)?(?:tv|show|shows|t)\s*#?\s*(\d+)", normalized)
    if match:
        return bump_queue("tv", int(match.group(1)), state)
    match = re.fullmatch(r"m(\d+)", normalized)
    if match:
        return bump_queue("movies", int(match.group(1)), state)
    match = re.fullmatch(r"t(\d+)", normalized)
    if match:
        return bump_queue("tv", int(match.group(1)), state)
    return "I did not recognize that command.\n\n" + menu()


def handle(text: str, state: dict | None = None) -> str:
    state = state if state is not None else {}
    normalized = " ".join((text or "").strip().lower().split())
    code_result = consume_security_code(normalized, state)
    if code_result is not None:
        return code_result
    return execute_command(normalized, state)


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    seen = state.get("seen", [])
    state["seen"] = seen[-400:]
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def unauthorized_alert_allowed(sender: str, state: dict) -> bool:
    key = f"unauthorized_alert:{sender or 'unknown'}"
    last = float(state.get(key) or 0)
    now = time.time()
    if now - last < UNAUTHORIZED_ALERT_COOLDOWN_SECONDS:
        return False
    state[key] = now
    return True


def unauthorized_alert_text(sender: str, text: str, room_id: str) -> str:
    preview = (text or "").strip().replace("`", "'")[:500]
    return (
        "**Unauthorized Webex bot access attempt**\n\n"
        f"- Sender: `{sender or 'unknown'}`\n"
        f"- Room: `{room_id}`\n"
        f"- Message: `{preview or '(empty)'}`\n\n"
        "No command was executed."
    )


def poll_once(token: str, bot_id: str, state: dict) -> int:
    rooms = api_request(token, "GET", "/rooms", query={"type": "direct", "max": 100}).get("items", [])
    seen = set(state.get("seen", []))
    handled = 0
    for room in rooms:
        room_id = room.get("id")
        if not room_id:
            continue
        messages = api_request(token, "GET", "/messages", query={"roomId": room_id, "max": 10}).get("items", [])
        for message in reversed(messages):
            mid = message.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            if message.get("personId") == bot_id:
                continue
            sender = (message.get("personEmail") or "").lower()
            text = message.get("text") or message.get("markdown") or ""
            if sender != ALLOWED_EMAIL:
                log(f"ignored unauthorized message from {sender or 'unknown'}: {text!r}")
                if unauthorized_alert_allowed(sender, state):
                    try:
                        send_direct_message(token, ALLOWED_EMAIL, unauthorized_alert_text(sender, text, room_id))
                    except Exception as exc:
                        log(f"failed sending unauthorized alert: {exc}")
                continue
            log(f"authorized command from {sender}: {text!r}")
            try:
                reply = handle(text, state)
            except Exception as exc:
                log(f"command failed: {exc}")
                reply = f"Command failed: `{exc}`"
            send_message(token, room_id, reply)
            handled += 1
    state["seen"] = list(seen)
    save_state(state)
    return handled


def main() -> int:
    once = "--once" in sys.argv
    prime = "--prime" in sys.argv
    token = load_token()
    bot = api_request(token, "GET", "/people/me")
    bot_id = bot.get("id", "")
    if not bot_id:
        raise RuntimeError("Could not identify WebEx bot account")
    state = load_state()
    log(f"listener started; allowed={ALLOWED_EMAIL}; bot={bot.get('emails', ['unknown'])[0]}")

    if prime:
        rooms = api_request(token, "GET", "/rooms", query={"type": "direct", "max": 100}).get("items", [])
        seen = set(state.get("seen", []))
        for room in rooms:
            room_id = room.get("id")
            if not room_id:
                continue
            messages = api_request(token, "GET", "/messages", query={"roomId": room_id, "max": 50}).get("items", [])
            seen.update(message.get("id") for message in messages if message.get("id"))
        state["seen"] = list(seen)
        save_state(state)
        log(f"primed state with {len(seen)} seen messages")
        return 0

    stop = False

    def on_signal(signum, frame):  # noqa: ANN001
        nonlocal stop
        stop = True
        log(f"received signal {signum}; stopping")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    while not stop:
        try:
            poll_once(token, bot_id, state)
        except Exception as exc:
            log(f"poll failed: {exc}")
        if once:
            break
        time.sleep(POLL_SECONDS)
    log("listener stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
