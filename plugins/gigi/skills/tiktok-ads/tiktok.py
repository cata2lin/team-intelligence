# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Read AND operate TikTok Ads for any team brand. Brand→accounts via the canonical Mapping
(brand_map.json); SHARED accounts (one advertiser running several brands, e.g. 'ROSSI Nails Romania')
are split by a campaign-name token (e.g. 'GT','APRECIAT') so spend is attributed correctly.
Creds (advertiser_id + token) from the `metrics` DB. Reads are free; writes are DRY-RUN unless --apply.
  DATABASE_URL_METRICS=... uv run tiktok.py accounts george-talent
  uv run tiktok.py report belasil --level campaign --range last_30d --sort roas
  uv run tiktok.py trend belasil --range last_14d
  uv run tiktok.py list belasil
  uv run tiktok.py pause belasil <campaign_id> [--apply]
  uv run tiktok.py budget belasil <campaign_id> --daily 200 [--apply]
"""
import os, sys, json, datetime, argparse, subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

_RATES = None
def rates():
    """currency -> RON multiplier (team fixed rates from KB config CURRENCY_RATES_RON; hardcoded fallback)."""
    global _RATES
    if _RATES is None:
        _RATES = {"RON":1.0,"USD":4.55,"EUR":5.24,"PLN":1.23,"HUF":0.01,"CZK":0.22}
        try:
            kb = Path.home()/".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
            v = subprocess.run(["uv","run",str(kb),"secret-get","CURRENCY_RATES_RON"], capture_output=True, text=True, timeout=30).stdout.strip()
            if v: _RATES.update({k.upper(): float(x) for k, x in json.loads(v).items()})
        except Exception: pass
    return _RATES
def _rate(cur): return rates().get((cur or "RON").upper(), 1.0)

def _kb_secret(key):
    try:
        kb = Path.home()/".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
        return subprocess.run(["uv","run",str(kb),"secret-get",key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception:
        return ""

def fx_index(currencies, start, end):
    """{(CURRENCY, date): rate→RON} from AWBprint.exchange_rates, forward-filled (per-day, dynamic)."""
    from collections import defaultdict
    need = sorted({(c or "RON").upper() for c in currencies if (c or "RON").upper() != "RON"})
    if not need: return {}
    dsn = os.environ.get("DATABASE_URL_AWBPRINT") or _kb_secret("DATABASE_URL_AWBPRINT")
    if not dsn: return {}
    s = datetime.date.fromisoformat(start); e = datetime.date.fromisoformat(end)
    try:
        cx = psycopg2.connect(_clean(dsn)); cx.set_session(readonly=True)
        with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""SELECT currency, rate_date, rate, multiplier FROM exchange_rates
                         WHERE currency = ANY(%s) AND rate_date BETWEEN %s AND %s ORDER BY currency, rate_date""",
                      (need, s - datetime.timedelta(days=10), e))
            rows = c.fetchall()
    except Exception as ex:
        sys.stderr.write(f"[tt] FX AWBprint indisponibil ({str(ex)[:80]}); folosesc curs fix din KB\n"); return {}
    by = defaultdict(list)
    for r in rows: by[r["currency"]].append((r["rate_date"], float(r["rate"]) / float(r["multiplier"] or 1)))
    out = {}
    for cur, series in by.items():
        last=None; i=0; d=s - datetime.timedelta(days=10)
        while d <= e:
            while i < len(series) and series[i][0] <= d: last=series[i][1]; i+=1
            if last is not None and s <= d <= e: out[(cur, d)] = last
            d += datetime.timedelta(days=1)
    return out

def conv(amount, cur, day, idx):
    """Convert to RON: per-day FX if available, else the fixed KB rate (fallback)."""
    cu = (cur or "RON").upper()
    if cu == "RON": return amount
    r = idx.get((cu, day)) if day else None
    return amount * (r if r else _rate(cu))

