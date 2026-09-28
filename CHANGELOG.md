# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.0] - 2026-09-28

An independent adversarial review of the shared-policy feature returned `no_ship`. Two of its
findings were data-corruption regressions in existing features, and one was a read-and-inline
primitive reachable from a downloaded template. All are fixed, each with a regression test, and
`bench/shared_policy_mutation_check.py` reverts all nine fixes and requires the suite to catch
every one.

### Fixed
- **`copy_agent` produced a Bot with two identities and the wrong role.** The policy block is
  injected at the top of `SOUL.md`, so line 1 became `<!-- forge:shared-policy:begin -->`.
  `forge.soul_role()` and `forge.ensure_identity()` both read line 1, so a copy rebuilt the file
  around the marker comment: the copy introduced itself as "the Bot" instead of its role while
  the original heading survived underneath. That is exactly the failure `ensure_identity` was
  written to prevent — its own docstring says so. Both functions now read the persona with the
  block stripped, and `ensure_identity` re-injects the block so a rename never drops it. A
  `SOUL.md` with no block is treated as all-persona, so pre-existing Bots do not acquire one.
- **`share_agent` → `import_agent` was broken for every Bot with a policy.** `portable.py`
  derives the exported template's `role` from `soul_role()`, which returned `''` once the block
  was present, and `forge` then rejected the import with "spec needs at least 'role'". A Bot
  built with a shared policy could be shared but not imported — while `share_agent` advertises
  the result as safe to post as a gist.
- **`shared_policy_path` could read any file on the machine.** It reached `forge` from a spec,
  and `create_agent` merges template keys over the spec, so a `.botforge.json` downloaded from a
  gist could name any path and have its contents inlined verbatim into a new Bot's system
  prompt — or written to, since a missing target was created. Paths are now constrained to the
  Hermes root. The check is *lexical*, not `resolve()`: resolving follows symlinks and would
  reject a user's own symlinked policy, and any Hermes root reached through a symlink. A `../`
  in a downloaded template is not a deliberate choice; a symlink is.
- **`check_policies` crashed on a non-UTF-8 file.** `UnicodeDecodeError` is a `ValueError`, not
  an `OSError`, so it escaped the `except OSError` around `read_text()` and raised out of a tool
  documented as read-only. One latin-1 apostrophe in a hand-edited `SOUL.md` was enough.
- **`check_policies` counted unreadable Bots as current.** `current` was computed as
  `checked - stale - without`, and a row that errored belonged to neither list — so it inflated
  `current` and the Bot appeared in no list at all. Now counted from the rows, with an explicit
  `unreadable` list. The `default` profile is also audited; its `SOUL.md` is at the Hermes root
  rather than under `profiles/`, so a stale default Bot was never reported.
- **A comments-only policy built Bots with no rules and reported them current.** The guard was
  `text.strip()`, which is truthy for a comments-only file, and `policy_body()` strips comments
  — so such a file hashed identically to an empty policy. Both the build and the check now
  reject a policy with no rules in it.
- **Cosmetic reformatting marked every Bot stale.** Only comments and blank-line runs were
  normalised, so a CRLF checkout, trailing whitespace, or a `*` bullet each looked like a
  changed rule — a Windows user editing the policy would see every Bot go stale at once. The
  fingerprint now normalises line endings, trailing whitespace, and bullet markers.
- **The documented fix for a stale Bot did not work.** `check_policies` said "re-run
  create_agent", but `create_agent` refuses a name that is already taken, so the advice could
  never converge. `update_agent` now takes `refresh_shared_policy: true`, which re-injects the
  policy in place, preserves identity and persona, backs the file up first, and is idempotent.

### Corrected
- The docs described an opt-in symlink mode that was never built. There is no `os.symlink` call
  and no schema key; inlining is the only mechanism. The docstring and design note now say so.

### Added
- `bench/shared_policy_mutation_check.py`, in CI: reverts all nine fixes above and requires the
  suite to catch each one.

## [0.15.2] - 2026-09-28

