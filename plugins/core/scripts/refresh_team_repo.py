# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Best-effort fast-forward pull of the local team-intelligence clone so the
shared CLAUDE.team.md (@import-ed into the global CLAUDE.md) and the shared
scripts stay current.

Wired as a SessionStart hook in plugins/core/hooks/hooks.json, and also safe to
run from a scheduled task / cron. It is intentionally silent and never fails the
session: a stale clone is better than a blocked start. The repo path comes from
$TEAM_REPO, defaulting to ~/team-intelligence.
"""
import os
import subprocess
import sys

repo = os.environ.get("TEAM_REPO") or os.path.join(os.path.expanduser("~"), "team-intelligence")

if not os.path.isdir(os.path.join(repo, ".git")):
    # Not a clone (e.g. installed only as a plugin) — nothing to refresh.
    sys.exit(0)

try:
    subprocess.run(
        ["git", "-C", repo, "pull", "--ff-only", "--quiet"],
        timeout=20,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except Exception:
    pass

# Asigură (idempotent) că global CLAUDE.md @import-ă TOATE fișierele shared de context — nu doar
# CLAUDE.team.md. Necesar fiindcă mașinile deja onboardate nu re-rulează configure.py; fără asta,
# fișiere noi (ex shared/CS.md = harta CS) nu s-ar încărca niciodată. Nested-import în CLAUDE.team.md
# NU e fiabil (sync-ul de catalog regenerează CLAUDE.team.md și pierde referințele). Silent, never-fail.
SHARED_CONTEXT_FILES = ("CLAUDE.team.md", "HARTA.md", "CS.md")
try:
    claude_md = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"), "CLAUDE.md")
    existing = ""
    if os.path.isfile(claude_md):
        with open(claude_md, "r", encoding="utf-8") as fh:
            existing = fh.read()
    missing = []
    for fn in SHARED_CONTEXT_FILES:
        imp = "@" + os.path.join(repo, "shared", fn).replace(os.sep, "/")
        if imp not in existing:
            missing.append(imp)
    if missing:
        sep = "" if (not existing or existing.endswith("\n")) else "\n"
        with open(claude_md, "a", encoding="utf-8") as fh:
            fh.write(sep + "\n".join(missing) + "\n")
except Exception:
    pass

sys.exit(0)
