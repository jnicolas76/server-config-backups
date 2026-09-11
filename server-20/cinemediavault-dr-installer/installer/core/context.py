"""The install context: everything a step needs, in one object."""

from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import loader
from ..config.schema import apply_defaults, get
from ..version import INSTALLER_VERSION
from .fsops import BackupSet, new_backup_set
from .journal import Journal
from .logging import InstallLogger
from .redact import REDACTOR
from .runner import Runner


@dataclass
class Layout:
    """Resolved installation paths."""

    install_root: Path
    config_root: Path
    state_root: Path
    log_root: Path
    cache_root: Path

    # -- derived ------------------------------------------------------------
    @property
    def app_dir(self) -> Path: return self.install_root / "app"

    @property
    def bin_dir(self) -> Path: return self.install_root / "bin"

    @property
    def scripts_dir(self) -> Path: return self.install_root / "scripts"

    @property
    def creators_dir(self) -> Path: return self.install_root / "creators"

    @property
    def assets_dir(self) -> Path: return self.install_root / "assets"

    @property
    def compose_dir(self) -> Path: return self.install_root / "compose"

    @property
    def modules_dir(self) -> Path: return self.install_root / "modules"

    @property
    def config_file(self) -> Path: return self.config_root / "cinemediavault.yaml"

    @property
    def env_file(self) -> Path: return self.config_root / "cinevault.env"

    @property
    def secrets_file(self) -> Path: return self.config_root / "secrets.env"

    @property
    def admin_bootstrap_file(self) -> Path: return self.config_root / "admin-bootstrap.json"

    @property
    def journal_file(self) -> Path: return self.state_root / "install-journal.json"

    @property
    def wizard_state_file(self) -> Path: return self.state_root / "setup-state.json"

    @property
    def tls_dir(self) -> Path: return self.config_root / "certs"

    @property
    def db_dir(self) -> Path: return self.state_root / "db"

    @property
    def db_file(self) -> Path: return self.db_dir / "cinemediavault.db"

    @property
    def music_db_file(self) -> Path: return self.db_dir / "music.db"

    @property
    def backup_dir(self) -> Path: return self.state_root / "backups"

    @property
    def change_backup_dir(self) -> Path: return self.state_root / "change-backups"

    @property
    def metadata_dir(self) -> Path: return self.state_root / "metadata"

    @property
    def movie_data_dir(self) -> Path: return self.metadata_dir / "movies"

    @property
    def tv_data_dir(self) -> Path: return self.metadata_dir / "tv"

    @property
    def music_art_dir(self) -> Path: return self.state_root / "music-art"

    @property
    def module_logo_dir(self) -> Path: return self.state_root / "module-logos"

    @property
    def promo_dir(self) -> Path: return self.state_root / "promo"

    @property
    def hls_cache_dir(self) -> Path: return self.cache_root / "hls"

    @property
    def subtitle_cache_dir(self) -> Path: return self.cache_root / "subtitles"

    @property
    def mobile_cache_dir(self) -> Path: return self.cache_root / "mobile-downloads"

    @property
    def version_file(self) -> Path: return self.install_root / "VERSION.json"

    @property
    def command_link_dir(self) -> Path | None:
        """Where `cinevaultctl` and `cinevault-create` are exposed on PATH.

        Only a normal system installation gets a link in /usr/local/sbin. A
        relocated install - a test sandbox, a second instance under a prefix -
        must not reach outside its own tree and quietly capture the system-wide
        command name.
        """
        if str(self.install_root) == "/opt/cinemediavault":
            return Path("/usr/local/sbin")
        return None

    def all_owned_dirs(self) -> list[Path]:
        return [self.install_root, self.config_root, self.state_root,
                self.log_root, self.cache_root]


@dataclass
class SystemFacts:
    """Facts about the machine, gathered once during preflight."""

    os_id: str = ""
    os_version: str = ""
    os_codename: str = ""
    arch: str = ""
    kernel: str = ""
    cpu_count: int = 0
    memory_total_mb: int = 0
    is_container: bool = False
    is_wsl: bool = False
    has_systemd: bool = False
    has_docker: bool = False
    docker_compose_v2: bool = False
    gpu_vendors: tuple[str, ...] = ()
    ffmpeg_path: str = ""
    ffmpeg_version: str = ""
    python_version: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "os": f"{self.os_id} {self.os_version}".strip(),
            "arch": self.arch,
            "kernel": self.kernel,
            "cpu": self.cpu_count,
            "memory_mb": self.memory_total_mb,
            "systemd": self.has_systemd,
            "docker": self.has_docker,
            "gpu": list(self.gpu_vendors),
            "ffmpeg": self.ffmpeg_version or "not installed",
            "python": self.python_version,
            "container": self.is_container,
            "wsl": self.is_wsl,
        }


