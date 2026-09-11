"""Create the installation directory tree with least-privilege permissions.

Permission model
----------------
* ``/opt/cinemediavault``  root-owned, group-readable. The service reads its
  own code but can never modify it, so a compromise of the web process cannot
  rewrite the application.
* ``/etc/cinemediavault``  root-owned, group ``cinevault``, mode 0750. Secrets
  inside are 0640 so only root writes and only the service group reads.
* ``/var/lib`` and ``/var/log`` and ``/var/cache``  owned by the service.
"""

from __future__ import annotations

from ..config.schema import PathSafetyError, check_media_path
from ..core.errors import StepError
from ..core.fsops import ensure_dir
from .base import Step, StepResult

#: (attribute name, mode, owned-by-service)
INSTALL_DIRS = [
    ("install_root", 0o755, False),
    ("app_dir", 0o755, False),
    ("bin_dir", 0o755, False),
    ("scripts_dir", 0o755, False),
    ("creators_dir", 0o755, False),
    ("assets_dir", 0o755, False),
    ("modules_dir", 0o755, False),
    ("compose_dir", 0o750, False),
    ("config_root", 0o750, False),
    ("tls_dir", 0o750, False),
    ("state_root", 0o750, True),
    ("db_dir", 0o750, True),
    ("backup_dir", 0o750, True),
    ("change_backup_dir", 0o700, False),
    ("metadata_dir", 0o750, True),
    ("movie_data_dir", 0o750, True),
    ("tv_data_dir", 0o750, True),
    ("music_art_dir", 0o750, True),
    ("module_logo_dir", 0o750, True),
    ("log_root", 0o750, True),
    ("cache_root", 0o750, True),
    ("hls_cache_dir", 0o750, True),
    ("subtitle_cache_dir", 0o750, True),
    ("mobile_cache_dir", 0o750, True),
]


class CreateDirectories(Step):
    id = "directories"
    title = "Directory layout"
    description = "Create the application, configuration, state, log and cache trees."
    depends_on = ("paths.install_root", "paths.config_root", "paths.state_root",
                  "paths.log_root", "paths.cache_root", "paths.service_user")
    requires = ("users",)

    def preview(self, ctx) -> str:
        return (f"Create {ctx.layout.install_root}, {ctx.layout.config_root}, "
                f"{ctx.layout.state_root}, {ctx.layout.log_root} and "
                f"{ctx.layout.cache_root}.")

    def run(self, ctx) -> StepResult:
        changed = 0
        for attribute, mode, service_owned in INSTALL_DIRS:
            path = getattr(ctx.layout, attribute)
            user = ctx.service_user if service_owned else "root"
            group = ctx.service_group
            if ensure_dir(path, mode=mode, user=user, group=group,
                          dry_run=ctx.dry_run, logger=ctx.logger):
                changed += 1
        return StepResult(changed=bool(changed),
                          summary=f"{changed} directories created or corrected")


class CreateMediaDirectories(Step):
    id = "directories.media"
    title = "Media directories"
    description = ("Create configured media directories that do not exist yet. "
                   "Existing content is never modified.")
    depends_on = ("media.movies_root", "media.tv_root", "media.music_root",
                  "media.books_root", "media.comics_root",
                  "media.comic_library_root", "media.games_root",
                  "media.recordings_root", "media.create_missing")
    requires = ("directories",)

    #: Roots CineMediaVault writes to. Only these are created and chowned; a
    #: read-only library keeps whatever ownership it already has.
    SERVICE_OWNED = {"comic_library", "games", "recordings"}

    def applies(self, ctx) -> bool:
        return bool(ctx.media_roots())

    def preview(self, ctx) -> str:
        from pathlib import Path
        missing = [p for p in ctx.media_roots().values() if not Path(p).exists()]
        if not missing:
            return "All configured media directories already exist."
        if not ctx.get("media.create_missing"):
            return ("These do not exist and 'create missing' is off, so the "
                    "install will stop: " + ", ".join(missing))
        return "Create: " + ", ".join(missing)

    def run(self, ctx) -> StepResult:
        from pathlib import Path

        created: list[str] = []
        skipped: list[str] = []
        warnings: list[str] = []

        for name, raw in ctx.media_roots().items():
            # Validated again here, not only in preflight: a step must be safe
            # to run on its own via `cinevaultctl repair --only directories.media`.
            try:
                path = Path(check_media_path(raw, label=name))
            except PathSafetyError as exc:
                raise StepError(str(exc), step=self.id, recoverable=False) from exc

            if path.exists():
                skipped.append(str(path))
                continue
            if not ctx.get("media.create_missing"):
                warnings.append(f"{path} does not exist and was not created")
                continue

            service_owned = name in self.SERVICE_OWNED
            ensure_dir(
                path,
                mode=0o755,
                user=ctx.service_user if service_owned else None,
                group=ctx.service_group if service_owned else None,
                dry_run=ctx.dry_run,
                logger=ctx.logger,
            )
            created.append(str(path))

        result = StepResult(
            changed=bool(created),
            summary=(f"created {len(created)}, left {len(skipped)} existing "
                     f"directories untouched"),
            data={"created": created, "existing": skipped},
        )
        for message in warnings:
            result.warn(message)
        return result


def steps():
    return [CreateDirectories(), CreateMediaDirectories()]
