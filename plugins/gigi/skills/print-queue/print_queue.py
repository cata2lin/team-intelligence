# /// script
# requires-python = ">=3.10"
# dependencies = ["requests", "psycopg2-binary", "pypdf>=4"]
# ///
"""
print_queue.py — coada de print (xConnector) pt DEPOZIT: DESCOPERIRE rapidă + PRINT în Chrome.

FLUX: interoghează xConnector pe TOATE magazinele ÎN PARALEL (doar comenzi NEexpediate) → filtrează
coada „de printat" (etichetă AWB nedescărcată) → salvează în `metrics.print_queue`. Depozitul apoi
INTEROGHEAZĂ INSTANT din DB și, când vrea, DESCHIDE etichetele filtrate ÎN CHROME (NU printează singur
— operatorul apasă Ctrl+P). Nu cere SumatraPDF/qpdf — merge PDF cu pypdf (Python) + Chrome.

  # 1. REFRESH index (~20s; la început de sesiune de print). Interval de zile: --days N sau --from/--to
  uv run print_queue.py sync --apply
  uv run print_queue.py sync --apply --from 2026-07-10 --to 2026-07-14

  # 2. CE E DE PRINTAT (instant, semantic pe magazin/țară/tip/SKU/cantitate)
  uv run print_queue.py query --sku HA --country RO --by-sku       # câte HA pe RO, per SKU
  uv run print_queue.py query --store esteban --items 3 --by-sku   # parfumuri de 3 pe Esteban
  uv run print_queue.py query --country RO --by-store              # tot ce-i de printat pe RO

  # 3. PRINT (deschide în CHROME ce-i filtrat; NU printează singur). Dry-run implicit; --open = execută.
  uv run print_queue.py print --sku HA --country RO                # DRY-RUN: ce s-ar deschide
  uv run print_queue.py print --sku HA --country RO --open         # descarcă+merge PDF → deschide Chrome → marchează printat
Read-only fără --apply/--open. Scrie DOAR în metrics.print_queue.
"""
import os, sys, argparse, subprocess, datetime, time, tempfile, collections, urllib.parse as up, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "xconnector"))
import xconnector as X
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")

# ── MAPARE SEMANTICĂ: domeniu myshopify (fără sufix) → magazin (name/country/type/aliases) ──
STORES = {
    "6f9e22-9d":      {"name": "Esteban",       "country": "RO",   "type": "perfume",   "aliases": ["esteban", "est"]},
    "ix5bxc-hr":      {"name": "George Talent",  "country": "RO",   "type": "perfume",   "aliases": ["george talent", "george", "talent", "gt"]},
    "bmuwvv-jy":      {"name": "Nubra",          "country": "RO",   "type": "perfume",   "aliases": ["nubra", "nub"]},
    "8e3700-d9":      {"name": "Apreciat",       "country": "RO",   "type": "perfume",   "aliases": ["apreciat", "apr"]},
    "1eee37-2d":      {"name": "Nocturna",       "country": "RO",   "type": "perfume",   "aliases": ["nocturna", "noc"]},
    "de51c5-b8":      {"name": "NocturnaLux",    "country": "RO",   "type": "perfume",   "aliases": ["nocturnalux", "lux"]},
    "dvk4hu-dq":      {"name": "Belasil",        "country": "RO",   "type": "cosmetics", "aliases": ["belasil", "bela"]},
    "1d2bce-2":       {"name": "Rossi Nails",    "country": "RO",   "type": "nails",     "aliases": ["rossi", "rossi nails", "nails", "unghii"]},
    "cn54vk-uz":      {"name": "Gento",          "country": "RO",   "type": "bags",      "aliases": ["gento", "gen", "genti"]},
    "nxfer1-n4":      {"name": "Carpetto",       "country": "RO",   "type": "carpets",   "aliases": ["carpetto", "carp"]},
    "bb4nmc-pb":      {"name": "Covoria",        "country": "RO",   "type": "carpets",   "aliases": ["covoria", "cov"]},
    "n12w89-yy":      {"name": "Grandia",        "country": "RO",   "type": "home",      "aliases": ["grandia", "gran"]},
    "ofertelezilei":  {"name": "Ofertele Zilei", "country": "RO",   "type": "deals",     "aliases": ["ofertele", "ofertele zilei", "ofertelezilei", "ofer"]},
    "covoareauto-ro": {"name": "MagDeal",        "country": "RO",   "type": "deals",     "aliases": ["magdeal", "mag", "covoareauto"]},
    "audusp-rf":      {"name": "Reduceri Bune",  "country": "RO",   "type": "deals",     "aliases": ["reduceri", "reduceri bune", "reduceribune", "red"]},
    "bonhaus":        {"name": "Bonhaus RO",     "country": "RO",   "type": "deals",     "aliases": ["bonhaus", "bonhaus ro", "bon"]},
    "ux1x6n-n2":      {"name": "Bonhaus BG",     "country": "INTL", "type": "deals",     "aliases": ["bonhaus bg", "bonhausbg", "bonbg", "bg"]},
    "vthuzq-7j":      {"name": "Bonhaus CZ",     "country": "INTL", "type": "deals",     "aliases": ["bonhaus cz", "bonhauscz", "cz"]},
    "f0yrmh-ia":      {"name": "Bonhaus PL",     "country": "INTL", "type": "deals",     "aliases": ["bonhaus pl", "bonhauspl", "pl"]},
}
TYPE_ALIASES = {"deals": "deals", "oferte": "deals", "ha": "deals", "parfum": "perfume", "parfumuri": "perfume",
                "perfume": "perfume", "covoare": "carpets", "carpets": "carpets", "unghii": "nails", "nails": "nails"}


