# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""reorder_planner.py — cât și când reaprovizionezi fiecare SKU, ca să NU rămâi fără stoc pe winneri.
Viteza: din Order Hub (implicit, 23-oct-2026) = bucăți PLASATE în fereastră pe TOATE magazinele × rata de livrare
a SKU-ului (livrate / (livrate + refuzate), statusul OH). Stocul: `metrics.inventory_daily_snapshots`, însumat pe
TOATE brandurile (stocul e comun; fiecare magazin ține doar o cotă din el).

De ce nu mai din scăderile de stoc pe brand: stock-sync redistribuie stocul comun între magazine, iar o cotă care
scade la un magazin nu e o vânzare. Măsurat pe OGLINDA: 2.010 „vândute" pe Reduceri Bune față de 223 livrate — viteza
ieșea de 4–9 ori mai mare și cantitatea de comandat la fel. Vechiul calcul rămâne la `--sursa-viteza snapshot`.
NU presupune lead time: îl dai tu.

  reorder_qty = viteză/zi × (lead_time + safety_days) − onHand − incoming   (rotunjit în sus, ≥0)
  stockout în = onHand / viteză/zi (zile)

  uv run reorder_planner.py --brand Grandia
  uv run reorder_planner.py --brand Esteban --lead-days 21 --safety-days 10 --top 30
  uv run reorder_planner.py --brand "George Talent" --days 28 --only-reorder
