#!/root/Scripturi/.venv/bin/python3
"""Wrapper VPS pt pipeline-ul Richpanel CS. Încarcă .env, rămâne sincron cu GitHub (git pull),
apoi rulează richpanel_pipeline.py cu runner-ul = venv-ul VPS (pg8000 e deja acolo).
Cron: intraday incremental (orele de lucru) + full nightly. Argumentele se pasează pipeline-ului."""
import os, sys, subprocess

BASE = "/root/Scripturi"
TI = BASE + "/team-intelligence"

# 1) încarcă .env (secrete: DATABASE_URL_METRICS, RICHPANEL_MCP_TOKEN, GITHUB_TOKEN…)
try:
    for line in open(BASE + "/.env", encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

# 2) git pull (best-effort) — ia automat actualizările de skill publicate pe GitHub.
#    Tokenul rămâne în env (helper-ul citește $GITHUB_TOKEN), nu apare în argv.
helper = '!f(){ echo username=x-access-token; echo "password=$GITHUB_TOKEN"; }; f'
try:
    r = subprocess.run(["git", "-C", TI, "-c", "credential.helper=" + helper, "pull", "--ff-only"],
                       timeout=120, capture_output=True, text=True)
    print("git pull:", (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "ok")
except Exception as e:
    print("git pull skipped:", e)

# 3) rulează pipeline-ul cu venv-ul VPS
os.environ["CS_PIPELINE_RUNNER"] = BASE + "/.venv/bin/python3"
os.environ.setdefault("RICHPANEL_DB", BASE + "/data/richpanel_tickets.db")
py = BASE + "/.venv/bin/python3"
pipeline = TI + "/plugins/gigi/skills/richpanel-export/richpanel_pipeline.py"
sys.stdout.flush()
os.execv(py, [py, pipeline] + sys.argv[1:])
