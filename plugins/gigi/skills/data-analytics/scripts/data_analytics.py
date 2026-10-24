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

SURSA = ORDER HUB (24-oct-2026), toate patru analizele:
  `GET /api/cs/livrabilitate?luna=AAAA-LL[&cu_client=true]` — o comandă pe rând, stare=Livrata, ziua comenzii
  (București), total în moneda magazinului. Cu `cu_client=true` vin `client_telefon` / `client_email`: PSEUDONIME de
  24 hex (sha256 cu sare peste indexul orb OH) — nu telefonul, nu emailul.
  Identitatea clientului = `client_telefon` (ultimele 9 cifre), cu `client_email` doar ca rezervă când telefonul lipsește.
  De ce nu emailul, ca înainte (AWBprint `customer_email`): măsurat 24-oct, pseudonimul de EMAIL e unic pe COMANDĂ
  până în iunie-2026 (0 clienți comuni între două luni consecutive; Esteban 2025-04..2026-06), deci n-ar exista nicio
  recumpărare înainte de iulie. Cel de TELEFON e stabil pe tot istoricul (2.352 clienți comuni mai–iunie 2026).
  Fereastra: --since AAAA-LL (implicit 2024-06, prima lună din OH). Lunile mai vechi de 90 de zile se țin în
  cache local (~/.cache/arona/data-analytics, doar pseudonime), ca istoricul să nu se descarce la fiecare rulare.
  Calea veche (AWBprint) doar explicit: --source awb, și doar pe 84.46 (de pe laptop AWBprint e refuzat).
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
DOAR_SERVER = ("--source awb citește AWBprint, care e refuzat de pe laptop: rulează doar pe 84.46 "
               "(ssh root@84.46.242.181, /root/Scripturi/team-intelligence). Implicit (Order Hub) merge de oriunde.")

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

def oh_luna(luna, cu_client=False):
    import json, urllib.request, urllib.error, gzip
    tok=token_oh()
    if not tok: raise OHIndisponibil("lipsește cheia Order Hub (OH_TEAM_READ_TOKEN în env sau în KB)")
    req=urllib.request.Request(f"{OH_BAZA}/api/cs/livrabilitate?luna={luna}" + ("&cu_client=true" if cu_client else ""),
        headers={"Authorization":"Bearer "+tok,"Accept":"application/json","Accept-Encoding":"gzip"})
    import time
    for incercare in range(3):   # 502/503/504 trecătoare (proxy) → reîncerc; 4xx = eroare pe loc
        try:
            with urllib.request.urlopen(req,timeout=300) as r:
                b=r.read()
                if r.headers.get("Content-Encoding")=="gzip": b=gzip.decompress(b)
            break
        except urllib.error.HTTPError as e:
            if e.code in (502,503,504) and incercare<2: time.sleep(5*(incercare+1)); continue
            raise OHIndisponibil(f"Order Hub a răspuns HTTP {e.code} pe luna {luna}")
        except Exception as e:
            if incercare<2: time.sleep(5*(incercare+1)); continue
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

# ── clienți din OH (RFM / cohort / LTV) ─────────────────────────────────
OH_PRIMA_LUNA = "2024-06"
CACHE_DIR = os.path.expanduser(os.environ.get("DATA_ANALYTICS_CACHE") or "~/.cache/arona/data-analytics")
CACHE_ZILE = 90   # o lună încheiată de peste atâtea zile nu-și mai schimbă statusurile (refuzurile au sosit)

