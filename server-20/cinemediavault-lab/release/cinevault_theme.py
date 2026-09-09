"""Shared per-user color theme support for CineVault.

Three themes are supported: the original gold/amber "default" look, plus
two account-selectable alternates ("royal-blue" and "green"). The default
palette continues to live inline in each page's own CSS exactly as before;
this module only adds a small, centrally maintained override stylesheet
(cinevault-theme.css) that is linked into every rendered page, plus a
`data-theme` attribute on <html> so the browser applies the right palette
on the very first paint (no flash of the default gold theme).

Imported by the main wrapper and by every mixin/module that renders full
HTML pages (music, DVR, video lists, and the movie/TV library modules), so
it is intentionally dependency-free and safe to import from a standalone
script as well as from the combined CineVault process.
"""

import re

THEME_ALLOWED = ("default", "royal-blue", "green")
DEFAULT_THEME = "default"

# Served from ASSET_DIR (see serve_asset in the main wrapper); a plain
# cacheable static file rather than an inline blob repeated on every response.
THEME_STYLE_LINK = '<link rel="stylesheet" href="/assets/cinevault-theme.css">'

_HTML_OPEN_RE = re.compile(r"<html(\s[^>]*)?>")


def normalize_theme(value) -> str:
    """Validate a theme value against the strict allow-list."""
    value = (value or "").strip().lower()
    return value if value in THEME_ALLOWED else DEFAULT_THEME


def theme_for_row(user) -> str:
    """Resolve the theme for a sqlite3.Row-like user record (or None/anonymous)."""
    if not user:
        return DEFAULT_THEME
    try:
        value = user["theme"]
    except (IndexError, KeyError, TypeError):
        value = None
    return normalize_theme(value)


def inject_theme(body: str, theme) -> str:
    """Stamp a fully-rendered HTML page with the resolved theme.

    No-op for the default theme (the page's original inline styling is left
    completely untouched). For an alternate theme, adds data-theme to <html>
    and links the shared override stylesheet into <head>. Idempotent: safe
    to call more than once on the same body.
    """
    theme = normalize_theme(theme)
    if theme == DEFAULT_THEME:
        return body
    if 'data-theme=' not in body:
        body = _HTML_OPEN_RE.sub(
            lambda m: f'<html{m.group(1) or ""} data-theme="{theme}">', body, count=1
        )
    if THEME_STYLE_LINK not in body and "<head>" in body:
        body = body.replace("<head>", "<head>" + THEME_STYLE_LINK, 1)
    return body
