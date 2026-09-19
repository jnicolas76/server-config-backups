#!/usr/bin/env python3
"""
render_broadcast_v3.py - "Cinemedia Vault Sports and News" broadcast renderer.
Builds on render_broadcast_v2.py (which fixed the v1 ticker-position bug and
unified the chrome). Reads the SAME data.json produced by broadcast_generator,
plus script_v3.json (same content, rebranded narration/title text - see
broadcast_generator_v3.py). No network calls beyond an OPTIONAL logo fetch
that --offline disables entirely.

What changed vs v2:
  - Real photographic backgrounds for NFL/NBA/MLB (football.png, basketball.jpg,
    baseball.jpg - sourced/generated separately, see IMAGE_SOURCES.txt) instead
    of flat procedural colors / a looping stock video for MLB.
  - Weather background is now the real, colorized continental-US map
    (usa_weather_map.png) with each city's dot/label baked in at its true
    geographic position (see make_weather_map.py); the renderer overlays only
    the DYNAMIC icon + temp/condition at each city's fixed coordinate (CITY_XY)
    instead of a generic two-column list.
  - Ticker: combined into one continuous string (was two time-sliced phases
    that reset every segment, so it never finished a lap) and given a running
    time offset across segments so the scroll is continuous through the whole
    concatenated broadcast instead of restarting at t=0 every ~8-20s. Also
    slowed down (34px/s, was 60).
  - Narration: Kokoro speed=0.85 (was 1.0) for clearer, less rushed delivery.
  - Rebranded chrome text to "CINEMEDIA VAULT SPORTS AND NEWS".

  <venv>/python render_broadcast_v3.py --proof
  <venv>/python render_broadcast_v3.py
"""
import argparse, json, subprocess, sys, urllib.request, wave, hashlib, textwrap
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import citymap
WORK = HERE / "work_v3"; OUT = HERE / "output"; LOGOS = HERE / "logos_cache"; ICONS = HERE / "icons"
BGS = HERE / "backgrounds_v2"; PHOTOS = HERE / "photos_v3"
MODELS = Path("/home/jnicolas/barker-suite/models")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H, FPS = 1280, 720, 30
ACCENT = "0x59a5ff"; NAVY = "0x0b1930"
UA = "CineVaultGenChannel/0.3"
BRAND = "CINEMEDIA VAULT SPORTS AND NEWS"
TTS_SPEED = 0.85

SAFE_X = 80
HEADER_H = 96
HEADER_ACCENT_H = 3
FOOTER_H = 74
CARD_X, CARD_Y, CARD_W, CARD_H = 140, 150, 1000, 420
PANEL_FILL = "0x0b1a33@0.62"

TICKER_FG = "0x0b8a3a"; TICKER_PXPS = 34

# City label positions baked into usa_weather_map.png come from
# citymap.headline_city_px() - the single source of truth both the map baker
# and this renderer use, so the live icon/temp text lands at the same spot
# as the baked dot/label instead of drifting out of sync.
CITY_XY = citymap.headline_city_px(W, H)

def esc(t):
    return (t or "").replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace('"', "")

def wrap(text, width):
    return textwrap.fill(text or "", width=width).split("\n")

def short_date(ts):
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %-d")
    except Exception:
        return ""

def cache_logo(url, offline=False):
    if not url:
        return None
    LOGOS.mkdir(exist_ok=True)
    fn = LOGOS / (hashlib.md5(url.encode()).hexdigest() + ".png")
    if fn.exists() and fn.stat().st_size > 0:
        return fn
    if offline:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15) as r:
            d = r.read()
        fn.write_bytes(d)
        return fn if fn.stat().st_size > 0 else None
    except Exception:
        return None

def kokoro():
    from kokoro_onnx import Kokoro
    return Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))

