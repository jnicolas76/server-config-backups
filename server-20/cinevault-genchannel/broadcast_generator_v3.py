#!/usr/bin/env python3
"""
broadcast_generator_v3.py - "Cinemedia Vault Sports and News" content engine.

Everything here is deterministic Python driven by real fetched data (NWS,
TheSportsDB, FMP, CDOT, RSS) - no AI/LLM writes any of the narration. This is
the piece CineMediaVault runs on its own: gather() pulls live sources,
script() turns that snapshot into a full ~30-minute rotation of segments by
template-filling real numbers/headlines into fixed sentence patterns, and
make_weather_map_v3.build() regenerates the heat-map background from that
run's real temperatures. render_broadcast_v3.py then does TTS + compositing.

Stages:
  1. gather()   -> data.json         (pull + normalize all feeds, ~50 cities)
  2. script()   -> script_v3.json    (dual-anchor rotation, content-filled to
                                       a target runtime)
  3. render_broadcast_v3.py does TTS + video compositing.

Run:
  python3 broadcast_generator_v3.py --minutes 30 --seed <int>
  python3 broadcast_generator_v3.py --reuse-data --seed <int>   # offline replay
"""
import argparse, json, random, re, sys, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import citymap
import staff_reporter

UA = "CineVaultGenChannel/0.3"
LEAGUES = {"MLB":4424,"NBA":4387,"NFL":4391,"NHL":4380,"Boxing":4445}
ESPN_LEAGUES = {"MLB": ("baseball", "mlb"), "NBA": ("basketball", "nba"),
                "NFL": ("football", "nfl"), "NHL": ("hockey", "nhl")}
STOCK_SYMBOLS = ["NFLX","AMZN","NVDA","KO","GOOGL","AAPL","DIS","MSFT","CVX","SONY","TSLA","FUBO"]
DENVER_BBOX = (-105.3,39.5,-104.6,39.95)

