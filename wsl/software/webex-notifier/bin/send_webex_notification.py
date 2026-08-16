#!/usr/bin/env python3
import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


TOKEN_FILE = Path.home() / ".config" / "webex" / "token"
DEFAULT_EMAIL = "jonathan.nicolas@gmail.com"
MESSAGES_URL = "https://webexapis.com/v1/messages"


def load_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main():
    parser = argparse.ArgumentParser(description="Send a direct Webex notification.")
    parser.add_argument("message")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config" / "application.properties"))
    parser.add_argument("--email", default=None)
    args = parser.parse_args()

    config = load_properties(Path(args.config))
    token_file = Path(config.get("webex.token.file", str(TOKEN_FILE))).expanduser()
    email = args.email or config.get("webex.to.email", DEFAULT_EMAIL)
    messages_url = config.get("webex.messages.url", MESSAGES_URL)

    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(f"Webex token is empty: {token_file}")

    payload = json.dumps({
        "toPersonEmail": email,
        "markdown": args.message,
    }).encode("utf-8")
    request = urllib.request.Request(
        messages_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Webex HTTP {error.code}: {detail}") from None
    except urllib.error.URLError as error:
        raise SystemExit(f"Webex connection failed: {error.reason}") from None

    print(f"sent message_id={result.get('id', 'unknown')}")


if __name__ == "__main__":
    main()
