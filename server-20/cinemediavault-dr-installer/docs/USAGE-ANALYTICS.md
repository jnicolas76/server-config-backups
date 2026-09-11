# Bandwidth and CPU usage analytics

CineMediaVault measures real bandwidth and CPU/memory usage for playback,
downloads, Live TV/DVR, and music, attributes it to the authenticated user
and session that generated it, and shows it under **Admin → Active &
Historical Usage** (`/admin/usage`). A compact live version of the same
numbers appears on the Video Wall.

Every number that can be measured directly (bytes actually written to a
client socket) is measured directly — nothing is inferred from a source
file's bitrate. The one number that cannot always be attributed exactly
(per-user CPU) is either tied to a real transcoder process or left blank;
it is never fabricated. See "Exact vs. estimated" below.

## Where things live

- `payload/app/cinevault_usage.py` — the only place usage tracking logic
  lives: the SQLite schema, the in-memory session table, the CPU/memory
  sampler, interval-aggregate flushing, retention pruning, and the
  `active_snapshot()` / `historical_query()` functions the admin APIs call.
  Copied verbatim into `payload/app/media-download-library/` and
  `payload/app/tv-download-library/` — the same convention as
  `cinevault_theme.py` (see [ACCOUNT-THEMES.md](ACCOUNT-THEMES.md)) — so the
  movie/TV library modules can `import cinevault_usage` whether invoked as
  unbound methods borrowed by the combined handler or standalone.
- `payload/app/cinemediavault.py` — calls `cinevault_usage.configure()` /
  `ensure_schema()` at startup, starts the sampler thread
  (`cinevault_usage.start_background_thread()`), wraps Direct-play, HLS,
  download, and Live TV/DVR/tuner response writes with `session_start()` /
  `record_bytes()` / `session_touch()` / `session_end()`, serves the
  `/admin/usage` page and its four `/api/admin/usage/*` JSON endpoints, and
  serves the compact `/api/video-wall/bandwidth` summary the Wall polls.
- `payload/app/music_module.py`,
  `payload/app/media-download-library/media_download_server.py`,
  `payload/app/tv-download-library/tv_download_server.py` — each wraps its
  own file-serving path (`serve_file`, movie direct/download, TV
  direct/download/HLS) the same way.
- SQLite tables `usage_system_interval`, `usage_breakdown_interval`,
  `usage_settings` — created idempotently by
  `cinevault_usage.ensure_schema()` (`CREATE TABLE IF NOT EXISTS` /
  `INSERT OR IGNORE`), called every startup exactly like the rest of this
  codebase's schema migrations. No separate migration file or tool.

## How bytes get measured

Two calling conventions cover every place CineVault writes response bytes:

- **Held-open responses** (Direct play, downloads, music streaming, mobile
  packages, season zips): the handler calls `session_start()` once, then
  either loops manually calling `record_bytes(len(chunk))` after each
  `wfile.write(chunk)`, or wraps the writer in `cinevault_usage.CountingWriter`
  when the existing code uses a bulk writer (`zipfile`, `shutil.copyfileobj`)
  that can't easily be restructured into a chunk loop. `session_end()` runs
  in a `finally` block so a dropped connection still closes the session.
- **Poll-based responses** (HLS: one HTTP request per segment/playlist
  fetch): `session_touch()` upserts the session's `updated_at` on every
  request rather than holding a connection open, and `record_bytes()` is
  called once per segment/playlist response with the real byte count of
  that response. A background thread prunes any poll-based session that
  goes quiet for `CINEVAULT_USAGE_SESSION_IDLE_SECONDS` (default 20s) —
  long enough to survive normal segment-fetch spacing, short enough that a
  closed player disappears from the Active view within a few ticks.

Both paths record `len(chunk)` — the number of bytes actually handed to the
socket for that response — never a source-file size or a computed bitrate.
Range requests, retries, and preload requests are all just separate calls to
`record_bytes()` with their own actual byte counts; nothing double-counts
because nothing multiplies a duration by an assumed rate.

## Session identity and double-counting

Each open connection or poll sequence gets its own `session_key` (per
Direct-play connection, per download, per HLS client sequence, per Video
Wall tile). `record_bytes()` always updates the global per
`(user, service, delivery)` counter *and*, when given a `session_key`, that
session's own running total. A stale/duplicate HLS segment request (a
retry, or a client re-requesting a playlist) is simply another
`record_bytes()` call for the bytes that response actually contained — it
adds real bytes for a real response, not a phantom duplicate of a prior
one. Two Video Wall tiles playing the same title are two independent
sessions with two independent `session_key`s, so they are counted, and
attributed, separately.

## The sampler: live numbers and interval aggregates

A single daemon thread (`_sampler_loop`, started once via
`start_background_thread()`) ticks every `CINEVAULT_USAGE_TICK_SECONDS`
(default 2s):