def _key(d): return (d or "").split(".")[0]
def store_of(d): return STORES.get(_key(d), {"name": _key(d), "country": "RO", "type": "other", "aliases": []})
def resolve_domains(token):
    t = (token or "").strip().lower()
    if not t: return set()
    out = set()
    for k, s in STORES.items():
        if (t == k or t in k or t in s["name"].lower() or t in s["aliases"]
                or t == s["country"].lower() or t == s["type"] or TYPE_ALIASES.get(t) == s["type"]):
            out.add(k)
    return out


def _metrics():
    dsn = os.environ.get("DATABASE_URL_METRICS") or subprocess.run(
        ["uv", "run", KB, "secret-get", "DATABASE_URL_METRICS"], capture_output=True, text=True).stdout.strip()
    import psycopg2
    p = up.urlsplit(dsn)
    return psycopg2.connect(up.urlunsplit((p.scheme, p.netloc, p.path, "", "")))


DDL = """
CREATE TABLE IF NOT EXISTS public.print_queue (
  store_domain text, store text, country text, store_type text, order_name text, order_id text,
  sku text, total_items int, line_items_count int, hold_status text,
  connector_id text, tracking_number text, label_url text,
  synced_at timestamptz DEFAULT now(), printed_at timestamptz,
  PRIMARY KEY (order_id, sku)
);
ALTER TABLE public.print_queue ADD COLUMN IF NOT EXISTS store_type text;
ALTER TABLE public.print_queue ADD COLUMN IF NOT EXISTS connector_id text;
ALTER TABLE public.print_queue ADD COLUMN IF NOT EXISTS tracking_number text;
ALTER TABLE public.print_queue ADD COLUMN IF NOT EXISTS label_url text;
ALTER TABLE public.print_queue ADD COLUMN IF NOT EXISTS printed_at timestamptz;
CREATE INDEX IF NOT EXISTS idx_pq_country_sku ON public.print_queue (country, sku);
CREATE INDEX IF NOT EXISTS idx_pq_type ON public.print_queue (store_type);
CREATE INDEX IF NOT EXISTS idx_pq_store ON public.print_queue (store);
CREATE INDEX IF NOT EXISTS idx_pq_items ON public.print_queue (total_items);
"""


