# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_duplicate_orders.py — COMENZI DUBLE: același client plasează 2 comenzi la minute
distanță (degetul a apăsat de două ori / s-a întors și a mai comandat). Le prinzi ÎNAINTE
de expediere -> anulezi dublura. Fiecare dublură expediată = transport dus+retur + refuz COD
aproape sigur (clientul oricum nu plătește de două ori). ~2-3/zi de verificat.

Logică: grupează pe TELEFON normalizat (COALESCE(phone,shippingPhone), ultimele 9 cifre) +
brand, comenzi NECANCELATE, perechi consecutive la <24h una de alta. 3 niveluri de încredere:
  • EXACT    — semnătură line items identică (sku×qty) -> aproape sigur dublu accidental
  • PROBABIL — totalPrice identic + <2h între ele
  • POSIBIL  — <24h, dar valori/produse diferite (poate a vrut să mai adauge ceva)
Sortat după încredere (EXACT primele), apoi după cât de apropiate-s în timp.

  uv run cs_duplicate_orders.py                       # ultimele 24h, toate magazinele
  uv run cs_duplicate_orders.py --hours 48            # fereastră mai largă (creare comenzi)
  uv run cs_duplicate_orders.py --store Esteban       # un singur magazin
  uv run cs_duplicate_orders.py --json                # pt automatizare

READ-ONLY. Nu scrie nimic nicăieri (Postgres metrics, în tranzacție read-only).
"""
import os, sys, json, subprocess, urllib.parse, argparse, datetime
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))

# prefix comandă -> (nume magazin, limbă) pt mesajul de confirmare
PREFIX_LANG = {
    "EST": "ro", "GT": "ro", "NUBRA": "ro", "NUB": "ro", "GEN": "ro", "GRAND": "ro", "GRAN": "ro",
    "BELA": "ro", "CARP": "ro", "COV": "ro", "MAG": "ro", "OFER": "ro", "RED": "ro", "BON": "ro",
    "APR": "ro", "ROSSI": "ro", "CZ": "cz", "PL": "pl", "BONBG": "bg",
}
# mesaj de confirmare gata de trimis clientului, per limbă
MSG = {
    "ro": "Bună {n}! Suntem de la {b}. Vedem că ai plasat două comenzi foarte apropiate ({o1} și {o2}) — pare o dublură din greșeală. Confirmi că vrei o singură comandă? O anulăm pe cealaltă ca să nu plătești transport degeaba. 🙏",
    "cz": "Dobrý den {n}! Tady {b}. Vidíme dvě objednávky těsně po sobě ({o1} a {o2}) — vypadá to jako omylem zdvojené. Potvrdíte, že chcete jen jednu? Druhou zrušíme, ať neplatíte dopravu zbytečně. 🙏",
    "pl": "Cześć {n}! Tu {b}. Widzimy dwa zamówienia tuż po sobie ({o1} i {o2}) — wygląda na przypadkowy duplikat. Potwierdzasz, że chcesz tylko jedno? Drugie anulujemy, żebyś nie płacił/a za dostawę niepotrzebnie. 🙏",
    "bg": "Здравейте {n}! Това е {b}. Виждаме две поръчки една след друга ({o1} и {o2}) — изглежда като случаен дубликат. Потвърждавате ли, че искате само една? Другата ще анулираме, за да не плащате доставка излишно. 🙏",
}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def mconn():
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def lang_for(prefix):
    return PREFIX_LANG.get((prefix or "").upper(), "ro")


# Toată detecția într-un singur SQL (window pe telefon+brand, perechi consecutive).
# Filtrăm pe ultimele ~30 zile ca să nu tragem toată tabela; fereastra de timp e parametrizată.
SQL = """
WITH base AS (
  SELECT o.id, o.name, o."brandId", b.name AS brand,
         o."totalPrice"::float AS total, o."shopifyCreatedAt" AS created,
         COALESCE(o."shippingName",'') AS cust,
         RIGHT(regexp_replace(COALESCE(o.phone, o."shippingPhone",''), '[^0-9]', '', 'g'), 9) AS ph9
  FROM orders o
  JOIN brands b ON b.id = o."brandId"
  WHERE o."cancelledAt" IS NULL
    AND o."shopifyCreatedAt" >= now() - interval '30 days'
    AND length(RIGHT(regexp_replace(COALESCE(o.phone, o."shippingPhone",''), '[^0-9]', '', 'g'), 9)) = 9
    {store_filter}
),
seq AS (
  SELECT *,
    lag(id)      OVER w AS prev_id,
    lag(name)    OVER w AS prev_name,
    lag(total)   OVER w AS prev_total,
    lag(created) OVER w AS prev_created,
    lag(cust)    OVER w AS prev_cust
  FROM base WINDOW w AS (PARTITION BY ph9, "brandId" ORDER BY created)
),
sig AS (
  SELECT li."orderId",
         string_agg(li.sku || 'x' || li.quantity, '|' ORDER BY li.sku, li.quantity) AS s
  FROM order_line_items li
  GROUP BY li."orderId"
)
SELECT
  s.prev_name AS o1, s.name AS o2, s.brand, s.ph9,
  round(EXTRACT(EPOCH FROM (s.created - s.prev_created))/60.0)::int AS mins_apart,
  s.prev_total AS t1, s.total AS t2,
  COALESCE(NULLIF(s.cust,''), s.prev_cust) AS cust,
  (sg1.s IS NOT NULL AND sg1.s = sg2.s) AS same_items
