"""Mutation check for the round-4 follow-up fixes (J1-J4).

An independent review returned six findings against the code as it stood at `cccf53a`. The
headline one -- a whole list block re-indenting to the same fingerprint -- was a documented
intended behaviour and is not a defect. The other five are, and each of these mutations
reverts one of the fixes for them.

The first three are FALSE NEGATIVES: something that claims to detect a problem reporting
clean. A Bot drifted from its policy, or the duplicate-Bot guard went blind, and the tooling
said all was well -- the failure mode that makes these checks worse than not having them.

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
        "J1", "blank-line collapse runs after dedent again: policy_body is not a fixed point",
        "policy.py",
        '    body = re.sub(r"\\n{3,}", "\\n\\n", body)' + "\n",
        "",
    ),
    (
        "J2", "survey reads the raw SOUL head again: persona behind a policy is invisible",
        "survey.py",
        "        soul = policy.strip_block(raw_soul)[:2000]",
        "        soul = raw_soul[:2000]",
    ),
    (
        "J3", "an exported template carries the originating Bot's policy again",
        "portable.py",
        '        "soul_md": policy.strip_block((pdir / "SOUL.md").read_text(errors="ignore")).lstrip() if (pdir / "SOUL.md").exists() else "",',
        '        "soul_md": (pdir / "SOUL.md").read_text(errors="ignore") if (pdir / "SOUL.md").exists() else "",',
    ),
    (
        "J4", "a NUL byte in shared_policy_path is accepted again (write raises ValueError)",
        "policy.py",
        '    if chr(0) in str(relative):\n'
        '        raise PolicyPathError("shared_policy_path must not contain a NUL byte")\n',
        "",
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

    print(f"{len(MUTATIONS)} mutations, each reverting one round-4 follow-up fix.\n")
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
            import ast
            ast.parse(target.read_text())  # never leave a file we could not restore
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