BASE = "https://business-api.tiktok.com/open_api/v1.3"
DL  = {"account":"AUCTION_ADVERTISER","campaign":"AUCTION_CAMPAIGN","adgroup":"AUCTION_ADGROUP","ad":"AUCTION_AD"}
DIM = {"account":["advertiser_id"],"campaign":["campaign_id"],"adgroup":["adgroup_id"],"ad":["ad_id"]}
NAMEF = {"campaign":"campaign_name","adgroup":"adgroup_name","ad":"ad_name"}
_PG_OK = {"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}

def _clean(d):
    p = urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() in _PG_OK]),p.fragment))
def _db():
    dsn = os.environ.get("DATABASE_URL_METRICS")
    if not dsn: sys.exit("set DATABASE_URL_METRICS")
    cx = psycopg2.connect(_clean(dsn)); cx.set_session(readonly=True); return cx

def accounts_for(brand):
    """[{adv, nm, cur, tok, filter}] — the brand's TikTok advertisers + token; filter = campaign token
    for shared accounts (None for dedicated)."""
    import brandmap
    tts = brandmap.tiktok_accounts(brand)
    if not tts: return []
    byname = {t["name"].strip().lower(): t["campaign_filter"] for t in tts}
    cx = _db()
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('''SELECT a.name nm, a."tikTokAccountId" adv, a.currency cur, t."accessToken" tok
                     FROM tiktok_ad_accounts a JOIN tiktok_access_tokens t ON t.id = a."tokenId"
                     WHERE a."isActive" AND t."isActive" AND NOT COALESCE(t."needsReauth",false)
                       AND lower(a.name) = ANY(%s)''', (list(byname.keys()),))
        rows = [dict(r) for r in c.fetchall()]
    got = {r["nm"].strip().lower() for r in rows}
    for miss in [n for n in byname if n not in got]:        # surfaces sheet/DB name mismatches (e.g. 'Esteban' vs DB 'Esteban.ro')
        sys.stderr.write(f"[tt] cont din mapping fără potrivire în DB: '{miss}'\n")
    return [{"adv":r["adv"],"nm":r["nm"],"cur":r["cur"],"tok":r["tok"],"filter":byname.get(r["nm"].strip().lower())} for r in rows]

def tk_get(path, token, params):
    import time
    out=[]; page=1
    while True:
        p=dict(params); p["page"]=page; p.setdefault("page_size",1000)
        j=None
        for attempt in range(6):
            j=requests.get(BASE+path, headers={"Access-Token":token}, params=p, timeout=90).json()
            code=j.get("code")
            if code==0: break
            transient = code in (40100,40016,50000,40001) or "rate" in str(j.get("message","")).lower() or "too many" in str(j.get("message","")).lower()
            if transient and attempt<5:
                time.sleep(min(90,5*(2**attempt))); continue
            sys.stderr.write(f"[tt] {code}: {j.get('message','')[:160]}\n"); return out
        d=j.get("data",{}); out+=d.get("list",[])
        if page>=d.get("page_info",{}).get("total_page",1): break
        page+=1
    return out
def tk_post(path, token, body):
    return requests.post(BASE+path, headers={"Access-Token":token,"Content-Type":"application/json"}, json=body, timeout=60).json()

def daterange(rng):
    rng=(rng or "last_30d").strip().lower()
    if "," in rng: s,u=rng.split(",",1); return s.strip(),u.strip()
    t=datetime.date.today()
    if rng=="today": return t.isoformat(),t.isoformat()
    if rng=="yesterday": d=t-datetime.timedelta(1); return d.isoformat(),d.isoformat()
    if rng=="this_month": return t.replace(day=1).isoformat(),t.isoformat()
    n={"last_7d":7,"last_14d":14,"last_30d":30,"last_90d":90}.get(rng,30)
    return (t-datetime.timedelta(n)).isoformat(), t.isoformat()

def _f(m,k):
    try: return float(m.get(k,0) or 0)
    except: return 0.0

