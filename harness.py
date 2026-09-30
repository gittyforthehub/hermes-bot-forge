"""Expert harness curation — give a Bot a *verified* skill set for a domain, not an inherited one.

bot-forge's stock behaviour is `disabled_skills()`: a denylist computed from the profile it
cloned. That answers "which skills does this Bot NOT need?" and never "which skills make it
good at the job?". This module inverts that:

  1. resolve a domain to a curated manifest (`harness_manifest`) — or accept one inline,
  2. install anything from the skill registries that the Bot is missing (`hermes skills install`),
  3. verify every skill in the manifest actually resolves inside the new profile,
  4. reduce the profile's enabled skills to exactly the manifest allowlist.

Step 4 is the part that makes a harness *expert* rather than merely large: an expert does not
carry fifty half-relevant skills, it carries the right ten.

The manifest format is deliberately small and portable, so a community manifest is just a
JSON file in `harnesses/`:

    {
      "domain": "ios",
      "label": "Native iOS engineering",
      "summary": "...",
      "skill_categories": ["software-development"],   # local categories kept wholesale
      "skills": ["ios-app-delivery", "test-driven-development"],   # individual skills, always kept
      "toolsets": ["terminal", "vision", "code_execution"],
      "registry_skills": [                           # optional: pulled from skills.sh / clawhub
        {"query": "swiftui", "identifier": "skills-sh/dpearson2699/swift-ios-skills/...",
         "category": "software-development", "optional": true}
      ],
      "defaults": {"sandbox": "local", "approvals": [...]}
    }

Everything is best-effort and reported: a manifest that cannot be half-installed still yields a
usable Bot plus a `harness.gaps` list the caller can act on, rather than a hard failure.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
HARNESS_DIR = PLUGIN_DIR / "harnesses"

# Never disabled: these are the plumbing every Bot needs to load skills, read files and use the web.
ALWAYS_KEEP = {"autonomous-ai-agents", "research", "web"}

SCHEMA_VERSION = 1


# ── manifest loading ─────────────────────────────────────────────────────────
def manifest_path(domain: str) -> Path:
    """Path for a domain manifest. The key is normalized (case, spaces, punctuation), so
    'iOS', 'IOS' and 'ios' all resolve to the same file. Normalization does NOT create
    aliases: 'iOS App Delivery' normalizes to 'ios-app-delivery', which is a different
    (skill) name and will simply not resolve.
    """
    key = re.sub(r"[^a-z0-9]+", "-", str(domain).lower()).strip("-")
    return HARNESS_DIR / f"{key}.json"


def load_manifest(domain: str, root: Path | None = None) -> dict | None:
    """Load a curated manifest by domain key. Returns None when the domain is unknown."""
    path = manifest_path(domain)
    if not path.exists() and root:
        path = Path(root) / "harnesses" / path.name
    if not path.exists():
        return None
    # The contributor template is a starting point, not a domain. Enumerating it is
    # excluded in available_domains(), but a direct load would still succeed on a
    # case-insensitive filesystem, so the guard has to be here too.
    if path.stem.lower() == "template":
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("skills"):
        return None
    data.setdefault("domain", domain)
    return data


def available_domains() -> list[str]:
    """Every domain key with a bundled manifest, sorted.

    `TEMPLATE.json` is a copyable starting point for contributors, not a domain — it is
    excluded here so it can never be resolved as one.

    Keys are normalised to lower case because `manifest_path` lower-cases too; without
    that, an upper-case filename resolves on a case-insensitive filesystem (macOS) and
    silently fails on Linux. CI is the only place that difference shows up.
    """
    if not HARNESS_DIR.is_dir():
        return []
    return sorted({
        p.stem.lower() for p in HARNESS_DIR.glob("*.json")
        if p.stem.lower() != "template"
    })


# ── skill inventory ──────────────────────────────────────────────────────────
def skill_name(skill_md: Path) -> str:
    """The `name:` from a SKILL.md frontmatter, falling back to the directory name."""
    try:
        m = re.search(r"^name:\s*['\"]?([^'\"\n]+)", skill_md.read_text(errors="ignore"), re.M)
    except OSError:
        m = None
    return (m.group(1).strip() if m else skill_md.parent.name)


def inventory(skills_dir: Path) -> dict[str, Path]:
    """Map every skill name under `skills_dir` to its SKILL.md path."""
    if not skills_dir.is_dir():
        return {}
    out: dict[str, Path] = {}
    for skill_md in skills_dir.rglob("SKILL.md"):
        out.setdefault(skill_name(skill_md), skill_md)
    return out


def external_dirs(cfg: dict) -> list[Path]:
    """Skill directories a profile loads from *outside* its own skills/ folder.

    A profile config can point at shared or OMH skill roots via `skills.external_dirs`.
    Those skills are genuinely available to the Bot, so the curator must see them — otherwise
    every shared skill is misreported as "missing" and re-installed for no reason.
    """
    skills_cfg = cfg.get("skills") if isinstance(cfg.get("skills"), dict) else {}
    dirs = skills_cfg.get("external_dirs") or []
    if isinstance(dirs, str):
        dirs = [dirs]
    return [Path(str(d)).expanduser() for d in dirs if d]


def _read_cfg(profile_dir: Path, cfg: dict | None = None) -> dict:
    """The profile config, from the caller, the file on disk, or an empty dict."""
    if cfg is not None:
        return cfg
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        import yaml  # local import: only needed when reading a real config
        loaded = yaml.safe_load(cfg_path.read_text())
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def inventory_with_externals(profile_dir: Path, cfg: dict | None = None) -> dict[str, Path]:
    """Full inventory for a profile: its own skills/ plus any configured external_dirs.

    Own skills win on a name collision — they are the profile-local override.
    """
    out: dict[str, Path] = {}
    cfg = _read_cfg(profile_dir, cfg)
    for d in external_dirs(cfg):
        out.update(inventory(d))   # externals first, so local ones override below
    out.update(inventory(profile_dir / "skills"))
    return out


def _category_of(skill_md: Path, roots: list[Path]) -> str:
    """The category a skill sits in, resolved against whichever root contains it.

    A skill's category is its first path segment below a *skill root* — a profile's own
    `skills/`, or any `external_dirs` root. Resolving only against the profile-local dir
    made every external skill look like it had no category, so an `ALWAYS_KEEP` member
    living in a shared root was disabled while the same skill in the profile's own
    `research/` folder was kept. The two sides have to be measured the same way.
    """
    for root in roots:
        try:
            rel = skill_md.relative_to(root).parts
        except ValueError:
            continue
        if len(rel) > 1:
            return rel[0]
    return ""


# Hermes refuses to disable these whatever a config says: the system prompt points at
# `hermes-agent` unconditionally, so listing it in `skills.disabled` is a silent no-op.
# Curating against it would report a disable that never takes effect.
NEVER_DISABLE = frozenset({"hermes-agent"})


# ── curation ─────────────────────────────────────────────────────────────────
def _name_list(value) -> list[str]:
    """A manifest's name list, tolerating whatever a contributor actually wrote.

    Manifests are community-authored JSON, so the schema has to survive bad input without
    crashing the build. A bare string is read as a one-item list (not iterated into
    characters), and a number or null yields nothing rather than a TypeError.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def plan(profile_dir: Path, manifest: dict, cfg: dict | None = None,
         extra_keep: set[str] | None = None) -> dict:
    """Compute the enable/disable sets for `manifest` against a real profile directory.

    Pure: touches no files, so it is safe to call from tests and from a dry run.
    `cfg` may be passed to supply the profile config (for `skills.external_dirs`); when omitted
    the config is read from disk. A malformed manifest produces a thin result, never an
    exception: a contributor's typo must not take down an unrelated `create_agent` call.
    `extra_keep` names skills the caller has just installed and knows belong to this Bot --
    currently the `registry_skills` a manifest pulls. Without it those skills are installed
    and then immediately disabled by the very curation step meant to enable them, and the
    Bot reports them as neither present nor missing.
    """
    skills_dir = profile_dir / "skills"
    inv = inventory(skills_dir)                       # profile-local: the only ones we may disable
    all_inv = inventory_with_externals(profile_dir, cfg)  # everything actually loadable
    categories = set(_name_list(manifest.get("skill_categories")))
    required = set(_name_list(manifest.get("skills")))
    categories |= ALWAYS_KEEP

    keep_names: set[str] = set()
    # Every skill root the profile loads from, so a shared skill's category resolves the
    # same way a profile-local one does.
    roots = [skills_dir, *external_dirs(_read_cfg(profile_dir, cfg))]
    for name, skill_md in all_inv.items():
        if _category_of(skill_md, roots) in categories:
            keep_names.add(name)

    resolved, missing = set(), []
    for name in sorted(required):
        if name in all_inv:
            resolved.add(name)
        else:
            missing.append(name)

    # Curate across every skill the profile can load, not just its own. `skills.disabled`
    # is matched by name against all skill directories, so a shared skill left in the list
    # is exactly the contamination this is meant to remove. (An earlier version treated
    # external skills as untouchable, which left ~138 irrelevant skills loaded per Bot.)
    # NEVER_DISABLE is excluded because Hermes ignores a disable for those names anyway.
    # `extra_keep` is the registry skills just installed: they belong to the manifest even
    # though they never appeared in `manifest["skills"]`, so without this line the curation
    # would install them and disable them in the same pass.
    keep = keep_names | resolved | {n for n in (extra_keep or set()) if n in all_inv}
    disabled = {n for n in all_inv if n not in keep and n not in NEVER_DISABLE}

    return {
        "keep": sorted(keep),
        "enable": sorted(resolved),
        "disabled": sorted(disabled),
        "missing": missing,
        "installed_count": len(all_inv),
        "local_count": len(inv),
    }


