---
name: drive-grok
description: How Claude (architect/reviewer) drives the Grok CLI (implementer) to write code against a plan, keeping one Grok session in context across phases, then reviews and sends rework. Use whenever implementing the Salesforce anomaly detector phases, or any task where Grok writes the code and Claude plans + reviews.
---

# Driving Grok as the implementer

Claude plans and reviews. Grok writes the code. One Grok session carries context across phases.

## Roles
- **Claude** = architect + reviewer. Owns `.claude/plans/*`, decides scope per phase, reviews every
  diff, runs tests, decides commit vs rework.
- **Grok** = implementer. Receives one tightly-scoped instruction per turn and edits files.

## Grok CLI invocation

Always run from the project root.

- **First task of a fresh session** (starts a new Grok session for this dir):
  ```
  grok -p "<instruction>" --always-approve
  ```
- **Every following task** (continues the SAME session so Grok keeps context):
  ```
  grok -c -p "<instruction>" --always-approve
  ```
- Resume a specific past session if `-c` drifts: `grok -r <SESSION_ID> -p "..." --always-approve`.
- List sessions: `grok sessions list`.

Flags that matter:
- `-p/--single` = headless single-turn, prints to stdout and exits.
- `-c/--continue` = continue most recent session for cwd (THIS is how context is maintained).
- `--always-approve` = auto-approve tool use (required for headless file edits).
- Use a long Bash `timeout` (e.g. 300000–600000 ms); Grok edits can take minutes.

## Instruction-writing rules (Claude -> Grok)
Grok obeys precise, bounded instructions. Each instruction must include:
1. **Context pointer**: "Read `.claude/plans/00-master-architecture.md` section N before coding."
2. **Exact files** to create/modify and their target paths.
3. **Exact public API** (class/function signatures, Protocol methods) so output is reviewable.
4. **Constraints**: layering/dependency rules, "no behavior change", "keep endpoints identical",
   "type hints + docstrings", "do not touch <files>".
5. **Done criteria**: e.g. "run `pytest` and report results", "do not edit `tests/`".
One phase per instruction. Never ask Grok to "refactor everything."

## Review protocol (Claude, after each Grok turn)
1. `git --no-pager diff --stat` then read each changed file fully.
2. Verify against the plan: layering respected, Protocols (not concretes) at boundaries, naming,
   no regressions, hints/docstrings, no secrets logged, no scope creep.
3. Run `pytest -q`.
4. **Green + clean** -> `git commit -m "Phase N: ..."`, tick the Task.
   **Red or wrong** -> write a specific rework instruction quoting the exact problem and re-drive
   with `grok -c -p`. Re-review. Only hand-fix trivial one-liners yourself.

## Gotchas
- Windows + PowerShell shell; run grok via the Bash tool with forward-slash paths.
- Grok may "improve" beyond scope — diff every time and revert/redirect scope creep.
- Keep `tests/test_scanner.py` green at every phase boundary; it's the compatibility contract.
- If Grok reports it edited files, still verify with `git diff` — never trust the summary alone.
