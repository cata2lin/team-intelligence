# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Switch a campaign to Maximize Clicks (TargetSpend) with a CPC ceiling — fixes cold-start non-serving. --apply to run."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v21"; CID=os.environ.get("CIDARG","7566352958"); CAMP=os.environ.get("CAMPARG","23927269391")
CPC_CEIL=os.environ.get("CPCARG","3000000")  # 3.0 RON cap
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
body={"operations":[{"update":{"resourceName":f"customers/{CID}/campaigns/{CAMP}","targetSpend":{"cpcBidCeilingMicros":CPC_CEIL}},"updateMask":"target_spend.cpc_bid_ceiling_micros"}],"validateOnly":(not apply)}
rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/campaigns:mutate",headers=H,json=body,timeout=60)
print(("APLICAT" if apply else "DRY-RUN"),"| HTTP",rr.status_code)
print("  Max Clicks (cap CPC", int(CPC_CEIL)/1e6,"RON) pe campania",CAMP if rr.status_code==200 else "", "" if rr.status_code==200 else rr.text[:400])
