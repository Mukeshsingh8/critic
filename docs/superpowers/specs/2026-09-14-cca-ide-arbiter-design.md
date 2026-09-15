# CCA IDE Arbiter — Design

*Date: 2026-09-14 · Status: approved, ready for implementation planning*

The arbiter's seat, moved into the editor. A VS Code extension renders the critic loop live, and
when the plugin reaches a decision that is the human's to make — a critic block, a phase commit —
Claude is held while the human answers in the IDE.

Companion to [`2026-09-14-cca-ensemble-plugin-design.md`](2026-09-14-cca-ensemble-plugin-design.md)
and [`2026-09-14-cca-phase-gate-design.md`](2026-09-14-cca-phase-gate-design.md). The critics judge
**whether a change is good**; the phase gate governs **how big a change may be**; this layer governs
**how the human is asked**. All three share one event log and one `.cca/` directory.

---

## 1. Goal

Make arbitration a decision the human is asked to take at the moment it matters, in the place they
are already looking, with one click — instead of a paragraph printed into a terminal after Claude
has already moved on. Show the loop as it runs: what is being reviewed, what the critics said, what
the gate is waiting for.

### Non-goals

- **Claude never commits.** The commit button runs `git` as the human's click. The plugin still
  only observes commits.
- **The extension never decides.** It renders a request and returns a choice. Every enforcement
  path stays in the Python hooks.
- Not required. With the extension absent the plugin behaves exactly as it does today.
- Not the ledger, not one-phase-per-session enforcement, not the intent gate, not Layer 0. Those
  are separate sub-projects (§13).

---

## 2. Verified environment facts

Established on 2026-09-14, macOS, Claude Code 2.1.261:

| Fact | Evidence |
|---|---|
| Hook stdin carries `session_id`, `cwd`, `tool_name`, `tool_input`, `stop_hook_active` | Already consumed by `critic_hook.py`, `phase_hook.py`, `drain_hook.py` |
| A `PostToolUse` hook may print `{"decision":"block","reason":…}` and Claude receives the reason | `critic_hook.py`, covered by `test_hooks.py` |
| Critics run in parallel with per-critic timeout ≤ 120s | `runner.run_all`, `config.DEFAULTS` |
| Hook timeouts are set by the plugin's own `hooks/hooks.json` | `PostToolUse` 300s, `Stop` 600s today |
| `CF.md` entry headings are the only lines starting with `## ` | Documented invariant in `feedback.py`; `open_items()` depends on it |
| The dashboard is a single self-contained HTML file that polls `events.jsonl` | `dashboard/index.html`; no build step |
| Node 22.21.1, npm 10.9.4 available; the `code` CLI is not on PATH | `node --version`, `npm --version`; irrelevant to F5 debugging and `vsce package` |

**Not yet exercised here** (from the VS Code extension API, to be confirmed in the first task):
`workspaceContains:<glob>` activation, `showWarningMessage(…, {modal: true}, …buttons)`,
`WorkspaceEdit` on a file that is not open in an editor.

---

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Bridge | File mailbox under `.cca/` | Same "no daemon, no sockets, no lifecycle" decision the dashboard already made; works in every VS Code fork; testable with a fake arbiter thread exactly like the fake `claude` binary |
| What is asked live | Critic **blocks** and the phase **commit** | Both are already human decisions in today's design — today they are just asked badly. Queued findings stay silent-to-`CF.md`; that is what non-blocking means |
| Default on silence | Today's behaviour | The bridge can only *relax* a gate when a human acts, never by inaction (§10) |
| Presence | Heartbeat file | The hook must not wait three minutes for an arbiter who is not there |
| Vocabulary | The pending file carries its own `options` | The plugin owns the words; the extension renders buttons from data and never grows a second copy of the rules |
| Commit command | Emitted in the `phase_gate` event payload | The TypeScript side must never re-parse plans. Two lines of Python instead of a second `planparse` |
| Extension logic | Pure modules + thin VS Code adapters | `protocol.ts`, `cfmd.ts`, `eventlog.ts` are unit-tested with no editor; the API surface is one file |
| Host portability | No VS Code-proprietary APIs | One `.vsix` for Marketplace and Open VSX (Cursor, Windsurf, Antigravity) |