# ───────────────────────── SYNC ─────────────────────────
def _pull(sh, dfrom, dto):
    info = store_of(sh["shopDomain"])
    xc = X.XC(sh["apiKey"])
    rows, n, q = [], 0, 0
    for o in xc.orders(dfrom, dto, {"dispatched": "false"}):
        n += 1
        doc = X.awb_doc(o)
        if not doc or doc.get("downloaded") is not False:
            continue
        q += 1
        cid = doc.get("connectorId"); trk = X.doc_tracking(doc)
        lurl = doc.get("url") or doc.get("awbPdfUrl") or (
            X.XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s" % (cid, up.quote(str(trk or ""))))
        for sku in ([s for s in (o.get("skus") or []) if s] or [None]):
            rows.append((sh["shopDomain"], info["name"], info["country"], info["type"], o.get("orderName"),
                         str(o.get("orderId")), sku, int(o.get("totalItemsCount") or 0),
                         int(o.get("lineItemsCount") or 0), str(o.get("holdStatus") or ""), cid, trk, lurl))
    return {"name": info["name"], "country": info["country"], "queue": q, "rows": rows}


def cmd_sync(a):
    # implicit: coada până IERI (nu printăm ziua curentă — comenzile de azi încă intră)
    dto = a.to or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    dfrom = a.from_ or (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    shops = X.load_shops()
    if a.shop:
        want = set().union(*(resolve_domains(t) for t in a.shop.split(",")))
        shops = [s for s in shops if _key(s["shopDomain"]) in want]
    print("═" * 66); print("  REFRESH coadă print — %d magazine (paralel, NEexpediate, %s→%s)" % (len(shops), dfrom, dto)); print("═" * 66)
    res = []
    with ThreadPoolExecutor(max_workers=min(10, len(shops) or 1)) as ex:
        futs = {ex.submit(_pull, s, dfrom, dto): s for s in shops}
        for f in as_completed(futs):
            try: res.append(f.result())
            except Exception as e: print("  ⚠️ %s: %s" % (futs[f]["shopDomain"], str(e)[:60]))
    res.sort(key=lambda r: -r["queue"])
    rows = [r for x in res for r in x["rows"]]
    for x in res:
        if x["queue"]: print("  %-16s %-5s %5d de printat" % (x["name"][:16], x["country"], x["queue"]))
    print("─" * 66); print("  TOTAL: %d comenzi de printat → %d linii SKU" % (sum(x["queue"] for x in res), len(rows)))
    if not a.apply:
        print("\n  DRY-RUN — nu am scris. Adaugă --apply."); return
    import psycopg2.extras
    cn = _metrics(); cur = cn.cursor(); cur.execute(DDL)
    if a.shop:
        cur.execute("DELETE FROM public.print_queue WHERE store_domain IN %s", (tuple(set(s["shopDomain"] for s in shops)),))
    else:
        cur.execute("DELETE FROM public.print_queue")
    if rows:
        psycopg2.extras.execute_values(cur,
            """INSERT INTO public.print_queue
               (store_domain,store,country,store_type,order_name,order_id,sku,total_items,line_items_count,hold_status,connector_id,tracking_number,label_url)
               VALUES %s ON CONFLICT (order_id,sku) DO UPDATE SET total_items=EXCLUDED.total_items, synced_at=now(), printed_at=NULL""",
            rows, page_size=2000)
    cn.commit(); cn.close()
    print("  ✓ metrics.print_queue reîmprospătat: %d linii" % len(rows))


# ───────────────────────── filtru comun ─────────────────────────
def _where(a):
    where, params = ["TRUE"], []
    if getattr(a, "country", None): where.append("country=%s"); params.append(a.country.upper())
    if getattr(a, "type", None): where.append("store_type=%s"); params.append(TYPE_ALIASES.get(a.type.lower(), a.type.lower()))
    if getattr(a, "store", None):
        dk = set().union(*(resolve_domains(t) for t in a.store.split(",")))
        if dk: where.append("store_domain IN %s"); params.append(tuple(k + ".myshopify.com" for k in dk))
        else: where.append("lower(store) LIKE %s"); params.append("%" + a.store.lower() + "%")
    if getattr(a, "sku", None): where.append("sku ILIKE %s"); params.append(a.sku.upper() + "%")
    if getattr(a, "items", None) is not None: where.append("total_items=%s"); params.append(a.items)
    return " AND ".join(where), params


def _check_index(cur):
    try:
        cur.execute("SELECT max(synced_at) FROM public.print_queue"); return cur.fetchone()[0]
    except Exception:
        return None


# ───────────────────────── QUERY ─────────────────────────
def cmd_query(a):
    cn = _metrics(); cur = cn.cursor()
    snap = _check_index(cur)
    if not snap:
        print("  Index gol — rulează întâi: print_queue.py sync --apply"); return
    w, params = _where(a); print("  (index: %s)" % snap)
    if a.by_sku:
        cur.execute(f"SELECT sku, count(DISTINCT order_id) etichete, sum(total_items) buc FROM public.print_queue WHERE {w} AND printed_at IS NULL GROUP BY sku ORDER BY etichete DESC", params)
        r = cur.fetchall(); print("  %-18s %9s %8s" % ("SKU", "etichete", "buc"))
        for sku, e, b in r: print("  %-18s %9d %8s" % (sku or "—", e, b))
        print("  ── %d SKU-uri, %d etichete total" % (len(r), sum(x[1] for x in r)))
    elif a.by_store:
        cur.execute(f"SELECT store, country, store_type, count(DISTINCT order_id) e FROM public.print_queue WHERE {w} AND printed_at IS NULL GROUP BY store,country,store_type ORDER BY e DESC", params)
        for st, co, ty, e in cur.fetchall(): print("  %-18s %-5s %-9s %6d etichete" % (st, co, ty, e))
    else:
        cur.execute(f"SELECT DISTINCT order_name, store, total_items, sku FROM public.print_queue WHERE {w} AND printed_at IS NULL ORDER BY store, order_name LIMIT %s", params + [a.limit])
        for nm, st, ti, sku in cur.fetchall(): print("  %-12s %-16s %d buc  %s" % (nm, st[:16], ti, sku or ""))
    cn.close()


# ───────────────────────── PRINT (deschide în Chrome; NU printează singur) ─────────────────────────
def _open_chrome(path):
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Google Chrome", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            subprocess.Popen(["cmd", "/c", "start", "chrome", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["google-chrome", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def cmd_print(a):
    cn = _metrics(); cur = cn.cursor()
    if not _check_index(cur):
        print("  Index gol — rulează întâi: print_queue.py sync --apply"); return
    w, params = _where(a)
    # o etichetă per COMANDĂ (nu per SKU) — dedup pe order_id
    cur.execute(f"""SELECT DISTINCT ON (order_id) order_id, order_name, store, store_domain, tracking_number, label_url
                    FROM public.print_queue WHERE {w} AND printed_at IS NULL AND label_url IS NOT NULL
                    ORDER BY order_id, store""", params)
    jobs = cur.fetchall()
    print("═" * 60); print("  PRINT %s — %d etichete de deschis în Chrome" % ("(APLIC)" if a.open else "(DRY-RUN)", len(jobs))); print("═" * 60)
    for oid, nm, st, dom, trk, url in jobs[:30]:
        print("  %-12s %-16s AWB %s" % (nm, st[:16], trk or "—"))
    if len(jobs) > 30: print("  … +%d" % (len(jobs) - 30))
    if not a.open:
        print("\n  DRY-RUN — nimic descărcat/deschis. Adaugă --open ca să deschizi în Chrome + marchezi printat."); cn.close(); return
    if not jobs:
        print("  Nimic de printat."); cn.close(); return
    # descarcă în LOTURI de max --batch etichete → un PDF/lot → deschide fiecare în Chrome (Chrome/imprimanta nu duc un PDF uriaș)
    from pypdf import PdfWriter
    xcs = {s["shopDomain"]: X.XC(s["apiKey"]) for s in X.load_shops()}
    outdir = os.path.join(tempfile.gettempdir(), "print_queue")
    os.makedirs(outdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%H%M%S")
    BATCH = a.batch or 250
    lots = [jobs[i:i + BATCH] for i in range(0, len(jobs), BATCH)]
    done_ids = []; fails = 0; total_ok = 0; files = []
    for bi, chunk in enumerate(lots, 1):
        writer = PdfWriter(); ok_ids = []
        for oid, nm, st, dom, trk0, url0 in chunk:
            xc = xcs.get(dom)
            try:
                # RE-INTEROGHEAZĂ comanda ACUM (AWB-ul poate s-a schimbat între sync și print) — nu folosi URL-ul vechi
                s, d = xc.get("/api/orders/by-id", "orderId=%s" % oid)
                o = d if (isinstance(d, dict) and d.get("orderId")) else ((d.get("order") if isinstance(d, dict) else None) or d)
                doc = X.awb_doc(o) if o else None
                if not doc:
                    print("  ⚠️ %s: nu mai are AWB (sar)" % nm); continue
                if doc.get("downloaded") is not False:
                    done_ids.append(oid); continue  # descărcat/printat între timp → doar marchez printat
                cid = doc.get("connectorId"); trk = X.doc_tracking(doc)
                url = doc.get("url") or doc.get("awbPdfUrl") or (
                    X.XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s" % (cid, up.quote(str(trk or ""))))
                req = urllib.request.Request(url, headers={"Authorization": xc.h["Authorization"]}) if url.startswith(X.XBASE) else urllib.request.Request(url)
                data = urllib.request.urlopen(req, timeout=60).read()
                fp = os.path.join(outdir, "%s.pdf" % nm)
                open(fp, "wb").write(data)
                writer.append(fp); ok_ids.append(oid)
            except Exception as e:
                fails += 1; print("  ⚠️ %s: %s" % (nm, str(e)[:50]))
            time.sleep(0.5)  # pacing ≤2/s
        if not ok_ids:
            continue
        merged = os.path.join(outdir, "print_%s_lot%02d.pdf" % (stamp, bi))
        with open(merged, "wb") as fh: writer.write(fh)
        _open_chrome(merged); files.append(merged)
        cur.execute("UPDATE public.print_queue SET printed_at=now() WHERE order_id IN %s", (tuple(ok_ids),)); cn.commit()
        total_ok += len(ok_ids)
        print("  ✓ lot %d/%d: %d etichete → %s (deschis în Chrome)" % (bi, len(lots), len(ok_ids), os.path.basename(merged)))
    if done_ids:
        cur.execute("UPDATE public.print_queue SET printed_at=now() WHERE order_id IN %s", (tuple(done_ids),)); cn.commit()
        print("  (%d erau deja descărcate → marcate printat)" % len(done_ids))
    cn.close()
    print("\n  ✓ TOTAL %d etichete în %d lot(uri) de max %d → apasă Ctrl+P în fiecare fereastră Chrome.%s" % (
        total_ok, len(files), BATCH, (" %d eșec." % fails) if fails else ""))


def cmd_printed(a):
    """Câte s-au PRINTAT de la baseline (indexul). xConnector n-are timestamp pe descărcare → singura
    cale = DIFF: comenzile din index care ACUM sunt downloaded=true = printate de la baseline încoace.
    Cu cronul de la 1 noaptea (baseline = început de zi), asta = „printate azi"."""
    cn = _metrics(); cur = cn.cursor()
    snap = _check_index(cur)
    if not snap:
        print("  Index gol — rulează întâi: print_queue.py sync --apply"); return
    w, params = _where(a)
    cur.execute(f"SELECT DISTINCT order_id, store_domain, store FROM public.print_queue WHERE {w}", params)
    rows = cur.fetchall(); cn.close()
    xcs = {s["shopDomain"]: X.XC(s["apiKey"]) for s in X.load_shops()}
    def chk(oid, dom, store):
        try:
            s, d = xcs[dom].get("/api/orders/by-id", "orderId=%s" % oid)
            o = d if (isinstance(d, dict) and d.get("orderId")) else ((d.get("order") if isinstance(d, dict) else None) or d)
            doc = X.awb_doc(o) if o else None
            return (store, doc.get("downloaded") if doc else None)
        except Exception:
            return (store, "err")
    printed = collections.Counter(); still = collections.Counter(); total = 0
    with ThreadPoolExecutor(max_workers=14) as ex:
        for f in as_completed([ex.submit(chk, oid, dom, st) for oid, dom, st in rows]):
            st, dl = f.result(); total += 1
            printed[st] += 1 if dl is True else 0
            still[st] += 1 if dl is False else 0
    print("  Baseline (index): %s · %d comenzi verificate" % (snap, total))
    print("  ✓ PRINTATE de la baseline: %d" % sum(printed.values()))
    for st, c in printed.most_common():
        if c: print("     %-18s %d" % (st, c))
    print("  · încă de printat: %d" % sum(still.values()))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync"); s.add_argument("--apply", action="store_true"); s.add_argument("--days", type=int, default=14)
    s.add_argument("--from", dest="from_"); s.add_argument("--to"); s.add_argument("--shop"); s.set_defaults(fn=cmd_sync)
    q = sub.add_parser("query")
    for f in ["sku", "store", "country", "type"]: q.add_argument("--" + f)
    q.add_argument("--items", type=int); q.add_argument("--by-sku", action="store_true"); q.add_argument("--by-store", action="store_true"); q.add_argument("--limit", type=int, default=60); q.set_defaults(fn=cmd_query)
    pr = sub.add_parser("print")
    for f in ["sku", "store", "country", "type"]: pr.add_argument("--" + f)
    pr.add_argument("--items", type=int); pr.add_argument("--open", action="store_true"); pr.add_argument("--batch", type=int, default=250); pr.set_defaults(fn=cmd_print)
    pt = sub.add_parser("printed")
    for f in ["sku", "store", "country", "type"]: pt.add_argument("--" + f)
    pt.add_argument("--items", type=int); pt.set_defaults(fn=cmd_printed)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
