#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
import zipfile
import posixpath
from io import BytesIO
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image


DEFAULT_BOOK_ROOT = Path("/home/jnicolas/Data4/Movies/Books")
DEFAULT_PORT = 8111
SCAN_TTL_SECONDS = 300
BOOK_EXTENSIONS = {".epub", ".pdf"}
COVER_DIR = Path(__file__).resolve().parent / "covers"
INDEX_CACHE = Path(__file__).resolve().parent / "book-index-cache.json"
METADATA_CACHE = Path(__file__).resolve().parent / "book-metadata-cache.json"
METADATA_LOG = Path(__file__).resolve().parent / "book-metadata.log"
GOOGLE_BOOKS_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
METADATA_REQUEST_DELAY = float(os.environ.get("BOOKVAULT_METADATA_DELAY", "1.0"))
TTS_OUTPUT_DIR = Path(__file__).resolve().parent / "generated-audiobooks"
TTS_WORKER = Path(__file__).resolve().parent / "book_tts_worker.py"
TTS_PYTHON = Path("/home/jnicolas/cinemediavault-lab/tts-venv/bin/python")
TTS_VOICES = {"male", "female"}
TTS_TTL_SECONDS = 7 * 24 * 3600


def audiobook_key(book_id: str) -> str:
    return hashlib.sha256(book_id.encode("utf-8")).hexdigest()[:24]


def audiobook_paths(key: str, book_path: Path | None = None):
    TTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = (book_path.parent / f"{book_path.stem} - Full Audiobook.mp3") if book_path else None
    return output, TTS_OUTPUT_DIR / f"{key}.json", TTS_OUTPUT_DIR / f"{key}.log"


def read_audiobook_status(key: str) -> dict:
    _output, status, _log = audiobook_paths(key)
    try:
        data = json.loads(status.read_text(encoding="utf-8"))
    except Exception:
        data = {"state": "idle", "progress": 0, "message": "Not generated"}
    output = Path(data.get("output_path", "")) if data.get("output_path") else None
    if data.get("state") == "ready" and (not output or not output.is_file()):
        data = {"state": "idle", "progress": 0, "message": "Not generated"}
    data["key"] = key
    if data.get("state") == "ready":
        data["play_url"] = f"/audiobook/{key}.mp3"
        data["download_url"] = f"/audiobook/{key}.mp3?download=1"
    return data


def cleanup_expired_audiobooks():
    if not TTS_OUTPUT_DIR.exists():
        return
    now = int(time.time())
    for status in TTS_OUTPUT_DIR.glob("*.json"):
        try:
            data = json.loads(status.read_text(encoding="utf-8"))
            if data.get("state") == "ready" and int(data.get("expires_at") or 0) <= now:
                key = status.stem
                output = Path(data.get("output_path", "")) if data.get("output_path") else None
                if output:
                    output.unlink(missing_ok=True)
                (TTS_OUTPUT_DIR / f"{key}.mp3").unlink(missing_ok=True)
                for path in audiobook_paths(key)[1:]:
                    path.unlink(missing_ok=True)
        except Exception:
            continue


def audiobook_panel(book_id: str, chapter: int = 0) -> str:
    return f"""
  <section class="audiobook-panel" id="audiobookPanel" data-book="{html.escape(book_id)}">
    <strong>Listen with Kokoro</strong>
    <label>Narrator <select id="narratorVoice"><option value="female">Female</option><option value="male">Male</option></select></label>
    <button type="button" id="generateBook">Create full audiobook</button>
    <button type="button" id="cancelAudio" hidden>Cancel</button>
    <span id="audioStatus">Nothing generated yet.</span>
    <progress id="audioProgress" max="100" value="0" hidden></progress>
    <audio id="bookAudio" controls preload="metadata" hidden></audio>
    <a id="audioDownload" class="download" hidden>Download audiobook</a>
    <button type="button" id="deleteAudio" hidden>Delete audiobook</button>
  </section>
  <style>
    .audiobook-panel{{display:flex;flex-wrap:wrap;align-items:center;gap:9px;padding:11px 14px;background:#111722;border-bottom:1px solid #30394a}}
    .audiobook-panel label{{display:flex;align-items:center;gap:7px;color:#cbd4e2;font-weight:700}}
    .audiobook-panel button,.audiobook-panel select{{min-height:38px;border:1px solid #3b4658;border-radius:999px;background:#232b38;color:#fff;padding:0 13px;font-weight:800}}
    .audiobook-panel button{{cursor:pointer}} .audiobook-panel progress{{width:160px}} .audiobook-panel audio{{height:40px;max-width:100%}}
    #audioStatus{{color:#b7c2d3;font-size:13px}} @media(max-width:720px){{.audiobook-panel{{align-items:stretch}}.audiobook-panel>*{{max-width:100%}}}}
  </style>
  <script>
  (()=>{{
    const panel=document.getElementById('audiobookPanel'),voice=document.getElementById('narratorVoice'),status=document.getElementById('audioStatus'),progress=document.getElementById('audioProgress'),audio=document.getElementById('bookAudio'),download=document.getElementById('audioDownload'),cancel=document.getElementById('cancelAudio'),del=document.getElementById('deleteAudio');
    let timer=null; const payload=()=>({{id:panel.dataset.book,voice:voice.value}});
    async function refresh(){{clearTimeout(timer);const q=new URLSearchParams(payload()),r=await fetch('/api/audiobook/status?'+q),d=await r.json(),busy=d.state==='starting'||d.state==='running';status.textContent=d.message||d.state;progress.hidden=!busy;progress.value=Number(d.progress||0);cancel.hidden=!busy;voice.disabled=busy||d.state==='ready';if(d.state==='ready'){{audio.hidden=false;audio.src=d.play_url;download.hidden=false;download.href=d.download_url;del.hidden=false}}else{{audio.hidden=true;download.hidden=true;del.hidden=true}}if(busy)timer=setTimeout(refresh,2500)}}
    async function start(){{audio.hidden=true;download.hidden=true;status.textContent='Starting full audiobook…';const r=await fetch('/api/audiobook/generate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload())}}),d=await r.json();if(d.error){{status.textContent=d.error;return}};refresh()}}
    document.getElementById('generateBook').onclick=start;
    cancel.onclick=async()=>{{await fetch('/api/audiobook/cancel',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload())}});refresh()}};
    del.onclick=async()=>{{if(confirm('Delete this generated full audiobook?')){{await fetch('/api/audiobook/delete',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload())}});refresh()}}}};
    voice.onchange=()=>refresh();refresh();
  }})();
  </script>"""


