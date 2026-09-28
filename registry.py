"""Skill-registry adapter. Wraps the `hermes skills` CLI so harness curation uses the real
registries (skills.sh, clawhub, well-known endpoints) instead of a bespoke downloader.

Every function here is defensive: a missing CLI, a network failure or an unexpected payload
returns an empty result plus a reason. Nothing in this module raises into the forge.

Parsing note: the CLI offers `--json` precisely for scripting, and its table output truncates
and wraps long identifiers across lines — so a table parser would silently produce corrupt
identifiers. This module uses `--json` and treats a malformed payload as an empty result.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

TIMEOUT = 120


class RegistryError(RuntimeError):
    """Raised only when a caller explicitly asks for a hard failure."""


def _run(args: list[str], root: Path, timeout: int = TIMEOUT):
    exe = shutil.which("hermes")
    if not exe:
        raise RegistryError("`hermes` CLI not on PATH")
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERMES_PROFILE")}
    return subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout, cwd=str(root), env=env)


def _json_from(text: str):
    """Extract and parse the JSON payload in CLI output, tolerating log noise.

    Uses `raw_decode` so a trailing epilogue — `[1/3] installing` before the payload, or
    `Updated 1 skill in 0.4s` after it — does not invalidate the whole parse. A successful
    CLI call reported as an empty registry is the worst possible failure: the caller is told
    its query was wrong when the registry answered it.

    So the first *parseable* value is not automatically the payload. A status object or an
    empty list printed before the real result (progress reporting, a header) would be
    returned and then rejected downstream as the wrong shape, reintroducing exactly that
    failure. Prefer a non-empty list, then a non-empty object, and only fall back to an
    empty one if nothing better exists — an empty list is a legitimate answer from a
    registry that genuinely has no matches, and must not be confused with noise.
    """
    if not text:
        return None
    # A CLI progress line like "[1/3] installing" opens with a bracket, so the FIRST bracket
    # is not necessarily the payload. Scan every bracket/brace position in order.
    candidates = sorted(i for i in (text.find("["), text.find("{")) if i != -1)
    for ch in ("[", "{"):
        start = 0
        while True:
            i = text.find(ch, start)
            if i == -1:
                break
            candidates.append(i)
            start = i + 1
    found = []
    for start in sorted(set(candidates)):
        try:
            value, _end = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(value, (list, dict)):
            found.append(value)
    if not found:
        return None
    # Best: a non-empty list (the shape every caller here wants), then any non-empty value.
    for value in found:
        if isinstance(value, list) and value:
            return value
    for value in found:
        if isinstance(value, (list, dict)) and value:
            return value
    return found[0]


def _norm(row: dict) -> dict:
    """Normalize one registry row across the field names the CLI has used."""
    return {
        "name": (row.get("name") or "").strip(),
        "identifier": (row.get("identifier") or row.get("id") or "").strip(),
        "source": (row.get("source") or "").strip(),
        "trust": (row.get("trust_level") or row.get("trust") or "").strip(),
        "description": " ".join((row.get("description") or "").split())[:300],
    }


def search(query: str, limit: int = 5, root: Path | None = None) -> list[dict]:
    """Search the skill registries. Returns [] on any failure — callers report a gap instead."""
    try:
        proc = _run(["skills", "search", query, "--json", "--limit", str(max(1, int(limit)))],
                    root or Path.home())
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    data = _json_from(proc.stdout)
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if isinstance(row, dict):
            item = _norm(row)
            if item["identifier"] or item["name"]:
                out.append(item)
    return out[:limit]


def install(identifier: str, category: str | None = None, name: str | None = None,
            root: Path | None = None, force: bool = False) -> dict:
    """Install one skill by identifier. Never raises; returns a status dict."""
    args = ["skills", "install", identifier, "--yes"]
    if category:
        args += ["--category", category]
    if name:
        args += ["--name", name]
    if force:
        args.append("--force")
    try:
        proc = _run(args, root or Path.home())
    except subprocess.TimeoutExpired:
        return {"identifier": identifier, "status": "timeout"}
    except Exception as exc:
        return {"identifier": identifier, "status": f"error: {type(exc).__name__}: {exc}"[:160]}
    out = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0
    return {"identifier": identifier, "status": "installed" if ok else "failed",
            "detail": None if ok else out[-300:]}


def list_installed(root: Path | None = None) -> list[dict]:
    """Installed skills with their category and enabled/disabled status.

    Note: `hermes skills list` has no JSON output and renders names truncated to ~16
    columns, so the table form is unparseable. If a future Hermes adds `--json` here this
    starts returning data with no other change; until then it returns [] and callers fall
    back to scanning the skills directories on disk, which is the authoritative source for
    what a profile can actually load.
    """
    try:
        proc = _run(["skills", "list", "--json"], root or Path.home())
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    data = _json_from(proc.stdout)
    if not isinstance(data, list):
        return []
    out = []
    for row in data:
        if isinstance(row, dict):
            item = _norm(row)
            item["category"] = (row.get("category") or "").strip()
            item["status"] = (row.get("status") or "").strip()
            out.append(item)
    return out
