# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
deliverability_monitor.py — Diagnoza scurgerii de bani din REFUZ ramburs / colete
nelivrate, pe toate brandurile Arona.

Sursa de adevar = engine-ul de profitabilitate de pe VPS (data/profitability.db):
  profit_orders(month, prefix, order_name, revenue, currency, status_category
                [Livrata/Refuzata/Netrimisa/Anulata/'In curs de livrare'],
                courier_key, courier_status, skus, tags)
  profit_transport_costs(month, prefix, cost_per_parcel)
  profit_sku_titles(sku, title)

Pentru defalcarea pe JUDET facem join in Python pe order_name cu baza `metrics`
(Postgres): orders.name -> orders."shippingProvince" per brand. Judetul exista
doar pentru brandurile RO (EST/GT/NUB/GRAN/RED/BON/OFER/MAG/...); brandurile
straine (CZ/PL/BG) n-au shippingProvince populat -> apar ca '?'.

Calculeaza: rata de refuz, venitul-la-risc si transportul irosit
(colete refuzate x cost_per_parcel x2 = dus-intors) pe brand, pe curier,
pe judet si pe SKU. Scoate la suprafata buzunarele judet x curier x brand cele
mai scumpe ca ops sa poata bloca judete, sa stranga validarea adresei la COD,
sau sa schimbe curierul pe regiune.

NU scrie nimic in nicio baza. Doar SELECT.

Folosire:
  uv run deliverability_monitor.py --month 2026-05
  uv run deliverability_monitor.py --month 2026-05 --by courier
  uv run deliverability_monitor.py --month 2026-05 --by county --brand EST
  uv run deliverability_monitor.py --month 2026-05 --by sku --brand EST --limit 20
  uv run deliverability_monitor.py --month 2026-05 --by pocket   # judet x curier x brand
  uv run deliverability_monitor.py --month 2026-05 --brand GT

