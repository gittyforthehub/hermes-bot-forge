"""Process benchmark: does curated harness selection behave correctly for ANY domain?

A per-domain gold set is not a general test — it needs a human to know what an expert in
that domain should carry, which is exactly what you cannot write for every possible Bot.
So this benchmark scores the *process* instead: the curation pipeline is fed a spec
(manifest) and must realize it faithfully, for arbitrary generated domains.

Three properties are checkable without knowing anything about the domain:

  fidelity      every skill the spec names is either enabled, or reported as a gap.
                Never silently dropped, never claimed but absent.
  contamination nothing survives that the spec did not ask for, beyond the floor that
                every Bot gets anyway. This is what separates curation from inheritance.
  efficiency    the set kept is the set the spec asked for — no padding from category
                fallbacks, no redundant skills. Measured as: for a spec that names skills
                and no categories, curation keeps exactly the named skills plus the
                floor, and nothing else.

Deliberately NOT claimed: that curation always keeps *fewer* skills than stock. It does
not, and it should not. Stock is "pick a category"; a manifest that names six specific
skills legitimately carries more than stock's single category, and that is the manifest
being precise rather than padded. The real claim is fidelity and the absence of
contamination — a harness is judged on whether it delivers exactly the spec, not on
winning a count contest against a different question.

Stock bot-forge still appears in the report, as context for how many skills the two
approaches carry — not as a pass/fail line.

    python3 bench/process_bench.py            # score, print a table
    python3 bench/process_bench.py --gated    # fail on any invariant breach (CI runs this)
    python3 bench/process_bench.py --json     # machine-readable

Domains are generated, not hand-written, so this scales to a manifest nobody has seen yet.
"""

from __future__ import annotations

import json
import random
import re
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import harness  # noqa: E402
from benchmark import ALWAYS_ON, build_profile, fixture_universe  # noqa: E402

# Every Bot gets these regardless of domain: the tools, plus the skills inside the
# categories ALWAYS_KEEP protects. Those are *category members*, not category names —
# mixing the two silently undercounts the floor and manufactures fake contamination.
FLOOR_CATEGORIES = set(harness.ALWAYS_KEEP)


def floor_skills(profile: Path) -> set[str]:
    """The concrete skills ALWAYS_KEEP guarantees, resolved against the real fixture."""
    out = set(ALWAYS_ON)
    for skill_md in (profile / "skills").rglob("SKILL.md"):
        rel = skill_md.relative_to(profile / "skills").parts
        if len(rel) > 1 and rel[0] in FLOOR_CATEGORIES:
            out.add(harness.skill_name(skill_md))
    return out

def category_members(profile: Path, category: str) -> set[str]:
    """Concrete skills living in one category, resolved against the real fixture."""
    out = set()
    for skill_md in (profile / "skills").rglob("SKILL.md"):
        rel = skill_md.relative_to(profile / "skills").parts
        if len(rel) > 1 and rel[0] == category:
            out.add(harness.skill_name(skill_md))
    return out


CATEGORIES = ["software-development", "planner", "reviewer", "finance", "social-media",
              "creative", "media", "productivity", "email", "research", "note-taking",
              "mlops", "operator", "tracker", "guide", "creative-writing"]

SILLY = ["forge", "atlas", "quill", "beekeeping", "sourdough", "orbital-mechanics",
         "chess-openings", "birdwatching", "cartography", "luthiery"]


def synth_domains(n: int, seed: int = 7) -> list[dict]:
    """Generate `n` arbitrary manifests, including deliberately awkward ones."""
    rng = random.Random(seed)
    universe = sorted({v for v in fixture_universe().values()})
    out = []
    for i in range(n):
        cats = rng.sample(CATEGORIES, rng.randint(0, 3))
        k = rng.randint(1, 8)
        skills = rng.sample(universe, min(k, len(universe)))
        out.append({
            "domain": f"{rng.choice(SILLY)}-{i}",
            "label": f"Synthetic domain {i}",
            "summary": "generated for the process benchmark",
            "skill_categories": cats,
            "skills": skills,
        })
    # Edge cases a generated corpus would otherwise never produce.
    out.append({"domain": "edge-empty-skills", "label": "No skills", "summary": "s",
                "skill_categories": [], "skills": []})
    out.append({"domain": "edge-no-categories", "label": "No categories", "summary": "s",
                "skill_categories": [], "skills": ["ios-app-delivery"]})
    out.append({"domain": "edge-broad", "label": "Everything", "summary": "s",
                "skill_categories": CATEGORIES[:8], "skills": ["ios-app-delivery"]})
    out.append({"domain": "edge-absent", "label": "Skills that do not exist", "summary": "s",
                "skill_categories": [], "skills": ["no-such-skill-a", "no-such-skill-b"]})
    out.append({"domain": "edge-overlap", "label": "Skills inside its own categories", "summary": "s",
                "skill_categories": ["software-development"],
                "skills": ["test-driven-development", "codebase-inspection"]})
    out.append({"domain": "edge-dupes", "label": "Duplicate entries", "summary": "s",
                "skill_categories": ["finance", "finance"],
                "skills": ["stocks", "stocks", " stocks "]})
    return out


