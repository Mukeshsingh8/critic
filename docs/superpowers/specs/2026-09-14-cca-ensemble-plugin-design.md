# CCA Ensemble Plugin — Design

*Date: 2026-09-14 · Status: approved, ready for implementation planning*

A Claude Code plugin implementing **Coder / Critic / Arbiter** entirely inside Claude. The main
session writes code; two fresh Claude instances on different models review every edit; the human
arbitrates. No external model APIs, no API keys.

Derived from [`cca-ensemble-claude-code-plan.md`](../../../cca-ensemble-claude-code-plan.md), which
analysed Ultra/TIDE's "Ensemble mode". This spec supersedes that document's Level 3 sketch.

---

## 1. Goal

Reproduce Ultra's CCA loop, adapted to two constraints the original did not have:

1. **All-Claude.** Independence comes from different *briefs* and different *models*, not different
   vendors.
2. **The critics exist primarily to stop AI slop**, not just to find bugs. Reuse over reinvention,
   correct decomposition, complexity appropriate to the data, schema and migration safety, and
   adherence to the project's own documented conventions.

### Non-goals

- Replacing the human arbiter with an automated tie-breaker.
- Reviewing anything the deterministic tooling already proves (typecheck, lint, duplicate blocks).
- Supporting non-Claude critics. The critic invocation is a single function; adding one later is a
  local change, not an architectural one.

---

## 2. Verified environment facts

Established empirically on 2026-09-14, macOS, Claude Code 2.1.261:

| Fact | Evidence |
|---|---|
| `claude -p --model <id>` runs headless and returns text | `claude -p --model claude-opus-5 'Reply with exactly: OPUS_OK'` → `OPUS_OK` |
| A second model works the same way | `claude -p --model claude-fable-5-1 ...` → `FABLE_OK` |
| Auth needs no API key | Uses the existing Teams-plan subscription |
| `claude -p` restricts tools via `--allowedTools` / `--disallowedTools` | `claude --help`; agent `tools:` frontmatter is a *different* mechanism a headless session never reads |
| Critic briefs are injected with `--system-prompt` / `--append-system-prompt` | `claude --help` |
| `type: "command"` is the stable hook path | Every installed plugin (superpowers, ECC, dash0) uses only `command` hooks; `agent`/`prompt` types are experimental and unused |
| Codex and Gemini CLIs are **not** installed | `~/.codex` is the ChatGPT desktop app, `~/.gemini` is Antigravity; neither binary resolves on PATH |

### Model costs (from the claude-api skill, cached 2026-06-24)

| Model | ID | Input $/1M | Output $/1M |
|---|---|---|---|
| Claude Fable 5.1 | `claude-fable-5-1` | $10.00 | $50.00 |
| Claude Opus 5 | `claude-opus-5` | $5.00 | $25.00 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | $1.00 | $5.00 |

Fable 5.1 is the most expensive model available and has always-on thinking, making it both the
costliest and the slowest critic. At ~5K in / 1–4K out per critic call, a per-edit review costs
roughly **$0.05 (Opus) + $0.10–0.25 (Fable)**; a 30-edit task is **$5–12 equivalent**. Billing is
against the Teams plan rather than per-token, but the ratio governs how fast plan usage is consumed.

**Consequence for the design:** the critic roster is a config value, not a constant. Swapping the
correctness seat from Fable to Haiku must be a one-line change.

---

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Visibility | Separate live dashboard | Watching the loop is a primary requirement, not a nicety |
| Trigger | Every edit | Faithful to Ultra; accepted knowing the cost |
| Critic independence | Different jobs | Strongest substitute for cross-vendor diversity when both critics are Claude |
| Arbitration | Supervise, don't gate | Vote rule decides automatically; human intervenes by editing `CF.md` or interrupting |
| Location | New repo at `~/Documents/ChatGPT/CCA`, exercised against a scratch project | Keeps early buggy hooks away from real work; planted defects make critic quality measurable |
| State model | Append-only event log | Doubles as the tuning instrument for judging whether each critic earns its cost |

---

## 4. Architecture

```
┌─ main session (CODER, Opus) ───────────────────────────────┐
│  Edit / Write                                               │
└───────────────┬─────────────────────────────────────────────┘
                ▼
        PostToolUse hook — matcher: Edit|Write
                │
                ├─ guard: CCA_INNER=1 set?        → exit 0   (recursion firewall)
                ├─ guard: path in ignore globs?   → exit 0
                │
                ├─ LAYER 0 (deterministic, ~1s, free)
                │     typecheck · lint · duplicate-block scan · dead-export scan
                │
                ├─ LAYER 1 (parallel, every edit)
                │     CCA_INNER=1 claude -p --model claude-opus-5      → Reuse & Design
                │     CCA_INNER=1 claude -p --model claude-fable-5-1   → Correctness & Complexity
                │
                ├─ LAYER 2 (path-triggered only)
                │     migrations/ schema/ entity files → Schema & Migration critic
                │
                ├─ append events ──────────────────────► .cca/events.jsonl
                │
                └─ VOTE
                     typecheck error, both fail, or any critical → {"decision":"block"}
                     one fail, or any warn                       → append CF.md + additionalContext
                     all pass                                    → exit 0, silent

        Stop hook — drain.py
                └─ CF.md has open items && !stop_hook_active → block with the open list

        dashboard/index.html — polls .cca/events.jsonl every 500ms, rendered in the Browser pane
```

