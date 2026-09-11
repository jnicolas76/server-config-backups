"""Wizard API handlers.

Each stage POSTs its fields here. The handler validates them through the same
schema the unattended path uses, merges the accepted values into the wizard
state and reports any problems back per-field so the browser can highlight them.

There is no second validation implementation: if a value is acceptable here it
is acceptable in ``cinemediavault.yaml``, and vice versa.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import socket
import time
from pathlib import Path
from typing import Any

from installer.config.schema import (
    PathSafetyError, apply_defaults, check_media_path, check_password, errors,
    get, put, validate, warnings as schema_warnings,
)
from installer.config.secrets import hash_password
from installer.core.redact import MASK, REDACTOR

#: Which schema keys each stage owns. Anything not listed is ignored, so a
#: crafted request cannot set a key the stage was never supposed to touch.
STAGE_FIELDS: dict[str, tuple[str, ...]] = {
    "welcome": ("deployment.mode", "deployment.timezone",
                "deployment.accept_licenses", "meta.instance_name"),
    "admin": ("admin.username", "admin.full_name", "admin.email", "admin.password"),
    "network": ("network.hostname", "network.listen_address", "network.http_port",
                "network.enable_http", "network.https_port", "network.tls_mode",
                "network.tls_cert_path", "network.tls_key_path",
                "network.trusted_networks", "network.configure_firewall"),
    "media": ("media.movies_root", "media.tv_root", "media.music_root",
              "media.books_root", "media.comics_root", "media.comic_library_root",
              "media.games_root", "media.recordings_root", "media.create_missing"),
    "mounts": ("media.mounts",),
    "metadata": ("metadata.tmdb_api_key", "metadata.tmdb_read_access_token",
                 "metadata.google_books_api_key", "metadata.refresh_cron",
                 "metadata.fetch_on_install"),
    "livetv": ("livetv.enabled", "livetv.discovery", "livetv.device_address",
               "livetv.device_id", "livetv.tuner_count", "livetv.reserved_tuners",
               "livetv.channels"),
    "epg": ("epg.mode", "epg.collector_bind_address", "epg.collector_port",
            "epg.feed_url", "epg.horizon_days", "epg.refresh_time",
            "epg.request_delay_ms", "epg.max_connections",
            "epg.request_timeout_ms", "epg.memory_limit_mb", "epg.cpu_limit",
            "epg.channel_map"),
    "dvr": ("dvr.enabled", "dvr.padding_start_seconds", "dvr.padding_end_seconds",
            "dvr.retention_days", "dvr.conflict_policy", "dvr.min_free_gb",
            "dvr.poll_seconds"),
    "subtitles": ("subtitles.use_existing", "subtitles.languages",
                  "subtitles.subdl_enabled", "subtitles.subdl_api_key",
                  "subtitles.subdl_daily_search_limit",
                  "subtitles.whisper_enabled", "subtitles.whisper_model",
                  "subtitles.whisper_device", "subtitles.whisper_workers",
                  "subtitles.whisper_cpu_quota_percent",
                  "subtitles.whisper_memory_limit_mb",
                  "subtitles.whisper_start_enabled"),
    "integrations": ("integrations.webex_webhook_url",
                     "integrations.homepage_enabled",
                     "integrations.transcode_control_url",
                     "integrations.transcode_control_db",
                     "integrations.notify_on_install"),
    "modules": ("modules.movies", "modules.tv", "modules.music",
                "modules.bookvault", "modules.comics", "modules.video_wall",
                "modules.downloads", "modules.games", "modules.creators",
                "modules.bookvault_port", "modules.comics_port",
                "modules.game_port_base", "transcode.hls_enabled",
                "transcode.hls_encoder", "transcode.hls_video_bitrate",
                "transcode.default_playback_mode",
                "transcode.library_queue_enabled",
                "resources.app_memory_limit_mb",
                "resources.app_cpu_quota_percent",
                "promo_channel.enabled",
                "promo_channel.model_path", "promo_channel.voices_path",
                "promo_channel.schedule_calendar", "promo_channel.minutes",
                "promo_channel.cpu_quota_percent", "promo_channel.memory_limit_mb"),
    "review": (),
    "install": (),
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400,
                 fields: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.fields = fields or {}


# --------------------------------------------------------------------------
# Stage submission
# --------------------------------------------------------------------------

def submit_stage(state, stage: str, payload: dict) -> dict:
    """Validate and store one stage's fields."""
    if stage not in STAGE_FIELDS:
        raise ApiError(f"unknown stage: {stage}", 404)

    allowed = STAGE_FIELDS[stage]
    candidate = json.loads(json.dumps(state.config))    # deep copy
    field_errors: dict[str, str] = {}

    for key in allowed:
        if key not in payload:
            continue
        value = payload[key]

        # A masked secret coming back means "unchanged", not "set to the mask".
        if value == MASK:
            continue

        try:
            put(candidate, key, _coerce_field(key, value))
        except ValueError as exc:
            field_errors[key] = str(exc)

    if field_errors:
        raise ApiError("some values could not be read", 400, field_errors)

    # The password is special: validate it, hash it, and never store the
    # plaintext - not in the state file, not in memory beyond this call.
    if stage == "admin":
        password = get(candidate, "admin.password") or ""
        if password:
            problems = check_password(password)
            if problems:
                raise ApiError("the password does not meet the policy", 400,
                               {"admin.password": "; ".join(p.message for p in problems)})
            REDACTOR.register(password)
            put(candidate, "admin.password_hash", hash_password(password))
            put(candidate, "admin.password", "")
            state.admin_hashed = True
        elif not get(candidate, "admin.password_hash"):
            raise ApiError("a password is required", 400,
                           {"admin.password": "is required"})

    # Full-document validation, restricted to the fields this stage owns so an
    # incomplete later stage does not block an earlier one.
    merged = apply_defaults(candidate)
    issues = validate(merged, for_install=False)
    stage_errors = {
        issue.key: issue.message for issue in errors(issues)
        if issue.key in allowed or _belongs_to_stage(issue.key, allowed)
    }
    if stage_errors:
        raise ApiError("some values are not valid", 400, stage_errors)

    state.config = candidate
    state.complete(stage)

    # Errors that belong to another stage are not blocking here - the operator
    # may not have reached that stage yet, or may need to go back to it. They
    # are surfaced as warnings so the problem appears next to the choice that
    # caused it, rather than as a surprise on the review screen.
    # Only report against stages already visited: telling someone on the
    # welcome screen that they have not set a media path yet is noise, not help.
    elsewhere = []
    for issue in errors(issues):
        if issue.key in stage_errors:
            continue
        owner = _owning_stage(issue.key)
        if owner not in state.completed:
            continue
        elsewhere.append({
            "key": issue.key,
            "message": f"{issue.message} (fix this on the '{owner}' step "
                       f"before installing)",
        })

    return {
        "ok": True,
        "next": state.current,
        "warnings": [
            {"key": i.key, "message": i.message}
            for i in schema_warnings(issues)
            if i.key in allowed or _belongs_to_stage(i.key, allowed)
        ] + elsewhere,
        "progress": state.progress(),
    }


