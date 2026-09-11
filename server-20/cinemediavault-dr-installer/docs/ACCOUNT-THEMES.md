# Account color themes

CineMediaVault ships three color themes: the original gold/amber look
(`default`), `royal-blue`, and `green`. The theme is a per-account
preference stored in SQLite, not a per-device setting — it follows a user
between Android browsers, iPhone/iPad Safari, desktop browsers, the
installed APK/PWA, and any new browser session on any device.

## Where things live

- `payload/app/cinevault_theme.py` — the only place theme logic lives.
  Three functions: `normalize_theme()` (strict allow-list validation),
  `theme_for_row()` (resolve a user's theme from a `sqlite3.Row`, defaulting
  safely), and `inject_theme()` (stamp a fully-rendered HTML page with
  `data-theme` and the override stylesheet link). Copied verbatim into
  `payload/app/media-download-library/` and `payload/app/tv-download-library/`
  so the movie/TV library modules can `import cinevault_theme` even when
  invoked as unbound methods borrowed by the combined handler, or run
  standalone.
- `payload/assets/cinevault-theme.css` — the only file that knows the
  three themes' actual color values. Served as a normal cacheable static
  asset (`/assets/cinevault-theme.css`), not inlined into every response.
- `users.theme` column (SQLite, `cinemediavault-lab.db` / the production
  equivalent) — authoritative. `default` for every existing account until
  they change it. Local storage / cookies are never authoritative for this
  value; nothing in this feature depends on client-side storage at all,
  because the theme is resolved server-side before the first byte of HTML
  is written.
- `/account` (GET) and `/account/theme` (POST) on the combined handler —
  self-service only. The POST handler always writes to
  `self.current_user()`'s own row; there is no `user_id` field a client can
  set, so there is no way for one account to change another's preference
  short of an admin with direct database access.

## How a page picks up the right theme with no flash

Every full HTML page in this codebase is a large Python string template
with its own inline `<style>` block defining `:root { --gold: #f5b73f; ... }`
(and, on the movie/TV grid and detail pages, `--accent` / `--accent2`).
Rather than templating three copies of every page, `inject_theme()` adds one
small thing to the final rendered HTML before it is sent:

```html
<html data-theme="royal-blue">
<head><link rel="stylesheet" href="/assets/cinevault-theme.css"> ...
```

`cinevault-theme.css` contains only two rule blocks:

```css
html[data-theme="royal-blue"] { --gold: #4d90fe; --accent: #4d90fe; --accent2: #4d90fe; }
html[data-theme="green"]      { --gold: #1f9d55; --accent: #1f9d55; --accent2: #1f9d55; }
```

`html[data-theme="..."]` has higher CSS specificity (0,1,1) than a page's own
`:root { ... }` (0,1,0), so these two rules win regardless of where the
`<link>` lands in `<head>` relative to the page's inline `<style>` block. The
`default` theme adds no attribute and no override rule at all — every page's
original gold/amber styling is completely untouched in that case, byte for
byte, which is how the original appearance is preserved exactly.

Because the `data-theme` attribute and the correct-colored token values are
present in the very first HTML the server writes, there is no client-side
theme swap and therefore no flash of the default theme while a stylesheet or
script loads.

Theme resolution happens once per request, in `dispatch()` / `do_POST()`,
right after the session cookie is resolved to a user row, and is cached on
`self._cinevault_theme` for the rest of that request (including calls into
`music_module`, `dvr_module`, `cinevault_video_lists`, and the borrowed
`movie_app.Handler.*` / `tv_app.Handler.*` methods, all of which receive the
same handler instance as `self`/`handler`). Anonymous requests — `/login`,
`/logout`, static assets — are never resolved past the `default` theme.

## Adding a themed page

1. If the page's `<style>` block defines its accent color as a CSS custom
   property (`--gold`, `--accent`, or `--accent2`) rather than a scattered
   literal hex, it is already themeable — no page-specific change needed.
2. If an element hardcodes the literal accent hex (`#f5b73f` or one of its
   near-duplicates introduced over time — `#f5a524`, `#f5b400`, `#f7b733`,
   `#f2b542`) instead of `var(--gold)`, switch it to the variable. Check
   first whether it is actually a brand mark (a specific module's logo
   gradient, for example) rather than the CineVault accent — those are left
   alone deliberately; see "What was deliberately left alone" below.
3. Confirm the page's HTML is ultimately sent through `render_html()`, or
   has its own `body = inject_theme(body, getattr(self, "_cinevault_theme",
   DEFAULT_THEME))` immediately before it gets encoded and written. Every
   existing full-page response already does one or the other.

## What was deliberately left alone

- **Semantic colors** — the fixed status green used for "live"/"recording
  completed" indicators (`--green`, generally `#2ee66b`-`#36dc79` depending
  on the page), error red (`--red` / `--danger`), and the unauthenticated
  landing page's own `--accent`/`--blue` scheme — are not touched by any
  theme. In the `green` theme specifically, the account-accent green
  (`#1f9d55`, a deeper/richer shade) was picked to stay visually distinct
  from the brighter fixed status green, so "the accent is green" and "this
  recording completed successfully" never read as the same signal.
- **Per-module brand marks** — the small logo badges for Comics, NES, SEGA,
  DOS, and MAME on the home page and Modules admin page keep their own fixed
  colors (including the Comics badge's red/gold CSS gradient) across all
  three themes. They are third-party/emulator brand identities, not
  CineVault's own accent.
- **Poster art, backdrops, and other media-derived imagery** are never
  affected by theme selection.
- **BookVault, Comics reader, and the NES/SEGA/DOS/MAME emulator front
  ends** run as separate standalone processes on their own ports (see
  `docs/MODULE-INVENTORY.md`) with their own codebases and, in some cases,
  their own auth. Only CineVault's own launcher tiles for these modules are
  themed; their internal UIs are out of scope for this feature.

## Testing

`tests/test_idempotency.py::GeneratedFilePermissions
.test_the_account_theme_feature_survives_patching` asserts that a full
simulated install still contains the migration, the allow-list, the
`/account` and `/account/theme` routes, `cinevault_theme.py`, and
`cinevault-theme.css` — i.e. that the installer's own admin-bootstrap
patching step (`installer/steps/s050_payload.py`) does not silently strip
the feature. Manual verification (login, change theme, open a new session,
confirm the new session already has the chosen theme, confirm a second
account is unaffected) was performed against the live instance; see the
operator's working notes for the exact steps and screenshots.
