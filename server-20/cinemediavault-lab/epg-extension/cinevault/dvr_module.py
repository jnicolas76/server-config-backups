#!/usr/bin/env python3
"""CineMediaVault HDHomeRun DVR: scheduler, recording engine, APIs, and guide UI."""
import datetime
import html
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

DB_PATH = Path(os.environ.get("CINEVAULT_DB", "/home/jnicolas/cinevault-data/cinevault.db"))
GUIDE_PATH = Path(os.environ.get("HDHR_GUIDE_CACHE_FILE", "/home/jnicolas/cinemediavault-lab/hdhr-guide-cache.json"))
DVR_ROOT = Path(os.environ.get("CINEVAULT_DVR_ROOT", "/media/jnicolas/Expansion/CineVault DVR"))
WEBEX_WEBHOOK = os.environ.get("CINEVAULT_WEBEX_WEBHOOK_URL", "").strip()
POLL_SECONDS = max(2, int(os.environ.get("CINEVAULT_DVR_POLL_SECONDS", "5")))
LOCK = threading.RLock()
PROCESSES = {}
STARTED = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS dvr_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), recording_root TEXT NOT NULL,
 padding_start INTEGER NOT NULL DEFAULT 60, padding_end INTEGER NOT NULL DEFAULT 120,
 retention_days INTEGER NOT NULL DEFAULT 0, auto_transcode INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dvr_series_rules (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
 guide_number TEXT, new_only INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
 padding_start INTEGER, padding_end INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(user_id,title,guide_number));
