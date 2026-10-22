# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31","paramiko>=3.4"]
# ///
"""arona-ads MCP — un singur server peste TOT ce ține de Google Ads + Merchant Center (+ Shopify, adăugat separat).

Reutilizează engine-ul existent (gads.py importat direct; scripturile testate prin subprocess) ca să nu
duplic logica. Read-only by default; mutațiile Google Ads sunt DRY-RUN dacă nu pui apply=true.
Credențialele vin din KB (nu se printează). Rulează local pe stdio.

Register (Claude Code / config MCP):
  "arona-ads": { "command": "uv", "args": ["run", "<abs path>/mcp_server.py"] }
"""
import os, sys, json, subprocess, importlib.util
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True, stdin=subprocess.DEVNULL, timeout=120).stdout.strip()
if not os.environ.get("DATABASE_URL_METRICS"): os.environ["DATABASE_URL_METRICS"]=_kb("DATABASE_URL_METRICS")
# gads.py e import-safe (CLI sub if __name__) → îl importăm pt read-uri structurate
_spec=importlib.util.spec_from_file_location("gads",os.path.join(HERE,"gads.py")); g=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(g)
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-ads")
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
MERCHANT=os.path.join(HERE,"..","merchant-center-feed","merchant_feed.py")
def _run(script, args, timeout=240):
    """Rulează un script din skill prin uv, întoarce stdout (reutilizează tool-urile testate + dry-run-ul lor)."""
    env=_env()
    r=subprocess.run(["uv","run",os.path.join(HERE,script)]+args,capture_output=True,text=True,env=env,timeout=timeout, stdin=subprocess.DEVNULL)
    out=(r.stdout or "").strip()
    if r.returncode!=0: out+=("\n[stderr] "+(r.stderr or "")[:500])
    return out or "(fără output)"

# ─────────────────── READ ───────────────────
@mcp.tool()
def gads_accounts() -> str:
    """Listează toate conturile ENABLED din MCC (id, nume, monedă). Începe de aici ca să afli customer_id-urile."""
    c=g.get_connection(None)
    rows=g.search(c,g._digits(c["mcc"]),"SELECT customer_client.id,customer_client.descriptive_name,customer_client.currency_code FROM customer_client WHERE customer_client.manager=false AND customer_client.status='ENABLED'")
    out=[{"id":g._get(r,"customerClient.id"),"name":g._get(r,"customerClient.descriptiveName"),"currency":g._get(r,"customerClient.currencyCode")} for r in rows]
    return json.dumps(sorted(out,key=lambda x:x["name"] or ""),ensure_ascii=False,indent=1)

@mcp.tool()
def gads_query(customer_id: str, gaql: str) -> str:
    """Rulează un query GAQL raw pe un cont (read-only). Câmpurile din răspuns sunt camelCase (adGroup.id), deși GAQL cere snake_case. Ex: SELECT campaign.name,metrics.cost_micros FROM campaign WHERE segments.date DURING LAST_7_DAYS."""
    c=g.get_connection(None)
    rows=g.search(c,customer_id,gaql)
    return json.dumps(rows,ensure_ascii=False)[:60000]

@mcp.tool()
def gads_portfolio() -> str:
    """Performanță pe TOT portofoliul (toate conturile), 30z+7z în RON: spend, conv, CPA, ROAS, venit, trend, verdict vs breakeven."""
    return _run("gads_portfolio.py",[])

@mcp.tool()
def gads_profit_verdict(days: int = 30) -> str:
    """Verdict SCALE/HOLD/CUT per campanie pe PROFIT (CPA 30z vs breakeven brandref, RON) + semnal budget-limited IERI. Gate-ul canonic de scalare."""
    return _run("profit_verdict.py",["--days",str(days)])

@mcp.tool()
def gads_ngram(customer_id: str, days: int = 30, brand: str = "", min_cost: float = 20.0) -> str:
    """N-gram mining pe search terms: risipă (candidați NEGATIVE) + câștigători (candidați KEYWORD). brand=termeni de brand csv de exclus."""
    a=["--customer",customer_id,"--days",str(days),"--min-cost",str(min_cost)]
    if brand: a+=["--brand",brand]
    return _run("ngram_miner.py",a)