def _owning_stage(key: str) -> str:
    """Which wizard stage collects *key*. Used to point the operator at it."""
    for stage, fields in STAGE_FIELDS.items():
        if key in fields:
            return stage
    prefix = key.split(".")[0]
    for stage, fields in STAGE_FIELDS.items():
        if any(f.startswith(prefix + ".") for f in fields):
            return stage
    return "review"


def _belongs_to_stage(key: str, allowed: tuple[str, ...]) -> bool:
    """Cross-field errors are reported against whichever key the stage owns."""
    return any(key.startswith(a.split(".")[0] + ".") for a in allowed)


def _coerce_field(key: str, value: Any) -> Any:
    from installer.config.schema import SCHEMA_BY_KEY

    field = SCHEMA_BY_KEY.get(key)
    if field is None:
        raise ValueError("unknown setting")

    if field.type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "yes", "on", "1"):
                return True
            if low in ("false", "no", "off", "0", ""):
                return False
        raise ValueError("expected true or false")

    if field.type in ("int", "float"):
        if isinstance(value, bool):
            raise ValueError("expected a number")
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return field.default
            try:
                return int(text) if field.type == "int" else float(text)
            except ValueError as exc:
                raise ValueError("expected a number") from exc
        if isinstance(value, (int, float)):
            return int(value) if field.type == "int" else float(value)
        raise ValueError("expected a number")

    if field.type == "list":
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [p.strip() for p in value.split(",") if p.strip()]
        raise ValueError("expected a list")

    if field.type == "dict":
        if isinstance(value, dict):
            return value
        raise ValueError("expected an object")

    text = "" if value is None else str(value)
    return text.strip()