"""
import os, sys, math, argparse, subprocess, json, urllib.request, urllib.parse
from collections import defaultdict
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
def conn():
    c=psycopg2.connect(clean(os.getenv("DATABASE_URL_METRICS") or kb("DATABASE_URL_METRICS"))); c.set_session(readonly=True); return c

OH = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")


def _oh(path, token):
    req = urllib.request.Request(OH + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise RuntimeError("Order Hub %s: %s" % (path, d.get("rezultat") or d.get("mesaj")))
    return d


def viteza_oh(zile):
    """{sku_jos: (plasate, livrate, refuzate)} pe TOATE magazinele. Codurile numerice (parfumuri) sunt produse diferite
    pe magazine diferite, deci nu se adună: le întoarcem separat ca ambigue."""
    token = kb("OH_CS_SB_TOKEN")
    if not token:
        raise RuntimeError("lipsește cheia OH_CS_SB_TOKEN (seif)")
    pl = defaultdict(int); magazine = set()
    for r in _oh("/api/cs/vanzari-pe-sku?zile=%d" % zile, token).get("randuri") or []:
        pl[(r.get("sku") or "").strip().lower()] += int(r.get("bucati") or 0); magazine.add(r.get("magazin"))
    liv = defaultdict(lambda: [0, 0])
    for m in sorted(x for x in magazine if x):
        try:
            for r in _oh("/api/cs/statistici-sku?magazin=" + urllib.parse.quote(m), token).get("randuri") or []:
                k = (r.get("sku") or "").strip().lower()
                liv[k][0] += int(r.get("livrate") or 0); liv[k][1] += int(r.get("refuzate") or 0)
        except Exception as e:
            print(f"⚠ Order Hub n-a dat livrările pentru {m}: {e}", file=sys.stderr)
    return {k: (q, liv[k][0], liv[k][1]) for k, q in pl.items()}


def rata_livrare(livrate, refuzate, implicit):
    """livrate / (livrate + refuzate); sub 10 colete închise, rata medie (un SKU nou n-are încă istoric)."""
    n = livrate + refuzate
    return livrate / n if n >= 10 else implicit


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--brand", required=True, help="nume brand (ILIKE) ex: Grandia / Esteban / 'George Talent'")
    ap.add_argument("--days", type=int, default=28, help="fereastra de viteză (zile snapshot)")
    ap.add_argument("--lead-days", type=int, default=30, help="lead time furnizor (zile până sosește marfa)")
    ap.add_argument("--safety-days", type=int, default=14, help="stoc de siguranță (zile tampon)")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--only-reorder", action="store_true", help="doar SKU-urile care trebuie comandate ACUM")
    ap.add_argument("--sursa-viteza", choices=["oh", "snapshot"], default="oh",
                    help="oh = livrate din Order Hub (implicit); snapshot = scăderile de stoc pe brand (vechi, umflat)")
    a=ap.parse_args()
    cx=conn()
    q="""
    WITH daily AS (
      SELECT s.sku, s."snapshotDate" d,
             SUM(s."quantityOnHand") oh, SUM(s."quantityIncoming") inc,
             MAX(s."costPerItem") cost, MAX(s.price) price, MAX(s."productTitle") title
      FROM inventory_daily_snapshots s JOIN brands b ON b.id=s."brandId"
      -- locațiile înghețate nu se citesc (S3, 22-oct-2026): „Bonhaus BG" sub Casa Ofertelor (oprită din 28-apr,
      -- 101.592 buc constant) și Covoria (oprit din 6-oct) — contează doar ce a reîmprospătat metrics în 3 zile
      JOIN locations l ON l.id=s."locationId" AND l."updatedAt" >= NOW() - INTERVAL '3 days'
      WHERE TRIM(b.name) ILIKE %s AND s."snapshotDate" >= CURRENT_DATE - %s
      GROUP BY s.sku, s."snapshotDate"),
    lagged AS (SELECT sku,d,oh,inc,cost,price,title,
             LAG(oh) OVER (PARTITION BY sku ORDER BY d) prev FROM daily),
    vel AS (SELECT sku, SUM(GREATEST(prev-oh,0)) sold, COUNT(*) FILTER (WHERE prev IS NOT NULL) ndays
            FROM lagged GROUP BY sku),
    latest AS (SELECT DISTINCT ON (sku) sku,oh,inc,cost,price,title FROM daily ORDER BY sku,d DESC),
    -- stocul e COMUN: cota unui singur brand nu e stocul produsului; însumăm toate locațiile proaspete, ultima zi
    toate AS (SELECT DISTINCT ON (sku) sku, oh_all, inc_all FROM (
                SELECT s.sku, s."snapshotDate" d, SUM(s."quantityOnHand") oh_all, SUM(s."quantityIncoming") inc_all
                FROM inventory_daily_snapshots s
                JOIN locations l ON l.id=s."locationId" AND l."updatedAt" >= NOW() - INTERVAL '3 days'
                WHERE s."snapshotDate" >= CURRENT_DATE - 3 AND s.sku IN (SELECT sku FROM latest)
                GROUP BY s.sku, s."snapshotDate") x ORDER BY sku, d DESC)
    SELECT l.sku,l.title,l.oh,l.inc,l.cost,l.price,v.sold,v.ndays,t.oh_all,t.inc_all
    FROM latest l JOIN vel v USING(sku) LEFT JOIN toate t USING(sku)
    """
    with cx.cursor() as c:
        c.execute(q,[f"%{a.brand.strip()}%", a.days]); rows=c.fetchall()
    if not rows: print(f"Niciun SKU pt brand ILIKE '{a.brand}'. Verifică numele (brands.name)."); return
    horizon=a.lead_days+a.safety_days
    vit = {}
    if a.sursa_viteza == "oh":
        vit = viteza_oh(a.days)
        L = sum(x[1] for x in vit.values()); R = sum(x[2] for x in vit.values())
        implicit = L / (L + R) if L + R else 0.85
    out=[]; ambigue=0
    for sku,title,oh,inc,cost,price,sold,ndays,oh_all,inc_all in rows:
        price=float(price or 0); cost=float(cost or 0)
        if a.sursa_viteza == "oh":
            oh=float(oh_all if oh_all is not None else oh or 0); inc=float(inc_all if inc_all is not None else inc or 0)
            k=(sku or "").strip().lower()
            if k.isdigit():
                ambigue+=1                       # cod numeric = alt produs pe alt magazin: nu-l adunăm
                vday=(float(sold or 0)/ndays) if ndays else 0.0
            else:
                q,lv,rf=vit.get(k,(0,0,0)); vday=q*rata_livrare(lv,rf,implicit)/a.days
        else:
            oh=float(oh or 0); inc=float(inc or 0)
            vday=(float(sold or 0)/ndays) if ndays else 0.0
        cover=(oh/vday) if vday>0 else (999 if oh>0 else 0)
        need=vday*horizon-oh-inc
        reorder=max(0, math.ceil(need)) if vday>0 else 0
        rev_risk=vday*price            # venit/zi în joc dacă rămâi fără stoc
        po_cost=reorder*cost           # cât te costă comanda (COGS)
        out.append((sku,title or "",oh,inc,vday,cover,reorder,rev_risk,po_cost))
    # rank: produsele care se termină cel mai repede ȘI vând (rev_risk mare), reorder>0 sus
    out.sort(key=lambda r:(0 if r[6]>0 else 1, r[5], -r[7]))
    if a.only_reorder: out=[r for r in out if r[6]>0]
    print(f"\n=== Reorder planner — {a.brand} (viteză {a.days}z din {'Order Hub, livrate' if a.sursa_viteza=='oh' else 'scăderi de stoc pe brand (VECHI)'}"
          f" · lead {a.lead_days}z · safety {a.safety_days}z) ===")
    if a.sursa_viteza == "oh":
        print(f"stoc = comun, pe toate magazinele · rata medie de livrare {implicit:.0%} (SKU cu <10 colete închise)"
              + (f" · {ambigue} coduri numerice pe viteza veche (ambigue între magazine)" if ambigue else ""))
    print(f"{'SKU':22} {'onHand':>7} {'incom':>6} {'vânz/zi':>8} {'cover(z)':>9} {'COMANDĂ':>8} {'venit/zi risc':>13}")
    urgent=0
    for sku,title,oh,inc,vday,cover,reorder,rev_risk,po in out[:a.top]:
        flag="🔴" if cover<a.lead_days and vday>0 else ("🟡" if cover<horizon and vday>0 else "  ")
        if flag=="🔴": urgent+=1
        cov=f"{cover:.0f}" if cover<999 else "∞"
        print(f"{flag}{sku[:20]:20} {oh:>7.0f} {inc:>6.0f} {vday:>8.2f} {cov:>9} {reorder:>8} {rev_risk:>13,.0f}")
        if title: print(f"   └ {title[:70]}")
    tot_reorder=sum(r[6] for r in out); tot_po=sum(r[8] for r in out)
    print(f"\n🔴 = stoc se termină ÎNAINTE să sosească marfa (cover<lead) → comandă ACUM · 🟡 = sub pragul de siguranță")
    print(f"Total: {urgent} SKU urgente · {len([r for r in out if r[6]>0])} de comandat · {tot_reorder:,} buc · cost PO ≈ {tot_po:,.0f} (moneda brand)")
    print("Cover ∞ = nu s-a vândut în fereastră (viteză 0). Lead/safety ajustabile cu --lead-days/--safety-days.")

if __name__=="__main__": main()
