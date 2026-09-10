# /// script
# dependencies = ["playwright"]
# ///
"""Descarcă facturile Shopify (bills: abonament + aplicații) din admin, ca PDF, pentru TOATE
magazinele, pe o lună dată. Lucrează prin Chrome-ul utilizatorului (deja logat cu contul upstream)
conectat prin CDP — facturile NU sunt expuse de Admin API.

  uv run shopify_facturi.py 2026-08 --out "C:/Users/Admin/Desktop/facturi shopify august"
  uv run shopify_facturi.py 2026-08 --stores esteban,gt   # doar câteva prefixe din stores.csv
  uv run shopify_facturi.py 2026-08 --port 9223             # alt port CDP

Pre-condiție: un Chrome pornit cu --remote-debugging-port=<port> în care ești logat în Shopify
(vezi SKILL.md — fereastră SEPARATĂ, nu închide Chrome-ul principal al utilizatorului).
Acoperă cele 3 tipuri de pagini de facturare din Shopify:
  A) /settings/billing                     — magazin cu facturare proprie
  B) /settings/organization-billing        — organizație veche: listă de magazine → pagină per magazin
  C) /settings/organization-billing        — organizație nouă (IndexTable): facturi consolidate per org
"""
import re, sys, csv, time, json, argparse, pathlib, datetime, subprocess, os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

