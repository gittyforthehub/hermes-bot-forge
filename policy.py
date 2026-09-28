"""Shared operating policy: write once, every Bot picks it up.

The problem this solves is copy drift. `create_agent` writes each Bot's `SOUL.md` as a
literal file, so a rule that applies to every Bot — spend nothing without asking, never
push to main, don't paste secrets — has to be pasted into each one. The moment a Bot is
rebuilt, updated, or copied, the copies differ, and nothing notices.

`steipete/agent-scripts` solves the same problem for Claude Code and Codex: one canonical
`AGENTS.MD`, symlinked into each agent's expected filename, with a pointer-style line in
downstream repos. That pattern is borrowed here, adapted for two facts about Hermes that
make the naive version wrong:

1. **A whole-file symlink is wrong.** `ensure_identity()` writes the Bot's own name and
   profile id into the first lines of `SOUL.md`. One canonical file symlinked into every
   profile would give every Bot the same identity — they would each introduce themselves
   as whichever Bot was built first. Verified, not assumed: the identity line is required
   to be unique per Bot.
2. **A pointer line is also wrong.** Hermes reads `SOUL.md` straight into the system
   prompt (`prompt_builder._read_context_file`, which follows symlinks and reads through
   them). A line saying "READ ~/policy/AGENTS.MD BEFORE ANYTHING" puts the *instruction* in
   the prompt and the *policy* wherever the agent decides to go look. It follows only if
   the agent chooses to read it — which is exactly the case that matters least, because
   the agent already has everything else.

So the shared policy is **inlined at build time and kept honest by a fingerprint**. The Bot
gets the full text in its own `SOUL.md` (correct prompt semantics, unique identity, no
runtime dependency), and forge records what it injected. `check_policies` then reports any
Bot whose shared block has drifted from the canonical file — the drift becomes visible
instead of silent.

Hermes does follow symlinks when it reads context files (verified against
`prompt_builder._read_context_file` and `context_file_sources`, including the broken-link and
self-referential-loop cases, both of which degrade to empty content rather than hanging). That
is why a symlinked SOUL.md *would* work mechanically — and it is still the wrong default, for
the two reasons above. There is no opt-in symlink mode; inlining is the only mechanism.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# ── markers ────────────────────────────────────────────────────────────────────
# The injected block is fenced by these so a later check can find, replace, or remove it
# without guessing, and so a human reading SOUL.md can see what is generated.
BEGIN = "<!-- forge:shared-policy:begin -->"
END = "<!-- forge:shared-policy:end -->"

DEFAULT_RELATIVE = "shared/BOT-POLICY.md"

# Written on first use so the shared floor exists without the user having to author it.
# Deliberately opinionated and short: a policy nobody reads is worse than no policy. The
# user is meant to edit this file — `check_policies` then shows which Bots are stale.
STARTER_POLICY = """# Shared operating policy — every Bot on this Hermes deployment

These rules apply to every Bot, regardless of domain. A Bot's own `SOUL.md` adds its
identity, expertise and approvals; this file is the floor they all stand on.

Edit this file, then run `check_policies` (or rebuild the Bots) to propagate.

## Money and external state

- Ask before spending money, buying anything, or opening a paid service — including a free
  trial that will charge later.
- Ask before publishing, sending, or posting anywhere a real person will see it.
- Ask before deleting data, and never delete anything you did not create this session.

## Secrets and privacy

- Never print, log, commit, or paste a secret value, even one that "looks harmless".
  Reference secrets by name.
- Read local data from disk before reaching for an API key or a network fetch.
- Send nothing private to a public audience or external service without approval of both
  the content and the destination.

## Honesty

- Report what you verified and what you did not. "Probably works" and "I could not check"
  are both useful; a confident claim you cannot support is not.
- A missing or skipped check is a finding, not a footnote. Say it plainly.
- If blocked, say what is missing and what would unblock it — do not quietly narrow the
  task to something you can finish.
- Prefer an unmeasured statement over an invented number.

## Working style

- Do the task asked. If a different approach seems better, do the asked task, then say
  what you would change and why.
- Read the project's own instructions and code before inventing a method.
- Make the change, verify it with the project's real checks, and report the real output.
- Prefer deleting an old path over layering a new one beside it.
- Leave the working tree as you found it, apart from what you were asked to change.

## Scope

- Ask before anything that spends, publishes, leaves this machine, or is hard to undo.
  Otherwise act, and report what you did.
