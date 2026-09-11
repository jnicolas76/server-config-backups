#!/usr/bin/env python3
"""Unit/integration tests for virtual_channels.py and genre_catalog.py.

Run with: python3 -m unittest test_virtual_channels -v

These tests never touch the live CineVault database or ffprobe a real
process; virtual_channels.DB_PATH is pointed at a throwaway temp file per
test and _ffprobe_raw is monkeypatched to a deterministic stub, so the
scheduling algorithm's determinism/dedup/ordering guarantees can be checked
without any dependency on the production host.
"""
import datetime
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import genre_catalog
import virtual_channels as vc


class FakeHandler:
    """Minimal stand-in for CombinedHandler, just enough to exercise
    watch_channel()/handle_get() without a real HTTP connection."""

    def __init__(self, path):
        self.path = path
        self.rendered = None
        self.rendered_status = None
        self.redirected_to = None
        self.errors = []

    def render_html(self, body, status=200):
        self.rendered = body
        self.rendered_status = status
        return ("rendered", status)

    def redirect(self, target, cookie=None, clear_cookie=False):
        self.redirected_to = target
        return ("redirect", target)

    def send_error(self, code, message=""):
        self.errors.append((code, message))
        return ("error", code)


class BarkerPresentationTests(unittest.TestCase):
    def test_episode_codes_are_spoken_naturally(self):
        self.assertEqual(vc._spoken_episode("S01E01"), "season 1, episode 1")
        self.assertEqual(vc._spoken_episode("S1E09 - Pilot"), "season 1, episode 9 - Pilot")
        self.assertEqual(vc._spoken_episode("S03"), "season 3")

    def test_combined_barker_has_no_native_playback_controls(self):
        self.assertIn('id="channel" autoplay playsinline', vc.COMBINED_BARKER_PAGE)
        self.assertNotIn('id="channel" controls', vc.COMBINED_BARKER_PAGE)

    def test_combined_barker_is_embedded_not_a_separate_guide_destination(self):
        page = vc._guide_page("movie")
        self.assertNotIn('href="/vchannels/barker"', page)
        self.assertIn('/api/vchannels/barker/video', page)

    def test_program_click_selects_details_including_now_playing(self):
        page = vc._guide_page("movie")
        self.assertIn("renderDetails(p,b.dataset.channel)", page)
        self.assertNotIn("b.classList.contains('now')", page)

    def test_channel_identity_is_the_explicit_live_tuning_target(self):
        page = vc._guide_page("movie")
        self.assertIn('class="channel-name" href="/watch/vchannel/${ch.id}"', page)
        self.assertIn('aria-label="Watch ${esc(ch.name)} live"', page)
        self.assertIn("channel-logos-sprite-20260910.png", page)
        self.assertIn("channel-logo-simpsons-clean.png", page)

    def test_guide_switch_uses_cache_prefetch_and_cancels_stale_requests(self):
        page = vc._guide_page("movie")
        self.assertIn("const guideCache=new Map()", page)
        self.assertIn("new AbortController()", page)
        self.assertIn("prefetchOtherGuide", page)
        self.assertNotIn("previews.teardownAll().finally(loadGuide)", page)

    def test_guide_navigation_does_not_wait_for_preview_stop(self):
        page = vc._guide_page("movie")
        self.assertIn("previews.teardownAll();queueMicrotask(go)", page)


def make_file(tmp: Path, name: str) -> Path:
    p = tmp / name
    p.write_bytes(b"x")
    return p


class FakeMovieItem:
    def __init__(self, id_, title, path):
        self.id = id_
        self.title = title
        self.path = path
        self.rel_path = f"{title}/{title}.mp4"
        self.size = 1000


class FakeMovieApp:
    def __init__(self, items, metadata):
        self.movie_index = SimpleNamespace(items=items)
        self._metadata = metadata

    def metadata_for(self, item):
        return self._metadata.get(item.title, {})

    def poster_url_for(self, item):
        return ""

    def stable_asset_key(self, item):
        return f"asset:{item.title.lower()}"


class FakeEpisode:
    def __init__(self, id_, path, season, episode):
        self.id = id_
        self.path = path
        self._season = season
        self._episode = episode


class FakeSeason:
    def __init__(self, episodes):
        self.episodes = episodes


