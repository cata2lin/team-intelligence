# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""tom_po.py — ce s-a comandat / anulat / expediat in TOM Arona (WMS-ul de aprovizionare).

TOM = ce s-a CERUT la furnizor (purchase_orders + purchase_order_items).
NU e sursa de adevar pentru "ce vine si cand" — aia e in fisierul de containere KDocs
(vezi SKILL.md). Foloseste asta ca sa raspunzi la "a mai fost comandat X?".

  uv run tom_po.py search --q pijam            # toate liniile care contin "pijam" (orice PO)
  uv run tom_po.py search --q satin --open     # doar liniile NEanulate
  uv run tom_po.py po --tom TOM-014            # detaliul unui PO
  uv run tom_po.py recent --limit 20           # ultimele PO-uri, cu status + nr linii
  uv run tom_po.py shipments                   # loturile (shipments) si ce contin

Coloanele in TOM sunt camelCase => TREBUIE ghilimele in SQL ("poId", "externalSku", ...).
FK-ul e purchase_order_items."poId" -> purchase_orders.id (NU purchase_order_id).
"""
import os, sys, argparse, subprocess
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import psycopg2


def kb(key):
    v = os.environ.get(key)
    if v:
        return v
    p = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", p, "secret-get", key],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def clean(url):
    p = urlsplit(url)
    OK = {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout"}
    if p.query:
        q = urlencode([(k, v) for k, v in parse_qsl(p.query, True) if k.lower() in OK])
        url = urlunsplit((p.scheme, p.netloc, p.path, q, p.fragment))
    return url


def conn():
    dsn = kb("DATABASE_URL_TOM")
    if not dsn:
        sys.exit("Lipseste DATABASE_URL_TOM (kb.py secret-get DATABASE_URL_TOM).")
    return psycopg2.connect(clean(dsn), connect_timeout=20)


def rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def table(data, cols):
    if not data:
        print("  (nimic)")
        return
    w = {c: max(len(c), max(len(str(d.get(c) if d.get(c) is not None else "")) for d in data)) for c in cols}
    print("  " + "  ".join(c.ljust(w[c]) for c in cols))
    print("  " + "  ".join("-" * w[c] for c in cols))
    for d in data:
        print("  " + "  ".join(str(d.get(c) if d.get(c) is not None else "").ljust(w[c]) for c in cols))


def cmd_search(cur, a):
    where = """(poi."externalTitle" ILIKE %(q)s OR poi."externalSku" ILIKE %(q)s
                OR poi."externalBarcode" ILIKE %(q)s)"""
    if a.open:
        where += " AND poi.status <> 'CANCELLED'"
    cur.execute(f"""
        SELECT po."tomNumber", po.status AS po_status, po."createdAt"::date AS created,
               poi."externalSku" AS sku, poi."externalTitle" AS title,
               poi."requestedQty" AS req, poi."orderedQty" AS ord,
               poi."receivedQty" AS recv, poi."shippedQty" AS shipped,
               poi.status AS item_status, poi."cancelNote" AS cancel_note
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.id = poi."poId"
        WHERE {where}
        ORDER BY po."createdAt" DESC, poi."externalSku"
    """, {"q": f"%{a.q}%"})
    d = rows(cur)
    print(f"\n=== TOM: linii care contin '{a.q}' ({len(d)}) ===")
    table(d, ["tomNumber", "created", "sku", "title", "req", "ord", "recv", "shipped",
              "item_status", "cancel_note"])
    live = [x for x in d if x["item_status"] != "CANCELLED"]
    print(f"\n  ACTIVE (necanulate): {len(live)} linii, {sum(x['req'] or 0 for x in live)} buc cerute")
    print(f"  ANULATE            : {len(d) - len(live)} linii")
    if d and not live:
        print("  => In TOM NU mai e comandat nimic activ. Verifica fisierul de containere (KDocs).")


def cmd_po(cur, a):
    cur.execute("""
        SELECT poi."externalSku" AS sku, poi."externalTitle" AS title,
               poi."requestedQty" AS req, poi."orderedQty" AS ord,
               poi."receivedQty" AS recv, poi."shippedQty" AS shipped,
               poi.status, poi."cancelNote" AS cancel_note
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.id = poi."poId"
        WHERE po."tomNumber" = %s
        ORDER BY poi."externalSku"
    """, (a.tom,))
    print(f"\n=== {a.tom} ===")
    table(rows(cur), ["sku", "title", "req", "ord", "recv", "shipped", "status", "cancel_note"])


def cmd_recent(cur, a):
    cur.execute("""
        SELECT po."tomNumber", po.title, po.status, po."createdAt"::date AS created,
               count(poi.id) AS lines,
               count(poi.id) FILTER (WHERE poi.status = 'CANCELLED') AS cancelled,
               coalesce(sum(poi."requestedQty"), 0) AS req_qty
        FROM purchase_orders po
        LEFT JOIN purchase_order_items poi ON poi."poId" = po.id
        GROUP BY po.id, po."tomNumber", po.title, po.status, po."createdAt"
        ORDER BY po."createdAt" DESC
        LIMIT %s
    """, (a.limit,))
    print(f"\n=== Ultimele {a.limit} PO-uri TOM ===")
    table(rows(cur), ["tomNumber", "created", "title", "status", "lines", "cancelled", "req_qty"])


def cmd_containers(cur, a):
    """shipments = grupuri de containere (ex 'Container 43-44-45'), cu ce PO-items contin.
    ATENTIE: contin DOAR liniile care au ramas atasate; ce s-a anulat in TOP (mutat pe tabele
    de marimi) NU apare aici, desi marfa exista fizic in container. Vezi KDocs."""
    if a.name:
        cur.execute("""
            SELECT s.name AS container, po."tomNumber", poi."externalSku" AS sku,
                   poi."externalTitle" AS title, poi."requestedQty" AS req, poi.status
            FROM shipments s
            JOIN shipment_lines sl ON sl."shipmentId" = s.id
            JOIN purchase_order_items poi ON poi.id = sl."poItemId"
            JOIN purchase_orders po ON po.id = poi."poId"
            WHERE s.name ILIKE %s
            ORDER BY poi."externalTitle"
        """, (f"%{a.name}%",))
        print(f"\n=== Continutul containerului '{a.name}' (dupa TOM) ===")
        table(rows(cur), ["container", "tomNumber", "sku", "title", "req", "status"])
    else:
        cur.execute("""
            SELECT s.name AS container, s.status, count(*) AS lines,
                   sum(poi."requestedQty") AS req_qty,
                   string_agg(DISTINCT po."tomNumber", ',') AS pos
            FROM shipments s
            JOIN shipment_lines sl ON sl."shipmentId" = s.id
            JOIN purchase_order_items poi ON poi.id = sl."poItemId"
            JOIN purchase_orders po ON po.id = poi."poId"
            GROUP BY s.id, s.name, s.status, s."createdAt"
            ORDER BY s."createdAt" DESC
        """)
        print("\n=== Grupuri de containere (shipments TOM) ===")
        table(rows(cur), ["container", "status", "lines", "req_qty", "pos"])
    print("\n  ATENTIE: liniile ANULATE in TOM nu sunt atasate niciunui container aici,")
    print("  desi marfa POATE fi produsa si incarcata. Confirma in fisierul KDocs (vezi SKILL.md).")


def cmd_latest(cur, a):
    """Verificare de PROSPETIME: cel mai nou grup de containere din TOM + cel mai mare NUMAR de
    container vazut. Ruleaza asta INAINTE sa raspunzi la o intrebare despre containere — daca
    numarul e peste ce stiai, a sosit ceva nou (si verifica si o foaie noua in KDocs)."""
    import re
    cur.execute("""
        SELECT s.name, s."createdAt"::date AS created
        FROM shipments s ORDER BY s."createdAt" DESC
    """)
    data = rows(cur)
    maxnum, newest = 0, None
    for d in data:
        for n in re.findall(r"\d+", d.get("name") or ""):
            maxnum = max(maxnum, int(n))
    newest = data[0] if data else None
    print("\n=== PROSPETIME containere (TOM) ===")
    if newest:
        print(f"  Cel mai nou grup adaugat : {newest['name']}  (creat {newest['created']})")
    print(f"  Cel mai mare nr. container : #{maxnum}")
    print("  → Daca #-ul asta e peste ce stiai, a sosit ceva nou. Verifica si o FOAIE noua in KDocs")
    print("    (window.__sheets()) si STATUSUL produselor comandate (tom_po.py search --q <produs>).")


def cmd_shipments(cur, a):
    cur.execute("""
        SELECT s.code, s.name, s.status, s.carrier, s."trackingNumber" AS tracking,
               s."departedAt"::date AS departed, s."arrivedAt"::date AS arrived,
               count(sl.id) AS lines
        FROM shipments s
        LEFT JOIN shipment_lines sl ON sl."shipmentId" = s.id
        GROUP BY s.id, s.code, s.name, s.status, s.carrier, s."trackingNumber",
                 s."departedAt", s."arrivedAt", s."createdAt"
        ORDER BY s."createdAt" DESC
    """)
    print("\n=== Shipments (loturi) in TOM ===")
    table(rows(cur), ["code", "name", "status", "carrier", "tracking", "departed", "arrived", "lines"])
    print("\n  ATENTIE: daca toate sunt DRAFT / fara date => TOM nu urmareste containerele.")
    print("  Ce vine si cand se citeste din fisierul KDocs de containere (vezi SKILL.md).")


def main():
    ap = argparse.ArgumentParser(description="TOM Arona — ce s-a comandat/anulat/expediat")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="cauta linii dupa titlu/SKU/barcode")
    s.add_argument("--q", required=True)
    s.add_argument("--open", action="store_true", help="doar liniile NEanulate")

    p = sub.add_parser("po", help="detaliul unui PO")
    p.add_argument("--tom", required=True, help="ex TOM-014")

    r = sub.add_parser("recent", help="ultimele PO-uri")
    r.add_argument("--limit", type=int, default=20)

    k = sub.add_parser("containers", help="grupuri de containere (shipments) + ce contin")
    k.add_argument("--name", help="ex 43  (filtreaza dupa numele shipment-ului)")

    sub.add_parser("shipments", help="loturile din TOM, brut")

    sub.add_parser("latest", help="PROSPETIME: cel mai nou/mare container din TOM (ruleaza intai)")

    a = ap.parse_args()
    with conn() as c, c.cursor() as cur:
        {"search": cmd_search, "po": cmd_po, "recent": cmd_recent,
         "containers": cmd_containers, "shipments": cmd_shipments, "latest": cmd_latest}[a.cmd](cur, a)


if __name__ == "__main__":
    main()