"""

_BLOCK = re.compile(
    re.escape(BEGIN) + r"\n?(.*?)" + re.escape(END),
    re.DOTALL,
)


class PolicyPathError(ValueError):
    """A shared-policy path that points outside the Hermes root."""


def policy_path(hermes_root: Path, relative: str | None = None) -> Path:
    """Where the canonical shared policy lives for a given Hermes root.

    `relative` is constrained to the Hermes root. It reaches here from a spec, and
    `create_agent` merges template keys over the spec — so a `.botforge.json` downloaded from
    a gist could otherwise name any file on the machine and have its contents copied into a
    new Bot's system prompt, or written to if it did not exist. The policy file is content
    that goes straight into a prompt, so an unconstrained path is a read-and-inline primitive,
    not a convenience.

    Containment is checked on the *lexical* path (what was written in the spec), not on
    `resolve()`. Resolving follows symlinks, and a perfectly legitimate
    `shared/BOT-POLICY.md` that is a symlink into another location — or a Hermes root that is
    itself reached through a symlink, which is common — would then be rejected as an escape,
    breaking the feature for the reason it exists. A symlinked leaf is a deliberate,
    visible choice by the user; a `../` in a downloaded template is not.
    """
    root = Path(hermes_root).expanduser()
    if relative is not None and not isinstance(relative, str):
        # Reachable from a downloaded template, so never assume the type. `root / [...]` on a
        # list or a Path raises TypeError far from here, and an absolute Path in the spec
        # silently replaces the root, discarding the containment check entirely.
        raise PolicyPathError(
            f"shared_policy_path must be a string, got {type(relative).__name__}"
        )
    # `expanduser` must run on the RELATIVE part only. On the joined path it would turn
    # "~/.hermes/shared/BOT-POLICY.md" into a literal "~" directory inside the root, and
    # create_agent would write the starter policy there -- still lexically contained, so the
    # check below would pass, and silently in the wrong place.
    rel = Path(relative).expanduser() if relative is not None else Path(DEFAULT_RELATIVE)
    if rel.is_absolute():
        raise PolicyPathError(
            f"shared_policy_path must be relative to the Hermes root, got {relative!r}"
        )
    candidate = root / rel
    try:
        lex = Path(os.path.normpath(str(candidate)))
    except (OSError, ValueError) as exc:
        raise PolicyPathError(f"bad shared policy path {candidate}: {exc}") from exc
    root_lex = Path(os.path.normpath(str(root)))
    if lex != root_lex and root_lex not in lex.parents:
        raise PolicyPathError(
            f"shared policy path escapes the Hermes root: {candidate} is outside {root}"
        )
    return candidate


def policy_body(text: str) -> str:
    """The policy text proper, with fence markers and HTML comments removed.

    A canonical policy file has no markers, so its whole text is the body. A Bot's SOUL.md
    has them, so only the fenced interior counts. Hashing the body is what makes a canonical
    file and a Bot's inlined block comparable — hashing either whole file would not, because
    the block additionally carries the Bot's identity and persona.

    Comments are stripped so that annotating the policy file is not mistaken for changing a
    rule. A drift report that cries wolf on every reworded comment is one people learn to
    ignore, and then it stops being a report.
    """
    raw = text or ""
    m = _BLOCK.search(raw)
    body = m.group(1) if m else raw
    # A comment is not a rule. Remove whole-line comments along with the line itself, so
    # they don't leave a blank line where a rule used to be; then unwrap any inline comment
    # left over. Two files that state identical rules must hash identically no matter how
    # their comments are placed — a drift report that fires on reformatting is a report
    # people learn to ignore.
    body = re.sub(r"(?m)^[ \t]*<!--.*?-->[ \t]*\n?", "", body)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    # Normalise the remaining cosmetic differences: a CRLF checkout, trailing whitespace, and
    # a different bullet marker all say the same thing. Without this, saving the policy on a
    # Windows editor marked *every* Bot stale, which is the fastest way to make a drift
    # report something people stop reading.
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    # Normalise the bullet marker but KEEP the indentation, normalised to spaces, and only
    # after removing the indentation every line shares. Collapsing `  - sub` to `- sub` would
    # make a nested rule hash identically to a flat one -- a real change to the policy's
    # structure reporting as no drift at all, which is far worse than the cosmetic false
    # positive this normalisation exists to avoid. Dedenting the COMMON margin first keeps the
    # cosmetic case working: a list indented under its heading is the same list, while a
    # sub-bullet stays deeper than its parent.
    body = _dedent(body)
    body = re.sub(r"(?m)^([ \t]*)[-*+][ \t]+", lambda m: m.group(1) + "- ", body)
    body = re.sub(r"(?m)^([ \t]*)(\d+)[.)][ \t]+", lambda m: m.group(1) + f"{m.group(2)}. ", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def _dedent(text: str) -> str:
    """Remove, per block, the indentation every line in that block shares; tabs become spaces.

    Dedenting per block rather than per document is what makes both cases work: a list
    written two spaces under its heading is the same list, while a sub-bullet inside an
    already-flush list keeps its depth relative to its parent. Only the *relative* depth of
    a line against its siblings carries meaning.
    """
    blocks: list[list[str]] = [[]]
    for line in text.split("\n"):
        if line.strip():
            blocks[-1].append(line)
        else:
            blocks.append([])
    out: list[str] = []
    for block in blocks:
        if not block:
            out.append("")
            continue
        # Expand tabs FIRST, then measure. Doing it per-line in two different ways made a
        # tab-indented list dedent to one space instead of none.
        flat = [ln.expandtabs(2) for ln in block]
        common = min(len(ln) - len(ln.lstrip(" ")) for ln in flat)
        out.extend(ln[common:] if common else ln for ln in flat)
    return "\n".join(out)


def fingerprint(text: str) -> str:
    """Short, stable digest of the policy body.

    Only the policy itself is hashed, not the surrounding file, so adding a comment or
    changing a Bot's persona does not make that Bot look stale — but editing a rule does.
    """
    return hashlib.sha256(policy_body(text).encode("utf-8")).hexdigest()[:12]


def render_block(text: str) -> str:
    """The fenced block to inline into a Bot's SOUL.md."""
    body = (text or "").strip()
    return f"{BEGIN}\n{body}\n{END}"


