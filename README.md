# CCA — Coder / Critic / Arbiter for Claude Code

Every edit you make is reviewed, as it happens, by two fresh Claude instances on different models
with different jobs. You arbitrate. No external model APIs, no API keys — critics run as headless
`claude -p` calls on your existing subscription.

```
Edit/Write ──► PostToolUse hook ──► claude -p --model claude-opus-5      (reuse & design)
                                    claude -p --model claude-fable-5-1   (correctness & complexity)
                                    claude -p --model claude-opus-5      (schema — migrations only)
                                              │
                                              ▼
                         both fail / any critical → BLOCK, fix now
                         one fail / any warn      → queue to CF.md
                         all pass                 → silent
```

## Before you install: this spends your Claude plan

Every edit you make triggers **two headless `claude -p` calls**. That is the whole design — it is
not a background nicety, it is two model reviews per `Edit` or `Write`, on your own subscription.

The shipped roster is Opus for design and **Haiku** for correctness, chosen to keep that
affordable. At roughly 5K in / 1–4K out per critic, a 30-edit task costs single-digit dollars
equivalent against a Teams plan. If you swap the correctness seat to Fable 5.1 — the most capable
and by far the most expensive option — expect several times that.

If a review on every keystroke-sized edit is not a trade you want, this tool is not for you, and
there is no way to have it cheaply: the reviews are the product.

## Install

```
/plugin marketplace add https://gitlab.com/mukesh886singh/critic
/plugin install cca@cca
```

Then restart Claude Code. The plugin is Python-3.9 stdlib only — there is nothing to build and no
dependency to install.

To scope it to one project rather than everything you work on:

```bash
cd /path/to/your/project
claude plugin install cca@cca --scope project
```

**Start with the phase gate off.** The critics alone are the useful half; the gate assumes a
`writing-plans` document with `### Task N` headings and sits inert without one:

```json
{ "phases": { "enabled": false } }
```

### Choosing the roster

Everything is data. Swapping a critic is one line in `.cca/config.json`:

```json
{
  "critics": [
    {"name": "design",      "model": "claude-opus-5",    "brief": "design",      "timeout": 120},
    {"name": "correctness", "model": "claude-fable-5-1", "brief": "correctness", "timeout": 120}
  ]
}
```

That is the expensive, most rigorous pairing. Watch `agreement_rate` on the dashboard to decide
whether the second seat is earning its cost — if one critic never finds anything the other missed,
it is not.

## The dashboard — CCA Precinct

```bash
python3 scripts/dashboard.py /path/to/your/project 7878
```

Then open <http://127.0.0.1:7878/index.html>, or run `CCA: Open Dashboard` in the editor. Same board
either way: `dashboard/` is the only copy, and `dashboard.js` picks its transport at runtime
(postMessage inside the webview, polling `events.jsonl` outside it).

The board is a squad-room dispatch display, and every animation encodes real data rather than
decorating it:

| What happened | What you see |
|---|---|
| an edit under review | a suspect walks onto the beat |
| a critic | an officer who takes the shot |
| a finding | a hit, sized and coloured by severity |
| `pass` | **CLEARED** — the suspect walks |
| `queue` | **CITED** — a ticket, and the finding lands on the rap sheet |
| `block` | **BUSTED** — sirens, stamp, screen shake |
| a degraded critic | greyed out, **10-7 OUT OF SERVICE** |
| agreement rate | **PARTNER SYNC** |
| the four gate rungs | the checks a case clears before booking |

### The case file

Under the street is the part that answers "so what do I do now". One panel, three columns, all
drawn from the same event stream:

| Column | What it shows |
|---|---|
| **WHAT CLAUDE DID** | the file, the tool, `+added / −removed`, and a real colour-coded diff of the change |
| **WHAT THE CRITICS SAID** | each finding with its severity, which critic raised it, and its proof — or, when there is none, *"no evidence given — cannot block"* |
| **WHAT TO DO NEXT** | the verdict in plain English, then the critics' own `suggestion` fields as a numbered list, deduplicated, each with its `file:line` |

Each step in that third column carries **Fix this** and **Skip**. You are the arbiter: some findings
are worth acting on and some are the officers overthinking it, and skipping records that judgement
rather than discarding it — as `dismissed`, never as `done`, so nothing later claims work nobody did.

**While Claude is stopped** — which is exactly when the critics have blocked an edit — the column
says so and offers *Send N fixes to Claude now*. That answers the block with the remedies you
accepted, so they are carried out in that same turn, before anything else happens. If you skipped
everything, the button becomes *Let the edit stand*.

**When Claude is not stopped**, the same accepted fixes are handed over the moment it next tries to
finish, or on your next message. The board says which of the two situations you are in rather than
leaving "queued" to be read as "now":