class FakeShow:
    def __init__(self, id_, title, seasons):
        self.id = id_
        self.title = title
        self.seasons = seasons


class FakeTvApp:
    def __init__(self, shows, metadata):
        self.tv_index = SimpleNamespace(shows=shows)
        self._metadata = metadata

    def metadata_for(self, show):
        return self._metadata.get(show.title, {})

    def season_episode_numbers(self, ep):
        return ep._season, ep._episode

    def episode_metadata(self, metadata, ep):
        key = f"S{ep._season:02d}E{ep._episode:02d}"
        return (metadata.get("episodes") or {}).get(key, {})


class VirtualChannelsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.db_path = self.tmp / "test.db"
        self._orig_db_path = vc.DB_PATH
        vc.DB_PATH = self.db_path
        self._orig_ffprobe = vc._ffprobe_raw
        vc._ffprobe_raw = lambda path: (5400.0, "eng")
        vc.STARTED = False
        vc.init_schema()
        vc.ensure_channel_defs()

    def tearDown(self):
        vc.DB_PATH = self._orig_db_path
        vc._ffprobe_raw = self._orig_ffprobe
        self._tmpdir.cleanup()

    # -- genre normalization -------------------------------------------------
    def test_genre_alias_merges_without_losing_original(self):
        self.assertEqual(genre_catalog.canonical_genre("Sci-Fi & Fantasy"), "Science Fiction")
        self.assertEqual(genre_catalog.canonical_genre("Action & Adventure"), "Action")
        self.assertEqual(genre_catalog.canonical_genre("Science Fiction"), "Science Fiction")
        self.assertEqual(genre_catalog.canonical_genre(""), "")

    def test_missing_genre_titles_excluded_from_tiles(self):
        items = [FakeMovieItem(1, "NoGenre", make_file(self.tmp, "a.mp4"))]
        metadata = {"NoGenre": {"title": "NoGenre", "vote_average": 5.0}}
        app = FakeMovieApp(items, metadata)
        tiles = genre_catalog.movie_genre_tiles(app)
        self.assertEqual(tiles, [])

    # -- eligibility heuristics -----------------------------------------------
    def test_film_noir_requires_crime_plus_dark_genre(self):
        self.assertTrue(vc.film_noir_eligible({"Crime", "Thriller"}))
        self.assertTrue(vc.film_noir_eligible({"Crime", "Drama"}))
        self.assertFalse(vc.film_noir_eligible({"Crime", "Comedy"}))
        self.assertFalse(vc.film_noir_eligible({"Comedy"}))

    def test_international_uses_real_embedded_audio_tag_not_fabricated(self):
        path = make_file(self.tmp, "intl.mp4")
        self.assertFalse(vc.international_eligible(path))  # not probed yet -> excluded, never guessed
        vc.store_probe(str(path), 5000, "fre")
        self.assertTrue(vc.international_eligible(path))
        vc.store_probe(str(path), 5000, "eng")
        self.assertFalse(vc.international_eligible(path))
        vc.store_probe(str(path), 5000, "")
        self.assertFalse(vc.international_eligible(path))

    # -- movie schedule generation --------------------------------------------
    def _movie_app_with_pool(self, genre, n, tmp):
        items, metadata = [], {}
        for i in range(n):
            title = f"{genre}Movie{i}"
            items.append(FakeMovieItem(i, title, make_file(tmp, f"{title}.mp4")))
            metadata[title] = {"title": title, "genres": [genre], "vote_average": 5.0 + i}
        return FakeMovieApp(items, metadata)

    def test_no_duplicate_movie_same_calendar_day_across_lineup(self):
        movie_app = self._movie_app_with_pool("Action", 3, self.tmp)
        tv_app = FakeTvApp([], {})
        today = datetime.datetime.now(ZoneInfo("America/Denver")).date()
        defs = vc.channels("movie")
        pools = {ch["id"]: vc.eligible_movie_pool(ch["genre_key"], movie_app) for ch in defs}
        vc.generate_movie_day(today, defs, pools)
        c = vc.connect()
        try:
            rows = c.execute(
                "SELECT stable_key FROM vchannel_schedule WHERE local_date=? AND media_kind='movie'",
                (today.isoformat(),),
            ).fetchall()
        finally:
            c.close()
        keys = [r[0] for r in rows]
        self.assertEqual(len(keys), len(set(keys)), "same movie scheduled twice on the same calendar day across the lineup")

    def test_schedule_generation_is_idempotent_across_reruns(self):
        movie_app = self._movie_app_with_pool("Action", 5, self.tmp)
        tv_app = FakeTvApp([], {})
        vc.generate_horizon(movie_app, tv_app, horizon_days=2)
        c = vc.connect()
        try:
            first = [dict(r) for r in c.execute("SELECT channel_id,start_ts,stop_ts,stable_key FROM vchannel_schedule ORDER BY channel_id,start_ts").fetchall()]
        finally:
            c.close()
        # Re-running the generator must not reshuffle already-persisted rows.
        vc.generate_horizon(movie_app, tv_app, horizon_days=2)
        c = vc.connect()
        try:
            second = [dict(r) for r in c.execute("SELECT channel_id,start_ts,stop_ts,stable_key FROM vchannel_schedule ORDER BY channel_id,start_ts").fetchall()]
        finally:
            c.close()
        self.assertEqual(first, second)

    def test_movie_uses_real_probed_duration_not_fixed_two_hours(self):
        vc._ffprobe_raw = lambda path: (3723.0, "eng")  # 1h02m03s, deliberately not 2h
        movie_app = self._movie_app_with_pool("Action", 2, self.tmp)
        tv_app = FakeTvApp([], {})
        today = datetime.datetime.now(ZoneInfo("America/Denver")).date()
        defs = vc.channels("movie")
        pools = {ch["id"]: vc.eligible_movie_pool(ch["genre_key"], movie_app) for ch in defs}
        vc.generate_movie_day(today, defs, pools)
        c = vc.connect()
        try:
            row = c.execute("SELECT start_ts,stop_ts FROM vchannel_schedule WHERE local_date=? LIMIT 1", (today.isoformat(),)).fetchone()
        finally:
            c.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[1] - row[0], 3723)

    # -- TV chronological progression -----------------------------------------
    def _tv_app_with_show(self):
        eps = [
            FakeEpisode(1, make_file(self.tmp, "s1e1.mp4"), 1, 1),
            FakeEpisode(2, make_file(self.tmp, "s1e2.mp4"), 1, 2),
            FakeEpisode(3, make_file(self.tmp, "s2e1.mp4"), 2, 1),
        ]
        show = FakeShow(1, "TestShow", {"s1": FakeSeason(eps)})
        metadata = {
            "TestShow": {
                "genres": ["Drama"],
                "vote_average": 8.0,
                "episodes": {
                    "S01E01": {"runtime": 30, "vote_average": 8.0},
                    "S01E02": {"runtime": 30, "vote_average": 8.0},
                    "S02E01": {"runtime": 30, "vote_average": 8.0},
                },
            }
        }
        return FakeTvApp([show], metadata)

    def test_episodes_air_in_chronological_order_never_backward(self):
        tv_app = self._tv_app_with_show()
        movie_app = FakeMovieApp([], {})
        vc.generate_horizon(movie_app, tv_app, horizon_days=14)
        c = vc.connect()
        try:
            rows = c.execute(
                "SELECT episode_index FROM vchannel_schedule WHERE media_kind='episode' ORDER BY start_ts"
            ).fetchall()
        finally:
            c.close()
        indices = [r[0] % 3 for r in rows]  # only 3 episodes exist; progression wraps via modulo
        # Chronological & monotonic before any wrap: each successive airing's raw
        # next_index must strictly increase (never jumps backward).
        raw = [r[0] for r in rows]
        self.assertEqual(raw, sorted(raw))

    # -- seek-offset math -------------------------------------------------------
    def test_seek_offset_computation(self):
        start_ts = 1000
        stop_ts = 1000 + 3600
        now = 1000 + 900
        offset = max(0, now - start_ts)
        self.assertEqual(offset, 900)
        scheduled_len = max(1, stop_ts - start_ts)
        self.assertLess(offset, scheduled_len - 1)

    def test_seek_offset_past_end_triggers_advance(self):
        start_ts, stop_ts = 1000, 1600
        now = 1650  # past stop_ts
        offset = max(0, now - start_ts)
        scheduled_len = max(1, stop_ts - start_ts)
        self.assertGreaterEqual(offset, scheduled_len - 1)

    # -- Watch Live regression (KeyError: 'number' crash) ----------------------
    def _insert_now_playing_movie_row(self, channel, item, movie_app, subtitle="", rating=5.0):
        stable_key = movie_app.stable_asset_key(item)
        now = int(time.time())
        c = vc.connect()
        try:
            c.execute(
                "INSERT INTO vchannel_schedule(channel_id,start_ts,stop_ts,media_kind,stable_key,show_key,"
                "episode_index,title,subtitle,rating,local_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (channel["id"], now - 60, now + 3600, "movie", stable_key, None, None,
                 item.title, subtitle, rating, "2026-01-01", vc.now_text()),
            )
            c.commit()
        finally:
            c.close()

    def test_watch_live_tunes_now_playing_program_without_crashing(self):
        # Regression test for a live bug: watch_channel() read channel['number']
        # but vchannel_defs' column (and channel_by_id()'s dict) is
        # 'channel_number' -- every "Watch Live" click on a program that was
        # actually airing raised an unhandled KeyError and killed the HTTP
        # connection with no response ever sent to the browser.
        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)

        handler = FakeHandler(f"/watch/vchannel/{channel['id']}")
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": f"/play/{item_id}", "is_hls": False}
        vc.watch_channel(handler, {"id": 1, "is_admin": 1}, channel["id"], movie_app, tv_app, resolve_source_fn)

        self.assertEqual(handler.errors, [], "watch_channel must not error out for a currently-live program")
        self.assertIsNotNone(handler.rendered, "watch_channel must render the tune-in player page")
        self.assertIn(channel["channel_number"], handler.rendered)
        self.assertIn(item.title, handler.rendered)
        self.assertIn("/play/", handler.rendered)  # Direct source wired into the player

    def test_watch_live_dispatches_through_handle_get_without_crashing(self):
        # Same regression, exercised through the real dispatch entry point
        # (handle_get -> watch_channel) the way the guide's Watch Live link
        # actually reaches it.
        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)

        handler = FakeHandler(f"/watch/vchannel/{channel['id']}")
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": f"/play/{item_id}", "is_hls": False}
        result = vc.handle_get(handler, {"id": 1, "is_admin": 1}, handler.path, movie_app, tv_app, resolve_source_fn)

        self.assertIsNot(result, False)
        self.assertEqual(handler.errors, [])
        self.assertIsNotNone(handler.rendered)
        self.assertIn(channel["channel_number"], handler.rendered)

    def test_watch_live_holding_page_does_not_touch_channel_number(self):
        # No live program right now (channel is on a holding gap): must still
        # render cleanly, exercising the code path that does NOT reference
        # channel['number']/channel['channel_number'] at all.
        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        handler = FakeHandler(f"/watch/vchannel/{channel['id']}")
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": "", "is_hls": False}
        vc.watch_channel(handler, {"id": 1, "is_admin": 1}, channel["id"], movie_app, tv_app, resolve_source_fn)
        self.assertEqual(handler.errors, [])
        self.assertIsNotNone(handler.rendered)
        self.assertIn("Off Air", handler.rendered)

    def test_play_from_beginning_href_uses_normal_player_route(self):
        # Play from Beginning must always resolve to the same /player/<kind>/<id>
        # route ordinary playback uses -- no parallel playback code path.
        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)
        handler = FakeHandler(f"/watch/vchannel/{channel['id']}")
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": f"/play/{item_id}", "is_hls": False}
        vc.watch_channel(handler, {"id": 1, "is_admin": 1}, channel["id"], movie_app, tv_app, resolve_source_fn)
        self.assertIn(f"/player/movie/{item.id}", handler.rendered)

    # -- guide payload details panel fields -------------------------------------
    def test_guide_payload_includes_overview_and_poster_for_details_panel(self):
        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        movie_app._metadata["ActionMovie0"]["overview"] = "A daring heist."
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)
        tv_app = FakeTvApp([], {})
        payload = vc.guide_payload("movie", {"from": [str(int(time.time()) - 300)], "hours": ["2"]}, movie_app, tv_app)
        chan = next(c for c in payload["channels"] if c["id"] == channel["id"])
        live = next(p for p in chan["programmes"] if not p.get("holding"))
        self.assertEqual(live["overview"], "A daring heist.")
        self.assertEqual(live["play_href"], f"/player/movie/{item.id}")
        self.assertIn("poster", live)

    # -- seamless (same-document) Up Next transition / fullscreen fix ----------
    # Root cause of the reported bug: navigating with location.href/replace
    # always exits the browser's native Fullscreen API on the <video>
    # element, and per spec that cannot be silently re-requested without a
    # fresh user gesture. tune_payload()/_resolve_tune() exist so the tune
    # page can fetch the next program's source in place instead of
    # navigating - these tests cover that these two entry points agree with
    # watch_channel() (the original HTML page) on every field the seamless
    # JS transition depends on.
    def test_tune_payload_matches_watch_channel_for_the_same_live_program(self):
        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": f"/play/{item_id}", "is_hls": False}

        handler = FakeHandler(f"/watch/vchannel/{channel['id']}")
        vc.watch_channel(handler, {"id": 1, "is_admin": 1}, channel["id"], movie_app, tv_app, resolve_source_fn)

        payload = vc.tune_payload(channel["id"], movie_app, tv_app, resolve_source_fn, None)
        self.assertTrue(payload["ok"])
        self.assertIn(item.title, handler.rendered)
        self.assertIn(payload["source"], handler.rendered)
        self.assertEqual(payload["play_beginning_href"], f"/player/movie/{item.id}")
        # advance_after must be exactly the live program's own start_ts, since
        # the tune page's JS passes this value straight back in as
        # advance_after to reach "the program after this one" once it ends.
        c = vc.connect()
        try:
            row = c.execute(
                "SELECT start_ts FROM vchannel_schedule WHERE channel_id=? ORDER BY start_ts DESC LIMIT 1",
                (channel["id"],),
            ).fetchone()
        finally:
            c.close()
        self.assertEqual(payload["advance_after"], row[0])

    def test_tune_payload_advance_after_resolves_the_next_program(self):
        # Simulates the automatic Up Next transition: two back-to-back movies
        # on the same channel, fetch with advance_after=<first program's
        # start_ts> and confirm it resolves to the SECOND program starting at
        # offset 0 (a fresh join), not a recomputed "what's live now".
        movie_app = self._movie_app_with_pool("Action", 2, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        first, second = movie_app.movie_index.items
        now = int(time.time())
        c = vc.connect()
        try:
            c.execute(
                "INSERT INTO vchannel_schedule(channel_id,start_ts,stop_ts,media_kind,stable_key,show_key,"
                "episode_index,title,subtitle,rating,local_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (channel["id"], now - 3600, now - 10, "movie", movie_app.stable_asset_key(first), None, None,
                 first.title, "", 5.0, "2026-01-01", vc.now_text()),
            )
            c.execute(
                "INSERT INTO vchannel_schedule(channel_id,start_ts,stop_ts,media_kind,stable_key,show_key,"
                "episode_index,title,subtitle,rating,local_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (channel["id"], now - 10, now + 3600, "movie", movie_app.stable_asset_key(second), None, None,
                 second.title, "", 5.0, "2026-01-01", vc.now_text()),
            )
            c.commit()
        finally:
            c.close()
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": f"/play/{item_id}", "is_hls": False}
        payload = vc.tune_payload(channel["id"], movie_app, tv_app, resolve_source_fn, None, advance_after=str(now - 3600))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["offset"], 0, "joining the program right after the just-finished one must start at 0, not a recomputed live offset")
        self.assertIn(second.title, handler_title := payload["title"])
        self.assertNotIn(first.title, handler_title)

    def test_tune_payload_not_found_for_unknown_channel(self):
        movie_app = FakeMovieApp([], {})
        tv_app = FakeTvApp([], {})
        resolve_source_fn = lambda kind, item_id, path, mode: {"source": "", "is_hls": False}
        payload = vc.tune_payload(999999, movie_app, tv_app, resolve_source_fn, None)
        self.assertEqual(payload, {"ok": False, "reason": "not_found"})

    # -- audio-track callback threading (older 4-arg callbacks still work) ----
    def test_resolve_source_fn_called_with_four_args_when_no_audio_requested(self):
        # A resolve_source_fn written against the pre-audio-selector contract
        # (playback_mode only, no audio_index) must keep working unchanged
        # when nobody asks for a specific audio track - this is the common
        # case for every existing caller/test in this suite.
        received = {}

        def resolve_source_fn(kind, item_id, path, mode):
            received["args"] = (kind, item_id, path, mode)
            return {"source": f"/play/{item_id}", "is_hls": False}

        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)
        payload = vc.tune_payload(channel["id"], movie_app, tv_app, resolve_source_fn, None)
        self.assertTrue(payload["ok"])
        self.assertIn("args", received)

    def test_resolve_source_fn_receives_resolved_audio_index_when_requested(self):
        def audio_markup_fn(path, requested_index):
            return "<select id='audioSelect'></select>", 1  # pretend order=1 is what got resolved

        captured = {}

        def resolve_source_fn(kind, item_id, path, mode, audio_index=None):
            captured["audio_index"] = audio_index
            return {"source": f"/play/{item_id}", "is_hls": True}

        movie_app = self._movie_app_with_pool("Action", 1, self.tmp)
        tv_app = FakeTvApp([], {})
        channel = vc.channels("movie")[0]
        item = movie_app.movie_index.items[0]
        self._insert_now_playing_movie_row(channel, item, movie_app)
        payload = vc.tune_payload(
            channel["id"], movie_app, tv_app, resolve_source_fn, None,
            audio_index=1, audio_markup_fn=audio_markup_fn,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(captured["audio_index"], 1)
        self.assertIn("audioSelect", payload["audio_control_html"])


    # -- five additional TV channels (T11-T15), added 2026-09-09 -------------
    def test_tv_channel_roster_is_fifteen_and_preserves_the_original_ten(self):
        self.assertEqual(len(vc.TV_CHANNELS), 15)
        self.assertEqual(len(vc.MOVIE_CHANNELS), 10, "movie roster must be untouched by this change")
        original_ten = [
            ("t1", "T1", "Drama", "Drama"), ("t2", "T2", "Comedy", "Comedy"),
            ("t3", "T3", "Science Fiction", "Science Fiction"), ("t4", "T4", "Action & Adventure", "Action"),
            ("t5", "T5", "Crime", "Crime"), ("t6", "T6", "Mystery", "Mystery"),
            ("t7", "T7", "Animation", "Animation"), ("t8", "T8", "Family & Kids", "Family"),
            ("t9", "T9", "Documentary", "Documentary"), ("t10", "T10", "Western", "Western"),
        ]
        self.assertEqual(vc.TV_CHANNELS[:10], original_ten, "existing ten TV channels must be preserved exactly, in order")
        new_five = vc.TV_CHANNELS[10:]
        self.assertEqual([c[0] for c in new_five], ["t11", "t12", "t13", "t14", "t15"])
        self.assertEqual([c[1] for c in new_five], ["T11", "T12", "T13", "T14", "T15"])
        self.assertEqual([c[3] for c in new_five], ["Knowledge", "TheSimpsons", "TheZone", "Nostalgia", "Sitcom"])
        slugs = [c[0] for c in vc.TV_CHANNELS]
        numbers = [c[1] for c in vc.TV_CHANNELS]
        self.assertEqual(len(slugs), len(set(slugs)), "channel slugs must be unique")
        self.assertEqual(len(numbers), len(set(numbers)), "channel numbers must be unique")

    def test_schedule_stats_reports_dynamic_channel_totals(self):
        stats = vc.schedule_stats()
        self.assertEqual(stats["tv"]["total"], 15)
        self.assertEqual(stats["movie"]["total"], 10)

    def test_title_normalization_matches_the_and_punctuation_variants_not_substrings(self):
        self.assertTrue(vc._title_matches_any("The Simpsons", vc.SIMPSONS_TITLES))
        self.assertTrue(vc._title_matches_any("simpsons", vc.SIMPSONS_TITLES))
        self.assertTrue(vc._title_matches_any("  THE   SIMPSONS!! ", vc.SIMPSONS_TITLES))
        # Must never substring-match: a real library has "Super Friends" /
        # "Garfield and Friends" that must NOT satisfy a "Friends" priority rule.
        self.assertFalse(vc._title_matches_any("Super Friends", {"Friends"}))
        self.assertFalse(vc._title_matches_any("Garfield and Friends", {"Friends"}))
        self.assertTrue(vc._title_matches_any("Friends", {"Friends"}))

    # -- Knowledge (T11): named anchors + Documentary genre fallback ---------
    def test_knowledge_eligible_matches_named_shows_and_documentary_genre(self):
        self.assertTrue(vc.knowledge_eligible(set(), "How It's Made"))
        self.assertTrue(vc.knowledge_eligible(set(), "Modern Marvels"))
        self.assertTrue(vc.knowledge_eligible({"Documentary"}, "Ancient Aliens"))
        self.assertFalse(vc.knowledge_eligible({"Comedy"}, "Seinfeld"))

    # -- The Simpsons (T12): exactly one show, nothing else ------------------
    def test_simpsons_eligible_is_exact_show_only(self):
        self.assertTrue(vc.simpsons_eligible("The Simpsons"))
        self.assertFalse(vc.simpsons_eligible("The Simpsons Movie"))
        self.assertFalse(vc.simpsons_eligible("Simpsons Comics"))
        self.assertFalse(vc.simpsons_eligible("Family Guy"))

    # -- The Zone (T13): named anthology anchors + SciFi-and-Mystery combo ---
    def test_zone_eligible_matches_named_shows_and_scifi_mystery_combo(self):
        self.assertTrue(vc.zone_eligible(set(), "The Twilight Zone"))
        self.assertTrue(vc.zone_eligible(set(), "The Twilight Zone 1959"))
        self.assertTrue(vc.zone_eligible(set(), "Twilight Zone 2019"))
        self.assertTrue(vc.zone_eligible(set(), "The Outer Limits"))
        self.assertTrue(vc.zone_eligible(set(), "The Outer Limit 1995"))
        self.assertTrue(vc.zone_eligible({"Science Fiction", "Mystery", "Drama"}, "Fringe"))
        # Science Fiction alone (no Mystery) must not qualify - that's T3's
        # whole library, not a curated anthology neighborhood.
        self.assertFalse(vc.zone_eligible({"Science Fiction", "Action"}, "Star Trek: The Next Generation"))
        self.assertFalse(vc.zone_eligible({"Mystery", "Crime"}, "Murder, She Wrote"))

    # -- Nostalgia (T14): named anchors + 1960-1999 release year -------------
    def test_nostalgia_eligible_matches_named_shows_and_decade_window(self):
        self.assertTrue(vc.nostalgia_eligible(set(), "Knight Rider", {"year": "1982"}))
        self.assertTrue(vc.nostalgia_eligible({"Drama"}, "Little House on the Prairie", {"year": "1974"}))
        self.assertFalse(vc.nostalgia_eligible({"Drama"}, "Stranger Things", {"year": "2016"}))
        self.assertFalse(vc.nostalgia_eligible({"Comedy"}, "Some Show", {"year": ""}))

    # -- Sitcom (T15): named anchors + Comedy minus Animation ----------------
    def test_sitcom_eligible_matches_named_shows_and_live_action_comedy(self):
        self.assertTrue(vc.sitcom_eligible(set(), "Seinfeld"))
        self.assertTrue(vc.sitcom_eligible({"Comedy"}, "Malcolm in the Middle"))
        self.assertFalse(vc.sitcom_eligible({"Comedy", "Animation"}, "Garfield and Friends"))
        self.assertFalse(vc.sitcom_eligible({"Drama"}, "Some Drama"))

    # -- integration: pool building must not leave any of the five empty -----
    def _multi_show_tv_app(self):
        def show(id_, title, genres, year, n_eps=2):
            eps = [FakeEpisode(id_ * 100 + i, make_file(self.tmp, f"{title}-{i}.mp4"), 1, i + 1) for i in range(n_eps)]
            return FakeShow(id_, title, {"s1": FakeSeason(eps)}), {
                "genres": genres, "vote_average": 6.0, "year": year,
                "episodes": {f"S01E{i+1:02d}": {"runtime": 22, "vote_average": 6.0} for i in range(n_eps)},
            }
        shows, metadata = [], {}
        for id_, title, genres, year in [
            (1, "How It's Made", ["Documentary"], "2001"),
            (2, "Ancient Aliens", ["Documentary"], "2010"),
            (3, "The Simpsons", ["Family", "Animation", "Comedy"], "1989"),
            (4, "Fringe", ["Science Fiction", "Mystery", "Drama"], "2008"),
            (5, "Knight Rider", ["Action", "Science Fiction", "Crime"], "1982"),
            (6, "Seinfeld", ["Comedy"], "1989"),
            (7, "Garfield and Friends", ["Animation", "Comedy"], "1988"),  # Sitcom must exclude this (Animation)
            (8, "Modern Drama Show", ["Drama"], "2020"),  # matches nothing new (control)
        ]:
            s, m = show(id_, title, genres, year)
            shows.append(s)
            metadata[title] = m
        return FakeTvApp(shows, metadata)

    def test_build_all_pools_populates_all_five_new_channels_without_bleeding_into_wrong_ones(self):
        tv_app = self._multi_show_tv_app()
        movie_app = FakeMovieApp([], {})
        movie_defs, tv_defs = vc.channels("movie"), vc.channels("tv")
        _movie_pools, tv_pools = vc._build_all_pools(movie_app, tv_app, movie_defs, tv_defs)
        by_slug = {ch["slug"]: ch["id"] for ch in tv_defs}

        knowledge = tv_pools[by_slug["t11"]]
        self.assertEqual(set(knowledge.keys()), {"How It's Made", "Ancient Aliens"})

        simpsons = tv_pools[by_slug["t12"]]
        self.assertEqual(set(simpsons.keys()), {"The Simpsons"})

        zone = tv_pools[by_slug["t13"]]
        self.assertEqual(set(zone.keys()), {"Fringe"})

        nostalgia = tv_pools[by_slug["t14"]]
        self.assertEqual(set(nostalgia.keys()), {"The Simpsons", "Knight Rider", "Seinfeld", "Garfield and Friends"})

        sitcom = tv_pools[by_slug["t15"]]
        self.assertEqual(set(sitcom.keys()), {"Seinfeld"}, "Sitcom must include live-action Comedy but exclude Animation")

        # No empty channel when qualifying media exists for it.
        for slug in ("t11", "t12", "t13", "t14", "t15"):
            self.assertTrue(tv_pools[by_slug[slug]], f"{slug} must not be empty given qualifying local media")

    def test_priority_named_shows_get_a_rating_floor_boost(self):
        tv_app = self._multi_show_tv_app()
        movie_app = FakeMovieApp([], {})
        tv_defs = vc.channels("tv")
        _movie_pools, tv_pools = vc._build_all_pools(movie_app, tv_app, vc.channels("movie"), tv_defs)
        by_slug = {ch["slug"]: ch["id"] for ch in tv_defs}
        knowledge = tv_pools[by_slug["t11"]]
        self.assertGreaterEqual(knowledge["How It's Made"]["rating"], vc.PRIORITY_RATING_FLOOR)

    def test_generate_horizon_schedules_all_five_new_channels_and_preserves_chronological_order(self):
        tv_app = self._multi_show_tv_app()
        movie_app = FakeMovieApp([], {})
        vc.generate_horizon(movie_app, tv_app, horizon_days=3)
        tv_defs = vc.channels("tv")
        by_slug = {ch["slug"]: ch["id"] for ch in tv_defs}
        c = vc.connect()
        try:
            for slug in ("t11", "t12", "t13", "t14", "t15"):
                rows = c.execute(
                    "SELECT COUNT(*) FROM vchannel_schedule WHERE channel_id=?", (by_slug[slug],)
                ).fetchone()[0]
                self.assertGreater(rows, 0, f"{slug} produced no scheduled programs at all")
            # Sitcom (Seinfeld only, in this fixture) must air its episodes in
            # strict, never-backward season/episode order - the same
            # universal guarantee test_episodes_air_in_chronological_order_
            # never_backward already covers for the original ten channels,
            # re-checked explicitly against this new channel.
            sitcom_rows = c.execute(
                "SELECT episode_index FROM vchannel_schedule WHERE channel_id=? ORDER BY start_ts",
                (by_slug["t15"],),
            ).fetchall()
        finally:
            c.close()
        raw = [r[0] for r in sitcom_rows]
        self.assertEqual(raw, sorted(raw), "Sitcom channel must never air an earlier episode after a later one")

    def test_admin_page_reports_fifteen_tv_and_ten_movie_channels(self):
        handler = FakeHandler("/admin/vchannels")
        result = vc.admin_page(handler)
        self.assertIsNotNone(result)
        self.assertIn("of 15 channels currently on air", handler.rendered)
        self.assertIn("of 10 channels currently on air", handler.rendered)


if __name__ == "__main__":
    unittest.main()