def synth(k, text, voice, wav):
    import soundfile as sf
    s, sr = k.create(text, voice=voice, speed=TTS_SPEED, lang="en-us")
    sf.write(str(wav), s, sr)
    with wave.open(str(wav)) as w:
        return w.getnframes() / w.getframerate()


def build_crew_intro(k, script, intro_video):
    """Mix the selected crew introduction over the authorized local song."""
    text = (script.get("intro_text") or "").strip()
    if not text:
        return intro_video
    wav = WORK / "intro-crew.wav"
    out = WORK / "intro-with-crew.mp4"
    spoken = synth(k, text, script.get("intro_voice") or "am_michael", wav)
    # The station ID is 20 seconds. Gently accelerate only when a set of long
    # names would otherwise run past the picture; never clip an introduction.
    tempo = max(1.0, min(2.0, spoken / 18.5))
    intro_song = Path("/media/jnicolas/Expansion/Music/Various Artists/Transformers - the Album/01 What I've Done.mp3")
    if not intro_song.is_file():
        raise FileNotFoundError(f"Authorized intro song is missing: {intro_song}")
    af = (f"[2:a]atrim=start=0:duration=20,asetpts=N/SR/TB,volume=0.18,afade=t=out:st=18:d=2[bed];"
          f"[1:a]atempo={tempo:.5f},apad=pad_dur=20,volume=1.0[voice];"
          f"[bed][voice]amix=inputs=2:duration=first:normalize=0[aout]")
    crew_slug = str(script.get("crew_key") or "morning").lower().replace(" crew", "").replace(" ", "-")
    crew_sheet = HERE / "staff-portraits" / "sheets" / f"{crew_slug}.png"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(intro_video), "-i", str(wav), "-ss", "0", "-i", str(intro_song)]
    if crew_sheet.exists():
        vf = "[3:v]scale=360:360,format=rgba,colorchannelmixer=aa=0.96[crew];[0:v][crew]overlay=W-w-54:120:shortest=1[vout]"
        cmd += ["-loop", "1", "-i", str(crew_sheet), "-filter_complex", af + ";" + vf,
                "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    else:
        cmd += ["-filter_complex", af, "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy"]
    cmd += [
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "1",
        "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out

# league -> (file, is_photo). Photos live in PHOTOS/, flat art in BGS/.
LEAGUE_BG = {
    "NFL": ("football.png", True), "NBA": ("basketball.jpg", True), "MLB": ("baseball.jpg", True),
    "NHL": ("nhl.png", False), "Boxing": ("boxing.png", False),
}

def header(fc, draws, last, title):
    fc.append(f"[{last}]drawbox=x=0:y=0:w={W}:h={HEADER_H}:color={NAVY}@0.94:t=fill[hdr]")
    fc.append(f"[hdr]drawbox=x=0:y={HEADER_H}:w={W}:h={HEADER_ACCENT_H}:color={ACCENT}@1.0:t=fill[hdrb]")
    last = "hdrb"
    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(title)}':fontcolor=white:fontsize=36:"
                 f"x=(w-text_w)/2:y={(HEADER_H-36)//2}")
    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(BRAND)}':fontcolor={ACCENT}:fontsize=13:"
                 f"x={SAFE_X}:y={HEADER_H-22}")
    draws.append(f"drawtext=fontfile='{FONT}':text='● LIVE':fontcolor=0xff6b5a:fontsize=14:"
                 f"x={W-SAFE_X-60}:y={HEADER_H-22}")
    return last

