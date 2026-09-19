"""Small, isolated HTTP endpoint for the generated weekly PDF guide."""
from pathlib import Path
import json
import re
import shutil

# This module is imported from the application root while generated issues live
# with the standalone magazine tooling.
GUIDE_DIR = Path(__file__).resolve().parent / "cine-guide-magazine" / "weekly-guides"
LATEST = GUIDE_DIR / "CineMedia-Vault-Guide-Latest.pdf"
ARCHIVE_DIR = Path("/media/jnicolas/Expansion/CineGuideMagazine")


def current_download_name() -> str:
    """Return the archived issue name that corresponds to the current PDF."""
    manifest = ARCHIVE_DIR / "issues.jsonl"
    try:
        records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
        for record in reversed(records):
            candidate = ARCHIVE_DIR / str(record.get("file") or "")
            if candidate.is_file() and candidate.stat().st_size == LATEST.stat().st_size:
                return candidate.name
    except (OSError, ValueError, TypeError):
        pass
    issues = sorted(ARCHIVE_DIR.glob("CineMedia-Vault-Guide-Issue-*.pdf"), key=lambda item: item.stat().st_mtime, reverse=True)
    return issues[0].name if issues else "CineMedia-Vault-Guide-Latest.pdf"


def handle_get(handler, path: str):
    if path not in ("/weekly-guide", "/weekly-guide.pdf"):
        return False
    if not LATEST.is_file():
        handler.send_error(503, "The weekly guide is being prepared")
        return True
    filename = re.sub(r'[^A-Za-z0-9._-]+', '-', current_download_name())
    if path == "/weekly-guide":
        # Give every published issue a unique browser URL. Chrome's built-in
        # PDF viewer otherwise keeps an already-open document alive even when
        # the stable endpoint has changed underneath it.
        handler.send_response(302)
        handler.send_header("Location", f"/weekly-guide.pdf?issue={filename}")
        handler.send_header("Cache-Control", "no-store, max-age=0")
        handler.send_header("Pragma", "no-cache")
        handler.send_header("Expires", "0")
        handler.end_headers()
        return True
    size = LATEST.stat().st_size
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Length", str(size))
    handler.send_header("Content-Disposition", f'inline; filename="{filename}"')
    # The endpoint is stable while its contents change weekly. Explicitly
    # forbid browser/proxy storage so Chrome cannot reopen an older issue.
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Expires", "0")
    handler.send_header("X-CineGuide-Issue", filename)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    with LATEST.open("rb") as source:
        shutil.copyfileobj(source, handler.wfile)
    return True