---

## 4. Architecture

```
extension ── every 5s ──► .cca/arbiter.json                              presence
                          { pid, ts, host, version }

PostToolUse (critics voted block)
   critic_hook ── arbiter.present()? ──no──► block, exactly as today
                       │yes
                       ├─► .cca/pending/<id>.json  { kind: "block", options: [uphold, queue, overrule], … }
                       ├─► extension: modal + webview strip + status bar
                       ├─► human clicks ──► .cca/decisions/<id>.json  { choice, note }
                       └─► hook polls 250ms ──► acts on choice ──► `arbitration` event ──► delete both

Stop (gate ladder reaches rung "commit")
   drain_hook ── arbiter.present()? ──no──► block with the command, exactly as today
                       │yes
                       ├─► .cca/pending/<id>.json  { kind: "commit", options: [commit, edit, later], command, phase, … }
                       ├─► human clicks Commit ──► extension runs git ──► { choice: "commit", ok, error }
                       └─► hook re-runs gate.evaluate ──► pass, or block with the remaining rung's reason
```

The hooks are the lock. The extension is a view with buttons. Killing the extension mid-wait
returns the plugin to today's behaviour at the next deadline.

---

## 5. Protocol contract

All writes are **tmp-then-rename** in the same directory, so a reader never sees a partial file.

### Heartbeat — `.cca/arbiter.json`

```json
{"pid": 48213, "ts": 1757880000.12, "host": "vscode", "version": "0.1.0"}
```

Written at activation and every 5s; deleted on deactivation. **Present** means
`now − ts < arbiter.stale_after` (default 15s). A crashed extension is absent within 15s.

### Request — `.cca/pending/<id>.json`

`id` is `<epoch_ms>-<session_id[:8]>-<4 random hex>`. Common fields:

```json
{"id": "…", "kind": "block", "session_id": "…", "created": 1757880000.5,
 "deadline": 1757880180.5, "options": ["uphold", "queue", "overrule"]}
```

`kind: "block"` adds `file`, `reason` (the formatted findings, as printed today) and `findings`
(the surviving list, for the tree view).

`kind: "commit"` uses `options: ["commit", "edit", "later"]` and adds `phase`
(`{number, title, total, files}`), `plan_rel`, `command` (the display string), `message` (the
default `[phase-N] Title`) and `checks`.

### Decision — `.cca/decisions/<id>.json`

```json
{"id": "…", "choice": "overrule", "note": "false positive: the helper is test-only", "ts": 1757880042.0}
```

For `choice: "commit"` the extension adds `"ok": true|false` and, on failure, `"error"` (git's
stderr). `choice` **must** be one of the request's `options`; anything else is malformed and
ignored (§10).

### Lifecycle

1. Hook sweeps: `pending/*.json` past `deadline + 60s` and `decisions/*.json` with no matching
   pending and mtime older than 60s are deleted. Crash leftovers never accumulate.
2. Hook writes the request, appends `arbiter_pending`.
3. Hook polls every 250ms until a well-formed decision appears or `deadline` passes.
4. Hook acts, appends `arbitration` `{id, kind, choice, note, waited, timed_out, present}`,
   deletes both files. The extension never deletes plugin files; it only hides requests past their
   deadline.

---

## 6. The two decisions

### Block — `PostToolUse`, critics voted block

| Button | Hook output |
|---|---|
| **Uphold** | `{"decision": "block", "reason": …}` — today's path |
| **Queue instead** | findings appended to `CF.md`; `additionalContext` "queued N findings" — today's queue path |
| **Overrule** (+ optional note) | nothing; the edit stands. Note recorded in the `arbitration` event |
| *silence / absent / malformed* | **Uphold** |

### Commit — `Stop`, gate rung `commit`

| Button | Extension | Hook |
|---|---|---|
| **Commit** | `git add -- <phase.files…> <plan_rel>` then `git commit -m <message>` via `execFile` in the project root — no shell, no quoting; writes `{ok, error}` | on `ok`, re-runs `gate.evaluate` with fresh `git` state: tree clean → allow the stop; otherwise block with that rung's reason. On `!ok`, block with git's error as the reason |
| **Edit message…** | input box prefilled with `message`; then as **Commit** | as above |
| **Later** | writes the decision | block with the command — today's path |
| *silence / absent / malformed* | — | **Later** |

