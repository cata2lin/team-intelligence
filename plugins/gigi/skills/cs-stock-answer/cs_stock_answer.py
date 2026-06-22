# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "paramiko>=3.0"]
# ///
"""
cs_stock_answer.py — RĂSPUNS INSTANT la întrebările presale de stoc:
  „mai e pe stoc?" / „când revine?" / „mai revine?" — fără să cauți manual în 16 admin-uri.

Sursa 1 (STOC LIVE): data/product_analytics.db pe VPS → analytics_products
  (sku, title, price, prefix, inventory_qty), ~5.200 SKU peste toate magazinele.
  Cauți pe --sku (exact + LIKE) SAU pe --product (LIKE pe titlu).
  Arată stocul PE MAGAZIN (prefix) → up-sell cross-magazin
  („nu mai e la GT, dar există la Nubra").

Sursa 2 (ETA RESTOCK), doar dacă e epuizat: Postgres TOM → purchase_order_items
  (type='RESTOCK') JOIN products → status restock = ETA aproximativ:
    NEW       → cerut, comandă încă neplasată         → „revine, fără dată fermă"
    ORDERED   → comandat (orderedAt)                  → „revine ~ orderedAt + lead"
    SHIPPED   → expediat / la depozit                 → „revine în câteva zile"
    RECEIVED  → recepționat la depozit                → „revine imediat"
    CANCELLED → cancelReason (OUT_OF_STOCK / ...)     → „NU mai revine — oferă alternativă"
  Matching TOM: întâi externalSku exact, apoi externalTitle ILIKE pe titlul produsului
  (SKU-urile Shopify per variantă ≠ SKU-urile interne TOM, deci titlul e puntea sigură).
  Lead-time = aproximare simplă (AIR ~%d zile order→sosire), nu desfacem po_item_events.

OUTPUT: 2-3 rânduri status + mesaj client gata de trimis în RO (și scurt CZ/PL/BG).
  uv run cs_stock_answer.py --sku gt-140
  uv run cs_stock_answer.py --product "aparat foto instant"
  uv run cs_stock_answer.py --product "incalzitor diesel" --json

READ-ONLY total. Nu scrie nimic în Postgres / Shopify / Richpanel.
"""
import os, sys, re, json, subprocess, shlex, urllib.parse, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"

