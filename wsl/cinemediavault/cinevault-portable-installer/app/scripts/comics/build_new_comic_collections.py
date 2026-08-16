#!/usr/bin/env python3
import html
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


COMICS = Path("/home/jnicolas/Data9/Comics")
LIBRARY = Path("/home/jnicolas/Data9/comic-library")
COLLECTIONS = LIBRARY / "collections"
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}


def natural_key(value):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    ]


def safe_slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def extract_cbz(source, destination):
    destination.mkdir(parents=True)
    pages = []
    with zipfile.ZipFile(source) as archive:
        members = sorted(
            (
                item for item in archive.infolist()
                if not item.is_dir()
                and Path(item.filename).suffix.lower() in IMAGE_EXTENSIONS
            ),
            key=lambda item: natural_key(item.filename),
        )
        for number, member in enumerate(members, 1):
            extension = Path(member.filename).suffix.lower()
            filename = f"page-{number:04d}{extension}"
            with archive.open(member) as source_handle:
                with (destination / filename).open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle)
            pages.append(filename)
    if not pages:
        raise RuntimeError(f"No image pages found in {source}")
    return pages


def render_pdf(source, destination):
    destination.mkdir(parents=True)
    prefix = destination / "page"
    subprocess.run(
        [
            "pdftoppm", "-jpeg", "-jpegopt", "quality=85",
            "-scale-to", "1800", str(source), str(prefix),
        ],
        check=True,
    )
    generated = sorted(destination.glob("page-*.jpg"), key=lambda path: natural_key(path.name))
    pages = []
    for number, path in enumerate(generated, 1):
        target = destination / f"page-{number:04d}.jpg"
        if path != target:
            path.rename(target)
        pages.append(target.name)
    if not pages:
        raise RuntimeError(f"No pages rendered from {source}")
    return pages