The commit is the human's. The extension runs what the human clicked, with the exact files the
gate named, and nothing else. The checkbox tick (`gate.tick_steps`) happens before the request is
written, as today, so the plan file is in the `add` and the tree ends clean.

---

## 7. Time budgets

| Hook | `hooks.json` timeout | Consumed before the question | Wait default |
|---|---|---|---|
| `PostToolUse` | 300 → **600** | critics, ≤ 120s in parallel | `arbiter.block_wait` = 180 |
| `Stop` | 600 → **900** | tests, ≤ 600s (`gate._TEST_TIMEOUT`) | `arbiter.commit_wait` = 300 |

`arbiter.py` carries the shipped hook budgets as constants and clamps every wait to
`budget − elapsed − 10`, so a misconfigured `block_wait` cannot push the hook past Claude Code's
timeout and lose the decision.

---

## 8. Python changes

| File | Change |
|---|---|
| `scripts/ccalib/arbiter.py` (**new**) | `present(root, cfg)`, `sweep(root)`, `request(root, cfg, kind, payload, budget_left)` → decision dict or `None`. Owns file lifecycle and the two events |
| `scripts/critic_hook.py` | on `block`: `arbiter.request(…)`; branch on `uphold` / `queue` / `overrule` / `None` |
| `scripts/drain_hook.py` | `command` and `message` added to the `phase_gate` payload; on rung `commit`: request; on `commit ok`: re-evaluate |
| `scripts/ccalib/events.py` | `arbiter_pending`, `arbitration` |
| `scripts/ccalib/config.py` | `arbiter` defaults (§11) |
| `hooks/hooks.json` | timeouts per §7 |

Every new path degrades to today's behaviour on any exception, in keeping with the hooks' outer
`except Exception: exit 0`.

---

## 9. Extension — `extension/` in this repository

TypeScript, esbuild → `dist/extension.js`, no UI framework, no `any`. The webview is today's
`dashboard/index.html` with `poll()` replaced by a `message` listener.

| Component | Responsibility |
|---|---|
| `extension.ts` | activation (`workspaceContains:.cca` + command `CCA: Open Dashboard`), root = first workspace folder containing `.cca/` — or, when activated by the command with none present, the first folder, creating `.cca/`; wires the adapters below, owns disposables |
| `heartbeat.ts` | writes `arbiter.json` every 5s; deletes it on deactivate |
| `eventlog.ts` (pure) | incremental tail by byte offset; detects truncation and restarts from 0; skips corrupt lines; yields typed events |
| `protocol.ts` (pure) | request/decision schemas, validation, atomic write, expiry |
| `cfmd.ts` (pure) | parse `## ` entries into `{heading, line, wontfix, findings[]}`; produce the text edits for *Overrule* (delete the heading block) and *Wontfix* (insert `[wontfix] <reason>` into the heading) |
| `dashboard.ts` | `WebviewPanel`; forwards events; renders a *Pending decisions* strip above the feed |
| `decisions.ts` | watches `pending/`; per request: modal `showWarningMessage` with buttons from `options`, status-bar item `⏳`, webview strip; on click writes the decision; for `commit` runs git first |
| `cfTree.ts` | `TreeDataProvider` — *Open* / *Wontfix* sections; item actions *Open at line*, *Overrule*, *Wontfix…*, applied via `WorkspaceEdit` so they are undoable |
| `statusBar.ts` | `CCA · phase 3/11 · 2 open · ⏳` → opens the dashboard |
| `nextSession.ts` | *Start next phase* button on the toast `decisions.ts` shows when a `commit` decision returns `ok: true`: opens a new terminal running `claude` |

The VS Code API is imported only in `extension.ts`, `dashboard.ts`, `decisions.ts`, `cfTree.ts`,
`statusBar.ts`, `nextSession.ts`. Everything under test runs without an editor.

---

## 10. Failure modes — the bridge fails to today

