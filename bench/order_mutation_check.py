"""Prove the process benchmark is independent of filesystem enumeration order.

A skill can live in more than one category folder (the fixture has `obsidian` under both
`productivity/` and `note-taking/`). `inventory()` keeps the first SKILL.md it enumerates for
a duplicate name, so which copy "wins" — and therefore which category that skill appears to
belong to — depends on rglob order. That order differs between macOS and Linux, so an oracle
reading a single copy passed locally and failed in CI: 2 of 46 domains leaked `obsidian`.

This script forces the opposite order and requires the benchmark to be unaffected. A
benchmark whose verdict flips with directory ordering is not measuring the pipeline.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import sys
from pathlib import Path
ROOT = Path(%r)
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "bench"))
import process_bench as pb

_real = pb._own_inventory

def reversed_own_inventory(profile: Path):
    """The oracle's own inventory, with rglob order reversed for every root.

    Monkeypatching harness.inventory would not exercise the failure: the oracle stopped
    calling the implementation. The order sensitivity lived in *its* enumeration, so that
    is what has to be reversed.
    """
    out = {}
    for root in pb._skill_roots(profile):
        if not root.is_dir():
            continue
        for sm in reversed(sorted(root.rglob("SKILL.md"))):
            name = pb._own_skill_name(sm)
            if name and name not in out:
                out[name] = sm
    for sm in reversed(sorted((profile / "skills").rglob("SKILL.md"))):
        name = pb._own_skill_name(sm)
        if name:
            out[name] = sm
    return out

pb._own_inventory = reversed_own_inventory
result = pb.run()
breaches = [b["domain"] for b in result["breaches"]]
print("BREACHES:" + ",".join(breaches))
print("PASSRATE:%%.3f" %% result["pass_rate"])
sys.exit(1 if breaches else 0)
''' % (str(ROOT),)


def main() -> int:
    baseline = subprocess.run([sys.executable, "bench/process_bench.py", "--gated"],
                             cwd=ROOT, capture_output=True, text=True, timeout=900)
    if baseline.returncode != 0:
        print("baseline FAILED")
        print((baseline.stdout + baseline.stderr)[-2000:])
        return 1
    print("baseline (native order): PASS")

    probe = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT,
                           capture_output=True, text=True, timeout=900)
    out = probe.stdout + probe.stderr
    breaches = ""
    for line in out.splitlines():
        if line.startswith("BREACHES:"):
            breaches = line.split(":", 1)[1].strip()
    if probe.returncode == 0:
        print("reversed enumeration order: PASS (order-independent)")
        return 0
    print(f"reversed enumeration order: FAIL — {breaches or out[-500:]}")
    print("  the oracle still depends on which copy of a duplicate-named skill comes first")
    return 1


if __name__ == "__main__":
    sys.exit(main())
