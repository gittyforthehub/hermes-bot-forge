"""Mutation check: revert each load-bearing policy decision and confirm tests catch it.

A passing suite proves nothing if the guard under test is inert. Each mutation here is a
deliberate reversion of a real design decision; every one MUST produce a test failure.
If any mutation survives, that guard is vacuous and the suite is overstating what it covers.

Run directly, or as a CI step. Restores policy.py on every path, including failure.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "policy.py"
ORIGINAL = POLICY.read_text()

MUTATIONS = [
    ("M1", "fingerprint hashes the whole file instead of the policy body",
     'return hashlib.sha256(policy_body(text).encode("utf-8")).hexdigest()[:12]',
     'return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]'),

    ("M2", "comments not stripped, so a comment-only edit looks like drift",
     'body = re.sub(r"(?m)^[ \\t]*<!--.*?-->[ \\t]*\\n?", "", body)\n'
     '    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)\n'
     '    return re.sub(r"\\n{3,}", "\\n\\n", body).strip()',
     "    return body.strip()"),

    ("M3", "inject always appends, so rebuilds accumulate duplicate blocks",
     'body = _BLOCK.sub("", soul or "").lstrip()\n'
     '    return f"{block}\\n\\n{body}" if body else f"{block}\\n"',
     'return f"{soul}\\n\\n{block}\\n"'),

    ("M4", "audit always reports current, so drift becomes invisible",
     "if actual != expected_fp:\n"
     '        return False, f"policy drifted (bot {actual}, canonical {expected_fp})"',
     "if False:\n"
     '        return False, f"policy drifted (bot {actual}, canonical {expected_fp})"'),

    ("M5", "a Bot with no shared block is treated as fine",
     'if block is None:\n        return False, "no shared-policy block"',
     'if block is None:\n        return True, "current"'),

    ("M6", "block regex loses its capture group, so the body keeps the markers",
     're.escape(BEGIN) + r"\\n?(.*?)" + re.escape(END)',
     're.escape(BEGIN) + r"\\n?.*?" + re.escape(END)'),
]


def run_tests():
    return subprocess.run(
        [sys.executable, "-m", "unittest", "tests.test_policy"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )


def main() -> int:
    base = run_tests()
    if base.returncode != 0:
        print("baseline FAILED — fix the suite before trusting any mutation result")
        print(base.stderr[-2000:])
        return 1
    ran = re.search(r"Ran (\d+) tests", base.stderr)
    print(f"baseline: PASS ({ran.group(1) if ran else '?'} tests)\n")

    caught, broken = [], []
    for mid, desc, find, repl in MUTATIONS:
        if find not in ORIGINAL:
            broken.append(mid)
            print(f"{mid}: PATTERN NOT FOUND — the mutation no longer matches the source")
            continue
        POLICY.write_text(ORIGINAL.replace(find, repl, 1))
        try:
            p = run_tests()
        finally:
            POLICY.write_text(ORIGINAL)
        if p.returncode != 0:
            caught.append(mid)
            print(f"{mid} CAUGHT   {desc}")
        else:
            print(f"{mid} *** MISSED *** {desc}  <-- this guard is vacuous")

    final = run_tests()
    restored = final.returncode == 0
    print(f"\nrestored baseline: {'PASS' if restored else 'FAIL'}")
    print(f"caught {len(caught)}/{len(MUTATIONS)}: {', '.join(caught) or 'none'}")
    if broken:
        print(f"patterns that no longer match: {', '.join(broken)}  <-- update this script")
    return 0 if len(caught) == len(MUTATIONS) and restored and not broken else 1


if __name__ == "__main__":
    sys.exit(main())
