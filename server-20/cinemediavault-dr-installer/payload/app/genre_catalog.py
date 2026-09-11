#!/usr/bin/env python3
"""CineMediaVault genre discovery: browse/search Movies and TV Shows by their
already-stored TMDb genre metadata (movie-metadata-map.json / tv-metadata-map.json).

No network calls are made here. Genre tiles and title lists are derived on each
request directly from the in-memory catalogs (movie_app.movie_index.items /
tv_app.tv_index.shows) that the server already keeps loaded, so this stays cheap
(a few thousand dict lookups) without a background cache of its own.
"""
import html
import re

# TMDb uses a slightly different genre vocabulary for movies vs TV, and some
# names are near-duplicates of each other (e.g. "Science Fiction" vs
# "Sci-Fi & Fantasy"). These aliases group equivalent values into one canonical
# label for browsing/search WITHOUT touching the underlying stored metadata -
# the original genre strings are never modified or dropped.
GENRE_ALIASES = {
    "action & adventure": "Action",
    "sci-fi & fantasy": "Science Fiction",
    "war & politics": "War",
    "kids": "Family",
}

CARD_STYLE = """
:root{color-scheme:dark;--bg:#090a0d;--panel:#181a1e;--panel2:#23262b;--line:#393d43;--gold:#f5b400;--muted:#aeb4bc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#fff;font:15px system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;z-index:8;background:#111;border-bottom:1px solid #333;padding:13px 18px;padding-top:calc(13px + env(safe-area-inset-top))}
.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px}
.brand{font-size:22px;font-weight:900}.brand b{color:var(--gold)}
a.btn{border:1px solid var(--line);background:var(--panel2);color:#fff;border-radius:8px;min-height:40px;padding:9px 14px;font-weight:750;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center}
a.btn:focus-visible,.tile:focus-visible,.card a:focus-visible{outline:3px solid var(--gold);outline-offset:2px}
main{padding:18px}
.crumb{color:var(--muted);margin-bottom:14px}.crumb a{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;text-decoration:none;color:#fff;display:block;transition:transform .12s,border-color .12s}
.tile:hover,.tile:focus-visible{border-color:var(--gold);transform:translateY(-2px)}
.tile h3{margin:0 0 6px;font-size:19px}
.tile span{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.card a{display:block;color:#fff;text-decoration:none}
.card img{width:100%;aspect-ratio:2/3;object-fit:cover;background:#000;display:block}
.card .noposter{width:100%;aspect-ratio:2/3;background:#222;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px;text-align:center;padding:8px}
.card .meta{padding:8px 10px}
.card .meta b{display:block;font-size:13px;line-height:1.3}
.card .meta span{color:var(--muted);font-size:12px}
.empty{color:var(--muted);padding:40px 0;text-align:center}
"""


def canonical_genre(name):
    name = (name or "").strip()
    if not name:
        return ""
    alias = GENRE_ALIASES.get(name.casefold())
    return alias or name


def genre_slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").casefold()).strip("-") or "genre"


def _movie_genre_map(movie_app):
    out = {}
    for item in movie_app.movie_index.items:
        metadata = movie_app.metadata_for(item)
        raw_genres = metadata.get("genres") or []
        seen = set()
        for raw in raw_genres:
            canon = canonical_genre(raw)
            if not canon or canon in seen:
                continue
            seen.add(canon)
            out.setdefault(canon, []).append((item, metadata))
    return out


def _tv_genre_map(tv_app):
    out = {}
    for show in tv_app.tv_index.shows:
        metadata = tv_app.metadata_for(show)
        raw_genres = metadata.get("genres") or []
        seen = set()
        for raw in raw_genres:
            canon = canonical_genre(raw)
            if not canon or canon in seen:
                continue
            seen.add(canon)
            out.setdefault(canon, []).append((show, metadata))
    return out


def movie_genre_tiles(movie_app):
    gm = _movie_genre_map(movie_app)
    tiles = [{"name": name, "slug": genre_slug(name), "count": len(items)} for name, items in gm.items()]
    tiles.sort(key=lambda t: (-t["count"], t["name"]))
    return tiles


def tv_genre_tiles(tv_app):
    gm = _tv_genre_map(tv_app)
    tiles = [{"name": name, "slug": genre_slug(name), "count": len(items)} for name, items in gm.items()]
    tiles.sort(key=lambda t: (-t["count"], t["name"]))
    return tiles


def movie_cards_for_genre(movie_app, slug):
    gm = _movie_genre_map(movie_app)
    for name, items in gm.items():
        if genre_slug(name) == slug:
            cards = []
            for item, metadata in items:
                try:
                    rating = float(metadata.get("vote_average") or 0)
                except (TypeError, ValueError):
                    rating = 0.0
                cards.append({
                    "id": item.id,
                    "title": metadata.get("title") or item.title,
                    "year": metadata.get("year") or "",
                    "poster": movie_app.poster_url_for(item),
                    "rating": rating,
                    "href": f"/movie/{item.id}",
                })
            cards.sort(key=lambda c: c["title"].casefold())
            return name, cards
    return None, []


def tv_cards_for_genre(tv_app, slug):
    gm = _tv_genre_map(tv_app)
    for name, items in gm.items():
        if genre_slug(name) == slug:
            cards = []
            for show, metadata in items:
                try:
                    rating = float(metadata.get("vote_average") or 0)
                except (TypeError, ValueError):
                    rating = 0.0
                cards.append({
                    "id": show.id,
                    "title": metadata.get("title") or show.title,
                    "year": metadata.get("year") or "",
                    "poster": tv_app.poster_url_for(show),
                    "rating": rating,
                    "href": f"/tv/show/{show.id}",
                })
            cards.sort(key=lambda c: c["title"].casefold())
            return name, cards
    return None, []


