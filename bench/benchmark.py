"""Benchmark: does a curated harness actually produce a sharper Bot than stock creation?

Stock bot-forge keeps every skill in the categories you name. A harness keeps those
categories *and* the domain's must-have skills, but is judged here on the whole picture:
does the Bot end up carrying the skills an expert needs, without carrying a pile of
irrelevant ones?

The gold sets below are written from each domain's actual requirements and are deliberately
NOT derived from the shipped manifests — a benchmark that scores a manifest against itself
proves nothing. `ios.gold` and `harnesses/ios.json` were written independently and are
allowed to disagree; where they do, the disagreement is a finding.

    python3 bench/benchmark.py            # score everything, print a table
    python3 bench/benchmark.py --json     # machine-readable
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import harness  # noqa: E402

# ── the corpus ───────────────────────────────────────────────────────────────
# Each case: a realistic ask, the skill categories a user would naturally name, and the
# skills a competent expert in that domain would be expected to have.
CORPUS = [
    {
        "domain": "ios",
        "ask": "make me a bot that's an expert at iOS apps",
        "skill_categories": ["software-development", "planner", "reviewer"],
        "gold": ["ios-app-delivery", "test-driven-development", "systematic-debugging",
                 "feature-scoping", "project-verification-harness", "grounded-citations"],
        "distractors": ["stocks", "xurl", "youtube-content", "himalaya", "notion"],
    },
    {
        "domain": "trading",
        "ask": "make me a trading bot that does real market analysis",
        "skill_categories": ["finance"],
        "gold": ["stocks", "grounded-citations"],
        "distractors": ["ios-app-delivery", "notion", "youtube-content", "obsidian"],
    },
    {
        "domain": "social-media",
        "ask": "build me a social media manager for x.com",
        "skill_categories": ["social-media", "creative", "media"],
        "gold": ["xurl", "humanizer"],
        "distractors": ["ios-app-delivery", "stocks", "himalaya", "stripe"],
    },
    {
        "domain": "business-ops",
        "ask": "make me a bot to run my business day to day",
        "skill_categories": ["productivity", "finance", "email"],
        "gold": ["project-verification-harness", "grounded-citations", "himalaya"],
        "distractors": ["ios-app-delivery", "xurl", "swift"],
    },
]

# Skills a Bot needs regardless of domain — the floor every approach must clear.
ALWAYS_ON = {"file", "web", "browser"}


def build_profile(tmp: Path, skills: dict[str, str]) -> Path:
    """A throwaway profile dir with `skills` = {category/skill-name: skill-name}.

    Also builds a shared `external_dirs` root holding a handful of root-level skills, so the
    benchmark exercises the case that a real install always has: skills reachable from another
    profile's directory. `skills.disabled` is matched by name across all directories, so a
    harness that only curates profile-local skills leaves the shared ones loaded — a bug this
    fixture exists to make visible.
    """
    profile = tmp / "profile"
    for rel, name in skills.items():
        d = profile / "skills" / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: benchmark fixture\n---\n\nx\n")
    shared = tmp / "shared-skills"
    shared.mkdir(parents=True, exist_ok=True)
    # Deliberately category-nested, not root-level: a real shared root is a full skill
    # tree. Root-level fixtures make `_category_of` return "" for every shared skill, which
    # hides the case where an ALWAYS_KEEP member lives outside the profile and gets disabled.
    # Names are deliberately NOT ones the profile also has: inventory lets the profile-local
    # copy win a collision, so a duplicate name here would silently leave no external member
    # of an ALWAYS_KEEP category and the case would go untested.
    for rel, name in (("research/shared-omh-web-research", "shared-omh-web-research"),
                      ("research/shared-llm-wiki", "shared-llm-wiki"),
                      ("autonomous-ai-agents/shared-claude-code", "shared-claude-code"),
                      ("operator/shared-omh-plan", "shared-omh-plan")):
        d = shared / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: shared fixture\n---\n\nx\n")
    # one genuinely root-level shared skill, so both shapes are covered
    (shared / "qmd-memory").mkdir(parents=True, exist_ok=True)
    (shared / "qmd-memory" / "SKILL.md").write_text(
        "---\nname: qmd-memory\ndescription: shared fixture\n---\n\nx\n")
    (profile / "config.yaml").write_text(
        "model:\n  default: m\nskills:\n  external_dirs:\n"
        f"    - {shared}\n")
    return profile


def stock_selection(profile: Path, categories: list[str]) -> set[str]:
    """Baseline: exactly what stock create_agent keeps — every skill in the named categories."""
    keep = set(ALWAYS_ON)
    for skill_md in (profile / "skills").rglob("SKILL.md"):
        rel = skill_md.relative_to(profile / "skills").parts
        if len(rel) > 1 and rel[0] in set(categories) | harness.ALWAYS_KEEP:
            keep.add(harness.skill_name(skill_md))
    return keep


def harness_selection(profile: Path, manifest: dict, categories: list[str]) -> set[str]:
    """Treatment: the harness plan, plus the same always-on floor.

    Note the manifest's own `skill_categories` are used as-is. Substituting the benchmark
    case's categories here would silently re-broaden the manifest and measure the case
    rather than the curation — which is exactly the mistake this benchmark exists to catch.
    """
    result = harness.plan(profile, manifest)
    return set(result["keep"]) | ALWAYS_ON


def score(selected: set[str], case: dict) -> dict:
    """Precision/recall/F1 against the gold set, plus an irrelevance count.

    `curated_*` exclude the always-on floor, so they measure what curation actually
    decides rather than the toolsets and research skills every Bot gets regardless.
    """
    gold = set(case["gold"])
    hit = selected & gold
    distractors = selected & set(case["distractors"])
    fp = selected - gold
    precision = len(hit) / len(selected) if selected else 0.0
    recall = len(hit) / len(gold) if gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    curated = selected - FLOOR
    curated_hits = curated & gold
    c_prec = len(curated_hits) / len(curated) if curated else 1.0
    c_rec = len(curated_hits) / len(gold) if gold else 0.0
    c_f1 = (2 * c_prec * c_rec / (c_prec + c_rec)) if (c_prec + c_rec) else 0.0
    return {
        "domain": case["domain"],
        "selected": len(selected),
        "gold": len(gold),
        "hits": sorted(hit),
        "missed": sorted(gold - hit),
        "irrelevant": len(fp),
        "curated_selected": len(curated),
        "distractors_carried": sorted(distractors),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "curated_precision": round(c_prec, 4),
        "curated_recall": round(c_rec, 4),
        "curated_f1": round(c_f1, 4),
    }


def fixture_universe() -> dict[str, str]:
    """A skill universe wide enough that every case's gold and distractors exist."""
    spec: dict[str, str] = {}
    per_category = {
        "software-development": ["ios-app-delivery", "test-driven-development", "systematic-debugging",
                                 "feature-scoping", "codebase-inspection", "claude-code", "codex",
                                 "opencode", "github", "spike", "simplify-code"],
        "planner": ["omh-plan", "omh-product-brief", "omh-cto-loop", "plan-blueprint-tdd"],
        "reviewer": ["omh-code-review", "omh-security-safety-review", "requesting-code-review",
                     "omh-verification-gate"],
        "finance": ["stocks", "consumer-billing-disputes", "consumer-dispute-correspondence"],
        "social-media": ["xurl", "omh-image-cards"],
        "creative": ["humanizer", "omh-frontend", "claude-design"],
        "media": ["youtube-content", "gif-search"],
        "productivity": ["notion", "xlsx", "pdf", "scheduled-tasks", "obsidian"],
        "email": ["himalaya"],
        "research": ["omh-web-research", "omh-research-brief", "llm-wiki", "arxiv"],
        "apple": ["macos-storage-cleanup", "findmy"],
        "note-taking": ["obsidian", "second-brain"],
        "review-digest-html": ["review-digest-html"],
        "finance-extra": ["grounded-citations"],
    }
    for cat, names in per_category.items():
        for n in names:
            spec[f"{cat}/{n}"] = n
    # root-level shared skills (external_dirs style)
    for n in ["grounded-citations", "project-verification-harness", "qmd-memory", "i-have-adhd"]:
        spec[n] = n
    return spec