def stock_keep(profile: Path, manifest: dict, floor: set[str]) -> set[str]:
    """Baseline: what stock create_agent keeps — every skill in the named categories.

    The always-on floor is included, because a real Bot gets it either way. Excluding it
    from the baseline while the treatment keeps it would penalise curation with a fixed
    cost it has no control over, and make small focused specs look 'not sharper' when they
    are in fact carrying exactly the right skills.
    """
    cats = set(manifest.get("skill_categories") or []) | set(harness.ALWAYS_KEEP)
    keep = set(floor)
    for skill_md in (profile / "skills").rglob("SKILL.md"):
        rel = skill_md.relative_to(profile / "skills").parts
        if len(rel) > 1 and rel[0] in cats:
            keep.add(harness.skill_name(skill_md))
    return keep


def _skill_roots(profile: Path) -> list[Path]:
    """Every skill directory this profile loads, re-derived from its config.yaml.

    Deliberately independent of `harness.external_dirs`. The oracle has to be able to
    disagree with the implementation, or it cannot catch a bug in it — an earlier version
    of this file called the helper under test, which meant breaking external-dir discovery
    broke both sides at once and all 46 gates still passed.

    The YAML is parsed here rather than via `harness._read_cfg` for the same reason.
    """
    roots = [profile / "skills"]
    cfg_path = profile / "config.yaml"
    if not cfg_path.is_file():
        return roots
    try:
        cfg = yaml.safe_load(cfg_path.read_text())
    except Exception:
        return roots
    skills_cfg = cfg.get("skills") if isinstance(cfg, dict) else None
    if not isinstance(skills_cfg, dict):
        return roots
    dirs = skills_cfg.get("external_dirs") or []
    if isinstance(dirs, str):
        dirs = [dirs]
    for d in dirs:
        if d:
            roots.append(Path(str(d)).expanduser())
    return roots


def _own_inventory(profile: Path) -> dict[str, Path]:
    """Every skill this profile can actually load, mapped name -> SKILL.md, derived here.

    Walks the filesystem rather than calling `harness.inventory_with_externals`. The oracle
    has to be able to disagree with the implementation; an oracle that enumerates skills
    with the same code it is checking cannot disagree about anything that matters.

    `rglob`, not `glob`: Hermes skill trees nest (a category folder containing a skill), and
    a one-level scan silently under-counts the world — which makes the oracle wrong in a way
    that looks exactly like the implementation being wrong. The value is the SKILL.md path,
    matching the real inventory, so the category walk below can take its first segment.
    Profile-local skills win a name collision, matching the real load order.
    """
    out: dict[str, Path] = {}
    for root in _skill_roots(profile):
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            name = _own_skill_name(skill_md)
            if name and name not in out:
                out[name] = skill_md
    # local overrides external
    for skill_md in sorted((profile / "skills").rglob("SKILL.md")):
        name = _own_skill_name(skill_md)
        if name:
            out[name] = skill_md
    return out


def _own_skill_name(skill_md: Path) -> str:
    """The `name:` from a SKILL.md, re-derived with its own regex."""
    try:
        text = skill_md.read_text(errors="ignore")
    except OSError:
        return ""
    m = re.search(r"^name:\s*['\"]?([^'\"\n]+)", text, re.M)
    return m.group(1).strip() if m else skill_md.parent.name


