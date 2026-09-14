#!/usr/bin/env python3
"""trendyol_invoicing.py — facturează automat comenzile Trendyol livrate (SmartBill) + push în Trendyol.

Flux/comandă: comenzi Delivered NEanfacturate (invoiceStatus=NotInvoiced) → factură SmartBill (CIF ARONA,
serie ARONA, TVA pe ȚARA comenzii: RO 21% / BG 20%, prețuri TVA-incluse, B2B dacă are CUI) → PDF → găzduit
public pe scripts.arona.ro/invoices/<token>.pdf → push Trendyol POST seller-invoice-links → guard în SQLite.

  preview --order N            # dry-run: arată factura SmartBill + payload Trendyol, NIMIC emis
  issue   --order N --apply    # emite REAL 1 comandă (SmartBill + host + push)
  run     --apply [--limit N]  # toate Delivered neanfacturate (ultimele ~30 zile), cu guard
  status                       # câte livrate / neanfacturate
"""
import os, sys, json, time, base64, sqlite3, secrets, datetime, argparse, urllib.request, urllib.error
sys.path.insert(0, "/root/Scripturi")
import core.config  # load_dotenv
from core.trendyol_client import TrendyolClient
import requests

SMARTBILL_BASE = "https://ws.smartbill.ro/SBORO/api"
SB_EMAIL = os.environ.get("SMARTBILL_EMAIL"); SB_TOKEN = os.environ.get("SMARTBILL_TOKEN")
SB_CIF = os.environ.get("SMARTBILL_CIF", "RO37247302")
SERIES = os.environ.get("TRENDYOL_INVOICE_SERIES", "ARONA")
PUBLIC_BASE = os.environ.get("TRENDYOL_INVOICE_PUBLIC_BASE", "https://scripts.arona.ro/invoices")
PDF_DIR = "/var/www/invoices"
DB = "/root/Scripturi/data/trendyol_orders.db"
TAX_BY_COUNTRY = {"RO": ("Normala", 21), "BG": ("Bulgaria", 20), "SK": ("Slovacia", 23), "HU": ("Ungaria", 27), "PL": ("Polonia", 23)}

def _sb_auth():
    return "Basic " + base64.b64encode(f"{SB_EMAIL}:{SB_TOKEN}".encode()).decode()

