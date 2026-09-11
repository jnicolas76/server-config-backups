# Genre discovery and virtual channels

Added to the live application on 2026-09-08 and reconciled into this package
on 2026-09-10 (the 2026-09-02 package predates both features entirely - if
you have that package, upgrade). Both are core, always-on parts of the
application, installed unconditionally like Movies or TV. There is no
installer switch for either.

## Genre discovery

`genre_catalog.py` adds genre tile grids and genre poster grids for both
Movies and TV:

- `GET /genres/movies`, `GET /genres/tv` - tile grid (name + live count).
- `GET /genres/movies/<slug>`, `GET /genres/tv/<slug>` - poster grid of local
  titles in that genre, linking to the ordinary `/movie/<id>` /
  `/tv/show/<id>` detail and playback pages.
- `GET /api/genres/movies`, `/api/genres/tv` and the `<slug>` variants - JSON,
  for Tizen/programmatic use.

Genres are read live from the already-loaded movie/TV catalogue metadata
(TMDb genre strings cached in `movie-metadata-map.json` / `tv-metadata-map.json`)
- no database table, no TMDb call per browse. `canonical_genre()` merges
near-duplicate TMDb vocabularies (`"Action & Adventure"` -> `Action`,
`"Kids"` -> `Family`, etc.) for browsing only; the original stored genre
strings are never modified. A title with no genre is excluded from every
tile and page rather than guessed at.

Ordinary text search (`/search`) is untouched.

## Virtual channels

`virtual_channels.py` adds ten clock-driven virtual movie channels and ten
virtual TV channels, each with a 14-day, cable-style guide (real local time,
program start/end, artwork, a Now marker, day/time navigation).

**Movie channels (M1-M10):** Action, Romance, Comedy, Science Fiction,
Horror, Thriller, Drama, Film Noir, Kids & Family, International. Film Noir
and International are documented, non-fabricating fallbacks (Film Noir =
`Crime` and one of `Thriller`/`Mystery`/`Drama`; International = the file's
own embedded audio-language tag via `ffprobe`, never TMDb) rather than
invented genres.

**TV channels (T1-T10, plus a `T12` single-show slot):** the ten largest real genre buckets found in the
library's own TV metadata at build time (Drama, Comedy, Science Fiction,
Action & Adventure, Crime, Mystery, Animation, Family, Documentary, Western,
as tallied against the live host's library on 2026-09-08). A different
library will tally differently; the channel set is generated from what is
actually present, never hard-coded to someone else's catalogue.

### Scheduling

- Schedules are generated 14 days ahead, deterministically (seeded on
  channel/date/slot), and **persisted** in `vchannel_schedule` - they do not
  reshuffle on page refresh or service restart.
- A movie does not repeat more than once on the same calendar day across the
  ten movie channels. TV episodes for a given show always progress forward in
  season/episode order (`vchannel_show_progress`); a weekly slot continues
  with the next episode the following week, never backward.
- Higher-rated titles are preferred during 5:00 PM-12:00 AM `America/Denver`.
- Real media duration is used (a durable `ffprobe` cache, `vchannel_media_probe`,
  replacing an in-memory-only cache that did not survive a restart), never an
  assumed fixed block length.
- Generation/extension runs on a bounded background thread
  (`cinevault-vchannel-scheduler`, started once from `main()`), not per page
  request. `GET /api/vchannels/status` reports the last/next build. An admin
  action (`POST /admin/vchannels`, `action=repair_all`) validates and extends
  both schedules without re-randomizing them; `payload/scripts/repair_virtual_schedules.py`
  drives the same action from a cron job or the command line.

### The `T12` channel and its logo

The live application also runs one additional, single-show TV channel slot
(`T12`, `"The Simpsons"`) beyond the ten genre-based TV channels the original
brief specified - it schedules episodes of that one show from the operator's
own library, the same way every other channel schedules from the library. Its
on-screen logo tile used a static image that turned out, on inspection, to be
Fox/Disney's actual trademarked wordmark rather than original artwork; that
one file is **not** bundled in this package (see
`docs/THIRD-PARTY-NOTICES.md`). The channel still works - its logo tile is
simply blank until you place your own image at
`payload/assets/channel-logo-simpsons-clean.png` before installing.

### Playback

Tuning in (`/watch/vchannel/<id>`) computes `now - program_start` and starts
the local file at that offset using the application's existing Direct/HLS
selection - nothing streams in the background before that. A separate
**Play from beginning** action opens the ordinary playback route at zero.
Captions reuse the existing subtitle discovery/markup pipeline unchanged.

**Known, already-fixed bug:** the live application's first cut of
`watch_channel()` read `channel['number']` where the row's actual key is
`channel['channel_number']`, so every "Watch Live" click on a program that
had actually started crashed the request handler outright (no HTTP response
at all, not even a 500 - a bare `http.server` process dies on an unhandled
exception mid-request). Confirmed from real client traffic in
`logs/cinemediavault-lab-5000.err` before the fix. This package ships the
corrected `virtual_channels.py` (synced from the live host on 2026-09-10,
after the fix); nothing further to do here.

### Tests

`test_virtual_channels.py` (in `payload/app/`, shipped alongside the module
it tests) covers scheduling invariants and seek-offset math, including the
regression tests added for the crash above.

### Reconciliation note (2026-09-10)

The 2026-09-02 installer package predates this feature entirely - it has
neither `virtual_channels.py` nor `genre_catalog.py`. This package's
`payload/app/cinemediavault.py`, `dvr_module.py`,
`media-download-library/media_download_server.py` and
`tv-download-library/tv_download_server.py` were re-synced from the live
host (2026-09-10) specifically to pick up the `import virtual_channels` /
`import genre_catalog` wiring, the `Virtual Channels` navigation links, and
the `user_tv_state` table. The default-administrator patch
(`tools/patch-payload.py`) was re-applied afterward and verified.