### Fixed
- **The process benchmark's verdict no longer depends on filesystem enumeration order.** A skill can live in more than one category folder — the fixture has `obsidian` under both `productivity/` and `note-taking/` — and `inventory()` keeps whichever copy it enumerates first. Which copy that is differs between macOS and Linux, so a manifest naming the `note-taking` category was judged correctly on macOS and reported as leaking `obsidian` on Linux. The gate passed locally and failed in CI with 2 of 46 domains breaching, which is what surfaced it. The oracle now collects every category a skill appears in rather than trusting one arbitrary copy, and the same skill is allowed if any of its copies is in a requested category.

### Added
- `bench/order_mutation_check.py`, in CI: reverses enumeration order and requires an identical verdict. It exits non-zero naming the affected domains if the single-copy logic returns, so this cannot regress unnoticed.

## [0.15.1] - 2026-09-28

An independent adversarial review of the harness work found three real defects and one
overstated claim. All are fixed, and each fix is now covered by a test that fails when the
fix is reverted.

### Fixed
- **A malformed `harness_manifest` could crash the build and strand a half-created Bot.** The type guard was `inline_manifest and not isinstance(inline_manifest, dict)`, so a *falsy* non-dict — `[]`, `0`, `false` — passed the guard and reached `.get("domain")`. That raised `AttributeError` **before** the rollback handler, so the exception escaped `forge()` and the already-created profile was never cleaned up. The caller received a raw traceback string instead of a result. The guard is now a plain `isinstance` check, and rejection happens before any profile is created.
- **A status object or empty list printed before the registry payload was mistaken for the payload.** `_json_from` returned the first *parseable* value, so output like `{"status":"ok"}\n[{...}]` yielded the status object, which `search()` then rejected as "not a list" — reported to the contributor as "not found in registry", the same misleading status the previous fix was meant to eliminate, just for a different input shape. It now prefers a non-empty list, then any non-empty value, and only falls back to an empty result when nothing better exists. An empty list from a registry with no matches is still a real answer, not noise.
- **`TEMPLATE.json` is no longer resolvable as a domain.** `available_domains()` globbed every `*.json` in `harnesses/`, so the contributor template was listed and `harness: "TEMPLATE"` built a Bot from placeholder skills that do not exist. The exclusion had to go in two places: the listing, *and* `load_manifest`, because on a case-insensitive filesystem a direct load still succeeded after the listing was fixed.

### Corrected
- **The process benchmark's independence claim was overstated, and is now true.** `ad065d7` said the benchmark "re-derives the floor and category walk independently". The floor and category walk were re-derived; **root discovery and inventory were not** — the oracle called `harness.external_dirs()` and `harness.inventory_with_externals()`, the same functions it was checking. Breaking external-dir discovery therefore broke the oracle and the implementation together, and all 46 gates still passed. An independent reviewer demonstrated this: reverting `external_dirs()` to `[]` passed 46/46 *and* all four negative tests. The oracle now parses `config.yaml` and walks the filesystem itself. Reverting the original `ALWAYS_KEEP` fix still fails 46 of 46, and breaking `external_dirs()` now fails the contamination gate (2/46) instead of passing silently.

### Added
- `bench/external_mutation_check.py`, in CI: breaks external-skill handling two ways and requires `--gated` to notice. This is the specific gap that made the previous claim false, so it is now a standing gate rather than a claim in a commit message.

## [0.15.0] - 2026-09-28

