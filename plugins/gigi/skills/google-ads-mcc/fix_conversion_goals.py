# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Make ONLY the Purchase goal biddable on an account (account-default conversion goals).
Sets customer_conversion_goal.biddable=false for every non-PURCHASE category/origin and
biddable=true for PURCHASE — so campaigns bid and report on purchases only.
Dry-run by default; --apply to execute.   CIDARG env overrides the customer id."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v20"; CID=os.environ.get("CIDARG","5229815058")
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
apply="--apply" in sys.argv

# current goals
q={"query":"SELECT customer_conversion_goal.category, customer_conversion_goal.origin, customer_conversion_goal.biddable, customer_conversion_goal.resource_name FROM customer_conversion_goal"}
rows=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:search",headers=H,json=q,timeout=60).json().get("results",[])
ops=[]
print("goal-uri cont (categorie · origin · biddable acum → nou):")
for row in rows:
    g=row["customerConversionGoal"]
    want = (g.get("category")=="PURCHASE")
    cur  = g.get("biddable")
    print(f"  {g.get('category','?'):16} {g.get('origin','?'):12} {str(cur):>5} → {want}")
    ops.append({"update":{"resourceName":g["resourceName"],"biddable":want},"updateMask":"biddable"})
body={"operations":ops,"validateOnly":(not apply)}
rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/customerConversionGoals:mutate",headers=H,json=body,timeout=60)
print(("APLICAT" if apply else "DRY-RUN"),"| HTTP",rr.status_code)
if rr.status_code!=200: print(rr.text[:800])
else: print(f"  {len(ops)} goal-uri setate (doar PURCHASE biddable)")
