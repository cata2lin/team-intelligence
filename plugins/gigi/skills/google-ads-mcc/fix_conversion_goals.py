# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Igiena conversiilor pe un cont:
 A) doar categoria PURCHASE biddable (customer_conversion_goal) — restul (PAGE_VIEW/ADD_TO_CART/
    BEGIN_CHECKOUT/DEFAULT) pe false, ca bidding-ul să optimizeze pe achiziții, nu micro-conversii.
 B) DE-DUP: dacă mai multe acțiuni categorie PURCHASE numără (include_in_conversions_metric=true),
    lasă să numere DOAR „Google Shopping App Purchase" și pune restul (ex. generic „Purchase") pe
    primary_for_goal=false — altfel se dublează achizițiile și ROAS-ul e umflat.
Dry-run by default; --apply to execute.   CIDARG env overrides the customer id.
Capcană: goal-urile YouTube (ENGAGEMENT/UNKNOWN ~ YOUTUBE_HOSTED) au biddable=None și-s NE-mutabile
(404 'Requested entity was not found') → le sărim. Verifică cross-account înainte: dacă
customer.conversion_tracking_setting.cross_account_conversion_tracking_id != NULL, schimbarea e MCC-wide."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v24"; CID=os.environ.get("CIDARG","5229815058")
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
print("A) goal-uri cont (categorie · origin · biddable acum → nou):")
for row in rows:
    g=row["customerConversionGoal"]
    cur = g.get("biddable")
    if cur is None:                                   # YouTube engagement/unknown — ne-mutabil (404), sări
        print(f"  {g.get('category','?'):16} {g.get('origin','?'):12} (None — skip ne-mutabil)"); continue
    want = (g.get("category")=="PURCHASE")
    if cur==want: continue                             # deja corect
    print(f"  {g.get('category','?'):16} {g.get('origin','?'):12} {str(cur):>5} → {want}")
    ops.append({"update":{"resourceName":g["resourceName"],"biddable":want},"updateMask":"biddable"})
if ops:
    body={"operations":ops,"validateOnly":(not apply)}
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/customerConversionGoals:mutate",headers=H,json=body,timeout=60)
    print(("  APLICAT" if apply else "  DRY-RUN"),"| HTTP",rr.status_code, "" if rr.status_code==200 else rr.text[:400])
    if rr.status_code==200: print(f"  {len(ops)} goal-uri setate (doar PURCHASE biddable)")
else:
    print("  goal-uri deja corecte")

# B) DE-DUP acțiuni PURCHASE — lasă să numere doar „Google Shopping App Purchase"
KEEP="Google Shopping App Purchase"
qa={"query":"SELECT conversion_action.resource_name, conversion_action.name, conversion_action.include_in_conversions_metric FROM conversion_action WHERE conversion_action.status='ENABLED' AND conversion_action.category='PURCHASE'"}
acts=[a["conversionAction"] for a in requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:search",headers=H,json=qa,timeout=60).json().get("results",[])]
counting=[a for a in acts if a.get("includeInConversionsMetric")]
dedup=[a for a in counting if a.get("name")!=KEEP] if any(a.get("name")==KEEP for a in counting) else counting[1:]
print(f"\nB) acțiuni PURCHASE care numără: {[a.get('name') for a in counting]}")
if dedup:
    dops=[{"update":{"resourceName":a["resourceName"],"primaryForGoal":False},"updateMask":"primary_for_goal"} for a in dedup]
    rr2=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/conversionActions:mutate",headers=H,json={"operations":dops,"validateOnly":(not apply)},timeout=60)
    print(f"  {'APLICAT' if apply else 'DRY-RUN'}: {[a.get('name') for a in dedup]} → secundar | HTTP {rr2.status_code}", "" if rr2.status_code==200 else rr2.text[:400])
else:
    print("  fără dublare (numără una singură)")