NEWS_FEEDS = {
    "top": [
        ("NPR", "https://feeds.npr.org/1001/rss.xml"),
        ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
        ("PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/headlines"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ],
    "local": [
        ("Colorado Public Radio", "https://www.cpr.org/feed/"),
        ("Colorado Sun", "https://coloradosun.com/feed/"),
        ("Denverite", "https://denverite.com/feed/"),
    ],
}
ENTERTAINMENT_FEEDS = [
    ("Variety", "https://variety.com/feed/"),
    ("The Hollywood Reporter", "https://www.hollywoodreporter.com/feed/"),
    ("Deadline", "https://deadline.com/feed/"),
    ("Rolling Stone", "https://www.rollingstone.com/feed/"),
    ("Billboard", "https://www.billboard.com/feed/"),
    ("IGN", "https://feeds.ign.com/ign/all"),
    ("TMZ", "https://www.tmz.com/rss.xml"),
    ("NPR Pop Culture", "https://feeds.npr.org/1039/rss.xml"),
    ("Google News Entertainment", "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en"),
]

CDOT_DISCLAIMER = ("Traffic data provided by the Colorado Department of Transportation. "
                   "Data is modified from its original source and used at one's own risk.")

def condition_icon(short):
    s = (short or "").lower()
    if "thunder" in s or "t-storm" in s: return "storm","Thunderstorms"
    if "hail" in s: return "hail","Hail"
    if "snow" in s or "flurr" in s or "wintry" in s: return "snow","Snow"
    if "fog" in s or "haze" in s: return "fog","Fog"
    if "rain" in s or "shower" in s or "drizzle" in s: return "rain","Rain"
    if "cloud" in s or "overcast" in s: return "cloudy","Cloudy"
    if "sun" in s or "clear" in s or "fair" in s: return "sun","Sunny"
    return "cloudy", (short or "Fair").split(",")[0][:16]

def getjson(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8","replace"))

def gettext(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def secret(name):
    p = HERE/".secrets.env"
    if not p.exists():
        return ""
    for line in p.read_text().splitlines():
        if line.startswith(name+"="): return line.split("=",1)[1].strip()
    return ""

def parse_rss(raw_bytes, limit=8):
    """Lenient RSS 2.0 parser (stdlib only). Skips a feed entirely on error
    rather than crashing the whole gather - matches the project's established
    tolerance for flaky/malformed feeds. Keeps the full <description> (up to
    900 chars) so the broadcast can read the actual story summary, not just
    the headline - RSS descriptions are typically a real paragraph, not a
    scraped article body (which would be far more legally fraught to reuse)."""
    out = []
    try:
        root = ET.fromstring(raw_bytes)
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            desc = (item.findtext("description") or "").strip()
            desc = re.sub("<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc)[:900].strip()
            # trim to the last full sentence so we don't cut off mid-word when read aloud
            if len(desc) > 700:
                cut = max(desc.rfind(". "), desc.rfind("! "), desc.rfind("? "))
                if cut > 200:
                    desc = desc[:cut+1]
            if title:
                out.append({"title": title, "summary": desc,
                            "link": (item.findtext("link") or "").strip()})
    except Exception:
        pass
    return out

# ---------- 1. gather ----------
def gather():
    out = {"generated": datetime.now(timezone.utc).isoformat(), "weather": {}, "sports": {}, "stocks": {}, "traffic": {}, "news": {}, "entertainment": []}

    print(f"  weather: fetching {len(citymap.CITY_LATLON)} cities...")
    for i, (city, (lat, lon)) in enumerate(citymap.CITY_LATLON.items()):
        try:
            pts = getjson(f"https://api.weather.gov/points/{lat},{lon}")
            fc = getjson(pts["properties"]["forecast"])
            p = fc["properties"]["periods"][0]
            out["weather"][city] = {"period": p["name"], "temp": p["temperature"],
                                    "unit": p["temperatureUnit"], "short": p["shortForecast"],
                                    "wind": p.get("windSpeed","")}
        except Exception as e:
            out["weather"][city] = {"error": e.__class__.__name__}
        if (i+1) % 15 == 0:
            print(f"    ...{i+1}/{len(citymap.CITY_LATLON)}")

    for lg,lid in LEAGUES.items():
        res = {"past": [], "next": []}
        try:
            past = (getjson(f"https://www.thesportsdb.com/api/v1/json/3/eventspastleague.php?id={lid}") or {}).get("events") or []
            # The past-league endpoint may return more than one scoreboard day.
            # Keep the complete response; _sports_text selects every completed
            # game from the latest date instead of arbitrarily taking one game.
            for e in past:
                res["past"].append({"event":e.get("strEvent"),"home":e.get("strHomeTeam"),"away":e.get("strAwayTeam"),
                                    "hs":e.get("intHomeScore"),"as":e.get("intAwayScore"),"ts":e.get("strTimestamp"),
                                    "homeBadge":e.get("strHomeTeamBadge"),"awayBadge":e.get("strAwayTeamBadge"),
                                    "leagueBadge":e.get("strLeagueBadge")})
            # Ask for the whole latest scoreboard day as well. The free
            # past-league endpoint can expose only a partial slate.
            latest_day = max((str(e.get("strTimestamp") or "")[:10] for e in past), default="")
            if latest_day:
                day_events = (getjson(f"https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d={latest_day}&l={lg}") or {}).get("events") or []
                known = {(g.get("event"), str(g.get("ts") or "")[:10]) for g in res["past"]}
                for e in day_events:
                    key = (e.get("strEvent"), str(e.get("strTimestamp") or "")[:10])
                    if key in known:
                        continue
                    res["past"].append({"event":e.get("strEvent"),"home":e.get("strHomeTeam"),"away":e.get("strAwayTeam"),
                                        "hs":e.get("intHomeScore"),"as":e.get("intAwayScore"),"ts":e.get("strTimestamp"),
                                        "homeBadge":e.get("strHomeTeamBadge"),"awayBadge":e.get("strAwayTeamBadge"),
                                        "leagueBadge":e.get("strLeagueBadge")})
        except Exception: pass
        # ESPN's public scoreboard returns the complete daily slate. Prefer it
        # for the major US leagues because TheSportsDB's free past endpoint can
        # return just one representative event.
        if lg in ESPN_LEAGUES:
            sport, league_slug = ESPN_LEAGUES[lg]
            complete_day = []
            now = datetime.now(timezone.utc).date()
            for day in (now, now - timedelta(days=1), now - timedelta(days=2)):
                try:
                    scoreboard = getjson(
                        f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league_slug}/scoreboard?dates={day.strftime('%Y%m%d')}&limit=1000"
                    )
                    day_games = []
                    for event in scoreboard.get("events", []):
                        competition = (event.get("competitions") or [{}])[0]
                        status = competition.get("status") or event.get("status") or {}
                        if not (status.get("type") or {}).get("completed"):
                            continue
                        sides = {c.get("homeAway"): c for c in competition.get("competitors", [])}
                        home, away = sides.get("home"), sides.get("away")
                        if not home or not away:
                            continue
                        def team_name(side):
                            return (side.get("team") or {}).get("displayName") or (side.get("team") or {}).get("name") or "Team"
                        def badge(side):
                            logos = (side.get("team") or {}).get("logos") or []
                            return logos[0].get("href") if logos else None
                        day_games.append({"event": event.get("name"), "home": team_name(home), "away": team_name(away),
                                          "hs": home.get("score"), "as": away.get("score"), "ts": event.get("date"),
                                          "day": day.isoformat(),
                                          "homeBadge": badge(home), "awayBadge": badge(away), "leagueBadge": None})
                    if day_games:
                        complete_day = day_games
                        break
                except Exception:
                    continue
            if complete_day:
                res["past"] = complete_day
        try:
            nxt = (getjson(f"https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php?id={lid}") or {}).get("events") or []
            for e in nxt[:6]:
                res["next"].append({"event":e.get("strEvent"),"ts":e.get("strTimestamp"),
                                    "homeBadge":e.get("strHomeTeamBadge"),"awayBadge":e.get("strAwayTeamBadge")})
        except Exception: pass
        out["sports"][lg] = res

    fmp = secret("FMP_API_KEY")
    for s in STOCK_SYMBOLS:
        try:
            d = getjson(f"https://financialmodelingprep.com/stable/quote?symbol={s}&apikey={fmp}")
            if isinstance(d,list) and d:
                q=d[0]; out["stocks"][s]={"price":q.get("price"),"change":q.get("change"),"pct":q.get("changePercentage")}
        except Exception: pass

    cdot = secret("CDOT_API_KEY")
    try:
        d = getjson(f"https://data.cotrip.org/api/v1/incidents?apiKey={cdot}")
        feats = d.get("features",[])
        metro=[]
        for f in feats:
            try:
                coords=f["geometry"]["coordinates"]
                c=coords[0] if isinstance(coords[0],list) else coords
                lon,lat=c[0],c[1]
                if DENVER_BBOX[0]<lon<DENVER_BBOX[2] and DENVER_BBOX[1]<lat<DENVER_BBOX[3]:
                    p=f["properties"]
                    metro.append({"type":p.get("type") or p.get("category"),
                                  "route":p.get("routeName") or p.get("patrolRoute"),
                                  "desc":(p.get("travelerInformationMessage") or p.get("name") or "")[:220],
                                  "lat":lat,"lon":lon})
            except Exception: continue
        out["traffic"]={"metro_count":len(metro),"statewide":len(feats),"items":metro[:8],"disclaimer":CDOT_DISCLAIMER}
    except Exception as e:
        out["traffic"]={"error":e.__class__.__name__,"disclaimer":CDOT_DISCLAIMER}

    print("  news: fetching RSS feeds...")
    for cat, feeds in NEWS_FEEDS.items():
        items = []
        for name, url in feeds:
            try:
                items.append({"source": name, "items": parse_rss(gettext(url))})
            except Exception as e:
                items.append({"source": name, "items": [], "error": e.__class__.__name__})
        out["news"][cat] = items

    print("  entertainment: fetching and validating attributed RSS headlines...")
    for name, url in ENTERTAINMENT_FEEDS:
        checked = datetime.now(timezone.utc).isoformat()
        try:
            items = parse_rss(gettext(url), limit=12)
            out["entertainment"].append({"source": name, "url": url, "checked_at": checked,
                                         "reachable": bool(items), "items": items})
        except Exception as exc:
            out["entertainment"].append({"source": name, "url": url, "checked_at": checked,
                                         "reachable": False, "items": [], "error": exc.__class__.__name__})
    (HERE / "entertainment-source-validation.json").write_text(
        json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(),
                    "policy": "Headline-level summaries with source attribution and links; no full article republication.",
                    "sources": out["entertainment"]}, indent=2))

    (HERE/"data.json").write_text(json.dumps(out,indent=2))
    return out

# ---------- 2. script (30-minute rotation) ----------
VOICE_A = "af_heart"
VOICE_B = "am_michael"
# Stable, unique English voices for every fictional member of the six crews.
# These assignments never change between editions.
STAFF_VOICES = {
    "Grant Halloway": "am_adam", "Vivian Castellano": "af_alloy",
    "Priya Anand": "af_aoede", "Marcus Delgado": "am_echo",
    "Theo Brennan": "am_eric", "Renata Solis": "af_bella",
    "Camille Okafor": "af_heart", "Devin Yamamoto": "am_fenrir",
    "Roland Whitfield": "am_liam", "Adele Marchetti": "af_jessica",
    "Simone Beaumont": "af_kore", "Xavier Cole": "am_michael",
    "Julian Reyes": "am_onyx", "Harper Lindqvist": "af_nicole",
    "Nadia Petrov": "af_nova", "Terrence Boone": "am_puck",
    "Miles Ashford": "bm_daniel", "Bianca Fontaine": "af_river",
    "Elena Vasquez": "af_sarah", "Damon Kessler": "bm_fable",
    "Sterling Vance": "bm_george", "Ingrid Larsen": "af_sky",
    "Rosa Delacroix": "bf_alice", "Malik Hartley": "bm_lewis",
}
WORDS_PER_SEC = 1.7  # measured against real Kokoro speed=0.85 output, used only to size the rotation

def _dur_est(text):
    return max(len(text.split()) / WORDS_PER_SEC + 1.5, 8.0)

def _weather_text(data, detailed=True):
    cities = [(c, w) for c, w in data["weather"].items() if "temp" in w]
    if not cities:
        return "Weather data is temporarily unavailable.", []
    headline = [(c, w) for c, w in cities if c in citymap.HEADLINE_CITIES]
    random.shuffle(headline)
    lines = [f"{c}, {condition_icon(w.get('short'))[1].lower()}, {w['temp']} degrees." for c, w in headline]
    temps = [w["temp"] for _, w in cities]
    avg = sum(temps) / len(temps)
    warmest = max(cities, key=lambda cw: cw[1]["temp"])
    coldest = min(cities, key=lambda cw: cw[1]["temp"])
    zone_line = (f"Nationally, the warmest reading is {warmest[0]} at {warmest[1]['temp']} degrees, "
                 f"and the coldest is {coldest[0]} at {coldest[1]['temp']} degrees.")
    city_cards = [{"city": c, "temp": w.get("temp"), "cond": condition_icon(w.get("short"))[1],
                   "icon": condition_icon(w.get("short"))[0]} for c, w in headline]
    if detailed:
        text = "Here's your national weather. " + " ".join(lines) + " " + zone_line
    else:
        text = "A quick weather update. " + " ".join(lines[:5])
    return text, city_cards

def _sports_text(lg, res):
    played=[g for g in res["past"] if g.get("hs") not in (None,"")]
    if lg=="Boxing": played=res["past"]
    dated = [g for g in played if g.get("day") or g.get("ts")]
    if dated:
        latest_day = max(str(g.get("day") or g.get("ts") or "")[:10] for g in dated)
        played = [g for g in played if str(g.get("day") or g.get("ts") or "")[:10] == latest_day]
    if not played and not res["next"]:
        return None
    lines=[]
    for g in played:
        if g.get("hs") not in (None,""):
            hs,as_=int(g["hs"]),int(g["as"])
            win,los,ws,ls=(g['home'],g['away'],hs,as_) if hs>=as_ else (g['away'],g['home'],as_,hs)
            lines.append(f"{win} beat {los}, {ws} to {ls}.")
        else:
            lines.append(f"{g.get('event')}.")
    if res["next"]:
        lines.append(f"Coming up: {res['next'][0].get('event')}.")
        if len(res["next"]) > 1:
            lines.append(f"Also on the schedule: {res['next'][1].get('event')}.")
    text = f"{lg} scores. " + " ".join(lines)
    return text, {"template":"sports","league":lg,"games":played,"next":res["next"][:2]}

def _traffic_text(tr):
    n=tr.get("metro_count",0)
    if n==0:
        return "Denver and Aurora roads are clear this morning. No major incidents to report.", tr.get("items",[])
    items="; ".join((i.get("desc") or i.get("type","incident")) for i in tr.get("items",[])[:3])
    return (f"Denver and Aurora traffic. {n} incident{'s' if n!=1 else ''} reported on metro roads. "
            f"{items}."), tr.get("items",[])

def _markets_text(stocks):
    lines=[]
    for s,q in stocks.items():
        if q.get("price") is None: continue
        d = "up" if (q.get("change") or 0) >= 0 else "down"
        lines.append(f"{s} {d} at {q['price']:.2f}.")
    if not lines:
        return "Market data is temporarily unavailable."
    random.shuffle(lines)
    return "A look at the markets. " + " ".join(lines[:10])

def _news_text(cat_items, label, stories=3):
    """Reads the actual story - title plus its RSS summary paragraph - not
    just the headline, so a news block is a real amount of spoken content."""
    picked = []
    for src in cat_items:
        for it in src.get("items", [])[:3]:
            picked.append((src["source"], it))
    if not picked:
        return None
    random.shuffle(picked)
    picked = picked[:stories]
    lines = []
    for src, it in picked:
        piece = f"From {src}: {it['title']}."
        if it.get("summary"):
            piece += f" {it['summary']}"
        lines.append(piece)
    headlines = [{"source": src, "title": it["title"]} for src, it in picked]
    return f"{label}. " + " ".join(lines), headlines

def _entertainment_text(feeds, stories=4):
    """Create an attributed headline digest without republishing article text."""
    pool=[]
    for feed in feeds:
        if not feed.get("reachable"): continue
        for item in feed.get("items", [])[:3]: pool.append((feed["source"], item))
    if not pool: return None
    random.shuffle(pool); picked=pool[:stories]
    lines=[f"{src} reports: {item['title']}." for src,item in picked]
    cards=[{"source":src,"title":item["title"],"link":item.get("link","")} for src,item in picked]
    return "Entertainment news. " + " ".join(lines), cards

def script(data, minutes, seed):
    rng = random.Random(seed)
    random.seed(seed)
    segs = []
    def add(kind, text, visual=None):
        segs.append({"kind": kind, "text": text, "visual": visual or {}})

    # ---- crew + banter, from /mnt/c/DATA/staff.txt and reporter.txt ----
    crews = staff_reporter.parse_staff()
    crew_key, daypart = staff_reporter.select_crew()
    crew = crews.get(crew_key, {})
    buckets = staff_reporter.parse_reporter()
    used_banter = set()

    def banter(category, k=1, chance=1.0):
        # Conversation belongs where a human producer would use it: most
        # handoffs get one line, some go straight to the story, and no authored
        # line repeats during the same edition.
        if rng.random() > chance:
            return []
        contextual = "CONTEXT_" + category
        picks = staff_reporter.pick_lines(buckets, contextual, crew, daypart, rng, k, used_banter)
        return picks or staff_reporter.pick_lines(buckets, category, crew, daypart, rng, k, used_banter)

    def toss(category, chance=0.72):
        picks = banter(category, 1, chance)
        return (picks[0] + " ") if picks else ""

    # Reserve room for the station intro and close; the finished proof must
    # stay at or below about 35 minutes even when a final segment runs long.
    minutes = min(int(minutes), 35)
    target = min(minutes * 60 - 75, 33 * 60)

    # Keep this compact enough to fit over the 20-second station-intro video
    # while naming the entire selected crew.
    intro_text = (
        f"Good {daypart}. I'm {crew.get('ML')}, with {crew.get('FL')}. "
        f"{crew.get('WX')} has weather, and {crew.get('TS')} has sports and traffic. "
        f"This is Cine News."
    )
    open_text = (f"Welcome to Cinemedia Vault Sports and News, your {daypart} broadcast. "
                 f"Here's what's happening today.")
    add("open", open_text,
        {"template": "title", "title": "Cinemedia Vault Sports and News",
         "subtitle": f"{daypart.capitalize()} Broadcast", "crew": crew, "daypart": daypart,
         "reporter": crew.get("ML")})

    leagues = list(data["sports"].items()); rng.shuffle(leagues)
    sports_segs = []
    for lg, res in leagues:
        r = _sports_text(lg, res)
        if r:
            full_text, visual = r
            games = visual.get("games", [])
            if len(games) <= 8:
                sports_segs.append((f"sports_{lg}", full_text, visual))
            else:
                chunks = [games[i:i + 8] for i in range(0, len(games), 8)]
                for part, chunk in enumerate(chunks, 1):
                    lines = []
                    for game in chunk:
                        hs, as_ = int(game["hs"]), int(game["as"])
                        win, los, ws, ls = ((game["home"], game["away"], hs, as_)
                                            if hs >= as_ else (game["away"], game["home"], as_, hs))
                        lines.append(f"{win} beat {los}, {ws} to {ls}.")
                    part_text = f"{lg} scores, part {part} of {len(chunks)}. " + " ".join(lines)
                    part_visual = dict(visual)
                    part_visual["games"] = chunk
                    part_visual["next"] = visual.get("next", []) if part == len(chunks) else []
                    sports_segs.append((f"sports_{lg}", part_text, part_visual))

    tr = data.get("traffic", {})
    total = _dur_est(segs[0]["text"])
    si = 0
    pass_n = 0
    weather_count = 0
    while total < target:
        pass_n += 1
        before = total

        # Exactly two weather appearances per edition: a full forecast early
        # and a shorter update on the second pass. Never repeat it afterward.
        if weather_count < 2 and pass_n <= 2:
            wtext, cities = _weather_text(data, detailed=(weather_count == 0))
            wtext = toss("ANCHOR_WEATHER", 0.82 if weather_count == 0 else 0.55) + wtext
            add("weather", wtext, {"template": "weather", "cities": cities,
                                    "reporter": crew.get("WX"), "reporter_role": "Meteorologist"})
            total += _dur_est(wtext); weather_count += 1
            if total >= target: break

        entertainment = _entertainment_text(data.get("entertainment", []), stories=4 if pass_n == 1 else 3)
        if entertainment:
            etext, cards = entertainment
            etext = toss("ANCHOR_ANCHOR", 0.65) + etext
            add("entertainment", etext, {"template":"news", "label":"Entertainment", "headlines":cards,
                                          "reporter":crew.get("FL"), "reporter_role":"Entertainment Desk"})
            total += _dur_est(etext)
        if total >= target: break

        for cat, label, rep in (("top", "Top stories", crew.get("FL")), ("local", "Colorado news", crew.get("ML"))):
            items = data.get("news", {}).get(cat, [])
            r = _news_text(items, label if pass_n == 1 else f"{label} update", stories=3)
            if r:
                ntext, headlines = r
                ntext = toss("ANCHOR_ANCHOR", 0.68) + ntext
                add(f"news_{cat}", ntext, {"template": "news", "label": label, "headlines": headlines,
                                            "reporter": rep, "reporter_role": "Anchor"})
                total += _dur_est(ntext)
            if total >= target: break
        if total >= target: break

        if si < len(sports_segs):
            kind, text, visual = sports_segs[si]
            text = toss("ANCHOR_SPORTS", 0.78) + text
            visual = dict(visual); visual["reporter"] = crew.get("TS"); visual["reporter_role"] = "Sports & Traffic"
            add(kind, text, visual); total += _dur_est(text); si += 1
        if total >= target: break

        ttext, titems = _traffic_text(tr)
        ttext = toss("ANCHOR_SPORTS", 0.72) + ttext
        add("traffic", ttext, {"template": "traffic", "items": titems, "disclaimer": tr.get("disclaimer"),
                                "reporter": crew.get("TS"), "reporter_role": "Traffic & Sports"})
        total += _dur_est(ttext)
        if total >= target: break

        mtext = _markets_text(data.get("stocks", {}))
        mtext = toss("ANCHOR_ANCHOR", 0.60) + mtext
        add("markets", mtext, {"template": "markets", "stocks": data.get("stocks", {})})
        total += _dur_est(mtext)
        if total >= target: break

        if si < len(sports_segs):
            kind, text, visual = sports_segs[si]
            text = toss("ANCHOR_SPORTS", 0.62) + text
            visual = dict(visual); visual["reporter"] = crew.get("TS"); visual["reporter_role"] = "Sports & Traffic"
            add(kind, text, visual); total += _dur_est(text); si += 1

        if pass_n > 40 or total - before < 1.0:
            break  # safety valve - stop if we've done a LOT of passes, or a
                    # full pass added ~nothing (sources genuinely exhausted)

    close_banter = " ".join(banter("GROUP", 2, 1.0))
    close_text = f"{close_banter} That's your Cinemedia Vault Sports and News broadcast. Back soon with more."
    add("close", close_text,
        {"template": "title", "title": "Cinemedia Vault Sports and News", "subtitle": "Stay tuned",
         "reporter": crew.get("FL")})

    for i, seg in enumerate(segs):
        reporter = (seg.get("visual") or {}).get("reporter")
        if not reporter:
            if seg["kind"] == "markets": reporter = crew.get("ML")
            else: reporter = crew.get("FL") if i % 2 else crew.get("ML")
            seg.setdefault("visual", {})["reporter"] = reporter
        seg["voice"] = STAFF_VOICES.get(reporter, VOICE_A if i % 2 == 0 else VOICE_B)

    stock_ticks=[]
    for s,q in data["stocks"].items():
        if q.get("price") is not None:
            arrow = "▲" if (q.get("change") or 0)>=0 else "▼"
            stock_ticks.append(f"{s} {q['price']} {arrow}{abs(q.get('change') or 0):.2f}")
    score_ticks=[]
    for lg,res in data["sports"].items():
        for g in res["past"]:
            if g.get("hs") not in (None,""):
                score_ticks.append(f"{lg}: {g.get('home','')} {g['hs']}-{g['as']} {g.get('away','')}")
    out={"seed":seed,"target_minutes":minutes,"est_seconds":round(total),
          "crew_key": crew_key, "crew": crew, "daypart": daypart,
         "intro_text": intro_text, "intro_voice": STAFF_VOICES.get(crew.get("ML"), VOICE_B),
         "staff_voices": {name: STAFF_VOICES.get(name) for name in crew.values()},
         "ticker_stocks":"    •    ".join(stock_ticks),
         "ticker_scores":"    •    ".join(score_ticks) or "Scores updating...",
         "segments":segs}
    (HERE/"script_v3.json").write_text(json.dumps(out,indent=2))
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--minutes",type=int,default=35)
    ap.add_argument("--seed",type=int,default=None)
    ap.add_argument("--gather-only",action="store_true")
    ap.add_argument("--reuse-data",action="store_true")
    ap.add_argument("--skip-map",action="store_true",help="don't regenerate usa_weather_map.png")
    args=ap.parse_args()
    seed=args.seed if args.seed is not None else random.randint(1,10_000_000)
    if args.reuse_data:
        data=json.loads((HERE/"data.json").read_text())
        print("reusing cached data.json (offline, no network calls)")
    else:
        print("gathering feeds...")
        data=gather()
    print("weather:",sum(1 for w in data['weather'].values() if 'temp' in w),"cities |",
          "sports leagues:",len(data['sports']),"| stocks:",len(data['stocks']),
          "| traffic metro incidents:",data['traffic'].get('metro_count'),
          "| news items:", sum(len(s.get('items',[])) for cat in data.get('news',{}).values() for s in cat))
    if not args.skip_map:
        import make_weather_map_v3
        make_weather_map_v3.build(data.get("weather", {}))
        print("regenerated usa_weather_map.png from this run's real temperatures")
    if args.gather_only: return
    s=script(data,args.minutes,seed)
    print(f"script: {len(s['segments'])} segments, seed {seed}, est {s['est_seconds']}s (~{s['est_seconds']/60:.1f} min)")
    print("wrote script_v3.json")

if __name__=="__main__":
    main()
