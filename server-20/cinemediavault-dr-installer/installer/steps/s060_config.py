"""Write the configuration file, the service environment file and the secrets.

The application is configured entirely through environment variables. This step
is the single place that translates the operator-facing configuration schema
into the variable names the application actually reads, so a rename upstream is
a one-line change here rather than a hunt through the installer.
"""

from __future__ import annotations

import json
import time

from ..config import loader
from ..config.secrets import SecretStore, render_env_file
from ..core.fsops import write_file
from ..version import CONFIG_SCHEMA_VERSION, INSTALLER_VERSION
from .base import Step, StepResult

CONFIG_HEADER = """CineMediaVault configuration.

Written by the installer. Edit with `cinevaultctl config edit`, or edit by hand
and then run `cinevaultctl apply` so the derived service environment, systemd
units and container settings are regenerated together.

Secrets are NOT stored here. They live in secrets.env with mode 0640.
"""

ENV_HEADER = """CineMediaVault service environment.

GENERATED FILE - do not edit. Every value here is derived from
cinemediavault.yaml; hand edits are overwritten on the next `cinevaultctl apply`.
"""


def build_environment(ctx) -> dict[str, str]:
    """Map the configuration schema onto the application's environment variables."""
    layout = ctx.layout
    get = ctx.get
    env: dict[str, str] = {}

    def put(name: str, value) -> None:
        if value is None:
            return
        text = str(value)
        if text == "":
            return
        env[name] = text

    # -- identity and network ------------------------------------------------
    put("CINEVAULT_SERVER_NAME", get("network.hostname"))
    put("CINEVAULT_INSTANCE_NAME", get("meta.instance_name"))
    put("CINEVAULT_HOST", get("network.listen_address"))
    put("CINEVAULT_PORT", get("network.https_port") if get("network.tls_mode") != "none"
        else get("network.http_port"))
    put("CINEVAULT_HTTP_PORT", get("network.http_port") if get("network.enable_http") else None)
    put("TZ", get("deployment.timezone"))

    if get("network.tls_mode") != "none":
        put("CINEVAULT_TLS_CERT", str(layout.tls_dir / "cinemediavault.crt")
            if get("network.tls_mode") == "self_signed" else get("network.tls_cert_path"))
        put("CINEVAULT_TLS_KEY", str(layout.tls_dir / "cinemediavault.key")
            if get("network.tls_mode") == "self_signed" else get("network.tls_key_path"))

    # -- session and security --------------------------------------------------
    put("CINEVAULT_SESSION_COOKIE", "cinevault_session")
    put("CINEVAULT_SESSION_DAYS", get("ops.session_days"))
    put("CINEVAULT_ADMIN_BOOTSTRAP", str(layout.admin_bootstrap_file))

    # -- databases and state ---------------------------------------------------
    put("CINEVAULT_DB", str(layout.db_file))
    put("CINEVAULT_MUSIC_DB", str(layout.music_db_file))
    put("CINEVAULT_BACKUP_DIR", str(layout.backup_dir))
    put("CINEVAULT_BACKUP_KEEP", get("ops.db_backup_keep"))
    put("CINEVAULT_MODULE_CONFIG_FILE", str(layout.state_root / "modules.json"))
    put("CINEVAULT_MODULE_LOGO_DIR", str(layout.module_logo_dir))
    put("CINEVAULT_PLAYBACK_MODE_FILE", str(layout.state_root / "playback-mode.txt"))
    put("CINEVAULT_PLAYBACK_MODE", get("transcode.default_playback_mode"))
    put("CINEVAULT_POSTER_ROTATION_SECONDS", get("ops.poster_rotation_seconds"))
    put("CINEVAULT_POSTER_ROTATION_CACHE_FILE",
        str(layout.state_root / "poster-rotation-cache.json"))
    put("SCAN_PROGRESS_FILE", str(layout.state_root / "scan-progress.json"))
    put("MEDIA_LIBRARY_LOG_DIR", str(layout.log_root / "media-library"))
    put("MEDIA_REFRESH_SCRIPT", str(layout.scripts_dir / "media-library-refresh.sh"))

    # -- application module directories -----------------------------------------
    put("MOVIE_APP_DIR", str(layout.app_dir / "media-download-library"))
    put("TV_APP_DIR", str(layout.app_dir / "tv-download-library"))
    put("MEDIA_LIBRARY_ASSET_DIR", str(layout.assets_dir))

    # -- libraries -----------------------------------------------------------------
    put("MOVIE_ROOT", get("media.movies_root"))
    put("TV_ROOT", get("media.tv_root"))
    put("CINEVAULT_MUSIC_ROOT", get("media.music_root"))
    put("CINEVAULT_MUSIC_ART_CACHE", str(layout.music_art_dir))
    put("CINEVAULT_BOOK_ROOT", get("media.books_root"))
    put("CINEVAULT_BOOKVAULT_INDEX_CACHE",
        str(layout.state_root / "bookvault" / "book-index-cache.json"))
    put("COMICS_ROOT", get("media.comics_root"))
    put("CINEVAULT_COMIC_LIBRARY_ROOT", get("media.comic_library_root"))
    put("CINEVAULT_ROM_ROOT", get("media.games_root"))

    # Generated metadata lives in state, never beside the user's media.
    movie_data = layout.movie_data_dir
    tv_data = layout.tv_data_dir
    put("MOVIE_LIVE_CACHE", str(movie_data / "movie-live-index.json"))
    put("MOVIE_METADATA_MAP", str(movie_data / "movie-metadata-map.json"))
    put("MOVIE_METADATA_MISSES", str(movie_data / "movie-metadata-misses.json"))
    put("MOVIE_POSTER_MAP", str(movie_data / "poster-map.json"))
    put("MOVIE_POSTER_MISSES", str(movie_data / "poster-misses.json"))
    put("MOVIE_POSTER_DIR", str(movie_data / "posters"))
    put("MOVIE_MANUAL_METADATA_OVERRIDES",
        str(movie_data / "manual-metadata-overrides.json"))
    put("MOVIE_MANUAL_POSTER_OVERRIDES",
        str(movie_data / "manual-poster-overrides.json"))
    put("TV_LIVE_CACHE", str(tv_data / "tv-live-index.json"))
    put("TV_METADATA_MAP", str(tv_data / "tv-metadata-map.json"))
    put("TV_METADATA_MISSES", str(tv_data / "tv-metadata-misses.json"))
    put("TV_POSTER_MAP", str(tv_data / "tv-poster-map.json"))
    put("TV_POSTER_MISSES", str(tv_data / "tv-poster-misses.json"))
    put("TV_POSTER_DIR", str(tv_data / "posters"))
    put("TV_CUSTOM_ART_MAP", str(tv_data / "custom-art-map.json"))
    put("TV_EPISODE_THUMB_DIR", str(tv_data / "episode-thumbnails"))
    put("TMDB_CONFIG_FILE", str(ctx.layout.config_root / "tmdb.json"))

    # -- caches ------------------------------------------------------------------------
    put("HLS_CACHE_DIR", str(layout.hls_cache_dir))
    put("CINEVAULT_SUBTITLE_CACHE_DIR", str(layout.subtitle_cache_dir))
    put("MOBILE_DOWNLOAD_CACHE_DIR", str(layout.mobile_cache_dir))

    # -- transcoding ---------------------------------------------------------------------
    put("HLS_ENCODER", get("transcode.hls_encoder"))
    put("HLS_VIDEO_BITRATE", get("transcode.hls_video_bitrate"))
    put("HLS_VIDEO_MAXRATE", get("transcode.hls_video_bitrate"))
    put("HLS_CACHE_MAX_AGE_HOURS", get("transcode.hls_cache_max_age_hours"))
    put("MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR",
        get("transcode.mobile_download_target_mb_per_hour"))
    if ctx.facts.ffmpeg_path:
        put("FFMPEG_BIN", ctx.facts.ffmpeg_path)

    # -- Live TV / DVR / EPG -------------------------------------------------------------
    put("HDHR_GUIDE_CACHE_FILE", str(layout.state_root / "hdhr-guide-cache.json"))
    put("HDHR_GUIDE_MAX_AGE_HOURS", get("epg.local_cache_max_age_hours"))
    if get("dvr.enabled"):
        put("CINEVAULT_DVR_ROOT", get("media.recordings_root"))
        put("CINEVAULT_DVR_POLL_SECONDS", get("dvr.poll_seconds"))
        put("CINEVAULT_DVR_PADDING_START", get("dvr.padding_start_seconds"))
        put("CINEVAULT_DVR_PADDING_END", get("dvr.padding_end_seconds"))
        put("CINEVAULT_DVR_RETENTION_DAYS", get("dvr.retention_days"))
        put("CINEVAULT_DVR_MIN_FREE_GB", get("dvr.min_free_gb"))
        put("CINEVAULT_DVR_CONFLICT_POLICY", get("dvr.conflict_policy"))
    if get("livetv.enabled") and get("livetv.device_address"):
        put("CINEVAULT_HDHR_ADDRESS", get("livetv.device_address"))
    if get("livetv.tuner_count"):
        put("CINEVAULT_HDHR_TUNERS", get("livetv.tuner_count"))
    if get("livetv.reserved_tuners"):
        put("CINEVAULT_HDHR_RESERVED_TUNERS", get("livetv.reserved_tuners"))

    extended = get("epg.mode") == "extended"
    put("CINEVAULT_EPG_EXTEND_ENABLED", "1" if extended else "0")
    if extended:
        feed = (get("epg.feed_url") or "").strip() or \
            f"http://{get('epg.collector_bind_address')}:{get('epg.collector_port')}/guide.xml"
        put("CINEVAULT_EPG_EXTEND_URL", feed)
        put("CINEVAULT_EPG_EXTEND_DAYS", get("epg.horizon_days"))
        put("CINEVAULT_EPG_EXTEND_MAP", str(layout.state_root / "epg-extend-map.json"))
        put("CINEVAULT_EPG_EXTEND_TIMEOUT", 60)

    # -- optional integrations -----------------------------------------------------------
    if get("integrations.transcode_control_db"):
        put("TRANSCODE_CONTROL_DB", get("integrations.transcode_control_db"))

    # -- music -----------------------------------------------------------------------------
    put("CINEMEDIAVAULT_MUSIC_AUTO_SCAN", "0")

    # -- promo/barker channel ---------------------------------------------------------------
    # Virtual channels and genre discovery (virtual_channels.py, genre_catalog.py) are
    # always-on core application modules with no environment-level switch, matching the
    # live 2026-09-08 application exactly. Only the optional spoken-preview add-on is
    # conditional: these four names are read directly by virtual_channels.py itself.
    if get("promo_channel.enabled"):
        put("CINEVAULT_BARKER_TTS_PYTHON", str(layout.promo_dir / "venv" / "bin" / "python"))
        put("CINEVAULT_BARKER_TTS_SCRIPT", str(layout.app_dir / "barker_tts_generate.py"))
        put("CINEVAULT_BARKER_TTS_MODEL", get("promo_channel.model_path"))
        put("CINEVAULT_BARKER_TTS_VOICES", get("promo_channel.voices_path"))

    return env


