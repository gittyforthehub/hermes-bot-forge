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

- **List skills explicitly. Avoid broad `skill_categories`.** Keeping a whole category pulls
  in everything filed under it, and category membership is not expertise. Naming `research`
  to get one web-search skill also gets you `arxiv` and `llm-wiki`. Use `skill_categories`
  only when the whole category genuinely *is* the job.
- **Local skills beat registry skills** for anything a project already depends on —
  Appllama's iOS design skills, for instance, are not on any public registry.
- **Mark approvals honestly.** A harness that installs a trading skill without an
  approval checkpoint is a harness that will eventually place a real order.

### The process benchmark

```bash
python3 bench/process_bench.py            # invariants across 46 generated domains
python3 bench/process_bench.py --gated    # same, plus hard CI gates
python3 bench/process_bench.py --negative # prove the gates catch real faults
python3 bench/process_bench.py --json     # machine-readable
```

There is no benchmark for "is this a good trading Bot" and there cannot be — that would
need someone who knows trading. Instead the benchmark tests the **general process**,
which is the only part that can be checked for every domain without domain expertise:

| invariant | meaning | result |
|---|---|---|
| `fidelity` | every skill the spec names is enabled, or reported as a gap — never silently dropped | 46/46 |
| `contamination` | nothing survives that the spec did not ask for, beyond the universal floor | 46/46 |
| `efficiency` | a spec naming skills and no categories keeps exactly those skills plus the floor — no padding | 46/46 |
| `deterministic` | the same manifest always produces the same plan | 46/46 |
| `idempotent` | applying the plan twice equals applying it once | 46/46 |

The corpus is 40 generated manifests plus six deliberately awkward edge cases (no skills,
no categories, everything, skills that do not exist, missing and unknown fields, duplicate
entries). It is offline and deterministic.

**What it deliberately does not claim.** Curation does not always keep *fewer* skills than
stock bot-forge — on 14 of 46 domains it keeps more, and that is correct. Stock answers
"give me a category"; a manifest that names six specific skills legitimately carries more
than stock's one category. That is the manifest being precise, not padded. So skill count
is reported as context, never gated. A harness earns trust on fidelity and the absence of
contamination, not on winning a count contest against a different question.

**Why the gates can be trusted.** A benchmark that passes because it checks nothing is
worse than no benchmark, so `--negative` injects four faults into the real pipeline —
silently dropping missing skills, padding the allowlist, adding a category fallback to an
exact spec, and making planning order-dependent — and confirms each invariant fails. All
four are caught.

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

### What a curated Bot actually loads

The `ios` harness, on one real install (158 profile-local skills, 138 shared/OMH skills),
enables 36 and disables 260. What the Bot actually carries is the enabled set plus
`hermes-agent`, which Hermes refuses to disable because the system prompt points at it
unconditionally — **37 skills, not 36**, and not the ~172 an earlier version shipped.
Treat the counts as *one machine's* numbers: the guarantee is the set, and `gaps` says
what did not resolve. A different skill library gives different counts and the same rules.

Two things make this work, and both were wrong in the first implementation:

- `skills.disabled` is matched by name against **every** skill directory, not just the
  profile's own. Verified on a live profile: disabling a skill that exists only in
  `~/.omh/skills` really does stop it loading. The first version treated shared skills as
  untouchable — "not this profile's to disable" — which quietly left 138 irrelevant skills
  loaded in every Bot. That reasoning was wrong: the disable list is per-profile config, so
  it affects only the profile that sets it, and the name is simply ignored elsewhere.
- The shared roots themselves are never modified. The harness only writes this profile's
  `config.yaml`; it does not touch `~/.omh/skills` or `~/.hermes/shared/skills`. Disabling a
  name is reversible and scoped to the profile that disables it.

If you *want* a shared skill available to every Bot, name it in the manifest.

## Testing

```bash
python -m unittest discover -s tests
python bench/process_bench.py --gated
python bench/process_bench.py --negative
```

The harness suite covers inventory, allowlist planning, the external-dirs rules, the
registry JSON parsing, failure handling, and the `forge()` wiring. The process benchmark
checks five domain-agnostic invariants across 46 generated manifests, and the negative
tests prove each gate fails when the behaviour it guards is broken. The integration
tests stub profile creation, so everything runs offline in seconds.

## Contributing a manifest

Add `harnesses/<domain>.json` and a test asserting it loads and has a summary. The
manifest is the whole contribution — no code required. The process benchmark already
guarantees the pipeline delivers any manifest faithfully; whether the manifest names the
*right* skills for the domain is the judgment a contributor brings.