def clean_label(text: str) -> str:
    text = html.unescape(re.sub(r"\s+", " ", text or "")).strip()
    text = re.sub(r"^(chapter|section)\s+\d+\s*[:.-]?\s*", lambda m: m.group(0).strip(), text, flags=re.I)
    return text[:90] if text else ""


def clean_epub_href(opf_dir: str, href: str) -> str:
    href = urllib.parse.unquote((href or "").split("#", 1)[0])
    return posixpath.normpath(posixpath.join(opf_dir, href)).lstrip("/")


def text_from_html_fragment(fragment: str) -> str:
    fragment = re.sub(r"<script\b.*?</script>", "", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<style\b.*?</style>", "", fragment, flags=re.I | re.S)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return clean_label(fragment)


def html_member_title(zf: zipfile.ZipFile, member: str) -> str:
    try:
        text = zf.read(member).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    for pattern in (
        r"<h1\b[^>]*>(.*?)</h1>",
        r"<h2\b[^>]*>(.*?)</h2>",
        r"<h3\b[^>]*>(.*?)</h3>",
        r"<title\b[^>]*>(.*?)</title>",
    ):
        match = re.search(pattern, text, re.I | re.S)
        if match:
            label = text_from_html_fragment(match.group(1))
            if label:
                return label
    return ""


def epub_package_info(path: Path):
    ns_container = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    ns_opf = {"opf": "http://www.idpf.org/2007/opf"}
    ns_ncx = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
    try:
        with zipfile.ZipFile(path) as zf:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", ns_container)
            if rootfile is None:
                return None
            opf_path = rootfile.attrib.get("full-path")
            if not opf_path:
                return None
            opf_dir = posixpath.dirname(opf_path)
            opf = ET.fromstring(zf.read(opf_path))
            manifest = {}
            cover_id = None
            nav_href = None
            ncx_href = None
            for item in opf.findall(".//opf:manifest/opf:item", ns_opf):
                item_id = item.attrib.get("id")
                href = item.attrib.get("href")
                media_type = item.attrib.get("media-type", "")
                if item_id and href:
                    full_href = posixpath.normpath(posixpath.join(opf_dir, href)).lstrip("/")
                    if "cover-image" in item.attrib.get("properties", "").lower():
                        cover_id = item_id
                    if "nav" in item.attrib.get("properties", "").lower():
                        nav_href = full_href
                    if media_type == "application/x-dtbncx+xml":
                        ncx_href = full_href
                    manifest[item_id] = {
                        "href": full_href,
                        "media_type": media_type,
                    }
            if not cover_id:
                meta_cover = opf.find(".//opf:metadata/opf:meta[@name='cover']", ns_opf)
                if meta_cover is not None:
                    cover_id = meta_cover.attrib.get("content")
            chapters = []
            for itemref in opf.findall(".//opf:spine/opf:itemref", ns_opf):
                item = manifest.get(itemref.attrib.get("idref", ""))
                if item and item["media_type"] in {"application/xhtml+xml", "text/html"}:
                    chapters.append(item["href"])
            cover = manifest.get(cover_id, {}).get("href") if cover_id else None
            toc_items = []
            toc_seen = set()
            if ncx_href:
                try:
                    ncx = ET.fromstring(zf.read(ncx_href))
                    for point in ncx.findall(".//ncx:navPoint", ns_ncx):
                        label_el = point.find(".//ncx:navLabel/ncx:text", ns_ncx)
                        content_el = point.find("ncx:content", ns_ncx)
                        label = clean_label(label_el.text if label_el is not None else "")
                        href = clean_epub_href(posixpath.dirname(ncx_href), content_el.attrib.get("src", "") if content_el is not None else "")
                        if href and href not in toc_seen and href in chapters:
                            toc_items.append({"href": href, "label": label})
                            toc_seen.add(href)
                except Exception:
                    pass
            if not toc_items and nav_href:
                try:
                    nav_text = zf.read(nav_href).decode("utf-8", errors="ignore")
                    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", nav_text, re.I | re.S):
                        href = clean_epub_href(posixpath.dirname(nav_href), match.group(1))
                        label = text_from_html_fragment(match.group(2))
                        if href and href not in toc_seen and href in chapters:
                            toc_items.append({"href": href, "label": label})
                            toc_seen.add(href)
                except Exception:
                    pass
            if len(toc_items) >= 2:
                chapter_items = toc_items
                chapters = [item["href"] for item in chapter_items]
            else:
                chapter_items = []
                for index, chapter in enumerate(chapters):
                    label = html_member_title(zf, chapter) or f"Section {index + 1}"
                    chapter_items.append({"href": chapter, "label": label})
            return {
                "opf_path": opf_path,
                "chapters": chapters,
                "chapter_items": chapter_items,
                "manifest": manifest,
                "cover": cover,
            }
    except Exception:
        return None


def first_image_from_html(zf: zipfile.ZipFile, html_path: str) -> str | None:
    try:
        text = zf.read(html_path).decode("utf-8", errors="ignore")
    except Exception:
        return None
    match = re.search(r"""<img[^>]+src=["']([^"']+)["']""", text, re.I)
    if not match:
        match = re.search(r"""<image[^>]+(?:href|xlink:href)=["']([^"']+)["']""", text, re.I)
    if not match:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(html_path), match.group(1)))


def cover_path_for(book_id: str) -> Path:
    return COVER_DIR / f"{book_id}.jpg"


def ensure_pdf_cover(book_path: Path, output: Path) -> Path | None:
    tmp_prefix = COVER_DIR / f".pdf-cover-{output.stem}"
    tmp_file = tmp_prefix.with_suffix(".jpg")
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                "1",
                "-singlefile",
                "-jpeg",
                "-r",
                "120",
                str(book_path),
                str(tmp_prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
        )
        if not tmp_file.is_file() or tmp_file.stat().st_size == 0:
            return None
        with Image.open(tmp_file) as image:
            image = image.convert("RGB")
            image.thumbnail((520, 780))
            image.save(output, "JPEG", quality=86, optimize=True)
        return output
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        return None
    finally:
        try:
            tmp_file.unlink()
        except OSError:
            pass