def apply_plan(cfg: dict, result: dict, taught: set[str] | None = None) -> dict:
    """Fold a `plan()` result into a profile config dict (mutates and returns it)."""
    taught = taught or set()
    disabled = {s for s in result["disabled"] if s not in taught}
    if disabled:
        skills_cfg = cfg.get("skills") if isinstance(cfg.get("skills"), dict) else {}
        # Merge, don't clobber: a profile may already carry a curated disabled list.
        skills_cfg["disabled"] = sorted(set(skills_cfg.get("disabled") or []) | disabled)
        cfg["skills"] = skills_cfg
    return cfg


# ── registry pulls ───────────────────────────────────────────────────────────
def resolve_registry_skill(registry_mod, spec: dict) -> dict:
    """Resolve one `registry_skills` entry against a registry module.

    Accepts the module as an argument rather than importing it, so this stays pure and testable.
    Never raises: a network failure is a reported gap, not a crash.
    """
    entry = {
        "identifier": spec.get("identifier"),
        "query": spec.get("query"),
        "name": spec.get("name"),
        "category": spec.get("category"),
        "optional": bool(spec.get("optional", True)),
        "status": "pending",
    }
    if not entry["identifier"] and not entry["query"]:
        entry["status"] = "invalid: needs 'identifier' or 'query'"
        return entry
    # An explicit identifier is the author naming the exact artifact. Honour it verbatim.
    # Searching instead and taking the top hit is how a manifest asking for `combine` got
    # `chatgpt-apps`, and how three MIT-identified entries silently resolved to a
    # PolyForm-licensed repo the manifest had deliberately rejected: the query won, and the
    # identifier was only a starting hint. Search is for entries that have no identifier.
    if entry["identifier"]:
        entry["status"] = "resolved"
        return entry
    try:
        found = registry_mod.search(entry["query"], limit=1)
        if not found:
            entry["status"] = "not found in registry"
            return entry
        hit = found[0]
        entry["identifier"] = hit.get("identifier") or entry["identifier"]
        entry["name"] = hit.get("name") or entry["name"]
        entry["source"] = hit.get("source")
        entry["status"] = "resolved"
    except Exception as exc:  # network, CLI missing, registry down
        entry["status"] = f"lookup failed: {type(exc).__name__}: {exc}"[:160]
    return entry


