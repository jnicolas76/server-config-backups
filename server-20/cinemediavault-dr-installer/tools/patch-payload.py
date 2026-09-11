#!/usr/bin/env python3
"""Remove the upstream default administrator from the staged payload.

The installer applies this patch when it copies the application into place, and
refuses to install if it cannot. Applying it here as well means the *shipped
package* also contains no default credential - so a copy of
`payload/app/cinemediavault.py` run directly by someone poking around cannot
create a super-administrator with a publicly known password either.

The install-time patch stays: it is what protects a payload re-staged from a
different source, and it is idempotent, so a pre-patched payload is simply
detected and skipped.

Run this after re-staging the payload from upstream source.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.steps.s050_payload import (  # noqa: E402
    BOOTSTRAP_REPLACEMENT, DEFAULT_ADMIN_MARKER,
)

PATTERN = re.compile(
    r"[ \t]*now = auth_now\(\)\n"
    r"[ \t]*admin_hash = password_hash\([^\n]*\)\n"
    r"[ \t]*conn\.execute\(\s*\n"
    r"(?:.*?\n)*?"
    r"[ \t]*\)\n",
)


def main() -> int:
    target = ROOT / "payload" / "app" / "cinemediavault.py"
    if not target.is_file():
        print(f"payload not found: {target}", file=sys.stderr)
        return 2

    text = target.read_text(encoding="utf-8")

    if "CineMediaVault installer patch" in text:
        print("already patched; nothing to do")
        return 0

    match = PATTERN.search(text)
    if not match or DEFAULT_ADMIN_MARKER not in match.group(0):
        print("could not locate the default-administrator block. The payload does "
              "not match what this installer expects; re-stage it or update "
              "installer/steps/s050_payload.py.", file=sys.stderr)
        return 1

    print("removing:")
    for line in match.group(0).splitlines():
        if "password_hash(" in line or "VALUES(" in line:
            print(f"    {line.strip()}")

    patched = text[:match.start()] + BOOTSTRAP_REPLACEMENT + text[match.end():]
    try:
        compile(patched, str(target), "exec")
    except SyntaxError as exc:
        print(f"the patch produced invalid Python ({exc}); nothing written.",
              file=sys.stderr)
        return 1

    target.write_text(patched, encoding="utf-8")
    print(f"patched {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
