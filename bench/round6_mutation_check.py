"""Mutation check for the round-5 fixes (K1-K5).

An independent review of the shared-policy work found a critical defect in the fingerprint and
two high-severity ones in registry curation. Each mutation below reverts one of those fixes;
every one must be caught, or the fix is decorative.

The critical one (K1) is the reason this gate exists: a canonical policy that *documents* the
fence markers was truncated to its own example, so two policies with different rules hashed
identically and every Bot built from one reported drift it could never clear.

Run directly, or as a CI step. Restores every touched file on all paths.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, label, file, old, new)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "K1", "the fence matches anywhere again: a policy that documents the markers is truncated",
        "policy.py",
        "    m = _boundary_block(raw)\n",
        "    m = _BLOCK.search(raw)\n",
    ),
    (
        "K2", "render_block no longer strips pre-existing markers (they nest and cut SOUL.md short)",
        "policy.py",
        '    body = _MARKER_LINE.sub("", text or "").strip()\n',
        '    body = (text or "").strip()\n',
    ),
    (
        "K3", "the repair loop re-plans without extra_keep again (registry skills get disabled)",
        # 0.19.0 moved the curation path into harness.curate (shared by create and update).
        "harness.py",
        "                    result = plan(pdir, manifest, cfg, extra_keep=registry_kept)\n",
        "                    result = plan(pdir, manifest, cfg)\n",
    ),
    (
        "K4", "a failed install counts as resolved again (required skill reports no gap)",
        "harness.py",
        '        and (e.get("status") != "resolved" or e.get("install") == "failed")\n',
        '        and e.get("status") != "resolved"\n',
    ),
    (
        "K5", "an identifier-only registry entry is not added to the keep-set again",
        "harness.py",
        '            installed_name = entry.get("name") or entry["identifier"].rstrip("/").rsplit("/", 1)[-1]\n',
        '            installed_name = entry.get("name")\n',
    ),
]

TESTS = "tests"


def run_suite() -> tuple[bool, str]:
    # unittest writes its report to stderr, and the exit status alone is not a reliable
    # pass/fail signal -- read the report, not just the return code.
    # -B: never write __pycache__, or a stale .pyc from the previous run would shadow the
    # mutation and make it look like the suite still passed.
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", TESTS, "-q"],
        cwd=ROOT, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    passed = proc.returncode == 0 and "OK" in out and "FAILED" not in out
    return passed, out[-3000:]


def main() -> int:
    print("baseline: running the suite unmodified")
    ok, out = run_suite()
    if not ok:
        print("baseline FAILED -- fix the suite before trusting these results:\n", out)
        return 1
    print("baseline: PASS\n")

    print(f"{len(MUTATIONS)} mutations, each reverting one round-5 fix.\n")
    missed: list[str] = []
    for mid, label, filename, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / filename
            target = ROOT / filename
            shutil.copy(target, backup)
            src = target.read_text()
            if old not in src:
                print(f"{mid} STALE     {label} -- pattern not found in {filename}")
                missed.append(mid)
                continue
            target.write_text(src.replace(old, new, 1))
            try:
                ast.parse(target.read_text())  # a mutation that cannot parse proves nothing
            except SyntaxError:
                shutil.copy(backup, target)
                print(f"{mid} BROKEN    {label} -- the mutation did not parse")
                missed.append(mid)
                continue
            try:
                # suite_passed must be True for the mutation to have SURVIVED. Inverting
                # this is the easy mistake: the names below read as "caught"/"missed", and
                # getting it backwards turns every caught mutation into a false MISSED.
                suite_passed, out = run_suite()
            finally:
                shutil.copy(backup, target)

        caught = not suite_passed
        if caught:
            print(f"{mid} caught    {label}")
        else:
            print(f"{mid} MISSED    {label}  <-- the suite still passed with this reverted")
            missed.append(mid)

    print()
    if missed:
        print(f"result: FAIL -- survived: {', '.join(missed)}")
        return 1
    print(f"result: PASS -- caught {len(MUTATIONS)}/{len(MUTATIONS)}: "
          + ", ".join(m[0] for m in MUTATIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
