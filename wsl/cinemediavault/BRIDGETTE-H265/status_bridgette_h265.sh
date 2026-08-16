#!/usr/bin/env bash
set -euo pipefail
STATE=/mnt/c/DATA/BRIDGETTE-H265
SCRIPT="$STATE/bridgette_h265_worker.py"
pgrep -af "(^|/)python3 $SCRIPT" || echo "Worker: stopped"
python3 - <<'PY'
import json
from pathlib import Path
q=Path('/mnt/c/DATA/BRIDGETTE-H265/queue.json')
if not q.exists():
    print('Queue: not created')
    raise SystemExit
x=json.loads(q.read_text())
from collections import Counter
c=Counter(i.get('status','pending') for i in x)
print('Queue:', dict(c), 'total=', len(x))
active=next((i for i in x if i.get('status')=='processing'),None)
if active: print('Current:', active['source'])
PY
tail -n 8 "$STATE/bridgette-h265.log" 2>/dev/null || true