def _vps_run(remote_cmd):
    """Run a command on the profit VPS over SSH (paramiko, password from KB/env).
    Zero-touch: PROFIT_SSH_HOST/USER/PASS are read from env, else the team KB.
    Returns a CompletedProcess-like object (.stdout/.stderr/.returncode)."""
    import os as _os, sys as _sys, types as _types, subprocess as _sp
    def _sec(k):
        v = _os.environ.get(k)
        if v:
            return v
        kb = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                           "..", "..", "..", "core", "scripts", "kb.py")
        try:
            return _sp.run(["uv", "run", kb, "secret-get", k],
                           capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            return ""
    host = _sec("PROFIT_SSH_HOST") or "84.46.242.181"
    user = _sec("PROFIT_SSH_USER") or "root"
    pwd = _sec("PROFIT_SSH_PASS")
    if not pwd:
        _sys.exit("Lipsa PROFIT_SSH_PASS (KB/env). Ruleaza: kb.py secret-set PROFIT_SSH_PASS ...")
    import paramiko
    cl = paramiko.SSHClient()
    cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cl.connect(host, username=user, password=pwd, timeout=30)
    _i, _o, _e = cl.exec_command(remote_cmd, timeout=180)
    out = _o.read().decode(); err = _e.read().decode()
    rc = _o.channel.recv_exit_status()
    cl.close()
    return _types.SimpleNamespace(stdout=out, stderr=err, returncode=rc)
HERE = os.path.dirname(os.path.abspath(__file__))

# lead-time mediu order->sosire (AIR), măsurat din TOM (~15z). Folosit doar pt ETA aproximativ.
LEAD_DAYS = 15

# prefix magazin -> (nume, limbă pt mesaj client)
PREFIX = {
    "EST": ("Esteban", "ro"), "GT": ("George Talent", "ro"), "NUB": ("Nubra", "ro"),
    "GEN": ("Gento", "ro"), "GRAN": ("Grandia", "ro"), "GRAND": ("Grandia", "ro"),
    "BELA": ("Belasil", "ro"), "CARP": ("Carpetto", "ro"), "COV": ("Covoria", "ro"),
    "MAG": ("Magdeal", "ro"), "OFER": ("Ofertele Zilei", "ro"), "RED": ("Reduceri bune", "ro"),
    "BON": ("Bonhaus RO", "ro"), "BONBG": ("Bonhaus BG", "bg"), "BG": ("Bonhaus BG", "bg"),
    "CZ": ("Bonhaus CZ", "cz"), "PL": ("Bonhaus PL", "pl"), "APR": ("Apreciat", "ro"),
    "ROSSI": ("Rossi Nails", "ro"), "LUX": ("Lux", "ro"), "NOC": ("Nocturna", "ro"),
    "PAT": ("Pat", "ro"),
}

# mesaje client gata de trimis — IN_STOCK / RESTOCK_ETA / RESTOCK_SOON / NO_RESTOCK
MSG = {
    "in_stock": {
        "ro": "Bună {n}! Da, „{t}” este pe stoc la {b} și se poate comanda acum. 😊 Îți rezervăm exemplarul?",
        "cz": "Dobrý den! Ano, „{t}” je skladem ({b}) a lze objednat hned. 😊",
        "pl": "Cześć! Tak, „{t}” jest dostępny ({b}) i można zamówić od razu. 😊",
        "bg": "Здравейте! Да, „{t}” е в наличност ({b}) и може да се поръча веднага. 😊",
    },
    "elsewhere": {
        "ro": "Bună {n}! „{t}” nu mai e pe stoc la {b}, dar avem un model echivalent disponibil acum la {b2} — îți pot trimite linkul. Vrei? 😊",
        "cz": "Dobrý den! „{t}” momentálně není skladem, ale máme obdobný model dostupný u {b2}. Mám poslat odkaz? 😊",
        "pl": "Cześć! „{t}” jest chwilowo niedostępny, ale mamy podobny model dostępny w {b2}. Wysłać link? 😊",
        "bg": "Здравейте! „{t}” в момента не е наличен, но имаме подобен модел при {b2}. Да изпратя линк? 😊",
    },
    "eta": {
        "ro": "Bună {n}! „{t}” e momentan epuizat la {b}, dar avem deja stoc comandat — estimăm că revine în jur de {d}. Vrei să te anunțăm când intră? 📦",
        "cz": "Dobrý den! „{t}” je momentálně vyprodáno, ale máme objednáno — očekáváme naskladnění kolem {d}. Dáme vědět? 📦",
        "pl": "Cześć! „{t}” jest chwilowo niedostępny, ale mamy zamówiony — spodziewamy się dostawy około {d}. Powiadomić Cię? 📦",
        "bg": "Здравейте! „{t}” в момента е изчерпан, но вече е поръчан — очакваме наличност около {d}. Да Ви уведомим? 📦",
    },
    "soon": {
        "ro": "Bună {n}! „{t}” e epuizat acum la {b}, dar transportul e deja expediat — intră pe stoc în câteva zile. Vrei să te anunțăm imediat ce e disponibil? 📦",
        "cz": "Dobrý den! „{t}” je vyprodáno, ale zásilka už je na cestě — naskladníme během několika dní. Dáme vědět? 📦",
        "pl": "Cześć! „{t}” jest niedostępny, ale dostawa jest już w drodze — pojawi się za kilka dni. Powiadomić? 📦",
        "bg": "Здравейте! „{t}” е изчерпан, но пратката вече е изпратена — ще е налично след няколко дни. Да Ви уведомим? 📦",
    },
    "no_restock": {
        "ro": "Bună {n}! Din păcate „{t}” nu se mai aduce pe stoc la {b}. Pot să-ți recomand o alternativă similară — vrei să-ți trimit câteva opțiuni? 🙏",
        "cz": "Dobrý den! Bohužel „{t}” se už nebude doplňovat. Mohu doporučit podobnou alternativu — mám poslat pár možností? 🙏",
        "pl": "Cześć! Niestety „{t}” nie będzie już dostępny. Mogę polecić podobną alternatywę — wysłać kilka opcji? 🙏",
        "bg": "Здравейте! За съжаление „{t}” вече няма да се зарежда. Мога да предложа подобна алтернатива — да изпратя няколко опции? 🙏",
    },
    "unknown_restock": {
        "ro": "Bună {n}! „{t}” e epuizat momentan la {b} și verificăm dacă mai vine reaprovizionare. Îți revin cu un răspuns ferm cât de repede pot. 🙏",
        "cz": "Dobrý den! „{t}” je momentálně vyprodáno, ověřujeme doplnění a brzy se ozveme. 🙏",
        "pl": "Cześć! „{t}” jest chwilowo niedostępny, sprawdzamy dostawę i wkrótce wrócimy z odpowiedzią. 🙏",
        "bg": "Здравейте! „{t}” в момента е изчерпан, проверяваме за зареждане и скоро ще се свържем. 🙏",
    },
}

# cuvinte de ignorat la potrivirea pe titlu (TOM) — prea generice
STOPWORDS = {"set", "pentru", "din", "cu", "si", "și", "de", "la", "un", "o", "the", "and",
             "for", "with", "fara", "fără", "auto", "pcs", "buc", "ml", "cm", "kg",
             "parfum", "model", "premium", "negru", "alb", "gri"}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k],
                          capture_output=True, text=True).stdout.strip()