def _categories_by_name(available: dict[str, Path], roots: list[Path]) -> dict[str, set[str]]:
    """Every category each skill name appears in, across ALL of its copies.

    `available` keeps one path per name (first one wins), so a skill that exists in two
    category folders is represented by a single arbitrary copy. Using that copy's category
    makes the oracle depend on filesystem enumeration order, which differs between macOS and
    Linux — the same manifest then passes on one platform and fails on the other. So walk
    every root again and collect the full set.
    """
    def category_of(path: Path) -> str:
        for r in roots:
            try:
                rel = path.relative_to(r).parts
            except ValueError:
                continue
            if len(rel) > 1:
                return rel[0]
        return ""

    out: dict[str, set[str]] = {n: set() for n in available}
    for root in roots:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            name = _own_skill_name(skill_md)
            if name in out:
                cat = category_of(skill_md)
                if cat:
                    out[name].add(cat)
    return out


def check_domain(profile: Path, manifest: dict, floor: set[str]) -> dict:
    """Run the real pipeline against one manifest and evaluate the three properties."""
    cats = set(harness._name_list(manifest.get("skill_categories")))
    # Normalize exactly as the pipeline does, or this check measures its own fixture:
    # `" stocks "` is one skill name, not a gap.
    named = set(harness._name_list(manifest.get("skills")))
    result = harness.plan(profile, manifest)
    keep = set(result["keep"])
    # Inventory is re-derived here too, not taken from the helper under test. When external
    # roots went missing from the implementation, an oracle built on the implementation's
    # own inventory simply saw a smaller world and agreed with it — 46/46 passed while a real
    # shared skill had silently stopped being curated. The oracle has to enumerate the
    # filesystem itself.
    available = _own_inventory(profile)
    report = harness.report(manifest, result, [])

    cfg = {"skills": {}}
    harness.apply_plan(cfg, result)
    disabled = set(cfg["skills"].get("disabled") or [])

    # ── fidelity: named skills are enabled if present, reported as gaps if not ──
    present = named & set(available)
    absent = named - set(available)
    fidelity_ok = (
        present <= set(result["enable"]) | keep
        and absent == set(result["missing"]) == set(report["missing_skills"])
    )

    # ── contamination: nothing kept that the spec did not ask for, beyond the floor ──
    # The expected set is computed here, independently of harness's own category helper.
    # Reusing the helper under test makes the check agree with whatever the code does —
    # which is how a bug in that helper passed 46/46 for a whole review cycle. A shared
    # skill's category is its first segment below ANY skill root, and that is re-derived
    # from the path rather than by calling the implementation.
    #
    # Root discovery is re-derived too. An earlier version built this list with
    # `harness.external_dirs(harness._read_cfg(profile))`, so breaking external-dir
    # discovery broke the oracle in the same way as the code under test and every gate
    # still passed 46/46 — the benchmark could not see the exact class of regression it
    # was cited as covering. Read the config here instead.
    roots = _skill_roots(profile)

    # The floor is re-derived here, not taken from floor_skills(): that helper resolves
    # against the same code path under test, so it agreed with the bug. The three rules a
    # skill must satisfy to be exempt from curation, spelled out independently.
    # Categories come from ALL copies of a skill (see _categories_by_name), for the same
    # reason: a single arbitrary copy makes the answer depend on enumeration order.
    multi_cat = _categories_by_name(available, roots)
    all_cats = multi_cat
    derived_floor = {n for n in all_cats
                     if n in harness.NEVER_DISABLE or n in ALWAYS_ON
                     or all_cats[n] & set(harness.ALWAYS_KEEP)}

    allowed = (named
               | {n for n, cats_of in all_cats.items() if cats_of & set(cats)}
               | derived_floor)
    leaks = sorted(keep - allowed)
    # Every loadable skill outside the allowlist must be disabled — external ones included,
    # because `skills.disabled` is matched by name across all skill directories. Anything
    # still loadable after the plan is contamination the harness failed to remove.
    still_loadable = sorted((set(available) - set(disabled)) - allowed)
    # A disable Hermes ignores is worse than none: it looks curated but changes nothing.
    phantom_disables = sorted(disabled - set(available))
    # ALWAYS_KEEP is unconditional: a skill in one of those categories is never disabled,
    # from ANY root. Asserted as a floor invariant rather than a category check, because a
    # per-manifest check only covers it when the manifest happens to name such a category.
    always_keep_members = {n for n, cats_of in all_cats.items() if cats_of & set(harness.ALWAYS_KEEP)}
    floor_violations = sorted(always_keep_members & disabled)
    # ── efficiency: a spec that names skills and no categories must keep exactly those
    #    skills plus the floor. Any extra skill is padding the spec did not ask for.
    #    Broader specs are not measured here: naming categories legitimately adds skills.
    #    Compare against the skills that actually exist: a name that is absent from the
    #    profile cannot be kept, and reporting it as a gap is the correct behaviour
    #    (fidelity covers that). Judging efficiency on it would punish the honest path.
    exact = bool(named) and not cats
    expected = present | derived_floor
    efficiency = (not exact) or (keep | derived_floor) == expected

    stock = stock_keep(profile, manifest, derived_floor)
    keep_with_floor = keep | derived_floor
    focused = bool(named) and len(cats) <= 2
    sharper = (not focused) or len(keep_with_floor) < len(stock)

    # ── determinism / idempotence ──
    again = harness.plan(profile, manifest)
    deterministic = again == result
    cfg2 = {"skills": {}}
    harness.apply_plan(cfg2, result)
    harness.apply_plan(cfg2, harness.plan(profile, manifest))
    idempotent = cfg2 == cfg

    return {
        "domain": manifest["domain"],
        "kept": len(keep_with_floor),
        "stock": len(stock),
        "named": len(named),
        "focused": focused,
        "exact": exact,
        "present": len(present),
        "absent": sorted(absent),
        "leaks": leaks,
        "still_loadable": still_loadable,
        "phantom_disables": phantom_disables,
        "floor_violations": floor_violations,
        "fidelity": fidelity_ok,
        "contamination": not leaks and not still_loadable and not phantom_disables and not floor_violations,
        "efficiency": efficiency,
        "sharper": sharper,
        "deterministic": deterministic,
        "idempotent": idempotent,
    }