```
Fix this ──► .cca/fixes.json  status: todo
Skip     ──► .cca/fixes.json  status: dismissed   (never reaches Claude)
              │
              ├─ block is live      "Send to Claude now"  ──► done in THIS turn
              ├─ Stop hook          Claude tries to finish ──► blocked with the instruction
              ├─ UserPromptSubmit   you type anything      ──► injected as context
              └─ extension          a terminal running claude gets a nudge
              │
              ▼                                                      status: sent
          Claude edits the file again, critics pass       ──►        status: done
```

Once the fixes are sent, the column tracks them the rest of the way, each stage read from events
the plugin was already writing:

| Stage | What you see |
|---|---|
| dispatched | *Sent. Waiting for Claude to start.* |
| Claude edits the file | *Claude is working on it.* — with the file named |
| critics re-run | *The officers are checking the fix*, with each one's call as it lands: `✔ DESIGN` `… CORRECTNESS` |
| both pass | *Satisfied. Claude may carry on.* and the accepted fixes close |
| they still object | *Still not satisfied*, and the new findings are above it |

Ordering comes from position in the append-only log rather than wall-clock time — "did this happen
after the dispatch" is the question the whole strip rests on, and a timestamp can be equal, skewed,
or written out of order.

The Stop gate is what makes it actually happen — a fix you accepted cannot be quietly skipped,
because the session cannot finish while one is outstanding. Resolution is by evidence, not
assumption: a fix closes when a later edit to that same file passes review. Nothing here writes
code; it carries an instruction and the coder does the work.

That third column exists because the data was already there and being thrown away: every critic
returns a `suggestion` and the board never rendered it. The first column exists because
`edit_started` recorded *that* a file changed but never *what* changed — so the board could show a
verdict without ever showing the thing being judged. `ccalib/change.py` now summarises the change
into the event, bounded to 16 lines and 160 characters each so an append-only log written on every
edit cannot bloat.

### The rap sheet

