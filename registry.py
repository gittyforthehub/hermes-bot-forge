"""Skill-registry adapter. Wraps the `hermes skills` CLI so harness curation uses the real
registries (skills.sh, clawhub, well-known endpoints) instead of a bespoke downloader.

Every function here is defensive: a missing CLI, a network failure or an unparseable table
returns an empty result plus a reason. Nothing in this module ever raises into the forge.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
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


# `hermes skills search` prints a Rich table. Parse rows rather than screen-scraping prose.
_ROW = re.compile(r"^\s*[│┃]\s*(?P<name>[^│┃]+?)\s*[│┃]\s*(?P<desc>.*?)\s*[│┃]\s*(?P<source>[^│┃]*?)\s*"
                  r"[│┃]\s*(?P<trust>[^│┃]*?)\s*[│┃]\s*(?P<ident>[^│┃]*?)\s*[│┃]?\s*$")


def parse_search_table(text: str) -> list[dict]:
    """Parse `hermes skills search` output into {name, description, source, trust, identifier}."""
    rows: list[dict] = []
    for line in (text or "").splitlines():
        if "│" not in line and "┃" not in line:
            continue
        m = _ROW.match(line)
        if not m:
            continue
        row = {k: (v or "").strip() for k, v in m.groupdict().items()}
        if not row.get("ident") or row["name"] in {"Name", "─"}:
            continue
        rows.append({
            "name": row["name"],
            "description": " ".join(row["desc"].split())[:300],
            "source": row["source"],
            "trust": row["trust"],
            "identifier": row["ident"],
        })
    return rows


def search(query: str, limit: int = 5, root: Path | None = None) -> list[dict]:
    """Search the skill registries. Returns [] on any failure — callers report a gap instead."""
    try:
        proc = _run(["skills", "search", query], root or Path.home())
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return parse_search_table(proc.stdout)[:limit]


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
    """Installed skills with their category and enabled/disabled status."""
    try:
        proc = _run(["skills", "list"], root or Path.home())
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        if "│" not in line and "┃" not in line:
            continue
        cells = [c.strip() for c in re.split(r"[│┃]", line)]
        if len(cells) < 5 or cells[0] in {"Name", ""} or set(cells[0]) <= {"─"}:
            continue
        out.append({"name": cells[0], "category": cells[1], "source": cells[2],
                    "trust": cells[3], "status": cells[4]})
    return out
