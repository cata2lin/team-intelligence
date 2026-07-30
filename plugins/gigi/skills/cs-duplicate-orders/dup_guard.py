# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "requests"]
# ///
"""dup_guard.py — GARDA de comenzi duble: detectează + MARCHEAZĂ (tag `duplicata`) + ia ACȚIUNE.

Extinde detecția din cs_duplicate_orders.py cu decizia „care rămâne / care se anulează",
după regulile confirmate. Fereastră: ultimele 7 zile, același telefon + brand.

ARBORELE DE DECIZIE (doar pe duplicate EXACTE — line-items identice):
  1. PLATA bate tot: comanda PLĂTITĂ (card) nu se anulează niciodată.
       card nou + cash vechi → anulez cash-ul.  card vechi + cash nou → anulez cash-ul (nou).
       ambele plătite (card) → NU anulez, raport CS (posibil dublă încasare).
  2. AWB (dacă ambele cash):
       cea veche are AWB → anulez cea NOUĂ (cea veche pleacă).
         excepție: adresă DIFERITĂ + AWB încă anulabil → anulez AWB vechi (xConnector) + comanda veche; cea nouă pe HOLD.
         dacă AWB deja plecat (scanat) → nu se poate anula → anulez cea nouă (flag CS dacă adresă greșită).
  3. Niciun AWB:
       adresă DIFERITĂ → anulez cea VECHE, cea NOUĂ pe HOLD (CS verifică adresa).
       aceeași adresă → anulez cea NOUĂ (dublu accidental).
Tag `duplicata` = ACELAȘI tag cu flow-ul Shopify care BLOCHEAZĂ crearea AWB (înainte de hold/direct).
  Îl PUN pe comenzile care NU rămân (anulată / HOLD / CS) și îl SCOT de pe cea PĂSTRATĂ — altfel
  cea păstrată rămâne blocată și nu i se mai face AWB niciodată. PROBABIL/POSIBIL → doar tag + raport CS.

  uv run dup_guard.py                     # DRY-RUN, toate magazinele, ultimele 7 zile
  uv run dup_guard.py --store Esteban     # un magazin
  uv run dup_guard.py --days 7            # fereastra
  uv run dup_guard.py --apply             # EXECUTĂ (tag + anulare + hold + awb-void)

DRY-RUN implicit. Citirile-s read-only. Anularea/hold/awb-void doar cu --apply.
"""
import os, sys, re, json, argparse, subprocess, datetime, urllib.parse as up
import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
CANCELLABLE = {"waiting_for_courier", "fulfilled"}          # AWB făcut, încă opribil
SHIPPED = {"in_transit","delivered","back_to_sender","unsuccessful_delivery",
           "returning_to_sender","refused","customer_pickup","lost_in_transit"}  # plecat, nu se anulează

def secret(k):
    v = os.environ.get(k)
    if v: return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv","run",kb,"secret-get",k], capture_output=True, text=True).stdout.strip()

def conn(dsn):
    p = up.urlsplit(dsn)
    return psycopg2.connect(up.urlunsplit((p.scheme,p.netloc,p.path,"","")))

def addr_differs(old, new):
    """Adresă diferită = ZIP diferit (semnalul de încredere — textul străzii se scrie în 10 feluri).
    Fără ZIP pe una → cade pe stradă+oraș normalizat, dar CONSERVATOR (necunoscut = aceeași)."""
    z1 = re.sub(r"\D", "", str(old.get("zip") or "")); z2 = re.sub(r"\D", "", str(new.get("zip") or ""))
    if z1 and z2:
        return z1 != z2
    def n(o): return re.sub(r"[^a-z0-9]", "", (str(o.get("a1") or "") + str(o.get("city") or "")).lower())
    n1, n2 = n(old), n(new)
    if not n1 or not n2:
        return False
    return n1 != n2

def is_paid(o):
    return (o.get("fin") or "").upper() == "PAID"

# ── execuția acțiunilor (Shopify tag + xConnector cancel/hold) ───────────
XC = os.path.join(HERE, "..", "xconnector", "xconnector.py")
STATE = os.path.join(HERE, ".dup_guard_handled")   # perechi deja procesate (idempotență peste lag-ul warehouse)
def load_handled():
    try: return set(open(STATE, encoding="utf-8").read().split())
    except FileNotFoundError: return set()
def mark_handled(key):
    with open(STATE, "a", encoding="utf-8") as f: f.write(key + "\n")
_STORES = None
def stores_map():
    import csv, io
    m = {}
    for row in csv.DictReader(io.StringIO(secret("SHOPIFY_STORES_CSV"))):
        m[(row.get("prefix") or "").strip().upper()] = ((row.get("shop") or "").strip(), (row.get("token") or "").strip())
    return m

