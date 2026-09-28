# Expert harnesses

Ask for a Bot that is genuinely good at something, and get one whose skills were
**chosen and verified** for that job — not inherited from whatever profile happened to
clone it.

## The problem this solves

Stock bot-forge creates a Bot by cloning your default profile and then *disabling*
everything in the categories you didn't name. That produces a Bot with a large,
mostly-irrelevant skill set. A Bot that carries fifty half-relevant skills is not an
expert; it is a generalist with a head start.

An expert harness inverts the direction: start from a curated **manifest** for the
domain, install what is missing from the skill registries, verify every skill actually
resolves, and disable everything outside the allowlist. The result is a small, sharp
toolkit.

```
                       before                     after
skills available   293 (whole clone)     →   ~50 (manifest allowlist)
verified           "whatever was there"   →   every name checked on disk
missing            silent                 →   reported as gaps
```

## Using it

Ask your agent in plain language:

> Make me a bot that's an expert at iOS apps.

The agent calls `harness_domains` to see what's available, then passes the matching key
as `harness` in `create_agent`:

```json
{
  "role": "iOS Engineer",
  "harness": "ios",
  "display_name": "Sable",
  "soul_md": "# Sable — iOS Engineer\n..."
}
```

The result comes back with a `harness` block showing exactly what happened:

```json
"harness": {
  "domain": "ios",
  "label": "Native iOS engineering",
  "kept_skills": ["ios-app-delivery", "test-driven-development", ...],
  "disabled_count": 101,
  "missing_skills": [],
  "gaps": []
}
```

A non-empty `gaps` list is the honest signal: those skills could not be found or
installed, and you should know before trusting the Bot's expertise.

## Writing a manifest

A manifest is one JSON file in `harnesses/`. The filename is the domain key.

```json
{
  "version": 1,
  "domain": "trading",
  "label": "Trading & markets",
  "summary": "Research-driven market analysis with verified data sources.",
  "skill_categories": ["finance", "research"],
  "skills": ["stocks", "grounded-citations"],
  "toolsets": ["web", "code_execution", "file"],
  "registry_skills": [
    { "query": "trading", "category": "finance", "optional": true }
  ],
  "defaults": {
    "sandbox": "local",
    "approvals": ["placing any real trade", "moving money"]
  }
}
```

| Field | Meaning |
|---|---|
| `skill_categories` | Local category folders kept **wholesale** |
| `skills` | Individual skills that must be present, whatever category they're in |
| `toolsets` | Tools the Bot needs (terminal, vision, code_execution, …) |
| `registry_skills` | Skills pulled from skills.sh / clawhub by `query` or exact `identifier` |
| `defaults.sandbox` | `local` or `docker` |
| `defaults.approvals` | Hard checkpoints written into the Bot's SOUL.md |

`optional: true` (the default) means a failed lookup is a reported note, not a build
failure. Set `optional: false` for a skill the domain genuinely cannot work without.

### Design rules

- **Small is the point.** A 60-skill harness is a generalist. If everything is
  required, nothing is prioritised.
- **Local skills beat registry skills** for anything a project already depends on —
  Appllama's iOS design skills, for instance, are not on any public registry.
- **Categories and skills compose.** Use `skill_categories` for a coherent body of
  knowledge, `skills` for the specific must-haves that span categories.
- **Mark approvals honestly.** A harness that installs a trading skill without an
  approval checkpoint is a harness that will eventually place a real order.

## Anything not listed

Bundled domains are a floor, not a ceiling. Pass a manifest inline to curate a domain
that isn't in the repo:

```json
{
  "role": "Beekeeper",
  "harness": "beekeeping",
  "harness_manifest": { "domain": "beekeeping", "skills": ["..."], "...": "..." }
}
```

An unknown `harness` key with no manifest is **reported, not ignored** — the build
still succeeds and the response tells you which domains exist.

## How it works

1. **Resolve** the manifest before the duplicate-job guard runs, so the guard compares
   against the skills the Bot will actually have.
2. **Apply defaults** — toolsets, sandbox, approvals — only where the spec didn't
   specify them. Explicit arguments always win.
3. **After the profile is created**, resolve registry entries, install what's missing,
   then recompute against the real profile on disk.
4. **Reduce to the allowlist** and merge into the existing `skills.disabled` list
   rather than clobbering it.

Two subtleties worth knowing if you read the code:

- A profile's `skills.external_dirs` (shared/OMH skill roots) are genuinely loadable,
  so the curator counts them as available — and never adds them to the disable list.
  They belong to another profile.
- `plan()` is pure. It touches no files, which is what makes the curation testable
  without creating a single profile.

## Testing

```bash
python -m unittest discover -s tests
```

The harness suite covers inventory, allowlist planning, the external-dirs rules, the
registry table parser, failure handling, and the `forge()` wiring. The integration tests
stub profile creation, so the full suite runs offline in under a second.

## Contributing a manifest

Add `harnesses/<domain>.json` and a test asserting it loads and has a summary. The
manifest is the whole contribution — no code required.