def extract_block(soul: str) -> str | None:
    """The shared-policy block from a SOUL.md, or None if it has none."""
    m = _BLOCK.search(soul or "")
    return m.group(0) if m else None


def strip_block(soul: str) -> str:
    """The SOUL.md with the shared-policy block (markers included) removed.

    Use this before any analysis that assumes the Bot's own content starts at the top —
    heading detection, identity rewriting, role extraction. The block is generated, and a
    reader that treats `<!-- forge:... -->` as the persona's first line silently loses the
    role and can leave a second identity in the file.
    """
    return _BLOCK.sub("", soul or "")


def inject(text: str, soul: str, *, position: str = "top") -> str:
    """Inline `text` into `soul`, replacing any block already there.

    Idempotent: injecting the same policy twice yields byte-identical output, so a rebuild
    does not accumulate copies. `position="top"` puts shared policy ahead of the Bot's own
    identity, which is deliberate — the standing rules should be read before the persona
    that has to follow them.
    """
    block = render_block(text)
    if position == "append":
        body = _BLOCK.sub("", soul or "").rstrip()
        return f"{body}\n\n{block}\n" if body else f"{block}\n"
    body = _BLOCK.sub("", soul or "").lstrip()
    return f"{block}\n\n{body}" if body else f"{block}\n"


def check_soul(soul: str, expected_fp: str | None) -> tuple[bool, str]:
    """Is this Bot's shared block current? Returns (ok, reason)."""
    block = extract_block(soul)
    if block is None:
        return False, "no shared-policy block"
    if not expected_fp:
        return False, "no canonical policy to compare against"
    # fingerprint() strips the fence itself, so the block and the canonical file are
    # compared as policy bodies — no marker juggling here.
    actual = fingerprint(block)
    if actual != expected_fp:
        return False, f"policy drifted (bot {actual}, canonical {expected_fp})"
    return True, "current"


def audit_soul(soul: str, expected_fp: str | None) -> dict:
    """Structured result for one Bot, for the check tool and tests."""
    ok, reason = check_soul(soul, expected_fp)
    block = extract_block(soul)
    return {
        "has_shared_policy": block is not None,
        "current": ok,
        "reason": reason,
        "fingerprint": fingerprint(block) if block else None,
        "canonical_fingerprint": expected_fp,
    }


def policy_dir_note(relative: str) -> str:
    """The one-line pointer a human can put in a repo, mirroring the agent-scripts pattern.

    Not used by the plugin — it is here so a user wiring the same discipline into a
    *repository's* AGENTS.md has the canonical wording to copy, and so the pattern is
    documented in code rather than only in prose.
    """
    return (f"READ ~/.hermes/{relative} BEFORE ANYTHING (skip if missing). "
            f"Repo-specific rules go below this line — do not copy the shared block here.")