@mcp.tool()
def gads_negative_cleaner(customer_id: str, brand: str, limit: int = 300) -> str:
    """Găsește negativele PREA LARGI care blochează trafic legitim (LLM). brand=descriere brand+ce vinde."""
    return _run("negative_cleaner.py",["--customer",customer_id,"--brand",brand,"--limit",str(limit)])

@mcp.tool()
def gads_change_history(customer_id: str, days: int = 8) -> str:
    """Cine a schimbat ce & CÂND (buget/bid/status) — timestamp-uri reale din change_event. Rulează ÎNAINTE de orice buget/bid ca să știi cât a trecut."""
    return _run("change_history.py",["--customer",customer_id,"--days",str(days)])

# ─────────────────── MERCHANT CENTER ───────────────────
@mcp.tool()
def merchant_feed_health(merchant_id: str) -> str:
    """Sănătatea feed-ului Google Shopping pt un Merchant Center: produse ELIGIBLE / LIMITED / DISAPPROVED + motive."""
    env=_env()
    r=subprocess.run(["uv","run",MERCHANT,"--store",merchant_id],capture_output=True,text=True,env=env,timeout=240, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")

MERCHANT_REP=os.path.join(HERE,"..","merchant-center-feed","merchant_reports.py")
@mcp.tool()
def merchant_performance(store: str, days: int = 30, top: int = 30) -> str:
    """PERFORMANCE per produs în Merchant Center (clicuri/impresii/CTR/conv) + lista ZOMBIE (0 clicuri). store=nume (grandia/esteban) sau merchant_id. Zombie = candidați pt shopify_feed_gaps/feed_attr_filler."""
    env=_env()
    r=subprocess.run(["uv","run",MERCHANT_REP,"--store",store,"--days",str(days),"--top",str(top)],capture_output=True,text=True,env=env,timeout=240, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")

# ─────────────────── WRITE (dry-run default) ───────────────────
def _write(sub, args, apply):
    a=list(args)+(["--apply"] if apply else [])
    env=_env()
    r=subprocess.run(["uv","run",os.path.join(HERE,"gads.py"),sub]+a,capture_output=True,text=True,env=env,timeout=120, stdin=subprocess.DEVNULL)
    return ((r.stdout or "")+((r.stderr or "")[:400])).strip()

@mcp.tool()
def gads_set_budget(customer_id: str, campaign_id: str, daily: float, apply: bool = False) -> str:
    """Setează bugetul zilnic al unei campanii (moneda contului). DRY-RUN dacă apply=false. Regulă: ≤20%/pas, nu re-stivui în aceeași zi (vezi gads_change_history)."""
    return _write("set-budget",["--customer",customer_id,"--campaign",campaign_id,"--daily",str(daily)],apply)

@mcp.tool()
def gads_set_tcpa(customer_id: str, campaign_id: str, cpa: float, apply: bool = False) -> str:
    """Setează Target CPA (RON) → comută pe Max Conversions + tCPA (RESETEAZĂ learning). DOAR ≥15-30 conv. DRY-RUN dacă apply=false."""
    return _write("set-tcpa",["--customer",customer_id,"--campaign",campaign_id,"--cpa",str(cpa)],apply)

@mcp.tool()
def gads_set_status(customer_id: str, campaign_id: str, status: str, apply: bool = False) -> str:
    """Schimbă statusul unei campanii: ENABLED / PAUSED. DRY-RUN dacă apply=false."""
    return _write("set-status",["--customer",customer_id,"--campaign",campaign_id,"--status",status],apply)

@mcp.tool()
def gads_add_negatives(customer_id: str, campaign_id: str, terms: str, match: str = "PHRASE", apply: bool = False) -> str:
    """Adaugă negative keywords la nivel de campanie (terms=csv, match=EXACT/PHRASE/BROAD). DRY-RUN dacă apply=false."""
    return _write("add-negatives",["--customer",customer_id,"--campaign",campaign_id,"--terms",terms,"--match",match],apply)

@mcp.tool()
def gads_add_keywords(customer_id: str, adgroup_id: str, terms: str, match: str = "EXACT", apply: bool = False) -> str:
    """Adaugă keywords pozitive la un ad group (terms=csv, match=EXACT/PHRASE/BROAD). DRY-RUN dacă apply=false."""
    return _write("add-keywords",["--customer",customer_id,"--adgroup",adgroup_id,"--terms",terms,"--match",match],apply)

# ─────────────────── SHOPIFY (multi-magazin, tokenuri din KB) ───────────────────
SHOPIFY_GQL=os.path.join(HERE,"..","shopify-stores","scripts","shopify_gql.py")
@mcp.tool()
def shopify_stores() -> str:
    """Listează prefixele magazinelor Shopify ale echipei (GT, EST, NUB, …) — fără tokenuri. Începe de aici."""
    env=_env()
    r=subprocess.run(["uv","run",SHOPIFY_GQL,"--list"],capture_output=True,text=True,env=env,timeout=90, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")

@mcp.tool()
def shopify_graphql(prefix: str, query: str, variables: str = "{}", confirm_mutation: bool = False) -> str:
    """Rulează un op Admin GraphQL pe magazinul <prefix> (rezolvă shop+token din KB, backoff 429). variables=JSON.
    ⚠️ Shopify NU are dry-run — o MUTAȚIE se execută imediat. Dacă query-ul e o mutație, trebuie confirm_mutation=true
    (altfel e refuzată). Read-urile merg direct. Ex read: 'query{ shop{ name } }'."""
    is_mut="mutation" in " ".join(query.lower().split()[:3]) or query.strip().lower().startswith("mutation")
    if is_mut and not confirm_mutation:
        return "⛔ Query-ul pare o MUTAȚIE (scrie în magazin, fără dry-run pe Shopify). Rerulează cu confirm_mutation=true dacă chiar vrei să execuți."
    env=_env()
    r=subprocess.run(["uv","run",SHOPIFY_GQL,"--prefix",prefix,"--query",query,"--vars",variables],capture_output=True,text=True,env=env,timeout=120, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:400]) if r.returncode!=0 else "")

# ─────────────────── META / TIKTOK ADS (paid-media hub) ───────────────────
META=os.path.join(HERE,"..","meta-ads","meta.py")
TIKTOK=os.path.join(HERE,"..","tiktok-ads","tiktok.py")
def _ext(path, args, timeout=180):
    r=subprocess.run(["uv","run",path]+args,capture_output=True,text=True,env=_env(),timeout=timeout, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")
@mcp.tool()
def meta_report(brand: str, level: str = "campaign", range: str = "last_30d", sort: str = "spend") -> str:
    """Meta (Facebook/IG) Ads — performanță (spend/purchases/ROAS/CPA/CTR) pt un brand. level=account/campaign/adset/ad. Toate în RON."""
    return _ext(META,["report",brand,"--level",level,"--range",range,"--sort",sort])
@mcp.tool()
def meta_list(brand: str) -> str:
    """Meta — listează campaniile brandului (id/status/buget) — ca să ai id-urile pt pause/budget."""
    return _ext(META,["list",brand])
@mcp.tool()
def meta_set_budget(brand: str, campaign_id: str, daily: float, apply: bool = False) -> str:
    """Meta — setează bugetul zilnic al unei campanii (RON). DRY-RUN dacă apply=false."""
    return _ext(META,["budget",brand,campaign_id,"--daily",str(daily)]+(["--apply"] if apply else []))
@mcp.tool()
def tiktok_report(brand: str, level: str = "campaign", range: str = "last_30d", sort: str = "spend") -> str:
    """TikTok Ads — performanță (spend/purchases/ROAS/CPA) pt un brand. level=account/campaign/adgroup/ad. RON."""
    return _ext(TIKTOK,["report",brand,"--level",level,"--range",range,"--sort",sort])
@mcp.tool()
def tiktok_list(brand: str) -> str:
    """TikTok — listează campaniile brandului (id/status/buget)."""
    return _ext(TIKTOK,["list",brand])
@mcp.tool()
def tiktok_set_budget(brand: str, campaign_id: str, daily: float, apply: bool = False) -> str:
    """TikTok — setează bugetul zilnic al unei campanii. DRY-RUN dacă apply=false."""
    return _ext(TIKTOK,["budget",brand,campaign_id,"--daily",str(daily)]+(["--apply"] if apply else []))
SEARCHTERMS=os.path.join(HERE,"..","search-terms","scripts","search_terms.py")
WEEKLY=os.path.join(HERE,"..","weekly-insights","scripts","weekly_insights.py")
PACING=os.path.join(HERE,"..","spend-pacing","scripts","spend_pacing.py")
@mcp.tool()
def gads_search_terms(customer_id: str, brand_terms: str = "", competitor_terms: str = "", days: int = 30, min_waste: float = 20.0) -> str:
    """Google Ads search-term analysis: risipă (0-conv), câștigători non-brand, split brand/non-brand. brand_terms/competitor_terms = csv."""
    a=["--customer",customer_id,"--days",str(days),"--min-waste",str(min_waste)]
    if brand_terms: a+=["--brand-terms",brand_terms]
    if competitor_terms: a+=["--competitor-terms",competitor_terms]
    return _ext(SEARCHTERMS,a)
@mcp.tool()
def weekly_insights(brand: str) -> str:
    """Insights săptămânali per brand — week-over-week combinând Google + Meta (spend/conv/CPA/ROAS)."""
    return _ext(WEEKLY,["--brand",brand])
@mcp.tool()
def spend_pacing(store: str = "") -> str:
    """Pacing buget ads + MER pe lună (spend token-independent, proiecție run-rate). store gol = toate."""
    return _ext(PACING,(["--store",store] if store else []))

@mcp.tool()
def shopify_feed_gaps(prefix: str, limit: int = 40, enrich: bool = False, brand: str = "") -> str:
    """Produse Shopify ACTIVE fără productType (feed slab → zombie PMax). enrich=true rulează feed_attr_filler ca să genereze titlu/atribute. Read-only pe Shopify."""
    a=["--prefix",prefix,"--limit",str(limit)]
    if enrich: a.append("--enrich")
    if brand: a+=["--brand",brand]
    env=_env()
    r=subprocess.run(["uv","run",os.path.join(HERE,"shopify_feed_gaps.py")]+a,capture_output=True,text=True,env=env,timeout=320, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")


# ─── Ads intelligence (skill-uri surori, până acum doar CLI) ──────────────
def _sib(skill, name, args, timeout=420):
    """Rulează un script dintr-un skill FRATE (../<skill>/<name>)."""
    r=subprocess.run(["uv","run",os.path.join(HERE,"..",skill,name)]+[str(x) for x in args],
                     capture_output=True,text=True,env=_env(),timeout=timeout, stdin=subprocess.DEVNULL)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:300]) if r.returncode!=0 else "")

