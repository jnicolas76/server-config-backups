"""Loading, merging and writing ``cinemediavault.yaml``.

The installer ships without third-party dependencies, so it carries a small
strict YAML reader that accepts the subset the configuration file actually uses
(nested mappings, lists, scalars, quoted strings, comments). Anything outside
that subset is a hard error rather than a silent misparse. JSON is also
accepted, and is what the wizard writes internally.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from ..core.errors import ConfigError
from ..core.fsops import write_file
from .schema import SCHEMA, SECRET_KEYS, apply_defaults, get, put

# --------------------------------------------------------------------------
# Minimal YAML subset reader
# --------------------------------------------------------------------------

_SCALAR_TRUE = {"true", "yes", "on"}
_SCALAR_FALSE = {"false", "no", "off"}
_SCALAR_NULL = {"null", "~", ""}


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        if text[0] == '"':
            body = body.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
        else:
            body = body.replace("''", "'")
        return body
    low = text.lower()
    if low in _SCALAR_TRUE:
        return True
    if low in _SCALAR_FALSE:
        return False
    if low in _SCALAR_NULL:
        return None
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?", text):
        return float(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part) for part in _split_flow(inner)]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return {}
        out: dict[str, Any] = {}
        for part in _split_flow(inner):
            if ":" not in part:
                raise ConfigError(f"invalid inline mapping entry: {part!r}")
            k, v = part.split(":", 1)
            out[_parse_scalar(k)] = _parse_scalar(v)
        return out
    return text


def _split_flow(text: str) -> list[str]:
    """Split a flow sequence on commas that are not inside quotes or brackets."""
    parts: list[str] = []
    depth = 0
    quote = ""
    current = ""
    for char in text:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            current += char
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def parse_yaml(text: str) -> dict:
    """Parse the YAML subset used by CineMediaVault configuration files."""
    root: dict[str, Any] = {}
    # stack entries: (indent, container, pending_key)
    stack: list[tuple[int, Any]] = [(-1, root)]
    lines = text.splitlines()

    for lineno, raw in enumerate(lines, 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"line {lineno}: tabs are not allowed for indentation")
        indent = len(raw) - len(raw.lstrip(" "))
        line = _strip_comment(raw.strip())
        if not line:
            continue

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        container = stack[-1][1]

        if line.startswith("- "):
            item = line[2:].strip()
            if not isinstance(container, list):
                raise ConfigError(f"line {lineno}: list item outside a list")
            if ":" in item and not item.startswith(("[", "{", '"', "'")):
                key, _, value = item.partition(":")
                entry: dict[str, Any] = {}
                container.append(entry)
                stack.append((indent, entry))
                value = value.strip()
                if value:
                    entry[key.strip()] = _parse_scalar(value)
                else:
                    child: dict[str, Any] = {}
                    entry[key.strip()] = child
                    stack.append((indent + 1, child))
            else:
                container.append(_parse_scalar(item))
            continue

        if ":" not in line:
            raise ConfigError(f"line {lineno}: expected 'key: value', got {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not isinstance(container, dict):
            raise ConfigError(f"line {lineno}: mapping key inside a list item")

        if value == "":
            # Look ahead: a list follows if the next content line is "- ".
            nxt = _next_content_line(lines, lineno)
            if nxt is not None and nxt[1].lstrip().startswith("- ") and nxt[0] > indent:
                child_list: list[Any] = []
                container[key] = child_list
                stack.append((indent, child_list))
            elif nxt is not None and nxt[0] > indent:
                child_map: dict[str, Any] = {}
                container[key] = child_map
                stack.append((indent, child_map))
            else:
                container[key] = None
        else:
            container[key] = _parse_scalar(value)

    return root


def _strip_comment(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
    return line


def _next_content_line(lines: list[str], after: int) -> tuple[int, str] | None:
    for raw in lines[after:]:
        if raw.strip() and not raw.lstrip().startswith("#"):
            return len(raw) - len(raw.lstrip(" ")), raw
    return None


# --------------------------------------------------------------------------
# Writer
# --------------------------------------------------------------------------

def dump_yaml(data: Any, indent: int = 0) -> str:
    """Serialise to the same YAML subset. Deterministic key order."""
    pad = "  " * indent
    out: list[str] = []
    if isinstance(data, dict):
        for key in data:
            value = data[key]
            if isinstance(value, dict) and value:
                out.append(f"{pad}{key}:")
                out.append(dump_yaml(value, indent + 1))
            elif isinstance(value, dict):
                out.append(f"{pad}{key}: {{}}")
            elif isinstance(value, list) and value:
                out.append(f"{pad}{key}:")
                for item in value:
                    if isinstance(item, dict):
                        rendered = dump_yaml(item, indent + 2).splitlines()
                        first = rendered[0].strip() if rendered else ""
                        out.append(f"{'  ' * (indent + 1)}- {first}")
                        out.extend(rendered[1:])
                    else:
                        out.append(f"{'  ' * (indent + 1)}- {_dump_scalar(item)}")
            elif isinstance(value, list):
                out.append(f"{pad}{key}: []")
            else:
                out.append(f"{pad}{key}: {_dump_scalar(value)}")
    return "\n".join(line for line in out if line != "")


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_./:@+\-]+", text) and not re.fullmatch(
            r"(?i)(true|false|yes|no|on|off|null|~|[+-]?\d+(\.\d+)?)", text):
        return text
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def load(path: str | os.PathLike) -> dict:
    """Load a configuration file (YAML subset or JSON)."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"configuration file not found: {file}")
    text = file.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{file}: invalid JSON: {exc}") from exc
    else:
        data = parse_yaml(text)
    if not isinstance(data, dict):
        raise ConfigError(f"{file}: top level must be a mapping")
    return data


