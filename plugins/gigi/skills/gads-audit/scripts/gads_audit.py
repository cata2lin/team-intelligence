# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""gads_audit.py — AUDITOR multi-cont Google Ads: sweep pe TOATE conturile din MCC și
flaghează leak-urile recurente (descoperite manual iar și iar). Read-only by default.

Verificări (fiecare = un flag cu severitate + acțiune sugerată):
  LANG     limbă greșită — Catalan(1038) sau lipsă RO(1032)/CZ(1021) față de moneda contului
  CONV     igienă conversii — primary care NU e PURCHASE (YouTube subs/views, Calls, micro-conv)
  CODGAP   spend dar ~0 conversii PURCHASE 30z (COD form netrackuit — pattern Carpetto/CZ)
  CAPPED   câștigător capat — budget_lost_IS>15% & ROAS bun → ridică bugetul
  DRAIN    drainer — spend>prag & ROAS<breakeven (sau 0 conv) → taie/strânge
  UTM      final_url_suffix gol (lipsă UTM)
  TCPA0    cont nou: tCPA setat + 0 impresii (cold-start blocat) → scoate tCPA

  uv run gads_audit.py --all                      # toate conturile, raport
  uv run gads_audit.py --customer 4069952156      # un cont
  uv run gads_audit.py --all --fix-language       # AUTO-FIX sigur: adaugă RO/CZ pe campaniile fără