@mcp.tool()
def ads_anomalies(customer: str = "", recent: int = 7, baseline: int = 28, min_spend: float = 0) -> str:
    """Detector de ANOMALII pe Google Ads — compară ultimele zile complete cu baseline-ul și scoate
    deviațiile de spend/ROAS/CPA/CPC. ⚠️ Ultimele 1-2 zile mint mereu (lag de conversie) — vezi
    `gigi:gads-verdict-traps`."""
    a=["--recent",recent,"--baseline",baseline]
    if customer: a+=["--customer",customer]
    if min_spend: a+=["--min-spend",min_spend]
    return _sib("ads-anomalies","scripts/anomaly_detector.py",a)

@mcp.tool()
def creative_fatigue(platform: str = "", recent: int = 7, baseline: int = 28,
                     all_accounts: bool = False, min_spend: float = 0) -> str:
    """Saturație de audiență / oboseală de creativ (Meta+TikTok): frecvență în creștere cu CTR în
    scădere sau CPA în creștere, la nivel de cont. platform=meta|tiktok."""
    a=["--recent",recent,"--baseline",baseline]
    if platform: a+=["--platform",platform]
    if all_accounts: a.append("--all")
    if min_spend: a+=["--min-spend",min_spend]
    return _sib("creative-fatigue","scripts/creative_fatigue.py",a)