TAG = "duplicata"   # ACELAȘI tag ca flow-ul Shopify (blochează crearea AWB). NU „duplicate".
def _tags_mutate(name, gid, add):
    """tagsAdd (add=True) sau tagsRemove (add=False) pentru tag-ul `duplicata`.
    Pe cea PĂSTRATĂ scot tag-ul → i se poate face AWB; pe restul îl pun (blochează AWB / vizibil CS)."""
    global _STORES
    if _STORES is None: _STORES = stores_map()
    import requests
    verb = "tagsAdd" if add else "tagsRemove"
    lbl = "tag+" if add else "tag-"
    m = re.match(r"^[A-Za-z]+", name or "")
    st = _STORES.get(m.group(0).upper()) if m else None
    if not st or not gid: return f"{lbl} skip ({name}: prefix/gid necunoscut)"
    dom, tok = st
    q = f"mutation($id:ID!,$t:[String!]!){{{verb}(id:$id,tags:$t){{userErrors{{message}}}}}}"
    try:
        r = requests.post(f"https://{dom}/admin/api/2024-10/graphql.json",
                          headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
                          json={"query": q, "variables": {"id": gid, "tags": [TAG]}}, timeout=30)
        err = r.json().get("data", {}).get(verb, {}).get("userErrors")
        return f"{lbl} ✓" if not err else f"{lbl} ✗ {err}"
    except Exception as e:
        return f"{lbl} ✗ {str(e)[:60]}"

def tag_order(name, gid):   return _tags_mutate(name, gid, add=True)
def untag_order(name, gid): return _tags_mutate(name, gid, add=False)

def xc(cmd, name, apply):
    """cheamă xconnector.py <cmd> --order NAME [--apply]. order-cancel voidează AWB + are garda 'plecată'.
    O eroare/timeout la o comandă NU trebuie să crape rularea — se loghează și se trece mai departe."""
    args = [os.environ.get("UV_BIN", "uv"), "run", "--no-project", XC, cmd, "--order", name]
    if apply: args.append("--apply")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=180)
        out = (r.stdout + r.stderr).strip().replace("\n", " ")
        return f"{cmd} {'✓' if r.returncode == 0 else '✗'} {out[-160:]}"
    except subprocess.TimeoutExpired:
        return f"{cmd} ✗ TIMEOUT (>180s) — sar peste, verific manual {name}"
    except Exception as e:
        return f"{cmd} ✗ {str(e)[:80]}"

# ── detecție (warehouse) ────────────────────────────────────────────────
Q = """
WITH sig AS (
  SELECT li."orderId", string_agg(li.sku||'x'||li.quantity,'|' ORDER BY li.sku,li.quantity) AS s
  FROM order_line_items li GROUP BY li."orderId"
)
SELECT o.name, o."orderNumber" ordnum, o."shopifyGid" gid, o."brandId" bid, b.name brand,
  RIGHT(regexp_replace(COALESCE(o.phone,o."shippingPhone",''),'[^0-9]','','g'),9) ph9,
  o."shopifyCreatedAt" created, o."financialStatus" fin,
  COALESCE(o."paymentGatewayNames"::text,'') gw, o."totalPrice"::float total,
  COALESCE(o."shippingAddress1",'') a1, COALESCE(o."shippingCity",'') city,
  COALESCE(o."shippingZip",'') zip, COALESCE(o."shippingName",'') cust, sg.s sig
FROM orders o JOIN brands b ON b.id=o."brandId"
LEFT JOIN sig sg ON sg."orderId"=o.id
WHERE o."cancelledAt" IS NULL
  AND o."shopifyCreatedAt" >= now() - (interval '1 day' * %s)
  AND length(RIGHT(regexp_replace(COALESCE(o.phone,o."shippingPhone",''),'[^0-9]','','g'),9))=9
  {store}
ORDER BY ph9, o."brandId", o."shopifyCreatedAt"
"""

def fetch_orders(days, store):
    dsn = secret("DATABASE_URL_METRICS"); cn = conn(dsn); cn.set_session(readonly=True); c = cn.cursor()
    sf = "AND b.name ILIKE %s" if store else ""
    params = ([f"%{store}%"] if store else []) + [float(days)]
    c.execute(Q.format(store=sf), params)
    cols = [d[0] for d in c.description]
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    cn.close()
    return rows

def awb_status(order_numbers):
    """order_number -> aggregated_status (+ awb_count) din AWBprint."""
    if not order_numbers: return {}
    dsn = secret("DATABASE_URL_AWBPRINT"); cn = conn(dsn); cn.set_session(readonly=True); c = cn.cursor()
    # AWBprint order_number poate fi cu sau fără prefix — încerc pe ambele forme
    keys = set()
    for n in order_numbers:
        keys.add(str(n)); keys.add(re.sub(r"^[A-Za-z]+","",str(n)))
    c.execute("""select order_number, aggregated_status, awb_count from orders
                 where order_number = ANY(%s)""", (list(keys),))
    out = {}
    for onum, st, cnt in c.fetchall():
        out[str(onum)] = {"status": st, "awb_count": cnt or 0}
    cn.close()
    return out