CREATE TABLE IF NOT EXISTS dvr_recordings (
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
 device_id TEXT NOT NULL, guide_number TEXT NOT NULL, guide_name TEXT NOT NULL,
 title TEXT NOT NULL, subtitle TEXT, description TEXT, category TEXT,
 episode_num TEXT, is_new INTEGER NOT NULL DEFAULT 0,
 start_ts INTEGER NOT NULL, stop_ts INTEGER NOT NULL,
 padded_start_ts INTEGER NOT NULL, padded_stop_ts INTEGER NOT NULL,
 status TEXT NOT NULL DEFAULT 'scheduled', priority INTEGER NOT NULL DEFAULT 50,
 series_rule_id INTEGER, programme_key TEXT NOT NULL UNIQUE,
 output_path TEXT, partial_path TEXT, process_pid INTEGER,
 bytes_written INTEGER NOT NULL DEFAULT 0, error TEXT, keep INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_dvr_recordings_due ON dvr_recordings(status,padded_start_ts);
CREATE INDEX IF NOT EXISTS idx_dvr_recordings_user_start ON dvr_recordings(user_id,start_ts);
CREATE TABLE IF NOT EXISTS dvr_tuner_sessions (
 id INTEGER PRIMARY KEY, device_id TEXT NOT NULL, tuner_slot INTEGER NOT NULL,
 recording_id INTEGER NOT NULL UNIQUE, channel_id INTEGER NOT NULL,
 started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
 UNIQUE(device_id,tuner_slot));
CREATE TABLE IF NOT EXISTS dvr_events (
 id INTEGER PRIMARY KEY, recording_id INTEGER, event_type TEXT NOT NULL,
 message TEXT NOT NULL, created_at TEXT NOT NULL);
"""

def now_text():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def connect():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init_schema():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = connect()
    try:
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO dvr_settings(id,recording_root,padding_start,padding_end,retention_days,auto_transcode,updated_at) VALUES(1,?,60,120,0,0,?)", (str(DVR_ROOT), now_text()))
        c.execute("DELETE FROM dvr_tuner_sessions")
        now = int(time.time())
        c.execute("UPDATE dvr_recordings SET status=CASE WHEN padded_stop_ts>? THEN 'scheduled' ELSE 'failed' END,error='Recording interrupted by service restart',process_pid=NULL,updated_at=? WHERE status IN ('starting','recording','finalizing')", (now, now_text()))
        c.commit()
    finally:
        c.close()

def setting():
    c = connect()
    try: return dict(c.execute("SELECT * FROM dvr_settings WHERE id=1").fetchone())
    finally: c.close()

_GUIDE_CACHE = {"key": None, "data": None}

def guide():
    # Memoised on file mtime/size: the 14-day merged guide is far larger than the
    # HDHomeRun-only guide and this is read on every guide page load.
    try:
        st = GUIDE_PATH.stat(); key = (st.st_mtime_ns, st.st_size)
        if _GUIDE_CACHE["key"] == key and _GUIDE_CACHE["data"] is not None:
            return _GUIDE_CACHE["data"]
        data = json.loads(GUIDE_PATH.read_text(encoding="utf-8"))
        _GUIDE_CACHE["key"] = key; _GUIDE_CACHE["data"] = data
        return data
    except Exception: return {"updated_at": 0, "programme_count": 0, "programmes": {}}

def safe_name(value):
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", str(value or "Recording"))
    return re.sub(r"\s+", " ", value).strip(" .")[:140] or "Recording"

def programme_key(device_id, number, start, stop, title):
    import hashlib
    raw = f"{device_id}|{number}|{int(start)}|{int(stop)}|{title.strip().casefold()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def event(c, recording_id, kind, message):
    c.execute("INSERT INTO dvr_events(recording_id,event_type,message,created_at) VALUES(?,?,?,?)", (recording_id, kind, str(message)[:2000], now_text()))

def notify(message):
    if not WEBEX_WEBHOOK: return
    try:
        req = urllib.request.Request(WEBEX_WEBHOOK, data=json.dumps({"markdown": message}).encode(), headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req, timeout=8).read()
    except Exception as exc:
        print(f"DVR Webex notification failed: {exc}", flush=True)

def channel_record(channel_id):
    c = connect()
    try:
        row = c.execute("""SELECT c.*,d.tuner_count,d.friendly_name FROM hdhr_channels c
          JOIN hdhr_devices d ON d.device_id=c.device_id WHERE c.id=? AND d.enabled=1""", (int(channel_id),)).fetchone()
        return dict(row) if row else None
    finally: c.close()

def create_recording(user_id, payload, series_rule_id=None):
    channel = channel_record(payload.get("channel_id"))
    if not channel: return {"ok":False,"error":"Channel not found"}
    try:
        start, stop = int(payload.get("start")), int(payload.get("stop"))
    except Exception: return {"ok":False,"error":"Invalid programme time"}
    if stop <= start or stop < time.time(): return {"ok":False,"error":"Programme has already ended"}
    settings = setting()
    ps = int(payload.get("padding_start", settings["padding_start"]))
    pe = int(payload.get("padding_end", settings["padding_end"]))
    title = str(payload.get("title") or "Live TV")[:300]
    key = programme_key(channel["device_id"], channel["guide_number"], start, stop, title)
    c = connect()
    try:
        cursor = c.execute("""INSERT OR IGNORE INTO dvr_recordings
          (user_id,channel_id,device_id,guide_number,guide_name,title,subtitle,description,category,episode_num,is_new,
           start_ts,stop_ts,padded_start_ts,padded_stop_ts,status,priority,series_rule_id,programme_key,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'scheduled',?,?,?,?,?)""",
          (int(user_id),int(channel["id"]),channel["device_id"],channel["guide_number"],channel["guide_name"],title,
           str(payload.get("subtitle") or "")[:300],str(payload.get("description") or "")[:4000],str(payload.get("category") or "")[:120],
           str(payload.get("episode_num") or "")[:80],1 if payload.get("is_new") else 0,start,stop,max(0,start-ps),stop+pe,
           int(payload.get("priority") or 50),series_rule_id,key,now_text(),now_text()))
        inserted = cursor.rowcount > 0
        row = c.execute("SELECT * FROM dvr_recordings WHERE programme_key=?", (key,)).fetchone()
        if inserted:
            event(c, row["id"], "scheduled", f"Scheduled {title} on {channel['guide_number']} {channel['guide_name']}")
        c.commit()
        return {"ok":True,"recording":dict(row),"duplicate":not inserted}
    finally: c.close()

def sync_series_rules():
    data = guide().get("programmes", {})
    c = connect()
    try:
        rules = [dict(x) for x in c.execute("SELECT * FROM dvr_series_rules WHERE active=1").fetchall()]
        channels = {x["guide_number"]:dict(x) for x in c.execute("SELECT * FROM hdhr_channels").fetchall()}
    finally: c.close()
    added = 0
    for rule in rules:
        numbers = [rule["guide_number"]] if rule.get("guide_number") else list(data)
        for number in numbers:
            channel = channels.get(number)
            if not channel: continue
            for item in data.get(number, []):
                if str(item.get("title") or "").casefold() != rule["title"].casefold(): continue
                if rule["new_only"] and not item.get("is_new"): continue
                result = create_recording(rule["user_id"], dict(item, channel_id=channel["id"], padding_start=rule["padding_start"] if rule["padding_start"] is not None else setting()["padding_start"], padding_end=rule["padding_end"] if rule["padding_end"] is not None else setting()["padding_end"]), rule["id"])
                if result.get("ok") and not result.get("duplicate"): added += 1
    return added

def choose_folder(row):
    category = (row.get("category") or "").casefold()
    if "movie" in category: bucket = "Movies"
    elif "sport" in category: bucket = "Sports"
    elif "news" in category: bucket = "News"
    else: bucket = "TV Shows"
    root = Path(setting()["recording_root"]) / bucket / safe_name(row["title"])
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.fromtimestamp(row["start_ts"]).strftime("%Y-%m-%d %H-%M")
    extra = f" - {safe_name(row['subtitle'])}" if row.get("subtitle") else ""
    return root / f"{stamp} - {safe_name(row['title'])}{extra}.ts"

def active_slots(c, device_id):
    return {int(x[0]) for x in c.execute("SELECT tuner_slot FROM dvr_tuner_sessions WHERE device_id=?", (device_id,)).fetchall()}

def device_busy_count(base_url):
    try:
        with urllib.request.urlopen(base_url.rstrip("/")+"/status.json", timeout=2) as response:
            status=json.loads(response.read().decode("utf-8","replace"))
        return sum(1 for item in status if any(item.get(k) for k in ("VctNumber","TargetIP","NetworkRate","SymbolQuality")))
    except Exception:
        return 0

def start_recording(recording_id):
    with LOCK:
        c = connect()
        try:
            row_obj = c.execute("""SELECT r.*,c.stream_url,d.tuner_count,d.base_url FROM dvr_recordings r
              JOIN hdhr_channels c ON c.id=r.channel_id JOIN hdhr_devices d ON d.device_id=r.device_id WHERE r.id=?""", (recording_id,)).fetchone()
            if not row_obj or row_obj["status"] != "scheduled": return
            row = dict(row_obj); used = active_slots(c,row["device_id"]); busy=max(len(used),device_busy_count(row["base_url"])); slot = next((n for n in range(int(row["tuner_count"])) if n not in used), None)
            if slot is None or busy >= int(row["tuner_count"]):
                c.execute("UPDATE dvr_recordings SET status='conflict',error='No tuner available',updated_at=? WHERE id=?", (now_text(),recording_id))
                event(c,recording_id,"conflict","No tuner was available at recording start")
                c.commit(); notify(f"⚠️ **CineVault DVR conflict:** {row['title']} could not start; both tuners were busy."); return
            output = choose_folder(row); partial = Path(str(output)+".partial")
            duration = max(1, int(row["padded_stop_ts"]-time.time()))
            cmd = ["ffmpeg","-nostdin","-y","-loglevel","warning","-i",row["stream_url"],"-map","0","-c","copy","-t",str(duration),"-f","mpegts",str(partial)]
            log_path = Path(str(output)+".log"); log = open(log_path,"ab",buffering=0)
            process = subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            PROCESSES[recording_id] = (process,log)
            c.execute("INSERT INTO dvr_tuner_sessions(device_id,tuner_slot,recording_id,channel_id,started_at,heartbeat_at) VALUES(?,?,?,?,?,?)", (row["device_id"],slot,recording_id,row["channel_id"],now_text(),now_text()))
            c.execute("UPDATE dvr_recordings SET status='recording',output_path=?,partial_path=?,process_pid=?,started_at=?,updated_at=?,error=NULL WHERE id=?", (str(output),str(partial),process.pid,now_text(),now_text(),recording_id))
            event(c,recording_id,"started",f"Recording started using tuner {slot+1}"); c.commit()
            notify(f"🔴 **CineVault DVR started:** {row['title']} — {row['guide_number']} {row['guide_name']}")
        except Exception as exc:
            c.execute("UPDATE dvr_recordings SET status='failed',error=?,updated_at=? WHERE id=?", (str(exc),now_text(),recording_id)); event(c,recording_id,"failed",str(exc)); c.commit()
        finally: c.close()

def finish_recording(recording_id, forced=False):
    with LOCK:
        proc_info = PROCESSES.pop(recording_id, None)
        if proc_info:
            process, log = proc_info
            if forced and process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except Exception: process.terminate()
                try: process.wait(timeout=8)
                except Exception: process.kill()
            rc = process.poll()
            log.close()
        else: rc = -1
        c=connect()
        try:
            row=c.execute("SELECT * FROM dvr_recordings WHERE id=?",(recording_id,)).fetchone()
            if not row:return
            partial=Path(row["partial_path"] or ""); output=Path(row["output_path"] or "")
            size=partial.stat().st_size if partial.is_file() else 0
            if size > 1024*1024 and not forced:
                partial.replace(output); status="completed"; error=None
            elif forced:
                status="cancelled"; error="Cancelled by user"
            else:
                status="failed"; error=f"ffmpeg exited {rc}; recorded {size} bytes"
            c.execute("DELETE FROM dvr_tuner_sessions WHERE recording_id=?",(recording_id,))
            c.execute("UPDATE dvr_recordings SET status=?,bytes_written=?,error=?,process_pid=NULL,completed_at=?,updated_at=? WHERE id=?",(status,size,error,now_text(),now_text(),recording_id))
            event(c,recording_id,status,error or f"Completed with {size} bytes"); c.commit()
            notify(("✅" if status=="completed" else "❌")+f" **CineVault DVR {status}:** {row['title']}")
        finally:c.close()

def scheduler_loop():
    last_series=0
    while True:
        try:
            now=int(time.time())
            if now-last_series>900: sync_series_rules(); last_series=now
            c=connect()
            try:
                due=[x[0] for x in c.execute("SELECT id FROM dvr_recordings WHERE status='scheduled' AND padded_start_ts<=? AND padded_stop_ts>? ORDER BY priority DESC,padded_start_ts",(now,now)).fetchall()]
                expired=[x[0] for x in c.execute("SELECT id FROM dvr_recordings WHERE status='scheduled' AND padded_stop_ts<=?",(now,)).fetchall()]
                for rid in expired:c.execute("UPDATE dvr_recordings SET status='missed',error='Scheduler did not start before programme ended',updated_at=? WHERE id=?",(now_text(),rid))
                active=[dict(x) for x in c.execute("SELECT id,partial_path,padded_stop_ts FROM dvr_recordings WHERE status='recording'").fetchall()]
                for x in active:
                    size=Path(x["partial_path"] or "").stat().st_size if Path(x["partial_path"] or "").is_file() else 0
                    c.execute("UPDATE dvr_recordings SET bytes_written=?,updated_at=? WHERE id=?",(size,now_text(),x["id"]))
                    c.execute("UPDATE dvr_tuner_sessions SET heartbeat_at=? WHERE recording_id=?",(now_text(),x["id"]))
                c.commit()
            finally:c.close()
            for rid in due:start_recording(rid)
            for x in active:
                info=PROCESSES.get(x["id"])
                if now>=x["padded_stop_ts"] or (info and info[0].poll() is not None):finish_recording(x["id"])
        except Exception as exc: print(f"DVR scheduler error: {exc}",flush=True)
        time.sleep(POLL_SECONDS)

def initialize():
    global STARTED
    with LOCK:
        if STARTED:return
        init_schema(); STARTED=True
        threading.Thread(target=scheduler_loop,daemon=True,name="cinevault-dvr-scheduler").start()

def rows(sql,args=()):
    c=connect()
    try:return [dict(x) for x in c.execute(sql,args).fetchall()]
    finally:c.close()

def guide_payload(query):
    now=int(time.time()); start=int(query.get("from",[now-now%1800])[0]); hours=max(2,min(24,int(query.get("hours",[6])[0]))); end=start+hours*3600
    channels=rows("""SELECT c.id,c.device_id,c.guide_number,c.guide_name,c.is_hd,d.friendly_name,d.tuner_count
      FROM hdhr_channels c JOIN hdhr_devices d ON d.device_id=c.device_id WHERE d.enabled=1 ORDER BY CAST(c.guide_number AS REAL),c.guide_number""")
    data=guide(); schedules=data.get("programmes",{}); icons=data.get("channel_icons",{})
    scheduled={x["programme_key"]:x for x in rows("SELECT id,programme_key,status FROM dvr_recordings WHERE start_ts<? AND stop_ts>?",(end,start))}
    for ch in channels:
        ch["logo"] = icons.get(ch["guide_number"], "")
        out=[]
        for item in schedules.get(ch["guide_number"],[]):
            if item.get("stop",0)<=start or item.get("start",0)>=end:continue
            item=dict(item); key=programme_key(ch["device_id"],ch["guide_number"],item["start"],item["stop"],item.get("title","")); item["programme_key"]=key
            if key in scheduled:item["recording"]={"id":scheduled[key]["id"],"status":scheduled[key]["status"]}
            out.append(item)
        ch["programmes"]=out
    return {"ok":True,"start":start,"end":end,"hours":hours,"updated_at":data.get("updated_at",0),"channels":channels}

def dashboard_payload(user_id):
    return {"ok":True,"settings":setting(),
      "scheduled":rows("SELECT * FROM dvr_recordings WHERE user_id=? AND status IN ('scheduled','starting','recording') ORDER BY padded_start_ts",(user_id,)),
      "recordings":rows("SELECT * FROM dvr_recordings WHERE user_id=? AND status IN ('completed','failed','cancelled','conflict','missed') ORDER BY start_ts DESC LIMIT 500",(user_id,)),
      "series":rows("SELECT * FROM dvr_series_rules WHERE user_id=? ORDER BY active DESC,title",(user_id,)),
      "conflicts":rows("SELECT * FROM dvr_recordings WHERE user_id=? AND status IN ('conflict','failed','missed') ORDER BY start_ts DESC LIMIT 200",(user_id,)),
      "sessions":rows("SELECT s.*,r.title,r.guide_number,r.bytes_written FROM dvr_tuner_sessions s JOIN dvr_recordings r ON r.id=s.recording_id ORDER BY s.device_id,s.tuner_slot")}

def recording_media(recording_id, user_id):
    c=connect()
    try:
        row=c.execute("SELECT * FROM dvr_recordings WHERE id=? AND user_id=? AND status='completed'",(int(recording_id),int(user_id))).fetchone()
        if not row:return None
        path=Path(row["output_path"] or "")
        if not path.is_file():return None
        return {"id":int(row["id"]),"title":row["title"],"subtitle":row["subtitle"] or row["guide_name"],"path":path,"duration":max(0,int(row["padded_stop_ts"])-int(row["padded_start_ts"]))}
    finally:c.close()

def handle_get(handler,user,path):
    initialize(); parsed=__import__('urllib.parse',fromlist=['urlparse']).urlparse(handler.path); query=__import__('urllib.parse',fromlist=['parse_qs']).parse_qs(parsed.query)
    if path in {"/live-tv","/dvr","/dvr/schedule","/dvr/recordings","/dvr/series","/dvr/conflicts","/dvr/settings"}:
        return handler.render_html(PAGE.replace("__INITIAL_TAB__", html.escape(path.rsplit('/',1)[-1] if path!='/live-tv' else 'guide')))
    if path=="/api/dvr/guide":return handler.json_response(guide_payload(query))
    if path=="/api/dvr/status":return handler.json_response(dashboard_payload(int(user["id"])))
    return False

def handle_post(handler,user,path):
    initialize(); payload=handler.read_json(); uid=int(user["id"])
    if path=="/api/dvr/record":return handler.json_response(create_recording(uid,payload))
    if path=="/api/dvr/series":
        title=str(payload.get("title") or "").strip(); number=str(payload.get("guide_number") or "").strip() or None
        if not title:return handler.json_response({"ok":False,"error":"Title is required"})
        c=connect()
        try:
            c.execute("""INSERT INTO dvr_series_rules(user_id,title,guide_number,new_only,active,padding_start,padding_end,created_at,updated_at)
              VALUES(?,?,?,?,1,?,?,?,?) ON CONFLICT(user_id,title,guide_number) DO UPDATE SET new_only=excluded.new_only,active=1,updated_at=excluded.updated_at""",
              (uid,title,number,1 if payload.get("new_only") else 0,payload.get("padding_start"),payload.get("padding_end"),now_text(),now_text())); c.commit()
        finally:c.close()
        added=sync_series_rules(); return handler.json_response({"ok":True,"scheduled":added})
    if path in {"/api/dvr/cancel","/api/dvr/delete"}:
        rid=int(payload.get("id") or 0); c=connect()
        try: row=c.execute("SELECT * FROM dvr_recordings WHERE id=? AND user_id=?",(rid,uid)).fetchone()
        finally:c.close()
        if not row:return handler.json_response({"ok":False,"error":"Recording not found"})
        if path.endswith("cancel"):
            if row["status"]=="recording":finish_recording(rid,True)
            else:
                c=connect(); c.execute("UPDATE dvr_recordings SET status='cancelled',error='Cancelled by user',updated_at=? WHERE id=?",(now_text(),rid)); c.commit(); c.close()
        else:
            if row["status"]=="recording":return handler.json_response({"ok":False,"error":"Cancel the active recording first"})
            for key in ("output_path","partial_path"):
                p=Path(row[key] or "");
                if p.is_file():p.unlink()
            c=connect(); c.execute("DELETE FROM dvr_recordings WHERE id=?",(rid,)); c.commit(); c.close()
        return handler.json_response({"ok":True})
    if path=="/api/dvr/series/toggle":
        c=connect(); c.execute("UPDATE dvr_series_rules SET active=?,updated_at=? WHERE id=? AND user_id=?",(1 if payload.get("active") else 0,now_text(),int(payload.get("id") or 0),uid)); c.commit(); c.close(); return handler.json_response({"ok":True})
    if path=="/api/dvr/settings":
        if not user["is_admin"]:return handler.send_error(403)
        root=Path(str(payload.get("recording_root") or DVR_ROOT)); root.mkdir(parents=True,exist_ok=True)
        c=connect(); c.execute("UPDATE dvr_settings SET recording_root=?,padding_start=?,padding_end=?,retention_days=?,auto_transcode=?,updated_at=? WHERE id=1",(str(root),max(0,int(payload.get("padding_start",60))),max(0,int(payload.get("padding_end",120))),max(0,int(payload.get("retention_days",0))),1 if payload.get("auto_transcode") else 0,now_text())); c.commit(); c.close(); return handler.json_response({"ok":True,"settings":setting()})
    return False

PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CineMediaVault Live TV & DVR</title><style>
:root{color-scheme:dark;--bg:#090a0d;--panel:#181a1e;--panel2:#23262b;--line:#393d43;--gold:#f5b400;--muted:#aeb4bc;--red:#e84b55;--green:#36d17c}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#fff;font:15px system-ui,Segoe UI,sans-serif}header{position:sticky;top:0;z-index:8;background:#111;border-bottom:1px solid #333;padding:13px 18px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px}.brand{font-size:24px;font-weight:900}.brand b{color:var(--gold)}button,a.btn{border:1px solid var(--line);background:var(--panel2);color:#fff;border-radius:8px;padding:10px 14px;font-weight:750;text-decoration:none;cursor:pointer}.primary{background:var(--gold);color:#111;border-color:var(--gold)}nav{display:flex;gap:6px;overflow:auto;margin-top:12px}nav button.active{color:var(--gold);border-bottom-color:var(--gold)}main{padding:16px}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}.muted{color:var(--muted)}.guide-wrap{overflow:auto;max-height:calc(100vh - 190px);border:1px solid var(--line);position:relative}.guide{min-width:1500px}.time-row,.channel-row{display:grid;grid-template-columns:155px 1fr}.time-row{position:sticky;top:0;z-index:5;background:#15171b}.channel-name{position:sticky;left:0;z-index:4;background:#17191d;border-right:1px solid var(--line);border-bottom:1px solid var(--line);padding:8px;display:flex;gap:8px;align-items:center}.channel-name img{width:38px;height:38px;object-fit:contain;background:#fff;border-radius:5px}.timeline{position:relative;height:70px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(90deg,#16181c 0,#16181c calc(8.333% - 1px),#353940 calc(8.333% - 1px),#353940 8.333%)}.program{position:absolute;top:5px;height:59px;padding:7px;border:1px solid #484d55;background:#292c31;overflow:hidden;text-align:left;border-radius:4px;font-size:13px}.program.rec{border-color:var(--red);box-shadow:inset 3px 0 var(--red)}.program.now{background:#34383e}.time-labels{height:42px;position:relative}.time-labels span{position:absolute;padding:12px 5px;color:var(--muted)}.now-line{position:absolute;top:0;bottom:0;width:2px;background:var(--gold);z-index:3}.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}.card h3{margin:0 0 6px}.status{font-weight:850;text-transform:uppercase;font-size:12px}.recording{color:var(--red)}.completed{color:var(--green)}.conflict,.failed,.missed{color:#ff747d}.tabsheet{display:none}.tabsheet.active{display:block}.modal{display:none;position:fixed;inset:0;background:#000b;z-index:20;align-items:center;justify-content:center;padding:18px}.modal.open{display:flex}.dialog{width:min(560px,100%);background:#1a1c20;border:1px solid #555;border-radius:12px;padding:20px}.dialog h2{margin-top:0}label{display:block;margin:10px 0 4px}input{width:100%;padding:11px;background:#101216;color:#fff;border:1px solid #444;border-radius:7px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}@media(max-width:700px){.brand{font-size:19px}.guide-wrap{max-height:calc(100vh - 215px)}main{padding:10px}}
/* Responsive header/navigation repair. */
.top>div:last-child{display:flex;gap:8px;flex:0 0 auto}.brand{line-height:1.15;min-width:0}nav{overflow-x:auto;overflow-y:hidden;padding-bottom:2px;scrollbar-width:thin}nav button{flex:0 0 auto;white-space:nowrap}
@media(max-width:700px){header{padding:12px 10px}.top{align-items:flex-start}.brand{font-size:18px;max-width:calc(100% - 132px)}.top a.btn{padding:8px 11px;font-size:14px}nav{margin-top:10px}nav button{padding:9px 12px}.cards{grid-template-columns:1fr}.card{padding:13px}}
</style></head><body><header><div class="top"><div class="brand">CineMedia<b>Vault</b> Live TV & DVR</div><div><a class="btn" href="/">Home</a><a class="btn" href="/wall">Wall</a></div></div><nav id="nav"><button data-tab="guide">Guide</button><button data-tab="schedule">DVR Schedule</button><button data-tab="recordings">Recordings</button><button data-tab="series">Series</button><button data-tab="conflicts">Conflicts</button><button data-tab="settings">Settings</button></nav></header><main>
<section id="guide" class="tabsheet"><div class="toolbar"><button id="prev">← Earlier</button><button id="today" class="primary">Now</button><button id="next">Later →</button><button id="refresh">Refresh guide</button><span id="guideInfo" class="muted"></span></div><div class="guide-wrap"><div id="grid" class="guide"></div></div></section>
<section id="schedule" class="tabsheet"><h2>DVR Schedule</h2><div id="scheduleCards" class="cards"></div></section><section id="recordings" class="tabsheet"><h2>Recordings</h2><div id="recordingCards" class="cards"></div></section><section id="series" class="tabsheet"><h2>Series Rules</h2><div id="seriesCards" class="cards"></div></section><section id="conflicts" class="tabsheet"><h2>Conflicts and Failures</h2><div id="conflictCards" class="cards"></div></section><section id="settings" class="tabsheet"><h2>DVR Settings</h2><div class="card"><label>Recording root</label><input id="root"><label>Start padding (seconds)</label><input id="padStart" type="number"><label>End padding (seconds)</label><input id="padEnd" type="number"><label>Retention days (0 = never auto-delete)</label><input id="retention" type="number"><div class="actions"><button id="saveSettings" class="primary">Save settings</button></div></div></section></main>
<div id="modal" class="modal"><div class="dialog"><h2 id="mTitle"></h2><div id="mMeta" class="muted"></div><p id="mDesc"></p><div class="actions"><button id="watch">Watch Live</button><button id="record" class="primary">Record</button><button id="seriesAll">Record Series</button><button id="seriesNew">New Episodes Only</button><button id="close">Close</button></div></div></div>
<script>const initial='__INITIAL_TAB__',esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let start=Math.floor(Date.now()/1800000)*1800,current=null,dash=null;function tab(n){document.querySelectorAll('.tabsheet').forEach(x=>x.classList.toggle('active',x.id===n));document.querySelectorAll('#nav button').forEach(x=>x.classList.toggle('active',x.dataset.tab===n));history.replaceState(null,'',n==='guide'?'/live-tv':'/dvr/'+n);if(n!=='guide')loadDash()}document.getElementById('nav').onclick=e=>{const b=e.target.closest('[data-tab]');if(b)tab(b.dataset.tab)};
function dt(t){return new Date(t*1000).toLocaleString([], {weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}function size(n){return n>1073741824?(n/1073741824).toFixed(1)+' GB':n>1048576?(n/1048576).toFixed(1)+' MB':n+' B'}async function loadGuide(){const r=await fetch(`/api/dvr/guide?from=${start}&hours=6`,{cache:'no-store'}),d=await r.json(),span=d.end-d.start;document.getElementById('guideInfo').textContent=`${d.channels.length} channels · guide updated ${dt(d.updated_at)}`;let times='<div class="time-row"><div class="channel-name"></div><div class="time-labels">';for(let t=d.start;t<=d.end;t+=1800)times+=`<span style="left:${(t-d.start)/span*100}%">${new Date(t*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}</span>`;times+='</div></div>';let rows=d.channels.map(ch=>{let ps=ch.programmes.map(p=>{const left=Math.max(0,(p.start-d.start)/span*100),right=Math.min(100,(p.stop-d.start)/span*100),w=Math.max(.7,right-left);return `<button class="program ${p.recording?'rec':''} ${p.start<=Date.now()/1000&&p.stop>Date.now()/1000?'now':''}" style="left:${left}%;width:${w}%" data-channel='${JSON.stringify(ch).replace(/'/g,'&#39;')}' data-program='${JSON.stringify(p).replace(/'/g,'&#39;')}'><b>${esc(p.title)}</b><br><span class="muted">${esc(p.subtitle||'')} ${p.recording?'●':''}</span></button>`}).join('');let now=(Date.now()/1000-d.start)/span*100;return `<div class="channel-row"><div class="channel-name">${ch.logo?`<img src="${esc(ch.logo)}" alt="">`:''}<span><b>${esc(ch.guide_number)} ${esc(ch.guide_name)}</b><br><span class="muted">${ch.is_hd?'HD · ':''}${esc(ch.friendly_name)}</span></span></div><div class="timeline">${now>=0&&now<=100?`<i class="now-line" style="left:${now}%"></i>`:''}${ps}</div></div>`}).join('');document.getElementById('grid').innerHTML=times+rows}document.getElementById('grid').onclick=e=>{const b=e.target.closest('.program');if(!b)return;current={ch:JSON.parse(b.dataset.channel),p:JSON.parse(b.dataset.program)};mTitle.textContent=current.p.title;mMeta.textContent=`${current.ch.guide_number} ${current.ch.guide_name} · ${dt(current.p.start)}–${new Date(current.p.stop*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'})}`;mDesc.textContent=current.p.description||current.p.category||'';modal.classList.add('open')};
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}),d=await r.json();if(!d.ok)alert(d.error||'Request failed');return d}record.onclick=async()=>{await post('/api/dvr/record',{...current.p,channel_id:current.ch.id});modal.classList.remove('open');loadGuide()};async function series(newOnly){await post('/api/dvr/series',{title:current.p.title,guide_number:current.ch.guide_number,new_only:newOnly});modal.classList.remove('open');loadGuide()}seriesAll.onclick=()=>series(false);seriesNew.onclick=()=>series(true);watch.onclick=()=>location.href='/watch/tuner/'+current.ch.id;close.onclick=()=>modal.classList.remove('open');prev.onclick=()=>{start-=10800;loadGuide()};next.onclick=()=>{start+=10800;loadGuide()};today.onclick=()=>{start=Math.floor(Date.now()/1800000)*1800;loadGuide()};refresh.onclick=async()=>{refresh.disabled=true;await fetch('/api/hdhr/guide/refresh',{method:'POST'});refresh.disabled=false;loadGuide()};
function card(x,buttons=''){return `<article class="card"><h3>${esc(x.title)}</h3><div>${esc(x.guide_number||'')} ${esc(x.guide_name||'')}</div><div class="muted">${dt(x.start_ts)} · ${esc(x.subtitle||x.category||'')}</div><p class="status ${esc(x.status)}">${esc(x.status)}</p>${x.bytes_written?`<p>${size(x.bytes_written)}</p>`:''}${x.error?`<p>${esc(x.error)}</p>`:''}<div class="actions">${buttons}</div></article>`}async function loadDash(){const r=await fetch('/api/dvr/status',{cache:'no-store'});dash=await r.json();scheduleCards.innerHTML=dash.scheduled.map(x=>card(x,`<button data-cancel="${x.id}">Cancel</button>`)).join('')||'<p class="muted">No upcoming recordings.</p>';recordingCards.innerHTML=dash.recordings.map(x=>card(x,`${x.status==='completed'?`<a class="btn primary" href="/dvr/play/${x.id}">Play</a><a class="btn" href="/dvr/download/${x.id}">Download</a>`:''}<button data-delete="${x.id}">Delete</button>`)).join('')||'<p class="muted">No completed recordings.</p>';conflictCards.innerHTML=dash.conflicts.map(x=>card(x)).join('')||'<p class="muted">No conflicts or failures.</p>';seriesCards.innerHTML=dash.series.map(x=>`<article class="card"><h3>${esc(x.title)}</h3><p>${esc(x.guide_number||'All channels')} · ${x.new_only?'New episodes only':'All episodes'}</p><button data-rule="${x.id}" data-active="${x.active?0:1}">${x.active?'Pause':'Resume'}</button></article>`).join('')||'<p class="muted">No series rules.</p>';root.value=dash.settings.recording_root;padStart.value=dash.settings.padding_start;padEnd.value=dash.settings.padding_end;retention.value=dash.settings.retention_days}document.querySelector('main').onclick=async e=>{let b=e.target.closest('[data-cancel]');if(b&&confirm('Cancel this recording?')){await post('/api/dvr/cancel',{id:+b.dataset.cancel});loadDash()}b=e.target.closest('[data-delete]');if(b&&confirm('Permanently delete this recording and its file?')){await post('/api/dvr/delete',{id:+b.dataset.delete});loadDash()}b=e.target.closest('[data-rule]');if(b){await post('/api/dvr/series/toggle',{id:+b.dataset.rule,active:+b.dataset.active});loadDash()}};saveSettings.onclick=async()=>{await post('/api/dvr/settings',{recording_root:root.value,padding_start:+padStart.value,padding_end:+padEnd.value,retention_days:+retention.value});loadDash()};tab(['guide','schedule','recordings','series','conflicts','settings'].includes(initial)?initial:'guide');loadGuide();setInterval(()=>{if(document.querySelector('#schedule.active,#recordings.active'))loadDash()},10000);</script></body></html>'''
