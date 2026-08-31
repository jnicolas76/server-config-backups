# CineVault Video Wall — "Full Screen Wall" feature

**Source inspected (read-only):** `jnicolas@192.168.1.20:/home/jnicolas/cinemediavault-lab/cinemediavault-lab-5000.py`
No changes were made to the live server. This is a design/handoff doc only.

## Current state of the Wall page

- `VIDEO_WALL_PAGE` (a raw Python triple-quoted string, ~line 4173–4211) is server-rendered HTML/CSS/JS for `/wall`, a single `BaseHTTPRequestHandler` app — no frontend framework, no build step, no bundler.
- The 2×2 grid lives in `<div class="wall" id="wall">` (line ~4184); `render()` (line ~4191) does `wall.innerHTML = ''` and rebuilds all 4 `<section class="tile">` elements **every time slots change**. This matters for placement of any persistent UI (see below).
- **Per-tile fullscreen already exists** (line ~4195): each tile's `data-fullscreen` button calls `tile.requestFullscreen()` / `webkitRequestFullscreen()`, with a `.tile.expanded` CSS fallback (`position:fixed;inset:0`) for browsers without the Fullscreen API. This is the pattern to mirror for a wall-level control, not invent a new one.
- The main video player elsewhere in the file (line ~2304–2326, `fullscreenElement()` / `requestPlayerFullscreen()` / `exitPlayerFullscreen()`) is another existing, proven helper-function pattern for the same API — reuse its shape.
- **No `manifest.json`, no service worker, no `apple-mobile-web-app-*` / standalone meta tags anywhere in the file.** "Add to Home Screen" today produces a plain bookmark shortcut that opens in normal Chrome (address bar visible), not an installed standalone PWA.
- No WebView bridge (`window.Android`, `addJavascriptInterface`, etc.) is referenced — nothing in this file talks to a native APK layer.

## Standards evaluation

