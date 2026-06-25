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

# ── KB reachability — cauza RECURENTĂ a „nu găsește / dă erori" pe orice skill care citește secrete
#    (xConnector, Richpanel, Shopify): un KB_DATABASE_URL stale/greșit pe acea mașină. Verific la fiecare
#    sesiune și STRIG clar dacă KB e inaccesibil, ca agentul să știe din prima „e KB-ul, NU lipsesc datele".
CANONICAL_KB = "38.242.226.83:5432/SharedClaude"
try:
    kb_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb.py")
    if os.path.isfile(kb_py):
        r = subprocess.run(["uv", "run", "--no-project", kb_py, "secret-get", "__healthcheck__"],
                           capture_output=True, text=True, timeout=25)
        err = (r.stderr or "").lower()
        # returncode 0 sau „is not set" = KB OK (s-a conectat). Eșec de CONEXIUNE = KB inaccesibil.
        kb_down = (r.returncode == 3 or (r.returncode != 0 and any(t in err for t in (
            "could not connect", "could not translate", "connection refused", "could not receive",
            "operationalerror", "psycopg2", "timeout expired", "no route to host", "server closed"))))
        if kb_down:
            print("⚠️ KB INACCESIBIL pe această mașină — nu pot citi secretele din SharedClaude "
                  "(KB_DATABASE_URL stale/greșit). TOATE skill-urile care au nevoie de credențiale "
                  "(xConnector, Richpanel, Shopify, etc.) vor eșua / da erori — ASTA NU înseamnă că datele "
                  "'nu există'. FIX: corectează KB_DATABASE_URL (host corect: %s) în ~/.claude/settings.json "
                  "→ 'env', apoi repornește sesiunea (sau re-rulează onboarding/configure)." % CANONICAL_KB)
except Exception:
    pass

sys.exit(0)