@mcp.tool()
def campaign_structure(customer: str = "", brand_terms: str = "", margin: float = 0,
                       delivery_rate: float = 0) -> str:
    """Revizuire de STRUCTURĂ pe campaniile ENABLED — separarea brand/non-brand, suprapuneri,
    ce ar trebui despărțit. brand_terms = csv."""
    a=[]
    if customer: a+=["--customer",customer]
    if brand_terms: a+=["--brand-terms",brand_terms]
    if margin: a+=["--margin",margin]
    if delivery_rate: a+=["--delivery-rate",delivery_rate]
    return _sib("campaign-structure","scripts/campaign_review.py",a)

@mcp.tool()
def budget_simulator(customer: str = "", campaign: str = "", days: int = 30,
                     elasticity: float = 0, margin: float = 0, scenarios: str = "") -> str:
    """Simulator „ce-ar fi dacă" pe buget — modelează efectul unei creșteri/scăderi.
    ⚠️ Verifică ÎNTÂI costul MARGINAL real (Δspend/Δcomenzi): un CPA mediu sănătos poate ascunde
    o scalare care pierde bani (`gigi:gads-verdict-traps`)."""
    a=["--days",days]
    for k,v in (("--customer",customer),("--campaign",campaign),("--elasticity",elasticity),
                ("--margin",margin),("--scenarios",scenarios)):
        if v: a+=[k,v]
    return _sib("budget-simulator","scripts/budget_sim.py",a)