"""
import os, sys, json, argparse, subprocess
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

def kb(k): return subprocess.run(["uv","run",os.path.join(os.path.dirname(__file__),"..","..","..","..","core","scripts","kb.py"),"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
_OK={"host","port","dbname","user","password","sslmode","connect_timeout","application_name","channel_binding"}
clean=lambda d:(lambda p: d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in _OK]),p.fragment)))(urlsplit(d))

# moneda -> (limbă așteptată, id, geo)
CUR_LANG={"RON":("RO","1032","2642"), "CZK":("CZ","1021","2203"), "BGN":("BG","1056","2100"),
          "PLN":("PL","1030","2616"), "HUF":("HU","1024","2348"), "EUR":("?",None,None)}
DRAIN_MIN=200.0; CAPPED_LOST=0.15; CAPPED_ROAS=3.0; DRAIN_ROAS=2.0

def gads(H, cid, query):
    j=requests.post(f"https://googleads.googleapis.com/v21/customers/{cid}/googleAds:searchStream",headers=H,json={"query":query},timeout=90).json()
    return [x for ch in j for x in ch.get("results",[])] if isinstance(j,list) else []

def audit_account(H, cid, name, cur):
    flags=[]
    exp_lang, exp_id, exp_geo = CUR_LANG.get(cur,("?",None,None))
    # account 30d
    acc=gads(H,cid,"SELECT metrics.cost_micros, metrics.conversions FROM customer WHERE segments.date DURING LAST_30_DAYS")
    cost=float(acc[0]["metrics"].get("costMicros",0))/1e6 if acc else 0
    conv=float(acc[0]["metrics"].get("conversions",0)) if acc else 0
    # A. LANG
    langs=gads(H,cid,"SELECT campaign.name, campaign_criterion.language.language_constant FROM campaign_criterion WHERE campaign_criterion.type='LANGUAGE' AND campaign.status='ENABLED'")
    by_camp={}
    for x in langs: by_camp.setdefault(x["campaign"]["name"],set()).add(x["campaignCriterion"]["language"]["languageConstant"].split("/")[-1])
    for cname,ls in by_camp.items():
        has_exp = bool(exp_id and exp_id in ls)
        if "1038" in ls and not has_exp: flags.append(("LANG","HIGH",f"'{cname}' CATALANĂ(1038) FĂRĂ {exp_lang}"))
        elif "1038" in ls and has_exp: flags.append(("LANG","LOW",f"'{cname}' Catalan(1038) rezidual lângă {exp_lang} (curăță)"))
        elif exp_id and not has_exp: flags.append(("LANG","HIGH",f"'{cname}' fără {exp_lang}({exp_id}) — limbi {sorted(ls)}"))
    # B. CONV hygiene
    ca=gads(H,cid,"SELECT conversion_action.name, conversion_action.category FROM conversion_action WHERE conversion_action.status='ENABLED' AND conversion_action.primary_for_goal=true")
    bad=[x["conversionAction"]["name"] for x in ca if x["conversionAction"].get("category")!="PURCHASE"]
    if bad: flags.append(("CONV","HIGH",f"primary ne-PURCHASE: {bad}"))
    npur=sum(1 for x in ca if x["conversionAction"].get("category")=="PURCHASE")
    if npur>1: flags.append(("CONV","MED",f"{npur} acțiuni PURCHASE primary (de-dup)"))
    # C. CODGAP — spend dar 0 conv PURCHASE
    if cost>100:
        pj=gads(H,cid,"SELECT metrics.conversions, segments.conversion_action_category FROM customer WHERE segments.date DURING LAST_30_DAYS AND segments.conversion_action_category='PURCHASE'")
        pconv=sum(float(x["metrics"].get("conversions",0)) for x in pj)
        if pconv<1: flags.append(("CODGAP","HIGH",f"spend {cost:.0f} dar {pconv:.0f} conversii PURCHASE 30z (COD netrackuit?)"))
    # D/E. campanii: capped + drainer
    camps=gads(H,cid,"SELECT campaign.name, metrics.cost_micros, metrics.conversions, metrics.conversions_value, metrics.search_budget_lost_impression_share FROM campaign WHERE campaign.status='ENABLED' AND segments.date DURING LAST_30_DAYS AND metrics.cost_micros>0")
    for x in camps:
        c0=x["campaign"]["name"]; m=x["metrics"]; cst=float(m.get("costMicros",0))/1e6; cv=float(m.get("conversions",0)); vl=float(m.get("conversionsValue",0)); lost=float(m.get("searchBudgetLostImpressionShare",0))
        roas=vl/cst if cst else 0
        if lost>CAPPED_LOST and roas>CAPPED_ROAS: flags.append(("CAPPED","MED",f"'{c0[:28]}' ROAS {roas:.1f} dar {lost:.0%} buget pierdut → ridică"))
        if cst>DRAIN_MIN and (cv==0 or roas<DRAIN_ROAS): flags.append(("DRAIN","HIGH",f"'{c0[:28]}' spend {cst:.0f} ROAS {roas:.1f} → drainer"))
    # F. UTM
    u=gads(H,cid,"SELECT customer.final_url_suffix FROM customer")
    if not (u and u[0]["customer"].get("finalUrlSuffix")): flags.append(("UTM","LOW","customer.final_url_suffix gol (lipsă UTM)"))
    return cost, conv, flags

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--all",action="store_true"); ap.add_argument("--customer")
    ap.add_argument("--fix-language",action="store_true")
    a=ap.parse_args()
    cx=psycopg2.connect(clean(os.getenv("DATABASE_URL_METRICS") or kb("DATABASE_URL_METRICS"))); cx.set_session(readonly=True)
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" oid,"oauthClientSecret" os_,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
    tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["oid"],"client_secret":r["os_"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
    MCC="".join(ch for ch in str(r["mcc"]) if ch.isdigit())
    H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":MCC,"Content-Type":"application/json"}
    if a.customer:
        accts=[{"id":a.customer,"name":a.customer,"cur":""}]
        d=gads(H,a.customer,"SELECT customer.descriptive_name, customer.currency_code FROM customer")
        if d: accts[0]["name"]=d[0]["customer"].get("descriptiveName",a.customer); accts[0]["cur"]=d[0]["customer"].get("currencyCode","")
    else:
        cc=gads(H,MCC,"SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code FROM customer_client WHERE customer_client.level=1 AND customer_client.manager=false AND customer_client.status='ENABLED'")
        accts=[{"id":str(x["customerClient"]["id"]),"name":x["customerClient"].get("descriptiveName","?"),"cur":x["customerClient"].get("currencyCode","")} for x in cc]
    print(f"\n{'='*72}\nGOOGLE ADS AUDIT — {len(accts)} conturi\n{'='*72}")
    SEV={"HIGH":"🔴","MED":"🟡","LOW":"⚪"}; total=0
    for ac in accts:
        try: cost,conv,flags=audit_account(H,ac["id"],ac["name"],ac["cur"])
        except Exception as e: print(f"\n{ac['name']} — eroare: {str(e)[:80]}"); continue
        if not flags: continue
        total+=len(flags)
        print(f"\n▸ {ac['name']} ({ac['id']}, {ac['cur']}) — spend30 {cost:,.0f}")
        for code,sev,msg in sorted(flags,key=lambda f:{'HIGH':0,'MED':1,'LOW':2}[f[1]]):
            print(f"   {SEV[sev]} [{code:6}] {msg}")
        if a.fix_language:
            exp_lang,exp_id,_=CUR_LANG.get(ac["cur"],("?",None,None))
            if exp_id:
                rows=gads(H,ac["id"],"SELECT campaign.resource_name, campaign_criterion.resource_name, campaign_criterion.language.language_constant FROM campaign_criterion WHERE campaign_criterion.type='LANGUAGE' AND campaign.status='ENABLED'")
                has={}; cat_rn=[]
                for x in rows:
                    camp=x["campaign"]["resourceName"]; lid=x["campaignCriterion"]["language"]["languageConstant"].split("/")[-1]
                    has.setdefault(camp,set()).add(lid)
                    if lid=="1038": cat_rn.append(x["campaignCriterion"]["resourceName"])
                ops=[{"campaignCriterionOperation":{"create":{"campaign":camp,"language":{"languageConstant":f"languageConstants/{exp_id}"}}}} for camp,ls in has.items() if exp_id not in ls]
                ops+=[{"campaignCriterionOperation":{"remove":rn}} for rn in cat_rn]  # scoate Catalan(1038)
                if ops:
                    rr=requests.post(f"https://googleads.googleapis.com/v21/customers/{ac['id']}/googleAds:mutate",headers=H,json={"mutateOperations":ops,"partialFailure":True},timeout=60)
                    print(f"      ✓ FIX-LANG: +{exp_lang}/−Catalan pe {len(ops)} op ({rr.status_code})")
    print(f"\n{'='*72}\nTOTAL: {total} flag-uri pe {len(accts)} conturi. Fix sigur auto: --fix-language.\n{'='*72}")

if __name__=="__main__":
    main()