def store_name(prefix):
    return PREFIX.get((prefix or "").upper(), (prefix or "?", "ro"))[0]


def store_lang(prefix):
    return PREFIX.get((prefix or "").upper(), ("?", "ro"))[1]


# ───────────────────────── Sursa 1: stoc live (SQLite pe VPS) ─────────────────────────
def fetch_live_stock(sku=None, product=None):
    """Caută în analytics_products. Pe --sku: exact ȘI prefix-LIKE. Pe --product: LIKE pe titlu."""
    if sku:
        mode, term = "sku", sku
    else:
        mode, term = "product", product
    py = (
        "import sqlite3,json,sys;"
        "mode=sys.argv[1];term=sys.argv[2];"
        "c=sqlite3.connect('data/product_analytics.db');"
        "c.row_factory=sqlite3.Row;"
        "rows=[];"
        "q1='SELECT sku,title,price,prefix,inventory_qty,product_type,vendor,currency FROM analytics_products';"
        "cond=(\" WHERE sku=? OR sku LIKE ?\" if mode=='sku' else \" WHERE title LIKE ?\");"
        "params=((term,'%'+term+'%') if mode=='sku' else ('%'+term+'%',));"
        "rows=[dict(r) for r in c.execute(q1+cond+' ORDER BY inventory_qty DESC LIMIT 400',params)];"
        "print(json.dumps(rows,ensure_ascii=False))"
    )
    cmd = ("cd /root/Scripturi && .venv/bin/python3 -c "
           + shlex.quote(py) + " " + shlex.quote(mode) + " " + shlex.quote(term))
    out = _vps_run(cmd)
    txt = (out.stdout or "").strip()
    try:
        return json.loads(txt.splitlines()[-1]) if txt else []
    except Exception:
        return []


# ───────────────────────── Sursa 2: ETA restock (Postgres TOM) ─────────────────────────
def tconn():
    url = secret("DATABASE_URL_TOM"); u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def _title_keywords(title):
    words = re.findall(r"[A-Za-zĂÂÎȘȚăâîșț0-9]{3,}", (title or "").lower())
    kw = [w for w in words if w not in STOPWORDS]
    return kw[:5]