def nameplate(fc, draws, last, reporter, role):
    """On-screen name plate for the crew member attributed to this segment
    (staff.txt: 'match the on-screen name plate / lower-third to the selected
    crew member'). Box width is estimated from the text itself so names never
    overflow it, per feedback that earlier boxes didn't fit their contents."""
    if not reporter:
        return last
    label = f"{reporter} — {role}" if role else reporter
    fontsize = 17
    est_w = int(len(label) * fontsize * 0.56) + 28
    box_w = max(140, min(est_w, CARD_W))
    box_x = SAFE_X
    box_y = HEADER_H + HEADER_ACCENT_H + 10
    box_h = 30
    fc.append(f"[{last}]drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:color={NAVY}@0.85:t=fill[npl]")
    fc.append(f"[npl]drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:color={ACCENT}@0.9:t=1[nplb]")
    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(label)}':fontcolor=white:fontsize={fontsize}:"
                 f"x={box_x+14}:y={box_y+6}")
    return "nplb"

def panel(fc, last, x=CARD_X, y=CARD_Y, w=CARD_W, h=CARD_H):
    fc.append(f"[{last}]drawbox=x={x}:y={y}:w={w}:h={h}:color={PANEL_FILL}:t=fill[pnl]")
    fc.append(f"[pnl]drawbox=x={x}:y={y}:w={w}:h={h}:color={ACCENT}@0.8:t=2[pnlb]")
    return "pnlb"