def report_rows(brand, level, start, end, extra=None):
    accts=accounts_for(brand)
    if not accts: sys.exit(f"niciun cont TikTok pentru '{brand}'")
    metrics=["spend","impressions","clicks","ctr","cpm","complete_payment","complete_payment_roas"]
    if level!="account": metrics.append(NAMEF[level])
    if level=="ad": metrics.append("campaign_name")
    extra = list(extra or [])
    if "stat_time_day" not in extra: extra.append("stat_time_day")   # day-level → per-day FX conversion
    dims = DIM[level] + extra
    rows=[]
    for ac in accts:
        for r in tk_get("/report/integrated/get/", ac["tok"], {"advertiser_id":ac["adv"],"report_type":"BASIC",
                "data_level":DL[level],"dimensions":json.dumps(dims),"metrics":json.dumps(metrics),
                "start_date":start,"end_date":end}):
            r["_acct"]=ac["nm"]; r["_filter"]=ac["filter"]; r["_cur"]=ac["cur"]; rows.append(r)
    return accts, rows

def _passes(r, level):
    f=r.get("_filter")
    if not f: return True
    m=r.get("metrics",{})
    cn = m.get("campaign_name","") if level=="ad" else m.get(NAMEF.get(level,""),"")
    return f.lower() in (cn or "").lower()

def cmd_accounts(a):
    accts=accounts_for(a.brand)
    if not accts: print("(niciun cont)"); return
    print(f"Conturi TikTok pentru '{a.brand}':")
    for x in accts:
        flag=f"  ⚠partajat → filtrez campaniile cu '{x['filter']}'" if x["filter"] else ""
        print(f"  {x['adv']:22} {x['cur']:>4}  {x['nm']}{flag}")

def cmd_report(a):
    start,end=daterange(a.range)
    fetch = "campaign" if a.level=="account" else a.level
    accts,rows=report_rows(a.brand,fetch,start,end)
    idx=fx_index([x["cur"] for x in accts], start, end)
    agg={}
    for r in rows:
        if not _passes(r, fetch): continue
        m=r.get("metrics",{})
        nm = r["_acct"] if a.level=="account" else m.get(NAMEF[a.level],"?")
        day=(r.get("dimensions",{}).get("stat_time_day","") or "")[:10]
        try: dd=datetime.date.fromisoformat(day)
        except Exception: dd=None
        sp=conv(_f(m,"spend"), r.get("_cur"), dd, idx)
        rv=conv(_f(m,"spend")*_f(m,"complete_payment_roas"), r.get("_cur"), dd, idx)
        g=agg.setdefault(nm, {"spend":0,"purch":0,"rev":0,"impr":0,"clk":0})
        g["spend"]+=sp; g["rev"]+=rv; g["purch"]+=_f(m,"complete_payment")
        g["impr"]+=_f(m,"impressions"); g["clk"]+=_f(m,"clicks")
    out=[]
    for nm,g in agg.items():
        out.append(dict(name=nm, **g, roas=(g["rev"]/g["spend"] if g["spend"] else 0),
                        cpa=(g["spend"]/g["purch"] if g["purch"] else 0),
                        ctr=(g["clk"]/g["impr"]*100 if g["impr"] else 0), cpm=(g["spend"]/g["impr"]*1000 if g["impr"] else 0)))
    keyf={"roas":lambda x:-x["roas"],"purchases":lambda x:-x["purch"],"spend":lambda x:-x["spend"],"cpa":lambda x:(x["cpa"] or 9e9)}
    out.sort(key=keyf.get(a.sort,keyf["spend"]))
    print(f"# {a.brand} · TikTok · {a.level} · {start}→{end} · sort={a.sort} · RON ({'FX/zi' if idx else 'curs fix'})")
    print(f"{'nume':40} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6} {'CTR%':>5} {'CPM':>6}")
    tot=dict(spend=0,purch=0,rev=0)
    for o in out[:a.limit]:
        print(f"{o['name'][:40]:40} {o['spend']:>9.0f} {o['purch']:>6.0f} {o['rev']:>9.0f} {o['roas']:>5.2f} {o['cpa']:>6.1f} {o['ctr']:>5.2f} {o['cpm']:>6.1f}")
        for k in tot: tot[k]+=o[k]
    print(f"{'TOTAL (afișate)':40} {tot['spend']:>9.0f} {tot['purch']:>6.0f} {tot['rev']:>9.0f} {(tot['rev']/tot['spend'] if tot['spend'] else 0):>5.2f} {(tot['spend']/tot['purch'] if tot['purch'] else 0):>6.1f}")

