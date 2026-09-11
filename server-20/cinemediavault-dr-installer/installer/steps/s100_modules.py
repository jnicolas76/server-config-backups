"""Configure the Modules page and install the auxiliary module servers.

CineMediaVault's Modules page links out to small, independent servers - Book
Vault, the comics library and the emulator libraries. Each is a static or
near-static server; none of them is exposed by default beyond the trusted
networks, and none is given access to anything outside its own root.

Emulator *runtimes* (jsnes, EmulatorJS, js-dos) are not shipped in this package.
EmulatorJS and js-dos are GPL-licensed and are downloaded from their official
releases at install time; jsnes is MIT and would be redistributable, but it is
fetched the same way so there is one consistent, auditable path. ROMs are never
downloaded: the operator supplies their own.
"""

from __future__ import annotations

import json

from ..core.fsops import ensure_dir, write_file
from .base import Step, StepResult

#: id -> (display name, logo label, default port offset, rom subdirectory, extensions)
GAME_MODULES = {
    "nes":        ("NES", "NES", 0, "nes", (".nes", ".zip", ".7z")),
    "sega":       ("SEGA", "SEGA", 1, "sega", (".gen", ".md", ".smd", ".bin", ".zip", ".7z")),
    "dos":        ("DOS", "DOS", 2, "dos", (".jsdos", ".zip", ".7z")),
    "mame":       ("MAME", "MAME", 3, "mame", (".zip", ".7z")),
    "gameboy":    ("GameBoy", "GB", 4, "gameboy", (".gb", ".gbc", ".zip", ".7z")),
    "gba":        ("GBA", "GBA", 5, "gba", (".gba", ".zip", ".7z")),
    "n64":        ("N64", "N64", 6, "n64", (".n64", ".z64", ".v64", ".zip", ".7z")),
    "ps1":        ("PS1", "PS1", 7, "ps1", (".bin", ".cue", ".iso", ".chd", ".pbp", ".zip")),
    "c64":        ("C64", "C64", 8, "c64", (".d64", ".t64", ".prg", ".zip", ".7z")),
    "atari2600":  ("Atari 2600", "ATARI", 9, "atari2600", (".a26", ".bin", ".zip")),
    "atari5200":  ("Atari 5200", "ATARI", 10, "atari5200", (".a52", ".bin", ".zip")),
    "atari7800":  ("Atari 7800", "ATARI", 11, "atari7800", (".a78", ".bin", ".zip")),
    "arcade":     ("Arcade", "MAME", 12, "arcade", (".zip", ".7z")),
}

#: Emulator runtimes, their licence, and where the official build comes from.
#: Nothing here is bundled; see docs/THIRD-PARTY-NOTICES.md.
RUNTIME_SOURCES = {
    "jsnes": {
        "licence": "MIT",
        "url": "https://cdn.jsdelivr.net/npm/jsnes@1.2.1/dist/jsnes.min.js",
        "target": "nes/runtime/jsnes.min.js",
        "used_by": ("nes",),
    },
    "emulatorjs": {
        "licence": "GPL-3.0",
        "url": "https://cdn.emulatorjs.org/stable/data/loader.js",
        "target": "shared/emulatorjs/loader.js",
        "used_by": ("sega", "mame", "gameboy", "gba", "n64", "ps1", "c64",
                    "atari2600", "atari5200", "atari7800", "arcade"),
        "note": ("EmulatorJS is GPL-3.0. It is loaded from the official CDN at "
                 "install time rather than redistributed inside this package."),
    },
    "js-dos": {
        "licence": "GPL-2.0",
        "url": "https://js-dos.com/6.22/current/js-dos.js",
        "target": "dos/runtime/js-dos.js",
        "used_by": ("dos",),
        "note": ("js-dos is GPL-2.0. It is downloaded from the upstream project "
                 "at install time rather than redistributed here."),
    },
}


