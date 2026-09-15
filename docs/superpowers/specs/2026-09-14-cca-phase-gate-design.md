# CCA Phase Gate — Design

*Date: 2026-09-14 · Status: approved, ready for implementation planning*

A shipping-discipline layer for the CCA plugin. A large plan must be delivered as a sequence of
small, individually committed, independently reviewable increments — never as one lump.

Companion to [`2026-09-14-cca-ensemble-plugin-design.md`](2026-09-14-cca-ensemble-plugin-design.md).
The critic loop judges **whether a change is good**; the phase gate governs **how big a change may
be and in what order**. They are independent features sharing one event log.

---

## 1. Goal

When a feature is large, prevent the session from producing it in a single undifferentiated change.
Force the shape that makes code reviewable: one phase at a time, each ending in its own commit,
with the plugin tracking what has shipped and what is next across session boundaries.

### Non-goals

- **Claude never commits, stages, or pushes.** The gate observes commits; it does not create them.
  This is a hard constraint from the operator's standing rules, not a preference.
- No branch creation, no merge requests, no merges.
- Not a replacement for the critic loop. Either feature can be disabled without the other.

---

## 2. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Phase source | The plan document | `writing-plans` already emits `### Task N` + a `**Files:**` block; the approved plan becomes the enforced contract, with no new authoring format |
| Done gate | tests + critics + commit + clean tree | The clean-tree requirement is the part that actually prevents lump-shipping; without it a phase silently carries later phases' uncommitted code |
| Scope fence | Block edits to *later* phases' files only | Precisely targets the failure mode. Unlisted files are allowed because plan file-lists are always slightly wrong and discovery is normal |
| Progress state | Derived from git, reflected in the plan | No stored state, no drift, survives session boundaries, and `git log` becomes the shipping record |
| Commit handoff | Plugin prints a ready-to-run command | Honours "I decide when and what to commit" exactly |
| Composition | Separate hooks sharing the event log | The two features fail differently and must be independently disableable |

---

## 3. Architecture

```
SessionStart ──► phase context injected into the session:
                 "plan X · phase 3/11 — Critic runner.
                  Scope: scripts/ccalib/runner.py, tests/test_runner.py.
                  Phases 1-2 shipped (a1b2c3d, e4f5g6h)."

PreToolUse (Edit|Write) ──► scripts/phase_hook.py  — the fence
    file belongs to the current phase   → allow
    file belongs to a LATER phase       → DENY (exit 2 + reason on stderr)
    file belongs to an EARLIER phase    → allow (fixing shipped work is legitimate)
    file belongs to no phase            → allow + emit a warning event
    plan missing / unparseable / no git → ALLOW (fail open)

Stop ──► scripts/drain_hook.py — the gate ladder, evaluated in order
    1. phase tests fail            → block: the failing output
    2. CF.md has open items        → block: drain the critics first
    3. no [phase-N] commit exists  → tick the plan checkbox, block with the commit command
    4. working tree dirty          → block: uncommitted work remains
    all clear                      → phase N+1 becomes current automatically
```

**Current phase** is the lowest-numbered task in the active plan with no `[phase-N]` commit in
`git log`. Nothing is stored; the answer is recomputed on every hook invocation.

**Active plan** is `.cca/config.json` → `phases.plan`, defaulting to the most recently modified
file in `docs/superpowers/plans/`.

### Ladder ordering is load-bearing

You are never told to commit work that still fails its tests or still has open critic findings.
Tests and critics are checked first precisely so that the commit, when it is finally suggested, is
a commit of work that passed.

### The checkbox/clean-tree circularity, resolved

Ticking `- [x]` in the plan dirties the working tree, which would then fail the clean-tree check the
gate just imposed. Resolution: when ladder steps 1–2 pass, the plugin ticks the current task's
checkbox **and includes the plan file in the `git add` command it hands the operator**. The tick
rides along inside the phase commit, so the tree ends clean.

The checkbox means "ready to ship". `git log` remains the source of truth — a stale tick left by an
abandoned phase is cosmetic, because the current phase is recomputed from git regardless.

---

## 4. Plan parsing contract

The parser reads only what `writing-plans` guarantees:

```markdown
### Task 4: Critic runner

**Files:**
- Create: `scripts/ccalib/runner.py`
- Modify: `scripts/critic_hook.py:12-40`
- Test: `tests/test_runner.py`
```

- **Phase number and title** from `### Task <N>: <title>`.
- **File list** from the `**Files:**` block: the backticked path on each `- Create:` / `- Modify:` /
  `- Test:` line, with any `:line-range` suffix stripped.
- A task with no `**Files:**` block contributes no scope and is never fenced against.
- Parsing is tolerant: an unrecognised line is skipped, never fatal.

**Produced shape:**

```python
{"number": 4, "title": "Critic runner",
 "files": ["scripts/ccalib/runner.py", "scripts/critic_hook.py", "tests/test_runner.py"],
 "checkbox_line": 812}
```

---

## 5. Gate criteria

| Check | How | Failure means |
|---|---|---|
| Tests | `.cca/config.json` → `phases.test_command`, run in the project root | block with the captured output |
| Critics | `feedback.open_items(root)` is empty | block listing the open items |
| Commit | `git log --format=%H %s` contains `[phase-N]` | tick checkbox, block with the commit command |
| Clean tree | no dirty path is owned by a **later** phase | block, naming each file and its owning phase |