def merge(base: dict, overlay: dict) -> dict:
    """Deep-merge *overlay* onto *base*. Lists replace, they do not append."""
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def apply_env_overrides(config: dict, environ: dict | None = None) -> dict:
    """Apply ``CMV_<SECTION>_<KEY>`` environment overrides.

    ``CMV_NETWORK_HTTPS_PORT=5443`` sets ``network.https_port``. Only keys that
    exist in the schema are accepted, so a typo is reported instead of silently
    creating a setting nobody reads.
    """
    environ = environ if environ is not None else os.environ
    out = copy.deepcopy(config)
    lookup = {f.key.replace(".", "_").upper(): f for f in SCHEMA}
    for name, raw in environ.items():
        if not name.startswith("CMV_"):
            continue
        suffix = name[4:]
        field = lookup.get(suffix)
        if field is None:
            raise ConfigError(
                f"{name} does not match any configuration key. "
                f"See docs/CONFIGURATION-REFERENCE.md.")
        put(out, field.key, _coerce(field.type, raw, name))
    return out


def _coerce(type_name: str, raw: str, origin: str) -> Any:
    text = raw.strip()
    try:
        if type_name == "bool":
            low = text.lower()
            if low in _SCALAR_TRUE:
                return True
            if low in _SCALAR_FALSE:
                return False
            raise ValueError("expected a boolean")
        if type_name == "int":
            return int(text)
        if type_name == "float":
            return float(text)
        if type_name == "list":
            if text.startswith("["):
                return json.loads(text)
            return [p.strip() for p in text.split(",") if p.strip()]
        if type_name == "dict":
            return json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{origin}: cannot read {text!r} as {type_name}: {exc}") from exc
    return text


def split_secrets(config: dict) -> tuple[dict, dict]:
    """Split a configuration into (public, secrets).

    The public part is what lands in ``cinemediavault.yaml``; secret values are
    replaced by an empty string there and written separately with 0600
    permissions. ``admin.password`` is dropped entirely - only its derived hash
    is kept, and even that lives in the secret file.
    """
    public = copy.deepcopy(config)
    secrets: dict[str, str] = {}
    for key in SECRET_KEYS:
        value = get(public, key)
        if value not in (None, ""):
            secrets[key] = str(value)
        put(public, key, "")
    return public, secrets


def redacted_example(config: dict | None = None) -> str:
    """Render an example configuration with every secret masked."""
    from ..core.redact import MASK
    data = apply_defaults(config or {})
    for key in SECRET_KEYS:
        if get(data, key):
            put(data, key, MASK)
    return dump_yaml(data)


def save(path: str | os.PathLike, config: dict, *, mode: int = 0o640,
         user: str | int | None = None, group: str | int | None = None,
         header: str = "", backups=None, dry_run: bool = False) -> bool:
    """Write a configuration file atomically."""
    body = dump_yaml(config)
    text = (header.rstrip() + "\n\n" if header else "") + body + "\n"
    return write_file(path, text, mode=mode, user=user, group=group,
                      backups=backups, dry_run=dry_run)


def config_hash(config: dict) -> str:
    """Hash of the non-secret configuration, used for change detection."""
    import hashlib
    public, _ = split_secrets(config)
    return hashlib.sha256(
        json.dumps(public, sort_keys=True, default=str).encode("utf-8")).hexdigest()