def module_definitions(ctx) -> list[dict]:
    """Build the modules.json the application reads."""
    layout = ctx.layout
    hostname = ctx.get("network.hostname") or "localhost"
    definitions: list[dict] = []

    if ctx.get("modules.comics"):
        definitions.append({
            "id": "comics", "name": "Comics", "logo": "COMICS", "enabled": True,
            "protocol": "http", "port": int(ctx.get("modules.comics_port")),
            "url": "",
            "start_script": f"systemctl start cinemediavault-module@comics.service",
            "pid_file": "", "stop_pattern": "",
            "systemd_unit": "cinemediavault-module@comics.service",
        })

    if ctx.get("modules.bookvault"):
        definitions.append({
            "id": "bookvault", "name": "Book Vault", "logo": "BOOKS", "enabled": True,
            "protocol": "http", "port": int(ctx.get("modules.bookvault_port")),
            "url": "",
            "start_script": "systemctl start cinemediavault-bookvault.service",
            "pid_file": "", "stop_pattern": "",
            "systemd_unit": "cinemediavault-bookvault.service",
        })

    base = int(ctx.get("modules.game_port_base") or 8090)
    for index, game in enumerate(ctx.selected_games()):
        name, logo, _offset, _sub, _ext = GAME_MODULES[game]
        definitions.append({
            "id": game, "name": name, "logo": logo, "enabled": True,
            "protocol": "http", "port": base + index, "url": "",
            "start_script": f"systemctl start cinemediavault-module@{game}.service",
            "pid_file": "", "stop_pattern": "",
            "systemd_unit": f"cinemediavault-module@{game}.service",
        })

    # The Whisper card is a control surface, not a server, so it has no port.
    if ctx.get("subtitles.whisper_enabled"):
        definitions.append({
            "id": "whisper", "name": "Whisper Subtitles", "logo": "CC",
            "enabled": True, "protocol": "http", "port": 0, "url": "",
            "start_script": "systemctl start cinemediavault-subtitles.service",
            "pid_file": "", "stop_pattern": "",
            "systemd_unit": "cinemediavault-subtitles.service",
            "remote_control": "whisper",
        })

    if ctx.get("integrations.transcode_control_url"):
        definitions.append({
            "id": "transcode", "name": "Transcode Control", "logo": "TC",
            "enabled": True, "protocol": "http", "port": 0,
            "url": ctx.get("integrations.transcode_control_url"),
            "start_script": "", "pid_file": "", "stop_pattern": "",
        })

    return definitions


