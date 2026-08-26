# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "paramiko>=3.0"]
# ///
"""
cs_ghost_shipments.py — COLETE FANTOMĂ: eticheta a fost printată (AWB emis) dar curierul
nu l-a scanat NICIODATĂ la ridicare -> clientul a primit mail Shopify că s-a expediat, dar
coletul n-a plecat din depozit. Cele mai furioase tichete WISMO ("scrie expediat de X zile,
unde e?!"). Acoperă gaura PRE-PICKUP pe care cs-proactive-delays (doar in-tranzit) o ratează.

Două semnale (DOAR profitability.db / profit_orders, citit prin SSH):
  (1) FANTOMĂ: shopify_delivery_status='LABEL_PRINTED' AND status_category='Netrimisa'
      AND created_at mai vechi de N zile (default 3). AWB-ul există (DPD) cu courier_status
      'Shipment data received' = înregistrat dar nescanat la ridicare. Coletul stă în depozit.
  (2) FĂRĂ TRACKING: status_category='Lipsa awb' = marcat expediat/FULFILLED fără AWB deloc.
      Clientul are mail de expediere dar n-are ce urmări.

Contact (nume/telefon/oraș) din metrics.orders. Sortare: vechime desc, apoi valoare desc.
Read-only total — nu scrie nimic în Postgres / Shopify / Richpanel.

  uv run cs_ghost_shipments.py                 # fantome >3 zile, toate magazinele
  uv run cs_ghost_shipments.py --days 5        # prag vechime 5 zile
  uv run cs_ghost_shipments.py --store Esteban # un singur magazin
  uv run cs_ghost_shipments.py --json          # pt automatizare
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"

# core/scripts in orice layout de instalare (clona repo, marketplace, plugin-cache
# core/<commit>/scripts). GARDA: iteram parents (fara index fix => fara IndexError) si
# preferam core-ul din ACELASI commit ca skill-ul, ca sa nu legam cod nou de helper vechi.
def _core_scripts(need="arona_ssh.py"):
    from pathlib import Path
    import os as _os
    h = Path(__file__).resolve()
    c = [Path(_os.environ["ARONA_CORE_SCRIPTS"])] if _os.environ.get("ARONA_CORE_SCRIPTS") else []
    for up in h.parents:
        c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
             (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
    ok = [x for x in c if (x / need).exists()]
    return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)


def _arona_ssh():
    """Helper SSH PARTAJAT (core/scripts/arona_ssh.py): CHEIE intai, apoi ssh-agent, parola
    doar ca ultim resort — VPS-ul de profit accepta doar `publickey`."""
    import sys as _sys
    cs = _core_scripts()
    if cs is None:
        _sys.exit("core/scripts/arona_ssh.py negasit — actualizeaza plugin-urile echipei "
                  "sau seteaza ARONA_CORE_SCRIPTS=/cale/spre/plugins/core/scripts")
    if str(cs) not in _sys.path:
        _sys.path.insert(0, str(cs))
    import arona_ssh
    return arona_ssh


def _vps_run(remote_cmd, timeout=180):
    """Ruleaza o comanda pe VPS-ul de profit. Aceeasi forma de raspuns ca inainte
    (.stdout/.stderr/.returncode)."""
    import sys as _sys
    ssh = _arona_ssh()
    try:
        return ssh.vps_run(remote_cmd, timeout=timeout)
    except ssh.SSHAuthError as e:
        _sys.exit(str(e))
HERE = os.path.dirname(os.path.abspath(__file__))

# prefix din order_name -> nume magazin afișat
PREFIX = {
    "EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento",
    "GRAN": "Grandia", "GRAND": "Grandia", "BELA": "Belasil", "CARP": "Carpetto",
    "COV": "Covoria", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune",
    "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL",
    "APR": "Apreciat", "ROSSI": "Rossi Nails",
}

# acțiune sugerată per semnal + vechime
def suggested_action(flag, age):
    if flag == "pierdut":
        # Coletul NU e în depozit — curierul l-a pierdut pe drum. Alt flux complet:
        # reclamație la curier pentru recuperarea valorii, apoi refund/re-expediere.
        return "RECLAMAȚIE la curier (colet pierdut în tranzit) + refund/re-expediere client"
    if flag == "lipsa_awb":
        return "Generează AWB ACUM (marcat expediat fără tracking) + mesaj proactiv"
    if age >= 7:
        return "URGENT: verifică depozit, re-expediere/refund + scuze proactive"
    if age >= 5:
        return "Verifică depozit + re-expediere; mesaj proactiv clientului"
    return "Verifică depozit (eticheta nescanata la curier) + mesaj proactiv"


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def fetch_profit(cut, prefixes):
    """Citește cele două semnale din profit_orders (SSH). cut = data prag YYYY-MM-DD (inclusiv)."""
    pf = ""
    if prefixes:
        pf = " AND prefix IN (" + ",".join(repr(p) for p in prefixes) + ")"
    py = (
        "import sqlite3,json;c=sqlite3.connect('data/profitability.db');cut=" + repr(cut) + ";"
        "cols=['order_name','prefix','created_at','revenue','currency','awb','courier_status','status_category','shopify_delivery_status'];"
        "q1=\"SELECT \"+','.join(cols)+\" FROM profit_orders WHERE shopify_delivery_status='LABEL_PRINTED' "
        "AND status_category='Netrimisa' AND substr(created_at,1,10) <= ?" + pf + " LIMIT 5000\";"
        "q2=\"SELECT \"+','.join(cols)+\" FROM profit_orders WHERE status_category='Lipsa awb'" + pf + " LIMIT 5000\";"
        "g=[dict(zip(cols,r)) for r in c.execute(q1,(cut,))];"
        "n=[dict(zip(cols,r)) for r in c.execute(q2)];"
        "print(json.dumps({'ghost':g,'noawb':n}))"
    )
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)
    out = _vps_run(cmd).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"ghost": [], "noawb": []}


def fetch_contacts(order_names):
    """Nume/telefon/oraș + TAGS din metrics.orders pentru lista de comenzi.

    `tags` de AICI e prima sursă (acoperire completă până în 2025), DAR nu e suficientă:
    `metrics.orders` NU conține toate magazinele — MagDeal lipsește complet din warehouse.
    De aceea `fetch_tags_awbprint()` completează, iar ce rămâne fără tag înainte de
    TAGS_RELIABLE_FROM se marchează NEVERIFICABIL, nu „real".
    """
    info = {}
    if not order_names:
        return info
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = conn.cursor()
    nn = list(order_names)
    for i in range(0, len(nn), 800):
        ch = nn[i:i + 800]; ph = ",".join(["%s"] * len(ch))
        cur.execute('SELECT name,"shippingName",COALESCE("shippingPhone",phone),"shippingCity",tags '
                    'FROM orders WHERE name IN (' + ph + ')', ch)
        for r in cur.fetchall():
            info[r[0]] = {"name": r[1], "phone": r[2], "city": r[3],
                          "tags": (r[4] if r[4] is not None else "")}
    conn.close()
    return info


def fetch_tags_awbprint(order_names):
    """Tags + STATUS CURENT din AWBprint.

    Necesar din două motive:
    1. `metrics.orders` NU acoperă toate magazinele (MagDeal lipsește complet din warehouse).
    2. `profit_orders` (sursa semnalelor) poate fi în urmă — o comandă marcată „fantomă" acolo
       poate fi între timp ANULATĂ, PIERDUTĂ de curier sau chiar în tranzit. Măsurat 30-iul-2026
       pe 147 „fantome": doar 56 erau reale (`waiting_for_courier`); 71 erau `lost_in_transit`,
       19 `cancelled`, 1 `in_transit` — adică 90 din 147 primeau o recomandare greșită.

    ⚠️ Coloana `tags` e populată abia din TAGS_RELIABLE_FROM; înainte e NULL pe tot.
    Absența tag-ului pe o comandă veche NU înseamnă „nu e test" — înseamnă „nu se știe".
    """
    out = {}
    if not order_names:
        return out
    # AWBprint nu mai e sursa de adevar agreata (raportul 26-aug-2026 pune Shopify).
    # Aici e doar imbogatire (tag `test` + status curent) peste semnalele din profit_orders;
    # ARONA_NO_AWBPRINT=1 o scoate complet, iar semnalele raman (cu mai putin context).
    if (os.environ.get("ARONA_NO_AWBPRINT") or "").strip() not in ("", "0", "false"):
        print("[info] ARONA_NO_AWBPRINT=1 — sar peste imbogatirea din AWBprint "
              "(tag `test` + status curent lipsesc din verdicte).", file=sys.stderr)
        return out
    try:
        url = secret("DATABASE_URL_AWBPRINT")
    except Exception:
        return out
    if not url:
        return out
    u = urllib.parse.urlparse(url)
    conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = conn.cursor()
    nn = list(order_names)
    for i in range(0, len(nn), 800):
        ch = nn[i:i + 800]; ph = ",".join(["%s"] * len(ch))
        cur.execute("SELECT order_number, tags::text, frisbo_created_at, "
                    "coalesce(aggregated_status,''), coalesce(courier_name,'') "
                    "FROM orders WHERE order_number IN (" + ph + ")", ch)
        for r in cur.fetchall():
            out[r[0]] = {"tags": r[1] or "", "created": r[2],
                         "status": r[3], "courier": r[4]}
    conn.close()
    return out


# Data de la care coloana `tags` din AWBprint e populată. Înainte, NULL = nesincronizat, NU „fără tag".
# Măsurat pe MagDeal, zi cu zi: până pe 2026-05-26 inclusiv erau 100% NULL (ex. 25-mai: 165/166),
# pe 27-mai au rămas doar 2/126, iar din 28-mai încolo ZERO. Deci cutover-ul real = 2026-05-27.
TAGS_RELIABLE_FROM = "2026-05-28"


def age_days(created_at, today):
    try:
        return (today - datetime.date.fromisoformat(str(created_at)[:10])).days
    except Exception:
        return 0


def build_rows(raw, today):
    rows = []
    for s in raw.get("ghost", []):
        rows.append({
            "flag": "fantoma", "o": s["order_name"], "prefix": s["prefix"],
            "brand": PREFIX.get(s["prefix"], s["prefix"]), "age": age_days(s["created_at"], today),
            "rev": float(s.get("revenue") or 0), "cur": s.get("currency") or "RON",
            "awb": s.get("awb") or "", "cs": s.get("courier_status") or "",
            "status": "Etichetă printată, NESCANATĂ de curier",
        })
    for s in raw.get("noawb", []):
        rows.append({
            "flag": "lipsa_awb", "o": s["order_name"], "prefix": s["prefix"],
            "brand": PREFIX.get(s["prefix"], s["prefix"]), "age": age_days(s["created_at"], today),
            "rev": float(s.get("revenue") or 0), "cur": s.get("currency") or "RON",
            "awb": s.get("awb") or "", "cs": s.get("courier_status") or "",
            "status": "Marcat expediat FĂRĂ AWB (fără tracking)",
        })
    return rows


def render(rows, days, store_filter, per_store):
    today = datetime.date.today()
    ghost = [r for r in rows if r["flag"] == "fantoma"]
    noawb = [r for r in rows if r["flag"] == "lipsa_awb"]
    val_total = sum(r["rev"] for r in rows)

    hdr = "=== COLETE FANTOMĂ — etichetă printată dar curierul nu a ridicat coletul ==="
    print(hdr)
    print("Prag vechime fantome: >%d zile%s" % (days, ("  |  magazin: " + store_filter) if store_filter else ""))
    print("FANTOME (expediat dar n-a plecat): %d  |  FĂRĂ TRACKING (lipsă AWB): %d  |  total: %d" % (
        len(ghost), len(noawb), len(rows)))
    print("Valoare blocată (revenue): %s lei\n" % "{:,.0f}".format(val_total))

    # Per magazin, fiecare sortat vechime desc apoi valoare desc
    by_store = {}
    for r in rows:
        by_store.setdefault(r["brand"], []).append(r)
    order = sorted(by_store.keys(),
                   key=lambda b: (-len(by_store[b]), -sum(x["rev"] for x in by_store[b])))

    for brand in order:
        lst = sorted(by_store[brand], key=lambda x: (-x["age"], -x["rev"]))
        sval = sum(x["rev"] for x in lst)
        print("── %s  (%d colete | %s lei blocați) %s" % (
            brand, len(lst), "{:,.0f}".format(sval), "─" * max(2, 40 - len(brand))))
        print("  %-13s %4s  %-34s %9s %-13s" % ("comandă", "zile", "status", "valoare", "AWB"))
        shown = lst[:per_store] if per_store > 0 else lst
        for x in shown:
            print("  %-13s %4d  %-34s %7s %-3s %-13s" % (
                x["o"], x["age"], x["status"][:34],
                "{:,.0f}".format(x["rev"]), x["cur"], (x["awb"] or "—")[:13]))
            print("     → %s" % suggested_action(x["flag"], x["age"]))
        if per_store > 0 and len(lst) > per_store:
            rest = lst[per_store:]
            print("  ... încă %d colete (%s lei) — vezi --json pt lista completă sau --per-store 0." % (
                len(rest), "{:,.0f}".format(sum(x["rev"] for x in rest))))
        print()

    # Sumar
    print("─" * 70)
    print("SUMAR: %d colete fantomă/fără-tracking, %s lei blocați." % (len(rows), "{:,.0f}".format(val_total)))
    if ghost:
        gval = sum(r["rev"] for r in ghost)
        oldest = max((r["age"] for r in ghost), default=0)
        print("  • %d fantome (etichetă nescanată), %s lei, cea mai veche de %d zile." % (
            len(ghost), "{:,.0f}".format(gval), oldest))
    if noawb:
        nval = sum(r["rev"] for r in noawb)
        print("  • %d marcate expediat FĂRĂ AWB, %s lei — clientul n-are ce urmări." % (
            len(noawb), "{:,.0f}".format(nval)))
    print("  Acțiune: verifică depozit / re-expediere / mesaj proactiv ÎNAINTE să-ți scrie clientul furios.")


def main():
    ap = argparse.ArgumentParser(description="Colete fantomă: etichetă printată dar curierul nu a ridicat coletul.")
    ap.add_argument("--days", type=int, default=3, help="prag vechime pt fantome (default 3)")
    ap.add_argument("--store", default="", help="filtrează un magazin (ex: Esteban, Grandia, Magdeal)")
    ap.add_argument("--per-store", type=int, default=20, dest="per_store",
                    help="câte colete afișezi per magazin în raport (0 = toate; default 20)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-test", action="store_true",
                    help="include și comenzile cu tag 'test' (implicit sunt excluse — nu-s fantome reale)")
    a = ap.parse_args()

    today = datetime.date.today()
    cut = (today - datetime.timedelta(days=a.days)).isoformat()

    prefixes = []
    if a.store:
        prefixes = [p for p, b in PREFIX.items() if a.store.lower() in b.lower()]
        if not prefixes:
            print("Magazin necunoscut: %s. Magazine: %s" % (
                a.store, ", ".join(sorted(set(PREFIX.values())))))
            return

    raw = fetch_profit(cut, prefixes)
    rows = build_rows(raw, today)
    # contacte din metrics (telefon/oraș, util pt CS) — atașăm dar afișăm compact
    contacts = fetch_contacts({r["o"] for r in rows})
    for r in rows:
        c = contacts.get(r["o"], {})
        r["name"] = c.get("name"); r["phone"] = c.get("phone"); r["city"] = c.get("city")
        r["tags"] = c.get("tags", "")

    # Tags și din AWBprint — metrics.orders NU acoperă toate magazinele (MagDeal lipsește total).
    awb_tags = fetch_tags_awbprint({r["o"] for r in rows})
    for r in rows:
        t = awb_tags.get(r["o"], {})
        if not r.get("tags"):
            r["tags"] = t.get("tags", "")
        r["_created_raw"] = t.get("created")
        r["awb_status"] = t.get("status", "")
        if t.get("courier"):
            r["courier"] = t["courier"]

    # Trei categorii, nu două. Comenzile de TEST nu sunt fantome; iar cele de dinainte de
    # sincronizarea tag-urilor NU se pot verifica — a le număra ca fantome reale e o alarmă falsă.
    # (Măsurat 30-iul-2026: pe fereastra cu tag-uri sigure, 155 din 155 „marcate expediate fără
    # AWB" erau teste = ZERO cazuri reale, raportate sub un titlu de 306k lei.)
    def _is_test(r):
        return "test" in str(r.get("tags", "")).lower()

    def _unverifiable(r):
        if r.get("tags"):
            return False
        c = r.get("_created_raw")
        try:
            return str(c)[:10] < TAGS_RELIABLE_FROM if c else False
        except Exception:
            return False

    n_before = len(rows)
    n_test = sum(1 for r in rows if _is_test(r))
    unver = [r for r in rows if not _is_test(r) and _unverifiable(r)]
    n_unver = len(unver)
    val_unver = sum(r["rev"] for r in unver)
    if not a.include_test:
        rows = [r for r in rows if not _is_test(r) and not _unverifiable(r)]

    # RECLASIFICARE pe statusul REAL din AWBprint. `profit_orders` poate fi în urmă, iar
    # o „fantomă" de acolo poate fi între timp anulată sau pierdută de curier — cazuri cu
    # acțiuni complet diferite (sau niciuna).
    NOT_GHOST = {"cancelled", "canceled", "in_transit", "delivered"}
    dropped = [r for r in rows if r.get("awb_status") in NOT_GHOST]
    n_dropped = len(dropped)
    drop_by = {}
    for r in dropped:
        drop_by[r["awb_status"]] = drop_by.get(r["awb_status"], 0) + 1
    rows = [r for r in rows if r.get("awb_status") not in NOT_GHOST]
    for r in rows:
        if r.get("awb_status") == "lost_in_transit":
            r["flag"] = "pierdut"          # curierul l-a pierdut → reclamație, nu căutare în depozit
    lost = [r for r in rows if r["flag"] == "pierdut"]

    rows.sort(key=lambda x: (-x["age"], -x["rev"]))

    if a.json:
        for r in rows:
            r["action"] = suggested_action(r["flag"], r["age"])
        print(json.dumps({
            "days": a.days, "store": a.store or None, "cut": cut,
            "total": len(rows),
            "ghost": sum(1 for r in rows if r["flag"] == "fantoma"),
            "noawb": sum(1 for r in rows if r["flag"] == "lipsa_awb"),
            "value_blocked": round(sum(r["rev"] for r in rows), 2),
            "lost": sum(1 for r in rows if r["flag"] == "pierdut"),
            "excluded_test": n_test,
            "excluded_unverifiable": n_unver,
            "excluded_unverifiable_value": round(val_unver, 2),
            "excluded_not_ghost": drop_by,
            "tags_reliable_from": TAGS_RELIABLE_FROM,
            "rows": rows,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        if n_test or n_unver or n_dropped:
            parts = []
            if n_test:
                parts.append("%d cu tag 'test'" % n_test)
            if n_unver:
                parts.append("%d NEVERIFICABILE (create înainte de %s, când AWBprint a început să "
                             "salveze tag-urile — nu se poate spune dacă-s teste; %s lei)"
                             % (n_unver, TAGS_RELIABLE_FROM, "{:,.0f}".format(val_unver)))
            if n_dropped:
                parts.append("%d nu mai sunt fantome azi (%s)"
                             % (n_dropped, ", ".join("%s=%d" % kv for kv in sorted(drop_by.items()))))
            print("ℹ️  Excluse din total: " + "  ·  ".join(parts) + ".\n")
        if lost:
            print("⚠️  %d colete PIERDUTE de curier (%s lei) — NU-s în depozit; cer RECLAMAȚIE, "
                  "nu căutare.\n" % (len(lost), "{:,.0f}".format(sum(r["rev"] for r in lost))))
        render(rows, a.days, a.store, a.per_store)


if __name__ == "__main__":
    main()
