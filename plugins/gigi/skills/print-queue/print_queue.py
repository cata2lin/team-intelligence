# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf"]
# ///
"""print-queue (LOCAL, depozit) — coada de print grupată pt pick&pack. Sursă = xConnector LIVE
(downloaded=false, MTD, dispatched=false). open = PDF-uri în Chrome (Ctrl+P) + log printed_log; reprint din log."""
import os, sys, re, json, time, sqlite3, argparse, datetime, importlib.util, urllib.request, io

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

def _find_xconnector():
    """xconnector.py sta in alt loc pe statie (marketplace) decat pe server (vault). Calea
    hardcodata ~/.claude/skills/... exista doar pe server, unde e symlink — pe o statie
    Windows lipseste, si scriptul raporta "xconnector.py lipseste" desi era instalat."""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [
        os.environ.get("XCONNECTOR_PY", ""),
        os.path.join(here, "..", "xconnector", "xconnector.py"),
        os.path.join(here, "xconnector.py"),
        os.path.expanduser("~/.claude/skills/xconnector/xconnector.py"),
        os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/"
                           "plugins/gigi/skills/xconnector/xconnector.py"),
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return os.path.abspath(cands[1])


XCONN = _find_xconnector()
DB = os.path.expanduser("~/.arona_print_queue.db")

def load_xconn():
    # xconnector.py importă module SURORI (address_rules, *_nomenclator...). Fără directorul
    # lui pe sys.path, importlib îl încarcă și crapă cu ModuleNotFoundError — se vede doar
    # când rulează din alt cwd (ex. server-side prin Second Brain, nu pe stație).
    _d = os.path.dirname(XCONN)
    if _d not in sys.path:
        sys.path.insert(0, _d)
    spec = importlib.util.spec_from_file_location("xconnector_mod", XCONN)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def db():
    c = sqlite3.connect(DB)
    c.execute("create table if not exists queue(shop text, order_name text, awb text, connector_id text, label_urls text, skus text, total_items int, line_items int, primary_sku text, primary_qty int, fetched_at text, primary key(shop, order_name))")
    c.execute("create table if not exists printed_log(printed_at text, batch text, pdf text, shop text, order_name text, awb text, sku text, qty int, label_urls text)")
    return c

def primary(o):
    skus = [s for s in (o.get("skus") or []) if s]; total = o.get("totalItemsCount")
    if len(skus) == 1 and total: return skus[0].upper(), int(total)
    if skus: return skus[0].upper(), 1
    return "~~~~~", 0

def shop_match(dom, wants): return (not wants) or any(dom.startswith(w) or w in dom for w in wants)

def cmd_pull(a):
    xc = load_xconn(); shops = xc.load_shops()
    wants = [w.strip() for w in (a.shop or "").split(",") if w.strip()]
    if getattr(a, "group", None): wants = STORE_GROUPS[a.group]
    _m = _machine_of(a)
    if _m and not wants and not getattr(a, "group", None): wants = _machine_stores(_m)
    today = datetime.date.today(); dto = a.to or today.isoformat()
    if a.from_: dfrom = a.from_
    elif a.days: dfrom = (today - datetime.timedelta(days=a.days)).isoformat()
    else: dfrom = today.replace(day=1).isoformat()
    con = db(); cur = con.cursor(); n = 0
    for dom in [s["shopDomain"] for s in shops if shop_match(s["shopDomain"], wants)]:
        cur.execute("delete from queue where shop=?", (dom,))
    for sh in shops:
        dom = sh["shopDomain"]
        if not shop_match(dom, wants): continue
        x = xc.XC(sh["apiKey"])
        for o in x.orders(dfrom, dto, {"dispatched": "false", "sort": "date", "sortDir": "desc"}):
            if o.get("dispatched"): continue
            labels = [d for d in (o.get("documents") or []) if isinstance(d, dict) and d.get("documentType") == "SHIPPING_LABEL" and d.get("url") and (getattr(a, "all", False) or not d.get("downloaded"))]
            if not labels: continue
            psku, pqty = primary(o)
            cur.execute("insert or replace into queue values (?,?,?,?,?,?,?,?,?,?,?)",
                (dom, o.get("orderName"), xc.doc_tracking(labels[0]), str(labels[0].get("connectorId") or ""),
                 json.dumps([d["url"] for d in labels]), json.dumps(o.get("skus") or []), o.get("totalItemsCount") or 0,
                 o.get("lineItemsCount") or 0, psku, pqty, datetime.datetime.now().isoformat(timespec="seconds")))
            n += 1
    con.commit()
    print(f"PULL live: {n} etichete de printat -> {DB}  ({dfrom}->{dto}, shops={wants or 'toate'})")
    for shop, tot in cur.execute("select shop, count(*) from queue group by shop order by 2 desc"): print(f"  {shop:30} {tot}")

