#!/usr/bin/env python3
import json
import sqlite3

db = sqlite3.connect("/config/db/bazarr.db")
db.row_factory = sqlite3.Row
tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
selected = [name for name in tables if any(word in name.lower() for word in ("movie", "series", "episode", "profile", "language", "history"))]
counts = {}
for table in selected:
    try:
        counts[table] = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        pass
print(json.dumps(counts, indent=2, sort_keys=True))
for table in ("table_movies", "table_shows"):
    if table in tables:
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        print(table, "columns", columns)
        if "profileId" in columns:
            print(table, "profiles", [tuple(row) for row in db.execute(f'SELECT profileId,count(*) FROM "{table}" GROUP BY profileId')])
        if "path" in columns:
            print(table, "sample_paths", [row[0] for row in db.execute(f'SELECT path FROM "{table}" LIMIT 3')])
if "table_history" in tables:
    columns = [row[1] for row in db.execute('PRAGMA table_info("table_history")')]
    safe = [name for name in ("timestamp", "provider", "language", "subtitles_path", "action") if name in columns]
    if safe:
        fields = ",".join(f'"{name}"' for name in safe)
        print("latest_tv_history", [dict(row) for row in db.execute(f'SELECT {fields} FROM table_history ORDER BY id DESC LIMIT 10')])