# --------------------------------------------------------------------------
# Live validation helpers used by the browser as the operator types
# --------------------------------------------------------------------------

def check_path(payload: dict) -> dict:
    """Inspect one media path: safety, existence, writability, free space."""
    raw = str(payload.get("path") or "").strip()
    purpose = str(payload.get("purpose") or "library")
    needs_write = bool(payload.get("needs_write"))

    if not raw:
        return {"ok": True, "status": "empty",
                "message": "Leave blank to skip this library."}

    try:
        normalised = check_media_path(raw, label=purpose)
    except PathSafetyError as exc:
        return {"ok": False, "status": "unsafe", "message": str(exc)}

    path = Path(normalised)
    result: dict[str, Any] = {"ok": True, "path": normalised}

    if not path.exists():
        parent = path.parent
        if parent.exists() and os.access(parent, os.W_OK):
            result.update({
                "status": "missing",
                "message": f"{normalised} does not exist yet. Turn on 'create "
                           f"missing folders' and the installer will create it.",
            })
        else:
            result.update({
                "ok": False, "status": "missing_parent",
                "message": f"{normalised} does not exist, and neither does "
                           f"{parent}. Create or mount the parent folder first.",
            })
        return result

    if not path.is_dir():
        return {"ok": False, "status": "not_a_directory",
                "message": f"{normalised} exists but is not a folder."}

    try:
        entries = sum(1 for _ in os.scandir(path))
    except OSError as exc:
        return {"ok": False, "status": "unreadable",
                "message": f"Cannot read {normalised}: {exc.strerror or exc}"}

    result["entries"] = entries
    readable = os.access(path, os.R_OK | os.X_OK)
    writable = _probe_write(path)
    result["readable"] = readable
    result["writable"] = writable

    if not readable:
        return {"ok": False, "status": "unreadable",
                "message": f"{normalised} cannot be read by this server."}
    if needs_write and not writable:
        return {"ok": False, "status": "read_only",
                "message": f"{normalised} is read-only, but {purpose} needs to "
                           f"write here."}

    try:
        stat = os.statvfs(path)
        free = stat.f_bavail * stat.f_frsize
        result["free_bytes"] = free
        result["free_human"] = _human(free)
    except OSError:
        pass

    if entries == 0:
        result["status"] = "empty"
        result["message"] = (
            f"{normalised} is empty. If your media should already be here, the "
            f"share is probably not mounted - CineMediaVault would record an "
            f"empty library.")
        result["warning"] = True
    else:
        result["status"] = "ok"
        result["message"] = (
            f"{normalised}: {entries} item(s), "
            f"{'read/write' if writable else 'read-only'}"
            + (f", {result.get('free_human')} free" if result.get("free_human") else ""))
    return result


def _probe_write(path: Path) -> bool:
    import tempfile
    try:
        fd, name = tempfile.mkstemp(dir=str(path), prefix=".cmv-probe-")
        os.close(fd)
        os.unlink(name)
        return True
    except OSError:
        return False


def _human(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} EiB"


