"""Shared operating policy: injection is correct, idempotent, and drift is visible."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import policy  # noqa: E402
import forge  # noqa: E402


class SharedPolicyRenderTests(unittest.TestCase):
    def test_fingerprints_ignore_surrounding_comments(self):
        a = "# title\n\n- ask before spending\n"
        b = "# title\n\n- ask before spending\n\n<!-- trailing note -->\n"
        self.assertEqual(policy.fingerprint(a), policy.fingerprint(b),
                         "editing a comment above the policy must not look like drift")

    def test_fingerprint_changes_with_policy_text(self):
        self.assertNotEqual(policy.fingerprint("- ask before spending"),
                            policy.fingerprint("- never spend"))

    def test_fingerprint_ignores_cosmetic_reformatting(self):
        """Regression: only comments were normalised, so cosmetics looked like drift.

        A CRLF checkout, trailing whitespace, or a different bullet marker each marked
        EVERY Bot stale. The report's own reasoning — a drift report that fires on
        cosmetics is one people learn to ignore — applies to those too.
        """
        plain = "## Rules\n\n- one\n- two\n"
        for label, variant in (
            ("crlf", "## Rules\r\n\r\n- one\r\n- two\r\n"),
            ("trailing whitespace", "## Rules\n\n- one   \n- two\t\n"),
            ("asterisk bullets", "## Rules\n\n* one\n* two\n"),
            ("plus bullets", "## Rules\n\n+ one\n+ two\n"),
            ("indented bullets", "## Rules\n\n  - one\n  - two\n"),
        ):
            with self.subTest(variant=label):
                self.assertEqual(policy.fingerprint(plain), policy.fingerprint(variant),
                                 f"{label} must not read as a policy change")
        # and a real change must still be caught
        self.assertNotEqual(policy.fingerprint(plain),
                            policy.fingerprint("## Rules\n\n- one\n- three\n"))

    def test_comment_edit_creates_no_blank_line_difference(self):
        """Regression: stripping a comment left its surrounding blank line behind.

        That made a pure annotation look like a changed rule, so every Bot would be
        reported stale after the user tidied the policy file's comments. Found by the
        end-to-end check, not by inspection.
        """
        plain = "## Rules\n\n- ask before spending\n- verify before claiming\n"
        commented = ("## Rules\n\n<!-- remember to word this gently -->\n"
                     "- ask before spending\n- verify before claiming\n")
        self.assertEqual(policy.policy_body(plain), policy.policy_body(commented))
        self.assertEqual(policy.fingerprint(plain), policy.fingerprint(commented))

    def test_comment_inserted_mid_list_is_still_not_drift(self):
        plain = "# p\n\n- one\n- two\n"
        commented = "# p\n\n- one\n<!-- why two matters -->\n- two\n"
        self.assertEqual(policy.fingerprint(plain), policy.fingerprint(commented))

    def test_inject_puts_block_first(self):
        out = policy.inject("- ask first", "# Sable\n\nI am Sable.")
        self.assertTrue(out.startswith(policy.BEGIN))
        self.assertIn("# Sable", out)

    def test_inject_is_idempotent(self):
        once = policy.inject("- ask first", "# Sable\n\nI am Sable.")
        twice = policy.inject("- ask first", once)
        self.assertEqual(once, twice, "rebuilding must not accumulate duplicate blocks")

    def test_reinject_replaces_rather_than_appends(self):
        old = policy.inject("- old rule", "# Sable")
        new = policy.inject("- new rule", old)
        self.assertNotIn("- old rule", new)
        self.assertIn("- new rule", new)
        self.assertEqual(new.count(policy.BEGIN), 1)

    def test_inject_append_position(self):
        out = policy.inject("- ask first", "# Sable", position="append")
        self.assertIn("# Sable", out)
        self.assertTrue(out.rstrip().endswith(policy.END))

    def test_inject_into_empty_soul(self):
        out = policy.inject("- ask first", "")
        self.assertEqual(out.count(policy.BEGIN), 1)
        self.assertIn("- ask first", out)

    def test_audit_reports_current(self):
        soul = policy.inject("- ask first", "# Sable")
        r = policy.audit_soul(soul, policy.fingerprint("- ask first"))
        self.assertTrue(r["current"])
        self.assertTrue(r["has_shared_policy"])

    def test_audit_detects_drift(self):
        soul = policy.inject("- ask first", "# Sable")
        r = policy.audit_soul(soul, policy.fingerprint("- something else"))
        self.assertFalse(r["current"])
        self.assertIn("drifted", r["reason"])

    def test_audit_detects_missing_block(self):
        r = policy.audit_soul("# Sable\n\nNo policy here.", "abc123")
        self.assertFalse(r["current"])
        self.assertFalse(r["has_shared_policy"])
        self.assertIn("no shared-policy block", r["reason"])

    def test_starter_policy_is_itself_valid(self):
        """The shipped starter must inject and audit clean against its own fingerprint."""
        soul = policy.inject(policy.STARTER_POLICY, "# Sable")
        r = policy.audit_soul(soul, policy.fingerprint(policy.STARTER_POLICY))
        self.assertTrue(r["current"])


class SharedPolicyForgeTests(unittest.TestCase):
    """The reason this design exists: identity must stay unique per Bot."""

    def _soul(self, name="sable", display="Sable", role="iOS developer"):
        """A Bot's finished SOUL.md, as forge would build it before policy injection.

        `one_job` is set by forge.forge() immediately before render_soul is called, so it
        is set here too — otherwise this helper would test a shape forge never produces.
        """
        spec = {"display_name": display, "role": role, "sandbox": "local",
                "one_job": f"acts as the user's {role}"}
        return forge.ensure_identity(forge.render_soul(spec, name), display, role, name)

    def test_policy_is_inlined_not_left_behind(self):
        soul = policy.inject(policy.STARTER_POLICY, self._soul())
        self.assertIn("Ask before spending money", soul)

    def test_each_bot_keeps_its_own_identity(self):
        """Two Bots built from the same policy must NOT collapse to one identity.

        This is the regression guard for the whole design: a naive whole-file symlink
        would give every Bot the same name and profile id.
        """
        a = self._soul("sable", "Sable", "iOS developer")
        b = self._soul("bloom", "Bloom", "Swift developer")
        sa = policy.inject(policy.STARTER_POLICY, a)
        sb = policy.inject(policy.STARTER_POLICY, b)
        self.assertIn("Sable", sa)
        self.assertIn("sable", sa)
        self.assertIn("Bloom", sb)
        self.assertIn("bloom", sb)
        self.assertNotEqual(
            [ln for ln in sa.splitlines() if ln.strip()],
            [ln for ln in sb.splitlines() if ln.strip()],
            "two Bots must not produce identical SOUL.md bodies",
        )

    def test_fork_symlink_would_collide_but_inline_does_not(self):
        """Prove the rejected alternative really is broken, so the choice is evidence-based."""
        with_shared = policy.inject(policy.STARTER_POLICY, self._soul())
        # what a whole-file symlink would yield: the SAME text for every Bot
        symlink_equivalent = policy.STARTER_POLICY
        self.assertNotEqual(with_shared, symlink_equivalent)
        # the symlink variant has no Bot-specific identity at all
        self.assertNotIn("sable", symlink_equivalent)
        self.assertNotIn("Sable", symlink_equivalent)


class SharedPolicyInteropTests(unittest.TestCase):
    """The policy block must not break the features that already read SOUL.md.

    Every one of these is a regression found by review, not a hypothetical: the block is
    injected at the top of the file, and three existing helpers assumed the persona's
    heading was line 1.
    """

    def _soul(self, name="sable", display="Sable", role="iOS developer"):
        spec = {"display_name": display, "role": role, "sandbox": "local",
                "one_job": f"acts as the user's {role}"}
        return forge.ensure_identity(forge.render_soul(spec, name), display, role, name)

    def test_role_is_recovered_through_the_block(self):
        """Regression: soul_role() read line 1, which is the block marker.

        Effect: a shared Bot exported no role, and import_agent then rejected the archive
        with "spec needs at least 'role'" — every Bot with a policy was shareable but not
        importable.
        """
        soul = policy.inject(policy.STARTER_POLICY, self._soul())
        self.assertEqual(soul.splitlines()[0], policy.BEGIN)
        self.assertEqual(forge.soul_role(soul), "iOS developer")

    def test_copy_does_not_produce_two_identities(self):
        """Regression: copying rebuilt the file around the marker comment.

        Effect: the copy introduced itself as "the Bot" instead of its role while the
        original heading survived below — exactly what ensure_identity exists to prevent.
        """
        soul = policy.inject(policy.STARTER_POLICY, self._soul())
        copied = forge.ensure_identity(soul, "Bloom", "Bot", "bloom")
        import re as _re
        names = _re.findall(r"You are \*\*([^*]+)\*\*", copied)
        self.assertEqual(names, ["Bloom"], f"expected exactly one identity, got {names}")
        self.assertNotIn("the Bot of this", copied)
        self.assertIn("iOS developer", copied)
        self.assertIn("# Bloom — iOS developer", copied)

    def test_identity_rebuild_keeps_the_policy_block(self):
        soul = policy.inject(policy.STARTER_POLICY, self._soul())
        rebuilt = forge.ensure_identity(soul, "Bloom", "iOS developer", "bloom")
        self.assertEqual(rebuilt.count(policy.BEGIN), 1, "the policy must survive a rename")
        self.assertTrue(policy.audit_soul(
            rebuilt, policy.fingerprint(policy.STARTER_POLICY))["current"])

    def test_share_import_round_trip_keeps_the_role(self):
        import portable
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "SOUL.md"
            p.write_text(policy.inject(policy.STARTER_POLICY, self._soul()))
            self.assertEqual(forge.soul_role(p.read_text(errors="ignore")), "iOS developer")


class SharedPolicyPathSafetyTests(unittest.TestCase):
    """`shared_policy_path` reaches forge from a spec, and specs can come from templates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "hr"
        (self.root / "shared").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_relative_escape_is_refused(self):
        for bad in ("../SECRET.txt", "../../etc/passwd", "shared/../../escape.md"):
            with self.subTest(path=bad):
                with self.assertRaises(policy.PolicyPathError):
                    policy.policy_path(self.root, bad)

    def test_absolute_path_outside_root_is_refused(self):
        outside = Path(self.tmp.name) / "SECRET.txt"
        outside.write_text("[REDACTED]")
        with self.assertRaises(policy.PolicyPathError):
            policy.policy_path(self.root, str(outside))

    def test_default_and_nested_paths_are_allowed(self):
        self.assertEqual(policy.policy_path(self.root), self.root / policy.DEFAULT_RELATIVE)
        nested = policy.policy_path(self.root, "shared/nested/POLICY.md")
        self.assertEqual(nested, self.root / "shared" / "nested" / "POLICY.md")

    def test_a_symlinked_leaf_is_allowed(self):
        """Deliberate: a user pointing their own policy elsewhere is a visible choice.

        Refusing it would also break a Hermes root that is itself reached through a symlink,
        which is common. Only lexical escapes are refused.
        """
        outside = Path(self.tmp.name) / "elsewhere.md"
        outside.write_text("- ask first\n")
        (self.root / "shared" / "BOT-POLICY.md").symlink_to(outside)
        self.assertTrue(policy.policy_path(self.root, "shared/BOT-POLICY.md").exists())

    def test_comments_only_canonical_policy_is_rejected_not_inlined(self):
        """Regression: a comments-only file hashed the same as an empty policy."""
        canon = policy.policy_path(self.root)
        canon.write_text("<!-- TODO: write the house policy -->\n")
        self.assertEqual(policy.fingerprint(canon.read_text()), policy.fingerprint(""))

    def test_forge_rejects_a_comments_only_policy(self):
        """The build path, not just the helper: Bots must not be built with no rules.

        `text.strip()` is truthy for a comments-only file, so the old guard let it through
        and every Bot got a SOUL.md whose policy was, by the drift report's own metric,
        indistinguishable from no policy at all.
        """
        import forge
        canon = policy.policy_path(self.root)
        canon.write_text("<!-- TODO: write the house policy -->\n")
        res = forge.forge({
            "name": "kestrel", "display_name": "Kestrel", "role": "trading analyst",
            "hermes_root": str(self.root), "sandbox": "local",
            "one_job": "analyses trades",
            "settings": {"inherit_model": False, "install_gateway": False,
                         "workspace_survey": False, "journal_enabled": False,
                         "ack_tapback": False, "ack_reactions": False},
        })
        self.assertFalse(res.get("ok"), "a policy with only comments must be rejected")
        self.assertIn("only comments", str(res.get("error", "")))
        self.assertFalse((self.root / "profiles" / "kestrel" / "SOUL.md").exists())


    def test_check_policies_survives_non_utf8_files(self):
        """Regression: UnicodeDecodeError is a ValueError, not an OSError.

        `except OSError` did not catch it, so one latin-1 apostrophe in a hand-edited
        SOUL.md raised out of a tool documented as safe and read-only.
        """
        import tools
        import json as _json
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_bytes(b"- caf\xe9 rule\n- ask first\n")
            (root / "profiles" / "x").mkdir(parents=True)
            (root / "profiles" / "x" / "SOUL.md").write_bytes(b"# X\n\xff\xfe junk")
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            try:
                out = _json.loads(tools.check_policies({}))
            finally:
                tools.hermes_root = orig
        self.assertTrue(out["ok"], out.get("error"))
        self.assertEqual(out["bots_checked"], 1)

    def test_unreadable_bot_is_not_counted_as_current(self):
        """Regression: `checked - stale - without` silently counted errored rows as current.

        An unreadable Bot then appeared in no list at all, and `current` overstated how
        many were up to date.
        """
        import tools
        import json as _json
        from unittest import mock
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_text("- ask first\n")
            (root / "profiles" / "locked").mkdir(parents=True)
            (root / "profiles" / "locked" / "SOUL.md").write_text(
                policy.inject("- ask first", "# Locked"))
            (root / "profiles" / "bad").mkdir(parents=True)
            (root / "profiles" / "bad" / "SOUL.md").write_text("# Bad")
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            real_read = Path.read_text

            def read_only_profiles(self_path, *a, **kw):
                # fail only for the Bot's SOUL.md, not the canonical policy
                if self_path.name == "SOUL.md":
                    raise OSError("locked")
                return real_read(self_path, *a, **kw)

            try:
                with mock.patch.object(Path, "read_text", read_only_profiles):
                    out = _json.loads(tools.check_policies({}))
            finally:
                tools.hermes_root = orig
        self.assertEqual(out["bots_checked"], 2)
        self.assertEqual(out["current"], 0, "an unreadable Bot is not current")
        self.assertIn("bad", out["unreadable"])
        # and it must be visible in some list, not vanish
        self.assertTrue(out["unreadable"])

    def test_default_profile_is_audited(self):
        """The default profile's SOUL.md is at the root, not under profiles/."""
        import tools
        import json as _json
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_text("- ask first\n")
            (root / "profiles").mkdir()
            (root / "SOUL.md").write_text(policy.inject("- older rule", "# Default"))
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            try:
                out = _json.loads(tools.check_policies({}))
            finally:
                tools.hermes_root = orig
        self.assertIn("default", out["stale"])

    def test_comments_only_canonical_policy_is_an_error(self):
        """A policy of only comments would build Bots with no rules at all."""
        import tools
        import json as _json
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_text("<!-- TODO -->\n")
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            try:
                out = _json.loads(tools.check_policies({}))
            finally:
                tools.hermes_root = orig
        self.assertFalse(out["ok"])
        self.assertIn("no rules", out["error"])


