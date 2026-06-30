# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""TikTok Business API -> metrics warehouse (tiktok_ad_insights_daily + tiktok_campaign_insights_daily).
Înlocuiește sync-ul mort de pe Vercel (oprit 06-19). Trage DIRECT cu tokenurile din tiktok_access_tokens.
  uv run tiktok_warehouse_sync.py --days 30            # DRY-RUN (nu scrie)
  uv run tiktok_warehouse_sync.py --days 30 --apply    # upsert în warehouse
Secrete din env (VPS/cron) sau KB. TikTok stat_time_day: max 30 zile/cerere -> --days clampat la 30."""
import os, sys, json, time, argparse, subprocess, datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import requests, psycopg2
from psycopg2.extras import execute_values

def kb(k):
    v=os.environ.get(k)
    if v: return v
    c="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
    if not os.path.exists(c): c=os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
    try: return subprocess.run(["uv","run",c,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
    except Exception: return ""
def clean(dsn):
    p=urlsplit(dsn); OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: dsn=urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    return dsn

AP=argparse.ArgumentParser(); AP.add_argument("--days",type=int,default=30); AP.add_argument("--apply",action="store_true"); A=AP.parse_args()
days=min(A.days,30)
DSN=clean(kb("DATABASE_URL_METRICS"))
cx=psycopg2.connect(DSN); cx.set_session(readonly=not A.apply); cu=cx.cursor()
# FX USD->RON
cu.execute("SELECT rate FROM fx_rates WHERE \"fromCurrency\"='USD' AND \"toCurrency\"='RON' ORDER BY \"rateDate\" DESC LIMIT 1")
r=cu.fetchone(); USD=float(r[0]) if r else 4.601
def fx(cur): return USD if cur=="USD" else 1.0
# active advertisers + token
cu.execute("""SELECT ta.id, ta."tikTokAccountId", ta.name, ta.currency, tt."accessToken"
              FROM tiktok_ad_accounts ta JOIN tiktok_access_tokens tt ON tt.id=ta."tokenId"
              WHERE ta."isActive" AND tt."isActive" """)
ADV=cu.fetchall()
end=datetime.date.today()-datetime.timedelta(days=1); start=end-datetime.timedelta(days=days-1)
B="https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"
MET=["spend","impressions","clicks","ctr","cpc","cpm","complete_payment","complete_payment_roas"]
def report(tok,adv,level,dims):
    out=[]; page=1
    while True:
        p={"advertiser_id":adv,"report_type":"BASIC","data_level":level,"dimensions":json.dumps(dims),
           "metrics":json.dumps(MET),"start_date":str(start),"end_date":str(end),"page":page,"page_size":1000}
        for att in range(4):
            j=requests.get(B,headers={"Access-Token":tok},params=p,timeout=90).json()
            if j.get("code")==0: break
            if att==3: return out,j.get("message","?")
            time.sleep(2*(att+1))
        d=j["data"]; out+=d["list"]
        pi=d.get("page_info",{})
        if page>=pi.get("total_page",1): break
        page+=1
    return out,None
def num(m,k): 
    try: return float(m.get(k) or 0)
    except: return 0.0
adrows=[]; camprows=[]; errs=[]; per=[]
for aid,advid,name,cur,tok in ADV:
    f=fx(cur)
    al,e1=report(tok,advid,"AUCTION_ADVERTISER",["advertiser_id","stat_time_day"])
    if e1: errs.append((name,"adv",e1))
    sp_tot=0; pu_tot=0
    for it in al:
        d=it["dimensions"]["stat_time_day"][:10]; m=it["metrics"]
        sp=num(m,"spend"); roas=num(m,"complete_payment_roas"); pu=num(m,"complete_payment"); val=sp*roas
        sp_tot+=sp; pu_tot+=pu
        adrows.append((f"{aid}_{d}",aid,d,sp,round(sp*f,2),int(num(m,'impressions')),int(num(m,'clicks')),
                       num(m,'ctr'),num(m,'cpc'),num(m,'cpm'),int(pu),round(val,2),round(val*f,2),roas,
                       round(sp/pu,2) if pu else 0,cur))
    cl,e2=report(tok,advid,"AUCTION_CAMPAIGN",["campaign_id","stat_time_day"])
    if e2: errs.append((name,"camp",e2))
    # campaign names
    cids=list({it["dimensions"]["campaign_id"] for it in cl})
    nm={}
    for i in range(0,len(cids),100):
        cr=requests.get("https://business-api.tiktok.com/open_api/v1.3/campaign/get/",headers={"Access-Token":tok},
            params={"advertiser_id":advid,"filtering":json.dumps({"campaign_ids":cids[i:i+100]}),
                    "fields":json.dumps(["campaign_id","campaign_name"]),"page_size":100},timeout=60).json()
        for x in cr.get("data",{}).get("list",[]): nm[x["campaign_id"]]=x.get("campaign_name","")
    for it in cl:
        d=it["dimensions"]["stat_time_day"][:10]; cid=it["dimensions"]["campaign_id"]; m=it["metrics"]
        sp=num(m,"spend"); roas=num(m,"complete_payment_roas"); pu=num(m,"complete_payment"); val=sp*roas
        camprows.append((f"{aid}_{cid}_{d}",aid,cid,nm.get(cid,""),d,sp,round(sp*f,2),int(num(m,'impressions')),
                         int(num(m,'clicks')),num(m,'ctr'),num(m,'cpc'),num(m,'cpm'),int(pu),round(val,2),round(val*f,2),
                         roas,round(sp/pu,2) if pu else 0,cur))
    per.append((name,len(al),round(sp_tot*f),int(pu_tot)))
print(f"=== DRY-RUN === fereastra {start}→{end} | {len(ADV)} advertisere | USD×{USD}")
print(f"rânduri pregătite: ad_insights={len(adrows)}  campaign_insights={len(camprows)}")
tot_ron=sum(p[2] for p in per)
print(f"spend total fereastră: {tot_ron:,} RON | conv: {sum(p[3] for p in per):,}")
print("\nTop 12 advertisere după spend:")
for n,nd,ron,pu in sorted(per,key=lambda x:-x[2])[:12]:
    print(f"  {n[:34]:34} {nd:>3}z {ron:>9,} RON {pu:>5} conv")
if errs:
    print(f"\n⚠ {len(errs)} erori (advertiser,level,msg):")
    for n,l,m in errs[:8]: print(f"  {n[:30]} [{l}] {m[:60]}")
if A.apply and adrows:
    adtmpl="("+",".join(["%s"]*len(adrows[0]))+",now())"
    camptmpl="("+",".join(["%s"]*len(camprows[0]))+",now())" if camprows else None
    execute_values(cu,
      'INSERT INTO tiktok_ad_insights_daily (id,"adAccountId",date,spend,"spendRon",impressions,clicks,ctr,cpc,cpm,'
      'purchases,"purchaseValue","purchaseValueRon",roas,"costPerPurchase",currency,"updatedAt") VALUES %s '
      'ON CONFLICT ("adAccountId",date) DO UPDATE SET spend=EXCLUDED.spend,"spendRon"=EXCLUDED."spendRon",'
      'impressions=EXCLUDED.impressions,clicks=EXCLUDED.clicks,ctr=EXCLUDED.ctr,cpc=EXCLUDED.cpc,cpm=EXCLUDED.cpm,'
      'purchases=EXCLUDED.purchases,"purchaseValue"=EXCLUDED."purchaseValue","purchaseValueRon"=EXCLUDED."purchaseValueRon",'
      'roas=EXCLUDED.roas,"costPerPurchase"=EXCLUDED."costPerPurchase",currency=EXCLUDED.currency,"updatedAt"=now()',
      adrows, template=adtmpl, page_size=1000)
    execute_values(cu,
      'INSERT INTO tiktok_campaign_insights_daily (id,"adAccountId","campaignId","campaignName",date,spend,"spendRon",'
      'impressions,clicks,ctr,cpc,cpm,purchases,"purchaseValue","purchaseValueRon",roas,"costPerPurchase",currency,"updatedAt") VALUES %s '
      'ON CONFLICT ("adAccountId","campaignId",date) DO UPDATE SET "campaignName"=EXCLUDED."campaignName",spend=EXCLUDED.spend,'
      '"spendRon"=EXCLUDED."spendRon",impressions=EXCLUDED.impressions,clicks=EXCLUDED.clicks,ctr=EXCLUDED.ctr,cpc=EXCLUDED.cpc,'
      'cpm=EXCLUDED.cpm,purchases=EXCLUDED.purchases,"purchaseValue"=EXCLUDED."purchaseValue","purchaseValueRon"=EXCLUDED."purchaseValueRon",'
      'roas=EXCLUDED.roas,"costPerPurchase"=EXCLUDED."costPerPurchase",currency=EXCLUDED.currency,"updatedAt"=now()',
      camprows, template=camptmpl, page_size=1000)
    cx.commit(); print(f"\n✅ APPLIED — upsert {len(adrows)} ad + {len(camprows)} campaign rânduri.")
else:
    print("\n(DRY-RUN — nimic scris. Adaugă --apply ca să scrii în warehouse.)")
cx.close()