def _rows(cur, a):
    wants = [w.strip() for w in (a.shop or "").split(",") if w.strip()]
    rows = [dict(shop=r[0], order=r[1], awb=r[2], urls=json.loads(r[3] or "[]"), sku=r[4], qty=r[5], skus_list=json.loads(r[6] or "[]"), lines=r[7] or 1)
            for r in cur.execute("select shop,order_name,awb,label_urls,primary_sku,primary_qty,skus,line_items from queue")]
    rows = [r for r in rows if shop_match(r["shop"], wants)]
    if getattr(a, "sku", None): rows = [r for r in rows if r["sku"].startswith(a.sku.upper())]
    if getattr(a, "items", None): rows = [r for r in rows if r["qty"] == a.items]
    _m = _machine_of(a)
    if _m: rows = [r for r in rows if _machine_keep(r, _m)]
    return rows

def _group(rows, threshold):
    groups = {}
    for r in rows: groups.setdefault((r["sku"], r["qty"]), []).append(r)
    own = sorted(((k, v) for k, v in groups.items() if len(v) > threshold), key=lambda kv: -len(kv[1]))
    diverse = sorted(((k, v) for k, v in groups.items() if len(v) <= threshold), key=lambda kv: (kv[0][0], kv[0][1]))
    return own, diverse

CAT_ORDER = ["HA", "AȘTERNUTURI", "LAVETE", "OGLINZI", "COVOARE", "BAIE", "MIXT"]
LUMP_CATS = {"AȘTERNUTURI"}
COVOARE_SKUS = {"NEGRU", "GRI", "GRI-DESCHIS", "GRI-INCHIS", "MARO", "ALBASTRU"}
def category(sku):
    s = (sku or "").upper()
    if s.startswith("HA"): return "HA"
    if s.startswith("ASTERNUT"): return "AȘTERNUTURI"
    if "LAVETE" in s: return "LAVETE"
    if s.startswith("OGLINDA"): return "OGLINZI"
    if s in COVOARE_SKUS: return "COVOARE"
    if s.startswith("BAIE"): return "BAIE"
    return "MIXT"
STORE_GROUPS = {"deals": ["covoareauto-ro", "bonhaus", "ofertelezilei", "audusp-rf"], "intl": ["vthuzq-7j", "f0yrmh-ia", "ux1x6n-n2"]}
STORE_SHORT = {"covoareauto-ro": "MagDeal", "bonhaus": "Casa", "ofertelezilei": "Ofertele", "audusp-rf": "Reduceri", "vthuzq-7j": "BonhausCZ", "f0yrmh-ia": "BonhausPL", "ux1x6n-n2": "BonhausBG", "6f9e22-9d": "Esteban", "ix5bxc-hr": "GT", "bmuwvv-jy": "Nubra", "31k0py-bi": "LabNoir", "bb4nmc-pb": "Covoria", "nxfer1-n4": "Carpetto", "n12w89-yy": "Grandia", "dvk4hu-dq": "Belasil", "8e3700-d9": "Apreciat", "cn54vk-uz": "Gento", "1eee37-2d": "Nocturna", "de51c5-b8": "NocturnaLux", "1d2bce-2": "Rossi"}
def short(dom): return STORE_SHORT.get(dom.split(".")[0], dom.split(".")[0])

def _cat_pdfs(rows, threshold):
    from collections import defaultdict
    cats = defaultdict(list)
    for r in rows: cats[category(r["sku"])].append(r)
    out = []
    for cat in CAT_ORDER:
        cr = cats.get(cat, [])
        if not cr: continue
        if cat in LUMP_CATS: groups = [("TOATE", cr)]
        else:
            own, diverse = _group(cr, threshold)
            groups = [(f"{sku}_x{qty}", items) for (sku, qty), items in own]
            if diverse: groups.append(("DIVERSE", [r for _, items in diverse for r in items]))
        out.append((cat, len(cr), groups))
    return out

def _qty_groups(rows):
    from collections import defaultdict
    by = defaultdict(lambda: defaultdict(list))
    for r in rows: by[r["shop"]][r["qty"]].append(r)
    return [(sd, [(q, by[sd][q]) for q in sorted(by[sd])]) for sd in sorted(by, key=short)]

def _is_lavete(sku):
    s = (sku or "").upper()
    return "LAVET" in s or bool(re.search(r"\d+-[MS](?:-|$)", s))

