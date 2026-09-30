"""Mutation check for the 0.19.0 (bot-forge-v2) fixes (L1-L3).

Each mutation reverts one fix; the suite must fail for every one. Run in a scratch clone:
it rewrites source files in place (restored on every path).
"""

import ast
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MUTATIONS = [
    (
        "L1", "the doctor CLI handler does a bare `import doctor` again",
        "__init__.py",
        '        cmd = [sys.executable, str(Path(__file__).parent / "doctor.py")]\n',
        '        import doctor\n        return doctor.cli(args)\n        cmd = []\n',
    ),
    (
        "L2", "the cached workspace index serves a stale Bot roster again",
        "survey.py",
        '            cached["hermes"] = scan_hermes(root)\n',
        '            pass\n',
    ),
    (
        "L3", "update_agent ignores a harness again",
        "manage.py",
        '    if s.get("harness") or s.get("harness_manifest"):\n',
        '    if False:\n',
    ),
]


def run_suite() -> bool:
    out = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"],
                         cwd=ROOT, capture_output=True, text=True, timeout=600)
    return out.returncode == 0


def main():
    print(f"{len(MUTATIONS)} mutations, each reverting one 0.19.0 fix.\n")
    missed = []
    for mid, label, filename, old, new in MUTATIONS:
        target = ROOT / filename
        with tempfile.TemporaryDirectory() as tmp:
            backup = Path(tmp) / "orig"
            shutil.copy(target, backup)
            try:
                src = target.read_text()
                if old not in src:
                    print(f"{mid} STALE     {label} -- pattern not found in {filename}")
                    missed.append(mid)
                    continue
                target.write_text(src.replace(old, new, 1))
                try:
                    ast.parse(target.read_text())
                except SyntaxError:
                    print(f"{mid} INVALID   {label} -- mutation does not parse")
                    missed.append(mid)
                    continue
                if run_suite():
                    print(f"{mid} SURVIVED  {label}")
                    missed.append(mid)
                else:
                    print(f"{mid} caught    {label}")
            finally:
                shutil.copy(backup, target)
    if missed:
        print(f"\nresult: FAIL -- survived: {', '.join(missed)}")
        sys.exit(1)
    print(f"\nresult: PASS -- caught {len(MUTATIONS)}/{len(MUTATIONS)}: {', '.join(m[0] for m in MUTATIONS)}")


if __name__ == "__main__":
    main()
