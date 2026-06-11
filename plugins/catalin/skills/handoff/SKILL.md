---
name: handoff
description: Write or update a HANDOFF.md so a fresh-context agent can resume work without re-deriving anything. Use right before /clear, /compact, or starting a new conversation on an unfinished multi-step task, or when the context window is filling up. Triggers: "create a handoff", "write a handoff doc", "I'm going to clear context", "hand this off", "document progress before starting fresh".
---

# handoff

> Author: Catalin.

> Context is best served fresh and condensed. Capture hard-won state in a file before you clear it, so the next (clean) session resumes instantly instead of re-deriving everything.

When invoked:

1. Read any existing `HANDOFF.md` and the files currently relevant to the task.
2. Write/overwrite `HANDOFF.md` in the project root with **exactly these five sections**:
   - **Goal** — what we're ultimately trying to achieve (1–2 sentences).
   - **Current Progress** — what's done, with concrete file paths and IDs.
   - **What Worked** — approaches/commands that succeeded (so they get reused).
   - **What Didn't Work** — dead ends and why, so the next agent doesn't repeat them.
   - **Next Steps** — the concrete, ordered to-do list.
3. Be specific: reference real files, commands, URLs, IDs, and decisions — never vague summaries.
4. Tell the user to start a **fresh conversation** and paste only the `HANDOFF.md` path as the opening message.
