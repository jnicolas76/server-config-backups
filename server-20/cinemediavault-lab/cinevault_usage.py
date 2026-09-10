"""cinevault_usage.py -- bandwidth/CPU usage tracking and SQLite aggregation.

Shared by the main CineVault wrapper, music_module, and the movie/TV
compatibility modules (movie_app / tv_app), which each keep their own copy of
this file the same way they keep their own copy of cinevault_theme.py.

Design summary (see CLAUDE_CINEVAULT_USAGE_ANALYTICS_RESULT for full detail):
  - Every place that writes response bytes to a client calls record_bytes()
    with the actual chunk length actually written -- bandwidth is measured,
    never inferred from source bitrate.
  - Held-open responses (direct play, downloads, music, mobile packages,
    season zips) call session_start()/session_end() around their write loop.
  - Poll-based responses (one HTTP request per HLS segment/playlist) call
    session_touch() on every request; a background thread prunes sessions
    that go quiet for USAGE_SESSION_IDLE_SECONDS.
  - A single daemon thread (start_background_thread) samples system +
    CineVault-process-tree CPU/memory every LIVE_TICK_SECONDS, updates
    per-session instantaneous bandwidth, and rolls everything into
    USAGE_BUCKET_SECONDS interval aggregates that get flushed to SQLite.
  - Per-user CPU is never fabricated: a session only gets a cpu_estimated_pct
    when it has a known ffmpeg/transcoder PID attached via set_session_pid();
    that PID's real psutil-measured CPU is attached to every session sharing
    it and flagged cpu_shared when more than one session shares the PID.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import deque

import psutil

LIVE_TICK_SECONDS = float(os.environ.get("CINEVAULT_USAGE_TICK_SECONDS", "2.0"))
USAGE_BUCKET_SECONDS = int(os.environ.get("CINEVAULT_USAGE_BUCKET_SECONDS", "300"))
USAGE_SESSION_IDLE_SECONDS = float(os.environ.get("CINEVAULT_USAGE_SESSION_IDLE_SECONDS", "20.0"))
USAGE_RETENTION_DAYS_DEFAULT = int(os.environ.get("CINEVAULT_USAGE_RETENTION_DAYS", "90"))
USAGE_PRUNE_INTERVAL_SECONDS = 3600.0
LIVE_SAMPLE_HISTORY = 150  # ~5 minutes at the default 2s tick

# service labels: movie|tv|music|dvr|live_tv
# delivery labels: direct|hls|download

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_system_interval (
  id INTEGER PRIMARY KEY,
  bucket_start INTEGER NOT NULL,
  interval_seconds INTEGER NOT NULL,
  cpu_system_pct_avg REAL NOT NULL DEFAULT 0,
  cpu_cinevault_pct_avg REAL NOT NULL DEFAULT 0,
  mem_used_mb_avg REAL,
  mem_total_mb REAL,
  total_bytes INTEGER NOT NULL DEFAULT 0,
  peak_bps REAL NOT NULL DEFAULT 0,
  concurrent_streams_max INTEGER NOT NULL DEFAULT 0,
  sample_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(bucket_start, interval_seconds)
);
CREATE INDEX IF NOT EXISTS idx_usage_sys_bucket ON usage_system_interval(bucket_start);

CREATE TABLE IF NOT EXISTS usage_breakdown_interval (
  id INTEGER PRIMARY KEY,
  bucket_start INTEGER NOT NULL,
  interval_seconds INTEGER NOT NULL,
  user_id INTEGER,
  service TEXT NOT NULL,
  delivery TEXT NOT NULL,
  bytes_total INTEGER NOT NULL DEFAULT 0,
  peak_bps REAL NOT NULL DEFAULT 0,
  concurrent_streams_max INTEGER NOT NULL DEFAULT 0,
  cpu_estimated_pct_avg REAL,
  sample_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(bucket_start, interval_seconds, user_id, service, delivery)
);
CREATE INDEX IF NOT EXISTS idx_usage_bd_bucket ON usage_breakdown_interval(bucket_start);
CREATE INDEX IF NOT EXISTS idx_usage_bd_user ON usage_breakdown_interval(user_id, bucket_start);

CREATE TABLE IF NOT EXISTS usage_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  retention_days INTEGER NOT NULL DEFAULT 90,
  updated_at TEXT NOT NULL
);
"""

_LOCK = threading.RLock()
_GET_CONN = None  # set via configure(); returns a sqlite3.Connection like the app's db_connect()

