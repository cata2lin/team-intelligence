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

sys.exit(0)
