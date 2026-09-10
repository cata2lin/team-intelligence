# /// script
# dependencies = ["playwright"]
# ///
"""Descarcă facturile TikTok Ads (Business Center → Payment management → Invoices) din luna dată, ca PDF,
pentru toate Business Center-urile la care are acces contul logat.

  uv run tiktok_facturi.py 2026-08                       # toate BC-urile, ieșire Desktop/facturi tiktok 2026-08
  uv run tiktok_facturi.py 2026-08 --bc "ARONA SRL,NVSDG" # doar câteva (nume sau id)
  uv run tiktok_facturi.py 2026-08 --out <folder> --port 9223

Pre-condiție: un Chrome SEPARAT pornit cu --remote-debugging-port=<port>, logat în business.tiktok.com
(vezi SKILL.md). Lista de BC-uri se ia live din /api/v2/bm/organizations/.
"""
import time, sys, re, json, csv, pathlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

import argparse
ap = argparse.ArgumentParser(); ap.add_argument("month"); ap.add_argument("--out"); ap.add_argument("--bc", default=""); ap.add_argument("--port", type=int, default=9223)
A = ap.parse_args()
MONTH = A.month
OUT = pathlib.Path(A.out) if A.out else pathlib.Path.home() / "Desktop" / f"facturi tiktok {MONTH}"; OUT.mkdir(parents=True, exist_ok=True)
ONLY = {x.strip().lower() for x in A.bc.split(",") if x.strip()}
FIELDS = ["bc", "bc_id", "invoice_serial", "invoice_id", "send_date", "amount", "currency", "status", "bg_name", "adv_ids", "file"]
recap_path = OUT / "recap.csv"
recap = list(csv.DictReader(open(recap_path, encoding="utf-8-sig"))) if recap_path.exists() else []
done = {r["invoice_id"] for r in recap if r.get("file", "").endswith(".pdf")}
def save():
    with open(recap_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(recap)
def safe(s): return re.sub(r"[^\w.-]+", "_", s or "").strip("_")[:60]

pw = sync_playwright().start()
try: b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{A.port}")
except Exception: sys.exit(f"Nu mă pot conecta la Chrome pe portul {A.port} — pornește-l cu --remote-debugging-port={A.port} (vezi SKILL.md)")
c = b.contexts[0]; p = c.pages[0] if c.pages else c.new_page()
p.goto("https://business.tiktok.com/manage/overview", wait_until="domcontentloaded", timeout=90000); time.sleep(6)
orgs = c.request.get("https://business.tiktok.com/api/v2/bm/organizations/?attr_source=&source_biz_id=&attr_type=web").json()
BCS = {str(o["id"]): o["name"] for o in ((orgs.get("data") or {}).get("organizations") or []) if o.get("id") and o.get("name")}
if not BCS: sys.exit("Nu am găsit Business Center-uri — ești logat în business.tiktok.com în fereastra CDP?")
print(f"{len(BCS)} Business Center-uri: " + ", ".join(BCS.values()))

def api(path, body):
    return p.evaluate("""async ([path, body]) => { const r = await fetch(path, {method:'POST', headers:{'content-type':'application/json','x-requested-with':'XMLHttpRequest'}, body: JSON.stringify(body)}); return await r.json(); }""", [path, body])

def status_text(inv):
    """Etichete verificate pe pagină (sep-2026): display_status 2 = Paid; 4 = 'No need to pay'
    (alimentări acoperite din sold); classify 10 / seria '-CN' = notă de credit; unpaid_amount>0 pe
    factură normală = Unpaid."""
    if inv.get("classify") == 10 or "-CN" in inv.get("invoice_serial", ""): return "NotaCredit"
    ds = inv.get("display_status")
    if ds == 2: return "Paid"
    if ds == 4: return "NoNeedToPay"
    if float(inv.get("unpaid_amount") or 0) > 0: return "Unpaid"
    return f"status{ds}-{inv.get('status')}"

for bc_id, bc_name in BCS.items():
    if ONLY and bc_id.lower() not in ONLY and bc_name.lower() not in ONLY: continue
    print(f"\n=== {bc_name} ({bc_id})")
    p.goto(f"https://business.tiktok.com/manage/billing/v2?org_id={bc_id}", wait_until="domcontentloaded", timeout=90000); time.sleep(6)
    try: p.get_by_role("button", name="Cancel").first.click(timeout=1500)
    except Exception: pass
    invs, page = [], 1
    while True:
        j = api("/pa/api/common/show/invoice/query_invoice_list", {"pagination": {"page_no": page, "page_size": 100}, "Context": {"platform": 2, "bc_id": bc_id}})
        if j.get("code") != 0: print("  list err:", j.get("msg")); break
        rows = (j.get("data") or {}).get("data") or []
        invs += rows
        if not rows or min(r["send_date"] for r in rows) < MONTH + "-01" or len(rows) < 100: break
        page += 1
    tg = [r for r in invs if r["send_date"].startswith(MONTH)]
    print(f"  {len(invs)} facturi listate, {len(tg)} în {MONTH}")
    if not tg: continue
    d = OUT / safe(bc_name); d.mkdir(exist_ok=True)
    for inv in tg:
        iid = inv["invoice_id"]
        if iid in done: print("   skip", inv["invoice_serial"]); continue
        st = status_text(inv)
        fname = f"{inv['send_date']}_{inv['invoice_serial']}_{inv['amount']}{inv['currency']}_{safe(st)}.pdf"
        rec = dict(bc=bc_name, bc_id=bc_id, invoice_serial=inv["invoice_serial"], invoice_id=iid, send_date=inv["send_date"], amount=inv["amount"], currency=inv["currency"], status=st, bg_name=inv.get("bg_name", ""), adv_ids=" ".join(inv.get("adv_id_list") or []), file="")
        try:
            ctx = {"platform": 2, "pa_id": inv.get("pa_id", ""), "bc_id": bc_id}
            cr = api("/pa/api/download/create", {"download_task_type": 136, "query_param": json.dumps({"invoice_id": iid}), "timezone": "Europe/Bucharest", "Context": ctx})
            if cr.get("code") != 0: raise RuntimeError("create: " + str(cr.get("msg")))
            task = cr["data"]["task_id"]; url = None
            for _ in range(30):
                q = api("/pa/api/download/query", {"task_id": task, "Context": ctx})
                if (q.get("data") or {}).get("status") == 1: url = q["data"]["download_url"]; break
                time.sleep(1)
            if not url: raise RuntimeError("no download_url")
            body = c.request.get(url).body()
            if not body.startswith(b"%PDF"): raise RuntimeError("not pdf")
            (d / fname).write_bytes(body); rec["file"] = str(d / fname); print("   OK", fname)
        except Exception as e:
            rec["file"] = "EROARE " + str(e)[:80]; print("   EROARE", inv["invoice_serial"], str(e)[:100])
        recap.append(rec); save()
pw.stop()
allp = OUT / "TOATE"; allp.mkdir(exist_ok=True); n = 0
for r in recap:
    f = pathlib.Path(r["file"]) if r["file"].endswith(".pdf") else None
    if f and f.exists(): (allp / f"{safe(r['bc'])}_{f.name}").write_bytes(f.read_bytes()); n += 1
print(f"\nGATA: {n} PDF în {allp}")