| Situation | Behaviour |
|---|---|
| Extension not installed or not running | heartbeat absent → no wait → today's output |
| Extension crashed mid-wait | heartbeat stale within 15s; hook still honours its deadline, then today's output |
| Hook killed by Claude Code's timeout | pending file left behind; swept on the next hook run; extension hides it at its deadline |
| Decision malformed or `choice` not in `options` | ignored; polling continues to the deadline |
| Two decisions for one `id` | first well-formed one wins; the hook reads once |
| Decision written after the hook gave up | orphan; swept 60s later |
| `git add`/`commit` fails | `ok: false`, `error` → hook blocks with git's stderr as the reason |
| Two Claude sessions in one project | two requests, each tagged `session_id`; both shown |
| Webview closed | modals and status bar still fire; the webview is a view, not the channel |
| `CF.md` edited by hand while the tree is open | tree re-parses on change; edits target headings by text, not by line number |
| Any exception in `arbiter.py` | caught, `None` returned → today's output |

**The governing rule:** silence, absence and error all mean "as if the extension did not exist".

---

## 11. Configuration

```json
{
  "arbiter": {
    "enabled": true,
    "block_wait": 180,
    "commit_wait": 300,
    "stale_after": 15
  }
}
```

`"enabled": false` disables the bridge; critics and the phase gate are untouched. Three features,
one config file, no coupling.

---

## 12. Verification

**Python** — `tests/test_arbiter.py` plus additions to `test_hooks.py` and `test_gate_hooks.py`.
A fake arbiter is a thread that writes a decision file after N ms; a fake `claude` on `PATH`
supplies the block. Zero model calls, throwaway git repos in temp dirs.

| Case | Expectation |
|---|---|
| No heartbeat | no pending file written; today's block output; `arbitration.present == false` |
| Stale heartbeat (> `stale_after`) | as above |
| Uphold | block output identical to today |
| Queue | `CF.md` gains the findings; `additionalContext` emitted |
| Overrule with note | no output; `arbitration` carries the note; both files deleted |
| Timeout | default applied; `timed_out == true` |
| Malformed decision, then a valid one | valid one wins |
| Stale pending / orphan decision present at start | swept |
| Commit ok, tree clean | stop allowed |
| Commit ok, later-phase file dirty | blocked at the tree rung with its reason |
| Commit failed | blocked; reason contains the git error |
| Later | today's commit-command block |
| `arbiter.enabled: false` | no heartbeat read, no request, today's output |
| Wait clamped to hook budget | `request()` returns before `budget_left − 10` |

**TypeScript** — vitest on the pure modules.

| Module | Cases |
|---|---|
| `eventlog.ts` | append-only tail; truncation restarts; corrupt line skipped; partial trailing line held until complete |
| `protocol.ts` | round-trip; rejects unknown `choice`; expiry; atomic write leaves no tmp on success |
| `cfmd.ts` | parses header + N entries; *Overrule* removes exactly one heading block; *Wontfix* tags exactly one heading; wontfix entries excluded from *Open* |

**Smoke** — F5 in VS Code against `test-project/` with the fake `claude` on `PATH`: a block appears
as a modal within a second, *Overrule* lets the edit stand, the gate's *Commit* produces a
`[phase-N]` commit in a throwaway repo. No model calls.

---

## 13. Deferred — the other sub-projects

In the order agreed on 2026-09-14:

2. **Ledger + one-phase-per-session.** A per-phase completion record (tests, findings, overrules
   with notes, wontfixes, duration) committed inside the `[phase-N]` commit and read at
   `SessionStart`; the fence denies every write once the session's phase has shipped. The
   `arbitration` events this design emits are the ledger's raw material.
3. **Intent gate.** Before the first write of a phase, Claude writes `.cca/intent.md`; a critic
   checks it against the task and the human approves it through this bridge before the fence
   opens. One model call per phase, not per edit.
4. **Layer 0 + security critic.** Deterministic pre-pass (`semgrep`, `bandit`, `jscpd`) feeding
   `vote.decide(verdicts, layer0)`, plus `critics/security.md` for logic-level findings.

---

## 14. Open questions

- Whether an *Overrule* should also write a `[wontfix]` entry into `CF.md` so the rationale is
  greppable in the repo rather than only in `events.jsonl`. Deferred to the ledger; the note is
  preserved in the event either way.
