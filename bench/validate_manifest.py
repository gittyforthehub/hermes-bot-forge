"""Validate a harness manifest before it becomes a Bot.

    python3 bench/validate_manifest.py harnesses/<domain>.json

Checks what a contributor can get wrong without any of it failing loudly at build time:
a manifest naming no skills, a `domain` that disagrees with its filename, an approval list
that is not a list of strings, a registry entry with neither `query` nor `identifier`.

It cannot check whether the manifest names the *right* skills for its domain. That is the
one judgment a contributor brings, and no script can make it. What this does is make sure
a typo is a message rather than a surprise.

Exit code 0 = usable, 1 = errors, 2 = file unreadable.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import harness  # noqa: E402


def validate(path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, notes) for one manifest file."""
    errors: list[str] = []
    notes: list[str] = []

    try:
        raw = path.read_text()
    except OSError as exc:
        return [f"cannot read: {exc}"], notes

    try:
        m = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"not valid JSON: line {exc.lineno} column {exc.colno}: {exc.msg}"], notes

    if not isinstance(m, dict):
        return ["top level must be a JSON object"], notes

    # The keys a contributor is most likely to get wrong, by a wide margin.
    for k in ("label", "summary"):
        if k in m and not isinstance(m[k], str):
            errors.append(f"'{k}' must be a string, got {type(m[k]).__name__}")

    domain = m.get("domain")
    if not isinstance(domain, str) or not domain.strip():
        errors.append("'domain' is required and must be a non-empty string")
    else:
        expected = path.stem
        if domain != expected:
            notes.append(f"filename is '{expected}.json' but domain is '{domain}' — "
                         f"pick one; `harness=\"{domain}\"` uses the domain field")

    skills = harness._name_list(m.get("skills"))
    cats = harness._name_list(m.get("skill_categories"))
    if not skills and not cats:
        errors.append("names no skills and no skill_categories — an expert must specify at "
                      "least one skill, or the Bot is a generalist wearing a specialist's name")
    elif not skills:
        notes.append("names categories but no explicit skills; categories are broad and are "
                     "not expertise — check that every member is one you actually want")
    elif cats:
        notes.append("names both skills and categories; the categories are additive, so a "
                     "category's members are kept too. Prefer one or the other.")

    approvals = m.get("approvals")
    if approvals is None and isinstance(m.get("defaults"), dict):
        approvals = m["defaults"].get("approvals")
        if approvals is not None:
            notes.append("'approvals' is nested under 'defaults'; both forms work, but the "
                         "README documents it at the top level")
    if approvals is not None:
        if not isinstance(approvals, list) or not all(isinstance(a, str) for a in approvals):
            errors.append("'approvals' must be a list of strings")
        elif not approvals:
            notes.append("'approvals' is empty, so this Bot will stop and ask about nothing "
                         "— correct only if it genuinely cannot cause harm")

    rs = m.get("registry_skills")
    if rs is not None:
        if not isinstance(rs, list):
            errors.append("'registry_skills' must be a list")
        else:
            for i, e in enumerate(rs):
                if not isinstance(e, dict):
                    errors.append(f"registry_skills[{i}] must be an object")
                elif not (e.get("query") or e.get("identifier")):
                    errors.append(f"registry_skills[{i}] needs a 'query' or an 'identifier'")
                elif e.get("query") and not e.get("optional"):
                    notes.append(f"registry_skills[{i}] uses a search query and is required; "
                                 "search is noisy, so prefer an explicit identifier")

    for unknown in set(m) - _KNOWN_KEYS:
        notes.append(f"unrecognised key '{unknown}' — ignored by the curator")

    return errors, notes


_KNOWN_KEYS = {
    "version", "domain", "label", "summary", "skills", "skill_categories",
    "registry_skills", "toolsets", "approvals", "defaults", "$comment",
    "description",
}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    targets: list[Path] = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            targets.extend(sorted(p.glob("*.json")))
        else:
            targets.append(p)

    rc = 0
    for path in targets:
        if path.name == "TEMPLATE.json":
            continue
        errors, notes = validate(path)
        if errors:
            rc = 1
            print(f"FAIL {path}")
            for e in errors:
                print(f"  error: {e}")
        else:
            print(f"ok   {path}")
        for n in notes:
            print(f"  note: {n}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