def fetch_restock(skus, titles):
    """Caută reaprovizionări (type='RESTOCK') în TOM pt setul de SKU-uri/titluri date.
    Întoarce lista de dict-uri restock cu status/eta. Best-effort (read-only)."""
    skus = [s for s in {s.strip() for s in skus if s and s.strip()}]
    # construiește un set de pattern-uri de titlu (cuvinte semnificative)
    title_kw = set()
    for t in titles:
        for w in _title_keywords(t):
            title_kw.add(w)
    if not skus and not title_kw:
        return []
    try:
        conn = tconn(); cur = conn.cursor()
    except Exception as e:
        return [{"_err": str(e)}]
    found = {}
    cols = ('i.id, i.status, i."externalSku", i."externalTitle", i."shippingMode", '
            'i."orderedAt", i."shippedAt", i."receivedAt", i."cancelReason", i."cancelNote", '
            'i."requestedQty", i."orderedQty", i."receivedQty", p.sku, p.title')
    base = ('FROM purchase_order_items i LEFT JOIN products p ON p.id = i."productId" '
            "WHERE i.type='RESTOCK' ")

    def add(rows):
        for r in rows:
            found[r[0]] = {
                "id": r[0], "status": r[1], "ext_sku": r[2], "ext_title": r[3],
                "ship_mode": r[4], "orderedAt": r[5], "shippedAt": r[6], "receivedAt": r[7],
                "cancelReason": r[8], "cancelNote": r[9], "reqQty": r[10], "ordQty": r[11],
                "recvQty": r[12], "prod_sku": r[13], "prod_title": r[14],
            }

    # 1) potrivire pe SKU exact (externalSku sau products.sku)
    if skus:
        ph = ",".join(["%s"] * len(skus))
        cur.execute('SELECT %s %s AND (i."externalSku" IN (%s) OR p.sku IN (%s))'
                    % (cols, base, ph, ph), skus + skus)
        add(cur.fetchall())
    # 2) potrivire pe titlu (cuvinte semnificative) — puntea sigură cross-system
    for kw in list(title_kw)[:6]:
        cur.execute('SELECT %s %s AND (i."externalTitle" ILIKE %%s OR p.title ILIKE %%s) LIMIT 40'
                    % (cols, base), ("%" + kw + "%", "%" + kw + "%"))
        add(cur.fetchall())
    conn.close()
    return list(found.values())


def classify_restock(items):
    """Reduce lista de restock-items la un singur verdict + ETA pt mesajul către client."""
    if not items:
        return None
    if any("_err" in it for it in items):
        return {"verdict": "error", "msg": items[0].get("_err")}
    order = {"SHIPPED": 0, "RECEIVED": 1, "ORDERED": 2, "NEW": 3, "CANCELLED": 4}
    items = sorted(items, key=lambda it: order.get(it.get("status"), 9))
    active = [it for it in items if it.get("status") in ("SHIPPED", "RECEIVED", "ORDERED", "NEW")]
    cancelled = [it for it in items if it.get("status") == "CANCELLED"]

    if active:
        it = active[0]
        st = it["status"]
        if st in ("SHIPPED", "RECEIVED"):
            return {"verdict": "soon", "status": st, "item": it,
                    "note": "expediat/recepționat — intră pe stoc în câteva zile"}
        if st == "ORDERED":
            eta = None
            if it.get("orderedAt"):
                try:
                    d = it["orderedAt"]
                    base = d.date() if hasattr(d, "date") else datetime.date.fromisoformat(str(d)[:10])
                    eta = base + datetime.timedelta(days=LEAD_DAYS)
                except Exception:
                    eta = None
            return {"verdict": "eta", "status": st, "item": it, "eta": eta,
                    "note": "comandat la furnizor — ETA aproximativ"}
        # NEW
        return {"verdict": "eta", "status": st, "item": it, "eta": None,
                "note": "cerere de reaprovizionare deschisă, fără dată fermă încă"}
    if cancelled:
        it = cancelled[0]
        reason = it.get("cancelReason") or "?"
        # OUT_OF_STOCK / REQUESTER_CANCELLED / OTHER
        return {"verdict": "no_restock", "status": "CANCELLED", "item": it, "reason": reason,
                "note": "reaprovizionare anulată (%s) — nu se mai aduce" % reason}
    return None