@mcp.tool()
def agency_audit(days: int = 30, from_date: str = "", to_date: str = "") -> str:
    """Responsabilizarea agenției pe paid social — ce a rulat efectiv vs ce a raportat."""
    a=[]
    if from_date and to_date: a+=["--from",from_date,"--to",to_date]
    else: a+=["--days",days]
    return _sib("agency-audit","agency_audit.py",a)

@mcp.tool()
def cpa_report(brand: str = "", store: str = "", from_date: str = "", to_date: str = "") -> str:
    """CPA per brand/magazin pe interval — pe comandă PLASATĂ. Praguri și reguli: `gigi:tcpa-policy`."""
    a=[]
    for k,v in (("--brand",brand),("--store",store),("--from",from_date),("--to",to_date)):
        if v: a+=[k,v]
    return _sib("cpa-report","scripts/cpa_report.py",a)

@mcp.tool()
def ads_transparency(region: str = "RO", limit: int = 20, samples: int = 0) -> str:
    """Ce reclame rulează un competitor, din Google Ads Transparency Center (fără login).
    Rank pe LONGEVITATE = cel mai bun proxy public de performanță."""
    a=["--region",region,"--limit",limit]
    if samples: a+=["--samples",samples]
    return _sib("ads-transparency","adstransparency.py",a)

# ─────────────────── GRANDIA regim weekend (DOAR CITIRE) ───────────────────
GR_CID="9069610821"; GR_SN="23794566678"; GR_HA="23566472089"; GR_FACTOR=0.72

def _vps_tail(path, n=10):
    import logging, shlex
    core=os.path.normpath(os.path.join(HERE,"..","..","..","core","scripts"))
    if core not in sys.path: sys.path.insert(0,core)
    import arona_ssh
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    for k in ("PROFIT_SSH_HOST","PROFIT_SSH_USER"):
        if not os.environ.get(k):
            os.environ[k]=arona_ssh._secret(k) or (arona_ssh.DEFAULT_HOST if k.endswith("HOST") else arona_ssh.DEFAULT_USER)
    try:
        cl=arona_ssh.connect()
    except Exception as e:
        return "(SSH eșuat: %s)"%str(e)[:300]
    try:
        _,o,e=cl.exec_command("tail -n %d %s"%(int(n),shlex.quote(path)),timeout=60)
        return (o.read().decode("utf-8","replace").rstrip() or e.read().decode("utf-8","replace").strip() or "(gol)")
    finally:
        cl.close()

