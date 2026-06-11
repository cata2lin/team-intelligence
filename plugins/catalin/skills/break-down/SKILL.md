---
name: break-down
description: Decompose a hard task into independently one-shottable subtasks and sequence them, planning before implementing. Use when a task is too big to do reliably in one go, when a first attempt failed, or when asked to "break this down", "make a plan", "decompose this", "plan this out".
---

# break-down

> Author: Catalin.

> Going A→A1→A2→A3→B is far more reliable than asking the agent to jump A→B. Decompose until each piece is small enough to one-shot and verify on its own.

When invoked:

1. Restate the goal in one sentence.
2. Split it into the **smallest subtasks that can each be built AND verified independently**. If a subtask still feels too big or uncertain, split it again.
3. Order them by dependency; mark which are independent and could run in parallel (separate worktrees / subagents).
4. For each subtask note: inputs, the concrete change, and **how it will be verified**.
5. Present the plan (use plan mode / `ExitPlanMode`) and get approval before building. Then build + verify **one subtask at a time**, integrating as you go — don't write everything then test at the end.
