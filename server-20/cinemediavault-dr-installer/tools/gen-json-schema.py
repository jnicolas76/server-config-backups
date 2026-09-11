#!/usr/bin/env python3
"""Emit config/cinemediavault.schema.json from the Python schema.

Editors and CI can validate a configuration file against this without running
the installer. It is generated, never hand-written, so it cannot drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from installer.config.schema import MEDIA_PATH, PATH, SCHEMA, SECRET  # noqa: E402
from installer.version import CONFIG_SCHEMA_VERSION, INSTALLER_VERSION  # noqa: E402

JSON_TYPES = {"str": "string", "int": "integer", "float": "number",
              "bool": "boolean", "list": "array", "dict": "object",
              "enum": "string"}


def field_schema(field) -> dict:
    node: dict = {"type": JSON_TYPES[field.type]}
    if field.help:
        node["description"] = field.help
    if field.default is not None:
        node["default"] = field.default
    if field.choices:
        node["enum"] = list(field.choices)
    if field.minimum is not None:
        node["minimum"] = field.minimum
    if field.maximum is not None:
        node["maximum"] = field.maximum
    if field.pattern:
        node["pattern"] = field.pattern
    if SECRET in field.tags:
        node["writeOnly"] = True
        node["description"] = (node.get("description", "") +
                               " (secret: stored in secrets.env, never in this file)")
    if MEDIA_PATH in field.tags:
        node["x-media-path"] = True
    elif PATH in field.tags:
        node["x-path"] = True
    return node


def main() -> int:
    properties: dict = {}
    for field in SCHEMA:
        section, _, leaf = field.key.partition(".")
        properties.setdefault(section, {
            "type": "object", "additionalProperties": False, "properties": {}})
        properties[section]["properties"][leaf] = field_schema(field)

    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://cinemediavault.local/schema/cinemediavault.schema.json",
        "title": "CineMediaVault configuration",
        "description": (
            f"Generated from installer/config/schema.py at installer version "
            f"{INSTALLER_VERSION} (configuration schema v{CONFIG_SCHEMA_VERSION}). "
            "This document describes structure and per-field constraints. The "
            "installer additionally enforces cross-field rules that JSON Schema "
            "cannot express - port uniqueness, module-to-library coupling, "
            "path-overlap detection, private-network enforcement and password "
            "policy - so `cinevaultctl validate` remains authoritative."),
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }

    target = ROOT / "config" / "cinemediavault.schema.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} ({len(SCHEMA)} settings in {len(properties)} sections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