INVARIANTS = ["fidelity", "contamination", "efficiency", "deterministic", "idempotent"]


def run(n_domains: int = 40) -> dict:
    cases = []
    with tempfile.TemporaryDirectory() as t:
        profile = build_profile(Path(t), fixture_universe())
        floor = floor_skills(profile)
        for manifest in synth_domains(n_domains):
            cases.append(check_domain(profile, manifest, floor))

    totals = {k: sum(1 for c in cases if c[k]) for k in INVARIANTS}
    breaches = [c for c in cases if not all(c[k] for k in INVARIANTS)]
    exact_cases = [c for c in cases if c.get("exact")]
    stock_mean = sum(c["stock"] for c in cases) / len(cases)
    kept_mean = sum(c["kept"] for c in cases) / len(cases)
    sharper_n = sum(1 for c in cases if c["sharper"])

    return {
        "cases": cases,
        "n_domains": len(cases),
        "n_exact": len(exact_cases),
        "pass_rate": round(1 - len(breaches) / len(cases), 4),
        "invariants": {k: {"passed": totals[k], "of": len(cases)} for k in INVARIANTS},
        "breaches": breaches,
        "mean_kept": round(kept_mean, 2),
        "mean_stock": round(stock_mean, 2),
        "domains_sharper_than_stock": sharper_n,
    }


def gates(result: dict) -> list[dict]:
    """Every invariant must hold for every generated domain. No partial credit.

    `sharper` is reported but never gated: whether curation beats stock on skill *count*
    depends on what the manifest asks for, not on whether the pipeline is correct.
    """
    out = []
    for name, tally in result["invariants"].items():
        if tally["passed"] != tally["of"]:
            out.append({"gate": f"{name} holds for all domains", "ok": False,
                        "detail": f"{tally['of'] - tally['passed']} of {tally['of']} domains breach it"})
    return out


