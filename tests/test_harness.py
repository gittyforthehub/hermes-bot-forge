"""Tests for expert-harness curation. Pure logic only — no network, no profile creation.

Run: python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import harness  # noqa: E402
import registry  # noqa: E402
import yaml  # noqa: E402


def make_skill(base: Path, rel: str, name: str) -> Path:
    """Create <base>/<rel>/SKILL.md with the given frontmatter name."""
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test skill\n---\n\nbody\n")
    return d / "SKILL.md"


class HarnessInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self.tmp.name)
        self.skills = self.profile / "skills"

    def tearDown(self):
        self.tmp.cleanup()

    def test_inventory_reads_frontmatter_name_not_dirname(self):
        make_skill(self.skills, "software-development/ios-app-delivery", "ios-app-delivery")
        inv = harness.inventory(self.skills)
        self.assertIn("ios-app-delivery", inv)
        # a differently-named dir must not leak in under its folder name
        make_skill(self.skills, "misc/dir-name-differs", "real-name")
        inv = harness.inventory(self.skills)
        self.assertIn("real-name", inv)
        self.assertNotIn("dir-name-differs", inv)

    def test_inventory_of_missing_dir_is_empty_not_error(self):
        self.assertEqual(harness.inventory(self.profile / "nope"), {})


class HarnessPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self.tmp.name)
        self.skills = self.profile / "skills"
        make_skill(self.skills, "software-development/ios-app-delivery", "ios-app-delivery")
        make_skill(self.skills, "software-development/tdd", "test-driven-development")
        make_skill(self.skills, "planner/plan", "omh-plan")
        make_skill(self.skills, "finance/stocks", "stocks")
        make_skill(self.skills, "social-media/xurl", "xurl")
        make_skill(self.skills, "research/web", "omh-web-research")
        make_skill(self.skills, "media/youtube", "youtube-content")

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, **over):
        m = {
            "domain": "ios",
            "skills": ["ios-app-delivery", "test-driven-development", "not-installed"],
            "skill_categories": ["software-development", "planner"],
        }
        m.update(over)
        return m

    def test_plan_keeps_manifest_skills_and_categories(self):
        r = harness.plan(self.profile, self.manifest())
        self.assertIn("ios-app-delivery", r["keep"])
        self.assertIn("test-driven-development", r["keep"])
        self.assertIn("omh-plan", r["keep"])  # from skill_categories

    def test_plan_always_keeps_research_and_autonomous_ai_agents(self):
        r = harness.plan(self.profile, self.manifest())
        self.assertIn("omh-web-research", r["keep"])

    def test_plan_disables_off_domain_skills(self):
        r = harness.plan(self.profile, self.manifest())
        self.assertIn("stocks", r["disabled"])
        self.assertIn("xurl", r["disabled"])
        self.assertIn("youtube-content", r["disabled"])
        self.assertNotIn("stocks", r["keep"])

    def test_plan_reports_missing_required_skills(self):
        r = harness.plan(self.profile, self.manifest())
        self.assertEqual(r["missing"], ["not-installed"])

    def test_missing_skill_is_not_accidentally_disabled_into_the_report(self):
        # A manifest skill absent from disk must appear in `missing`, never be silently
        # "handled" by being added to the disable list.
        r = harness.plan(self.profile, self.manifest())
        self.assertNotIn("not-installed", r["disabled"])
        self.assertNotIn("not-installed", r["keep"])

    def test_plan_is_deterministic(self):
        a = harness.plan(self.profile, self.manifest())
        b = harness.plan(self.profile, self.manifest())
        self.assertEqual(a, b)


class HarnessApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self.tmp.name)
        self.skills = self.profile / "skills"
        make_skill(self.skills, "software-development/ios", "ios-app-delivery")
        make_skill(self.skills, "finance/stocks", "stocks")

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_merges_into_existing_disabled_list(self):
        cfg = {"skills": {"disabled": ["preexisting"]}}
        r = harness.plan(self.profile, {"skills": ["ios-app-delivery"],
                                        "skill_categories": ["software-development"]})
        harness.apply_plan(cfg, r)
        self.assertIn("preexisting", cfg["skills"]["disabled"])
        self.assertIn("stocks", cfg["skills"]["disabled"])

    def test_apply_never_disables_a_taught_skill(self):
        cfg = {}
        r = harness.plan(self.profile, {"skills": [], "skill_categories": []})
        self.assertIn("ios-app-delivery", r["disabled"])  # off-manifest
        harness.apply_plan(cfg, r, taught={"ios-app-delivery"})
        self.assertNotIn("ios-app-delivery", cfg["skills"]["disabled"])


class ManifestTests(unittest.TestCase):
    def test_bundled_ios_manifest_is_valid(self):
        m = harness.load_manifest("ios")
        self.assertIsNotNone(m, "ios manifest must ship with the plugin")
        self.assertTrue(m["skills"], "manifest needs at least one required skill")
        self.assertTrue(m["toolsets"], "manifest should declare toolsets")

    def test_manifest_key_is_normalized(self):
        # case and separators normalize to the same key
        for key in ("ios", "iOS", "IOS", "  ios  "):
            self.assertIsNotNone(harness.load_manifest(key), f"{key!r} should resolve to the ios manifest")

    def test_normalization_does_not_invent_aliases(self):
        # 'iOS App Delivery' normalizes to 'ios-app-delivery', which is a *skill*, not a domain.
        self.assertIsNone(harness.load_manifest("iOS App Delivery"))

    def test_unknown_domain_returns_none(self):
        self.assertIsNone(harness.load_manifest("not-a-real-domain-xyz"))

    def test_available_domains_includes_ios(self):
        self.assertIn("ios", harness.available_domains())

    def test_every_bundled_manifest_has_a_summary(self):
        for key in harness.available_domains():
            m = harness.load_manifest(key)
            self.assertTrue(m.get("summary"), f"{key} manifest needs a summary")


class RegistryParseTests(unittest.TestCase):
    SAMPLE = """
                Skills Hub — 2 result(s)
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Name            ┃ Description ┃ Source   ┃ Trust     ┃ Identifier     ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ swiftui         │ Builds      │ skills.sh│ community │ skills-sh/x/y   │
│                 │ interfaces  │           │           │                 │
│ swift-concurrency│ Swift ARC   │ clawhub  │ community │ swift           │
└─────────────────┴────────────┴──────────┴────────────┴─────────────────┘
"""

    def test_parses_rows_and_ignores_borders(self):
        rows = registry.parse_search_table(self.SAMPLE)
        names = [r["name"] for r in rows]
        self.assertIn("swiftui", names)
        self.assertIn("swift-concurrency", names)
        self.assertNotIn("Name", names)

    def test_parsed_identifier_is_captured(self):
        rows = registry.parse_search_table(self.SAMPLE)
        row = next(r for r in rows if r["name"] == "swift-concurrency")
        self.assertEqual(row["identifier"], "swift")
        self.assertEqual(row["source"], "clawhub")

    def test_garbage_input_yields_empty_list(self):
        self.assertEqual(registry.parse_search_table("no table here"), [])
        self.assertEqual(registry.parse_search_table(""), [])


class FakeRegistry:
    def __init__(self, results=None, raises=None):
        self.results = results or []
        self.raises = raises
        self.calls = []

    def search(self, query, limit=5):
        self.calls.append((query, limit))
        if self.raises:
            raise self.raises
        return self.results[:limit]


class ResolveRegistryTests(unittest.TestCase):
    def test_resolves_query_to_identifier(self):
        fake = FakeRegistry([{"identifier": "skills-sh/a/b", "name": "swiftui", "source": "skills.sh"}])
        e = harness.resolve_registry_skill(fake, {"query": "swiftui"})
        self.assertEqual(e["status"], "resolved")
        self.assertEqual(e["identifier"], "skills-sh/a/b")

    def test_network_failure_is_a_gap_not_a_crash(self):
        fake = FakeRegistry(raises=OSError("network down"))
        e = harness.resolve_registry_skill(fake, {"query": "swiftui"})
        self.assertIn("lookup failed", e["status"])
        self.assertTrue(e["optional"], "entries are optional by default so a gap is not fatal")

    def test_empty_result_is_reported(self):
        e = harness.resolve_registry_skill(FakeRegistry([]), {"query": "nope"})
        self.assertEqual(e["status"], "not found in registry")

    def test_entry_without_query_or_identifier_is_invalid(self):
        e = harness.resolve_registry_skill(FakeRegistry([]), {})
        self.assertIn("invalid", e["status"])


class ExternalDirsTests(unittest.TestCase):
    """A profile's `skills.external_dirs` are genuinely loadable — the curator must see them
    as available and must never try to disable them (they belong to another profile)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.profile = self.base / "profile"
        self.skills = self.profile / "skills"
        self.shared = self.base / "shared-skills"
        make_skill(self.skills, "software-development/ios", "ios-app-delivery")
        make_skill(self.skills, "finance/stocks", "stocks")
        make_skill(self.shared, "grounded-citations", "grounded-citations")

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self):
        return {"skills": {"external_dirs": [str(self.shared)]}}

    def test_external_dirs_parsed_from_config(self):
        self.assertEqual(harness.external_dirs(self.cfg()), [self.shared])

    def test_external_dirs_accepts_a_bare_string(self):
        self.assertEqual(harness.external_dirs({"skills": {"external_dirs": str(self.shared)}}), [self.shared])

    def test_external_dirs_tolerate_missing_or_bad_config(self):
        self.assertEqual(harness.external_dirs({}), [])
        self.assertEqual(harness.external_dirs({"skills": "not-a-dict"}), [])
        self.assertEqual(harness.external_dirs({"skills": {"external_dirs": None}}), [])

    def test_shared_skill_counts_as_installed_not_missing(self):
        r = harness.plan(self.profile, {"skills": ["grounded-citations"], "skill_categories": []}, self.cfg())
        self.assertEqual(r["missing"], [])
        self.assertIn("grounded-citations", r["enable"])

    def test_shared_skill_is_never_disabled(self):
        r = harness.plan(self.profile, {"skills": ["ios-app-delivery"], "skill_categories": []}, self.cfg())
        self.assertNotIn("grounded-citations", r["disabled"],
                         "an external skill is not this profile's to disable")
        self.assertIn("stocks", r["disabled"], "profile-local off-domain skill is disabled")

    def test_local_skill_wins_over_external_of_same_name(self):
        make_skill(self.shared, "grounded-citations-override", "ios-app-delivery")
        inv = harness.inventory_with_externals(self.profile, self.cfg())
        self.assertTrue(str(inv["ios-app-delivery"]).startswith(str(self.skills)))

    def test_inventory_reads_config_from_disk_when_cfg_omitted(self):
        (self.profile / "config.yaml").write_text(
            yaml.safe_dump({"skills": {"external_dirs": [str(self.shared)]}}))
        inv = harness.inventory_with_externals(self.profile)
        self.assertIn("grounded-citations", inv)

    def test_bad_config_on_disk_does_not_raise(self):
        (self.profile / "config.yaml").write_text("{{{ not yaml")
        self.assertIsInstance(harness.inventory_with_externals(self.profile), dict)


class ReportTests(unittest.TestCase):
    def test_report_lists_gaps_from_missing_and_required_entries(self):
        m = {"domain": "ios", "label": "iOS", "summary": "s"}
        r = {"keep": ["a"], "enable": ["a"], "disabled": ["z", "y"], "missing": ["gone"]}
        entries = [{"query": "q1", "status": "not found in registry", "optional": False},
                   {"query": "q2", "status": "resolved", "optional": True}]
        rep = harness.report(m, r, entries)
        self.assertEqual(rep["missing_skills"], ["gone"])
        self.assertEqual(rep["disabled_count"], 2)
        self.assertTrue(any("gone" in g for g in rep["gaps"]))
        self.assertTrue(any("q1" in g for g in rep["gaps"]))
        self.assertEqual(rep["optional_unresolved"], [])

    def test_report_survives_missing_manifest_fields(self):
        rep = harness.report({}, {"keep": [], "enable": [], "disabled": [], "missing": []}, None)
        self.assertIsNone(rep["domain"])
        self.assertEqual(rep["gaps"], [])


if __name__ == "__main__":
    unittest.main()