### 1. Fullscreen API on Android Chrome (the relevant case here)
- `element.requestFullscreen()` on any element (not just `<video>`) is well supported in Chrome for Android and hides Chrome's own **address/tab bar**. This is exactly what the existing per-tile button already does successfully.
- Must be called synchronously from a real user gesture (click/tap handler) — a `button.onclick` satisfies this; calling it from a `setTimeout`, promise callback, etc. will silently fail.
- It does **not** reliably hide the Android **status bar** — that needs `display: fullscreen` in a Web App Manifest (installed PWA) or native immersive-mode APIs. For "hide the browser chrome," element-level Fullscreen API is sufficient and is the standard, minimal mechanism.
- Fullscreening does not pause/reset media — all 4 `<video>` elements keep playing through the transition, satisfying "keeps all four wall videos visible and playing."
- **Exit is handled by the platform, not app code**: Android system Back (button or gesture) exits the Fullscreen API element first, before it would navigate/close the page — this is standard Chrome behavior, no `popstate`/back-button interception needed. Desktop/keyboard `Escape` is intercepted by the browser itself to exit fullscreen; you cannot (and don't need to) handle `Escape` in JS. Both cases fire a `fullscreenchange` event, which is the one thing app code must listen for, to resync UI state (e.g. re-show the header).

### 2. PWA / installed web app behavior
- Without a manifest, "Add to Home Screen" just bookmarks the URL; Chrome UI (address bar) is present. Fullscreen API still works identically inside that shortcut.
- If a `manifest.json` with `"display": "standalone"` (or `"minimal-ui"`) were added and the site were installed, the address bar would be gone **persistently, with no user gesture required** — a stronger UX than per-session Fullscreen API. `"display": "fullscreen"` would also hide the status bar. This is a legitimate follow-up but is **out of scope for a minimal change**: it requires a manifest file, icons, an install prompt/flow, and (for the strongest guarantee) a service worker — none of which currently exist in this app. Recommend as a separate, later enhancement, not bundled into this one.

### 3. Android WebView APK (CineVault APK)
- A native WebView has no address bar by construction, so the "hide browser chrome" problem is largely moot inside the APK — unless the APK itself renders a native title/action bar or status bar the user also wants hidden. That is a native Android (Kotlin/Java) change (`WindowInsetsController`, hiding the `ActionBar`, etc.) — **cannot be done from this Python/JS file**, and the APK's source was not provided, so it's unverified.
- **Important caveat, do not skip:** the HTML5 Fullscreen API (`element.requestFullscreen()`) only works inside an Android `WebView` if the host app's `WebChromeClient` implements `onShowCustomView()` / `onHideCustomView()`. If the CineVault APK's WebView doesn't wire this up, `requestFullscreen()` calls will silently no-op (the promise rejects or nothing happens) — same as the existing per-tile fullscreen button would already be silently failing today inside the APK if this isn't implemented. This should be verified by testing the *existing* per-tile fullscreen button inside the APK before investing more UI around the same primitive. If it already works there, the new wall-level button will too, for free.

## Recommendation

Add one header button, **"Full Screen Wall"**, that calls `requestFullscreen()` on the `#wall` grid container (the same element/pattern already used per-tile), plus a small in-grid "exit" affordance and a `fullscreenchange` listener to resync UI. No manifest, no service worker, no native/APK changes — this is the safest minimal change that works today on Android Chrome and (if the WebView is wired for it) inside the APK unchanged.

Rely on the platform for exit-by-Back and exit-by-Escape (standard, automatic) rather than writing custom key/back handlers — do not intercept `popstate` or `keydown Escape` to "fake" this; the browser already does it, and fighting it (e.g. `preventDefault` on Escape) is not reliably possible and would be fragile/non-standard.

### Why fullscreen the `#wall` container (not `document.documentElement`, not each tile)
- Fullscreening `#wall` keeps the 2×2 grid and all 4 tiles' existing controls (play/pause/seek/remove/per-tile-fullscreen) intact and simply removes browser chrome + the page header — matching "keeps all four wall videos visible and playing."
- Fullscreening `documentElement` would work too but pulls in the `<aside class="drawer">` and bandwidth panel as siblings that need separate hiding logic; scoping to `#wall` is simpler and consistent with the existing per-tile precedent in this same file.

## Exact code changes (proposed, not applied)

All edits are inside the `VIDEO_WALL_PAGE` string in `cinemediavault-lab-5000.py` (~lines 4173–4211). Shown expanded for readability; match the file's existing minified single-line style when actually applying.

### 1. Structural HTML change — give the grid a stable child so re-renders don't wipe the exit button

`render()` currently does `wall.innerHTML=''` then `wall.appendChild(el)` for each tile — any exit button placed directly inside `#wall` would be destroyed on the next slot-change re-render (it fires on load and after every add/remove). Fix by adding an inner `#tiles` container that `render()` targets instead, leaving `#wall` itself untouched across renders.

**Before** (line ~4184):
```html
<main><div class="wall" id="wall"></div></main>
```

**After:**
```html
<main><div class="wall" id="wall"><div class="tiles" id="tiles"></div><button class="icon" id="exitWallFs" title="Exit full screen">&#10005;</button></main>
```

### 2. CSS — move grid layout to `#tiles`, add fullscreen-state styling

**Before** (in the big minified rule, line ~4180):
```css
.wall{height:calc(100vh - 92px);min-height:520px;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:10px}
```

**After:**
```css
.wall{height:calc(100vh - 92px);min-height:520px;position:relative;background:#000}
.tiles{height:100%;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:10px}
.wall.wall-fs-active{height:100vh}
.wall.expanded{position:fixed;inset:0;z-index:40;height:100vh}
#exitWallFs{display:none;position:absolute;top:10px;right:10px;z-index:41;width:40px;height:40px;border-radius:50%;background:rgba(0,0,0,.65)}
.wall.wall-fs-active #exitWallFs,.wall.expanded #exitWallFs{display:flex;align-items:center;justify-content:center}
```
(`.wall.expanded` mirrors the existing `.tile.expanded` fallback for browsers without the Fullscreen API.)

### 3. Header button

**Before** (line ~4183):
```html
<button id="playAll" class="primary">Play all</button><button id="pauseAll">Pause all</button><button id="syncAll">Sync</button>
```

**After:**
```html
<button id="playAll" class="primary">Play all</button><button id="pauseAll">Pause all</button><button id="syncAll">Sync</button><button id="fullWall">Full Screen Wall</button>
```

### 4. JS — target `#tiles` in `render()`, add the fullscreen control + listener

**Before** (line ~4187, ~4191):
```js
const wall=document.getElementById('wall'),drawer=...
...
function render(){hlsControllers.forEach(h=>h.destroy());hlsControllers=[];wall.innerHTML='';for(let n=1;n<=4;n++){...}wall.appendChild(el)}bindVideos();applyAudio();updateBandwidth()}
```

**After:**
```js
const wall=document.getElementById('wall'),tiles=document.getElementById('tiles'),drawer=...
...
function render(){hlsControllers.forEach(h=>h.destroy());hlsControllers=[];tiles.innerHTML='';for(let n=1;n<=4;n++){...}tiles.appendChild(el)}bindVideos();applyAudio();updateBandwidth()}

function fsElement(){return document.fullscreenElement||document.webkitFullscreenElement||null}
function requestWallFullscreen(){const req=wall.requestFullscreen||wall.webkitRequestFullscreen;if(req)req.call(wall).catch(()=>wall.classList.add('expanded'));else wall.classList.add('expanded')}
function exitWallFullscreen(){if(document.exitFullscreen)document.exitFullscreen().catch(()=>{});else if(document.webkitExitFullscreen)document.webkitExitFullscreen();wall.classList.remove('expanded')}
function syncWallFsUi(){wall.classList.toggle('wall-fs-active',fsElement()===wall)}
document.getElementById('fullWall').onclick=()=>{fsElement()===wall||wall.classList.contains('expanded')?exitWallFullscreen():requestWallFullscreen()};
document.getElementById('exitWallFs').onclick=exitWallFullscreen;
document.addEventListener('fullscreenchange',syncWallFsUi);
document.addEventListener('webkitfullscreenchange',syncWallFsUi);
```

That's the whole change: 1 new button, 1 new small exit button, ~10 lines of JS, a handful of CSS rules, and one `wall.innerHTML` → `tiles.innerHTML` retarget. No new dependencies, no new routes/endpoints, no manifest, no service worker.

## Limitations to flag to the user

1. **User-gesture requirement** — the button must stay a direct `onclick`; don't wrap it in an async pre-step (e.g. a confirmation fetch) before calling `requestFullscreen()`, or Chrome will reject it.
2. **Status bar stays visible** — plain Fullscreen API hides Chrome's address bar but not necessarily the Android status bar/clock. If a fully immersive look is wanted later, that needs a Web App Manifest (`display: fullscreen`) + install, or native immersive mode in the APK — separate follow-up work.
3. **APK behavior is unverified** — depends on whether the WebView's `WebChromeClient` implements `onShowCustomView`/`onHideCustomView`. Test the *existing* per-tile fullscreen button inside the APK first; if it already works there, this new control will too, unmodified. If it doesn't, no JS-only change (this one included) can fix it — that requires editing the native APK.
4. **iOS is out of scope** — Safari on iOS doesn't support `Element.requestFullscreen()` for arbitrary elements (only native `<video>` fullscreen), so this feature would silently fall back to the `.expanded` CSS class there. Not a concern per the stated Android Chrome / CineVault APK target, but worth knowing if iOS users ever show up.
5. Because `#wall`'s children remain visible while it's the fullscreen element, the per-tile `data-fullscreen` buttons still work *inside* wall-level fullscreen (nested fullscreen requests just retarget the fullscreen element to that tile) — no conflict, but worth a quick manual check that switching between "whole wall" and "single tile" fullscreen back and forth feels right.

## Suggested test plan (manual, on the actual device before merging)

1. Open `/wall` in Android Chrome with all 4 slots filled and playing.
2. Tap "Full Screen Wall" → confirm address bar disappears, all 4 tiles keep playing, exit (✕) button appears top-right.
3. Tap the ✕ → confirm return to normal page with header/address bar restored, playback uninterrupted.
4. Re-enter fullscreen, then press Android system Back → confirm it exits fullscreen only (does not navigate away from `/wall`).
5. Re-enter fullscreen, add/remove a wall slot via the drawer → confirm the grid re-renders correctly and the ✕ button survives (this is the specific bug the `#tiles` restructuring avoids).
6. Repeat steps 2–4 inside the CineVault APK if available; note whether fullscreen engages at all (validates/invalidates the `WebChromeClient` caveat above).
