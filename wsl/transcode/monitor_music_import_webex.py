#!/usr/bin/env python3
import csv
import json
import subprocess
import time
from pathlib import Path


PLAN = Path("/home/jnicolas/music2-plex-import-plan-20260704.csv")
LOG = Path("/home/jnicolas/music2-plex-import-copy-20260704.csv")
STATE = Path("/home/jnicolas/.music2-webex-monitor-state.json")
NOTIFIER = Path("/home/jnicolas/send_webex_notification.py")
DATA4 = Path("/home/jnicolas/Data4")


def notify(message):
    subprocess.run([str(NOTIFIER), message], check=False)


def mount_state():
    return subprocess.run(
        ["mountpoint", "-q", str(DATA4)], check=False
    ).returncode == 0


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {
        "mounted": mount_state(),
        "milestone": 0,
        "failures": 0,
        "completed_notice": False,
        "last_status": 0,
    }


def save_state(state):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(STATE)


def counts():
    copied = failed = 0
    if LOG.exists() and LOG.stat().st_size:
        with LOG.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row["status"] in {"copied", "already_copied"}:
                    copied += 1
                elif row["status"] == "failed":
                    failed += 1
    return copied, failed


def planned_total():
    with PLAN.open(newline="", encoding="utf-8") as handle:
        return sum(1 for row in csv.DictReader(handle) if row["status"] == "planned")


def main():
    total = planned_total()
    state = load_state()
    state.setdefault("last_status", 0)
    while True:
        now = time.time()
        mounted = mount_state()
        if mounted != state["mounted"]:
            if mounted:
                notify("**Music import:** Data4 remounted and processing can resume.")
            else:
                notify("**Music import warning:** Data4 disconnected. Recovery is active.")
            state["mounted"] = mounted

        copied, failed = counts()
        if now - state["last_status"] >= 5 * 60:
            condition = (
                "actively available"
                if mounted
                else "paused while Data4 recovery/remount is running"
            )
            notify(
                f"**Music transfer status:** {copied:,} of {total:,} files "
                f"copied; {failed} failures.\n\nData4 is {condition}."
            )
            state["last_status"] = now

        milestone = copied // 1000
        if milestone > state["milestone"]:
            notify(
                f"**Music import progress:** {copied:,} of {total:,} "
                f"files copied. Failures: {failed}."
            )
            state["milestone"] = milestone

        if failed > state["failures"]:
            notify(
                f"**Music import warning:** {failed - state['failures']} new "
                f"copy failure(s); total failures: {failed}."
            )
            state["failures"] = failed

        if copied + failed >= total:
            if not state["completed_notice"]:
                notify(
                    f"**Music import complete:** {copied:,} files copied; "
                    f"{failed} failures."
                )
                state["completed_notice"] = True
                save_state(state)
            return

        save_state(state)
        time.sleep(60)


if __name__ == "__main__":
    main()