FROM seq s
LEFT JOIN sig sg1 ON sg1."orderId" = s.prev_id
LEFT JOIN sig sg2 ON sg2."orderId" = s.id
WHERE s.prev_id IS NOT NULL
  AND s.created <= s.prev_created + (interval '1 hour' * %s)
ORDER BY mins_apart ASC
"""


def level_of(r):
    """EXACT / PROBABIL / POSIBIL în funcție de semnătură, preț, fereastră."""
    if r["same_items"]:
        return "EXACT"
    if abs((r["t1"] or 0) - (r["t2"] or 0)) < 0.01 and r["mins_apart"] < 120:
        return "PROBABIL"
    return "POSIBIL"


LEVEL_RANK = {"EXACT": 0, "PROBABIL": 1, "POSIBIL": 2}


def main():
    ap = argparse.ArgumentParser(description="Comenzi duble (același client, la minute distanță) de anulat înainte de expediere.")
    ap.add_argument("--hours", type=int, default=24, help="fereastra max între cele două comenzi (default 24)")
    ap.add_argument("--store", default="", help="filtrează un magazin (nume brand, ex. Esteban / George Talent / Nubra)")
    ap.add_argument("--draft", action="store_true", help="afișează mesajul de confirmare gata de trimis")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    store_filter = ""
    params = [float(a.hours)]
    if a.store:
        store_filter = "AND b.name ILIKE %s"
    sql = SQL.format(store_filter=store_filter)
    if a.store:
        # store filter param vine ÎNAINTE de window param (ordinea apariției în SQL)
        params = ["%" + a.store.strip() + "%", float(a.hours)]

    conn = mconn(); cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        cur.execute("SET TRANSACTION READ ONLY")
    except Exception:
        pass
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()

    # adaugă nivel + prefix limbă, sortează pe încredere apoi pe timp
    for r in rows:
        r["level"] = level_of(r)
        import re
        m = re.match(r"^([A-Za-z]+)", r["o1"] or "")
        r["lang"] = lang_for(m.group(1) if m else "")
        r["t1"] = round(float(r["t1"] or 0), 2)
        r["t2"] = round(float(r["t2"] or 0), 2)
    rows.sort(key=lambda r: (LEVEL_RANK[r["level"]], r["mins_apart"]))
    total = len(rows)
    n_exact = sum(1 for r in rows if r["level"] == "EXACT")
    n_prob = sum(1 for r in rows if r["level"] == "PROBABIL")
    n_pos = sum(1 for r in rows if r["level"] == "POSIBIL")
    rows = rows[:a.limit]

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return

    title = "=== COMENZI DUBLE (de anulat ÎNAINTE de expediere)"
    if a.store:
        title += " | " + a.store
    title += " | fereastră %dh ===" % a.hours
    print(title)
    capped = ("  (afișez primele %d)" % a.limit) if total > a.limit else ""
    print("Perechi suspecte în ultimele 30 zile: %d  (EXACT %d | PROBABIL %d | POSIBIL %d)%s\n"
          % (total, n_exact, n_prob, n_pos, capped))
    if not rows:
        print("Nicio dublură detectată. 👍")
        return
    print("Fiecare dublură expediată = transport dus+retur + refuz COD aproape sigur. Verifică EXACT/PROBABIL primele.\n")
    print("%-9s %-12s+%-12s %5s %-13s %9s %9s %s"
          % ("nivel", "comanda 1", "comanda 2", "min", "magazin", "val 1", "val 2", "client"))
    print("-" * 100)
    last_level = None
    for r in rows:
        if r["level"] != last_level:
            print("·" * 100)
            last_level = r["level"]
        print("%-9s %-12s+%-12s %5d %-13s %9s %9s %s"
              % (r["level"], r["o1"], r["o2"], r["mins_apart"], (r["brand"] or "")[:13].strip(),
                 "{:,.2f}".format(r["t1"]), "{:,.2f}".format(r["t2"]),
                 ("%s · tel ***%s" % ((r["cust"] or "—")[:20], r["ph9"][-4:]))))
        if a.draft:
            nm = (r["cust"] or "").split()[0] if r["cust"] else ""
            print("   → " + MSG.get(r["lang"], MSG["ro"]).format(
                n=nm, b=(r["brand"] or "").strip(), o1=r["o1"], o2=r["o2"]))
    print("\nDe verificat manual și anulat dublura înainte de a plăti transportul. EXACT = aproape sigur greșeală.")


if __name__ == "__main__":
    main()
