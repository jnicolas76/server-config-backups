import html
import sys
import urllib.parse


class VideoListsMixin:
    def _vl_app(self):
        return sys.modules[self.__class__.__module__]

    def video_list_item(self, payload):
        item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
        key = str(item.get("key") or "").strip()
        kind = "tv" if str(item.get("kind") or "").lower() == "tv" else "movie"
        try: media_id = int(key.rsplit(":", 1)[-1])
        except Exception: media_id = 0
        return {"key":key,"kind":kind,"media_id":media_id,"title":str(item.get("title") or "Untitled"),"subtitle":str(item.get("subtitle") or ""),"poster":str(item.get("poster") or ""),"href":str(item.get("href") or f"/player/{kind}/{media_id}")}

    def api_video_queue(self, user):
        app=self._vl_app(); data=self.read_json(); action=str(data.get("action") or "add"); uid=int(user["id"]); conn=app.db_connect()
        try:
            if action=="add":
                item=self.video_list_item(data)
                if not item["key"] or not item["media_id"]: return self.json_response({"ok":False,"error":"Invalid media item"})
                pos=int(conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM user_video_queue WHERE user_id=?",(uid,)).fetchone()[0])
                conn.execute("INSERT INTO user_video_queue(user_id,media_key,media_type,media_id,title,subtitle,poster,href,position,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,media_key) DO UPDATE SET title=excluded.title,subtitle=excluded.subtitle,poster=excluded.poster,href=excluded.href",(uid,item["key"],item["kind"],item["media_id"],item["title"],item["subtitle"],item["poster"],item["href"],pos,app.auth_now()))
            elif action in {"remove","finish"}: conn.execute("DELETE FROM user_video_queue WHERE user_id=? AND media_key=?",(uid,str(data.get("key") or "")))
            elif action=="clear": conn.execute("DELETE FROM user_video_queue WHERE user_id=?",(uid,))
            elif action in {"up","down"}:
                key=str(data.get("key") or ""); keys=[r[0] for r in conn.execute("SELECT media_key FROM user_video_queue WHERE user_id=? ORDER BY position,created_at",(uid,)).fetchall()]
                if key in keys:
                    i=keys.index(key); target=i-1 if action=="up" else i+1
                    if 0<=target<len(keys): keys[i],keys[target]=keys[target],keys[i]
                    for pos,mkey in enumerate(keys,1): conn.execute("UPDATE user_video_queue SET position=? WHERE user_id=? AND media_key=?",(pos,uid,mkey))
            conn.commit(); rows=conn.execute("SELECT * FROM user_video_queue WHERE user_id=? ORDER BY position,created_at",(uid,)).fetchall()
            return self.json_response({"ok":True,"count":len(rows),"next_href":str(rows[0]["href"]) if rows else ""})
        finally: conn.close()

    def api_video_playlists(self, user):
        app=self._vl_app(); data=self.read_json(); action=str(data.get("action") or "add"); uid=int(user["id"]); name=str(data.get("name") or "").strip()[:80]; conn=app.db_connect()
        try:
            if action=="add":
                if not name: return self.json_response({"ok":False,"error":"Playlist name is required"})
                now=app.auth_now(); conn.execute("INSERT INTO user_video_playlists(user_id,name,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET updated_at=excluded.updated_at",(uid,name,now,now)); pid=int(conn.execute("SELECT id FROM user_video_playlists WHERE user_id=? AND name=?",(uid,name)).fetchone()[0]); item=self.video_list_item(data); pos=int(conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM user_video_playlist_items WHERE playlist_id=?",(pid,)).fetchone()[0]); conn.execute("INSERT INTO user_video_playlist_items(playlist_id,media_key,media_type,media_id,title,subtitle,poster,href,position,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(playlist_id,media_key) DO UPDATE SET title=excluded.title,subtitle=excluded.subtitle,poster=excluded.poster,href=excluded.href",(pid,item["key"],item["kind"],item["media_id"],item["title"],item["subtitle"],item["poster"],item["href"],pos,now))
            elif action=="delete": conn.execute("DELETE FROM user_video_playlists WHERE id=? AND user_id=?",(int(data.get("playlist_id") or 0),uid))
            elif action=="remove":
                pid=int(data.get("playlist_id") or 0)
                if conn.execute("SELECT 1 FROM user_video_playlists WHERE id=? AND user_id=?",(pid,uid)).fetchone(): conn.execute("DELETE FROM user_video_playlist_items WHERE playlist_id=? AND media_key=?",(pid,str(data.get("key") or "")))
            conn.commit(); return self.json_response({"ok":True,"name":name})
        finally: conn.close()

    def api_video_playlist_next(self, user):
        q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); pid=int(q.get("playlist_id",[0])[0] or 0); pos=int(q.get("position",[0])[0] or 0); conn=self._vl_app().db_connect()
        try:
            row=conn.execute("SELECT i.href FROM user_video_playlist_items i JOIN user_video_playlists p ON p.id=i.playlist_id WHERE p.id=? AND p.user_id=? ORDER BY i.position,i.created_at LIMIT 1 OFFSET ?",(pid,int(user["id"]),pos+1)).fetchone(); return self.json_response({"ok":True,"next_href":str(row[0]) if row else ""})
        finally: conn.close()

    def video_lists_page(self, user):
        conn=self._vl_app().db_connect(); uid=int(user["id"])
        try:
            queue=conn.execute("SELECT * FROM user_video_queue WHERE user_id=? ORDER BY position,created_at",(uid,)).fetchall(); playlists=conn.execute("SELECT p.*,COUNT(i.media_key) item_count FROM user_video_playlists p LEFT JOIN user_video_playlist_items i ON i.playlist_id=p.id WHERE p.user_id=? GROUP BY p.id ORDER BY p.name",(uid,)).fetchall(); all_items={int(p["id"]):conn.execute("SELECT * FROM user_video_playlist_items WHERE playlist_id=? ORDER BY position,created_at",(int(p["id"]),)).fetchall() for p in playlists}
        finally: conn.close()
        def card(row,controls):
            art=f"<img src='{html.escape(row['poster'] or '')}' alt=''>" if row["poster"] else "<div class='missing'>No poster</div>"; return f"<article class='item'>{art}<div><h3>{html.escape(row['title'])}</h3><p>{html.escape(row['subtitle'] or '')}</p><div class='controls'>{controls}</div></div></article>"
        qhtml="".join(card(r,f"<a href='{html.escape(r['href'])}?play=1&queue=1'>Play</a><button data-queue='up' data-key='{html.escape(r['media_key'])}'>Up</button><button data-queue='down' data-key='{html.escape(r['media_key'])}'>Down</button><button data-queue='remove' data-key='{html.escape(r['media_key'])}'>Remove</button>") for r in queue) or "<p class='empty'>Your queue is empty.</p>"
        phtml=""
        for p in playlists:
            items=all_items[int(p["id"])]; play=f"<a class='primary' href='{html.escape(items[0]['href'])}?play=1&playlist={p['id']}&position=0'>Play all</a>" if items else ""; rows="".join(card(r,f"<a href='{html.escape(r['href'])}'>Open</a><button data-playlist-remove='{p['id']}' data-key='{html.escape(r['media_key'])}'>Remove</button>") for r in items) or "<p class='empty'>No items yet.</p>"; phtml+=f"<section class='playlist'><header><h2>{html.escape(p['name'])} <small>{len(items)} items</small></h2><div>{play}<button data-playlist-delete='{p['id']}'>Delete</button></div></header>{rows}</section>"
        body=f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Queue & Playlists</title><style>:root{{color-scheme:dark;--gold:#f7b733}}*{{box-sizing:border-box}}body{{margin:0;background:#080a0f;color:#fff;font:16px Arial,sans-serif}}main{{width:min(1050px,100%);margin:auto;padding:22px}}nav,.controls,header,header div{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}a,button{{border:1px solid #3a414d;border-radius:999px;background:#202631;color:#fff;padding:10px 15px;text-decoration:none;font-weight:800;cursor:pointer}}.primary{{background:var(--gold);color:#111}}h1{{font-size:clamp(28px,5vw,48px)}}section{{margin:28px 0}}.item{{display:grid;grid-template-columns:72px 1fr;gap:14px;padding:12px 0;border-bottom:1px solid #252b35}}.item img,.missing{{width:72px;height:102px;object-fit:cover;border-radius:5px;background:#151a22;display:grid;place-items:center;font-size:11px;color:#929baa}}h3,p{{margin:3px 0}}p{{color:#abb4c2}}.playlist header{{justify-content:space-between;border-bottom:1px solid #39404b;padding-bottom:10px}}small{{color:#9fa8b7;font-size:13px}}.empty{{padding:20px;border:1px dashed #343b46;border-radius:8px}}</style></head><body><main><nav><a href='/'>Home</a><a href='/movies'>Movies</a><a href='/tv'>TV Shows</a></nav><h1>Queue & Playlists</h1><section><header><h2>Up Next <small>{len(queue)} items</small></h2><button data-queue='clear'>Clear</button></header>{qhtml}</section>{phtml or '<section><h2>Playlists</h2><p class="empty">Add a movie or episode to create your first playlist.</p></section>'}</main><script>async function post(u,d){{await fetch(u,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(d)}});location.reload()}}document.querySelectorAll('[data-queue]').forEach(b=>b.onclick=()=>post('/api/video/queue',{{action:b.dataset.queue,key:b.dataset.key||''}}));document.querySelectorAll('[data-playlist-remove]').forEach(b=>b.onclick=()=>post('/api/video/playlists',{{action:'remove',playlist_id:b.dataset.playlistRemove,key:b.dataset.key}}));document.querySelectorAll('[data-playlist-delete]').forEach(b=>b.onclick=()=>confirm('Delete this playlist?')&&post('/api/video/playlists',{{action:'delete',playlist_id:b.dataset.playlistDelete}}));</script></body></html>"""; return self.render_html(body)