def resolve_registry(registry_mod, manifest: dict) -> list[dict]:
    """Resolve every `registry_skills` entry in a manifest. Never raises."""
    entries = (manifest.get("registry_skills") or []) if isinstance(manifest, dict) else []
    out = []
    for spec in entries:
        if not isinstance(spec, dict):
            continue
        try:
            out.append(resolve_registry_skill(registry_mod, spec))
        except Exception as exc:
            out.append({"query": spec.get("query") if isinstance(spec, dict) else None,
                        "status": f"error: {exc}"[:160], "optional": True})
    return out


# ── reporting ────────────────────────────────────────────────────────────────
def report(manifest: dict, result: dict, registry_entries: list[dict] | None = None) -> dict:
    """The `harness` block returned to the caller: what was kept, installed, and still missing."""
    optional = [e for e in (registry_entries or []) if e.get("optional")]
    # A required entry counts as missing unless it BOTH resolved and, if it was handed to the
    # installer, actually installed. Checking `status` alone let a required skill that 404'd at
    # install time report "resolved" and produce no gap at all -- the Bot shipped without it and
    # the report said everything was fine. `install` is absent when no install was attempted
    # (the profile is being audited rather than built), which is not a failure.
    required_missing = [
        e for e in (registry_entries or [])
        if not e.get("optional")
        and (e.get("status") != "resolved" or e.get("install") == "failed")
    ]
    return {
        "domain": manifest.get("domain"),
        "label": manifest.get("label"),
        "summary": manifest.get("summary"),
        "manifest_version": manifest.get("version", SCHEMA_VERSION),
        "kept_skills": result["keep"],
        "enabled_skills": result["enable"],
        "disabled_count": len(result["disabled"]),
        "missing_skills": result["missing"],
        "registry": registry_entries or [],
        "gaps": (
            [f"skill not installed and not found: {s}" for s in result["missing"]]
            + [f"registry skill {e.get('status') if e.get('install') == 'failed' else 'unresolved'}: "
               f"{e.get('identifier') or e.get('query')}"
               f"{' (install failed)' if e.get('install') == 'failed' else ''}"
               for e in required_missing]
        ),
        "optional_unresolved": [e.get("identifier") or e.get("query") for e in optional
                                if e.get("status") != "resolved"],
    }


