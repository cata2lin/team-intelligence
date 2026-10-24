# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""data_analytics.py — analitică de CLIENȚI pe datele ARONA (AWBprint = livrat, venit real, COD).
RFM segmentare · Cohort retention · LTV per cohortă · Forecast cerere. Identitate = customer_email,
venit = comenzi DELIVERED (refuzul COD nu e venit). Monetar în moneda magazinului (per-store).

  uv run data_analytics.py rfm --store esteban.ro
  uv run data_analytics.py cohort --store esteban.ro --months 12
  uv run data_analytics.py ltv --store esteban.ro
  uv run data_analytics.py forecast --store esteban.ro --weeks 8
  uv run data_analytics.py all --store esteban.ro

SURSE (24-oct-2026, AWBprint rămâne fără cititori de pe laptop):
  forecast           → ORDER HUB `GET /api/cs/livrabilitate?luna=` (stare=Livrata, ziua comenzii București, total).
                       Nu are nevoie de date de client. Calea veche doar explicit: --source awb.
  rfm / cohort / ltv → au nevoie de IDENTITATEA clientului (email). Order Hub n-are încă ruta
                       „comenzi livrate pe magazin cu email/telefon ca bidx" → rămân pe AWBprint DOAR pe server
                       (84.46). Pe laptop ies cu cod 2 și mesaj clar, fără să deschidă baza.