- Whether *queue* votes deserve a non-modal live surface (toast with *Dismiss*) once the block flow
  has been used for a while.
- Whether the heartbeat should carry the human's idle state, so a present editor with an absent
  human falls back sooner than the full wait.

---

## 15. Implementation notes

Written after the first implementation, 2026-09-14. The other two specs in this
repository carry the same kind of after-the-fact correction; it is what stops a
spec from drifting quietly away from the code.

### Confirmed as designed

- The mailbox works. The cross-language contract is covered by
  `extension/test/integration.test.ts`: the **real** Python hooks talking to the
  **real** TypeScript protocol code over the real files, with no editor and no
  model calls. Eight cases, including the two that matter most — no extension
  present, and heartbeat stale.
- Failing tests and open `CF.md` items never reach the human: the ladder stops
  above the question, as §6 requires.
- `gate.evaluate(..., skip_tests=True)` was needed exactly as anticipated. A
  test proves the command runs once per Stop, not twice.

### Changed during implementation

1. **Temp files must not end in `.json`.** Both writers originally produced
   `.tmp-<pid>-<name>.json`, which `sweep()` enumerates. A half-written temp
   file would have read as a corrupt request and been deleted mid-write. Both
   `arbiter.write_json` and `fsatomic.ts` now append `.tmp`.
2. **`_record` takes `timed_out` explicitly.** Deriving it from
   `decision is None` mislabelled the zero-budget case — where nobody was ever
   asked — as a timeout.
3. **`config.load` merged shallowly.** Pre-existing, found while adding the
   `arbiter` block: `{"arbiter": {"block_wait": 60}}` deleted the other three
   defaults, and the same was already true of `phases`. Now a one-level-deep
   merge; lists still replace wholesale, which is correct for the critic roster.
4. **The tree's node union had to be discriminated.** TypeScript cannot subtract
   a non-discriminated member from a union, so an `in`-based guard narrowed only
   the positive branch and `if (isGroup(node)) return;` left the whole union.
5. **Three new modules the design did not name:** `fsatomic.ts` (the atomic
   write, shared by heartbeat and decisions), `feed.ts` (one event-log reader
   for the whole extension), `git.ts` (`execFile`, never a shell).

### Contradicted

- §12's row **"commit ok, later-phase file dirty"** is unreachable and has no
  test. A later-phase file dirty *before* the commit stops the ladder at the
  tree rung, so the human is never asked, and nothing can make a file appear
  between the commit and the re-evaluation. What is reachable is leftover
  *unlisted* work, which blocks with the next phase's command; that is tested
  instead.

### The three previously unexercised VS Code APIs

`workspaceContains:.cca` activation, `showWarningMessage(..., {modal: true})`
and `WorkspaceEdit` on an unopened file are written and typecheck cleanly, but
**none of them has been exercised at runtime.** They need an Extension
Development Host and a human; the smoke list in the plan's Task 13 is the
procedure. Until someone runs it, treat the editor surface as unverified.

### Measured

| Suite | Count | Time |
|---|---|---|
| Python (`python3 -m unittest discover tests`) | 321 | ~22s |
| TypeScript unit (`vitest`, pure modules) | 28 | <1s |
| TypeScript integration (real hooks, real protocol) | 8 | ~23s |
| `tsc --noEmit` | clean | — |
| `npm audit` | 0 vulnerabilities | — |
| `cca-arbiter-0.1.0.vsix` | 7 files, 12.3 KB | — |


---

## 16. The board — a second pass (2026-09-15)

The webview shipped with the original dashboard's markup. It was replaced with a themed board
("CCA Precinct") on request. Three things are worth recording because they were defects, not taste.

1. **The two boards had already diverged.** Extracting `media/` in Task 11 left two copies of the
   render logic. `dashboard/` is now the single source: `dashboard.js` detects `acquireVsCodeApi`
   and chooses postMessage or polling, and the extension's `build` script copies it into `media/`.
   Skinned once.
2. **The standalone server served a stale board.** `dashboard.py` copied the assets into `.cca/` at
   startup, so any edit to the dashboard was invisible until a restart — which looked exactly like
   the edit having no effect. The handler now serves `index.html`, `dashboard.css` and
   `dashboard.js` live from the plugin's `dashboard/`, and sends `Cache-Control: no-store`; only
   `events.jsonl` still comes from `.cca/`.
