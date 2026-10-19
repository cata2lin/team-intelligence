"""PostToolUse logging hook (matcher: Skill|mcp__.*). Fast, local buffer only.

Appends a compact record of the skill / MCP-tool usage to a per-session buffer
file. No DB connection here (instant, no network) -> the Stop hook
(kb_hook_flush.py) batches the buffer into the `events` table once per turn.
Always exits 0 (PostToolUse can't block anyway).
"""
import json
import os
import sys
import tempfile
import time


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}

    if tool == "Skill":
        name = ti.get("name") or ti.get("skill") or ti.get("command") or "skill"
        et, act, summ = "skill", "used", f"skill {name}"
    elif tool == "Read":
        # skill_router trimite agentul să CITEASCĂ skill-urile care există doar în Second Brain;
        # fără asta, exact folosirile noi nu apăreau în statistici
        p = (ti.get("file_path") or "").replace("\\", "/")
        marker = "/.cache/arona-skills/skills/"
        if marker not in p or not p.endswith("/SKILL.md"):
            sys.exit(0)
        parts = p.split(marker, 1)[1].split("/")
        if len(parts) < 3:
            sys.exit(0)
        name = f"{parts[0]}:{parts[1]}"
        et, act, summ = "skill", "read", f"skill {name} (citit din cache)"
    elif tool in ("mcp__second-brain__read_skill", "mcp__second-brain__run_skill"):
        name = ti.get("skill") or "skill"
        act = "read" if tool.endswith("read_skill") else "used"
        et, summ = "skill", f"skill {name} (second-brain {act})"
    elif tool.startswith("mcp__"):
        parts = tool.split("__")
        server = parts[1] if len(parts) > 1 else tool
        if server.startswith("postgres"):
            et, act, name, summ = "db", "query", server, f"queried {server}"
        else:
            et, act, name, summ = "mcp", "used", server, tool
    else:
        sys.exit(0)  # only skills + MCP are logged here

    sid = data.get("session_id", "nosession")
    rec = {"ts": time.time(), "type": et, "action": act, "name": name, "summary": summ, "tool": tool}
    buf = os.path.join(tempfile.gettempdir(), f"claude_kb_{sid}.jsonl")
    try:
        with open(buf, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
