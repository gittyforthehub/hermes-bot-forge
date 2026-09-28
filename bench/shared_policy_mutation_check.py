"""Mutation check: revert each shared-policy fix and require the suite to catch it.

Every entry here is a defect an independent review actually found in this feature — two
data-corruption regressions in copy_agent and share_agent, an unconstrained policy path, and
several ways check_policies lied or crashed. Reverting any one of them must fail the suite;
a fix with no failing test is not a fix.

Run directly, or as a CI step. Restores every touched file on all paths.
"""
"""Mutation check for the shared-policy review fixes. Each MUST be caught."""
import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
R = ROOT
POL, FORGE, TOOLS = R / "policy.py", R / "forge.py", R / "tools.py"
ORIG = {p: p.read_text() for p in (POL, FORGE, TOOLS)}

MUTATIONS = [
    ("F1a", FORGE, "soul_role reads line 1 again (block hides the heading)",
     "    first = _persona(soul).lstrip().split(\"\\n\", 1)[0]",
     "    first = (soul or \"\").lstrip().split(\"\\n\", 1)[0]"),

    ("F1b", FORGE, "ensure_identity rebuilds around the whole file, stacking identity",
     "    persona = _persona(soul)",
     "    persona = soul or \"\""),

    ("F1c", FORGE, "re-inject policy even when there was no block",
     "    if not had_block:\n        return rebuilt",
     "    if False:\n        return rebuilt"),

    ("F3", POL, "policy_path does no containment check",
     "    if lex != root_lex and root_lex not in lex.parents:",
     "    if False:"),

    ("F4", TOOLS, "check_policies reads without errors=replace",
     "            text = soul.read_text(errors=\"replace\")",
     "            text = soul.read_text()"),

    ("F5", TOOLS, "current counted by subtraction again",
     "        \"current\": sum(1 for b in bots if b.get(\"current\")),",
     "        \"current\": len(bots) - len(stale) - len(without),"),

    ("F5b", TOOLS, "default profile not audited",
     "    if default_soul.exists():\n        candidates.append(root)",
     "    if False:\n        candidates.append(root)"),

    ("F8", POL, "cosmetic whitespace not normalised (CRLF/trailing/bullets)",
     "    body = body.replace(\"\\r\\n\", \"\\n\").replace(\"\\r\", \"\\n\")\n"
     "    body = \"\\n\".join(line.rstrip() for line in body.split(\"\\n\"))\n"
     "    body = re.sub(r\"(?m)^[ \\t]*[-*+][ \\t]+\", \"- \", body)\n"
     "    body = re.sub(r\"(?m)^[ \\t]*(\\d+)[.)][ \\t]+\", r\"\\1. \", body)",
     "    pass"),

    ("F9", FORGE, "comments-only policy accepted",
     "        if not policy_mod.policy_body(text):",
     "        if False:"),
]


def run():
    p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                       cwd=R, capture_output=True, text=True, timeout=900)
    return p.returncode, p.stdout + p.stderr


rc, out = run()
print(f"baseline: {'PASS' if rc == 0 else 'FAIL'}")
if rc != 0:
    print(out[-2500:]); sys.exit(1)

caught, unmatched = [], []
for mid, path, desc, find, repl in MUTATIONS:
    src = ORIG[path]
    if find not in src:
        unmatched.append(mid); print(f"{mid}: PATTERN NOT FOUND"); continue
    path.write_text(src.replace(find, repl, 1))
    try:
        rc, out = run()
    finally:
        path.write_text(src)
    if rc != 0:
        caught.append(mid); print(f"{mid} CAUGHT   {desc}")
    else:
        print(f"{mid} *** MISSED *** {desc}  <-- untested")

rc, _ = run()
print(f"\nrestored: {'PASS' if rc == 0 else 'FAIL'}")
print(f"caught {len(caught)}/{len(MUTATIONS)}: {', '.join(caught) or 'none'}")
if unmatched:
    print(f"unmatched: {', '.join(unmatched)}")
sys.exit(0 if len(caught) == len(MUTATIONS) and rc == 0 and not unmatched else 1)
