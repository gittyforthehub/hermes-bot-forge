"""Shared operating policy: injection is correct, idempotent, and drift is visible."""
import sys
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
