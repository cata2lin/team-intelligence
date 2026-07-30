# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""Portfolio Google Ads report across ALL MCC accounts (30d + 7d): spend, conv, CPA, ROAS, venit — în RON.

Ce s-a reparat (22-iul-2026): înainte avea o listă HARDCODATĂ de 10 conturi → lipseau conturile noi
(Casa Ofertelor, Bonhaus BG, Nocturna). Acum **auto-descoperă** toate conturile ENABLED non-manager din MCC,
**convertește valutar** (CZK/PLN/BGN/EUR → RON din metrics.fx_rates) și dă un TOTAL corect pe tot portofoliul.
Coloana `vs BE` = CPA (RON) vs breakeven din `brandref` (verdict la nivel de CONT — grosier, mixează
brand+non-brand+PMax; pentru scale/cut per campanie folosește `profit_verdict.py`)."""
import os, sys, importlib.util, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
if not os.environ.get("DATABASE_URL_METRICS"):
    os.environ["DATABASE_URL_METRICS"]=subprocess.run(["uv","run",KB,"secret-get","DATABASE_URL_METRICS"],capture_output=True,text=True).stdout.strip()
sp=importlib.util.spec_from_file_location("gads",os.path.join(HERE,"gads.py")); g=importlib.util.module_from_spec(sp); _a=sys.argv; sys.argv=["gads"]; sp.loader.exec_module(g); sys.argv=_a
sb=importlib.util.spec_from_file_location("brandref",os.path.join(HERE,"brandref.py")); br=importlib.util.module_from_spec(sb); sb.loader.exec_module(br)
import psycopg2, psycopg2.extras
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
def gv(d,p):
    cur=d
    for k in p.split("."):
        if not isinstance(cur,dict): return None
        cur=cur.get(k)
    return cur
def num(x): return float(x) if x not in (None,"") else 0.0
_OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
def _clean(d):
    p=urlsplit(d); return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _OK]),p.fragment))
cnx=psycopg2.connect(_clean(os.environ["DATABASE_URL_METRICS"])); cnx.set_session(readonly=True)
with cnx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cu:
    cu.execute("""SELECT DISTINCT ON ("fromCurrency") "fromCurrency" f, rate FROM fx_rates
                  WHERE "toCurrency"='RON' AND "fromCurrency" IN ('CZK','PLN','EUR','BGN','HUF') ORDER BY "fromCurrency","rateDate" DESC""")
    FX={r["f"]:float(r["rate"]) for r in cu.fetchall()}
FX["RON"]=1.0
# nume cont Google → cheie brandref
ALIASES=[("grandia","grandia"),("gt","gt"),("george","gt"),("nubra","nubra"),("belasil","belasil"),
 ("ofertele","ofertele"),("gento","gento"),("carpetto","carpetto"),("rossi","rossi"),("nocturna","nocturna"),
 ("bonhaus pl","bonhaus_pl"),("bonhaus cz","bonhaus_cz"),("bonhaus bg","bonhaus_bg"),("casa ofertelor","casaofertelor")]
def be_for(name):
    low=(name or "").lower()
    for sub,key in ALIASES:
        if sub in low:
            ref=br.get(key) or {}
            return ref.get("breakeven_cpa"), ref.get("scale_cpa")
    return None,None
c=g.get_connection(None)
accs=g.search(c,g._digits(c["mcc"]),"SELECT customer_client.id,customer_client.descriptive_name,customer_client.currency_code FROM customer_client WHERE customer_client.manager=false AND customer_client.status='ENABLED'")
def totals(cid,rng):
    cost=conv=val=0.0
    for r in g.search(c,cid,f"SELECT metrics.cost_micros,metrics.conversions,metrics.conversions_value FROM customer WHERE segments.date DURING {rng}"):
        cost+=num(gv(r,"metrics.costMicros"))/1e6; conv+=num(gv(r,"metrics.conversions")); val+=num(gv(r,"metrics.conversionsValue"))
    return cost,conv,val
rows=[]
for a in accs:
    cid=gv(a,"customerClient.id"); nm=gv(a,"customerClient.descriptiveName") or ""; cur=gv(a,"customerClient.currencyCode") or "RON"; fx=FX.get(cur,1.0)
    c30,v30,val30=totals(cid,"LAST_30_DAYS"); c7,v7,_=totals(cid,"LAST_7_DAYS")
    be,scale=be_for(nm)
    rows.append({"nm":nm,"cur":cur,"cost30":c30*fx,"conv30":v30,"val30":val30*fx,
                 "cpa30":(c30/v30*fx) if v30 else 0,"roas30":(val30/c30) if c30 else 0,
                 "cost7":c7*fx,"cpa7":(c7/v7*fx) if v7 else 0,"be":be,"scale":scale})
rows.sort(key=lambda x:-x["cost30"])
print("═══════ PERFORMANȚĂ PORTOFOLIU GOOGLE ADS (RON, toate conturile ENABLED) ═══════")
print(f"{'cont':16}{'cur':4}{'spend30':>9}{'conv':>6}{'CPA':>5}{'ROAS':>5}{'venit':>9} |{'CPA7z':>6}{'trend':>7}  vs BE")
T=dict(cost30=0,conv30=0,val30=0,cost7=0,cost7conv=0)
tconv7=0
for r in rows:
    T["cost30"]+=r["cost30"]; T["conv30"]+=r["conv30"]; T["val30"]+=r["val30"]; T["cost7"]+=r["cost7"]
    trend="—"
    if r["cpa30"] and r["cpa7"]:
        d=(r["cpa7"]-r["cpa30"])/r["cpa30"]*100; trend=f"{'↓' if d<0 else '↑'}{abs(d):.0f}%"
    v="—"
    if r["be"] and r["cpa30"]:
        v="🟢SCALE" if r["cpa30"]<=(r["scale"] or 0) else ("🟡HOLD" if r["cpa30"]<=r["be"] else "🔴CUT")
        v+=f" (BE{r['be']:.0f})"
    print(f"{r['nm'][:15]:16}{r['cur']:4}{r['cost30']:>9,.0f}{r['conv30']:>6.0f}{r['cpa30']:>5.0f}{r['roas30']:>5.1f}{r['val30']:>9,.0f} |{r['cpa7']:>6.0f}{trend:>7}  {v}")
tcpa=T["cost30"]/T["conv30"] if T["conv30"] else 0; troas=T["val30"]/T["cost30"] if T["cost30"] else 0
print("─"*78)
print(f"{'TOTAL':20}{T['cost30']:>9,.0f}{T['conv30']:>6.0f}{tcpa:>5.0f}{troas:>5.1f}{T['val30']:>9,.0f}")
print(f"\nSpend 30z: {T['cost30']:,.0f} RON · Conv: {T['conv30']:,.0f} · CPA: {tcpa:.0f} · ROAS(Google): {troas:.1f} · Venit: {T['val30']:,.0f} RON")
print(f"Spend 7z: {T['cost7']:,.0f} RON")
print("⚠️ ROAS/venit = raportat Google (~1.5× umflat); vs BE la nivel de cont e GROSIER → per-campanie = profit_verdict.py")