def enrich_awb(o, awbmap):
    for key in (str(o.get("name") or ""), str(o.get("ordnum") or ""), re.sub(r"^[A-Za-z]+","",str(o.get("name") or ""))):
        a = awbmap.get(key)
        if a: break
    else:
        a = {"status": None, "awb_count": 0}
    st = a["status"]
    o["has_awb"] = (a["awb_count"] or 0) > 0 or st in (CANCELLABLE|SHIPPED)
    o["awb_cancellable"] = st in CANCELLABLE
    o["awb_shipped"] = st in SHIPPED
    o["stoppable"] = st not in SHIPPED          # se mai poate opri? (nu e deja la curier/livrată)
    o["awb_status"] = st

# ── decizia ─────────────────────────────────────────────────────────────
def mk_cancel(target, keep, reason, cs=False):
    d = {"cancel": target["name"], "keep": keep["name"], "reason": reason}
    if cs: d["cs"] = True
    if target["awb_cancellable"]:            # are AWB dar încă anulabil → îl anulez întâi prin xConnector
        d["cancel_awb"] = target["name"]
    return d

def decide(old, new):
    """întoarce dict: {cancel, hold, cancel_awb, keep, cs, toolate, reason}. Anulez DOAR comenzi opribile."""
    o_stop, n_stop = old["stoppable"], new["stoppable"]
    addr_diff = addr_differs(old, new)

    # ambele deja plecate/livrate → prea târziu de anulat
    if not o_stop and not n_stop:
        return {"cs": True, "toolate": True, "reason": "ambele deja plecate/livrate — prea târziu de anulat; doar tag + verifică CS"}

    # 1) PLATA bate tot — protejez comanda plătită (card), anulez cash-ul dacă e opribil
    if is_paid(old) and is_paid(new):
        return {"cs": True, "reason": "ambele plătite (card) — nu anulez automat, verifică CS (posibilă dublă încasare)"}
    if is_paid(old) != is_paid(new):
        paid, unpaid = (old, new) if is_paid(old) else (new, old)
        which = "veche" if paid is old else "nouă"
        if unpaid["stoppable"]:
            return mk_cancel(unpaid, keep=paid, reason=f"cardul (comanda {which}) rămâne, cash-ul anulat")
        return {"cs": True, "reason": "cash-ul (de anulat) deja plecat — cardul rămâne, notează CS"}

    # 2) ambele cash → AWB-ul celei VECHI
    if old["has_awb"]:
        if addr_diff and old["awb_cancellable"]:
            return {"cancel_awb": old["name"], "cancel": old["name"], "hold": new["name"],
                    "reason": "adresă diferită + AWB vechi încă anulabil → void AWB + anulez cea veche, cea nouă pe HOLD (CS verifică adresa)"}
        if n_stop:            # cea veche pleacă → anulez cea nouă
            cs = addr_diff and old["awb_shipped"]
            r = "cea veche are AWB" + (" deja plecat" if old["awb_shipped"] else "") + " → anulez cea nouă"
            if cs: r += "  ⚠️ cea veche merge la adresă posibil greșită — flag CS"
            return mk_cancel(new, keep=old, cs=cs, reason=r)
        return {"cs": True, "reason": "cea nouă deja plecată + cea veche are AWB — prea târziu, notează CS"}

    # 3) cea veche fără AWB; dacă cea nouă are AWB → anulez cea veche (dacă opribilă)
    if new["has_awb"]:
        if o_stop:
            return mk_cancel(old, keep=new, reason="cea nouă are AWB, cea veche nu → anulez cea veche")
        return {"cs": True, "reason": "cea veche deja plecată — notează CS"}

    # 4) niciun AWB → adresa
    if addr_diff:
        if o_stop:
            return {"cancel": old["name"], "hold": new["name"], "reason": "adresă diferită, niciun AWB → anulez cea veche, cea nouă pe HOLD (CS verifică adresa)"}
        return mk_cancel(new, keep=old, reason="cea veche neopribilă → anulez cea nouă")
    if n_stop:
        return mk_cancel(new, keep=old, reason="aceeași adresă, niciun AWB → anulez cea nouă (dublu accidental)")
    if o_stop:
        return mk_cancel(old, keep=new, reason="cea nouă neopribilă → anulez cea veche")
    return {"cs": True, "toolate": True, "reason": "prea târziu — notează CS"}