@mcp.tool()
def grandia_regim_weekend_stare() -> str:
    """Starea regimului WEEKEND/WEEKDAY Grandia pe Google Ads (cont 9069610821): modul de azi, ce ar seta cronul
    /root/Scripturi/grandia_weekend_regime.py (zilnic 06:00) față de valorile LIVE — bugetul Shopping-New
    (23794566678; sâmbătă ×0,72, luni ÷0,72, altfel neatins) și tROAS h&a (23566472089; 4.5 weekend / 3.8 weekday) —
    plus ultimele 10 linii din /root/Scripturi/data/grandia_weekend.log. API Google Ads v24.
    DOAR CITIRE: NU schimbă nimic. Bugetul se schimbă doar de un OM, la ora planificată (regula ownerului 19-oct:
    un job de buget ratat NU se reface târziu în zi). Pentru o corecție aprobată: gads_set_budget (dry-run → OK)."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now=datetime.datetime.now(ZoneInfo("Europe/Bucharest"))
    except Exception:
        now=datetime.datetime.now()
    dow=now.weekday(); we=dow>=5
    zile=["luni","marți","miercuri","joi","vineri","sâmbătă","duminică"]
    out=["Grandia regim — azi %s %s: %s"%(zile[dow],now.strftime("%Y-%m-%d %H:%M"),"WEEKEND" if we else "WEEKDAY"),
         "(DOAR CITIRE — nimic nu se modifică; bugetul îl schimbă doar un om, la ora planificată)"]
    try:
        c=g.get_connection(None)
        rows=g.search(c,GR_CID,"SELECT campaign.id, campaign.name, campaign.status, campaign_budget.amount_micros, "
            "campaign.maximize_conversion_value.target_roas, campaign.target_roas.target_roas FROM campaign "
            "WHERE campaign.id IN (%s, %s)"%(GR_SN,GR_HA))
    except Exception as e:
        rows=None; out.append("⚠️ nu pot citi starea live din Google Ads: %s"%str(e)[:300])
    live={}
    for r in rows or []:
        cid=str(g._get(r,"campaign.id"))
        tr=g._get(r,"campaign.maximizeConversionValue.targetRoas") or g._get(r,"campaign.targetRoas.targetRoas")
        live[cid]={"name":g._get(r,"campaign.name"),"status":g._get(r,"campaign.status"),
                   "budget":int(g._get(r,"campaignBudget.amountMicros") or 0)/1e6,"troas":tr}
    if rows is not None:
        sn=live.get(GR_SN,{}); ha=live.get(GR_HA,{})
        b=sn.get("budget")
        if dow==5: rule="regula de sâmbătă: cronul de la 06:00 înmulțește bugetul de vineri cu 0,72 (−28%) — vezi în log dacă a reușit"
        elif dow==0: rule="regula de luni: cronul de la 06:00 împarte bugetul de duminică la 0,72 (restaurare) — vezi în log dacă a reușit"
        else: rule="azi cronul NU atinge bugetul (rămâne cel setat manual)"
        out.append("\nShopping-New %s (%s, %s): buget LIVE %s RON/zi — %s."%(GR_SN,sn.get("name","?"),sn.get("status","?"),
                   ("%.0f"%b) if b is not None else "?",rule))
        if dow in (0,5) and b:
            nxt=round(b*GR_FACTOR) if dow==5 else round(b/GR_FACTOR)
            out.append("  ⚠️ Pe buget cronul NU e idempotent în ziua tranziției: dacă ar rula ACUM ar mai aplica o dată "
                       "factorul (%.0f → %d). Nu-l rula manual; verifică în log că a rulat o singură dată."%(b,nxt))
        tgt=4.5 if we else 3.8
        cur=ha.get("troas")
        st="OK" if cur is not None and abs(float(cur)-tgt)<=0.01 else "DIFERĂ — cronul l-ar seta la %.1f la următoarea rulare"%tgt
        out.append("h&a %s (%s, %s): tROAS LIVE %s, țintă azi %.1f → %s."%(GR_HA,ha.get("name","?"),ha.get("status","?"),cur,tgt,st))
        if dow in (4,6):
            out.append("Mâine e tranziție (%s): bugetul Shopping-New se va schimba la 06:00."%("sâmbătă ×0,72" if dow==4 else "luni ÷0,72"))
    out.append("\nUltimele 10 linii din grandia_weekend.log:")
    out.append(_vps_tail("/root/Scripturi/data/grandia_weekend.log",10))
    return "\n".join(out)


if __name__=="__main__":
    mcp.run()
