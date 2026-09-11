#!/usr/bin/env bash
#
# Build the versioned source archive that ships beside this directory.
#
# The archive contains the installer, the wizard, the payload and the docs, and
# deliberately excludes anything generated, cached or local: no __pycache__, no
# test sandboxes, no editor state, and no secrets - there are none in the tree,
# and the check below fails the build if that ever stops being true.
#
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
NAME="$(basename "$ROOT")"
VERSION="$(python3 -c "
import sys; sys.path.insert(0, '$ROOT')
from installer.version import INSTALLER_VERSION
print(INSTALLER_VERSION)")"
STAMP="$(date +%Y%m%d)"
OUT="${1:-$(dirname "$ROOT")/CineMediaVault-Installer-${VERSION}-${STAMP}.zip}"

cd "$(dirname "$ROOT")"

echo ":: scanning for credentials before packaging"
# Looks for credential *values*, not credential-shaped pattern definitions:
# a hash needs its base64 body, a private key needs its base64 lines. The
# redaction module and its tests legitimately contain the bare markers.
if ! python3 - "$NAME" <<'SCAN'
import re, sys
from pathlib import Path

root = Path(sys.argv[1])
# Credential *values* anywhere in the tree.
everywhere = (
    ("password hash", re.compile(r"pbkdf2_sha256\$\d+\$[A-Za-z0-9+/=]{16,}\$")),
    ("private key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]{40,}")),
)
# The upstream default administrator, but only where it could actually run.
# The tests assert its absence and the build report documents its removal;
# both legitimately name it.
in_code_only = (
    ("default admin", re.compile(r'password_hash\("admin1"\)')),
)
CODE_AREAS = ("payload", "installer", "wizard", "templates")

offenders = []
for path in root.rglob("*"):
    if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for label, pattern in everywhere:
        if pattern.search(text):
            offenders.append(f"{label}: {path}")
    relative = path.relative_to(root)
    if relative.parts and relative.parts[0] in CODE_AREAS:
        for label, pattern in in_code_only:
            if pattern.search(text):
                offenders.append(f"{label}: {path}")
for line in offenders:
    print(line, file=sys.stderr)
raise SystemExit(1 if offenders else 0)
SCAN
then
  echo "XX refusing to package: a credential was found in the tree" >&2
  exit 1
fi
echo "OK no credentials found"

echo ":: building $OUT"
rm -f "$OUT"
zip -q -r "$OUT" "$NAME" \
  -x "$NAME/**/__pycache__/*" \
  -x "$NAME/**/*.pyc" \
  -x "$NAME/.git/*" \
  -x "$NAME/**/.DS_Store" \
  -x "$NAME/**/*.tmp" \
  -x "$NAME/**/.pytest_cache/*"

SIZE="$(du -h "$OUT" | cut -f1)"
COUNT="$(unzip -l "$OUT" | tail -1 | awk '{print $2}')"
echo "OK $OUT ($SIZE, $COUNT files)"
sha256sum "$OUT" | tee "${OUT}.sha256"
