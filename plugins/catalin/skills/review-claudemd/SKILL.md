---
name: review-claudemd
description: Mine recent conversation transcripts to suggest additions to and pruning of the project and global CLAUDE.md. Use periodically (e.g. weekly) or after a stretch of repeatedly correcting Claude. Triggers: "review my CLAUDE.md", "improve CLAUDE.md", "what rules am I repeating", "prune CLAUDE.md", "find stale instructions".
---

# review-claudemd

> Author: Catalin.

> Keep CLAUDE.md short and always-true. It loads into every conversation and costs tokens each session — capture rules you repeat, and delete rules that have gone stale.

When invoked:

1. Locate this project's transcripts: `~/.claude/projects/<cwd with slashes and special chars turned into dashes>/` — one `.jsonl` per conversation.
2. Take the ~15–20 most recent `.jsonl` files. Extract the user + assistant text (a `jq` pass over the message lines into a temp dir).
3. Compare them against the current **project** `CLAUDE.md` and the **global** `~/.claude/CLAUDE.md`. For large sets, fan out parallel subagents (batched by file size).
4. Produce four lists:
   - **Instructions violated** — rules that exist but were ignored (fix the wording or placement).
   - **Suggested additions (project)** — corrections you repeated that belong in the project file.
   - **Suggested additions (global)** — cross-project preferences that belong in `~/.claude/CLAUDE.md`.
   - **Potentially outdated** — rules contradicted by recent work; candidates to remove.
5. Present them for approval **before** editing any CLAUDE.md. Keep every addition concise — one rule per line with a short *why*.