### Three load-bearing details

1. **The recursion firewall is the first line of `critic.py`.** Critics are `claude -p` processes
   running inside the project; without `CCA_INNER=1` they load the same hooks and spawn critics of
   their own. This is the highest-risk detail in the build and must exist from the first commit.
2. **Critics are launched with `--allowedTools Read Grep Glob`** — no `Edit`, no `Write`. "The
   coder is the only model that touches files" becomes structural, enforced by the harness rather
   than by a prompt instruction the critic could talk itself out of.
3. **A timeout counts as `warn`, never `fail`.** A slow critic must not be able to block an edit.
   This is what makes the every-edit trigger survivable.

---

## 5. Repository layout

```
cca/
├── .claude-plugin/plugin.json
├── hooks/hooks.json                    # PostToolUse: Edit|Write → critic.py ; Stop → drain.py
├── scripts/
│   ├── critic.py                       # guards, layer 0, fan-out, parse, vote, emit
│   ├── drain.py                        # CF.md gate
│   ├── verdict.py                      # contract parsing + evidence-bar enforcement
│   ├── events.py                       # append-only event log writer
│   └── dashboard.py                    # static server wrapper
├── critics/                            # briefs, passed via --system-prompt
│   ├── design.md                       # → claude-opus-5
│   ├── correctness.md                  # → claude-fable-5-1
│   └── schema.md                       # → claude-opus-5
├── dashboard/index.html
├── skills/cca/SKILL.md
└── test-project/                       # scratch repo with planted defects
```

Per-project runtime state, gitignored, created on demand:

- `.cca/events.jsonl` — append-only event log
- `.cca/config.json` — critic roster, timeouts, ignore globs, critical paths
- `CF.md` — the critic feedback queue and arbitration surface

---

## 6. The critic roster

### Layer 0 — deterministic pre-pass

Every edit, ~1s, no model calls: typecheck, lint, duplicate-block scan (`jscpd` or equivalent),
dead-export scan (`knip` / `ts-prune`). Findings enter the same vote as a critic's. **Anything a tool
can prove, no model pays for.**

### Layer 1 — standing critics (every edit, parallel)

| | **Opus — Reuse & Design** | **Fable — Correctness & Complexity** |
|---|---|---|
| Question | Does this already exist? | Is this wrong? |
| Checks | Reinvention of existing symbols, types, utilities · decomposition (one unit doing several jobs) · unnecessary addition and scope creep beyond the spec · violations of the project's `CLAUDE.md` | Bugs and edge cases · error paths · complexity wrong *for the expected data size* (N+1s, repeated passes, unbounded growth) · concurrency and ordering |
| Primary tool | **Grep** — must search before claiming reinvention | Read + the diff |

### Layer 2 — path-triggered specialist

**Schema & Migration critic**, firing only on `**/migrations/**`, `**/schema/**`, and entity/model
files. Dev-to-production drift is a rare, catastrophic, structurally different failure mode.
Reviews backwards compatibility with currently-deployed code, behaviour of defaults against
*existing* rows, reversibility, lock duration on large tables, and the project's house rules
(`text` over `varchar`, enums over free strings, soft delete, no `ON DELETE CASCADE`).

### Calibration rules baked into every brief

- **Complexity** is judged against expected data size, never theoretical optimality. O(n²) over a
  five-item config is correct code.
- **DRY** does not apply to blocks that merely look alike; two things that change for different
  reasons stay separate.
- Critics **read the project's `CLAUDE.md`** and enforce the project's documented standards rather
  than generic best practices they invent.

---

## 7. Contracts

### Critic invocation

```bash
CCA_INNER=1 claude -p \
  --model "$MODEL" \
  --system-prompt-file "critics/$BRIEF.md" \
  --allowedTools Read Grep Glob \
  --permission-mode dontAsk \
  "$CHANGE_PAYLOAD"
```

`CCA_INNER=1` is the recursion firewall; `--allowedTools` is what makes a critic structurally
incapable of writing; `dontAsk` stops a headless critic hanging on a permission prompt (safe
precisely because the allow-list is read-only). The payload carries the change, the task spec, and the project's `CLAUDE.md`.

### Verdict (returned by each critic; JSON only, parsed defensively)