class WriteConfiguration(Step):
    id = "config"
    title = "Configuration files"
    description = ("Write cinemediavault.yaml, the derived service environment "
                   "and the 0640 secrets file.")
    requires = ("directories",)
    depends_on = ()

    def fingerprint_inputs(self, ctx):
        # Any configuration change at all must rewrite these files.
        return [ctx.config_hash]

    def preview(self, ctx) -> str:
        return (f"Write {ctx.layout.config_file}, {ctx.layout.env_file} and "
                f"{ctx.layout.secrets_file}.")

    def run(self, ctx) -> StepResult:
        changed = False

        # -- main configuration -------------------------------------------
        public, _ = loader.split_secrets(ctx.config)
        public.setdefault("meta", {})
        public["meta"]["schema_version"] = CONFIG_SCHEMA_VERSION
        public["meta"]["installer_version"] = INSTALLER_VERSION
        public["meta"]["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        changed |= loader.save(
            ctx.layout.config_file, public,
            mode=0o640, user="root", group=ctx.service_group,
            header=CONFIG_HEADER, backups=ctx.backups, dry_run=ctx.dry_run)

        # -- derived service environment ------------------------------------
        environment = build_environment(ctx)
        changed |= write_file(
            ctx.layout.env_file,
            render_env_file(environment, header=ENV_HEADER),
            mode=0o640, user="root", group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        # -- secrets -----------------------------------------------------------
        store = SecretStore(ctx.layout.secrets_file, group=ctx.service_group)
        changed |= store.write(ctx.config, backups=ctx.backups, dry_run=ctx.dry_run)

        # -- TMDb configuration file the fetchers read ---------------------------
        tmdb = {
            "api_key": ctx.get("metadata.tmdb_api_key") or "",
            "read_access_token": ctx.get("metadata.tmdb_read_access_token") or "",
        }
        if any(tmdb.values()):
            changed |= write_file(
                ctx.layout.config_root / "tmdb.json",
                json.dumps(tmdb, indent=2) + "\n",
                mode=0o640, user="root", group=ctx.service_group,
                backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        # -- version metadata ----------------------------------------------------
        changed |= write_file(
            ctx.layout.version_file,
            json.dumps({
                "installer_version": INSTALLER_VERSION,
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "instance_name": ctx.get("meta.instance_name"),
                "deployment_mode": ctx.get("deployment.mode"),
            }, indent=2) + "\n",
            mode=0o644, user="root", group=ctx.service_group,
            backups=ctx.backups, dry_run=ctx.dry_run, logger=ctx.logger)

        return StepResult(
            changed=changed,
            summary=f"{len(environment)} environment variables written",
            data={"env_var_count": len(environment)},
        )


def steps():
    return [WriteConfiguration()]
