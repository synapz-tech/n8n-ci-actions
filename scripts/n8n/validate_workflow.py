#!/usr/bin/env python3
"""
Validate n8n workflow JSON files against the repo conventions.

Usage:
    python scripts/n8n/validate_workflow.py <file1.json> [<file2.json> ...]
    python scripts/n8n/validate_workflow.py --all     # validate every *.json under workflows folders

Configuration via environment variable:
    N8N_REQUIRED_TAGS  Comma-separated list of tag names that every workflow
                       JSON must carry, e.g. "MyTag,Git Source of Truth".
                       Defaults to "Git Source of Truth" alone if not set.

Exit code: 0 on success, 1 on validation errors.

Checks performed (see AGENTS.md root for the rationale):
    1. JSON is parseable.
    2. Required top-level keys exist: name, nodes, connections, active, settings.
    3. `active` must be False (activation is controlled inside n8n, not in Git).
    4. `pinData` must be empty/missing (no test fixtures committed).
    5. Tags must contain every entry of N8N_REQUIRED_TAGS.
    6. There must be at least one sticky note named 'Git Source of Truth - Editing Notice'.
    7. The workflow name should match the file name (without .json).
    8. No obvious secrets in the JSON body (JWT, API keys patterns).
    9. No `credentials` block contains a raw value (only id + name).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(os.environ.get("N8N_WORKFLOWS_ROOT") or Path(__file__).resolve().parents[2])


def _required_tags_from_env() -> set[str]:
    raw = os.environ.get("N8N_REQUIRED_TAGS", "Git Source of Truth")
    tags = {t.strip() for t in raw.split(",") if t.strip()}
    return tags


REQUIRED_TAGS = _required_tags_from_env()
EDITING_NOTICE_NAME = "Git Source of Truth - Editing Notice"

# Folders that should NOT be scanned by --all
EXCLUDED_DIRS = {".git", ".github", ".kilo", "scripts", "node_modules"}

SECRET_PATTERNS = [
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\b"), "JWT"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "OpenAI-style API key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r'"password"\s*:\s*"[^"]+"'), "password field with value"),
]

ALLOWED_CREDENTIAL_KEYS = {"id", "name"}


class ValidationError(Exception):
    pass


def discover_workflow_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.json"):
        # Skip excluded directories anywhere in path
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def validate_file(path: Path) -> list[str]:
    """Return a list of validation error strings for `path`."""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read file: {e}"]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e}"]

    # 1. Required keys
    for key in ("name", "nodes", "connections", "active", "settings"):
        if key not in data:
            errors.append(f"missing required top-level key: '{key}'")

    # 2. active must be false
    if data.get("active") is not False:
        errors.append(f"`active` must be false in Git (got: {data.get('active')!r})")

    # 3. pinData must be empty
    pin = data.get("pinData")
    if pin and (not isinstance(pin, dict) or len(pin) > 0):
        errors.append(f"`pinData` must be empty (found {len(pin) if isinstance(pin, dict) else 'non-dict'} entries)")

    # 4. Tags
    tags = data.get("tags") or []
    tag_names = {t.get("name") for t in tags if isinstance(t, dict)}
    missing_tags = REQUIRED_TAGS - tag_names
    if missing_tags:
        errors.append(f"missing required tags: {sorted(missing_tags)}")

    # 5. Sticky note with editing notice
    nodes = data.get("nodes") or []
    has_notice = any(
        isinstance(n, dict)
        and n.get("type") == "n8n-nodes-base.stickyNote"
        and n.get("name") == EDITING_NOTICE_NAME
        for n in nodes
    )
    if not has_notice:
        errors.append(
            f"missing sticky note named '{EDITING_NOTICE_NAME}' "
            f"(see AGENTS.md > 'Aviso Git Source of Truth no workflow')"
        )

    # 6. File name vs workflow name
    expected_name = path.stem
    actual_name = data.get("name", "")
    if actual_name and actual_name != expected_name:
        errors.append(
            f"workflow name ({actual_name!r}) does not match file name ({expected_name!r})"
        )

    # 7. Secrets scan (against raw text, more aggressive than walking the tree)
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text):
            errors.append(f"suspicious secret detected: {label}")

    # 8. Credential blocks may only contain id + name (no raw value)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        creds = node.get("credentials")
        if not isinstance(creds, dict):
            continue
        for ctype, cinfo in creds.items():
            if not isinstance(cinfo, dict):
                continue
            unexpected = set(cinfo.keys()) - ALLOWED_CREDENTIAL_KEYS
            if unexpected:
                errors.append(
                    f"node {node.get('name', '?')!r}: credential {ctype!r} has unexpected keys "
                    f"{sorted(unexpected)} (only {sorted(ALLOWED_CREDENTIAL_KEYS)} allowed)"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate n8n workflow JSON files.")
    parser.add_argument("paths", nargs="*", help="JSON files to validate")
    parser.add_argument("--all", action="store_true", help="Validate every workflow JSON under the repo")
    args = parser.parse_args()

    if args.all:
        files = discover_workflow_files()
    else:
        if not args.paths:
            parser.error("provide file paths or use --all")
        files = [Path(p).resolve() for p in args.paths]

    if not files:
        print("No workflow JSON files found to validate.", file=sys.stderr)
        return 1

    total_errors = 0
    failed_files = 0
    for f in files:
        errs = validate_file(f)
        rel = f.relative_to(REPO_ROOT) if REPO_ROOT in f.parents or f == REPO_ROOT else f
        if errs:
            total_errors += len(errs)
            failed_files += 1
            print(f"FAIL {rel}")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"OK   {rel}")

    print()
    if total_errors:
        print(
            f"❌ {total_errors} validation error(s) across {failed_files}/{len(files)} file(s).",
            file=sys.stderr,
        )
        return 1
    print(f"✅ All {len(files)} workflow file(s) passed validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
