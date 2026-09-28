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
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("skills"):
        return None
    data.setdefault("domain", domain)
    return data


def available_domains() -> list[str]:
    """Every domain key with a bundled manifest, sorted."""
    if not HARNESS_DIR.is_dir():
        return []
    return sorted(p.stem for p in HARNESS_DIR.glob("*.json"))


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


def inventory_with_externals(profile_dir: Path, cfg: dict | None = None) -> dict[str, Path]:
    """Full inventory for a profile: its own skills/ plus any configured external_dirs.

    Own skills win on a name collision — they are the profile-local override.
    """
    out: dict[str, Path] = {}
    if cfg is None:
        cfg_path = profile_dir / "config.yaml"
        cfg = {}
        if cfg_path.exists():
            try:
                import yaml  # local import: only needed when reading a real config
                loaded = yaml.safe_load(cfg_path.read_text())
                cfg = loaded if isinstance(loaded, dict) else {}
            except Exception:
                cfg = {}
    for d in external_dirs(cfg):
        out.update(inventory(d))   # externals first, so local ones override below
    out.update(inventory(profile_dir / "skills"))
    return out


def _category_of(skill_md: Path, skills_dir: Path) -> str:
    try:
        rel = skill_md.relative_to(skills_dir).parts
    except ValueError:
        return ""
    return rel[0] if len(rel) > 1 else ""


# ── curation ─────────────────────────────────────────────────────────────────
def plan(profile_dir: Path, manifest: dict, cfg: dict | None = None) -> dict:
    """Compute the enable/disable sets for `manifest` against a real profile directory.

    Pure: touches no files, so it is safe to call from tests and from a dry run.
    `cfg` may be passed to supply the profile config (for `skills.external_dirs`); when omitted
    the config is read from disk.
    """
    skills_dir = profile_dir / "skills"
    inv = inventory(skills_dir)                       # profile-local: the only ones we may disable
    all_inv = inventory_with_externals(profile_dir, cfg)  # everything actually loadable
    categories = {str(c) for c in (manifest.get("skill_categories") or [])}
    required = {str(s) for s in (manifest.get("skills") or [])}
    categories |= ALWAYS_KEEP

    keep_names: set[str] = set()
    for name, skill_md in all_inv.items():
        if _category_of(skill_md, skills_dir) in categories:
            keep_names.add(name)

    resolved, missing = set(), []
    for name in sorted(required):
        if name in all_inv:
            resolved.add(name)
        else:
            missing.append(name)

    # Only profile-local skills may be disabled. A shared/external skill is disabled elsewhere
    # (in whatever profile owns it) and is not this profile's to switch off.
    disabled = {n for n in inv if n not in keep_names | resolved}

    return {
        "keep": sorted(keep_names | resolved),
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
    try:
        found = registry_mod.search(entry["query"] or entry["identifier"], limit=1)
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
    required_missing = [e for e in (registry_entries or []) if not e.get("optional") and e.get("status") != "resolved"]
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
            + [f"registry skill unresolved: {e.get('identifier') or e.get('query')} ({e.get('status')})"
               for e in required_missing]
        ),
        "optional_unresolved": [e.get("identifier") or e.get("query") for e in optional
                                if e.get("status") != "resolved"],
    }