1. Prunes stale poll-based sessions.
2. Samples system CPU (`psutil.cpu_percent`) and CineVault's own CPU/memory
   by walking the CineVault process tree (`psutil.Process(os.getpid())` plus
   all recursive children — this is what makes ffmpeg transcode children
   count toward "CineVault CPU" and not just the Python process itself).
3. For every session with a known worker/transcoder PID (attached via
   `session_set_pid()` when the caller starts an ffmpeg process for that
   session), samples that PID's own CPU and attaches it to the session as
   `cpu_estimated_pct`, flagging `cpu_shared=true` if more than one session
   shares the same PID (rare, but possible if a future code path reuses a
   worker across requests).
4. Computes each active session's instantaneous bytes-per-second from the
   delta since the previous tick.
5. Rolls the tick into the current 5-minute bucket's running totals (system:
   average CPU/mem, total bytes, peak instantaneous bps, max concurrent
   streams; breakdown: same, split by `(user_id, service, delivery)`).
6. Once a bucket's time window has fully elapsed, flushes it to SQLite with
   an `INSERT ... ON CONFLICT DO UPDATE` upsert (idempotent — a crash and
   restart mid-bucket just re-accumulates and re-upserts the same row) and
   drops it from the in-memory accumulator.
7. Once an hour, prunes rows older than the configured retention window.

This is the only thread that touches SQLite for usage data, and it does a
handful of small upserts every 5 minutes plus one delete sweep per hour —
not a write per byte or per request. Playback, transcoding, Whisper, DVR
recording, and EPG collection do not wait on or share a lock with this
thread beyond the in-memory `_LOCK` guarding the tracking dictionaries,
which every `record_bytes()` call already needed to hold to be safe under
concurrent playback anyway.

## Exact vs. estimated

- **Bandwidth/bytes transferred**: always exact. Every figure in the Active
  and Historical views (per-session, per-user, per-service, system totals)
  is a sum of real `record_bytes()` calls, each backed by an actual
  `len(chunk)` written to a real socket.
- **System CPU**: exact, from `psutil.cpu_percent()` — the whole host,
  including anything not related to CineVault (other services, backups,
  the OS itself).
- **CineVault CPU**: exact for the process tree that exists at sample time
  — `psutil` per-process CPU percentages, summed across the main process
  and every recursive child (transcodes, thumbnail generation, etc.),
  normalized by CPU count and capped at 100%.
- **Per-session CPU**: only ever shown when that session has a known
  transcoder PID attached. It is that PID's real `psutil`-measured CPU,
  not a guess or a share of the total — but it is labeled "est." in the UI
  and flagged `cpu_shared` when more than one session's PID happens to
  coincide, because attributing one process's CPU number to potentially
  more than one logical session is an approximation CineVault is explicit
  about rather than hiding. Sessions with no known PID (e.g. a plain Direct
  file copy with no transcoder) show no per-session CPU figure at all
  rather than a fabricated one.
- **Memory**: exact (RSS summed across the CineVault process tree via
  `psutil`), shown as a convenience alongside CPU, not a tracked historical
  dimension with its own chart beyond the interval average CineVault
  memory already stores per bucket.

## Historical storage and retention

`usage_system_interval` and `usage_breakdown_interval` store one row per
5-minute bucket (system-wide, and per `user × service × delivery`
respectively) — not a row per byte or per request. At typical home-lab
activity this is roughly 1-5 MB of SQLite data per 90 days (288 system rows
per day plus a handful of breakdown rows per day per distinct
user/service/delivery combination actually used, each row a few dozen
bytes). Retention defaults to 90 days
(`CINEVAULT_USAGE_RETENTION_DAYS`), is adjustable from the Historical tab
(30 / 90 / 180 days), and is enforced by an hourly prune sweep that deletes
rows with `bucket_start` older than the configured cutoff — it does not
grow unbounded, and does not require an operator cron job.

## Admin UI

- **Active tab**: total bandwidth, CineVault CPU, system CPU, memory
  (used/total), one card per active session (user, title, media type,
  delivery mode, live bandwidth, bytes transferred so far, duration,
  transcoding/direct badge, per-session CPU when known), and a per-user
  bandwidth-and-stream-count summary. Polls `/api/admin/usage/active` every
  3 seconds, and pauses polling when the tab is hidden
  (`visibilitychange`) so a backgrounded browser tab doesn't keep polling.
- **Historical tab**: preset ranges (15m / 1h / 6h / 24h / 7d / 30d) plus a
  custom start/end picker, optional user/service/delivery filters, and four
  dependency-free inline SVG charts (bandwidth by service over time, CPU
  over time, concurrent streams over time, per-user ranking) built by a
  small shared `lineChart()` helper with hover tooltips — no chart library,
  no CDN, no external font or script; everything needed to render the page
  ships in the page itself, the same policy the rest of CineVault's UI
  follows.