def cmd_trend(a):
    start,end=daterange(a.range)
    accts,rows=report_rows(a.brand,"campaign",start,end,extra=["stat_time_day"])
    idx=fx_index([x["cur"] for x in accts], start, end)
    agg={}
    for r in rows:
        if not _passes(r,"campaign"): continue
        d=(r.get("dimensions",{}).get("stat_time_day","") or "")[:10]; m=r.get("metrics",{})
        try: dd=datetime.date.fromisoformat(d)
        except Exception: dd=None
        g=agg.setdefault(d,{"spend":0,"purch":0,"rev":0})
        g["spend"]+=conv(_f(m,"spend"), r.get("_cur"), dd, idx); g["purch"]+=_f(m,"complete_payment")
        g["rev"]+=conv(_f(m,"spend")*_f(m,"complete_payment_roas"), r.get("_cur"), dd, idx)
    print(f"# {a.brand} · TikTok trend zilnic · {start}→{end} · RON")
    print(f"{'zi':12} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6}")
    for d in sorted(agg):
        g=agg[d]; print(f"{d:12} {g['spend']:>9.0f} {g['purch']:>6.0f} {g['rev']:>9.0f} {(g['rev']/g['spend'] if g['spend'] else 0):>5.2f} {(g['spend']/g['purch'] if g['purch'] else 0):>6.1f}")

def cmd_list(a):
    accts=accounts_for(a.brand)
    if not accts: sys.exit("niciun cont")
    print(f"# {a.brand} · TikTok campaigns  (id · status · buget · nume)")
    for ac in accts:
        for x in tk_get("/campaign/get/", ac["tok"], {"advertiser_id":ac["adv"],
                "fields":json.dumps(["campaign_id","campaign_name","operation_status","budget","budget_mode"]),
                "filtering":json.dumps({"primary_status":"STATUS_ALL"})}):
            nm=x.get("campaign_name","")
            if ac["filter"] and ac["filter"].lower() not in nm.lower(): continue
            print(f"  {x['campaign_id']:22} {x.get('operation_status','?'):8} {str(x.get('budget','')):>8}  {nm[:44]}  [{ac['nm']}]")

def cmd_products(a):
    """Spend per PRODUCT (Nomenclator mapping) split VÂNZARE vs TEST — campaign name contains 'TEST' → TEST."""
    import prodmap
    start,end=daterange(a.range)
    accts,rows=report_rows(a.brand,"campaign",start,end)
    idx=fx_index([x["cur"] for x in accts], start, end)
    agg={}
    for r in rows:
        if not _passes(r,"campaign"): continue
        m=r.get("metrics",{}); cname=m.get("campaign_name","")
        d=(r.get("dimensions",{}).get("stat_time_day","") or "")[:10]
        try: dd=datetime.date.fromisoformat(d)
        except Exception: dd=None
        sp=conv(_f(m,"spend"), r.get("_cur"), dd, idx); rv=conv(_f(m,"spend")*_f(m,"complete_payment_roas"), r.get("_cur"), dd, idx)
        pg=prodmap.product_of("tiktok", r["_acct"], cname, "")
        g=agg.setdefault(pg, dict(s_sp=0,s_pu=0,s_rv=0,t_sp=0))
        if prodmap.is_test(cname): g["t_sp"]+=sp
        else: g["s_sp"]+=sp; g["s_pu"]+=_f(m,"complete_payment"); g["s_rv"]+=rv
    out=sorted(agg.items(), key=lambda kv:-(kv[1]["s_sp"]+kv[1]["t_sp"]))
    print(f"# {a.brand} · produse TikTok · {start}→{end} · RON   [VÂNZARE | TEST]")
    print(f"{'produs':24} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6} | {'TEST':>8}")
    T=dict(s=0,p=0,r=0,t=0)
    for pg,g in out:
        roas=g["s_rv"]/g["s_sp"] if g["s_sp"] else 0; cpa=g["s_sp"]/g["s_pu"] if g["s_pu"] else 0
        print(f"{pg[:24]:24} {g['s_sp']:>9.0f} {g['s_pu']:>6.0f} {g['s_rv']:>9.0f} {roas:>5.2f} {cpa:>6.1f} | {g['t_sp']:>8.0f}")
        T['s']+=g['s_sp']; T['p']+=g['s_pu']; T['r']+=g['s_rv']; T['t']+=g['t_sp']
    print(f"{'TOTAL vânzare':24} {T['s']:>9.0f} {T['p']:>6.0f} {T['r']:>9.0f} {(T['r']/T['s'] if T['s'] else 0):>5.2f} {'':>6} | {T['t']:>8.0f}")

