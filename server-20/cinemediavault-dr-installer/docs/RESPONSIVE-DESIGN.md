# Responsive design conventions

How CineMediaVault's server-rendered pages stay usable on Android Chrome,
iOS Safari (including installed/PWA mode), and desktop browsers from 1080p
through 4K. Every page in `payload/app/` is a Python string template with
inline CSS — there is no separate stylesheet or build step, so these rules
are enforced by convention and by the checks below, not by a linter.

---

## Baseline every page must have

```html
<meta name="viewport" content="width=device-width,initial-scale=1">
```

Every template already has this. Keep it on any new page.

## Breakpoint direction

Compacting rules belong in `@media (max-width: ...)`. A `min-width` query
should only ever make a layout **more spacious** (more grid columns, a
side-by-side layout, larger optional chrome) — never smaller text or smaller
controls.

This exact mistake shipped for a long time in the movie grid, TV grid, and
their compatibility copies: `@media (min-width: 900px)` wrapped a block meant
for the narrowest phones (9px header text, 22px buttons, a 72px poster grid,
6px titles). Because the condition was inverted, every desktop browser at
1080p/1440p/4K got the cramped phone layout instead of every phone getting it.
Fixed 2026-09-02 by changing the condition to `max-width: 359px`, which is
below the smallest breakpoint any real device hits, effectively retiring the
block without deleting it. **When adding a compacting rule, sanity-check the
keyword (`min` vs `max`) against the values inside it** — extreme values under
a `min-width` query are the tell.

## Touch targets

Interactive elements (`a`, `button`, `input[type=button]`, checkboxes'
wrapping `<label>`) should resolve to at least 44×44 CSS px on touch
viewports. Concretely:

- Give buttons and pill/chip links `min-height:44px` (not 38px, not 34px —
  those numbers show up repeatedly in older admin pages and are all one
  size too small).
- A `@media (hover:none) and (pointer:coarse)` block that shrinks a control
  below its non-touch size is a bug, not an optimization — coarse pointers
  need the *larger* target, not the smaller one. Mobile-narrow breakpoints
  should grow tap targets, never shrink them, even when they also shrink
  font size or padding for density.
- Decorative or inline-text links (a wordmark, a middot-separated admin nav)
  are the one accepted exception per WCAG 2.5.8's inline-text carve-out.
- Native `<input type=checkbox>` stays small; make sure it is wrapped in a
  `<label>` with `display:flex` so the whole label — not just the box — is
  clickable.

## Viewport height: avoid bare `100vh`

Mobile Safari and Chrome resize the visual viewport as the address bar/tab
bar collapse and expand. A bare `100vh` is measured against the *largest*
state, so fixed-height players, walls, and full-screen overlays can be taller
than what's actually visible, clipping controls behind the browser chrome.

