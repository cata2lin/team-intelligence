# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31", "google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""Validate the LIVE ingester totals against 'Raport Zilnic 2' (the team's current per-brand FB/TT).
  uv run validate_vs_rz2.py 2026-05-01 2026-05-31
"""
import sys, json, subprocess
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ad_spend_live as live
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def kb(k): return subprocess.run(["uv","run",str(KB),"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
since, until = sys.argv[1], sys.argv[2]

# id->brand name (metrics)
mc = live.metrics_conn(); cur = mc.cursor(); cur.execute("SELECT id,name FROM brands"); id2n = {i: n.strip() for i, n in cur.fetchall()}; mc.close()

# LIVE per brand
rows = live.live_rows(since=since, until=until)
lf = defaultdict(float); lt = defaultdict(float); test_fb = test_tt = 0.0
for d, bid, key, lbl, plat, sp, src in rows:
    if key == "TEST":   # RZ2 exclude TEST din branduri → exclud și eu din comparație (dar îl însumez separat)
        if plat == "meta": test_fb += sp
        else: test_tt += sp
        continue
    b = id2n.get(bid, f"?{bid}")
    (lf if plat == "meta" else lt)[b] += sp

# RZ2 per brand
sa = json.loads(kb("GA4_SA_JSON"))
svc = build("sheets","v4",credentials=Credentials.from_service_account_info(sa,scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])).spreadsheets()
v = svc.values().get(spreadsheetId="1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo", range="'Raport Zilnic 2'!A2:D").execute().get("values", [])
def f(x):
    try: return float(str(x).replace('.','').replace(',','.'))
    except: return 0.0
rf = defaultdict(float); rt = defaultdict(float)
ym0, ym1 = since[:7], until[:7]
for r in v:
    if len(r) < 4 or not (ym0 <= r[0][:7] <= ym1): continue
    rf[r[1].strip()] += f(r[2]); rt[r[1].strip()] += f(r[3])

brands = sorted(set(lf)|set(lt)|set(rf)|set(rt), key=lambda b:-(rf[b]+rt[b]+lf[b]+lt[b]))
print(f"VALIDARE live vs Raport Zilnic 2 ({since}→{until})  [FB | TT]")
print(f"{'brand':18}{'LIVE fb':>10}{'RZ2 fb':>10}{'Δ%':>6}   {'LIVE tt':>9}{'RZ2 tt':>9}{'Δ%':>6}")
print("-"*82)
def pct(a,b): return (a-b)/b*100 if b else (0 if a==0 else 999)
for b in brands:
    print(f"{b[:18]:18}{lf[b]:>10.0f}{rf[b]:>10.0f}{pct(lf[b],rf[b]):>5.0f}%   {lt[b]:>9.0f}{rt[b]:>9.0f}{pct(lt[b],rt[b]):>5.0f}%")
print("-"*82)
print(f"{'TOTAL':18}{sum(lf.values()):>10.0f}{sum(rf.values()):>10.0f}{pct(sum(lf.values()),sum(rf.values())):>5.0f}%   {sum(lt.values()):>9.0f}{sum(rt.values()):>9.0f}{pct(sum(lt.values()),sum(rt.values())):>5.0f}%")
print(f"(separat) TEST exclus din branduri: FB {test_fb:.0f}  TT {test_tt:.0f}")
