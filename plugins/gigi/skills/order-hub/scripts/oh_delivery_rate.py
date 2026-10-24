#!/usr/bin/env python3
"""Rata de livrare per LUNĂ × MAGAZIN, calculată DIN ORDER HUB (nu AWBprint).

Rulează local: trimite interogarea prin ssh în containerul orderhub-web (DATABASE_URL din env-ul lui).
  uv run oh_delivery_rate.py                      # din 2026-08, toate magazinele
  uv run oh_delivery_rate.py --since 2026-09 --store grandia --min-orders 100
  uv run oh_delivery_rate.py --csv out.csv
  uv run oh_delivery_rate.py --canal              # + pe CANAL de marketing (tiktok/meta/google/…), lună și cohorte
Canalul vine din tabela Orders `comanda_canal` (services/canal_marketing, aceeași regulă ca
shared/scripturi-tools/canal_marketing.py); comenzile fără atribuire ies „necunoscut”.
"""
import argparse, csv, json, subprocess, sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OH_HOST = "root@161.97.69.226"
RELIABLE_FROM = "2026-07"   # statusurile de colet au fost importate doar de la iulie-2026
SKIP_STORES = {"test"}
# refuzurile importate din iulie n-au `last_status_at` (17 din 1.347 în săpt. 29-iun) → la vârstă egală ar dispărea
# și săptămâna ar ieși 100%; de la 3-aug 99% au timestamp
WEEKLY_RELIABLE_FROM = "2026-08-03"

REMOTE = r'''
import asyncio, os, json, asyncpg
# Starea comenzii se ia din COLETE (shipments.derived_status), NU din orders.derived_status: acela se
# recalculează doar la webhook Shopify / status NOU de la curier, iar pollerul sare comenzile anulate
# (COD refuzat = comandă anulată în Shopify) și coletele importate deja finale → ~6.500 refuzuri din
# iulie și ~47.000 de comenzi din august n-au status pe comandă, deși coletul îl are.
SH = """select order_id,
          bool_or(derived_status = 'delivered') d,
          bool_or(derived_status = 'refused') r,
          bool_or(derived_status in ('in_transit','processed','pickup_office','delivery_issues','shipped')) t,
          bool_or(derived_status is not null) s,
          max(last_status_at) filter (where derived_status in ('delivered','refused')) t_final
        from shipments group by 1"""
async def m():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    are_canal = await c.fetchval("select to_regclass('public.comanda_canal') is not null")
    CJ = "left join comanda_canal cc on cc.order_id = o.id" if are_canal else ""
    CC = "coalesce(cc.canal, 'necunoscut')" if are_canal else "'necunoscut'"
    rows = await c.fetch(f"""
      with sh as ({SH})
      select s.name store, {CC} canal,
        to_char(date_trunc('month', o.created_at at time zone 'Europe/Bucharest'), 'YYYY-MM') luna,
        count(*) total,
        count(*) filter (where sh.s) cu_status,
        count(*) filter (where sh.d) livrate,
        count(*) filter (where sh.r and not sh.d) refuzate,
        count(*) filter (where sh.t and not sh.d and not sh.r) in_curs,
        count(*) filter (where sh.order_id is null and o.cancelled_at is not null) anulate
      from orders o join stores s on s.id = o.store_id left join sh on sh.order_id = o.id {CJ}
      where coalesce(o.is_demo, false) = false and coalesce(s.is_demo, false) = false
        and o.created_at >= ($1::text || '-01')::date
      group by 1, 2, 3 order by 3, 1""", SINCE)
    # cohortă săptămânală la VÂRSTĂ EGALĂ: livrat/refuzat contează doar dacă statusul final a venit în ≤ AGE zile
    weekly = await c.fetch(f"""
      with sh as ({SH})
      select s.name store, {CC} canal,
        to_char(date_trunc('week', o.created_at at time zone 'Europe/Bucharest'),'YYYY-MM-DD') sapt,
        count(*) filter (where sh.d and sh.t_final <= o.created_at + make_interval(days => $2)) livrate,
        count(*) filter (where sh.r and not sh.d and sh.t_final <= o.created_at + make_interval(days => $2)) refuzate
      from orders o join stores s on s.id = o.store_id join sh on sh.order_id = o.id {CJ}
      where coalesce(o.is_demo,false) = false and coalesce(s.is_demo,false) = false
        and o.created_at >= ($1::text || '-01')::date and o.created_at <= now() - make_interval(days => $2)
      group by 1, 2, 3 order by 3, 1""", SINCE, AGE)
    print(json.dumps({"monthly": [dict(r) for r in rows], "weekly": [dict(r) for r in weekly]}))
asyncio.run(m())
'''

