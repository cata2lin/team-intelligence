# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Resolve Belasil's top Meta ads -> creative video -> source title, match to local files."""
import os, sys, json, re, glob
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
VER="v23.0"
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(dsn):
    p=urlsplit(dsn)
    if not p.query: return dsn
    k=[(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(k),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
def q(sql,a=None):
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c: c.execute(sql,a); return [dict(r) for r in c.fetchall()]
acc=q('SELECT a."metaAccountId" aid, t."accessToken" tok FROM meta_ad_accounts a JOIN meta_access_tokens t ON t.id=a."tokenId" WHERE a.name ILIKE %s AND a."isActive"=true AND t."isActive"=true',("%belasil%",))
def getall(url, params):
    out=[]
    while True:
        r=requests.get(url,params=params,timeout=60)
        if r.status_code!=200: print("WARN",r.status_code,r.text[:140]); break
        d=r.json(); out+=d.get("data",[]); nxt=d.get("paging",{}).get("next")
        if not nxt: break
        url=nxt; params=None
    return out
def numa(actions,keys):
    for a in actions or []:
        if a.get("action_type") in keys: return float(a.get("value",0))
    return 0.0

ads={}  # ad_id -> {name, spend, purch, rev, roas, video_id}
for ac in acc:
    tok=ac["tok"]; base=f"https://graph.facebook.com/{VER}/{ac['aid']}"
    # performance
    for r in getall(base+"/insights",{"level":"ad","fields":"ad_id,ad_name,spend,actions,purchase_roas","time_range":json.dumps({"since":"2025-04-01","until":"2026-06-10"}),"limit":"400","access_token":tok}):
        aid=r.get("ad_id"); a=ads.setdefault(aid,{"name":r.get("ad_name"),"spend":0,"purch":0,"rev":0,"vid":None})
        a["spend"]+=float(r.get("spend",0)); a["purch"]+=numa(r.get("actions"),("purchase","offsite_conversion.fb_pixel_purchase","omni_purchase")); a["rev"]+=numa(r.get("action_values"),("purchase","offsite_conversion.fb_pixel_purchase","omni_purchase"))
    # creatives (ad -> video_id)
    for r in getall(base+"/ads",{"fields":"id,name,creative{video_id,object_story_spec}","limit":"400","access_token":tok}):
        aid=r.get("id"); cr=r.get("creative") or {}; vid=cr.get("video_id")
        if not vid:
            oss=cr.get("object_story_spec") or {}; vid=(oss.get("video_data") or {}).get("video_id")
        if aid in ads: ads[aid]["vid"]=vid
        elif vid: ads[aid]={"name":r.get("name"),"spend":0,"purch":0,"rev":0,"vid":vid}

# resolve video titles for top-by-spend ads
top=sorted(ads.values(), key=lambda x:-x["spend"])[:25]
tok0=acc[0]["tok"]
vtitle={}
for a in top:
    if a["vid"] and a["vid"] not in vtitle:
        rr=requests.get(f"https://graph.facebook.com/{VER}/{a['vid']}",params={"fields":"title","access_token":tok0},timeout=30)
        vtitle[a["vid"]]=(rr.json().get("title") if rr.status_code==200 else None)

# local files
files=[]
for fol in ("/Users/gheorghebeschea/Downloads/Creative Belasil","/Users/gheorghebeschea/Downloads/Creative Belasil 2"):
    files+= [os.path.basename(f) for f in glob.glob(os.path.join(fol,"*")) if f.lower().endswith((".mp4",".mov",".m4v",".webm"))]
def norm(s): return re.sub(r"[^a-z0-9]","", re.sub(r"\.(mp4|mov|m4v|webm)$","",(s or "").lower()))
fn={norm(f):f for f in files}
def match(*names):
    for nm in names:
        n=norm(nm)
        if len(n)>=5:
            for k,f in fn.items():
                if n in k or k in n: return f
    return ""

print(f"{'spend':>8} {'roas':>5} {'purch':>6}  ad  |  video title  ->  fișier")
for a in top:
    roas=a["rev"]/a["spend"] if a["spend"]>0 else 0
    vt=vtitle.get(a["vid"])
    f=match(vt, a["name"])
    print(f"{a['spend']:>8.0f} {roas:>5.2f} {a['purch']:>6.0f}  {str(a['name'])[:22]:22} | {str(vt)[:32]:32} -> {f}")