Pattern used throughout the codebase — declare the `vh` value first as a
fallback, then repeat the property with `dvh` (or `svh` for content that must
never be allowed to grow, like a bottom sheet's max-height):

```css
.player-shell { min-height:100vh; min-height:100dvh; }
video { max-height:calc(100vh - 86px); max-height:calc(100dvh - 86px); }
```

The second declaration silently wins in browsers that support `dvh`/`svh`
(iOS 16+, current Chrome/Android) and is ignored — leaving the safe `vh`
value — everywhere else. Never replace the `vh` line; always add the
dynamic-unit line after it.

## Safe-area insets (notch, home indicator, PWA/standalone)

Anything pinned to a screen edge — sticky headers, fixed players, bottom
sheets, floating close/exit buttons — adds the inset on top of its normal
padding, not instead of it:

```css
header { padding:18px 22px; padding-top:calc(18px + env(safe-area-inset-top));
  padding-left:calc(22px + env(safe-area-inset-left));
  padding-right:calc(22px + env(safe-area-inset-right)); }
```

`env(safe-area-inset-*)` resolves to `0` on devices without a notch/home
indicator, so this is safe to add unconditionally. It matters most in
installed/PWA mode on iOS, where Safari's own chrome is gone and the app is
fully responsible for clearing the notch and home-indicator strip.

## Modal / sheet sizing

Any `.modal`, `.dialog`, `.sheet`, or `.more-card` that can contain a variable
amount of content (a form, a list of matches, an admin dialog) needs both:

```css
.dialog { max-height:calc(100vh - 36px); max-height:calc(100dvh - 36px);
  overflow:auto; -webkit-overflow-scrolling:touch; }
```

Without this, a tall form on a short landscape phone can push its own submit
button off-screen with no way to reach it. Bottom sheets additionally need
`padding-bottom:calc(<base> + env(safe-area-inset-bottom))`.

## Preventing horizontal scroll

Two recurring causes, both real bugs found in this codebase:

1. **A flex nav row with no wrap.** A `header { display:flex; }` with several
   pill/link children and no `flex-wrap:wrap` fits at desktop widths and
   silently overflows the viewport once the pills no longer fit on one line
   (found on the search page nav). Add `flex-wrap:wrap` to every flex header
   row unless it already degrades some other way (e.g. `flex-direction:column`
   in a narrow media query).
2. **Long unbroken text inside a CSS grid card.** A grid track declared
   `minmax(0, 1fr)` lets the *track* shrink, but a card's title/author text
   still has `min-width:auto` by default and can force the card (and the
   whole grid, and the document) wider than the viewport if the string has no
   natural break point (found in BookVault's book grid). Add
   `overflow-wrap:anywhere` to any card title/author/summary text, and
   `min-width:0` on the grid and the card itself.

Check for this class of bug with `document.documentElement.scrollWidth >
document.documentElement.clientWidth` — a false `false` from checking only
`overflow-x` on `body` misses it, since the overflow is on `html`.

## Inputs and iOS auto-zoom

Any `<input>`/`<select>` reachable by touch needs `font-size:16px` or larger.
Below 16px, iOS Safari zooms the page in on focus and the user has to zoom
back out manually. A `font-size:15px` input guarded behind
`@media (hover:hover) and (pointer:fine)` is fine — that query already
excludes touch — but an unconditional small input font is not.

## Autoplay

`<video autoplay>` is used only on pages the user reached by an explicit
"Play"/"Watch" action, and always paired with `controls` so a browser that
blocks autoplay (common in Safari without prior media engagement) still shows
a visible play button instead of a silently stuck black frame. The Video Wall
autoplays multiple tiles simultaneously and gets away with it because every
non-focused tile is `muted` — browsers allow muted autoplay unconditionally.
Do not add unmuted multi-element autoplay anywhere else.

## Manual verification checklist

Automated checks (`tests/test_templates.py`, `python3 -m compileall`) catch
syntax errors and template-variable mistakes, not rendering. Before shipping
a change to any page template, load it in a real or emulated browser at:

- 390×844 and 844×390 (iPhone-class phone, portrait/landscape)
- 412×915 and 915×412 (Android-class phone, portrait/landscape)
- 768×1024 (iPad-class tablet, portrait)
- 1920×1080, 2560×1440, 3840×2160 (desktop)

and confirm: no horizontal scroll, no element clipped by the viewport edge,
every button/link reachable by touch, modal content fully visible and
scrollable if tall, and the fullscreen player/wall never hides its controls
behind mobile browser chrome. A Playwright script that logs in and walks the
route list is the fastest way to do this across every viewport at once; see
`CLAUDE_CINEVAULT_RESPONSIVE_RESULT_2026-09-02.md` (outside this package, in
the operator's working notes) for the exact script used for the 2026-09-02
pass.

Physical-device checks that cannot be automated: iOS Safari address-bar
collapse/expand behavior during playback, VoiceOver/TalkBack focus order,
Dynamic Type / Android font-scale at large sizes, actual notch/home-indicator
clearance on a real iPhone, and installed-PWA safe-area behavior (`Add to
Home Screen` on iOS, "Install app" on Android Chrome).