def run(as_json: bool = False) -> tuple[dict, dict]:
    """Score the corpus. Returns (per-case results, summary)."""
    results = {"stock": [], "harness": []}

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        profile = build_profile(tmp, fixture_universe())

        for case in CORPUS:
            results["stock"].append(score(stock_selection(profile, case["skill_categories"]), case))

            manifest = harness.load_manifest(case["domain"])
            if manifest:
                sel = harness_selection(profile, manifest, case["skill_categories"])
            else:
                # No bundled manifest: fall back to categories only, which is what an
                # uncurated domain gets. A missing manifest is a real limitation, so it
                # must show up in the numbers rather than being quietly skipped.
                sel = set(stock_selection(profile, case["skill_categories"]))
            results["harness"].append(score(sel, case))

    def agg(rows, key):
        vals = [r[key] for r in rows]
        return sum(vals) / len(vals) if vals else 0.0

    summary = {
        approach: {
            "mean_precision": round(agg(rows, "precision"), 4),
            "mean_recall": round(agg(rows, "recall"), 4),
            "mean_f1": round(agg(rows, "f1"), 4),
            "mean_curated_f1": round(agg(rows, "curated_f1"), 4),
            "mean_curated_precision": round(agg(rows, "curated_precision"), 4),
            "mean_irrelevant": round(agg(rows, "irrelevant"), 2),
            "distractors_carried": sum(len(r["distractors_carried"]) for r in rows),
            "missed_gold": sum(len(r["missed"]) for r in rows),
        }
        for approach, rows in results.items()
    }
    return results, summary


