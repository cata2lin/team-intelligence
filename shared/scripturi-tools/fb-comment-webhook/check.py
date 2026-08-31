#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""check.py — starea webhookului FB de moderare comentarii, din LOGURI (durabil).
Inlocuieste ascultarea live (`tail`), care pica cu ConnectionClosedError si da fals-negative.

  uv run check.py            # ultimele 3h
  uv run check.py --hours 24
"""
import argparse, json, subprocess, sys, time, requests
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ACC = "1eb7468fccd79f85919d6e893298e7df"
KB = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts"

def sec(n):
    r = subprocess.run(["uv", "run", "kb.py", "secret-get", n], capture_output=True, text=True, cwd=KB)
    if r.returncode: sys.exit(f"kb.py secret-get {n}: {r.stderr[-200:]}")
    return r.stdout.strip().splitlines()[-1].strip()

a = argparse.ArgumentParser(); a.add_argument("--hours", type=float, default=3); a = a.parse_args()
H = {"Authorization": f"Bearer {sec('CLOUDFLARE_API_TOKEN_WORKERS')}", "Content-Type": "application/json"}
now = int(time.time() * 1000); frm = now - int(a.hours * 3600 * 1000)
body = {"queryId": "wk", "timeframe": {"from": frm, "to": now},
        "parameters": {"datasets": ["cloudflare-workers"],
                       "filters": [{"key": "$metadata.service", "operation": "eq",
                                    "value": "fb-comment-webhook", "type": "string"}]},
        "limit": 500, "view": "events"}
r = requests.post(f"https://api.cloudflare.com/client/v4/accounts/{ACC}/workers/observability/telemetry/query",
                  headers=H, data=json.dumps(body), timeout=60).json()
if not r.get("success"):
    sys.exit("query esuat: " + json.dumps(r.get("errors"))[:200])
ev = ((r.get("result") or {}).get("events") or {}).get("events") or []
blob = [json.dumps(e) for e in ev]
# Linia "wh <object> <n> <entry_ids> <fields>" scrisa de worker (adaugata 01-sep) spune EXACT ce a
# livrat Meta. Inainte, obiectul se DEDUCEA din user-agent — iar butonul "Test" din App Dashboard
# vine cu acelasi UA ca livrarile reale, deci masuratoarea era o inferenta, nu o observatie.
import re as _re
_wh = [m for b in blob for m in _re.findall(r'wh (page|instagram) (\d+) ([\d,]*) ([\w,]*)', b)]
n_page = sum(1 for o, *_ in _wh if o == "page")
n_ig = sum(1 for o, *_ in _wh if o == "instagram")
_ids = {}
for o, _n, ids, _f in _wh:
    for i in (ids.split(",") if ids else []):
        if i:
            _ids[i] = _ids.get(i, 0) + 1
fb = sum(1 for b in blob if "facebookexternalua" in b)
mine = sum(1 for b in blob if "python-requests" in b)
reload_ = sum(1 for b in blob if "maps: reincarcat" in b)
degraded = sum(1 for b in blob if "maps: eroare" in b)
fatal = sum(1 for b in blob if "maps indisponibil" in b)
print(f"  fereastra: ultimele {a.hours:g}h")
print(f"  invocari totale         {len(ev)}")
print(f"  de la Facebook          {fb}   <- daca e 0 pe o fereastra lunga, livrarea e RUPTA")
print(f"    ├─ object=page         {n_page}")
print(f"    └─ object=instagram    {n_ig}")
if _ids:
    top = sorted(_ids.items(), key=lambda x: -x[1])[:6]
    print("  surse (entry.id):       " + " · ".join(f"{k}={v}" for k, v in top))
print(f"  teste proprii           {mine}")
print(f"  maps reincarcat         {reload_}")
print(f"  maps degradat (cache)   {degraded}" + ("   ⚠️ cota Meta saturata" if degraded else ""))
print(f"  maps INDISPONIBIL       {fatal}" + ("   🔴 se pierd evenimente" if fatal else ""))
# coada de aprobare
q = requests.get("https://fb-comment-webhook.arona-ops.workers.dev/review", timeout=25).json()
print(f"  coada de aprobare       {len(q)} elemente")
print("\n  Nota: coada goala NU inseamna 'nu ajung evenimente' — doar comentariile de tip `reply`")
print("  intra in coada; `keep` si `hide` nu. Uita-te la 'de la Facebook'.")