3. **No external resources, by force.** The webview CSP is `default-src 'none'`, so there are no
   webfonts and no image assets. The figures are pixel grids drawn to a canvas and the wordmark's
   badge is a sprite — which is also why the type carries its character through weight, tracking
   and a hard shadow rather than through a typeface.

Reviewed in a real browser at 1180px and at 430px, in both colour schemes. Two fixes came out of
looking rather than reasoning: the light bars at full strength swallowed the wordmark on white (now
`--siren-strength`, .7 dark / .3 light), and every version of a light pool under the street lamp
read as a grey slab over the hatching, so it was cut entirely.


---

## 17. The case file (2026-09-15)

The board showed verdicts. It did not show the change being judged, nor the remedy, so it could tell
you that something was wrong without telling you what had been done or what to do. Three columns
now answer those in order: what Claude did, what the critics said, what to do next.

Two were data problems rather than layout problems.

- **`suggestion` was captured and discarded.** Every critic brief already requires it and
  `verdict.py` already preserves it through the evidence bar; only the board dropped it. It is the
  single field that turns a complaint into a next step, and it is now the third column, deduplicated
  across critics because both often land on the same remedy.
- **`edit_started` recorded `{file, tool}` and nothing else.** New `ccalib/change.py` produces a
  bounded summary (`added`, `removed`, `excerpt`, `truncated`) using stdlib `difflib`, capped at 16
  rows of 160 characters. The cap is the whole design constraint: this is written on *every* edit
  into an append-only log, so an unbounded excerpt would grow without limit. Counts are of the whole
  change even when the excerpt is truncated, so `+500` stays honest.

One correlation bug worth recording: the case first selected findings by matching the file name,
which pulled in every finding ever logged against that path, so a second edit to the same file
displayed the first edit's complaints alongside its own. It is now scoped to the verdicts that fall
between the edit and its vote.

`dashboard.py` was independently hardened during this pass (serving via `directory=` rather than a
process-global `chdir`, `allow_reuse_address`, and a readable message instead of a raw `OSError` on
a port clash), with tests in `TestServeRobustness`.

Measured after this pass: **342 Python tests**, **36 TypeScript tests**, `tsc --noEmit` clean.


---

## 18. Accepting a fix (2026-09-15)

The case file could say what to do next but not do it. Each remedy now carries a button that queues
it for the coder.

**The queue** is `.cca/fixes.json`, written by `ccalib/fixes.py` and by `extension/src/fixes.ts`.
Identity is `"file|line|suggestion"` (`fix_key`) hashed to twelve hex characters (`fix_id`), so
accepting the same remedy twice is one item whichever host queued it. The two implementations are
pinned against each other by tests that shell out to the real Python, including a unicode case:
a queue written by one side must be readable and extendable by the other.

**Delivery is three paths, because no single one covers every state a session can be in.**

| Path | Covers |
|---|---|
| `Stop` hook, above the CF.md rung | Claude trying to finish. This is the one that makes it happen — the session cannot end with a fix outstanding. |
| `UserPromptSubmit` hook (new `scripts/prompt_hook.py`) | a session idle at the prompt; the queue rides along with the next message |
| extension terminal nudge | immediacy when a terminal is already running Claude — best effort, never the guarantee |

It sits above CF.md deliberately: CF.md is a queue of findings still to be arbitrated, whereas an
accepted fix is a decision the operator has already made.

**Resolution is by evidence.** A fix closes when a later edit to its file draws a `pass` vote. That
is the only signal in the system that honestly indicates the remedy landed; marking it done on
dispatch would have claimed work that may never have happened. The operator can also dismiss one,
which closes it *without* claiming it was done — a distinct state for exactly that reason.

**The board writes, for the first time.** The standalone server grew a single `POST /fixes`
endpoint (plus `/fixes/dismiss`); it is bound to 127.0.0.1, accepts nothing else, and touches only
the queue. In the webview the same click goes through `postMessage` to the extension.

