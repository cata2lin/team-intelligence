# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""artevita_gads_upload.py — offline conversions gclid pt Artevita via DATA MANAGER API.
Sursă = Shopify Admin (client_credentials din artevita-bi/.env). gclid din customerJourneySummary
(landing page auto-tagged) SAU customAttributes. Upload email-hash + gclid pe conversion action-ul
Artevita (7757218711), prin MCC. DRY by default; --apply scrie real. Idempotent (SQLite).
"""
import os, sys, re, json, hashlib, sqlite3, argparse, subprocess, datetime as dt
import requests
from urllib.parse import urlsplit, parse_qs

CID = "6190561593"; MCC = "7467110480"; CONV = "7757218711"
DM_INGEST = "https://datamanager.googleapis.com/v1/events:ingest"
ARTEVITA_ENV = "/Users/gheorghebeschea/Downloads/Scripturi/artevita-bi/.env"

def load_env(p):
    d={}
    for line in open(p, encoding="utf-8"):
        line=line.strip()
        if line and not line.startswith("#") and "=" in line:
            k,v=line.split("=",1); d[k.strip()]=v.strip().strip('"').strip("'")
    return d

def kb(k):
    v=os.environ.get(k)
    if v: return v
    for c in (os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),):
        if os.path.exists(c):
            return subprocess.run(["uv","run",c,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
    return ""

def sha(s): return hashlib.sha256((s or "").strip().lower().encode()).hexdigest()

def shopify_token(cfg):
    return requests.post(f"https://{cfg['SHOPIFY_SHOP_DOMAIN']}/admin/oauth/access_token",
        data={"grant_type":"client_credentials","client_id":cfg["SHOPIFY_CLIENT_ID"],"client_secret":cfg["SHOPIFY_CLIENT_SECRET"]},timeout=30).json()["access_token"]

def extract_gclid(landing, custattrs):
    # 1) din customAttributes (dacă vreun snippet le pune)
    for k in ("gclid","gbraid","wbraid"):
        if custattrs.get(k): return (k, custattrs[k])
    # 2) din URL-ul de aterizare (auto-tagging Google)
    if landing:
        qs=parse_qs(urlsplit(landing).query)
        for k in ("gclid","gbraid","wbraid"):
            if qs.get(k): return (k, qs[k][0])
    return None

def fetch(cfg, tok, days):
    H={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}
    URL=f"https://{cfg['SHOPIFY_SHOP_DOMAIN']}/admin/api/{cfg.get('SHOPIFY_API_VERSION','2026-04')}/graphql.json"
    since=(dt.date.today()-dt.timedelta(days=days)).isoformat()
    Q=('query($c:String){ orders(first:100, after:$c, sortKey:CREATED_AT, reverse:true, query:"created_at:>=%s"){ '
       'pageInfo{hasNextPage endCursor} nodes{ id name createdAt displayFinancialStatus '
       'currentTotalPriceSet{shopMoney{amount currencyCode}} customer{email} '
       'customAttributes{key value} customerJourneySummary{ firstVisit{ landingPage } } } } }') % since
    out=[]; after=None
    while True:
        r=requests.post(URL,headers=H,json={"query":Q,"variables":{"c":after}},timeout=60).json()
        if r.get("errors"): sys.stderr.write("Shopify err: "+json.dumps(r["errors"])[:300]+"\n"); break
        conn=r["data"]["orders"]
        for o in conn["nodes"]:
            email=(o.get("customer") or {}).get("email") or ""
            price=float(o["currentTotalPriceSet"]["shopMoney"]["amount"] or 0)
            cur=o["currentTotalPriceSet"]["shopMoney"]["currencyCode"]
            ca={x["key"].lower():(x.get("value") or "").strip() for x in (o.get("customAttributes") or [])}
            landing=((o.get("customerJourneySummary") or {}).get("firstVisit") or {}).get("landingPage")
            out.append(dict(orderId=o["id"].split("/")[-1], name=o["name"], email=email, price=price, currency=cur,
                            ts=o["createdAt"], fin=o.get("displayFinancialStatus"),
                            gclid=extract_gclid(landing, ca), landing=landing))
        if conn["pageInfo"]["hasNextPage"]: after=conn["pageInfo"]["endCursor"]
        else: break
    return out

def dm_token():
    j=requests.post("https://oauth2.googleapis.com/token",timeout=30,data={"grant_type":"refresh_token",
        "client_id":kb("YOUTUBE_OAUTH_CLIENT_ID"),"client_secret":kb("YOUTUBE_OAUTH_CLIENT_SECRET"),
        "refresh_token":kb("DATAMANAGER_REFRESH_TOKEN")}).json()
    if "access_token" not in j: sys.exit("OAuth datamanager fail: "+str(j.get("error_description") or j))
    return j["access_token"]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=14); ap.add_argument("--vat",type=float,default=1.0,help="împarte valoarea la VAT (1.0=gross)")
    ap.add_argument("--marker-db",default=os.path.join(os.path.dirname(os.path.abspath(__file__)),"artevita_gads_uploaded.sqlite"))
    ap.add_argument("--apply",action="store_true"); ap.add_argument("--validate-only",action="store_true")
    a=ap.parse_args()
    cfg=load_env(ARTEVITA_ENV)
    tok=shopify_token(cfg)
    orders=fetch(cfg,tok,a.days)
    paid=[o for o in orders if o["fin"] in ("PAID","PARTIALLY_REFUNDED") and o["email"] and o["price"]>0]
    db=sqlite3.connect(a.marker_db); db.execute("CREATE TABLE IF NOT EXISTS conv(order_id TEXT PRIMARY KEY, ts TEXT, val REAL)"); db.commit()
    done={r[0] for r in db.execute("SELECT order_id FROM conv")}
    fresh=[o for o in paid if o["orderId"] not in done]
    gcov=sum(1 for o in fresh if o["gclid"])
    print(f"=== Artevita gclid upload · {a.days}z · conv {CONV} ===")
    print(f"comenzi total: {len(orders)} | plătite cu email+val: {len(paid)} | noi: {len(fresh)} | cu gclid: {gcov}/{len(fresh)}")
    if fresh[:1]:
        s=fresh[0]; print(f"  sample: {s['name']} val={s['price']}{s['currency']} gclid={s['gclid']} landing={(s['landing'] or '')[:60]}")
    if not a.apply:
        print("DRY-RUN — nimic trimis (plumbing OK dacă n-au fost erori). Adaugă --apply."); return
    if not fresh: print("nimic de trimis."); return
    digits=lambda s:"".join(c for c in str(s) if c.isdigit())
    dest={"operatingAccount":{"accountType":"GOOGLE_ADS","accountId":digits(CID)},
          "loginAccount":{"accountType":"GOOGLE_ADS","accountId":digits(MCC)},
          "productDestinationId":CONV}
    def ev(o):
        e={"eventTimestamp":o["ts"],"transactionId":o["orderId"],"conversionValue":round(o["price"]/a.vat,2),
           "currency":o["currency"],"eventSource":"WEB","userData":{"userIdentifiers":[{"emailAddress":sha(o["email"])}]}}
        if o["gclid"]: e["adIdentifiers"]={o["gclid"][0]:o["gclid"][1]}
        return e
    H={"Authorization":f"Bearer {dm_token()}","Content-Type":"application/json"}
    body={"destinations":[dest],"encoding":"HEX","events":[ev(o) for o in fresh],
          "consent":{"adUserData":"CONSENT_GRANTED","adPersonalization":"CONSENT_GRANTED"}}
    if a.validate_only: body["validateOnly"]=True
    r=requests.post(DM_INGEST,headers=H,json=body,timeout=120)
    if r.status_code!=200: print("INGEST HTTP",r.status_code,r.text[:500]); sys.exit(1)
    if not a.validate_only:
        for o in fresh: db.execute("INSERT OR REPLACE INTO conv VALUES(?,?,?)",(o["orderId"],o["ts"],o["price"]))
        db.commit()
    print(f"INGEST {'VALIDAT' if a.validate_only else 'trimis'}: {len(fresh)} · requestId={r.json().get('requestId','?')}")

if __name__=="__main__": main()