"""
import os, sys, argparse, subprocess, datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras

def kb(k):
    v=os.environ.get(k)
    if v: return v
    p=os.path.join(os.path.dirname(__file__),"..","..","..","..","core","scripts","kb.py")
    return subprocess.run(["uv","run",p,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
def conn():
    url=os.getenv("DATABASE_URL_AWBPRINT") or kb("DATABASE_URL_AWBPRINT")
    p=urlsplit(url); OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: url=urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    c=psycopg2.connect(url); c.set_session(readonly=True); return c

# ── unde rulează: AWBprint (date de client) doar pe server ──────────────
SERVERE = {"vmi2650854.contaboserver.net"}   # 84.46.242.181 (team-intelligence pe /root/Scripturi)
RUTA_LIPSA = ("ruta OH lipsește: „comenzi livrate pe magazin, cu email/telefon ca bidx (fără PII în clar)”; "
              "%s are nevoie de identitatea clientului, deci rulează doar pe 84.46 (ssh root@84.46.242.181, "
              "/root/Scripturi/team-intelligence). Pe laptop merge doar `forecast` (din Order Hub).")

def pe_server():
    """Server = hostname cunoscut sau ARONA_PE_SERVER=1 setat explicit în mediul serverului."""
    import socket
    if (os.environ.get("ARONA_PE_SERVER") or "").strip() == "1":
        return True
    try:
        return socket.gethostname() in SERVERE
    except Exception:
        return False

# ── Order Hub (forecast) ───────────────────────────────────────────────
OH_BAZA = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
# prefixul comenzii → domeniul public (aceleași nume ca `stores.name` din AWBprint, ca `--store` să rămână la fel)
PREFIX_DOMENIU = {
    "APR": "apreciat.ro", "ART": "artevita.ro", "BELA": "belasil.ro", "BONBG": "bonhaus.bg", "CZ": "bonhaus.cz",
    "HU": "bonhaus.hu", "PL": "bonhaus.pl", "SK": "bonhaus.sk", "CARP": "carpetto.ro", "BON": "casaofertelor.ro",
    "COV": "covoria.ro", "BUC": "delabucsa.ro", "DUP": "duppo.bg", "DUPBG": "duppo.bg", "MD": "duppo.md",
    "EST": "esteban.ro", "GEN": "gento.ro", "GT": "georgetalent.ro", "GRAND": "grandia.ro", "LAB": "labnoir.ro",
    "MAG": "magdeal.ro", "LUX": "nocturnalux.ro", "NOC": "nocturna.ro", "NUBRA": "nubra", "OFER": "ofertelezilei.ro",
    "ORC": "oriceredus.ro", "RED": "reduceribune.ro", "ROSSI": "rossinails.ro", "ZAF": "zafra.ro",
}

class OHIndisponibil(RuntimeError):
    pass

def token_oh():
    """env OH_TEAM_READ_TOKEN → OH_PROFIT_CITIRE_TOKEN → OH_CS_SB_TOKEN → KB OH_TEAM_READ_TOKEN."""
    for k in ("OH_TEAM_READ_TOKEN","OH_PROFIT_CITIRE_TOKEN","OH_CS_SB_TOKEN"):
        v=(os.environ.get(k) or "").strip()
        if v: return v
    try: return kb("OH_TEAM_READ_TOKEN")
    except Exception: return ""

def oh_luna(luna):
    import json, urllib.request, urllib.error, gzip
    tok=token_oh()
    if not tok: raise OHIndisponibil("lipsește cheia Order Hub (OH_TEAM_READ_TOKEN în env sau în KB)")
    req=urllib.request.Request(f"{OH_BAZA}/api/cs/livrabilitate?luna={luna}",
        headers={"Authorization":"Bearer "+tok,"Accept":"application/json","Accept-Encoding":"gzip"})
    try:
        with urllib.request.urlopen(req,timeout=300) as r:
            b=r.read()
            if r.headers.get("Content-Encoding")=="gzip": b=gzip.decompress(b)
    except urllib.error.HTTPError as e:
        raise OHIndisponibil(f"Order Hub a răspuns HTTP {e.code} pe luna {luna}")
    except Exception as e:
        raise OHIndisponibil(f"Order Hub nu răspunde pe luna {luna} ({type(e).__name__})")
    d=json.loads(b.decode("utf-8"))
    if not d.get("ok", True) or "randuri" not in d: raise OHIndisponibil(f"răspuns OH fără `randuri` pe luna {luna}")
    return d

def domeniu(comanda):
    import re
    m=re.match(r"^[A-Za-z]+",comanda or ""); pfx=(m.group(0) if m else "").upper()
    return PREFIX_DOMENIU.get(pfx, pfx.lower())

def saptamani_oh(store, azi=None, sapt=10, citeste=oh_luna):
    """[(luni, comenzi_livrate, venit)] pe săptămâni ÎNTREGI, din OH, ultimele `sapt` + cea curentă (parțială)."""
    azi=azi or dt.date.today()
    start=azi-dt.timedelta(days=azi.weekday())-dt.timedelta(weeks=sapt)
    luni=[]; l=start.replace(day=1)
    while l<=azi:
        luni.append(l.strftime("%Y-%m")); l=(l.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
    agg={}
    for luna in luni:
        d=citeste(luna); col={c:i for i,c in enumerate(d["coloane"])}
        for x in d["randuri"]:
            if x[col["stare"]]!="Livrata": continue
            if store and store.lower() not in domeniu(x[col["comanda"]]): continue
            zi=dt.date.fromisoformat(str(x[col["zi"]])[:10])
            if zi<start: continue
            wk=zi-dt.timedelta(days=zi.weekday())
            a=agg.setdefault(wk,[0,0.0]); a[0]+=1; a[1]+=float(x[col["total"]] or 0)
    return [(wk,n,round(v)) for wk,(n,v) in sorted(agg.items())]

def store_clause(store): return ("AND s.name ILIKE %s", [f"%{store}%"]) if store else ("",[])

def rfm(cx, store):
    w,p=store_clause(store)
    q=f"""WITH d AS (
      SELECT o.customer_email ce, MAX(o.frisbo_created_at::date) last_o, COUNT(*) freq, COALESCE(SUM(o.total_price),0) mon
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w}
      GROUP BY 1),
    sc AS (SELECT ce, last_o, freq, mon,
      NTILE(5) OVER (ORDER BY last_o) r, NTILE(5) OVER (ORDER BY freq) f, NTILE(5) OVER (ORDER BY mon) m FROM d)
    SELECT r,f,m,COUNT(*) n, ROUND(SUM(mon)) val, ROUND(AVG(freq),2) afreq FROM sc GROUP BY 1,2,3"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    seg={}
    for r,f,m,n,val,af in rows:
        fm=(f+m)/2
        if r>=4 and fm>=4: s="🏆 Champions"
        elif r>=3 and fm>=3: s="💚 Loyal"
        elif r>=4 and fm<3: s="✨ New/Promising"
        elif r<=2 and fm>=4: s="⚠️ At-Risk (valoroși, dispar)"
        elif r<=2 and fm<=2: s="💀 Lost/Hibernating"
        else: s="😐 Need attention"
        d=seg.setdefault(s,[0,0]); d[0]+=n; d[1]+=val or 0
    tot=sum(v[0] for v in seg.values()) or 1
    print(f"\n=== RFM — {store or 'toate'} (clienți delivered) ===")
    print(f"{'Segment':32} {'Clienți':>8} {'%':>5} {'Valoare':>12}")
    for s,(n,val) in sorted(seg.items(),key=lambda i:-i[1][1]):
        print(f"{s:32} {n:>8} {100*n/tot:>4.0f}% {val:>12,.0f}")

