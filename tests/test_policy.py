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

    def test_nested_and_flat_bullets_are_different_policies(self):
        """Regression: the F7 fix flattened indentation, so structure stopped mattering.

        Normalising `  - sub` to `- sub` made a nested rule hash identically to a flat one.
        Five of six distinct-policy pairs collided. That is the worst possible failure for a
        drift detector: a real change to the policy's structure reporting as no drift, and
        doing it silently. It was introduced while fixing a purely cosmetic false positive.

        A test that only checked "uniform indent is cosmetic" would have passed here, because
        a list indented under its heading and a list with a sub-bullet were being conflated.
        Both properties have to hold at once.
        """
        flat = "- Ask before spending.\n- Or over $100.\n- Ask before publishing.\n"
        nested = "- Ask before spending.\n  - Or over $100.\n- Ask before publishing.\n"
        self.assertNotEqual(policy.fingerprint(flat), policy.fingerprint(nested),
                            "a sub-bullet must not hash as a sibling rule")

        # structural edits, each of which must be visible
        for label, before, after in (
            ("sub-bullet added", "- A.\n- B.", "- A.\n  - sub\n- B."),
            ("sub-bullet removed", "- A.\n  - sub\n- B.", "- A.\n- B."),
            ("reparented", "- A.\n  - sub", "- A.\n- B.\n  - sub"),
            ("depth changed", "- R.\n    - a\n        - b", "- R.\n    - a\n      - b"),
        ):
            with self.subTest(edit=label):
                self.assertNotEqual(policy.fingerprint(before), policy.fingerprint(after),
                                    f"{label} must read as drift")

        # and the cosmetic cases the original fix was for must still hold
        self.assertEqual(policy.fingerprint("## R\n\n- a\n- b"),
                         policy.fingerprint("## R\n\n  - a\n  - b"),
                         "a list indented under its heading is the same list")
        self.assertEqual(policy.fingerprint("## R\n\n- a\n- b"),
                         policy.fingerprint("## R\n\n\t- a\n\t- b"),
                         "tabs and spaces are the same indent")
        self.assertEqual(policy.fingerprint("1. a\n2. b"),
                         policy.fingerprint("1) a\n2) b"),
                         "a numbered list's marker style is cosmetic")
        self.assertNotEqual(policy.fingerprint("1. a\n2. b"),
                            policy.fingerprint("1. a\n2. c"),
                            "renumbering the text is a real change")
        self.assertNotEqual(policy.fingerprint("1. a\n  1. sub"),
                            policy.fingerprint("1. a\n1. sub"),
                            "a numbered sub-list must keep its depth, like a bulleted one")

    def test_health_does_not_flag_a_bot_for_its_policy_block(self):
        """Regression: health read the raw SOUL, so a policy hid the Bot's own name.

        `health` checked the first 400 characters for the Bot's title. The policy block is
        ~2000 characters and is injected above the persona, so the name fell outside the
        window -- flagging two identical healthy personas as broken, one only because it
        carried a policy. Asserted through `health` itself, not through the helper, so the
        guard cannot be bypassed by re-pointing the accessor.
        """
        import health
        import time
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "hr"
            pdir = root / "profiles" / "handwritten"
            pdir.mkdir(parents=True)
            persona = ("# Handwritten Bot\n\n## Your one job\n\nDo the thing.\n\n"
                       "## Ask first\n\n- ask before spending\n")
            with_p = policy.inject(policy.STARTER_POLICY, persona)
            self.assertNotIn("Handwritten Bot", with_p[:400],
                             "precondition: the name IS pushed past the window")

            (pdir / "config.yaml").write_text(
                "model:\n  default: x\ntools:\n  backend: ''\napprovals:\n  mode: ''\n")
            (pdir / "routines.json").write_text("[]\n")
            now = time.time()

            def name_flag(text: str) -> list:
                (pdir / "SOUL.md").write_text(text)
                flags = health.check_bot(pdir, {}, now).get("flags", [])
                return [f for f in flags if "own name" in f]

            without = name_flag(persona)
            with_policy = name_flag(with_p)

        self.assertEqual(without, [],
                         f"a healthy persona should not be flagged: {without}")
        self.assertEqual(without, with_policy,
                         "carrying a policy must not change the health verdict")

    def test_unreadable_row_carries_both_fingerprints(self):
        """Regression: error rows omitted keys `audit_soul` always returns.

        docs/shared-policy.md says each entry in `bots` carries both fingerprints; a caller
        reading `row["fingerprint"]` on an unreadable Bot got a KeyError, so the one row you
        most need to inspect was the one that crashed the inspector.
        """
        import tools
        import json as _json
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_text("- ask first\n")
            pdir = root / "profiles" / "noperm"
            pdir.mkdir(parents=True)
            soul = pdir / "SOUL.md"
            soul.write_text("# No\n")
            soul.chmod(0o000)
            orig = tools.hermes_root
            tools.hermes_root = lambda: root
            try:
                out = _json.loads(tools.check_policies({}))
            finally:
                tools.hermes_root = orig
                soul.chmod(0o644)
        rows = {b["profile"]: b for b in out["bots"]}
        self.assertIn("noperm", rows)
        row = rows["noperm"]
        for key in ("fingerprint", "canonical_fingerprint", "current",
                    "has_shared_policy", "reason"):
            self.assertIn(key, row, f"unreadable row is missing {key!r}")
        self.assertIsNone(row["fingerprint"])
        self.assertEqual(row["canonical_fingerprint"], out["canonical_fingerprint"])

    def test_default_profile_is_not_audited(self):
        """The default profile is not a forge Bot, so it is not audited.

        Its SOUL.md sits at the root rather than under profiles/, so an earlier version
        added it by hand -- and then reported it as `no_shared_policy` forever. Nothing can
        fix that row: `_require_bot` refuses the default profile, so `forge` never writes a
        block there and no documented call reaches it. A drift report whose rows cannot be
        acted on is worse than one that omits them, because you learn to distrust the whole
        report.
        """
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
        names = [b["profile"] for b in out["bots"]]
        self.assertNotIn("default", names)
        self.assertEqual(out["bots_checked"], 0, "no forge Bots exist in this root")

    def test_policy_path_rejects_non_string(self):
        """A template value reaches policy_path untyped; a list must not raise TypeError.

        `create_agent` merges template keys over the spec, so this is reachable from a
        downloaded `.botforge.json` -- the same entry point that made F3 a read primitive.
        """
        for bad in ([], {}, Path("/etc/passwd"), 7, True):
            with self.subTest(value=bad):
                with self.assertRaises(policy.PolicyPathError):
                    policy.policy_path(Path("/tmp/hr"), bad)

    def test_policy_path_rejects_home_relative_paths(self):
        """A `~` path must not become a literal `~` directory inside the root.

        `expanduser` used to run on the JOINED path, so `~/.hermes/shared/BOT-POLICY.md`
        became <root>/~/.hermes/shared/BOT-POLICY.md -- still lexically contained, so the
        containment check passed and create_agent would write the starter policy there,
        silently in the wrong place. Rejecting it outright is stricter and simpler than
        trying to expand it usefully: `shared_policy_path` is by definition root-relative.
        """
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "hr"
            root.mkdir()
            for bad in ("~/.hermes/shared/BOT-POLICY.md", "~/policy.md"):
                with self.subTest(path=bad):
                    with self.assertRaises(policy.PolicyPathError):
                        policy.policy_path(root, bad)
            # a plain relative path still resolves inside the root
            ok = policy.policy_path(root, "shared/BOT-POLICY.md")
            self.assertEqual(ok, root / "shared" / "BOT-POLICY.md")

    def test_refresh_refuses_a_bare_soul(self):
        """A Bot with no persona must not be handed a policy-only SOUL.md.

        13 of the 17 real profiles are shaped this way. Writing one would replace an empty
        file with house rules and no identity -- a Bot that is all policy and no Bot -- and
        report ok: True, with no backup, because the file did not exist to back up.
        """
        import json as _json
        import manage
        import yaml
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "hr"
            pdir = root / "profiles" / "bare"
            pdir.mkdir(parents=True)
            (pdir / "config.yaml").write_text(yaml.safe_dump({"name": "bare"}))
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_text("- ask before spending\n")
            with self.assertRaises(ValueError) as ctx:
                manage.op_update({"name": "bare", "refresh_shared_policy": True}, root, {})
            self.assertIn("no content of its own", str(ctx.exception))
            self.assertEqual((pdir / "SOUL.md").exists(), False,
                             "a policy-only SOUL.md was written")

    def test_refresh_then_replace_soul_keeps_the_policy(self):
        """`soul_md` replaces the whole file, so a policy injected moments earlier is gone.

        The report used to say `shared_policy (already current)` while the write silently
        dropped it -- claiming success for work it had undone.
        """
        import json as _json
        import manage
        import yaml
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "hr"
            pdir = root / "profiles" / "sol"
            pdir.mkdir(parents=True)
            (pdir / "config.yaml").write_text(yaml.safe_dump({"name": "sol"}))
            (pdir / "SOUL.md").write_text("# Sol\n\nYou are **Sol**.\n")
            canon = policy.policy_path(root)
            canon.parent.mkdir(parents=True)
            canon.write_text("- ask before spending\n")
            out = manage.op_update({
                "name": "sol", "refresh_shared_policy": True,
                "soul_md": "# Sol\n\nYou are **Sol**.\n\nNew body.",
            }, root, {})
            self.assertTrue(out["ok"], out)
            text = (pdir / "SOUL.md").read_text()
            self.assertIn(policy.BEGIN, text, "the policy was dropped by the soul_md write")
            self.assertIn("New body.", text)
            self.assertNotIn("shared_policy (already current)", out.get("changed", []))

    def test_health_reads_the_name_past_a_policy_block(self):
        """A fourth 'persona starts at the top' reader, missed by the first review.

        `health` checked the first 400 characters of the raw SOUL.md, so the Bot's own name
        fell outside the window for any Bot carrying a policy block -- flagging two
        identical healthy personas as broken, one only because it had a policy.
        """
        import forge
        persona = "# Handwritten\\n\\n## Your one job\\n\\nDo the thing.\\n"
        block = policy.inject(policy.STARTER_POLICY, persona)
        self.assertEqual(forge.soul_role(persona), forge.soul_role(block),
                         "the role must survive a policy block")
        self.assertIn("Handwritten", forge.persona_text(block)[:400],
                      "the name must be findable in the persona, not the raw file")
        self.assertNotIn("Handwritten", block[:400],
                         "this is exactly the bug: the name was past the window")

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