def find_owner(brand, cid):
    for ac in accounts_for(brand):
        rows=tk_get("/campaign/get/", ac["tok"], {"advertiser_id":ac["adv"],
              "fields":json.dumps(["campaign_id","campaign_name","operation_status","budget"]),
              "filtering":json.dumps({"campaign_ids":[cid]})})
        if rows: return ac, rows[0]
    return None, None

def _status(a, status):
    ac,info=find_owner(a.brand, a.id)
    if not ac: sys.exit(f"campania {a.id} nu e în conturile '{a.brand}'")
    print(f"campanie {a.id}: '{info.get('campaign_name')}' · status acum {info.get('operation_status')} · cont {ac['nm']}")
    if not a.apply: print(f"DRY-RUN → aș seta {status} (adaugă --apply)"); return
    j=tk_post("/campaign/status/update/", ac["tok"], {"advertiser_id":ac["adv"],"campaign_ids":[a.id],"operation_status":status})
    print("APLICAT →", status, "|", "OK" if j.get("code")==0 else f"{j.get('code')} {j.get('message','')[:200]}")
def cmd_pause(a): _status(a,"DISABLE")
def cmd_activate(a): _status(a,"ENABLE")

def cmd_budget(a):
    ac,info=find_owner(a.brand, a.id)
    if not ac: sys.exit(f"campania {a.id} nu e în conturile '{a.brand}'")
    print(f"campanie {a.id}: '{info.get('campaign_name')}' · buget acum {info.get('budget')} · cont {ac['nm']} ({ac['cur']}) → nou: {a.daily}")
    if not a.apply: print(f"DRY-RUN → aș seta buget {a.daily} {ac['cur']} (adaugă --apply)"); return
    j=tk_post("/campaign/update/", ac["tok"], {"advertiser_id":ac["adv"],"campaign_id":a.id,"budget":float(a.daily)})
    print("APLICAT → budget", a.daily, "|", "OK" if j.get("code")==0 else f"{j.get('code')} {j.get('message','')[:200]}")

def main():
    ap=argparse.ArgumentParser(description="TikTok Ads performance + gated mutations (creds from metrics DB)")
    sub=ap.add_subparsers(dest="cmd", required=True)
    s=sub.add_parser("accounts"); s.add_argument("brand")
    s=sub.add_parser("report"); s.add_argument("brand"); s.add_argument("--level",default="campaign",choices=["account","campaign","adgroup","ad"]); s.add_argument("--range",default="last_30d"); s.add_argument("--sort",default="spend",choices=["spend","roas","purchases","cpa"]); s.add_argument("--limit",type=int,default=25)
    s=sub.add_parser("trend"); s.add_argument("brand"); s.add_argument("--range",default="last_14d")
    s=sub.add_parser("list"); s.add_argument("brand")
    s=sub.add_parser("products"); s.add_argument("brand"); s.add_argument("--range",default="last_30d")
    for nm in ("pause","activate"):
        s=sub.add_parser(nm); s.add_argument("brand"); s.add_argument("id",help="campaign id"); s.add_argument("--apply",action="store_true")
    s=sub.add_parser("budget"); s.add_argument("brand"); s.add_argument("id",help="campaign id"); s.add_argument("--daily",required=True); s.add_argument("--apply",action="store_true")
    a=ap.parse_args()
    {"accounts":cmd_accounts,"report":cmd_report,"trend":cmd_trend,"list":cmd_list,"products":cmd_products,
     "pause":cmd_pause,"activate":cmd_activate,"budget":cmd_budget}[a.cmd](a)

if __name__=="__main__":
    main()
