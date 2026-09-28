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


class MalformedManifestTests(unittest.TestCase):
    """Manifests are community-authored JSON, so a typo must not raise.

    A contributor submitting `"skills": "not-a-list"` gets a reported gap, not a crash that
    takes down an unrelated `create_agent` call.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = Path(self.tmp.name)
        make_skill(self.profile / "skills", "finance/stocks", "stocks")
        make_skill(self.profile / "skills", "planner/plan", "omh-plan")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bare_string_skill_is_one_name_not_characters(self):
        r = harness.plan(self.profile, {"skills": "stocks", "skill_categories": []})
        self.assertEqual(r["missing"], [], "a bare string is a one-item list, not iterable chars")
        self.assertIn("stocks", r["keep"])

    def test_non_list_categories_do_not_raise(self):
        r = harness.plan(self.profile, {"skill_categories": 5, "skills": ["stocks"]})
        self.assertIn("stocks", r["keep"])

    def test_empty_and_missing_keys_are_thin_not_fatal(self):
        for manifest in ({}, {"domain": "x"}, {"skills": None}, {"skill_categories": None}):
            r = harness.plan(self.profile, manifest)
            self.assertIsInstance(r["keep"], list)
            self.assertIsInstance(r["disabled"], list)

    def test_unknown_string_skill_is_reported_as_missing(self):
        r = harness.plan(self.profile, {"skills": "not-a-list", "skill_categories": []})
        self.assertEqual(r["missing"], ["not-a-list"])

    def test_dict_input_does_not_raise(self):
        # e.g. `"skills": {"a": 1}` — iterate the keys at worst, never explode
        r = harness.plan(self.profile, {"skills": {"stocks": True}, "skill_categories": []})
        self.assertIsInstance(r["keep"], list)


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
    """The CLI's `--json` output is the contract. Long identifiers get truncated and wrapped
    in the human table, so parsing JSON is the only safe path."""

    SAMPLE = """
noise before the payload
[
  {
    "name": "swiftui",
    "identifier": "skills-sh/prisma-labs-dev/apple-skills/swiftui",
    "source": "skills.sh",
    "trust_level": "community",
    "description": "Indexed by skills.sh from prisma-labs-dev/apple-skills"
  },
  {
    "name": "swiftui-animation",
    "identifier": "skills-sh/dpearson2699/swift-ios-skills/swiftui-animation",
    "source": "skills.sh",
    "trust_level": "community",
    "description": "Indexed by skills.sh from dpearson2699/swift-ios-skills"
  }
]
"""

    def test_extracts_json_past_leading_noise(self):
        data = registry._json_from(self.SAMPLE)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)

    def test_garbage_input_yields_none(self):
        self.assertIsNone(registry._json_from("no json here"))
        self.assertIsNone(registry._json_from(""))
        self.assertIsNone(registry._json_from("[not closed"))

    def test_norm_keeps_full_identifiers(self):
        row = registry._norm({"name": "swiftui",
                              "identifier": "skills-sh/prisma-labs-dev/apple-skills/swiftui",
                              "source": "skills.sh", "trust_level": "community"})
        self.assertEqual(row["identifier"], "skills-sh/prisma-labs-dev/apple-skills/swiftui")
        self.assertEqual(row["trust"], "community")
        self.assertEqual(row["name"], "swiftui")

    def test_norm_accepts_alternate_field_names(self):
        row = registry._norm({"name": "x", "id": "clawhub-x", "trust": "official"})
        self.assertEqual(row["identifier"], "clawhub-x")
        self.assertEqual(row["trust"], "official")

    def test_norm_collapses_multiline_description(self):
        row = registry._norm({"name": "x", "description": "line one\n   line two"})
        self.assertEqual(row["description"], "line one line two")

    def test_norm_tolerates_missing_fields(self):
        row = registry._norm({})
        self.assertEqual(row["name"], "")
        self.assertEqual(row["identifier"], "")
        self.assertEqual(row["description"], "")

    def test_no_corrupt_split_identifiers(self):
        """Regression: the human table wrapped long identifiers across lines, which a table
        parser turned into garbage like '-labs-dev/apple-' + 'skills/swiftui'."""
        for row in (registry._norm(r) for r in (registry._json_from(self.SAMPLE) or [])):
            self.assertNotIn(" ", row["identifier"])
            self.assertTrue(row["identifier"].startswith(("skills-sh/", "clawhub")))


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

    def test_shared_skill_is_disabled_when_the_manifest_does_not_want_it(self):
        # `skills.disabled` is matched by name against every skill directory, including
        # external ones, so a shared skill outside the allowlist must be switched off.
        # Leaving it enabled was a real bug: it loaded ~138 irrelevant skills per Bot.
        r = harness.plan(self.profile, {"skills": ["ios-app-delivery"], "skill_categories": []}, self.cfg())
        self.assertIn("grounded-citations", r["disabled"],
                      "an external skill outside the allowlist is still loadable and must be disabled")
        self.assertIn("stocks", r["disabled"], "profile-local off-domain skill is disabled")

    def test_shared_skill_named_in_the_manifest_is_kept(self):
        r = harness.plan(self.profile, {"skills": ["grounded-citations"], "skill_categories": []}, self.cfg())
        self.assertNotIn("grounded-citations", r["disabled"])
        self.assertIn("grounded-citations", r["keep"])

    def test_essential_skill_is_never_disabled(self):
        # Hermes ignores a disable for `hermes-agent`, so curating against it would
        # report a disable that silently never takes effect.
        make_skill(self.profile / "skills", "hermes-agent", "Use when configuring Hermes.")
        r = harness.plan(self.profile, {"skills": ["ios-app-delivery"], "skill_categories": []}, self.cfg())
        self.assertNotIn("hermes-agent", r["disabled"])
        self.assertIn("hermes-agent", harness.NEVER_DISABLE, "the guard must name what it protects")

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