Brand = prefixul din profit_orders (EST, GT, NUB, GRAN, RED, BON, OFER, MAG,
CZ, PL, BG, BELA, GEN, LUX, NOC, ROSSI, CARP, COV, APR, BONBG, PAT, BG).
"""
import sys, os, json, subprocess, argparse, urllib.parse
import pg8000.dbapi

VPS_HOST = "root@84.46.242.181"
VPS_PY = "/root/Scripturi/.venv/bin/python3"
VPS_DB = "/root/Scripturi/data/profitability.db"

# statusurile care numara drept "colet trimis" (a plecat efectiv la curier)
SENT = ("Livrata", "Refuzata", "In curs de livrare")
# statusul care e pierdere directa
REFUSED = "Refuzata"

# prefix profitabilitate -> slug brand in metrics (pentru join judet)
PREFIX_TO_SLUG = {
    "EST": "esteban", "GT": "george-talent", "NUB": "nubra", "GRAN": "grandia",
    "RED": "reduceri-bune", "BON": "bonhaus", "GEN": "gento", "CARP": "carpetto",
    "COV": "covoria", "CZ": "bonhaus-cz", "PL": "bonhaus-pl", "BONBG": "bonhaus-bg",
    "BG": "nocturna-bg", "BELA": "belasil", "NOC": "nocturna", "LUX": "nocturna-lux",
    "ROSSI": "rossi-nails", "OFER": "ofertele-zilei", "MAG": "magdeal",
    "APR": "apreciat", "PAT": "ce-pat-ai",
}
# curierele pe care le aratam frumos
COURIER_LABEL = {"dpd-ro": "DPD", "packeta": "Packeta", "econt": "Econt",
                 "sameday": "Sameday", "unknown": "(necunoscut)"}


# ---------------- Postgres metrics (read-only) -----------------
def get_metrics_conn():
    url = os.environ.get("DATABASE_URL_METRICS")
    if not url:
        kb = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "core", "scripts", "kb.py")
        url = subprocess.run(["uv", "run", kb, "secret-get", "DATABASE_URL_METRICS"],
                             capture_output=True, text=True).stdout.strip()
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True,
                                user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432,
                                database=(u.path or "/").lstrip("/"))


def fetch_provinces(month, prefixes):
    """order_name -> shippingProvince din metrics, pentru lunile/brandurile cerute."""
    slugs = sorted({PREFIX_TO_SLUG[p] for p in prefixes if p in PREFIX_TO_SLUG})
    if not slugs:
        return {}
    lo = month + "-01"
    try:
        conn = get_metrics_conn()
    except Exception as e:
        sys.stderr.write("ATENTIE: nu m-am putut conecta la metrics (%s); "
                         "judetul va lipsi.\n" % e)
        return {}
    cur = conn.cursor()
    ph = ",".join(["%s"] * len(slugs))
    # luam o fereastra ceva mai larga ca sa prindem comenzile create la final de
    # luna care apar in profitabilitate cu data lunii respective
    cur.execute(
        'SELECT o.name, o."shippingProvince" '
        'FROM orders o JOIN brands b ON b.id=o."brandId" '
        'WHERE b.slug IN (' + ph + ') '
        "AND o.\"shopifyCreatedAt\" >= %s::date - INTERVAL '5 days' "
        "AND o.\"shopifyCreatedAt\" <  (%s::date + INTERVAL '1 month' + INTERVAL '10 days')",
        slugs + [lo, lo])
    prov = {}
    for name, province in cur.fetchall():
        if name:
            prov[name] = (province or "").strip() or "?"
    conn.close()
    return prov


# ---------------- VPS profitability.db (read-only) -----------------
VPS_SCRIPT = r'''
import sqlite3, json, sys
month = sys.argv[1]
brand = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
c = sqlite3.connect("%(db)s")
cur = c.cursor()
where = "month=?"
args = [month]
if brand:
    where += " AND prefix=?"
    args.append(brand)
rows = []
for r in cur.execute(
    "SELECT prefix, order_name, revenue, currency, status_category, "
    "courier_key, courier_status, skus FROM profit_orders WHERE " + where, args):
    rows.append({
        "prefix": r[0], "order_name": r[1], "revenue": r[2] or 0.0,
        "currency": r[3] or "", "status": r[4] or "", "courier": r[5] or "unknown",
        "courier_status": r[6] or "", "skus": r[7] or "",
    })
tc = {}
for r in cur.execute("SELECT prefix, cost_per_parcel FROM profit_transport_costs WHERE month=?", [month]):
    tc[r[0]] = r[1] or 0.0
titles = {}
for r in cur.execute("SELECT sku, title FROM profit_sku_titles"):
    titles[r[0]] = r[1]
print(json.dumps({"rows": rows, "transport": tc, "titles": titles}))
''' % {"db": VPS_DB}


def fetch_vps(month, brand):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS_HOST,
           VPS_PY + " - " + month + " " + (brand or '""')]
    p = subprocess.run(cmd, input=VPS_SCRIPT, capture_output=True, text=True)
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        raise SystemExit("Eroare la citirea profitability.db de pe VPS.")
    return json.loads(p.stdout.strip())


# ---------------- AWBprint (rapid, ~99% complet, toate 21 magazinele, fara SSH) -----------------
# aggregated_status (Frisbo) -> categoria de status din profitabilitate
_AWB_CAT = {}
for _s in ("delivered", "customer_pickup", "administrative_closure"): _AWB_CAT[_s] = "Livrata"
for _s in ("back_to_sender", "returning_to_sender", "refused", "unsuccessful_delivery",
           "incorrect_address", "lost", "received_by_sender"): _AWB_CAT[_s] = "Refuzata"
for _s in ("in_transit", "fulfilled", "redirected", "deferred_delivery", "on_hold",
           "out_for_delivery", "waiting_for_courier"): _AWB_CAT[_s] = "In curs de livrare"
for _s in ("not_fulfilled", "new", "ready_for_pickup", "not_created", "created_awb"): _AWB_CAT[_s] = "Netrimisa"
for _s in ("cancelled",): _AWB_CAT[_s] = "Anulata"
_AWB_NORM = {"GRAND": "GRAN", "NUBRA": "NUB"}  # order-prefix -> prefix profitabilitate


def _awb_courier(name):
    n = (name or "").lower()
    for k, key in (("dpd", "dpd-ro"), ("packeta", "packeta"), ("econt", "econt"), ("sameday", "sameday")):
        if k in n:
            return key
    return "unknown"


def fetch_awb(month, brand):
    """Aceeasi forma ca fetch_vps {rows, transport, titles} dar din AWBprint (instant, complet).
    transport = cost REAL mediu/colet per prefix (din transport_cost), nu un cost_per_parcel estimat."""
    url = os.environ.get("DATABASE_URL_AWBPRINT")
    if not url:
        kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
        url = subprocess.run(["uv", "run", kb, "secret-get", "DATABASE_URL_AWBPRINT"],
                             capture_output=True, text=True).stdout.strip()
    u = urllib.parse.urlparse(url)
    conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = conn.cursor()
    lo = month + "-01"
    cur.execute("""
      SELECT substring(o.order_number from '^[A-Za-z]+') pfx, o.order_number,
             coalesce(o.total_price,0), coalesce(o.currency,'RON'), o.aggregated_status,
             o.courier_name, o.shipping_address->>'province', o.transport_cost,
             (SELECT string_agg(lower(it->'inventory_item'->>'sku'), ';')
              FROM jsonb_array_elements(o.line_items::jsonb) it
              WHERE nullif(it->'inventory_item'->>'sku','') IS NOT NULL)
      FROM orders o
      WHERE o.frisbo_created_at >= %s::date AND o.frisbo_created_at < (%s::date + INTERVAL '1 month')
        AND o.order_number ~ '^[A-Za-z]+'
    """, (lo, lo))
    rows, tcost = [], {}
    for pfx, oname, rev, curr, agg, courier, prov, tc, skus in cur.fetchall():
        pfx = _AWB_NORM.get((pfx or "").upper(), (pfx or "").upper())
        if brand and pfx != brand:
            continue
        rows.append({"prefix": pfx, "order_name": oname, "revenue": float(rev or 0),
                     "currency": curr or "RON", "status": _AWB_CAT.get((agg or "").lower(), ""),
                     "courier": _awb_courier(courier), "courier_status": agg or "",
                     "skus": skus or "", "province": (prov or "").strip() or "?"})
        if tc and float(tc) > 0:
            tcost.setdefault(pfx, []).append(float(tc))
    conn.close()
    transport = {p: (sum(v) / len(v)) for p, v in tcost.items()}  # cost real mediu/colet per brand
    return {"rows": rows, "transport": transport, "titles": {}}


# ---------------- aggregation -----------------
def split_skus(s):
    out = []
    for part in (s or "").replace(",", ";").split(";"):
        sk = part.strip()
        if sk:
            out.append(sk)
    return out


class Bucket:
    __slots__ = ("sent", "refused", "rev_risk", "currency")

    def __init__(self):
        self.sent = 0
        self.refused = 0
        self.rev_risk = 0.0
        self.currency = "RON"

    def add(self, row):
        st = row["status"]
        if st in SENT:
            self.sent += 1
        if st == REFUSED:
            self.refused += 1
            self.rev_risk += row["revenue"]
            self.currency = row["currency"] or self.currency

    @property
    def rate(self):
        return (100.0 * self.refused / self.sent) if self.sent else 0.0


def wasted_transport(refused, prefix, transport):
    cpp = transport.get(prefix, 0.0)
    return refused * cpp * 2  # dus + intors


# ---------------- formatting -----------------
def m(n):
    return "{:,.0f}".format(n)


def hdr(t):
    print("\n" + t)
    print("-" * len(t))


def main():
    ap = argparse.ArgumentParser(description="Monitor livrabilitate / refuz ramburs Arona")
    ap.add_argument("--month", required=True, help="ex: 2026-05")
    ap.add_argument("--brand", default=None, help="prefix profitabilitate (EST, GT, NUB, GRAN, ...)")
    ap.add_argument("--by", choices=["brand", "courier", "county", "sku", "pocket"],
                    default="brand")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-sent", type=int, default=30,
                    help="ignora bucketele cu mai putine colete trimise (zgomot)")
    ap.add_argument("--source", choices=["awb", "vps"], default="awb",
                    help="awb = AWBprint (default, instant, ~99%%, toate 21 magazinele, judet inclus); "
                         "vps = profitability.db de pe VPS prin SSH + judet din metrics (incomplet)")
    a = ap.parse_args()
    brand = a.brand.upper() if a.brand else None

    data = fetch_awb(a.month, brand) if a.source == "awb" else fetch_vps(a.month, brand)
    rows = data["rows"]
    transport = data["transport"]
    titles = data["titles"]
    if not rows:
        print("Nicio comanda pentru %s%s." % (a.month, (" / " + brand) if brand else ""))
        return

    prefixes = {r["prefix"] for r in rows}

    # --------- judet (awb: direct din shipping_address; vps: join in metrics) ----------
    prov = {}
    if a.by in ("county", "pocket"):
        if a.source == "awb":
            prov = {r["order_name"]: r.get("province", "?") for r in rows}
        else:
            prov = fetch_provinces(a.month, prefixes)

    # totaluri generale
    tot = Bucket()
    for r in rows:
        tot.add(r)
    tot_wasted = 0.0
    ref_by_prefix = {}
    for r in rows:
        if r["status"] == REFUSED:
            ref_by_prefix[r["prefix"]] = ref_by_prefix.get(r["prefix"], 0) + 1
    for pfx, cnt in ref_by_prefix.items():
        tot_wasted += wasted_transport(cnt, pfx, transport)

    print("=" * 64)
    print("LIVRABILITATE Arona — %s%s" % (a.month, (" / brand " + brand) if brand else ""))
    print("=" * 64)
    print("  Colete trimise (Livrata+Refuzata+In curs): %s" % m(tot.sent))
    print("  Colete REFUZATE:                           %s" % m(tot.refused))
    print("  Rata refuz:                                %.1f%%" % tot.rate)
    print("  Venit la risc (refuzat, valuta mixta):     ~%s" % m(tot.rev_risk))
    print("  Transport IROSIT (refuz x cost x2):        ~%s RON" % m(tot_wasted))

    # ---------------- BY BRAND ----------------
    if a.by == "brand":
        b = {}
        for r in rows:
            b.setdefault(r["prefix"], Bucket()).add(r)
        hdr("Pe BRAND (sortat dupa transport irosit)")
        print("%-7s%8s%9s%7s%14s%13s" %
              ("brand", "trimis", "refuzat", "rata", "venit_risc", "transp_irosit"))
        ranked = []
        for pfx, bk in b.items():
            w = wasted_transport(bk.refused, pfx, transport)
            ranked.append((pfx, bk, w))
        for pfx, bk, w in sorted(ranked, key=lambda x: -x[2]):
            if bk.sent < a.min_sent:
                continue
            print("%-7s%8s%9s%6.1f%%%14s%13s" %
                  (pfx, m(bk.sent), m(bk.refused), bk.rate, m(bk.rev_risk), m(w)))

    # ---------------- BY COURIER ----------------
    elif a.by == "courier":
        b = {}
        for r in rows:
            key = (r["prefix"], r["courier"])
            b.setdefault(key, Bucket()).add(r)
        hdr("Pe CURIER x BRAND (sortat dupa transport irosit)")
        print("%-9s%-7s%8s%9s%7s%13s" %
              ("curier", "brand", "trimis", "refuzat", "rata", "transp_irosit"))
        ranked = []
        for (pfx, cour), bk in b.items():
            w = wasted_transport(bk.refused, pfx, transport)
            ranked.append((pfx, cour, bk, w))
        for pfx, cour, bk, w in sorted(ranked, key=lambda x: -x[3])[:a.limit]:
            if bk.sent < a.min_sent:
                continue
            print("%-9s%-7s%8s%9s%6.1f%%%13s" %
                  (COURIER_LABEL.get(cour, cour), pfx, m(bk.sent),
                   m(bk.refused), bk.rate, m(w)))
        # rezumat curier total
        cb = {}
        for r in rows:
            cb.setdefault(r["courier"], Bucket()).add(r)
        hdr("Rezumat pe CURIER (toate brandurile)")
        print("%-12s%8s%9s%7s" % ("curier", "trimis", "refuzat", "rata"))
        for cour, bk in sorted(cb.items(), key=lambda x: -x[1].refused):
            if bk.sent < a.min_sent:
                continue
            print("%-12s%8s%9s%6.1f%%" %
                  (COURIER_LABEL.get(cour, cour), m(bk.sent), m(bk.refused), bk.rate))

    # ---------------- BY COUNTY ----------------
    elif a.by == "county":
        if not prov:
            print("\nNu am date de judet (metrics indisponibil sau branduri straine).")
            return
        b = {}
        matched = 0
        for r in rows:
            county = prov.get(r["order_name"])
            if county is None:
                continue
            matched += 1
            b.setdefault(county, Bucket()).add(r)
        hdr("Pe JUDET (sortat dupa rata refuz; potrivite %d/%d comenzi)" %
            (matched, len(rows)))
        print("%-18s%8s%9s%7s%14s" %
              ("judet", "trimis", "refuzat", "rata", "venit_risc"))
        ranked = [(c, bk) for c, bk in b.items() if bk.sent >= a.min_sent]
        for county, bk in sorted(ranked, key=lambda x: -x[1].rate)[:a.limit]:
            print("%-18s%8s%9s%6.1f%%%14s" %
                  (county[:18], m(bk.sent), m(bk.refused), bk.rate, m(bk.rev_risk)))

    # ---------------- BY SKU ----------------
    elif a.by == "sku":
        b = {}
        for r in rows:
            sks = split_skus(r["skus"])
            for sk in sks:
                bk = b.setdefault(sk, Bucket())
                bk.add(r)
        hdr("Pe SKU (sortat dupa nr refuzuri)")
        print("%-16s%8s%9s%7s  %s" %
              ("sku", "trimis", "refuzat", "rata", "titlu"))
        ranked = [(sk, bk) for sk, bk in b.items() if bk.refused > 0 and bk.sent >= max(5, a.min_sent // 3)]
        for sk, bk in sorted(ranked, key=lambda x: -x[1].refused)[:a.limit]:
            t = (titles.get(sk) or "")[:38]
            print("%-16s%8s%9s%6.1f%%  %s" %
                  (sk[:16], m(bk.sent), m(bk.refused), bk.rate, t))

    # ---------------- POCKETS: county x courier x brand ----------------
    elif a.by == "pocket":
        if not prov:
            print("\nNu am date de judet (metrics indisponibil).")
            return
        b = {}
        for r in rows:
            county = prov.get(r["order_name"])
            if county is None or county == "?":
                continue
            key = (county, r["courier"], r["prefix"])
            b.setdefault(key, Bucket()).add(r)
        hdr("BUZUNARE judet x curier x brand (cele mai scumpe; transport irosit)")
        print("%-16s%-9s%-7s%7s%8s%7s%12s" %
              ("judet", "curier", "brand", "trimis", "refuzat", "rata", "transp_iros"))
        ranked = []
        for (county, cour, pfx), bk in b.items():
            if bk.sent < a.min_sent:
                continue
            w = wasted_transport(bk.refused, pfx, transport)
            ranked.append((county, cour, pfx, bk, w))
        for county, cour, pfx, bk, w in sorted(ranked, key=lambda x: -x[4])[:a.limit]:
            print("%-16s%-9s%-7s%7s%8s%6.1f%%%12s" %
                  (county[:16], COURIER_LABEL.get(cour, cour)[:9], pfx,
                   m(bk.sent), m(bk.refused), bk.rate, m(w)))

    print("")


if __name__ == "__main__":
    main()
