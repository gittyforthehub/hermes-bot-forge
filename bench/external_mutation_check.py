"""Prove the process benchmark can actually see external-skill regressions.

`bench/process_bench.py` is only worth running if it can fail. This script breaks
external-skill handling in `harness.py` and requires `--gated` to notice.

It exists because of a specific past failure: the benchmark's oracle once called
`harness.external_dirs()` and `harness.inventory_with_externals()` — the same functions
it was checking. Breaking external-dir discovery therefore broke the oracle and the
implementation together, and all 46 gates still passed. A benchmark that cannot see the
regression it was written for is worse than no benchmark, because it is cited as evidence.

Run directly, or as a CI step. Always restores harness.py.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harness.py"
ORIGINAL = HARNESS.read_text()

MUTATIONS = [
    ("E1", "external_dirs() ignores skills.external_dirs, so shared skills vanish",
     '    dirs = skills_cfg.get("external_dirs") or []',
     "    dirs = []  # MUTANT: external roots ignored"),

    ("E2", "inventory_with_externals() returns only profile-local skills",
     "    for d in external_dirs(cfg):\n        out.update(inventory(d))   # externals first, so local ones override below",
     "    # MUTANT: externals never enumerated"),
]

E3_BROKEN_INVENTORY = re.compile(
    r"(def inventory\(skills_dir: Path\).*?)(return out)",
    re.DOTALL,
)


def _mutate_inventory_body(text: str) -> str | None:
    """Drop external roots from the inventory by neutering external_dirs at the call site."""
    m = E3_BROKEN_INVENTORY.search(text)
    if not m:
        return None
    return text[: m.end(1)] + "    return {}\n" + text[m.end(2):]


def run_gated():
    return subprocess.run(
        [sys.executable, "bench/process_bench.py", "--gated"],
        cwd=ROOT, capture_output=True, text=True, timeout=900,
    )


def main() -> int:
    base = run_gated()
    if base.returncode != 0:
        print("baseline FAILED — the benchmark must pass before mutations mean anything")
        print((base.stdout + base.stderr)[-2000:])
        return 1
    print("baseline: PASS\n")

    caught, unmatched = [], []
    for mid, desc, find, repl in MUTATIONS:
        if find not in ORIGINAL:
            unmatched.append(mid)
            print(f"{mid}: PATTERN NOT FOUND — update this script")
            continue
        HARNESS.write_text(ORIGINAL.replace(find, repl, 1))
        try:
            p = run_gated()
        finally:
            HARNESS.write_text(ORIGINAL)
        if p.returncode != 0:
            caught.append(mid)
            breach = [l.strip() for l in p.stdout.splitlines() if "FAIL" in l or "BREACH" in l]
            print(f"{mid} CAUGHT  {desc}")
            for b in breach[:2]:
                print(f"          {b}")
        else:
            print(f"{mid} *** MISSED *** {desc}  <-- the benchmark is blind to this")

    final = run_gated()
    restored = final.returncode == 0
    print(f"\nrestored baseline: {'PASS' if restored else 'FAIL'}")
    print(f"caught {len(caught)}/{len(MUTATIONS)}: {', '.join(caught) or 'none'}")
    if unmatched:
        print(f"patterns that no longer match: {', '.join(unmatched)}")
    return 0 if len(caught) == len(MUTATIONS) and restored and not unmatched else 1


if __name__ == "__main__":
    sys.exit(main())
