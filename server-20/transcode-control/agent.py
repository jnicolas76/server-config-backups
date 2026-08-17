#!/usr/bin/env python3
import csv
import json
import os
import re
import shutil
import signal
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(os.environ.get("DATA_ROOT", "/mnt/c/DATA"))
SERVER = os.environ.get("SERVER_URL", "http://192.168.1.20:8126").rstrip("/")
SECRET = os.environ.get("AGENT_SECRET", "")
POLL = int(os.environ.get("POLL_SECONDS", "10"))
MOVIES = ROOT / "orchestrator-movies-h265-queue.csv"
TV = ROOT / "orchestrator-tv-h265-queue.csv"
LEDGER = ROOT / "transcode-completed-ledger.csv"
PROFILES = ROOT / "transcode-job-profiles.json"
ORCH = ROOT / "combined_transcode_orchestrator.py"
START = ROOT / "start_combined_transcode_orchestrator.sh"
LOG = ROOT / "transcode-control-agent.log"
HEADERS = {"X-Agent-Secret": SECRET}


def log(message):
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as out: out.write(line + "\n")


def run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False).stdout.strip()


def queue_file(kind): return MOVIES if kind == "movie" else TV


def read_queue(kind):
    path = queue_file(kind)
    if not path.exists(): return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return [row.get("file_path") or row.get("path") or row.get("full_path") for row in csv.DictReader(fh) if any(row.values())]


def write_queue(kind, rows):
    path = queue_file(kind)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if path.exists(): shutil.copy2(path, path.with_name(path.name + f".bak-dashboard-{stamp}"))
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh); writer.writerow(["file_path"]); writer.writerows([[x] for x in rows])
    temp.replace(path)


def profiles():
    try: return json.loads(PROFILES.read_text(encoding="utf-8"))
    except Exception: return {}


def save_profiles(value):
    temp = PROFILES.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temp.replace(PROFILES)


def disk(path):
    try:
        usage = shutil.disk_usage(path)
        return {"total": usage.total, "used": usage.used, "free": usage.free}
    except OSError: return {"total":0,"used":0,"free":0}


def ledger():
    completed=[]; original=final=0
    if LEDGER.exists():
        with LEDGER.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                try: original += int(row.get("original_bytes") or 0); final += int(row.get("final_bytes") or 0)
                except ValueError: pass
                completed.append(row)
    return completed, max(0, original-final)


def active():
    text = run(["bash","-lc","ps -eo pid,etimes,args | grep -E 'HandBrakeCLI|combined_transcode_orchestrator.py' | grep -v grep"])
    result={"running":False,"processes":[],"title":"Idle","progress":0}
    for line in text.splitlines():
        result["processes"].append(line.strip())
        if "HandBrakeCLI" in line:
            result["running"]=True
            match=re.search(r"-i (.+?) -o ", line)
            result["title"]=Path(match.group(1)).name if match else "HandBrake encode"
    logs=[ROOT/"combined-transcode-movies-h265.log", ROOT/"combined-transcode-tv-h265.log", ROOT/"combined-transcode-orchestrator.log"]
    for path in logs:
        if path.exists():
            tail=run(["tail","-n","80",str(path)])
            matches=re.findall(r"(\d+(?:\.\d+)?)\s*%",tail)
            if matches: result["progress"]=min(100,float(matches[-1]))
            eta=re.findall(r"ETA\s+([0-9hms:]+)",tail)
            if eta: result["eta"]=eta[-1]
            result["log_tail"]=tail[-5000:]
            break
    return result


def queue_items(kind):
    p=profiles(); items=[]
    for index,path in enumerate(read_queue(kind)):
        try: size=Path(path).stat().st_size
        except OSError: size=0
        items.append({"index":index,"path":path,"name":Path(path).name,"size":size,"profile":p.get(path,{})})
    return items


def snapshot():
    done,saved=ledger()
    return {"agent":"WSL","agent_at":datetime.now().astimezone().isoformat(timespec="seconds"),"active":active(),
      "queues":{"movie":queue_items("movie"),"tv":queue_items("tv")},
      "completed":list(reversed(done[-500:])),
      "stats":{"movie_pending":len(read_queue("movie")),"tv_pending":len(read_queue("tv")),"completed":len(done),"saved_bytes":saved},
      "disks":{"movies":disk("/mnt/nfs-share-movies"),"tv":disk("/mnt/nfs-share-tvshows"),"local":disk(str(ROOT))}}


def execute(action,payload):
    kind=payload.get("kind","movie")
    if action in {"move","remove","add"}:
        rows=read_queue(kind)
        if action=="move":
            old=int(payload["from"]); new=max(0,min(int(payload["to"]),len(rows)-1)); item=rows.pop(old); rows.insert(new,item)
        elif action=="remove": rows.pop(int(payload["index"]))
        else:
            path=str(payload["path"])
            if not Path(path).is_file(): raise ValueError("Media path does not exist")
            if path not in rows: rows.insert(max(0,int(payload.get("position",0))),path)
        write_queue(kind,rows); return f"{action} completed for {kind} queue"
    if action=="set_profile":
        p=profiles(); path=str(payload["path"]); p[path]={"target_gb":float(payload["target_gb"]),"preset":payload.get("preset","slow")}; save_profiles(p)
        return "Per-job output profile saved"
    if action=="stop":
        subprocess.run(["pkill","-TERM","-f","combined_transcode_orchestrator.py|HandBrakeCLI"],check=False); return "Stop signal sent"
    if action=="start":
        if active()["running"]: return "A transcode is already running"
        subprocess.Popen(["bash",str(START)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); return "Orchestrator start requested"
    if action=="refresh": return "Status refreshed"
    raise ValueError("Unsupported action")


def main():
    if not SECRET: raise SystemExit("AGENT_SECRET is required")
    while True:
        try:
            requests.post(SERVER+"/api/agent/report",headers=HEADERS,json=snapshot(),timeout=20).raise_for_status()
            data=requests.get(SERVER+"/api/agent/commands",headers=HEADERS,timeout=20).json()
            for command in data.get("commands",[]):
                try: status="done"; result=execute(command["action"],json.loads(command["payload"]))
                except Exception as exc: status="failed"; result=str(exc); log(traceback.format_exc())
                requests.post(f"{SERVER}/api/agent/commands/{command['id']}",headers=HEADERS,json={"status":status,"result":result},timeout=20)
        except Exception as exc: log(f"poll failed: {exc}")
        time.sleep(POLL)


if __name__=="__main__": main()
