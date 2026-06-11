# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
stock_restock_alerts.py — Raport stoc / restock pentru TOATE magazinele Arona Shopify.

Citește din metrics.inventory_daily_snapshots (istoric zilnic, denormalizat: sku,
title, vendor, qtyAvailable, costPerItem, retailValue) și calculează per SKU:
  - viteza de vânzare pe 28 zile (qty acum 28z - qty azi, clamp >= 0)
  - days_of_cover = stoc_curent / (viteza/28)
  - data proiectată de epuizare (azi + days_cover)
  - valoarea „gap"-ului de restock (cât COGS trebuie comandat ca să acoperi lead-ul)

Moduri:
  oos       — produse rupte de stoc (qty <= 0) care AU vândut în 28z
  low       — sub pragul de days-of-cover (default 14)
  restock   — prioritate de recomandat: se vor epuiza în <= lead zile, ranked după gap value
  deadstock — stoc dar ZERO vânzări în 28z (slow movers / overstock), ranked după valoare blocată
  value     — valoarea inventarului (COGS + retail) pe brand / total

NU scrie nimic în baze. Doar SELECT.

Folosire:
  uv run stock_restock_alerts.py --report oos       --brand all
  uv run stock_restock_alerts.py --report low       --brand grandia --lead 14
  uv run stock_restock_alerts.py --report restock    --brand all --lead 14 --limit 30
  uv run stock_restock_alerts.py --report deadstock  --brand esteban
  uv run stock_restock_alerts.py --report value      --brand all
