# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Learning recipe: pull per-post performance (TikTok + Instagram) for all brands,
learn best hour/weekday/duration + top content. Bootstraps from existing history."""
import subprocess, json, requests, datetime, statistics, os
from collections import defaultdict
KB="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
TOK=subprocess.run(["/bin/zsh","-lc",f"uv run '{KB}' secret-get METRICOOL_API_TOKEN"],capture_output=True,text=True).stdout.strip()
H={"X-Mc-Auth":TOK}; UID=3986721
brands=json.load(open("mc_brands.json"))
FROM="2026-01-01T00:00:00+02:00"; TO="2026-07-02T00:00:00+03:00"
WD=["Lun","Mar","Mie","Joi","Vin","Sam","Dum"]

def get(path,blog):
    try:
        r=requests.get("https://app.metricool.com/api"+path,headers=H,
                       params={"userId":UID,"blogId":blog,"from":FROM,"to":TO},timeout=40)
        return r.json().get("data",[]) if r.status_code==200 else []
    except: return []

def parse_dt(v):
    if v is None: return None
    if isinstance(v,(int,float)):
        return datetime.datetime.fromtimestamp(v/1000 if v>1e12 else v)
    try: return datetime.datetime.fromisoformat(str(v).replace("Z","+00:00")).replace(tzinfo=None)
    except: return None

rows=[]
sample={}
for b in brands:
    for p in get("/v2/analytics/posts/tiktok",b["id"]):
        dt=parse_dt(p.get("createTime") or p.get("publishedAt"))
        if not dt: continue
        eng=(p.get("likeCount",0) or 0)+(p.get("commentCount",0) or 0)+(p.get("shareCount",0) or 0)
        views=p.get("viewCount",0) or p.get("playCount",0) or 0
        rows.append({"brand":b["label"],"net":"tiktok","dt":dt,"dur":p.get("duration"),
                     "eng":eng,"views":views,"desc":(p.get("videoDescription") or "")[:60]})
        sample.setdefault("tiktok",p)
    for p in get("/v2/analytics/reels/instagram",b["id"]):
        pa=p.get("publishedAt")
        dt=parse_dt(pa.get("dateTime") if isinstance(pa,dict) else pa)
        if not dt: continue
        rows.append({"brand":b["label"],"net":"instagram","dt":dt,"dur":None,
                     "eng":p.get("interactions",0) or (p.get("likes",0)+p.get("comments",0)),
                     "views":p.get("views",0) or p.get("reach",0) or 0,"desc":(p.get("content") or "")[:60]})
        sample.setdefault("instagram",p)

print(f"TOTAL postări cu performanță: {len(rows)}  (TikTok {sum(1 for r in rows if r['net']=='tiktok')}, IG {sum(1 for r in rows if r['net']=='instagram')})")
print("TikTok fields:", list(sample.get("tiktok",{}).keys()))
print("IG fields:", list(sample.get("instagram",{}).keys()))

def top_buckets(rows, keyfn, label, n=4):
    d=defaultdict(list)
    for r in rows:
        if r["views"]: d[keyfn(r)].append(r["views"])
    stats=[(k,statistics.median(v),len(v)) for k,v in d.items() if len(v)>=2]
    stats.sort(key=lambda x:-x[1])
    print(f"\n{label} (după views median, min 2 postări):")
    for k,med,n2 in stats[:n]: print(f"   {k}: {int(med)} views median ({n2} postări)")

for net in ("tiktok","instagram"):
    sub=[r for r in rows if r["net"]==net and r["views"]]
    if not sub: continue
    print(f"\n===== {net.upper()} ({len(sub)} postări cu views) =====")
    top_buckets(sub, lambda r:f"{r['dt'].hour:02d}:00", "ORĂ optimă")
    top_buckets(sub, lambda r:WD[r['dt'].weekday()], "ZI optimă")
    tops=sorted(sub,key=lambda r:-r["views"])[:5]
    print("   TOP 5 postări (views):")
    for r in tops: print(f"     {r['views']:>7} | {r['brand'][:12]:12} | {r['desc']}")
json.dump([{**r,"dt":r["dt"].isoformat()} for r in rows],open("recipe_data.json","w"),ensure_ascii=False)
print("\nsalvat recipe_data.json")
