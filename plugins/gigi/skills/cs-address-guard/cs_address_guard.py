# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "paramiko>=3.0"]
# ///
"""
cs_address_guard.py — GARDIAN DE ADRESĂ: coada de comenzi încă neexpediate (status
Netrimisa) cu ADRESĂ DEFECTĂ, pe care CS le confirmă telefonic ÎNAINTE ca depozitul
să facă AWB. Atacă direct refuzul: ~13% din comenzile RO n-au număr de stradă, iar o
adresă proastă = colet rătăcit = refuz/retur plătit de două ori (transport dus-întors).

Flux:
  1) SSH → data/profitability.db (profit_orders): order_name, prefix, revenue
     WHERE status_category='Netrimisa' din ultimele N zile (default 14, ~ultimele câteva mii).
  2) metrics.orders pt acele name-uri → adresa brută, zip, telefon, nume, oraș, țară, email.
     Join pe orders.name = profit_orders.order_name (format identic, ex EST184096).
  3) FLAG adresă proastă, fiecare cu motiv text:
       (a) NICIO cifră în shippingAddress1 → lipsă număr de stradă
       (b) len(shippingAddress1 strip) < 10 → adresă prea scurtă
       (c) shippingZip lipsă/gol → fără cod poștal
       (d) telefon invalid (după strip, nr de cifre nu e 9-12) → nu poate fi sunat
  4) OUTPUT per magazin: comenzile flag-uite (order_name, telefon, adresa brută, motiv)
     + un MESAJ de confirmare gata făcut per piață (RO; CZ/PL/BG scurt după țară).
     Sumar: câte flag-uite din total + estimare cost evitat.

Moduri:
  uv run cs_address_guard.py                       # sumar (toate magazinele)
  uv run cs_address_guard.py --store Esteban       # un singur magazin, detaliat
  uv run cs_address_guard.py --reasons no_number    # doar un tip de motiv (no_number|short|no_zip|bad_phone)
  uv run cs_address_guard.py --days 7 --limit 30   # fereastră + câte rânduri pe magazin
  uv run cs_address_guard.py --json                # pt automatizare (dialer/SMS/Sheet)

READ-ONLY total. Nu scrie nimic în Postgres/Shopify/Richpanel. Fără secrete în output.
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

# prefix (din order_name) -> (nume magazin, piață/limbă pt mesaj)
PREFIX = {
    "EST": ("Esteban", "ro"), "GT": ("George Talent", "ro"), "NUB": ("Nubra", "ro"),
    "GEN": ("Gento", "ro"), "GRAN": ("Grandia", "ro"), "GRAND": ("Grandia", "ro"),
    "BELA": ("Belasil", "ro"), "CARP": ("Carpetto", "ro"), "COV": ("Covoria", "ro"),
    "MAG": ("Magdeal", "ro"), "OFER": ("Ofertele Zilei", "ro"), "RED": ("Reduceri bune", "ro"),
    "BON": ("Bonhaus RO", "ro"), "BONBG": ("Bonhaus BG", "bg"), "CZ": ("Bonhaus CZ", "cz"),
    "PL": ("Bonhaus PL", "pl"), "APR": ("Apreciat", "ro"), "ROSSI": ("Rossi Nails", "ro"),
}

# Cost mediu de transport irosit pe un colet refuzat (dus + întors), RON. Conservator.
TRANSPORT_RT = {"ro": 30.0, "cz": 45.0, "pl": 45.0, "bg": 40.0}

# Mesaj de confirmare per piață — RO bogat, CZ/PL/BG scurt. {n}=prenume {b}=brand {o}=comandă
MSG = {
    "ro": ("Bună ziua {n}! Suntem de la {b}, în legătură cu comanda {o}. Înainte să o "
           "expediem, vrem să fim siguri că ajunge la dumneavoastră: ne puteți confirma "
           "adresa completă (stradă, NUMĂR, bloc/scară/apartament și codul poștal)? "
           "Răspundeți cu adresa corectă și o trimitem azi. Mulțumim! 📦"),
    "cz": ("Dobrý den {n}! {b} ohledně objednávky {o}. Než ji odešleme, potřebujeme "
           "ověřit úplnou adresu (ulice, ČÍSLO POPISNÉ a PSČ). Pošlete prosím správnou "
           "adresu a dnes ji odešleme. Děkujeme! 📦"),
    "pl": ("Dzień dobry {n}! {b} w sprawie zamówienia {o}. Zanim je wyślemy, prosimy o "
           "potwierdzenie pełnego adresu (ulica, NUMER domu/mieszkania i kod pocztowy). "
           "Odeślij poprawny adres, a wyślemy dziś. Dziękujemy! 📦"),
    "bg": ("Здравейте {n}! {b} относно поръчка {o}. Преди да я изпратим, моля потвърдете "
           "пълния адрес (улица, НОМЕР и пощенски код). Отговорете с верния адрес и я "
           "изпращаме днес. Благодаря! 📦"),
}

# Eticheta motivelor (cheie -> text RO)
REASON_LABEL = {
    "no_number": "FĂRĂ NUMĂR DE STRADĂ (nicio cifră în adresă)",
    "short": "ADRESĂ PREA SCURTĂ (< 10 caractere)",
    "no_zip": "FĂRĂ COD POȘTAL",
    "bad_phone": "TELEFON INVALID (nu poate fi sunat)",
}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def ssh(py):
    out = _vps_run("cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return None


def mconn():
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def digits(s):
    return "".join(c for c in (s or "") if c.isdigit())


def store_for(prefix):
    return PREFIX.get((prefix or "").upper(), ((prefix or "?"), "ro"))


def lang_for(prefix, country):
    """Limba mesajului: întâi după țară (mai sigură), apoi după prefix."""
    c = (country or "").strip().lower()
    if c.startswith("czech") or c == "cz":
        return "cz"
    if c.startswith("poland") or c == "pl":
        return "pl"
    if c.startswith("bulgar") or c == "bg":
        return "bg"
    return store_for(prefix)[1]


def flag_address(addr1, zipc, phone):
    """Returnează lista de chei-motiv (poate fi goală = adresă OK)."""
    reasons = []
    a = (addr1 or "").strip()
    if not re.search(r"\d", a):
        reasons.append("no_number")
    if len(a) < 10:
        reasons.append("short")
    if not (zipc or "").strip():
        reasons.append("no_zip")
    dn = len(digits(phone))
    if not (9 <= dn <= 12):
        reasons.append("bad_phone")
    return reasons


def fetch_netrimisa(days, prefix_filter):
    """SSH → comenzile Netrimisa recente (order_name, prefix, revenue)."""
    lo = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    pf = ("AND prefix=" + repr(prefix_filter)) if prefix_filter else ""
    py = ("import sqlite3,json;c=sqlite3.connect('data/profitability.db');lo=" + repr(lo) + ";"
          "rows=[dict(zip(['o','p','rev'],r)) for r in c.execute("
          "\"SELECT order_name,prefix,revenue FROM profit_orders WHERE status_category='Netrimisa' "
          "AND substr(created_at,1,10)>=? " + pf + " ORDER BY created_at DESC\",(lo,))];"
          "print(json.dumps(rows))")
    return ssh(py) or []


def fetch_addresses(cur, names):
    info = {}
    for i in range(0, len(names), 800):
        ch = names[i:i + 800]
        ph = ",".join(["%s"] * len(ch))
        cur.execute(
            'SELECT name,"shippingAddress1","shippingAddress2","shippingCity","shippingProvince",'
            '"shippingZip","shippingPhone","shippingName","shippingCountry",email '
            'FROM orders WHERE name IN (' + ph + ')', ch)
        for r in cur.fetchall():
            info[r[0]] = {
                "addr1": r[1] or "", "addr2": r[2] or "", "city": r[3] or "",
                "province": r[4] or "", "zip": r[5] or "", "phone": r[6] or "",
                "name": r[7] or "", "country": r[8] or "", "email": r[9] or "",
            }
    return info


def collect(days, prefix_filter, only_reason):
    net = fetch_netrimisa(days, prefix_filter)
    if net is None:
        return None
    total_seen = len(net)
    names = [o["o"] for o in net]
    conn = mconn(); cur = conn.cursor()
    info = fetch_addresses(cur, names)
    conn.close()

    flagged = []
    matched = 0
    for o in net:
        c = info.get(o["o"])
        if not c:
            continue  # fără date de adresă în metrics → nu putem evalua
        matched += 1
        reasons = flag_address(c["addr1"], c["zip"], c["phone"])
        if not reasons:
            continue
        if only_reason and only_reason not in reasons:
            continue
        store, _ = store_for(o["p"])
        lang = lang_for(o["p"], c["country"])
        raw_addr = ", ".join(x for x in [c["addr1"], c["addr2"], c["zip"], c["city"], c["province"]] if x).strip(", ")
        flagged.append({
            "order": o["o"], "prefix": o["p"], "store": store, "lang": lang,
            "name": c["name"], "phone": c["phone"], "zip": c["zip"],
            "city": c["city"], "country": c["country"], "email": c["email"],
            "addr_raw": raw_addr or "(adresă goală)",
            "addr1": c["addr1"], "reasons": reasons,
            "revenue": float(o.get("rev") or 0),
        })
    return {"total_seen": total_seen, "matched": matched, "flagged": flagged, "days": days}


def build_message(row):
    nm = (row["name"] or "").split()[0] if row["name"] else ""
    tmpl = MSG.get(row["lang"], MSG["ro"])
    return tmpl.format(n=nm, b=row["store"], o=row["order"])


def cost_avoided(flagged):
    return sum(TRANSPORT_RT.get(r["lang"], 30.0) for r in flagged)


# ───────────────────────── Render ─────────────────────────
def fmt0(x):
    return "{:,.0f}".format(x)


def render_summary(R, only_reason):
    flagged = R["flagged"]
    by_store = {}
    for r in flagged:
        by_store.setdefault(r["store"], []).append(r)
    by_reason = {}
    for r in flagged:
        for rs in r["reasons"]:
            if only_reason and rs != only_reason:
                continue
            by_reason[rs] = by_reason.get(rs, 0) + 1

    rate = (len(flagged) / R["matched"] * 100) if R["matched"] else 0
    print("=" * 74)
    print("  GARDIAN DE ADRESĂ — comenzi NETRIMISE cu adresă defectă (de confirmat")
    print("  telefonic ÎNAINTE de AWB).  Fereastră: ultimele %d zile." % R["days"])
    print("=" * 74)
    print("  Netrimise analizate (cu adresă în metrics): %s" % fmt0(R["matched"]))
    print("  FLAG-UITE pentru confirmare:                 %s  (%.1f%%)" % (fmt0(len(flagged)), rate))
    print("  Estimare cost transport evitat (dus-întors): %s lei" % fmt0(cost_avoided(flagged)))
    skipped = R["total_seen"] - R["matched"]
    if skipped > 0:
        print("  (Notă: %s netrimise n-au rând în metrics.orders — brand nesincronizat, ex" % fmt0(skipped))
        print("   Ofertele Zilei/Magdeal/Bonhaus PL/BG — nu pot fi evaluate aici.)")
    print()
    print("  Pe motiv:")
    for k in ("no_number", "short", "no_zip", "bad_phone"):
        if k in by_reason:
            print("    • %-46s %5s" % (REASON_LABEL[k], fmt0(by_reason[k])))
    print()
    print("  Pe magazin (de confirmat):")
    print("  %-18s %8s %10s %14s" % ("magazin", "flag", "din total", "cost evitat"))
    print("  " + "-" * 54)
    for store, rows in sorted(by_store.items(), key=lambda z: -len(z[1])):
        print("  %-18s %8s %10s %12s lei" % (
            store[:18], fmt0(len(rows)), fmt0(len(rows)), fmt0(cost_avoided(rows))))
    print()
    print("  → Detaliu + mesaje gata făcute: rulează cu --store <magazin>")
    print("  → Doar un tip de problemă:       --reasons no_number|short|no_zip|bad_phone")


def render_store(R, only_reason, limit):
    flagged = R["flagged"]
    if not flagged:
        print("Nicio comandă netrimisă cu adresă defectă pe acest filtru. (Bună treabă.)")
        return
    store = flagged[0]["store"]
    print("=" * 74)
    print("  GARDIAN DE ADRESĂ — %s — de confirmat ÎNAINTE de AWB (ultimele %d zile)" % (store, R["days"]))
    print("=" * 74)
    print("  Flag-uite: %s din %s netrimise analizate | cost evitat ~%s lei" % (
        fmt0(len(flagged)), fmt0(R["matched"]), fmt0(cost_avoided(flagged))))
    print()
    for r in sorted(flagged, key=lambda z: -z["revenue"])[:limit]:
        rlabels = " + ".join(REASON_LABEL[k].split(" (")[0] for k in r["reasons"]
                             if (not only_reason or k == only_reason))
        print("  ● %-13s  %-22s  %s lei" % (r["order"], (r["name"] or "—")[:22], fmt0(r["revenue"])))
        print("      tel : %s   zip: %s" % (r["phone"] or "—", r["zip"] or "—"))
        print("      adr : %s" % r["addr_raw"])
        print("      ⚠️  %s" % rlabels)
        print("      💬 %s" % build_message(r))
        print()


def main():
    ap = argparse.ArgumentParser(description="Gardian de adresă: comenzi netrimise cu adresă defectă, de confirmat înainte de AWB.")
    ap.add_argument("--store", default="", help="un singur magazin (ex Esteban, Grandia, Bonhaus CZ)")
    ap.add_argument("--reasons", default="", choices=["", "no_number", "short", "no_zip", "bad_phone"],
                    help="filtrează doar un tip de problemă")
    ap.add_argument("--days", type=int, default=14, help="fereastră zile pe created_at (default 14)")
    ap.add_argument("--limit", type=int, default=40, help="câte rânduri pe magazin în modul detaliat")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    # mapează --store -> prefix(uri) pentru a restrânge interogarea SSH
    prefix_filter = ""
    if a.store:
        for p, (b, _l) in PREFIX.items():
            if a.store.lower() in b.lower():
                prefix_filter = p
                break

    R = collect(a.days, prefix_filter, a.reasons or "")
    if R is None:
        print("Nu am putut citi comenzile (SSH/DB). Verifică accesul VPS și DATABASE_URL_METRICS.")
        sys.exit(1)

    if a.json:
        out = {
            "days": R["days"], "analyzed": R["matched"], "flagged_count": len(R["flagged"]),
            "cost_avoided_ron": round(cost_avoided(R["flagged"]), 0),
            "flagged": [
                {**{k: r[k] for k in ("order", "store", "name", "phone", "zip", "city",
                                      "country", "email", "addr_raw", "reasons", "revenue")},
                 "message": build_message(r)}
                for r in R["flagged"]
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    if a.store or a.reasons:
        render_store(R, a.reasons or "", a.limit)
    else:
        render_summary(R, a.reasons or "")


if __name__ == "__main__":
    main()
