# /// script
# dependencies = ["playwright"]
# ///
"""Facturarea lunară a spend-ului Meta per cont: THEWGRID LLC → ARONA SRL, în Zoho Invoice, ca DRAFT.

  uv run zoho_facturare.py plan   spend.csv 2026-08            # împarte conturile în facturi ≤ 31k, scrie plan.json
  uv run zoho_facturare.py create plan.json                     # creează drafturile în Zoho (prima prin UI, restul prin API)
  uv run zoho_facturare.py verify spend.csv 2026-08             # recitește drafturile lunii din Zoho și reconciliază per cont

spend.csv = coloane `cont,ad_account_id,suma_usd` (conturile RON deja convertite în USD, din sheet).
Pre-condiție pentru create/verify: Chrome SEPARAT pe --remote-debugging-port=9223 logat în invoice.zoho.eu
(org THEWGRID LLC). NU trimite niciodată facturi — Send dă DOAR Anne.
"""
import sys, json, csv, time, re, pathlib, datetime, calendar, argparse
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ORG = "20099310317"; CUSTOMER = "602270000000050151"; CUR = "602270000000000059"; TEMPLATE = "602270000000000103"
MAX = 31000
NOTES = "Thanks for your business."
TERMS = ("The payment has been processed directly by the client using their card for the Facebook Ads. This invoice reflects the management and administration services provided for the Facebook Ads campaigns.\n"
         "Facebook Ads were executed using the client's card, with our company's details used for managing the Business Manager account.\n"
         "If there are any discrepancies or questions regarding this invoice, please contact us within 5 business days of receiving it.\n"
         "Any notifications regarding this invoice should be sent via email to hello@thewowgrid.com")


