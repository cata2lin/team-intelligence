# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Rank Belasil's Meta ads by performance and match ad names to local video files."""
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
def q(sql,args=None):
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute(sql,args); return [dict(r) for r in c.fetchall()]

brand=q("SELECT id,name FROM brands WHERE name ILIKE %s",("%belasil%",))
if not brand: sys.exit("Belasil brand not found")
bid=brand[0]["id"]; print("brand:",brand[0]["name"],bid)
acc=q('SELECT a."metaAccountId" aid, a.currency cur, a.name nm, t."accessToken" tok FROM meta_ad_accounts a JOIN meta_access_tokens t ON t.id=a."tokenId" WHERE a.name ILIKE %s AND a."isActive"=true AND t."isActive"=true',("%belasil%",))
if not acc: sys.exit("no active Meta account named Belasil")
print("meta accounts:", [(x["aid"],x["nm"]) for x in acc])
rows=[]
for ac in acc:
    url=f"https://graph.facebook.com/{VER}/{ac['aid']}/insights"
    params={"level":"ad","fields":"ad_name,spend,impressions,actions,purchase_roas,action_values",
            "time_range":json.dumps({"since":"2025-04-01","until":"2026-06-10"}),"limit":"500","access_token":ac["tok"]}
    while True:
        r=requests.get(url,params=params,timeout=60)
        if r.status_code!=200: print("  WARN",ac['aid'],r.status_code,r.text[:160]); break
        d=r.json(); rows+=d.get("data",[])
        nxt=d.get("paging",{}).get("next")
        if not nxt: break
        url=nxt; params=None
print("ad rows:",len(rows))

def num(actions,keys):
    if not actions: return 0.0
    for a in actions:
        if a.get("action_type") in keys: return float(a.get("value",0))
    return 0.0
agg={}
for r in rows:
    name=r.get("ad_name","?")
    a=agg.setdefault(name,{"spend":0.0,"purch":0.0,"rev":0.0,"impr":0.0})
    a["spend"]+=float(r.get("spend",0)); a["impr"]+=float(r.get("impressions",0))
    a["purch"]+=num(r.get("actions"),("purchase","offsite_conversion.fb_pixel_purchase","omni_purchase"))
    a["rev"]+=num(r.get("action_values"),("purchase","offsite_conversion.fb_pixel_purchase","omni_purchase"))
out=[]
for name,a in agg.items():
    roas=(a["rev"]/a["spend"]) if a["spend"]>0 else 0
    cpa=(a["spend"]/a["purch"]) if a["purch"]>0 else 0
    out.append({"name":name,"spend":a["spend"],"purch":a["purch"],"rev":a["rev"],"roas":roas,"cpa":cpa})

# match to files
folder=sys.argv[1] if len(sys.argv)>1 else "/Users/gheorghebeschea/Downloads/Creative Belasil"
files=[os.path.basename(f) for f in glob.glob(os.path.join(folder,"*")) if f.lower().endswith((".mp4",".mov",".m4v",".webm"))]
def norm(s): return re.sub(r"[^a-z0-9]","", re.sub(r"belasil|lavete|video|\.(mp4|mov|m4v|webm)","",s.lower()))
fnorm={norm(f):f for f in files}
def matchfile(adname):
    n=norm(adname)
    if not n: return ""
    for fn,f in fnorm.items():
        if n and (n in fn or fn in n): return f
    # token overlap
    toks=set(re.findall(r"[a-z]{4,}", adname.lower()))-{"belasil","video","lavete"}
    best=""; besto=0
    for fn,f in fnorm.items():
        ft=set(re.findall(r"[a-z]{4,}", f.lower()))
        o=len(toks&ft)
        if o>besto: besto=o; best=f
    return best if besto>=1 else ""
# top ads by volume (the scaled winners) — for manual ID by user
topvol=sorted([o for o in out if o["spend"]>=200], key=lambda x:-x["purch"])[:18]
print("\n=== TOP reclame după ACHIZIȚII (câștigătorii scalați — identifică tu fișierul) ===")
print(f"{'achiz':>6} {'ROAS':>5} {'spend':>9}  nume reclamă")
for o in topvol: print(f"{o['purch']:>6.0f} {o['roas']:>5.2f} {o['spend']:>9.0f}  {o['name']}")

# rank FILES by performance of ads named after them
def ads_for_file(fname):
    base=os.path.splitext(fname)[0]
    fn=norm(fname)
    toks=set(re.findall(r"[a-z]{4,}", base.lower()))-{"belasil","video","lavete"}
    ms=[]
    for o in out:
        an=norm(o["name"]); ov=set(re.findall(r"[a-z]{4,}", o["name"].lower()))
        if (len(fn)>=5 and (fn in an or an in fn)) or len(toks&ov)>=2:
            ms.append(o)
    return ms
fileperf=[]
for f in files:
    ms=ads_for_file(f)
    sp=sum(o["spend"] for o in ms); pu=sum(o["purch"] for o in ms); rv=sum(o["rev"] for o in ms)
    fileperf.append({"file":f,"spend":sp,"purch":pu,"roas":(rv/sp if sp>0 else 0),"cpa":(sp/pu if pu>0 else 0),"nads":len(ms)})
ranked=[x for x in fileperf if x["spend"]>=100 and x["purch"]>=2]
ranked.sort(key=lambda x:-x["roas"])
print("\n=== FIȘIERE (creatori) clasate după ROAS-ul reclamelor lor pe Meta ===")
print(f"{'ROAS':>5} {'achiz':>6} {'spend':>9} {'CPA':>6}  fișier")
for x in ranked: print(f"{x['roas']:>5.2f} {x['purch']:>6.0f} {x['spend']:>9.0f} {x['cpa']:>6.1f}  {x['file']}")
nod=[x['file'] for x in fileperf if x['spend']<100]
print(f"\nFișiere fără date Meta suficiente (nerulate/nume diferit) — {len(nod)}:")
for f in nod: print("   ·",f)
