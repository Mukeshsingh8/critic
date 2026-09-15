# Publishing

Two artefacts, two places. The plugin is the product; the extension is optional.

Nothing here is committed or pushed for you — that is yours to decide.

---

## 0. Fill in who you are

Every identity field is a placeholder on purpose. `scripts/check_release.py` exits non-zero while
any remain, because a placeholder that reaches a public registry is permanent.

| Placeholder | Where | What to put |
|---|---|---|
| `mukesh886singh` | both manifests, both READMEs | your GitHub username |
| `Mukesh Singh` | `plugin.json`, `marketplace.json` | the name you want published |
| `65760067+Mukeshsingh8@users.noreply.github.com` | `plugin.json`, `marketplace.json` | a **personal** address, or `<id>+<user>@users.noreply.github.com` |
| `Mukesh Singh` | `LICENSE`, `extension/LICENSE` | the copyright holder |
| `__PUBLISHER_ID__` | `extension/package.json` | your registered Marketplace / Open VSX publisher id |

```bash
python3 scripts/check_release.py     # lists whatever is still outstanding
```

Two things worth deciding before you type them:

- **Do not publish a work email.** It becomes permanent, public, and ties the project to your
  employer. A GitHub noreply address works fine.
- **Check who owns this.** It was built on a work machine. That is a question for you, not a
  blocker, but it is much cheaper to answer now than after it is public.

---

## Where this lives

GitHub is home; GitLab is a mirror. Push both:

```bash
git push origin main      # GitHub
git push gitlab main      # mirror
```

## 1. The plugin — GitHub, no registry

Claude Code "marketplaces" are just repositories containing `.claude-plugin/marketplace.json`,
which this repo already has. There is no registry, no review, and no account beyond GitHub.

```bash
python3 -m unittest discover tests        # 380 tests, must be green
python3 scripts/check_release.py

git add -A
git commit -m "CCA 0.1.0"
git branch -M main
git remote add origin git@github.com:<you>/CCA.git
git push -u origin main
```

Make the repository public. Anyone can then run:

```
/plugin marketplace add <you>/CCA
/plugin install cca@cca
```

Tag the release so people can pin it:

```bash
git tag -a v0.1.0 -m "CCA 0.1.0" && git push origin v0.1.0
```

---

## 2. The extension — Open VSX and/or VS Code Marketplace

**Do the smoke test first.** The editor surface — modals, tree view, webview — is typechecked and
its pure modules are tested, but no human has exercised it end to end. The ten-step procedure is in
`docs/superpowers/plans/2026-09-14-cca-ide-arbiter.md`, Task 13, Step 5. Shipping an unexercised UI
to strangers is a different proposition from running it yourself.

### Open VSX — what Cursor, Windsurf and Antigravity read

1. Sign in at <https://open-vsx.org> with GitHub.
2. Sign the Eclipse Foundation publisher agreement (required, one-off).
3. Create an access token, then:

```bash
npm --prefix extension install
npx ovsx create-namespace <__PUBLISHER_ID__> -p <token>
npm --prefix extension run package
npx ovsx publish extension/cca-arbiter-0.1.0.vsix -p <token>
```

### VS Code Marketplace — Microsoft

1. Create an Azure DevOps organisation at <https://dev.azure.com>.
2. Generate a Personal Access Token scoped to **Marketplace → Manage**, all organisations.
3. Create the publisher at <https://marketplace.visualstudio.com/manage>.

```bash
npx vsce login <__PUBLISHER_ID__>
npm --prefix extension run publish:vsce
```

---

## 3. Before you announce it

- `python3 -m unittest discover tests` and `npm --prefix extension test` both green.
- `python3 scripts/check_release.py` clean.
- The cost warning is the first section of the README. Leave it there. Someone installing this
  casually will spend real plan quota on their next edit, and they should know that before the
  first `Edit`, not after.
- `.cca/` and `CF.md` are gitignored. Confirm `git status` is clean after a run, so nobody
  inherits your event log or your findings.
