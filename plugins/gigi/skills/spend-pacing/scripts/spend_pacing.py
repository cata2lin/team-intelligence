# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""spend_pacing.py — pacing buget ads + MER pe luna curentă, per brand și canal.
Spend = metrics.cache.daily_ad_spend_ron (RON, FRESH zilnic, toate 3 platformele, alimentat de
pipeline-ul WMS — independent de tabelele insight care pică odată cu tokenul Meta). MER = venit
PLASAT (AWBprint, comenzi create în lună, gross) / spend, doar magazine RON (non-RON = pacing only).

  proiecție lună = spend MTD / zile_scurse × zile_în_lună     (run-rate liniar)

  uv run spend_pacing.py                       # toate magazinele
  uv run spend_pacing.py --store esteban.ro    # un magazin + breakdown canal
  uv run spend_pacing.py --no-mer              # doar pacing spend (fără AWBprint)
"""
import os, sys, calendar, argparse, subprocess, datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import psycopg2

def kb(k):
    v=os.environ.get(k)
    if v: return v
    p=os.path.join(os.path.dirname(__file__),"..","..","..","..","core","scripts","kb.py")
    return subprocess.run(["uv","run",p,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
def clean(url):
    p=urlsplit(url); OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: url=urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    return url
def conn(key):
    c=psycopg2.connect(clean(os.getenv(key) or kb(key))); c.set_session(readonly=True); return c
def norm(s):  # cache store_name → domeniu AWBprint
    s=(s or "").strip().lower()
    return "nubra.ro" if s=="nubra" else s

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--store", default=None, help="filtru magazin (ILIKE pe store_name)")
    ap.add_argument("--no-mer", action="store_true", help="sari peste venit/MER (nu mai lovi AWBprint)")
    a=ap.parse_args()
    mx=conn("DATABASE_URL_METRICS")
    today=dt.date.today(); mstart=today.replace(day=1)
    dim=calendar.monthrange(today.year,today.month)[1]
    elapsed=today.day
    # spend MTD + last month total + freshness, per store/platform
    w="AND store_name ILIKE %s" if a.store else ""
    args=[mstart, today]+([f"%{a.store}%"] if a.store else [])
    q=f"""SELECT store_name, platform,
        SUM(spend_ron) FILTER (WHERE date >= %s AND date <= %s) mtd,
        SUM(spend_ron) FILTER (WHERE date >= date_trunc('month', %s::date - interval '1 day')
                                AND date < date_trunc('month', %s::date)) last_full,
        MAX(date) FILTER (WHERE spend_ron>0) fresh
      FROM cache.daily_ad_spend_ron WHERE 1=1 {w} GROUP BY store_name, platform"""
    with mx.cursor() as c:
        c.execute(q, [mstart, today, mstart, mstart]+([f"%{a.store}%"] if a.store else [])); rows=c.fetchall()
    store={}; fresh_max=None
    for sn,plat,mtd,lastf,fr in rows:
        mtd=float(mtd or 0); lastf=float(lastf or 0)
        if mtd==0 and lastf==0: continue
        d=store.setdefault(norm(sn),{"plats":{},"mtd":0.0,"last":0.0})
        d["plats"][plat]={"mtd":mtd,"last":lastf}; d["mtd"]+=mtd; d["last"]+=lastf
        if fr and (not fresh_max or fr>fresh_max): fresh_max=fr
    # revenue (placed, gross) per store RON, current month
    rev={}
    if not a.no_mer:
        try:
            ax=conn("DATABASE_URL_AWBPRINT")
            with ax.cursor() as c:
                c.execute("""SELECT lower(s.name), SUM(o.total_price)
                    FROM orders o JOIN stores s ON s.uid=o.store_uid
                    WHERE o.currency='RON' AND o.frisbo_created_at >= %s GROUP BY 1""",[mstart])
                rev={norm(k):float(v or 0) for k,v in c.fetchall()}
        except Exception as e:
            print(f"(MER skip — AWBprint indisponibil: {e})")
    print(f"\n=== Spend pacing & MER — luna {today:%Y-%m} (ziua {elapsed}/{dim}) ===")
    if fresh_max and fresh_max < today - dt.timedelta(days=2):
        print(f"⚠️  Spend cel mai recent = {fresh_max} ({(today-fresh_max).days}z în urmă) — pipeline ads întârziat, proiecția poate fi sub-estimată.")
    print(f"{'Magazin':18} {'spend MTD':>10} {'proiecție':>10} {'luna trec.':>10} {'vs LM':>6} {'venit MTD':>11} {'MER':>5}")
    tot_mtd=tot_proj=tot_last=tot_rev=0.0
    for sn,d in sorted(store.items(), key=lambda i:-i[1]["mtd"]):
        proj=d["mtd"]/elapsed*dim if elapsed else 0
        vs=(proj/d["last"]-1) if d["last"]>0 else None
        r=rev.get(sn); mer=(r/d["mtd"]) if (r and d["mtd"]>=100) else None  # prag: spend<100 RON → MER nerelevant
        tot_mtd+=d["mtd"]; tot_proj+=proj; tot_last+=d["last"]; tot_rev+=(r or 0)
        vs_s=f"{vs:+.0%}" if vs is not None else "  —"
        mer_s=f"{mer:.1f}" if mer is not None else "  —"
        rev_s=f"{r:,.0f}" if r is not None else "  —"
        print(f"{sn[:18]:18} {d['mtd']:>10,.0f} {proj:>10,.0f} {d['last']:>10,.0f} {vs_s:>6} {rev_s:>11} {mer_s:>5}")
        if a.store:  # detaliu canal
            for plat,pv in sorted(d["plats"].items(), key=lambda i:-i[1]["mtd"]):
                pproj=pv["mtd"]/elapsed*dim if elapsed else 0
                print(f"   └ {plat:8} MTD {pv['mtd']:>9,.0f} → proiecție {pproj:>9,.0f} (luna trec. {pv['last']:,.0f})")
    vs_t=(tot_proj/tot_last-1) if tot_last>0 else None
    mer_t=(tot_rev/tot_mtd) if tot_mtd>0 else None
    print("-"*78)
    print(f"{'TOTAL':18} {tot_mtd:>10,.0f} {tot_proj:>10,.0f} {tot_last:>10,.0f} {(f'{vs_t:+.0%}' if vs_t is not None else '—'):>6} {tot_rev:>11,.0f} {(f'{mer_t:.1f}' if mer_t else '—'):>5}")
    print("\nproiecție = spend MTD / zile_scurse × zile_lună · vs LM = proiecție vs luna trecută (full)")
    print("MER = venit PLASAT gross (AWBprint, comenzi create în lună) / spend; doar magazine RON (non-RON: pacing only).")
    print("MER ≠ profit: nu scade COGS/transport/refuz. Pt contribuție reală → multi-brand-pnl / profit_by_sku.")

if __name__=="__main__": main()
