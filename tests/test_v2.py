"""bot-forge-v2: in-place harness curation, doctor CLI import, survey roster freshness, skill rename.

Run: python -m unittest discover -s tests
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import forge  # noqa: E402
import manage  # noqa: E402
import survey  # noqa: E402
import yaml  # noqa: E402


def make_root(tmp: Path) -> Path:
    root = tmp / ".hermes"
    (root / "profiles").mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "root-model", "provider": "p"}}))
    return root


def make_bot(root: Path, name: str, title: str, one_job: str, skills=()) -> Path:
    d = root / "profiles" / name
    (d / "memories").mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(yaml.safe_dump({"platform_toolsets": {"cli": ["web", "file"]},
                                                   "skills": {"external_dirs": []}}))
    (d / "profile.yaml").write_text(yaml.safe_dump({"ui_meta": {"hermes-bots": {"title": title}}}))
    (d / "SOUL.md").write_text(f"# {title}\n\nYou are **{title}**, a specialist.\n\n## Your one job\n{one_job}\n")
    for cat, skill in skills:
        sd = d / "skills" / cat / skill if cat else d / "skills" / skill
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: d\n---\n")
    return d


class DoctorCliImport(unittest.TestCase):
    """`hermes bot-forge-doctor` crashed with ModuleNotFoundError: the handler did a bare
    `import doctor`, which only works when the plugin dir happens to be on sys.path. Hermes
    loads the plugin as a package, so the CLI path must not depend on that."""

    def test_doctor_handler_imports_without_plugin_dir_on_path(self):
        probe = f"""
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location(
    "bf_pkg", {str(ROOT / '__init__.py')!r}, submodule_search_locations=[{str(ROOT)!r}])
mod = importlib.util.module_from_spec(spec); sys.modules["bf_pkg"] = mod
sys.path = [p for p in sys.path if p not in ({str(ROOT)!r}, "")]
spec.loader.exec_module(mod)
captured = {{}}
class Ctx:
    def get_config(self, k, default=None): return default
    def register_tool(self, **kw): pass
    def register_hook(self, *a, **kw): pass
    def register_skill(self, *a, **kw): pass
    def register_cli_command(self, **kw): captured.update(kw)