**Where the clean-tree check runs (corrected after dogfooding).** The original design put it at
rung 4, *after* the commit. That rung is unreachable: `current` is by definition the lowest
**unshipped** phase, so `has_commit` is always false and `evaluate()` always returned at rung 3.
The check therefore never ran, and a repo containing every phase's work could be carved into
eleven tidy-looking commits without complaint — the exact failure it existed to prevent.

It now runs at rung 3, *before* the commit is offered, and asks a sharper question than "is the
tree clean": **is any uncommitted file owned by a later phase?** Unlisted files and fixes to
earlier phases do not block, so ordinary discovery still flows.

This also depends on `git status --porcelain -uall`. Without `-uall`, git collapses an untracked
directory to `dir/` and every file inside it becomes invisible to phase classification.

**The not-started rule** (added during implementation, after the first end-to-end run): if the
current phase has no commit **and the working tree is clean**, the phase has not been started and
there is nothing to ship, so the gate allows the stop. Without this, finishing phase 1 and stopping
for the day is blocked by phase 2's mere existence in the plan.

This also constrains when the checkbox may be ticked: the tick happens only on the blocking path,
so a phase that was never started is never ticked. Ticking on the allowed path would dirty the tree
and make the not-started rule permanently unreachable — the gate would create the condition that
makes it block.

If no `test_command` is configured the test check is skipped with a warning event rather than
failing — an unconfigured project must not be permanently blocked.

### Commit message the plugin suggests

```
[phase-3] Critic runner: parallel headless critics, timeout degrades to warn
```

`[phase-N]` is the tag the gate greps for. The text after it is the task title plus its one-line
deliverable. The operator is free to rewrite everything except the tag.

---

## 6. Failure modes — the fence must fail open

A false deny from the fence stops all work, which is strictly worse than a critic false positive.
Every uncertain path allows the edit.

| Situation | Behaviour |
|---|---|
| No plan file found | allow everything, gate inactive |
| Not a git repository | allow everything, gate inactive |
| Plan fails to parse | allow everything, one warning event |
| `git` missing, errors, or times out | allow everything |
| Current phase cannot be determined | allow everything |
| No `test_command` configured | skip the test check, warn |
| Commit tags lost to a rebase or squash | `.cca/config.json` → `phases.done: [1,2,3]` override |
| `stop_hook_active` is set | allow the stop; never loop |
| Hook itself raises | exit 0, allow |

**The governing rule:** a fence that cannot read the plan must never conclude "nothing is in scope".

---

## 7. Configuration

```json
{
  "phases": {
    "enabled": true,
    "plan": "docs/superpowers/plans/2026-09-14-cca-ensemble-plugin.md",
    "test_command": "python3 -m unittest discover tests",
    "done": []
  }
}
```

`"enabled": false` disables the whole gate while leaving the critic loop untouched. The critics are
disabled independently by emptying `critics`. Two features, one config file, no coupling.

---

## 8. Dashboard

A phase strip above the critic feed:

```
Phase 3/11 — Critic runner            tests ✅   critics ✅   commit ⬜   tree ⬜
shipped: 1 Event log (a1b2c3d) · 2 Verdict parsing (e4f5g6h)
```

Fed by new event types `phase_context`, `phase_fenced`, `phase_gate` appended to the same
`.cca/events.jsonl`.

---

## 9. Verification

Tests create **throwaway git repositories in temp directories** — the plugin's own repo is never
initialised, staged or committed by the test suite.

| Case | Expectation |
|---|---|
| Edit a current-phase file | allowed |
| Edit a later-phase file | denied, exit 2, reason names the owning phase |
| Edit an earlier-phase file | allowed |
| Edit an unlisted file | allowed, warning event emitted |
| Plan absent / unparseable / not a git repo | allowed in every case |
| Stop with failing tests | blocked at rung 1 |
| Stop with open CF.md items | blocked at rung 2 |
| Stop with no phase commit | blocked at rung 3, checkbox ticked, command suggested |
| Stop with dirty tree after commit | blocked at rung 4 |
| Stop with all four satisfied | allowed; current phase advances |
| `phases.done` override | listed phases treated as shipped |
| `enabled: false` | no fencing, no gating, critics unaffected |

---

## 10. Open questions

- ~~Whether the fence should also cover `Bash` heredoc writes~~ — **resolved.** The fence matches
  `Edit|Write|Bash`; `ccalib/bashwrite.py` extracts write targets (redirections including heredocs,
  `tee`, `cp`/`mv` destinations, `sed -i`, `touch`, `truncate`, `dd of=`) using `shlex` with
  `punctuation_chars`, so a `>` inside a quoted string is not mistaken for a redirection. Only write
  targets are fenced, never mere mentions of a path — `grep foo cli.py` stays allowed. `NotebookEdit` is covered — it carries `notebook_path` rather than
  `file_path`, and its schema requires an absolute path. Interpreter payloads are covered too, by
  **static analysis rather than execution**: Python `-c` strings and heredoc bodies are parsed with
  `ast`, `sh`/`bash -c` payloads recurse (depth-limited), and `node -e` is regex-scanned for the
  `fs.write*` family. The residual limit is dynamic paths (`open(name, "w")` with a computed
  `name`), which are undecidable without executing the program; the fence does not guess at them.
- Whether a phase whose tests live in a later phase (TDD across a phase boundary) needs an explicit
  `depends_on`, or whether the earlier-phase-allowed rule already covers it.
- Whether to detect squashed history automatically rather than relying on the `phases.done` escape
  hatch.