# --- Rutare pe MASINI de print (depozit / uzina2) - urmeaza split-ul de stoc pe locatii ---
DEPOZIT_STORES = {"6f9e22-9d","ix5bxc-hr","bmuwvv-jy","31k0py-bi","dvk4hu-dq","1d2bce-2"}   # Esteban, GT, Nubra, LabNoir, Belasil, Rossi
UZINA2_STORES  = {"n12w89-yy","cn54vk-uz","8e3700-d9","nxfer1-n4","1eee37-2d","de51c5-b8","bb4nmc-pb","ce-pat-ai"}   # Grandia, Gento, Apreciat, Carpetto, Nocturna, NocturnaLux, Covoria, CePatAi
SPLIT_STORES_M = {"covoareauto-ro","bonhaus","ofertelezilei","audusp-rf","vthuzq-7j","f0yrmh-ia","ux1x6n-n2","63e901-2f","16w7xv-0w","oriceredus"}   # deals+intl: HA+LAVETE->depozit, rest->uzina2
DEPOZIT_CATS   = {"HA","LAVETE"}   # din split-stores -> depozit; restul -> uzina2

def _machine_of(a):
    m = (getattr(a,"machine",None) or os.environ.get("PRINT_MACHINE") or os.environ.get("EMPLOYEE_HANDLE") or "").strip().lower()
    return m if m in ("depozit","uzina2") else None

def _machine_keep(r,m):
    k = r["shop"].split(".")[0]
    if k in SPLIT_STORES_M:
        dep = category(r["sku"]) in DEPOZIT_CATS or _is_lavete(r["sku"])
        return dep if m=="depozit" else (not dep)
    return (k in DEPOZIT_STORES) if m=="depozit" else (k not in DEPOZIT_STORES)

def _machine_stores(m):
    return sorted((DEPOZIT_STORES | SPLIT_STORES_M) if m=="depozit" else (UZINA2_STORES | SPLIT_STORES_M))


def _detergent_groups(rows):
    from collections import Counter
    def mono(r): return 0 if (r.get("lines") or 1) <= 1 else 1
    fara, cu = [], []
    for r in rows:
        skus = r.get("skus_list") or [r["sku"]]
        (cu if any(_is_lavete(s) for s in skus) else fara).append(r)
    def ordered(items):
        cnt = Counter((mono(r), r["qty"] or 0, r["sku"]) for r in items)
        return sorted(items, key=lambda r: (mono(r), r["qty"] or 0, -cnt[(mono(r), r["qty"] or 0, r["sku"])], r["sku"] or ""))
    out = []
    if fara: out.append(("FARA-LAVETE", ordered(fara)))
    if cu: out.append(("CU-LAVETE", ordered(cu)))
    return out

def cmd_plan(a):
    con = db(); rows = _rows(con.cursor(), a)
    if not rows: print("Nimic in DB pt filtru. Rulează întâi pull."); return
    if getattr(a, "by_sku", False):
        print(f"PLAN DETERGENT — {len(rows)} etichete (2 grupuri: FARA/CU lavete; mono pe cantitate apoi multi)")
        for name, items in _detergent_groups(rows):
            mono = sum(1 for r in items if (r.get("lines") or 1) <= 1)
            print(f"   {name:14} {len(items):3} buc  ({mono} mono + {len(items)-mono} multi)")
        return
    if getattr(a, "by_qty", False):
        print(f"PLAN PARFUMURI — {len(rows)} etichete (per magazin, pe cantitate)")
        for sd, qtys in _qty_groups(rows):
            print(f"== {short(sd)}: {sum(len(it) for _, it in qtys)} buc ==")
            for q, items in qtys: print(f"   x{q} {len(items)} buc")
        return
    if getattr(a, "by_category", False):
        print(f"PLAN pe CATEGORII — {len(rows)} etichete (prag {a.threshold})")
        cg = _cat_pdfs(rows, a.threshold)
        for cat, tot, groups in cg:
            print(f"== {cat}: {tot} buc =="); [print(f"   {name:30} {len(items)} buc") for name, items in groups]
        for cat, tot, groups in cg: print(f"   {cat:14} {tot:4} ({len(groups)} PDF)")
        return
    own, diverse = _group(rows, a.threshold)
    print(f"PLAN — {len(rows)} etichete (prag {a.threshold})")
    for (sku, qty), items in own: print(f"    {sku:22} x{qty} {len(items)}")
    print(f"  DIVERSE: {sum(len(v) for _, v in diverse)}")