MONTHS = {m: i for i, m in enumerate(["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
FIELDS = ["sursa", "magazin", "factura", "data", "motiv", "suma", "status", "fisier"]
MONEY = re.compile(r"^(?:[A-Za-z]{2,3}\s?)?[$€£]?\s?\d[\d,.]*(?:\s?[A-Z]{3})?$")


def parse_date(s):
    s = (s or "").strip()
    m = re.match(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", s)
    if m:
        return datetime.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
    if s.startswith(("Today", "Yesterday")):
        return datetime.date.today() - datetime.timedelta(days=1 if s.startswith("Yesterday") else 0)
    return None


def safe(s):
    return re.sub(r"[^\w.-]+", "_", s or "").strip("_")[:60]


def clean_total(t):
    return re.sub(r"[^0-9.,A-Za-z]+", "", (t or "").replace("€", "EUR").replace("$", "USD").replace("lei", "RON")) or "0"


def store_handles(only=None):
    """prefix -> myshopify handle, din secretul KB SHOPIFY_STORES_CSV (fără a afișa tokenii)."""
    kb = next(pathlib.Path.home().glob(".claude/plugins/cache/team-intelligence/core/*/scripts/kb.py"), None)
    if not kb:
        sys.exit("kb.py negăsit — rulează onboarding-ul team-intelligence")
    out = subprocess.run(["uv", "run", str(kb), "secret-get", "SHOPIFY_STORES_CSV"], capture_output=True, text=True, encoding="utf-8")
    rows = list(csv.reader(out.stdout.lstrip("\ufeff").splitlines()))
    res = {}
    for r in rows:
        if len(r) >= 2 and r[0] and r[0] != "prefix":
            res[r[0]] = r[1].replace(".myshopify.com", "")
    # magazine care NU sunt în stores.csv dar au facturi Shopify (verificat sep-2026)
    for k, v in {"ARTEVITA": "artevita-2"}.items():
        res.setdefault(k, v)
    if only:
        want = {x.strip().lower() for x in only.split(",")}
        res = {k: v for k, v in res.items() if k.lower() in want or v.lower() in want}
    return res


class Puller:
    def __init__(self, page, ctx, out, month):
        self.p, self.c, self.out, self.month = page, ctx, out, month
        self.first = datetime.date(int(month[:4]), int(month[5:]), 1)
        self.recap_path = out / "recap.csv"
        self.recap = [r for r in csv.DictReader(open(self.recap_path, encoding="utf-8-sig"))] if self.recap_path.exists() else []
        self.done = {r["factura"] for r in self.recap if r.get("factura")}
        cdp = ctx.new_cdp_session(page)
        (out / "_dl").mkdir(exist_ok=True)
        cdp.send("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(out / "_dl"), "eventsEnabled": True})
        self.cdp = cdp
        self.last_download = {}
        cdp.on("Browser.downloadWillBegin", lambda e: self.last_download.update(e))

    def save_recap(self):
        with open(self.recap_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(self.recap)

    def goto(self, url):
        self.p.goto(url, wait_until="networkidle", timeout=90000); time.sleep(3)

    # ---------- listare ----------
    def rows_polaris_table(self):
        """Tipurile A și B: div-table Polaris cu link /invoice/<id>."""
        rows_all = []
        for _ in range(6):
            rows = self.p.eval_on_selector_all(".Polaris-Table-TableRow", """trs => trs.map(tr => {
                const a = tr.querySelector("a[href*='/invoice/']");
                const tds = [...tr.querySelectorAll('.Polaris-Table-TableCell')].map(td => td.innerText.trim());
                return a ? [a.getAttribute('href'), tds] : null; }).filter(Boolean)""")
            rows_all += rows
            dates = [d for d in (next((parse_date(t) for t in tds if parse_date(t)), None) for _, tds in rows) if d]
            if not rows or not dates or min(dates) < self.first:
                break
            nxt = self.p.get_by_role("button", name="Next")
            if nxt.count() == 0 or nxt.first.is_disabled():
                break
            nxt.first.click(); time.sleep(3)
        return rows_all

    def rows_index_table(self, base):
        """Tipul C: IndexTable cu <tr id=<invoice>> și fără link; URL = base/invoice/<id>."""
        rows = self.p.eval_on_selector_all("tr.Polaris-IndexTable__TableRow", """trs => trs.map(tr =>
            [tr.id, [...tr.querySelectorAll('td')].map(td => td.innerText.trim())])""")
        return [(f"{base}/invoice/{rid}", tds) for rid, tds in rows if rid and rid.isdigit()]

    def targets(self, rows):
        res = []
        for href, tds in rows:
            inv = href.rstrip("/").split("/")[-1]
            d = next((parse_date(t) for t in tds if parse_date(t)), None)
            if not d or d.strftime("%Y-%m") != self.month:
                continue
            cells = [t for t in tds if t and not t.startswith("Select") and t.lstrip("#") != inv]
            total = next((t for t in cells if MONEY.match(t)), "")
            reason = next((t for t in cells if not MONEY.match(t) and not parse_date(t) and t not in ("Paid", "Unpaid")), "")
            status = "Paid" if "Paid" in tds else ("Unpaid" if "Unpaid" in tds else "")
            res.append((inv, href, d, reason, total, status))
        return res

    # ---------- export PDF ----------
    def export_pdf(self, href):
        url = href if href.startswith("http") else "https://admin.shopify.com" + href
        self.goto(url); time.sleep(1)
        holder, self.last_download = {}, {}
        def _onresp(r):
            if "BillPdfUrl" in r.url:
                try: holder["url"] = r.json()["data"]["invoice"]["pdfUrl"]
                except Exception as e: holder["err"] = str(e)
        self.p.on("response", _onresp)
        try:
            self.p.get_by_role("button", name="Export bill").first.click(); time.sleep(1.5)
            self.p.get_by_role("radio", name="PDF").first.check(); time.sleep(0.5)
            btns = self.p.get_by_role("button", name="Export bill")
            for i in range(btns.count()):
                btns.nth(i).evaluate("e => e.className")   # touch (face click-ul fiabil pe s-internal-button)
            try:
                with self.p.expect_download(timeout=8000):
                    btns.nth(0).click()
            except Exception:
                pass
            pdf_url = None
            for _ in range(40):
                if holder.get("url"): pdf_url = holder["url"]; break
                if "storage.googleapis.com" in self.p.url: pdf_url = self.p.url; break
                if self.last_download.get("url"): pdf_url = self.last_download["url"]; break
                time.sleep(0.5)
        finally:
            self.p.remove_listener("response", _onresp)
        if not pdf_url:
            raise RuntimeError("no pdf url " + holder.get("err", ""))
        if "pdf_download.pdf" in pdf_url:          # tipul C: Chrome a salvat deja fișierul în _dl
            for _ in range(30):
                fs = sorted((self.out / "_dl").glob("*.pdf"), key=lambda f: f.stat().st_mtime)
                if fs and fs[-1].stat().st_size > 0 and fs[-1].read_bytes()[:4] == b"%PDF":
                    body = fs[-1].read_bytes(); fs[-1].unlink(); return body
                time.sleep(1)
            raise RuntimeError("download nu a apărut în _dl")
        body = self.c.request.get(pdf_url).body()
        if not body.startswith(b"%PDF"):
            raise RuntimeError("not a pdf")
        return body

    def process(self, source, store, rows):
        tg = self.targets(rows)
        print(f"  {store}: {len(rows)} facturi listate, {len(tg)} în {self.month}")
        sdir = self.out / safe(store); sdir.mkdir(exist_ok=True)
        for inv, href, d, reason, total, status in tg:
            if inv in self.done:
                print("   skip (deja)", inv); continue
            fname = f"{d.isoformat()}_{inv}_{clean_total(total)}.pdf"
            try:
                (sdir / fname).write_bytes(self.export_pdf(href)); f = str(sdir / fname); print("   OK", fname)
            except Exception as e:
                f = "EROARE " + type(e).__name__; print("   EROARE", inv, type(e).__name__, str(e)[:100])
            self.recap.append(dict(sursa=source, magazin=store, factura=inv, data=d.isoformat(), motiv=reason, suma=total, status=status, fisier=f))
            self.done.add(inv); self.save_recap()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("month", help="YYYY-MM")
    ap.add_argument("--out", default=None, help="folder de ieșire (implicit Desktop/facturi shopify <YYYY-MM>)")
    ap.add_argument("--stores", default=None, help="prefixe/handle-uri separate prin virgulă (implicit toate)")
    ap.add_argument("--port", type=int, default=9223, help="port CDP al Chrome-ului logat")
    a = ap.parse_args()
    out = pathlib.Path(a.out) if a.out else pathlib.Path.home() / "Desktop" / f"facturi shopify {a.month}"
    out.mkdir(parents=True, exist_ok=True)
    handles = store_handles(a.stores)
    print(f"{len(handles)} magazine din stores.csv; ieșire: {out}")

    pw = sync_playwright().start()
    try:
        b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{a.port}")
    except Exception:
        sys.exit(f"Nu mă pot conecta la Chrome pe portul {a.port}. Pornește Chrome cu --remote-debugging-port={a.port} (vezi SKILL.md).")
    c = b.contexts[0]; p = c.pages[0] if c.pages else c.new_page()
    pl = Puller(p, c, out, a.month)

    org_stores = {}   # id -> (nume, url)  (tipul B)
    org_index = {}    # url org -> label     (tipul C)
    for prefix, handle in handles.items():
        print(f"\n=== {prefix} ({handle})")
        try:
            pl.goto(f"https://admin.shopify.com/store/{handle}/settings/billing")
        except Exception as e:
            print("  EROARE goto", type(e).__name__); continue
        url = p.url
        if "/settings/organization-billing" in url:
            links = p.eval_on_selector_all("a[href*='/settings/organization-billing/store/']", "els => els.map(e => [e.getAttribute('href'), e.innerText.trim()])")
            if links:
                new = 0
                for href, name in links:
                    sid = href.rstrip("/").split("/")[-1]
                    if name and sid not in org_stores:
                        org_stores[sid] = (name, "https://admin.shopify.com" + href); new += 1
                print(f"  organizație (tip B): {len(links)} magazine, {new} noi")
            else:
                base = url.split("?")[0].rstrip("/")
                rows = pl.rows_index_table(base)
                if rows:
                    key = tuple(sorted(r[0] for r in rows))
                    if key in org_index:
                        print("  organizație (tip C) deja procesată"); continue
                    org_index[key] = prefix
                    try: label = [t for t in p.locator("h2").all_inner_texts() if t.strip()][0].strip()
                    except Exception: label = prefix
                    pl.process(f"org:{prefix}", f"{label} (org {prefix})", rows)
                else:
                    print("  organizație fără facturi vizibile")
        elif "/settings/billing" in url:
            pl.process(prefix, prefix, pl.rows_polaris_table())
        else:
            print("  FĂRĂ ACCES ->", url)
            pl.recap.append(dict(sursa=prefix, magazin=handle, factura="", data="", motiv="FARA ACCES " + url, suma="", status="", fisier="")); pl.save_recap()

    print(f"\n##### magazine din organizații (tip B): {len(org_stores)}")
    for sid, (name, url) in org_stores.items():
        print(f"\n=== {name} ({sid})")
        try:
            pl.goto(url); pl.process(f"org:{sid}", name, pl.rows_polaris_table())
        except Exception as e:
            print("  EROARE", type(e).__name__, str(e)[:120])
    pw.stop()

    # folder cu toate la un loc
    allp = out / "TOATE"; allp.mkdir(exist_ok=True)
    n = 0
    for r in pl.recap:
        f = pathlib.Path(r["fisier"]) if r.get("fisier") else None
        if f and f.exists():
            dst = allp / f"{safe(r['magazin'])}_{r['data']}_{r['factura']}_{clean_total(r['suma'])}.pdf"
            dst.write_bytes(f.read_bytes()); n += 1
    try: (out / "_dl").rmdir()
    except OSError: pass
    print(f"\nGATA: {n} PDF-uri în {allp}; recap: {pl.recap_path}")


if __name__ == "__main__":
    main()