# ───────────────────────── Agregare & render ─────────────────────────
def aggregate(stock_rows):
    """Grupează pe titlu (produs) → pe magazin (prefix). Întoarce listă de produse."""
    by_title = {}
    for r in stock_rows:
        key = (r.get("title") or "").strip().lower()
        by_title.setdefault(key, {"title": r.get("title") or "(fără titlu)", "stores": {}})
        st = by_title[key]["stores"].setdefault(
            (r.get("prefix") or "?").upper(),
            {"prefix": (r.get("prefix") or "?").upper(), "qty": 0, "skus": [],
             "price": r.get("price"), "currency": r.get("currency") or "RON"})
        st["qty"] += int(r.get("inventory_qty") or 0)
        if r.get("sku"):
            st["skus"].append(r["sku"])
        if not st.get("price"):
            st["price"] = r.get("price")
    prods = []
    for v in by_title.values():
        stores = sorted(v["stores"].values(), key=lambda s: -s["qty"])
        prods.append({"title": v["title"], "stores": stores,
                      "total_qty": sum(s["qty"] for s in stores)})
    # produsele cu cele mai multe magazine/stoc primele
    prods.sort(key=lambda p: (-p["total_qty"], -len(p["stores"])))
    return prods


def fmt_eta(eta):
    if not eta:
        return "în curând (dată neconfirmată)"
    return eta.isoformat()


def pick_msg(verdict_key, lang, **kw):
    bucket = MSG.get(verdict_key, MSG["unknown_restock"])
    tmpl = bucket.get(lang) or bucket["ro"]
    return tmpl.format(**kw)


def render(query_label, prods, restock_verdict, want_store=None):
    print("=" * 64)
    print("  CS STOC — %s" % query_label)
    print("=" * 64)
    if not prods:
        print("  Niciun produs găsit în stocul live pt această căutare.")
        print("  → Verifică ortografia / încearcă --product cu un cuvânt-cheie mai scurt.")
        return

    if len(prods) > 1:
        print("  %d produse potrivite (afișez primele):\n" % len(prods))

    for prod in prods[:6]:
        title = prod["title"]
        stores = prod["stores"]
        if want_store:
            ws = want_store.upper()
            stores = [s for s in stores if s["prefix"] == ws or store_name(s["prefix"]).lower().startswith(want_store.lower())]
            if not stores:
                continue
        in_stock = [s for s in stores if s["qty"] > 0]
        oos = [s for s in stores if s["qty"] <= 0]

        print("─" * 64)
        print("  PRODUS: %s" % title)
        # status pe magazin
        for s in stores:
            tag = "PE STOC" if s["qty"] > 0 else "epuizat"
            price = ("%.0f %s" % (s["price"], s["currency"])) if s.get("price") else "-"
            sku0 = s["skus"][0] if s["skus"] else "-"
            extra = ("  +%d variante" % (len(s["skus"]) - 1)) if len(s["skus"]) > 1 else ""
            print("    %-16s %-8s stoc=%-5d preț=%-10s sku=%s%s" % (
                store_name(s["prefix"])[:16], tag, s["qty"], price, sku0, extra))

        # VERDICT + mesaj client
        if in_stock:
            s = in_stock[0]
            lang = store_lang(s["prefix"])
            bn = store_name(s["prefix"])
            print("\n  ✅ VERDICT: DA, pe stoc (%d buc la %s)." % (s["qty"], bn))
            print("  💬 RO: " + pick_msg("in_stock", "ro", n="", t=title, b=bn).strip())
            if lang != "ro":
                print("  💬 %s: %s" % (lang.upper(), pick_msg("in_stock", lang, n="", t=title, b=bn).strip()))
            continue

        # epuizat peste tot → up-sell sau ETA restock
        b_oos = store_name(oos[0]["prefix"]) if oos else "magazin"
        lang = store_lang(oos[0]["prefix"]) if oos else "ro"
        rv = restock_verdict  # un singur verdict global pe căutare (best-effort)
        if rv and rv.get("verdict") == "soon":
            print("\n  📦 VERDICT: epuizat — REVINE ÎN CÂTEVA ZILE (transport %s)." % (rv["item"].get("status")))
            print("  💬 RO: " + pick_msg("soon", "ro", n="", t=title, b=b_oos).strip())
            if lang != "ro":
                print("  💬 %s: %s" % (lang.upper(), pick_msg("soon", lang, n="", t=title, b=b_oos).strip()))
        elif rv and rv.get("verdict") == "eta":
            eta = rv.get("eta")
            print("\n  📦 VERDICT: epuizat — REVINE ~ %s (%s)." % (fmt_eta(eta), rv["item"].get("status")))
            print("  💬 RO: " + pick_msg("eta", "ro", n="", t=title, b=b_oos, d=fmt_eta(eta)).strip())
            if lang != "ro":
                print("  💬 %s: %s" % (lang.upper(), pick_msg("eta", lang, n="", t=title, b=b_oos, d=fmt_eta(eta)).strip()))
        elif rv and rv.get("verdict") == "no_restock":
            print("\n  ⛔ VERDICT: NU mai revine (reaprovizionare anulată: %s)." % rv.get("reason"))
            print("  💬 RO: " + pick_msg("no_restock", "ro", n="", t=title, b=b_oos).strip())
            if lang != "ro":
                print("  💬 %s: %s" % (lang.upper(), pick_msg("no_restock", lang, n="", t=title, b=b_oos).strip()))
        else:
            # niciun restock găsit în TOM — verdict necunoscut
            print("\n  ❓ VERDICT: epuizat — nicio reaprovizionare găsită în TOM (verifică manual / oferă alternativă).")
            print("  💬 RO: " + pick_msg("unknown_restock", "ro", n="", t=title, b=b_oos).strip())
            if lang != "ro":
                print("  💬 %s: %s" % (lang.upper(), pick_msg("unknown_restock", lang, n="", t=title, b=b_oos).strip()))

    print("─" * 64)


