#!/usr/bin/env python3
"""A lexical sanity check for the wizard's JavaScript.

Node is not a dependency of this installer, and the target machines will not
have it, so the test suite cannot shell out to `node --check`. This is a real
tokenizer rather than a naive brace count: it understands line and block
comments, all three string forms including nested `${}` interpolation, and
regular-expression literals (which it distinguishes from division by looking at
the previous significant token).

It catches the class of mistake that actually happens when generating
JavaScript - an unbalanced brace, an unterminated string or template - without
pretending to be a parser.
"""

from __future__ import annotations

import sys
from pathlib import Path

# A '/' after one of these starts a regex, not a division.
REGEX_PRECEDERS = {
    "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*",
    "%", "<", ">", "~", "^", "return", "typeof", "instanceof", "in", "of",
    "new", "delete", "void", "throw", "case", "do", "else", "yield", "await",
}


class JsSyntaxError(Exception):
    pass


def check(source: str, name: str = "<js>") -> list[str]:
    """Return a list of problems. Empty means the file is lexically sound."""
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    pairs = {"}": "{", ")": "(", "]": "["}

    i = 0
    line = 1
    length = len(source)
    previous_token = ""
    # Template literal nesting: each entry is the brace depth at which a `${`
    # was opened, so the matching `}` returns to template scanning.
    template_stack: list[int] = []

    while i < length:
        char = source[i]

        if char == "\n":
            line += 1
            i += 1
            continue

        # -- comments --------------------------------------------------
        if char == "/" and i + 1 < length:
            nxt = source[i + 1]
            if nxt == "/":
                end = source.find("\n", i)
                i = length if end == -1 else end
                continue
            if nxt == "*":
                end = source.find("*/", i + 2)
                if end == -1:
                    problems.append(f"{name}:{line}: unterminated block comment")
                    break
                line += source.count("\n", i, end)
                i = end + 2
                continue

        # -- strings ----------------------------------------------------
        if char in "'\"":
            i, line, ok = _scan_string(source, i, line, char)
            if not ok:
                problems.append(f"{name}:{line}: unterminated string")
                break
            previous_token = "str"
            continue

        if char == "`":
            i, line, ok, interpolation = _scan_template(source, i, line)
            if not ok:
                problems.append(f"{name}:{line}: unterminated template literal")
                break
            if interpolation:
                # We stopped at a `${`; record the brace depth to return to.
                template_stack.append(len(stack))
                stack.append(("{", line))
                previous_token = "{"
                continue
            previous_token = "str"
            continue

        # -- regex literals ---------------------------------------------
        if char == "/" and previous_token in REGEX_PRECEDERS:
            i, line, ok = _scan_regex(source, i, line)
            if not ok:
                problems.append(f"{name}:{line}: unterminated regular expression")
                break
            previous_token = "regex"
            continue

        # -- brackets ------------------------------------------------------
        if char in "{([":
            stack.append((char, line))
            previous_token = char
            i += 1
            continue

        if char in "})]":
            if not stack:
                problems.append(f"{name}:{line}: unexpected '{char}'")
                break
            opener, opened_at = stack.pop()
            if opener != pairs[char]:
                problems.append(
                    f"{name}:{line}: '{char}' closes '{opener}' opened on line "
                    f"{opened_at}")
                break
            if char == "}" and template_stack and len(stack) == template_stack[-1]:
                # This `}` ends a `${...}`; resume the template literal.
                template_stack.pop()
                i, line, ok, interpolation = _scan_template(source, i, line,
                                                            resume=True)
                if not ok:
                    problems.append(f"{name}:{line}: unterminated template literal")
                    break
                if interpolation:
                    template_stack.append(len(stack))
                    stack.append(("{", line))
                    previous_token = "{"
                else:
                    previous_token = "str"
                continue
            previous_token = char
            i += 1
            continue

        # -- identifiers and everything else -------------------------------
        if char.isalnum() or char in "_$":
            start = i
            while i < length and (source[i].isalnum() or source[i] in "_$"):
                i += 1
            previous_token = source[start:i]
            continue

        if not char.isspace():
            previous_token = char
        i += 1

    if stack:
        opener, opened_at = stack[-1]
        problems.append(f"{name}: '{opener}' opened on line {opened_at} is never closed")

    return problems


def _scan_string(source: str, i: int, line: int, quote: str):
    i += 1
    while i < len(source):
        char = source[i]
        if char == "\\":
            if source[i + 1:i + 2] == "\n":
                line += 1
            i += 2
            continue
        if char == "\n":
            return i, line, False
        if char == quote:
            return i + 1, line, True
        i += 1
    return i, line, False


def _scan_template(source: str, i: int, line: int, *, resume: bool = False):
    """Scan a template literal. Returns (index, line, ok, hit_interpolation)."""
    i += 1                       # skip the ` or the closing }
    while i < len(source):
        char = source[i]
        if char == "\\":
            i += 2
            continue
        if char == "\n":
            line += 1
            i += 1
            continue
        if char == "$" and source[i + 1:i + 2] == "{":
            # Return past the '{'. The caller pushes it onto the bracket stack
            # itself, so the main loop must not see it a second time.
            return i + 2, line, True, True
        if char == "`":
            return i + 1, line, True, False
        i += 1
    return i, line, False, False


def _scan_regex(source: str, i: int, line: int):
    i += 1
    in_class = False
    while i < len(source):
        char = source[i]
        if char == "\\":
            i += 2
            continue
        if char == "\n":
            return i, line, False
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            i += 1
            while i < len(source) and source[i].isalpha():
                i += 1
            return i, line, True
        i += 1
    return i, line, False


def check_file(path: str | Path) -> list[str]:
    file = Path(path)
    return check(file.read_text(encoding="utf-8"), file.name)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: jscheck.py FILE [FILE...]", file=sys.stderr)
        return 2
    failed = 0
    for target in argv:
        problems = check_file(target)
        if problems:
            failed += 1
            for problem in problems:
                print(problem, file=sys.stderr)
        else:
            print(f"{target}: OK")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