def _download(url, auth):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=({"Authorization": auth} if auth else {}))
            with urllib.request.urlopen(req, timeout=45) as r: data = r.read()
            if data[:5] == b"%PDF-": return data
        except Exception: time.sleep(1.2 * (attempt + 1))
    return None

def cmd_open(a):
    from pypdf import PdfReader, PdfWriter
    con = db(); rows = _rows(con.cursor(), a)
    if not rows: print("Nimic in DB. Rulează pull."); return
    outdir = os.path.expanduser(a.out or "~/Downloads/print-batch/grupat"); os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        try: os.remove(os.path.join(outdir, f))
        except Exception: pass
    batch_name = os.path.basename(outdir.rstrip("/\\")); now = datetime.datetime.now().isoformat(timespec="seconds"); logc = con.cursor()
    if a.from_batch:
        reader = PdfReader(a.from_batch); order_sorted = sorted(rows, key=lambda r: (r["shop"], r["sku"], r["qty"], r["order"]))
        if len(reader.pages) != len(order_sorted): print("!! batch nu corespunde"); return
        idx = {(r["shop"], r["order"]): i for i, r in enumerate(order_sorted)}
        def pages_of(r): return [reader.pages[idx[(r["shop"], r["order"])]]]
    else:
        xc = load_xconn(); auth = {s["shopDomain"]: "Bearer " + s["apiKey"] for s in xc.load_shops()}; cache = {}
        def pages_of(r):
            pgs = []
            for u in r["urls"]:
                if u not in cache: cache[u] = _download(u, auth.get(r["shop"], ""))
                if cache[u]: pgs += list(PdfReader(io.BytesIO(cache[u])).pages)
            return pgs
    def safe(s): return re.sub(r"[^A-Za-z0-9._-]", "_", s)
    def build(name, items):
        made = []
        for start in range(0, len(items), a.batch):
            chunk = items[start:start + a.batch]; suff = "" if len(items) <= a.batch else f"_lot{start//a.batch+1}"; fn = name.replace(".pdf", suff + ".pdf")
            w = PdfWriter(); ok = fail = 0
            for r in chunk:
                pgs = pages_of(r)
                if not pgs: fail += 1; continue
                for p in pgs: w.add_page(p)
                ok += 1
                logc.execute("insert into printed_log values (?,?,?,?,?,?,?,?,?)", (now, batch_name, fn, r["shop"], r["order"], r["awb"], r["sku"], r["qty"], json.dumps(r["urls"])))
            with open(os.path.join(outdir, fn), "wb") as f: w.write(f)
            made.append((fn, ok, fail))
        return made
    manifest = []; i = 1
    def emit(prefix, own, diverse):
        nonlocal i
        for (sku, qty), items in own:
            for fn, ok, fail in build(f"{i:02d}_{prefix}{safe(sku)}_x{qty}_{len(items)}buc.pdf", items): manifest.append((fn, ok, fail))
            i += 1
        if diverse:
            div = [r for _, items in diverse for r in items]
            for fn, ok, fail in build(f"{i:02d}_{prefix}DIVERSE_{len(div)}buc.pdf", div): manifest.append((fn, ok, fail))
            i += 1
    if getattr(a, "by_sku", False):
        for name, items in _detergent_groups(rows):
            for fn, ok, fail in build(f"{i:02d}_{safe(name)}_{len(items)}buc.pdf", items): manifest.append((fn, ok, fail))
            i += 1
    elif getattr(a, "by_qty", False):
        for sd, qtys in _qty_groups(rows):
            for q, items in qtys:
                for fn, ok, fail in build(f"{i:02d}_{safe(short(sd))}_x{q}_{len(items)}buc.pdf", items): manifest.append((fn, ok, fail))
                i += 1
    elif getattr(a, "by_category", False):
        for cat, tot, groups in _cat_pdfs(rows, a.threshold):
            for name, items in groups:
                for fn, ok, fail in build(f"{i:02d}_{cat}_{safe(name)}_{len(items)}buc.pdf", items): manifest.append((fn, ok, fail))
                i += 1
    else:
        own, diverse = _group(rows, a.threshold); emit("", own, diverse)
    print(f"OPEN -> {outdir}")
    for fn, ok, fail in manifest: print(f"  {fn:48} ok {ok}" + (f" fail {fail}" if fail else ""))
    con.commit(); print(f"  {sum(ok for _,ok,_ in manifest)} logate în printed_log (batch {batch_name})")
    if not a.no_open:
        import subprocess, shutil
        chrome = next((p for p in (shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")) if p and os.path.exists(p)), None)
        pdfs = [os.path.join(outdir, m[0]) for m in manifest]
        if chrome and pdfs: subprocess.Popen([chrome] + pdfs); print(f"  → deschis {len(pdfs)} PDF-uri în Chrome (Ctrl+P).")
        try: subprocess.Popen(["explorer", outdir])
        except Exception: pass

def cmd_printed(a):
    con = db(); cur = con.cursor()
    rows = list(cur.execute("select batch, max(printed_at), count(*) from printed_log group by batch order by max(printed_at) desc"))
    if not rows: print("printed_log gol."); return
    for batch, ts, n in rows: print(f"  {batch:30} {ts}  {n} etichete")

def cmd_reprint(a):
    from pypdf import PdfReader, PdfWriter
    import subprocess, shutil
    from collections import OrderedDict
    con = db(); cur = con.cursor()
    batch = a.batch_name or (cur.execute("select batch from printed_log order by printed_at desc limit 1").fetchone() or [None])[0]
    if not batch: print("Niciun print în log."); return
    wants = [w.strip() for w in (a.shop or "").split(",") if w.strip()]
    rows = [dict(pdf=r[0], shop=r[1], sku=r[2], order=r[3], urls=json.loads(r[4] or "[]")) for r in cur.execute("select pdf,shop,sku,order_name,label_urls from printed_log where batch=?", (batch,))]
    if getattr(a, "sku", None): rows = [r for r in rows if (r["sku"] or "").upper().startswith(a.sku.upper())]
    rows = [r for r in rows if shop_match(r["shop"], wants)]
    if not rows: print("Nimic în log pt filtru."); return
    xc = load_xconn(); auth = {s["shopDomain"]: "Bearer " + s["apiKey"] for s in xc.load_shops()}
    outdir = os.path.expanduser(f"~/Downloads/print-batch/{batch}_reprint"); os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        try: os.remove(os.path.join(outdir, f))
        except Exception: pass
    by_pdf = OrderedDict()
    for r in rows: by_pdf.setdefault(r["pdf"], []).append(r)
    cache = {}; made = []
    for pdf, items in by_pdf.items():
        w = PdfWriter(); ok = fail = 0
        for r in items:
            got = False
            for u in r["urls"]:
                if u not in cache: cache[u] = _download(u, auth.get(r["shop"], ""))
                if cache[u]:
                    for p in PdfReader(io.BytesIO(cache[u])).pages: w.add_page(p)
                    got = True
            ok += 1 if got else 0; fail += 0 if got else 1
        with open(os.path.join(outdir, pdf), "wb") as f: w.write(f)
        made.append((pdf, ok, fail))
    print(f"REPRINT {batch}: {len(rows)} etichete -> {outdir}")
    chrome = next((p for p in (shutil.which("chrome"), r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")) if p and os.path.exists(p)), None)
    pdfs = [os.path.join(outdir, m[0]) for m in made]
    if not getattr(a, "no_open", False) and chrome and pdfs:
        subprocess.Popen([chrome] + pdfs); subprocess.Popen(["explorer", outdir])

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull"); p.add_argument("--shop"); p.add_argument("--group", choices=sorted(STORE_GROUPS)); p.add_argument("--from", dest="from_"); p.add_argument("--to"); p.add_argument("--days", type=int, default=None); p.add_argument("--all", action="store_true"); p.add_argument("--machine", choices=["depozit","uzina2","all"])
    for name in ("plan", "open"):
        s = sub.add_parser(name); s.add_argument("--shop"); s.add_argument("--sku"); s.add_argument("--items", type=int); s.add_argument("--threshold", type=int, default=3); s.add_argument("--machine", choices=["depozit","uzina2","all"])
        s.add_argument("--by-category", action="store_true", dest="by_category"); s.add_argument("--by-qty", action="store_true", dest="by_qty"); s.add_argument("--by-sku", action="store_true", dest="by_sku")
        if name == "open": s.add_argument("--out"); s.add_argument("--batch", type=int, default=250); s.add_argument("--from-batch", dest="from_batch"); s.add_argument("--no-open", action="store_true")
    sub.add_parser("printed")
    rp = sub.add_parser("reprint"); rp.add_argument("--batch", dest="batch_name"); rp.add_argument("--shop"); rp.add_argument("--sku"); rp.add_argument("--no-open", action="store_true", dest="no_open")
    a = ap.parse_args()
    {"pull": cmd_pull, "plan": cmd_plan, "open": cmd_open, "printed": cmd_printed, "reprint": cmd_reprint}[a.cmd](a)

if __name__ == "__main__": main()