def print_table(results: dict, summary: dict) -> None:
    print(f"{'domain':<15}{'approach':<10}{'sel':>5}{'hits':>6}{'miss':>6}{'irrel':>7}{'prec':>8}{'rec':>8}{'F1':>8}")
    print("-" * 73)
    for approach in ("stock", "harness"):
        for r in results[approach]:
            print(f"{r['domain']:<15}{approach:<10}{r['selected']:>5}{len(r['hits']):>6}"
                  f"{len(r['missed']):>6}{r['irrelevant']:>7}{r['precision']:>8.3f}"
                  f"{r['recall']:>8.3f}{r['f1']:>8.3f}")
    print("-" * 73)
    for approach, s in summary.items():
        print(f"{'':<25}{approach:<10}{'':>5}{'':>6}{'':>6}"
              f"{s['mean_irrelevant']:>7.2f}{s['mean_precision']:>8.3f}"
              f"{s['mean_recall']:>8.3f}{s['mean_f1']:>8.3f}")
    print()
    s, h = summary["stock"], summary["harness"]
    print(f"mean F1   stock {s['mean_f1']:.3f}  ->  harness {h['mean_f1']:.3f}"
          f"   ({h['mean_f1'] - s['mean_f1']:+.3f})")
    print(f"precision stock {s['mean_precision']:.3f}  ->  harness {h['mean_precision']:.3f}"
          f"   ({h['mean_precision'] - s['mean_precision']:+.3f})")
    print(f"irrelevant skills carried: {s['mean_irrelevant']:.1f} -> {h['mean_irrelevant']:.1f}")
    print(f"distractor skills carried: {s['distractors_carried']} -> {h['distractors_carried']}")
    print(f"gold skills missed:        {s['missed_gold']} -> {h['missed_gold']}")
    print()
    print("curated only (excluding the always-on floor every Bot gets):")
    print(f"  curated F1        {s['mean_curated_f1']:.3f} -> {h['mean_curated_f1']:.3f}"
          f"   ({h['mean_curated_f1'] - s['mean_curated_f1']:+.3f})")
    print(f"  curated precision {s['mean_curated_precision']:.3f} -> {h['mean_curated_precision']:.3f}"
          f"   ({h['mean_curated_precision'] - s['mean_curated_precision']:+.3f})")


# ── gates ────────────────────────────────────────────────────────────────────
# Thresholds the shipped manifests must clear. These are regression gates, not goals:
# a change that makes a harness broader or sloppier should fail here rather than ship.

MIN_IOS_F1 = 0.60
MAX_IOS_IRRELEVANT = 12

# `file`/`web`/`browser` are handed to every Bot regardless of domain, and ALWAYS_KEEP
# guarantees the research/web skills every Bot needs to check its own facts. Those are the
# floor, not curation choices, so they are excluded from the gate's precision target.
FLOOR = {"file", "web", "browser", "omh-web-research"}


def gates(results: dict) -> list[dict]:
    """Check the shipped manifests against the corpus. Returns a list of failures."""
    out = []
    by_domain = {r["domain"]: r for r in results["harness"]}
    ios = by_domain.get("ios")
    if not ios:
        out.append({"gate": "ios manifest is benchmarked", "ok": False,
                    "detail": "no ios result — the only bundled manifest is not being measured"})
    else:
        if ios["f1"] < MIN_IOS_F1:
            out.append({"gate": f"ios F1 >= {MIN_IOS_F1}", "ok": False,
                        "detail": f"ios F1 is {ios['f1']:.3f} — the manifest is over-inclusive; "
                                  f"drop broad skill_categories and list skills explicitly"})
        if ios["irrelevant"] > MAX_IOS_IRRELEVANT:
            out.append({"gate": f"ios irrelevant <= {MAX_IOS_IRRELEVANT}", "ok": False,
                        "detail": f"ios carries {ios['irrelevant']} non-gold skills"})
        if ios["missed"]:
            out.append({"gate": "ios misses no gold skill", "ok": False,
                        "detail": f"missing: {ios['missed']}"})
    return out


def main_gated() -> int:
    """Run the benchmark, print it, then fail loudly if a shipped manifest regressed."""
    results, summary = run()
    print_table(results, summary)
    failures = gates(results)
    print()
    for f in failures:
        print(f"GATE FAILED  {f['gate']}: {f['detail']}")
    if failures:
        print(f"\n{len(failures)} benchmark gate(s) failed.")
        return 1
    print("All benchmark gates passed.")
    return 0


def main() -> int:
    if "--gated" in sys.argv:
        return main_gated()
    if "--json" in sys.argv:
        results, summary = run()
        print(json.dumps({"cases": results, "summary": summary}, indent=2))
        return 0
    results, summary = run()
    print_table(results, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
