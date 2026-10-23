# /// script
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Garda de dinainte de PO: spune, linie cu linie, ce NU trebuie comandat si de ce.

Regulile sunt cele aprobate de owner pe 22-oct-2026 (vezi memoria `reguli-comanda-furnizor-2027`
si skill-ul `gigi:po-lectii-2026`). Pragul de 4 luni e confirmat de back-test-ul din
`jurnal/2026-10-23-po-istoric-si-garzi.md`: ar fi oprit 2,01 mil. lei, din care 88% era intr-adevar
marfa care a stat.

    uv run po_garda.py --linii '[{"sku":"HA-0001","qty":3000}]'
    uv run po_garda.py --po-deschise          # proba pe PO-urile NEW/ORDERED din TOM
    uv run po_garda.py --linii-fisier cerere.json --json

Iesire: pentru fiecare linie `ok` sau `BLOCAT` + motivele. Cod de iesire 1 daca exista blocaje,
ca sa poata fi pus in fata unui script de creare PO.
"""
import argparse, datetime, json, re, subprocess, sys

import os
AICI = os.path.dirname(os.path.abspath(__file__))
KITURI = os.path.join(AICI, "po_garda_kituri.json")
KB = ("/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/"
      "plugins/core/scripts/kb.py")
PRAG_LUNI = 4.0            # calibrat: peste 4 luni, sell-through-ul la 90 de zile cade la 9%
PRAG_NOU = 300             # produs nou: peste atat fara test = blocat
PRAG_REFUZ = 20.0          # %
CUTOFF = {"vara": (11, 1), "craciun": (7, 1)}   # dupa 1 nov nu mai comanzi vara; dupa 1 iul, Craciun
VARA = re.compile(r"gradin|plaja|piscin|sezlong|racor|ventilator|aer condi|umbrel|camping", re.I)
CRACIUN = re.compile(r"craciun|brad|ornament|instalatie lumin|cadou", re.I)


def secret(k):
    d = subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()
    return re.sub(r"[?&]schema=[^&]*", "", d)      # Prisma pune ?schema=public, psycopg2 il refuza


def conectare(cheie):
    import psycopg2
    return psycopg2.connect(secret(cheie))


def hotness(skus):
    """Cat de cautata e pagina produsului pe eMAG: HOT / STANDARD / COLD / SUPER COLD.

    E doar un AVERTISMENT, niciodata blocaj: semnalul exista numai pentru produsele care au deja
    pagina pe eMAG (14 din 211 masurate pe 23-oct), iar o parte din EAN-urile noastre sunt generate
    intern si nu se potrivesc cu nicio pagina. Lipsa paginii NU inseamna produs slab.
    """
    fis = os.path.join(AICI, "po_garda_hotness.json")
    try:
        return {k.upper(): v for k, v in json.load(open(fis)).items() if not k.startswith("_")}
    except Exception:
        return {}


def kituri():
    """componenta -> kitul a carui viteza o mosteneste (vezi po_garda_kituri.json)"""
    try:
        return {k.upper(): v for k, v in json.load(open(KITURI)).items() if not k.startswith("_")}
    except Exception:
        return {}


def date_de_fond(skus):
    """stoc pe raft, cantitate pe drum, viteza pe 30 de zile, refuz, numar de comenzi anterioare."""
    skus = [s.upper() for s in skus]
    harta_kit = kituri()
    skus = sorted(set(skus) | {v.upper() for k, v in harta_kit.items() if k in skus})
    out = {s: {"stoc": 0, "pe_drum": 0, "viteza30": 0, "refuz": None, "comenzi_anterioare": 0,
               "nume": "", "viteza_din_kit": None} for s in skus}

    tom = conectare("DATABASE_URL_TOM"); t = tom.cursor()
    t.execute("""SELECT upper(i."externalSku"), sum(coalesce(i."orderedQty",0) - coalesce(i."receivedQty",0)),
                        count(*) FILTER (WHERE coalesce(i."receivedQty",0) > 0),
                        max(coalesce(i."externalTitle",''))
                 FROM purchase_order_items i JOIN purchase_orders p ON p.id = i."poId"
                 WHERE upper(i."externalSku") = ANY(%s) AND p.status NOT IN ('CANCELLED')
                 GROUP BY 1""", (skus,))
    for s, pe_drum, ant, nume in t.fetchall():
        out[s]["pe_drum"] = max(0, int(pe_drum or 0))
        out[s]["comenzi_anterioare"] = int(ant or 0)
        out[s]["nume"] = nume or ""
    tom.close()

    met = conectare("DATABASE_URL_METRICS"); m = met.cursor()
    m.execute("""SELECT upper(sku), sum(quantity)::int FROM order_line_items
                 WHERE upper(sku) = ANY(%s) AND "createdAt" > now() - interval '30 days'
                 GROUP BY 1""", (skus,))
    for s, q in m.fetchall():
        if s in out:
            out[s]["viteza30"] = int(q or 0)
    # componenta de kit: imprumuta viteza kitului, altfel pare marfa moarta
    for comp, kit in harta_kit.items():
        if comp in out and not out[comp]["viteza30"]:
            v = (out.get(kit.upper()) or {}).get("viteza30") or 0
            if v:
                out[comp]["viteza30"] = v
                out[comp]["viteza_din_kit"] = kit
    # Ambalajele (sticle, capace, cutii, etichete) NU se judeca pe viteza de vanzare: nu se vand,
    # se consuma. Fara exceptia asta garda bloca 43 din 47 de linii deschise, aproape toate
    # ambalaje Esteban/Lab Noir — adica ar fi fost ignorata din prima zi.
    m.execute("SELECT DISTINCT upper(sku) FROM variants WHERE upper(sku) = ANY(%s)", (skus,))
    vandabile = {r[0] for r in m.fetchall()}
    for s in out:
        out[s]["componenta"] = s not in vandabile
    met.close()

    # Stocul fizic din scanerul de depozit (toate statiile). Pe server NU exista binarul
    # `sqlite3`, deci citim prin python3, altfel comanda da "command not found" si stocul
    # ar ramane 0 — adica garda ar subestima lunile de stoc exact pe produsele pline.
    cmd = ("ssh -o ConnectTimeout=15 root@84.46.242.181 \"python3 -c \\\""
           "import sqlite3;"
           "c=sqlite3.connect('file:/root/Scripturi/data/locations.db?mode=ro',uri=True);"
           "print(chr(10).join('%s|%d'%(s.upper(),q) for s,q in "
           "c.execute('select sku,sum(qty) from shelf_items where qty>0 group by 1')))"
           "\\\"\"")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    if not r.stdout.strip():
        raise SystemExit("nu am putut citi stocul din scaner: %s" % (r.stderr or "").strip()[:200])
    for linie in r.stdout.splitlines():
        if "|" not in linie: continue
        s, q = linie.rsplit("|", 1)
        if s.strip().upper() in out:
            out[s.strip().upper()]["stoc"] = int(float(q))
    return out


def verifica(linii, azi=None, blacklist=()):
    azi = azi or datetime.date.today()
    fond = date_de_fond([l["sku"] for l in linii])
    hot = hotness([l["sku"] for l in linii])
    rez = []
    for l in linii:
        s = l["sku"].upper(); q = int(l["qty"])
        f = fond.get(s, {})
        motive = []
        viteza = f.get("viteza30") or 0
        total = (f.get("stoc") or 0) + (f.get("pe_drum") or 0) + q
        luni = (total / viteza) if viteza else None
        componenta = f.get("componenta")
        if componenta:
            pass                      # ambalaj / materie prima: se judeca pe consum, nu aici
        elif viteza == 0:
            if q > PRAG_NOU:
                motive.append("produs fara vanzari in ultimele 30 de zile si comanda de %d buc "
                              "(peste testul de %d) — daca e COMPONENTA DE KIT, judec-o pe viteza "
                              "kitului si treci blocajul cu motiv scris" % (q, PRAG_NOU))
        elif luni > PRAG_LUNI:
            motive.append("%.1f luni de stoc dupa comanda (stoc %d + pe drum %d + %d, viteza %d/luna) "
                          "— peste pragul de %.0f" % (luni, f.get("stoc") or 0, f.get("pe_drum") or 0,
                                                      q, viteza, PRAG_LUNI))
        nume = (f.get("nume") or "") + " " + (l.get("nume") or "")
        if VARA.search(nume) and (azi.month, azi.day) >= CUTOFF["vara"]:
            motive.append("produs de vara comandat dupa 1 noiembrie")
        if CRACIUN.search(nume) and (azi.month, azi.day) >= CUTOFF["craciun"]:
            motive.append("produs de Craciun comandat dupa 1 iulie")
        if s in blacklist:
            motive.append("SKU pe blacklist (stop la re-comanda)")
        if l.get("refuz") is not None and l["refuz"] > PRAG_REFUZ:
            motive.append("refuz %.0f%%, peste pragul de %.0f%%" % (l["refuz"], PRAG_REFUZ))
        h = (hot or {}).get(s)
        avertismente = []
        if h in ("SUPER COLD", "COLD"):
            avertismente.append("pagina eMAG e %s: cererea pe piata e mica" % h)
        elif h is None and not componenta:
            avertismente.append("produsul n-are pagina pe eMAG, deci nu avem semnal de cerere de acolo")
        rez.append({"sku": s, "qty": q, "nume": (f.get("nume") or "")[:50],
                    "componenta": bool(componenta), "viteza_din_kit": f.get("viteza_din_kit"),
                    "stoc": f.get("stoc"), "pe_drum": f.get("pe_drum"), "viteza30": viteza,
                    "luni_dupa_comanda": round(luni, 1) if luni else None,
                    "blocat": bool(motive), "motive": motive,
                    "hotness": h, "avertismente": avertismente})
    return rez


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--linii", help='JSON: [{"sku":"HA-0001","qty":3000}]')
    ap.add_argument("--linii-fisier")
    ap.add_argument("--po-deschise", action="store_true", help="proba pe PO-urile NEW/ORDERED din TOM")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.po_deschise:
        tom = conectare("DATABASE_URL_TOM"); t = tom.cursor()
        t.execute("""SELECT upper(i."externalSku"), i."orderedQty", p."tomNumber"
                     FROM purchase_order_items i JOIN purchase_orders p ON p.id=i."poId"
                     WHERE p.status IN ('NEW','ORDERED') AND coalesce(i."orderedQty",0) > 0
                       AND i."externalSku" IS NOT NULL""")
        linii = [{"sku": s, "qty": int(q), "po": po} for s, q, po in t.fetchall()]
        tom.close()
    elif a.linii_fisier:
        linii = json.load(open(a.linii_fisier))
    elif a.linii:
        linii = json.loads(a.linii)
    else:
        sys.exit("da --linii, --linii-fisier sau --po-deschise")

    rez = verifica(linii)
    if a.json:
        print(json.dumps(rez, ensure_ascii=False, indent=1))
    else:
        for r in rez:
            if r["blocat"]:
                print("BLOCAT  %-11s %6d buc · %s" % (r["sku"], r["qty"], r["nume"]))
                for m in r["motive"]:
                    print("        · %s" % m)
            else:
                print("ok      %-11s %6d buc · %.1f luni dupa comanda"
                      % (r["sku"], r["qty"], r["luni_dupa_comanda"] or 0))
            for av in r.get("avertismente") or []:
                print("        ⚠ %s" % av)
    n = sum(1 for r in rez if r["blocat"])
    print("\n%d linii, %d blocate" % (len(rez), n), file=sys.stderr if a.json else sys.stdout)
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