One design rule was broken and corrected during this pass: `fixes.ts` initially imported `vscode`
for the terminal nudge, which made the whole queue module untestable under vitest. The nudge moved
to `nextSession.ts`, which already owned terminal interaction, and `fixes.ts` went back to being
pure Node.

Measured: **364 Python tests**, **44 TypeScript tests**, `tsc --noEmit` clean.


---

## 19. Immediate, and refusable (2026-09-15)

Two corrections after the operator used §18.

**"Queued" was the wrong behaviour, not the wrong word.** Clicking a fix only took effect at the
next Stop, and the expectation — reasonably — was that the session pauses and the fix happens before
anything else. The machinery for that already existed and was not being used: when the critics
block, `critic_hook` is *still holding Claude* on the arbiter mailbox. `block` requests now offer a
fourth option, `fix`, and answering with it makes the accepted remedies the block reason. Claude
carries them out in that same turn instead of at the next Stop. The board reads
`arbiter_pending`/`arbitration` out of the event log to know whether a request is genuinely live,
and says "Claude is stopped, waiting on you" only when it is — so "queued" can never again be
misread as "now".

**The arbiter could accept a finding but not refuse one.** Skip is now a first-class action,
recorded as `dismissed` rather than removed. The distinction from `done` is the point: a skipped
finding was judged not worth fixing, and the log must never let that be mistaken for work that was
carried out. When every finding on a live block is skipped, the send action becomes *Let the edit
stand* — which is the existing `overrule`, reached through triage rather than as a blanket verdict.

The dashboard server grew `POST /fixes/skip` and `POST /answer`; the webview reaches the same two
through `postMessage`. `DecisionWatcher.answer()` claims the request so the modal cannot also fire
for something the board already settled.

Measured: **370 Python tests**, **44 TypeScript tests**, `tsc --noEmit` clean.


---

## 20. Watching the fix land (2026-09-15)

Sending a fix left a dead zone: the step said "handed to Claude" and then nothing happened visibly
until it was all over. The operator asked to see Claude working, and to see the critics sign off
before it moved on. Both were already in the event stream and simply unrendered.

`fixProgress()` derives five stages from `fix_dispatched`, `edit_started`, `critic_dispatched`,
`critic_verdict` and `vote` — waiting, working, reviewing, cleared, rejected — and reports each
officer separately (`✔ DESIGN`, `… CORRECTNESS`), because "both critics are convinced" is a
statement about two independent verdicts, not one aggregate.

**Ordering moved from timestamps to log position.** Staging the demo surfaced it: a vote written
with a timestamp slightly ahead of a later dispatch made a freshly-sent fix render as already
rejected. The log is append-only, so an event's index is a stronger ordering than its clock, and
"did this happen after the dispatch" is the question every stage depends on. `digest()` now carries
`at` (the index) on each record and the derivation compares those.

The only motion added is a single pulsing dot while work is genuinely in flight, disabled under
`prefers-reduced-motion`.

Measured: **374 Python tests**, **44 TypeScript tests**, `tsc --noEmit` clean.


---

## 21. The rap sheet earns its place (2026-09-15)

It was the weakest panel: history with no state. A finding you fixed, one you skipped and one still
open rendered identically, and two critics agreeing printed as two defects.

**Merged per defect, not per critic.** Rows combine on `file + line + issue`, keeping the highest
severity and the first available evidence and suggestion. Corroboration is then something the sheet
can state — *"both officers agreed"* — instead of something the reader has to infer from a
duplicate. Exact-issue matching is deliberate: two critics wording an observation differently have
made two observations, and merging those would hide one.

**Every row carries state**, resolved from the fix queue by the same `file|line|suggestion` key the
rest of the system uses: open, queued, fixed, skipped, superseded.

`superseded` is the one that needed care. A file passing review later means the finding no longer
describes the code — but nobody acted on *this* finding, so calling it fixed would claim work that
was never done. It is a distinct state for the same reason `dismissed` is distinct from `done`:
the log must never let a judgement look like an action.

Open rows sit above a collapsed disclosure of the closed ones. That disclosure has to remember it
is open, because the board repaints every 500ms and without it the group snapped shut while being
read — found by opening it and waiting rather than by opening it and screenshotting.

Measured: **380 Python tests**, **44 TypeScript tests**, `tsc --noEmit` clean.