Everything the officers have booked this session, one row per defect — not one per critic. Two
critics reaching the same conclusion is corroboration worth showing (*"both officers agreed: design
+ correctness"*), not two separate problems, so rows merge on file + line + issue. Differently
worded observations stay separate, because they really are different observations.

Every row carries its state, and open work is never buried under settled work:

| State | Meaning |
|---|---|
| **OPEN** | still stands, nothing done about it |
| **QUEUED FOR CLAUDE** | you accepted the fix; it is on its way or already sent |
| **FIXED** | you accepted it and the file passed review afterwards — struck through |
| **SKIPPED** | you judged it not worth fixing |
| **SUPERSEDED** | the file passed review later, so this no longer describes the code — but nobody acted on it, which is not the same as fixed |

Open findings sit at the top; the rest collapse behind a *"N closed"* disclosure that survives the
board's twice-a-second repaint.

Resting state is deliberately quiet: the siren bar is dark, the officers just hold their post.
`prefers-reduced-motion` removes every animation and the stamps appear instantly.

The figures are pixel sprites drawn from a grid in `dashboard.js` — no image files, no webfonts,
nothing fetched. That is not a stylistic flourish: the webview's content-security policy blocks
every external resource, so anything the board draws, it draws itself.

## The IDE arbiter

Search **CCA Arbiter** in your editor's extension panel, or build it yourself:

```bash
npm --prefix extension install && npm --prefix extension run package
code --install-extension extension/cca-arbiter-0.1.0.vsix
```

With the extension running, the two decisions that are yours stop being
paragraphs printed after the fact and become questions asked at the moment they
matter: a critic block (*Uphold* / *Queue* / *Overrule*) and the phase commit
(*Commit* / *Edit message…* / *Later*). The commit runs as your click.

Nothing about this is required. The hooks are still the lock — the extension
only renders the question and returns the answer. Close it, and every gate
behaves exactly as it does from the terminal. See [`extension/README.md`](extension/README.md).

## Arbitrating

- **`CF.md`** is your surface. Delete a heading to overrule a critic; leave it and Claude must
  address it before the session can finish.
- To close an item without fixing it, insert a `[wontfix]` tag into its heading with a reason.
- `Esc` interrupts at any time. Permission prompts stay on.

## Configuration

`.cca/config.json` in the target project. Everything is data — swapping a critic is one line:

```json
{
  "critics": [
    {"name": "design",      "model": "claude-opus-5",     "brief": "design",      "timeout": 120},
    {"name": "correctness", "model": "claude-haiku-4-5",  "brief": "correctness", "timeout": 60}
  ]
}
```

## Design notes

Three things do most of the work:

- **The evidence bar is code, not prompt** (`scripts/ccalib/verdict.py`). A `reuse` or `complexity`
  finding with no `evidence` field is downgraded so it can never block. Hedging language
  ("consider…", "would be cleaner") is dropped at parse time. Briefs cannot be trusted to police
  themselves.
- **The verdict is derived, never trusted.** A critic's own `verdict` field is recorded for
  agreement analysis but the effective verdict comes from the findings that survive filtering, so
  "says pass, lists three criticals" cannot happen.
- **It fails open.** Timeout, crash, missing binary, unparseable output — all degrade to `warn`.
  A critic must never be able to stop you working.

## Measuring whether the critics earn their cost

```bash
python3 scripts/cca_eval.py     # spends real model calls
```

Runs every critic against `test-project/`, which contains five planted defects **and a negative
control** — correct-but-inelegant code that nothing should flag. The control is the point: a defect
set containing only real defects can be passed by a critic that flags everything.

Watch `findings_by_critic` and `agreement_rate` on the dashboard. If one critic never finds anything
the other missed, it isn't earning its cost.

## Phase gate — shipping discipline

A large plan must ship as a sequence of small, individually committed increments. The plugin reads
`### Task N` headings from your plan document and enforces one phase at a time.

```
SessionStart          "phase 3 of 11 — Critic runner. Scope: … Shipped: 1 …, 2 …"
PreToolUse  (fence)   editing a LATER phase's file  → denied
                      editing an earlier or unlisted file → allowed
Stop        (ladder)  tests → critics → commit → clean tree
```

**Claude never commits.** At the gate it prints the command and stops:

```
Phase 3/11 — Critic runner is ready but has not shipped.

  git add scripts/ccalib/runner.py tests/test_runner.py docs/superpowers/plans/plan.md \
    && git commit -m "[phase-3] Critic runner"
```

`[phase-N]` is the tag the gate greps for. The plan file is in the `git add` so the ticked
checkboxes ride along inside the commit and the tree ends clean.

**Current phase** is the lowest-numbered task with no `[phase-N]` commit — derived from `git log`,
never stored, so it survives any session boundary and cannot drift.

**Building ahead is blocked.** If uncommitted work belongs to a later phase, the gate refuses and
names each file with its owning phase — that is the lump this whole mechanism exists to prevent.
Unlisted files and fixes to already-shipped phases never block.

**A phase you have not started never blocks you.** Clean tree plus no commit means there is nothing
to ship, so finishing one phase and stopping for the day is allowed.

### Config

```json
{ "phases": {
    "enabled": true,
    "plan": "docs/superpowers/plans/my-plan.md",
    "test_command": "python3 -m unittest discover tests",
    "done": []
} }
```

`enabled: false` turns the gate off and leaves the critics untouched. `done: [1,2,3]` is the escape
hatch when a rebase or squash loses the `[phase-N]` tags.

### Escape hatches

- A file genuinely belongs to this phase but the fence disagrees → add it to that task's `**Files:**`
  block in the plan.
- The fence covers `Bash` writes as well as `Edit`/`Write`: shell redirections (`>`, `>>`, including
  the `cat > file <<EOF` heredoc form), `tee`, `cp`/`mv` destinations, `sed -i`, `touch`, `truncate`
  and `dd of=`. Only write *targets* count, so reading a later-phase file (`cat`, `grep`, running
  tests) is never blocked.
- `NotebookEdit` is covered too (it uses `notebook_path`, not `file_path`).
- Interpreter payloads are covered by **static analysis, never execution**: Python `-c` strings and
  `python3 - <<'PY'` heredoc bodies are parsed with `ast` (`open(..., "w")`, `pathlib` writers,
  `os.remove`/`rename`, `shutil` destinations), `sh -c` / `bash -c` payloads recurse, and `node -e`
  is scanned for the `fs.write*` family.
- **The one real limit:** a dynamic path — `open(name, "w")` where `name` is computed — cannot be
  resolved without running the program, so it is not detected. This is a property of static
  analysis, not an omission, and the fence deliberately does not guess.

## Known gap

Layer 0 (deterministic pre-pass — typecheck, duplicate-block and dead-export scans) is **not
implemented**. The seam exists and is tested: `vote.decide(verdicts, layer0)` takes the findings
list and both paths are covered, but nothing produces them yet. Until it lands, cheap findings are
paid for at frontier-model rates. See the spec's open question 1.

## Publishing

See [`PUBLISHING.md`](PUBLISHING.md). Identity fields are placeholders until you fill them in;
`python3 scripts/check_release.py` refuses to let a placeholder reach a registry.

## Tests

```bash
python3 -m unittest discover tests
```

321 tests, stdlib only. Critic invocation is tested against a fake `claude` binary on `PATH`, and
the arbiter bridge against a fake arbiter thread, so the suite spends no model calls and runs in
about 20 seconds.

The extension has its own suite:

```bash
npm --prefix extension test     # 28 vitest tests on the pure modules, then tsc --noEmit
```
