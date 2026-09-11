"""Operating system, CPU, memory and privilege checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..core.fsops import free_bytes, human_bytes
from ..version import MIN_PYTHON, SUPPORTED_ARCH, SUPPORTED_UBUNTU
from . import CheckResult, fail, ok, warn

#: Minimums. Below the "required" figure the installer refuses; between
#: required and recommended it warns and the wizard shows a sizing note.
MIN_CPU = 2
RECOMMENDED_CPU = 4
MIN_MEMORY_MB = 2048
RECOMMENDED_MEMORY_MB = 4096
MIN_ROOT_FREE_GB = 8
RECOMMENDED_ROOT_FREE_GB = 20

#: Extra headroom each heavyweight option needs, in MB of RAM.
FEATURE_MEMORY_MB = {
    "epg.extended": 3072,
    "subtitles.whisper": 6144,
    "transcode.hls": 1024,
}


def checks(ctx) -> list[CheckResult]:
    facts = ctx.detect_facts()
    results: list[CheckResult] = []

    # -- privilege ---------------------------------------------------------
    if os.geteuid() != 0 and not ctx.dry_run:
        results.append(fail(
            "sys.root", "Administrator privileges",
            "The installer must run as root to create users, install packages "
            "and write systemd units.",
            "Re-run with sudo: sudo ./install.sh"))
    else:
        results.append(ok("sys.root", "Administrator privileges",
                          "running as root" if os.geteuid() == 0
                          else "dry-run does not require root"))

    # -- python ------------------------------------------------------------
    if sys.version_info[:2] < MIN_PYTHON:
        results.append(fail(
            "sys.python", "Python version",
            f"Python {'.'.join(map(str, MIN_PYTHON))}+ is required, found "
            f"{facts.python_version}.",
            "Install a newer Python or use a supported Ubuntu release."))
    else:
        results.append(ok("sys.python", "Python version", facts.python_version))

    # -- operating system --------------------------------------------------
    if facts.os_id == "ubuntu" and facts.os_version in SUPPORTED_UBUNTU:
        results.append(ok("sys.os", "Operating system",
                          f"Ubuntu {facts.os_version} ({facts.os_codename})"))
    elif facts.os_id in ("ubuntu", "debian"):
        results.append(warn(
            "sys.os", "Operating system",
            f"{facts.os_id} {facts.os_version} is not one of the tested releases "
            f"({', '.join(SUPPORTED_UBUNTU)}).",
            "Installation usually works, but package names and systemd behaviour "
            "are only verified on the tested releases."))
    else:
        results.append(fail(
            "sys.os", "Operating system",
            f"Unsupported distribution: {facts.os_id or 'unknown'} {facts.os_version}.",
            f"Use Ubuntu {' or '.join(SUPPORTED_UBUNTU)}."))

    # -- architecture ------------------------------------------------------
    if facts.arch in SUPPORTED_ARCH:
        results.append(ok("sys.arch", "CPU architecture", facts.arch))
    else:
        results.append(warn(
            "sys.arch", "CPU architecture",
            f"{facts.arch} is outside the tested set ({', '.join(SUPPORTED_ARCH)}).",
            "Hardware transcoding and the prebuilt emulator runtimes may be "
            "unavailable on this architecture."))

    # -- init system -------------------------------------------------------
    if facts.has_systemd:
        results.append(ok("sys.systemd", "Init system", "systemd is available"))
    else:
        results.append(fail(
            "sys.systemd", "Init system",
            "systemd is not running. CineMediaVault services, timers and the "
            "self-healing health check all depend on it.",
            "Install on a normal Ubuntu VM rather than a minimal container, or "
            "enable systemd in this environment."))

    if facts.is_container:
        results.append(warn(
            "sys.container", "Container environment",
            "Running inside a container. Device access for HDHomeRun discovery "
            "and hardware transcoding is often restricted.",
            "A full VM is recommended for Live TV and DVR."))
    if facts.is_wsl:
        results.append(warn(
            "sys.wsl", "WSL environment",
            "WSL does not provide a normal systemd/network stack for a server "
            "install.",
            "Install on a real Ubuntu VM or host."))

    # -- CPU ----------------------------------------------------------------
    if facts.cpu_count < MIN_CPU:
        results.append(fail(
            "sys.cpu", "CPU cores",
            f"{facts.cpu_count} core(s) available, {MIN_CPU} required.",
            f"Give the VM at least {RECOMMENDED_CPU} vCPUs."))
    elif facts.cpu_count < RECOMMENDED_CPU:
        results.append(warn(
            "sys.cpu", "CPU cores",
            f"{facts.cpu_count} cores. {RECOMMENDED_CPU} are recommended.",
            "Library scans, thumbnails and HLS transcoding will be slow."))
    else:
        results.append(ok("sys.cpu", "CPU cores", f"{facts.cpu_count} cores"))

    # -- memory -------------------------------------------------------------
    required_mb = MIN_MEMORY_MB
    wanted_mb = RECOMMENDED_MEMORY_MB
    extras: list[str] = []
    if ctx.get("epg.mode") == "extended":
        wanted_mb += FEATURE_MEMORY_MB["epg.extended"]
        extras.append("extended EPG collector")
    if ctx.get("subtitles.whisper_enabled"):
        wanted_mb += FEATURE_MEMORY_MB["subtitles.whisper"]
        extras.append("Whisper subtitles")
    if ctx.get("transcode.hls_enabled"):
        wanted_mb += FEATURE_MEMORY_MB["transcode.hls"]
        extras.append("HLS transcoding")

    if facts.memory_total_mb == 0:
        results.append(warn("sys.memory", "Memory", "could not read /proc/meminfo"))
    elif facts.memory_total_mb < required_mb:
        results.append(fail(
            "sys.memory", "Memory",
            f"{facts.memory_total_mb} MB available, {required_mb} MB required.",
            f"Increase the VM to at least {wanted_mb} MB."))
    elif facts.memory_total_mb < wanted_mb:
        detail = f"{facts.memory_total_mb} MB available; {wanted_mb} MB recommended"
        if extras:
            detail += f" for {', '.join(extras)}"
        results.append(warn(
            "sys.memory", "Memory", detail + ".",
            "Either add memory or turn off the heavier optional components."))
    else:
        results.append(ok("sys.memory", "Memory",
                          f"{facts.memory_total_mb} MB", total_mb=facts.memory_total_mb))

    # -- root filesystem ----------------------------------------------------
    try:
        target = ctx.layout.install_root
        probe = target if target.exists() else Path("/")
        free_gb = free_bytes(probe) / (1024 ** 3)
        if free_gb < MIN_ROOT_FREE_GB:
            results.append(fail(
                "sys.disk", "System disk space",
                f"{human_bytes(free_bytes(probe))} free on {probe}; "
                f"{MIN_ROOT_FREE_GB} GB required.",
                "Free space or grow the system volume."))
        elif free_gb < RECOMMENDED_ROOT_FREE_GB:
            results.append(warn(
                "sys.disk", "System disk space",
                f"{human_bytes(free_bytes(probe))} free; "
                f"{RECOMMENDED_ROOT_FREE_GB} GB recommended.",
                "Metadata, posters, thumbnails and HLS caches grow with the library."))
        else:
            results.append(ok("sys.disk", "System disk space",
                              f"{human_bytes(free_bytes(probe))} free"))
    except OSError as exc:
        results.append(warn("sys.disk", "System disk space", f"could not check: {exc}"))

    # -- timezone -----------------------------------------------------------
    tz = ctx.get("deployment.timezone") or "UTC"
    zone = Path("/usr/share/zoneinfo") / tz
    if tz == "UTC" or zone.exists():
        results.append(ok("sys.timezone", "Timezone", tz))
    else:
        results.append(fail(
            "sys.timezone", "Timezone",
            f"{tz} is not a timezone this system knows.",
            "Choose a valid IANA name, for example America/Denver. "
            "Run `timedatectl list-timezones` to see the list."))

    return results