def cohort(cx, store, months):
    w,p=store_clause(store)
    q=f"""WITH o2 AS (SELECT o.customer_email ce, date_trunc('month',o.frisbo_created_at)::date m
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w}),
    first AS (SELECT ce, MIN(m) cohort FROM o2 GROUP BY 1)
    SELECT f.cohort, (EXTRACT(YEAR FROM age(o2.m,f.cohort))*12+EXTRACT(MONTH FROM age(o2.m,f.cohort)))::int off, COUNT(DISTINCT o2.ce) n
    FROM o2 JOIN first f USING(ce) GROUP BY 1,2 ORDER BY 1,2"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    data={}; size={}
    for coh,off,n in rows:
        data[(coh,off)]=n
        if off==0: size[coh]=n
    cohs=sorted(size, reverse=True)[:months]
    print(f"\n=== Cohort retention — {store or 'toate'} (% clienți care recumpără) ===")
    hdr="Cohortă     mărime " + " ".join(f"M{i:>2}" for i in range(0,7))
    print(hdr)
    for coh in sorted(cohs):
        base=size.get(coh,0) or 1
        cells=" ".join(f"{100*data.get((coh,i),0)/base:>3.0f}" for i in range(0,7))
        print(f"{str(coh):12}{base:>6}  {cells}")
    print("(M0=100%; M1=% reveniți luna următoare, etc.)")

def ltv(cx, store):
    w,p=store_clause(store)
    q=f"""WITH d AS (
      SELECT o.customer_email ce, date_trunc('month',MIN(o.frisbo_created_at))::date cohort,
        COUNT(*) orders, COALESCE(SUM(o.total_price),0) rev
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w} GROUP BY 1)
    SELECT cohort, COUNT(*) custs, ROUND(AVG(orders),2) aov_orders, ROUND(AVG(rev)) ltv,
      ROUND(100.0*SUM((orders>1)::int)/COUNT(*),1) repeat_pct
    FROM d GROUP BY 1 ORDER BY 1 DESC LIMIT 12"""
    with cx.cursor() as c: c.execute(q,p); rows=c.fetchall()
    print(f"\n=== LTV per cohortă — {store or 'toate'} (delivered, moneda magazinului) ===")
    print(f"{'Cohortă':12} {'Clienți':>8} {'Cmd/client':>11} {'LTV':>10} {'Repeat%':>8}")
    for coh,cu,ao,lt,rp in rows:
        print(f"{str(coh):12} {cu:>8} {ao:>11} {lt:>10,.0f} {rp:>7}%")
    print("(LTV = venit mediu livrat/client; Repeat% = clienți cu 2+ comenzi)")

def forecast(cx, store, weeks, rows=None):
    if rows is None:
        rows=forecast_rows_awb(cx, store)
    _forecast_print(rows, store, weeks)

def forecast_rows_awb(cx, store):
    w,p=store_clause(store)
    q=f"""SELECT date_trunc('week',o.frisbo_created_at)::date wk, COUNT(*) n, ROUND(SUM(o.total_price)) rev
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.frisbo_created_at > now()-interval '52 weeks' {w}
      GROUP BY 1 ORDER BY 1"""
    with cx.cursor() as c: c.execute(q,p); return c.fetchall()

def _forecast_print(rows, store, weeks):
    if len(rows)<8: print("\nforecast: date insuficiente"); return
    series=rows[:-1]  # drop current partial week
    last8=series[-8:]
    avg_n=sum(r[1] for r in last8)/len(last8); avg_rev=sum(r[2] for r in last8)/len(last8)
    # trend: compara ultimele 4 vs precedentele 4
    a=sum(r[1] for r in series[-4:])/4; b=sum(r[1] for r in series[-8:-4])/4
    trend=(a-b)/b if b else 0
    print(f"\n=== Forecast cerere — {store or 'toate'} (delivered/săptămână) ===")
    print(f"  media ultimele 8 săpt: {avg_n:.0f} comenzi / {avg_rev:,.0f} venit")
    print(f"  trend 4săpt vs 4săpt anterioare: {trend:+.0%}")
    print(f"  prognoză următoarele {weeks} săptămâni (MA + trend):")
    for i in range(1,weeks+1):
        fn=avg_n*(1+trend*i*0.5); fr=avg_rev*(1+trend*i*0.5)
        print(f"    +{i}săpt: ~{fn:.0f} comenzi / ~{fr:,.0f} venit")
    print("(model simplu MA8 + trend liniar amortizat; pt sezonalitate fină = model dedicat)")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("analysis", choices=["rfm","cohort","ltv","forecast","all"])
    ap.add_argument("--store", default="esteban.ro", help="filtru magazin (ILIKE); '' = toate (atenție monedă)")
    ap.add_argument("--months", type=int, default=12); ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--source", choices=["oh","awb"], default=(os.environ.get("DATA_ANALYTICS_SURSA") or "oh").strip().lower(),
                    help="forecast: oh (implicit, Order Hub) | awb (calea veche, doar ca întoarcere)")
    a=ap.parse_args()
    store=None if a.store=="" else a.store
    pii=[x for x in ("rfm","cohort","ltv") if a.analysis in (x,"all")]
    if pii and not pe_server():
        print(RUTA_LIPSA % "/".join(pii), file=sys.stderr)
        if a.analysis=="all":
            print("(rulez doar forecast)\n", file=sys.stderr); pii=[]
        else:
            sys.exit(2)
    cx=None
    if pii or (a.analysis in ("forecast","all") and a.source=="awb"):
        try: cx=conn()
        except Exception as e: sys.exit(f"EROARE: conectarea la AWBprint a eșuat ({type(e).__name__}).")
    if "rfm" in pii: rfm(cx,store)
    if "cohort" in pii: cohort(cx,store,a.months)
    if "ltv" in pii: ltv(cx,store)
    if a.analysis in ("forecast","all"):
        if a.source=="awb":
            forecast(cx,store,a.weeks)
        else:
            try: rows=saptamani_oh(store)
            except OHIndisponibil as e:
                sys.exit(f"EROARE: {e}. Nu dau cifra veche în locul ei; calea veche doar explicit: --source awb.")
            print("(sursa: Order Hub /api/cs/livrabilitate — livrate pe săptămâna comenzii, ora București)")
            forecast(None,store,a.weeks,rows=rows)

if __name__=="__main__":
    main()
