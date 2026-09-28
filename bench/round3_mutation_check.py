"""Mutation check for the round-3 shared-policy fixes (G1-G7).

Each mutation reverts a fix an independent review found in the previous round of
fixes -- the drift detector started reporting no drift where drift existed. Run
directly, or as a CI step. Restores every touched file on all paths.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, label, file, old, new)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "G1", "policy_body drops indentation again (nested == flat)",
        "policy.py",
        '    body = _dedent(body)\n',
        '    body = _dedent(body)\n    body = re.sub(r"(?m)^[ \\t]*[-*+][ \\t]+", "- ", body)\n',
    ),
    (
        "G2", "policy_path accepts a non-string (template TypeError)",
        "policy.py",
        '    if relative is not None and not isinstance(relative, str):',
        '    if False:',
    ),
    (
        "G3", "policy_path expands ~ on the joined path again",
        "policy.py",
        "    rel = Path(relative).expanduser() if relative is not None else Path(DEFAULT_RELATIVE)\n"
        "    if rel.is_absolute():\n"
        "        raise PolicyPathError(\n"
        '            f"shared_policy_path must be relative to the Hermes root, got {relative!r}"\n'
        "        )\n"
        "    candidate = root / rel\n",
        "    candidate = (root / (relative or DEFAULT_RELATIVE)).expanduser()\n",
    ),
    (
        "G4", "health reads the raw SOUL again (name pushed past 400 chars)",
        "health.py",
        "forge.persona_text(soul).lower()[:400]",
        "soul.lower()[:400]",
    ),
    (
        "G5", "check_policies audits the un-actionable default profile again",
        "tools.py",
        "    for prof in candidates:\n        name = prof.name\n",
        '    if (root / "SOUL.md").exists():\n        candidates.append(root)\n'
        "    for prof in candidates:\n"
        '        name = "default" if prof == root else prof.name\n',
    ),
    (
        "G6", "unreadable rows drop the documented fingerprint keys",
        "tools.py",
        '                         "has_shared_policy": False, "reason": "unreadable",\n'
        '                         "fingerprint": None, "canonical_fingerprint": expected})',
        '                         "has_shared_policy": False, "reason": "unreadable"})',
    ),
    (
        "G7", "refresh overwrites a persona-less SOUL with policy only",
        "manage.py",
        "        if not forge.persona_text(soul).strip():\n",
        "        if False:\n",
    ),
    (
        "G8", "refresh + soul_md silently drops the policy again",
        "manage.py",
        "        if refresh_policy and pol_text:\n",
        "        if False:\n",
    ),
]

TESTS = "tests"


def run_suite() -> tuple[bool, str]:
    # unittest writes its report to stderr, and in this environment the exit status is not a
    # reliable pass/fail signal on its own -- so read the report, not just the return code.
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

    print(f"{len(MUTATIONS)} mutations, each reverting one round-3 fix.\n")
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
                # suite_passed must be True for the mutation to have SURVIVED. Inverting
                # this is the easy mistake: the names below read as "caught"/"missed", and
                # getting it backwards turns every caught mutation into a false MISSED.
                suite_passed, out = run_suite()
            finally:
                shutil.copy(backup, target)

        caught = not suite_passed
        if caught:
            print(f"{mid} CAUGHT    {label}")
        else:
            print(f"{mid} *** MISSED *** {label}")
            print("             the suite still passed with this fix reverted")
            missed.append(mid)

    print()
    if missed:
        print(f"result: FAIL -- {len(missed)} of {len(MUTATIONS)} mutations survived: "
              f"{', '.join(missed)}")
        return 1
    print(f"result: PASS -- caught {len(MUTATIONS)}/{len(MUTATIONS)}: "
          f"{', '.join(m[0] for m in MUTATIONS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