def dates_for(month):
    y, m = int(month[:4]), int(month[5:])
    d = datetime.date(y, m, 1)
    def add_months(dt, k, day):
        mm = dt.month - 1 + k; return datetime.date(dt.year + mm // 12, mm % 12 + 1, day)
    nxt = add_months(d, 1, 1)   # invoice date = 1 a lunii următoare (aug → 01 Sep)
    due = add_months(d, 3, 2)   # due = 2 a lunii peste trei față de luna facturată (aug → 02 Nov; iul → 02 Oct)
    period = f"1-{calendar.monthrange(y, m)[1]} {d.strftime('%b')}"
    return nxt.isoformat(), due.isoformat(), period


def read_spend(path):
    rows = []
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        rows.append((r["cont"].strip(), r["ad_account_id"].strip(), int(round(float(r["suma_usd"])))))
    return rows


def make_plan(rows):
    """Bin-pack în facturi ≤ MAX, spărgând doar conturile mari; alege chunk-ul cu cele mai puține facturi."""
    best = None
    for chunk in range(24000, MAX + 1, 250):
        pieces = []
        for n, i, a in rows:
            rem = a
            while rem > chunk: pieces.append((n, i, chunk)); rem -= chunk
            pieces.append((n, i, rem))
        pieces.sort(key=lambda x: -x[2]); bins = []
        for pc in pieces:
            cands = [b for b in bins if sum(x[2] for x in b) + pc[2] <= MAX]
            if cands: max(cands, key=lambda b: sum(x[2] for x in b)).append(pc)
            else: bins.append([pc])
        score = (len(bins), -min(sum(x[2] for x in b) for b in bins))
        if best is None or score < best[0]: best = (score, bins)
    bins = best[1]; bins.sort(key=lambda b: -sum(x[2] for x in b))
    return bins


def connect(port=9223):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try: b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    except Exception: sys.exit(f"Nu mă pot conecta la Chrome pe {port} — pornește-l separat cu --remote-debugging-port={port} și loghează-te în invoice.zoho.eu")
    c = b.contexts[0]
    pages = [p for p in c.pages if "zoho" in p.url]
    return pw, c, (pages[0] if pages else c.new_page())


def api_get(c, H, path):
    return c.request.get(f"https://invoice.zoho.eu/api/v3{path}{'&' if '?' in path else '?'}organization_id={ORG}", headers=H).json()


def api_write(c, H, path, body, put=False):
    fn = c.request.put if put else c.request.post
    r = fn(f"https://invoice.zoho.eu/api/v3{path}?organization_id={ORG}", headers=H, multipart={"JSONString": json.dumps(body), "is_simple_view": "false"})
    j = r.json() if "json" in r.headers.get("content-type", "") else {"code": r.status, "message": r.text()[:200]}
    return r.status, j


def show(inv):
    print(f"  {inv['invoice_number']} {inv['status']} date={inv['date']} due={inv['due_date']} terms={inv['payment_terms_label']} total=${inv['total']:,.2f}")
    for li in inv["line_items"]: print("     ", li["description"].replace("\n", " | "), li["rate"])


def ui_save_first(p, inv, date_s, due_s, period):
    """Salvează prima factură prin formular și capturează header-ul de scriere (zbcsparam)."""
    cap = {}
    def onreq(r):
        if r.method == "POST" and r.url.startswith("https://invoice.zoho.eu/api/v3/invoices"):
            cap["z"] = r.headers.get("x-zcsrf-token"); cap["role"] = r.headers.get("x-role-id", "")
    p.on("request", onreq)
    p.goto(f"https://invoice.zoho.eu/app/{ORG}#/invoices/new", wait_until="domcontentloaded", timeout=90000); time.sleep(8)
    p.get_by_text("Select or add a customer").first.click(); time.sleep(1); p.keyboard.type("ARONA"); time.sleep(2)
    p.get_by_text("ARONA SRL", exact=False).first.click(); time.sleep(3)
    di = p.locator("input[placeholder='dd MMM yyyy']").nth(0); di.click(); di.press("Control+A"); di.type(datetime.date.fromisoformat(date_s).strftime("%d %b %Y")); di.press("Tab"); time.sleep(1)
    for idx, (n, i, a) in enumerate(inv):
        if idx: p.get_by_text("Add New Row").first.click(); time.sleep(1)
        d = p.locator("textarea[placeholder='Type or click to select an item.']").nth(idx); d.click(); d.fill(f"{n} ({i})\n{period}"); p.keyboard.press("Escape"); time.sleep(0.4)
        r = p.locator("input[aria-label='Rate']").nth(idx); r.click(); r.fill(str(a)); r.press("Tab"); time.sleep(0.4)
    p.get_by_role("button", name="Save as Draft").first.click(); time.sleep(8)
    if not cap.get("z"): sys.exit("nu am capturat token-ul de scriere — verifică formularul în Chrome")
    return cap


def cmd_plan(a):
    rows = read_spend(a.spend); bins = make_plan(rows)
    date_s, due_s, period = dates_for(a.month)
    plan = {"month": a.month, "date": date_s, "due_date": due_s, "period": period, "invoices": bins}
    json.dump(plan, open(a.out, "w"), indent=1)
    for k, b in enumerate(bins, 1):
        print(f"\nFactura {k}: ${sum(x[2] for x in b):,}")
        for n, i, x in b: print(f"   {n} ({i}), {period}  {x:,}")
    print(f"\nTOTAL ${sum(r[2] for r in rows):,} | {len(bins)} facturi | date={date_s} due={due_s} | plan → {a.out}")


def cmd_create(a):
    plan = json.load(open(a.plan)); bins = plan["invoices"]; date_s, due_s, period = plan["date"], plan["due_date"], plan["period"]
    done_path = pathlib.Path(a.plan).with_suffix(".done.json"); done = json.load(open(done_path)) if done_path.exists() else {}
    pw, c, p = connect(a.port)
    if not done:
        cap = ui_save_first(p, bins[0], date_s, due_s, period)
        H = {"x-zcsrf-token": cap["z"], "x-zb-source": "zbclient", "x-role-id": cap["role"], "Accept": "application/json"}
        latest = api_get(c, H, "/invoices?per_page=1&sort_column=created_time&sort_order=D")["invoices"][0]
        s, r = api_write(c, H, f"/invoices/{latest['invoice_id']}", {"date": date_s, "due_date": due_s, "payment_terms": 62, "payment_terms_label": "Custom"}, put=True)
        z = r["invoice"]; print("#1 OK (UI)"); show(z)
        done["1"] = {"invoice_number": z["invoice_number"], "invoice_id": z["invoice_id"], "total": z["total"]}; done["_H"] = H
        json.dump(done, open(done_path, "w"), indent=1)
    H = done["_H"]
    for k, inv in enumerate(bins, 1):
        if str(k) in done: continue
        body = {"customer_id": CUSTOMER, "currency_id": CUR, "date": date_s, "due_date": due_s, "payment_terms": 62, "payment_terms_label": "Custom", "template_id": TEMPLATE,
                "notes": NOTES, "terms": TERMS, "is_inclusive_tax": False,
                "line_items": [{"description": f"{n} ({i})\n{period}", "rate": x, "quantity": 1, "item_order": o + 1} for o, (n, i, x) in enumerate(inv)]}
        s, r = api_write(c, H, "/invoices", body)
        if r.get("code") != 0: print(f"#{k} EROARE {s} {r.get('message')}"); break
        z = r["invoice"]; print(f"#{k} OK"); show(z)
        done[str(k)] = {"invoice_number": z["invoice_number"], "invoice_id": z["invoice_id"], "total": z["total"]}
        json.dump(done, open(done_path, "w"), indent=1); time.sleep(1)
    pw.stop()
    print("\nGATA — toate DRAFT. Send dă doar Anne.")


def cmd_verify(a):
    rows = read_spend(a.spend); sheet = {n: x for n, i, x in rows}; date_s, due_s, period = dates_for(a.month)
    pw, c, p = connect(a.port)
    ck = {x["name"]: x["value"] for x in c.cookies("https://invoice.zoho.eu")}
    H = {"X-ZCSRF-TOKEN": f"csrfp={ck.get('CSRF_TOKEN','')}", "Accept": "application/json"}
    lst = api_get(c, H, "/invoices?per_page=50&sort_column=created_time&sort_order=D")["invoices"]
    per = defaultdict(float); tot = 0; nums = []
    for i in [x for x in lst if x["date"] == date_s]:
        d = api_get(c, H, f"/invoices/{i['invoice_id']}")["invoice"]; nums.append(d["invoice_number"]); tot += d["total"]
        for li in d["line_items"]: per[re.sub(r" \(\d+\)\n.*", "", li["description"])] += li["rate"]
    pw.stop()
    print("facturi:", sorted(nums), f"| total Zoho ${tot:,.0f} | total sheet ${sum(sheet.values()):,}")
    bad = {k: (per.get(k, 0), v) for k, v in sheet.items() if abs(per.get(k, 0) - v) > 0.01}
    print("diferențe per cont:", bad or "niciuna", "| conturi în plus:", (set(per) - set(sheet)) or "niciunul")


ap = argparse.ArgumentParser(); sp = ap.add_subparsers(dest="cmd", required=True)
s1 = sp.add_parser("plan"); s1.add_argument("spend"); s1.add_argument("month"); s1.add_argument("--out", default="plan.json")
s2 = sp.add_parser("create"); s2.add_argument("plan"); s2.add_argument("--port", type=int, default=9223)
s3 = sp.add_parser("verify"); s3.add_argument("spend"); s3.add_argument("month"); s3.add_argument("--port", type=int, default=9223)
a = ap.parse_args(); {"plan": cmd_plan, "create": cmd_create, "verify": cmd_verify}[a.cmd](a)