# ── shared entry points (create_agent and update_agent both use these) ───────
def resolve_request(domain, inline_manifest, root: Path | None = None) -> tuple[dict | None, str | None]:
    """Turn a caller's `harness` / `harness_manifest` into a manifest, or an error.

    Returns (manifest, None), (None, error), or (None, None) when nothing was asked for.
    An explicit request that cannot be satisfied is always an error: the caller asked for an
    expert and must never receive an uncurated generalist without being told.
    """
    if isinstance(inline_manifest, str):
        try:
            inline_manifest = json.loads(inline_manifest)
        except json.JSONDecodeError as exc:
            return None, f"harness_manifest is not valid JSON: {exc}"[:200]
    if inline_manifest is None:
        inline_manifest = {}
    if not isinstance(inline_manifest, dict):
        return None, "harness_manifest must be a JSON object"
    domain = domain or inline_manifest.get("domain")
    if not domain and not inline_manifest:
        return None, None
    manifest = inline_manifest or load_manifest(str(domain or ""), root)
    if not manifest:
        if inline_manifest:
            return None, ("harness_manifest has no 'skills' key — an expert must name at least one "
                          "skill; use a domain manifest from harnesses/ for a ready-made set")
        return None, f"no harness manifest for '{domain}' (available: {', '.join(available_domains())})"
    if not _name_list(manifest.get("skills")) and not _name_list(manifest.get("skill_categories")):
        return None, ("harness_manifest names no skills and no skill_categories — an expert must "
                      "specify at least one skill")
    manifest = dict(manifest)
    manifest.setdefault("domain", domain)
    return manifest, None


def manifest_approvals(manifest: dict) -> list[str]:
    """Approvals may sit under `defaults` or at the top level; the README documented both."""
    defaults = manifest.get("defaults") or {}
    return [a for a in (defaults.get("approvals") or manifest.get("approvals") or []) if isinstance(a, str)]


def curate(pdir: Path, manifest: dict, cfg: dict, registry_mod, root: Path,
           install: bool = True, taught: set[str] | None = None) -> tuple[dict, dict]:
    """Install a manifest's registry skills, then reduce the profile to its allowlist.

    Returns (new_cfg, report). The caller writes the config. `registry_mod.install` writes
    skills to disk, so this is not pure — but it never writes config itself.
    """
    import forge  # local: forge imports this module

    cfg_path = Path(pdir) / "config.yaml"
    entries = resolve_registry(registry_mod, manifest) if install else []
    # Skills a manifest pulls from a registry belong to the curated set even though they are
    # not in `manifest["skills"]`. Without this keep-set they install and are disabled in the
    # same pass.
    registry_kept: set[str] = set()
    for entry in entries:
        if entry.get("status") == "resolved" and entry.get("identifier"):
            outcome = registry_mod.install(entry["identifier"], entry.get("category"),
                                           entry.get("name"), root=root)
            entry["install"] = outcome.get("status")
            # An identifier-only entry has no `name`; the installed directory is the last segment.
            installed_name = entry.get("name") or entry["identifier"].rstrip("/").rsplit("/", 1)[-1]
            if outcome.get("status") == "installed" and installed_name:
                registry_kept.add(installed_name)
    if entries and cfg_path.exists():
        cfg = forge.load_yaml(cfg_path)  # an install may have touched the profile config
    result = plan(pdir, manifest, cfg, extra_keep=registry_kept)
    if result["missing"] and install:
        # A missing local name needs a path-shaped registry identifier to install; look it up.
        for name in list(result["missing"]):
            hit = resolve_registry_skill(registry_mod, {"query": name, "optional": False})
            if hit.get("status") == "resolved" and hit.get("identifier"):
                outcome = registry_mod.install(hit["identifier"], hit.get("category"),
                                               hit.get("name"), root=root)
                if outcome.get("status") == "installed":
                    if cfg_path.exists():
                        cfg = forge.load_yaml(cfg_path)
                    # extra_keep must survive the re-plan, or the registry skills just
                    # installed are disabled by the next apply_plan.
                    result = plan(pdir, manifest, cfg, extra_keep=registry_kept)
    cfg = apply_plan(cfg, result, taught or set())
    return cfg, report(manifest, result, entries)
