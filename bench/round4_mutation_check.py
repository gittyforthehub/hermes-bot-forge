"""Mutation check for the round-4 drift-detector fixes (H1-H3).

Each mutation reverts a fix that the round-4 independent review surfaced, and one it
missed. Every one of these is a case where the drift detector reported no drift where
drift existed -- the failure mode that makes the whole feature worse than useless, because
a Bot keeps running the old rules while the report says it is fine.

Run directly, or as a CI step. Restores every touched file on all paths.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, label, file, old, new)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "H1", "per-block dedent returns: a rule after a blank line loses its nesting",
        "policy.py",
        "        continues = (\n"
        "            prev_was_item\n"
        "            and margin >= baseline\n"
        "            and all(_LIST_LINE.match(ln) for ln in flat)\n"
        "        )\n",
        "        continues = False\n",
    ),
    (
        "H2", "the continuation rule is dropped: baseline never inherits",
        "policy.py",
        "        if not continues:\n            baseline = margin\n",
        "        baseline = margin\n",
    ),
    (
        "H3", "a flush rule after an indented list is cut to nothing (rule deleted)",
        "policy.py",
        "            and margin >= baseline\n",
        "            and margin >= 0\n",
    ),
    (
        "H4", "a fence is treated as list continuation (keeps the list's depth)",
        "policy.py",
        "            and all(_LIST_LINE.match(ln) for ln in flat)\n",
        "            and True\n",
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

    print(f"{len(MUTATIONS)} mutations, each reverting one round-4 fix.\n")
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