def check_port(payload: dict) -> dict:
    """Is this port free, and if not, what holds it?"""
    try:
        port = int(payload.get("port") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "message": "Enter a number between 1 and 65535."}
    if not 1 <= port <= 65535:
        return {"ok": False, "message": "Enter a number between 1 and 65535."}
    if port < 1024:
        return {"ok": False,
                "message": f"Port {port} is privileged. CineMediaVault runs "
                           f"unprivileged; choose a port above 1023."}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        return {"ok": True, "message": f"Port {port} is available."}
    except OSError:
        return {"ok": False,
                "message": f"Port {port} is already in use on this machine."}
    finally:
        sock.close()


def discover_tuners(payload: dict) -> dict:
    """Find HDHomeRun devices, or probe one address."""
    from installer.discovery import hdhomerun

    address = str(payload.get("address") or "").strip()
    if address:
        device = hdhomerun.probe_device(address)
        if not device:
            return {"ok": False,
                    "message": f"No HDHomeRun answered at {address}. Check the "
                               f"address and that the tuner is powered on."}
        return {"ok": True, "devices": [hdhomerun.summarise(device)]}

    devices = hdhomerun.discover()
    if not devices:
        return {"ok": True, "devices": [],
                "message": "No tuner was found automatically. Automatic discovery "
                           "needs either internet access or a flat network path "
                           "to the tuner; entering the address manually always "
                           "works."}
    return {"ok": True, "devices": [hdhomerun.summarise(d) for d in devices]}


def fetch_lineup(payload: dict) -> dict:
    """Read a tuner's channel lineup. Never starts a scan."""
    from installer.discovery import hdhomerun

    address = str(payload.get("address") or "").strip()
    if not address:
        return {"ok": False, "message": "No tuner address."}
    channels = hdhomerun.lineup(address)
    if not channels:
        return {"ok": True, "channels": [],
                "message": "The tuner reported no channels. Run a channel scan "
                           "from the tuner's own web page first - CineMediaVault "
                           "will not start one, because a scan takes the tuner "
                           "offline."}
    return {"ok": True, "channels": channels, "count": len(channels)}


def list_mounts(payload: dict) -> dict:
    """Current mounts, so the operator can pick rather than type."""
    from installer.core.runner import Runner
    from installer.discovery.mounts import list_mounts as read_mounts

    mounts = read_mounts(Runner(dry_run=True))
    interesting = [
        m for m in mounts
        if m["fstype"] in ("nfs", "nfs4", "cifs", "smb3", "ext4", "xfs", "btrfs", "zfs")
        and not m["target"].startswith(("/proc", "/sys", "/dev", "/run", "/snap"))
    ]
    for mount in interesting:
        try:
            stat = os.statvfs(mount["target"])
            mount["free_human"] = _human(stat.f_bavail * stat.f_frsize)
        except OSError:
            mount["free_human"] = ""
    return {"ok": True, "mounts": interesting}


def timezones(payload: dict) -> dict:
    """The timezones this machine knows about."""
    root = Path("/usr/share/zoneinfo")
    names: list[str] = []
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if relative.startswith(("posix/", "right/", "Etc/")) or "." in relative:
                continue
            if re.match(r"^[A-Z][A-Za-z_+\-]*(/[A-Za-z0-9_+\-]+){1,2}$", relative):
                names.append(relative)
    if not names:
        names = ["UTC", "America/New_York", "America/Denver", "America/Los_Angeles",
                 "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney"]
    guess = ""
    try:
        guess = os.readlink("/etc/localtime").split("zoneinfo/")[-1]
    except OSError:
        pass
    return {"ok": True, "timezones": sorted(set(names)), "detected": guess}