def ensure_cover(book_id: str, book_path: Path) -> Path | None:
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    output = cover_path_for(book_id)
    if output.is_file() and output.stat().st_size > 0:
        return output
    if book_path.suffix.lower() == ".pdf":
        return ensure_pdf_cover(book_path, output)
    info = epub_package_info(book_path)
    if not info:
        return None
    image_member = info.get("cover")
    try:
        with zipfile.ZipFile(book_path) as zf:
            if not image_member:
                for chapter in info.get("chapters", [])[:5]:
                    image_member = first_image_from_html(zf, chapter)
                    if image_member:
                        break
            if not image_member:
                for item in info.get("manifest", {}).values():
                    if item.get("media_type", "").startswith("image/"):
                        image_member = item["href"]
                        break
            if not image_member:
                return None
            data = zf.read(image_member)
        with Image.open(BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((520, 780))
            image.save(output, "JPEG", quality=86, optimize=True)
        return output
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        return None


def metadata_log(message: str):
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with METADATA_LOG.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def fetch_json(url: str, timeout=15):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "BookVault/1.0 (personal book metadata library)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def normalize_metadata_result(source: str, raw: dict) -> dict:
    if source == "google":
        info = raw.get("volumeInfo") or {}
        image_links = info.get("imageLinks") or {}
        identifiers = {
            item.get("type", ""): item.get("identifier", "")
            for item in info.get("industryIdentifiers") or []
        }
        return {
            "source": "google",
            "external_id": raw.get("id", ""),
            "title": info.get("title", ""),
            "authors": info.get("authors") or [],
            "author": ", ".join(info.get("authors") or []),
            "description": re.sub(r"<[^>]+>", " ", info.get("description") or "").strip(),
            "categories": info.get("categories") or [],
            "published_date": info.get("publishedDate", ""),
            "publisher": info.get("publisher", ""),
            "page_count": info.get("pageCount") or 0,
            "rating": info.get("averageRating"),
            "ratings_count": info.get("ratingsCount") or 0,
            "isbn": identifiers.get("ISBN_13") or identifiers.get("ISBN_10") or "",
            "thumbnail": image_links.get("thumbnail") or image_links.get("smallThumbnail") or "",
        }
    authors = raw.get("author_name") or []
    description = raw.get("description") or ""
    if isinstance(description, dict):
        description = description.get("value", "")
    cover_id = raw.get("cover_i")
    return {
        "source": "openlibrary",
        "external_id": raw.get("key", "").split("/")[-1],
        "title": raw.get("title", ""),
        "authors": authors,
        "author": ", ".join(authors),
        "description": description,
        "categories": (raw.get("subject") or [])[:8],
        "published_date": str(raw.get("first_publish_year") or ""),
        "publisher": (raw.get("publisher") or [""])[0],
        "page_count": raw.get("number_of_pages_median") or 0,
        "rating": raw.get("ratings_average"),
        "ratings_count": raw.get("ratings_count") or 0,
        "isbn": (raw.get("isbn") or [""])[0],
        "thumbnail": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else "",
    }


def search_book_metadata(title: str, author: str = "", limit=8) -> list[dict]:
    title = re.sub(r"[_\[\]()]+", " ", title or "")
    title = re.sub(r"\s+", " ", title).strip()
    author = re.sub(r"^_Unknown Author$", "", author or "", flags=re.I).strip()
    results = []
    google_query = f'intitle:"{title}"'
    if author:
        google_query += f' inauthor:"{author}"'
    params = {"q": google_query, "maxResults": min(max(limit, 10), 20), "printType": "books"}
    if GOOGLE_BOOKS_API_KEY:
        params["key"] = GOOGLE_BOOKS_API_KEY
    try:
        data = fetch_json("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(params))
        results.extend(normalize_metadata_result("google", item) for item in data.get("items") or [])
    except Exception as exc:
        metadata_log(f"Google Books search failed for {title!r}: {exc}")
    ol_params = {
        "title": title,
        "author": author,
        "limit": min(max(limit, 10), 20),
        "fields": "key,title,author_name,first_publish_year,publisher,isbn,cover_i,subject,number_of_pages_median,ratings_average,ratings_count",
    }
    try:
        data = fetch_json("https://openlibrary.org/search.json?" + urllib.parse.urlencode(ol_params))
        existing = {(item["source"], item["external_id"]) for item in results}
        for raw in data.get("docs") or []:
            item = normalize_metadata_result("openlibrary", raw)
            if (item["source"], item["external_id"]) not in existing:
                results.append(item)
    except Exception as exc:
        metadata_log(f"Open Library search failed for {title!r}: {exc}")
    wanted_title = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    wanted_author = re.sub(r"[^a-z0-9]+", " ", author.lower()).strip()
    def score(item):
        item_title = re.sub(r"[^a-z0-9]+", " ", item.get("title", "").lower()).strip()
        item_author = re.sub(r"[^a-z0-9]+", " ", item.get("author", "").lower()).strip()
        value = 20 if item_title == wanted_title else 8 if wanted_title and wanted_title in item_title else 0
        if wanted_author and (wanted_author in item_author or item_author in wanted_author):
            value += 8
        if item.get("description"):
            value += 6
        if item.get("thumbnail"):
            value += 2
        if item.get("rating"):
            value += 1
        return value
    results.sort(key=score, reverse=True)
    return results[:limit]


def fetch_selected_metadata(source: str, external_id: str) -> dict | None:
    try:
        if source == "google":
            raw = fetch_json(f"https://www.googleapis.com/books/v1/volumes/{urllib.parse.quote(external_id)}")
            return normalize_metadata_result("google", raw)
        if source == "openlibrary":
            raw = fetch_json(f"https://openlibrary.org/works/{urllib.parse.quote(external_id)}.json")
            result = normalize_metadata_result("openlibrary", raw)
            result["external_id"] = external_id
            return result
    except Exception as exc:
        metadata_log(f"Selected metadata lookup failed for {source}:{external_id}: {exc}")
    return None


class BookIndex:
    def __init__(self, root: Path):
        self.root = root
        self.last_scan = 0.0
        self.books = []
        self.by_id = {}
        self.metadata = {}
        self.metadata_lock = threading.Lock()
        self.metadata_worker = None
        self.load_metadata()
        self.load_cache()

    def _book_id(self, rel_path: str) -> str:
        return hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]

    def scan(self, force=False):
        now = time.monotonic()
        if not force and self.books:
            return
        books = []
        by_id = {}
        if self.root.is_dir():
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = [
                    name for name in dirnames
                    if name not in {"@eaDir", "@EADIR", "$RECYCLE.BIN", "System Volume Information"}
                ]
                for name in filenames:
                    path = Path(dirpath) / name
                    if path.suffix.lower() not in BOOK_EXTENSIONS:
                        continue
                    try:
                        stat = path.stat()
                        rel = str(path.relative_to(self.root))
                    except OSError:
                        continue
                    parts = Path(rel).parts
                    author = parts[0] if len(parts) > 1 else "_Unknown Author"
                    language = "spanish" if author.lower() in {"en español", "en espanol", "spanish", "español", "espanol"} else "english"
                    title = path.stem
                    item = {
                        "id": self._book_id(rel),
                        "author": author,
                        "language": language,
                        "title": title,
                        "file_name": path.name,
                        "relative_path": rel,
                        "size_bytes": stat.st_size,
                        "size": human_size(stat.st_size),
                        "format": path.suffix.lower().lstrip(".").upper(),
                        "modified": int(stat.st_mtime),
                    }
                    item["cover"] = f"/cover/{item['id']}"
                    item.update(self.metadata.get(item["id"], {}))
                    books.append(item)
                    by_id[item["id"]] = path
        books.sort(key=lambda item: (item["author"].lower(), item["title"].lower()))
        self.books = books
        self.by_id = by_id
        self.last_scan = now
        self.save_cache()
        self.start_metadata_worker()

    def load_metadata(self):
        try:
            data = json.loads(METADATA_CACHE.read_text(encoding="utf-8"))
            self.metadata = data.get("books") or {}
        except Exception:
            self.metadata = {}

    def save_metadata(self):
        with self.metadata_lock:
            try:
                tmp = METADATA_CACHE.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps({"version": 1, "books": self.metadata}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                tmp.replace(METADATA_CACHE)
            except Exception as exc:
                metadata_log(f"Metadata cache save failed: {exc}")

    def apply_metadata(self, book_id: str, metadata: dict, manual=False):
        allowed = {
            "source", "external_id", "title", "authors", "author", "description",
            "categories", "published_date", "publisher", "page_count", "rating",
            "ratings_count", "isbn", "thumbnail",
        }
        clean = {key: metadata.get(key) for key in allowed if metadata.get(key) not in (None, "", [])}
        clean["metadata_status"] = "matched"
        clean["metadata_manual"] = bool(manual)
        clean["metadata_updated"] = int(time.time())
        self.metadata[book_id] = clean
        for item in self.books:
            if item.get("id") == book_id:
                item.update(clean)
                break
        self.save_metadata()
        self.save_cache()

    def mark_no_match(self, book_id: str):
        value = {
            "metadata_status": "not_found",
            "metadata_updated": int(time.time()),
            "metadata_manual": False,
        }
        self.metadata[book_id] = value
        for item in self.books:
            if item.get("id") == book_id:
                item.update(value)
                break
        self.save_metadata()

    def start_metadata_worker(self):
        if self.metadata_worker and self.metadata_worker.is_alive():
            return
        pending = [
            item["id"] for item in self.books
            if not item.get("metadata_status") and not self.metadata.get(item["id"], {}).get("metadata_status")
        ]
        if not pending:
            return
        self.metadata_worker = threading.Thread(
            target=self._metadata_worker,
            args=(pending,),
            name="bookvault-metadata",
            daemon=True,
        )
        self.metadata_worker.start()

    def _metadata_worker(self, book_ids):
        metadata_log(f"Background metadata matching started for {len(book_ids)} books")
        for book_id in book_ids:
            book = next((item for item in self.books if item.get("id") == book_id), None)
            if not book or self.metadata.get(book_id, {}).get("metadata_manual"):
                continue
            results = search_book_metadata(book.get("title", ""), book.get("author", ""), limit=3)
            if results:
                self.apply_metadata(book_id, results[0], manual=False)
                metadata_log(f"Matched {book.get('title')} -> {results[0].get('title')} ({results[0].get('source')})")
            else:
                self.mark_no_match(book_id)
                metadata_log(f"No match for {book.get('title')}")
            time.sleep(max(METADATA_REQUEST_DELAY, 0.2))
        metadata_log("Background metadata matching finished")

    def load_cache(self):
        try:
            data = json.loads(INDEX_CACHE.read_text(encoding="utf-8"))
            if data.get("root") != str(self.root):
                return
            books = data.get("books") or []
            by_id = {}
            for item in books:
                rel = item.get("relative_path")
                book_id = item.get("id")
                if rel and book_id:
                    by_id[book_id] = self.root / rel
            self.books = books
            self.by_id = by_id
            for item in self.books:
                item.update(self.metadata.get(item.get("id", ""), {}))
            self.last_scan = time.monotonic()
        except Exception:
            pass

    def save_cache(self):
        try:
            tmp = INDEX_CACHE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"root": str(self.root), "books": self.books}, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(INDEX_CACHE)
        except Exception:
            pass


def human_size(size_bytes):
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024


def render_index():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BookVault</title>
  <style>
    :root { color-scheme: dark; --gold:#f6b82e; --panel:#17191f; --muted:#aeb4c0; --line:#2b303a; }
    * { box-sizing: border-box; }
    body { margin:0; background:#08090c; color:#f5f6f8; font-family:Inter,Segoe UI,Arial,sans-serif; }
    header { position:sticky; top:0; z-index:10; background:rgba(8,9,12,.94); border-bottom:1px solid #20242b; padding:18px 24px 14px; backdrop-filter:blur(16px); }
    .top { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
    h1 { margin:0; font-size:34px; letter-spacing:0; }
    h1 span { color:var(--gold); }
    .sub { color:var(--muted); margin-top:4px; font-size:14px; }
    .controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    button, .pill { border:1px solid #343945; background:#20242b; color:#f5f6f8; border-radius:999px; padding:10px 15px; font-weight:700; cursor:pointer; text-decoration:none; }
    button.primary { background:var(--gold); color:#111; border-color:#8b620e; }
    .tabs { display:flex; gap:8px; margin-top:14px; overflow-x:auto; scrollbar-width:none; }
    .tabs::-webkit-scrollbar { display:none; }
    .tab { flex:0 0 auto; border:1px solid #343945; background:#20242b; color:#f5f6f8; border-radius:999px; padding:8px 13px; font-weight:800; cursor:pointer; }
    .tab.active { background:var(--gold); color:#111; border-color:#8b620e; }
    input { width:min(720px,100%); margin-top:16px; border:1px solid #343945; background:#151820; color:#fff; border-radius:12px; padding:13px 15px; font-size:16px; outline:none; }
    main { padding:22px 24px 44px; }
    .stats { color:var(--muted); margin-bottom:18px; }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; max-width:760px; margin:0 auto; min-width:0; }
    .book { background:linear-gradient(180deg,#1b1f28,#111319); border:1px solid var(--line); border-radius:14px; min-height:310px; padding:14px; display:flex; flex-direction:column; min-width:0; justify-content:space-between; box-shadow:0 12px 32px rgba(0,0,0,.28); cursor:pointer; transition:transform .16s ease,border-color .16s ease; }
    .book:hover { transform:translateY(-2px); border-color:#566070; }
    .cover { width:min(132px,100%); height:198px; margin:0 auto 13px; border-radius:8px; background:radial-gradient(circle at 30% 20%,#384153,#14171f 65%); display:flex; align-items:center; justify-content:center; text-align:center; padding:0; color:#fff; font-size:25px; font-weight:900; line-height:1.05; border:1px solid #303642; overflow:hidden; box-shadow:6px 8px 18px rgba(0,0,0,.38); }
    .cover img { width:100%; height:100%; object-fit:cover; display:block; }
    .title { font-size:17px; font-weight:800; line-height:1.2; margin-bottom:7px; overflow-wrap:anywhere; }
    .author { color:var(--muted); font-size:13px; min-height:32px; overflow-wrap:anywhere; }
    .summary { color:#c7ccd6; font-size:12px; line-height:1.35; margin:8px 0; display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
    .rating { color:var(--gold); font-weight:800; }
    .meta { color:#8f98a8; font-size:12px; margin:10px 0 12px; }
    .actions { display:flex; gap:7px; justify-content:center; }
    .actions a { flex:0 1 auto; min-width:74px; text-align:center; border-radius:999px; padding:7px 10px; text-decoration:none; font-weight:800; font-size:13px; }
    .download { background:#f6b82e; color:#15100a; }
    .fix { background:#303642; color:#fff; border:0; }
    .empty { border:1px dashed #38404e; border-radius:14px; padding:28px; color:var(--muted); }
    dialog { width:min(720px,calc(100% - 24px)); max-height:86vh; border:1px solid #343945; border-radius:14px; background:#101218; color:#fff; padding:0; }
    dialog::backdrop { background:rgba(0,0,0,.78); }
    .modal-head { position:sticky; top:0; background:#101218; display:flex; justify-content:space-between; align-items:center; gap:12px; padding:16px; border-bottom:1px solid var(--line); }
    .modal-head h2 { margin:0; font-size:20px; }
    .modal-body { padding:16px; }
    .match-search { display:flex; gap:8px; margin-bottom:14px; }
    .match-search input { margin:0; flex:1; }
    .match-list { display:grid; gap:10px; }
    .match { display:grid; grid-template-columns:72px 1fr auto; gap:12px; align-items:start; border:1px solid var(--line); background:#171a22; border-radius:10px; padding:10px; }
    .match img { width:72px; height:105px; object-fit:cover; background:#242832; }
    .match h3 { margin:0 0 4px; font-size:16px; }
    .match p { margin:4px 0; color:var(--muted); font-size:12px; line-height:1.35; }
    @media (max-width:640px) {
      header { padding:16px; }
      main { padding:16px; }
      h1 { font-size:28px; }
      .grid { grid-template-columns:repeat(3,minmax(0,1fr)); gap:9px; }
      .book { min-height:220px; padding:8px; border-radius:10px; }
      .cover { width:min(92px,100%); height:138px; font-size:17px; margin-bottom:8px; }
      .title { font-size:12px; }
      .author { font-size:11px; min-height:25px; }
      .meta { font-size:10px; margin:7px 0 8px; }
      .actions a { min-width:0; padding:6px 8px; font-size:11px; }
      .actions { flex-direction:column; }
      .tab { padding:7px 10px; font-size:12px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="top">
      <div>
        <h1>Book<span>Vault</span></h1>
        <div class="sub" id="subtitle">Loading local book library...</div>
      </div>
      <div class="controls">
        <a class="pill" href="/">Home</a>
        <button class="primary" id="refresh">Scan</button>
      </div>
    </div>
    <div class="tabs" role="tablist" aria-label="Book language">
      <button class="tab active" data-language="all" type="button">All</button>
      <button class="tab" data-language="english" type="button">English</button>
      <button class="tab" data-language="spanish" type="button">En Espa&ntilde;ol</button>
    </div>
    <input id="search" placeholder="Search books, authors, or file names" autocomplete="off">
  </header>
  <main>
    <div class="stats" id="stats"></div>
    <div class="grid" id="grid"></div>
  </main>
  <dialog id="matchDialog">
    <div class="modal-head"><h2>Fix Match</h2><button id="closeMatch" type="button">Close</button></div>
    <div class="modal-body">
      <div class="match-search"><input id="matchQuery" placeholder="Book title and author"><button class="primary" id="runMatch" type="button">Search</button></div>
      <div id="matchStatus" class="stats"></div><div id="matchList" class="match-list"></div>
    </div>
  </dialog>
  <script>
    let books = [];
    const grid = document.getElementById('grid');
    const search = document.getElementById('search');
    const stats = document.getElementById('stats');
    const subtitle = document.getElementById('subtitle');
    let activeLanguage = 'all';
    let activeBook = null;
    function escapeHtml(text) {
      return String(text || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }
    function initials(book) {
      const source = book.title || book.file_name || 'EPUB';
      return source.split(/\\s+/).filter(Boolean).slice(0, 3).map(w => w[0]).join('').toUpperCase();
    }
    function render() {
      const q = search.value.trim().toLowerCase();
      const languageBooks = books.filter(b => activeLanguage === 'all' || (b.language || 'english') === activeLanguage);
      const filtered = languageBooks.filter(b => !q || [b.title,b.author,b.file_name].join(' ').toLowerCase().includes(q));
      const languageLabel = activeLanguage === 'spanish' ? 'Spanish' : activeLanguage === 'english' ? 'English' : 'All';
      stats.textContent = `${filtered.length} of ${languageBooks.length} ${languageLabel} books`;
      if (!filtered.length) {
        grid.innerHTML = '<div class="empty">No books match that search.</div>';
        return;
      }
      grid.innerHTML = filtered.map(book => `
        <article class="book" onclick="location.href='/reader?id=${book.id}'" tabindex="0" role="link" aria-label="Open ${escapeHtml(book.title)}">
          <div>
            <div class="cover" data-initials="${escapeHtml(initials(book))}"><img src="${book.cover}" alt="" onerror="this.parentElement.textContent=this.parentElement.dataset.initials; this.remove();"></div>
            <div class="title">${escapeHtml(book.title)}</div>
            <div class="author">${escapeHtml(book.author)}</div>
            ${book.description ? `<div class="summary">${escapeHtml(book.description)}</div>` : ''}
            <div class="meta">${escapeHtml(book.size)} ${escapeHtml(book.format || 'BOOK')} ${book.rating ? `<span class="rating">★ ${Number(book.rating).toFixed(1)}</span>` : ''}</div>
          </div>
          <div class="actions">
            <a class="download" href="/download/${book.id}" onclick="event.stopPropagation()">Download</a>
            ${book.audiobook && book.audiobook.state === 'ready' ? `<a class="download" href="${book.audiobook.download_url}" onclick="event.stopPropagation()">Audiobook</a><button class="fix" type="button" onclick="event.stopPropagation(); deleteAudiobook('${book.id}')">Delete audio</button>` : ''}
            ${book.audiobook && ['starting','running'].includes(book.audiobook.state) ? `<span class="audio-job">Audiobook in progress: ${escapeHtml(book.audiobook.message || '')}</span>` : ''}
            <button class="fix" type="button" onclick="event.stopPropagation(); openMatch('${book.id}')">Fix Match</button>
          </div>
        </article>`).join('');
    }
    async function load(force=false) {
      subtitle.textContent = force ? 'Scanning Books folder...' : 'Loading local book library...';
      const res = await fetch('/api/books' + (force ? '?refresh=1' : ''));
      const data = await res.json();
      books = data.books || [];
      subtitle.textContent = `${data.count || books.length} books from the Movies NFS Books folder`;
      render();
    }
    async function deleteAudiobook(id) {
      if (!confirm('Delete the generated full audiobook?')) return;
      await fetch('/api/audiobook/delete', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
      await load(false);
    }
    search.addEventListener('input', render);
    document.querySelectorAll('.tab').forEach(tab => {
      tab.addEventListener('click', () => {
        activeLanguage = tab.dataset.language || 'all';
        document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === tab));
        render();
      });
    });
    grid.addEventListener('keydown', e => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('book')) {
        e.preventDefault();
        e.target.click();
      }
    });
    document.getElementById('refresh').addEventListener('click', () => load(true));
    const matchDialog = document.getElementById('matchDialog');
    const matchQuery = document.getElementById('matchQuery');
    const matchList = document.getElementById('matchList');
    const matchStatus = document.getElementById('matchStatus');
    function openMatch(bookId) {
      activeBook = books.find(book => book.id === bookId);
      if (!activeBook) return;
      matchQuery.value = [activeBook.title, activeBook.author].filter(Boolean).join(' ');
      matchList.innerHTML = '';
      matchStatus.textContent = '';
      matchDialog.showModal();
      runMatch();
    }
    async function runMatch() {
      if (!activeBook) return;
      matchStatus.textContent = 'Searching Google Books and Open Library...';
      matchList.innerHTML = '';
      const url = `/api/match-options?id=${encodeURIComponent(activeBook.id)}&q=${encodeURIComponent(matchQuery.value)}`;
      const data = await fetch(url).then(response => response.json());
      const results = data.results || [];
      matchStatus.textContent = results.length ? `${results.length} possible matches` : 'No matches found. Try a simpler title or add the author.';
      matchList.innerHTML = results.map((item, index) => `
        <div class="match">
          ${item.thumbnail ? `<img src="${escapeHtml(item.thumbnail.replace('http:','https:'))}" alt="">` : '<div></div>'}
          <div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.author || 'Unknown author')} ${item.published_date ? `• ${escapeHtml(item.published_date)}` : ''}</p><p>${escapeHtml(item.description || 'No summary available.')}</p><p>${escapeHtml(item.source)}</p></div>
          <button class="primary" type="button" onclick="chooseMatch(${index})">Use</button>
        </div>`).join('');
      window.currentMatches = results;
    }
    async function chooseMatch(index) {
      const match = (window.currentMatches || [])[index];
      if (!activeBook || !match) return;
      matchStatus.textContent = 'Saving match...';
      const response = await fetch('/api/fix-match', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:activeBook.id, metadata:match})});
      if (!response.ok) { matchStatus.textContent = 'Could not save that match.'; return; }
      matchDialog.close();
      await load(false);
    }
    document.getElementById('runMatch').addEventListener('click', runMatch);
    document.getElementById('closeMatch').addEventListener('click', () => matchDialog.close());
    load();
  </script>
</body>
</html>"""


def render_reader(book, chapter_items, current):
    title = html.escape(book.get("title", "Book"))
    author = html.escape(book.get("author", ""))
    chapters = [item.get("href", "") for item in chapter_items if item.get("href")]
    current = max(0, min(current, max(0, len(chapters) - 1)))
    prev_href = f"/reader?id={book['id']}&chapter={current - 1}" if current > 0 else "#"
    next_href = f"/reader?id={book['id']}&chapter={current + 1}" if current + 1 < len(chapters) else "#"
    content_href = f"/content/{book['id']}/{urllib.parse.quote(chapters[current])}" if chapters else ""
    chapter_options = "\n".join(
        f'<option value="{i}" {"selected" if i == current else ""}>{html.escape(chapter_items[i].get("label") or f"Section {i + 1}")}</option>'
        for i in range(len(chapter_items))
    )
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>""" + title + """</title>
  <style>
    :root { color-scheme: dark; --gold:#f6b82e; }
    * { box-sizing:border-box; }
    body { margin:0; background:#08090c; color:#fff; font-family:Inter,Segoe UI,Arial,sans-serif; overflow:auto; }
    header { min-height:62px; display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 14px; background:#0d0f14; border-bottom:1px solid #252a34; }
    a, select { border:1px solid #353b47; background:#1c2028; color:#fff; border-radius:999px; padding:9px 13px; text-decoration:none; font-weight:800; cursor:pointer; }
    .download { background:var(--gold); color:#111; border-color:#8b620e; }
    #title { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; color:#d8dce4; font-weight:800; line-height:1.2; }
    #title small { display:block; color:#9aa2b1; font-weight:600; }
    iframe { width:100%; min-height:calc(100vh - 62px); height:calc(100vh - 62px); border:0; background:#f5f2eb; color:#111; display:block; }
    .nav { display:flex; gap:8px; align-items:center; }
    #message { padding:24px; color:#111; background:#f5f2eb; height:calc(100vh - 58px); }
    @media (max-width:720px) { header { flex-wrap:wrap; } #title { order:3; width:100%; } iframe { min-height:calc(100vh - 105px); height:calc(100vh - 105px); } }
  </style>
</head>
<body>
  <header>
    <a href="/">BookVault</a>
    <div id="title">""" + title + """<small>""" + author + """</small></div>
    <div class="nav">
      <select id="chapter">""" + chapter_options + """</select>
    </div>
  </header>
  """ + audiobook_panel(book["id"], current) + (f'<iframe id="reader-frame" src="{content_href}" scrolling="yes"></iframe>' if content_href else '<div id="message">This EPUB did not expose readable chapters.</div>') + """
  <script>
    const prevPage = """ + json.dumps(prev_href if prev_href != "#" else "") + """;
    const nextPage = """ + json.dumps(next_href if next_href != "#" else "") + """;
    document.getElementById('chapter').addEventListener('change', e => {
      location.href = '/reader?id=""" + book["id"] + """&chapter=' + e.target.value;
    });
    let startX = 0;
    let startY = 0;
    document.addEventListener('touchstart', e => {
      const touch = e.changedTouches[0];
      startX = touch.clientX;
      startY = touch.clientY;
    }, {passive:true});
    document.addEventListener('touchend', e => {
      const touch = e.changedTouches[0];
      const dx = touch.clientX - startX;
      const dy = touch.clientY - startY;
      if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
      if (dx < 0 && nextPage) location.href = nextPage;
      if (dx > 0 && prevPage) location.href = prevPage;
    }, {passive:true});
    document.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight' && nextPage) location.href = nextPage;
      if (e.key === 'ArrowLeft' && prevPage) location.href = prevPage;
    });
  </script>
</body>
</html>"""


def render_pdf_reader(book):
    title = html.escape(book.get("title", "Book"))
    author = html.escape(book.get("author", ""))
    source = f"/file/{book['id']}"
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>""" + title + """</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:#08090c; color:#fff; font-family:Inter,Segoe UI,Arial,sans-serif; }
    header { min-height:62px; display:flex; align-items:center; gap:12px; padding:10px 14px; background:#0d0f14; border-bottom:1px solid #252a34; }
    a { border:1px solid #353b47; background:#1c2028; color:#fff; border-radius:999px; padding:9px 13px; text-decoration:none; font-weight:800; }
    #title { min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; color:#d8dce4; font-weight:800; line-height:1.2; }
    #title small { display:block; color:#9aa2b1; font-weight:600; }
    iframe { width:100%; height:calc(100vh - 62px); border:0; background:#1b1e25; display:block; }
  </style>
</head>
<body>
  <header>
    <a href="/">BookVault</a>
    <div id="title">""" + title + """<small>""" + author + """ - PDF</small></div>
  </header>
  """ + audiobook_panel(book["id"], 0) + """
  <iframe src=\"""" + source + """\"></iframe>
</body>
</html>"""


class BookVaultHandler(BaseHTTPRequestHandler):
    index: BookIndex = None

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def send_text(self, text, content_type="text/html; charset=utf-8", status=HTTPStatus.OK):
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, data):
        self.send_text(json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def send_file(self, path: Path, download=False):
        if path is None or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Book file missing")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/epub+zip"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        if download:
            safe = path.name.replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.end_headers()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def send_epub_member(self, book_path: Path, member: str, prev_page: str = "", next_page: str = ""):
        if book_path is None or not book_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Book file missing")
            return
        member = posixpath.normpath(urllib.parse.unquote(member)).lstrip("/")
        if member.startswith("../"):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid EPUB path")
            return
        try:
            with zipfile.ZipFile(book_path) as zf:
                data = zf.read(member)
        except Exception:
            self.send_error(HTTPStatus.NOT_FOUND, "EPUB content missing")
            return
        content_type = mimetypes.guess_type(member)[0] or "application/octet-stream"
        if member.lower().endswith((".xhtml", ".html", ".htm")):
            # Serve EPUB XHTML as HTML so mobile browsers do not expose strict
            # XML parse errors from imperfect EPUB markup or injected controls.
            content_type = "text/html; charset=utf-8"
            try:
                text = data.decode("utf-8", errors="ignore")
                swipe_script = f"""
<script id="bookvault-swipe-fix">
(function() {{
  var prevPage = {json.dumps(prev_page)};
  var nextPage = {json.dumps(next_page)};
  var startX = 0, startY = 0;
  document.addEventListener('touchstart', function(e) {{
    var t = e.changedTouches[0];
    startX = t.clientX; startY = t.clientY;
  }}, {{passive:true}});
  document.addEventListener('touchend', function(e) {{
    var t = e.changedTouches[0];
    var dx = t.clientX - startX;
    var dy = t.clientY - startY;
    if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
    if (dx < 0) {{ if (nextPage) window.top.location.href = nextPage; }}
    if (dx > 0) {{ if (prevPage) window.top.location.href = prevPage; }}
  }}, {{passive:true}});
  document.addEventListener('keydown', function(e) {{
    if (e.key === 'ArrowRight') {{ if (nextPage) window.top.location.href = nextPage; }}
    if (e.key === 'ArrowLeft') {{ if (prevPage) window.top.location.href = prevPage; }}
  }});
}})();
</script>
"""
                override = """
<style id="bookvault-reader-fix">
html, body {
  height: auto !important;
  min-height: 100% !important;
  max-height: none !important;
  overflow: visible !important;
  overflow-y: visible !important;
}
body {
  margin: 0 auto !important;
  padding: 24px !important;
  max-width: 820px !important;
  box-sizing: border-box !important;
  line-height: 1.55 !important;
}
img, svg {
  max-width: 100% !important;
  height: auto !important;
}
* {
  max-height: none !important;
}
@media (max-width: 720px) {
  body { padding: 18px !important; font-size: 18px !important; }
}
</style>
"""
                if "</head>" in text.lower():
                    index = text.lower().find("</head>")
                    text = text[:index] + override + text[index:]
                else:
                    text = override + text
                if "</body>" in text.lower():
                    body_index = text.lower().rfind("</body>")
                    text = text[:body_index] + swipe_script + text[body_index:]
                else:
                    text += swipe_script
                data = text.encode("utf-8")
            except Exception:
                pass
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_cover(self, book_id: str):
        book_path = self.index.by_id.get(book_id)
        cover_path = ensure_cover(book_id, book_path) if book_path else None
        if not cover_path:
            self.send_error(HTTPStatus.NOT_FOUND, "Cover not found")
            return
        self.send_file(cover_path, download=False)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        cleanup_expired_audiobooks()
        force = query.get("refresh", ["0"])[0] == "1"
        if force:
            self.index.scan(force=True)

        if path == "/":
            self.send_text(render_index())
            return
        if path == "/reader":
            book_id = query.get("id", [""])[0]
            try:
                current = int(query.get("chapter", ["0"])[0])
            except ValueError:
                current = 0
            book = next((item for item in self.index.books if item["id"] == book_id), None)
            book_path = self.index.by_id.get(book_id)
            if book and book_path and book_path.suffix.lower() == ".pdf":
                self.send_text(render_pdf_reader(book))
                return
            info = epub_package_info(book_path) if book_path else None
            if not book or not info:
                self.send_error(HTTPStatus.NOT_FOUND, "Book reader data missing")
                return
            self.send_text(render_reader(book, info.get("chapter_items") or [{"href": chapter, "label": f"Section {i + 1}"} for i, chapter in enumerate(info["chapters"])], current))
            return
        if path == "/api/books":
            if not self.index.books:
                self.index.load_cache()
            books = []
            for item in self.index.books:
                enriched = dict(item)
                enriched["audiobook"] = read_audiobook_status(audiobook_key(item["id"]))
                books.append(enriched)
            self.send_json({"count": len(books), "root": str(self.index.root), "books": books})
            return
        if path == "/api/match-options":
            book_id = query.get("id", [""])[0]
            book = next((item for item in self.index.books if item.get("id") == book_id), None)
            if not book:
                self.send_json({"error": "Book not found", "results": []})
                return
            search_text = query.get("q", [""])[0].strip()
            title = search_text or book.get("title", "")
            author = "" if search_text else book.get("author", "")
            self.send_json({"id": book_id, "results": search_book_metadata(title, author, limit=10)})
            return
        if path == "/api/metadata-status":
            worker = self.index.metadata_worker
            matched = sum(1 for item in self.index.books if item.get("metadata_status") == "matched")
            missing = sum(1 for item in self.index.books if item.get("metadata_status") == "not_found")
            self.send_json({
                "running": bool(worker and worker.is_alive()),
                "matched": matched,
                "not_found": missing,
                "pending": max(0, len(self.index.books) - matched - missing),
            })
            return
        if path == "/api/audiobook/status":
            book_id = query.get("id", [""])[0]
            if book_id not in self.index.by_id:
                self.send_json({"error": "Invalid audiobook request"})
                return
            self.send_json(read_audiobook_status(audiobook_key(book_id)))
            return
        if path.startswith("/audiobook/"):
            name = path.rsplit("/", 1)[-1]
            key = name[:-4] if name.endswith(".mp3") else ""
            if not re.fullmatch(r"[a-f0-9]{24}", key):
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid audiobook key")
                return
            data = read_audiobook_status(key)
            output = Path(data.get("output_path", "")) if data.get("output_path") else None
            self.send_file(output if output and output.is_file() else None, download=query.get("download", ["0"])[0] == "1")
            return
        if path.startswith("/epub/") or path.startswith("/file/") or path.startswith("/download/"):
            book_id = path.rsplit("/", 1)[-1]
            book_path = self.index.by_id.get(book_id)
            self.send_file(book_path, download=path.startswith("/download/"))
            return
        if path.startswith("/cover/"):
            self.send_cover(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/content/"):
            parts = path.split("/", 3)
            if len(parts) < 4:
                self.send_error(HTTPStatus.NOT_FOUND, "Missing EPUB content path")
                return
            book_id = parts[2]
            member = parts[3]
            book_path = self.index.by_id.get(book_id)
            info = epub_package_info(book_path) if book_path else None
            chapter_items = info.get("chapter_items") if info else []
            chapters = [item.get("href", "") for item in chapter_items if item.get("href")]
            current = chapters.index(urllib.parse.unquote(member)) if urllib.parse.unquote(member) in chapters else 0
            prev_page = f"/reader?id={book_id}&chapter={current - 1}" if current > 0 else ""
            next_page = f"/reader?id={book_id}&chapter={current + 1}" if current + 1 < len(chapters) else ""
            self.send_epub_member(book_path, member, prev_page, next_page)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8")) if content_length else {}
        except Exception:
            self.send_json({"error": "Invalid JSON"})
            return
        if parsed.path == "/api/fix-match":
            book_id = str(payload.get("id") or "")
            metadata = payload.get("metadata") or {}
            if book_id not in self.index.by_id or not metadata.get("title"):
                self.send_json({"error": "Book or metadata selection missing"})
                return
            self.index.apply_metadata(book_id, metadata, manual=True)
            self.send_json({"ok": True, "id": book_id})
            return
        if parsed.path in {"/api/audiobook/generate", "/api/audiobook/cancel", "/api/audiobook/delete"}:
            book_id = str(payload.get("id") or "")
            voice = str(payload.get("voice") or "female")
            book_path = self.index.by_id.get(book_id)
            book = next((item for item in self.index.books if item.get("id") == book_id), None)
            if not book_path or not book or voice not in TTS_VOICES:
                self.send_json({"error": "Invalid audiobook request"})
                return
            key = audiobook_key(book_id)
            output, status_path, log_path = audiobook_paths(key, book_path)
            current = read_audiobook_status(key)
            if parsed.path.endswith("/delete"):
                if current.get("state") in {"starting", "running"}:
                    self.send_json({"error": "Audiobook generation is still in progress"})
                    return
                old_output = Path(current.get("output_path", "")) if current.get("output_path") else output
                old_output.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
                log_path.unlink(missing_ok=True)
                self.send_json({"ok": True, "state": "idle", "message": "Audiobook deleted"})
                return
            if parsed.path.endswith("/cancel"):
                pid = int(current.get("pid") or 0)
                if pid > 1:
                    try:
                        os.kill(pid, 15)
                    except (ProcessLookupError, PermissionError):
                        pass
                output.with_suffix(".part.mp3").unlink(missing_ok=True)
                state = {"state": "cancelled", "progress": current.get("progress", 0), "message": "Generation cancelled", "pid": 0, "updated_at": int(time.time())}
                status_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
                self.send_json({"ok": True, **state})
                return
            if current.get("state") in {"starting", "running", "ready"}:
                self.send_json({"ok": True, **current})
                return
            initial = {"state": "starting", "progress": 0, "message": "Full audiobook generation already in progress", "pid": 0, "created_at": int(time.time()), "voice": voice, "output_path": str(output)}
            status_path.write_text(json.dumps(initial, indent=2), encoding="utf-8")
            command = [str(TTS_PYTHON), str(TTS_WORKER), "--book", str(book_path), "--voice", voice, "--output", str(output), "--status", str(status_path), "--title", str(book.get("title") or book_path.stem)]
            with log_path.open("ab") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(Path(__file__).resolve().parent))
            initial["pid"] = process.pid
            status_path.write_text(json.dumps(initial, indent=2), encoding="utf-8")
            self.send_json({"ok": True, "key": key, **initial})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--book-root", default=str(DEFAULT_BOOK_ROOT))
    args = parser.parse_args()

    BookVaultHandler.index = BookIndex(Path(args.book_root))
    if os.environ.get("BOOKVAULT_INITIAL_SCAN", "0") == "1":
        BookVaultHandler.index.scan(force=True)
    else:
        BookVaultHandler.index.start_metadata_worker()
    server = ThreadingHTTPServer((args.host, args.port), BookVaultHandler)
    print(f"BookVault serving {args.book_root} on http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
