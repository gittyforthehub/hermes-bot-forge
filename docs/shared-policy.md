# Shared operating policy

One file, `~/.hermes/shared/BOT-POLICY.md`, holds the rules that apply to **every** Bot.
`create_agent` inlines it into each Bot's `SOUL.md`, fenced by HTML markers, so a rule you
edit once reaches every Bot on the next build.

## Why not just symlink it

The obvious design — and the one `steipete/agent-scripts` uses for Claude Code and Codex —
is to symlink one canonical `AGENTS.MD` into each agent's expected filename. Bot Forge
borrows the *discipline* (one canonical file, change once) but not the mechanism, because two
facts about Hermes make a symlink the wrong default here.

**A whole-file symlink would give every Bot the same identity.** `ensure_identity()` writes
the Bot's own name and profile id into the first lines of `SOUL.md`, and the identity line
is required to be unique. One canonical file symlinked into every profile means every Bot
introduces itself as whichever Bot was built first. There is a test for exactly this:
`test_each_bot_keeps_its_own_identity`, and `test_fork_symlink_would_collide_but_inline_does_not`
which asserts the rejected alternative really is broken rather than merely disfavoured.

**A pointer line would put the rule outside the prompt.** Hermes reads `SOUL.md` straight
into the system prompt. A line saying "READ ~/policy/AGENTS.MD BEFORE ANYTHING" puts the
*instruction* in the prompt and the *policy* wherever the agent chooses to look — enforced
only if the agent decides to read it, which is exactly the case that matters least because
it already has everything else. This is the one case where "it depends on the model
choosing" is the wrong trade.

So the policy is **inlined at build time and kept honest by a fingerprint**. Correct prompt
semantics, unique identity per Bot, no runtime dependency. The cost is that a Bot built
before your edit holds the old text — so drift has to be *visible*, which is what
`check_policies` is for.

Hermes does follow symlinks when it reads context files (verified against
`prompt_builder._read_context_file` and `context_file_sources`, including the broken-link and
self-referential-loop cases, both of which degrade to empty content rather than hanging). The
symlink path is available if you want live-pointer semantics; it is opt-in, never the
default, and it deliberately trades a guaranteed-loaded policy for a single source of truth.

## The drift check

`check_policies` compares each Bot's inlined block against the canonical file by
fingerprint, and returns:

- `stale` — built before your latest edit; rebuild them
- `no_shared_policy` — built with `shared_policy: false`, or before this feature existed
- `bots` — per-Bot `current`, `reason`, and both fingerprints

Only the policy **body** is hashed, with fence markers and comments removed and blank-line
runs collapsed. Two consequences worth knowing:

- Editing a comment is not a policy change. Reformatting the file will not mark every Bot
  stale — a drift report that fires on cosmetics is a report people learn to ignore, and then
  it stops being a report.
- A Bot's persona, identity, and approvals are *outside* the fence, so giving a Bot a new
  name or role does not make it look stale, and a shared rule that happens to mention a Bot
  name does not pin that Bot's copy to a particular edit.

## Opting out

Pass `"shared_policy": false` in a spec to build a Bot with no shared block at all. Useful
for a Bot you deliberately want to run unconstrained by the house rules.

## Using it

```json
{
  "name": "kestrel",
  "role": "trading analyst",
  "shared_policy": true
}
```

`shared_policy_path` points at a different canonical file (relative to the Hermes root) if
you want, say, a stricter policy for production Bots than for throwaway ones.
`shared_policy_create: false` makes a missing policy file a hard error instead of seeding
the starter — the right setting for a machine where an unwritten policy should fail loudly.

The starter policy ships in `policy.py` as `STARTER_POLICY` and is written on first use. It
is deliberately short and opinionated; edit it to match how you actually want to work. It is
seeded, not enforced — nothing checks that you kept it, and nothing stops you replacing it.