def suggest_defaults(payload: dict) -> dict:
    """Sensible starting values derived from this machine."""
    from installer.core.context import Context
    from installer.core.logging import InstallLogger
    from installer.preflight.gpu import recommended_encoder
    from installer.preflight.network import primary_lan_address

    logger = InstallLogger(console=False)
    ctx = Context({}, logger=logger, dry_run=True)
    facts = ctx.detect_facts()

    address = primary_lan_address()
    network = ""
    try:
        network = str(ipaddress.ip_network(f"{address}/24", strict=False))
    except ValueError:
        network = "192.168.0.0/16"

    memory = facts.memory_total_mb or 4096
    return {
        "ok": True,
        "hostname": socket.gethostname(),
        "lan_address": address,
        "trusted_network": network,
        "cpu_count": facts.cpu_count,
        "memory_mb": memory,
        "gpu": list(facts.gpu_vendors),
        "encoder": recommended_encoder(ctx),
        "has_docker": facts.has_docker,
        # Leave the machine at least a quarter of its memory, and never claim
        # more than 8 GB for a media catalogue.
        "suggested_app_memory_mb": max(1024, min(8192, int(memory * 0.5))),
        "can_run_whisper": memory >= 8192,
        "can_run_epg": facts.has_docker or True,
        "facts": facts.summary(),
    }


def redacted_config(state) -> dict:
    """The wizard document with every secret masked, for redisplay."""
    from installer.config.schema import SECRET_KEYS

    data = json.loads(json.dumps(state.config))
    for key in SECRET_KEYS:
        if get(data, key):
            put(data, key, MASK)
    return data


def review(state) -> dict:
    """Everything the review stage shows: plan, preflight and warnings."""
    from installer.core.context import Context
    from installer.core.logging import InstallLogger
    from installer.engine import Engine
    from installer import preflight

    config = apply_defaults(state.config)
    logger = InstallLogger(console=False)
    ctx = Context(config, logger=logger, dry_run=True)
    ctx.detect_facts()

    issues = validate(config)
    checks = preflight.run_all(ctx)

    return {
        "ok": not errors(issues) and all(c.ok for c in checks),
        "errors": [{"key": i.key, "message": i.message} for i in errors(issues)],
        "warnings": [{"key": i.key, "message": i.message}
                     for i in schema_warnings(issues)],
        "preflight": preflight.summarise(checks),
        "plan": Engine(ctx).plan(),
        "summary": _summary(ctx),
        "config": redacted_config(state),
    }


def _summary(ctx) -> dict:
    """The plain-language recap shown before the operator commits."""
    libraries = []
    for label, key in (("Movies", "media.movies_root"), ("TV shows", "media.tv_root"),
                       ("Music", "media.music_root"), ("Books", "media.books_root"),
                       ("Comics", "media.comics_root"),
                       ("Games", "media.games_root"),
                       ("Recordings", "media.recordings_root")):
        value = (ctx.get(key) or "").strip()
        if value:
            libraries.append({"label": label, "path": value})

    features = []
    if ctx.get("livetv.enabled"):
        features.append(f"Live TV via HDHomeRun at "
                        f"{ctx.get('livetv.device_address') or 'auto-discovered'}")
    if ctx.get("dvr.enabled"):
        features.append(
            f"DVR recording to {ctx.get('media.recordings_root')} with "
            f"{ctx.get('dvr.padding_start_seconds')}s/"
            f"{ctx.get('dvr.padding_end_seconds')}s padding")
    if ctx.get("epg.mode") == "extended":
        features.append(f"Extended guide, up to {ctx.get('epg.horizon_days')} days")
    if ctx.get("subtitles.whisper_enabled"):
        features.append(f"Whisper subtitles ({ctx.get('subtitles.whisper_model')} "
                        f"on {ctx.get('subtitles.whisper_device')})")
    if ctx.get("subtitles.subdl_enabled"):
        features.append("SubDL subtitle lookup")
    if ctx.selected_games():
        features.append(f"Game modules: {', '.join(ctx.selected_games())}")
    if ctx.selected_creators():
        features.append(f"Creator tools: {', '.join(ctx.selected_creators())}")
    if ctx.get("transcode.library_queue_enabled"):
        features.append("Bulk transcode queue (installed stopped)")

    return {
        "url": ctx.primary_url(),
        "admin": ctx.get("admin.username"),
        "instance": ctx.get("meta.instance_name"),
        "timezone": ctx.get("deployment.timezone"),
        "libraries": libraries,
        "features": features,
        "memory_limit_mb": ctx.get("resources.app_memory_limit_mb"),
        "tls": ctx.get("network.tls_mode"),
    }
