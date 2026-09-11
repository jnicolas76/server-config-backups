"""Extended XMLTV guide merge for CineVault Live TV / DVR.

Merges the 14-day XMLTV feed produced by the iptv-org/epg collector
(cinevault-epg on 192.168.1.134) behind the authoritative HDHomeRun guide.

Design rules, in priority order:

1.  The HDHomeRun guide is always preferred.  Extended programmes are only ever
    appended strictly after the last HDHomeRun programme on that channel, so the
    two sources can never disagree about the same airing and no de-duplication
    of overlapping content is required.
2.  A channel is only extended if the extended feed agrees with the HDHomeRun
    guide inside the overlap window.  That single test re-proves both the channel
    mapping and the timezone/DST handling on every refresh, so a lineup change or
    a DST bug degrades to "HDHomeRun only" instead of showing wrong times.
3.  Any failure - unreachable collector, malformed XML, stale feed, too few
    channels agreeing - leaves the HDHomeRun payload completely untouched.

Everything here is additive: programme dictionaries keep their existing keys and
only gain a "source" field, so the DVR scheduler, series rules and the grid guide
continue to work unchanged.
"""

from __future__ import annotations

import datetime
import difflib
import json
import os
import re
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ENABLED = os.environ.get("CINEVAULT_EPG_EXTEND_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
FEED_URL = os.environ.get("CINEVAULT_EPG_EXTEND_URL", "http://192.168.1.134:3010/guide.xml")
MAP_FILE = Path(os.environ.get("CINEVAULT_EPG_EXTEND_MAP",
                              "/home/jnicolas/cinemediavault-lab/cinevault-data/epg-extend-map.json"))
TIMEOUT = float(os.environ.get("CINEVAULT_EPG_EXTEND_TIMEOUT", "60"))

# tvpassport.com publishes D+0..D+13 for this market and silently repeats today's
# listings beyond that, so the horizon is a hard ceiling rather than a preference.
MAX_HORIZON_DAYS = float(os.environ.get("CINEVAULT_EPG_EXTEND_DAYS", "14"))

# Per-channel overlap agreement required before that channel may be extended.
MIN_OVERLAP_SAMPLES = 6
MIN_OVERLAP_RATE = 0.55
# If hardly any channel agrees, treat the whole feed as suspect and merge nothing.
MIN_CHANNELS_ACCEPTED_RATE = 0.30

MAX_PROGRAMME_SECONDS = 12 * 3600
PREFIX = "hdhr-"


def _norm_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").lower()
    value = re.sub(r"\(.*?\)", " ", value)
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    value = re.sub(r"\b(the|a|an|show|tv|with|and)\b", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _titles_agree(left: str, right: str) -> bool:
    a, b = _norm_title(left), _norm_title(right)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.82


def _timestamp(value: str) -> int:
    """Parse an XMLTV timestamp. The explicit UTC offset makes this DST-exact."""
    try:
        return int(datetime.datetime.strptime(str(value or "").strip(), "%Y%m%d%H%M%S %z").timestamp())
    except (TypeError, ValueError):
        return 0


def load_map() -> dict:
    try:
        return json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "CineMediaVault/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def _parse_feed(raw: bytes) -> dict[str, list[dict]]:
    """Return {guide_number: [programme, ...]} for channels named hdhr-<number>."""
    root = ET.fromstring(raw)
    out: dict[str, list[dict]] = {}
    for item in root.findall("programme"):
        channel_id = str(item.get("channel") or "")
        if not channel_id.startswith(PREFIX):
            continue
        number = channel_id[len(PREFIX):]
        start = _timestamp(item.get("start"))
        stop = _timestamp(item.get("stop"))
        if not start or not stop or stop <= start or (stop - start) > MAX_PROGRAMME_SECONDS:
            continue
        title = str(item.findtext("title") or "").strip()
        if not title:
            continue
        icon = ""
        icon_element = item.find("icon")
        if icon_element is not None and icon_element.get("src"):
            icon = str(icon_element.get("src"))
        elif item.findtext("image"):
            icon = str(item.findtext("image")).strip()
        categories = [str(element.text or "").strip() for element in item.findall("category")]
        out.setdefault(number, []).append({
            "title": title,
            "subtitle": str(item.findtext("sub-title") or "").strip(),
            "description": str(item.findtext("desc") or "").strip(),
            "category": ", ".join([c for c in categories if c]),
            "episode_num": str(item.findtext("episode-num") or "").strip(),
            "is_new": item.find("new") is not None,
            "date": str(item.findtext("date") or "").strip(),
            "icon": icon,
            "start": start,
            "stop": stop,
            "source": "tvpassport",
        })
    for values in out.values():
        values.sort(key=lambda entry: entry["start"])
    return out


def _overlap_agreement(extended: list[dict], baseline: list[dict]) -> tuple[int, int, int]:
    """Compare extended programmes against HDHomeRun inside the HDHomeRun window.

    The comparison is repeated at +/-1h and +/-2h offsets as well.  If the feed lines
    up better when shifted than it does as published, its clock is wrong (a timezone
    or DST fault) and the caller must not trust it, even if the zero-offset score on
    its own would have passed.

    Returns (hits_at_zero_offset, comparable_programmes, best_offset_hours).
    """
    if not baseline:
        return 0, 0, 0
    first, last = baseline[0]["start"], baseline[-1]["stop"]
    window = [e for e in extended if first <= e["start"] < last]
    scores: dict[int, tuple[int, int]] = {}
    for shift in (0, -2, -1, 1, 2):
        hits = total = 0
        for entry in window:
            moment = entry["start"] + shift * 3600
            match = next((p for p in baseline if p["start"] <= moment < p["stop"]), None)
            if not match:
                continue
            total += 1
            if _titles_agree(entry["title"], match.get("title", "")):
                hits += 1
        scores[shift] = (hits, total)
    best_shift = max(scores, key=lambda s: (scores[s][0] / scores[s][1]) if scores[s][1] else 0.0)
    hits, total = scores[0]
    return hits, total, best_shift


def merge_into_payload(payload: dict) -> dict:
    """Merge the extended feed into an HDHomeRun guide payload, in place.

    Returns a stats dict describing what happened. The payload is left untouched
    on any failure, so the caller always ends up with at least the HDHomeRun guide.
    """
    stats: dict = {"ok": False, "enabled": ENABLED, "url": FEED_URL, "source": "tvpassport.com",
                   "checked_at": int(time.time()), "channels_extended": 0, "programmes_added": 0}
    if not ENABLED:
        stats["error"] = "disabled by CINEVAULT_EPG_EXTEND_ENABLED"
        return stats

    channel_map = load_map()
    allowed = set((channel_map.get("channels") or {}).keys())
    if not allowed:
        stats["error"] = f"no verified channel map at {MAP_FILE}"
        return stats

    try:
        feed = _parse_feed(_fetch(FEED_URL))
    except Exception as exc:
        stats["error"] = f"fetch/parse failed: {exc}"
        return stats

    if not feed:
        stats["error"] = "feed contained no usable programmes"
        return stats

    now = int(time.time())
    horizon = now + int(MAX_HORIZON_DAYS * 86400)
    newest = max((entry["stop"] for entries in feed.values() for entry in entries), default=0)
    if newest < now + 3600:
        stats["error"] = "feed is stale (nothing scheduled in the future)"
        return stats

    programmes = payload.setdefault("programmes", {})
    accepted: dict[str, list[dict]] = {}
    rejected: dict[str, str] = {}
    considered = 0

    for number, entries in feed.items():
        if number not in allowed:
            rejected[number] = "not in verified channel map"
            continue
        baseline = programmes.get(number)
        if not baseline:
            rejected[number] = "no HDHomeRun programmes to validate against"
            continue
        considered += 1
        hits, total, best_shift = _overlap_agreement(entries, baseline)
        if total < MIN_OVERLAP_SAMPLES:
            rejected[number] = f"only {total} overlapping programmes to validate"
            continue
        rate = hits / total
        if best_shift != 0:
            rejected[number] = f"aligns better at {best_shift:+d}h offset (timezone fault)"
            continue
        if rate < MIN_OVERLAP_RATE:
            rejected[number] = f"overlap agreement {rate:.0%} below threshold"
            continue
        baseline_end = max(p["stop"] for p in baseline)
        # De-duplicate against the HDHomeRun guide and within the feed itself. The
        # source lists a programme that straddles its own page boundary on both of
        # the days it touches, so the same airing can arrive twice.
        seen = {(p["start"], p["stop"], _norm_title(p.get("title", ""))) for p in baseline}
        additions = []
        for entry in entries:
            if entry["start"] < baseline_end or entry["stop"] > horizon:
                continue
            key = (entry["start"], entry["stop"], _norm_title(entry["title"]))
            if key in seen:
                continue
            seen.add(key)
            additions.append(entry)
        if additions:
            accepted[number] = additions

    if considered and (len(accepted) / considered) < MIN_CHANNELS_ACCEPTED_RATE:
        stats["error"] = (f"only {len(accepted)}/{considered} channels agreed with the "
                          f"HDHomeRun guide; feed rejected")
        stats["rejected"] = rejected
        return stats

    added = 0
    for number, additions in accepted.items():
        target = programmes[number]
        target.extend(additions)
        target.sort(key=lambda entry: entry["start"])
        added += len(additions)

    stats.update(ok=True, channels_considered=considered, channels_extended=len(accepted),
                 programmes_added=added, horizon_ts=horizon,
                 feed_latest_stop=newest, rejected=rejected)
    payload["programme_count"] = sum(len(v) for v in programmes.values())
    return stats


def coverage(payload: dict) -> dict:
    """Summarise how far the merged guide reaches, for reporting and the admin API."""
    now = int(time.time())
    per = {}
    for number, entries in (payload.get("programmes") or {}).items():
        if not entries:
            continue
        latest = max(e["stop"] for e in entries)
        per[number] = round((latest - now) / 86400, 2)
    if not per:
        return {"channels": 0}
    values = sorted(per.values())
    return {
        "channels": len(per),
        "min_days": values[0],
        "median_days": values[len(values) // 2],
        "max_days": values[-1],
    }
