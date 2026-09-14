#!/usr/bin/env python3
"""parcel_count_watch.py — verifica NR. DE COLETE de pe ULTIMUL AWB fata de ce calculeaza regula ACUM,
si trimite pe mail DOAR diferentele NOI.

⚠️ Nr. de colete NU se ia din AWBprint: `order_awbs.package_count` a incetat sa mai fie populat
(99% in mai, 59% in iunie, 0% din iulie). Sursa corecta = xConnector: numele/tracking-ul etichetei
SHIPPING_LABEL contine segmentele coletelor ("81344758669-81344758660029" = 2 colete).
Continutul comenzii (SKU+cantitate) vine din AWBprint. Un singur apel bulk per magazin.

Dedup in data/parcel_watch_seen.db. --dry-run = afiseaza. --baseline = marcheaza vazut fara email.
"""
import os, sys, json, math, sqlite3, smtplib, collections, html
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText

sys.path.insert(0, "/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
os.chdir("/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
import address_rules as AR
import xconnector as X

EMAIL_TO = "gheorghe.beschea@overheat.agency"
MAP_PATH = "/root/Scripturi/data/sku_box_map.json"
SEEN_DB = "/root/Scripturi/data/parcel_watch_seen.db"
DAYS = int(os.environ.get("WATCH_DAYS", "5"))
DRY = "--dry-run" in sys.argv
BASELINE = "--baseline" in sys.argv
# doar coletele NEPLECATE se mai pot reface
FIXABIL = ("waiting_for_courier", "not_fulfilled", "fulfilled", "incorrect_address", None)


def load_env():
    for f in ("/root/Scripturi/.env", "/root/Scripturi/.env.xconnector"):
        try:
            for line in open(f):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass


def orders_from_awbprint():
    """order_number -> (domeniu, status, line_items) pt fereastra recenta."""
    import psycopg2
    c = psycopg2.connect(os.environ["DATABASE_URL_AWBPRINT"])
    cur = c.cursor()
    cur.execute("""SELECT o.order_number, s.name, o.aggregated_status, o.line_items::text
                   FROM orders o LEFT JOIN stores s ON s.uid = o.store_uid
                   WHERE o.frisbo_created_at >= now() - interval '%d days'""" % DAYS)
    out = {}
    for name, dom, st, li in cur.fetchall():
        try:
            out[(name or "").upper()] = (dom, st, json.loads(li))
        except Exception:
            pass
    c.close()
    return out


def parcels_from_xconnector():
    """orderName -> nr colete, din segmentele AWB-ului de pe eticheta (bulk, 1 apel/magazin)."""
    dto = date.today().isoformat()
    dfrom = (date.today() - timedelta(days=DAYS + 1)).isoformat()
    res = {}
    for sh in X.load_shops():
        try:
            xc = X.XC(sh["apiKey"])
            for o in xc.orders(dfrom, dto, {}):
                doc = X.awb_doc(o)
                if not doc:
                    continue
                trk = X.doc_tracking(doc)
                if not trk:
                    continue
                res[(o.get("orderName") or "").upper()] = (len([s for s in str(trk).split("-") if s]),
                                                           str(trk), sh["shopDomain"])
        except Exception as e:
            print("  !! %s: %s" % (sh.get("shopDomain"), str(e)[:90]))
    return res


def expected(slug, lis, mp):
    """Oglindeste order_parcel_count: split -> suma pe statie (min 1/statie); altfel suma simpla."""
    is_split = slug in AR.SPLIT_STORE_SLUGS
    per = collections.defaultdict(float)
    total, found = 0.0, False
    for li in lis:
        sku = ((li.get("inventory_item") or {}).get("sku") or "").strip()
        if not sku:
            continue                       # linie virtuala (livrare express etc.) — nu e colet fizic
        qty = float(li.get("quantity") or 1)
        box = mp.get(sku)
        if box is None:
            box = X._default_box(slug)   # 5 buc/colet, dar 0 la parfumuri (paritate cu order_parcel_count)
        total += box * qty
        found = True
        if is_split:
            per[AR.sku_station(sku)] += box * qty
    if is_split and per:
        return sum(max(1, math.ceil(v - 1e-9)) for v in per.values())
    return max(1, math.ceil(total - 1e-9)) if (found and total > 0) else 1


def confirma(shop, token, order_name, awb_colete):
    """Recalculează cu regula REALĂ de creare AWB (include metafield-ul de comandă, pe care copia
    locală nu-l vede). Întoarce (e_diferit, colete_regula). Fără asta raportam AWB-uri corecte:
    la Grandia/Belasil `xconnector.parcel-count` e setat pe comandă și bate densitățile."""
    try:
        n = X.order_parcel_count(shop, token, order_name)
    except Exception:
        return True, None                  # nu putem confirma — raportăm, ca să nu pierdem un caz real
    return (n != awb_colete), n


def main():
    load_env()
    mp = json.load(open(MAP_PATH))
    awb = orders_from_awbprint()
    xcp = parcels_from_xconnector()
    print("comenzi AWBprint: %d | comenzi cu eticheta in xConnector: %d" % (len(awb), len(xcp)))

    con = sqlite3.connect(SEEN_DB)
    con.execute("""create table if not exists seen(
        order_number text, awb text, real_c int, expected_c int, seen_at text,
        primary key(order_number, awb))""")

    # tokenurile Admin, ca să putem confirma fiecare candidat cu regula REALĂ de creare AWB
    tok_by_shop = {}
    try:
        import csv as _csv
        with open("/root/Scripturi/.shopify_stores.csv") as _fh:
            for _r in _csv.DictReader(_fh):
                tok_by_shop[_r["shop"].strip().lower()] = _r["token"].strip()
    except Exception as _e:
        print("  (fara tokenuri Admin: %s)" % str(_e)[:50])

    diffs = []
    for name, (real_c, trk, dom) in xcp.items():
        rec = awb.get(name)
        if not rec:
            continue                        # comanda in afara ferestrei AWBprint
        adom, status, lis = rec
        slug = (dom or "").split(".")[0]
        exp = expected(slug, lis, mp)
        if exp == real_c:
            continue
        # CONFIRMARE cu regula autoritară (vede și metafield-ul de comandă `xconnector.parcel-count`,
        # pe care copia locală nu-l are). Fără pasul ăsta raportam AWB-uri CORECTE ca fiind greșite —
        # la Grandia/Belasil metafield-ul e setat pe comandă și bate densitățile din hartă.
        _tok = tok_by_shop.get((dom or "").lower())
        if _tok:
            _dif, _n = confirma(dom, _tok, name, real_c)
            if not _dif:
                continue
            if _n is not None:
                exp = _n
        if con.execute("select 1 from seen where order_number=? and awb=?", (name, trk)).fetchone():
            continue
        skus = " + ".join("%dx%s" % (int(float(l.get("quantity") or 1)),
                                     ((l.get("inventory_item") or {}).get("sku") or "?"))
                          for l in lis if ((l.get("inventory_item") or {}).get("sku")))
        diffs.append({"order": name, "shop": adom or dom, "status": status or "?", "real": real_c,
                      "exp": exp, "awb": trk, "skus": skus, "fixabil": status in FIXABIL})

    diffs.sort(key=lambda d: (not d["fixabil"], -(d["real"] - d["exp"])))
    fix = [d for d in diffs if d["fixabil"]]
    print("%s | fereastra %d zile | diferente NOI: %d (reparabile: %d)"
          % (datetime.now().isoformat(timespec="seconds"), DAYS, len(diffs), len(fix)))
    for d in diffs[:40]:
        print("  %-14s %-18s AWB %-3s vs regula %-3s  %-20s %s"
              % (d["order"], d["shop"], d["real"], d["exp"], d["status"], d["skus"][:46]))

    if diffs and (BASELINE or not DRY):
        for d in diffs:
            con.execute("insert or replace into seen values(?,?,?,?,?)",
                        (d["order"], d["awb"], d["real"], d["exp"],
                         datetime.now().isoformat(timespec="seconds")))
        con.commit()

    if not diffs:
        print("✅ nicio diferenta — fara email.")
        return
    if BASELINE:
        print("BASELINE — marcate ca vazute, fara email.")
        return
    if DRY:
        print("DRY-RUN — fara email.")
        return

    rows_html = "".join(
        "<tr style='background:%s'><td><b>%s</b></td><td>%s</td><td align=center>%s</td>"
        "<td align=center><b>%s</b></td><td>%s</td><td style='font:12px monospace'>%s</td></tr>"
        % ("#fff4f4" if d["fixabil"] else "#fafafa", html.escape(d["order"]), html.escape(d["shop"] or ""),
           d["real"], d["exp"], html.escape(d["status"]), html.escape(d["skus"][:70]))
        for d in diffs)
    body = """<p>Comenzi unde <b>nr. de colete de pe ultimul AWB</b> difera de ce calculeaza regula acum
    (densitati din <a href="https://scripts.arona.ro/colete">scripts.arona.ro/colete</a>).</p>
    <p><b>%d diferente</b>, din care <b>%d inca reparabile</b> (coletul n-a plecat):<br>
    <code>uv run xconnector.py awb-regen --order X --parcels N --apply</code></p>
    <table cellpadding=6 style="border-collapse:collapse;font:14px system-ui">
    <tr style="background:#eee"><th>Comanda</th><th>Magazin</th><th>AWB are</th><th>Regula zice</th>
    <th>Status</th><th>Continut</th></tr>%s</table>
    <p style="color:#888;font-size:12px">Rosu = inca se poate reface. Fereastra %d zile.
    Sursa colete: eticheta din xConnector (AWBprint nu mai populeaza package_count din iulie).</p>""" % (
        len(diffs), len(fix), rows_html, DAYS)

    user = os.environ.get("SMTP_USER")
    msg = MIMEText(body, "html", "utf-8")
    msg["To"] = EMAIL_TO
    msg["From"] = "ARONA colete <%s>" % user
    msg["Subject"] = "[colete] %d comenzi cu nr. gresit de colete (%d reparabile)" % (len(diffs), len(fix))
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                      int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        s.login(user, os.environ["SMTP_PASS"])
        s.sendmail(user, [EMAIL_TO], msg.as_string())
    print("📧 mail trimis catre %s" % EMAIL_TO)


if __name__ == "__main__":
    main()