def gallery_html(title, issues, colors):
    issue_json = json.dumps(issues, ensure_ascii=True).replace("</", "<\\/")
    escaped_title = html.escape(title)
    primary, secondary, accent = colors
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped_title} Library</title>
<style>
:root{{--primary:{primary};--secondary:{secondary};--accent:{accent};--ink:#080b12}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--secondary);color:#fff;font-family:Arial,sans-serif}}
header{{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:18px;padding:18px 24px;background:var(--primary);border-bottom:7px solid var(--accent);box-shadow:0 5px 0 var(--ink)}}
h1{{margin:0;font-size:28px;text-transform:uppercase;text-shadow:3px 3px 0 var(--ink)}}#count{{font-size:18px;font-weight:900}}
input{{margin-left:auto;width:min(360px,42vw);padding:12px;border:4px solid var(--ink);border-radius:4px;font-size:16px}}
#library{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:28px;padding:30px}}
.issue{{min-width:0;padding:9px;border:4px solid var(--ink);border-radius:4px;background:var(--accent);color:var(--ink);box-shadow:8px 8px 0 var(--ink);cursor:pointer;text-align:left}}
.cover{{display:block;width:100%;aspect-ratio:2/3;object-fit:cover;border:3px solid var(--ink);background:#fff}}
.title{{display:block;padding:12px 6px 5px;font-size:17px;font-weight:900;line-height:1.25}}
dialog{{width:100%;height:100%;max-width:none;max-height:none;margin:0;padding:0;border:0;background:#111;color:#fff}}
dialog::backdrop{{background:#111}}.toolbar{{position:sticky;top:0;z-index:3;display:flex;align-items:center;gap:8px;padding:9px;background:var(--primary);border-bottom:5px solid var(--accent)}}
.toolbar button{{padding:9px 13px;border:3px solid var(--ink);border-radius:4px;background:#fff;font-weight:900;cursor:pointer}}
#readerTitle{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:900}}#pageCount{{margin-left:auto;font-weight:900}}
#pages{{padding:16px 0 40px}}.page{{display:block;max-width:100%;width:auto;max-height:none;margin:0 auto 18px;background:#fff;box-shadow:0 5px 20px #000}}
@media(max-width:700px){{header{{flex-wrap:wrap;padding:14px}}h1{{font-size:21px}}input{{order:3;width:100%;margin:0}}#library{{grid-template-columns:1fr;gap:25px;padding:18px}}.title{{font-size:18px}}#pages{{padding-top:5px}}.page{{width:100%;margin-bottom:8px}}}}
</style>
</head>
<body>
<header><h1>{escaped_title}</h1><span id="count"></span><input id="search" type="search" placeholder="Search issues"></header>
<main id="library"></main>
<dialog id="reader"><div class="toolbar"><button id="close">Close</button><span id="readerTitle"></span><span id="pageCount"></span></div><div id="pages"></div></dialog>
<script>
const issues={issue_json},library=document.querySelector('#library'),search=document.querySelector('#search'),count=document.querySelector('#count'),reader=document.querySelector('#reader'),pages=document.querySelector('#pages'),readerTitle=document.querySelector('#readerTitle'),pageCount=document.querySelector('#pageCount');
function render(){{const query=search.value.trim().toLowerCase(),shown=issues.filter(x=>x.title.toLowerCase().includes(query));library.replaceChildren();for(const issue of shown){{const button=document.createElement('button');button.className='issue';button.innerHTML=`<img class="cover" loading="lazy" src="issues/${{encodeURIComponent(issue.folder)}}/${{encodeURIComponent(issue.cover)}}" alt=""><span class="title">${{issue.title}}</span>`;button.onclick=()=>openIssue(issue);library.appendChild(button)}}count.textContent=`${{shown.length}} issue${{shown.length===1?'':'s'}}`}}
function openIssue(issue){{readerTitle.textContent=issue.title;pageCount.textContent=`${{issue.pages.length}} pages`;pages.replaceChildren();issue.pages.forEach((page,index)=>{{const image=document.createElement('img');image.className='page';image.loading=index<2?'eager':'lazy';image.src=`issues/${{encodeURIComponent(issue.folder)}}/${{encodeURIComponent(page)}}`;image.alt=`${{issue.title}} page ${{index+1}}`;pages.appendChild(image)}});reader.showModal();reader.scrollTop=0}}
document.querySelector('#close').onclick=()=>reader.close();search.oninput=render;render();
</script>
</body>
</html>"""


def build_collection(slug, title, sources, colors):
    target = COLLECTIONS / slug
    if target.exists():
        raise RuntimeError(f"Collection already exists: {target}")
    temporary = COLLECTIONS / f".{slug}-building-{os.getpid()}"
    if temporary.exists():
        shutil.rmtree(temporary)
    issues_root = temporary / "issues"
    issues_root.mkdir(parents=True)
    issues = []
    try:
        for number, (issue_title, source, source_type) in enumerate(sources, 1):
            folder = f"{number:04d}-{safe_slug(issue_title)}"
            issue_dir = issues_root / folder
            print(f"Building {title}: {issue_title}", flush=True)
            if source_type == "cbz":
                pages = extract_cbz(source, issue_dir)
            elif source_type == "pdf":
                pages = render_pdf(source, issue_dir)
            else:
                raise RuntimeError(f"Unsupported source type: {source_type}")
            issues.append({
                "title": issue_title,
                "folder": folder,
                "cover": pages[0],
                "pages": pages,
            })
        (temporary / "index.html").write_text(
            gallery_html(title, issues, colors), encoding="utf-8"
        )
        (temporary / "gallery_config.json").write_text(
            json.dumps({
                "title": title,
                "slug": slug,
                "primary": colors[0],
                "secondary": colors[1],
                "accent": colors[2],
            }, indent=2),
            encoding="utf-8",
        )
        temporary.rename(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "title": title,
        "slug": slug,
        "primary": colors[0],
        "secondary": colors[1],
        "accent": colors[2],
    }


def update_hub(new_collections):
    index_path = LIBRARY / "index.html"
    document = index_path.read_text(encoding="utf-8")
    match = re.search(r"const collections=(\[.*?\]),nav=", document, re.DOTALL)
    if not match:
        raise RuntimeError("Could not locate collection data in library index")
    collections = json.loads(match.group(1))
    by_slug = {item["slug"]: item for item in collections}
    for item in new_collections:
        by_slug[item["slug"]] = item
    updated = sorted(by_slug.values(), key=lambda item: item["title"].casefold())
    replacement = "const collections=" + json.dumps(
        updated, ensure_ascii=True, separators=(",", ":")
    ) + ",nav="
    document = document[:match.start()] + replacement + document[match.end():]
    temporary = index_path.with_suffix(".html.tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(index_path)
    print(f"Updated comic hub with {len(updated)} collections", flush=True)


def main():
    COLLECTIONS.mkdir(parents=True, exist_ok=True)
    built = []
    built.append(build_collection(
        "the-complete-far-side",
        "The Complete Far Side",
        [
            (
                "Volume One - 1980-1986",
                COMICS / "The Complete Far Side" / "Volume One - 1980-1986.cbz",
                "cbz",
            ),
            (
                "Volume Two - 1987-1994",
                COMICS / "The Complete Far Side" / "Volume Two - 1987-1994.cbz",
                "cbz",
            ),
            (
                "The Far Side - Last Chapter and Worse",
                COMICS / "The Complete Far Side"
                / "The Far Side Last Chapter and Worse by Gary Larson.pdf",
                "pdf",
            ),
        ],
        ("#d92525", "#172f3d", "#ffd447"),
    ))
    built.append(build_collection(
        "mad-magazine",
        "Mad Magazine",
        [
            (
                "Mad Magazine - The Big Book of Spy vs. Spy",
                COMICS / "Mad.Magazine-The.Big.Book.Of.Spy.Vs.Spy---420ebooks.pdf",
                "pdf",
            ),
        ],
        ("#d71920", "#171717", "#ffd329"),
    ))
    update_hub(built)


if __name__ == "__main__":
    main()
