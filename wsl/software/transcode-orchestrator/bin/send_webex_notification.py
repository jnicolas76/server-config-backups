#!/usr/bin/env python3
import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


TOKEN_FILE = Path.home() / ".config" / "webex" / "token"
DEFAULT_EMAIL = "jonathan.nicolas@gmail.com"
MESSAGES_URL = "https://webexapis.com/v1/messages"


def main():
    parser = argparse.ArgumentParser(description="Send a direct Webex notification.")
    parser.add_argument("message")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    args = parser.parse_args()

    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit(f"Webex token is empty: {TOKEN_FILE}")

    payload = json.dumps({
        "toPersonEmail": args.email,
        "markdown": args.message,
    }).encode("utf-8")
    request = urllib.request.Request(
        MESSAGES_URL,
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