NAV_HTML = (
    "<header><div class='top'>"
    "<div class='brand'>CineMedia<b>Vault</b> Genres</div>"
    "<div><a class='btn' href='/movies'>Movies</a> <a class='btn' href='/tv'>TV Shows</a> "
    "<a class='btn' href='/search'>Search</a> <a class='btn' href='/'>Home</a></div>"
    "</div></header>"
)

SPATIAL_NAV_JS = """
<script>
(function(){
  function focusables(){return Array.prototype.slice.call(document.querySelectorAll('a.tile,.card a'))}
  document.addEventListener('keydown', function(e){
    var keys=['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'];
    if(keys.indexOf(e.key)===-1) return;
    var items=focusables();
    if(!items.length) return;
    var active=document.activeElement;
    var idx=items.indexOf(active);
    if(idx===-1){items[0].focus();e.preventDefault();return}
    var rect=active.getBoundingClientRect();
    var cols=Math.max(1, Math.round(document.body.clientWidth/(rect.width+14)));
    var next=idx;
    if(e.key==='ArrowRight') next=idx+1;
    else if(e.key==='ArrowLeft') next=idx-1;
    else if(e.key==='ArrowDown') next=idx+cols;
    else if(e.key==='ArrowUp') next=idx-cols;
    if(next>=0 && next<items.length){items[next].focus();e.preventDefault()}
  });
})();
</script>
"""


def tiles_page(kind, tiles):
    label = "Movies" if kind == "movies" else "TV Shows"
    body_tiles = "".join(
        f"<a class='tile' href='/genres/{kind}/{html.escape(t['slug'])}' tabindex='0'>"
        f"<h3>{html.escape(t['name'])}</h3><span>{t['count']} title{'s' if t['count'] != 1 else ''}</span></a>"
        for t in tiles
    ) or "<p class='empty'>No genre-tagged titles found yet.</p>"
    return (
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>CineMediaVault - {label} Genres</title><style>{CARD_STYLE}</style></head><body>"
        f"{NAV_HTML}<main><div class='crumb'><a href='/genres/movies'>Movie Genres</a> &middot; "
        f"<a href='/genres/tv'>TV Genres</a></div><h1>{label} by Genre</h1>"
        f"<div class='tiles'>{body_tiles}</div></main>{SPATIAL_NAV_JS}</body></html>"
    )


def cards_page(kind, genre_name, cards):
    label = "Movies" if kind == "movies" else "TV Shows"
    back = f"/genres/{kind}"
    body_cards = "".join(
        (
            f"<article class='card'><a href='{html.escape(c['href'])}' tabindex='0'>"
            + (f"<img loading='lazy' src='{html.escape(c['poster'])}' alt=''>" if c.get("poster") else "<div class='noposter'>No Poster</div>")
            + f"<div class='meta'><b>{html.escape(c['title'])}</b>"
            + f"<span>{html.escape(str(c.get('year') or ''))}"
            + (f" &middot; &#9733; {c['rating']:.1f}" if c.get("rating") else "")
            + "</span></div></a></article>"
        )
        for c in cards
    ) or "<p class='empty'>No local titles tagged with this genre.</p>"
    title = html.escape(genre_name or "Genre")
    return (
        "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>CineMediaVault - {title}</title><style>{CARD_STYLE}</style></head><body>"
        f"{NAV_HTML}<main><div class='crumb'><a href='{back}'>&larr; {label} Genres</a></div>"
        f"<h1>{title} <span style='color:var(--muted);font-size:16px'>({len(cards)} {label.lower()})</span></h1>"
        f"<div class='cards'>{body_cards}</div></main>{SPATIAL_NAV_JS}</body></html>"
    )


def handle_get(handler, user, path, movie_app, tv_app):
    if path == "/genres/movies":
        return handler.render_html(tiles_page("movies", movie_genre_tiles(movie_app)))
    if path == "/genres/tv":
        return handler.render_html(tiles_page("tv", tv_genre_tiles(tv_app)))
    if path.startswith("/genres/movies/"):
        slug = path.rsplit("/", 1)[-1]
        name, cards = movie_cards_for_genre(movie_app, slug)
        if name is None:
            return handler.send_error(404, "Unknown genre")
        return handler.render_html(cards_page("movies", name, cards))
    if path.startswith("/genres/tv/"):
        slug = path.rsplit("/", 1)[-1]
        name, cards = tv_cards_for_genre(tv_app, slug)
        if name is None:
            return handler.send_error(404, "Unknown genre")
        return handler.render_html(cards_page("tv", name, cards))
    if path == "/api/genres/movies":
        return handler.json_response({"ok": True, "genres": movie_genre_tiles(movie_app)})
    if path == "/api/genres/tv":
        return handler.json_response({"ok": True, "genres": tv_genre_tiles(tv_app)})
    if path.startswith("/api/genres/movies/"):
        slug = path.rsplit("/", 1)[-1]
        name, cards = movie_cards_for_genre(movie_app, slug)
        return handler.json_response({"ok": name is not None, "name": name, "items": cards})
    if path.startswith("/api/genres/tv/"):
        slug = path.rsplit("/", 1)[-1]
        name, cards = tv_cards_for_genre(tv_app, slug)
        return handler.json_response({"ok": name is not None, "name": name, "items": cards})
    return False
