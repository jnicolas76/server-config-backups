#!/usr/bin/env python3
import argparse
import base64
import concurrent.futures
import datetime
import gzip
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="cgi")
import cgi
import hashlib
import hmac
import html
import importlib.util
import json
import math
import mimetypes
import os
import random
import re
import shutil
import socket
import sqlite3
import ssl
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from cinevault_video_lists import VideoListsMixin
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
import music_module
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MOVIE_APP_DIR = Path(os.environ.get("MOVIE_APP_DIR", "/home/jnicolas/media-download-library")).resolve()
TV_APP_DIR = Path(os.environ.get("TV_APP_DIR", "/home/jnicolas/tv-download-library")).resolve()
MOVIE_ROOT = os.environ.get("MOVIE_ROOT", "/media/jnicolas/Expansion/Movies")
TV_ROOT = os.environ.get("TV_ROOT", "/media/jnicolas/Elements/TV Shows")
ASSET_DIR = Path(os.environ.get("MEDIA_LIBRARY_ASSET_DIR", "/home/jnicolas/media-library-assets")).resolve()
COMIC_LIBRARY_ROOT = Path(os.environ.get("CINEVAULT_COMIC_LIBRARY_ROOT", "/media/jnicolas/Expansion/comic-library")).resolve()
BOOK_ROOT = Path(os.environ.get("CINEVAULT_BOOK_ROOT", "/media/jnicolas/Expansion/Books")).resolve()
BOOKVAULT_INDEX_CACHE = Path(os.environ.get("CINEVAULT_BOOKVAULT_INDEX_CACHE", "/home/jnicolas/bookvault/book-index-cache.json")).resolve()
ROM_ROOT = Path(os.environ.get("CINEVAULT_ROM_ROOT", "/home/jnicolas/roms")).resolve()
MODULE_GAME_ROOTS = {
    "nes": (Path("/home/jnicolas/software/NES/roms"), {".nes", ".zip", ".7z"}),
    "sega": (Path("/home/jnicolas/software/SEGA/roms"), {".gen", ".md", ".smd", ".bin", ".zip", ".7z"}),
    "dos": (Path("/home/jnicolas/software/DOS/games/library"), {".jsdos", ".zip", ".7z"}),
    "mame": (Path("/home/jnicolas/software/MAME/roms"), {".zip", ".7z"}),
    "arcade": (ROM_ROOT / "arcade", {".zip", ".7z"}),
    "gameboy": (Path("/home/jnicolas/software/GAMEBOY/roms"), {".gb", ".gbc", ".zip", ".7z"}),
    "gba": (Path("/home/jnicolas/software/GBA/roms"), {".gba", ".zip", ".7z"}),
    "n64": (Path("/home/jnicolas/software/N64/roms"), {".n64", ".z64", ".v64", ".zip", ".7z"}),
    "ps1": (Path("/home/jnicolas/software/PS1/roms"), {".bin", ".cue", ".iso", ".chd", ".pbp", ".zip", ".7z"}),
    "c64": (Path("/home/jnicolas/software/C64/roms"), {".d64", ".t64", ".crt", ".prg", ".zip", ".7z"}),
    "atari2600": (Path("/home/jnicolas/software/ATARI2600/roms"), {".a26", ".bin", ".zip", ".7z"}),
    "atari5200": (Path("/home/jnicolas/software/ATARI5200/roms"), {".a52", ".bin", ".zip", ".7z"}),
    "atari7800": (Path("/home/jnicolas/software/ATARI7800/roms"), {".a78", ".bin", ".zip", ".7z"}),
}
HLS_CACHE_DIR = Path(os.environ.get("HLS_CACHE_DIR", "/tmp/cinevault-hls")).resolve()
SUBTITLE_CACHE_DIR = Path(os.environ.get("CINEVAULT_SUBTITLE_CACHE_DIR", "/tmp/cinemediavault-lab-subtitles")).resolve()
MEDIA_REFRESH_SCRIPT = Path(os.environ.get("MEDIA_REFRESH_SCRIPT", "/home/jnicolas/media-library-refresh.sh")).resolve()
SCAN_PROGRESS_FILE = Path(os.environ.get("SCAN_PROGRESS_FILE", "/home/jnicolas/cinemediavault-lab/.cinemediavault-scan-progress-5000.json")).resolve()
CINEVAULT_DB = Path(os.environ.get("CINEVAULT_DB", "/home/jnicolas/cinevault-data/cinevault.db")).resolve()
CINEVAULT_BACKUP_DIR = Path(os.environ.get("CINEVAULT_BACKUP_DIR", str(CINEVAULT_DB.parent / "backups"))).resolve()
CINEVAULT_BACKUP_KEEP = int(os.environ.get("CINEVAULT_BACKUP_KEEP", "10"))
CINEVAULT_SESSION_COOKIE = os.environ.get("CINEVAULT_SESSION_COOKIE", "cinevault_session")
CINEVAULT_SESSION_DAYS = int(os.environ.get("CINEVAULT_SESSION_DAYS", "30"))
PLAYBACK_MODE_FILE = Path(os.environ.get("CINEVAULT_PLAYBACK_MODE_FILE", str(CINEVAULT_DB.parent / "playback-mode.txt"))).resolve()
MODULE_CONFIG_FILE = Path(os.environ.get("CINEVAULT_MODULE_CONFIG_FILE", str(CINEVAULT_DB.parent / "modules.json"))).resolve()
MODULE_LOGO_DIR = Path(os.environ.get("CINEVAULT_MODULE_LOGO_DIR", str(CINEVAULT_DB.parent / "module-logos"))).resolve()
HLS_START_TIMEOUT = float(os.environ.get("HLS_START_TIMEOUT", "24"))
HLS_SEGMENT_WAIT_TIMEOUT = float(os.environ.get("HLS_SEGMENT_WAIT_TIMEOUT", "120"))
HLS_PREBUFFER_SEGMENTS = int(os.environ.get("HLS_PREBUFFER_SEGMENTS", "3"))
HLS_SEGMENT_SECONDS = os.environ.get("HLS_SEGMENT_SECONDS", "4")
HLS_VIDEO_BITRATE = os.environ.get("HLS_VIDEO_BITRATE", "4500k")
HLS_VIDEO_MAXRATE = os.environ.get("HLS_VIDEO_MAXRATE", HLS_VIDEO_BITRATE)
HLS_VIDEO_BUFSIZE = os.environ.get("HLS_VIDEO_BUFSIZE", "9000k")
HLS_ENCODER = os.environ.get("HLS_ENCODER", "auto").lower()
CINEVAULT_PLAYBACK_MODE = os.environ.get("CINEVAULT_PLAYBACK_MODE", "direct").lower()
HLS_CACHE_MAX_AGE_HOURS = float(os.environ.get("HLS_CACHE_MAX_AGE_HOURS", "6"))
HLS_CLEANUP_INTERVAL_MINUTES = float(os.environ.get("HLS_CLEANUP_INTERVAL_MINUTES", "30"))
HLS_IDLE_STOP_MINUTES = float(os.environ.get("HLS_IDLE_STOP_MINUTES", "10"))
HLS_VIRTUAL_VOD = os.environ.get("HLS_VIRTUAL_VOD", "1").lower() not in {"0", "false", "no"}
MOBILE_DOWNLOAD_CACHE_DIR = Path(os.environ.get("MOBILE_DOWNLOAD_CACHE_DIR", "/home/jnicolas/cinemediavault-lab/mobile-download-cache")).resolve()
MOBILE_DOWNLOAD_CACHE_MAX_AGE_HOURS = float(os.environ.get("MOBILE_DOWNLOAD_CACHE_MAX_AGE_HOURS", "24"))
MOBILE_DOWNLOAD_CACHE_MAX_AGE_OPTIONS = {6, 12, 24}
MOBILE_DOWNLOAD_SETTINGS_FILE = CINEVAULT_DB.parent / "mobile-download-settings.json"
MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR = float(os.environ.get("MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR", "150"))
HDHR_GUIDE_CACHE_FILE = Path(os.environ.get("HDHR_GUIDE_CACHE_FILE", "/home/jnicolas/cinemediavault-lab/hdhr-guide-cache.json")).resolve()
HDHR_GUIDE_MAX_AGE_HOURS = float(os.environ.get("HDHR_GUIDE_MAX_AGE_HOURS", "22"))
MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO = float(os.environ.get("MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO", "0.40"))
MOBILE_DOWNLOAD_MAX_OUTPUT_RATIO = float(os.environ.get("MOBILE_DOWNLOAD_MAX_OUTPUT_RATIO", "0.98"))
MOBILE_DOWNLOAD_ABSOLUTE_MAX_OUTPUT_RATIO = float(os.environ.get("MOBILE_DOWNLOAD_ABSOLUTE_MAX_OUTPUT_RATIO", "1.00"))
MOBILE_DOWNLOAD_TARGET_TOLERANCE = float(os.environ.get("MOBILE_DOWNLOAD_TARGET_TOLERANCE", "1.08"))
MOBILE_DOWNLOAD_MIN_EPISODE_MB = float(os.environ.get("MOBILE_DOWNLOAD_MIN_EPISODE_MB", "8"))
MOBILE_DOWNLOAD_VIDEO_CODEC = os.environ.get("MOBILE_DOWNLOAD_VIDEO_CODEC", "libx265")
MOBILE_DOWNLOAD_VIDEO_PRESET = os.environ.get("MOBILE_DOWNLOAD_VIDEO_PRESET", "fast")
MOBILE_DOWNLOAD_AUDIO_KBPS = int(os.environ.get("MOBILE_DOWNLOAD_AUDIO_KBPS", "64"))
MOBILE_DOWNLOAD_CONTAINER_OVERHEAD_KBPS = int(os.environ.get("MOBILE_DOWNLOAD_CONTAINER_OVERHEAD_KBPS", "25"))
SERVER_DISPLAY_NAME = os.environ.get("CINEVAULT_SERVER_NAME") or socket.gethostname()
TRANSCODES: dict[str, dict] = {}
DIRECT_STREAMS: dict[str, dict] = {}
HLS_VIEWERS: dict[str, dict] = {}
DIRECT_BANDWIDTH_SAMPLES = deque(maxlen=50000)
MOBILE_DOWNLOAD_JOBS: dict[str, dict] = {}
MEDIA_DURATION_CACHE: dict[str, float] = {}
MEDIA_CODEC_CACHE: dict[str, tuple[float, int, dict]] = {}
SUBTITLE_DISCOVERY_CACHE: dict[str, tuple[float, int, int, list[dict]]] = {}
TRANSCODE_LOCK = threading.Lock()
DIRECT_STREAM_LOCK = threading.Lock()
HLS_VIEWER_LOCK = threading.Lock()
MOBILE_DOWNLOAD_LOCK = threading.Lock()
MEDIA_SCAN_PROCESS = None
MEDIA_SCAN_LOCK = threading.Lock()
MEDIA_SCAN_LAST_RESULT = {"running": False}
HDHR_SCAN_LOCK = threading.Lock()
HDHR_GUIDE_LOCK = threading.Lock()


def save_scan_progress(payload: dict) -> None:
    data = dict(payload)
    data["updated_at"] = time.time()
    SCAN_PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SCAN_PROGRESS_FILE.with_suffix(SCAN_PROGRESS_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(data), encoding="utf-8")
    temp.replace(SCAN_PROGRESS_FILE)


def load_scan_progress() -> dict:
    try:
        return json.loads(SCAN_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


DEFAULT_MODULES = [
    {"id": "comics", "name": "Comics", "logo": "COMICS", "enabled": True, "protocol": "http", "port": 8110, "url": "", "start_script": "/media/jnicolas/Expansion/comic-library/start_library.sh", "pid_file": "/media/jnicolas/Expansion/comic-library/.server.pid", "stop_pattern": "python3 -m http.server 8110"},
    {"id": "bookvault", "name": "BookVault", "logo": "BOOKS", "enabled": True, "protocol": "http", "port": 8112, "url": "", "start_script": "/home/jnicolas/start_bookvault_8112.sh", "pid_file": "/home/jnicolas/bookvault-8112.pid", "stop_pattern": "bookvault_server.py"},
    {"id": "nes", "name": "NES", "logo": "NES", "enabled": True, "protocol": "https", "port": 8092, "url": "", "start_script": "/home/jnicolas/software/NES/start.sh", "pid_file": "/home/jnicolas/software/NES/.server.pid", "stop_pattern": "/home/jnicolas/software/NES/scripts/serve.py"},
    {"id": "sega", "name": "SEGA", "logo": "SEGA", "enabled": True, "protocol": "https", "port": 8094, "url": "", "start_script": "/home/jnicolas/software/SEGA/start.sh", "pid_file": "/home/jnicolas/software/SEGA/.server.pid", "stop_pattern": "/home/jnicolas/software/SEGA/scripts/serve.py"},
    {"id": "dos", "name": "DOS", "logo": "DOS", "enabled": True, "protocol": "http", "port": 8091, "url": "", "start_script": "/home/jnicolas/software/DOS/start.sh", "pid_file": "/home/jnicolas/software/DOS/.server.pid", "stop_pattern": "/home/jnicolas/software/DOS/scripts/serve.py"},
    {"id": "mame", "name": "MAME", "logo": "MAME", "enabled": True, "protocol": "https", "port": 8101, "url": "", "start_script": "/home/jnicolas/software/MAME/start.sh", "pid_file": "/home/jnicolas/software/MAME/.server.pid", "stop_pattern": "/home/jnicolas/software/MAME/scripts/serve.py"},
    {"id": "arista", "name": "Arista", "logo": "ARISTA", "enabled": False, "protocol": "http", "port": 8095, "url": "", "start_script": "/home/jnicolas/software/ARISTA/start.sh", "pid_file": "/home/jnicolas/software/ARISTA/.server.pid", "stop_pattern": "/home/jnicolas/software/ARISTA/scripts/serve.py"},
    {"id": "lost", "name": "Lost", "logo": "LOST", "enabled": False, "protocol": "http", "port": 8096, "url": "", "start_script": "/home/jnicolas/software/LOST/start.sh", "pid_file": "/home/jnicolas/software/LOST/.server.pid", "stop_pattern": "/home/jnicolas/software/LOST/scripts/serve.py"},
    {"id": "gameboy", "name": "GameBoy", "logo": "GB", "enabled": False, "protocol": "https", "port": 8097, "url": "", "start_script": "/home/jnicolas/software/GAMEBOY/start.sh", "pid_file": "/home/jnicolas/software/GAMEBOY/.server.pid", "stop_pattern": "/home/jnicolas/software/GAMEBOY/scripts/serve.py"},
    {"id": "gba", "name": "GBA", "logo": "GBA", "enabled": False, "protocol": "https", "port": 8098, "url": "", "start_script": "/home/jnicolas/software/GBA/start.sh", "pid_file": "/home/jnicolas/software/GBA/.server.pid", "stop_pattern": "/home/jnicolas/software/GBA/scripts/serve.py"},
    {"id": "n64", "name": "N64", "logo": "N64", "enabled": False, "protocol": "https", "port": 8099, "url": "", "start_script": "/home/jnicolas/software/N64/start.sh", "pid_file": "/home/jnicolas/software/N64/.server.pid", "stop_pattern": "/home/jnicolas/software/N64/scripts/serve.py"},
    {"id": "ps1", "name": "PS1", "logo": "PS1", "enabled": False, "protocol": "https", "port": 8100, "url": "", "start_script": "/home/jnicolas/software/PS1/start.sh", "pid_file": "/home/jnicolas/software/PS1/.server.pid", "stop_pattern": "/home/jnicolas/software/PS1/scripts/serve.py"},
    {"id": "c64", "name": "C64", "logo": "C64", "enabled": False, "protocol": "https", "port": 8102, "url": "", "start_script": "/home/jnicolas/software/C64/start.sh", "pid_file": "/home/jnicolas/software/C64/.server.pid", "stop_pattern": "/home/jnicolas/software/C64/scripts/serve.py"},
    {"id": "atari2600", "name": "Atari 2600", "logo": "ATARI", "enabled": False, "protocol": "https", "port": 8103, "url": "", "start_script": "/home/jnicolas/software/ATARI2600/start.sh", "pid_file": "/home/jnicolas/software/ATARI2600/.server.pid", "stop_pattern": "/home/jnicolas/software/ATARI2600/scripts/serve.py"},
    {"id": "atari5200", "name": "Atari 5200", "logo": "ATARI", "enabled": False, "protocol": "https", "port": 8104, "url": "", "start_script": "/home/jnicolas/software/ATARI5200/start.sh", "pid_file": "/home/jnicolas/software/ATARI5200/.server.pid", "stop_pattern": "/home/jnicolas/software/ATARI5200/scripts/serve.py"},
    {"id": "atari7800", "name": "Atari 7800", "logo": "ATARI", "enabled": False, "protocol": "https", "port": 8105, "url": "", "start_script": "/home/jnicolas/software/ATARI7800/start.sh", "pid_file": "/home/jnicolas/software/ATARI7800/.server.pid", "stop_pattern": "/home/jnicolas/software/ATARI7800/scripts/serve.py"},
]


def read_global_playback_mode() -> str:
    try:
        mode = PLAYBACK_MODE_FILE.read_text(encoding="utf-8").strip().lower()
        if mode in {"direct", "hls"}:
            return mode
    except OSError:
        pass
    return CINEVAULT_PLAYBACK_MODE if CINEVAULT_PLAYBACK_MODE in {"direct", "hls"} else "direct"


def write_global_playback_mode(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if normalized not in {"direct", "hls"}:
        raise ValueError("Playback mode must be direct or hls")
    PLAYBACK_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLAYBACK_MODE_FILE.write_text(normalized + "\n", encoding="utf-8")
    return normalized


def default_module_url(module: dict) -> str:
    protocol = (module.get("protocol") or "http").replace(":", "")
    port = int(module.get("port") or 80)
    return f"{protocol}://{SERVER_DISPLAY_NAME}:{port}/"


def module_public_url(module: dict, hostname: str | None = None) -> str:
    custom = str(module.get("url") or "").strip()
    if custom:
        return custom
    protocol = (module.get("protocol") or "http").replace(":", "")
    port = int(module.get("port") or 80)
    host = hostname or SERVER_DISPLAY_NAME
    return f"{protocol}://{host}:{port}/"


def load_modules() -> list[dict]:
    defaults = {item["id"]: dict(item) for item in DEFAULT_MODULES}
    try:
        stored = json.loads(MODULE_CONFIG_FILE.read_text(encoding="utf-8"))
        for item in stored if isinstance(stored, list) else []:
            module_id = str(item.get("id") or "")
            if module_id in defaults:
                defaults[module_id].update({k: v for k, v in item.items() if k in defaults[module_id] or k in {"url", "enabled", "port", "protocol", "name", "logo_url"}})
    except (OSError, json.JSONDecodeError):
        pass
    return list(defaults.values())


def save_modules(modules: list[dict]) -> None:
    MODULE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODULE_CONFIG_FILE.write_text(json.dumps(modules, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def find_module(module_id: str) -> dict | None:
    for module in load_modules():
        if module.get("id") == module_id:
            return module
    return None


def port_listening(port: int) -> bool:
    try:
        result = subprocess.run(["ss", "-ltn"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
        return any(re.search(rf"(:|\]){int(port)}$", line.split()[3] if len(line.split()) > 3 else "") for line in result.stdout.splitlines())
    except Exception:
        return False


def module_running(module: dict) -> bool:
    pid_file = Path(str(module.get("pid_file") or ""))
    try:
        if pid_file.is_file():
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)
            return True
    except Exception:
        pass
    return port_listening(int(module.get("port") or 0))


def start_module(module: dict) -> tuple[bool, str]:
    script = Path(str(module.get("start_script") or ""))
    if not script.is_file():
        return False, f"Start script not found: {script}"
    if module_running(module):
        return True, f"{module.get('name')} is already running."
    port = str(int(module.get("port") or 0))
    log_path = MODULE_CONFIG_FILE.parent / f"module-{module.get('id')}.log"
    with log_path.open("ab") as log:
        subprocess.Popen([str(script), port], stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    time.sleep(1.5)
    return module_running(module), f"Started {module.get('name')} on port {port}. Check {log_path} if it did not open."


def stop_module(module: dict) -> tuple[bool, str]:
    stopped = False
    pid_file = Path(str(module.get("pid_file") or ""))
    try:
        if pid_file.is_file():
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            os.kill(pid, 15)
            stopped = True
            time.sleep(0.8)
            try:
                os.kill(pid, 0)
                os.kill(pid, 9)
            except OSError:
                pass
            pid_file.unlink(missing_ok=True)
    except Exception:
        pass
    pattern = str(module.get("stop_pattern") or "")
    if pattern:
        try:
            result = subprocess.run(["pgrep", "-f", pattern], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3)
            pids = [int(value) for value in result.stdout.split() if value.isdigit() and int(value) != os.getpid()]
            if pids:
                subprocess.run(["kill", *map(str, pids)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                stopped = True
                time.sleep(0.8)
        except Exception:
            pass
    return stopped, f"Stopped {module.get('name')}." if stopped else f"No running process found for {module.get('name')}."


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  full_name TEXT,
  email TEXT,
  password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  is_super_admin INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at REAL NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent TEXT,
  remote_addr TEXT
);

CREATE TABLE IF NOT EXISTS user_watchlist (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, media_type, media_id)
);

CREATE TABLE IF NOT EXISTS user_ratings (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL,
  rating INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, media_type, media_id)
);

CREATE TABLE IF NOT EXISTS user_media_state (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  media_key TEXT NOT NULL,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL DEFAULT 0,
  title TEXT,
  subtitle TEXT,
  poster TEXT,
  href TEXT,
  detail_href TEXT,
  position_seconds REAL NOT NULL DEFAULT 0,
  duration_seconds REAL NOT NULL DEFAULT 0,
  progress REAL NOT NULL DEFAULT 0,
  watched INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, media_key)
);

CREATE INDEX IF NOT EXISTS idx_user_sessions_expires ON user_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_user_media_state_user_updated ON user_media_state(user_id, updated_at);

CREATE TABLE IF NOT EXISTS user_play_history (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  media_key TEXT NOT NULL,
  media_type TEXT NOT NULL,
  media_id INTEGER NOT NULL DEFAULT 0,
  title TEXT,
  subtitle TEXT,
  poster TEXT,
  href TEXT,
  detail_href TEXT,
  first_played_at TEXT NOT NULL,
  last_played_at TEXT NOT NULL,
  play_count INTEGER NOT NULL DEFAULT 1,
  max_position_seconds REAL NOT NULL DEFAULT 0,
  duration_seconds REAL NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, media_key)
);
CREATE INDEX IF NOT EXISTS idx_user_play_history_user_last ON user_play_history(user_id, last_played_at);
CREATE INDEX IF NOT EXISTS idx_user_play_history_type_last ON user_play_history(media_type, last_played_at);

CREATE TABLE IF NOT EXISTS pending_users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  full_name TEXT,
  email TEXT,
  password_hash TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewed_at TEXT,
  reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_pending_users_status ON pending_users(status, requested_at);
CREATE TABLE IF NOT EXISTS actors (id INTEGER PRIMARY KEY, name TEXT NOT NULL, name_norm TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS actor_media (
  actor_id INTEGER NOT NULL REFERENCES actors(id) ON DELETE CASCADE,
  media_type TEXT NOT NULL, media_id INTEGER NOT NULL, title TEXT NOT NULL,
  subtitle TEXT, poster TEXT, href TEXT NOT NULL,
  PRIMARY KEY(actor_id, media_type, media_id)
);
CREATE INDEX IF NOT EXISTS idx_actors_name_norm ON actors(name_norm);
CREATE INDEX IF NOT EXISTS idx_actor_media_actor ON actor_media(actor_id, media_type, title);
CREATE TABLE IF NOT EXISTS user_video_wall (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 4),
  media_type TEXT NOT NULL CHECK(media_type IN ('movie', 'tv', 'tuner')),
  media_id INTEGER NOT NULL,
  media_path TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, slot)
);
CREATE TABLE IF NOT EXISTS hdhr_devices (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL UNIQUE,
  friendly_name TEXT NOT NULL,
  model_number TEXT,
  base_url TEXT NOT NULL,
  lineup_url TEXT NOT NULL,
  tuner_count INTEGER NOT NULL DEFAULT 0,
  firmware_version TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hdhr_channels (
  id INTEGER PRIMARY KEY,
  device_id TEXT NOT NULL,
  guide_number TEXT NOT NULL,
  guide_name TEXT NOT NULL,
  stream_url TEXT NOT NULL,
  video_codec TEXT,
  audio_codec TEXT,
  is_hd INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  UNIQUE(device_id, guide_number)
);
CREATE INDEX IF NOT EXISTS idx_hdhr_channels_device_number ON hdhr_channels(device_id, guide_number);
CREATE TABLE IF NOT EXISTS user_video_queue (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  media_key TEXT NOT NULL, media_type TEXT NOT NULL, media_id INTEGER NOT NULL,
  title TEXT NOT NULL, subtitle TEXT, poster TEXT, href TEXT NOT NULL,
  position INTEGER NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, media_key)
);
CREATE INDEX IF NOT EXISTS idx_video_queue_order ON user_video_queue(user_id, position);
CREATE TABLE IF NOT EXISTS user_video_playlists (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS user_video_playlist_items (
  playlist_id INTEGER NOT NULL REFERENCES user_video_playlists(id) ON DELETE CASCADE,
  media_key TEXT NOT NULL, media_type TEXT NOT NULL, media_id INTEGER NOT NULL,
  title TEXT NOT NULL, subtitle TEXT, poster TEXT, href TEXT NOT NULL,
  position INTEGER NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(playlist_id, media_key)
);
CREATE INDEX IF NOT EXISTS idx_video_playlist_order ON user_video_playlist_items(playlist_id, position);
"""


def db_connect() -> sqlite3.Connection:
    CINEVAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CINEVAULT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def auth_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    rounds = 260000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def password_ok(password: str, stored: str) -> bool:
    try:
        scheme, rounds_text, salt_text, digest_text = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_text.encode())
        expected = base64.b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds_text))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def ensure_auth_schema() -> None:
    conn = db_connect()
    try:
        conn.executescript(AUTH_SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "is_super_admin" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN is_super_admin INTEGER NOT NULL DEFAULT 0")
        wall_columns = {row["name"] for row in conn.execute("PRAGMA table_info(user_video_wall)").fetchall()}
        if "media_path" not in wall_columns:
            conn.execute("ALTER TABLE user_video_wall ADD COLUMN media_path TEXT NOT NULL DEFAULT ''")
        wall_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='user_video_wall'").fetchone()
        if wall_sql_row and "'tuner'" not in (wall_sql_row["sql"] or ""):
            conn.execute("ALTER TABLE user_video_wall RENAME TO user_video_wall_legacy")
            conn.execute("""CREATE TABLE user_video_wall (
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 4),
              media_type TEXT NOT NULL CHECK(media_type IN ('movie','tv','tuner')),
              media_id INTEGER NOT NULL, media_path TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
              PRIMARY KEY(user_id,slot))""")
            conn.execute("""INSERT INTO user_video_wall(user_id,slot,media_type,media_id,media_path,updated_at)
              SELECT user_id,slot,media_type,media_id,media_path,updated_at FROM user_video_wall_legacy""")
            conn.execute("DROP TABLE user_video_wall_legacy")
        now = auth_now()
        admin_hash = password_hash("admin1")
        conn.execute(
            """
            INSERT INTO users(username, full_name, email, password_hash, is_admin, is_super_admin, active, created_at, updated_at)
            VALUES('jnicolas', 'Jonathan Nicolas', 'jonathan.nicolas@gmail.com', ?, 1, 1, 1, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
              full_name=excluded.full_name,
              email=excluded.email,
              is_admin=1,
              is_super_admin=1,
              active=1,
              updated_at=excluded.updated_at
            """,
            (admin_hash, now, now),
        )
        conn.commit()
    finally:
        conn.close()


ensure_auth_schema()


def hdhr_json(url: str, timeout: float = 1.5):
    request = urllib.request.Request(url, headers={"User-Agent": "CineMediaVault/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def hdhr_probe(ip: str) -> dict | None:
    try:
        data = hdhr_json(f"http://{ip}/discover.json", 0.7)
        if not isinstance(data, dict) or not data.get("DeviceID") or not data.get("LineupURL"):
            return None
        return data
    except Exception:
        return None


def hdhr_local_prefixes() -> list[str]:
    prefixes = {"192.168.1"}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        address = sock.getsockname()[0]
        sock.close()
        parts = address.split(".")
        if len(parts) == 4 and not address.startswith("127."):
            prefixes.add(".".join(parts[:3]))
    except OSError:
        pass
    return sorted(prefixes)


def scan_hdhr_devices() -> dict:
    with HDHR_SCAN_LOCK:
        candidates = [f"{prefix}.{host}" for prefix in hdhr_local_prefixes() for host in range(1, 255)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
            devices = [item for item in pool.map(hdhr_probe, candidates) if item]
        conn = db_connect()
        channel_count = 0
        try:
            now = auth_now()
            for device in devices:
                device_id = str(device.get("DeviceID"))
                base_url = str(device.get("BaseURL") or "").rstrip("/")
                lineup_url = str(device.get("LineupURL") or f"{base_url}/lineup.json")
                friendly = str(device.get("FriendlyName") or "HDHomeRun")
                conn.execute("""INSERT INTO hdhr_devices(device_id,friendly_name,model_number,base_url,lineup_url,tuner_count,firmware_version,enabled,last_seen)
                    VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET friendly_name=excluded.friendly_name,
                    model_number=excluded.model_number,base_url=excluded.base_url,lineup_url=excluded.lineup_url,
                    tuner_count=excluded.tuner_count,firmware_version=excluded.firmware_version,enabled=1,last_seen=excluded.last_seen""",
                    (device_id, friendly, str(device.get("ModelNumber") or ""), base_url, lineup_url,
                     int(device.get("TunerCount") or 0), str(device.get("FirmwareVersion") or ""), 1, now))
                try:
                    lineup = hdhr_json(lineup_url, 5.0)
                except Exception:
                    lineup = []
                for channel in lineup if isinstance(lineup, list) else []:
                    number = str(channel.get("GuideNumber") or "").strip()
                    name = str(channel.get("GuideName") or f"Channel {number}").strip()
                    stream_url = str(channel.get("URL") or "").strip()
                    if not number or not stream_url:
                        continue
                    video_codec = str(channel.get("VideoCodec") or "")
                    audio_codec = str(channel.get("AudioCodec") or "")
                    is_hd = 1 if "HD" in name.upper() or number.endswith(".1") else 0
                    conn.execute("""INSERT INTO hdhr_channels(device_id,guide_number,guide_name,stream_url,video_codec,audio_codec,is_hd,updated_at)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(device_id,guide_number) DO UPDATE SET guide_name=excluded.guide_name,
                        stream_url=excluded.stream_url,video_codec=excluded.video_codec,audio_codec=excluded.audio_codec,
                        is_hd=excluded.is_hd,updated_at=excluded.updated_at""",
                        (device_id, number, name, stream_url, video_codec, audio_codec, is_hd, now))
                    channel_count += 1
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "devices": len(devices), "channels": channel_count}


def hdhr_channel(channel_id: int):
    conn = db_connect()
    try:
        return conn.execute("""SELECT c.*,d.friendly_name,d.model_number,d.base_url,d.tuner_count
            FROM hdhr_channels c JOIN hdhr_devices d ON d.device_id=c.device_id
            WHERE c.id=? AND d.enabled=1""", (int(channel_id),)).fetchone()
    finally:
        conn.close()


def xmltv_timestamp(value: str) -> int:
    try:
        return int(datetime.datetime.strptime(value.strip(), "%Y%m%d%H%M%S %z").timestamp())
    except (TypeError, ValueError):
        return 0


def refresh_hdhr_guide(force: bool = False) -> dict:
    with HDHR_GUIDE_LOCK:
        if not force and HDHR_GUIDE_CACHE_FILE.is_file():
            age = time.time() - HDHR_GUIDE_CACHE_FILE.stat().st_mtime
            if age < HDHR_GUIDE_MAX_AGE_HOURS * 3600:
                cached = json.loads(HDHR_GUIDE_CACHE_FILE.read_text(encoding="utf-8"))
                return {"ok": True, "cached": True, "programmes": cached.get("programme_count", 0)}
        conn = db_connect()
        try:
            devices = [dict(row) for row in conn.execute(
                "SELECT device_id,base_url FROM hdhr_devices WHERE enabled=1 ORDER BY device_id"
            ).fetchall()]
        finally:
            conn.close()
        auth_tokens = []
        for device in devices:
            try:
                details = hdhr_json(device["base_url"].rstrip("/") + "/discover.json", 5.0)
                token = str(details.get("DeviceAuth") or "").strip()
                if token:
                    auth_tokens.append(token)
            except Exception:
                continue
        if not auth_tokens:
            raise RuntimeError("No HDHomeRun DeviceAuth token is available")
        url = "https://api.hdhomerun.com/api/xmltv?DeviceAuth=" + urllib.parse.quote("".join(auth_tokens))
        request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "CineMediaVault/1.0"})
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        root = ET.fromstring(raw)
        channel_numbers = {}
        for channel in root.findall("channel"):
            names = [str(item.text or "").strip() for item in channel.findall("display-name")]
            number = next((name for name in names if re.fullmatch(r"\d+(?:\.\d+)?", name)), "")
            if number:
                channel_numbers[str(channel.get("id") or "")] = number
        programmes = {}
        count = 0
        for item in root.findall("programme"):
            number = channel_numbers.get(str(item.get("channel") or ""))
            if not number:
                continue
            start = xmltv_timestamp(str(item.get("start") or ""))
            stop = xmltv_timestamp(str(item.get("stop") or ""))
            if not start or stop < time.time() - 7200:
                continue
            entry = {
                "title": str(item.findtext("title") or "Live TV").strip(),
                "subtitle": str(item.findtext("sub-title") or "").strip(),
                "description": str(item.findtext("desc") or "").strip(),
                "category": str(item.findtext("category") or "").strip(),
                "start": start,
                "stop": stop,
            }
            programmes.setdefault(number, []).append(entry)
            count += 1
        for values in programmes.values():
            values.sort(key=lambda entry: entry["start"])
        payload = {"updated_at": int(time.time()), "programme_count": count, "programmes": programmes}
        HDHR_GUIDE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = HDHR_GUIDE_CACHE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(HDHR_GUIDE_CACHE_FILE)
        return {"ok": True, "cached": False, "channels": len(programmes), "programmes": count}


def hdhr_guide_snapshot() -> dict:
    try:
        return json.loads(HDHR_GUIDE_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": 0, "programme_count": 0, "programmes": {}}


def start_hdhr_guide_thread() -> None:
    def worker() -> None:
        while True:
            try:
                refresh_hdhr_guide(False)
            except Exception as exc:
                print(f"HDHomeRun guide refresh failed: {exc}", flush=True)
            time.sleep(random.uniform(20, 28) * 3600)
    threading.Thread(target=worker, daemon=True, name="hdhr-guide-refresh").start()


def cinevault_db_status() -> dict:
    tables = [
        "libraries",
        "media_items",
        "movies",
        "tv_shows",
        "tv_seasons",
        "tv_episodes",
        "people",
        "genres",
        "artwork",
        "watch_progress",
        "scan_runs",
    ]
    if not CINEVAULT_DB.is_file():
        return {"ok": False, "db_path": str(CINEVAULT_DB), "error": "database file missing"}
    try:
        conn = sqlite3.connect(CINEVAULT_DB)
        conn.row_factory = sqlite3.Row
        counts = {}
        for table in tables:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        latest = conn.execute(
            "SELECT started_at, finished_at, status, message, movies_count, shows_count, episodes_count FROM scan_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "ok": True,
            "db_path": str(CINEVAULT_DB),
            "db_size_bytes": CINEVAULT_DB.stat().st_size,
            "db_modified": CINEVAULT_DB.stat().st_mtime,
            "counts": counts,
            "latest_scan": dict(latest) if latest else None,
        }
    except Exception as exc:
        return {"ok": False, "db_path": str(CINEVAULT_DB), "error": str(exc)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def human_bytes(value: int | float) -> str:
    size = float(value or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def prune_cinevault_db_backups(keep: int = CINEVAULT_BACKUP_KEEP) -> list[Path]:
    backups = sorted(CINEVAULT_BACKUP_DIR.glob("cinevault-db-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[Path] = []
    for path in backups[max(1, keep):]:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            pass
    return removed


def list_cinevault_db_backups(limit: int = 10) -> list[dict]:
    CINEVAULT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = sorted(CINEVAULT_BACKUP_DIR.glob("cinevault-db-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    rows = []
    for path in backups[:limit]:
        stat = path.stat()
        rows.append({"name": path.name, "path": str(path), "size": stat.st_size, "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))})
    return rows


def backup_cinevault_database(reason: str = "manual") -> Path:
    if not CINEVAULT_DB.is_file():
        raise FileNotFoundError(f"CineMediaVault database missing: {CINEVAULT_DB}")
    CINEVAULT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe_reason = re.sub(r"[^a-zA-Z0-9_-]+", "-", reason.strip().lower() or "manual").strip("-")[:32] or "manual"
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    destination = CINEVAULT_BACKUP_DIR / f"cinevault-db-{timestamp}-{safe_reason}.db"
    source = sqlite3.connect(f"file:{CINEVAULT_DB}mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if str(result).lower() != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
        target.commit()
    finally:
        target.close()
        source.close()
    prune_cinevault_db_backups()
    return destination


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def configure_environment() -> None:
    os.environ.setdefault("MOVIE_ROOT", MOVIE_ROOT)
    os.environ.setdefault("MOVIE_LIVE_CACHE", str(MOVIE_APP_DIR / "movie-live-index.json"))
    os.environ.setdefault("MOVIE_POSTER_MAP", str(MOVIE_APP_DIR / "poster-map.json"))
    os.environ.setdefault("MOVIE_POSTER_DIR", str(MOVIE_APP_DIR / "posters"))
    os.environ.setdefault("MOVIE_METADATA_MAP", str(MOVIE_APP_DIR / "movie-metadata-map.json"))
    os.environ.setdefault("TV_ROOT", TV_ROOT)
    os.environ.setdefault("TV_LIVE_CACHE", str(TV_APP_DIR / "tv-live-index.json"))
    os.environ.setdefault("TV_POSTER_MAP", str(TV_APP_DIR / "tv-poster-map.json"))
    os.environ.setdefault("TV_POSTER_DIR", str(TV_APP_DIR / "posters"))
    os.environ.setdefault("TV_METADATA_MAP", str(TV_APP_DIR / "tv-metadata-map.json"))


configure_environment()
movie_app = load_module("movie_download_app", MOVIE_APP_DIR / "media_download_server.py")
tv_app = load_module("tv_download_app", TV_APP_DIR / "tv_download_server.py")


def reload_media_state() -> dict:
    try:
        movie_app.movie_index.load_csv_bootstrap()
    except Exception as exc:
        print(f"Movie cache reload failed, rebuilding: {exc}", flush=True)
        movie_app.movie_index.items = []
    if not movie_app.movie_index.items:
        movie_app.movie_index.refresh()
    try:
        tv_app.tv_index.load_cache()
    except Exception as exc:
        print(f"TV cache reload failed, rebuilding: {exc}", flush=True)
        tv_app.tv_index.shows = []
        tv_app.tv_index.episode_by_id = {}
    if not tv_app.tv_index.shows:
        tv_app.tv_index.refresh()
    movie_app.load_poster_map()
    movie_app.load_metadata_map()
    tv_app.load_poster_map()
    tv_app.load_metadata_map()
    migrate_legacy_movie_state_keys()
    rebuild_actor_index()
    return {
        "movies": len(movie_app.movie_index.items),
        "shows": len(tv_app.tv_index.shows),
        "episodes": len(tv_app.tv_index.episode_by_id),
        "movie_posters": len(movie_app.poster_map),
        "tv_posters": len(tv_app.poster_map),
    }


def watch_full_scan(process, out_path: Path, err_path: Path) -> None:
    global MEDIA_SCAN_LAST_RESULT
    return_code = process.wait()
    payload = {
        "running": False,
        "pid": process.pid,
        "returncode": return_code,
        "stdout": str(out_path),
        "stderr": str(err_path),
        "finished_at": time.time(),
    }
    if return_code == 0:
        try:
            payload.update(reload_media_state())
            payload["reloaded"] = True
        except Exception as exc:
            payload["reloaded"] = False
            payload["reload_error"] = str(exc)
    with MEDIA_SCAN_LOCK:
        MEDIA_SCAN_LAST_RESULT = payload
    final = dict(load_scan_progress())
    final.update(payload)
    final.update({"percent": 100 if return_code == 0 else final.get("percent", 0),
                  "phase": "Complete" if return_code == 0 else "Scan error",
                  "message": "Library scan completed" if return_code == 0 else "Library scan failed"})
    save_scan_progress(final)


PLAYER_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{TITLE}}</title>
  <style>
    :root { color-scheme:dark; --bg:#05070b; --text:#f7fbff; --muted:#a8b2bf; --green:#2ee66b; --line:rgba(255,255,255,.16); }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font-family:Arial, Helvetica, sans-serif; }
    .page { min-height:100vh; display:grid; grid-template-rows:auto 1fr; }
    header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:14px 18px; border-bottom:1px solid var(--line); background:#0b1017; }
    .back { color:#fff; text-decoration:none; font-weight:900; }
    .title { min-width:0; font-weight:900; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .status { color:var(--muted); font-size:13px; white-space:nowrap; }
    main { display:grid; place-items:center; padding:14px; }
    video { width:min(100%, 1280px); max-height:calc(100vh - 92px); background:#000; border:1px solid var(--line); border-radius:8px; box-shadow:0 28px 80px rgba(0,0,0,.52); }
    .message { width:min(900px, 100%); margin-top:12px; color:#c9d2df; line-height:1.4; font-size:14px; }
    .message strong { color:var(--green); }
    @media (max-width:640px) {
      header { padding:12px; align-items:flex-start; flex-direction:column; }
      .status { white-space:normal; }
      main { padding:8px; align-content:start; }
      video { max-height:70vh; border-radius:6px; }
    }
  </style>
</head>
<body>
  <div class="page">
    <header>
      <a class="back" href="{{BACK}}">&larr; Back</a>
      <div class="title">{{TITLE}}</div>
      <div class="status">Transcoded H.264/AAC playback</div>
    </header>
    <div class="home-search-panel" id="homeSearchPanel"><input id="homeSearch" type="search" placeholder="Search movies, shows, actors, or genres"></div>
  <main>
      <div>
        <video id="player" controls autoplay playsinline></video>
        <div class="message" id="message"><strong>Starting stream.</strong> If this is the first play, CineMediaVault is creating HLS segments now.</div>
      </div>
    </main>
  </div>
  <script src="/assets/hls.min.js"></script>
  <script>
    const video = document.getElementById("player");
    const message = document.getElementById("message");
    const source = "{{PLAYLIST}}";
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = source;
    } else if (window.Hls && Hls.isSupported()) {
      const hls = new Hls({ lowLatencyMode: false, backBufferLength: 90, startPosition: 0 });
      hls.loadSource(source);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        try { video.currentTime = 0; } catch (_) {}
        message.textContent = "Stream ready.";
        video.play().catch(() => {});
      });
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) message.textContent = "Playback hit a fatal HLS error. Try refreshing after a few seconds.";
      });
    } else {
      message.textContent = "This browser cannot play HLS directly and hls.js was not available.";
    }
  </script>
</body>
</html>
"""


LANDING_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CineMediaVault</title>
  <style>
    :root { color-scheme: dark; --bg:#080a12; --panel:rgba(255,255,255,.10); --line:rgba(255,255,255,.18); --text:#f8fbff; --muted:#d7deea; --accent:#d9ff4a; --blue:#3fa2ff; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; font-family:Arial, Helvetica, sans-serif; background:var(--bg); color:var(--text); overflow-x:hidden; }
    .hero { position:relative; min-height:100vh; overflow:hidden; display:flex; flex-direction:column; }
    .hero::before { content:""; position:absolute; inset:0; background:
      radial-gradient(circle at 78% 18%, rgba(82,135,255,.54), transparent 34%),
      radial-gradient(circle at 18% 22%, rgba(161,55,210,.58), transparent 40%),
      linear-gradient(110deg, rgba(8,10,18,.98) 0%, rgba(38,20,88,.92) 39%, rgba(0,118,205,.84) 100%);
      z-index:0;
    }
    .hero::after { content:""; position:absolute; inset:0; background:linear-gradient(90deg, rgba(8,10,18,.82) 0%, rgba(8,10,18,.62) 38%, rgba(8,10,18,.18) 74%, rgba(8,10,18,.48) 100%); z-index:2; }
    .nav { position:relative; z-index:4; display:grid; grid-template-columns:auto minmax(220px,420px) auto; align-items:center; gap:28px; padding:22px 30px; }
    .brand-wrap { min-width:max-content; }
    .brand { font-size:30px; font-weight:900; letter-spacing:0; line-height:1; }
    .cmv-logo { display:inline-flex; align-items:center; gap:.34em; color:#fff; text-decoration:none; text-transform:uppercase; letter-spacing:0; line-height:.88; }
    .cmv-left { display:grid; gap:.08em; align-items:center; }
    .cmv-cine,.cmv-media { font-size:.54em; font-weight:950; letter-spacing:.03em; line-height:.86; }
    .cmv-divider { width:.055em; height:1.16em; background:rgba(255,255,255,.72); }
    .cmv-vault { color:var(--accent); font-size:.98em; font-weight:950; letter-spacing:.02em; }
    .cmv-mark { width:1.2em; height:1.2em; color:var(--accent); flex:0 0 auto; }
    .brand-credit { margin-top:4px; color:rgba(238,244,255,.72); font-size:11px; font-weight:700; }
    .search { width:100%; height:38px; border-radius:999px; border:1px solid var(--line); background:rgba(255,255,255,.12); color:#fff; padding:0 16px; font-size:15px; }
    .nav-links { display:flex; justify-content:flex-end; gap:24px; color:#edf3ff; font-weight:800; font-size:14px; white-space:nowrap; }
    .nav-links a { color:#edf3ff; text-decoration:none; }
    .nav-links a:hover { color:var(--accent); }
    .poster-cloud { position:absolute; left:-120px; right:-120px; top:42px; width:auto; display:grid; grid-template-columns:repeat(14, minmax(62px, 1fr)); gap:10px; transform:rotate(10deg); z-index:1; opacity:.88; }
    .poster-cloud img { width:100%; aspect-ratio:2/3; object-fit:cover; border-radius:9px; box-shadow:0 20px 42px rgba(0,0,0,.45); filter:saturate(1.08) contrast(1.05); }
    .poster-cloud img:nth-child(3n) { transform:translateY(26px); }
    .poster-cloud img:nth-child(4n) { transform:translateY(-14px); }
    .poster-cloud img:nth-child(5n) { transform:translateY(46px); }
    main { position:relative; z-index:3; width:min(1180px,100%); padding:70px 30px 36px; flex:1; display:flex; flex-direction:column; justify-content:center; }
    h1 { margin:0; font-size:clamp(22px,3.5vw,43px); line-height:1.02; letter-spacing:0; max-width:460px; }
    .tagline { max-width:460px; margin:14px 0 24px; color:#eef4ff; font-size:clamp(12px,1.2vw,15px); line-height:1.32; font-weight:800; }
    .actions { display:flex; flex-wrap:wrap; gap:9px; align-items:center; margin-bottom:22px; }
    .primary { display:inline-flex; align-items:center; gap:6px; min-height:34px; padding:0 12px; border-radius:999px; background:var(--accent); color:#10131a; text-decoration:none; font-weight:900; font-size:13px; }
    .secondary { display:inline-flex; align-items:center; min-height:34px; padding:0 12px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid var(--line); color:#fff; text-decoration:none; font-weight:900; font-size:13px; }
    .stats { display:flex; flex-wrap:wrap; gap:7px; }
    .pill { display:inline-flex; align-items:center; gap:5px; min-height:28px; padding:0 10px; border-radius:999px; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.15); color:#fff; font-weight:800; font-size:12px; }
    .pill span { color:#dfe8f7; font-weight:700; }
    .mobile-nav { display:none; }
    @media (max-width:1040px){
      .nav { grid-template-columns:auto 1fr; }
      .search { max-width:none; }
      .nav-links { display:none; }
      .mobile-nav { display:flex; flex-wrap:wrap; gap:7px; margin:0 0 18px; }
      .mobile-nav a { min-height:28px; display:inline-flex; align-items:center; padding:0 10px; border-radius:999px; color:#fff; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.16); text-decoration:none; font-weight:800; font-size:11px; }
      .poster-cloud { opacity:.54; left:-210px; right:-240px; top:86px; grid-template-columns:repeat(11, minmax(60px, 1fr)); }
      main { padding-top:44px; }
    }
    @media (max-width:620px){
      .hero { min-height:100svh; }
      .hero::after { background:linear-gradient(180deg, rgba(8,10,18,.28) 0%, rgba(8,10,18,.78) 34%, rgba(8,10,18,.96) 100%); }
      .nav { grid-template-columns:1fr; gap:12px; padding:16px; }
      .brand { font-size:22px; }
      .cmv-mark { width:.82em; height:.82em; }
      .brand-credit { font-size:10px; }
      .search { display:none; }
      main { padding:18px 16px 24px; justify-content:flex-end; min-height:calc(100svh - 88px); }
      .poster-cloud { left:-230px; right:-300px; top:48px; gap:8px; opacity:.48; grid-template-columns:repeat(9, minmax(58px, 1fr)); transform:rotate(10deg) scale(.98); }
      h1 { font-size:clamp(24px,7vw,32px); max-width:300px; }
      .tagline { margin:10px 0 16px; font-size:12px; line-height:1.32; font-weight:800; max-width:300px; }
      .actions { align-items:stretch; gap:7px; margin-bottom:12px; }
      .primary,.secondary { width:100%; justify-content:center; }
      .stats { gap:8px; }
      .pill { min-height:26px; padding:0 9px; font-size:11px; }
    }
    @media (max-width:380px){
      .mobile-nav a { flex:1 1 calc(50% - 8px); justify-content:center; }
      .poster-cloud { right:-320px; }
    }
  </style>
</head>
<body>
  <section class="hero">
    <nav class="nav">
      <div class="brand-wrap"><div class="brand cmv-logo"><span class="cmv-left"><span class="cmv-cine">Cine</span><span class="cmv-media">Media</span></span><span class="cmv-divider"></span><span class="cmv-vault">Vault</span><svg class="cmv-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="32" cy="32" r="9" fill="none" stroke="currentColor" stroke-width="4"/><path d="M32 6v17M32 41v17M6 32h17M41 32h17M13.6 13.6l12 12M38.4 38.4l12 12M50.4 13.6l-12 12M25.6 38.4l-12 12" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="32" cy="32" r="3" fill="currentColor"/><path d="M32 32l5 4M32 32l-5 4M32 32v-6" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg></div><div class="brand-credit">Brought to you by Jonathan Nicolas</div></div>
      <input class="search" type="search" placeholder="Search from Movies or TV pages">
      <div class="nav-links"><a href="/movies">Movies</a><a href="/tv">TV Shows</a><a href="/movies">Play</a><a href="/movies">Downloads</a></div>
    </nav>
    <div class="poster-cloud" aria-hidden="true">{{POSTER_CLOUD}}</div>
    <main>
      <div class="mobile-nav"><a href="/movies">Movies</a><a href="/tv">TV Shows</a><a href="/movies">Play</a><a href="/movies">Downloads</a></div>
      <h1>Your private cinema library.</h1>
      <div class="tagline">Browse your movie vault and TV archive from one local server with posters, playback, downloads, and summary details.</div>
      <div class="actions">
        <a class="primary" href="/movies">Browse Movies</a>
        <a class="secondary" href="/tv">Browse TV Shows</a>
      </div>
      <div class="stats">
        <div class="pill">{{MOVIE_COUNT}} <span>movies</span></div>
        <div class="pill">{{TV_COUNT}} <span>shows</span></div>
        <div class="pill">{{EPISODE_COUNT}} <span>episodes</span></div>
      </div>
    </main>
  </section>
</body>
</html>
"""


HOME_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CineMediaVault</title>
  <style>
    :root { color-scheme:dark; --bg:#0b0b0d; --panel:#171719; --text:#f7f7f8; --muted:#a7a7ad; --gold:#f5b73f; --line:rgba(255,255,255,.10); }
    * { box-sizing:border-box; }
    html, body { width:100%; max-width:100%; overflow-x:hidden; }
    body { margin:0; min-height:100vh; background:var(--bg); color:var(--text); font-family:Arial, Helvetica, sans-serif; padding-bottom:0; position:relative; }
    body::before { content:""; position:fixed; inset:-70px; z-index:-2; background:radial-gradient(circle at 18% 10%,rgba(88,166,255,.22),transparent 28%),linear-gradient(135deg,#050506,#111217); }
    .home-backdrop { position:fixed; inset:-90px -120px auto -120px; height:calc(100vh + 190px); z-index:0; display:grid; grid-template-columns:repeat(15, minmax(58px, 1fr)); gap:10px; transform:rotate(-10deg) translateY(-34px); opacity:.42; pointer-events:none; overflow:hidden; filter:saturate(1.12) contrast(1.04); }
    .home-backdrop::after { content:""; position:absolute; inset:-20px; background:linear-gradient(180deg,rgba(0,0,0,.10) 0%,rgba(5,5,6,.46) 58%,rgba(11,11,13,.92) 100%),linear-gradient(90deg,rgba(5,5,6,.66),rgba(5,5,6,.12),rgba(5,5,6,.70)); }
    .home-backdrop img { width:100%; aspect-ratio:2/3; object-fit:cover; border-radius:10px; box-shadow:0 18px 36px rgba(0,0,0,.55); }
    .home-backdrop img:nth-child(3n) { transform:translateY(34px); }
    .home-backdrop img:nth-child(4n) { transform:translateY(-22px); }
    .home-backdrop img:nth-child(5n) { transform:translateY(56px); }
    header { position:sticky; top:0; z-index:5; width:100%; max-width:100vw; min-width:0; display:flex; align-items:center; justify-content:space-between; gap:18px; padding:22px 24px 14px; background:linear-gradient(180deg,#050506 0%,rgba(5,5,6,.92) 74%,rgba(5,5,6,0) 100%); }
    .brand { font-size:34px; font-weight:900; letter-spacing:0; }
    .cmv-logo { display:inline-flex; align-items:center; gap:.30em; color:#fff; text-decoration:none; text-transform:uppercase; line-height:.88; white-space:nowrap; }
    .cmv-left { display:grid; gap:.08em; align-items:center; }
    .cmv-cine,.cmv-media { font-size:.54em; font-weight:950; letter-spacing:.03em; line-height:.86; }
    .cmv-divider { width:.052em; height:1.12em; background:rgba(255,255,255,.66); }
    .cmv-vault { color:var(--gold); font-size:.95em; font-weight:950; letter-spacing:.02em; }
    .cmv-mark { width:1.08em; height:1.08em; color:var(--gold); flex:0 0 auto; }
    .top-actions { min-width:0; display:flex; align-items:center; gap:18px; color:#fff; }
    .home-search-panel { display:none; position:sticky; top:82px; z-index:4; padding:0 24px 14px; background:linear-gradient(180deg,rgba(5,5,6,.92),rgba(5,5,6,0)); }
    .home-search-panel.open { display:block; }
    .home-search-panel input { width:min(720px,100%); height:44px; border-radius:14px; border:1px solid rgba(88,166,255,.62); background:#20252d; color:#fff; padding:0 16px; font-size:17px; outline:none; box-shadow:0 0 0 3px rgba(47,157,255,.22); }
    .icon { width:36px; height:36px; display:grid; place-items:center; border-radius:999px; color:#fff; text-decoration:none; font-size:22px; background:transparent; border:0; cursor:pointer; }
    .playback-toggle { min-height:34px; min-width:72px; display:inline-flex; align-items:center; justify-content:center; padding:0 12px; border-radius:999px; border:1px solid rgba(255,255,255,.18); background:rgba(255,255,255,.08); color:#fff; font-size:12px; font-weight:900; cursor:pointer; }
    .playback-toggle.hls { color:#06111c; background:#f5b73f; border-color:#f5b73f; }
    .cast-button svg { width:23px; height:23px; display:block; }
    .cast-fallback { position:fixed; inset:0; z-index:30; display:none; align-items:end; background:rgba(0,0,0,.62); }
    .cast-fallback.open { display:flex; }
    .cast-sheet { width:100%; max-width:560px; margin:0 auto; border-radius:22px 22px 0 0; background:#050506; color:#fff; padding:22px 24px 28px; box-shadow:0 -20px 70px rgba(0,0,0,.55); position:relative; }
    .cast-sheet h2 { margin:8px 0 18px; text-align:center; font-size:22px; }
    .cast-sheet p { color:#d7dbe2; font-size:15px; line-height:1.35; }
    .cast-note { color:#a7a7ad !important; }
    .cast-close { position:absolute; top:14px; right:16px; width:36px; height:36px; border:0; border-radius:999px; background:transparent; color:#fff; font-size:30px; line-height:1; cursor:pointer; }
    .cast-list { display:grid; gap:8px; margin:8px 0 14px; }
    .cast-device { width:100%; display:grid; grid-template-columns:36px 1fr auto; gap:12px; align-items:center; border:0; border-radius:12px; background:rgba(255,255,255,.08); color:#fff; padding:12px; text-align:left; }
    .cast-device[disabled] { opacity:.55; cursor:not-allowed; }
    .cast-device-icon { font-size:22px; }
    .cast-device-name { font-weight:900; font-size:16px; }
    .cast-device-meta { color:#a7a7ad; font-size:12px; margin-top:2px; }
    .cast-device-action { color:#f5b73f; font-weight:900; font-size:12px; }
    .brand-roku { color:#6f1ab1; font-weight:900; font-size:12px; letter-spacing:0; }
    .brand-samsung { color:#5d8dff; font-weight:900; font-size:8px; letter-spacing:.2px; }
    .brand-google-cast { width:22px; height:22px; display:grid; place-items:center; border-radius:5px; border:2px solid #6aa8ff; color:#6aa8ff; font-weight:900; font-size:14px; }
    .brand-device { color:#d8dee9; font-weight:900; font-size:11px; }
    .scan-button,.home-search-button { min-height:38px; display:inline-flex; align-items:center; justify-content:center; padding:0 14px; border-radius:999px; background:rgba(245,183,63,.18); border:1px solid rgba(245,183,63,.42); color:#fff; font-weight:900; cursor:pointer; }
    .home-search-button { background:rgba(255,255,255,.08); border-color:rgba(255,255,255,.18); gap:7px; }
    .home-search-button svg { width:15px; height:15px; display:block; }
    .scan-button.running { opacity:.7; cursor:wait; }
    .scan-progress { display:none; position:relative; z-index:2; margin:0 24px 16px; }
    .scan-progress.visible { display:block; }
    .scan-progress-row { display:flex; justify-content:space-between; gap:12px; color:#d9dde5; font-size:12px; margin-bottom:6px; }
    .scan-progress-track { height:7px; overflow:hidden; border-radius:999px; background:rgba(255,255,255,.14); }
    .scan-progress-fill { width:0; height:100%; border-radius:999px; background:var(--gold); transition:width .35s ease; }
    .account-wrap { position:relative; }
    .avatar { width:29px; height:29px; display:grid; place-items:center; border-radius:999px; background:#75a9d9; color:#fff; font-weight:900; font-size:14px; position:relative; text-decoration:none; border:0; cursor:pointer; }
    .avatar .badge { position:absolute; top:-6px; right:-6px; min-width:16px; height:16px; padding:0 4px; display:grid; place-items:center; border-radius:999px; background:#e83f4f; color:#fff; font-size:10px; line-height:1; border:2px solid #08090c; }
    .account-menu { position:absolute; right:0; top:calc(100% + 10px); min-width:180px; display:none; border:1px solid var(--line); border-radius:14px; background:rgba(12,14,20,.98); box-shadow:0 18px 45px rgba(0,0,0,.52); padding:8px; }
    .account-menu.open { display:grid; gap:4px; }
    .account-menu a { color:#fff; text-decoration:none; border-radius:10px; padding:10px 11px; font-size:13px; font-weight:850; }
    .account-menu a:hover { background:rgba(255,255,255,.09); }
    main { position:relative; z-index:1; width:100%; max-width:100vw; min-width:0; padding:0 0 18px; overflow:hidden; }
    .tabs { display:flex; gap:10px; padding:0 24px 22px; overflow-x:auto; scrollbar-width:none; }
    .tabs::-webkit-scrollbar { display:none; }
    .tab { flex:0 0 auto; display:inline-flex; align-items:center; justify-content:center; min-height:42px; padding:0 17px; border-radius:14px; border:1px solid rgba(255,255,255,.18); color:#eee; text-decoration:none; font-size:17px; font-weight:800; background:rgba(255,255,255,.06); }
    .tab.active { background:rgba(255,255,255,.07); }
    .tab.external { color:#f1f5ff; }
    .tab .module-logo { display:inline-flex; align-items:center; justify-content:center; min-width:26px; height:26px; margin-right:8px; border-radius:7px; background:rgba(255,255,255,.12); font-size:9px; font-weight:950; overflow:hidden; }
    .module-logo-img { width:100%; height:100%; object-fit:contain; display:block; }
    .game-logo { display:inline-flex; align-items:center; justify-content:center; line-height:1; letter-spacing:0; }
    .logo-comics { min-width:42px; color:#fff; background:linear-gradient(135deg,#e3342f,#f5b73f); font-size:10px; font-family:Impact,Arial Black,Arial,sans-serif; text-shadow:1px 1px 0 #111; }
    .logo-nes { min-width:58px; min-height:22px; padding:0 7px; border:2px solid #e60012; border-radius:999px; color:#e60012; background:#fff; font-size:14px; font-weight:900; font-family:Arial Black, Arial, Helvetica, sans-serif; }
    .logo-sega { color:#0877d8; font-size:21px; font-weight:900; font-family:Arial Black, Arial, Helvetica, sans-serif; text-shadow:1px 0 #fff,-1px 0 #fff,0 1px #fff,0 -1px #fff; }
    .logo-dos { color:#79ff8c; font-size:17px; font-weight:900; font-family:Consolas, monospace; }
    .logo-mame { color:#ffcf2e; font-size:19px; font-weight:900; font-family:Impact, Arial Black, Arial, sans-serif; text-shadow:2px 2px 0 #236bff; }
    .section { margin:0 0 34px; }
    .section.hidden { display:none; }
    .section-head { display:flex; align-items:end; justify-content:space-between; gap:12px; padding:0 24px 14px; }
    h2 { margin:0; font-size:26px; line-height:1.05; letter-spacing:0; }
    .section-head a { color:#fff; text-decoration:none; font-size:24px; }
    .sub { color:var(--muted); font-size:15px; margin-top:4px; }
    .rail { display:flex; gap:14px; overflow-x:auto; padding:0 24px 6px; scroll-snap-type:x proximity; scrollbar-width:none; }
    .rail::-webkit-scrollbar { display:none; }
    .card { flex:0 0 138px; min-width:0; color:#fff; text-decoration:none; scroll-snap-align:start; }
    .poster { position:relative; width:100%; aspect-ratio:2/3; border-radius:8px; overflow:hidden; background:#16181d; box-shadow:0 12px 26px rgba(0,0,0,.42); }
    .poster img { width:100%; height:100%; object-fit:cover; display:block; }
    .poster.missing { display:grid; place-items:center; color:#818793; font-weight:900; border:1px solid var(--line); }
    .card.watched .poster { outline:3px solid #30d158; outline-offset:2px; }
    .watched-badge { position:absolute; left:8px; top:8px; z-index:2; border-radius:999px; padding:3px 7px; background:rgba(48,209,88,.92); color:#031007; font-size:10px; font-weight:950; text-transform:uppercase; letter-spacing:.02em; }
    .progress { position:absolute; left:8px; right:8px; bottom:8px; height:6px; border-radius:999px; background:rgba(0,0,0,.72); overflow:hidden; }
    .progress span { display:block; height:100%; width:0%; background:var(--gold); border-radius:999px; }
    .card-title { margin-top:10px; font-size:19px; line-height:1.15; font-weight:800; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
    .card-meta { color:var(--muted); font-size:16px; margin-top:4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .libraries { display:flex; gap:16px; overflow-x:auto; padding:0 24px 4px; scrollbar-width:none; }
    .library { flex:0 0 272px; display:flex; align-items:center; gap:14px; min-height:66px; padding:0 19px; border-radius:30px; background:#1c1c1f; color:#fff; text-decoration:none; }
    .library-icon { width:34px; height:34px; display:grid; place-items:center; color:#f4f6f8; flex:0 0 34px; }
    .library-svg, .library-svg svg { width:100%; height:100%; display:block; }
    .library-title { font-size:19px; font-weight:900; }
    .library-sub { color:#c5c5c9; font-size:14px; margin-top:2px; }
    .empty { margin:0 24px; color:var(--muted); border:1px dashed rgba(255,255,255,.18); border-radius:12px; padding:18px; }
    .bottom-nav { position:fixed; left:0; right:0; bottom:0; z-index:8; height:72px; display:none; grid-template-columns:repeat(3,1fr); border-top:1px solid var(--line); background:rgba(6,6,7,.96); backdrop-filter:blur(10px); }
    .bottom-nav a { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; color:#b7b7bc; text-decoration:none; font-size:12px; }
    .bottom-nav a.active { color:var(--gold); }
    .bottom-nav span { font-size:24px; line-height:1; }
    .bottom-nav .library-svg { width:24px; height:24px; display:block; }
    .bottom-nav .library-svg svg { width:24px; height:24px; display:block; }
    @media (min-width: 900px) and (hover: hover) and (pointer: fine) {
      body { padding-bottom:0; }
      .bottom-nav { display:none; }
      header { gap:12px; padding:16px 18px 10px; }
      .brand { font-size:24px; }
      .top-actions { gap:14px; }
      .home-search-panel { top:56px; padding:0 18px 10px; }
      .home-search-panel input { height:40px; border-radius:12px; font-size:15px; }
      .scan-button,.home-search-button { min-height:36px; padding:0 13px; font-size:12px; }
      .playback-toggle { min-height:34px; min-width:72px; font-size:12px; }
      .icon { width:36px; height:36px; font-size:18px; }
      .cast-button svg { width:23px; height:23px; }
      .avatar { width:36px; height:36px; font-size:16px; }
      .tabs { gap:10px; padding:0 18px 16px; }
      .tab { flex:0 0 auto; min-height:38px; padding:0 15px; border-radius:13px; font-size:14px; }
      .logo-nes { min-width:37px; min-height:14px; padding:0 4px; font-size:6px; border-width:1px; }
      .logo-sega { font-size:9px; }
      .logo-dos { font-size:7px; }
      .logo-mame { font-size:8px; text-shadow:1px 1px 0 #236bff; }
      .section { margin-bottom:24px; }
      .section-head { padding:0 18px 10px; }
      h2 { font-size:22px; }
      .section-head a { font-size:20px; }
      .sub { font-size:13px; margin-top:3px; }
      .rail { gap:14px; padding:0 18px 5px; overflow-x:auto; overflow-y:hidden; }
      .card { flex:0 0 236px; width:236px; }
      .poster { border-radius:6px; box-shadow:0 8px 18px rgba(0,0,0,.38); }
      .progress { left:6px; right:6px; bottom:6px; height:4px; }
      .card-title { margin-top:8px; font-size:14px; line-height:1.15; }
      .card-meta { margin-top:3px; font-size:12px; }
      .libraries { gap:14px; padding:0 18px 4px; overflow-x:auto; overflow-y:hidden; }
      .library { flex:0 0 348px; width:348px; min-height:60px; gap:12px; padding:0 17px; border-radius:26px; }
      .library-icon { width:30px; height:30px; flex-basis:30px; }
      .library-title { font-size:17px; }
      .library-sub { font-size:12px; }
      main { padding-bottom:42px; }
    }
    @media (max-width: 560px) and (hover: none) and (pointer: coarse) {
      body { padding-bottom:78px; }
      .bottom-nav { display:grid; }
      .home-backdrop { height:470px; inset:-58px -190px auto -170px; grid-template-columns:repeat(11, minmax(58px,1fr)); opacity:.40; gap:8px; }
      header { padding:22px 24px 12px; }
      .brand { font-size:23px; }
      .cmv-mark { width:.80em; height:.80em; }
      .cmv-media { letter-spacing:.03em; }
      .top-actions { gap:12px; }
      .playback-toggle { min-height:30px; min-width:54px; padding:0 8px; font-size:10px; }
      .home-search-panel { top:68px; padding:0 24px 12px; }
      .home-search-panel input { height:42px; border-radius:13px; font-size:16px; }
      .icon { width:32px; }
      .tabs { gap:9px; padding-bottom:18px; }
      .tab { min-height:38px; padding:0 14px; font-size:16px; }
      h2 { font-size:25px; }
      .card { flex-basis:138px; }
      .library { flex-basis:274px; }
    }
    @media (max-width: 700px) {
      header { flex-wrap:wrap; align-items:center; gap:10px; padding:18px 18px 10px; }
      header .brand { flex:1 1 100%; min-width:0; }
      .top-actions { flex:1 1 100%; width:100%; min-width:0; gap:7px; justify-content:flex-start; }
      .scan-button,.home-search-button { min-height:34px; padding:0 11px; font-size:12px; flex:0 0 auto; }
      .playback-toggle { flex:0 0 auto; }
      .cast-button { margin-left:auto; flex:0 0 32px; }
      .account-wrap,.top-actions > .avatar { flex:0 0 auto; }
      .home-search-panel { top:104px; padding-left:18px; padding-right:18px; }
    }
  </style>
</head>
<body>
  <div class="home-backdrop" aria-hidden="true">{{HOME_BACKDROP}}</div>
  <header>
    <div class="brand cmv-logo"><span class="cmv-left"><span class="cmv-cine">Cine</span><span class="cmv-media">Media</span></span><span class="cmv-divider"></span><span class="cmv-vault">Vault</span><svg class="cmv-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="32" cy="32" r="9" fill="none" stroke="currentColor" stroke-width="4"/><path d="M32 6v17M32 41v17M6 32h17M41 32h17M13.6 13.6l12 12M38.4 38.4l12 12M50.4 13.6l-12 12M25.6 38.4l-12 12" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="32" cy="32" r="3" fill="currentColor"/><path d="M32 32l5 4M32 32l-5 4M32 32v-6" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg></div>
    <div class="top-actions"><button class="scan-button" id="scanButton">Scan</button><button class="playback-toggle {{GLOBAL_PLAYBACK_MODE_CLASS}}" id="playbackModeButton" type="button" data-mode="{{GLOBAL_PLAYBACK_MODE}}" title="Default playback mode">{{GLOBAL_PLAYBACK_MODE_LABEL}}</button><button class="home-search-button" id="homeSearchToggle" type="button" aria-label="Search" title="Search"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10.8 18.2a7.4 7.4 0 1 1 0-14.8 7.4 7.4 0 0 1 0 14.8Zm5.3-2.1 4.5 4.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg><span>Search</span></button><button class="icon cast-button" id="castButton" type="button" aria-label="Cast" title="Cast"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 10V7.5C5 6.1 6.1 5 7.5 5h17C25.9 5 27 6.1 27 7.5v17c0 1.4-1.1 2.5-2.5 2.5H22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><path d="M5 21c3.3 0 6 2.7 6 6M5 15c6.6 0 12 5.4 12 12M5 27h.1" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg></button><div class="avatar">J</div></div>
  </header>
  <div class="scan-progress" id="scanProgress"><div class="scan-progress-row"><span id="scanProgressMessage">Preparing scan</span><strong id="scanProgressPercent">0%</strong></div><div class="scan-progress-track"><div class="scan-progress-fill" id="scanProgressFill"></div></div></div>
  <div class="home-search-panel" id="homeSearchPanel"><input id="homeSearch" type="search" placeholder="Search movies, shows, actors, or genres"></div>
  <main>
    <nav class="tabs"><a class="tab active" href="/">Home</a><a class="tab" href="/movies">Movies</a><a class="tab" href="/tv">TV Shows</a><a class="tab" href="/music">Music</a><a class="tab" href="/video-lists">My Lists</a><a class="tab" href="/live-tv">Live TV</a><a class="tab" href="/wall">Video Wall</a>{{MODULE_TABS}}</nav>
    <section class="section" id="continueSection">
      <div class="section-head"><div><h2>Continue Watching</h2></div></div>
      <div class="rail" id="continueRail"></div>
    </section>
    <section class="section">
      <div class="section-head"><div><h2>Browse Libraries</h2><div class="sub">{{SERVER_NAME}}</div></div></div>
      <div class="libraries">
        <a class="library" href="/movies"><div class="library-icon"><span class="library-svg film-reel" aria-hidden="true"><svg viewBox="0 0 32 32"><circle cx="16" cy="16" r="11" fill="none" stroke="currentColor" stroke-width="2.4"/><circle cx="16" cy="16" r="2.2" fill="currentColor"/><circle cx="16" cy="8.7" r="2.3" fill="currentColor"/><circle cx="23.3" cy="16" r="2.3" fill="currentColor"/><circle cx="16" cy="23.3" r="2.3" fill="currentColor"/><circle cx="8.7" cy="16" r="2.3" fill="currentColor"/><path d="M25.5 23.5h4.2" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></span></div><div><div class="library-title">Movies</div><div class="library-sub">{{SERVER_NAME}}</div></div></a>
        <a class="library" href="/tv"><div class="library-icon"><span class="library-svg tv-set" aria-hidden="true"><svg viewBox="0 0 32 32"><rect x="5" y="8" width="22" height="15" rx="2.2" fill="none" stroke="currentColor" stroke-width="2.4"/><path d="M12 27h8M16 23v4M11 4l5 4 5-4" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></span></div><div><div class="library-title">TV Shows</div><div class="library-sub">{{SERVER_NAME}}</div></div></a>
        <a class="library" href="/music"><div class="library-icon" aria-hidden="true">&#9835;</div><div><div class="library-title">Music</div><div class="library-sub">Artists, albums, tracks, and playlists</div></div></a>
      </div>
    </section>
    <section class="section">
      <div class="section-head"><div><h2>Recently Added in Movies</h2><div class="sub">{{SERVER_NAME}}</div></div><a href="/movies">&rsaquo;</a></div>
      <div class="rail">{{RECENT_MOVIES}}</div>
    </section>
    <section class="section">
      <div class="section-head"><div><h2>Recently Added in TV Shows</h2><div class="sub">{{SERVER_NAME}}</div></div><a href="/tv">&rsaquo;</a></div>
      <div class="rail">{{RECENT_TV}}</div>
    </section>
    {{RECENT_RELEASED_SECTION}}
  </main>
  <nav class="bottom-nav"><a class="active" href="/"><span>&#8962;</span>Home</a><a href="/movies"><span><span class="library-svg film-reel" aria-hidden="true"><svg viewBox="0 0 32 32"><circle cx="16" cy="16" r="11" fill="none" stroke="currentColor" stroke-width="2.4"/><circle cx="16" cy="16" r="2.2" fill="currentColor"/><circle cx="16" cy="8.7" r="2.3" fill="currentColor"/><circle cx="23.3" cy="16" r="2.3" fill="currentColor"/><circle cx="16" cy="23.3" r="2.3" fill="currentColor"/><circle cx="8.7" cy="16" r="2.3" fill="currentColor"/><path d="M25.5 23.5h4.2" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg></span></span>Movies</a><a href="/tv"><span><span class="library-svg tv-set" aria-hidden="true"><svg viewBox="0 0 32 32"><rect x="5" y="8" width="22" height="15" rx="2.2" fill="none" stroke="currentColor" stroke-width="2.4"/><path d="M12 27h8M16 23v4M11 4l5 4 5-4" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></span></span>TV Shows</a></nav>
  <script>
    const rail = document.getElementById("continueRail");
    const homeSearchToggle = document.getElementById("homeSearchToggle");
    const homeSearchPanel = document.getElementById("homeSearchPanel");
    const homeSearch = document.getElementById("homeSearch");
    const continueSection = document.getElementById("continueSection");
    const scanButton = document.getElementById("scanButton");
    const playbackModeButton = document.getElementById("playbackModeButton");
    const accountButton = document.getElementById("accountButton");
    const accountMenu = document.getElementById("accountMenu");
    if (accountButton && accountMenu) {
      accountButton.addEventListener("click", event => {
        event.stopPropagation();
        accountMenu.classList.toggle("open");
      });
      document.addEventListener("click", event => {
        if (!accountMenu.contains(event.target) && event.target !== accountButton) accountMenu.classList.remove("open");
      });
    }
    function setPlaybackModeButton(mode) {
      if (!playbackModeButton) return;
      const normalized = mode === "hls" ? "hls" : "direct";
      playbackModeButton.dataset.mode = normalized;
      playbackModeButton.textContent = normalized === "hls" ? "HLS" : "Direct";
      playbackModeButton.classList.toggle("hls", normalized === "hls");
    }
    if (playbackModeButton) {
      playbackModeButton.addEventListener("click", async () => {
        const nextMode = playbackModeButton.dataset.mode === "hls" ? "direct" : "hls";
        playbackModeButton.disabled = true;
        try {
          const response = await fetch("/api/playback-mode", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({mode: nextMode})
          });
          const payload = await response.json();
          if (!payload.ok) throw new Error(payload.error || "mode update failed");
          setPlaybackModeButton(payload.mode);
        } catch {
          setPlaybackModeButton(playbackModeButton.dataset.mode || "direct");
        } finally {
          playbackModeButton.disabled = false;
        }
      });
    }
    if (homeSearchToggle && homeSearchPanel && homeSearch) {
      homeSearchToggle.addEventListener("click", () => {
        homeSearchPanel.classList.toggle("open");
        if (homeSearchPanel.classList.contains("open")) homeSearch.focus();
      });
      homeSearch.addEventListener("keydown", event => {
        if (event.key === "Enter" && homeSearch.value.trim()) {
          location.href = `/search?q=${encodeURIComponent(homeSearch.value.trim())}`;
        }
        if (event.key === "Escape") homeSearchPanel.classList.remove("open");
      });
    }
    const castButton = document.getElementById("castButton");
    const CAST_API = `${location.protocol}//${location.hostname}:8120`;
    function mediaSourceForCast() {
      const video = document.getElementById("player");
      const source = video ? video.dataset.source : "";
      return source ? new URL(source, location.origin).href : "";
    }
    function mediaTitleForCast() {
      const title = document.querySelector(".player-title strong, .card-title, h1");
      return title ? title.textContent.trim() : "CineMediaVault";
    }
    function castIconFor(device) {
      const name = `${device.name || ""} ${device.model || ""}`.toLowerCase();
      if (device.type === "chromecast") return `<span class="brand-google-cast">G</span>`;
      if (name.includes("roku")) return `<span class="brand-roku">Roku</span>`;
      if (name.includes("samsung")) return `<span class="brand-samsung">SAMSUNG</span>`;
      return `<span class="brand-device">TV</span>`;
    }
    function ensureCastPanel() {
      let panel = document.getElementById("castFallback");
      if (!panel) {
        panel = document.createElement("div");
        panel.id = "castFallback";
        panel.className = "cast-fallback";
        panel.innerHTML = `<div class="cast-sheet"><button class="cast-close" type="button" aria-label="Close">&times;</button><h2>Connect To</h2><div class="cast-list" id="castDeviceList"></div><p class="cast-note" id="castNote"></p></div>`;
        document.body.appendChild(panel);
        panel.querySelector(".cast-close").addEventListener("click", () => panel.classList.remove("open"));
        panel.addEventListener("click", event => { if (event.target === panel) panel.classList.remove("open"); });
      }
      return panel;
    }
    function setCastPanel(message, devices=[]) {
      const panel = ensureCastPanel();
      const list = panel.querySelector("#castDeviceList");
      const note = panel.querySelector("#castNote");
      list.innerHTML = "";
      const mediaUrl = mediaSourceForCast();
      for (const device of devices) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "cast-device";
        const canSelect = Boolean(device.playable);
        const canPlay = Boolean(device.playable && mediaUrl);
        if (!canSelect) button.disabled = true;
        button.innerHTML = `<span class="cast-device-icon">${castIconFor(device)}</span><span><span class="cast-device-name"></span><span class="cast-device-meta"></span></span><span class="cast-device-action">${canPlay ? "Play" : (canSelect ? "Select" : "Found")}</span>`;
        button.querySelector(".cast-device-name").textContent = device.name || "Unknown device";
        button.querySelector(".cast-device-meta").textContent = `${device.type || "device"}${device.host ? " - " + device.host : ""}${device.model ? " - " + device.model : ""}`;
        if (canPlay) {
          button.addEventListener("click", async () => {
            localStorage.setItem("cinevaultCastDevice", JSON.stringify(device));
            if (!mediaUrl) {
              note.textContent = `${device.name} selected. Open a movie or episode, then press Cast to play it there.`;
              return;
            }
            note.textContent = `Sending ${mediaTitleForCast()} to ${device.name}...`;
            try {
              const response = await fetch(`${CAST_API}/api/cast/play`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({device_id:device.id, media_url:mediaUrl, title:mediaTitleForCast()})});
              const result = await response.json();
              note.textContent = result.ok ? `Playing on ${device.name}.` : `Cast failed: ${result.error || "unknown error"}`;
            } catch (error) {
              note.textContent = `Cast failed: ${error.message || error}`;
            }
          });
        }
        list.appendChild(button);
      }
      if (!devices.length) list.innerHTML = `<p>No cast devices found.</p>`;
      note.textContent = message;
      panel.classList.add("open");
    }
    async function openCastPicker() {
      setCastPanel("Searching local network from the CineMediaVault server...");
      try {
        const response = await fetch(`${CAST_API}/api/cast/devices`, {cache:"no-store"});
        const result = await response.json();
        const message = result.errors && result.errors.length ? result.errors.join("; ") : "Device discovery completed on the server.";
        setCastPanel(message, result.devices || []);
      } catch (error) {
        setCastPanel(`Could not reach cast controller on ${CAST_API}: ${error.message || error}`);
      }
    }
    if (castButton) castButton.addEventListener("click", openCastPicker);
    function esc(value) {
      return String(value || "").replace(/[&<>"']/g, ch => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[ch]));
    }
    let validatedContinueKeys = null;
    function rawContinueItems() {
      try {
        const data = JSON.parse(localStorage.getItem("cinevaultContinue") || "[]");
        return Array.isArray(data) ? data : [];
      } catch (_) {
        return [];
      }
    }
    function validProgressItem(item) {
      if (!item || !item.key || !item.href || !item.title) return false;
      const progress = Number(item.progress || 0);
      const position = Number(item.position || 0);
      const duration = Number(item.duration || 0);
      if (duration && position >= duration * 0.92) return false;
      return progress >= 0.03 || position >= 10;
    }
    function continueItems() {
      const data = rawContinueItems();
      let cleaned = data.filter(validProgressItem);
      if (validatedContinueKeys) {
        cleaned = cleaned.filter(item => validatedContinueKeys.has(item.key));
      }
      if (cleaned.length !== data.length) localStorage.setItem("cinevaultContinue", JSON.stringify(cleaned));
      return cleaned.sort((a,b) => (b.updatedAt || 0) - (a.updatedAt || 0)).slice(0, 20);
    }
    function renderContinue() {
      const items = continueItems();
      continueSection.classList.toggle("hidden", items.length === 0);
      rail.innerHTML = items.map(item => {
        const pct = Math.max(2, Math.min(100, Number(item.progress || 0) * 100));
        const poster = item.poster ? `<img src="${esc(item.poster)}" alt="">` : "";
        return `<a class="card" href="${esc(item.href)}"><div class="poster ${poster ? "" : "missing"}">${poster || "No Poster"}<div class="progress"><span style="width:${pct}%"></span></div></div><div class="card-title">${esc(item.title)}</div><div class="card-meta">${esc(item.subtitle || "")}</div></a>`;
      }).join("");
    }
    continueSection.classList.add("hidden");
    window.addEventListener("storage", event => {
      if (event.key === "cinevaultContinue") refreshContinueMetadata();
    });
    async function refreshContinueMetadata() {
      try {
        const response = await fetch(`/api/watch/continue`, { cache: "no-store" });
        const payload = await response.json();
        const items = Array.isArray(payload.items) ? payload.items : [];
        validatedContinueKeys = new Set(items.map(item => item.key));
        localStorage.setItem("cinevaultContinue", JSON.stringify(items));
        renderContinue();
      } catch (_) {
        validatedContinueKeys = null;
        renderContinue();
      }
    }
    refreshContinueMetadata();
    let scanObservedRunning = false;
    async function pollScanStatus() {
      try {
        const response = await fetch("/api/full-scan-status", { cache: "no-store" });
        const payload = await response.json();
        updateScanProgress(payload);
        if (payload.running) {
          scanObservedRunning = true;
          setTimeout(pollScanStatus, 2000);
          return;
        }
        scanButton.classList.remove("running");
        if (payload.returncode === 0 && payload.reloaded) {
          scanButton.textContent = "Updated";
          const completionId = String(payload.started_at || payload.finished_at || payload.pid || "complete");
          const reloadKey = "cmvLastCompletedScanReload";
          if (scanObservedRunning && sessionStorage.getItem(reloadKey) !== completionId) {
            sessionStorage.setItem(reloadKey, completionId);
            setTimeout(() => location.reload(), 900);
          } else {
            scanButton.textContent = "Scan";
          }
        } else if (payload.returncode === 0) {
          scanButton.textContent = "Scan Done";
        } else if (payload.returncode === undefined || payload.returncode === null) {
          scanButton.textContent = "Scan";
        } else {
          scanButton.textContent = "Scan Error";
        }
      } catch (_) {
        scanButton.textContent = "Scan Error";
        scanButton.classList.remove("running");
      }
    }
    function updateScanProgress(payload) {
      const box = document.getElementById("scanProgress");
      const fill = document.getElementById("scanProgressFill");
      const label = document.getElementById("scanProgressMessage");
      const value = document.getElementById("scanProgressPercent");
      const percent = Math.max(0, Math.min(100, Number(payload.percent || 0)));
      if (payload.running || scanObservedRunning) box.classList.add("visible");
      else box.classList.remove("visible");
      fill.style.width = `${percent}%`;
      value.textContent = `${Math.round(percent)}%`;
      label.textContent = payload.message || payload.phase || "Scanning library";
      scanButton.textContent = payload.running ? `Scan ${Math.round(percent)}%` : "Scan";
      scanButton.classList.toggle("running", !!payload.running);
      if (!payload.running && scanObservedRunning && percent >= 100) {
        setTimeout(() => box.classList.remove("visible"), 1800);
      }
    }
    scanButton.addEventListener("click", async () => {
      scanButton.classList.add("running");
      scanButton.textContent = "Scanning";
      try {
        const response = await fetch("/api/full-scan", { cache: "no-store" });
        const payload = await response.json();
        if (!payload.ok) {
          scanButton.textContent = "Scan Error";
          scanButton.classList.remove("running");
          return;
        }
        if (payload.running) scanObservedRunning = true;
        scanButton.textContent = payload.running ? "Scanning" : "Scan Started";
        setTimeout(pollScanStatus, 1500);
      } catch (_) {
        scanButton.textContent = "Scan Error";
        scanButton.classList.remove("running");
      }
    });
    pollScanStatus();
  </script>
</body>
</html>
"""


DIRECT_PLAYER_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{TITLE}}</title>
  <style>
    :root { color-scheme:dark; --bg:#050506; --text:#fff; --muted:#b7bac2; --line:rgba(255,255,255,.13); }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:#000; color:var(--text); font-family:Arial, Helvetica, sans-serif; }
    .page { min-height:100vh; display:grid; grid-template-rows:auto 1fr; }
    header { display:flex; align-items:center; gap:14px; padding:12px 16px; background:rgba(0,0,0,.78); border-bottom:1px solid var(--line); }
    .back { color:#fff; text-decoration:none; font-size:28px; width:42px; height:42px; display:grid; place-items:center; border-radius:999px; background:rgba(255,255,255,.10); }
    .text { min-width:0; }
    .title { font-weight:900; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .sub { color:var(--muted); font-size:13px; margin-top:3px; }
    main { display:grid; place-items:center; padding:10px; }
    video { width:100%; max-width:1280px; max-height:calc(100vh - 84px); background:#000; border-radius:6px; }
  </style>
</head>
<body>
  <div class="page">
    <header><a class="back" href="{{BACK}}">&lsaquo;</a><div class="text"><div class="title">{{TITLE}}</div><div class="sub">{{SUBTITLE}}</div></div></header>
    <main><video id="player" controls autoplay playsinline preload="metadata" src="{{SOURCE}}"></video></main>
  </div>
  <script>
    const item = {{ITEM_JSON}};
    const nextItem = {{NEXT_JSON}};
    const video = document.getElementById("player");
    const key = "cinevaultContinue";
    function loadList() {
      try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (_) { return []; }
    }
    function saveList(list) {
      localStorage.setItem(key, JSON.stringify(list.slice(0, 60)));
    }
    function upsert(entry) {
      const list = loadList().filter(existing => existing.key !== entry.key);
      list.unshift(entry);
      saveList(list);
    }
    function remove(entryKey) {
      saveList(loadList().filter(existing => existing.key !== entryKey));
    }
    function saveProgress() {
      if (!video.duration || video.duration < 30) return;
      const progress = video.currentTime / video.duration;
      if (progress >= 0.92) {
        remove(item.key);
        return;
      }
      if (video.currentTime < 10) return;
      upsert({ ...item, progress, position: video.currentTime, duration: video.duration, updatedAt: Date.now() });
    }
    video.addEventListener("loadedmetadata", () => {
      const saved = loadList().find(existing => existing.key === item.key);
      if (saved && saved.position && saved.duration && saved.position < saved.duration * 0.9) {
        video.currentTime = Math.max(0, saved.position - 8);
      }
    });
    video.addEventListener("timeupdate", () => {
      if (!video._lastSave || Date.now() - video._lastSave > 5000) {
        video._lastSave = Date.now();
        saveProgress();
      }
    });
    video.addEventListener("pause", saveProgress);
    video.addEventListener("ended", () => {
      remove(item.key);
    });
  </script>
</body>
</html>
"""


DIRECT_PLAYER_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{TITLE}}</title>
  <style>
    :root { color-scheme:dark; --text:#fff; --muted:#c2c7d1; --gold:#f5b73f; --line:rgba(255,255,255,.13); }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:#000; color:var(--text); font-family:Arial, Helvetica, sans-serif; }
    .watch-page { min-height:100vh; position:relative; overflow:hidden; padding:22px clamp(20px,4vw,56px) 34px; }
    .watch-page::before { content:""; position:fixed; inset:-26px; background-image:var(--poster-bg); background-size:cover; background-position:center top; opacity:.66; filter:blur(7px) saturate(1.25); transform:scale(1.08); }
    .watch-page::after { content:""; position:fixed; inset:0; background:linear-gradient(180deg,rgba(0,0,0,.12) 0%,rgba(0,0,0,.38) 28%,rgba(18,34,13,.82) 100%), linear-gradient(90deg,rgba(0,0,0,.88) 0%,rgba(0,0,0,.50) 48%,rgba(0,0,0,.76) 100%); }
    .topbar,.hero,.player-shell { position:relative; z-index:2; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap:14px; max-width:1180px; margin:0 auto; }
    .circle { width:46px; height:46px; display:grid; place-items:center; border-radius:999px; background:rgba(8,12,18,.48); border:1px solid rgba(255,255,255,.12); color:#fff; text-decoration:none; font-size:25px; backdrop-filter:blur(10px); }
    .circle svg { width:24px; height:24px; display:block; }
    .topbar-actions { display:flex; align-items:center; gap:10px; }
    .circle.cast-button { cursor:pointer; }
    .circle.cast-button svg { width:25px; height:25px; display:block; }
    .cast-fallback { position:fixed; inset:0; z-index:30; display:none; align-items:end; background:rgba(0,0,0,.62); }
    .cast-fallback.open { display:flex; }
    .cast-sheet { width:100%; max-width:560px; margin:0 auto; border-radius:22px 22px 0 0; background:#050506; color:#fff; padding:22px 24px 28px; box-shadow:0 -20px 70px rgba(0,0,0,.55); position:relative; }
    .cast-sheet h2 { margin:8px 0 18px; text-align:center; font-size:22px; }
    .cast-note { color:#a7a7ad; font-size:15px; line-height:1.35; }
    .cast-close { position:absolute; top:14px; right:16px; width:36px; height:36px; border:0; border-radius:999px; background:transparent; color:#fff; font-size:30px; line-height:1; cursor:pointer; }
    .cast-list { display:grid; gap:8px; margin:8px 0 14px; }
    .cast-device { width:100%; display:grid; grid-template-columns:52px 1fr auto; gap:12px; align-items:center; border:0; border-radius:12px; background:rgba(255,255,255,.08); color:#fff; padding:12px; text-align:left; }
    .cast-device[disabled] { opacity:.55; cursor:not-allowed; }
    .cast-device-icon { display:grid; place-items:center; min-width:44px; }
    .cast-device-name { font-weight:900; font-size:16px; }
    .cast-device-meta { color:#a7a7ad; font-size:12px; margin-top:2px; }
    .cast-device-action { color:#f5b73f; font-weight:900; font-size:12px; }
    .hero { max-width:900px; min-height:calc(100vh - 96px); margin:0 auto; display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center; }
    .poster-logo { width:min(460px,82vw); max-height:42vh; object-fit:contain; filter:drop-shadow(0 18px 34px rgba(0,0,0,.62)); margin-bottom:18px; border-radius:14px; }
    .poster-logo.missing { aspect-ratio:2/3; display:grid; place-items:center; background:rgba(255,255,255,.08); border:1px solid var(--line); color:var(--muted); font-weight:900; }
    h1 { margin:0; font-size:clamp(32px,7vw,76px); line-height:.96; letter-spacing:0; text-shadow:0 6px 22px rgba(0,0,0,.62); }
    .episode-title { margin:12px 0 8px; font-size:clamp(22px,4vw,34px); font-weight:900; }
    .meta { display:flex; flex-wrap:wrap; justify-content:center; gap:9px 14px; color:#eef2f7; font-size:17px; margin:12px 0 18px; }
    .rating { display:inline-flex; align-items:center; min-height:31px; padding:0 10px; border-radius:10px; background:#050506; font-weight:900; }
    .resume-row { display:grid; grid-template-columns:minmax(190px,400px) 44px; gap:10px; width:min(464px,100%); margin:8px auto 16px; }
    .resume { min-height:42px; border:0; border-radius:999px; background:#fff; color:#111; font-size:17px; font-weight:900; cursor:pointer; }
    .restart { width:44px; height:44px; display:grid; place-items:center; border-radius:999px; border:1px solid rgba(255,255,255,.12); background:rgba(122,54,70,.50); color:#fff; font-size:0; cursor:pointer; position:relative; }
    .restart::before { content:"\\21BB"; font-size:23px; line-height:1; transform:translateX(-2px); }
    .restart::after { content:"\\25B6"; position:absolute; font-size:11px; line-height:1; transform:translate(5px,1px); }
    .mode-switch { display:inline-flex; gap:6px; align-items:center; justify-content:center; padding:5px; margin:0 auto 10px; border-radius:999px; background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.12); }
    .mode-switch a { min-width:74px; min-height:30px; display:inline-flex; align-items:center; justify-content:center; border-radius:999px; color:#e8edf5; text-decoration:none; font-size:12px; font-weight:950; }
    .mode-switch a.active { background:#fff; color:#111; }
    .caption-control { display:inline-flex; align-items:center; gap:9px; margin:0 auto 14px; padding:7px 10px 7px 13px; border-radius:999px; background:rgba(0,0,0,.42); border:1px solid rgba(255,255,255,.18); color:#fff; font-size:13px; font-weight:950; }
    .caption-control select { min-height:32px; max-width:min(280px,62vw); border:0; border-radius:999px; background:#fff; color:#111; padding:0 28px 0 11px; font-weight:850; }
    .download-size-panel { display:none; width:min(500px,100%); margin:0 auto 14px; padding:12px 14px; border-radius:18px; background:rgba(0,0,0,.34); border:1px solid rgba(255,255,255,.15); backdrop-filter:blur(10px); }
    .download-size-panel.open { display:block; }
    .download-size-panel.visible { display:block; }
    .download-size-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; color:#fff; font-size:13px; font-weight:900; }
    .download-size-head span:last-child { color:#f5b73f; }
    .download-size-panel input[type=range] { width:100%; accent-color:#f5b73f; margin:8px 0 6px; }
    .download-size-meta { display:flex; justify-content:space-between; gap:10px; color:rgba(238,242,247,.78); font-size:11px; font-weight:800; }
    .actions { display:flex; flex-wrap:wrap; justify-content:center; gap:13px; margin:0 0 26px; }
    .video-list-actions { display:flex; justify-content:center; gap:9px; flex-wrap:wrap; margin:12px 0 18px; }
    .video-list-actions a { min-height:42px; padding:0 16px; border:1px solid rgba(255,255,255,.18); border-radius:999px; background:rgba(255,255,255,.12); color:#fff; display:inline-flex; align-items:center; justify-content:center; gap:7px; text-decoration:none; font-size:14px; font-weight:900; }
    .video-list-actions a:first-child { background:#f5b73f; border-color:#f5b73f; color:#111; }
    .action { width:84px; color:#e8edf5; text-decoration:none; font-size:12px; line-height:1.25; }
    .action span { width:50px; height:50px; display:grid; place-items:center; margin:0 auto 7px; border-radius:999px; background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.12); font-size:21px; }
    .action.download span, .action.mark-watched span { background:rgba(255,255,255,.10); border:3px solid rgba(255,255,255,.92); color:#fff; font-size:25px; }
    .action.download span { font-size:27px; }
    .action.watched { color:#fff; }
    .action.watched span { background:rgba(245,183,63,.28); border-color:rgba(245,183,63,.56); }
    .summary { max-width:760px; font-size:18px; line-height:1.46; margin:0 auto 22px; text-align:left; color:#f1f5fb; text-shadow:0 2px 14px rgba(0,0,0,.42); }
    .cast-panel { display:none; max-width:760px; width:100%; margin:0 auto 22px; text-align:left; }
    .cast-panel.open { display:block; }
    .cast-panel h2 { margin:0 0 12px; font-size:22px; }
    .cast-list { display:flex; flex-wrap:wrap; gap:9px; padding:0; margin:0; list-style:none; }
    .cast-list li { border:1px solid rgba(255,255,255,.16); background:rgba(255,255,255,.11); border-radius:999px; padding:8px 11px; color:#eef5ff; font-size:14px; }
    .file-grid { display:grid; grid-template-columns:120px minmax(0,1fr); gap:12px 22px; max-width:520px; width:100%; margin:0 auto; text-align:left; font-size:17px; }
    .label { color:rgba(236,243,255,.72); }
    .player-shell { display:none; min-height:100vh; grid-template-rows:auto 1fr; }
    .player-shell.open { display:grid; }
    .player-shell.fullscreen-mode { position:fixed; inset:0; z-index:20; min-height:100vh; background:#000; grid-template-rows:1fr; }
    .player-shell.fullscreen-mode .player-head { position:absolute; left:0; right:0; top:0; z-index:3; padding:10px 14px; background:linear-gradient(180deg,rgba(0,0,0,.72),rgba(0,0,0,0)); opacity:0; transition:opacity .18s ease; }
    .player-shell.fullscreen-mode:hover .player-head,
    .player-shell.fullscreen-mode .player-head:focus-within { opacity:1; }
    .player-shell.fullscreen-mode .video-wrap { min-height:100vh; }
    .player-shell.fullscreen-mode video { width:100vw; height:100vh; max-width:none; max-height:none; border-radius:0; object-fit:contain; }
    .player-head { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:10px 0; }
    .player-title { min-width:0; }
    .player-title strong { display:block; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-size:20px; }
    .player-title span { color:var(--muted); font-size:14px; }
    .episode-nav { display:flex; gap:10px; }
    .episode-nav a { min-height:40px; display:inline-flex; align-items:center; padding:0 13px; border-radius:999px; background:rgba(255,255,255,.12); color:#fff; text-decoration:none; font-weight:800; border:1px solid var(--line); }
    .video-wrap { display:grid; place-items:center; position:relative; overflow:hidden; background:#000; }
    video { width:100%; max-width:1280px; max-height:calc(100vh - 86px); background:#000; border-radius:6px; }
    .player-overlay { position:absolute; inset:0; display:grid; grid-template-rows:auto 1fr auto; padding:22px clamp(18px,4vw,42px) 26px; color:#fff; opacity:0; pointer-events:none; transition:opacity .18s ease; background:linear-gradient(180deg,rgba(0,0,0,.74),rgba(0,0,0,.18) 26%,rgba(0,0,0,.12) 58%,rgba(0,0,0,.78)); }
    .player-overlay.visible { opacity:1; pointer-events:auto; }
    .overlay-top { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; }
    .overlay-title strong { display:block; max-width:min(70vw,780px); font-size:clamp(19px,2.8vw,30px); line-height:1.1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-shadow:0 3px 14px rgba(0,0,0,.78); }
    .overlay-title span { display:block; margin-top:7px; color:rgba(255,255,255,.78); font-size:clamp(13px,1.8vw,17px); }
    .overlay-badges { display:inline-flex; gap:9px; margin-left:12px; vertical-align:middle; }
    .overlay-badges b { display:inline-grid; place-items:center; min-width:40px; height:28px; padding:0 8px; border-radius:8px; background:#050506; color:#fff; font-size:14px; }
    .overlay-close { display:grid; place-items:center; width:46px; height:46px; border-radius:999px; color:#fff; text-decoration:none; border:1px solid rgba(255,255,255,.14); background:rgba(0,0,0,.28); font-size:34px; line-height:1; cursor:pointer; }
    .overlay-bottom { align-self:end; display:grid; grid-template-columns:auto 1fr auto; gap:12px; align-items:center; }
    .overlay-time { min-width:64px; color:#fff; font-size:15px; font-weight:800; text-shadow:0 2px 8px rgba(0,0,0,.8); }
    .overlay-time.remaining { text-align:right; }
    .seek { width:100%; accent-color:#f6b51f; cursor:pointer; }
    .seek::-webkit-slider-runnable-track { height:7px; border-radius:999px; background:rgba(255,255,255,.36); }
    .seek::-webkit-slider-thumb { width:22px; height:22px; margin-top:-7px; border-radius:999px; background:#fff; box-shadow:0 2px 8px rgba(0,0,0,.5); }
    .seek::-moz-range-track { height:7px; border-radius:999px; background:rgba(255,255,255,.36); }
    .seek::-moz-range-thumb { width:22px; height:22px; border:0; border-radius:999px; background:#fff; box-shadow:0 2px 8px rgba(0,0,0,.5); }
    .up-next { position:absolute; inset:0; z-index:5; display:none; align-items:center; justify-content:center; padding:clamp(18px,4vw,48px); background:linear-gradient(115deg,rgba(35,8,18,.88),rgba(9,21,42,.90)); color:#fff; }
    .up-next.open { display:flex; }
    .up-next-grid { width:min(1040px,100%); display:grid; grid-template-columns:minmax(180px,360px) minmax(0,1fr); gap:34px; align-items:center; }
    .up-next-poster { width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:14px; box-shadow:0 22px 60px rgba(0,0,0,.45); background:#111; }
    .up-next-kicker { font-size:clamp(15px,2.8vw,27px); font-weight:950; margin-bottom:12px; }
    .up-next-title { font-size:clamp(20px,3.5vw,38px); line-height:1.05; font-weight:950; margin-bottom:10px; }
    .up-next-subtitle { color:#d7dde8; font-size:clamp(12px,1.8vw,18px); font-weight:850; margin-bottom:12px; }
    .up-next .summary { font-size:clamp(13px,1.9vw,18px); line-height:1.38; }
    .up-next-actions { display:flex; gap:12px; flex-wrap:wrap; margin-top:22px; align-items:center; }
    .up-next-actions a,.up-next-actions button { min-width:116px; min-height:48px; display:inline-flex; align-items:center; justify-content:center; padding:0 22px; border-radius:999px; border:1px solid rgba(255,255,255,.18); background:rgba(255,255,255,.13); color:#fff; font-size:16px; font-weight:950; cursor:pointer; text-decoration:none; line-height:1; }
    .up-next-actions .play-next { background:#fff; color:#111; border-color:#fff; }
    .up-next-actions .cancel-next { min-width:104px; background:rgba(255,255,255,.10); }
    @media (max-width:640px) {
      .watch-page { padding:18px 24px 30px; }
      .topbar { margin-bottom:10px; }
      .hero { min-height:calc(100svh - 96px); justify-content:end; padding-bottom:10px; }
      .poster-logo { max-height:34vh; }
      .resume-row { grid-template-columns:1fr 42px; gap:9px; }
      .resume { min-height:40px; font-size:17px; }
      .restart { width:42px; height:42px; }
      .actions { gap:9px; }
      .action { width:70px; font-size:11px; }
      .action span { width:46px; height:46px; font-size:20px; }
      .action.download span, .action.mark-watched span { width:46px; height:46px; font-size:24px; border-width:3px; }
      .download-size-panel { padding:10px 12px; margin-bottom:12px; }
      .summary { font-size:17px; }
      .cast-panel h2 { font-size:20px; }
      .file-grid { font-size:16px; }
      .player-head { align-items:flex-start; flex-direction:column; }
      .episode-nav { width:100%; overflow:auto; }
      .up-next-grid { grid-template-columns:1fr; gap:18px; }
    }
  </style>
</head>
<body>
  <main class="watch-page"{{BACKGROUND_STYLE}}>
    <div class="topbar"><a class="circle" href="{{BACK}}" aria-label="Back">&lsaquo;</a><div class="topbar-actions"><button class="circle cast-button" id="castButton" type="button" aria-label="Cast" title="Cast"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 10V7.5C5 6.1 6.1 5 7.5 5h17C25.9 5 27 6.1 27 7.5v17c0 1.4-1.1 2.5-2.5 2.5H22" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/><path d="M5 21c3.3 0 6 2.7 6 6M5 15c6.6 0 12 5.4 12 12M5 27h.1" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round"/></svg></button><a class="circle home-circle" href="/" aria-label="Home" title="Home"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M5 15.5 16 6l11 9.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M8.5 14.5V27h15V14.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/><path d="M13 27v-8h6v8" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/></svg></a></div></div>
    <section class="hero" id="hero"{{HERO_ATTRS}}>
      {{POSTER}}
      <h1>{{TITLE}}</h1>
      <div class="episode-title">{{EPISODE_TITLE}}</div>
      <div class="meta">{{META}}</div>
      <div class="resume-row"><button class="resume" id="resumeButton">Play</button><button class="restart" id="restartButton" title="Start from beginning" aria-label="Start from beginning">Start over</button></div>
      <div class="mode-switch"><a class="{{DIRECT_MODE_CLASS}}" href="{{DIRECT_MODE_HREF}}">Direct</a><a class="{{HLS_MODE_CLASS}}" href="{{HLS_MODE_HREF}}">HLS</a></div>
      {{CAPTION_CONTROL}}
      <div class="video-list-actions"><a href="#queue" data-video-queue="1">Add to Queue</a><a href="#playlist" data-video-playlist="1">Add to Playlist</a><a href="/video-lists">View My Lists</a></div>
      <div class="download-size-panel{{DOWNLOAD_SIZE_OPEN_CLASS}}" id="downloadSizePanel" data-source-bytes="{{SOURCE_BYTES}}" data-default-ratio="{{DOWNLOAD_DEFAULT_RATIO}}">
        <div class="download-size-head"><span>HLS compressed download size</span><span id="downloadSizeValue">{{DOWNLOAD_DEFAULT_LABEL}}</span></div>
        <input id="downloadSizeSlider" type="range" min="20" max="95" step="5" value="{{DOWNLOAD_DEFAULT_PERCENT}}" aria-label="Compressed download target size">
        <div class="download-size-meta"><span>Smaller file</span><span>Original: {{SOURCE_SIZE_LABEL}}</span><span>Better quality</span></div>
      </div>
      <div class="actions">{{ACTIONS}}</div>
      <p class="summary">{{SUMMARY}}</p>
      <section class="cast-panel" id="castPanel"><h2>Cast & Crew</h2><ul class="cast-list">{{ACTORS}}</ul></section>
      <div class="file-grid"><div class="label">Video</div><div>{{VIDEO_LABEL}}</div><div class="label">Audio</div><div>Original audio</div><div class="label">Subtitles</div><div>{{SUBTITLE_SUMMARY}}</div></div>
    </section>
    <section class="player-shell{{PLAYER_OPEN_CLASS}}{{PLAYER_FULLSCREEN_CLASS}}" id="playerShell">
      <div class="player-head">
        <a class="circle" href="{{BACK}}" aria-label="Back">&lsaquo;</a>
        <div class="player-title"><strong>{{EPISODE_OR_TITLE}}</strong><span>{{SUBTITLE}}</span></div>
        <div class="episode-nav">{{PLAYER_NAV}}</div>
      </div>
      <div class="video-wrap">
        <video id="player" controls playsinline preload="metadata"{{VIDEO_AUTOPLAY}} data-source="{{SOURCE}}">{{SUBTITLE_TRACKS}}</video>
        <div class="player-overlay" id="playerOverlay">
          <div class="overlay-top">
            <div class="overlay-title"><strong>{{EPISODE_OR_TITLE}}</strong><span>{{SUBTITLE}}</span></div>
            <button class="overlay-close" id="closePlayerButton" type="button" aria-label="Close player">&times;</button>
          </div>
          <div></div>
          <div class="overlay-bottom">
            <div class="overlay-time" id="elapsedTime">0:00</div>
            <input class="seek" id="seekBar" type="range" min="0" max="1000" value="0" step="1" aria-label="Seek">
            <div class="overlay-time remaining" id="remainingTime">-0:00</div>
          </div>
        </div>
        <div class="up-next" id="upNext">
          <div class="up-next-grid">
            <img class="up-next-poster" id="upNextPoster" alt="">
            <div>
              <div class="up-next-kicker">Up Next &bull; <span id="upNextCount">10</span></div>
              <div class="up-next-title" id="upNextTitle"></div>
              <div class="up-next-subtitle" id="upNextSubtitle"></div>
              <p class="summary" id="upNextSummary"></p>
              <div class="up-next-actions"><a class="play-next" id="upNextPlay" href="#">Play now</a><button class="cancel-next" type="button" id="upNextCancel">Cancel</button></div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </main>
  <script src="/assets/hls.min.js"></script>
  <script>
    const item = {{ITEM_JSON}};
    const previousItem = {{PREV_JSON}};
    const nextItem = {{NEXT_JSON}};
    const playbackParams = new URLSearchParams(location.search);
    const queuePlayback = playbackParams.get("queue") === "1";
    const playlistPlayback = Number(playbackParams.get("playlist") || 0);
    const playlistPosition = Number(playbackParams.get("position") || 0);
    const video = document.getElementById("player");
    const captionSelect = document.getElementById("captionSelect");
    const captionSources = Array.from(video.querySelectorAll("track")).map(element => ({
      src: element.getAttribute("src"),
      srclang: element.getAttribute("srclang") || "und",
      label: element.getAttribute("label") || "Subtitles"
    }));
    let activeCaptionValue = null;
    function applyCaptionSelection(value) {
      const selected = String(value);
      const current = video.querySelector("track[data-active-caption]");
      if (selected === activeCaptionValue && current) {
        if (current.track) current.track.mode = "showing";
        return;
      }
      Array.from(video.textTracks || []).forEach(track => { track.mode = "disabled"; });
      Array.from(video.querySelectorAll("track")).forEach(element => element.remove());
      activeCaptionValue = selected;
      const source = captionSources[Number(selected)];
      if (selected !== "off" && source) {
        const active = document.createElement("track");
        active.kind = "subtitles";
        active.src = source.src;
        active.srclang = source.srclang;
        active.label = source.label;
        active.default = true;
        active.dataset.activeCaption = "1";
        active.addEventListener("load", () => { if (active.track) active.track.mode = "showing"; });
        video.appendChild(active);
        if (active.track) active.track.mode = "showing";
      }
      try { localStorage.setItem("cinevaultCaptionTrack", String(value)); } catch (_) {}
    }
    if (captionSelect) {
      let savedCaption = "off";
      try { savedCaption = localStorage.getItem("cinevaultCaptionTrack") || "off"; } catch (_) {}
      if (![...captionSelect.options].some(option => option.value === savedCaption)) savedCaption = "off";
      captionSelect.value = savedCaption;
      captionSelect.addEventListener("change", () => {
        applyCaptionSelection(captionSelect.value);
        setTimeout(() => applyCaptionSelection(captionSelect.value), 150);
        setTimeout(() => applyCaptionSelection(captionSelect.value), 750);
      });
      video.addEventListener("loadedmetadata", () => applyCaptionSelection(captionSelect.value));
      setTimeout(() => applyCaptionSelection(captionSelect.value), 0);
    }
    const hero = document.getElementById("hero");
    const playerShell = document.getElementById("playerShell");
    const resumeButton = document.getElementById("resumeButton");
    const key = "cinevaultContinue";
    const mediaSource = video.dataset.source;
    const downloadSizePanel = document.getElementById("downloadSizePanel");
    const downloadSizeSlider = document.getElementById("downloadSizeSlider");
    const downloadSizeValue = document.getElementById("downloadSizeValue");
    const sourceBytes = Number(downloadSizePanel?.dataset.sourceBytes || 0);
    const modeLinks = Array.from(document.querySelectorAll(".mode-switch a"));
    let selectedPlaybackMode = "direct";
    function setDownloadSizePanelVisible(isVisible) {
      if (downloadSizePanel) downloadSizePanel.classList.toggle("visible", !!isVisible);
    }
    function modeFromHref(href) {
      return /mode=hls/.test(href || "") ? "hls" : "direct";
    }
    function currentModeFromLinks() {
      const active = modeLinks.find(link => link.classList.contains("active"));
      return modeFromHref(active ? active.getAttribute("href") : "");
    }
    function setSelectedPlaybackMode(mode, updateUrl=false) {
      selectedPlaybackMode = mode === "hls" ? "hls" : "direct";
      modeLinks.forEach(link => link.classList.toggle("active", modeFromHref(link.getAttribute("href")) === selectedPlaybackMode));
      setDownloadSizePanelVisible(selectedPlaybackMode === "hls");
      if (updateUrl && history.replaceState) {
        const url = new URL(location.href);
        url.searchParams.set("mode", selectedPlaybackMode);
        history.replaceState(null, "", url.toString());
      }
    }
    function humanBytes(bytes) {
      if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
      const units = ["B","KB","MB","GB","TB"];
      let value = bytes;
      let idx = 0;
      while (value >= 1024 && idx < units.length - 1) { value /= 1024; idx += 1; }
      const digits = idx >= 3 ? 1 : 0;
      return `${value.toFixed(digits)} ${units[idx]}`;
    }
    function selectedDownloadRatio() {
      return Math.max(0.20, Math.min(0.95, Number(downloadSizeSlider?.value || 40) / 100));
    }
    function updateDownloadSizeLabel() {
      if (!downloadSizeSlider || !downloadSizeValue) return;
      const percent = Math.round(selectedDownloadRatio() * 100);
      downloadSizeValue.textContent = `${humanBytes(sourceBytes * selectedDownloadRatio())} (${percent}%)`;
    }
    if (downloadSizeSlider) {
      downloadSizeSlider.addEventListener("input", updateDownloadSizeLabel);
      updateDownloadSizeLabel();
    }
    setSelectedPlaybackMode(currentModeFromLinks(), false);
    modeLinks.forEach(link => {
      link.addEventListener("click", event => {
        event.preventDefault();
        setSelectedPlaybackMode(modeFromHref(link.getAttribute("href") || ""), true);
      });
    });
    let serverState = null;
    let watchedState = false;
    const playerOverlay = document.getElementById("playerOverlay");
    const seekBar = document.getElementById("seekBar");
    const elapsedTime = document.getElementById("elapsedTime");
    const remainingTime = document.getElementById("remainingTime");
    let overlayTimer = null;
    let userSeeking = false;
    let mediaAttached = false;
    let hlsController = null;
    let upNextTimer = null;
    function loadList() {
      try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (_) { return []; }
    }
    function saveList(list) {
      localStorage.setItem(key, JSON.stringify(list.slice(0, 60)));
    }
    function upsert(entry) {
      const list = loadList().filter(existing => existing.key !== entry.key);
      list.unshift(entry);
      saveList(list);
    }
    function remove(entryKey) {
      saveList(loadList().filter(existing => existing.key !== entryKey));
    }
    function savedItem() {
      return serverState || loadList().find(existing => existing.key === item.key);
    }
    function formatRemaining(seconds) {
      const minutes = Math.max(1, Math.round(seconds / 60));
      return ` - ${minutes}m left`;
    }
    function hasRealResume(saved) {
      return saved && saved.duration && saved.position && saved.position > 10 && saved.position < saved.duration * 0.9;
    }
    function updatePlayButton() {
      const saved = savedItem();
      watchedState = Boolean(saved && saved.watched);
      if (hasRealResume(saved)) {
        resumeButton.textContent = "Resume" + formatRemaining(saved.duration - saved.position);
      } else {
        resumeButton.textContent = "Play";
      }
      document.querySelectorAll("[data-mark-watched]").forEach(link => {
        link.classList.toggle("watched", watchedState);
        link.lastChild.textContent = watchedState ? "Mark Unwatched" : "Mark Watched";
      });
    }
    async function loadServerState() {
      try {
        const stateQuery = new URLSearchParams({key:item.key, title:item.title || "", subtitle:item.subtitle || ""});
        const response = await fetch(`/api/watch/state?${stateQuery}`, {cache:"no-store"});
        const payload = await response.json();
        serverState = payload.item || null;
        updatePlayButton();
      } catch (_) {}
    }
    async function postWatchProgress(entry) {
      try {
        await fetch("/api/watch/progress", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(entry)});
      } catch (_) {}
    }
    async function postWatched(watched) {
      try {
        const response = await fetch("/api/watch/watched", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({item, watched, duration: video.duration || 0})});
        const payload = await response.json();
        if (payload.ok) {
          watchedState = watched;
          serverState = watched ? {...item, watched:true, progress:0, position:0, duration:video.duration || 0, updatedAt:Date.now()} : null;
          if (watched) remove(item.key);
          updatePlayButton();
        }
      } catch (_) {}
    }
    async function postBulkWatched(scope, watched=true) {
      try {
        const response = await fetch("/api/watch/bulk-watched", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({item, scope, watched})});
        const payload = await response.json();
        return Boolean(payload.ok);
      } catch (_) {
        return false;
      }
    }
    async function postQueuedNext(entry) {
      if (!entry || entry.kind !== "tv") return;
      try {
        await fetch("/api/watch/progress", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({item:entry, queued:true, position:0, duration:0, progress:0, watched:false})});
      } catch (_) {}
    }
    async function addCurrentToQueue() {
      try {
        const response = await fetch("/api/video/queue", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:"add", item})});
        const payload = await response.json();
        alert(payload.ok ? `Added to queue (${payload.count} items)` : (payload.error || "Could not add to queue"));
      } catch (_) { alert("Could not add to queue"); }
    }
    async function addCurrentToPlaylist() {
      const name = prompt("Playlist name");
      if (!name) return;
      try {
        const response = await fetch("/api/video/playlists", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:"add", name, item})});
        const payload = await response.json();
        alert(payload.ok ? `Added to ${name}` : (payload.error || "Could not add to playlist"));
      } catch (_) { alert("Could not add to playlist"); }
    }
    async function playNextFromVideoList() {
      try {
        if (queuePlayback) {
          const response = await fetch("/api/video/queue", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:"finish", key:item.key})});
          const payload = await response.json();
          if (payload.next_href) { location.href = `${payload.next_href}${payload.next_href.includes("?") ? "&" : "?"}play=1&queue=1`; return true; }
        }
        if (playlistPlayback) {
          const response = await fetch(`/api/video/playlist-next?playlist_id=${playlistPlayback}&position=${playlistPosition}`, {cache:"no-store"});
          const payload = await response.json();
          if (payload.next_href) { location.href = `${payload.next_href}${payload.next_href.includes("?") ? "&" : "?"}play=1&playlist=${playlistPlayback}&position=${playlistPosition + 1}`; return true; }
        }
      } catch (_) {}
      return false;
    }
    function showUpNext() {
      if (!nextItem || nextItem.kind !== "tv") return false;
      exitPlayerFullscreen();
      const panel = document.getElementById("upNext");
      const count = document.getElementById("upNextCount");
      const poster = document.getElementById("upNextPoster");
      const title = document.getElementById("upNextTitle");
      const subtitle = document.getElementById("upNextSubtitle");
      const summary = document.getElementById("upNextSummary");
      const play = document.getElementById("upNextPlay");
      const cancel = document.getElementById("upNextCancel");
      if (!panel || !play) return false;
      let remaining = 10;
      if (poster) poster.src = nextItem.poster || "";
      if (title) title.textContent = nextItem.episodeTitle || nextItem.title || "Next episode";
      if (subtitle) subtitle.textContent = `${nextItem.title || ""}${nextItem.subtitle ? " - " + nextItem.subtitle : ""}`;
      if (summary) summary.textContent = nextItem.summary || "";
      const nextHref = nextItem.href ? `${nextItem.href}${nextItem.href.includes("?") ? "&" : "?"}play=1` : "#";
      play.href = nextHref;
      count.textContent = String(remaining);
      panel.classList.add("open");
      clearInterval(upNextTimer);
      upNextTimer = setInterval(() => {
        remaining -= 1;
        count.textContent = String(Math.max(0, remaining));
        if (remaining <= 0) {
          clearInterval(upNextTimer);
          location.href = nextHref || "/";
        }
      }, 1000);
      cancel.onclick = () => {
        clearInterval(upNextTimer);
        panel.classList.remove("open");
      };
      return true;
    }
    function fullscreenElement() {
      return document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement;
    }
    function requestPlayerFullscreen() {
      const target = video.requestFullscreen ? video : playerShell;
      const request = target.requestFullscreen || target.webkitRequestFullscreen || target.msRequestFullscreen;
      if (request) {
        try {
          const result = request.call(target);
          if (result && result.catch) result.catch(() => {});
        } catch (_) {}
      }
      if (video.webkitEnterFullscreen && !fullscreenElement()) {
        try { video.webkitEnterFullscreen(); } catch (_) {}
      }
    }
    function exitPlayerFullscreen() {
      try {
        if (document.exitFullscreen && document.fullscreenElement) document.exitFullscreen();
        else if (document.webkitExitFullscreen && document.webkitFullscreenElement) document.webkitExitFullscreen();
      } catch (_) {}
      try {
        if (video.webkitExitFullscreen) video.webkitExitFullscreen();
      } catch (_) {}
    }
    function formatClock(seconds) {
      if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
      seconds = Math.floor(seconds);
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = String(seconds % 60).padStart(2, "0");
      return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs}` : `${minutes}:${secs}`;
    }
    function showOverlay(hold=false) {
      if (!playerOverlay) return;
      playerOverlay.classList.add("visible");
      clearTimeout(overlayTimer);
      if (!hold && !video.paused) {
        overlayTimer = setTimeout(() => playerOverlay.classList.remove("visible"), 1000);
      }
    }
    function hideOverlaySoon() {
      showOverlay(false);
    }
    function updateOverlay() {
      const duration = Number.isFinite(video.duration) ? video.duration : 0;
      const current = Number.isFinite(video.currentTime) ? video.currentTime : 0;
      if (elapsedTime) elapsedTime.textContent = formatClock(current);
      if (remainingTime) remainingTime.textContent = duration ? `-${formatClock(Math.max(0, duration - current))}` : "-0:00";
      if (seekBar && !userSeeking && duration > 0) {
        seekBar.value = String(Math.round((current / duration) * Number(seekBar.max || 1000)));
      }
    }
    function seekTo(seconds) {
      const target = Math.max(0, seconds || 0);
      try {
        if (video.fastSeek) video.fastSeek(target);
        else video.currentTime = target;
      } catch (_) {
        try { video.currentTime = target; } catch (_) {}
      }
      updateOverlay();
    }
    function attachMediaSource(fromBeginning=false) {
      if (mediaAttached) return;
      mediaAttached = true;
      if (mediaSource.endsWith(".m3u8") && window.Hls && Hls.isSupported()) {
        hlsController = new Hls({
          lowLatencyMode: false,
          startPosition: 0,
          backBufferLength: 120,
          maxBufferLength: 45,
          maxMaxBufferLength: 90,
          startFragPrefetch: true
        });
        hlsController.loadSource(mediaSource);
        hlsController.attachMedia(video);
        hlsController.on(Hls.Events.MANIFEST_PARSED, () => {
          if (fromBeginning || video._forceStartFromBeginning) {
            seekTo(0);
            try { hlsController.startLoad(0); } catch (_) {}
          }
        });
      } else {
        video.src = mediaSource;
      }
    }
    function startPlayback(fromBeginning=false, askFullscreen=true) {
      if (selectedPlaybackMode === "hls" && !mediaSource.endsWith(".m3u8")) {
        const url = new URL(location.href);
        url.searchParams.set("mode", "hls");
        url.searchParams.set("play", "1");
        if (fromBeginning) url.searchParams.set("restart", "1");
        location.href = url.toString();
        return;
      }
      if (fromBeginning) {
        video._forceStartFromBeginning = true;
        serverState = null;
        remove(item.key);
        updatePlayButton();
        if (hlsController) {
          try { hlsController.startLoad(0); } catch (_) {}
        }
      }
      function begin() {
        hero.style.display = "none";
        playerShell.classList.add("open");
        playerShell.classList.add("fullscreen-mode");
        const saved = fromBeginning ? null : savedItem();
        if (!fromBeginning && hasRealResume(saved)) {
          seekTo(Math.max(0, saved.position - 8));
        } else if (fromBeginning) {
          seekTo(0);
          setTimeout(() => seekTo(0), 250);
          setTimeout(() => seekTo(0), 900);
        }
        if (askFullscreen) requestPlayerFullscreen();
        video.play().finally(() => {
          if (fromBeginning) setTimeout(() => { video._forceStartFromBeginning = false; }, 1800);
        }).catch(() => {});
      }
      attachMediaSource(fromBeginning);
      begin();
    }
    function closePlayer() {
      saveProgress();
      try { video.pause(); } catch (_) {}
      exitPlayerFullscreen();
      playerShell.classList.remove("open");
      playerShell.classList.remove("fullscreen-mode");
      hero.style.display = "";
      updatePlayButton();
      showOverlay(false);
    }
    function saveProgress() {
      if (video._forceStartFromBeginning) return;
      if (!video.duration || video.duration < 30) return;
      const progress = video.currentTime / video.duration;
      if (progress >= 0.92) {
        postWatched(true);
        remove(item.key);
        return;
      }
      if (video.currentTime < 10) return;
      const entry = { ...item, progress, position: video.currentTime, duration: video.duration, updatedAt: Date.now(), watched:false };
      serverState = entry;
      upsert(entry);
      postWatchProgress({item, progress, position: video.currentTime, duration: video.duration});
    }
    video.addEventListener("loadedmetadata", updatePlayButton);
    video.addEventListener("timeupdate", () => {
      if (!video._lastSave || Date.now() - video._lastSave > 5000) {
        video._lastSave = Date.now();
        saveProgress();
      }
    });
    video.addEventListener("pause", saveProgress);
    video.addEventListener("ended", async () => {
      postWatched(true);
      remove(item.key);
      if (await playNextFromVideoList()) return;
      postQueuedNext(nextItem);
      showUpNext();
    });
    document.querySelectorAll("[data-video-queue]").forEach(link => link.addEventListener("click", event => { event.preventDefault(); addCurrentToQueue(); }));
    document.querySelectorAll("[data-video-playlist]").forEach(link => link.addEventListener("click", event => { event.preventDefault(); addCurrentToPlaylist(); }));
    document.querySelectorAll("[data-mark-watched]").forEach(link => {
      link.addEventListener("click", event => {
        event.preventDefault();
        postWatched(!watchedState);
      });
    });
    document.querySelectorAll("[data-mark-bulk]").forEach(link => {
      link.addEventListener("click", async event => {
        event.preventDefault();
        const ok = await postBulkWatched(link.dataset.markBulk, true);
        if (ok) {
          link.classList.add("watched");
          link.lastChild.textContent = link.dataset.markBulk === "show" ? "Show Watched" : "Season Watched";
        }
      });
    });
    document.querySelectorAll("[data-more-toggle]").forEach(link => {
      link.addEventListener("click", event => {
        event.preventDefault();
        const panel = document.getElementById("castPanel");
        if (!panel) return;
        const isOpen = panel.classList.toggle("open");
        link.classList.toggle("watched", isOpen);
        if (isOpen) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
    });
    document.querySelectorAll("[data-mobile-download]").forEach(link => {
      link.addEventListener("click", async event => {
        if (selectedPlaybackMode !== "hls") return;
        event.preventDefault();
        const originalText = link.lastChild ? link.lastChild.textContent : "Download";
        if (link.dataset.busy === "1") return;
        link.dataset.busy = "1";
        if (link.lastChild) link.lastChild.textContent = "Queueing";
        try {
          const response = await fetch("/api/mobile-download/enqueue", {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({scope: link.dataset.mobileScope, item_id: link.dataset.mobileId, target_ratio: selectedDownloadRatio()})
          });
          const payload = await response.json();
          if (!payload.ok) throw new Error(payload.error || "Download queue failed");
          if (payload.ready && payload.download_url) {
            if (link.lastChild) link.lastChild.textContent = "Ready";
            location.href = payload.download_url;
          } else {
            const eta = payload.estimate_seconds ? Math.ceil(payload.estimate_seconds / 60) : null;
            if (link.lastChild) link.lastChild.textContent = eta ? `Queued ${eta}m` : "Queued";
          }
        } catch (error) {
          if (link.lastChild) link.lastChild.textContent = "Failed";
          alert(error.message || String(error));
          setTimeout(() => { if (link.lastChild) link.lastChild.textContent = originalText; }, 2500);
        } finally {
          setTimeout(() => { link.dataset.busy = "0"; }, 1000);
        }
      });
    });
    resumeButton.addEventListener("click", () => startPlayback(false, true));
    document.getElementById("restartButton").addEventListener("click", () => startPlayback(true, true));
    const closePlayerButton = document.getElementById("closePlayerButton");
    if (closePlayerButton) closePlayerButton.addEventListener("click", closePlayer);
    playerShell.addEventListener("pointermove", () => showOverlay(false));
    playerShell.addEventListener("click", event => {
      if (event.target.closest("a,button,input")) return;
      showOverlay(false);
      if (!fullscreenElement()) requestPlayerFullscreen();
    });
    video.addEventListener("play", () => {
      updateOverlay();
      hideOverlaySoon();
      if (!fullscreenElement() && video._playCameFromTap) requestPlayerFullscreen();
    });
    video.addEventListener("pause", () => {
      updateOverlay();
      showOverlay(true);
    });
    video.addEventListener("loadedmetadata", updateOverlay);
    video.addEventListener("durationchange", updateOverlay);
    video.addEventListener("timeupdate", updateOverlay);
    video.addEventListener("pointerdown", () => {
      video._playCameFromTap = true;
      showOverlay(false);
      if (!fullscreenElement()) requestPlayerFullscreen();
      setTimeout(() => { video._playCameFromTap = false; }, 1200);
    });
    if (seekBar) {
      seekBar.addEventListener("pointerdown", () => { userSeeking = true; showOverlay(true); });
      seekBar.addEventListener("input", () => {
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        const value = Number(seekBar.value || 0) / Number(seekBar.max || 1000);
        if (duration > 0) {
          const target = duration * value;
          if (elapsedTime) elapsedTime.textContent = formatClock(target);
          if (remainingTime) remainingTime.textContent = `-${formatClock(Math.max(0, duration - target))}`;
        }
      });
      seekBar.addEventListener("change", () => {
        const duration = Number.isFinite(video.duration) ? video.duration : 0;
        const value = Number(seekBar.value || 0) / Number(seekBar.max || 1000);
        if (duration > 0) seekTo(duration * value);
        userSeeking = false;
        hideOverlaySoon();
      });
      seekBar.addEventListener("pointerup", () => { userSeeking = false; hideOverlaySoon(); });
    }
    loadServerState();
    updatePlayButton();
    const castButton = document.getElementById("castButton");
    const CAST_API = `${location.protocol}//${location.hostname}:8120`;
    function castIconFor(device) {
      const name = `${device.name || ""} ${device.model || ""}`.toLowerCase();
      if (device.type === "chromecast") return `<span class="brand-google-cast">G</span>`;
      if (name.includes("roku")) return `<span class="brand-roku">Roku</span>`;
      if (name.includes("samsung")) return `<span class="brand-samsung">SAMSUNG</span>`;
      return `<span class="brand-device">TV</span>`;
    }
    function mediaSourceForCast() {
      const source = video ? video.dataset.source : "";
      return source ? new URL(source, location.origin).href : "";
    }
    function mediaTitleForCast() {
      const title = document.querySelector(".player-title strong, h1");
      return title ? title.textContent.trim() : "CineMediaVault";
    }
    function ensureCastPanel() {
      let panel = document.getElementById("castFallback");
      if (!panel) {
        panel = document.createElement("div");
        panel.id = "castFallback";
        panel.className = "cast-fallback";
        panel.innerHTML = `<div class="cast-sheet"><button class="cast-close" type="button" aria-label="Close">&times;</button><h2>Connect To</h2><div class="cast-list" id="castDeviceList"></div><p class="cast-note" id="castNote"></p></div>`;
        document.body.appendChild(panel);
        panel.querySelector(".cast-close").addEventListener("click", () => panel.classList.remove("open"));
        panel.addEventListener("click", event => { if (event.target === panel) panel.classList.remove("open"); });
      }
      return panel;
    }
    function setCastPanel(message, devices=[]) {
      const panel = ensureCastPanel();
      const list = panel.querySelector("#castDeviceList");
      const note = panel.querySelector("#castNote");
      list.innerHTML = "";
      const mediaUrl = mediaSourceForCast();
      for (const device of devices) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "cast-device";
        const canPlay = Boolean(device.playable && mediaUrl);
        if (!canPlay) button.disabled = true;
        button.innerHTML = `<span class="cast-device-icon">${castIconFor(device)}</span><span><span class="cast-device-name"></span><span class="cast-device-meta"></span></span><span class="cast-device-action">${canPlay ? "Play" : (device.playable ? "No video" : "Found")}</span>`;
        button.querySelector(".cast-device-name").textContent = device.name || "Unknown device";
        button.querySelector(".cast-device-meta").textContent = `${device.type || "device"}${device.host ? " - " + device.host : ""}${device.model ? " - " + device.model : ""}`;
        if (canPlay) {
          button.addEventListener("click", async () => {
            localStorage.setItem("cinevaultCastDevice", JSON.stringify(device));
            if (!mediaUrl) {
              note.textContent = `${device.name} selected. Open a movie or episode, then press Cast to play it there.`;
              return;
            }
            note.textContent = `Sending ${mediaTitleForCast()} to ${device.name}...`;
            try {
              const response = await fetch(`${CAST_API}/api/cast/play`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({device_id:device.id, media_url:mediaUrl, title:mediaTitleForCast()})});
              const result = await response.json();
              note.textContent = result.ok ? `Playing on ${device.name}.` : `Cast failed: ${result.error || "unknown error"}`;
            } catch (error) {
              note.textContent = `Cast failed: ${error.message || error}`;
            }
          });
        }
        list.appendChild(button);
      }
      if (!devices.length) list.innerHTML = `<p>No cast devices found.</p>`;
      note.textContent = message;
      panel.classList.add("open");
    }
    async function openCastPicker() {
      setCastPanel("Searching local network from the CineMediaVault server...");
      try {
        const response = await fetch(`${CAST_API}/api/cast/devices`, {cache:"no-store"});
        const result = await response.json();
        const message = result.errors && result.errors.length ? result.errors.join("; ") : "Device discovery completed on the server.";
        setCastPanel(message, result.devices || []);
      } catch (error) {
        setCastPanel(`Could not reach cast controller on ${CAST_API}: ${error.message || error}`);
      }
    }
    if (castButton) castButton.addEventListener("click", openCastPicker);
    if (new URLSearchParams(location.search).get("play") === "1") {
      startPlayback(false, false);
    }
  </script>
</body>
</html>
"""


def home_backdrop_html(movie_items=None, tv_shows=None, limit: int = 70) -> str:
    posters: list[str] = []
    movie_items = list(movie_items if movie_items is not None else getattr(movie_app.movie_index, "items", []))
    tv_shows = list(tv_shows if tv_shows is not None else getattr(tv_app.tv_index, "shows", []))
    random.shuffle(movie_items)
    random.shuffle(tv_shows)
    for item in movie_items[: limit * 3]:
        poster = movie_app.poster_url_for(item)
        if poster:
            posters.append(poster)
    for show in tv_shows[: limit * 3]:
        poster = tv_app.poster_url_for(show)
        if poster:
            posters.append(poster)
    seen: set[str] = set()
    unique = []
    for poster in posters:
        if poster in seen:
            continue
        seen.add(poster)
        unique.append(poster)
    random.shuffle(unique)
    return "".join(f"<img src='{html.escape(poster)}' alt=''>" for poster in unique[:limit])


def random_login_backdrop_html(limit: int = 70) -> str:
    posters: list[str] = []
    movie_items = list(getattr(movie_app.movie_index, "items", []))
    tv_shows = list(getattr(tv_app.tv_index, "shows", []))
    random.shuffle(movie_items)
    random.shuffle(tv_shows)
    for item in movie_items[: limit * 2]:
        poster = movie_app.poster_url_for(item)
        if poster:
            posters.append(poster)
    for show in tv_shows[: limit * 2]:
        poster = tv_app.poster_url_for(show)
        if poster:
            posters.append(poster)
    seen: set[str] = set()
    unique = []
    random.shuffle(posters)
    for poster in posters:
        if poster in seen:
            continue
        seen.add(poster)
        unique.append(poster)
        if len(unique) >= limit:
            break
    return "".join(f"<img src='{html.escape(poster)}' alt=''>" for poster in unique)


def home_poster_html(poster: str) -> str:
    if poster:
        return f"<img src='{html.escape(poster)}' alt=''>"
    return "No Poster"


def watched_keys_for_user(user) -> set[str]:
    if not user:
        return set()
    conn = db_connect()
    try:
        rows = conn.execute("SELECT media_key FROM user_media_state WHERE user_id=? AND watched=1", (int(user["id"]),)).fetchall()
        return {str(row["media_key"]) for row in rows}
    finally:
        conn.close()


def watched_badge_html(watched: bool) -> str:
    return "<span class='watched-badge'>Watched</span>" if watched else ""


def movie_media_key(item) -> str:
    """Return a scan-order-independent identity for a movie file."""
    relative = str(getattr(item, "rel_path", "") or getattr(item, "path", "")).replace("\\", "/").casefold()
    return "movie-path:" + hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]


def movie_for_media_key(key: str):
    if not str(key).startswith("movie-path:"):
        return None
    return next((item for item in movie_app.movie_index.items if movie_media_key(item) == key), None)


def movie_for_saved_title(title: str, subtitle: str = ""):
    """Repair legacy numeric resume keys after scan-order IDs have shifted."""
    wanted = str(title or "").strip().casefold()
    year_match = re.search(r"(?:19|20)\d{2}", str(subtitle or ""))
    matches = []
    for item in movie_app.movie_index.items:
        metadata = movie_app.metadata_for(item)
        current_title = str(metadata.get("title") or item.title).strip().casefold()
        if current_title != wanted:
            continue
        current_year = str(metadata.get("year") or "")
        if year_match and current_year and year_match.group(0) != current_year:
            continue
        matches.append(item)
    return matches[0] if len(matches) == 1 else None


def home_movie_card(item, watched_keys: set[str] | None = None) -> str:
    poster = movie_app.poster_url_for(item)
    metadata = movie_app.metadata_for(item)
    title = metadata.get("title") or item.title
    year = metadata.get("year") or ""
    poster_class = "" if poster else " missing"
    watched = bool(watched_keys and movie_media_key(item) in watched_keys)
    card_class = "card watched" if watched else "card"
    return (
        f"<a class='{card_class}' href='/movie/{item.id}'>"
        f"<div class='poster{poster_class}'>{watched_badge_html(watched)}{home_poster_html(poster)}</div>"
        f"<div class='card-title'>{html.escape(title)}</div>"
        f"<div class='card-meta'>{html.escape(str(year) or movie_app.human_size(item.size))}</div>"
        f"</a>"
    )


def show_watched(show, watched_keys: set[str] | None = None) -> bool:
    if not watched_keys:
        return False
    episodes = [episode for season in show.seasons.values() for episode in season.episodes]
    return bool(episodes) and all(f"tv:{episode.id}" in watched_keys for episode in episodes)


def home_show_card(show, watched_keys: set[str] | None = None) -> str:
    poster = tv_app.poster_url_for(show)
    metadata = tv_app.metadata_for(show)
    title = metadata.get("title") or show.title
    latest = latest_episode_for_show(show)
    subtitle = latest_episode_label(latest) if latest else f"{show.count} episodes"
    poster_class = "" if poster else " missing"
    watched = show_watched(show, watched_keys)
    card_class = "card watched" if watched else "card"
    return (
        f"<a class='{card_class}' href='/tv/show/{show.id}'>"
        f"<div class='poster{poster_class}'>{watched_badge_html(watched)}{home_poster_html(poster)}</div>"
        f"<div class='card-title'>{html.escape(title)}</div>"
        f"<div class='card-meta'>{html.escape(subtitle)}</div>"
        f"</a>"
    )


def module_logo_html(module: dict) -> str:
    logo_url = str(module.get("logo_url") or "").strip()
    if logo_url:
        return f"<span class='module-logo'><img class='module-logo-img' src='{html.escape(logo_url)}' alt=''></span>"
    logo = str(module.get("logo") or module.get("name") or "?").strip()
    module_id = str(module.get("id") or "").lower()
    key = logo.upper()
    compact = html.escape(logo[:12])
    if module_id == "comics" or key == "COMICS":
        return "<span class='module-logo game-logo logo-comics'>COMICS</span>"
    if key == "NES":
        return "<span class='module-logo game-logo logo-nes'>NES</span>"
    if key == "SEGA":
        return "<span class='module-logo game-logo logo-sega'>SEGA</span>"
    if key == "DOS":
        return "<span class='module-logo game-logo logo-dos'>C:\\&gt;</span>"
    if key == "MAME":
        return "<span class='module-logo game-logo logo-mame'>MAME</span>"
    if key in {"ATARI", "2600", "5200", "7800"}:
        return "<span class='module-logo game-logo logo-mame'>ATARI</span>"
    return f"<span class='module-logo module-logo-text'>{compact}</span>"


def home_module_tabs(hostname: str | None = None) -> str:
    tabs = []
    for module in load_modules():
        if not module.get("enabled"):
            continue
        name = html.escape(str(module.get("name") or module.get("id") or "Module"))
        url = module_public_url(module, hostname)
        logo = module_logo_html(module)
        tabs.append(f'<a class="tab external" href="{html.escape(url)}">{logo}{name}</a>')
    return "".join(tabs)


def text_blob(*values) -> str:
    parts = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value if item)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values() if item)
        else:
            parts.append(str(value))
    return " ".join(parts).lower()


def metadata_search_blob(metadata: dict) -> str:
    actors = metadata.get("actors") or metadata.get("cast") or []
    genres = metadata.get("genres") or []
    directors = metadata.get("directors") or metadata.get("director") or []
    return text_blob(
        metadata.get("title"),
        metadata.get("name"),
        metadata.get("year"),
        metadata.get("overview"),
        metadata.get("summary"),
        metadata.get("tagline"),
        actors,
        genres,
        directors,
    )


def actor_names_for(metadata: dict) -> list[str]:
    values = metadata.get("actors") or metadata.get("cast") or []
    names: list[str] = []; seen: set[str] = set()
    for value in values:
        if isinstance(value, dict): value = value.get("name") or value.get("original_name") or ""
        name = re.sub(r"\s+", " ", str(value or "")).strip(); key = name.casefold()
        if name and key not in seen: seen.add(key); names.append(name)
    return names


def actor_name_norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").casefold()).strip()


def rebuild_actor_index() -> dict:
    rows = []
    for item in movie_app.movie_index.items:
        metadata = movie_app.metadata_for(item); title = str(metadata.get("title") or item.title)
        for name in actor_names_for(metadata): rows.append((name,actor_name_norm(name),"movie",int(item.id),title,str(metadata.get("year") or movie_app.human_size(item.size)),str(movie_app.poster_url_for(item) or ""),f"/movie/{item.id}"))
    for show in tv_app.tv_index.shows:
        metadata = tv_app.metadata_for(show); title = str(metadata.get("title") or show.title)
        for name in actor_names_for(metadata): rows.append((name,actor_name_norm(name),"tv",int(show.id),title,f"{show.count} episodes",str(tv_app.poster_url_for(show) or ""),f"/tv/show/{show.id}"))
    conn = db_connect()
    try:
        conn.execute("DELETE FROM actor_media"); conn.execute("DELETE FROM actors")
        for name,norm,media_type,media_id,title,subtitle,poster,href in rows:
            if not norm: continue
            conn.execute("INSERT OR IGNORE INTO actors(name,name_norm) VALUES(?,?)",(name,norm)); actor_id=conn.execute("SELECT id FROM actors WHERE name_norm=?",(norm,)).fetchone()["id"]
            conn.execute("INSERT OR REPLACE INTO actor_media(actor_id,media_type,media_id,title,subtitle,poster,href) VALUES(?,?,?,?,?,?,?)",(actor_id,media_type,media_id,title,subtitle,poster,href))
        conn.commit(); return {"actors":conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0],"links":conn.execute("SELECT COUNT(*) FROM actor_media").fetchone()[0]}
    finally: conn.close()


def actor_search_results(query: str, limit: int = 20) -> list[dict]:
    norm=actor_name_norm(query)
    if not norm: return []
    conn=db_connect()
    try:
        rows=conn.execute("SELECT a.name,COUNT(*) media_count,COALESCE(MAX(NULLIF(am.poster,'')),'') poster FROM actors a JOIN actor_media am ON am.actor_id=a.id WHERE a.name_norm LIKE ? GROUP BY a.id ORDER BY a.name LIMIT ?",(f"%{norm}%",limit)).fetchall()
        return [{"kind":"Actor","title":r["name"],"subtitle":f"{r['media_count']} library title(s)","poster":r["poster"],"href":f"/actor?name={urllib.parse.quote(r['name'])}"} for r in rows]
    finally: conn.close()


def actor_media_results(name: str) -> list[dict]:
    conn=db_connect()
    try:
        rows=conn.execute("SELECT am.media_type,am.title,am.subtitle,am.poster,am.href FROM actors a JOIN actor_media am ON am.actor_id=a.id WHERE a.name_norm=? ORDER BY am.media_type,am.title",(actor_name_norm(name),)).fetchall()
        return [{"kind":"Movie" if r["media_type"]=="movie" else "TV Show","title":r["title"],"subtitle":r["subtitle"],"poster":r["poster"],"href":r["href"]} for r in rows]
    finally: conn.close()


def unified_search_results(query: str, limit: int = 100, watched_keys: set[str] | None = None) -> list[dict]:
    terms = [term.lower() for term in re.findall(r"\w+", query or "")]
    if not terms:
        return []
    results: list[dict] = actor_search_results(query)
    for item in movie_app.movie_index.items:
        metadata = movie_app.metadata_for(item)
        title = metadata.get("title") or item.title
        year = metadata.get("year") or ""
        blob = text_blob(title, item.title, item.rel_path, metadata_search_blob(metadata))
        if all(term in blob for term in terms):
            results.append({
                "kind": "Movie",
                "key": movie_media_key(item),
                "title": title,
                "subtitle": str(year) if year else movie_app.human_size(item.size),
                "poster": movie_app.poster_url_for(item),
                "href": f"/movie/{item.id}",
            })
    for show in tv_app.tv_index.shows:
        metadata = tv_app.metadata_for(show)
        title = metadata.get("title") or show.title
        latest = latest_episode_for_show(show)
        subtitle = latest_episode_label(latest) if latest else f"{show.count} episodes"
        blob = text_blob(title, show.title, getattr(show, "rel_path", ""), getattr(show, "root", ""), metadata_search_blob(metadata))
        episode_hit = False
        for season in show.seasons.values():
            for episode in season.episodes:
                episode_meta = tv_episode_display_metadata(metadata, episode)
                episode_blob = text_blob(episode.title, episode.rel_path, episode_meta.get("title"), episode_meta.get("summary"), episode_meta.get("air_date"))
                if all(term in episode_blob for term in terms):
                    episode_hit = True
                    break
            if episode_hit:
                break
        if all(term in blob for term in terms) or episode_hit:
            results.append({
                "kind": "TV Show",
                "key": f"show:{show.id}",
                "title": title,
                "subtitle": subtitle,
                "poster": tv_app.poster_url_for(show),
                "href": f"/tv/show/{show.id}",
                "watched": show_watched(show, watched_keys),
            })
    return results[:limit]


def search_result_card(item: dict, watched_keys: set[str] | None = None) -> str:
    poster = str(item.get("poster") or "")
    poster_class = "" if poster else " missing"
    watched = bool(item.get("watched") or (watched_keys and item.get("key") in watched_keys))
    return (
        f"<a class='card{' watched' if watched else ''}' href='{html.escape(str(item.get('href') or '#'))}'>"
        f"<div class='poster{poster_class}'>{watched_badge_html(watched)}{home_poster_html(poster)}</div>"
        f"<div class='card-title'>{html.escape(str(item.get('title') or 'Untitled'))}</div>"
        f"<div class='card-meta'>{html.escape(str(item.get('kind') or ''))} &bull; {html.escape(str(item.get('subtitle') or ''))}</div>"
        f"</a>"
    )


def metadata_year(metadata: dict, fallback: str = "") -> int:
    value = str(metadata.get("year") or fallback or "")
    match = re.search(r"(19|20)\d{2}", value)
    return int(match.group(0)) if match else 0


def current_month_start() -> float:
    now = time.localtime()
    return time.mktime((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def recently_released_items(limit: int = 15) -> list[tuple[str, object, float]]:
    now = time.localtime()
    this_year = now.tm_year
    month_start = current_month_start()
    items: list[tuple[str, object, float]] = []
    for item in movie_app.movie_index.items:
        if not item.modified or item.modified < month_start:
            continue
        metadata = movie_app.metadata_for(item)
        if metadata_year(metadata, item.title) == this_year:
            items.append(("movie", item, item.modified))
    for show in tv_app.tv_index.shows:
        modified = tv_app.show_modified(show)
        if not modified or modified < month_start:
            continue
        metadata = tv_app.metadata_for(show)
        if metadata_year(metadata, show.title) == this_year:
            items.append(("tv", show, modified))
    return sorted(items, key=lambda entry: entry[2], reverse=True)[:limit]


def recently_released_section(items: list[tuple[str, object, float]], watched_keys: set[str] | None = None) -> str:
    if not items:
        return ""
    month_label = time.strftime("%B %Y", time.localtime())
    cards = []
    for kind, item, _modified in items:
        cards.append(home_movie_card(item, watched_keys) if kind == "movie" else home_show_card(item, watched_keys))
    return (
        "<section class='section'>"
        f"<div class='section-head'><div><h2>Recently Released This Month</h2><div class='sub'>Added in {html.escape(month_label)} and released in {time.localtime().tm_year}</div></div></div>"
        f"<div class='rail'>{''.join(cards)}</div>"
        "</section>"
    )


def latest_episode_for_show(show):
    episodes = [episode for season in show.seasons.values() for episode in season.episodes]
    if not episodes:
        return None
    return max(episodes, key=lambda episode: episode.modified)


def episode_sequence() -> list:
    episodes = [episode for show in tv_app.tv_index.shows for season in show.seasons.values() for episode in season.episodes]
    return sorted(
        episodes,
        key=lambda ep: (
            tv_app.natural_key(ep.show),
            tv_app.season_sort_key(ep.season),
            tv_app.episode_sort_key(ep.title),
            tv_app.natural_key(ep.rel_path),
        ),
    )


def next_episode_for(episode):
    episodes = [candidate for candidate in episode_sequence() if candidate.show == episode.show]
    for index, candidate in enumerate(episodes):
        if candidate.id == episode.id and index + 1 < len(episodes):
            return episodes[index + 1]
    return None


def previous_episode_for(episode):
    episodes = [candidate for candidate in episode_sequence() if candidate.show == episode.show]
    for index, candidate in enumerate(episodes):
        if candidate.id == episode.id and index > 0:
            return episodes[index - 1]
    return None


def show_for_episode(episode):
    for show in tv_app.tv_index.shows:
        if show.title == episode.show:
            return show
    return None


def episode_label(episode) -> str:
    match = re.search(r"[Ss](\d{1,2})\s*(?:EP?|x)\s*(\d{1,3})", episode.title, re.IGNORECASE)
    if match:
        return f"S{int(match.group(1))} - E{int(match.group(2))}"
    return episode.season


def tv_episode_display_metadata(metadata: dict, episode) -> dict:
    get_row = getattr(tv_app, "episode_metadata", None)
    get_title = getattr(tv_app, "display_episode_title", None)
    get_summary = getattr(tv_app, "episode_summary", None)
    get_date = getattr(tv_app, "episode_air_date", None)
    row = get_row(metadata, episode) if get_row else {}
    title = get_title(metadata, episode) if get_title else episode.title
    summary = get_summary(metadata, episode) if get_summary else ""
    air_date = get_date(metadata, episode) if get_date else ""
    return {
        "title": str(title or episode.title),
        "summary": str(summary or ""),
        "air_date": str(air_date or ""),
        "runtime": row.get("runtime") if isinstance(row, dict) else None,
        "vote_average": row.get("vote_average") if isinstance(row, dict) else None,
        "still_path": row.get("still_path") if isinstance(row, dict) else "",
    }


def latest_episode_label(episode) -> str:
    label = episode_label(episode)
    return label if label else episode.title


SEARCH_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Search</title>
<style>
:root { color-scheme:dark; --bg:#08090c; --panel:#11151d; --line:rgba(255,255,255,.12); --muted:#aeb7c5; --gold:#f5b73f; }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }
header { position:sticky; top:0; z-index:3; display:flex; align-items:center; gap:14px; padding:18px clamp(18px,4vw,42px); background:rgba(8,9,12,.92); border-bottom:1px solid var(--line); backdrop-filter:blur(14px); }
.brand { font-size:clamp(24px,4vw,44px); font-weight:950; letter-spacing:0; }
.cmv-logo { display:inline-flex; align-items:center; gap:.30em; color:#fff; text-decoration:none; text-transform:uppercase; line-height:.88; white-space:nowrap; }
.cmv-left { display:grid; gap:.08em; align-items:center; }
.cmv-cine,.cmv-media { font-size:.54em; font-weight:950; letter-spacing:.03em; line-height:.86; }
.cmv-divider { width:.052em; height:1.12em; background:rgba(255,255,255,.66); }
.cmv-vault { color:var(--gold); font-size:.95em; font-weight:950; letter-spacing:.02em; }
.cmv-mark { width:1.08em; height:1.08em; color:var(--gold); flex:0 0 auto; }
a { color:#fff; text-decoration:none; } .pill { border:1px solid var(--line); border-radius:999px; padding:11px 18px; background:#1a1f29; font-weight:850; }
main { padding:22px clamp(18px,4vw,42px) 80px; }
.search-row { display:flex; gap:12px; margin:10px 0 24px; max-width:980px; }
input { flex:1; min-height:48px; border-radius:15px; border:1px solid #344155; background:#151b25; color:#fff; padding:0 16px; font-size:18px; }
button { min-height:48px; border:0; border-radius:999px; padding:0 22px; font-weight:900; cursor:pointer; background:var(--gold); color:#111; }
.muted { color:var(--muted); } .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(145px, 1fr)); gap:24px 18px; }
.card { min-width:0; } .poster { aspect-ratio:2/3; border-radius:8px; overflow:hidden; background:#101722; border:1px solid rgba(255,255,255,.1); display:flex; align-items:center; justify-content:center; color:#9aa4b2; font-weight:800; }
.poster img { width:100%; height:100%; object-fit:cover; } .card-title { margin-top:10px; font-weight:900; line-height:1.15; } .card-meta { margin-top:4px; color:var(--muted); font-size:14px; }
@media (min-width:900px) { .grid { grid-template-columns:repeat(auto-fill, minmax(126px, 126px)); } }
@media (max-width:700px) { .cmv-mark { width:.80em; height:.80em; } .cmv-media { letter-spacing:.03em; } }
</style></head><body>
<header><a class="brand cmv-logo" href="/"><span class="cmv-left"><span class="cmv-cine">Cine</span><span class="cmv-media">Media</span></span><span class="cmv-divider"></span><span class="cmv-vault">Vault</span><svg class="cmv-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="32" cy="32" r="9" fill="none" stroke="currentColor" stroke-width="4"/><path d="M32 6v17M32 41v17M6 32h17M41 32h17M13.6 13.6l12 12M38.4 38.4l12 12M50.4 13.6l-12 12M25.6 38.4l-12 12" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="32" cy="32" r="3" fill="currentColor"/><path d="M32 32l5 4M32 32l-5 4M32 32v-6" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg></a><a class="pill" href="/">Home</a><a class="pill" href="/movies">Movies</a><a class="pill" href="/tv">TV Shows</a><a class="pill" href="/music">Music</a></header>
<main>
<form class="search-row" action="/search" method="get"><input name="q" value="{{QUERY}}" placeholder="Search movies, shows, actors, genres, or episodes" autofocus><button type="submit">Search</button></form>
<h1>{{TITLE}}</h1><p class="muted">{{SUBTITLE}}</p>
<div class="grid">{{RESULTS}}</div>
</main></body></html>"""


def continue_item_for_movie(item) -> dict:
    metadata = movie_app.metadata_for(item)
    title = metadata.get("title") or item.title
    year = metadata.get("year") or ""
    poster = movie_app.poster_url_for(item)
    return {
        "key": movie_media_key(item),
        "kind": "movie",
        "mediaId": int(item.id),
        "title": title,
        "subtitle": str(year) if year else movie_app.human_size(item.size),
        "poster": poster,
        "href": f"/player/movie/{item.id}",
        "detailHref": f"/movie/{item.id}",
    }


def continue_item_for_episode(episode) -> dict:
    show = show_for_episode(episode)
    poster = tv_app.poster_url_for(show) if show else ""
    metadata = tv_app.metadata_for(show) if show else {}
    episode_meta = tv_episode_display_metadata(metadata, episode)
    subtitle = episode_label(episode) or episode.season
    return {
        "key": f"tv:{episode.id}",
        "kind": "tv",
        "title": episode.show,
        "episodeTitle": episode_meta.get("title") or episode.title,
        "summary": episode_meta.get("summary") or "",
        "subtitle": subtitle,
        "poster": poster,
        "href": f"/player/tv/{episode.id}",
        "detailHref": f"/tv/show/{show.id}" if show else "/tv",
        "showId": show.id if show else 0,
        "season": episode.season,
    }


def continue_metadata_for_key(key: str) -> dict | None:
    if key.startswith("movie-path:"):
        item = movie_for_media_key(key)
        return continue_item_for_movie(item) if item else None
    if key.startswith("movie:"):
        try:
            item = movie_app.safe_item(key.split(":", 1)[1])
            return continue_item_for_movie(item)
        except Exception:
            return None
    if key.startswith("tv:"):
        try:
            episode = tv_app.safe_episode(key.split(":", 1)[1])
            return continue_item_for_episode(episode)
        except Exception:
            return None
    return None


def sqlite_timestamp_to_ms(value: str) -> int:
    try:
        return int(time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")) * 1000)
    except Exception:
        return int(time.time() * 1000)


def user_state_item(row) -> dict:
    return {
        "key": row["media_key"],
        "kind": row["media_type"],
        "title": row["title"] or "",
        "subtitle": row["subtitle"] or "",
        "poster": row["poster"] or "",
        "href": row["href"] or "",
        "detailHref": row["detail_href"] or "",
        "position": float(row["position_seconds"] or 0),
        "duration": float(row["duration_seconds"] or 0),
        "progress": float(row["progress"] or 0),
        "watched": bool(row["watched"]),
        "updatedAt": sqlite_timestamp_to_ms(row["updated_at"]),
    }


def continue_card_item(row) -> dict:
    item = user_state_item(row)
    if item.get("kind") == "movie":
        current = continue_metadata_for_key(str(item.get("key") or ""))
        if not current:
            current_movie = movie_for_saved_title(item.get("title", ""), item.get("subtitle", ""))
            current = continue_item_for_movie(current_movie) if current_movie else None
        if current:
            item.update({key: value for key, value in current.items() if key not in {"position", "duration", "progress", "watched", "updatedAt"}})
        return item
    if item.get("kind") != "tv":
        return item
    current = continue_metadata_for_key(str(item.get("key") or ""))
    if current and current.get("poster"):
        item["poster"] = current["poster"]
        item["title"] = current.get("title") or item["title"]
        item["subtitle"] = current.get("subtitle") or item["subtitle"]
        item["href"] = current.get("href") or item["href"]
        item["detailHref"] = current.get("detailHref") or item["detailHref"]
    return item


def migrate_legacy_movie_state_keys() -> int:
    """Convert scan-order movie IDs in saved state to stable path identities."""
    conn = db_connect()
    migrated = 0
    try:
        rows = conn.execute("SELECT * FROM user_media_state WHERE media_type='movie' AND media_key LIKE 'movie:%'").fetchall()
        for row in rows:
            movie = movie_for_saved_title(row["title"], row["subtitle"])
            if not movie:
                continue
            current = continue_item_for_movie(movie)
            existing = conn.execute(
                "SELECT 1 FROM user_media_state WHERE user_id=? AND media_key=?",
                (int(row["user_id"]), current["key"]),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM user_media_state WHERE user_id=? AND media_key=?", (int(row["user_id"]), row["media_key"]))
            else:
                conn.execute(
                    """UPDATE user_media_state SET media_key=?,media_id=?,title=?,subtitle=?,poster=?,href=?,detail_href=?
                       WHERE user_id=? AND media_key=?""",
                    (current["key"], int(movie.id), current["title"], current["subtitle"], current["poster"],
                     current["href"], current["detailHref"], int(row["user_id"]), row["media_key"]),
                )
            migrated += 1
        conn.commit()
        return migrated
    finally:
        conn.close()


def duration_minutes_from_metadata(metadata: dict) -> float:
    for key in ("runtime", "episode_run_time"):
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        if isinstance(value, list) and value:
            try:
                return float(value[0] or 0)
            except Exception:
                pass
    return 0.0


def library_stats() -> dict:
    movie_count = len(movie_app.movie_index.items)
    movie_size = sum(int(getattr(item, "size", 0) or 0) for item in movie_app.movie_index.items)
    movie_minutes = 0.0
    for item in movie_app.movie_index.items:
        movie_minutes += duration_minutes_from_metadata(movie_app.metadata_for(item))

    show_count = len(tv_app.tv_index.shows)
    episodes = [episode for show in tv_app.tv_index.shows for season in show.seasons.values() for episode in season.episodes]
    tv_size = sum(int(getattr(episode, "size", 0) or 0) for episode in episodes)
    tv_minutes = 0.0
    for episode in episodes:
        show = show_for_episode(episode)
        metadata = tv_app.metadata_for(show) if show else {}
        episode_meta = tv_episode_display_metadata(metadata, episode)
        runtime = episode_meta.get("runtime")
        if isinstance(runtime, (int, float)) and runtime > 0:
            tv_minutes += float(runtime)
    return {
        "movies": {"assets": movie_count, "size": movie_size, "runtime_minutes": movie_minutes},
        "tv": {"assets": len(episodes), "shows": show_count, "size": tv_size, "runtime_minutes": tv_minutes},
    }


def count_files(root: Path, extensions: set[str] | None = None, max_seconds: float = 2.0, max_files: int = 25000) -> tuple[int, int]:
    count = 0
    size = 0
    deadline = time.monotonic() + max_seconds
    try:
        if not root.exists():
            return 0, 0
        for path in root.rglob("*"):
            if count >= max_files or time.monotonic() > deadline:
                break
            try:
                if not path.is_file():
                    continue
                if extensions and path.suffix.lower() not in extensions:
                    continue
                count += 1
                size += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return count, size
    return count, size


def count_dirs(root: Path) -> int:
    try:
        if not root.exists():
            return 0
        return sum(1 for path in root.iterdir() if path.is_dir())
    except OSError:
        return 0


def comic_collection_count() -> int:
    collections_root = COMIC_LIBRARY_ROOT / "collections"
    count = count_dirs(collections_root)
    return count if count else count_dirs(COMIC_LIBRARY_ROOT)


def bookvault_stats() -> tuple[int, int]:
    try:
        data = json.loads(BOOKVAULT_INDEX_CACHE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            items = data.get("books") or data.get("items") or data.get("entries") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        if isinstance(items, list):
            size = 0
            for item in items:
                if isinstance(item, dict):
                    size += int(item.get("size_bytes") or item.get("bytes") or 0)
            return len(items), size
    except Exception:
        pass
    return count_files(BOOK_ROOT, {".epub", ".pdf", ".mobi", ".azw", ".azw3"}, max_seconds=1.0, max_files=10000)


def game_library_stats() -> dict:
    stats = {}
    total = 0
    for key, (root, extensions) in MODULE_GAME_ROOTS.items():
        count, size = count_files(root, extensions, max_seconds=0.7, max_files=10000)
        stats[key] = {"games": count, "size": size}
        total += count
    stats["total_games"] = total
    return stats


def homepage_status_payload(handler=None) -> dict:
    stats = library_stats()
    comics_count = comic_collection_count()
    comic_files, comic_size = 0, 0
    books_count, books_size = bookvault_stats()
    modules = load_modules()
    module_rows = []
    for module in modules:
        enabled = bool(module.get("enabled"))
        running = module_running(module)
        module_rows.append({
            "id": str(module.get("id") or ""),
            "name": str(module.get("name") or module.get("id") or ""),
            "enabled": enabled,
            "running": running,
            "url": module_public_url(module, handler.request_hostname() if handler else None),
        })
    hls_items = handler.hls_cache_items() if handler else []
    direct_items = handler.direct_stream_items() if handler else []
    mobile_items = handler.mobile_download_cache_items() if handler and hasattr(handler, "mobile_download_cache_items") else []
    hls_size = sum(int(item.get("size") or 0) for item in hls_items)
    mobile_size = sum(int(item.get("size") or 0) for item in mobile_items)
    games = game_library_stats()
    movie_assets = int(stats["movies"]["assets"])
    tv_episodes = int(stats["tv"]["assets"])
    tv_shows = int(stats["tv"]["shows"])
    return {
        "ok": True,
        "name": "CineMediaVault",
        "url": "https://192.168.1.20:5000",
        "movies": movie_assets,
        "movie_size": stats["movies"]["size"],
        "movie_size_label": movie_app.human_size(stats["movies"]["size"]),
        "tv_shows": tv_shows,
        "tv_episodes": tv_episodes,
        "tv_size": stats["tv"]["size"],
        "tv_size_label": movie_app.human_size(stats["tv"]["size"]),
        "comics": comics_count,
        "comic_files": comic_files,
        "comic_size": comic_size,
        "comic_size_label": movie_app.human_size(comic_size),
        "books": books_count,
        "book_size": books_size,
        "book_size_label": movie_app.human_size(books_size),
        "games": games["total_games"],
        "game_libraries": games,
        "nes_games": games.get("nes", {}).get("games", 0),
        "sega_games": games.get("sega", {}).get("games", 0),
        "dos_games": games.get("dos", {}).get("games", 0),
        "mame_games": games.get("mame", {}).get("games", 0) + games.get("arcade", {}).get("games", 0),
        "atari_games": games.get("atari2600", {}).get("games", 0) + games.get("atari5200", {}).get("games", 0) + games.get("atari7800", {}).get("games", 0),
        "handheld_games": games.get("gameboy", {}).get("games", 0) + games.get("gba", {}).get("games", 0),
        "active_streams": len(hls_items) + len(direct_items),
        "hls_streams": len([item for item in hls_items if item.get("running")]),
        "direct_streams": len(direct_items),
        "hls_cache_size": hls_size,
        "hls_cache_size_label": movie_app.human_size(hls_size),
        "mobile_download_jobs": len(mobile_items),
        "mobile_download_cache_size": mobile_size,
        "mobile_download_cache_size_label": movie_app.human_size(mobile_size),
        "modules": module_rows,
        "modules_enabled": len([item for item in module_rows if item["enabled"]]),
        "modules_running": len([item for item in module_rows if item["running"]]),
        "updated_at": auth_now(),
    }


def pending_user_count() -> int:
    conn = db_connect()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM pending_users WHERE status='pending'").fetchone()[0] or 0)
    finally:
        conn.close()


def format_runtime_minutes(minutes: float) -> str:
    minutes = int(minutes or 0)
    if minutes <= 0:
        return "Unknown"
    days, remainder = divmod(minutes, 1440)
    hours, mins = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins or not parts:
        parts.append(f"{mins}m")
    return " ".join(parts)


def action_link(icon: str, label: str, href: str, attrs: str = "") -> str:
    extra_class = ""
    if label.lower() == "download":
        extra_class = " download"
    elif label.lower().startswith("mark "):
        extra_class = " mark-watched"
    return f"<a class='action{extra_class}' href='{html.escape(href)}'{attrs}><span>{icon}</span>{html.escape(label)}</a>"


def player_nav_link(label: str, href: str) -> str:
    return f"<a href='{html.escape(href)}'>{html.escape(label)}</a>"


def actor_items_for(metadata: dict) -> str:
    actors = actor_names_for(metadata)
    return "".join(f"<li><a href='/actor?name={urllib.parse.quote(actor)}'>{html.escape(actor)}</a></li>" for actor in actors) or "<li>No actor data available yet.</li>"


SUBTITLE_TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
SUBTITLE_SIDECAR_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa"}
SUBTITLE_LANGUAGE_NAMES = {"eng": "English", "en": "English", "spa": "Spanish", "es": "Spanish", "fre": "French", "fra": "French", "fr": "French"}


def subtitle_language_label(code: str) -> str:
    normalized = str(code or "").strip().lower()
    return SUBTITLE_LANGUAGE_NAMES.get(normalized, normalized.upper() if normalized and normalized != "und" else "Unknown")


def discover_subtitles(path: Path, kind: str, item_id: str) -> list[dict]:
    path = path.resolve()
    stat = path.stat()
    directory_mtime = path.parent.stat().st_mtime_ns
    cache_key = str(path)
    cached = SUBTITLE_DISCOVERY_CACHE.get(cache_key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size and cached[2] == directory_mtime:
        return cached[3]
    tracks = []
    movie_stem = path.stem.casefold()
    for sidecar in sorted(path.parent.iterdir(), key=lambda value: value.name.casefold()):
        if not sidecar.is_file() or sidecar.suffix.lower() not in SUBTITLE_SIDECAR_EXTENSIONS:
            continue
        sidecar_stem = sidecar.stem.casefold()
        if not (sidecar_stem == movie_stem or sidecar_stem.startswith(movie_stem + ".") or sidecar_stem.startswith(movie_stem + " ")):
            continue
        suffix = sidecar.stem[len(path.stem):].strip(" ._-")
        language = suffix.split(".", 1)[0].split(" ", 1)[0].lower() if suffix else "eng"
        label = subtitle_language_label(language)
        if "forced" in suffix.casefold():
            label += " Forced"
        elif any(tag in re.split(r"[._\s-]+", suffix.casefold()) for tag in ("sdh", "hi")):
            label += " SDH"
        tracks.append({"token": f"sidecar-{len(tracks)}", "source": "sidecar", "path": str(sidecar), "stream": None,
                       "language": language, "label": f"{label} (SRT)"})
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries",
             "stream=index,codec_name:stream_tags=language,title:stream_disposition=default,forced,hearing_impaired",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=20, check=True,
        )
        streams = json.loads(probe.stdout or "{}").get("streams") or []
    except Exception:
        streams = []
    for stream in streams:
        codec = str(stream.get("codec_name") or "").lower()
        if codec not in SUBTITLE_TEXT_CODECS:
            continue
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        language = str(tags.get("language") or "und").lower()
        label = subtitle_language_label(language)
        if disposition.get("forced") and "forced" not in label.casefold():
            label += " Forced"
        elif disposition.get("hearing_impaired") and "sdh" not in label.casefold():
            label += " SDH"
        tracks.append({"token": f"embedded-{int(stream['index'])}", "source": "embedded", "path": str(path),
                       "stream": int(stream["index"]), "language": language, "label": f"{label} (Embedded SRT)"})
    for track in tracks:
        track["url"] = f"/subtitles/{kind}/{urllib.parse.quote(str(item_id))}/{track['token']}.vtt"
    SUBTITLE_DISCOVERY_CACHE[cache_key] = (stat.st_mtime, stat.st_size, directory_mtime, tracks)
    return tracks


def subtitle_markup(tracks: list[dict]) -> tuple[str, str, str]:
    track_html = "".join(
        f'<track kind="subtitles" src="{html.escape(track["url"])}" srclang="{html.escape(track["language"])}" label="{html.escape(track["label"])}"'
        f'{" default" if index == 0 else ""}>'
        for index, track in enumerate(tracks)
    )
    options = '<option value="off">Off</option>' + "".join(
        f'<option value="{index}">{html.escape(track["label"])}</option>' for index, track in enumerate(tracks)
    )
    control = f'<label class="caption-control">CC <select id="captionSelect" aria-label="Closed captions">{options}</select></label>' if tracks else ""
    summary = f"{len(tracks)} available" if tracks else "None found"
    return track_html, control, summary


def subtitle_detail_summary(path: Path) -> str:
    tracks = discover_subtitles(Path(path), "detail", "0")
    if not tracks:
        return "None"
    if any(track["source"] == "embedded" for track in tracks):
        return "Embedded SRT"
    return "SRT"


movie_app.subtitle_summary_for_path = subtitle_detail_summary
tv_app.subtitle_summary_for_path = subtitle_detail_summary


def direct_player_context(kind: str, item_id: str, is_admin: bool = False, playback_mode: str | None = None) -> dict:
    playback_mode = (playback_mode or read_global_playback_mode()).lower()
    if playback_mode not in {"direct", "hls"}:
        playback_mode = "direct"
    if kind == "movie":
        item = movie_app.safe_item(item_id)
        caption_tracks = discover_subtitles(item.path, kind, item_id)
        caption_track_html, caption_control, caption_summary = subtitle_markup(caption_tracks)
        metadata = movie_app.metadata_for(item)
        title = metadata.get("title") or item.title
        subtitle = str(metadata.get("year") or "Movie")
        poster = movie_app.poster_url_for(item)
        poster_html = f"<img class='poster-logo' src='{html.escape(poster)}' alt=''>" if poster else "<div class='poster-logo missing'>No Poster</div>"
        summary = metadata.get("overview") or "No summary details available yet."
        actor_items = actor_items_for(metadata)
        source = f"/play/{item.id}"
        video_label = "Local file stream"
        if playback_mode == "hls":
            stream = ensure_hls_stream(kind, item_id, item.path)
            source = stream["playlist_url"]
            video_label = "Adaptive HLS stream"
        meta = "".join(
            f"<span>{html.escape(value)}</span>"
            for value in [subtitle, movie_app.human_size(item.size), "Movie"]
            if value
        ) + "<span class='rating'>Local</span>"
        return {
            "title": title,
            "subtitle": subtitle,
            "back": f"/movie/{item.id}",
            "source": source,
            "item": continue_item_for_movie(item),
            "prev": None,
            "next": None,
            "poster": poster_html,
            "background": f" style=\"--poster-bg:url('{html.escape(poster)}')\"" if poster else "",
            "episode_title": "",
            "episode_or_title": title,
            "meta": meta,
            "summary": summary,
            "video_label": video_label,
            "source_size": item.size,
            "source_size_label": movie_app.human_size(item.size),
            "actions": "".join([
                action_link("&#8595;", "Download", f"/download/{item.id}", " data-mobile-download='1' data-mobile-scope='movie' data-mobile-id='" + html.escape(str(item.id)) + "'"),
                action_link("&#10003;", "Mark Watched", f"/movie/{item.id}", " data-mark-watched='1'"),
                action_link("&#8943;", "More", "#more", " data-more-toggle='1'"),
            ]),
            "actors": actor_items,
            "player_nav": "",
            "playback_mode": playback_mode,
            "caption_tracks": caption_track_html,
            "caption_control": caption_control,
            "caption_summary": caption_summary,
        }
    if kind == "tv":
        episode = tv_app.safe_episode(item_id)
        caption_tracks = discover_subtitles(episode.path, kind, item_id)
        caption_track_html, caption_control, caption_summary = subtitle_markup(caption_tracks)
        show = show_for_episode(episode)
        previous_episode = previous_episode_for(episode)
        next_episode = next_episode_for(episode)
        if show and hasattr(tv_app, "detail_art_url_for"):
            poster = tv_app.detail_art_url_for(show)
        else:
            poster = tv_app.poster_url_for(show) if show else ""
        metadata = tv_app.metadata_for(show) if show else {}
        episode_meta = tv_episode_display_metadata(metadata, episode)
        episode_title = episode_meta["title"]
        episode_air_date = episode_meta["air_date"]
        runtime = episode_meta["runtime"]
        runtime_label = f"{int(runtime)}m" if isinstance(runtime, (int, float)) and runtime else ""
        try:
            episode_rating = float(episode_meta["vote_average"] or 0)
        except (TypeError, ValueError):
            episode_rating = 0.0
        poster_html = f"<img class='poster-logo' src='{html.escape(poster)}' alt=''>" if poster else "<div class='poster-logo missing'>No Poster</div>"
        summary = episode_meta["summary"] or metadata.get("overview") or "No summary details available yet."
        actor_items = actor_items_for(metadata)
        show_href = f"/tv/show/{show.id}" if show else "/tv"
        season_href = f"{show_href}#season-{tv_app.slugify(episode.season)}"
        source = f"/play/episode/{episode.id}"
        video_label = "Local episode stream"
        if playback_mode == "hls":
            stream = ensure_hls_stream(kind, item_id, episode.path)
            source = stream["playlist_url"]
            video_label = "Adaptive HLS stream"
        meta_values = [episode_label(episode), episode_air_date, runtime_label, "TV Show"]
        meta = "".join(
            f"<span>{html.escape(value)}</span>"
            for value in meta_values
            if value
        )
        if episode_rating > 0:
            meta += f"<span class='rating'>TMDb {episode_rating:.1f}</span>"
        meta += "<span class='rating'>Local</span>"
        actions = [
            action_link("&#9635;", "Show", show_href),
            action_link("&#9776;", "Season", season_href),
            action_link("&#8595;", "Download", f"/download/episode/{episode.id}", " data-mobile-download='1' data-mobile-scope='episode' data-mobile-id='" + html.escape(str(episode.id)) + "'"),
            action_link("&#10003;", "Mark Watched", show_href, " data-mark-watched='1'"),
            action_link("&#10003;", "Mark Season", season_href, " data-mark-bulk='season'"),
            action_link("&#10003;", "Mark Show", show_href, " data-mark-bulk='show'"),
            action_link("&#8943;", "More", "#more", " data-more-toggle='1'"),
        ]
        if previous_episode:
            actions.append(action_link("&#9664;", "Previous", f"/player/tv/{previous_episode.id}"))
        if next_episode:
            actions.append(action_link("&#9654;", "Next", f"/player/tv/{next_episode.id}"))
        player_nav = []
        if previous_episode:
            player_nav.append(player_nav_link("Previous", f"/player/tv/{previous_episode.id}"))
        if next_episode:
            player_nav.append(player_nav_link("Next", f"/player/tv/{next_episode.id}"))
        return {
            "title": episode.show,
            "subtitle": f"{episode_label(episode)} - {episode_title}",
            "back": show_href,
            "source": source,
            "item": continue_item_for_episode(episode),
            "prev": continue_item_for_episode(previous_episode) if previous_episode else None,
            "next": continue_item_for_episode(next_episode) if next_episode else None,
            "poster": poster_html,
            "background": f" style=\"--poster-bg:url('{html.escape(poster)}')\"" if poster else "",
            "episode_title": episode_title,
            "episode_or_title": episode_title,
            "meta": meta,
            "summary": summary,
            "actors": actor_items,
            "video_label": video_label,
            "source_size": episode.size,
            "source_size_label": movie_app.human_size(episode.size),
            "actions": "".join(actions),
            "player_nav": "".join(player_nav),
            "playback_mode": playback_mode,
            "caption_tracks": caption_track_html,
            "caption_control": caption_control,
            "caption_summary": caption_summary,
        }
    raise FileNotFoundError("Unknown media kind")


def mobile_safe_name(value: str, default: str = "download") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:140] or default


def mobile_normalize_target_ratio(target_ratio: float | str | None = None) -> float:
    try:
        ratio = float(target_ratio) if target_ratio is not None else MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO
    except (TypeError, ValueError):
        ratio = MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO
    return min(max(ratio, 0.20), MOBILE_DOWNLOAD_MAX_OUTPUT_RATIO)


def mobile_target_key(target_ratio: float | str | None = None) -> str:
    # Lab compressed downloads use a fixed phone/tablet H.265 budget instead of
    # the old source-ratio slider. Keep the key policy-specific so stale H.264
    # cached files are not reused after encoder changes.
    codec_key = re.sub(r"[^a-z0-9]+", "", MOBILE_DOWNLOAD_VIDEO_CODEC.lower()) or "h265"
    return f"{codec_key}-{int(round(MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR))}mbph"


def mobile_job_id(scope: str, item_id: str, target_ratio: float | str | None = None) -> str:
    raw = f"{scope}:{item_id}:{mobile_target_key(target_ratio)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def mobile_cache_paths(scope: str, item_id: str, target_ratio: float | str | None = None) -> tuple[str, Path, Path]:
    job_id = mobile_job_id(scope, item_id, target_ratio)
    job_dir = MOBILE_DOWNLOAD_CACHE_DIR / job_id
    return job_id, job_dir, job_dir / "status.json"


def mobile_status_payload(job: dict) -> dict:
    payload = {key: value for key, value in job.items() if key != "thread"}
    if payload.get("ready") and payload.get("download_url"):
        payload["ready"] = True
    return payload


def mobile_load_ready_status(scope: str, item_id: str, target_ratio: float | str | None = None) -> dict | None:
    job_id, job_dir, status_file = mobile_cache_paths(scope, item_id, target_ratio)
    if not status_file.is_file():
        return None
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    output = job_dir / status.get("filename", "")
    if status.get("status") == "ready" and output.is_file():
        status["job_id"] = job_id
        status["ready"] = True
        status["download_url"] = f"/mobile-download/file/{job_id}"
        return status
    return None


def mobile_load_cached_status(scope: str, item_id: str, target_ratio: float | str | None = None) -> dict | None:
    job_id, job_dir, status_file = mobile_cache_paths(scope, item_id, target_ratio)
    if not status_file.is_file():
        return None
    try:
        status = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    status["job_id"] = job_id
    status["ready"] = False
    if status.get("status") == "ready":
        output = job_dir / status.get("filename", "")
        if output.is_file():
            status["ready"] = True
            status["download_url"] = f"/mobile-download/file/{job_id}"
    return status


def mobile_episode_records_for_scope(scope: str, item_id: str) -> tuple[str, list[dict], dict]:
    if scope == "movie":
        item = movie_app.safe_item(item_id)
        metadata = movie_app.metadata_for(item)
        title = metadata.get("title") or item.title
        return title, [{
            "path": item.path,
            "title": title,
            "filename": f"{mobile_safe_name(title)}.mp4",
            "summary": metadata.get("overview") or "",
            "poster": movie_app.poster_url_for(item),
            "kind": "movie",
        }], {"kind": "movie", "title": title, "poster": movie_app.poster_url_for(item), "summary": metadata.get("overview") or ""}
    if scope == "episode":
        episode = tv_app.safe_episode(item_id)
        show = show_for_episode(episode)
        metadata = tv_app.metadata_for(show) if show else {}
        title = tv_app.display_episode_title(metadata, episode) if hasattr(tv_app, "display_episode_title") else episode.title
        label = episode_label(episode)
        show_title = (metadata.get("title") or episode.show)
        return f"{show_title} - {label}", [{
            "path": episode.path,
            "title": title,
            "filename": f"{mobile_safe_name(show_title)} - {mobile_safe_name(label)} - {mobile_safe_name(title)}.mp4",
            "summary": tv_app.episode_summary(metadata, episode) if hasattr(tv_app, "episode_summary") else "",
            "poster": tv_app.poster_url_for(show) if show else "",
            "still": tv_app.episode_still_url(metadata, episode) if hasattr(tv_app, "episode_still_url") else "",
            "kind": "tv",
            "show": show_title,
            "season": episode.season,
            "episode": label,
        }], {"kind": "tv", "title": show_title, "season": episode.season, "poster": tv_app.poster_url_for(show) if show else "", "summary": metadata.get("overview") or ""}
    if scope == "season":
        season = tv_app.safe_season(item_id)
        show = next((candidate for candidate in tv_app.tv_index.shows if any(existing is season for existing in candidate.seasons.values())), None)
        metadata = tv_app.metadata_for(show) if show else {}
        show_title = metadata.get("title") or (show.title if show else "TV Show")
        records = []
        for episode in season.episodes:
            title = tv_app.display_episode_title(metadata, episode) if hasattr(tv_app, "display_episode_title") else episode.title
            label = episode_label(episode)
            records.append({
                "path": episode.path,
                "title": title,
                "filename": f"{mobile_safe_name(show_title)} - {mobile_safe_name(label)} - {mobile_safe_name(title)}.mp4",
                "summary": tv_app.episode_summary(metadata, episode) if hasattr(tv_app, "episode_summary") else "",
                "poster": tv_app.poster_url_for(show) if show else "",
                "still": tv_app.episode_still_url(metadata, episode) if hasattr(tv_app, "episode_still_url") else "",
                "kind": "tv",
                "show": show_title,
                "season": season.label,
                "episode": label,
            })
        return f"{show_title} - {season.label}", records, {"kind": "season", "title": show_title, "season": season.label, "poster": tv_app.poster_url_for(show) if show else "", "summary": metadata.get("overview") or ""}
    raise FileNotFoundError("Unknown mobile download scope")


def mobile_estimate_seconds(records: list[dict]) -> int:
    total_size = sum((record["path"].stat().st_size if record["path"].is_file() else 0) for record in records)
    total_duration = sum(ffprobe_duration(record["path"]) for record in records if record["path"].is_file())
    return max(30, int((total_size / (14 * 1024 * 1024)) + (total_duration * 0.16)))


def mobile_target_budget_bytes(path: Path, scale: float = 1.0) -> int:
    duration = max(1.0, ffprobe_duration(path))
    source_size = max(1, path.stat().st_size)
    duration_hours = duration / 3600.0
    target_bytes = int(MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR * 1024 * 1024 * duration_hours * max(0.05, scale))
    absolute_budget = int(source_size * MOBILE_DOWNLOAD_ABSOLUTE_MAX_OUTPUT_RATIO)
    # Never allow a prepared mobile download to be larger than the source.
    return max(1, min(target_bytes, absolute_budget))


def mobile_target_video_kbps(path: Path, target_ratio: float | str | None = None, scale: float = 1.0) -> int:
    duration = max(1.0, ffprobe_duration(path))
    target_bytes = mobile_target_budget_bytes(path, scale)
    audio_k = MOBILE_DOWNLOAD_AUDIO_KBPS
    container_overhead_k = MOBILE_DOWNLOAD_CONTAINER_OVERHEAD_KBPS
    video_k = int(((target_bytes * 8) / duration / 1000) - audio_k - container_overhead_k)
    return max(80, video_k)


def mobile_output_budget_bytes(path: Path, target_ratio: float | str | None = None, scale: float = 1.0) -> int:
    return int(mobile_target_budget_bytes(path, scale) * MOBILE_DOWNLOAD_TARGET_TOLERANCE)


def mobile_package_mime(path: Path) -> str:
    if path.suffix.lower() == ".zip":
        return "application/zip"
    if path.suffix.lower() == ".mp4":
        return "video/mp4"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def mobile_ffmpeg_transcode(src: Path, dst: Path, target_ratio: float | str | None = None, scale: float = 1.0) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vb = mobile_target_video_kbps(src, target_ratio, scale)
    command = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=w=1280:h=720:force_original_aspect_ratio=decrease:force_divisible_by=2,format=yuv420p",
        "-c:v", MOBILE_DOWNLOAD_VIDEO_CODEC, "-preset", MOBILE_DOWNLOAD_VIDEO_PRESET,
        "-tag:v", "hvc1",
        "-b:v", f"{vb}k", "-maxrate", f"{int(vb * 1.35)}k", "-bufsize", f"{max(vb * 2, 256)}k",
        "-c:a", "aac", "-b:a", f"{MOBILE_DOWNLOAD_AUDIO_KBPS}k", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def mobile_write_status(status_file: Path, job: dict) -> None:
    status_file.parent.mkdir(parents=True, exist_ok=True)
    data = {key: value for key, value in job.items() if key not in {"thread"}}
    status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def mobile_worker(scope: str, item_id: str, job_id: str) -> None:
    job = MOBILE_DOWNLOAD_JOBS[job_id]
    job_dir = Path(job["dir"])
    status_file = job_dir / "status.json"
    source_dir = job_dir / "source"
    output_dir = job_dir / "output"
    try:
        target_ratio = mobile_normalize_target_ratio(job.get("target_ratio"))
        title, records, manifest = mobile_episode_records_for_scope(scope, item_id)
        manifest["target_ratio"] = target_ratio
        source_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        job.update({"status": "running", "message": "Preparing local source copy", "progress": 2, "title": title})
        mobile_write_status(status_file, job)
        outputs = []
        total = max(1, len(records))
        for index, record in enumerate(records, 1):
            src = record["path"]
            local_src = source_dir / f"{index:03d}{src.suffix.lower()}"
            out = output_dir / record["filename"]
            job.update({"message": f"Copying {index} of {total}", "progress": int(((index - 1) / total) * 70)})
            mobile_write_status(status_file, job)
            shutil.copy2(src, local_src)
            job.update({"message": f"Compressing {index} of {total}", "progress": int(((index - 1) / total) * 70) + 8})
            mobile_write_status(status_file, job)
            mobile_ffmpeg_transcode(local_src, out, target_ratio)
            budget = mobile_output_budget_bytes(local_src, target_ratio)
            if out.is_file() and out.stat().st_size > budget:
                accepted = False
                for scale in (0.72, 0.55, 0.42, 0.32):
                    retry = out.with_suffix(f".retry-{int(scale * 100)}.mp4")
                    if retry.exists():
                        retry.unlink()
                    job.update({"message": f"Retrying {index} of {total} at lower bitrate", "progress": int(((index - 1) / total) * 70) + 12})
                    mobile_write_status(status_file, job)
                    mobile_ffmpeg_transcode(local_src, retry, target_ratio, scale=scale)
                    if retry.is_file() and retry.stat().st_size <= budget:
                        retry.replace(out)
                        accepted = True
                        break
                    if retry.exists():
                        retry.unlink()
                if not accepted:
                    if out.exists():
                        out.unlink()
                    target_percent = int(round(mobile_normalize_target_ratio(target_ratio) * 100))
                    raise RuntimeError(
                        f"Compressed output exceeded {target_percent}% target budget for {src.name}; "
                        f"refusing oversized mobile download"
                    )
            outputs.append((record, out))
            job.update({"progress": int((index / total) * 85), "message": f"Finished {index} of {total}"})
            mobile_write_status(status_file, job)
        manifest["schema"] = "cinemediavault-mobile-download-v1"
        manifest["generated_at"] = time.time()
        manifest["episodes"] = [
            {key: value for key, value in record.items() if key != "path"} | {"file": out.name, "size": out.stat().st_size}
            for record, out in outputs
        ]
        manifest_path = output_dir / "cinemediavault-mobile.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if scope == "season":
            filename = f"{mobile_safe_name(title)} - Mobile.zip"
            package = job_dir / filename
            job.update({"progress": 92, "message": "Building mobile season ZIP"})
            mobile_write_status(status_file, job)
            with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(manifest_path, arcname="cinemediavault-mobile.json")
                for _, out in outputs:
                    archive.write(out, arcname=out.name)
        else:
            package = outputs[0][1]
            filename = str(package.relative_to(job_dir))
        shutil.rmtree(source_dir, ignore_errors=True)
        job.update({
            "status": "ready", "ready": True, "progress": 100, "message": "Ready",
            "filename": filename, "content_type": mobile_package_mime(package),
            "size": package.stat().st_size, "download_url": f"/mobile-download/file/{job_id}",
            "completed_at": time.time(), "expires_at": time.time() + mobile_download_cache_max_age_hours() * 3600,
        })
        mobile_write_status(status_file, job)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        tail = "\n".join(stderr.splitlines()[-30:])
        err_file = job_dir / "ffmpeg-error.log"
        err_file.write_text(tail, encoding="utf-8", errors="replace")
        concise = next((line.strip() for line in reversed(stderr.splitlines()) if line.strip()), "")
        if not concise or concise == "Conversion failed!":
            concise = f"ffmpeg failed with exit status {exc.returncode}"
        job.update({
            "status": "error", "ready": False, "message": concise[:240], "error": concise[:240],
            "ffmpeg_error_tail": tail, "ffmpeg_error_log": str(err_file), "completed_at": time.time(),
        })
        mobile_write_status(status_file, job)
    except Exception as exc:
        message = str(exc)
        job.update({"status": "error", "ready": False, "message": message[:240], "error": message[:240], "completed_at": time.time()})
        mobile_write_status(status_file, job)


def mobile_enqueue(scope: str, item_id: str, target_ratio: float | str | None = None) -> dict:
    target_ratio = mobile_normalize_target_ratio(target_ratio)
    job_id, job_dir, status_file = mobile_cache_paths(scope, item_id, target_ratio)
    ready = mobile_load_ready_status(scope, item_id)
    if ready:
        return ready
    with MOBILE_DOWNLOAD_LOCK:
        existing = MOBILE_DOWNLOAD_JOBS.get(job_id)
        if existing and existing.get("status") in {"queued", "running"}:
            payload = mobile_status_payload(existing)
            payload["message"] = payload.get("message") or "Already in progress"
            return payload
        cached = mobile_load_cached_status(scope, item_id)
        if cached and cached.get("status") in {"queued", "running"}:
            cached["message"] = cached.get("message") or "Already in progress"
            return cached
        if cached and cached.get("status") == "error":
            shutil.rmtree(job_dir, ignore_errors=True)
        title, records, _ = mobile_episode_records_for_scope(scope, item_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "job_id": job_id, "scope": scope, "item_id": item_id, "target_ratio": target_ratio, "target_percent": int(round(target_ratio * 100)), "target_mb_per_hour": MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR, "video_codec": MOBILE_DOWNLOAD_VIDEO_CODEC, "title": title, "dir": str(job_dir),
            "status": "queued", "ready": False, "progress": 0, "message": f"Added to H.265 mobile download queue ({int(MOBILE_DOWNLOAD_TARGET_MB_PER_HOUR)} MB/hour)",
            "estimate_seconds": mobile_estimate_seconds(records), "created_at": time.time(),
        }
        thread = threading.Thread(target=mobile_worker, args=(scope, item_id, job_id), daemon=True)
        job["thread"] = thread
        MOBILE_DOWNLOAD_JOBS[job_id] = job
        mobile_write_status(status_file, job)
        thread.start()
        return mobile_status_payload(job)


def mobile_download_cache_max_age_hours() -> float:
    try:
        if MOBILE_DOWNLOAD_SETTINGS_FILE.is_file():
            data = json.loads(MOBILE_DOWNLOAD_SETTINGS_FILE.read_text(encoding="utf-8"))
            hours = int(data.get("cache_max_age_hours") or MOBILE_DOWNLOAD_CACHE_MAX_AGE_HOURS)
            if hours in MOBILE_DOWNLOAD_CACHE_MAX_AGE_OPTIONS:
                return float(hours)
    except Exception:
        pass
    env_hours = int(MOBILE_DOWNLOAD_CACHE_MAX_AGE_HOURS)
    return float(env_hours if env_hours in MOBILE_DOWNLOAD_CACHE_MAX_AGE_OPTIONS else 24)


def save_mobile_download_cache_max_age(hours: int) -> int:
    if hours not in MOBILE_DOWNLOAD_CACHE_MAX_AGE_OPTIONS:
        raise ValueError("Download cache retention must be 6, 12, or 24 hours")
    MOBILE_DOWNLOAD_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MOBILE_DOWNLOAD_SETTINGS_FILE.write_text(
        json.dumps({"cache_max_age_hours": hours, "updated_at": auth_now()}, indent=2),
        encoding="utf-8",
    )
    return hours


def cleanup_mobile_download_cache_once() -> None:
    cutoff = time.time() - (mobile_download_cache_max_age_hours() * 3600)
    MOBILE_DOWNLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for child in MOBILE_DOWNLOAD_CACHE_DIR.iterdir():
        if not child.is_dir():
            continue
        status_file = child / "status.json"
        try:
            status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {}
            completed = float(status.get("completed_at") or child.stat().st_mtime)
            if status.get("status") in {"queued", "running"}:
                continue
            if completed < cutoff:
                shutil.rmtree(child, ignore_errors=True)
        except Exception:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)


LIVE_TV_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Live TV</title><style>
:root{color-scheme:dark;--bg:#080a0f;--panel:#121720;--line:#2a3340;--muted:#9ba7b8;--gold:#f5b73f;--green:#35dc79}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#fff;font-family:Inter,system-ui,Segoe UI,sans-serif}header{position:sticky;top:0;z-index:4;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;background:rgba(8,10,15,.95);border-bottom:1px solid var(--line)}h1{font-size:24px;margin:0}h1 b{color:var(--gold)}button,.button{border:1px solid var(--line);border-radius:8px;min-height:40px;padding:0 14px;background:#1a202a;color:#fff;font-weight:850;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;cursor:pointer}.primary{background:var(--gold);border-color:var(--gold);color:#111}main{max-width:1200px;margin:auto;padding:18px}.summary{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:16px}.device{border:1px solid var(--line);background:var(--panel);padding:12px;border-radius:8px}.device small{display:block;color:var(--muted);margin-top:4px}.search{width:100%;min-height:48px;border:1px solid var(--line);border-radius:8px;background:#151b25;color:#fff;padding:0 14px;font-size:16px;margin-bottom:14px}.guide{display:grid;gap:8px}.channel{display:grid;grid-template-columns:72px 1fr auto;gap:12px;align-items:center;padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}.number{font-size:20px;color:var(--gold);font-weight:950}.channel small{display:block;color:var(--muted);margin-top:3px}.actions{display:flex;gap:7px}.live{color:var(--green);font-size:12px;font-weight:950}.slots{display:none;gap:5px}.channel.choosing .slots{display:flex}.channel.choosing .watch{display:none}.slots button{width:36px;padding:0}.player{display:none;position:fixed;inset:0;z-index:20;background:#000}.player.open{display:block}.player video{width:100%;height:100%;object-fit:contain}.player .close{position:absolute;right:16px;top:16px;z-index:2;border-radius:50%;width:44px;padding:0}@media(max-width:650px){header{align-items:flex-start}.channel{grid-template-columns:58px 1fr}.actions{grid-column:1/-1}.channel{padding:10px}h1{font-size:20px}}
</style></head><body><header><h1>CineMedia<b>Vault</b> Live TV</h1><div><a class="button" href="/">Home</a> <a class="button" href="/wall">Wall</a> <button id="refreshGuide">Refresh guide</button> <button class="primary" id="scan">Scan tuners</button></div></header><main><div class="summary" id="devices"></div><input class="search" id="query" type="search" placeholder="Search channel, program, or description"><div class="guide" id="guide"></div></main><div class="player" id="player"><button class="close" id="close">&#10005;</button><video id="video" controls autoplay playsinline></video></div><script src="/assets/hls.min.js"></script><script>
let channels=[],hls=null;const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clock=n=>n?new Date(n*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):'';
async function load(){const r=await fetch('/api/hdhr/devices',{cache:'no-store'}),d=await r.json();channels=d.channels||[];const updated=d.guide_updated_at?new Date(d.guide_updated_at*1000).toLocaleString():'not loaded';document.getElementById('devices').innerHTML=(d.devices||[]).map(x=>`<div class="device"><strong>${esc(x.friendly_name)} ${esc(x.model_number)}</strong><small>${esc(x.device_id)} · ${x.tuner_count} tuners · ${x.channel_count} channels</small><small>${d.programme_count||0} guide entries · updated ${esc(updated)}</small><small><a href="${esc(x.base_url)}" target="_blank">Device resources</a></small></div>`).join('')||'<p>No tuner saved yet. Select Scan tuners.</p>';render()}
function render(){const q=document.getElementById('query').value.trim().toLowerCase();const text=c=>`${c.guide_number} ${c.guide_name} ${c.current?.title||''} ${c.current?.subtitle||''} ${c.current?.description||''} ${c.next?.title||''}`.toLowerCase();const list=channels.filter(c=>!q||text(c).includes(q));document.getElementById('guide').innerHTML=list.map(c=>`<article class="channel" data-id="${c.id}"><div class="number">${esc(c.guide_number)}</div><div><strong>${esc(c.guide_name)}</strong>${c.current?`<small><b>${clock(c.current.start)}-${clock(c.current.stop)} · ${esc(c.current.title)}</b>${c.current.subtitle?' · '+esc(c.current.subtitle):''}</small><small>${esc(c.current.description||c.current.category||'')}</small>`:'<small>Program information unavailable</small>'}${c.next?`<small>Next ${clock(c.next.start)} · ${esc(c.next.title)}</small>`:''}<small><span class="live">LIVE</span> · ${esc(c.friendly_name)}${c.video_codec?' · '+esc(c.video_codec):''}${c.audio_codec?' / '+esc(c.audio_codec):''}</small></div><div class="actions"><button class="watch">Watch</button><button class="wall">Add to wall</button><span class="slots">${[1,2,3,4].map(n=>`<button data-slot="${n}">${n}</button>`).join('')}</span></div></article>`).join('')||'<p>No matching channels or programs.</p>'}
document.getElementById('query').oninput=render;document.getElementById('scan').onclick=async()=>{const b=document.getElementById('scan');b.disabled=true;b.textContent='Scanning...';try{const r=await fetch('/api/hdhr/scan',{method:'POST'}),d=await r.json();if(!d.ok)alert(d.error||'Scan failed');await load()}finally{b.disabled=false;b.textContent='Scan tuners'}};
document.getElementById('refreshGuide').onclick=async()=>{const b=document.getElementById('refreshGuide');b.disabled=true;b.textContent='Refreshing...';try{const r=await fetch('/api/hdhr/guide/refresh',{method:'POST'}),d=await r.json();if(!d.ok)alert(d.error||'Guide refresh failed');await load()}finally{b.disabled=false;b.textContent='Refresh guide'}};
document.getElementById('guide').onclick=async e=>{const row=e.target.closest('.channel');if(!row)return;const id=+row.dataset.id;if(e.target.closest('.watch'))play(id);if(e.target.closest('.wall'))row.classList.toggle('choosing');const slot=e.target.closest('[data-slot]');if(slot){await fetch('/api/video-wall/slot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot:+slot.dataset.slot,media_type:'tuner',media_id:id})});row.classList.remove('choosing');alert(`Live channel added to wall ${slot.dataset.slot}`)}};
function play(id){const v=document.getElementById('video'),url=`/hls/tuner/${id}/index.m3u8`;document.getElementById('player').classList.add('open');if(hls){hls.destroy();hls=null}if(v.canPlayType('application/vnd.apple.mpegurl'))v.src=url;else if(window.Hls&&Hls.isSupported()){hls=new Hls({liveSyncDurationCount:3});hls.loadSource(url);hls.attachMedia(v)}else v.src=url;v.play().catch(()=>{})}document.getElementById('close').onclick=()=>{const v=document.getElementById('video');v.pause();v.removeAttribute('src');v.load();if(hls){hls.destroy();hls=null}document.getElementById('player').classList.remove('open')};load();
</script></body></html>"""


VIDEO_WALL_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Video Wall</title><style>
:root{color-scheme:dark;--bg:#07090d;--panel:#11161f;--line:#2b3442;--text:#f7f9fc;--muted:#9ca8b8;--gold:#f5b73f;--green:#36dc78}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,system-ui,Segoe UI,sans-serif;overflow-x:hidden}
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;background:rgba(7,9,13,.94);border-bottom:1px solid var(--line);backdrop-filter:blur(14px)}
.brand{font-size:24px;font-weight:950}.brand b{color:var(--gold)}.controls{display:flex;gap:8px;flex-wrap:wrap}button,.button{min-height:38px;border:1px solid var(--line);border-radius:8px;padding:0 13px;background:#171d27;color:#fff;font-weight:850;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.primary{background:var(--gold);color:#111;border-color:var(--gold)}
main{padding:14px}.wall{height:calc(100vh - 92px);min-height:520px;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:10px}.tile{position:relative;min-width:0;min-height:0;overflow:hidden;background:#000;border:2px solid transparent;border-radius:8px}.tile.active{border-color:var(--green)}.tile.expanded{position:fixed;inset:0;z-index:30;border:0;border-radius:0}.tile video{width:100%;height:100%;object-fit:contain;background:#000}.empty{height:100%;display:grid;place-items:center;text-align:center;color:var(--muted);border:1px dashed var(--line);padding:18px}.tile-bar{position:absolute;left:0;right:0;bottom:0;display:grid;grid-template-columns:auto auto minmax(90px,1fr) auto auto auto auto;align-items:center;gap:7px;padding:26px 9px 9px;background:linear-gradient(transparent,rgba(0,0,0,.94));opacity:0;transition:opacity .18s}.tile:hover .tile-bar,.tile.active .tile-bar,.tile.expanded .tile-bar{opacity:1}.tile-title{position:absolute;left:10px;right:10px;bottom:50px;min-width:0;font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 3px #000}.icon{width:34px;height:34px;padding:0;border-radius:50%}.seek{width:100%;accent-color:var(--gold)}.time{font-size:12px;color:#fff;white-space:nowrap;font-variant-numeric:tabular-nums}.bandwidth{display:none;align-items:center;gap:14px;padding:9px 14px;border-bottom:1px solid var(--line);background:#101620}.bandwidth.open{display:flex}.bandwidth strong{font-size:18px;color:var(--green);font-variant-numeric:tabular-nums}.bandwidth span{color:var(--muted);font-size:13px}
.drawer{position:fixed;inset:0 0 0 auto;z-index:10;width:min(440px,100%);padding:18px;background:#0c1017;border-left:1px solid var(--line);transform:translateX(105%);transition:transform .2s;overflow:auto}.drawer.open{transform:none}.drawer-head{display:flex;justify-content:space-between;align-items:center}.search{width:100%;min-height:48px;margin:16px 0;border:1px solid var(--line);border-radius:8px;background:#171d27;color:#fff;padding:0 14px;font-size:16px}.results{display:grid;gap:9px}.result{display:grid;grid-template-columns:52px 1fr auto;align-items:center;gap:10px;border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--panel)}.result img{width:52px;aspect-ratio:2/3;object-fit:cover;background:#06080c}.result small{display:block;color:var(--muted);margin-top:3px}.slot-picker{display:flex;gap:5px}.slot-picker button{width:34px;height:34px;min-height:34px;padding:0}.hint{color:var(--muted);font-size:13px}
@media(max-width:720px){header{align-items:flex-start;flex-direction:column}.wall{height:auto;min-height:0;grid-template-columns:1fr;grid-template-rows:none}.tile{aspect-ratio:16/9}.tile-bar{opacity:1}.brand{font-size:20px}}
</style></head><body><header><div class="brand">CineMedia<b>Vault</b> Wall</div><div class="controls"><a class="button" href="/">Home</a><button id="playAll" class="primary">Play all</button><button id="pauseAll">Pause all</button><button id="syncAll">Sync</button><button id="toggleBandwidth">Bandwidth</button><button id="addMedia">Add media</button></div></header><div class="bandwidth" id="bandwidthPanel"><strong id="bandwidthValue">0.00 Mbps</strong><span id="bandwidthBytes">0 B/s</span><span id="playingCount">0 playing</span></div>
<main><div class="wall" id="wall"></div></main>
<aside class="drawer" id="drawer"><div class="drawer-head"><div><h2>Add to wall</h2><div class="hint">Search a movie, episode, or live tuner channel, then choose a slot.</div></div><button class="icon" id="closeDrawer" aria-label="Close">&#10005;</button></div><input class="search" id="wallSearch" type="search" placeholder="Search movies, episodes, or live channels"><div class="results" id="results"></div></aside>
<script src="/assets/hls.min.js"></script><script>
const wall=document.getElementById('wall'),drawer=document.getElementById('drawer'),search=document.getElementById('wallSearch'),results=document.getElementById('results');let slots=[],active=1,timer,hlsControllers=[];
const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const r=await fetch('/api/video-wall');const d=await r.json();slots=d.slots||[];render()}
function fmt(t){if(!Number.isFinite(t))return '0:00';t=Math.max(0,Math.floor(t));return `${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`}
function render(){hlsControllers.forEach(h=>h.destroy());hlsControllers=[];wall.innerHTML='';for(let n=1;n<=4;n++){const item=slots.find(x=>x.slot===n),el=document.createElement('section');el.className='tile'+(n===active?' active':'');el.dataset.slot=n;if(!item){el.innerHTML=`<button class="empty" data-add="${n}">Slot ${n}<br>Add a movie or episode</button>`}else{const source=item.requires_hls?item.hls_url:item.stream_url;el.innerHTML=`<video playsinline preload="metadata" muted data-source="${esc(source)}" data-hls="${item.requires_hls?'1':'0'}" data-slot="${n}"></video><div class="tile-bar"><span class="tile-title">${esc(item.title)} <small>${esc(item.subtitle)}${item.requires_hls?' - HLS':''}</small></span><button class="icon" data-back title="Rewind 10 seconds">-10</button><button class="icon" data-toggle title="Play or pause">&#9654;</button><input class="seek" data-seek type="range" min="0" max="1000" value="0" aria-label="Seek"><span class="time">0:00 / 0:00</span><button class="icon" data-forward title="Forward 10 seconds">+10</button><button class="icon" data-fullscreen title="Full screen">&#9974;</button><button class="icon" data-remove="${n}" title="Remove">&#10005;</button></div>`}wall.appendChild(el)}bindVideos();applyAudio();updateBandwidth()}
function wallUrl(url,slot){const join=url.includes('?')?'&':'?';return `${url}${join}wall=1&slot=${slot}`}
function bindVideos(){document.querySelectorAll('.tile video').forEach(v=>{const tile=v.closest('.tile'),seek=tile.querySelector('[data-seek]'),label=tile.querySelector('.time'),toggle=tile.querySelector('[data-toggle]'),source=v.dataset.source,slot=v.dataset.slot;if(v.dataset.hls==='1'){if(v.canPlayType('application/vnd.apple.mpegurl'))v.src=wallUrl(source,slot);else if(window.Hls&&Hls.isSupported()){const h=new Hls({lowLatencyMode:false,backBufferLength:90,xhrSetup:(xhr,url)=>xhr.open('GET',wallUrl(url,slot),true)});h.loadSource(wallUrl(source,slot));h.attachMedia(v);hlsControllers.push(h)}else v.src=wallUrl(source,slot)}else v.src=wallUrl(source,slot);const update=()=>{if(seek&&Number.isFinite(v.duration)&&v.duration>0)seek.value=Math.round(v.currentTime/v.duration*1000);if(label)label.textContent=`${fmt(v.currentTime)} / ${fmt(v.duration)}`;if(toggle)toggle.innerHTML=v.paused?'&#9654;':'&#10074;&#10074;'};v.addEventListener('timeupdate',update);v.addEventListener('durationchange',update);v.addEventListener('play',update);v.addEventListener('pause',update);update()})}
function applyAudio(){document.querySelectorAll('.tile').forEach(t=>{t.classList.toggle('active',+t.dataset.slot===active);const v=t.querySelector('video');if(v)v.muted=+t.dataset.slot!==active})}
wall.addEventListener('click',async e=>{const tile=e.target.closest('.tile');if(tile){active=+tile.dataset.slot;applyAudio()}const v=tile&&tile.querySelector('video');if(e.target.closest('[data-add]'))openDrawer(+e.target.closest('[data-add]').dataset.add);if(e.target.closest('[data-remove]')){await updateSlot(+e.target.closest('[data-remove]').dataset.remove,null);load()}if(v&&e.target.closest('[data-back]'))v.currentTime=Math.max(0,v.currentTime-10);if(v&&e.target.closest('[data-forward]'))v.currentTime=Math.min(Number.isFinite(v.duration)?v.duration:v.currentTime+10,v.currentTime+10);if(v&&e.target.closest('[data-toggle]'))v.paused?v.play().catch(()=>{}):v.pause();if(tile&&e.target.closest('[data-fullscreen]')){if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});else if(tile.requestFullscreen)tile.requestFullscreen().catch(()=>tile.classList.toggle('expanded'));else if(tile.webkitRequestFullscreen)tile.webkitRequestFullscreen();else tile.classList.toggle('expanded')}});
wall.addEventListener('input',e=>{const seek=e.target.closest('[data-seek]');if(!seek)return;const v=seek.closest('.tile').querySelector('video');if(v&&Number.isFinite(v.duration))v.currentTime=(+seek.value/1000)*v.duration});
function openDrawer(slot){active=slot||active;drawer.classList.add('open');search.focus()}
document.getElementById('addMedia').onclick=()=>openDrawer(active);document.getElementById('closeDrawer').onclick=()=>drawer.classList.remove('open');
document.getElementById('playAll').onclick=()=>document.querySelectorAll('video').forEach(v=>v.play().catch(()=>{}));document.getElementById('pauseAll').onclick=()=>document.querySelectorAll('video').forEach(v=>v.pause());document.getElementById('syncAll').onclick=()=>{const vs=[...document.querySelectorAll('video')];if(!vs.length)return;const t=Math.min(...vs.map(v=>v.currentTime||0));vs.forEach(v=>v.currentTime=t)};
const bandwidthPanel=document.getElementById('bandwidthPanel');document.getElementById('toggleBandwidth').onclick=()=>{bandwidthPanel.classList.toggle('open');localStorage.setItem('cmv-wall-bandwidth',bandwidthPanel.classList.contains('open')?'1':'0');updateBandwidth()};if(localStorage.getItem('cmv-wall-bandwidth')==='1')bandwidthPanel.classList.add('open');
function humanRate(n){if(n>=1048576)return (n/1048576).toFixed(2)+' MB/s';if(n>=1024)return (n/1024).toFixed(1)+' KB/s';return Math.round(n)+' B/s'}async function updateBandwidth(){const playing=[...document.querySelectorAll('video')].filter(v=>!v.paused&&!v.ended).length;document.getElementById('playingCount').textContent=`${playing} playing`;if(!bandwidthPanel.classList.contains('open'))return;try{const r=await fetch('/api/video-wall/bandwidth',{cache:'no-store'}),d=await r.json();document.getElementById('bandwidthValue').textContent=`${Number(d.mbps||0).toFixed(2)} Mbps`;document.getElementById('bandwidthBytes').textContent=humanRate(Number(d.bytes_per_second||0))}catch(_){document.getElementById('bandwidthValue').textContent='Unavailable'}}setInterval(updateBandwidth,1000);
async function updateSlot(slot,item){await fetch('/api/video-wall/slot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slot,media_type:item&&item.media_type,media_id:item&&item.media_id})})}
search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(runSearch,250)});async function runSearch(){const q=search.value.trim();if(q.length<2){results.innerHTML='';return}const r=await fetch('/api/video-wall/search?q='+encodeURIComponent(q));const d=await r.json();results.innerHTML=(d.items||[]).map(i=>`<article class="result">${i.poster?`<img src="${esc(i.poster)}" alt="">`:'<span></span>'}<div><strong>${esc(i.title)}</strong><small>${esc(i.subtitle)}</small></div><div class="slot-picker">${[1,2,3,4].map(n=>`<button data-pick="${n}" data-kind="${i.media_type}" data-id="${i.media_id}">${n}</button>`).join('')}</div></article>`).join('')||'<p class="hint">No matches.</p>'}
results.addEventListener('click',async e=>{const b=e.target.closest('[data-pick]');if(!b)return;await updateSlot(+b.dataset.pick,{media_type:b.dataset.kind,media_id:+b.dataset.id});active=+b.dataset.pick;drawer.classList.remove('open');load()});load();
</script></body></html>"""


class CombinedHandler(VideoListsMixin, BaseHTTPRequestHandler):
    server_version = "CombinedMediaLibrary/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def do_HEAD(self):
        return self.dispatch(head=True)

    def do_GET(self):
        return self.dispatch(head=False)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/music/"):
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            handled = music_module.handle_post(self, user, path)
            if handled is not False:
                return handled
        if path == "/login":
            return self.login_submit()
        if path == "/signup":
            return self.signup_submit()
        if path == "/admin/users":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            return self.admin_users_submit(user)
        if path == "/admin/hls":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            return self.admin_hls_submit(user)
        if path == "/admin/modules":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            return self.admin_modules_submit(user)
        if path == "/admin/modules/logo":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            return self.admin_module_logo_submit(user)
        if path == "/admin/media/delete":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            return self.admin_media_delete_submit(user)
        if path == "/api/watch/progress":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_watch_progress(user)
        if path == "/api/watch/watched":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_watch_watched(user)
        if path == "/api/watch/bulk-watched":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_watch_bulk_watched(user)
        if path == "/api/video/queue":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_video_queue(user)
        if path == "/api/video/playlists":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_video_playlists(user)
        if path == "/api/playback-mode":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_playback_mode_set(user)
        if path == "/api/mobile-download/enqueue":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_mobile_download_enqueue(user)
        if path == "/api/video-wall/slot":
            user = self.current_user()
            if not user:
                return self.require_auth(path)
            return self.api_video_wall_slot(user)
        if path == "/api/hdhr/scan":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            try:
                result = scan_hdhr_devices()
                result["guide"] = refresh_hdhr_guide(True)
                return self.json_response(result)
            except Exception as exc:
                return self.json_response({"ok": False, "error": str(exc)})
        if path == "/api/hdhr/guide/refresh":
            user = self.current_user()
            if not user or not user["is_admin"]:
                return self.send_error(403)
            try:
                return self.json_response(refresh_hdhr_guide(True))
            except Exception as exc:
                return self.json_response({"ok": False, "error": str(exc)})
        if path.startswith("/movie/upload-art/"):
            if not self.require_auth(path):
                return
            return movie_app.Handler.upload_art(self, path.rsplit("/", 1)[-1])
        if path.startswith("/tv/upload-art/"):
            if not self.require_auth(path):
                return
            return tv_app.Handler.upload_art(self, path.rsplit("/", 1)[-1])
        self.send_error(404)

    def read_form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return {key: values[-1] for key, values in urllib.parse.parse_qs(body, keep_blank_values=True).items()}

    def request_hostname(self) -> str:
        host = (self.headers.get("Host") or SERVER_DISPLAY_NAME).split(":", 1)[0].strip()
        return host or SERVER_DISPLAY_NAME

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except Exception:
            return {}

    def api_mobile_download_enqueue(self, user):
        item = self.read_json()
        scope = str(item.get("scope") or "").strip().lower()
        item_id = urllib.parse.unquote(str(item.get("item_id") or "").strip())
        target_ratio = item.get("target_ratio")
        if scope not in {"movie", "episode", "season"} or not item_id:
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return self.wfile.write(json.dumps({"ok": False, "error": "scope and item_id are required"}).encode("utf-8"))
        try:
            status = mobile_enqueue(scope, item_id, target_ratio)
            status["ok"] = True
            return self.json_response(status)
        except Exception as exc:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode("utf-8"))

    def api_mobile_download_status(self, user):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        scope = (params.get("scope", [""])[0] or "").strip().lower()
        item_id = urllib.parse.unquote((params.get("item_id", [""])[0] or "").strip())
        if scope not in {"movie", "episode", "season"} or not item_id:
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return self.wfile.write(json.dumps({"ok": False, "error": "scope and item_id are required"}).encode("utf-8"))
        target_ratio = params.get("target_ratio", [None])[0]
        job_id, _, _ = mobile_cache_paths(scope, item_id, target_ratio)
        with MOBILE_DOWNLOAD_LOCK:
            existing = MOBILE_DOWNLOAD_JOBS.get(job_id)
            if existing:
                payload = mobile_status_payload(existing)
                payload["ok"] = True
                return self.json_response(payload)
        ready = mobile_load_ready_status(scope, item_id, target_ratio)
        if ready:
            ready["ok"] = True
            return self.json_response(ready)
        cached = mobile_load_cached_status(scope, item_id, target_ratio)
        if cached:
            cached["ok"] = True
            return self.json_response(cached)
        return self.json_response({"ok": True, "job_id": job_id, "scope": scope, "item_id": item_id, "status": "missing", "ready": False})

    def mobile_download_file(self, user, job_id: str):
        if not re.fullmatch(r"[a-f0-9]{20}", job_id or ""):
            return self.send_error(404)
        job_dir = MOBILE_DOWNLOAD_CACHE_DIR / job_id
        status_file = job_dir / "status.json"
        if not status_file.is_file():
            return self.send_error(404)
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            return self.send_error(404)
        if status.get("status") != "ready":
            return self.send_error(409, "Mobile download is not ready yet")
        package = (job_dir / status.get("filename", "")).resolve()
        if not str(package).startswith(str(job_dir.resolve()) + os.sep) or not package.is_file():
            return self.send_error(404)
        return self.serve_download_package(package)

    def cookie_value(self, name: str) -> str:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if "=" not in part:
                continue
            key, value = part.strip().split("=", 1)
            if key == name:
                return urllib.parse.unquote(value)
        return ""

    def current_user(self):
        token = self.cookie_value(CINEVAULT_SESSION_COOKIE)
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        conn = db_connect()
        try:
            row = conn.execute(
                """
                SELECT users.id, users.username, users.full_name, users.email, users.is_admin, users.is_super_admin, users.active
                FROM user_sessions
                JOIN users ON users.id = user_sessions.user_id
                WHERE user_sessions.token_hash=? AND user_sessions.expires_at>? AND users.active=1
                """,
                (token_hash, time.time()),
            ).fetchone()
            if row:
                conn.execute("UPDATE user_sessions SET last_seen_at=? WHERE token_hash=?", (auth_now(), token_hash))
                conn.commit()
            return row
        finally:
            conn.close()

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = auth_now()
        expires = time.time() + (CINEVAULT_SESSION_DAYS * 86400)
        conn = db_connect()
        try:
            conn.execute("DELETE FROM user_sessions WHERE expires_at<=?", (time.time(),))
            conn.execute(
                """
                INSERT INTO user_sessions(user_id, token_hash, created_at, expires_at, last_seen_at, user_agent, remote_addr)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, token_hash, now, expires, now, self.headers.get("User-Agent", ""), self.client_address[0]),
            )
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now, user_id))
            conn.commit()
        finally:
            conn.close()
        return token

    def set_session_cookie(self, token: str) -> None:
        max_age = CINEVAULT_SESSION_DAYS * 86400
        self.send_header(
            "Set-Cookie",
            f"{CINEVAULT_SESSION_COOKIE}={urllib.parse.quote(token)}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax",
        )

    def clear_session_cookie(self) -> None:
        self.send_header("Set-Cookie", f"{CINEVAULT_SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")

    def redirect(self, target: str, cookie: str | None = None, clear_cookie: bool = False):
        self.send_response(303)
        self.send_header("Location", target)
        if cookie is not None:
            self.set_session_cookie(cookie)
        if clear_cookie:
            self.clear_session_cookie()
        self.end_headers()

    def require_auth(self, path: str) -> bool:
        if self.current_user():
            return True
        if path.startswith("/api/"):
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":false,"error":"login required"}')
            return False
        return self.redirect(f"/login?next={urllib.parse.quote(self.path)}") is None

    def render_html(self, body: str, status: int = 200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def login_page(self, error: str = "", message: str = ""):
        parsed = urllib.parse.urlparse(self.path)
        next_url = urllib.parse.parse_qs(parsed.query).get("next", ["/"])[0] or "/"
        error_html = f"<div class='error'>{html.escape(error)}</div>" if error else ""
        message_html = f"<div class='message'>{html.escape(message)}</div>" if message else ""
        backdrop = random_login_backdrop_html()
        return self.render_html(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Login</title>
<style>
:root {{ color-scheme:dark; --lime:#c6ff00; --gold:#f5b73f; --line:rgba(255,255,255,.20); }}
* {{ box-sizing:border-box; }} body {{ margin:0; min-height:100vh; background:#050506; color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; overflow-x:hidden; }}
.backdrop {{ position:fixed; inset:-14vh -22vw; z-index:0; display:grid; grid-template-columns:repeat(14,minmax(58px,1fr)); gap:10px; transform:rotate(9deg) scale(1.08); opacity:.68; filter:saturate(1.08) contrast(1.02); }}
.backdrop img {{ width:100%; aspect-ratio:2/3; object-fit:cover; border-radius:12px; box-shadow:0 14px 34px rgba(0,0,0,.40); }}
.veil {{ position:fixed; inset:0; z-index:1; background:linear-gradient(115deg,rgba(38,12,58,.82),rgba(5,9,20,.78) 42%,rgba(0,38,82,.88)), linear-gradient(180deg,rgba(0,0,0,.30),rgba(0,0,0,.78)); }}
.page {{ position:relative; z-index:2; min-height:100vh; display:flex; flex-direction:column; padding:30px clamp(24px,5vw,70px); }}
.topbar {{ display:flex; justify-content:space-between; align-items:flex-start; gap:18px; }}
.brand h1 {{ margin:0; }}
.brand .cmv-logo {{ font-size:clamp(38px,7vw,68px); }}
.cmv-logo {{ display:inline-flex; align-items:center; gap:.32em; color:#fff; text-decoration:none; text-transform:uppercase; line-height:.88; }}
.cmv-left {{ display:grid; gap:.08em; align-items:center; }}
.cmv-cine,.cmv-media {{ font-size:.54em; font-weight:950; letter-spacing:.03em; line-height:.86; }}
.cmv-divider {{ width:.055em; height:1.14em; background:rgba(255,255,255,.72); }}
.cmv-vault {{ color:var(--lime); font-size:.96em; font-weight:950; letter-spacing:.02em; }}
.cmv-mark {{ width:1.1em; height:1.1em; color:var(--lime); flex:0 0 auto; }}
.signin-open {{ min-height:43px; padding:0 22px; border:1px solid rgba(255,255,255,.24); border-radius:999px; background:rgba(255,255,255,.13); color:#fff; font-size:17px; font-weight:950; cursor:pointer; backdrop-filter:blur(12px); }}
.content {{ width:min(720px,100%); margin-top:auto; padding:0 0 48px; }}
h2 {{ margin:0 0 18px; font-size:clamp(44px,8vw,74px); line-height:.98; letter-spacing:0; }} .sub {{ margin:0 0 26px; color:#f4f6fa; font-size:clamp(19px,3vw,29px); line-height:1.35; font-weight:850; }}
.login-modal {{ position:fixed; inset:0; z-index:5; display:none; align-items:center; justify-content:center; padding:22px; background:rgba(0,0,0,.62); }}
.login-modal.open {{ display:flex; }}
.login {{ width:min(430px,100%); padding:22px; border:1px solid rgba(255,255,255,.18); border-radius:22px; background:rgba(3,7,14,.90); backdrop-filter:blur(16px); box-shadow:0 24px 80px rgba(0,0,0,.55); }} .login-title {{ display:flex; justify-content:space-between; align-items:center; gap:12px; margin:0 0 14px; font-size:22px; font-weight:950; color:#e8edf6; }}
.close {{ width:38px; height:38px; display:grid; place-items:center; border:0; border-radius:999px; background:rgba(255,255,255,.10); color:#fff; font-size:24px; cursor:pointer; }}
.fields {{ display:grid; gap:13px; }} label {{ display:block; color:#cfd6e2; font-size:12px; font-weight:900; text-transform:uppercase; }} input {{ width:100%; min-height:46px; margin-top:6px; border-radius:13px; border:1px solid rgba(255,255,255,.20); background:rgba(9,14,22,.86); color:#fff; padding:0 12px; font-size:16px; }} .login button[type=submit] {{ width:100%; min-height:48px; margin-top:8px; border-radius:999px; border:0; background:var(--gold); color:#111; cursor:pointer; font-size:18px; font-weight:950; }}
.error {{ margin:0 0 12px; padding:10px 12px; border-radius:12px; background:#5c1821; color:#ffd8de; font-weight:800; }}
.message {{ margin:0 0 12px; padding:10px 12px; border-radius:12px; background:#15351f; color:#dfffe8; font-weight:800; }}
.signup {{ margin-top:14px; padding-top:14px; border-top:1px solid rgba(255,255,255,.14); }}
.signup summary {{ cursor:pointer; font-size:14px; color:#fff; font-weight:950; margin-bottom:10px; }}
.signup button {{ width:100%; min-height:42px; margin-top:8px; border-radius:999px; border:1px solid rgba(255,255,255,.22); background:rgba(255,255,255,.12); color:#fff; cursor:pointer; font-size:15px; font-weight:950; }}
@media (max-width:700px) {{ .page {{ padding:30px; }} .backdrop {{ grid-template-columns:repeat(8,minmax(58px,1fr)); gap:8px; inset:-8vh -78vw; }} .content {{ padding-bottom:42px; }} .brand .cmv-logo {{ font-size:clamp(30px,9vw,44px); }} .cmv-mark {{ width:.82em; height:.82em; }} .cmv-media {{ letter-spacing:.03em; }} }}
</style></head><body>
<div class="backdrop">{backdrop}</div><div class="veil"></div>
<main class="page">
  <section class="topbar"><div class="brand"><h1><span class="cmv-logo"><span class="cmv-left"><span class="cmv-cine">Cine</span><span class="cmv-media">Media</span></span><span class="cmv-divider"></span><span class="cmv-vault">Vault</span><svg class="cmv-mark" viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="26" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="32" cy="32" r="9" fill="none" stroke="currentColor" stroke-width="4"/><path d="M32 6v17M32 41v17M6 32h17M41 32h17M13.6 13.6l12 12M38.4 38.4l12 12M50.4 13.6l-12 12M25.6 38.4l-12 12" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="32" cy="32" r="3" fill="currentColor"/><path d="M32 32l5 4M32 32l-5 4M32 32v-6" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg></span></h1></div><button class="signin-open" type="button" id="openLogin">Sign In</button></section>
  <section class="content">
    <h2>Your private cinema library.</h2>
    <p class="sub">Browse your movie vault and TV archive from one local server with posters, playback, downloads, and summary details.</p>
  </section>
</main>
<div class="login-modal{' open' if error or message else ''}" id="loginModal">
  <form class="login" method="post" action="/login">
      <div class="login-title"><span>Sign in</span><button class="close" type="button" id="closeLogin" aria-label="Close">&times;</button></div>{error_html}{message_html}
      <input type="hidden" name="next" value="{html.escape(next_url)}">
      <div class="fields"><label>Username<input name="username" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button type="submit">Log In</button></div>
      <details class="signup">
        <summary>Need an account</summary>
        <div class="fields">
          <label>Username<input form="signupForm" name="username" required></label>
          <label>Password<input form="signupForm" name="password" type="password" required></label>
          <label>Full name<input form="signupForm" name="full_name"></label>
          <label>Email<input form="signupForm" name="email" type="email"></label>
        </div>
        <button form="signupForm" type="submit">Request Access</button>
      </details>
    </form>
  <form id="signupForm" method="post" action="/signup"></form>
  </div>
<script>
const modal=document.getElementById("loginModal");
document.getElementById("openLogin").addEventListener("click",()=>{{modal.classList.add("open"); const input=modal.querySelector("input[name=username]"); if(input) setTimeout(()=>input.focus(),50);}});
document.getElementById("closeLogin").addEventListener("click",()=>modal.classList.remove("open"));
modal.addEventListener("click",event=>{{if(event.target===modal) modal.classList.remove("open");}});
</script></body></html>""")

    def login_submit(self):
        form = self.read_form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        next_url = form.get("next") or "/"
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        conn = db_connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        finally:
            conn.close()
        if not row or not password_ok(password, row["password_hash"]):
            self.path = f"/login?next={urllib.parse.quote(next_url)}"
            return self.login_page("Invalid username or password.")
        return self.redirect(next_url, cookie=self.create_session(int(row["id"])))

    def signup_submit(self):
        form = self.read_form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        full_name = (form.get("full_name") or "").strip()
        email = (form.get("email") or "").strip()
        if not username or not password:
            return self.login_page("Username and password are required.")
        now = auth_now()
        conn = db_connect()
        try:
            existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if existing:
                return self.login_page("That username already exists.")
            conn.execute(
                """
                INSERT INTO pending_users(username, full_name, email, password_hash, requested_at, status)
                VALUES(?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(username) DO UPDATE SET
                  full_name=excluded.full_name,
                  email=excluded.email,
                  password_hash=excluded.password_hash,
                  requested_at=excluded.requested_at,
                  status='pending',
                  reviewed_at=NULL,
                  reviewed_by=NULL,
                  note=NULL
                """,
                (username, full_name, email, password_hash(password), now),
            )
            conn.commit()
        finally:
            conn.close()
        return self.login_page(message="Access request submitted. An admin must approve the account before you can log in.")

    def logout(self):
        token = self.cookie_value(CINEVAULT_SESSION_COOKIE)
        if token:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            conn = db_connect()
            try:
                conn.execute("DELETE FROM user_sessions WHERE token_hash=?", (token_hash,))
                conn.commit()
            finally:
                conn.close()
        return self.redirect("/login", clear_cookie=True)

    def admin_users_page(self, user, message: str = ""):
        conn = db_connect()
        try:
            rows = conn.execute("SELECT id, username, full_name, email, is_admin, is_super_admin, active, created_at, last_login_at FROM users ORDER BY username").fetchall()
            pending_rows = conn.execute("SELECT * FROM pending_users WHERE status='pending' ORDER BY requested_at").fetchall()
            history_rows = conn.execute(
                """
                SELECT h.*, u.username, u.full_name
                FROM user_play_history h
                JOIN users u ON u.id=h.user_id
                ORDER BY h.last_played_at DESC
                """
            ).fetchall()
        finally:
            conn.close()
        stats = library_stats()
        stats_html = f"""
          <div class="stats-grid">
            <div class="stat"><strong>Movies</strong><span>{stats['movies']['assets']} assets</span><span>{human_bytes(stats['movies']['size'])}</span><span>{format_runtime_minutes(stats['movies']['runtime_minutes'])}</span></div>
            <div class="stat"><strong>TV Shows</strong><span>{stats['tv']['shows']} shows</span><span>{stats['tv']['assets']} episodes</span><span>{human_bytes(stats['tv']['size'])}</span><span>{format_runtime_minutes(stats['tv']['runtime_minutes'])}</span></div>
          </div>
        """
        pending_html = ""
        for row in pending_rows:
            pending_html += f"""
              <tr><td>{html.escape(row['username'])}</td><td>{html.escape(row['full_name'] or '')}</td><td>{html.escape(row['email'] or '')}</td><td>{html.escape(row['requested_at'] or '')}</td>
              <td class="actions">
                <form method="post" action="/admin/users"><input type="hidden" name="action" value="approve_pending"><input type="hidden" name="pending_id" value="{row['id']}"><button type="submit">Approve</button></form>
                <form method="post" action="/admin/users"><input type="hidden" name="action" value="deny_pending"><input type="hidden" name="pending_id" value="{row['id']}"><button class="danger" type="submit">Deny</button></form>
              </td></tr>"""
        pending_html = pending_html or "<tr><td colspan='5'>No pending account requests.</td></tr>"
        history_html = ""
        for row in history_rows:
            user_label = row["full_name"] or row["username"]
            history_html += (
                f"<tr><td>{html.escape(user_label)}</td><td>{html.escape(row['media_type'])}</td><td>{html.escape(row['title'] or '')}</td>"
                f"<td>{html.escape(row['subtitle'] or '')}</td><td>{html.escape(format_seconds(row['max_position_seconds'] or 0))} / {html.escape(format_seconds(row['duration_seconds'] or 0))}</td>"
                f"<td>{'Yes' if row['completed'] else 'No'}</td><td>{row['play_count']}</td><td>{html.escape(row['last_played_at'] or '')}</td></tr>"
            )
        history_html = history_html or "<tr><td colspan='8'>No play history yet.</td></tr>"
        backup_rows = []
        for backup in list_cinevault_db_backups():
            backup_rows.append(
                f"<tr><td>{html.escape(backup['name'])}</td><td>{html.escape(human_bytes(backup['size']))}</td><td>{html.escape(backup['modified'])}</td><td>{html.escape(backup['path'])}</td></tr>"
            )
        backups_html = "".join(backup_rows) or "<tr><td colspan='4'>No backups yet.</td></tr>"
        user_rows = []
        for row in rows:
            protected = bool(row["is_super_admin"])
            delete = "" if protected else f"""
              <form method="post" action="/admin/users" onsubmit="return confirm('Delete user {html.escape(row['username'])}')">
                <input type="hidden" name="action" value="delete"><input type="hidden" name="user_id" value="{row['id']}">
                <button class="danger" type="submit">Delete</button>
              </form>"""
            actions = f"""
              <div class="actions">
                <form class="password-form" method="post" action="/admin/users">
                  <input type="hidden" name="action" value="change_password"><input type="hidden" name="user_id" value="{row['id']}">
                  <input name="password" type="password" placeholder="New password" autocomplete="new-password" required>
                  <button type="submit">Change Pass</button>
                </form>
                <form method="post" action="/admin/users" onsubmit="return confirm('Clear play history for {html.escape(row['username'])}')">
                  <input type="hidden" name="action" value="clear_history_user"><input type="hidden" name="user_id" value="{row['id']}">
                  <button type="submit">Clear History</button>
                </form>
                {delete}
              </div>"""
            role = "Super Admin" if protected else ("Admin" if row["is_admin"] else "User")
            user_rows.append(f"""<tr><td>{html.escape(row['username'])}</td><td>{html.escape(row['full_name'] or '')}</td><td>{html.escape(row['email'] or '')}</td><td>{role}</td><td>{'Active' if row['active'] else 'Disabled'}</td><td>{html.escape(row['last_login_at'] or '')}</td><td>{actions}</td></tr>""")
        note = f"<div class='note'>{html.escape(message)}</div>" if message else ""
        return self.render_html(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Users</title><style>
:root {{ color-scheme:dark; --gold:#f5b73f; --line:rgba(255,255,255,.14); }} body {{ margin:0; background:#08090c; color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }}
* {{ box-sizing:border-box; }} header {{ display:flex; justify-content:space-between; align-items:center; gap:14px; padding:18px 24px; border-bottom:1px solid var(--line); }} a {{ color:#fff; }} main {{ width:100%; max-width:none; margin:0; padding:24px; }}
.panel {{ width:100%; border:1px solid var(--line); border-radius:18px; padding:18px; background:#11151d; margin-bottom:22px; overflow:hidden; }} input {{ width:100%; min-height:40px; border-radius:10px; border:1px solid #2a3341; background:#0b1017; color:#fff; padding:0 10px; }}
.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }} label {{ font-weight:800; color:#cfd5df; }} button {{ min-height:38px; border:0; border-radius:999px; padding:0 14px; font-weight:900; cursor:pointer; background:var(--gold); color:#111; }}
.danger {{ background:#62212b; color:#ffdfe4; }} .muted {{ color:#aab4c3; overflow-wrap:anywhere; }} .table-wrap {{ width:100%; overflow-x:auto; }} table {{ width:100%; min-width:920px; border-collapse:collapse; }} th,td {{ padding:10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }} .actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }} .actions form {{ margin:0; }} .password-form {{ display:flex; gap:8px; align-items:center; }} .password-form input {{ width:170px; min-height:36px; }} .note {{ padding:10px 12px; border-radius:10px; background:#15351f; margin-bottom:14px; }}
.stats-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }} .stat {{ border:1px solid var(--line); border-radius:14px; padding:14px; background:#0b1017; }} .stat strong,.stat span {{ display:block; }} .stat strong {{ font-size:20px; margin-bottom:8px; }} .stat span {{ color:#cfd8e6; margin:4px 0; }}
@media (max-width:700px) {{ header {{ padding:14px 18px; }} main {{ padding:18px; }} .grid {{ grid-template-columns:1fr; }} .panel {{ padding:14px; border-radius:14px; }} table {{ min-width:820px; }} .password-form input {{ width:145px; }} }}
</style></head><body><header><strong>CineMediaVault User Management</strong><nav><a href="/admin/activity">Activity</a> | <a href="/admin/modules">Modules</a> &middot; <a href="/">Home</a> &middot; <a href="/admin/hls">Live Streams</a> &middot; <a href="/logout">Logout</a></nav></header><main>{note}
<section class="panel"><h2>Library Totals</h2>{stats_html}</section>
<section class="panel"><h2>Pending Account Requests</h2><div class="table-wrap"><table><thead><tr><th>Username</th><th>Name</th><th>Email</th><th>Requested</th><th>Actions</th></tr></thead><tbody>{pending_html}</tbody></table></div></section>
<section class="panel"><h2>Add User</h2><form method="post" action="/admin/users"><input type="hidden" name="action" value="create"><div class="grid">
<label>Username<input name="username" required></label><label>Password<input name="password" type="password" required></label>
<label>Full name<input name="full_name"></label><label>Email<input name="email" type="email"></label>
<label><input name="is_admin" type="checkbox" value="1" style="width:auto;min-height:auto"> Admin</label></div><button type="submit">Create User</button></form></section>
<section class="panel users-panel"><h2>Users</h2><div class="table-wrap"><table><thead><tr><th>Username</th><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last login</th><th>Actions</th></tr></thead><tbody>{''.join(user_rows)}</tbody></table></div></section>
<section class="panel"><h2>Play History</h2><p class="muted">Stored indefinitely until an admin clears it.</p>
<form method="post" action="/admin/users" onsubmit="return confirm('Clear play history for all users')"><input type="hidden" name="action" value="clear_history_all"><button class="danger" type="submit">Clear All Play History</button></form>
<div class="table-wrap"><table><thead><tr><th>User</th><th>Type</th><th>Title</th><th>Episode / Year</th><th>Max Progress</th><th>Completed</th><th>Plays</th><th>Last Played</th></tr></thead><tbody>{history_html}</tbody></table></div></section>
<section class="panel"><h2>Database Backups</h2><p class="muted">Backups are saved to {html.escape(str(CINEVAULT_BACKUP_DIR))}. Daily backups run at 1:00 AM and keep the latest {CINEVAULT_BACKUP_KEEP} files.</p>
<form method="post" action="/admin/users"><input type="hidden" name="action" value="backup_database"><button type="submit">Backup Database Now</button></form>
<div class="table-wrap"><table><thead><tr><th>Backup</th><th>Size</th><th>Created</th><th>Path</th></tr></thead><tbody>{backups_html}</tbody></table></div></section>
</main></body></html>""")

    def admin_users_submit(self, user):
        form = self.read_form()
        action = form.get("action")
        conn = db_connect()
        try:
            if action == "create":
                username = (form.get("username") or "").strip()
                password = form.get("password") or ""
                if not username or not password:
                    return self.admin_users_page(user, "Username and password are required.")
                now = auth_now()
                conn.execute(
                    "INSERT INTO users(username, full_name, email, password_hash, is_admin, active, created_at, updated_at) VALUES(?, ?, ?, ?, ?, 1, ?, ?)",
                    (username, form.get("full_name") or "", form.get("email") or "", password_hash(password), 1 if form.get("is_admin") == "1" else 0, now, now),
                )
                conn.commit()
                return self.admin_users_page(user, f"Created user {username}.")
            if action == "delete":
                user_id = int(form.get("user_id") or 0)
                row = conn.execute("SELECT username, is_super_admin FROM users WHERE id=?", (user_id,)).fetchone()
                if not row:
                    return self.admin_users_page(user, "User not found.")
                if row["is_super_admin"]:
                    return self.admin_users_page(user, "The master admin account cannot be deleted.")
                conn.execute("DELETE FROM users WHERE id=?", (user_id,))
                conn.commit()
                return self.admin_users_page(user, f"User {row['username']} and all associated user data were deleted.")
            if action == "change_password":
                user_id = int(form.get("user_id") or 0)
                new_password = form.get("password") or ""
                if not user_id or not new_password:
                    return self.admin_users_page(user, "User and new password are required.")
                conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (password_hash(new_password), auth_now(), user_id))
                conn.commit()
                return self.admin_users_page(user, "Password updated.")
            if action == "approve_pending":
                pending_id = int(form.get("pending_id") or 0)
                pending = conn.execute("SELECT * FROM pending_users WHERE id=? AND status='pending'", (pending_id,)).fetchone()
                if not pending:
                    return self.admin_users_page(user, "Pending request not found.")
                now = auth_now()
                conn.execute(
                    """
                    INSERT INTO users(username, full_name, email, password_hash, is_admin, is_super_admin, active, created_at, updated_at)
                    VALUES(?, ?, ?, ?, 0, 0, 1, ?, ?)
                    """,
                    (pending["username"], pending["full_name"] or "", pending["email"] or "", pending["password_hash"], now, now),
                )
                conn.execute("UPDATE pending_users SET status='approved', reviewed_at=?, reviewed_by=? WHERE id=?", (now, int(user["id"]), pending_id))
                conn.commit()
                return self.admin_users_page(user, f"Approved account request for {pending['username']}.")
            if action == "deny_pending":
                pending_id = int(form.get("pending_id") or 0)
                now = auth_now()
                conn.execute("UPDATE pending_users SET status='denied', reviewed_at=?, reviewed_by=? WHERE id=? AND status='pending'", (now, int(user["id"]), pending_id))
                conn.commit()
                return self.admin_users_page(user, "Pending request denied.")
            if action == "clear_history_all":
                conn.execute("DELETE FROM user_play_history")
                conn.commit()
                return self.admin_users_page(user, "All play history cleared.")
            if action == "clear_history_user":
                user_id = int(form.get("user_id") or 0)
                target = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
                if not target:
                    return self.admin_users_page(user, "User not found.")
                conn.execute("DELETE FROM user_play_history WHERE user_id=?", (user_id,))
                conn.commit()
                return self.admin_users_page(user, f"Cleared play history for {target['username']}.")
            if action == "backup_database":
                backup = backup_cinevault_database("manual")
                return self.admin_users_page(user, f"Database backup saved: {backup}")
        except sqlite3.IntegrityError:
            return self.admin_users_page(user, "That username already exists.")
        finally:
            conn.close()
        return self.admin_users_page(user, "No change made.")

    def admin_modules_page(self, user, message: str = ""):
        modules = load_modules()
        note = f"<div class='note'>{html.escape(message)}</div>" if message else ""
        rows = []
        for module in modules:
            module_id = str(module.get("id") or "")
            enabled = "checked" if module.get("enabled") else ""
            running = module_running(module)
            status = "Running" if running else "Stopped"
            status_class = "running" if running else "stopped"
            current_url = module_public_url(module, self.request_hostname())
            start_script = str(module.get("start_script") or "")
            rows.append(f"""
            <section class="module-card">
              <div class="module-head">
                <div class="module-icon">{module_logo_html(module)}</div>
                <div><h2>{html.escape(str(module.get('name') or module_id))}</h2><p class="muted">{html.escape(module_id)} &middot; <span class="{status_class}">{status}</span></p></div>
              </div>
              <form method="post" action="/admin/modules" class="module-form">
                <input type="hidden" name="module_id" value="{html.escape(module_id)}">
                <label class="check"><input type="checkbox" name="enabled" value="1" {enabled}> Show on CineMediaVault home</label>
                <label>Name<input name="name" value="{html.escape(str(module.get('name') or ''))}"></label>
                <div class="grid">
                  <label>Protocol<select name="protocol"><option value="http" {'selected' if str(module.get('protocol') or 'http') == 'http' else ''}>http</option><option value="https" {'selected' if str(module.get('protocol') or 'http') == 'https' else ''}>https</option></select></label>
                  <label>Port<input name="port" inputmode="numeric" value="{html.escape(str(module.get('port') or ''))}"></label>
                </div>
                <label>Override URL<input name="url" value="{html.escape(str(module.get('url') or ''))}" placeholder="Blank uses current server host plus protocol/port"></label>
                <p class="muted"><strong>Current URL:</strong> {html.escape(current_url)}</p>
                <p class="muted"><strong>Start script:</strong> {html.escape(start_script)}</p>
                <div class="actions">
                  <button type="submit" name="action" value="save">Save</button>
                  <button type="submit" name="action" value="start">Start</button>
                  <button class="danger" type="submit" name="action" value="stop">Stop</button>
                </div>
              </form>
              <form method="post" action="/admin/modules/logo" enctype="multipart/form-data" class="module-form logo-form">
                <input type="hidden" name="module_id" value="{html.escape(module_id)}">
                <label>Replace logo<input name="logo_file" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"></label>
                <div class="actions">
                  <button type="submit">Upload Logo</button>
                </div>
              </form>
            </section>""")
        return self.render_html(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Modules</title><style>
:root {{ color-scheme:dark; --gold:#f5b73f; --line:rgba(255,255,255,.14); }} body {{ margin:0; background:#08090c; color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }}
* {{ box-sizing:border-box; }} header {{ display:flex; justify-content:space-between; align-items:center; gap:14px; padding:18px 24px; border-bottom:1px solid var(--line); }} a {{ color:#fff; }} main {{ width:100%; max-width:1180px; margin:0 auto; padding:24px; }}
.module-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:18px; }} .module-card {{ border:1px solid var(--line); border-radius:18px; padding:18px; background:#11151d; }}
.module-head {{ display:flex; gap:14px; align-items:center; margin-bottom:14px; }} h1,h2,p {{ margin-top:0; }} h2 {{ margin-bottom:2px; }} .muted {{ color:#aeb7c5; overflow-wrap:anywhere; }}
.module-icon {{ width:54px; height:54px; display:flex; align-items:center; justify-content:center; border-radius:14px; background:#1b2230; border:1px solid var(--line); font-weight:950; }} .module-logo-text {{ display:inline-block; font-size:14px; }}
.module-logo {{ display:inline-flex; align-items:center; justify-content:center; min-width:42px; min-height:26px; border-radius:8px; overflow:hidden; background:rgba(255,255,255,.10); font-weight:950; }} .module-logo-img {{ width:100%; height:100%; object-fit:contain; display:block; }} .game-logo {{ line-height:1; }} .logo-comics {{ color:#fff; background:linear-gradient(135deg,#e3342f,#f5b73f); font-family:Impact,Arial Black,Arial,sans-serif; font-size:12px; text-shadow:1px 1px 0 #111; }} .logo-nes {{ min-width:58px; min-height:22px; padding:0 7px; border:2px solid #e60012; border-radius:999px; color:#e60012; background:#fff; font-size:14px; font-weight:900; font-family:Arial Black,Arial,sans-serif; }} .logo-sega {{ color:#0877d8; font-size:18px; font-weight:900; font-family:Arial Black,Arial,sans-serif; text-shadow:1px 0 #fff,-1px 0 #fff,0 1px #fff,0 -1px #fff; }} .logo-dos {{ color:#79ff8c; font-size:14px; font-family:Consolas,monospace; }} .logo-mame {{ color:#ffcf2e; font-size:16px; font-family:Impact,Arial Black,Arial,sans-serif; text-shadow:1px 1px 0 #236bff; }}
label {{ display:block; font-weight:850; color:#cfd5df; margin:10px 0 6px; }} input,select {{ width:100%; min-height:40px; border-radius:10px; border:1px solid #2a3341; background:#0b1017; color:#fff; padding:0 10px; }}
.logo-form {{ border-top:1px solid var(--line); margin-top:14px; padding-top:12px; }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }} .check {{ display:flex; gap:8px; align-items:center; }} .check input {{ width:auto; min-height:auto; }} button {{ min-height:38px; border:0; border-radius:999px; padding:0 16px; font-weight:900; cursor:pointer; background:var(--gold); color:#111; }}
.danger {{ background:#62212b; color:#ffdfe4; }} .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }} .note {{ padding:10px 12px; border-radius:10px; background:#15351f; margin-bottom:14px; }} .running {{ color:#8dff9f; }} .stopped {{ color:#ffb0b9; }}
@media (max-width:700px) {{ header {{ align-items:flex-start; flex-direction:column; }} main {{ padding:18px; }} .module-grid {{ grid-template-columns:1fr; }} }}
</style></head><body><header><strong>CineMediaVault Modules</strong><nav><a href="/admin/users">Users</a> &middot; <a href="/admin/activity">Activity</a> &middot; <a href="/admin/hls">Live Streams</a> &middot; <a href="/">Home</a> &middot; <a href="/logout">Logout</a></nav></header>
<main>{note}<h1>Modules</h1><p class="muted">Show or hide external libraries on the CineMediaVault home page, change their destination URLs, and start or stop the backing services.</p><div class="module-grid">{''.join(rows)}</div></main></body></html>""")

    def admin_modules_submit(self, user):
        form = self.read_form()
        action = form.get("action") or ""
        module_id = form.get("module_id") or ""
        modules = load_modules()
        target = None
        for module in modules:
            if module.get("id") == module_id:
                target = module
                break
        if not target:
            return self.admin_modules_page(user, "Module not found.")
        if action in {"save", "start", "stop"}:
            try:
                target["enabled"] = form.get("enabled") == "1"
                target["name"] = (form.get("name") or target.get("name") or module_id).strip()
                target["protocol"] = (form.get("protocol") or "http").replace(":", "").lower()
                target["port"] = int(form.get("port") or target.get("port") or 80)
                target["url"] = (form.get("url") or "").strip()
            except ValueError:
                return self.admin_modules_page(user, "Port must be a number.")
            save_modules(modules)
        if action == "start":
            ok, msg = start_module(target)
            return self.admin_modules_page(user, msg if ok else f"Could not start module: {msg}")
        if action == "stop":
            _ok, msg = stop_module(target)
            return self.admin_modules_page(user, msg)
        if action == "save":
            return self.admin_modules_page(user, f"Saved {target.get('name')}.")
        return self.admin_modules_page(user, "No action selected.")

    def admin_module_logo_submit(self, user):
        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                },
            )
            module_id = str(form.getfirst("module_id") or "").strip()
            file_item = form["logo_file"] if "logo_file" in form else None
        except Exception as exc:
            return self.admin_modules_page(user, f"Could not read upload: {exc}")
        if not module_id or file_item is None or not getattr(file_item, "filename", ""):
            return self.admin_modules_page(user, "Choose a module and logo file first.")
        modules = load_modules()
        target = next((module for module in modules if module.get("id") == module_id), None)
        if not target:
            return self.admin_modules_page(user, "Module not found.")
        original_name = Path(str(file_item.filename)).name
        suffix = Path(original_name).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
            return self.admin_modules_page(user, "Logo must be PNG, JPG, WEBP, GIF, or SVG.")
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", f"{module_id}{suffix}").strip(".-")
        MODULE_LOGO_DIR.mkdir(parents=True, exist_ok=True)
        target_path = (MODULE_LOGO_DIR / safe_name).resolve()
        if not str(target_path).startswith(str(MODULE_LOGO_DIR) + os.sep):
            return self.admin_modules_page(user, "Invalid logo filename.")
        with target_path.open("wb") as handle:
            shutil.copyfileobj(file_item.file, handle)
        target["logo_url"] = f"/module-logos/{safe_name}"
        save_modules(modules)
        return self.admin_modules_page(user, f"Updated logo for {target.get('name') or module_id}.")

    def admin_activity_page(self, user):
        conn = db_connect()
        try:
            recent = conn.execute(
                """
                SELECT h.*, u.username, u.full_name
                FROM user_play_history h
                JOIN users u ON u.id=h.user_id
                ORDER BY h.last_played_at DESC
                LIMIT 500
                """
            ).fetchall()
            top_movies = conn.execute(
                """
                SELECT media_type, media_id, title, poster, href, SUM(play_count) AS plays,
                       GROUP_CONCAT(DISTINCT COALESCE(NULLIF(u.username,''), 'user')) AS users,
                       MAX(last_played_at) AS last_played
                FROM user_play_history h
                JOIN users u ON u.id=h.user_id
                WHERE media_type='movie'
                GROUP BY media_type, media_id, title, poster, href
                ORDER BY plays DESC, last_played DESC
                LIMIT 50
                """
            ).fetchall()
            top_tv = conn.execute(
                """
                SELECT media_type, title, poster, detail_href AS href, SUM(play_count) AS plays,
                       GROUP_CONCAT(DISTINCT COALESCE(NULLIF(u.username,''), 'user')) AS users,
                       MAX(last_played_at) AS last_played
                FROM user_play_history h
                JOIN users u ON u.id=h.user_id
                WHERE media_type='tv'
                GROUP BY media_type, title, poster, detail_href
                ORDER BY plays DESC, last_played DESC
                LIMIT 50
                """
            ).fetchall()
        finally:
            conn.close()

        def poster_html(src: str, title: str, large: bool = False) -> str:
            cls = "poster large" if large else "poster"
            if src:
                return f"<img class='{cls}' src='{html.escape(src)}' alt=''>"
            return f"<div class='{cls} missing'>{html.escape((title or '')[:1].upper())}</div>"

        def user_label(row) -> str:
            return row["full_name"] or row["username"] or "user"

        def time_ago(value: str) -> str:
            try:
                ts = time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
                delta = max(0, int(time.time() - ts))
                if delta < 3600:
                    return f"{max(1, delta // 60)} minutes ago"
                if delta < 86400:
                    return f"{delta // 3600} hours ago"
                return f"{delta // 86400} days ago"
            except Exception:
                return value or ""

        recent_rows = []
        for row in recent:
            title = row["title"] or ""
            subtitle = row["subtitle"] or ""
            recent_rows.append(f"""
              <a class="activity-row" href="{html.escape(row['href'] or row['detail_href'] or '/')}">
                {poster_html(row['poster'] or '', title)}
                <div><strong>{html.escape(title)}</strong><span>{html.escape(subtitle)}</span><em>{html.escape(user_label(row))} - {html.escape(time_ago(row['last_played_at'] or ''))}</em></div>
              </a>""")
        recent_html = "".join(recent_rows) or "<div class='empty'>No play activity yet.</div>"

        def chart_rows(rows, label: str) -> str:
            items = []
            for row in rows:
                title = row["title"] or ""
                users = ", ".join([part.strip() for part in str(row["users"] or "").split(",") if part.strip()])
                href = row["href"] or "/"
                items.append(f"""
                  <a class="chart-row" href="{html.escape(href)}">
                    {poster_html(row['poster'] or '', title)}
                    <div><strong>{html.escape(title)}</strong><span>{int(row['plays'] or 0)} plays</span><em>{html.escape(users)}</em></div>
                  </a>""")
            body = "".join(items) or "<div class='empty'>No chart data yet.</div>"
            hero = rows[0]["poster"] if rows and rows[0]["poster"] else ""
            return f"""
              <section class="chart-card">
                <div class="chart-hero" style="background-image:linear-gradient(rgba(0,0,0,.28),rgba(0,0,0,.55)),url('{html.escape(hero)}')"><h2>{html.escape(label)}</h2></div>
                <div class="chart-list">{body}</div>
              </section>"""

        top_movies_html = chart_rows(top_movies, "Movies")
        top_tv_html = chart_rows(top_tv, "Television")
        return self.render_html(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Activity</title><style>
:root {{ color-scheme:dark; --bg:#090a0d; --panel:#11151d; --line:rgba(255,255,255,.14); --muted:#aab4c3; --gold:#f5b73f; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }}
header {{ position:sticky; top:0; z-index:5; display:flex; justify-content:space-between; align-items:center; gap:14px; padding:18px 22px; background:rgba(9,10,13,.94); border-bottom:1px solid var(--line); }}
a {{ color:#fff; }} main {{ padding:22px; max-width:1280px; margin:0 auto 40px; }}
.top {{ display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:18px; }} h1 {{ margin:0; font-size:34px; }} .controls {{ display:flex; gap:10px; align-items:center; }}
button {{ min-height:38px; border:0; border-radius:999px; padding:0 14px; font-weight:900; cursor:pointer; background:var(--gold); color:#111; }} .danger {{ background:#62212b; color:#ffdfe4; }}
.layout {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(320px,.9fr); gap:18px; align-items:start; }}
.panel,.chart-card {{ border:1px solid var(--line); border-radius:22px; background:var(--panel); overflow:hidden; box-shadow:0 18px 50px rgba(0,0,0,.28); }}
.panel h2 {{ margin:0; padding:18px 18px 8px; font-size:24px; }}
.activity-list,.chart-list {{ display:grid; gap:0; padding:10px 18px 18px; max-height:calc(100vh - 150px); overflow:auto; }}
.activity-row,.chart-row {{ display:grid; grid-template-columns:54px minmax(0,1fr); gap:14px; padding:9px 0; text-decoration:none; border-bottom:1px solid rgba(255,255,255,.08); }}
.activity-row:last-child,.chart-row:last-child {{ border-bottom:0; }}
.poster {{ width:54px; aspect-ratio:2/3; object-fit:cover; border-radius:6px; background:#06080d; }}
.poster.missing {{ display:grid; place-items:center; color:#8995a8; font-weight:950; border:1px solid var(--line); }}
strong {{ display:block; font-size:18px; line-height:1.15; }} span {{ display:block; color:#c8d0dc; margin-top:3px; }} em {{ display:block; color:#8f9aaa; font-style:normal; margin-top:5px; }}
.charts {{ display:grid; grid-template-columns:1fr; gap:18px; }} .chart-hero {{ height:150px; display:grid; place-items:center; background-size:cover; background-position:center; }} .chart-hero h2 {{ margin:0; text-transform:uppercase; letter-spacing:.02em; text-shadow:0 4px 18px rgba(0,0,0,.72); }}
.empty {{ color:var(--muted); padding:14px; border:1px dashed var(--line); border-radius:14px; }}
@media (max-width:900px) {{ main {{ padding:18px; }} .layout {{ grid-template-columns:1fr; }} .activity-list,.chart-list {{ max-height:none; }} h1 {{ font-size:30px; }} }}
</style></head><body>
<header><strong>CineMediaVault Activity</strong><nav><a href="/admin/users">Users</a> &middot; <a href="/admin/hls">Live Streams</a> &middot; <a href="/">Home</a></nav></header>
<main>
  <div class="top"><h1>Top Charts</h1><div class="controls"><form method="post" action="/admin/users" onsubmit="return confirm('Clear play history for all users')"><input type="hidden" name="action" value="clear_history_all"><button class="danger" type="submit">Clear History</button></form></div></div>
  <div class="layout">
    <section class="panel"><h2>Recent Activity</h2><div class="activity-list">{recent_html}</div></section>
    <div class="charts">{top_tv_html}{top_movies_html}</div>
  </div>
</main></body></html>""")

    def media_state_payload(self, payload: dict, watched: int | None = None) -> dict:
        item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        key = str(item.get("key") or payload.get("key") or "")
        kind = str(item.get("kind") or payload.get("kind") or "").replace("tv", "tv")
        media_id = 0
        try:
            media_id = int(item.get("mediaId") or key.rsplit(":", 1)[-1])
        except Exception:
            pass
        position = float(payload.get("position", item.get("position", 0)) or 0)
        duration = float(payload.get("duration", item.get("duration", 0)) or 0)
        progress = float(payload.get("progress", item.get("progress", 0)) or 0)
        if duration > 0 and position > 0:
            progress = max(progress, min(1.0, position / duration))
        if watched is None:
            watched = 1 if bool(payload.get("watched", item.get("watched", False))) else 0
        return {
            "key": key,
            "kind": "tv" if kind == "tv" else "movie",
            "media_id": media_id,
            "title": str(item.get("title") or ""),
            "subtitle": str(item.get("subtitle") or ""),
            "poster": str(item.get("poster") or ""),
            "href": str(item.get("href") or ""),
            "detail_href": str(item.get("detailHref") or item.get("detail_href") or ""),
            "position": position,
            "duration": duration,
            "progress": progress,
            "watched": int(watched),
        }

    def api_watch_progress(self, user):
        payload = self.read_json()
        state = self.media_state_payload(payload)
        if not state["key"]:
            return self.json_response({"ok": False, "error": "missing media key"})
        queued = bool(payload.get("queued"))
        if state["duration"] and state["position"] >= state["duration"] * 0.92:
            state["watched"] = 1
            state["position"] = 0
            state["progress"] = 0
        elif state["position"] < 10 and not (queued and state["kind"] == "tv"):
            return self.json_response({"ok": True, "ignored": True})
        conn = db_connect()
        try:
            conn.execute(
                """
                INSERT INTO user_media_state(user_id, media_key, media_type, media_id, title, subtitle, poster, href, detail_href, position_seconds, duration_seconds, progress, watched, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, media_key) DO UPDATE SET
                  media_type=excluded.media_type, media_id=excluded.media_id, title=excluded.title,
                  subtitle=excluded.subtitle, poster=excluded.poster, href=excluded.href, detail_href=excluded.detail_href,
                  position_seconds=excluded.position_seconds, duration_seconds=excluded.duration_seconds,
                  progress=excluded.progress, watched=excluded.watched, updated_at=excluded.updated_at
                """,
                (
                    int(user["id"]), state["key"], state["kind"], state["media_id"], state["title"], state["subtitle"],
                    state["poster"], state["href"], state["detail_href"], state["position"], state["duration"],
                    state["progress"], state["watched"], auth_now(),
                ),
            )
            self.write_play_history_row(conn, int(user["id"]), state)
            conn.commit()
        finally:
            conn.close()
        return self.json_response({"ok": True, "state": state})

    def api_watch_watched(self, user):
        payload = self.read_json()
        watched = 1 if payload.get("watched", True) else 0
        state = self.media_state_payload(payload, watched=watched)
        if not state["key"]:
            return self.json_response({"ok": False, "error": "missing media key"})
        if watched:
            state["position"] = 0
            state["progress"] = 0
        conn = db_connect()
        try:
            conn.execute(
                """
                INSERT INTO user_media_state(user_id, media_key, media_type, media_id, title, subtitle, poster, href, detail_href, position_seconds, duration_seconds, progress, watched, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, media_key) DO UPDATE SET
                  media_type=excluded.media_type, media_id=excluded.media_id, title=excluded.title,
                  subtitle=excluded.subtitle, poster=excluded.poster, href=excluded.href, detail_href=excluded.detail_href,
                  position_seconds=excluded.position_seconds, duration_seconds=excluded.duration_seconds,
                  progress=excluded.progress, watched=excluded.watched, updated_at=excluded.updated_at
                """,
                (
                    int(user["id"]), state["key"], state["kind"], state["media_id"], state["title"], state["subtitle"],
                    state["poster"], state["href"], state["detail_href"], state["position"], state["duration"],
                    state["progress"], state["watched"], auth_now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.json_response({"ok": True, "watched": bool(watched)})

    def write_state_row(self, conn, user_id: int, state: dict) -> None:
        conn.execute(
            """
            INSERT INTO user_media_state(user_id, media_key, media_type, media_id, title, subtitle, poster, href, detail_href, position_seconds, duration_seconds, progress, watched, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, media_key) DO UPDATE SET
              media_type=excluded.media_type, media_id=excluded.media_id, title=excluded.title,
              subtitle=excluded.subtitle, poster=excluded.poster, href=excluded.href, detail_href=excluded.detail_href,
              position_seconds=excluded.position_seconds, duration_seconds=excluded.duration_seconds,
              progress=excluded.progress, watched=excluded.watched, updated_at=excluded.updated_at
            """,
            (
                user_id, state["key"], state["kind"], state["media_id"], state["title"], state["subtitle"],
                state["poster"], state["href"], state["detail_href"], state["position"], state["duration"],
                state["progress"], state["watched"], auth_now(),
            ),
        )

    def write_play_history_row(self, conn, user_id: int, state: dict) -> None:
        now = auth_now()
        completed = 1 if state.get("watched") else 0
        history_position = max(float(state.get("position") or 0), float(state.get("duration") or 0) if completed else 0.0)
        conn.execute(
            """
            INSERT INTO user_play_history(user_id, media_key, media_type, media_id, title, subtitle, poster, href, detail_href, first_played_at, last_played_at, play_count, max_position_seconds, duration_seconds, completed)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_id, media_key) DO UPDATE SET
              media_type=excluded.media_type,
              media_id=excluded.media_id,
              title=excluded.title,
              subtitle=excluded.subtitle,
              poster=excluded.poster,
              href=excluded.href,
              detail_href=excluded.detail_href,
              last_played_at=excluded.last_played_at,
              play_count=user_play_history.play_count + CASE WHEN (julianday(excluded.last_played_at)-julianday(user_play_history.last_played_at))*86400.0 > 300 THEN 1 ELSE 0 END,
              max_position_seconds=MAX(user_play_history.max_position_seconds, excluded.max_position_seconds),
              duration_seconds=MAX(user_play_history.duration_seconds, excluded.duration_seconds),
              completed=MAX(user_play_history.completed, excluded.completed)
            """,
            (
                user_id, state["key"], state["kind"], state["media_id"], state["title"], state["subtitle"],
                state["poster"], state["href"], state["detail_href"], now, now,
                history_position, max(float(state.get("duration") or 0), 0.0), completed,
            ),
        )

    def api_watch_bulk_watched(self, user):
        payload = self.read_json()
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        scope = str(payload.get("scope") or "").lower()
        watched = 1 if payload.get("watched", True) else 0
        if item.get("kind") != "tv":
            return self.json_response({"ok": False, "error": "bulk watched currently applies to TV only"})
        try:
            show_id = int(item.get("showId") or 0)
        except Exception:
            show_id = 0
        show = tv_app.tv_index.show_by_id.get(show_id)
        if not show:
            return self.json_response({"ok": False, "error": "show not found"})
        target_season = str(item.get("season") or "")
        episodes = []
        for season in show.seasons.values():
            if scope == "season" and season.label != target_season:
                continue
            episodes.extend(season.episodes)
        if scope not in {"season", "show"}:
            return self.json_response({"ok": False, "error": "scope must be season or show"})
        conn = db_connect()
        try:
            for episode in episodes:
                media_item = continue_item_for_episode(episode)
                state = self.media_state_payload({"item": media_item, "watched": bool(watched)}, watched=watched)
                state["position"] = 0
                state["duration"] = 0
                state["progress"] = 0
                self.write_state_row(conn, int(user["id"]), state)
            conn.commit()
        finally:
            conn.close()
        return self.json_response({"ok": True, "scope": scope, "watched": bool(watched), "episodes": len(episodes)})

    def api_watch_state(self, user):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        key = params.get("key", [""])[0]
        title = params.get("title", [""])[0]
        subtitle = params.get("subtitle", [""])[0]
        conn = db_connect()
        try:
            row = conn.execute("SELECT * FROM user_media_state WHERE user_id=? AND media_key=?", (int(user["id"]), key)).fetchone()
            if not row and key.startswith("movie-path:") and title:
                movie = movie_for_saved_title(title, subtitle)
                if movie and movie_media_key(movie) == key:
                    legacy = conn.execute(
                        """SELECT * FROM user_media_state WHERE user_id=? AND media_type='movie'
                           AND lower(title)=lower(?) ORDER BY updated_at DESC LIMIT 1""",
                        (int(user["id"]), title),
                    ).fetchone()
                    if legacy:
                        current = continue_item_for_movie(movie)
                        conn.execute(
                            """UPDATE user_media_state SET media_key=?,media_id=?,title=?,subtitle=?,poster=?,href=?,detail_href=?
                               WHERE user_id=? AND media_key=?""",
                            (current["key"], int(movie.id), current["title"], current["subtitle"], current["poster"],
                             current["href"], current["detailHref"], int(user["id"]), legacy["media_key"]),
                        )
                        conn.commit()
                        row = conn.execute("SELECT * FROM user_media_state WHERE user_id=? AND media_key=?", (int(user["id"]), key)).fetchone()
            return self.json_response({"ok": True, "item": user_state_item(row) if row else None})
        finally:
            conn.close()

    def api_watch_continue(self, user):
        conn = db_connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM user_media_state
                WHERE user_id=? AND watched=0 AND (
                  (duration_seconds>0 AND position_seconds>=10 AND position_seconds<duration_seconds*0.92)
                  OR (media_type='tv' AND duration_seconds=0 AND position_seconds=0)
                )
                ORDER BY updated_at DESC
                LIMIT 30
                """,
                (int(user["id"]),),
            ).fetchall()
            items = []
            for row in rows:
                card = continue_card_item(row)
                if row["media_type"] == "movie" and card.get("key", "").startswith("movie-path:") and row["media_key"] != card["key"]:
                    conn.execute(
                        """UPDATE user_media_state SET media_key=?,media_id=?,title=?,subtitle=?,poster=?,href=?,detail_href=?
                           WHERE user_id=? AND media_key=?""",
                        (card["key"], int(card.get("mediaId") or 0), card["title"], card["subtitle"], card["poster"],
                         card["href"], card["detailHref"], int(user["id"]), row["media_key"]),
                    )
                items.append(card)
            conn.commit()
            return self.json_response({"ok": True, "items": items})
        finally:
            conn.close()

    def api_playback_mode_get(self, user):
        return self.json_response({"ok": True, "mode": read_global_playback_mode()})

    def api_playback_mode_set(self, user):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""
        data = {}
        if "application/json" in (self.headers.get("Content-Type") or "").lower():
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {}
        else:
            data = {key: values[-1] for key, values in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        try:
            mode = write_global_playback_mode(data.get("mode", ""))
        except ValueError as exc:
            return self.json_response({"ok": False, "error": str(exc)})
        return self.json_response({"ok": True, "mode": mode})

    def dispatch(self, head: bool = False):
        path = self.path.split("?", 1)[0]
        if path.startswith("/posters/"):
            try:
                return movie_app.Handler.serve_poster(self, path)
            except FileNotFoundError:
                return tv_app.Handler.serve_poster(self, path)
        if path.startswith("/episode-thumbnails/"):
            return tv_app.Handler.serve_episode_thumbnail(self, path)
        if path.startswith("/assets/"):
            return self.serve_asset(path)
        if path.startswith("/module-logos/"):
            return self.serve_module_logo(path)
        if path == "/login":
            if self.current_user():
                return self.redirect("/")
            return self.login_page()
        if path == "/logout":
            return self.logout()
        if path == "/api/homepage/status":
            return self.json_response(homepage_status_payload(self))
        user = self.current_user()
        if not user:
            return self.require_auth(path)
        if path == "/video-lists":
            return self.video_lists_page(user)
        if path == "/api/video/playlist-next":
            return self.api_video_playlist_next(user)
        if path == "/admin/users":
            if not user["is_admin"]:
                return self.send_error(403)
            return self.admin_users_page(user)
        if path == "/downloads":
            return self.downloads_page(user)
        if path == "/admin/activity":
            if not user["is_admin"]:
                return self.send_error(403)
            return self.admin_activity_page(user)
        if path == "/admin/hls":
            if not user["is_admin"]:
                return self.send_error(403)
            return self.admin_hls_page(user)
        if path == "/admin/modules":
            if not user["is_admin"]:
                return self.send_error(403)
            return self.admin_modules_page(user)
        if path.startswith("/admin/media/"):
            if not user["is_admin"]:
                return self.send_error(403)
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[0] == "admin" and parts[1] == "media":
                return self.admin_media_page(user, parts[2], parts[3])
            return self.send_error(404)
        if path == "/":
            return self.landing()
        if path == "/search":
            return self.search_page()
        if path == "/music" or path.startswith("/music/") or path.startswith("/api/music/"):
            handled = music_module.handle_get(self, user, path, head=head)
            if handled is not False:
                return handled
        if path == "/wall":
            return self.video_wall_page()
        if path == "/live-tv":
            return self.live_tv_page()
        if path == "/actor":
            return self.actor_page()
        if path == "/movies":
            return movie_app.Handler.page(self)
        if path == "/tv":
            return tv_app.Handler.page(self)
        if path.startswith("/watch/movie/"):
            return self.watch_media("movie", path.rsplit("/", 1)[-1])
        if path.startswith("/watch/tv/"):
            return self.watch_media("tv", path.rsplit("/", 1)[-1])
        if path.startswith("/player/movie/"):
            return self.direct_player("movie", path.rsplit("/", 1)[-1])
        if path.startswith("/player/tv/"):
            return self.direct_player("tv", path.rsplit("/", 1)[-1])
        if path.startswith("/subtitles/"):
            return self.serve_subtitle(path, head_only=head)
        if path.startswith("/hls/"):
            return self.serve_hls(path)
        if path == "/api/refresh":
            movie_app.movie_index.refresh_background()
            tv_app.tv_index.refresh_background()
            movie_app.load_poster_map()
            movie_app.load_metadata_map()
            tv_app.load_poster_map()
            tv_app.load_metadata_map()
            return self.json_response({"ok": True, "movies": len(movie_app.movie_index.items), "shows": len(tv_app.tv_index.shows), "episodes": len(tv_app.tv_index.episode_by_id), "movie_posters": len(movie_app.poster_map), "tv_posters": len(tv_app.poster_map)})
        if path == "/api/full-scan":
            return self.start_full_scan()
        if path == "/api/full-scan-status":
            return self.full_scan_status()
        if path == "/api/reload-posters":
            movie_app.load_poster_map()
            movie_app.load_metadata_map()
            tv_app.load_poster_map()
            tv_app.load_metadata_map()
            return self.json_response({"ok": True, "movie_posters": len(movie_app.poster_map), "tv_posters": len(tv_app.poster_map)})
        if path == "/api/db/status":
            return self.json_response(cinevault_db_status())
        if path == "/api/watch/state":
            return self.api_watch_state(user)
        if path == "/api/watch/continue":
            return self.api_watch_continue(user)
        if path == "/api/playback-mode":
            return self.api_playback_mode_get(user)
        if path == "/api/mobile-download/status":
            return self.api_mobile_download_status(user)
        if path == "/api/video-wall":
            return self.api_video_wall(user)
        if path == "/api/video-wall/search":
            return self.api_video_wall_search(user)
        if path == "/api/video-wall/bandwidth":
            return self.api_video_wall_bandwidth(user)
        if path == "/api/hdhr/devices":
            return self.api_hdhr_devices(user)
        if path.startswith("/mobile-download/file/"):
            return self.mobile_download_file(user, path.rsplit("/", 1)[-1])
        if path == "/api/continue-metadata":
            parsed = urllib.parse.urlparse(self.path)
            keys = urllib.parse.parse_qs(parsed.query).get("keys", [""])[0]
            items = {}
            for key in [value.strip() for value in keys.split(",") if value.strip()]:
                current = continue_metadata_for_key(key)
                if current:
                    items[key] = current
            return self.json_response({"ok": True, "items": items})
        if path == "/api/movies":
            return movie_app.Handler.api_movies(self)
        if path.startswith("/tv/show/"):
            return tv_app.Handler.show_detail(self, path.rsplit("/", 1)[-1])
        if path.startswith("/movie/fix-match/"):
            return movie_app.Handler.fix_match_page(self, path.rsplit("/", 1)[-1], urllib.parse.urlparse(self.path))
        if path.startswith("/movie/apply-match/"):
            return movie_app.Handler.apply_match(self, path.rsplit("/", 1)[-1], urllib.parse.urlparse(self.path))
        if path.startswith("/movie/"):
            return movie_app.Handler.movie_detail(self, path.rsplit("/", 1)[-1])
        if path.startswith("/tv/fix-match/"):
            return tv_app.Handler.fix_match_page(self, path.rsplit("/", 1)[-1], urllib.parse.urlparse(self.path))
        if path.startswith("/tv/apply-match/"):
            return tv_app.Handler.apply_match(self, path.rsplit("/", 1)[-1], urllib.parse.urlparse(self.path))
        if path.startswith("/play/episode/"):
            return self.direct_stream("tv", path.rsplit("/", 1)[-1], head_only=head)
        if path.startswith("/download/episode/"):
            return tv_app.Handler.download_episode(self, path.rsplit("/", 1)[-1])
        if path.startswith("/download/season/"):
            return tv_app.Handler.download_season(self, path.rsplit("/", 1)[-1])
        if path.startswith("/play/"):
            return self.direct_stream("movie", path.rsplit("/", 1)[-1], head_only=head)
        if path.startswith("/download/"):
            return movie_app.Handler.download(self, path.rsplit("/", 1)[-1], head_only=head)
        self.send_error(404)

    def landing(self):
        user = self.current_user()
        initial = (user["username"][:1].upper() if user and user["username"] else "U")
        account_links = ['<a href="/downloads">Downloads</a>', '<a href="/logout">Logout</a>']
        if user and user["is_admin"]:
            pending_count = pending_user_count()
            badge = f"<span class='badge'>{pending_count}</span>" if pending_count else ""
            account_links = [
                '<a href="/downloads">Downloads</a>',
                '<a href="/admin/users">Users</a>',
                '<a href="/admin/modules">Modules</a>',
                '<a href="/admin/hls">Live Streams</a>',
                '<a href="/logout">Logout</a>',
            ]
        else:
            badge = ""
        avatar = (
            f'<div class="account-wrap"><button class="avatar" id="accountButton" type="button" '
            f'aria-label="Account menu">{html.escape(initial)}{badge}</button>'
            f'<div class="account-menu" id="accountMenu">{"".join(account_links)}</div></div>'
        )
        recent_movies = sorted(
            [item for item in movie_app.movie_index.items if item.modified],
            key=lambda item: item.modified,
            reverse=True,
        )[:15]
        recent_shows = sorted(
            [show for show in tv_app.tv_index.shows if tv_app.show_modified(show)],
            key=tv_app.show_modified,
            reverse=True,
        )[:15]
        recent_released = recently_released_items(15)
        playback_mode = read_global_playback_mode()
        watched_keys = watched_keys_for_user(user)
        body = (
            HOME_PAGE
            .replace("{{MOVIE_COUNT}}", str(len(movie_app.movie_index.items)))
            .replace("{{TV_COUNT}}", str(len(tv_app.tv_index.shows)))
            .replace("{{EPISODE_COUNT}}", str(len(tv_app.tv_index.episode_by_id)))
            .replace("{{SERVER_NAME}}", html.escape(SERVER_DISPLAY_NAME))
            .replace("{{HOME_BACKDROP}}", home_backdrop_html())
            .replace("{{RECENT_MOVIES}}", "".join(home_movie_card(item, watched_keys) for item in recent_movies))
            .replace("{{RECENT_TV}}", "".join(home_show_card(show, watched_keys) for show in recent_shows))
            .replace("{{RECENT_RELEASED_SECTION}}", recently_released_section(recent_released, watched_keys))
            .replace("{{MODULE_TABS}}", home_module_tabs(self.request_hostname()))
            .replace("{{GLOBAL_PLAYBACK_MODE}}", playback_mode)
            .replace("{{GLOBAL_PLAYBACK_MODE_LABEL}}", "HLS" if playback_mode == "hls" else "Direct")
            .replace("{{GLOBAL_PLAYBACK_MODE_CLASS}}", "hls" if playback_mode == "hls" else "")
            .replace('<div class="avatar">J</div>', avatar)
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def search_page(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0].strip()
        watched_keys = watched_keys_for_user(self.current_user())
        results = unified_search_results(query, watched_keys=watched_keys)
        cards = "".join(search_result_card(item, watched_keys) for item in results)
        if not query:
            title = "Search CineMediaVault"
            subtitle = "Search across movies, TV shows, actors, genres, and episode titles."
        else:
            title = f"Search: {query}"
            subtitle = f"{len(results)} result(s) across Movies and TV Shows."
        if query and not cards:
            cards = "<p class='muted'>No matching movies or shows found.</p>"
        body = (
            SEARCH_PAGE
            .replace("{{QUERY}}", html.escape(query))
            .replace("{{TITLE}}", html.escape(title))
            .replace("{{SUBTITLE}}", html.escape(subtitle))
            .replace("{{RESULTS}}", cards)
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def actor_page(self):
        parsed = urllib.parse.urlparse(self.path)
        name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0].strip()
        results = actor_media_results(name)
        watched_keys = watched_keys_for_user(self.current_user())
        cards = "".join(search_result_card(item, watched_keys) for item in results) or "<p class='muted'>No matching library titles found.</p>"
        body = (SEARCH_PAGE.replace("{{QUERY}}", html.escape(name)).replace("{{TITLE}}", html.escape(name or "Actor"))
                .replace("{{SUBTITLE}}", html.escape(f"{len(results)} movie and TV title(s) in your library."))
                .replace("{{RESULTS}}", cards))
        data = body.encode("utf-8")
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def json_response(self, payload):
        return movie_app.Handler.json_response(self, payload)

    def full_scan_status(self):
        global MEDIA_SCAN_PROCESS
        with MEDIA_SCAN_LOCK:
            running = MEDIA_SCAN_PROCESS is not None and MEDIA_SCAN_PROCESS.poll() is None
            payload = dict(MEDIA_SCAN_LAST_RESULT)
            payload.update(load_scan_progress())
            payload.update({
                "ok": True,
                "running": running,
                "pid": MEDIA_SCAN_PROCESS.pid if running else payload.get("pid"),
            })
            return self.json_response(payload)

    def start_full_scan(self):
        global MEDIA_SCAN_PROCESS, MEDIA_SCAN_LAST_RESULT
        if not MEDIA_REFRESH_SCRIPT.is_file():
            return self.json_response({"ok": False, "running": False, "error": f"Missing scan script: {MEDIA_REFRESH_SCRIPT}"})
        with MEDIA_SCAN_LOCK:
            if MEDIA_SCAN_PROCESS is not None and MEDIA_SCAN_PROCESS.poll() is None:
                payload = dict(MEDIA_SCAN_LAST_RESULT)
                payload.update({"ok": True, "running": True, "pid": MEDIA_SCAN_PROCESS.pid, "message": "Scan already running"})
                return self.json_response(payload)
            log_dir = MEDIA_REFRESH_SCRIPT.parent / "media-library-refresh-logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            out_path = log_dir / f"web-scan-{timestamp}.out"
            err_path = log_dir / f"web-scan-{timestamp}.err"
            out_handle = out_path.open("ab")
            err_handle = err_path.open("ab")
            env = os.environ.copy()
            env["RESTART_SERVICES"] = "0"
            env["SCAN_PROGRESS_FILE"] = str(SCAN_PROGRESS_FILE)
            try:
                MEDIA_SCAN_PROCESS = subprocess.Popen(
                    ["bash", str(MEDIA_REFRESH_SCRIPT)],
                    cwd=str(MEDIA_REFRESH_SCRIPT.parent),
                    stdout=out_handle,
                    stderr=err_handle,
                    env=env,
                    close_fds=True,
                )
            finally:
                out_handle.close()
                err_handle.close()
            MEDIA_SCAN_LAST_RESULT = {
                "running": True,
                "pid": MEDIA_SCAN_PROCESS.pid,
                "stdout": str(out_path),
                "stderr": str(err_path),
                "started_at": time.time(),
            }
            save_scan_progress({**MEDIA_SCAN_LAST_RESULT, "percent": 1, "phase": "Starting", "message": "Starting full library scan"})
            threading.Thread(target=watch_full_scan, args=(MEDIA_SCAN_PROCESS, out_path, err_path), daemon=True).start()
            return self.json_response({
                "ok": True,
                "running": True,
                "pid": MEDIA_SCAN_PROCESS.pid,
                "message": "Started full media scan",
                "stdout": str(out_path),
                "stderr": str(err_path),
            })

    def parse_hls_key(self, key: str) -> tuple[str, str]:
        parts = key.split("-", 2)
        if len(parts) < 2 or parts[0] not in {"movie", "tv"}:
            return "", ""
        return parts[0], parts[1]

    def hls_media_summary(self, kind: str, item_id: str) -> dict:
        try:
            if kind == "movie":
                item = movie_app.safe_item(item_id)
                metadata = movie_app.metadata_for(item)
                title = metadata.get("title") or item.title
                poster = movie_app.poster_url_for(item) or ""
                duration = ffprobe_duration(item.path)
                return {"title": title, "subtitle": movie_app.release_label_for(metadata), "poster": poster, "duration": duration, "href": f"/player/movie/{item.id}"}
            if kind == "tv":
                episode = tv_app.safe_episode(item_id)
                show = show_for_episode(episode)
                metadata = tv_app.metadata_for(show) if show else {}
                episode_meta = tv_episode_display_metadata(metadata, episode)
                still = tv_app.episode_still_url(metadata, episode) if metadata else ""
                poster = still or (tv_app.poster_url_for(show) if show else "") or ""
                title = episode.show
                subtitle = f"{episode_label(episode)} - {episode_meta['title']}"
                duration = ffprobe_duration(episode.path)
                return {"title": title, "subtitle": subtitle, "poster": poster, "duration": duration, "href": f"/player/tv/{episode.id}"}
        except Exception:
            fallback = self.hls_state_fallback(kind, item_id)
            if fallback:
                return fallback
        return {"title": f"{kind} {item_id}", "subtitle": "", "poster": "", "duration": 0.0, "href": "/"}

    def hls_state_fallback(self, kind: str, item_id: str) -> dict | None:
        try:
            media_id = int(item_id)
        except (TypeError, ValueError):
            return None
        conn = db_connect()
        try:
            row = conn.execute(
                """
                SELECT title, subtitle, poster, href, detail_href, duration_seconds
                FROM user_media_state
                WHERE media_type=? AND media_id=?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (kind, media_id),
            ).fetchone()
            if not row:
                return None
            return {
                "title": row["title"] or f"{kind} {item_id}",
                "subtitle": row["subtitle"] or "",
                "poster": row["poster"] or "",
                "duration": float(row["duration_seconds"] or 0),
                "href": row["detail_href"] or row["href"] or "/",
            }
        finally:
            conn.close()

    def hls_user_progress(self, kind: str, item_id: str) -> list[dict]:
        now = time.time()
        active = []
        with HLS_VIEWER_LOCK:
            for key, viewer in list(HLS_VIEWERS.items()):
                if now - float(viewer.get("updated_at") or 0) > 45:
                    HLS_VIEWERS.pop(key, None)
                    continue
                if viewer.get("kind") == kind and str(viewer.get("item_id")) == str(item_id):
                    duration = float(viewer.get("duration") or 0)
                    position = float(viewer.get("position") or 0)
                    active.append({
                        "user": viewer.get("user") or "Signed-in user",
                        "position": position,
                        "duration": duration,
                        "progress": (position / duration * 100.0) if duration > 0 else 0.0,
                        "updated": viewer.get("updated_at") or 0,
                    })
        if active:
            return sorted(active, key=lambda row: float(row["updated"] or 0), reverse=True)[:6]
        conn = db_connect()
        try:
            rows = conn.execute(
                """
                SELECT users.username, users.full_name, state.position_seconds, state.duration_seconds, state.progress, state.updated_at
                FROM user_media_state state
                JOIN users ON users.id = state.user_id
                WHERE state.media_type=? AND state.media_id=? AND state.watched=0
                ORDER BY state.updated_at DESC
                LIMIT 6
                """,
                (kind, int(item_id)),
            ).fetchall()
            return [
                {
                    "user": row["full_name"] or row["username"],
                    "position": float(row["position_seconds"] or 0),
                    "duration": float(row["duration_seconds"] or 0),
                    "progress": float(row["progress"] or 0),
                    "updated": row["updated_at"] or "",
                }
                for row in rows
            ]
        except Exception:
            return []
        finally:
            conn.close()

    def hls_cache_items(self) -> list[dict]:
        HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        items = []
        with TRANSCODE_LOCK:
            known = dict(TRANSCODES)
        for directory in sorted([path for path in HLS_CACHE_DIR.iterdir() if path.is_dir()], key=latest_mtime, reverse=True):
            key = directory.name
            kind, item_id = self.parse_hls_key(key)
            info = self.hls_media_summary(kind, item_id) if kind else {"title": key, "subtitle": "", "poster": "", "duration": 0.0, "href": "/"}
            stream = known.get(key, {})
            process = stream.get("process")
            pid = None
            running = False
            if process:
                pid = process.pid
                running = process.poll() is None
            pid_file = directory / "ffmpeg.pid"
            if not running and pid_file.is_file():
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                    os.kill(pid, 0)
                    running = True
                except (OSError, ValueError):
                    pass
            segment_count = hls_segment_count(directory)
            cached_seconds = segment_count * max(1.0, float(HLS_SEGMENT_SECONDS))
            duration = info.get("duration") or 0.0
            progress = min(100.0, (cached_seconds / duration) * 100.0) if duration > 0 else 0.0
            size = 0
            for file in directory.glob("*"):
                try:
                    if file.is_file():
                        size += file.stat().st_size
                except OSError:
                    pass
            users = self.hls_user_progress(kind, item_id) if kind and item_id else []
            items.append({
                "key": key,
                "kind": kind,
                "item_id": item_id,
                "title": info["title"],
                "subtitle": info["subtitle"],
                "poster": info["poster"],
                "href": info["href"],
                "duration": duration,
                "cached_seconds": cached_seconds,
                "progress": progress,
                "size": size,
                "segments": segment_count,
                "running": running,
                "pid": pid,
                "users": users,
                "modified": latest_mtime(directory),
            })
        return items

    def stop_hls_key(self, key: str) -> bool:
        stopped = False
        with TRANSCODE_LOCK:
            stream = TRANSCODES.get(key)
            if stream:
                process = stream.get("process")
                if process and process.poll() is None:
                    stopped = stop_process(process) or stopped
                TRANSCODES.pop(key, None)
        pid_file = HLS_CACHE_DIR / key / "ffmpeg.pid"
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, 15)
                stopped = True
            except (OSError, ValueError):
                pass
            try:
                pid_file.unlink(missing_ok=True)
            except OSError:
                pass
        return stopped

    def stop_direct_stream(self, stream_id: str) -> bool:
        with DIRECT_STREAM_LOCK:
            stream = DIRECT_STREAMS.get(stream_id)
            if not stream:
                return False
            stream["killed"] = True
            request = stream.get("request")
        try:
            request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            request.close()
        except OSError:
            pass
        return True

    def direct_stream_items(self) -> list[dict]:
        with DIRECT_STREAM_LOCK:
            items = []
            now = time.time()
            for stream_id, stream in list(DIRECT_STREAMS.items()):
                if stream.get("done"):
                    DIRECT_STREAMS.pop(stream_id, None)
                    continue
                copied = dict(stream)
                copied["id"] = stream_id
                copied["age_seconds"] = now - float(stream.get("started_at") or now)
                items.append(copied)
            return sorted(items, key=lambda item: item.get("started_at") or 0, reverse=True)

    def stop_all_hls(self) -> int:
        count = 0
        keys = {item["key"] for item in self.hls_cache_items() if item["running"]}
        for key in keys:
            if self.stop_hls_key(key):
                count += 1
        return count

    def clear_hls_cache(self) -> int:
        self.stop_all_hls()
        removed = 0
        try:
            directories = [path for path in HLS_CACHE_DIR.iterdir() if path.is_dir()]
        except OSError:
            return 0
        for directory in directories:
            try:
                shutil.rmtree(directory)
                removed += 1
            except OSError:
                pass
        with TRANSCODE_LOCK:
            TRANSCODES.clear()
        return removed

    def downloads_page(self, user):
        mobile_items = self.mobile_download_cache_items()
        mobile_cache_size = sum(item["size"] for item in mobile_items)

        def mobile_card(item: dict) -> str:
            ready_link = ""
            if item["ready"] and item["download_url"]:
                ready_link = f"<a class='download-link' href='{html.escape(item['download_url'])}'>Download Prepared File</a>"
            eta = f" &middot; estimate {format_seconds(item['estimate_seconds'])}" if item["estimate_seconds"] else ""
            message = html.escape(item["message"][:260])
            return f"""
            <article class="download-card">
              <div class="download-head">
                <a href="{html.escape(item['href'])}"><h2>{html.escape(item['title'])}</h2></a>
                <span class="state {html.escape(item['status'])}">{html.escape(item['status'].title())}</span>
              </div>
              <div class="meta">{html.escape(item['scope'])} {html.escape(item['item_id'])} &middot; {movie_app.human_size(item['size'])}{eta}</div>
              <div class="bar"><i style="width:{item['progress']:.1f}%"></i></div>
              <div class="meta">{item['progress']:.0f}% &middot; {message}</div>
              {ready_link}
            </article>"""

        rows = "".join(mobile_card(item) for item in mobile_items) or "<p class='empty'>No compressed mobile downloads are queued or ready.</p>"
        return self.render_html(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Downloads</title><style>
:root {{ color-scheme:dark; --bg:#080a0f; --panel:#11151d; --line:#263041; --gold:#f5b73f; --muted:#aab4c3; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }}
header {{ position:sticky; top:0; z-index:3; display:flex; justify-content:space-between; align-items:center; gap:12px; padding:18px 22px; background:rgba(8,10,15,.94); border-bottom:1px solid var(--line); }}
a {{ color:#fff; }} main {{ padding:22px; max-width:920px; margin:0 auto; }} h1,h2,p {{ margin-top:0; }}
.summary {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }}
.pill {{ min-height:34px; display:inline-flex; align-items:center; padding:0 12px; border-radius:999px; background:#151b25; border:1px solid var(--line); color:#dbe3ef; font-weight:850; }}
.grid {{ display:grid; gap:14px; }}
.download-card {{ border:1px solid var(--line); border-radius:18px; background:var(--panel); padding:15px; }}
.download-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
.download-head a {{ color:#fff; text-decoration:none; min-width:0; }}
.download-head h2 {{ margin:0 0 6px; font-size:20px; line-height:1.14; }}
.meta {{ color:var(--muted); font-size:13px; margin:7px 0; overflow-wrap:anywhere; }}
.bar {{ width:100%; height:8px; border-radius:999px; background:#030507; overflow:hidden; margin:7px 0; }}
.bar i {{ display:block; height:100%; border-radius:999px; background:var(--gold); }}
.state {{ flex:0 0 auto; border-radius:999px; padding:5px 9px; background:#202836; color:#dbe3ef; font-size:12px; font-weight:900; }}
.state.ready {{ background:#15351f; color:#baffc4; }} .state.error {{ background:#411924; color:#ffd9df; }} .state.running,.state.queued {{ background:#3a2b0c; color:#ffdc8a; }}
.download-link {{ display:inline-flex; min-height:36px; align-items:center; margin-top:10px; padding:0 13px; border-radius:999px; background:#1d7f3a; color:#fff; text-decoration:none; font-weight:900; }}
.empty {{ color:var(--muted); padding:14px; border:1px dashed var(--line); border-radius:14px; }}
@media (max-width:650px) {{ main {{ padding:16px; }} header {{ padding:14px 16px; align-items:flex-start; flex-direction:column; }} .download-head h2 {{ font-size:17px; }} }}
</style></head><body><header><strong>CineMediaVault Downloads</strong><nav><a href="/">Home</a> &middot; <a href="/movies">Movies</a> &middot; <a href="/tv">TV Shows</a> &middot; <a href="/logout">Logout</a></nav></header>
<main><h1>Downloads</h1><div class="summary"><span class="pill">{len(mobile_items)} mobile download job(s)</span><span class="pill">{movie_app.human_size(mobile_cache_size)} prepared cache</span></div><div class="grid">{rows}</div></main></body></html>""")

    def clear_mobile_download_cache(self) -> int:
        removed = 0
        MOBILE_DOWNLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with MOBILE_DOWNLOAD_LOCK:
            MOBILE_DOWNLOAD_JOBS.clear()
        try:
            directories = [path for path in MOBILE_DOWNLOAD_CACHE_DIR.iterdir() if path.is_dir()]
        except OSError:
            return 0
        for directory in directories:
            try:
                shutil.rmtree(directory)
                removed += 1
            except OSError:
                pass
        return removed

    def remove_mobile_download_cache_job(self, job_id: str) -> bool:
        if not re.fullmatch(r"[a-f0-9]{20}", job_id or ""):
            return False
        job_dir = MOBILE_DOWNLOAD_CACHE_DIR / job_id
        removed = False
        with MOBILE_DOWNLOAD_LOCK:
            MOBILE_DOWNLOAD_JOBS.pop(job_id, None)
        if job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
            removed = True
        return removed

    def mobile_download_cache_items(self) -> list[dict]:
        MOBILE_DOWNLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        records: list[dict] = []
        with MOBILE_DOWNLOAD_LOCK:
            live = {key: mobile_status_payload(value) for key, value in MOBILE_DOWNLOAD_JOBS.items()}
        seen: set[str] = set()
        for job_id, job in live.items():
            seen.add(job_id)
            job_dir = Path(job.get("dir") or (MOBILE_DOWNLOAD_CACHE_DIR / job_id))
            records.append(self.mobile_download_admin_record(job_id, job, job_dir))
        try:
            status_files = sorted(MOBILE_DOWNLOAD_CACHE_DIR.glob("*/status.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            status_files = []
        for status_file in status_files:
            job_id = status_file.parent.name
            if job_id in seen:
                continue
            try:
                job = json.loads(status_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records.append(self.mobile_download_admin_record(job_id, job, status_file.parent))
        records.sort(key=lambda item: item["updated_at"], reverse=True)
        return records

    def mobile_download_admin_record(self, job_id: str, job: dict, job_dir: Path) -> dict:
        size = 0
        try:
            for path in job_dir.rglob("*"):
                if path.is_file():
                    size += path.stat().st_size
        except OSError:
            pass
        progress = float(job.get("progress") or 0)
        updated_at = float(job.get("completed_at") or job.get("created_at") or 0)
        status = str(job.get("status") or "unknown")
        title = str(job.get("title") or f"{job.get('scope', 'job')} {job.get('item_id', job_id)}")
        message = str(job.get("message") or job.get("error") or "")
        href = "#"
        if job.get("scope") == "movie":
            href = f"/player/movie/{job.get('item_id')}"
        elif job.get("scope") in {"tv", "episode"}:
            href = f"/player/tv/{job.get('item_id')}"
        elif job.get("scope") == "season":
            href = f"/tv"
        return {
            "job_id": job_id, "title": title, "scope": str(job.get("scope") or ""), "item_id": str(job.get("item_id") or ""),
            "status": status, "ready": bool(job.get("ready")), "progress": max(0.0, min(100.0, progress)),
            "message": message, "size": size, "updated_at": updated_at, "download_url": str(job.get("download_url") or ""),
            "filename": str(job.get("filename") or ""), "estimate_seconds": int(job.get("estimate_seconds") or 0),
            "href": href,
        }

    def direct_stream(self, kind: str, item_id: str, head_only: bool = False):
        if kind == "movie":
            item = movie_app.safe_item(item_id)
            path = item.path
            info = self.hls_media_summary("movie", str(item.id))
            media_id = int(item.id)
        else:
            episode = tv_app.safe_episode(item_id)
            path = episode.path
            info = self.hls_media_summary("tv", str(episode.id))
            media_id = int(episode.id)

        file_size = path.stat().st_size
        start = 0
        end = file_size - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                end = min(end, file_size - 1)
                status = 206
        length = max(0, end - start + 1)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        encoded_name = urllib.parse.quote(path.name)
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{encoded_name}")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if head_only:
            return

        user = self.current_user()
        request_query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        is_video_wall = request_query.get("wall", [""])[0] == "1"
        wall_user_id = int(user["id"]) if user and is_video_wall else 0
        user_label = "Unknown user"
        if user:
            user_label = user["full_name"] or user["username"] or user_label
        duration = float(info.get("duration") or ffprobe_duration(path) or 0)
        stream_id = secrets.token_urlsafe(12)
        stream = {
            "id": stream_id,
            "kind": kind,
            "media_id": media_id,
            "item_id": str(item_id),
            "title": info.get("title") or f"{kind} {item_id}",
            "subtitle": info.get("subtitle") or "",
            "poster": info.get("poster") or "",
            "href": info.get("href") or "/",
            "user": user_label,
            "started_at": time.time(),
            "range_start": start,
            "range_end": end,
            "file_size": file_size,
            "bytes_sent": 0,
            "position": 0.0,
            "duration": duration,
            "request": self.request,
            "killed": False,
            "done": False,
            "remote": self.client_address[0] if self.client_address else "",
        }
        with DIRECT_STREAM_LOCK:
            DIRECT_STREAMS[stream_id] = stream
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    with DIRECT_STREAM_LOCK:
                        if DIRECT_STREAMS.get(stream_id, {}).get("killed"):
                            break
                    chunk = handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                    sent = length - remaining
                    byte_position = min(file_size, start + sent)
                    position = (byte_position / file_size) * duration if file_size > 0 and duration > 0 else 0.0
                    with DIRECT_STREAM_LOCK:
                        current = DIRECT_STREAMS.get(stream_id)
                        if current:
                            current["bytes_sent"] = sent
                            current["position"] = position
                            current["updated_at"] = time.time()
                        if wall_user_id:
                            DIRECT_BANDWIDTH_SAMPLES.append((time.monotonic(), wall_user_id, len(chunk)))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with DIRECT_STREAM_LOCK:
                current = DIRECT_STREAMS.get(stream_id)
                if current:
                    current["done"] = True
                DIRECT_STREAMS.pop(stream_id, None)

    def admin_hls_page(self, user, message: str = ""):
        items = self.hls_cache_items()
        active = [item for item in items if item["running"]]
        direct_items = self.direct_stream_items()
        mobile_items = self.mobile_download_cache_items()
        cache_size = sum(item["size"] for item in items)
        mobile_cache_size = sum(item["size"] for item in mobile_items)
        mobile_retention_hours = int(mobile_download_cache_max_age_hours())

        def card(item: dict, active_view: bool) -> str:
            poster = f"<img src='{html.escape(item['poster'])}' alt=''>" if item["poster"] else "<div class='poster missing'>No Poster</div>"
            users = item["users"] or [{"user": "Unknown user", "position": 0, "duration": item["duration"], "progress": 0, "updated": ""}]
            user_rows = []
            for row in users:
                duration = row["duration"] or item["duration"]
                progress = row["progress"] or ((row["position"] / duration) * 100.0 if duration else 0.0)
                user_rows.append(
                    f"<div class='user-row'><strong>{html.escape(row['user'])}</strong>"
                    f"<span>{format_seconds(row['position'])} / {format_seconds(duration)}</span>"
                    f"<div class='bar'><i style='width:{max(0,min(100,progress)):.1f}%'></i></div></div>"
                )
            status = "Running" if item["running"] else "Cached"
            pid = f"PID {item['pid']}" if item["pid"] else "No PID"
            return f"""
            <article class="stream-card">
              <a class="poster-link" href="{html.escape(item['href'])}">{poster}</a>
              <div class="stream-copy">
                <h3>{html.escape(item['title'])}</h3>
                <p>{html.escape(item['subtitle'])}</p>
                <div class="meta">{status} &middot; {pid} &middot; {item['segments']} segments &middot; {movie_app.human_size(item['size'])}</div>
                <div class="bar cache"><i style="width:{item['progress']:.1f}%"></i></div>
                <div class="meta">Cache progress {format_seconds(item['cached_seconds'])} / {format_seconds(item['duration'])}</div>
                <div class="users">{''.join(user_rows)}</div>
                <form method="post" action="/admin/hls"><input type="hidden" name="action" value="stop"><input type="hidden" name="key" value="{html.escape(item['key'])}"><button type="submit"{'' if item['running'] else ' disabled'}>Kill Stream</button></form>
              </div>
            </article>"""

        def direct_card(item: dict) -> str:
            poster = f"<img src='{html.escape(item['poster'])}' alt=''>" if item.get("poster") else "<div class='poster missing'>No Poster</div>"
            duration = float(item.get("duration") or 0)
            position = float(item.get("position") or 0)
            progress = (position / duration * 100.0) if duration > 0 else 0.0
            return f"""
            <article class="stream-card direct">
              <a class="poster-link" href="{html.escape(item.get('href') or '/')}">{poster}</a>
              <div class="stream-copy">
                <h3>{html.escape(item.get('title') or 'Direct Stream')}</h3>
                <p>{html.escape(item.get('subtitle') or '')}</p>
                <div class="meta">Direct HTTP &middot; {html.escape(item.get('remote') or '')} &middot; age {format_seconds(item.get('age_seconds') or 0)}</div>
                <div class="user-row"><strong>{html.escape(item.get('user') or 'Unknown user')}</strong><span>{format_seconds(position)} / {format_seconds(duration)}</span><div class="bar"><i style="width:{max(0,min(100,progress)):.1f}%"></i></div></div>
                <form method="post" action="/admin/hls"><input type="hidden" name="action" value="stop_direct"><input type="hidden" name="stream_id" value="{html.escape(item['id'])}"><button type="submit">Kill Direct Stream</button></form>
              </div>
            </article>"""

        def mobile_card(item: dict) -> str:
            ready_link = ""
            if item["ready"] and item["download_url"]:
                ready_link = f"<a class='download-link' href='{html.escape(item['download_url'])}'>Download Prepared File</a>"
            eta = f" &middot; estimate {format_seconds(item['estimate_seconds'])}" if item["estimate_seconds"] else ""
            message = html.escape(item["message"][:260])
            return f"""
            <article class="mobile-card">
              <div class="mobile-head">
                <a href="{html.escape(item['href'])}"><h3>{html.escape(item['title'])}</h3></a>
                <span class="state {html.escape(item['status'])}">{html.escape(item['status'].title())}</span>
              </div>
              <div class="meta">{html.escape(item['scope'])} {html.escape(item['item_id'])} &middot; {movie_app.human_size(item['size'])}{eta}</div>
              <div class="bar"><i style="width:{item['progress']:.1f}%"></i></div>
              <div class="meta">{item['progress']:.0f}% &middot; {message}</div>
              <div class="job-actions">{ready_link}<form method="post" action="/admin/hls" onsubmit="return confirm('Delete this prepared mobile download?')"><input type="hidden" name="action" value="clear_mobile_job"><input type="hidden" name="job_id" value="{html.escape(item['job_id'])}"><button class="danger-btn small-btn" type="submit">Delete</button></form></div>
            </article>"""

        active_rows = "".join(card(item, True) for item in active) or "<p class='empty'>No active HLS ffmpeg streams.</p>"
        direct_rows = "".join(direct_card(item) for item in direct_items) or "<p class='empty'>No active direct HTTP streams.</p>"
        mobile_rows = "".join(mobile_card(item) for item in mobile_items) or "<p class='empty'>No compressed mobile download jobs.</p>"
        cache_rows = "".join(card(item, False) for item in items) or "<p class='empty'>No HLS cache folders.</p>"
        note = f"<div class='note'>{html.escape(message)}</div>" if message else ""
        return self.render_html(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault HLS Streams</title><style>
:root {{ color-scheme:dark; --bg:#080a0f; --panel:#11151d; --line:#263041; --gold:#f5b73f; --danger:#cf3448; --muted:#aab4c3; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }}
header {{ position:sticky; top:0; z-index:3; display:flex; justify-content:space-between; align-items:center; gap:12px; padding:18px 22px; background:rgba(8,10,15,.94); border-bottom:1px solid var(--line); }}
a {{ color:#fff; }} main {{ padding:22px; max-width:1180px; margin:0 auto; }} h1,h2,h3,p {{ margin-top:0; }} .summary {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:18px; }}
.pill {{ min-height:34px; display:inline-flex; align-items:center; padding:0 12px; border-radius:999px; background:#151b25; border:1px solid var(--line); color:#dbe3ef; font-weight:850; }}
.controls {{ display:flex; gap:10px; flex-wrap:wrap; margin:0 0 22px; }} button {{ min-height:38px; border:0; border-radius:999px; padding:0 15px; background:var(--gold); color:#111; font-weight:950; cursor:pointer; }} button:disabled {{ opacity:.45; cursor:not-allowed; }} .danger-btn {{ background:var(--danger); color:#fff; }}
.note {{ padding:12px 14px; border-radius:12px; background:#15351f; color:#dfffe7; font-weight:800; margin-bottom:16px; }}
.section {{ margin:0 0 28px; }} .grid {{ display:grid; gap:14px; }} .stream-card {{ display:grid; grid-template-columns:110px minmax(0,1fr); gap:16px; border:1px solid var(--line); border-radius:18px; background:var(--panel); padding:14px; }}
.poster-link img,.poster {{ width:110px; aspect-ratio:2/3; object-fit:cover; border-radius:10px; background:#07090d; }} .poster.missing {{ display:grid; place-items:center; color:#8290a3; font-size:12px; font-weight:900; border:1px solid var(--line); text-align:center; }}
h3 {{ margin:0 0 4px; font-size:20px; }} p {{ color:#dbe2ec; margin:0 0 8px; }} .meta {{ color:var(--muted); font-size:13px; margin:6px 0; }}
.bar {{ width:100%; height:8px; border-radius:999px; background:#030507; overflow:hidden; margin:5px 0; }} .bar i {{ display:block; height:100%; border-radius:999px; background:var(--gold); }}
.users {{ display:grid; gap:9px; margin:12px 0; }} .user-row {{ border:1px solid rgba(255,255,255,.10); border-radius:12px; padding:9px; background:rgba(255,255,255,.04); }} .user-row span {{ display:block; color:var(--muted); font-size:12px; margin-top:3px; }}
.mobile-card,.settings-card {{ border:1px solid var(--line); border-radius:18px; background:var(--panel); padding:14px; margin-bottom:18px; }}
.mobile-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
.mobile-head a {{ color:#fff; text-decoration:none; }}
.state {{ flex:0 0 auto; border-radius:999px; padding:5px 9px; background:#202836; color:#dbe3ef; font-size:12px; font-weight:900; }}
.state.ready {{ background:#15351f; color:#baffc4; }} .state.error {{ background:#411924; color:#ffd9df; }} .state.running,.state.queued {{ background:#3a2b0c; color:#ffdc8a; }}
.download-link {{ display:inline-flex; min-height:34px; align-items:center; margin-top:10px; padding:0 12px; border-radius:999px; background:#1d7f3a; color:#fff; text-decoration:none; font-weight:900; }} .job-actions {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }} .small-btn {{ min-height:34px; padding:0 12px; margin-top:10px; }} label {{ display:inline-flex; gap:7px; align-items:center; min-height:38px; padding:0 12px; border:1px solid var(--line); border-radius:999px; background:#151b25; font-weight:850; }}
.empty {{ color:var(--muted); padding:14px; border:1px dashed var(--line); border-radius:14px; }}
@media (max-width:650px) {{ main {{ padding:16px; }} header {{ padding:14px 16px; align-items:flex-start; flex-direction:column; }} .stream-card {{ grid-template-columns:82px minmax(0,1fr); gap:12px; padding:12px; }} .poster-link img,.poster {{ width:82px; }} h3 {{ font-size:17px; }} }}
</style></head><body><header><strong>CineMediaVault HLS Admin</strong><nav><a href="/admin/activity">Activity</a> | <a href="/admin/users">Users</a> &middot; <a href="/">Home</a></nav></header>
<main>{note}<div class="summary"><span class="pill">{len(active)} active HLS stream(s)</span><span class="pill">{len(direct_items)} active direct stream(s)</span><span class="pill">{len(items)} cache folder(s)</span><span class="pill">{movie_app.human_size(cache_size)} cache used</span></div>
<div class="summary"><span class="pill">{len(mobile_items)} mobile download job(s)</span><span class="pill">{movie_app.human_size(mobile_cache_size)} mobile cache used</span></div>
<form class="controls" method="post" action="/admin/hls"><button class="danger-btn" name="action" value="stop_all" type="submit">Stop All ffmpeg HLS</button><button class="danger-btn" name="action" value="clear_cache" type="submit" onclick="return confirm('Stop all HLS streams and clear the HLS cache')">Clear HLS Cache</button><button class="danger-btn" name="action" value="clear_mobile_cache" type="submit" onclick="return confirm('Clear prepared compressed mobile downloads')">Clear Mobile Download Cache</button></form>
<section class="settings-card"><h2>Download Cache Retention</h2><p>Prepared compressed downloads are kept for this long before automatic cleanup. Default is 24 hours.</p><form class="controls" method="post" action="/admin/hls"><input type="hidden" name="action" value="set_mobile_cache_age"><label><input type="radio" name="hours" value="6" {'checked' if mobile_retention_hours == 6 else ''}> 6 hours</label><label><input type="radio" name="hours" value="12" {'checked' if mobile_retention_hours == 12 else ''}> 12 hours</label><label><input type="radio" name="hours" value="24" {'checked' if mobile_retention_hours == 24 else ''}> 24 hours</label><button type="submit">Save</button></form></section>
<section class="section"><h2>Live Streams</h2><div class="grid">{active_rows}</div></section>
<section class="section"><h2>Direct Streams</h2><div class="grid">{direct_rows}</div></section>
<section class="section"><h2>Mobile Download Jobs</h2><div class="grid">{mobile_rows}</div></section>
<section class="section"><h2>HLS Cache</h2><div class="grid">{cache_rows}</div></section>
</main></body></html>""")

    def admin_hls_submit(self, user):
        form = self.read_form()
        action = form.get("action") or ""
        if action == "stop":
            key = form.get("key") or ""
            stopped = self.stop_hls_key(key)
            return self.admin_hls_page(user, f"{'Stopped' if stopped else 'No running process for'} {key}.")
        if action == "stop_direct":
            stream_id = form.get("stream_id") or ""
            stopped = self.stop_direct_stream(stream_id)
            return self.admin_hls_page(user, f"{'Stopped direct stream' if stopped else 'No active direct stream'} {stream_id}.")
        if action == "stop_all":
            count = self.stop_all_hls()
            return self.admin_hls_page(user, f"Stopped {count} active HLS stream(s).")
        if action == "clear_cache":
            count = self.clear_hls_cache()
            return self.admin_hls_page(user, f"Removed {count} HLS cache folder(s).")
        if action == "clear_mobile_cache":
            count = self.clear_mobile_download_cache()
            return self.admin_hls_page(user, f"Removed {count} mobile download cache folder(s).")
        if action == "clear_mobile_job":
            job_id = form.get("job_id") or ""
            removed = self.remove_mobile_download_cache_job(job_id)
            return self.admin_hls_page(user, f"{'Removed' if removed else 'Could not remove'} mobile download job {job_id}.")
        if action == "set_mobile_cache_age":
            try:
                hours = save_mobile_download_cache_max_age(int(form.get("hours") or 24))
                cleanup_mobile_download_cache_once()
                return self.admin_hls_page(user, f"Mobile download cache retention set to {hours} hours.")
            except Exception as exc:
                return self.admin_hls_page(user, f"Could not save mobile download cache retention: {exc}")
        return self.admin_hls_page(user, "No action selected.")

    def admin_media_context(self, kind: str, item_id: str) -> dict:
        if kind == "movie":
            item = movie_app.safe_item(item_id)
            metadata = movie_app.metadata_for(item)
            title = metadata.get("title") or item.title
            path = item.path
            size = int(getattr(item, "size", 0) or (path.stat().st_size if path.exists() else 0))
            return {"kind": "movie", "id": str(item.id), "match_id": str(item.id), "title": title, "subtitle": "Movie", "path": path, "size": size, "back": f"/player/movie/{item.id}"}
        if kind == "tv":
            episode = tv_app.safe_episode(item_id)
            show = show_for_episode(episode)
            metadata = tv_app.metadata_for(show) if show else {}
            episode_meta = tv_episode_display_metadata(metadata, episode)
            title = f"{episode.show} - {episode_label(episode)} - {episode_meta['title']}"
            path = episode.path
            size = int(getattr(episode, "size", 0) or (path.stat().st_size if path.exists() else 0))
            match_id = str(show.id) if show else str(episode.id)
            return {"kind": "tv", "id": str(episode.id), "match_id": match_id, "title": title, "subtitle": "TV Episode", "path": path, "size": size, "back": f"/player/tv/{episode.id}"}
        raise FileNotFoundError("Unknown media kind")

    def delete_path_allowed(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def admin_media_page(self, user, kind: str, item_id: str, message: str = ""):
        item = self.admin_media_context(kind, item_id)
        path = Path(item["path"]).resolve(strict=False)
        root = Path(getattr(movie_app, "MOVIE_ROOT", MOVIE_ROOT) if kind == "movie" else getattr(tv_app, "TV_ROOT", TV_ROOT)).resolve(strict=False)
        allowed = self.delete_path_allowed(path, root)
        exists = path.is_file()
        delete_disabled = "" if allowed and exists else " disabled"
        status = "Ready" if allowed and exists else "Blocked"
        note = f"<div class='note'>{html.escape(message)}</div>" if message else ""
        return self.render_html(f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Admin - {html.escape(item['title'])}</title>
<style>
:root {{ color-scheme:dark; --bg:#080a0f; --panel:#10141d; --line:#242b36; --gold:#f5b73f; --danger:#cf3448; }}
* {{ box-sizing:border-box; }} body {{ margin:0; background:var(--bg); color:#fff; font-family:Inter,system-ui,Segoe UI,sans-serif; }}
header {{ position:sticky; top:0; z-index:2; display:grid; grid-template-columns:48px 1fr; align-items:center; gap:10px; min-height:68px; padding:12px 20px; background:rgba(8,10,15,.94); border-bottom:1px solid var(--line); }}
a {{ color:#fff; }} .back {{ width:44px; height:44px; display:grid; place-items:center; border-radius:999px; background:#151a23; text-decoration:none; font-size:25px; }}
h1 {{ margin:0; font-size:22px; text-align:center; }} main {{ width:min(860px,100%); padding:22px; }}
.menu {{ display:grid; gap:12px; margin:8px 0 22px; }} .menu a {{ min-height:48px; display:flex; align-items:center; padding:0 16px; border-radius:14px; background:#151a23; border:1px solid var(--line); font-size:20px; font-weight:850; color:#f1f4f8; text-decoration:none; }}
.panel {{ border:1px solid var(--line); border-radius:18px; background:var(--panel); padding:18px; margin:0 0 18px; }}
h2 {{ margin:0 0 12px; font-size:21px; }} .kv {{ display:grid; grid-template-columns:110px 1fr; gap:10px; color:#d9dee8; overflow-wrap:anywhere; }}
.label {{ color:#8f98a6; }} .note {{ margin:0 0 14px; padding:12px 14px; border-radius:12px; background:#15351f; color:#dfffe7; font-weight:800; }}
.danger {{ border-color:rgba(207,52,72,.55); }} .danger h2 {{ color:#ffb6bf; }} .warning {{ color:#ffccd2; line-height:1.35; }}
input {{ width:100%; min-height:42px; margin:10px 0; border-radius:12px; border:1px solid #343d4c; background:#090d14; color:#fff; padding:0 12px; font-size:16px; }}
button {{ min-height:42px; border:0; border-radius:999px; padding:0 18px; font-weight:950; cursor:pointer; }}
button.delete {{ background:var(--danger); color:#fff; }} button:disabled {{ opacity:.45; cursor:not-allowed; }}
@media (max-width:700px) {{ main {{ padding:20px; }} .kv {{ grid-template-columns:1fr; gap:3px; }} .menu div {{ font-size:20px; }} }}
</style></head><body>
<header><a class="back" href="{html.escape(item['back'])}">&lsaquo;</a><h1>{html.escape(item['title'])}</h1></header>
<main>{note}
<section class="menu">
<a href="/{html.escape(kind)}/fix-match/{html.escape(item['match_id'])}">Fix Match</a>
<a href="/{html.escape(kind)}/fix-match/{html.escape(item['match_id'])}">Update Poster</a>
<a href="#delete">Delete</a>
</section>
<section class="panel"><h2>File Info</h2><div class="kv">
<div class="label">Type</div><div>{html.escape(item['subtitle'])}</div>
<div class="label">Size</div><div>{html.escape(movie_app.human_size(item['size']))}</div>
<div class="label">Status</div><div>{html.escape(status)}</div>
<div class="label">Path</div><div>{html.escape(str(path))}</div>
<div class="label">Root</div><div>{html.escape(str(root))}</div>
</div></section>
<section class="panel danger" id="delete"><h2>Delete From Server And NFS</h2>
<p class="warning">This permanently deletes the media file shown above from the server/NFS path and removes its CineMediaVault watch-state rows. Type DELETE to enable the final delete.</p>
<form method="post" action="/admin/media/delete" onsubmit="return confirm('Delete this media file from server and NFS')">
<input type="hidden" name="kind" value="{html.escape(kind)}"><input type="hidden" name="item_id" value="{html.escape(item['id'])}">
<input name="confirm" placeholder="Type DELETE" autocomplete="off" required>
<button class="delete" type="submit"{delete_disabled}>Delete File</button>
</form></section>
</main></body></html>""")

    def remove_deleted_media_from_indexes(self, kind: str, item_id: str) -> None:
        numeric_id = int(item_id)
        if kind == "movie":
            movie_app.movie_index.items = [item for item in movie_app.movie_index.items if int(item.id) != numeric_id]
            if hasattr(movie_app.movie_index, "by_id"):
                movie_app.movie_index.by_id.pop(numeric_id, None)
            if hasattr(movie_app.movie_index, "save_live_cache"):
                movie_app.movie_index.save_live_cache(movie_app.movie_index.items)
            return
        if kind == "tv":
            episode = tv_app.tv_index.episode_by_id.pop(numeric_id, None)
            if episode:
                for show in tv_app.tv_index.shows:
                    for season in show.seasons.values():
                        season.episodes = [ep for ep in season.episodes if int(ep.id) != numeric_id]
                    show.count = sum(len(season.episodes) for season in show.seasons.values())
                    show.size = sum(ep.size for season in show.seasons.values() for ep in season.episodes)
            if hasattr(tv_app.tv_index, "save_cache"):
                tv_app.tv_index.save_cache()

    def admin_media_delete_submit(self, user):
        form = self.read_form()
        kind = form.get("kind") or ""
        item_id = form.get("item_id") or ""
        if form.get("confirm") != "DELETE":
            return self.admin_media_page(user, kind, item_id, "Delete was not performed. Confirmation text must be DELETE.")
        item = self.admin_media_context(kind, item_id)
        path = Path(item["path"]).resolve(strict=False)
        root = Path(getattr(movie_app, "MOVIE_ROOT", MOVIE_ROOT) if kind == "movie" else getattr(tv_app, "TV_ROOT", TV_ROOT)).resolve(strict=False)
        if not self.delete_path_allowed(path, root):
            return self.admin_media_page(user, kind, item_id, "Blocked: file is outside the configured media root.")
        if not path.is_file():
            return self.admin_media_page(user, kind, item_id, "Blocked: file does not exist.")
        deleted_path = str(path)
        path.unlink()
        self.remove_deleted_media_from_indexes(kind, item_id)
        conn = db_connect()
        try:
            conn.execute("DELETE FROM user_media_state WHERE media_type=? AND media_id=?", (kind, int(item_id)))
            conn.commit()
        finally:
            conn.close()
        return self.render_html(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineMediaVault Deleted</title><style>:root{{color-scheme:dark}}body{{margin:0;background:#080a0f;color:#fff;font-family:Inter,system-ui,Segoe UI,sans-serif;padding:24px}}a{{color:#fff}}.panel{{max-width:760px;border:1px solid #242b36;border-radius:18px;background:#10141d;padding:20px}}p{{overflow-wrap:anywhere;color:#cfd6e2}}</style></head>
<body><section class="panel"><h1>Deleted</h1><p>{html.escape(deleted_path)}</p><p><a href="/{'movies' if kind == 'movie' else 'tv'}">Back to library</a></p></section></body></html>""")

    def video_wall_item(self, media_type: str, media_id: int, media_path: str = "", include_compatibility: bool = True) -> dict | None:
        try:
            if media_type == "tuner":
                channel = hdhr_channel(int(media_id))
                if not channel:
                    return None
                return {"media_type": "tuner", "media_id": int(channel["id"]),
                        "title": f"{channel['guide_number']}  {channel['guide_name']}",
                        "subtitle": f"Live TV - {channel['friendly_name']}", "poster": "",
                        "stream_url": f"/hls/tuner/{channel['id']}/index.m3u8",
                        "hls_url": f"/hls/tuner/{channel['id']}/index.m3u8", "requires_hls": True,
                        "media_path": channel["stream_url"]}
            if media_type == "movie":
                item = next((value for value in movie_app.movie_index.items if media_path and str(value.path) == media_path), None)
                item = item or movie_app.safe_item(str(media_id))
                meta = movie_app.metadata_for(item)
                compatibility = browser_media_compatibility(item.path) if include_compatibility else {"requires_hls": False}
                return {"media_type": "movie", "media_id": int(item.id),
                        "title": meta.get("title") or item.title,
                        "subtitle": str(meta.get("year") or "Movie"),
                        "poster": movie_app.poster_url_for(item) or "",
                        "stream_url": f"/play/{item.id}", "hls_url": f"/hls/movie/{item.id}/index.m3u8",
                        "requires_hls": compatibility["requires_hls"], "media_path": str(item.path)}
            episode = next((value for value in tv_app.tv_index.episode_by_id.values() if media_path and str(value.path) == media_path), None)
            episode = episode or tv_app.safe_episode(str(media_id))
            show = show_for_episode(episode)
            meta = tv_app.metadata_for(show) if show else {}
            episode_meta = tv_episode_display_metadata(meta, episode)
            compatibility = browser_media_compatibility(episode.path) if include_compatibility else {"requires_hls": False}
            return {"media_type": "tv", "media_id": int(episode.id),
                    "title": episode.show,
                    "subtitle": f"{episode_label(episode)} - {episode_meta.get('title') or episode.title}",
                    "poster": (tv_app.poster_url_for(show) if show else "") or "",
                    "stream_url": f"/play/episode/{episode.id}", "hls_url": f"/hls/tv/{episode.id}/index.m3u8",
                    "requires_hls": compatibility["requires_hls"], "media_path": str(episode.path)}
        except Exception:
            return None

    def video_wall_page(self):
        return self.render_html(VIDEO_WALL_PAGE)

    def live_tv_page(self):
        return self.render_html(LIVE_TV_PAGE)

    def api_hdhr_devices(self, user):
        conn = db_connect()
        try:
            devices = [dict(row) for row in conn.execute("""SELECT d.*,
                (SELECT COUNT(*) FROM hdhr_channels c WHERE c.device_id=d.device_id) AS channel_count
                FROM hdhr_devices d WHERE d.enabled=1 ORDER BY d.friendly_name""").fetchall()]
            channels = [dict(row) for row in conn.execute("""SELECT c.*,d.friendly_name,d.model_number
                FROM hdhr_channels c JOIN hdhr_devices d ON d.device_id=c.device_id
                WHERE d.enabled=1 ORDER BY CAST(c.guide_number AS REAL),c.guide_number""").fetchall()]
        finally:
            conn.close()
        guide = hdhr_guide_snapshot()
        now = int(time.time())
        for channel in channels:
            schedule = guide.get("programmes", {}).get(str(channel["guide_number"]), [])
            current_index = next((index for index, item in enumerate(schedule) if item["start"] <= now < item["stop"]), -1)
            channel["current"] = schedule[current_index] if current_index >= 0 else None
            channel["next"] = schedule[current_index + 1] if current_index >= 0 and current_index + 1 < len(schedule) else None
        return self.json_response({"ok": True, "devices": devices, "channels": channels,
            "guide_updated_at": guide.get("updated_at", 0), "programme_count": guide.get("programme_count", 0)})

    def api_video_wall(self, user):
        conn = db_connect()
        try:
            rows = conn.execute("SELECT slot, media_type, media_id, media_path FROM user_video_wall WHERE user_id=? ORDER BY slot", (int(user["id"]),)).fetchall()
        finally:
            conn.close()
        slots = []
        for row in rows:
            item = self.video_wall_item(row["media_type"], int(row["media_id"]), row["media_path"])
            if item:
                item["slot"] = int(row["slot"])
                item.pop("media_path", None)
                slots.append(item)
        return self.json_response({"ok": True, "slots": slots})

    def api_video_wall_slot(self, user):
        payload = self.read_json()
        try:
            slot = int(payload.get("slot"))
        except Exception:
            slot = 0
        if slot not in {1, 2, 3, 4}:
            return self.json_response({"ok": False, "error": "slot must be 1 through 4"})
        conn = db_connect()
        try:
            media_type = str(payload.get("media_type") or "")
            media_id = payload.get("media_id")
            if not media_id:
                conn.execute("DELETE FROM user_video_wall WHERE user_id=? AND slot=?", (int(user["id"]), slot))
            elif media_type in {"movie", "tv", "tuner"} and (wall_item := self.video_wall_item(media_type, int(media_id))):
                conn.execute("""INSERT INTO user_video_wall(user_id,slot,media_type,media_id,media_path,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,slot) DO UPDATE SET
                    media_type=excluded.media_type,media_id=excluded.media_id,media_path=excluded.media_path,updated_at=excluded.updated_at""",
                    (int(user["id"]), slot, media_type, int(wall_item["media_id"]), wall_item["media_path"], auth_now()))
            else:
                return self.json_response({"ok": False, "error": "media item not found"})
            conn.commit()
        finally:
            conn.close()
        return self.json_response({"ok": True, "slot": slot})

    def api_video_wall_search(self, user):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0].strip().casefold()
        found = []
        if len(query) >= 2:
            for item in list(movie_app.movie_index.items):
                meta = movie_app.metadata_for(item)
                title = str(meta.get("title") or item.title)
                haystack = f"{title} {meta.get('year') or ''} {item.path.name}".casefold()
                if query in haystack:
                    found.append(self.video_wall_item("movie", int(item.id), include_compatibility=False))
                    if len(found) >= 30:
                        break
            if len(found) < 30:
                for episode in list(tv_app.tv_index.episode_by_id.values()):
                    haystack = f"{episode.show} {episode.title} {episode_label(episode)} {episode.path.name}".casefold()
                    if query in haystack:
                        found.append(self.video_wall_item("tv", int(episode.id), include_compatibility=False))
                        if len(found) >= 30:
                            break
            if len(found) < 30:
                conn = db_connect()
                try:
                    channels = conn.execute("""SELECT id FROM hdhr_channels
                        WHERE lower(guide_number || ' ' || guide_name) LIKE ?
                        ORDER BY CAST(guide_number AS REAL) LIMIT ?""",
                        (f"%{query}%", 30 - len(found))).fetchall()
                finally:
                    conn.close()
                for channel in channels:
                    found.append(self.video_wall_item("tuner", int(channel["id"]), include_compatibility=False))
        public_items = []
        for item in found:
            if item:
                item.pop("media_path", None)
                public_items.append(item)
        return self.json_response({"ok": True, "items": public_items})

    def api_video_wall_bandwidth(self, user):
        now = time.monotonic()
        window_seconds = 3.0
        user_id = int(user["id"])
        with DIRECT_STREAM_LOCK:
            while DIRECT_BANDWIDTH_SAMPLES and DIRECT_BANDWIDTH_SAMPLES[0][0] < now - 10.0:
                DIRECT_BANDWIDTH_SAMPLES.popleft()
            byte_count = sum(
                sample_bytes for sample_time, sample_user, sample_bytes in DIRECT_BANDWIDTH_SAMPLES
                if sample_time >= now - window_seconds and sample_user == user_id
            )
        bytes_per_second = byte_count / window_seconds
        return self.json_response({
            "ok": True,
            "window_seconds": window_seconds,
            "bytes_per_second": round(bytes_per_second, 2),
            "mbps": round(bytes_per_second * 8 / 1_000_000, 3),
        })

    def watch_media(self, kind: str, item_id: str):
        item = media_for_kind(kind, item_id)
        stream = ensure_hls_stream(kind, item_id, item["path"])
        body = (
            PLAYER_PAGE
            .replace("{{TITLE}}", html.escape(item["title"]))
            .replace("{{BACK}}", item["back"])
            .replace("{{PLAYLIST}}", stream["playlist_url"])
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def direct_player(self, kind: str, item_id: str):
        user = self.current_user()
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        playback_mode = (query.get("mode", [read_global_playback_mode()])[0] or "direct").lower()
        if playback_mode not in {"direct", "hls"}:
            playback_mode = "direct"
        context = direct_player_context(kind, item_id, playback_mode=playback_mode)
        if user and user["is_admin"]:
            context["actions"] += action_link("A", "Admin", f"/admin/media/{kind}/{item_id}")
        direct_play = query.get("play", [""])[0] == "1"
        base_href = f"/player/{kind}/{item_id}"
        direct_href = f"{base_href}?mode=direct"
        hls_href = f"{base_href}?mode=hls"
        body = (
            DIRECT_PLAYER_PAGE
            .replace("{{TITLE}}", html.escape(context["title"]))
            .replace("{{SUBTITLE}}", html.escape(context["subtitle"]))
            .replace("{{BACK}}", context["back"])
            .replace("{{SOURCE}}", context["source"])
            .replace("{{BACKGROUND_STYLE}}", context["background"])
            .replace("{{POSTER}}", context["poster"])
            .replace("{{EPISODE_TITLE}}", html.escape(context["episode_title"]))
            .replace("{{EPISODE_OR_TITLE}}", html.escape(context["episode_or_title"]))
            .replace("{{META}}", context["meta"])
            .replace("{{SUMMARY}}", html.escape(context["summary"]))
            .replace("{{ACTORS}}", context["actors"])
            .replace("{{VIDEO_LABEL}}", html.escape(context["video_label"]))
            .replace("{{SUBTITLE_TRACKS}}", context["caption_tracks"])
            .replace("{{CAPTION_CONTROL}}", context["caption_control"])
            .replace("{{SUBTITLE_SUMMARY}}", html.escape(context["caption_summary"]))
            .replace("{{DIRECT_MODE_CLASS}}", "active" if playback_mode == "direct" else "")
            .replace("{{HLS_MODE_CLASS}}", "active" if playback_mode == "hls" else "")
            .replace("{{DIRECT_MODE_HREF}}", html.escape(direct_href))
            .replace("{{HLS_MODE_HREF}}", html.escape(hls_href))
            .replace("{{DOWNLOAD_SIZE_OPEN_CLASS}}", "")
            .replace("{{SOURCE_BYTES}}", str(int(context.get("source_size") or 0)))
            .replace("{{SOURCE_SIZE_LABEL}}", html.escape(context.get("source_size_label") or ""))
            .replace("{{DOWNLOAD_DEFAULT_RATIO}}", f"{MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO:.2f}")
            .replace("{{DOWNLOAD_DEFAULT_PERCENT}}", str(int(round(MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO * 100))))
            .replace("{{DOWNLOAD_DEFAULT_LABEL}}", html.escape(movie_app.human_size(int((context.get("source_size") or 0) * MOBILE_DOWNLOAD_TARGET_SOURCE_RATIO))))
            .replace("{{ACTIONS}}", context["actions"])
            .replace("{{PLAYER_NAV}}", context["player_nav"])
            .replace("{{HERO_ATTRS}}", " style=\"display:none\"" if direct_play else "")
            .replace("{{PLAYER_OPEN_CLASS}}", " open" if direct_play else "")
            .replace("{{PLAYER_FULLSCREEN_CLASS}}", " fullscreen-mode" if direct_play else "")
            .replace("{{VIDEO_AUTOPLAY}}", " autoplay" if direct_play else "")
            .replace("{{ITEM_JSON}}", json.dumps(context["item"]))
            .replace("{{PREV_JSON}}", json.dumps(context["prev"]))
            .replace("{{NEXT_JSON}}", json.dumps(context["next"]))
        )
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_subtitle(self, request_path: str, head_only: bool = False):
        match = re.match(r"^/subtitles/(movie|tv)/([^/]+)/([a-z0-9-]+)\.vtt$", request_path)
        if not match:
            return self.send_error(404, "Invalid subtitle path")
        kind, item_id, token = match.groups()
        media = media_for_kind(kind, urllib.parse.unquote(item_id))
        tracks = discover_subtitles(media["path"], kind, item_id)
        track = next((entry for entry in tracks if entry["token"] == token), None)
        if not track:
            return self.send_error(404, "Subtitle track not found")
        source = Path(track["path"]).resolve()
        source_stat = source.stat()
        identity = f"{source}:{source_stat.st_mtime_ns}:{source_stat.st_size}:{track.get('stream')}"
        cache_name = hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".vtt"
        SUBTITLE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        target = SUBTITLE_CACHE_DIR / cache_name
        if not target.is_file() or target.stat().st_size < 8:
            temporary = target.with_suffix(".tmp")
            command = ["ffmpeg", "-y", "-v", "error", "-i", str(source)]
            if track["source"] == "embedded":
                command += ["-map", f"0:{int(track['stream'])}"]
            command += ["-f", "webvtt", str(temporary)]
            try:
                subprocess.run(command, check=True, timeout=90, capture_output=True)
                temporary.replace(target)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                print(f"Subtitle conversion failed for {source}: {exc}", flush=True)
                return self.send_error(500, "Subtitle conversion failed")
        size = target.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", "text/vtt; charset=utf-8")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head_only:
            with target.open("rb") as handle:
                shutil.copyfileobj(handle, self.wfile)

    def serve_hls(self, request_path: str):
        match = re.match(r"^/hls/(movie|tv|tuner)/([^/]+)/([^/]+)$", request_path)
        if not match:
            self.send_error(404, "Invalid HLS path")
            return
        kind, item_id, filename = match.groups()
        if "/" in filename or "\\" in filename or filename.startswith("."):
            self.send_error(403, "Invalid HLS filename")
            return
        if kind == "tuner":
            channel = hdhr_channel(int(item_id))
            if not channel:
                return self.send_error(404, "Tuner channel missing")
            item = {"path": channel["stream_url"], "duration": 0}
            stream = ensure_live_hls(item_id, channel["stream_url"])
        else:
            item = media_for_kind(kind, item_id)
            stream = ensure_hls_stream(kind, item_id, item["path"])
        user = self.current_user()
        if user:
            user_id = int(user["id"])
            remote = self.client_address[0] if self.client_address else ""
            viewer_key = f"{stream['key']}:{user_id}:{remote}"
            segment_match = re.search(r"(\d+)(?=\.[^.]+$)", filename)
            with HLS_VIEWER_LOCK:
                previous = HLS_VIEWERS.get(viewer_key, {})
                position = float(previous.get("position") or 0)
                if segment_match:
                    position = (int(segment_match.group(1)) + 1) * max(1.0, float(HLS_SEGMENT_SECONDS))
                HLS_VIEWERS[viewer_key] = {
                    "kind": kind,
                    "item_id": str(item_id),
                    "user_id": user_id,
                    "user": user["full_name"] or user["username"] or "Signed-in user",
                    "remote": remote,
                    "position": position,
                    "duration": float(item.get("duration") or (0 if kind == "tuner" else ffprobe_duration(item["path"])) or 0),
                    "updated_at": time.time(),
                }
        if kind != "tuner" and filename.endswith(".m3u8") and HLS_VIRTUAL_VOD:
            data = virtual_vod_playlist(item["path"])
            if data:
                try:
                    (stream["dir"] / ".last_access").write_text(str(time.time()), encoding="utf-8")
                except OSError:
                    pass
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
        target = (stream["dir"] / filename).resolve()
        if not str(target).startswith(str(stream["dir"]) + os.sep):
            self.send_error(403, "Refusing path outside stream cache")
            return
        timeout = HLS_START_TIMEOUT if filename.endswith(".m3u8") else HLS_SEGMENT_WAIT_TIMEOUT
        deadline = time.time() + timeout
        while not target.is_file() and time.time() < deadline:
            time.sleep(0.25)
        if not target.is_file():
            self.send_error(404, "Stream segment not ready")
            return
        try:
            (stream["dir"] / ".last_access").write_text(str(time.time()), encoding="utf-8")
        except OSError:
            pass
        data = target.read_bytes()
        mime = hls_mime_type(filename)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if filename.endswith(".m3u8") else "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if query.get("wall", [""])[0] == "1":
            if user:
                with DIRECT_STREAM_LOCK:
                    DIRECT_BANDWIDTH_SAMPLES.append((time.monotonic(), int(user["id"]), len(data)))

    def serve_asset(self, request_path: str):
        name = request_path.rsplit("/", 1)[-1]
        if "/" in name or "\\" in name or name.startswith("."):
            self.send_error(403, "Invalid asset filename")
            return
        target = (ASSET_DIR / name).resolve()
        if not str(target).startswith(str(ASSET_DIR) + os.sep):
            self.send_error(403, "Refusing path outside asset root")
            return
        if not target.is_file():
            self.send_error(404, "Asset missing")
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def serve_module_logo(self, request_path: str):
        name = request_path.rsplit("/", 1)[-1]
        if "/" in name or "\\" in name or name.startswith("."):
            self.send_error(403, "Invalid logo filename")
            return
        target = (MODULE_LOGO_DIR / name).resolve()
        if not str(target).startswith(str(MODULE_LOGO_DIR) + os.sep):
            self.send_error(403, "Refusing path outside logo root")
            return
        if not target.is_file():
            self.send_error(404, "Logo missing")
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def serve_media(self, item_id: str, disposition: str, head_only: bool = False):
        return movie_app.Handler.serve_media(self, item_id, disposition=disposition, head_only=head_only)

    def serve_file(self, path: Path, disposition: str = "attachment"):
        return tv_app.Handler.serve_file(self, path, disposition=disposition)

    def serve_download_package(self, path: Path):
        encoded_name = urllib.parse.quote(path.name)
        data_size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", mobile_package_mime(path))
        self.send_header("Content-Length", str(data_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)


def media_for_kind(kind: str, item_id: str) -> dict:
    if kind == "movie":
        item = movie_app.safe_item(item_id)
        return {
            "title": item.title,
            "path": item.path,
            "back": f"/movie/{item.id}",
        }
    if kind == "tv":
        episode = tv_app.safe_episode(item_id)
        return {
            "title": f"{episode.show} - {episode.title}",
            "path": episode.path,
            "back": "/tv",
        }
    raise FileNotFoundError("Unknown media kind")


def hls_mime_type(filename: str) -> str:
    if filename.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if filename.endswith(".ts"):
        return "video/mp2t"
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def browser_media_compatibility(path: Path) -> dict:
    """Return a conservative direct-play decision, cached by file identity."""
    try:
        stat = path.stat()
    except OSError:
        return {"requires_hls": True, "video_codec": "", "audio_codec": ""}
    key = str(path)
    cached = MEDIA_CODEC_CACHE.get(key)
    if cached and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return dict(cached[2])
    video_codec = ""
    audio_codec = ""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
            "-of", "json", str(path),
        ], capture_output=True, text=True, timeout=20, check=False)
        for stream in json.loads(result.stdout or "{}").get("streams", []):
            codec_type = str(stream.get("codec_type") or "")
            codec_name = str(stream.get("codec_name") or "").lower()
            if codec_type == "video" and not video_codec:
                video_codec = codec_name
            elif codec_type == "audio" and not audio_codec:
                audio_codec = codec_name
    except Exception:
        pass
    direct_video = {"h264", "vp8", "vp9", "av1"}
    direct_audio = {"aac", "mp3", "opus", "vorbis"}
    requires_hls = not video_codec or video_codec not in direct_video or (audio_codec and audio_codec not in direct_audio)
    value = {"requires_hls": requires_hls, "video_codec": video_codec, "audio_codec": audio_codec}
    MEDIA_CODEC_CACHE[key] = (stat.st_mtime, stat.st_size, value)
    return dict(value)


def ffprobe_duration(path: Path) -> float:
    key = str(path)
    cached = MEDIA_DURATION_CACHE.get(key)
    if cached:
        return cached
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=20)
        duration = float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        duration = 0.0
    if duration > 0:
        MEDIA_DURATION_CACHE[key] = duration
    return duration


def virtual_vod_playlist(path: Path) -> bytes:
    duration = ffprobe_duration(path)
    segment_seconds = max(1.0, float(HLS_SEGMENT_SECONDS))
    if duration <= 0:
        return b""
    segment_count = max(1, math.ceil(duration / segment_seconds))
    target_duration = math.ceil(segment_seconds)
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for index in range(segment_count):
        remaining = duration - (index * segment_seconds)
        extinf = min(segment_seconds, max(0.001, remaining))
        lines.append(f"#EXTINF:{extinf:.6f},")
        lines.append(f"seg_{index:05d}.ts")
    lines.append("#EXT-X-ENDLIST")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("jellyfin-ffmpeg") or shutil.which("ffmpeg") or "ffmpeg"


def can_use_vaapi() -> bool:
    device = Path("/dev/dri/renderD128")
    return device.exists() and os.access(device, os.R_OK | os.W_OK) and shutil.which(ffmpeg_bin()) is not None


def hls_segment_count(stream_dir: Path) -> int:
    return len(list(stream_dir.glob("seg_*.ts")))


def wait_for_hls_ready(stream_dir: Path, playlist: Path, process: subprocess.Popen) -> None:
    deadline = time.time() + max(1.0, HLS_START_TIMEOUT)
    target_segments = max(1, HLS_PREBUFFER_SEGMENTS)
    while time.time() < deadline:
        if playlist.is_file() and hls_segment_count(stream_dir) >= target_segments:
            return
        if process.poll() is not None:
            return
        time.sleep(0.25)


def hls_ffmpeg_command(path: Path, stream_dir: Path, playlist: Path) -> list[str]:
    segment_seconds = str(HLS_SEGMENT_SECONDS)
    common_output = [
        "-f",
        "hls",
        "-hls_time",
        segment_seconds,
        "-hls_list_size",
        "0",
        "-hls_playlist_type",
        "event",
        "-hls_flags",
        "independent_segments+program_date_time",
        "-hls_segment_filename",
        str(stream_dir / "seg_%05d.ts"),
        str(playlist),
    ]
    common_input = [
        ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-y",
    ]
    # Old AVI/Xvid files commonly use MPEG-4 ASP and packed B-frames. VAAPI
    # often fails before producing any HLS segments, so force software encode.
    software_only_exts = {".avi", ".divx", ".xvid"}
    use_vaapi = HLS_ENCODER in {"auto", "vaapi"} and can_use_vaapi() and path.suffix.lower() not in software_only_exts
    if use_vaapi:
        return common_input + [
            "-hwaccel",
            "vaapi",
            "-hwaccel_device",
            "/dev/dri/renderD128",
            "-hwaccel_output_format",
            "vaapi",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            "scale_vaapi=format=nv12",
            "-c:v",
            "h264_vaapi",
            "-b:v",
            HLS_VIDEO_BITRATE,
            "-maxrate",
            HLS_VIDEO_MAXRATE,
            "-bufsize",
            HLS_VIDEO_BUFSIZE,
            "-force_key_frames",
            f"expr:gte(t,n_forced*{segment_seconds})",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ac",
            "2",
        ] + common_output
    return common_input + [
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-maxrate",
        HLS_VIDEO_MAXRATE,
        "-bufsize",
        HLS_VIDEO_BUFSIZE,
        "-pix_fmt",
        "yuv420p",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{segment_seconds})",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ac",
        "2",
    ] + common_output


def stream_key(kind: str, item_id: str, path: Path) -> str:
    stat = path.stat()
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", item_id)
    return f"{kind}-{safe_id}-{int(stat.st_mtime)}-{stat.st_size}"


def ensure_hls_stream(kind: str, item_id: str, path: Path) -> dict:
    key = stream_key(kind, item_id, path)
    stream_dir = HLS_CACHE_DIR / key
    playlist = stream_dir / "index.m3u8"
    playlist_url = f"/hls/{kind}/{item_id}/index.m3u8"
    pid_file = stream_dir / "ffmpeg.pid"
    with TRANSCODE_LOCK:
        existing = TRANSCODES.get(key)
        if existing:
            process = existing.get("process")
            if playlist.is_file() or (process and process.poll() is None):
                return existing
        stream_dir.mkdir(parents=True, exist_ok=True)
        if pid_file.is_file():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(pid, 0)
                stream = {
                    "key": key,
                    "dir": stream_dir,
                    "playlist": playlist,
                    "playlist_url": playlist_url,
                    "process": None,
                    "started_at": time.time(),
                    "source": str(path),
                    "log": stream_dir / "ffmpeg.log",
                }
                TRANSCODES[key] = stream
                return stream
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)
        if playlist.is_file() and "EXT-X-ENDLIST" in playlist.read_text(encoding="utf-8", errors="ignore"):
            stream = {
                "key": key,
                "dir": stream_dir,
                "playlist": playlist,
                "playlist_url": playlist_url,
                "process": None,
                "started_at": time.time(),
                "source": str(path),
                "log": stream_dir / "ffmpeg.log",
            }
            TRANSCODES[key] = stream
            return stream
        if playlist.is_file() or any(stream_dir.glob("seg_*.ts")):
            shutil.rmtree(stream_dir, ignore_errors=True)
            stream_dir.mkdir(parents=True, exist_ok=True)
        log_path = stream_dir / "ffmpeg.log"
        command = hls_ffmpeg_command(path, stream_dir, playlist)
        log_handle = log_path.open("ab")
        try:
            log_handle.write(("COMMAND " + " ".join(command) + "\n").encode("utf-8", errors="ignore"))
            log_handle.flush()
            process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT, close_fds=True)
        finally:
            log_handle.close()
        stream = {
            "key": key,
            "dir": stream_dir,
            "playlist": playlist,
            "playlist_url": playlist_url,
            "process": process,
            "started_at": time.time(),
            "source": str(path),
            "log": log_path,
        }
        pid_file.write_text(str(process.pid), encoding="utf-8")
        TRANSCODES[key] = stream
        wait_for_hls_ready(stream_dir, playlist, process)
        return stream


def ensure_live_hls(item_id: str, stream_url: str) -> dict:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(item_id))
    key = f"tuner-{safe_id}"
    stream_dir = HLS_CACHE_DIR / key
    playlist = stream_dir / "index.m3u8"
    playlist_url = f"/hls/tuner/{item_id}/index.m3u8"
    pid_file = stream_dir / "ffmpeg.pid"
    with TRANSCODE_LOCK:
        existing = TRANSCODES.get(key)
        if existing and existing.get("process") and existing["process"].poll() is None:
            return existing
        if pid_file.is_file():
            try:
                old_pid = int(pid_file.read_text(encoding="utf-8").strip())
                os.kill(old_pid, 0)
                return {"key": key, "dir": stream_dir, "playlist": playlist, "playlist_url": playlist_url,
                        "process": None, "started_at": time.time(), "source": stream_url,
                        "log": stream_dir / "ffmpeg.log"}
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)
        shutil.rmtree(stream_dir, ignore_errors=True)
        stream_dir.mkdir(parents=True, exist_ok=True)
        log_path = stream_dir / "ffmpeg.log"
        command = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-y", "-fflags", "+genpts+discardcorrupt",
                   "-i", stream_url, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "23", "-maxrate", HLS_VIDEO_MAXRATE,
                   "-bufsize", HLS_VIDEO_BUFSIZE, "-pix_fmt", "yuv420p", "-g", "60",
                   "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-f", "hls",
                   "-hls_time", str(HLS_SEGMENT_SECONDS), "-hls_list_size", "12",
                   "-hls_flags", "delete_segments+independent_segments+append_list+program_date_time",
                   "-hls_delete_threshold", "6", "-hls_segment_filename", str(stream_dir / "seg_%05d.ts"),
                   str(playlist)]
        with log_path.open("ab") as log_handle:
            log_handle.write(("COMMAND " + " ".join(command) + "\n").encode("utf-8", errors="ignore"))
            log_handle.flush()
            process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT, close_fds=True)
        stream = {"key": key, "dir": stream_dir, "playlist": playlist, "playlist_url": playlist_url,
                  "process": process, "started_at": time.time(), "source": stream_url, "log": log_path}
        pid_file.write_text(str(process.pid), encoding="utf-8")
        TRANSCODES[key] = stream
        wait_for_hls_ready(stream_dir, playlist, process)
        return stream


def latest_mtime(path: Path) -> float:
    try:
        newest = path.stat().st_mtime
    except OSError:
        return 0.0
    try:
        for child in path.rglob("*"):
            try:
                newest = max(newest, child.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return newest


def last_hls_access(stream_dir: Path) -> float:
    marker = stream_dir / ".last_access"
    if marker.is_file():
        try:
            return float(marker.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pass
    return latest_mtime(stream_dir)


def stop_process(process: subprocess.Popen, timeout: float = 5.0) -> bool:
    if process.poll() is not None:
        return False
    try:
        process.terminate()
        process.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        process.kill()
        return True
    except OSError:
        return False


def cleanup_hls_cache_once() -> None:
    HLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    max_age_seconds = max(60.0, HLS_CACHE_MAX_AGE_HOURS * 3600.0)
    idle_seconds = max(60.0, HLS_IDLE_STOP_MINUTES * 60.0)
    cutoff = time.time() - max_age_seconds
    with TRANSCODE_LOCK:
        active_dirs = set()
        for key, stream in list(TRANSCODES.items()):
            process = stream.get("process")
            stream_dir = stream["dir"].resolve()
            if process and process.poll() is None:
                if time.time() - last_hls_access(stream_dir) > idle_seconds:
                    if stop_process(process):
                        print(f"HLS idle stop: {stream_dir}", flush=True)
                else:
                    active_dirs.add(stream_dir)
                    continue
            if not stream_dir.exists():
                TRANSCODES.pop(key, None)
            elif latest_mtime(stream_dir) < cutoff:
                TRANSCODES.pop(key, None)
    try:
        candidates = [path for path in HLS_CACHE_DIR.iterdir() if path.is_dir()]
    except OSError as exc:
        print(f"HLS cleanup skipped: {exc}", flush=True)
        return
    removed = 0
    for directory in candidates:
        resolved = directory.resolve()
        if resolved in active_dirs:
            continue
        if latest_mtime(resolved) >= cutoff:
            continue
        try:
            shutil.rmtree(resolved)
            removed += 1
        except OSError as exc:
            print(f"HLS cleanup could not remove {resolved}: {exc}", flush=True)
    if removed:
        print(f"HLS cleanup removed {removed} stale stream cache folder(s)", flush=True)


def start_hls_cleanup_thread() -> None:
    interval = max(60.0, HLS_CLEANUP_INTERVAL_MINUTES * 60.0)

    def worker() -> None:
        while True:
            try:
                cleanup_hls_cache_once()
                cleanup_mobile_download_cache_once()
            except Exception as exc:
                print(f"HLS/mobile cleanup error: {exc}", flush=True)
            time.sleep(interval)

    threading.Thread(target=worker, daemon=True, name="hls-cache-cleanup").start()


def main():
    parser = argparse.ArgumentParser(description="Combined Movies and TV media library")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8093)
    args = parser.parse_args()

    try:
        movie_loaded = movie_app.movie_index.load_csv_bootstrap()
    except Exception as exc:
        print(f"Movie cache load failed, rebuilding: {exc}", flush=True)
        movie_loaded = False
        movie_app.movie_index.items = []
    if not movie_app.movie_index.items:
        movie_app.movie_index.refresh()
    try:
        tv_loaded = tv_app.tv_index.load_cache()
    except Exception as exc:
        print(f"TV cache load failed, rebuilding: {exc}", flush=True)
        tv_loaded = False
        tv_app.tv_index.shows = []
        tv_app.tv_index.episode_by_id = {}
    if not tv_loaded:
        tv_app.tv_index.refresh()
    movie_app.load_poster_map()
    movie_app.load_metadata_map()
    tv_app.load_poster_map()
    tv_app.load_metadata_map()
    migrated_movie_states = migrate_legacy_movie_state_keys()
    actor_stats = rebuild_actor_index()
    movie_app.movie_index.refresh_background()
    tv_app.tv_index.refresh_background()
    start_hls_cleanup_thread()
    start_hdhr_guide_thread()

    print(f"Loaded {len(movie_app.movie_index.items)} movies", flush=True)
    print(f"Loaded {len(tv_app.tv_index.shows)} shows and {len(tv_app.tv_index.episode_by_id)} episodes", flush=True)
    print(f"Indexed {actor_stats['actors']} actors across {actor_stats['links']} library links", flush=True)
    print(f"Migrated {migrated_movie_states} legacy movie state key(s) to stable identities", flush=True)
    print(
        f"HLS cache cleanup: {HLS_CACHE_DIR} older than {HLS_CACHE_MAX_AGE_HOURS:g} hour(s), every {HLS_CLEANUP_INTERVAL_MINUTES:g} minute(s)",
        flush=True,
    )
    tls_cert = os.environ.get("CINEVAULT_TLS_CERT", "")
    tls_key = os.environ.get("CINEVAULT_TLS_KEY", "")
    scheme = "https" if tls_cert and tls_key else "http"
    print(f"Serving combined media library on {scheme}://{args.host}:{args.port}/", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), CombinedHandler)
    if tls_cert and tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
