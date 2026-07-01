# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31","psycopg2-binary>=2.9"]
# ///
"""Analytics pe postările ORGANICE (FB Page + Instagram) ale brandurilor ARONA — ce s-a postat, ce
performanță, ce tipar. Token The Wow Grid SU. Engagement = likes+comentarii+share (FB) / likes+comentarii (IG).
  uv run social_analytics.py [--days 90] [--brands gento,nubra,gt] [--reach]
"""
import sys, re, argparse, datetime, statistics, requests
sys.path.insert(0, "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/social-post")
import social_post as s
G = s.G
AP = argparse.ArgumentParser(); AP.add_argument("--days", type=int, default=90)
AP.add_argument("--brands", default=""); AP.add_argument("--reach", action="store_true"); A = AP.parse_args()
tok = s.su_token()
cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=A.days)
def dt(x):
    try: return datetime.datetime.fromisoformat(x.replace("+0000","+00:00").replace("Z","+00:00")[:19]+ (x[19:].replace("+0000","+00:00") if len(x)>19 else "+00:00"))
    except Exception:
        return datetime.datetime.strptime(x[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
def feats(cap):
    cap = cap or ""
    return len(cap), cap.count("#"), bool(re.search(r"[\U0001F300-\U0001FAFF☀-➿]", cap))
posts = []  # dict rows
pgs = s.pages(tok)
want = [b.strip().lower() for b in A.brands.split(",") if b.strip()]
for pg in pgs:
    nm = pg["name"]; pid = pg["id"]; ptok = pg["access_token"]; ig = pg.get("instagram_business_account")
    if want and not any(w in s.norm(nm) for w in want): continue
    # ---- FB ----
    url = f"{G}/{pid}/posts"; params = {"fields":"created_time,message,attachments{media_type,type},shares,likes.summary(true),comments.summary(true)","limit":50,"access_token":ptok}
    fbn = 0
    while url and fbn < 200:
        j = requests.get(url, params=params, timeout=40).json()
        if "error" in j: break
        for p in j.get("data", []):
            t = dt(p["created_time"])
            if t < cut: url = None; break
            att = (p.get("attachments",{}).get("data",[{}]) or [{}])[0]
            typ = (att.get("media_type") or att.get("type") or "status").lower()
            lk = p.get("likes",{}).get("summary",{}).get("total_count",0); cm = p.get("comments",{}).get("summary",{}).get("total_count",0); sh = p.get("shares",{}).get("count",0)
            cl, hs, em = feats(p.get("message"))
            posts.append(dict(brand=nm, plat="FB", type=typ, t=t, eng=lk+cm+sh, likes=lk, com=cm, sh=sh, caplen=cl, tags=hs, emoji=em, msg=(p.get("message") or "")[:70]))
            fbn += 1
        else:
            url = j.get("paging",{}).get("next"); params=None; continue
        break
    # ---- IG ----
    if ig:
        igid = ig["id"]; url = f"{G}/{igid}/media"; params={"fields":"caption,media_type,media_product_type,timestamp,like_count,comments_count","limit":50,"access_token":ptok}
        ign = 0
        while url and ign < 200:
            j = requests.get(url, params=params, timeout=40).json()
            if "error" in j: break
            stop = False
            for m in j.get("data", []):
                t = dt(m["timestamp"])
                if t < cut: stop = True; break
                typ = (m.get("media_product_type") or m.get("media_type") or "").lower()
                lk = m.get("like_count",0) or 0; cm = m.get("comments_count",0) or 0
                cl, hs, em = feats(m.get("caption"))
                reach = None
                if A.reach and ign < 40:
                    ii = requests.get(f"{G}/{m['id']}/insights", params={"metric":"reach","access_token":ptok}, timeout=25).json()
                    reach = (ii.get("data",[{}])[0].get("values",[{}])[0].get("value") if "data" in ii else None)
                posts.append(dict(brand=nm, plat="IG", type=typ, t=t, eng=lk+cm, likes=lk, com=cm, sh=0, caplen=cl, tags=hs, emoji=em, reach=reach, msg=(m.get("caption") or "")[:70]))
                ign += 1
            if stop: break
            url = j.get("paging",{}).get("next"); params=None

if not posts: sys.exit("Nicio postare organică găsită în fereastră.")
def avg(xs): return round(statistics.mean(xs),1) if xs else 0
def med(xs): return round(statistics.median(xs),1) if xs else 0
print(f"=== ANALYTICS SOCIAL ORGANIC — ultimele {A.days} zile — {len(posts)} postări ===\n")
# per brand
print(f"{'BRAND':30} {'#post':>5} {'/săpt':>6} {'eng.med':>7} {'top':>5}  mix tip")
bysb = {}
for p in posts: bysb.setdefault((p['brand'],p['plat']),[]).append(p)
for (b,pl),ps in sorted(bysb.items(), key=lambda kv:-sum(x['eng'] for x in kv[1])):
    types = {}
    for x in ps: types[x['type']] = types.get(x['type'],0)+1
    mix = ",".join(f"{k}:{v}" for k,v in sorted(types.items(),key=lambda z:-z[1])[:3])
    print(f"{(b[:26]+' '+pl):30} {len(ps):>5} {len(ps)/(A.days/7):>6.1f} {avg([x['eng'] for x in ps]):>7} {max(x['eng'] for x in ps):>5}  {mix}")
# content type
print(f"\n--- ENGAGEMENT MEDIU pe TIP conținut ---")
byt = {}
for p in posts: byt.setdefault((p['plat'],p['type']),[]).append(p['eng'])
for (pl,t),e in sorted(byt.items(), key=lambda kv:-avg(kv[1])):
    if len(e)>=3: print(f"  {pl} {t:14} n={len(e):>3}  eng.med={avg(e):>6}  median={med(e)}")
# timing
print(f"\n--- pe ZI a săptămânii (eng mediu) ---")
byd = {}
for p in posts: byd.setdefault(p['t'].astimezone().strftime("%a"),[]).append(p['eng'])
order=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
for d in order:
    if d in byd: print(f"  {d}  n={len(byd[d]):>3}  eng.med={avg(byd[d]):>6}")
# caption length
print(f"\n--- LUNGIME caption vs engagement ---")
buck = {"scurt <80":[], "mediu 80-150":[], "lung >150":[]}
for p in posts:
    k = "scurt <80" if p['caplen']<80 else ("mediu 80-150" if p['caplen']<=150 else "lung >150")
    buck[k].append(p['eng'])
for k,e in buck.items():
    if e: print(f"  {k:14} n={len(e):>3}  eng.med={avg(e):>6}")
# hashtags
print(f"\n--- HASHTAG-uri vs engagement ---")
hb = {"0":[], "1-3":[], "4+":[]}
for p in posts:
    k = "0" if p['tags']==0 else ("1-3" if p['tags']<=3 else "4+")
    hb[k].append(p['eng'])
for k,e in hb.items():
    if e: print(f"  {k:4} hashtag  n={len(e):>3}  eng.med={avg(e):>6}")
# top posts
print(f"\n--- TOP 12 postări (după engagement) ---")
for p in sorted(posts, key=lambda x:-x['eng'])[:12]:
    r = f" reach={p.get('reach')}" if p.get('reach') is not None else ""
    print(f"  {p['t'].strftime('%Y-%m-%d')} {p['plat']} {p['type'][:8]:8} eng={p['eng']:>4} (L{p['likes']}/C{p['com']}/S{p['sh']}){r} [{p['brand'][:14]}] {p['msg']!r}")
