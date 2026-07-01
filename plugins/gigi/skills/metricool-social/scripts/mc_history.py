# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
import subprocess, json, requests, sys, datetime
KB="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
TOK=subprocess.run(["/bin/zsh","-lc",f"uv run '{KB}' secret-get METRICOOL_API_TOKEN"],capture_output=True,text=True).stdout.strip()
H={"X-Mc-Auth":TOK}
UID=3986721
brands=json.load(open("mc_brands.json"))
gt=[b for b in brands if b['label']=='George Talent'][0]
start="2026-04-01T00:00:00"; end="2026-07-02T00:00:00"
base="https://app.metricool.com/api/v2/scheduler/posts"
# try formats
for params in [
    {"userId":UID,"blogId":gt['id'],"start":start,"end":end},
    {"userId":UID,"blogId":gt['id'],"start":"20260401","end":"20260702"},
]:
    r=requests.get(base,headers=H,params=params,timeout=30)
    print("params",{k:params[k] for k in ('start','end')},"->HTTP",r.status_code, "len", len(r.text))
    if r.status_code==200:
        try:
            d=r.json(); data=d.get("data",d)
            print("  count:", len(data) if isinstance(data,list) else "?")
            if isinstance(data,list) and data:
                p=data[0]; print("  sample keys:", list(p.keys())[:15])
        except Exception as e: print("  parse err",e, r.text[:200])
        break
