import json
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "/app/backend/data/webui.db"
base_url = sys.argv[2] if len(sys.argv) > 2 else "http://192.168.1.232:11434"

with sqlite3.connect(db_path) as connection:
    row = connection.execute("SELECT value FROM config WHERE key = 'ollama.base_urls'").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            ("ollama.base_urls", json.dumps([base_url])),
        )
    else:
        connection.execute(
            "UPDATE config SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
            (json.dumps([base_url]), "ollama.base_urls"),
        )
    connection.commit()

print(json.dumps({"ollama.base_urls": [base_url]}))
