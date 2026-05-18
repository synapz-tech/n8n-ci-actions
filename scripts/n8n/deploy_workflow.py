#!/usr/bin/env python3
"""
Deploy n8n workflow JSON files to a running n8n instance via the Public API.

Usage:
    N8N_API_URL=https://n8n.example.com \
    N8N_API_KEY=eyJ... \
    N8N_REQUIRED_TAGS="MyTag,Git Source of Truth" \
    python scripts/n8n/deploy_workflow.py <file1.json> [<file2.json> ...]
    python scripts/n8n/deploy_workflow.py --all     # deploy every workflow under the repo
    python scripts/n8n/deploy_workflow.py --dry-run --all

Configuration via environment variables:
    N8N_API_URL          (required) base URL of the n8n instance
    N8N_API_KEY          (required) long-lived Public API key
    N8N_REQUIRED_TAGS    (optional) comma-separated tags ensured on every workflow.
                         Defaults to "Git Source of Truth" only.
    N8N_WORKFLOWS_ROOT   (optional) directory to scan when --all is given. Defaults to
                         the repo root inferred from this script's path. Override when
                         running from another repo (e.g. via a reusable GitHub Action).

Behavior:
    - For each workflow, look it up on n8n by `id` (from the JSON).
    - If found: PUT /api/v1/workflows/{id} with `name`, `nodes`, `connections`, `settings`,
      `staticData`. (Public API does NOT accept `active`, `tags`, `pinData` in the body.)
    - If not found: POST /api/v1/workflows with the same body to create.
    - After upsert: ensure tags listed in N8N_REQUIRED_TAGS are applied via
      PUT /api/v1/workflows/{id}/tags (creating any tag that doesn't exist yet).
    - We never activate a workflow. The Git source has `active: false` by design; activation
      is a manual operator action inside n8n.
    - We never set `parentFolderId` (folder placement) — that requires the internal /rest API
      with a browser session cookie, not the Public API key. Folder placement stays manual
      on first creation; updates preserve it because we don't touch it.

Notes about the n8n Public API quirks (handled below):
    - The PUT/POST body must NOT include extra fields. Allowed: name, nodes, connections, settings, staticData.
    - The body must NOT include `id` (we use the path parameter for PUT).
    - The response uses 200 for PUT and 200/201 for POST.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(os.environ.get("N8N_WORKFLOWS_ROOT") or Path(__file__).resolve().parents[2])


def _required_tags_from_env() -> list[str]:
    raw = os.environ.get("N8N_REQUIRED_TAGS", "Git Source of Truth")
    return [t.strip() for t in raw.split(",") if t.strip()]


REQUIRED_TAGS = _required_tags_from_env()

EXCLUDED_DIRS = {".git", ".github", ".kilo", "scripts", "node_modules"}

# Allowed top-level keys for the Public API PUT/POST workflow body
ALLOWED_BODY_KEYS = ("name", "nodes", "connections", "settings", "staticData")

# Allowed keys inside the `settings` object. The n8n Public API rejects unknown
# fields with "request/body/settings must NOT have additional properties".
# Some fields are accepted only by the internal /rest API (e.g. `binaryMode`,
# `callerPolicy`) and must be stripped before calling /api/v1/workflows.
# Source: n8n schema as of v1.x — keep updated if breaking changes happen.
ALLOWED_SETTINGS_KEYS = (
    "executionOrder",
    "saveDataErrorExecution",
    "saveDataSuccessExecution",
    "saveManualExecutions",
    "saveExecutionProgress",
    "executionTimeout",
    "errorWorkflow",
    "timezone",
)


class DeployError(RuntimeError):
    pass


def env(name: str, required: bool = True, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise DeployError(f"environment variable {name!r} is required")
    return val or ""


def http(method: str, url: str, api_key: str, body: Any = None) -> tuple[int, Any]:
    headers = {
        "X-N8N-API-KEY": api_key,
        "Accept": "application/json",
        "User-Agent": "n8n-ci-actions/1.0",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def discover_workflow_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.json"):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def load_workflow(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DeployError(f"{path}: cannot read/parse: {e}") from e
    if not isinstance(data, dict):
        raise DeployError(f"{path}: top-level must be an object")
    return data


def workflow_exists(api_url: str, api_key: str, wf_id: str) -> bool:
    status, _ = http("GET", f"{api_url}/api/v1/workflows/{wf_id}", api_key)
    return status == 200


def fetch_tag_id_map(api_url: str, api_key: str) -> dict[str, str]:
    """Return {tag_name: tag_id} for all tags in the instance."""
    status, body = http("GET", f"{api_url}/api/v1/tags?limit=250", api_key)
    if status != 200 or not isinstance(body, dict):
        raise DeployError(f"GET /api/v1/tags failed: {status} {body!r}")
    return {t["name"]: t["id"] for t in body.get("data", []) if t.get("id") and t.get("name")}


def ensure_tag(api_url: str, api_key: str, tag_name: str, cache: dict[str, str]) -> str:
    if tag_name in cache:
        return cache[tag_name]
    status, body = http("POST", f"{api_url}/api/v1/tags", api_key, {"name": tag_name})
    if status not in (200, 201) or not isinstance(body, dict):
        raise DeployError(f"failed to create tag {tag_name!r}: {status} {body!r}")
    cache[tag_name] = body["id"]
    return body["id"]


def build_body(wf: dict[str, Any]) -> dict[str, Any]:
    """Extract only the fields the Public API accepts in PUT/POST body."""
    body = {k: wf[k] for k in ALLOWED_BODY_KEYS if k in wf}
    # `name`, `nodes`, `connections`, `settings` are required by the API
    for required in ("name", "nodes", "connections", "settings"):
        if required not in body:
            # connections/settings can be empty objects; nodes must be array
            body[required] = {} if required != "nodes" else []
    # The Public API rejects any extra key inside `settings`. Strip them so we
    # do not have to mutate the JSON committed to Git (some keys like
    # `binaryMode` are valid only on the internal /rest API).
    if isinstance(body.get("settings"), dict):
        body["settings"] = {
            k: v for k, v in body["settings"].items() if k in ALLOWED_SETTINGS_KEYS
        }
    return body


def deploy_workflow(
    api_url: str,
    api_key: str,
    wf_path: Path,
    tag_cache: dict[str, str],
    dry_run: bool = False,
) -> str:
    wf = load_workflow(wf_path)
    wf_id = wf.get("id")
    body = build_body(wf)

    rel = wf_path.relative_to(REPO_ROOT) if REPO_ROOT in wf_path.parents else wf_path

    if wf_id and workflow_exists(api_url, api_key, wf_id):
        action = f"UPDATE {wf_id}"
        url = f"{api_url}/api/v1/workflows/{wf_id}"
        method = "PUT"
    else:
        action = "CREATE" if not wf_id else f"CREATE (id {wf_id} not found)"
        url = f"{api_url}/api/v1/workflows"
        method = "POST"

    if dry_run:
        print(f"DRY-RUN {action:30} {rel}  [{len(body.get('nodes', []))} nodes]")
        return "dry-run"

    status, response = http(method, url, api_key, body)
    if status not in (200, 201) or not isinstance(response, dict):
        raise DeployError(f"{rel}: {method} {url} -> {status} {response!r}")

    result_id = response.get("id") or wf_id
    if not result_id:
        raise DeployError(f"{rel}: response missing workflow id: {response!r}")

    # Apply tags (only required ones; extra tags on the JSON are also applied to keep parity)
    desired_tag_names = list(REQUIRED_TAGS)
    for t in wf.get("tags") or []:
        if isinstance(t, dict) and t.get("name") and t["name"] not in desired_tag_names:
            desired_tag_names.append(t["name"])

    tag_ids = [{"id": ensure_tag(api_url, api_key, name, tag_cache)} for name in desired_tag_names]
    status, response = http("PUT", f"{api_url}/api/v1/workflows/{result_id}/tags", api_key, tag_ids)
    if status != 200:
        raise DeployError(f"{rel}: failed to set tags: {status} {response!r}")

    print(f"OK      {action:30} {rel}  (tags: {desired_tag_names})")
    return result_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy n8n workflow JSONs to an n8n instance.")
    parser.add_argument("paths", nargs="*", help="JSON files to deploy")
    parser.add_argument("--all", action="store_true", help="Deploy every workflow JSON under the repo")
    parser.add_argument("--dry-run", action="store_true", help="Do not call write endpoints")
    args = parser.parse_args()

    try:
        api_url = env("N8N_API_URL").rstrip("/")
        api_key = env("N8N_API_KEY")
    except DeployError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    if args.all:
        files = discover_workflow_files()
    else:
        if not args.paths:
            parser.error("provide file paths or use --all")
        files = [Path(p).resolve() for p in args.paths]

    if not files:
        print("No workflow JSON files to deploy.", file=sys.stderr)
        return 1

    # Pre-fetch existing tags once (avoids N round-trips per workflow)
    try:
        tag_cache = {} if args.dry_run else fetch_tag_id_map(api_url, api_key)
        # Ensure required tags exist up-front (not in dry-run)
        if not args.dry_run:
            for tag in REQUIRED_TAGS:
                ensure_tag(api_url, api_key, tag, tag_cache)
    except DeployError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2

    errors: list[str] = []
    for path in files:
        try:
            deploy_workflow(api_url, api_key, path, tag_cache, dry_run=args.dry_run)
        except DeployError as e:
            errors.append(str(e))
            print(f"FAIL  {path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path}: {e}")

    print()
    if errors:
        print(f"❌ {len(errors)} workflow(s) failed to deploy.", file=sys.stderr)
        return 1
    print(f"✅ Successfully {'validated' if args.dry_run else 'deployed'} {len(files)} workflow(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