- Units: bandwidth is always shown as Kbps/Mbps/Gbps (bits, matching how
  ISPs and network tools report it); transferred totals are shown as
  KB/MB/GB (bytes). Timestamps use the browser's local timezone via
  `toLocaleString()` — no server-side timezone configuration needed.
- Empty states: "No active playback right now." / "No active usage right
  now." on the Active tab when nothing is playing, and a "No data for this
  range" message drawn directly into each chart's SVG when a historical
  query returns no rows, rather than an empty or broken-looking chart.

## APIs

All under `/api/admin/usage/` and `/admin/usage*`, all admin-only (see
"Security" below):

| Route | Method | Purpose |
| --- | --- | --- |
| `/admin/usage` | GET | The Active & Historical Usage page |
| `/api/admin/usage/active` | GET | Live snapshot: system/CineVault CPU, memory, total bandwidth, active sessions, per-user totals |
| `/api/admin/usage/history` | GET | Historical query. Params: `range` (`15m`\|`1h`\|`6h`\|`24h`\|`7d`\|`30d`\|`custom`), `start`/`end` (epoch seconds, `custom` only), `user_id`, `service`, `delivery` |
| `/api/admin/usage/users` | GET | User list for the history filter dropdown (id + display name only) |
| `/admin/usage/retention` | POST | Set retention (30/90/180 days), form-encoded, same auth pattern as the rest of the admin panel |
| `/api/video-wall/bandwidth` | GET | Compact live summary for the Video Wall (total Mbps, bytes/sec, CineVault CPU, per-stream breakdown) — available to any signed-in user, not admin-only, since the Wall itself isn't admin-only |

## Server-side validation and bounds

`api_admin_usage_history` validates the `range` value against a fixed
preset set (or `custom`), rejects `custom` ranges where `end <= start` or
where the span exceeds 400 days, and clamps a future `end` to "now". A bad
`range` value or an invalid custom span returns HTTP 400 with a JSON error
body rather than a stack trace. `historical_query()` additionally validates
`service`/`delivery` against fixed allow-lists and re-derives the bucket
resolution from the requested span: it groups buckets together (never
finer than the underlying 5-minute storage resolution) so that the number
of points returned for any range — including a 30-day or custom multi-year
range — is capped at `MAX_POINTS` (720 rows), which bounds both the SQLite
aggregation work and the JSON payload size regardless of how wide a range
an admin requests. `user_id` is parsed as a plain integer and simply
produces zero rows if it doesn't match anything; it is never interpolated
into SQL (every query uses parameter binding).

## Security

- Every usage route and API checks `user["is_admin"]` after CineVault's
  existing `current_user()` session-cookie check, using the same
  `if not user["is_admin"]: return self.send_error(403)` pattern as
  `/admin/activity`, `/admin/hls`, and `/admin/users` — no new
  authentication or authorization mechanism was introduced.
- Session cards intentionally omit IP addresses, session tokens, and raw
  cookies; the "client" field is the browser/app User-Agent string,
  truncated to 80 characters, which is the same class of information
  already visible in server logs and is not a secret.
- No token, password, or cookie value is ever written into a usage log
  line, a session record, or a SQLite row.

## Video Wall integration

The Wall's existing compact bandwidth line (toggle button, top-right) now
shows total Mbps, bytes/sec, active stream count, and CineVault CPU when
the panel is open, backed by `/api/video-wall/bandwidth` polled once per
second only while the panel is expanded (not while collapsed, so it costs
nothing when unused). A "Per-stream breakdown" toggle inside that same
panel reveals a compact per-slot rate list rather than adding text to every
tile. The header carries a link to `/admin/usage` for admins ("Full Usage
Details") so there is a one-click path from the compact Wall view to the
full historical picture. None of this touches the Wall's full-screen
behavior, saved tile positions, or theme handling.

## Testing

- `tests/test_idempotency.py::GeneratedFilePermissions::test_the_usage_analytics_feature_survives_patching`
  — asserts a clean sandboxed install still has the admin route table, the
  page heading, the background-thread startup call, and all three copies
  of `cinevault_usage.py` with its schema and `record_bytes()` intact, and
  that the movie/TV/music modules still call into it, after the installer's
  default-administrator patch step runs (the same shape of test the
  account-theme feature has — see [ACCOUNT-THEMES.md](ACCOUNT-THEMES.md)).
- `tests/run-tests.sh` also re-verifies (via the existing static checks)
  that every shipped Python file, including `cinevault_usage.py` and its
  two copies, still compiles.
- Live functional verification (admin/non-admin access split, range
  validation, and a real measured Direct-play byte count matching a real
  Range request) was run against the live host during development; see
  `CLAUDE_CINEVAULT_USAGE_ANALYTICS_RESULT_2026-09-03.md` for the exact
  commands and results.
