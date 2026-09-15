# Changelog

## 0.1.0

First release.

- **Live dashboard** (`CCA: Open Dashboard`) — the critic feed, the duty roster, the
  phase ladder and the case file, streamed from `.cca/events.jsonl`.
- **Block modal** — uphold, queue to `CF.md`, overrule with a note, or send the
  accepted fixes straight back to Claude while it is still stopped.
- **Commit modal** — commit, edit the message, or defer, when a phase is ready.
  The commit runs as your click, with exactly the files the phase gate named.
- **Case file** — what Claude changed, what the critics said about it, and what to
  do next, with each remedy actionable.
- **Rap sheet** — every finding, merged per defect, carrying its state.
- **Critic Feedback tree** — `CF.md` with overrule and wontfix as undoable edits.

Known limit: the editor surface (modals, tree, webview) is covered by typechecking
and by tests of its pure modules, but has not been exercised end to end by a human.
See the smoke procedure in `docs/superpowers/plans/2026-09-14-cca-ide-arbiter.md`.