class SharedPolicyCheckToolTests(unittest.TestCase):
    def test_check_policies_reports_missing_canonical_file(self):
        import tools
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "profiles" / "sable").mkdir(parents=True)
            (root / "profiles" / "sable" / "SOUL.md").write_text("# Sable")
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            try:
                out = tools.check_policies({})
            finally:
                tools.hermes_root = orig
        self.assertIn("no shared policy at", out)

    def test_check_policies_finds_drift(self):
        import tools
        import json as _json
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            canon = root / "shared" / "BOT-POLICY.md"
            canon.parent.mkdir(parents=True)
            canon.write_text("- ask first\n")
            # one current, one stale, one with no policy
            (root / "profiles" / "fresh").mkdir(parents=True)
            (root / "profiles" / "fresh" / "SOUL.md").write_text(
                policy.inject("- ask first", "# Fresh"))
            (root / "profiles" / "old").mkdir(parents=True)
            (root / "profiles" / "old" / "SOUL.md").write_text(
                policy.inject("- something older", "# Old"))
            (root / "profiles" / "bare").mkdir(parents=True)
            (root / "profiles" / "bare" / "SOUL.md").write_text("# Bare")
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            try:
                out = _json.loads(tools.check_policies({}))
            finally:
                tools.hermes_root = orig
        self.assertTrue(out["ok"])
        self.assertEqual(out["bots_checked"], 3)
        self.assertIn("old", out["stale"])
        self.assertIn("bare", out["no_shared_policy"])
        self.assertNotIn("fresh", out["stale"])
        self.assertNotIn("fresh", out["no_shared_policy"])


if __name__ == "__main__":
    unittest.main()