"""
import os, sys, subprocess, argparse, urllib.parse, datetime
import pg8000.dbapi


def get_conn():
    url = os.environ.get("DATABASE_URL_METRICS")
    if not url:
        kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
        url = subprocess.run(["uv", "run", kb, "secret-get", "DATABASE_URL_METRICS"],
                             capture_output=True, text=True).stdout.strip()
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def brands(cur, want):
    cur.execute('SELECT id, slug, name FROM brands WHERE "isActive"=true ORDER BY name')
    rows = [{"id": r[0], "slug": r[1], "name": (r[2] or "").strip()} for r in cur.fetchall()]
    if want and want.lower() != "all":
        w = want.lower()
        rows = [b for b in rows if w in b["slug"].lower() or w in b["name"].lower()]
    return rows


def load_skus(cur, brand, window, max_real=100000):
    """Returnează rândurile per variant pentru un brand, cu velocity pe `window` zile.

    `max_real` = plafonul de stoc considerat REAL. SKU-urile cu stoc placeholder
    „infinit" (ex. produsele „surpriză"/mystery box seedate cu ~990.000 buc ca să
    nu se rupă niciodată de stoc în Shopify) sunt excluse — nu sunt inventar real
    și ar distorsiona viteza/valoarea. Întoarce și câte au fost sărite."""
    bid = brand["id"]
    # ultima zi cu snapshot pt brand
    cur.execute('SELECT MAX("snapshotDate") FROM inventory_daily_snapshots WHERE "brandId"=%s', (bid,))
    latest = cur.fetchone()[0]
    if latest is None:
        return [], None, None, 0
    # ziua de referință pt fereastra: cel mai apropiat snapshot <= latest - window
    cur.execute('SELECT MAX("snapshotDate") FROM inventory_daily_snapshots '
                'WHERE "brandId"=%s AND "snapshotDate" <= %s',
                (bid, latest - datetime.timedelta(days=window)))
    back = cur.fetchone()[0]
    if back is None:
        # nu avem destulă istorie -> luăm cel mai vechi snapshot disponibil
        cur.execute('SELECT MIN("snapshotDate") FROM inventory_daily_snapshots WHERE "brandId"=%s', (bid,))
        back = cur.fetchone()[0]
    actual_days = max((latest - back).days, 1)

    cur.execute(
        'WITH cur AS ('
        '  SELECT "variantId", sku, "productTitle", "variantTitle", "productType", vendor,'
        '         "quantityAvailable" qa, "costPerItem" cpi, price, "retailValue" rv'
        '  FROM inventory_daily_snapshots WHERE "brandId"=%s AND "snapshotDate"=%s'
        '), old AS ('
        '  SELECT "variantId", "quantityAvailable" qa'
        '  FROM inventory_daily_snapshots WHERE "brandId"=%s AND "snapshotDate"=%s'
        ') '
        'SELECT cur.sku, cur."productTitle", cur."variantTitle", cur."productType", cur.vendor,'
        '       cur.qa, cur.cpi, cur.price, cur.rv, old.qa '
        'FROM cur LEFT JOIN old USING ("variantId")',
        (bid, latest, bid, back))
    out = []
    skipped = 0
    for r in cur.fetchall():
        sku, title, vtitle, ptype, vendor, qa, cpi, price, rv, old_qa = r
        qa = int(qa or 0)
        old_qa = int(old_qa) if old_qa is not None else qa
        # placeholder „stoc infinit" (mystery box / surpriză) -> nu e inventar real
        if qa >= max_real or old_qa >= max_real:
            skipped += 1
            continue
        cpi = float(cpi or 0)
        price = float(price or 0)
        rv = float(rv or 0)
        sold = max(old_qa - qa, 0)               # unități vândute în fereastră (clamp >= 0)
        vel = sold / actual_days                 # unități/zi
        days_cover = (qa / vel) if vel > 0 else (None if qa > 0 else 0.0)
        if qa <= 0:
            stockout = None  # deja rupt
        elif vel > 0:
            stockout = datetime.date.today() + datetime.timedelta(days=round(qa / vel))
        else:
            stockout = None  # nu se mișcă
        out.append({
            "brand": brand["name"], "slug": brand["slug"],
            "sku": sku or "(fără SKU)", "title": title or "(fără titlu)",
            "vtitle": vtitle, "ptype": ptype, "vendor": vendor,
            "cur": qa, "old": old_qa, "sold": sold, "vel": vel,
            "days_cover": days_cover, "stockout": stockout,
            "cpi": cpi, "price": price, "retail_value": rv,
            "stock_value": qa * cpi,
        })
    return out, latest, back, actual_days, skipped


# ---------- formatare ----------
def f0(n): return "{:,.0f}".format(n) if n is not None else "-"
def fdc(n): return "∞" if n is None else ("{:.0f}".format(n) if n >= 10 else "{:.1f}".format(n))
def fdate(d): return d.isoformat() if d else "-"


def hr(): print("-" * 118)


def report_oos(rows, args):
    sel = [r for r in rows if r["cur"] <= 0 and r["sold"] > 0]
    sel.sort(key=lambda r: -r["sold"])
    print("=== RUPT DE STOC (OOS) — produse cu vânzări în %dz dar stoc <= 0 ===" % args.window)
    hr()
    print("%-9s %-44s %-12s %6s %7s %9s" % ("brand", "produs", "sku", "stoc", "vand%dz" % args.window, "vel/zi"))
    hr()
    for r in sel[:args.limit]:
        print("%-9s %-44s %-12s %6d %7d %9.2f" % (
            r["slug"][:9], r["title"][:44], r["sku"][:12], r["cur"], r["sold"], r["vel"]))
    if not sel:
        print("  (nimic rupt de stoc cu vânzări recente)")
    print("\nTOTAL SKU rupte cu cerere: %d" % len(sel))


def report_low(rows, args):
    sel = [r for r in rows if r["cur"] > 0 and r["days_cover"] is not None and r["days_cover"] <= args.threshold]
    sel.sort(key=lambda r: r["days_cover"])
    print("=== STOC SCĂZUT — days-of-cover <= %d ===" % args.threshold)
    hr()
    print("%-9s %-40s %-12s %6s %7s %7s %12s" % (
        "brand", "produs", "sku", "stoc", "vand%dz" % args.window, "zile", "epuizare"))
    hr()
    for r in sel[:args.limit]:
        print("%-9s %-40s %-12s %6d %7d %7s %12s" % (
            r["slug"][:9], r["title"][:40], r["sku"][:12], r["cur"], r["sold"],
            fdc(r["days_cover"]), fdate(r["stockout"])))
    if not sel:
        print("  (niciun produs sub prag)")
    print("\nTOTAL SKU sub %d zile cover: %d" % (args.threshold, len(sel)))


def report_restock(rows, args):
    # prioritate: se epuizează în <= lead zile (sau deja rupte cu cerere); ranked după gap value
    sel = []
    for r in rows:
        if r["vel"] <= 0:
            continue
        dc = r["days_cover"] if r["days_cover"] is not None else 0.0
        if r["cur"] <= 0 or dc <= args.lead:
            # cât trebuie comandat ca să acoperi (lead + buffer) zile de vânzare, minus stocul curent
            target_units = r["vel"] * (args.lead + args.buffer)
            gap_units = max(target_units - r["cur"], 0)
            r["gap_units"] = gap_units
            r["gap_value"] = gap_units * r["cpi"]
            sel.append(r)
    sel.sort(key=lambda r: (-r["gap_value"], r["days_cover"] if r["days_cover"] is not None else -1))
    print("=== PRIORITATE RESTOCK — se epuizează în <= %d zile (lead) [+%d buffer] ===" % (args.lead, args.buffer))
    hr()
    print("%-9s %-38s %-11s %5s %6s %6s %8s %11s %9s" % (
        "brand", "produs", "sku", "stoc", "vand", "zile", "de_com", "epuizare", "gap_cost"))
    hr()
    tot_gap = 0.0
    for r in sel[:args.limit]:
        tot_gap += r["gap_value"]
        print("%-9s %-38s %-11s %5d %6d %6s %8.0f %11s %9s" % (
            r["slug"][:9], r["title"][:38], r["sku"][:11], r["cur"], r["sold"],
            fdc(r["days_cover"]), r["gap_units"], fdate(r["stockout"]), f0(r["gap_value"])))
    if not sel:
        print("  (nimic de recomandat în fereastra de lead)")
    full_gap = sum(r["gap_value"] for r in sel)
    print("\nTOTAL SKU de recomandat: %d | valoare gap afișat (COGS): %s | valoare gap TOTAL: %s" % (
        len(sel), f0(tot_gap), f0(full_gap)))


def report_deadstock(rows, args):
    # stoc > 0 dar ZERO vânzări în fereastră; ranked după valoarea blocată (COGS)
    sel = [r for r in rows if r["cur"] > 0 and r["sold"] == 0]
    sel.sort(key=lambda r: -r["stock_value"])
    print("=== DEAD-STOCK / OVERSTOCK — stoc dar 0 vânzări în %dz ===" % args.window)
    hr()
    print("%-9s %-46s %-12s %7s %12s" % ("brand", "produs", "sku", "stoc", "val_COGS"))
    hr()
    tot = 0.0
    for r in sel[:args.limit]:
        tot += r["stock_value"]
        print("%-9s %-46s %-12s %7d %12s" % (
            r["slug"][:9], r["title"][:46], r["sku"][:12], r["cur"], f0(r["stock_value"])))
    full = sum(r["stock_value"] for r in sel)
    if not sel:
        print("  (niciun dead-stock)")
    print("\nTOTAL SKU dead-stock: %d | capital blocat afișat: %s | capital blocat TOTAL: %s" % (
        len(sel), f0(tot), f0(full)))


def report_value(by_brand, args):
    print("=== VALOARE INVENTAR pe brand (azi) ===")
    hr()
    print("%-16s %8s %8s %10s %14s %14s %10s" % (
        "brand", "SKU", "OOS", "unități", "val_COGS", "val_retail", "dead_val"))
    hr()
    tot_cogs = tot_retail = tot_units = 0.0
    tot_sku = tot_oos = 0
    tot_dead = 0.0
    for name, rows in sorted(by_brand.items()):
        cogs = sum(r["stock_value"] for r in rows)
        retail = sum(r["retail_value"] for r in rows)
        units = sum(r["cur"] for r in rows)
        sku = len(rows)
        oos = sum(1 for r in rows if r["cur"] <= 0)
        dead = sum(r["stock_value"] for r in rows if r["cur"] > 0 and r["sold"] == 0)
        tot_cogs += cogs; tot_retail += retail; tot_units += units
        tot_sku += sku; tot_oos += oos; tot_dead += dead
        print("%-16s %8d %8d %10d %14s %14s %10s" % (
            name[:16], sku, oos, units, f0(cogs), f0(retail), f0(dead)))
    hr()
    print("%-16s %8d %8d %10d %14s %14s %10s" % (
        "TOTAL", tot_sku, tot_oos, int(tot_units), f0(tot_cogs), f0(tot_retail), f0(tot_dead)))


def main():
    ap = argparse.ArgumentParser(description="Raport stoc/restock pentru magazinele Arona Shopify")
    ap.add_argument("--report", choices=["oos", "low", "restock", "deadstock", "value"], default="restock")
    ap.add_argument("--brand", default="all", help="slug/nume brand sau 'all'")
    ap.add_argument("--window", type=int, default=28, help="fereastra de velocity (zile, default 28)")
    ap.add_argument("--threshold", type=int, default=14, help="prag days-of-cover pt raportul low (default 14)")
    ap.add_argument("--lead", type=int, default=14, help="lead time furnizor pt restock (zile, default 14)")
    ap.add_argument("--buffer", type=int, default=14, help="buffer suplimentar de acoperit la restock (zile)")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--max-real-stock", type=int, default=100000, dest="max_real",
                    help="plafon stoc real; peste el = placeholder 'infinit' (mystery box) și se exclude")
    args = ap.parse_args()

    conn = get_conn(); cur = conn.cursor()
    bs = brands(cur, args.brand)
    if not bs:
        print("Niciun brand activ pt filtrul '%s'." % args.brand); return

    all_rows, by_brand = [], {}
    meta = []
    for b in bs:
        rows, latest, back, days, _skipped = load_skus(cur, b, args.window)
        if rows:
            all_rows.extend(rows)
            by_brand[b["name"]] = rows
            meta.append((b["name"], latest, back, days, len(rows)))
    conn.close()

    if not all_rows:
        print("Nu există snapshot-uri de inventar pt selecție."); return

    print("Brand(uri): %s | fereastra velocity: %d zile" % (", ".join(b["name"] for b in bs), args.window))
    for name, latest, back, days, n in meta:
        print("  %-16s snapshot %s vs %s (%d zile reale), %d SKU" % (
            name[:16], fdate(latest.date() if hasattr(latest, "date") else latest),
            fdate(back.date() if hasattr(back, "date") else back), days, n))
    print()

    if args.report == "oos":
        report_oos(all_rows, args)
    elif args.report == "low":
        report_low(all_rows, args)
    elif args.report == "restock":
        report_restock(all_rows, args)
    elif args.report == "deadstock":
        report_deadstock(all_rows, args)
    elif args.report == "value":
        report_value(by_brand, args)


if __name__ == "__main__":
    main()