def fetch(since, age):
    payload = f"SINCE = {since!r}\nAGE = {int(age)}\n" + REMOTE
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", OH_HOST, "docker exec -i orderhub-web python3 -"],
                       input=payload, capture_output=True, text=True, timeout=300)
    if r.returncode:
        sys.exit(f"Order Hub query failed:\n{r.stderr.strip()}")
    return json.loads(r.stdout.strip().splitlines()[-1])

def pct(a, b):
    return round(100 * a / b, 1) if b else None

def enrich(r):
    fin = r["livrate"] + r["refuzate"]
    r["rata_livrare"] = pct(r["livrate"], fin)                       # din colete FINALIZATE
    r["rata_minima"] = pct(r["livrate"], fin + r["in_curs"])          # dacă tot ce e în curs s-ar refuza
    r["acoperire"] = pct(r["cu_status"], r["total"])
    r["in_curs_pct"] = pct(r["in_curs"], fin + r["in_curs"])
    flags = []
    # înainte de iulie-2026 coletele au fost importate FĂRĂ status (mai/iunie: <1% acoperire)
    if r["luna"] < RELIABLE_FROM or (r["acoperire"] or 0) < 90: flags.append("DATE INCOMPLETE")
    if (r["in_curs_pct"] or 0) > 5: flags.append("IMATUR")            # refuzurile vin ultimele → rata scade
    r["flag"] = ",".join(flags)
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07", help="YYYY-MM (statusuri de colet din iul-2026)")
    ap.add_argument("--store", help="filtru pe numele magazinului (substring)")
    ap.add_argument("--min-orders", type=int, default=50)
    ap.add_argument("--age", type=int, default=14, help="zile de maturizare pt comparația săptămânală")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--csv")
    ap.add_argument("--canal", action="store_true", help="și pe canal de marketing (tiktok / meta / google / …)")
    a = ap.parse_args()

    data = fetch(a.since, a.age)
    # rândurile vin pe (magazin, canal, lună); vederea pe magazin le adună peste canale
    pe_mag = {}
    for r in data["monthly"]:
        if r["store"] in SKIP_STORES:
            continue
        t = pe_mag.setdefault((r["store"], r["luna"]), dict(r, canal="toate"))
        if t is not r and t.get("_v"):
            for k in ("total", "cu_status", "livrate", "refuzate", "in_curs", "anulate"):
                t[k] += r[k]
        t["_v"] = True
    rows = [enrich({k: v for k, v in r.items() if k != "_v"}) for r in pe_mag.values()]
    if a.store:
        rows = [r for r in rows if a.store.lower() in r["store"].lower()]

    months = sorted({r["luna"] for r in rows})
    tot = {}
    for r in rows:
        t = tot.setdefault(r["luna"], {k: 0 for k in ("total", "cu_status", "livrate", "refuzate", "in_curs", "anulate")})
        for k in t: t[k] += r[k]
    print("TOTAL PORTOFOLIU (Order Hub)")
    print(f"{'luna':8} {'comenzi':>8} {'livrate':>8} {'refuz':>7} {'în curs':>8} {'rată':>6} {'min':>6} {'acop':>5}  flag")
    for mth in months:
        t = enrich(dict(tot[mth], store="TOTAL", luna=mth))
        print(f"{mth:8} {t['total']:>8} {t['livrate']:>8} {t['refuzate']:>7} {t['in_curs']:>8} "
              f"{t['rata_livrare'] or 0:>5}% {t['rata_minima'] or 0:>5}% {t['acoperire'] or 0:>4}%  {t['flag']}")

    print("\nPER MAGAZIN — rată livrare % (livrate / livrate+refuzate); * = imatur, ? = date incomplete")
    stores = sorted({r["store"] for r in rows if r["total"] >= a.min_orders})
    by = {(r["store"], r["luna"]): r for r in rows}
    print(f"{'magazin':28}" + "".join(f"{m:>10}" for m in months) + f"{'Δ ultimele 2 mat.':>19}")
    for s in stores:
        cells, mature = [], []
        for mth in months:
            r = by.get((s, mth))
            if not r or r["total"] < a.min_orders or r["rata_livrare"] is None:
                cells.append(f"{'-':>10}"); continue
            mark = ("?" if "INCOMPLETE" in r["flag"] else "") + ("*" if "IMATUR" in r["flag"] else "")
            cells.append(f"{r['rata_livrare']:>9}{mark or ' '}")
            if not r["flag"]: mature.append(r["rata_livrare"])
        delta = f"{mature[-1] - mature[-2]:+.1f} pp" if len(mature) >= 2 else ""
        print(f"{s[:28]:28}" + "".join(cells) + f"{delta:>19}")

    wk_brut = [r for r in data["weekly"] if r["store"] not in SKIP_STORES and r["sapt"] >= WEEKLY_RELIABLE_FROM]
    wk_m = {}
    for r in wk_brut:
        t = wk_m.setdefault((r["store"], r["sapt"]), {"store": r["store"], "sapt": r["sapt"], "livrate": 0, "refuzate": 0})
        t["livrate"] += r["livrate"]; t["refuzate"] += r["refuzate"]
    wk = list(wk_m.values())
    if a.store:
        wk = [r for r in wk if a.store.lower() in r["store"].lower()]
    weeks = sorted({r["sapt"] for r in wk})[-a.weeks:]
    print(f"\nCOHORTE SĂPTĂMÂNALE LA VÂRSTĂ EGALĂ ({a.age} zile) — comparație corectă între săptămâni")
    wt = {}
    for r in wk:
        t = wt.setdefault(r["sapt"], [0, 0]); t[0] += r["livrate"]; t[1] += r["refuzate"]
    print(f"{'TOTAL':28}" + "".join(f"{w[5:]:>8}" for w in weeks))
    print(f"{'':28}" + "".join(f"{pct(*wt[w][:1], sum(wt[w])) or 0:>8}" for w in weeks))
    wby = {(r["store"], r["sapt"]): r for r in wk}
    for s in stores:
        cells = []
        for w in weeks:
            r = wby.get((s, w)); n = (r["livrate"] + r["refuzate"]) if r else 0
            cells.append(f"{pct(r['livrate'], n):>8}" if n >= max(20, a.min_orders // 4) else f"{'-':>8}")
        print(f"{s[:28]:28}" + "".join(cells))

    if a.canal:
        pe_canal(data, a, months, weeks)

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
        print(f"\nCSV: {a.csv}")

def pe_canal(data, a, months, weeks):
    """Rata de livrare pe CANAL × lună și cohortele săptămânale la vârstă egală pe canal."""
    cm, cw = {}, {}
    for r in data["monthly"]:
        if r["store"] in SKIP_STORES or (a.store and a.store.lower() not in r["store"].lower()):
            continue
        t = cm.setdefault((r["canal"], r["luna"]), [0, 0, 0]); t[0] += r["livrate"]; t[1] += r["refuzate"]; t[2] += r["in_curs"]
    for r in data["weekly"]:
        if r["store"] in SKIP_STORES or r["sapt"] < WEEKLY_RELIABLE_FROM or (a.store and a.store.lower() not in r["store"].lower()):
            continue
        t = cw.setdefault((r["canal"], r["sapt"]), [0, 0]); t[0] += r["livrate"]; t[1] += r["refuzate"]
    canale = sorted({c for c, _ in cm}, key=lambda c: -sum(v[0] + v[1] for (cc, _), v in cm.items() if cc == c))
    print("\nPE CANAL DE MARKETING — rată livrare % (livrate / livrate+refuzate), N = comenzi finalizate")
    print(f"{'canal':12}" + "".join(f"{m:>16}" for m in months))
    for c in canale:
        cells = []
        for m in months:
            l, f, _i = cm.get((c, m), [0, 0, 0])
            cells.append(f"{(str(pct(l, l + f)) + '%') if l + f else '-':>9} ({l + f:>4})")
        print(f"{c:12}" + "".join(cells))
    print(f"\nPE CANAL — cohorte săptămânale la vârstă egală ({a.age} zile)")
    print(f"{'canal':12}" + "".join(f"{w[5:]:>8}" for w in weeks))
    for c in canale:
        cells = []
        for w in weeks:
            l, f = cw.get((c, w), [0, 0])
            cells.append(f"{pct(l, l + f) if l + f >= 30 else '-':>8}")
        print(f"{c:12}" + "".join(cells))
    print("(costul pe livrată pe canal și mixul de spend pe magazin: shared/scripturi-tools/livrare_pe_canal.py)")


if __name__ == "__main__":
    main()
