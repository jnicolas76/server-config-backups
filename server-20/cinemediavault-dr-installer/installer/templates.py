"""A deliberately small template renderer.

The installer has no third-party dependencies, and a full template language
would be more power than the job needs. Templates use ``{{ name }}`` for
substitution and ``{% if name %}`` / ``{% endif %}`` for whole-line
conditionals. That is enough for systemd units and Compose files, and it is
small enough to reason about.

Substituted values are stringified but never escaped, because every template
target is a configuration format the installer controls and every value has
already passed schema validation. Where a value could contain a quote - a
display name in a systemd ``Description=`` - the template quotes it explicitly.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
IF_RE = re.compile(r"^\s*\{%\s*if\s+([A-Za-z_][A-Za-z0-9_]*)\s*%\}\s*$")
ELSE_RE = re.compile(r"^\s*\{%\s*else\s*%\}\s*$")
ENDIF_RE = re.compile(r"^\s*\{%\s*endif\s*%\}\s*$")


class TemplateError(ValueError):
    pass


def render(text: str, variables: Mapping[str, Any], *, name: str = "<template>") -> str:
    """Render *text*. Every referenced variable must exist."""
    out_lines: list[str] = []
    # Stack of (emitting, seen_else) for nested conditionals.
    stack: list[list[bool]] = []

    for number, line in enumerate(text.splitlines(), 1):
        match = IF_RE.match(line)
        if match:
            key = match.group(1)
            if key not in variables:
                raise TemplateError(f"{name}:{number}: unknown variable {key!r}")
            parent_emitting = all(frame[0] for frame in stack) if stack else True
            stack.append([bool(variables[key]) and parent_emitting, False])
            continue
        if ELSE_RE.match(line):
            if not stack:
                raise TemplateError(f"{name}:{number}: {{% else %}} without {{% if %}}")
            frame = stack[-1]
            if frame[1]:
                raise TemplateError(f"{name}:{number}: duplicate {{% else %}}")
            parent_emitting = all(f[0] for f in stack[:-1]) if len(stack) > 1 else True
            frame[0] = (not frame[0]) and parent_emitting
            frame[1] = True
            continue
        if ENDIF_RE.match(line):
            if not stack:
                raise TemplateError(f"{name}:{number}: {{% endif %}} without {{% if %}}")
            stack.pop()
            continue
        if stack and not all(frame[0] for frame in stack):
            continue

        def substitute(m: re.Match) -> str:
            key = m.group(1)
            if key not in variables:
                raise TemplateError(f"{name}:{number}: unknown variable {key!r}")
            value = variables[key]
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)

        out_lines.append(VAR_RE.sub(substitute, line))

    if stack:
        raise TemplateError(f"{name}: unclosed {{% if %}}")
    return "\n".join(out_lines) + "\n"


def render_template(package_root: str | Path, relative: str,
                    variables: Mapping[str, Any]) -> str:
    """Render ``templates/<relative>`` from the installer package."""
    path = Path(package_root) / "templates" / relative
    if not path.is_file():
        raise TemplateError(f"template not found: {path}")
    return render(path.read_text(encoding="utf-8"), variables, name=relative)


def list_templates(package_root: str | Path) -> list[str]:
    root = Path(package_root) / "templates"
    if not root.is_dir():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def required_variables(text: str) -> set[str]:
    """Every variable a template references. Used by the template tests."""
    names = set(VAR_RE.findall(text))
    names.update(IF_RE.match(line).group(1)
                 for line in text.splitlines() if IF_RE.match(line))
    return names
