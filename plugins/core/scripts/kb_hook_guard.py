"""PreToolUse guardrail hook (matcher: Bash). Fast, local-only, fail-open.

Reads the PreToolUse JSON on stdin, inspects the Bash command, and returns a
permission decision:
  - deny  : hard-blocked (catastrophic / never-allowed)
  - ask   : require explicit user confirmation
  - (none): allow normal flow (exit 0, no output)

Rules = built-in critical defaults (always on) + team rules synced from the DB
into ~/.claude/team-guardrails.json (see kb_guard_sync.py) or the plugin's
committed guardrails.json. No DB call here -> no latency on the hot path.
"""
import json
import os
import re
import sys

# Built-in critical rules (always enforced). (pattern, reason)
DENY = [
    (r"rm\s+-rf\s+(/|~|\$HOME)(\s|/|$)", "Catastrophic recursive delete blocked."),
    (r"(?i)\bdrop\s+(database|schema)\b", "Dropping a database/schema is not allowed via the agent."),
    (r"(?i)\btruncate\s+table\b", "TRUNCATE wipes a whole table; run it manually if truly intended."),
    (r"\bmkfs(\.|\s)|\bdd\s+if=", "Disk-destroying command blocked."),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "Fork bomb blocked."),
]
ASK = [
    (r"(?i)\b(delete\s+from|update\s+\S+\s+set)\b",
     "Destructive SQL (DELETE/UPDATE): run the matching SELECT, show row counts, and confirm first."),
    (r"(?i)\binsert\s+into\b", "Writing DB rows: confirm the target DB and intent."),
    (r"(?i)\balter\s+table\b", "Schema change: confirm first."),
    (r"\bgit\s+push\b", "Pushing to git: confirm first."),
]


def load_custom():
    for path in (os.path.expanduser("~/.claude/team-guardrails.json"),
                 os.path.join(os.environ.get("CLAUDE_PLUGIN_ROOT", ""), "guardrails.json")):
        if path and os.path.exists(path):
            try:
                cfg = json.load(open(path, encoding="utf-8"))
                deny = [(r["pattern"], r.get("reason", "Blocked by a team guardrail.")) for r in cfg.get("deny", [])]
                ask = [(r["pattern"], r.get("reason", "Confirm: team guardrail.")) for r in cfg.get("ask", [])]
                return deny, ask
            except Exception:
                return [], []
    return [], []


def _decide(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": f"[guardrail] {reason}",
    }}))
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # fail-open
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not cmd:
        sys.exit(0)
    cdeny, cask = load_custom()
    for pat, reason in DENY + cdeny:
        try:
            if re.search(pat, cmd):
                _decide("deny", reason)
        except re.error:
            continue
    for pat, reason in ASK + cask:
        try:
            if re.search(pat, cmd):
                _decide("ask", reason)
        except re.error:
            continue
    sys.exit(0)  # allow


if __name__ == "__main__":
    main()
