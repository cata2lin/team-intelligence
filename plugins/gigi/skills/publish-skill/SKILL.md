---
name: publish-skill
description: Publish a team skill end-to-end with ONE command — registers it in the knowledge base, then branches, commits, pushes, opens a PR, merges to main, syncs local, and logs it. Reuses the GitHub credential already in the keychain (no `gh auth login`). Use whenever you've created or edited a skill under plugins/<you>/skills/<name> and want the whole team to get it, or when someone asks how to "upload / publish / ship / share a skill". Use --no-merge to open the PR for review instead of auto-merging.
argument-hint: "--path plugins/<you>/skills/<name> [--no-merge]"
---

# publish-skill

> Author: **Gigi**. Shared with the whole team via the `gigi` plugin.

Turns the manual contribute flow (register → branch → commit → push → PR → merge →
sync → log) into one command, so anyone can ship a skill to the whole team without
touching git or `gh`.

## Use it

```bash
# auto-merge to main (team gets it on next plugin update):
uv run "${CLAUDE_PLUGIN_ROOT}/skills/publish-skill/scripts/publish_skill.py" --path plugins/<you>/skills/<name>

# open the PR but leave it for review:
uv run "${CLAUDE_PLUGIN_ROOT}/skills/publish-skill/scripts/publish_skill.py" --path plugins/<you>/skills/<name> --no-merge
```

That's it. It prints the PR URL and confirms the merge.

## What it does, in order
1. Reads `name`/`description` from the skill's `SKILL.md` frontmatter.
2. `kb.py skill-register` — records it in the SharedClaude knowledge base.
3. Creates branch `<author>/<name>-skill` (or reuses your current feature branch),
   `git add`s the skill, commits with a Co-Authored-By trailer, pushes.
4. Opens a PR with `gh` (reuses an existing PR for the branch if there is one).
5. Unless `--no-merge`: squash-merges to `main`, checks out `main`, fast-forward
   pulls, deletes the feature branch.
6. `kb.py log` records the publish.

## Requirements / notes
- **`gh` installed once:** `brew install gh`. No login needed — it pulls the
  github.com token straight from the keychain (the same credential `git push`
  uses). The token is never printed.
- **First push ever on a machine** must have happened once so the credential is in
  the keychain; after that this is fully hands-off.
- **It pushes and merges to `main` by default.** Running it is the act of
  publishing. Use `--no-merge` when the change should be reviewed first.
- Author + skill name are derived from the path
  (`plugins/<author>/skills/<name>`), so put your skill in your own plugin first
  (see `CONTRIBUTING.md`).
- Idempotent enough to re-run: re-running after edits commits the new changes and
  reuses the open PR.