def negative_tests() -> list[dict]:
    """Fault injection: break the pipeline on purpose, confirm the gate catches it.

    A benchmark that passes because it checks nothing is worse than no benchmark, so
    every invariant must be shown to fail when the behaviour it guards is removed. Each
    case monkeypatches a single seam in the real pipeline, re-runs the generated corpus,
    and asserts the named invariant actually fails.
    """
    import harness as _h
    cases = []

    def check(label, invariant, fault):
        """Apply `fault`, run the corpus, and record whether `invariant` caught it."""
        original = fault["restore"]
        fault["apply"]()
        try:
            result = run()
        finally:
            original()
        tally = result["invariants"][invariant]
        caught = tally["passed"] != tally["of"]
        cases.append({
            "label": label,
            "invariant": invariant,
            "caught": caught,
            "passed": tally["passed"],
            "of": tally["of"],
        })

    real_plan = _h.plan

    # 1. Fidelity: stop reporting skills that are missing.
    def drop_missing():
        _h.plan = lambda p, m: {**real_plan(p, m), "missing": []}
    check("missing skills silently dropped", "fidelity", {
        "apply": drop_missing,
        "restore": lambda: setattr(_h, "plan", real_plan),
    })

    # 2. Contamination: pad the allowlist with a skill the spec never asked for.
    def leak_skill():
        def leaky(p, m):
            r = real_plan(p, m)
            extra = sorted(set(r["keep"]) | {"himalaya"})[0]
            return {**r, "keep": set(r["keep"]) | {"himalaya"}, "enable": set(r["enable"]) | {extra}}
        _h.plan = leaky
    check("allowlist padded with an unrequested skill", "contamination", {
        "apply": leak_skill,
        "restore": lambda: setattr(_h, "plan", real_plan),
    })

    # 3. Efficiency: fall back to a whole category when skills are named.
    #    Uses a category that genuinely exists in the fixture, so the fault is a real
    #    over-inclusion rather than a phantom name that could not inflate anything.
    def category_fallback():
        def fat(p, m):
            r = real_plan(p, m)
            if m.get("skills") and not m.get("skill_categories"):
                r = {**r, "keep": set(r["keep"]) | category_members(p, "productivity")}
            return r
        _h.plan = fat
    check("category fallback added to an exact spec", "efficiency", {
        "apply": category_fallback,
        "restore": lambda: setattr(_h, "plan", real_plan),
    })

    # 4. Determinism: make planning depend on call order.
    _state = {"n": 0}
    def nondeterministic():
        def wobbly(p, m):
            _state["n"] += 1
            r = real_plan(p, m)
            # alternate between two valid answers on successive calls
            if _state["n"] % 2:
                return {**r, "keep": set(r["keep"]) | {"himalaya"}}
            return r
        _h.plan = wobbly
    check("plan varies between identical calls", "deterministic", {
        "apply": nondeterministic,
        "restore": lambda: setattr(_h, "plan", real_plan),
    })

    return cases


def main() -> int:
    if "--negative" in sys.argv:
        rows = negative_tests()
        print("negative tests — each fault must be caught by its invariant")
        print("-" * 52)
        for r in rows:
            status = "CAUGHT" if r["caught"] else "MISSED"
            print(f"  {r['invariant']:<14}{status:<8} {r['label']}")
        missed = [r for r in rows if not r["caught"]]
        if missed:
            print(f"\n{len(missed)} invariant(s) failed to catch an injected fault.")
            return 1
        print(f"\nAll {len(rows)} injected faults caught.")
        return 0

    result = run()
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
        return 0

    print(f"process benchmark — {result['n_domains']} generated domains")
    print("-" * 52)
    for name, t in result["invariants"].items():
        bar = "PASS" if t["passed"] == t["of"] else "FAIL"
        print(f"  {name:<15}{bar}   {t['passed']:>3}/{t['of']}")
    print("-" * 52)
    print(f"  pass rate          {result['pass_rate']:.3f}")
    print(f"  mean skills kept   {result['mean_kept']:.1f}  (stock picks a category: {result['mean_stock']:.1f})")
    print(f"  exact-spec domains {result['n_exact']} of {result['n_domains']} measured for efficiency")
    print(f"  fewer than stock   {result['domains_sharper_than_stock']}/{result['n_domains']} domains"
          f"  (context, not a gate)")

    if result["breaches"]:
        print()
        for b in result["breaches"][:5]:
            bad = [k for k in INVARIANTS if not b[k]]
            print(f"  BREACH {b['domain']}: {', '.join(bad)}")
            if b["leaks"]:
                print(f"     leaked: {b['leaks'][:5]}")
            if b["absent"]:
                print(f"     unreported/absent mismatch: {b['absent'][:5]}")

    if "--gated" in sys.argv:
        failures = gates(result)
        print()
        for f in failures:
            print(f"GATE FAILED  {f['gate']}: {f['detail']}")
        if failures:
            print(f"\n{len(failures)} gate(s) failed.")
            return 1
        print("All process gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
