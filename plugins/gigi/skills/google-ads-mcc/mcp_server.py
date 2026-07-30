# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2","psycopg2-binary>=2.9","requests>=2.31"]
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
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
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
    r=subprocess.run(["uv","run",os.path.join(HERE,script)]+args,capture_output=True,text=True,env=env,timeout=timeout)
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
    r=subprocess.run(["uv","run",MERCHANT,"--store",merchant_id],capture_output=True,text=True,env=env,timeout=240)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")

MERCHANT_REP=os.path.join(HERE,"..","merchant-center-feed","merchant_reports.py")
@mcp.tool()
def merchant_performance(store: str, days: int = 30, top: int = 30) -> str:
    """PERFORMANCE per produs în Merchant Center (clicuri/impresii/CTR/conv) + lista ZOMBIE (0 clicuri). store=nume (grandia/esteban) sau merchant_id. Zombie = candidați pt shopify_feed_gaps/feed_attr_filler."""
    env=_env()
    r=subprocess.run(["uv","run",MERCHANT_REP,"--store",store,"--days",str(days),"--top",str(top)],capture_output=True,text=True,env=env,timeout=240)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")

# ─────────────────── WRITE (dry-run default) ───────────────────
def _write(sub, args, apply):
    a=list(args)+(["--apply"] if apply else [])
    env=_env()
    r=subprocess.run(["uv","run",os.path.join(HERE,"gads.py"),sub]+a,capture_output=True,text=True,env=env,timeout=120)
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
    r=subprocess.run(["uv","run",SHOPIFY_GQL,"--list"],capture_output=True,text=True,env=env,timeout=90)
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
    r=subprocess.run(["uv","run",SHOPIFY_GQL,"--prefix",prefix,"--query",query,"--vars",variables],capture_output=True,text=True,env=env,timeout=120)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:400]) if r.returncode!=0 else "")

# ─────────────────── META / TIKTOK ADS (paid-media hub) ───────────────────
META=os.path.join(HERE,"..","meta-ads","meta.py")
TIKTOK=os.path.join(HERE,"..","tiktok-ads","tiktok.py")
def _ext(path, args, timeout=180):
    r=subprocess.run(["uv","run",path]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
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
    r=subprocess.run(["uv","run",os.path.join(HERE,"shopify_feed_gaps.py")]+a,capture_output=True,text=True,env=env,timeout=320)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+r.stderr[:300]) if r.returncode!=0 else "")

if __name__=="__main__":
    mcp.run()