def _guard_db():
    os.makedirs(PDF_DIR, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS trendyol_invoices(
        order_number TEXT PRIMARY KEY, package_id TEXT, series TEXT, number TEXT,
        invoice_url TEXT, country TEXT, vat INTEGER, total REAL, pushed INTEGER DEFAULT 0,
        created_at TEXT)""")
    c.commit(); return c

def already_invoiced(order_number):
    c = _guard_db()
    row = c.execute("SELECT number,pushed FROM trendyol_invoices WHERE order_number=?", (order_number,)).fetchone()
    c.close(); return row

def fetch_order(tc, order_number):
    url = f"{tc.BASE_URL}/integration/order/sellers/{tc.seller_id}/orders"
    hdr = tc._order_headers() if hasattr(tc, "_order_headers") else tc._headers()
    r = requests.get(url, headers=hdr, params={"orderNumber": order_number}, timeout=40)
    r.raise_for_status()
    cs = r.json().get("content", [])
    return cs[0] if cs else None

def fetch_delivered_uninvoiced(tc, days=30, limit=None):
    url = f"{tc.BASE_URL}/integration/order/sellers/{tc.seller_id}/orders"
    hdr = tc._order_headers() if hasattr(tc, "_order_headers") else tc._headers()
    end = int(time.time()*1000); out = {}
    for w in range((days//14)+1):
        e = end - w*14*24*3600*1000; s = e - 14*24*3600*1000
        page = 0
        while page < 20:
            r = requests.get(url, headers=hdr, params={"status": "Delivered", "startDate": s, "endDate": e,
                             "page": page, "size": 200, "orderByField": "PackageLastModifiedDate", "orderByDirection": "DESC"}, timeout=40)
            if r.status_code != 200: break
            d = r.json()
            for o in d.get("content", []):
                if (o.get("invoiceStatus") == "NotInvoiced") and not o.get("invoiceNumber"):
                    out[o.get("orderNumber")] = o
            if page >= d.get("totalPages", 1)-1: break
            page += 1
    orders = list(out.values())
    return orders[:limit] if limit else orders

def build_sb_invoice(o):
    country = (o.get("orderCountryCode") or "RO").upper()
    taxname, vat = TAX_BY_COUNTRY.get(country, ("Normala", 21))
    ia = o.get("invoiceAddress") or {}
    is_b2b = bool(o.get("taxNumber") or ia.get("company"))
    name = ia.get("company") or f"{ia.get('firstName','')} {ia.get('lastName','')}".strip() \
           or f"{o.get('customerFirstName','')} {o.get('customerLastName','')}".strip()
    addr = " ".join(filter(None, [ia.get("address1"), ia.get("address2")])) or "-"
    body = {
        "companyVatCode": SB_CIF, "seriesName": SERIES, "isDraft": False,
        "currency": o.get("currencyCode", "RON"), "language": "RO", "precision": 2,
        "issueDate": datetime.date.today().isoformat(),
        "client": {"name": name, "vatCode": (o.get("taxNumber") or ""), "isTaxPayer": is_b2b,
                   "address": addr, "city": ia.get("city", ""), "county": ia.get("countyName", ""),
                   "country": "Romania" if country == "RO" else country, "email": o.get("customerEmail", ""),
                   "saveToDb": False},
        "products": [{"name": (ln.get("productName") or ln.get("sku") or "Produs")[:200],
                      "code": ln.get("merchantSku") or ln.get("barcode") or "",
                      "measuringUnitName": "buc", "currency": ln.get("currencyCode", "RON"),
                      "quantity": ln.get("quantity", 1), "price": ln.get("lineUnitPrice", ln.get("price")),
                      "isTaxIncluded": True, "taxName": taxname, "taxPercentage": vat,
                      "saveToDb": False, "isService": False} for ln in o.get("lines", [])],
    }
    return body, country, vat

def sb_create(body):
    req = urllib.request.Request(SMARTBILL_BASE + "/invoice", data=json.dumps(body).encode(),
        headers={"authorization": _sb_auth(), "content-type": "application/json", "accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"SmartBill {e.code}: {e.read().decode()[:200]}")

def sb_pdf(series, number):
    u = f"{SMARTBILL_BASE}/invoice/pdf?cif={SB_CIF}&seriesname={series}&number={number}"
    req = urllib.request.Request(u, headers={"authorization": _sb_auth(), "accept": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()

def host_pdf(pdf_bytes, order_number):
    os.makedirs(PDF_DIR, exist_ok=True)
    token = secrets.token_urlsafe(24)
    fn = f"{order_number}-{token}.pdf"
    with open(os.path.join(PDF_DIR, fn), "wb") as f:
        f.write(pdf_bytes)
    return f"{PUBLIC_BASE}/{fn}"

def trendyol_push(tc, package_id, invoice_url, invoice_number, invoice_dt_ms):
    url = f"{tc.BASE_URL}/integration/sellers/{tc.seller_id}/seller-invoice-links"
    hdr = tc._headers(); hdr["Content-Type"] = "application/json"
    body = {"invoiceLink": invoice_url, "shipmentPackageId": int(package_id),
            "invoiceDateTime": int(invoice_dt_ms), "invoiceNumber": invoice_number}
    r = requests.post(url, headers=hdr, json=body, timeout=40)
    return r.status_code, (r.text[:300] if r.text else "")

def issue_one(tc, o, apply):
    onum = o.get("orderNumber"); pkg = o.get("id")
    prev = already_invoiced(onum)
    if prev and prev[1]:
        print(f"  ⏭  {onum} deja facturat+push ({prev[0]}) — sar"); return
    body, country, vat = build_sb_invoice(o)
    total = sum((p["price"] or 0)*p["quantity"] for p in body["products"])
    print(f"  📄 {onum} pkg={pkg} {country} TVA{vat}% total={round(total,2)} {body['currency']} "
          f"{'B2B' if body['client']['isTaxPayer'] else 'B2C'} → {body['client']['name']}")
    if not apply:
        print("     [dry-run] SmartBill body:", json.dumps(body, ensure_ascii=False)[:400], "…")
        return
    res = sb_create(body)
    series = res.get("series") or SERIES; number = res.get("number")
    if not number:
        print("     ✗ SmartBill n-a întors număr:", res); return
    inv_full = f"{series}{number}"
    pdf = sb_pdf(series, number)
    url = host_pdf(pdf, onum)
    dt_ms = o.get("lastModifiedDate") or int(time.time()*1000)
    st, txt = trendyol_push(tc, pkg, url, inv_full, dt_ms)
    c = _guard_db()
    c.execute("""INSERT OR REPLACE INTO trendyol_invoices
        (order_number,package_id,series,number,invoice_url,country,vat,total,pushed,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (onum, str(pkg), series, str(number), url, country, vat, round(total,2),
         1 if st in (200,201) else 0, datetime.datetime.now().isoformat()))
    c.commit(); c.close()
    print(f"     ✅ SmartBill {inv_full} · PDF {url}")
    print(f"     {'✅ push Trendyol OK '+str(st) if st in (200,201) else '✗ push Trendyol '+str(st)+': '+txt}")

def main():
    if not (SB_EMAIL and SB_TOKEN):
        raise SystemExit("lipsesc SMARTBILL_EMAIL/TOKEN din env (.env)")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("preview", "issue"):
        sp = sub.add_parser(name); sp.add_argument("--order", required=True); sp.add_argument("--apply", action="store_true")
    rr = sub.add_parser("run"); rr.add_argument("--apply", action="store_true"); rr.add_argument("--limit", type=int); rr.add_argument("--days", type=int, default=30)
    sub.add_parser("status")
    a = p.parse_args()
    tc = TrendyolClient()
    if a.cmd in ("preview", "issue"):
        o = fetch_order(tc, a.order)
        if not o: raise SystemExit("comandă negăsită")
        issue_one(tc, o, apply=(a.cmd == "issue" and a.apply))
    elif a.cmd == "run":
        orders = fetch_delivered_uninvoiced(tc, days=a.days, limit=a.limit)
        print(f"Delivered neanfacturate: {len(orders)}")
        for o in orders:
            try: issue_one(tc, o, apply=a.apply)
            except Exception as ex: print(f"  ✗ {o.get('orderNumber')}: {ex}")
    elif a.cmd == "status":
        orders = fetch_delivered_uninvoiced(tc, days=30)
        print(f"Delivered NEanfacturate (30 zile): {len(orders)}")


if __name__ == "__main__":
    main()
