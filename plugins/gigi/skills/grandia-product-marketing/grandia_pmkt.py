# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
grandia_pmkt.py — Atribuire marketing PER PRODUS pentru Grandia, cu discernământ
DIRECT (campanie pe produs) vs CATEGORIE (campanie pe categorie, împărțită pe
produsele tipului) vs UNTRACKED (catalog întreg / negrupabil).

NU scrie nimic în baze. Citește live din Postgres-ul Grandia + clasifică campaniile
FB după nume și răspunde la întrebări. Sursa FB = fbads_raw_spend_rows (STANDARD,
dedup MAX(spend) per reportDate+fbAdId = spend real per campanie). Google = gads_daily_product_spend.

Folosire:
  uv run grandia_pmkt.py summary  --month 2026-05
  uv run grandia_pmkt.py pnl      --month 2026-05 [--losers|--winners] [--limit 15]
  uv run grandia_pmkt.py product  "oglinda baie led" --month 2026-05
  uv run grandia_pmkt.py category "Oglinzi LED" --month 2026-05
"""
import sys, os, re, subprocess, argparse, urllib.parse
import pg8000.dbapi

VAT = 1.21


def get_conn():
    url = os.environ.get("DATABASE_URL_GRANDIA")
    if not url:
        kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
        url = subprocess.run(["uv", "run", kb, "secret-get", "DATABASE_URL_GRANDIA"], capture_output=True, text=True).stdout.strip()
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


# ---------- classification (campaign name -> direct product / category / untracked) ----------
STOP = set("de la cu pt pentru si si a al ale un o din new catalog grandia copy test bid".split())
PRODUCT_OV = [("set greutati", "15583662997848"), ("oglinda machiaj", "15366038290776"),
              ("oglinda machiaj", "15366038290776"), ("bananier", "15499986600280"),
              ("delmar", "15572403388760"), ("scaune bar", "15582213669208"), ("scaune de bar", "15582213669208")]
CAT_RULES = [
    (lambda k: "oglind" in k and ("led" in k or "baie" in k), "Oglinzi LED"),
    (lambda k: k.strip() in ("baterie de bucatarie", "baterie bucatarie", "baterii bucatarie"), "Baterii bucatarie"),
    (lambda k: "rafturi metalice" in k and "biblio" not in k, "Rafturi metalice"),
    (lambda k: "covoare baie" in k or ("covor" in k and "baie" in k), "Set Covorase Baie"),
    (lambda k: "covoare mari" in k or ("covor" in k and "mar" in k), "Covor"),
    (lambda k: "magazii" in k, "Magazii de gradina"),
    (lambda k: "lustre led" in k, "Lustre"),
    (lambda k: "iluminat interior" in k or ("corpuri" in k and "iluminat" in k), "GRUPA"),
    (lambda k: "rafturi" in k and "biblio" in k, "GRUPA"),
    (lambda k: k.strip() in ("mobilier", "mobila"), "GRUPA"),
]


def deacc(s):
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ş", "s"), ("ț", "t"), ("ţ", "t")):
        s = s.replace(a, b)
    return s


def norm(s):
    s = re.sub(r'[,.()/]', ' ', s)
    s = s.replace("&", " ")
    s = deacc(s)
    return re.sub(r'\s+', ' ', s).strip()


def clean(name):
    s = name.lower()
    s = re.sub(r'^\{[^}]*\}\s*-?\s*', '', s)
    s = re.sub(r'-\s*\d{2}\.\d{2}\.\d{2,4}.*$', '', s)
    s = re.sub(r'#\s*\d+', '', s)
    s = re.sub(r'\b(new|catalog|grandia|alx)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip(' -')


class Catalog:
    def __init__(self, cur):
        cur.execute('SELECT "shopifyNumericId", title, lower(title), "productType", "meta_grupa_principala", "meta_subcategorie", "shopifyGid" FROM "Product" WHERE status=\'ACTIVE\' AND title IS NOT NULL')
        self.prods = [{"nid": str(r[0]), "title": r[1], "lt": r[2], "pt": r[3], "grupa": r[4], "sub": r[5], "gid": r[6]} for r in cur.fetchall()]
        self.pt_lower = {p["pt"].lower(): p["pt"] for p in self.prods if p["pt"]}
        self.grupa = {p["grupa"].lower(): p["grupa"] for p in self.prods if p["grupa"]}
        self.subcat = {}
        for p in self.prods:
            if p["sub"] and p["pt"]:
                self.subcat.setdefault(p["sub"].lower(), set()).add(p["pt"])
        self.by_type = {}
        for p in self.prods:
            if p["pt"]:
                self.by_type.setdefault(p["pt"], []).append(p)

    def cat_by_name(self, kw):
        k = norm(kw); kws = set(w for w in k.split() if len(w) >= 4)
        for d, kind in ((self.pt_lower, "PT"), (self.grupa, "GR"), (self.subcat, "SC")):
            for nm, val in d.items():
                if norm(nm) == k:
                    return (kind, val if kind != "SC" else nm)
        for d, kind in ((self.subcat, "SC"), (self.pt_lower, "PT"), (self.grupa, "GR")):
            for nm, val in d.items():
                nws = set(w for w in norm(nm).split() if len(w) >= 4)
                if kws and nws and (kws <= nws or nws <= kws):
                    return (kind, val if kind != "SC" else nm)
        return None

    def best_product(self, kw):
        words = [deacc(w) for w in re.split(r'[^a-z0-9]+', deacc(kw)) if len(w) >= 4 and w not in STOP]
        best, bestn = None, 0
        for p in self.prods:
            lt = deacc(p["lt"]); n = sum(1 for w in words if w in lt)
            if n > bestn:
                best, bestn = p, n
        return (best, bestn) if best else (None, 0)


def classify(name, cat):
    kw = clean(name)
    if any(t in name.lower() for t in ("all active", "product test", "alx new")):
        return ("UNTRACKED", None, None)
    for sub, nid in PRODUCT_OV:
        if sub in kw:
            return ("PRODUCT", None, nid)
    for pred, target in CAT_RULES:
        if pred(kw):
            return ("UNTRACKED", None, None) if target == "GRUPA" else ("PRODUCT_TYPE", target, None)
    if kw in ("upd", "articole de petrecere", "sale") or len(kw) < 3:
        return ("UNTRACKED", None, None)
    cm = cat.cat_by_name(kw)
    if cm:
        kind, val = cm
        if kind == "PT":
            return ("PRODUCT_TYPE", val, None)
        if kind == "GR":
            return ("UNTRACKED", None, None)
        if kind == "SC":
            pts = sorted(cat.subcat.get(val, []))
            return ("PRODUCT_TYPE", pts[0], None) if len(pts) == 1 else ("UNTRACKED", None, None)
    p, hits = cat.best_product(kw)
    if p and hits >= 1:
        return ("PRODUCT", None, p["nid"])
    return ("UNTRACKED", None, None)


def attribute(cur, cat, month):
    lo, hi = month + "-01", month + "-31"
    # campaign spend in month (dedup per date+ad)
    cur.execute('SELECT "fbCampaignName", ROUND(SUM(s)::numeric) FROM (SELECT "fbCampaignName","reportDate","fbAdId",MAX(spend) s FROM fbads_raw_spend_rows WHERE "sourceType"=\'STANDARD\' AND "reportDate" BETWEEN %s AND %s GROUP BY 1,2,3) x GROUP BY 1', (lo, hi))
    camp_spend = {r[0]: float(r[1] or 0) for r in cur.fetchall()}
    fb_direct, fb_cat = {}, {}
    untracked = 0.0
    for name, sp in camp_spend.items():
        kind, pt, nid = classify(name, cat)
        if kind == "PRODUCT" and nid:
            fb_direct[nid] = fb_direct.get(nid, 0) + sp
        elif kind == "PRODUCT_TYPE" and pt and cat.by_type.get(pt):
            members = cat.by_type[pt]
            share = sp / len(members)
            for p in members:
                fb_cat[p["nid"]] = fb_cat.get(p["nid"], 0) + share
        else:
            untracked += sp
    # google per product
    cur.execute('SELECT "shopifyProductId", ROUND(SUM(spend)::numeric) FROM gads_daily_product_spend WHERE "reportDate" BETWEEN %s AND %s GROUP BY 1', (lo, hi))
    google = {str(r[0]): float(r[1] or 0) for r in cur.fetchall()}
    # FB DPA per-product shape -> used to distribute UNTRACKED (catalog/grup) spend = 100% attribution
    cur.execute('SELECT "shopifyProductId", ROUND(SUM("catalogSpend")::numeric) FROM fbads_daily_product_spend WHERE "reportDate" BETWEEN %s AND %s GROUP BY 1', (lo, hi))
    catkey = {str(r[0]): float(r[1] or 0) for r in cur.fetchall()}
    catkey_total = sum(catkey.values())
    # sales per product (by shopifyGid)
    cur.execute('SELECT oli."productId", SUM(oli.quantity), SUM(oli.price*oli.quantity), SUM(COALESCE(v."costPerItem",0)*oli.quantity) FROM "OrderLineItem" oli LEFT JOIN "Variant" v ON v.id=oli."variantId" WHERE oli."createdAt">=%s AND oli."createdAt"<(%s::date + INTERVAL \'1 month\') GROUP BY 1', (lo, month + "-01"))
    sales = {r[0]: (float(r[1] or 0), float(r[2] or 0), float(r[3] or 0)) for r in cur.fetchall()}
    rev_total = sum(s[1] for s in sales.values()) or 1
    rows = []
    for p in cat.prods:
        g = google.get(p["nid"], 0); fd = fb_direct.get(p["nid"], 0); fc = fb_cat.get(p["nid"], 0)
        u, rev, cogs = sales.get(p["gid"], (0, 0, 0))
        # distribute UNTRACKED by FB DPA shape (catalogSpend), fallback to revenue share
        if catkey_total > 0:
            fu = untracked * catkey.get(p["nid"], 0) / catkey_total
        else:
            fu = untracked * rev / rev_total
        mkt = g + fd + fc + fu
        if mkt < 1 and rev < 1:
            continue
        net = rev / VAT - cogs / VAT - mkt
        rows.append({"title": p["title"], "pt": p["pt"], "units": u, "rev": rev / VAT, "google": g,
                     "fb_direct": fd, "fb_cat": fc, "fb_unt": fu, "mkt": mkt, "net": net,
                     "roas": (rev / VAT / mkt) if mkt else 0})
    return rows, untracked, camp_spend


def fmt(n):
    return "{:,.0f}".format(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["summary", "pnl", "product", "category"])
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--month", required=True)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--losers", action="store_true")
    ap.add_argument("--winners", action="store_true")
    a = ap.parse_args()
    conn = get_conn(); cur = conn.cursor()
    cat = Catalog(cur)
    rows, untracked, camp_spend = attribute(cur, cat, a.month)

    if a.mode == "summary":
        tg = sum(r["google"] for r in rows); td = sum(r["fb_direct"] for r in rows)
        tc = sum(r["fb_cat"] for r in rows); tu = sum(r["fb_unt"] for r in rows)
        print("=== Grandia marketing %s (atribuit 100%% per produs) ===" % a.month)
        print("  Google (per produs):          %12s" % fmt(tg))
        print("  FB DIRECT (pe produs):        %12s" % fmt(td))
        print("  FB CATEGORIE (impartit):      %12s" % fmt(tc))
        print("  FB UNTRACKED -> alocat (DPA): %12s" % fmt(tu))
        print("  ---")
        print("  TOTAL atribuit per produs:    %12s" % fmt(tg + td + tc + tu))
        print("  Campanii FB clasificate:", len(camp_spend))
    elif a.mode == "pnl":
        rev = lambda r: r["net"]
        rs = sorted([r for r in rows if r["mkt"] >= 50], key=rev, reverse=not a.losers)
        title = "PIERZATORI" if a.losers else ("CASTIGATORI" if a.winners else "P&L per produs")
        print("=== Grandia %s — %s (venit/COGS/marketing, fara TVA) ===" % (a.month, title))
        print("%-33s%5s%8s%7s%7s%7s%7s%9s%5s" % ("produs", "buc", "venit", "ggl", "fb_dir", "fb_cat", "fb_unt", "NET", "roas"))
        for r in rs[:a.limit]:
            print("%-33s%5d%8s%7s%7s%7s%7s%9s%5.1f" % (r["title"][:32], r["units"], fmt(r["rev"]), fmt(r["google"]), fmt(r["fb_direct"]), fmt(r["fb_cat"]), fmt(r["fb_unt"]), fmt(r["net"]), r["roas"]))
    elif a.mode == "product":
        q = norm(a.query)
        hits = [r for r in rows if q in norm(r["title"])]
        if not hits:
            print("Niciun produs cu marketing/vanzari pe '%s' in %s." % (a.query, a.month)); return
        for r in sorted(hits, key=lambda r: -r["mkt"])[:8]:
            print("• %s [%s]" % (r["title"], r["pt"]))
            print("   venit %s | buc %d | Google %s | FB direct %s | FB categ %s | FB catalog %s | NET %s | ROAS %.1f" % (
                fmt(r["rev"]), r["units"], fmt(r["google"]), fmt(r["fb_direct"]), fmt(r["fb_cat"]), fmt(r["fb_unt"]), fmt(r["net"]), r["roas"]))
    elif a.mode == "category":
        pt = a.query
        members = [r for r in rows if (r["pt"] or "").lower() == pt.lower()]
        if not members:
            print("Categoria '%s' n-are produse cu activitate in %s." % (pt, a.month)); return
        sp = sum(r["fb_cat"] + r["google"] + r["fb_direct"] for r in members)
        units = sum(r["units"] for r in members); rev = sum(r["rev"] for r in members)
        print("=== Categorie '%s' %s ===" % (pt, a.month))
        print("  produse: %d | venit %s | buc %d | marketing %s | CPA %.1f | ROAS %.1f" % (
            len(members), fmt(rev), units, fmt(sp), (sp / units if units else 0), (rev / sp if sp else 0)))
        for r in sorted(members, key=lambda r: -r["mkt"])[:10]:
            print("   %-40s mkt %s (cat %s) | NET %s" % (r["title"][:40], fmt(r["mkt"]), fmt(r["fb_cat"]), fmt(r["net"])))
    conn.close()


if __name__ == "__main__":
    main()
