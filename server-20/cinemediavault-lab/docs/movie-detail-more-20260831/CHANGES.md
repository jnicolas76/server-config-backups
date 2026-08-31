# CineVault movie-detail More-menu fix

## Root cause

`/player/movie/<id>` (canonical player, `cinemediavault-lab-5000.py`) and
`/movie/<id>` (normal detail page) are rendered by two different code paths.
The canonical player builds its actions with `action_link()` /
`more_menu_link()` (`cinemediavault-lab-5000.py:3581-3594`) and renders a
proper accessible bottom sheet (`#moreSheet`, `role="dialog"
aria-modal="true"`, focus-on-open, Escape/backdrop/close-button dismissal —
`cinemediavault-lab-5000.py:2018`, `:2508-2527`) containing Fix Match,
Cast & Crew, and (for admins) Admin.

`/movie/<id>` is served by the imported module
`media-download-library/media_download_server.py`, whose `movie_detail()`
method (was `media_download_server.py:987`) and `DETAIL_TEMPLATE` render an
older, independent layout: five always-visible top-level buttons (Mark
Watched, Download, Fix Match, More, Admin), where "More" only toggled the
inline Cast & Crew panel — there was no bottom sheet, and Fix Match/Admin
were never hidden behind "More". This is why the same action reached via
`/player/movie/3469` looked different from `/movie/2087` in the logs.

## Fix

Changed only `media-download-library/media_download_server.py`:

- **Route/handler**: `movie_detail()` — replaced the `admin_action` local
  (previously injected via `{{ADMIN_ACTION}}`) with a `more_menu` string
  containing Fix Match and Cast & Crew links, with Admin appended only when
  `current_user["is_admin"]` is true (mirrors the conditional admin
  more-menu entry at `cinemediavault-lab-5000.py:6728-6729`). Template
  placeholder `{{ADMIN_ACTION}}` → `{{MORE_MENU}}`.
- **Template (`DETAIL_TEMPLATE`)**:
  - `.action-row` now renders exactly three actions: Mark Watched, Download,
    More (grid changed from 5 to 3 columns in the base rule and both
    `@media` overrides).
  - Added a `#moreSheet` bottom sheet (identical markup/CSS/behavior to the
    canonical player's: `.more-sheet`/`.more-card`/`.more-head`/
    `.more-close`/`.more-menu` rules copied from
    `cinemediavault-lab-5000.py:1910-1921`), populated by `{{MORE_MENU}}`.
  - JS: `data-more-toggle` now opens the sheet (focus-managed, closes on the
    close button, backdrop click, or Escape) instead of directly toggling
    the cast panel; a new `data-cast-toggle` handler (used by the Cast &
    Crew entry inside the sheet) closes the sheet and opens the existing
    `#cast` panel, preserving the original cast-panel reveal behavior.

No changes were made to `cinemediavault-lab-5000.py` (already canonical) or
to any TV/music/wall/auth/subtitle/download/playback code. Play, Direct/HLS
mode links, Restart ("Start over"), lists (`video-lists`... note: this
detail page doesn't have queue/playlist controls, none were touched),
metadata, recommendations, playback-progress polling, mobile/download
handling, casting button, fix-match flow, and the admin-only authorization
check are all unchanged — only how "Fix Match", "Cast & Crew", and "Admin"
are exposed (grouped behind "More" instead of always visible inline).

## Why not redirect `/movie/<id>` to `/player/movie/<id>`

The player route is an immersive video-player page (autoplay-oriented hero
layout, `player-shell`, queue/playlist controls, etc.) and is a materially
different experience from the detail page (large poster, TMDb score row,
Direct/HLS static links, file-grid, recommendations rail). Redirecting would
have satisfied the "consistent More menu" requirement but violated the
explicit constraint to preserve "the normal detail-page experience" and its
metadata. Reusing the existing bottom-sheet markup/CSS/JS pattern in-place
keeps both routes visually/behaviorally consistent for the actions menu
without collapsing the two distinct pages into one.