### Added
- **Shared operating policy: write a rule once, every Bot picks it up.** The rules that apply to every Bot — spend nothing without asking, never paste a secret, report what you didn't check — used to be pasted into each `SOUL.md` individually, so a rebuilt or copied Bot silently lost them. They now live in one file, `~/.hermes/shared/BOT-POLICY.md`, which `create_agent` inlines into each Bot's `SOUL.md` inside fenced markers. A starter policy is written on first use.
- **`check_policies` (read-only) makes drift visible.** It fingerprints each Bot's inlined block against the canonical file and returns `stale` (built before your latest edit), `no_shared_policy` (opted out, or predates this feature), and a per-Bot reason. A Bot rebuilds into currency; nothing is auto-rewritten behind your back. Opt out per Bot with `"shared_policy": false`.
- Design note and tests in [`docs/shared-policy.md`](docs/shared-policy.md): the mechanism borrows the *discipline* from [steipete/agent-scripts](https://github.com/steipete/agent-scripts) but deliberately not its symlink, because a whole-file symlink would give every Bot the same identity and a pointer line would put the policy outside the system prompt. Both rejected alternatives are asserted broken by tests rather than merely disfavoured.
- `bench/policy_mutation_check.py` reverts each of six load-bearing policy decisions and requires the suite to catch every one, in CI. A drift detector that cannot fail is not a detector.

### Fixed
- A comment-only edit to the policy file no longer marks every Bot stale. Removing a comment left the blank line it sat on behind, so annotating the file looked like changing a rule. Found by the end-to-end check, not by inspection; the fingerprint now strips whole-line comments, unwraps inline ones, and collapses blank-line runs.

## [0.14.0] - 2026-09-28

### Added
- **Expert harnesses: a Bot whose skills were chosen and verified, not inherited.** Stock creation clones a profile and disables what you didn't name, which yields a large, mostly-irrelevant skill set. Passing `harness: "<domain>"` to `create_agent` resolves a curated manifest instead, installs anything missing from the skill registries, verifies every skill actually resolves, and reduces the profile to that allowlist. `harness_domains` lists what's available; any JSON manifest can be passed inline as `harness_manifest` to curate a domain that isn't bundled. See `docs/expert-harnesses.md` and `harnesses/`.
- The first bundled harness: **`ios`** — native iOS engineering, with the App Store / signing / device-permission approvals a real iOS Bot needs.
- Curation reports honestly. `create_agent` returns a `harness` block with kept skills, the disabled count, and a `gaps` list for anything that couldn't be found or installed. A missing skill is never silently dropped.
- `bench/process_bench.py` tests the **general process**, not any one domain: five invariants (fidelity, contamination, efficiency, determinism, idempotence) across 46 generated manifests including edge cases, all 46/46. `--negative` injects four faults into the real pipeline and confirms each gate catches its fault, so a pass means something. CI runs both. There is deliberately no per-domain "is this a good Bot" score — that needs domain expertise no benchmark can supply, so it stays the contributor's judgment. `bench/benchmark.py` remains only as the shared fixture and an illustrative iOS example; it is not gated.

### Changed
- **The bundled `ios` manifest no longer names broad `skill_categories`.** The three-category version kept 25 skills to cover the handful that mattered, because `research` drags in `arxiv` and `llm-wiki` whether or not an iOS engineer needs them. Listing the six skills explicitly cuts the set to 10. Category membership is not expertise; this is now the first design rule in `docs/expert-harnesses.md`.

### Fixed
- **A curated Bot now carries what it asked for, not its neighbour's skill library.** The curator only ever disabled *profile-local* skills, on the reasoning that a shared skill (OMH, `~/.hermes/shared/skills`) is "not this profile's to disable". That is wrong: `skills.disabled` is matched by name across every skill directory, so leaving those names out kept ~138 irrelevant skills loaded in every Bot. Verified on a live profile — disabling a skill that exists only in `~/.omh/skills` genuinely stops it loading, and the Bot still names the right iOS skills afterwards. The shared directories themselves are still never written to; only the profile's own config changes.
- **`ALWAYS_KEEP` no longer fails open for shared skills.** The fix above made the *disable* side reach external skill roots, but the *keep* side still resolved a skill's category only against the profile's own `skills/`. A path outside that directory looked category-less, so the new disable pass switched off exactly what `ALWAYS_KEEP` promises to protect: a research or web skill in a shared root was disabled on any profile whose shared tree has category folders. Both sides now resolve against every root the profile loads from. The process benchmark could not see it — its shared fixtures sat at root level, and its expected set was derived from the same helper that was wrong, so it agreed with itself. It now re-derives the floor, the category walk, **and the skill-root discovery** independently — the last of these was still calling the helper under test, so breaking external-dir discovery broke the oracle in the same way as the code and all 46 gates still passed. Reverting the `ALWAYS_KEEP` fix itself now fails 46 of 46 domains, and breaking `external_dirs()` fails the contamination gate rather than passing silently.
- **An inline `harness_manifest` is no longer silently ignored without a `harness` key.** Resolution lived inside the `if domain:` guard, so the documented path for every domain that doesn't ship — pass a manifest, no domain key — built an uncurated Bot and returned `ok: true`. The user was told they got an expert and got a generalist. A manifest that names no skills at all is now an error rather than a Bot carrying only the floor. A manifest with no `skills` key is also distinguished from an unknown domain, and the JSON-string form is accepted.
- **`harness_install` is now a real switch.** It was documented as configurable but missing from the plugin's settings tuple, so `settings.get("harness_install", True)` always returned `True`: the setting that stops `create_agent` from shelling out to remote registries did nothing. The one test for it injected settings straight into `forge.forge()`, the only path where it can be `False`; there is now one that goes through the real `register()` closure.
- **Registry search results survive CLI epilogues.** The JSON parser took everything from the first bracket to the end of the output, so a `hermes` CLI that printed `[1/3] installing` or `Updated 1 skill in 0.4s` came back as an empty registry — reported to the contributor as "not found in registry", the one status that reads as "my query is wrong".
- **The missing-skill repair can now actually work.** It passed a *local* skill name to `hermes skills install`, which needs a path-shaped registry identifier, hardcoded one category, and stopped after a single attempt. It now resolves the name through a registry search first, and repairs every missing skill rather than the first.
- **`harness_domains` returns the approval strings, not just a count.** The tool exists so the agent can tell the user what a Bot will be gated on *before* building it; for `ios` that is the App Store and signing-identity boundary.
- **The `harness` schema no longer advertises four domains that don't ship.** It named `trading`, `social-media`, `business-ops` and `hermes-tuning` while only `ios` exists, so an agent that followed it got an uncurated Bot plus an error to report. It now points at `harness_domains` as the authoritative list.
- **The `NEVER_DISABLE` test is no longer vacuous.** Its fixture wrote the skill *description* into the frontmatter `name:` field, so the skill was literally named "Use when configuring Hermes." and never collided with the protected name — it passed with the guard removed. A companion test now proves the guard is load-bearing.
- **A profile's `skills.external_dirs` are loadable, and the curator now knows it.** Shared and OMH skill roots were invisible to skill resolution, so every shared skill was misreported as missing and re-installed for no reason. They now count as available.

## [0.13.0] - 2026-09-28

### Added
- **A blocked Bot emails you instead of waiting to be asked.** `check_agents` gathers what needs you when you think to ask; this is the push half. When a Bot journals a `blocked` or `failed` entry it sends one plain-text message, and `notify.py --action digest` sends the whole queue on a schedule. It reuses the email Hermes already has (`EMAIL_SMTP_HOST` / `EMAIL_ADDRESS` / `EMAIL_PASSWORD`) — no new credential, no new service, nothing to sign up for. `notify_email` redirects it or switches it off; `notify_blocked` controls the per-blocker message.
- `check_install` now reports whether a blocked Bot can reach you, or is waiting silently.

### Security
- **Outbound only, and the recipient is resolved from config alone** — never from a tool argument, a persona, or any text a model produced. A Bot can write to the user's own address and no other, so it cannot be talked into mailing a third party, and two Bots cannot start a reply loop. Nothing in the plugin reads a mailbox. Messages are rate limited per Bot, secret-scanned before sending, and skipped silently when mail is unconfigured or the server is unreachable — a failed send can never break a turn, a journal write, or a routine.

## [0.12.1] - 2026-09-25

### Fixed
- **The survey is useful on a real machine, not just a tidy one.** Three faults, all found by running it against a workspace with ten roots and thirty near-identical checkouts:
  - One crowded root ate the whole scan budget, so the nine roots after it were never looked at and a Bot was pointed at whatever sorted first. Each root now gets its own share.
  - A directory with almost no words in it matched anything it shared one word with — a folder named `omarchy` scored a perfect 1.00 against a theming Bot on the strength of its own name. A place now needs real vocabulary and at least two shared terms before it can be recommended.
  - Nested copies of the same checkout appeared three times; the shallowest path now wins.
- **A job and a directory rarely use the same word for the same thing.** The job's vocabulary is widened with related terms before places are searched, so a Bot whose job says "x.com posts" finds the repo that holds that work even when it never uses the word "social", and an inbox Bot finds the repo that watches mail. The widening applies only to the job and only when matching places — never to the corpus, and never to the duplicate guard, which still compares what two Bots actually say.

## [0.12.0] - 2026-09-25

### Added
- **A new Bot knows where it landed.** `create_agent` now surveys the workspace the Bot was born into — the directory Hermes is running in and the repos under it, plus this install's own Bots, skills and plugins — scores it against the Bot's own SOUL.md and one-job, and writes the result into the Bot's memory. The Bot knows which repo holds its work on its first turn, and neither it nor the calling agent has to research the user's machine. Deterministic word matching, no model call. The result's `workspace` block carries `fits`, `covered_by`, `skills_here` and `next_steps` for the reply.
- **A Bot that already exists is not built twice.** When an existing Bot's job covers the new one, `create_agent` refuses and names it, rather than letting the roster fill with overlapping Bots. `allow_overlap: true` is the way past it, once the user has said they want both.
- `workspace_survey` (default on) and `workspace_roots` configure the scan. It is read-only and shallow: directory names, git remotes, and the head of a README / AGENTS.md / CLAUDE.md — never source files, never the home directory unless it is named in `workspace_roots`, never inside dependency trees, capped at 3s and cached so the tenth Bot costs nothing. Anything the secret scanner flags never reaches a Bot's memory.

## [0.11.0] - 2026-09-24

### Fixed
- **The desktop tapback shipped in 0.9.0 never worked, anywhere.** Three faults stacked, and the third hid the other two:
  - A Hermes hook runs in the profile that runs the turn. Bot Forge lives in the profile that *creates* Bots, so when the user talked to a Bot, none of this plugin's code was loaded — the reaction could not be placed no matter what.
  - `react_to_message` registers itself when its module is imported, and that import is lazy. In a turn's process the registry had no such entry, so the call returned `Unknown tool: react_to_message`.
  - `dispatch_tool` returns failures as a **value**, never as an exception. The hook caught only exceptions, so every failed reaction was recorded as a success — a feature that had never once worked reported as working, in the tests too, because the test double could not fail either.

  The reaction now ships *inside* each Bot as `bot-forge-marks`: two hooks and no tools, so a Bot gains the acknowledgement and not the ability to create or delete Bots. It is installed with every new Bot, imports the tool before dispatching, reads the dispatch result and honours Settings → Appearance → Message Reactions.

### Added
- `update_agent(name, ack_tapback: true)` installs the reaction hook into a Bot made before this release.
- `check_install` reports how many Bots can react, names those that cannot and why, and says when Message Reactions is off in Settings — so a silent reaction is visible instead of invisible.

## [0.10.0] - 2026-09-24

### Added
- **`check_agents` now leads with what is waiting on you.** A Bot that gets blocked writes it in its journal and goes quiet, so the user had to open each Bot to find out. The new `waiting_on_you` list gathers every unresolved blocked or failed entry across every Bot — which Bot, what it needs, and how many days it has sat there — and the summary reads "2 waiting on you — Marlow: Need the Stripe key…". An item closes itself when the Bot later records the same title as completed.

### Fixed
- **Upgrading acknowledgements could silently switch a Bot's journal off.** Replacing the old acknowledgement block cut everything up to the next heading, which swallowed the `<!-- bot-forge-journal:v1 -->` marker sitting on the line above it: the guidance text stayed, the marker went, and `agent_journal` then answered "journaling is not enabled for this Bot". Stripping now stops at the next heading *or* the next marker comment, and re-enabling a journal clears guidance text left orphaned by the old bug instead of appending a second copy. Affected Bots are repaired by calling `agent_journal` with `action: enable` once.

## [0.9.0] - 2026-09-24

### Added
- **The acknowledgement now lands on your own message in Hermes Desktop**, the moment you send it: 👀 while the Bot works, then ✅ / ✋ / ⚠️ for how the turn ended. The plugin places it through `react_to_message` itself rather than leaving it to the model — that tool tells the model it is a human touch, "never as a status signal", so a Bot asked to signal status with it did nothing. Desktop sessions only, needs Settings → Appearance → Message Reactions, off with `ack_tapback: false`, and a failed reaction can never break a turn. The reply prefix still covers every other surface.

## [0.8.0] - 2026-09-24

### Changed
- **Acknowledgements now ride on the reply**, not on an emoji tapback: a Bot begins its answer with 👀 / 💬 / ✅ / ✋ / ⚠️ / ⏳ and then answers as normal. Reactions could not do this job — Hermes' own `react_to_message` is documented as a human touch and "never as a status signal", so a Bot told to use it for status followed neither instruction, and it existed only in Desktop sessions with an opt-in setting enabled. The reply prefix works in Desktop, editor clients, the CLI, cron and messaging alike, with no setting.
- Enabling acknowledgements on a Bot that carries the old convention replaces that block instead of stacking a second one, and reports `upgraded`.

## [0.7.2] - 2026-09-24

### Fixed
- A Bot created by Bot Forge could never react in Hermes Desktop. Its canonical Bot Chat was created through `chat -c "Bot Chat" --create-if-missing`, which hardcodes `source="cli"` upstream, and a session's stored source is what decides its client surface — so the Desktop toolset holding `react_to_message` was never offered. On a Bot Mode install the chat is now started with `--source desktop` and then given the canonical title, matching what Desktop itself creates.

## [0.7.1] - 2026-09-24

### Fixed
- Acknowledgements only reached newly created Bots, so the Bot a user actually talks to stayed silent and the feature looked broken. `check_agents` now reports `acknowledges` per Bot and lists `not_acknowledging`, the doctor counts how many Bots acknowledge and names the ones that don't, and the skill tells an agent to check the current Bot first when reactions aren't happening. Switch one on with `update_agent(ack_reactions: true)`.

## [0.7.0] - 2026-09-24

### Added
- Acknowledgement reactions — a Bot taps back on the message it picked up: 👀 started, 💬 answering now, ✅ done, ✋ needs your approval, ⚠️ blocked, ⏳ scheduled. Once on pickup, once on the outcome, and never instead of a reply. On by default (`ack_reactions`); `update_agent(ack_reactions: true)` adds it to a Bot created earlier.

## [0.6.0] - 2026-09-24

### Added
- `agent_journal` — enable, append to, and read a Bot's private dated work journal. Entries capture outcomes, evidence, blockers and next steps while refusing credential-shaped content.
- New Bots receive concise journaling guidance by default (`journal_enabled: true`); older Bots can be enabled without replacing their persona.

### Changed
- Shareable `.botforge.json` templates continue to exclude activity history, now explicitly including Bot work journals. Full private backups retain them.
- The doctor no longer warns about profiles that deliberately don't have Bot Forge enabled, and says plainly on Windows that the platform is untested.
- README states which platforms are tested.

## [0.5.0] - 2026-09-23

### Added
- `hermes bot-forge-doctor` (and the `check_install` tool) — checks the install itself: which profiles have Bot Forge enabled, whether each gateway is running the current plugin code (the usual reason the tools never appear), Desktop Bot Mode, whether a new Bot's inherited model can sign in, usable sandbox backends, bundled templates, and version drift between profiles. Read-only, and it prints the exact next commands.
- `create_agent(sandbox=...)` — give a Bot its own computer: `docker`, `singularity` or `apptainer` put its shell in a container instead of on the user's machine. The call is refused before anything is created when the backend is not usable here. Also available per member in `create_team`.
- `check_agents` reports each Bot's sandbox and flags Bots with `terminal` / `code_execution` / `computer_use` that run directly on the real machine.

## [0.4.1] - 2026-09-19

### Fixed
- `delete_agent` now takes a real `.tar.gz` backup (`mode=backup`) before deleting and refuses to delete when that backup fails; before, it silently wrote a design-only template or nothing at all.
- `allow_secrets` is now an operator setting (`config_schema`), not a tool argument — a model could previously pass it to `share_agent` / `import_agent` and bypass the BLOCK verdict of the secret scanner.
- `share_agent` `path` must stay under `<hermes>/profile-exports` and never overwrites an existing file; before, it could write anywhere the process could.

## [0.4.0] - 2026-09-19

### Added
- `check_agents` — read-only health check: routines with estimated runs/day, too-frequent / paused / never-run routines, unused Bots, stopped gateways, and personas missing their own name or approval checkpoints.
- Starter templates: `chief-of-staff`, `morning-brief`, `research-digest`, `competitor-watcher`, `engineering-outer-loop` — `create_agent(template=...)`, overridable field by field.
- Portable `.botforge.json` templates with a secret scanner (CLEAN / WARN / BLOCK) on export and import. Findings report the kind and line of a secret, never its value.
- Draft-first defaults: every new Bot gets approval checkpoints for sending/publishing, spending and deleting unless `approvals: []` is passed explicitly.
- Routine guard: schedules faster than every 30 minutes are refused unless `allow_frequent` is set.

### Fixed
- **Privacy:** `share_agent` exported the whole profile, including the Bot's chat history (`state.db`) and facts about the user (`USER.md`). It now writes a design-only template; a full backup is an explicit `mode: backup`, labelled as private.
- Renaming, copying or importing a Bot stacked a second "You are **Name**" line on top of the old one, so the Bot could introduce itself by its previous name. The persona now has exactly one identity, and its heading is renamed too.
- `update_agent` with a new `display_name` renamed the roster entry but not the persona.
- `create_team` dropped each member's warnings and connector suggestions from its result.
- Guardrail sections no longer leave stray blank lines in SOUL.md.

## [0.3.0] - 2026-09-17

### Added
- `create_team` — build a lead plus up to six specialists in one request. Each member reports to the lead, the lead learns the roster so it can delegate, and a member that fails is rolled back on its own.
- `teach_agent` — save a procedure as a skill in the Bot's own skills folder ("remember how I write my weekly report"), and re-enable it if that skill was disabled.
- `create_agent` now suggests matching servers from Hermes' MCP catalog for the Bot's job (`suggest_connectors`, on by default; suggestion only).

### Changed
- **The plugin no longer touches credentials.** `share_login` is gone; sharing one login across Bots now lives in `extras/share_login.py`, an optional script the user runs themselves, with the trade-offs documented.

## [0.2.0] - 2026-09-17

### Added
- `update_agent` — edit a Bot by chatting: persona (append or replace), name, description, memory, toolsets, skill categories, model, face and routines. Replaced files are backed up under `<profile>/backups/bot-forge/`.
- `copy_agent` — duplicate a Bot under a new name.
- `share_agent` / `import_agent` — export a Bot to a `.tar.gz` and import it back. Credentials are never included.
- `hide_agent` — hide or unhide a Bot in the Desktop roster without touching it.
- `delete_agent` — permanent delete, disabled unless `allow_delete` is set, requiring the Bot's exact name and backing it up first.
- `create_agent` now takes `approvals` (checkpoints written into SOUL.md and memory) and `reports_to` (its chief of staff).
- `list_agents` also reports display name, hidden state and routine count.
- CI: unit tests on Python 3.11 and 3.12.

### Fixed
- Rewriting a Bot's `config.yaml` kept the default umask, which could widen the file's permissions; the original mode is now preserved and the write is flushed to disk.
- Sharing the root login no longer creates or modifies any file inside the root profile, and swaps the link atomically.
- Rollback now reports whether the half-built profile was really deleted, and tells the user the cleanup command if not.
- Windows: the fallback Hermes root is `%LOCALAPPDATA%\hermes`.
- SQLite connections used to find the calling profile are closed (they blocked profile deletion on Windows).
- `profile create` gets a longer timeout, since it copies the whole skills tree.

### Changed
- `plugin.yaml` declares `requires_hermes: ">=0.21"`.

## [0.1.1] - 2026-09-17

### Fixed
- `hermes plugins install` refused the plugin: Hermes' installer supports `manifest_version` 1 only, so the v2 manifest markers were dropped.
- Deleted Bots no longer reserve their name — a tombstoned profile directory made a freed name look taken.

## [0.1.0] - 2026-09-17

### Added
- `create_agent` — build a complete Hermes Bot from one sentence: unique Proper Case name, blob face, SOUL.md, memory, toolsets, skill categories, routines, Bot Chat self-introduction and gateway service, with rollback on failure.
- `list_agents`, `ask_agent`.
- Bundled skill `bot-forge:bot-forge` with role defaults and a SOUL.md template.
- Settings: `inherit_model`, `fallback_model`, `probe_local_models`, `install_gateway`, and opt-in `share_login`.