def level_of(old, new):
    if old["sig"] and old["sig"] == new["sig"]: return "EXACT"
    if abs((old["total"] or 0)-(new["total"] or 0)) < 0.01: return "PROBABIL"
    return "POSIBIL"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--store", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all", action="store_true", help="arată și perechile prea-târzii (istoric, deja plecate)")
    ap.add_argument("--limit", type=int, default=0, help="procesează doar primele N perechi (test controlat)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    global HANDLED
    HANDLED = load_handled()

    rows = fetch_orders(a.days, a.store)
    # grupare pe telefon+brand, perechi consecutive
    from itertools import groupby
    rows.sort(key=lambda r: (r["ph9"], r["bid"], r["created"]))
    awbmap = awb_status([r["name"] for r in rows] + [r["ordnum"] for r in rows])
    pairs = []
    for _, grp in groupby(rows, key=lambda r: (r["ph9"], r["bid"])):
        g = list(grp)
        for i in range(1, len(g)):
            old, new = g[i-1], g[i]
            lvl = level_of(old, new)
            if lvl == "POSIBIL":  # prea slab — ignor (nici tag)
                continue
            enrich_awb(old, awbmap); enrich_awb(new, awbmap)
            mins = round((new["created"]-old["created"]).total_seconds()/60)
            dec = decide(old, new) if lvl == "EXACT" else {"cs": True, "reason": "PROBABIL — doar tag + raport CS (fără anulare automată)"}
            pairs.append({"level": lvl, "old": old["name"], "new": new["name"], "brand": old["brand"],
                          "old_gid": old["gid"], "new_gid": new["gid"],
                          "mins": mins, "cust": old["cust"] or new["cust"], "ph9": old["ph9"],
                          "old_pay": "card" if is_paid(old) else "cash", "new_pay": "card" if is_paid(new) else "cash",
                          "old_awb": old.get("awb_status"), "new_awb": new.get("awb_status"),
                          "decision": dec})
    pairs.sort(key=lambda p: (0 if p["level"]=="EXACT" else 1, p["mins"]))
    toolate = [p for p in pairs if p["decision"].get("toolate")]
    active  = pairs if a.all else [p for p in pairs if not p["decision"].get("toolate")]
    if a.limit:
        active = active[:a.limit]

    if a.json:
        print(json.dumps(active, ensure_ascii=False, indent=2, default=str)); return

    mode = "APLIC" if a.apply else "DRY-RUN"
    print(f"=== GARDĂ COMENZI DUBLE ({mode}) — ultimele {a.days} zile" + (f" · {a.store}" if a.store else " · toate") + " ===")
    n_cancel = sum(1 for p in active if p["decision"].get("cancel"))
    print(f"{len(active)} perechi ACȚIONABILE (din care {n_cancel} cu anulare) · {len(toolate)} prea-târzii ascunse (istoric; --all le arată)\n")
    for p in active:
        d = p["decision"]
        act = ("→ CS" if d.get("cs") and "cancel" not in d else
               f"ANULEZ {d.get('cancel')}" + (f" + HOLD {d.get('hold')}" if d.get("hold") else "") + (f" + void AWB {d.get('cancel_awb')}" if d.get("cancel_awb") else ""))
        keep = d.get("keep")
        tagmsg = f"tag duplicata (SCOT de pe {keep} → poate face AWB)" if keep else "tag duplicata pe ambele"
        print(f"[{p['level']:8}] {p['old']}({p['old_pay']}/{p['old_awb'] or '-'}) + {p['new']}({p['new_pay']}/{p['new_awb'] or '-'})  {p['mins']}min  {p['brand'][:12]}")
        print(f"           {tagmsg} · {act}")
        print(f"           motiv: {d['reason']}")
        if a.apply:
            key = f"{p['old']}+{p['new']}"
            if key in HANDLED:
                print("           (deja procesat — sar)")
            else:
                # cea PĂSTRATĂ: scot `duplicata` (deblochez AWB-ul ei). restul: pun `duplicata`.
                log = [untag_order(nm, gid) if nm == keep else tag_order(nm, gid)
                       for nm, gid in ((p["old"], p["old_gid"]), (p["new"], p["new_gid"]))]
                ok = True
                if d.get("cancel"):                   # order-cancel voidează AWB + are garda 'plecată'
                    r = xc("order-cancel", d["cancel"], apply=True); log.append(r); ok = ok and " ✓" in r
                if d.get("hold"):
                    r = xc("awb-hold", d["hold"], apply=True); log.append(r); ok = ok and " ✓" in r
                print("           execuție: " + " · ".join(log))
                if ok:                                # marchez DOAR dacă acțiunea a reușit (altfel reîncearcă)
                    mark_handled(key); HANDLED.add(key)
    if not a.apply:
        print("\nDRY-RUN — nimic nu s-a modificat. Verifică deciziile, apoi --apply.")

if __name__ == "__main__":
    main()
