---
name: gha-investigate
description: Root-cause a failing GitHub Actions run — the real cause (vs warnings), whether it's flaky, the breaking commit, and any existing fix. Use when CI fails and you have a workflow-run URL, or when asked "why did CI fail", "investigate this failure", "is this test flaky", "which commit broke the build".
argument-hint: <github-actions-run-url>
---

# gha-investigate

> Author: Catalin.

Take the run URL ($ARGUMENTS) and use the `gh` CLI to investigate methodically:

1. **Cause** — `gh run view <id> --log-failed`. Find what actually exited non-zero; separate fatal errors from warnings/noise. Quote the exact failing step + line.
2. **Flakiness** — check the last 10–20 runs of the *exact* failing job: `gh run list --workflow=<name> --json conclusion,headSha,createdAt`. Is it intermittent (flaky) or consistent (real break)?
3. **Breaking commit** — identify first-fail vs last-pass commit; bisect, and where cheap re-run to confirm.
4. **Existing fix** — `gh pr list --state open --search "<area/file>"` to see if someone is already fixing it before you propose a new fix.
5. **Report** — state the root cause plainly, then recommend the fix. Optionally open a **draft** PR with the fix — never push to a protected branch without explicit confirmation.