def build_json(query_label, prods, rv):
    out = {"query": query_label, "products": []}
    for p in prods:
        in_stock = [s for s in p["stores"] if s["qty"] > 0]
        out["products"].append({
            "title": p["title"], "total_qty": p["total_qty"],
            "in_stock": bool(in_stock),
            "stores": [{"store": store_name(s["prefix"]), "prefix": s["prefix"],
                        "qty": s["qty"], "price": s.get("price"),
                        "currency": s.get("currency"), "n_skus": len(s["skus"]),
                        "sample_sku": s["skus"][0] if s["skus"] else None}
                       for s in p["stores"]],
        })
    if rv:
        it = rv.get("item") or {}
        out["restock"] = {
            "verdict": rv.get("verdict"), "status": rv.get("status"),
            "reason": rv.get("reason"), "note": rv.get("note"),
            "eta": fmt_eta(rv.get("eta")) if rv.get("verdict") in ("eta",) else None,
            "matched_sku": it.get("ext_sku") or it.get("prod_sku"),
            "matched_title": it.get("ext_title") or it.get("prod_title"),
            "orderedAt": str(it.get("orderedAt"))[:10] if it.get("orderedAt") else None,
            "shippedAt": str(it.get("shippedAt"))[:10] if it.get("shippedAt") else None,
        }
    else:
        out["restock"] = None
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Răspuns instant CS la întrebări de stoc (mai e / când revine / nu mai revine).")
    ap.add_argument("--sku", help="SKU exact sau parțial (ex. gt-140)")
    ap.add_argument("--product", help="cuvânt-cheie din titlu (ex. 'aparat foto instant')")
    ap.add_argument("--store", help="filtrează / preferă un magazin (nume sau prefix, ex. GT / Nubra)")
    ap.add_argument("--json", action="store_true", help="ieșire JSON pt automatizare")
    a = ap.parse_args()
    if not a.sku and not a.product:
        print("Dă --sku sau --product. Ex: uv run cs_stock_answer.py --product \"aparat foto\"")
        return

    label = ("SKU=" + a.sku) if a.sku else ("produs=" + a.product)
    stock_rows = fetch_live_stock(sku=a.sku, product=a.product)
    prods = aggregate(stock_rows)

    # restock ETA doar dacă NU avem nimic pe stoc nicăieri
    any_in_stock = any(p["total_qty"] > 0 for p in prods)
    rv = None
    if prods and not any_in_stock:
        skus = [r.get("sku") for r in stock_rows if r.get("sku")]
        titles = list({p["title"] for p in prods})
        items = fetch_restock(skus, titles)
        rv = classify_restock(items)

    if a.json:
        print(json.dumps(build_json(label, prods, rv), ensure_ascii=False, indent=2, default=str))
    else:
        render(label, prods, rv, want_store=a.store)


if __name__ == "__main__":
    main()
