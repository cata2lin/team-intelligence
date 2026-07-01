# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
import subprocess, json, requests
from collections import Counter
KB="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
TOK=subprocess.run(["/bin/zsh","-lc",f"uv run '{KB}' secret-get METRICOOL_API_TOKEN"],capture_output=True,text=True).stdout.strip()
H={"X-Mc-Auth":TOK}; UID=3986721
brands=json.load(open("mc_brands.json"))
start="2026-05-01T00:00:00"; end="2026-07-02T00:00:00"  # last 2 months = current cadence
print(f"{'BRAND':14} {'tiktok':>7} {'instagram':>10} {'facebook':>9} {'youtube':>8}  (postări prin Metricool, ultimele 2 luni)")
for b in brands:
    r=requests.get("https://app.metricool.com/api/v2/scheduler/posts",headers=H,
                   params={"userId":UID,"blogId":b["id"],"start":start,"end":end},timeout=30)
    data=[p for p in (r.json().get("data",[]) if r.status_code==200 else []) if not p.get("draft")]
    c=Counter()
    for p in data:
        for pr in p.get("providers",[]):
            c[pr.get("network")]+=1
    print(f"{b['label']:14} {c.get('tiktok',0):7} {c.get('instagram',0):10} {c.get('facebook',0):9} {c.get('youtube',0):8}")