def build(seg, dur, wav, out_mp4, ticker_text, t_offset, offline=False):
    v = seg.get("visual") or {}; tpl = v.get("template", "title"); kind = seg.get("kind", "")
    league = v.get("league")
    inputs = []; fc = []; idx = 0; draws = []

    # ---- background (index 0) ----
    is_photo = False
    if tpl == "sports" and league in LEAGUE_BG:
        fname, is_photo = LEAGUE_BG[league]
        bgfile = (PHOTOS if is_photo else BGS) / fname
    elif tpl == "weather":
        bgfile = HERE / "usa_weather_map.png"
    else:
        bgmap = {"traffic": "traffic.png", "title": "title.png", "news": "news.png", "markets": "news.png"}
        bgfile = BGS / bgmap.get(tpl, "title.png")
    inputs += ["-loop", "1", "-i", str(bgfile)]
    fc.append(f"[0:v]scale={W}:{H},trim=duration={dur:.2f},setsar=1[bg0]")
    last = "bg0"
    if is_photo:
        fc.append(f"[bg0]drawbox=x=0:y=0:w={W}:h={H}:color=black@0.30:t=fill[bg]")
        last = "bg"
    inputs += ["-i", str(wav)]
    audio_idx = idx = 1; idx += 1

    if tpl == "title":
        draws.append(f"drawtext=fontfile='{FONT}':text='{esc(v.get('title',''))}':fontcolor=white:fontsize=58:"
                     f"x=(w-text_w)/2:y=(h-text_h)/2-40")
        if v.get("subtitle"):
            draws.append(f"drawtext=fontfile='{FONT}':text='{esc(v['subtitle'])}':fontcolor={ACCENT}:fontsize=34:"
                         f"x=(w-text_w)/2:y=(h-text_h)/2+40")
        crew = v.get("crew") or {}
        if crew.get("ML") and crew.get("FL"):
            withtext = f"with {crew['ML']} and {crew['FL']}"
            draws.append(f"drawtext=fontfile='{FONT}':text='{esc(withtext)}':fontcolor=0x9fb4d0:fontsize=20:"
                         f"x=(w-text_w)/2:y=(h-text_h)/2+90")

    elif tpl == "weather":
        last = header(fc, draws, last, "NATIONAL WEATHER")
        last = nameplate(fc, draws, last, v.get("reporter"), v.get("reporter_role"))
        by_city = {c["city"]: c for c in v.get("cities", [])}
        for city, (x, y) in CITY_XY.items():
            c = by_city.get(city)
            if not c:
                continue
            ic = ICONS / f"{c.get('icon','cloudy')}.png"
            if ic.exists():
                inputs += ["-i", str(ic)]; fc.append(f"[{idx}:v]scale=32:32[ic{idx}]")
                fc.append(f"[{last}][ic{idx}]overlay={x-16}:{y+10}[b{idx}]"); last = f"b{idx}"; idx += 1
            txt = f"{c.get('temp','')}°  {esc(c.get('cond',''))}"
            draws.append(f"drawtext=fontfile='{FONT}':text='{txt}':fontcolor=0xffe27a:fontsize=17:"
                         f"x={x+20}:y={y+16}:shadowcolor=black@0.8:shadowx=1:shadowy=1")

    elif tpl == "sports":
        last = header(fc, draws, last, f"{esc(league or '')} SCORES")
        last = nameplate(fc, draws, last, v.get("reporter"), v.get("reporter_role"))
        last = panel(fc, last)
        games = [g for g in v.get("games", []) if g.get("hs") not in (None, "")][:8]
        if games:
            cols = 2
            rows = (len(games) + cols - 1) // cols
            gap = 14
            cell_w = (CARD_W - 52 - gap) // 2
            cell_h = min(126, (CARD_H - 46 - gap * max(0, rows - 1)) // max(1, rows))
            top = CARD_Y + 24
            for gi, g in enumerate(games):
                col, row = gi % 2, gi // 2
                x = CARD_X + 26 + col * (cell_w + gap)
                y = top + row * (cell_h + gap)
                fc.append(f"[{last}]drawbox=x={x}:y={y}:w={cell_w}:h={cell_h}:color=0x07162f@0.88:t=fill[sg{gi}]"); last = f"sg{gi}"
                fc.append(f"[{last}]drawbox=x={x}:y={y}:w={cell_w}:h={cell_h}:color={ACCENT}@0.5:t=2[ss{gi}]"); last = f"ss{gi}"
                away_lines = wrap(g.get("away", ""), 23)[:2]
                home_lines = wrap(g.get("home", ""), 23)[:2]
                for li, line in enumerate(away_lines):
                    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(line)}':fontcolor=0xd6e4f7:fontsize=18:x={x+14}:y={y+13+li*22}")
                for li, line in enumerate(home_lines):
                    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(line)}':fontcolor=0xd6e4f7:fontsize=18:x={x+cell_w-14}-text_w:y={y+13+li*22}")
                draws.append(f"drawtext=fontfile='{FONT}':text='{g.get('as')}  -  {g.get('hs')}':fontcolor=white:fontsize=30:x={x+cell_w/2}-text_w/2:y={y+cell_h-48}")
                draws.append(f"drawtext=fontfile='{FONT}':text='FINAL':fontcolor={ACCENT}:fontsize=14:x={x+cell_w/2}-text_w/2:y={y+cell_h-18}")
        else:
            draws.append(f"drawtext=fontfile='{FONT}':text='No games to report':fontcolor=white:fontsize=30:"
                         f"x=(w-text_w)/2:y={CARD_Y+CARD_H//2-15}")
        nxt = v.get("next") or []
        if nxt and len(games) <= 4:
            fc.append(f"[{last}]drawbox=x={CARD_X+40}:y={CARD_Y+CARD_H-78}:w={CARD_W-80}:h=1:"
                     f"color={ACCENT}@0.4:t=fill[div]"); last = "div"
            draws.append(f"drawtext=fontfile='{FONT}':text='{esc('NEXT: ' + (nxt[0].get('event') or ''))}':"
                         f"fontcolor=0x9fb4d0:fontsize=20:x=(w-text_w)/2:y={CARD_Y+CARD_H-56}")

    elif tpl == "traffic":
        last = header(fc, draws, last, "DENVER / AURORA TRAFFIC")
        last = nameplate(fc, draws, last, v.get("reporter"), v.get("reporter_role"))
        last = panel(fc, last)
        items = v.get("items", [])[:3]
        if not items:
            draws.append(f"drawtext=fontfile='{FONT}':text='ALL CLEAR':fontcolor={ACCENT}:fontsize=52:"
                         f"x=(w-text_w)/2:y={CARD_Y+CARD_H//2-50}")
            draws.append(f"drawtext=fontfile='{FONT}':text='No major incidents reported this morning':"
                         f"fontcolor=white:fontsize=24:x=(w-text_w)/2:y={CARD_Y+CARD_H//2+16}")
        else:
            row_h = min(CARD_H // len(items), 150)
            block_y0 = CARD_Y + (CARD_H - row_h * len(items)) // 2
            for i, it in enumerate(items):
                ry = block_y0 + i * row_h + 14
                label = "●  " + (it.get('type') or 'Incident').upper()
                if it.get('route'):
                    label += "  —  " + str(it['route'])
                draws.append(f"drawtext=fontfile='{FONT}':text='{esc(label)}':fontcolor={ACCENT}:fontsize=22:"
                             f"x=(w-text_w)/2:y={ry-2}")
                for li, ln in enumerate(wrap(it.get('desc', ''), 58)[:2]):
                    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(ln)}':fontcolor=white:fontsize=22:"
                                 f"x=(w-text_w)/2:y={ry+30+li*30}")
                if i < len(items) - 1:
                    fc.append(f"[{last}]drawbox=x={CARD_X+40}:y={block_y0+(i+1)*row_h}:w={CARD_W-80}:h=1:"
                             f"color=white@0.15:t=fill[rl{i}]"); last = f"rl{i}"
        disc = v.get("disclaimer", "")
        if disc:
            for li, ln in enumerate(wrap(disc, 120)):
                draws.append(f"drawtext=fontfile='{FONT}':text='{esc(ln)}':fontcolor=0x9fb4d0:fontsize=13:"
                             f"x={SAFE_X}:y={H-FOOTER_H-40+li*18}")

    elif tpl == "news":
        last = header(fc, draws, last, esc(v.get("label", "NEWS")).upper())
        last = nameplate(fc, draws, last, v.get("reporter"), v.get("reporter_role"))
        last = panel(fc, last)
        heads = v.get("headlines", [])[:3]
        if not heads:
            draws.append(f"drawtext=fontfile='{FONT}':text='Updating...':fontcolor=white:fontsize=30:"
                         f"x=(w-text_w)/2:y={CARD_Y+CARD_H//2-15}")
        else:
            row_h = CARD_H // len(heads)
            for i, hl in enumerate(heads):
                ry = CARD_Y + i * row_h + 18
                src_name = (hl.get("source") or "").upper()
                draws.append(f"drawtext=fontfile='{FONT}':text='{esc(src_name)}':"
                             f"fontcolor={ACCENT}:fontsize=17:x={CARD_X+40}:y={ry}")
                for li, ln in enumerate(wrap(hl.get("title", ""), 62)[:2]):
                    draws.append(f"drawtext=fontfile='{FONT}':text='{esc(ln)}':fontcolor=white:fontsize=25:"
                                 f"x={CARD_X+40}:y={ry+26+li*32}")
                if i < len(heads) - 1:
                    fc.append(f"[{last}]drawbox=x={CARD_X+40}:y={CARD_Y+(i+1)*row_h}:w={CARD_W-80}:h=1:"
                             f"color=white@0.15:t=fill[nl{i}]"); last = f"nl{i}"

    elif tpl == "markets":
        last = header(fc, draws, last, "MARKETS")
        last = panel(fc, last)
        stocks = [(s, q) for s, q in v.get("stocks", {}).items() if q.get("price") is not None]
        if not stocks:
            draws.append(f"drawtext=fontfile='{FONT}':text='Updating...':fontcolor=white:fontsize=30:"
                         f"x=(w-text_w)/2:y={CARD_Y+CARD_H//2-15}")
        else:
            per_col = (len(stocks) + 1) // 2
            for i, (sym, q) in enumerate(stocks[:12]):
                col = i // per_col; row = i % per_col
                x = CARD_X + 90 + col * (CARD_W // 2)
                y = CARD_Y + 40 + row * 56
                up = (q.get("change") or 0) >= 0
                arrow = "▲" if up else "▼"
                color = "0x3fd67a" if up else "0xff6b5a"
                draws.append(f"drawtext=fontfile='{FONT}':text='{esc(sym)}':fontcolor=white:fontsize=24:x={x}:y={y}")
                draws.append(f"drawtext=fontfile='{FONT}':text='{q['price']:.2f}  {arrow}{abs(q.get('change') or 0):.2f}':"
                             f"fontcolor={color}:fontsize=22:x={x+140}:y={y+2}")

    if draws:
        fc.append(f"[{last}]{','.join(draws)}[draw0]")
    else:
        fc.append(f"[{last}]null[draw0]")

    # ---- footer ticker: one continuous string, running offset across segments ----
    fc.append(f"[draw0]drawbox=x=0:y=ih-{FOOTER_H}-2:w=iw:h=2:color={ACCENT}@0.8:t=fill[tbtop]")
    fc.append(f"[tbtop]drawbox=x=0:y=ih-{FOOTER_H}:w=iw:h={FOOTER_H}:color=white@0.96:t=fill[tb]")
    tk = esc(ticker_text)
    fc.append(f"[tb]drawtext=fontfile='{FONT}':text='{tk}':fontcolor={TICKER_FG}:fontsize=27:y=h-50:"
              f"x=w-mod((t+{t_offset:.2f})*{TICKER_PXPS}\\,w+tw)[vout]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", f"{audio_idx}:a", "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(out_mp4)]
    subprocess.run(cmd, check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proof", action="store_true")
    ap.add_argument("--only", help="only render segments of this kind (e.g. sports_MLB)")
    ap.add_argument("--min-seg", type=float, default=8.0)
    ap.add_argument("--offline", action="store_true", help="never fetch logos over the network; skip if not cached")
    ap.add_argument("--script", default="script_v3.json", help="script file to render")
    ap.add_argument("--out-name", default=None, help="override the output filename (for staging builds)")
    args = ap.parse_args()
    WORK.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
    sc = json.loads((HERE / args.script).read_text())
    segs = sc["segments"]
    if args.only:
        segs = [s for s in segs if s["kind"] == args.only]
    elif args.proof:
        segs = sc["segments"][:3]
    stocks = sc.get("ticker_stocks", ""); scores = sc.get("ticker_scores", "")
    ticker_text = f"MARKETS:  {stocks}      SCORES:  {scores}      "
    k = kokoro(); parts = []
    intro_video = HERE / "news_intro_overlay.mp4"
    if intro_video.exists() and not args.only:
        parts.append(build_crew_intro(k, sc, intro_video))
    t_offset = 0.0
    for i, s in enumerate(segs):
        wav = WORK / f"{i:02d}.wav"; mp4 = WORK / f"{i:02d}.mp4"
        dur = max(synth(k, s["text"], s["voice"], wav) + 0.8, args.min_seg)
        print(f"  seg {i:02d} [{s['kind']:14s}] {s['voice']:10s} {dur:5.1f}s")
        build(s, dur, wav, mp4, ticker_text, t_offset, offline=args.offline); parts.append(mp4)
        t_offset += dur
    concat = WORK / "concat.txt"; concat.write_text("".join(f"file '{p}'\n" for p in parts))
    name = args.out_name or ("broadcast-morning-v3-proof.mp4" if (args.proof or args.only) else "broadcast-morning-v3.mp4")
    out = OUT / name
    # +faststart moves the moov atom (stream index, incl. the audio track) to the
    # front of the file. Without it, moov sits after mdat on a concat'd file this
    # size, and some browsers render video but never initialize audio because they
    # haven't parsed the index yet - silent playback despite a valid audio stream.
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(out)], check=True)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    print(f"\nWROTE {out} (~{float(dur):.0f}s, {len(parts)} segments)")

if __name__ == "__main__":
    main()
