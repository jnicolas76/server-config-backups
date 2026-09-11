#!/usr/bin/env python3
import html
import json
import os
import shutil
from pathlib import Path

HUB = Path(__file__).resolve().parent
ROOT = HUB.parent
CONFIG = json.loads((HUB / "hub_config.json").read_text(encoding="utf-8"))
MOUNTS = HUB / "collections"


def main():
    MOUNTS.mkdir(parents=True, exist_ok=True)
    collections = []
    expected = set()

    for folder in sorted(ROOT.iterdir(), key=lambda path: path.name.lower()):
        config_path = folder / "gallery_config.json"
        index_path = folder / "web" / "index.html"
        if not folder.is_dir() or not config_path.exists() or not index_path.exists():
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        slug = folder.name
        expected.add(slug)
        mount = MOUNTS / slug
        if mount.is_symlink():
            mount.unlink()
        elif mount.exists():
            raise RuntimeError(f"Refusing to replace non-link mount: {mount}")
        os.symlink(folder / "web", mount, target_is_directory=True)
        collections.append({
            "title": config["title"],
            "slug": slug,
            "primary": config["primary"],
            "secondary": config["secondary"],
            "accent": config["accent"],
        })

    for mount in MOUNTS.iterdir():
        if mount.is_symlink() and mount.name not in expected:
            mount.unlink()

    data = json.dumps(collections, ensure_ascii=True).replace("</", "<\\/")
    title = html.escape(CONFIG["title"])
    document = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:dark;--primary:#d71920;--secondary:#174a9c;--accent:#ffd329;--ink:#080b12}
*{box-sizing:border-box}html,body{height:100%;margin:0}body{overflow:hidden;background:var(--secondary);color:#fff;font:15px Arial,sans-serif}
.app{height:100%;display:grid;grid-template-columns:250px minmax(0,1fr);grid-template-rows:70px minmax(0,1fr)}
header{grid-column:1/-1;display:flex;align-items:center;gap:18px;padding:12px 18px;background:var(--primary);border-bottom:6px solid var(--accent);box-shadow:0 4px 0 var(--ink);z-index:2}
h1{margin:0;font-size:25px;font-weight:900;text-transform:uppercase;text-shadow:3px 3px 0 var(--ink)}
select{display:none;margin-left:auto;max-width:55vw;padding:10px;border:3px solid var(--ink);border-radius:4px;background:#fff;color:var(--ink);font-weight:800}
nav{overflow:auto;padding:14px 10px;background:var(--secondary);border-right:5px solid var(--ink)}
nav button{display:block;width:100%;margin:0 0 9px;padding:11px 10px;border:3px solid var(--ink);border-radius:4px;background:#fff;color:var(--ink);font-weight:900;text-align:left;cursor:pointer;box-shadow:4px 4px 0 var(--ink)}
nav button.active,nav button:hover{background:var(--accent);transform:translate(-1px,-1px)}
main{min-width:0;min-height:0;padding:8px;background:var(--accent)}
iframe{display:block;width:100%;height:100%;border:4px solid var(--ink);background:#fff}
@media(max-width:760px){.app{grid-template-columns:1fr;grid-template-rows:78px minmax(0,1fr)}header{padding:10px 12px;flex-wrap:wrap}h1{font-size:20px}select{display:block}nav{display:none}main{padding:4px}iframe{border-width:2px}}
</style>
</head>
<body>
<div class="app">
<header><h1>__TITLE__</h1><select id="picker" aria-label="Collection"></select></header>
<nav id="nav"></nav>
<main><iframe id="reader" title="Collection reader"></iframe></main>
</div>
<script>
const collections=__COLLECTIONS__,nav=document.querySelector('#nav'),picker=document.querySelector('#picker'),reader=document.querySelector('#reader');
function choose(slug){const item=collections.find(x=>x.slug===slug)||collections[0];if(!item)return;document.documentElement.style.setProperty('--primary',item.primary);document.documentElement.style.setProperty('--secondary',item.secondary);document.documentElement.style.setProperty('--accent',item.accent);reader.src=`collections/${encodeURIComponent(item.slug)}/index.html`;picker.value=item.slug;for(const button of nav.children)button.classList.toggle('active',button.dataset.slug===item.slug);history.replaceState(null,'','#'+encodeURIComponent(item.slug))}
for(const item of collections){const button=document.createElement('button');button.textContent=item.title;button.dataset.slug=item.slug;button.onclick=()=>choose(item.slug);nav.appendChild(button);const option=document.createElement('option');option.value=item.slug;option.textContent=item.title;picker.appendChild(option)}
picker.onchange=()=>choose(picker.value);choose(decodeURIComponent(location.hash.slice(1))||collections[0]?.slug);
</script>
</body>
</html>""".replace("__TITLE__", title).replace("__COLLECTIONS__", data)
    (HUB / "index.html").write_text(document, encoding="utf-8")
    print(f"Built {HUB / 'index.html'} with {len(collections)} collections")


if __name__ == "__main__":
    main()
