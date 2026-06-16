# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Google Ads Anomaly Detector — compare the last few complete days vs a baseline window, per
ENABLED campaign + account-wide, and flag what broke: spend spike/drop, ROAS drop, CPA rise,
CPC rise, click/impression collapse, and ZERO conversions where there were some (tracking/feed).
Read-only. Run daily.

    uv run anomaly_detector.py --customer 5229815058
    uv run anomaly_detector.py --customer 7566352958 --recent 1 --baseline 14
"""
import os, sys, argparse, datetime, collections
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API=os.environ.get("GADS_API_VERSION","v21")
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))

def daily(rows_by_date, dates):
    """average per-day metrics across the given dates."""
    n=len(dates) or 1
    agg={"spend":0.0,"conv":0.0,"val":0.0,"clicks":0,"impr":0}
    for d in dates:
        m=rows_by_date.get(d,{})
        for k in agg: agg[k]+=m.get(k,0)
    for k in agg: agg[k]/=n
    s=agg
    s["roas"]=s["val"]/s["spend"] if s["spend"] else 0
    s["cpa"]=s["spend"]/s["conv"] if s["conv"] else 0
    s["cpc"]=s["spend"]/s["clicks"] if s["clicks"] else 0
    return s

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--customer",required=True)
    ap.add_argument("--recent",type=int,default=3,help="# of latest complete days = 'recent'")
    ap.add_argument("--baseline",type=int,default=14,help="# of days before that = baseline")
    ap.add_argument("--spend-dev",type=float,default=0.40,help="spend deviation to flag (0.4=±40%%)")
    ap.add_argument("--roas-drop",type=float,default=0.30)
    ap.add_argument("--cpa-rise",type=float,default=0.40)
    ap.add_argument("--cpc-rise",type=float,default=0.35)
    ap.add_argument("--min-spend",type=float,default=20.0,help="ignore tiny campaigns (baseline daily spend below this)")
    a=ap.parse_args()
    cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
    c=cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
    tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
    H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
    win=a.recent+a.baseline+2
    end=datetime.date.today(); start=end-datetime.timedelta(days=win)
    q=(f"SELECT campaign.id, campaign.name, segments.date, metrics.cost_micros, metrics.conversions, "
       f"metrics.conversions_value, metrics.clicks, metrics.impressions FROM campaign "
       f"WHERE campaign.status='ENABLED' AND segments.date BETWEEN '{start}' AND '{end}'")
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{a.customer}/googleAds:searchStream",headers=H,json={"query":q},timeout=120)
    if rr.status_code!=200: sys.exit(f"Ads API {rr.status_code}: {rr.text[:300]}")
    camps=collections.defaultdict(lambda:{"name":"","byd":collections.defaultdict(lambda:{"spend":0.0,"conv":0.0,"val":0.0,"clicks":0,"impr":0})})
    alldates=set()
    for b in rr.json():
        for row in b.get("results",[]):
            cid=row["campaign"]["id"]; dt=row["segments"]["date"]; m=row["metrics"]; alldates.add(dt)
            camps[cid]["name"]=row["campaign"]["name"]
            x=camps[cid]["byd"][dt]
            x["spend"]+=float(m.get("costMicros",0))/1e6; x["conv"]+=float(m.get("conversions",0))
            x["val"]+=float(m.get("conversionsValue",0)); x["clicks"]+=int(m.get("clicks",0)); x["impr"]+=int(m.get("impressions",0))
    dates=sorted(alldates)
    today=str(datetime.date.today())
    if dates and dates[-1]==today: dates=dates[:-1]   # drop partial 'today'
    recent=dates[-a.recent:]; baseline=dates[-a.recent-a.baseline:-a.recent]
    if not recent or not baseline: sys.exit("nu sunt destule zile de date")
    print(f"\n=== Anomalii · {a.customer} === recent {recent[0]}..{recent[-1]} vs baseline {baseline[0]}..{baseline[-1]}")

    def pct(new,old): return (new-old)/old if old else (1.0 if new else 0.0)
    alerts=[]
    # account-level rollup = sum of campaigns per day
    acct=collections.defaultdict(lambda:{"spend":0.0,"conv":0.0,"val":0.0,"clicks":0,"impr":0})
    for cid,cd in camps.items():
        for dt,x in cd["byd"].items():
            for k in acct[dt]: acct[dt][k]+=x[k]
    def check(name, byd, scope):
        R=daily(byd,recent); B=daily(byd,baseline)
        if B["spend"]<a.min_spend: return
        if B["conv"]>=1 and R["conv"]==0:
            alerts.append((90,scope,name,f"CONVERSII 0 (baseline {B['conv']:.1f}/zi) — posibil TRACKING/FEED rupt"))
        sd=pct(R["spend"],B["spend"])
        if abs(sd)>=a.spend_dev:
            alerts.append((70 if sd>0 else 55,scope,name,f"spend {'+'if sd>0 else ''}{sd*100:.0f}% ({B['spend']:.0f}→{R['spend']:.0f} lei/zi)"))
        if R["conv"]>0 and B["conv"]>0:
            rd=pct(R["roas"],B["roas"])
            if rd<=-a.roas_drop: alerts.append((75,scope,name,f"ROAS {rd*100:.0f}% ({B['roas']:.1f}→{R['roas']:.1f})"))
            cr=pct(R["cpa"],B["cpa"])
            if cr>=a.cpa_rise: alerts.append((65,scope,name,f"CPA +{cr*100:.0f}% ({B['cpa']:.0f}→{R['cpa']:.0f} lei)"))
        if R["clicks"]>0 and B["clicks"]>0:
            cc=pct(R["cpc"],B["cpc"])
            if cc>=a.cpc_rise: alerts.append((45,scope,name,f"CPC +{cc*100:.0f}% ({B['cpc']:.2f}→{R['cpc']:.2f} lei)"))
        ic=pct(R["impr"],B["impr"])
        if ic<=-0.5: alerts.append((50,scope,name,f"impresii {ic*100:.0f}% — colaps acoperire"))
    check("CONT (toate campaniile)",acct,"ACCOUNT")
    for cid,cd in camps.items(): check(cd["name"],cd["byd"],"campaign")
    if not alerts:
        print("  ✓ nicio anomalie peste praguri."); return
    sev={90:"🔴",75:"🔴",70:"🟠",65:"🟠",55:"🟠",50:"🟡",45:"🟡"}
    for s,scope,name,msg in sorted(alerts,reverse=True):
        ic=sev.get(s,"🟡")
        print(f"  {ic} [{scope:8s}] {name[:34]:34s} {msg}")

if __name__=="__main__":
    main()