class Context:
    """Shared state handed to every preflight check and install step."""

    def __init__(
        self,
        config: dict,
        *,
        logger: InstallLogger,
        dry_run: bool = False,
        force_steps: tuple[str, ...] = (),
        only_steps: tuple[str, ...] = (),
        skip_steps: tuple[str, ...] = (),
        package_root: str | os.PathLike | None = None,
        assume_yes: bool = False,
        offline: bool = False,
    ):
        self.config = apply_defaults(config)
        self.logger = logger
        self.dry_run = dry_run
        self.force_steps = tuple(force_steps)
        self.only_steps = tuple(only_steps)
        self.skip_steps = tuple(skip_steps)
        self.assume_yes = assume_yes
        self.offline = offline
        self.started_at = time.time()
        self.installer_version = INSTALLER_VERSION

        self.package_root = Path(package_root or Path(__file__).resolve().parents[2])
        self.runner = Runner(dry_run=dry_run, logger=logger)
        self.facts = SystemFacts()
        self.notes: list[str] = []
        self.service_urls: dict[str, str] = {}

        self.layout = Layout(
            install_root=Path(self.get("paths.install_root")),
            config_root=Path(self.get("paths.config_root")),
            state_root=Path(self.get("paths.state_root")),
            log_root=Path(self.get("paths.log_root")),
            cache_root=Path(self.get("paths.cache_root")),
        )

        # Registering secrets before anything else means no later log line can
        # print one, even if a step accidentally interpolates the config.
        REDACTOR.register_config(self.config)

        self._journal: Journal | None = None
        self._backups: BackupSet | None = None

    # -- accessors ----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return get(self.config, key, default)

    @property
    def service_user(self) -> str:
        return self.get("paths.service_user")

    @property
    def service_group(self) -> str:
        return self.get("paths.service_group")

    @property
    def journal(self) -> Journal:
        """The install journal.

        A dry run, a plan and the wizard's review screen all build a context
        without being allowed to touch the system - and they may not even be
        running as root. In that case the journal reads any existing file but
        keeps its writes in a temporary location, so planning never creates the
        state tree it is only describing.
        """
        if self._journal is None:
            path = self.layout.journal_file
            if not self._can_write_state():
                import tempfile
                scratch = Path(tempfile.mkdtemp(prefix="cmv-plan-"))
                journal = Journal(path if path.is_file() else scratch / "journal.json")
                journal.path = scratch / "journal.json"
                self._journal = journal
            else:
                self.layout.state_root.mkdir(parents=True, exist_ok=True)
                self._journal = Journal(path)
        return self._journal

    def _can_write_state(self) -> bool:
        """True when this process may actually create the state tree."""
        if self.dry_run:
            return False
        root = self.layout.state_root
        probe = root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return os.access(probe, os.W_OK)

    @property
    def backups(self) -> BackupSet:
        if self._backups is None:
            root = self.layout.change_backup_dir
            if not self._can_write_state():
                import tempfile
                root = Path(tempfile.mkdtemp(prefix="cmv-backups-"))
            root.mkdir(parents=True, exist_ok=True)
            self._backups = new_backup_set(root, "install")
        return self._backups

    @property
    def config_hash(self) -> str:
        return loader.config_hash(self.config)

    # -- helpers ------------------------------------------------------------
    def note(self, text: str) -> None:
        self.notes.append(text)

    def enabled(self, key: str) -> bool:
        return bool(self.get(key))

    def selected_games(self) -> list[str]:
        return list(self.get("modules.games") or [])

    def selected_creators(self) -> list[str]:
        return list(self.get("modules.creators") or [])

    def media_roots(self) -> dict[str, str]:
        keys = {
            "movies": "media.movies_root",
            "tv": "media.tv_root",
            "music": "media.music_root",
            "books": "media.books_root",
            "comics": "media.comics_root",
            "comic_library": "media.comic_library_root",
            "games": "media.games_root",
            "recordings": "media.recordings_root",
        }
        return {name: (self.get(key) or "").strip()
                for name, key in keys.items() if (self.get(key) or "").strip()}

    def primary_url(self) -> str:
        host = self.get("network.hostname") or "localhost"
        if self.get("network.tls_mode") != "none":
            return f"https://{host}:{self.get('network.https_port')}/"
        return f"http://{host}:{self.get('network.http_port')}/"

    def detect_facts(self) -> SystemFacts:
        """Populate :attr:`facts`. Read-only; safe in dry-run."""
        facts = self.facts
        facts.arch = platform.machine()
        facts.kernel = platform.release()
        facts.python_version = platform.python_version()
        facts.cpu_count = os.cpu_count() or 1

        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition("=")
                value = value.strip().strip('"')
                if key == "ID":
                    facts.os_id = value
                elif key == "VERSION_ID":
                    facts.os_version = value
                elif key == "VERSION_CODENAME":
                    facts.os_codename = value
        except OSError:
            pass

        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    facts.memory_total_mb = int(line.split()[1]) // 1024
                    break
        except (OSError, ValueError, IndexError):
            pass

        facts.is_wsl = "microsoft" in facts.kernel.lower()
        facts.is_container = (
            Path("/.dockerenv").exists()
            or Path("/run/.containerenv").exists()
        )
        facts.has_systemd = Path("/run/systemd/system").is_dir()
        facts.has_docker = self.runner.has("docker")
        if facts.has_docker:
            facts.docker_compose_v2 = self.runner.probe(
                ["docker", "compose", "version"]).returncode == 0

        vendors: list[str] = []
        if self.runner.has("nvidia-smi") and \
                self.runner.probe(["nvidia-smi", "-L"]).returncode == 0:
            vendors.append("nvidia")
        try:
            dri = Path("/dev/dri")
            if dri.is_dir() and any(dri.iterdir()):
                render = self.runner.probe(["lspci"]).stdout.lower()
                if "intel" in render:
                    vendors.append("intel")
                if "amd" in render or "radeon" in render:
                    vendors.append("amd")
                if not vendors:
                    vendors.append("vaapi")
        except OSError:
            pass
        facts.gpu_vendors = tuple(dict.fromkeys(vendors))

        ffmpeg = self.runner.which("ffmpeg")
        if ffmpeg:
            facts.ffmpeg_path = ffmpeg
            out = self.runner.probe([ffmpeg, "-version"]).stdout.splitlines()
            facts.ffmpeg_version = out[0] if out else "unknown"
        return facts