class WriteModuleConfig(Step):
    id = "modules"
    title = "Modules page"
    description = "Register the enabled modules so they appear on the Modules page."
    depends_on = ("modules.comics", "modules.bookvault", "modules.games",
                  "modules.comics_port", "modules.bookvault_port",
                  "modules.game_port_base", "subtitles.whisper_enabled",
                  "integrations.transcode_control_url", "network.hostname")
    requires = ("config",)

    def preview(self, ctx) -> str:
        definitions = module_definitions(ctx)
        if not definitions:
            return "No auxiliary modules selected."
        return "Register: " + ", ".join(d["name"] for d in definitions)

    def run(self, ctx) -> StepResult:
        definitions = module_definitions(ctx)
        target = ctx.layout.state_root / "modules.json"

        # A repair run must not discard a logo or a custom URL the operator set
        # in the interface, so existing per-module overrides are preserved.
        existing: dict[str, dict] = {}
        if target.is_file():
            try:
                for entry in json.loads(target.read_text(encoding="utf-8")) or []:
                    if isinstance(entry, dict) and entry.get("id"):
                        existing[entry["id"]] = entry
            except (OSError, ValueError):
                ctx.logger.warning("existing modules.json is unreadable; regenerating")

        merged: list[dict] = []
        for definition in definitions:
            previous = existing.get(definition["id"], {})
            for preserved in ("url", "logo", "enabled", "name"):
                if preserved in previous and previous[preserved] not in (None, ""):
                    definition[preserved] = previous[preserved]
            merged.append(definition)

        changed = write_file(
            target, json.dumps(merged, indent=2) + "\n",
            mode=0o640, user=ctx.service_user, group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        return StepResult(changed=changed,
                          summary=f"{len(merged)} module(s) registered",
                          data={"modules": [m["id"] for m in merged]})


class InstallGameModules(Step):
    id = "modules.games"
    title = "Game libraries"
    description = ("Create each selected emulator library and download its "
                   "official runtime. ROMs are never downloaded.")
    depends_on = ("modules.games", "media.games_root", "deployment.accept_licenses")
    requires = ("modules", "payload")

    def applies(self, ctx) -> bool:
        return bool(ctx.selected_games())

    def preview(self, ctx) -> str:
        games = ctx.selected_games()
        runtimes = sorted({
            name for name, meta in RUNTIME_SOURCES.items()
            if any(g in meta["used_by"] for g in games)})
        return (f"Create {len(games)} game library/libraries "
                f"({', '.join(games)}) and download runtimes: "
                f"{', '.join(runtimes)}.")

    def run(self, ctx) -> StepResult:
        games = ctx.selected_games()
        roms_root = ctx.get("media.games_root")
        created = 0
        warnings: list[str] = []

        for game in games:
            _name, _logo, _offset, subdir, _ext = GAME_MODULES[game]
            library = ctx.layout.modules_dir / game
            ensure_dir(library, mode=0o755, user=ctx.service_user,
                       group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)
            ensure_dir(library / "runtime", mode=0o755, user=ctx.service_user,
                       group=ctx.service_group, dry_run=ctx.dry_run, logger=ctx.logger)
            if roms_root:
                ensure_dir(f"{roms_root}/{subdir}", mode=0o755, user=ctx.service_user,
                           group=ctx.service_group, dry_run=ctx.dry_run,
                           logger=ctx.logger)
            created += 1

        downloaded = self._download_runtimes(ctx, games, warnings)

        result = StepResult(
            changed=bool(created),
            summary=f"{created} game library/libraries prepared, "
                    f"{downloaded} runtime file(s) downloaded",
            data={"games": games},
        )
        for message in warnings:
            result.warn(message)
        result.warn(
            "No ROMs or BIOS files are supplied. Copy titles you are entitled "
            f"to run into {roms_root or '<games root>'}/<platform>/ and then run "
            "`cinevaultctl module rebuild <platform>`.")
        return result

    def _download_runtimes(self, ctx, games: list[str], warnings: list[str]) -> int:
        if ctx.offline:
            warnings.append("Offline mode: emulator runtimes were not downloaded. "
                            "Game pages will not play until they are installed.")
            return 0
        count = 0
        for name, meta in RUNTIME_SOURCES.items():
            if not any(game in meta["used_by"] for game in games):
                continue
            target = ctx.layout.modules_dir / meta["target"]
            if target.is_file() and target.stat().st_size > 0:
                ctx.logger.debug(f"{name} runtime already present")
                continue
            if ctx.dry_run:
                ctx.logger.info(f"[dry-run] would download {name} ({meta['licence']})")
                count += 1
                continue
            ensure_dir(target.parent, mode=0o755, user=ctx.service_user,
                       group=ctx.service_group, logger=ctx.logger)
            result = ctx.runner.run(
                ["curl", "-fsSL", "--proto", "=https", "--tlsv1.2",
                 "--max-time", "120", "-o", str(target), meta["url"]],
                check=False, timeout=180)
            if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
                warnings.append(
                    f"Could not download the {name} runtime ({meta['licence']}) from "
                    f"{meta['url']}. That game module's pages will not play until "
                    f"the file is placed at {target}.")
                if target.exists():
                    target.unlink()
                continue
            ctx.logger.success(f"downloaded {name} runtime ({meta['licence']})")
            count += 1
        return count


def steps():
    return [WriteModuleConfig(), InstallGameModules()]