mod.register(Ctx())
handler = captured["handler_fn"]
import doctor as _never  # must fail here, proving the plugin dir is NOT on sys.path
"""
        # Precondition: prove a bare import really is unavailable in the probe.
        pre = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd="/")
        self.assertIn("No module named 'doctor'", pre.stderr, "precondition: plugin dir must be off sys.path")
        run = probe.replace("import doctor as _never  # must fail here, proving the plugin dir is NOT on sys.path",
                            "print('RC', handler(types.SimpleNamespace(json=True)))")
        out = subprocess.run([sys.executable, "-c", run], capture_output=True, text=True, cwd="/", timeout=120)
        self.assertNotIn("ModuleNotFoundError", out.stderr, out.stderr[-800:])
        self.assertIn("RC", out.stdout, out.stderr[-800:])


class TempRootGuard(unittest.TestCase):
    """The real `hermes` CLI, run against a throwaway HERMES_HOME, republished the user's
    install launchers bound to that root's runtime store. When the temp dir was deleted the
    user's `hermes` command pointed at a Python that no longer existed. Tests and gates must
    never reach the real CLI with a temp root."""

    def test_forge_run_refuses_a_temp_root(self):
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(forge.subprocess, "run", side_effect=AssertionError("hermes was run")):
            with self.assertRaises(RuntimeError) as ctx:
                forge.run(Path(t) / "hr", "profile", "create", "x")
            self.assertIn("temp Hermes root", str(ctx.exception))

    def test_registry_refuses_a_temp_root(self):
        import registry
        with tempfile.TemporaryDirectory() as t, \
                mock.patch.object(registry.shutil, "which", return_value="/bin/hermes"), \
                mock.patch.object(registry.subprocess, "run", side_effect=AssertionError("hermes was run")):
            with self.assertRaises(registry.RegistryError):
                registry._run(["skills", "list"], Path(t))

    def test_real_root_is_not_a_temp_root(self):
        self.assertFalse(forge._is_temp_root(Path.home() / ".hermes"))


class SurveyRosterFreshness(unittest.TestCase):
    """The workspace index is cached for 6h, and it carried the Bot roster with it. Delete a
    Bot and recreate one for the same job, and the duplicate guard refused it against a Bot
    that no longer existed, for up to six hours, without naming the cache."""

    def test_deleted_bot_disappears_from_a_cached_index(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = make_root(tmp)
            (tmp / "ws").mkdir()
            settings = {"workspace_roots": [str(tmp / "ws")]}
            make_bot(root, "sable", "Sable", "write swift and swiftui ios apps with xcode")
            first = survey.workspace_index(root, settings, refresh=True)
            self.assertIn("sable", [b["bot"] for b in first["hermes"]["bots"]], "precondition")
            import shutil
            shutil.rmtree(root / "profiles" / "sable")
            again = survey.workspace_index(root, settings, now=time.time() + 60)
            self.assertNotIn("sable", [b["bot"] for b in again["hermes"]["bots"]],
                             "a deleted Bot must not survive in the cached roster")

    def test_places_are_still_served_from_cache(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            root = make_root(tmp)
            (tmp / "ws").mkdir()
            settings = {"workspace_roots": [str(tmp / "ws")]}
            survey.workspace_index(root, settings, refresh=True)
            with mock.patch.object(survey, "scan_places", side_effect=AssertionError("re-scanned places")):
                survey.workspace_index(root, settings, now=time.time() + 60)


LEGAL_MANIFEST = {
    "version": 1, "domain": "legal-test", "label": "Legal", "summary": "s",
    "skills": ["contract-review", "nda-review"],
    "approvals": ["sending anything to a counterparty"],
}


class UpdateInPlaceHarness(unittest.TestCase):
    """update_agent must be able to apply an expert harness to an existing Bot — the same
    curation create_agent does — without recreating it."""

    def _legal(self, root):
        return make_bot(root, "legal", "Legal", "review contracts",
                        skills=[("", "contract-review"), ("commercial-legal", "nda-review"),
                                ("creative", "p5js"), ("media", "gif-search")])

    def _update(self, root, **kw):
        return manage.manage({"op": "update", "hermes_root": str(root), "name": "legal",
                              "settings": {"harness_install": False}, **kw})

    def test_inline_manifest_curates_existing_bot(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(Path(t))
            pdir = self._legal(root)
            out = self._update(root, harness_manifest=LEGAL_MANIFEST)
            self.assertTrue(out["ok"], out)
            self.assertIn("harness", out["changed"])
            disabled = yaml.safe_load((pdir / "config.yaml").read_text())["skills"]["disabled"]
            self.assertIn("p5js", disabled)
            self.assertIn("gif-search", disabled)
            self.assertNotIn("contract-review", disabled)
            self.assertNotIn("nda-review", disabled)
            self.assertEqual(out["harness"]["domain"], "legal-test")
            self.assertEqual(out["harness"]["gaps"], [])
            self.assertTrue(out["backups"].get("config.yaml"))

    def test_harness_approvals_are_added_to_soul_once(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(Path(t))
            pdir = self._legal(root)
            self._update(root, harness_manifest=LEGAL_MANIFEST)
            self._update(root, harness_manifest=LEGAL_MANIFEST)
            soul = (pdir / "SOUL.md").read_text()
            self.assertEqual(soul.count("## Ask first"), 1)
            self.assertIn("sending anything to a counterparty", soul)

    def test_missing_skill_is_reported_as_a_gap(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(Path(t))
            self._legal(root)
            m = dict(LEGAL_MANIFEST, skills=["contract-review", "not-installed-anywhere"])
            out = self._update(root, harness_manifest=m)
            self.assertTrue(out["ok"], out)
            self.assertIn("not-installed-anywhere", json.dumps(out["harness"]["gaps"]))

    def test_unknown_domain_is_refused_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(Path(t))
            self._legal(root)
            out = self._update(root, harness="no-such-domain")
            self.assertFalse(out["ok"])
            self.assertIn("no-such-domain", out["error"])

    def test_manifest_without_skills_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            root = make_root(Path(t))
            self._legal(root)
            out = self._update(root, harness_manifest={"domain": "x"})
            self.assertFalse(out["ok"])

    def test_update_schema_exposes_harness(self):
        import schemas
        props = schemas.UPDATE_AGENT["parameters"]["properties"]
        for key in ("harness", "harness_manifest", "approvals"):
            self.assertIn(key, props)


class BundledManifests(unittest.TestCase):
    def test_legal_and_tax_domains_ship_and_validate(self):
        import harness
        self.assertIn("legal", harness.available_domains())
        self.assertIn("tax", harness.available_domains())
        for d in ("legal", "tax"):
            out = subprocess.run([sys.executable, str(ROOT / "bench" / "validate_manifest.py"),
                                  str(ROOT / "harnesses" / f"{d}.json")], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class SkillRename(unittest.TestCase):
    def test_bundled_skill_is_bot_forge_v2(self):
        self.assertTrue((ROOT / "skills" / "bot-forge-v2" / "SKILL.md").is_file())
        self.assertFalse((ROOT / "skills" / "bot-forge").exists())
        text = (ROOT / "skills" / "bot-forge-v2" / "SKILL.md").read_text()
        self.assertIn("name: bot-forge-v2", text)

    def test_no_stale_skill_references(self):
        for rel in ("schemas.py", "README.md"):
            self.assertNotIn("bot-forge:bot-forge'", (ROOT / rel).read_text())
            self.assertNotIn("bot-forge:bot-forge`", (ROOT / rel).read_text())


if __name__ == "__main__":
    unittest.main()


class InheritMainProfileDefaults(unittest.TestCase):
    """A new Bot should start from the main profile's defaults without hand-editing:
    its model, and the plugins the main profile has enabled — including the provider plugin
    that model needs. Previously the model came from whichever profile *asked* for the Bot,
    and root-installed plugins were listed in config but never linked into the Bot, so a Bot
    on a plugin provider silently fell back to a free model."""

    def _root(self, t):
        root = make_root(Path(t))
        (root / "config.yaml").write_text(yaml.safe_dump({
            "model": {"default": "main-model", "provider": "main-provider", "base_url": "process://x"},
            "plugins": {"enabled": ["bot-forge", "provider-plugin", "omh", "not-installed"]}}))
        for p in ("bot-forge", "provider-plugin", "omh"):
            (root / "plugins" / p).mkdir(parents=True)
            (root / "plugins" / p / "plugin.yaml").write_text(f"name: {p}\n")
        caller = root / "profiles" / "legal"
        caller.mkdir(parents=True)
        (caller / "config.yaml").write_text(yaml.safe_dump({"model": {"default": "caller-model", "provider": "c"}}))
        return root

    def _forge(self, root, **settings):
        def fake_run(r, *a, **k):
            if a[:2] == ("profile", "create"):
                d = Path(r) / "profiles" / a[2]
                (d / "plugins").mkdir(parents=True, exist_ok=True)
                # `--clone-from default` copies config.yaml
                (d / "config.yaml").write_text((Path(r) / "config.yaml").read_text())
            return mock.Mock(returncode=0, stdout="", stderr="")

        spec = {"hermes_root": str(root), "role": "Researcher", "display_name": "Kestrel",
                "launch_profile": "legal",
                "settings": {"install_gateway": False, "workspace_survey": False, "journal_enabled": False,
                             "ack_tapback": False, "ack_reactions": False, **settings}}
        with mock.patch.object(forge, "run", side_effect=fake_run), \
                mock.patch.object(forge, "bot_chat", return_value=(True, "hi")), \
                mock.patch.object(forge, "existing_bot_names", return_value=set()):
            out = forge.forge(spec)
        self.assertTrue(out["ok"], out.get("error"))
        return root / "profiles" / out["name"]

    def test_model_comes_from_the_main_profile_not_the_caller(self):
        with tempfile.TemporaryDirectory() as t:
            pdir = self._forge(self._root(t))
            model = yaml.safe_load((pdir / "config.yaml").read_text())["model"]
            self.assertEqual(model["default"], "main-model")
            self.assertEqual(model["provider"], "main-provider")

    def test_caller_model_is_still_available_as_an_option(self):
        with tempfile.TemporaryDirectory() as t:
            pdir = self._forge(self._root(t), inherit_from="caller")
            self.assertEqual(yaml.safe_load((pdir / "config.yaml").read_text())["model"]["default"], "caller-model")

    def test_main_profile_plugins_are_linked_into_the_bot(self):
        with tempfile.TemporaryDirectory() as t:
            root = self._root(t)
            pdir = self._forge(root)
            for p in ("provider-plugin", "omh"):
                link = pdir / "plugins" / p
                self.assertTrue(link.is_symlink(), f"{p} not linked")
                self.assertEqual(link.resolve(), (root / "plugins" / p).resolve())

    def test_bot_forge_itself_is_never_linked_into_a_bot(self):
        # A Bot must not gain the power to create or delete Bots.
        with tempfile.TemporaryDirectory() as t:
            pdir = self._forge(self._root(t))
            self.assertFalse((pdir / "plugins" / "bot-forge").exists())

    def test_an_enabled_plugin_that_is_not_installed_is_left_alone(self):
        with tempfile.TemporaryDirectory() as t:
            pdir = self._forge(self._root(t))
            self.assertFalse((pdir / "plugins" / "not-installed").exists())