```json
{
  "verdict": "pass | warn | fail",
  "findings": [{
    "severity": "critical | major | minor",
    "kind": "reuse | correctness | complexity | decomposition | scope | convention | schema",
    "file": "src/lib/pricing.ts",
    "line": 42,
    "issue": "Reimplements currency formatting",
    "evidence": "lib/format.ts:12 already exports formatCurrency() with the same signature",
    "suggestion": "Import formatCurrency from lib/format"
  }]
}
```

### The evidence bar (enforced in `verdict.py`, not in the prompt)

A brief like "find reuse violations and slop" is infinitely satisfiable; an unconstrained critic
flags every edit and produces the ping-pong the source plan warns about. Enforcement in code:

- `reuse` and `complexity` findings **require** `evidence`. Missing → downgraded to `warn`, cannot block.
- A `complexity` finding must state the data size it is wrong for. "Could be faster" → dropped.
- Hedging language (*consider*, *might want to*, *would be cleaner*) → dropped at parse time.
- Restyling and preference findings → forbidden by brief, dropped by kind.

### Vote

| Condition | Outcome |
|---|---|
| Typecheck or lint error (layer 0) | **block** — a fact, not an opinion |
| Both standing critics `fail`, or any `critical` | **block** — fix now |
| Exactly one `fail`, or any `warn` | **queue** to `CF.md`, coder continues |
| All `pass` | silent, exit 0 |

### Event log (`.cca/events.jsonl`, one JSON object per line)

`edit_started` · `layer0_result` · `critic_dispatched` · `critic_verdict` · `vote` · `queued` ·
`blocked` · `drain_blocked` · `session_end`. Each carries a timestamp, the file path, and the
originating hook event.

---

## 8. Failure modes — the system fails open

A critic that can brick the session is worse than no critic.

| Failure | Behaviour |
|---|---|
| Critic exceeds timeout | counts as `warn`, logged — a slow critic can never block an edit |
| Critic returns unparseable output | `warn` + raw output appended to `CF.md` |
| `claude -p` missing or auth expired | exit 0, warn once on stderr |
| Both critics fail to launch | exit 0, degraded banner on dashboard |
| `critic.py` itself throws | exit 0 via top-level trap |
| `CF.md` deleted mid-session | treated as empty — the arbiter's prerogative |
| `Stop` hook loop risk | honour `stop_hook_active`; Claude Code's 8-block cap is the backstop |

---

## 9. Dashboard

Static page polling `.cca/events.jsonl` every 500ms, served by a plain static server and displayed
in the Browser pane beside the session. No daemon, no sockets, no process lifecycle.

Renders:

- **Header** — coder model, critic models and their briefs, arbiter seat (mirrors Ultra's cast line)
- **Live timeline** — edits and critic verdicts as they arrive
- **Findings** — grouped by severity and kind
- **Session stats** — edits reviewed, blocks, warns, and **critic agreement rate**

The agreement rate is the component that earns its place: if the correctness critic never finds
anything the design critic missed, it is not earning its cost, and the log proves it rather than
leaving it to intuition.

---

## 10. Verification — planted defects

The scratch project ships a catalogue of known defects, each mapped to the critic that must catch it:

| Planted defect | Expected catcher |
|---|---|
| Utility duplicating an existing exported helper | Opus — reuse |
| Query issued inside a loop over records | Fable — complexity |
| Migration adding `NOT NULL` with no default for existing rows | Schema critic |
| `any` on a public function signature | Layer 0 / convention |
| 200-line function doing four distinct jobs | Opus — decomposition |
| **Correct but slightly inelegant code violating nothing** | **nobody — false-positive control** |

The negative control is what makes the suite honest. A set containing only real defects can be
passed by a critic that flags everything, and over-flagging is the failure mode most likely to cause
abandonment.

---

## 11. Phasing

1. **Skeleton + firewall** — plugin manifest, hook wiring, `CCA_INNER` guard, event log. Critics
   stubbed. Proves hooks fire and nothing recurses.
2. **One real critic** — the Opus reuse/design critic end to end, with the evidence bar.
3. **Second critic + vote** — Fable correctness critic, parallel dispatch, three-outcome vote.
4. **`CF.md` + drain** — queue and the `Stop` gate.
5. **Layer 0** — deterministic pre-pass.
6. **Schema critic** — path-triggered layer 2.
7. **Dashboard** — event-log renderer and stats.
8. **Planted-defect suite** — including the negative control; tune briefs against results.
9. **Package** — `SKILL.md`, README, installable plugin.

Phases 1–4 are the working system. Everything after is depth.

---

## 12. Open questions

- Which deterministic tools to standardise on for layer 0 across stacks (the scratch project is
  TypeScript; other projects in the same workspace are Python).
- Whether the schema critic should also fire on ORM entity changes that generate migrations
  indirectly, or only on migration files themselves.
- Whether to keep Fable in the correctness seat after phase 8 measures its agreement rate against
  Opus and its real latency per edit.
