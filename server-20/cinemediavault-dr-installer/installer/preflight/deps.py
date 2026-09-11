"""Package and external-tool availability.

The installer only installs what the selected configuration actually needs, so
this module is also the single place that answers "what will `apt-get install`
be asked to do", which the wizard shows on the preview step.
"""

from __future__ import annotations

from . import CheckResult, fail, ok, warn

#: Always needed.
BASE_PACKAGES = ("python3", "python3-venv", "ca-certificates", "curl",
                 "openssl", "sqlite3")

#: Needed for any playback beyond raw direct-play of already-compatible files.
MEDIA_PACKAGES = ("ffmpeg",)

#: Per-feature package sets.
FEATURE_PACKAGES = {
    "bookvault": ("python3-pil",),
    "comics": ("unrar-free", "p7zip-full"),
    "creators.comics": ("unrar-free", "p7zip-full", "ghostscript"),
    "creators.books": ("poppler-utils",),
    "mount.nfs": ("nfs-common",),
    "mount.cifs": ("cifs-utils",),
    "firewall": ("ufw",),
    "epg": ("docker.io", "docker-compose-v2"),
    "whisper": ("python3-venv", "python3-dev", "build-essential"),
    "promo_channel": ("python3-venv", "fonts-dejavu-core"),
}

#: Packages that only exist, or only make sense, on x86_64.
ARCH_RESTRICTED = {
    "docker-compose-v2": ("x86_64", "aarch64"),
}


def required_packages(ctx) -> list[str]:
    """Every apt package this configuration needs, deduplicated and ordered."""
    packages: list[str] = list(BASE_PACKAGES)
    packages.extend(MEDIA_PACKAGES)

    if ctx.get("modules.bookvault"):
        packages.extend(FEATURE_PACKAGES["bookvault"])
    if ctx.get("modules.comics"):
        packages.extend(FEATURE_PACKAGES["comics"])
    for creator in ctx.get("modules.creators") or []:
        packages.extend(FEATURE_PACKAGES.get(f"creators.{creator}", ()))
    for mount in ctx.get("media.mounts") or []:
        kind = str((mount or {}).get("type") or "")
        packages.extend(FEATURE_PACKAGES.get(f"mount.{kind}", ()))
    if ctx.get("network.configure_firewall"):
        packages.extend(FEATURE_PACKAGES["firewall"])
    if ctx.get("epg.mode") == "extended":
        packages.extend(FEATURE_PACKAGES["epg"])
    if ctx.get("subtitles.whisper_enabled"):
        packages.extend(FEATURE_PACKAGES["whisper"])
    if ctx.get("promo_channel.enabled"):
        packages.extend(FEATURE_PACKAGES["promo_channel"])

    arch = ctx.facts.arch or "x86_64"
    out: list[str] = []
    for package in packages:
        allowed = ARCH_RESTRICTED.get(package)
        if allowed and arch not in allowed:
            continue
        if package not in out:
            out.append(package)
    return out


def checks(ctx) -> list[CheckResult]:
    results: list[CheckResult] = []
    packages = required_packages(ctx)
    missing = [p for p in packages if not ctx.runner.apt_installed(p)]

    if not missing:
        results.append(ok("deps.packages", "System packages",
                          f"all {len(packages)} required packages already installed"))
    else:
        results.append(ok(
            "deps.packages", "System packages",
            f"{len(missing)} to install: {', '.join(missing)}",
            missing=missing, required=packages))

    if not ctx.runner.has("apt-get"):
        results.append(fail(
            "deps.apt", "Package manager",
            "apt-get is not available.",
            "This installer targets Debian/Ubuntu."))

    # -- ffmpeg -----------------------------------------------------------------
    if ctx.facts.ffmpeg_version:
        results.append(ok("deps.ffmpeg", "ffmpeg", ctx.facts.ffmpeg_version))
    else:
        results.append(ok("deps.ffmpeg", "ffmpeg",
                          "not installed; the installer will install it"))

    # -- docker, only when the EPG collector was selected ------------------------
    if ctx.get("epg.mode") == "extended":
        if ctx.facts.has_docker and ctx.facts.docker_compose_v2:
            results.append(ok("deps.docker", "Docker",
                              "docker with compose v2 is available"))
        elif ctx.facts.has_docker:
            results.append(warn(
                "deps.docker", "Docker",
                "docker is present but 'docker compose' v2 is not.",
                "The installer will install docker-compose-v2."))
        else:
            results.append(ok(
                "deps.docker", "Docker",
                "not installed; the installer will install docker.io and "
                "docker-compose-v2 for the EPG collector"))
        if ctx.offline:
            results.append(fail(
                "deps.docker.offline", "EPG collector",
                "The collector image is built from the iptv-org/epg source at "
                "install time, which needs internet access.",
                "Disable the extended EPG, or install with network access."))

    # -- unrar for comics -------------------------------------------------------
    if ctx.get("modules.comics") or "comics" in (ctx.get("modules.creators") or []):
        if not (ctx.runner.has("unrar") or ctx.runner.has("unrar-free")
                or ctx.runner.has("7z")):
            results.append(ok(
                "deps.unrar", "CBR support",
                "no RAR extractor yet; the installer will install unrar-free "
                "and p7zip-full"))
        else:
            results.append(ok("deps.unrar", "CBR support", "extractor available"))
        results.append(warn(
            "deps.unrar.note", "CBR (RAR) support",
            "unrar-free handles most comic archives but not every RAR5 variant.",
            "If some .cbr files fail to open, install the non-free 'unrar' "
            "package yourself; its licence prevents this installer from "
            "shipping or enabling it for you."))

    return results