_SESSIONS: dict[str, dict] = {}
_COUNTERS: dict[tuple, int] = {}          # (user_id, service, delivery) -> cumulative bytes
_COUNTER_LAST: dict[tuple, int] = {}      # snapshot of _COUNTERS at the previous tick
_LIVE_SAMPLES: deque = deque(maxlen=LIVE_SAMPLE_HISTORY)  # (ts, cpu_sys, cpu_cv, mem_used_mb, mem_total_mb, total_bps, concurrent)

_BUCKET_SYS_ACC: dict[int, dict] = {}
_BUCKET_BD_ACC: dict[tuple, dict] = {}

_PROC_CACHE: dict[int, "psutil.Process"] = {}
_CPU_COUNT = max(1, psutil.cpu_count(logical=True) or 1)

_THREAD_STARTED = False
_LAST_PRUNE = 0.0
_RETENTION_DAYS_CACHE = None


def configure(get_conn) -> None:
    global _GET_CONN
    _GET_CONN = get_conn


def ensure_schema() -> None:
    conn = _GET_CONN()
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO usage_settings(id, retention_days, updated_at) VALUES(1, ?, ?)",
            (USAGE_RETENTION_DAYS_DEFAULT, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        conn.commit()
    finally:
        conn.close()


def get_retention_days() -> int:
    global _RETENTION_DAYS_CACHE
    if _RETENTION_DAYS_CACHE is not None:
        return _RETENTION_DAYS_CACHE
    conn = _GET_CONN()
    try:
        row = conn.execute("SELECT retention_days FROM usage_settings WHERE id=1").fetchone()
        _RETENTION_DAYS_CACHE = int(row["retention_days"]) if row else USAGE_RETENTION_DAYS_DEFAULT
    finally:
        conn.close()
    return _RETENTION_DAYS_CACHE


def set_retention_days(days: int) -> int:
    global _RETENTION_DAYS_CACHE
    days = max(1, min(3650, int(days)))
    conn = _GET_CONN()
    try:
        conn.execute(
            "UPDATE usage_settings SET retention_days=?, updated_at=? WHERE id=1",
            (days, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        conn.commit()
    finally:
        conn.close()
    _RETENTION_DAYS_CACHE = days
    return days


def _user_id_of(user) -> int | None:
    if not user:
        return None
    try:
        return int(user["id"])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _user_label_of(user) -> str:
    if not user:
        return "Unknown"
    try:
        return user["full_name"] or user["username"] or "Unknown"
    except (KeyError, IndexError):
        return "Unknown"


class CountingWriter:
    """Wraps a file-like object so bulk writers (zipfile, shutil.copyfileobj) can be
    metered without restructuring them into a manual chunk loop."""

    def __init__(self, inner, on_bytes):
        self._inner = inner
        self._on_bytes = on_bytes

    def write(self, data):
        result = self._inner.write(data)
        self._on_bytes(len(data))
        return result

    def flush(self):
        return self._inner.flush()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def record_bytes(user, service: str, delivery: str, nbytes: int, session_key: str | None = None) -> None:
    """Record actually-written response bytes. Call this with len(chunk), never an estimate."""
    if nbytes <= 0:
        return
    user_id = _user_id_of(user)
    key = (user_id, service, delivery)
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0) + nbytes
        if session_key:
            session = _SESSIONS.get(session_key)
            if session is not None:
                session["bytes_total"] += nbytes
                session["updated_at"] = time.time()


def session_start(session_key: str, *, user, service: str, delivery: str, media_type: str = "",
                   media_id="", title: str = "", subtitle: str = "", transcoding: bool = False,
                   client: str = "", kind: str = "hold") -> None:
    """Register a session. kind='hold' for a long-lived connection (caller must session_end()),
    kind='poll' for one-shot requests kept alive by repeated session_touch() calls (HLS)."""
    now = time.time()
    with _LOCK:
        _SESSIONS[session_key] = {
            "user_id": _user_id_of(user),
            "user": _user_label_of(user),
            "service": service,
            "delivery": delivery,
            "media_type": media_type,
            "media_id": media_id,
            "title": title,
            "subtitle": subtitle,
            "transcoding": transcoding,
            "client": (client or "")[:80],
            "kind": kind,
            "started_at": now,
            "updated_at": now,
            "bytes_total": 0,
            "bps": 0.0,
            "pid": None,
            "cpu_estimated_pct": None,
            "cpu_shared": False,
            "_last_bytes": 0,
            "_last_tick": now,
        }


def session_touch(session_key: str, *, user, service: str, delivery: str, media_type: str = "",
                   media_id="", title: str = "", subtitle: str = "", transcoding: bool = False,
                   client: str = "", pid: int | None = None) -> None:
    """Upsert a poll-based session (HLS segment/playlist fetches)."""
    with _LOCK:
        session = _SESSIONS.get(session_key)
        if session is None:
            session_start(session_key, user=user, service=service, delivery=delivery, media_type=media_type,
                          media_id=media_id, title=title, subtitle=subtitle, transcoding=transcoding,
                          client=client, kind="poll")
            session = _SESSIONS[session_key]
        else:
            session["updated_at"] = time.time()
            if title:
                session["title"] = title
            if subtitle:
                session["subtitle"] = subtitle
        if pid:
            session["pid"] = pid


def session_set_pid(session_key: str, pid: int | None) -> None:
    with _LOCK:
        session = _SESSIONS.get(session_key)
        if session is not None and pid:
            session["pid"] = pid


def session_end(session_key: str) -> None:
    with _LOCK:
        _SESSIONS.pop(session_key, None)


def _tracked_processes() -> list["psutil.Process"]:
    root_pid = os.getpid()
    root = _PROC_CACHE.get(root_pid)
    if root is None:
        root = psutil.Process(root_pid)
        root.cpu_percent(None)
        _PROC_CACHE[root_pid] = root
    live_pids = {root_pid}
    procs = [root]
    try:
        children = root.children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        live_pids.add(child.pid)
        cached = _PROC_CACHE.get(child.pid)
        if cached is None:
            try:
                cached = psutil.Process(child.pid)
                cached.cpu_percent(None)
            except psutil.Error:
                continue
            _PROC_CACHE[child.pid] = cached
        procs.append(cached)
    for pid in list(_PROC_CACHE):
        if pid not in live_pids:
            _PROC_CACHE.pop(pid, None)
    return procs


def _sample_cpu_mem() -> tuple[float, float, float, float]:
    system_pct = psutil.cpu_percent(None)
    cinevault_raw = 0.0
    mem_mb = 0.0
    for proc in _tracked_processes():
        try:
            cinevault_raw += proc.cpu_percent(None)
            mem_mb += proc.memory_info().rss / 1_000_000.0
        except psutil.Error:
            continue
    cinevault_pct = min(100.0, cinevault_raw / _CPU_COUNT)
    try:
        vmem = psutil.virtual_memory()
        mem_total_mb = vmem.total / 1_000_000.0
    except psutil.Error:
        mem_total_mb = 0.0
    return system_pct, cinevault_pct, mem_mb, mem_total_mb


def _bucket_floor(ts: float) -> int:
    return int(ts // USAGE_BUCKET_SECONDS) * USAGE_BUCKET_SECONDS


def _tick() -> None:
    now = time.time()
    now_m = time.monotonic()
    with _LOCK:
        stale = [
            key for key, session in _SESSIONS.items()
            if session["kind"] == "poll" and now - session["updated_at"] > USAGE_SESSION_IDLE_SECONDS
        ]
        for key in stale:
            _SESSIONS.pop(key, None)

        system_pct, cinevault_pct, mem_used_mb, mem_total_mb = _sample_cpu_mem()

        pid_cpu: dict[int, float] = {}
        pid_sessions: dict[int, list[str]] = {}
        for key, session in _SESSIONS.items():
            pid = session.get("pid")
            if pid:
                pid_sessions.setdefault(pid, []).append(key)
        for pid in pid_sessions:
            proc = _PROC_CACHE.get(pid)
            if proc is None:
                try:
                    proc = psutil.Process(pid)
                    proc.cpu_percent(None)
                    _PROC_CACHE[pid] = proc
                except psutil.Error:
                    continue
            try:
                pid_cpu[pid] = min(100.0, proc.cpu_percent(None) / _CPU_COUNT)
            except psutil.Error:
                pid_cpu[pid] = 0.0
        for pid, keys in pid_sessions.items():
            shared = len(keys) > 1
            for key in keys:
                session = _SESSIONS.get(key)
                if session is not None:
                    session["cpu_estimated_pct"] = pid_cpu.get(pid)
                    session["cpu_shared"] = shared

        total_delta = 0
        for key, cumulative in _COUNTERS.items():
            last = _COUNTER_LAST.get(key, 0)
            delta = max(0, cumulative - last)
            _COUNTER_LAST[key] = cumulative
            total_delta += delta
            if delta <= 0:
                continue
            bucket_start = _bucket_floor(now)
            bd_key = (bucket_start, *key)
            acc = _BUCKET_BD_ACC.setdefault(bd_key, {"bytes": 0, "peak_bps": 0.0, "concurrent_max": 0, "samples": 0})
            acc["bytes"] += delta
            acc["peak_bps"] = max(acc["peak_bps"], delta / LIVE_TICK_SECONDS)
            acc["samples"] += 1
            user_id, service, delivery = key
            concurrent = sum(
                1 for s in _SESSIONS.values() if s["user_id"] == user_id and s["service"] == service and s["delivery"] == delivery
            )
            acc["concurrent_max"] = max(acc["concurrent_max"], concurrent)

        total_bps = total_delta / LIVE_TICK_SECONDS

        for key, session in _SESSIONS.items():
            delta_bytes = session["bytes_total"] - session["_last_bytes"]
            dt = max(0.001, now - session["_last_tick"])
            session["bps"] = max(0.0, delta_bytes / dt)
            session["_last_bytes"] = session["bytes_total"]
            session["_last_tick"] = now

        bucket_start = _bucket_floor(now)
        sys_acc = _BUCKET_SYS_ACC.setdefault(bucket_start, {
            "cpu_sys_sum": 0.0, "cpu_cv_sum": 0.0, "mem_used_sum": 0.0, "mem_total": mem_total_mb,
            "bytes": 0, "peak_bps": 0.0, "concurrent_max": 0, "samples": 0,
        })
        sys_acc["cpu_sys_sum"] += system_pct
        sys_acc["cpu_cv_sum"] += cinevault_pct
        sys_acc["mem_used_sum"] += mem_used_mb
        sys_acc["mem_total"] = mem_total_mb
        sys_acc["bytes"] += total_delta
        sys_acc["peak_bps"] = max(sys_acc["peak_bps"], total_bps)
        sys_acc["concurrent_max"] = max(sys_acc["concurrent_max"], len(_SESSIONS))
        sys_acc["samples"] += 1

        _LIVE_SAMPLES.append((now, system_pct, cinevault_pct, mem_used_mb, mem_total_mb, total_bps, len(_SESSIONS)))

    _maybe_flush(now)
    _maybe_prune(now)


def _maybe_flush(now: float) -> None:
    current_bucket = _bucket_floor(now)
    completed_sys = {b: acc for b, acc in _BUCKET_SYS_ACC.items() if b < current_bucket}
    completed_bd = {k: acc for k, acc in _BUCKET_BD_ACC.items() if k[0] < current_bucket}
    if not completed_sys and not completed_bd:
        return
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn = _GET_CONN()
    try:
        for bucket_start, acc in completed_sys.items():
            n = max(1, acc["samples"])
            conn.execute(
                """INSERT INTO usage_system_interval
                   (bucket_start, interval_seconds, cpu_system_pct_avg, cpu_cinevault_pct_avg,
                    mem_used_mb_avg, mem_total_mb, total_bytes, peak_bps, concurrent_streams_max,
                    sample_count, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(bucket_start, interval_seconds) DO UPDATE SET
                     cpu_system_pct_avg=excluded.cpu_system_pct_avg,
                     cpu_cinevault_pct_avg=excluded.cpu_cinevault_pct_avg,
                     mem_used_mb_avg=excluded.mem_used_mb_avg,
                     mem_total_mb=excluded.mem_total_mb,
                     total_bytes=excluded.total_bytes,
                     peak_bps=excluded.peak_bps,
                     concurrent_streams_max=excluded.concurrent_streams_max,
                     sample_count=excluded.sample_count""",
                (bucket_start, USAGE_BUCKET_SECONDS, acc["cpu_sys_sum"] / n, acc["cpu_cv_sum"] / n,
                 acc["mem_used_sum"] / n, acc["mem_total"], acc["bytes"], acc["peak_bps"],
                 acc["concurrent_max"], acc["samples"], created_at),
            )
            _BUCKET_SYS_ACC.pop(bucket_start, None)
        for bd_key, acc in completed_bd.items():
            bucket_start, user_id, service, delivery = bd_key
            conn.execute(
                """INSERT INTO usage_breakdown_interval
                   (bucket_start, interval_seconds, user_id, service, delivery, bytes_total,
                    peak_bps, concurrent_streams_max, cpu_estimated_pct_avg, sample_count, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(bucket_start, interval_seconds, user_id, service, delivery) DO UPDATE SET
                     bytes_total=excluded.bytes_total,
                     peak_bps=excluded.peak_bps,
                     concurrent_streams_max=excluded.concurrent_streams_max,
                     sample_count=excluded.sample_count""",
                (bucket_start, USAGE_BUCKET_SECONDS, user_id, service, delivery, acc["bytes"],
                 acc["peak_bps"], acc["concurrent_max"], None, acc["samples"], created_at),
            )
            _BUCKET_BD_ACC.pop(bd_key, None)
        conn.commit()
    finally:
        conn.close()


def _maybe_prune(now: float) -> None:
    global _LAST_PRUNE
    if now - _LAST_PRUNE < USAGE_PRUNE_INTERVAL_SECONDS:
        return
    _LAST_PRUNE = now
    cutoff = int(now) - get_retention_days() * 86400
    conn = _GET_CONN()
    try:
        conn.execute("DELETE FROM usage_system_interval WHERE bucket_start < ?", (cutoff,))
        conn.execute("DELETE FROM usage_breakdown_interval WHERE bucket_start < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()


def _sampler_loop() -> None:
    while True:
        try:
            _tick()
        except Exception:
            pass
        time.sleep(LIVE_TICK_SECONDS)


def start_background_thread() -> None:
    global _THREAD_STARTED
    if _THREAD_STARTED:
        return
    _THREAD_STARTED = True
    threading.Thread(target=_sampler_loop, daemon=True, name="cinevault-usage-sampler").start()


def active_snapshot() -> dict:
    with _LOCK:
        latest = _LIVE_SAMPLES[-1] if _LIVE_SAMPLES else None
        sessions = []
        per_user: dict[int | None, dict] = {}
        for key, session in _SESSIONS.items():
            row = {
                "id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:12],
                "user": session["user"],
                "user_id": session["user_id"],
                "service": session["service"],
                "delivery": session["delivery"],
                "media_type": session["media_type"],
                "media_id": session["media_id"],
                "title": session["title"],
                "subtitle": session["subtitle"],
                "transcoding": session["transcoding"],
                "client": session["client"],
                "started_at": session["started_at"],
                "duration_seconds": max(0.0, time.time() - session["started_at"]),
                "bytes_total": session["bytes_total"],
                "bps": session["bps"],
                "cpu_estimated_pct": session["cpu_estimated_pct"],
                "cpu_shared": session["cpu_shared"],
            }
            sessions.append(row)
            bucket = per_user.setdefault(session["user_id"], {"user": session["user"], "user_id": session["user_id"], "bytes_total": 0, "bps": 0.0, "streams": 0})
            bucket["bytes_total"] += session["bytes_total"]
            bucket["bps"] += session["bps"]
            bucket["streams"] += 1
        sessions.sort(key=lambda r: r["bps"], reverse=True)
        per_user_totals = sorted(per_user.values(), key=lambda r: r["bps"], reverse=True)
        return {
            "ok": True,
            "cpu_system_pct": latest[1] if latest else 0.0,
            "cpu_cinevault_pct": latest[2] if latest else 0.0,
            "mem_used_mb": latest[3] if latest else 0.0,
            "mem_total_mb": latest[4] if latest else 0.0,
            "total_bps": latest[5] if latest else 0.0,
            "concurrent_streams": latest[6] if latest else len(_SESSIONS),
            "sessions": sessions,
            "per_user_totals": per_user_totals,
            "sampled_at": latest[0] if latest else time.time(),
        }


VALID_SERVICES = {"movie", "tv", "music", "dvr", "live_tv"}
VALID_DELIVERIES = {"direct", "hls", "download"}
MAX_RANGE_SECONDS = 400 * 86400  # generous outer bound; UI only offers up to 30 days + custom
MAX_POINTS = 720


def historical_query(start_ts: float, end_ts: float, user_id: int | None = None,
                      service: str | None = None, delivery: str | None = None) -> dict:
    start_ts = float(start_ts)
    end_ts = float(end_ts)
    if end_ts <= start_ts:
        raise ValueError("end must be after start")
    if end_ts - start_ts > MAX_RANGE_SECONDS:
        start_ts = end_ts - MAX_RANGE_SECONDS
    if service is not None and service not in VALID_SERVICES:
        raise ValueError("invalid service")
    if delivery is not None and delivery not in VALID_DELIVERIES:
        raise ValueError("invalid delivery")

    span = end_ts - start_ts
    raw_buckets = max(1, int(span // USAGE_BUCKET_SECONDS))
    group_size = max(1, -(-raw_buckets // MAX_POINTS))  # ceil division, bound output rows
    resolution_seconds = USAGE_BUCKET_SECONDS * group_size

    conn = _GET_CONN()
    try:
        sys_rows = conn.execute(
            """SELECT (bucket_start / ?) * ? AS grp, AVG(cpu_system_pct_avg) AS cpu_system_pct_avg,
                      AVG(cpu_cinevault_pct_avg) AS cpu_cinevault_pct_avg, SUM(total_bytes) AS total_bytes,
                      MAX(peak_bps) AS peak_bps, MAX(concurrent_streams_max) AS concurrent_streams_max,
                      AVG(mem_used_mb_avg) AS mem_used_mb_avg, MAX(mem_total_mb) AS mem_total_mb
               FROM usage_system_interval
               WHERE bucket_start >= ? AND bucket_start < ?
               GROUP BY grp ORDER BY grp""",
            (resolution_seconds, resolution_seconds, int(start_ts), int(end_ts)),
        ).fetchall()

        bd_where = "WHERE bucket_start >= ? AND bucket_start < ?"
        bd_params: list = [int(start_ts), int(end_ts)]
        if user_id is not None:
            bd_where += " AND user_id = ?"
            bd_params.append(user_id)
        if service is not None:
            bd_where += " AND service = ?"
            bd_params.append(service)
        if delivery is not None:
            bd_where += " AND delivery = ?"
            bd_params.append(delivery)

        series_rows = conn.execute(
            f"""SELECT (bucket_start / {resolution_seconds}) * {resolution_seconds} AS grp,
                       service, delivery, SUM(bytes_total) AS bytes_total, MAX(peak_bps) AS peak_bps
               FROM usage_breakdown_interval
               {bd_where}
               GROUP BY grp, service, delivery ORDER BY grp""",
            bd_params,
        ).fetchall()

        ranking_rows = conn.execute(
            f"""SELECT user_id, SUM(bytes_total) AS bytes_total, MAX(peak_bps) AS peak_bps
               FROM usage_breakdown_interval
               {bd_where}
               GROUP BY user_id ORDER BY bytes_total DESC LIMIT 50""",
            bd_params,
        ).fetchall()

        user_rows = conn.execute("SELECT id, username, full_name FROM users").fetchall()
        user_names = {row["id"]: (row["full_name"] or row["username"]) for row in user_rows}
    finally:
        conn.close()

    total_bytes = sum(row["total_bytes"] or 0 for row in sys_rows)
    peak_bps = max((row["peak_bps"] or 0 for row in sys_rows), default=0.0)
    avg_bps = (total_bytes / span) if span > 0 else 0.0

    return {
        "ok": True,
        "start": start_ts,
        "end": end_ts,
        "resolution_seconds": resolution_seconds,
        "system_series": [
            {
                "bucket_start": row["grp"],
                "cpu_system_pct": row["cpu_system_pct_avg"] or 0.0,
                "cpu_cinevault_pct": row["cpu_cinevault_pct_avg"] or 0.0,
                "total_bytes": row["total_bytes"] or 0,
                "peak_bps": row["peak_bps"] or 0.0,
                "concurrent_streams_max": row["concurrent_streams_max"] or 0,
                "mem_used_mb": row["mem_used_mb_avg"] or 0.0,
                "mem_total_mb": row["mem_total_mb"] or 0.0,
            }
            for row in sys_rows
        ],
        "breakdown_series": [
            {
                "bucket_start": row["grp"],
                "service": row["service"],
                "delivery": row["delivery"],
                "bytes_total": row["bytes_total"] or 0,
                "peak_bps": row["peak_bps"] or 0.0,
            }
            for row in series_rows
        ],
        "user_ranking": [
            {
                "user_id": row["user_id"],
                "user": user_names.get(row["user_id"], "Deleted user" if row["user_id"] else "Unattributed"),
                "bytes_total": row["bytes_total"] or 0,
                "peak_bps": row["peak_bps"] or 0.0,
            }
            for row in ranking_rows
        ],
        "totals": {
            "total_bytes": total_bytes,
            "peak_bps": peak_bps,
            "avg_bps": avg_bps,
        },
    }
