# CCA Arbiter

The arbiter's seat for the CCA plugin — Coder / Critic / Arbiter for Claude Code —
inside the editor. (The plugin's own README sits at the root of the CCA repository;
it is not part of this package.)

The Python hooks decide what to ask. This extension renders the question and
returns your answer. It never decides anything on its own, and with it closed
the plugin behaves exactly as it does from the terminal.

## What it does

- **Live dashboard** (`CCA: Open Dashboard`) — the critic feed, the phase strip
  and the tuning stats, streamed from `.cca/events.jsonl`.
- **Block modal** — when the critics reject an edit: *Uphold*, *Queue to CF.md*,
  or *Overrule* with a note. Dismiss it and the block stands.
- **Commit modal** — when a phase is ready: *Commit*, *Edit message…*, *Later*.
  The commit runs as your click, from your workspace, with exactly the files the
  phase gate named.
- **Critic Feedback tree** — `CF.md` as a list, with *Overrule* and *Wontfix…*
  applied as undoable workspace edits.
- **Status bar** — `CCA · phase 3/11 · 2 open · ⏳ waiting on you`.

## Install

This extension does nothing on its own. It is the editor surface for the **CCA plugin for Claude
Code**, and needs that installed first:

```
/plugin marketplace add Mukeshsingh8/critic
/plugin install cca@cca
```

Then install this from your editor's extension panel, or build it:

```bash
npm --prefix extension install
npm --prefix extension run package      # produces cca-arbiter-0.1.0.vsix
code --install-extension extension/cca-arbiter-0.1.0.vsix
```

Note that the plugin spends your Claude subscription: two model reviews per edit. See the project
README before installing.

Any VS Code 1.85+ host works, including Cursor, Windsurf and Antigravity — the
extension uses no proprietary APIs. It cannot hook a different agent's edits,
though: the enforcement is Claude Code's hooks, so this helps only when Claude
Code is the one editing.

## How it talks to the plugin

A file mailbox under `.cca/`. No ports, no daemon, no sockets.

| File | Written by | Meaning |
|---|---|---|
| `arbiter.json` | extension, every 5s | a human is reachable |
| `pending/<id>.json` | hook | a question, with a deadline |
| `decisions/<id>.json` | extension | the answer |

If the heartbeat is missing or older than `arbiter.stale_after` (15s), the hooks
do not wait at all. Timeout, malformed answer and crash all fall back to the
plugin's own default: **Uphold** for a block, **Later** for a commit.

## Configuration

`.cca/config.json` in the project:

```json
{ "arbiter": { "enabled": true, "block_wait": 180, "commit_wait": 300, "stale_after": 15 } }
```

`"enabled": false` turns the bridge off and leaves the critics and the phase
gate untouched.

## Development

```bash
npm --prefix extension test     # vitest on the pure modules, then tsc --noEmit
npm --prefix extension run build
```

F5 in `extension/` opens an Extension Development Host on `../test-project`.
