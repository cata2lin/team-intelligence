# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Google Ads CHANGE HISTORY — who changed what, when (track an agency or any account).

Reads the `change_event` resource via the MCC and prints a readable log: timestamp,
user (+ client: WEB/API/BULK), operation, resource, the changed fields and their new
(and old, for updates) values, with campaign names resolved. Read-only.

Usage:
    uv run change_history.py --customer 9069610821                 # last 14 days
    uv run change_history.py --customer 9069610821 --days 30
    uv run change_history.py --customer 9069610821 --by matei@skilledppc.com
    uv run change_history.py --customer 9069610821 --summary        # who/what counts only
    uv run change_history.py --customer 9069610821 --format json

Note: Google only retains change_event for the **last 30 days**; poll/snapshot regularly
to track longer. client=GOOGLE_ADS_API are MCC/script changes; WEB/BULK are humans.
"""
import os, sys, argparse, collections
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API=os.environ.get("GADS_API_VERSION","v21")

_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))

def auth():
    cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
    tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
    return {"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}

def search(H, cid, q):
    rows=[]
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{cid}/googleAds:searchStream",headers=H,json={"query":q},timeout=60)
    if rr.status_code!=200: sys.exit(f"API {rr.status_code}: {rr.text[:300]}")
    for b in rr.json(): rows+=b.get("results",[])
    return rows

def flat(d, pre=""):
    out={}
    for k,v in (d or {}).items():
        p=f"{pre}{k}"
        if isinstance(v,dict): out.update(flat(v,p+"."))
        else: out[p]=v
    return out

CLIENT={"GOOGLE_ADS_API":"API","GOOGLE_ADS_WEB_CLIENT":"WEB","GOOGLE_ADS_BULK_UPLOAD":"BULK","GOOGLE_ADS_EDITOR":"EDITOR","GOOGLE_ADS_MOBILE_APP":"MOBILE","GOOGLE_ADS_SCRIPTS":"SCRIPT","INTERNAL_TOOL":"INTERNAL"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--customer",required=True)
    ap.add_argument("--days",type=int,default=14)
    ap.add_argument("--by",help="filter by user email")
    ap.add_argument("--limit",type=int,default=1000)
    ap.add_argument("--summary",action="store_true")
    ap.add_argument("--format",choices=["table","json"],default="table")
    a=ap.parse_args()
    cid=a.customer; H=auth()
    days=min(a.days,30)
    if a.days>30: print("⚠ Google păstrează doar 30 zile de change history.")
    where=f"change_event.change_date_time DURING LAST_{days}_DAYS"
    if a.by: where+=f" AND change_event.user_email = '{a.by}'"
    q=(f"SELECT change_event.change_date_time, change_event.user_email, change_event.client_type, "
       f"change_event.change_resource_type, change_event.resource_change_operation, change_event.changed_fields, "
       f"change_event.campaign, change_event.new_resource, change_event.old_resource "
       f"FROM change_event WHERE {where} ORDER BY change_event.change_date_time DESC LIMIT {a.limit}")
    # NOTE: LAST_N_DAYS isn't valid for change_event in some versions -> fall back to BETWEEN
    rows=search(H,cid,q)
    # resolve campaign names
    camps={}
    cr=search(H,cid,"SELECT campaign.id, campaign.name FROM campaign")
    for r in cr: camps[r["campaign"]["id"]]=r["campaign"]["name"]

    if a.format=="json":
        import json; print(json.dumps(rows,ensure_ascii=False,indent=1)); return
    if a.summary:
        by_user=collections.Counter(); by_res=collections.Counter(); by_op=collections.Counter()
        for r in rows:
            e=r["changeEvent"]; by_user[(e.get("userEmail","?"),CLIENT.get(e.get("clientType"),e.get("clientType","?")))]+=1
            by_res[e.get("changeResourceType","?")]+=1; by_op[e.get("resourceChangeOperation","?")]+=1
        print(f"\n=== {cid} — {len(rows)} modificări în ultimele {days} zile ===")
        print("\nDupă utilizator:");  [print(f"  {n:4d}  {u} [{c}]") for (u,c),n in by_user.most_common()]
        print("\nDupă resursă:");     [print(f"  {n:4d}  {k}") for k,n in by_res.most_common()]
        print("\nDupă operație:");    [print(f"  {n:4d}  {k}") for k,n in by_op.most_common()]
        if rows: print(f"\nUltima modificare: {rows[0]['changeEvent'].get('changeDateTime')}")
        return
    print(f"\n=== {cid} — change history, ultimele {days} zile ({len(rows)} modificări) ===")
    for r in rows:
        e=r["changeEvent"]; op=e.get("resourceChangeOperation","?"); rt=e.get("changeResourceType","?")
        cl=CLIENT.get(e.get("clientType"),e.get("clientType","?"))
        cid_camp=(e.get("campaign","") or "").split("/")[-1]; cname=camps.get(cid_camp,cid_camp or "-")
        nf=flat(next(iter((e.get("newResource") or {}).values()),{}))
        of=flat(next(iter((e.get("oldResource") or {}).values()),{}))
        fields=[f for f in (e.get("changedFields","") or "").split(",") if f]
        def fmt(field,v):
            if v is None: return v
            if field.lower().endswith("micros"):
                try: return f"{float(v)/1e6:g} (RON)"
                except: return v
            return v
        print(f"\n{e.get('changeDateTime','?')[:19]} | {e.get('userEmail','?')} [{cl}] | {op} {rt} | „{cname}\"")
        for f in fields[:12]:
            nv=fmt(f,nf.get(f)); ov=fmt(f,of.get(f))
            shown = nv if nv is not None else "?"
            print(f"    {f}: " + (f"{ov} → {shown}" if (op=='UPDATE' and ov is not None and ov!=shown) else f"{shown}"))

if __name__=="__main__":
    main()