def _luni(de_la, azi):
    l=dt.date.fromisoformat(de_la+"-01"); out=[]
    while l<=azi:
        out.append(l.strftime("%Y-%m")); l=(l.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
    return out

def livrate_cu_client(luna, azi, citeste=oh_luna, cache=True):
    """[(comanda, zi, total, client)] doar livratele lunii; client = pseudonim telefon, altfel pseudonim email.
    Lunile vechi vin din cache (doar pseudonime)."""
    import json, gzip
    sf=(dt.date.fromisoformat(luna+"-01").replace(day=28)+dt.timedelta(days=4)).replace(day=1)
    stabila = (azi-sf).days >= CACHE_ZILE
    f=os.path.join(CACHE_DIR, f"livrate_client_v2_{luna}.json.gz")
    if cache and stabila:
        try:
            with gzip.open(f,"rt",encoding="utf-8") as h: return [tuple(x) for x in json.load(h)]
        except (OSError, ValueError): pass
    d=citeste(luna, cu_client=True); col={c:i for i,c in enumerate(d["coloane"])}
    if "client_telefon" not in col: raise OHIndisponibil(f"răspuns OH fără `client_telefon` pe luna {luna}")
    out=[(x[col["comanda"]], str(x[col["zi"]])[:10], float(x[col["total"]] or 0),
          ("t:"+x[col["client_telefon"]]) if x[col["client_telefon"]] else
          (("e:"+x[col["client_email"]]) if x[col["client_email"]] else ""))
         for x in d["randuri"] if x[col["stare"]]=="Livrata"]
    if cache and stabila:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with gzip.open(f+".tmp","wt",encoding="utf-8") as h: json.dump(out,h)
            os.replace(f+".tmp", f)
        except OSError: pass
    return out

def comenzi_client_oh(store, since=None, azi=None, citeste=oh_luna, cache=True):
    """[(client, zi:date, total)] — livrate, cu client cunoscut, pe magazin."""
    azi=azi or dt.date.today(); out=[]
    for luna in _luni(since or OH_PRIMA_LUNA, azi):
        for com,zi,tot,ce in livrate_cu_client(luna, azi, citeste, cache):
            if not ce: continue
            if store and store.lower() not in domeniu(com): continue
            out.append((ce, dt.date.fromisoformat(zi), tot))
    return out

def _ntile(chei, n=5):
    """NTILE(n) OVER (ORDER BY cheie), ca în Postgres: primele (N mod n) grupe au un element în plus."""
    ordine=sorted(range(len(chei)), key=lambda i: chei[i]); N=len(chei); out=[0]*N
    baza, rest = divmod(N, n); poz=0
    for b in range(1, n+1):
        for _ in range(baza + (1 if b<=rest else 0)):
            if poz<N: out[ordine[poz]]=b; poz+=1
    return out

def rfm_rows_oh(comenzi):
    agg={}
    for ce,zi,tot in comenzi:
        a=agg.setdefault(ce,[zi,0,0.0]); a[0]=max(a[0],zi); a[1]+=1; a[2]+=tot
    v=list(agg.values())
    r=_ntile([x[0] for x in v]); f=_ntile([x[1] for x in v]); m=_ntile([x[2] for x in v])
    g={}
    for i,x in enumerate(v):
        k=g.setdefault((r[i],f[i],m[i]),[0,0.0,0]); k[0]+=1; k[1]+=x[2]; k[2]+=x[1]
    return [(a,b,c,n,round(val),round(fr/n,2)) for (a,b,c),(n,val,fr) in g.items()]

def cohort_rows_oh(comenzi):
    prima={}
    for ce,zi,_ in comenzi:
        m=zi.replace(day=1)
        if ce not in prima or m<prima[ce]: prima[ce]=m
    g={}
    for ce,zi,_ in comenzi:
        c=prima[ce]; m=zi.replace(day=1); off=(m.year-c.year)*12+(m.month-c.month)
        g.setdefault((c,off),set()).add(ce)
    return [(c,off,len(s)) for (c,off),s in sorted(g.items())]

def ltv_rows_oh(comenzi):
    agg={}
    for ce,zi,tot in comenzi:
        a=agg.setdefault(ce,[zi,0,0.0]); a[0]=min(a[0],zi); a[1]+=1; a[2]+=tot
    g={}
    for z,n,rev in agg.values():
        k=g.setdefault(z.replace(day=1),[0,0,0.0,0]); k[0]+=1; k[1]+=n; k[2]+=rev; k[3]+=(n>1)
    return [(c,cu,round(o/cu,2),round(rv/cu),round(100.0*rp/cu,1))
            for c,(cu,o,rv,rp) in sorted(g.items(), reverse=True)[:12]]

def store_clause(store, since=None):
    w,p=(["AND s.name ILIKE %s"],[f"%{store}%"]) if store else ([],[])
    if since: w.append("AND o.frisbo_created_at >= %s"); p.append(since+"-01")
    return (" ".join(w), p)

def rfm(cx, store, since=None, rows=None):
    if rows is None: rows=rfm_rows_awb(cx, store, since)
    _rfm_print(rows, store)

def rfm_rows_awb(cx, store, since=None):
    w,p=store_clause(store, since)
    q=f"""WITH d AS (
      SELECT o.customer_email ce, MAX(o.frisbo_created_at::date) last_o, COUNT(*) freq, COALESCE(SUM(o.total_price),0) mon
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w}
      GROUP BY 1),
    sc AS (SELECT ce, last_o, freq, mon,
      NTILE(5) OVER (ORDER BY last_o) r, NTILE(5) OVER (ORDER BY freq) f, NTILE(5) OVER (ORDER BY mon) m FROM d)
    SELECT r,f,m,COUNT(*) n, ROUND(SUM(mon)) val, ROUND(AVG(freq),2) afreq FROM sc GROUP BY 1,2,3"""
    with cx.cursor() as c: c.execute(q,p); return c.fetchall()

def _rfm_print(rows, store):
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
    print(f"\n=== RFM — {store or 'toate'} (clienți delivered: {sum(v[0] for v in seg.values())}) ===")
    print(f"{'Segment':32} {'Clienți':>8} {'%':>5} {'Valoare':>12}")
    for s,(n,val) in sorted(seg.items(),key=lambda i:-i[1][1]):
        print(f"{s:32} {n:>8} {100*n/tot:>4.0f}% {val:>12,.0f}")

def cohort(cx, store, months, since=None, rows=None):
    if rows is None: rows=cohort_rows_awb(cx, store, since)
    _cohort_print(rows, store, months)

def cohort_rows_awb(cx, store, since=None):
    w,p=store_clause(store, since)
    q=f"""WITH o2 AS (SELECT o.customer_email ce, date_trunc('month',o.frisbo_created_at)::date m
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w}),
    first AS (SELECT ce, MIN(m) cohort FROM o2 GROUP BY 1)
    SELECT f.cohort, (EXTRACT(YEAR FROM age(o2.m,f.cohort))*12+EXTRACT(MONTH FROM age(o2.m,f.cohort)))::int off, COUNT(DISTINCT o2.ce) n
    FROM o2 JOIN first f USING(ce) GROUP BY 1,2 ORDER BY 1,2"""
    with cx.cursor() as c: c.execute(q,p); return c.fetchall()

def _cohort_print(rows, store, months):
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

def ltv(cx, store, since=None, rows=None):
    if rows is None: rows=ltv_rows_awb(cx, store, since)
    _ltv_print(rows, store)

def ltv_rows_awb(cx, store, since=None):
    w,p=store_clause(store, since)
    q=f"""WITH d AS (
      SELECT o.customer_email ce, date_trunc('month',MIN(o.frisbo_created_at))::date cohort,
        COUNT(*) orders, COALESCE(SUM(o.total_price),0) rev
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status='delivered' AND o.customer_email<>'' {w} GROUP BY 1)
    SELECT cohort, COUNT(*) custs, ROUND(AVG(orders),2) aov_orders, ROUND(AVG(rev)) ltv,
      ROUND(100.0*SUM((orders>1)::int)/COUNT(*),1) repeat_pct
    FROM d GROUP BY 1 ORDER BY 1 DESC LIMIT 12"""
    with cx.cursor() as c: c.execute(q,p); return c.fetchall()

def _ltv_print(rows, store):
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
    ap.add_argument("--store", default="esteban.ro", help="filtru magazin (domeniul public, ex. esteban.ro); '' = toate (atenție monedă)")
    ap.add_argument("--months", type=int, default=12); ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--since", default=None, help="rfm/cohort/ltv: de la luna AAAA-LL (implicit OH 2024-06; AWBprint: tot)")
    ap.add_argument("--fara-cache", action="store_true", help="descarcă din nou și lunile vechi din OH")
    ap.add_argument("--source", choices=["oh","awb"], default=(os.environ.get("DATA_ANALYTICS_SURSA") or "oh").strip().lower(),
                    help="oh (implicit, Order Hub) | awb (calea veche, doar pe 84.46, doar ca întoarcere)")
    a=ap.parse_args()
    store=None if a.store=="" else a.store
    if a.since and not (len(a.since)==7 and a.since[4]=="-"): sys.exit("--since vrea AAAA-LL")
    pii=[x for x in ("rfm","cohort","ltv") if a.analysis in (x,"all")]
    fc=a.analysis in ("forecast","all")
    if a.source=="awb":
        if not pe_server(): print(DOAR_SERVER, file=sys.stderr); sys.exit(2)
        try: cx=conn()
        except Exception as e: sys.exit(f"EROARE: conectarea la AWBprint a eșuat ({type(e).__name__}).")
        print("(sursa: AWBprint — calea veche, cerută explicit)")
        if "rfm" in pii: rfm(cx,store,a.since)
        if "cohort" in pii: cohort(cx,store,a.months,a.since)
        if "ltv" in pii: ltv(cx,store,a.since)
        if fc: forecast(cx,store,a.weeks)
        return
    try:
        com=comenzi_client_oh(store, a.since, cache=not a.fara_cache) if pii else None
        rows=saptamani_oh(store) if fc else None
    except OHIndisponibil as e:
        sys.exit(f"EROARE: {e}. Nu dau cifra veche în locul ei; calea veche doar explicit: --source awb (pe 84.46).")
    print(f"(sursa: Order Hub /api/cs/livrabilitate — livrate, ziua comenzii București"
          + (f"; client = pseudonim telefon (rezervă: email), de la {a.since or OH_PRIMA_LUNA}" if pii else "") + ")")
    if "rfm" in pii: rfm(None,store,rows=rfm_rows_oh(com))
    if "cohort" in pii: cohort(None,store,a.months,rows=cohort_rows_oh(com))
    if "ltv" in pii: ltv(None,store,rows=ltv_rows_oh(com))
    if fc: forecast(None,store,a.weeks,rows=rows)

if __name__=="__main__":
    main()
