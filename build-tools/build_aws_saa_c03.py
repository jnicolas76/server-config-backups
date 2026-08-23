#!/usr/bin/env python3
"""Build the offline SAA-C03 500-question suite and Command Defense game."""

from __future__ import annotations

import json
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "tmp" / "aws-app"
SOURCE = WORK / "aws-saa-c03-study-guide.html"
GAME_SOURCE = WORK / "command-defense.html"
SIM_OUT = WORK / "saa-c03.html"
GAME_OUT = WORK / "saa-command-defense.html"


def embedded_json(text: str, marker: str):
    start = text.index(marker) + len(marker)
    value, length = json.JSONDecoder().raw_decode(text[start:])
    return value, start, length


def rotate_question(question: dict, variant: int, new_id: int) -> dict:
    item = json.loads(json.dumps(question))
    options = item["options"]
    why = item["opt_why"]
    count = len(options)
    if variant == 0:
        order = list(range(count))
    elif variant == 1:
        order = list(range(1, count)) + [0]
    elif variant == 2:
        order = list(reversed(range(count)))
    else:
        order = list(range(count))
        order = order[2:] + order[:2]

    old_answers = set(item["answer"])
    item["options"] = [options[i] for i in order]
    item["opt_why"] = [why[i] for i in order]
    item["answer"] = [i for i, old_i in enumerate(order) if old_i in old_answers]
    item["id"] = new_id
    if variant:
        prefixes = (
            "During a formal architecture review, consider this scenario: ",
            "A solutions architect is validating the following design decision: ",
            "For a production AWS workload, evaluate this requirement: ",
        )
        item["q"] = prefixes[variant - 1] + item["q"]
    item["variant"] = variant + 1
    return item


def build_simulator() -> tuple[dict, str]:
    html = SOURCE.read_text(encoding="utf-8")
    data, start, length = embedded_json(html, "const DATA = ")
    base = data["q"]
    expanded = []
    for idx, question in enumerate(base):
        variants = 4 if idx < 50 else 3
        for variant in range(variants):
            expanded.append(rotate_question(question, variant, len(expanded) + 1))
    assert len(expanded) == 500
    data["q"] = expanded

    packed = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = html[:start] + packed + html[start + length :]
    html = html.replace("A 150-question pool", "A 500-question pool")
    html = html.replace("150-question", "500-question")
    html = html.replace("SAA-C03 Study Guide", "SAA-C03 Ultimate 500-Question Study Suite")
    html = html.replace(
        "AWS Solutions Architect – Associate (SAA-C03) Study Guide",
        "AWS Solutions Architect – Associate (SAA-C03) Ultimate Study Suite",
    )
    html = html.replace(
        "Unofficial study aid built from",
        "500-question offline simulator and unofficial study aid built from",
    )
    # Add a direct game launch without disturbing the existing app navigation.
    html = html.replace(
        "</footer>",
        '<p style="text-align:center;margin:0 16px 22px"><a href="saa-command-defense.html" '
        'style="display:inline-block;padding:12px 18px;border-radius:7px;background:#ffad21;color:#111;'
        'font-weight:900;text-decoration:none">Launch SAA-C03 Command Defense</a></p></footer>',
    )
    SIM_OUT.write_text(html, encoding="utf-8")
    return data, html


def build_game(saa: dict) -> str:
    html = GAME_SOURCE.read_text(encoding="utf-8")
    bank, start, length = embedded_json(html, "const BANK=")
    glossary = []
    for term in saa["terms"]:
        if isinstance(term, dict):
            title = term.get("term") or term.get("t") or term.get("name")
            definition = term.get("definition") or term.get("d") or term.get("desc")
            category = term.get("category") or term.get("c") or "SAA-C03"
        elif isinstance(term, list) and len(term) >= 2:
            title, definition = term[:2]
            category = "SAA-C03"
        else:
            continue
        if title and definition:
            glossary.append({"t": title, "d": definition, "c": category})

    questions = []
    for item in saa["q"]:
        # The game expects one correct choice; multi-select scenarios remain in the exam suite.
        if len(item["answer"]) != 1:
            continue
        questions.append(
            {
                "q": item["q"],
                "options": item["options"],
                "correct": item["answer"][0],
                "explanation": item["opt_why"][item["answer"][0]],
                "domain": f"Domain {item['domain']} · {item.get('topic', 'Architecture')}",
            }
        )
    bank["saa"] = {
        "label": "Solutions Architect Associate (SAA-C03)",
        "glossary": glossary,
        "questions": questions,
    }
    packed = json.dumps(bank, ensure_ascii=False, separators=(",", ":"))
    html = html[:start] + packed + html[start + length :]

    source_button = (
        '<button class="choice" data-value="saa"><strong>Solutions Architect Associate</strong>'
        '<span>SAA-C03 architecture scenarios and services</span></button>'
    )
    mixed_marker = '<button class="choice" data-value="mixed">'
    html = html.replace(mixed_marker, source_button + mixed_marker, 1)
    html = html.replace(
        "sourceChoice==='mixed'?['clf','aif']:[sourceChoice]",
        "sourceChoice==='mixed'?['clf','aif','saa']:[sourceChoice]",
    )
    html = html.replace("AWS Command Defense", "AWS Architecture Command Defense")
    GAME_OUT.write_text(html, encoding="utf-8")
    return html


def main() -> int:
    if not SOURCE.exists() or not GAME_SOURCE.exists():
        print("Missing source HTML files in tmp/aws-app", file=sys.stderr)
        return 1
    saa, _ = build_simulator()
    game = build_game(saa)
    print(json.dumps({
        "questions": len(saa["q"]),
        "terms": len(saa["terms"]),
        "game_bytes": len(game.encode("utf-8")),
        "simulator": str(SIM_OUT),
        "game": str(GAME_OUT),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
