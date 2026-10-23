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
# kb.py sta in alt loc pe laptop fata de VPS; iar pe VPS secretele vin de obicei din .env.
# Fara cautarea asta, `secret()` intorcea text gol si psycopg2 incerca conexiunea locala, cu
# rolul `root` inexistent — adica garda cadea tacut exact pe serverul unde ruleaza cronul.
KB_CAI = [
    "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/"
    "plugins/core/scripts/kb.py",
    "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py",
]
KB = next((c for c in KB_CAI if os.path.exists(c)), KB_CAI[0])
# 4 luni: DECIZIA OWNERULUI din 23-oct-2026, luata dupa back-test. Nu se schimba fara el.
# Masurat: sub prag sell-through-ul la 90 de zile e 46%, peste prag 9%. Din 10 blocari, 9 au fost
# corecte, iar cele gresite erau la limita (marfa s-a vandut in 3,4-3,5 luni, nu in 4).
PRAG_LUNI = 4.0
PRAG_NOU = 300             # produs nou: peste atat fara test = blocat
# Masurat pe istoric (23-oct): sub 500 de bucati sell-through-ul la 90 de zile e 26-41%, peste
# 500 cade la 9-15%. 44 de linii peste prag inseamna 817.337 lei, jumatate din capitalul cohortei,
# la 12% sell-through median. Regula asta prinde cazul pe care lunile de stoc il lasa sa treaca:
# cele 35.000 de lavete (149.940 lei blocati) erau "3 luni de stoc" la ritmul de atunci.
PRAG_BUCATI = 500
PRAG_REFUZ = 20.0          # %
# Cutoff-ul se refera la DATA COMENZII, nu a vanzarii: marfa comandata in iulie ajunge in
# august-septembrie, la coada sezonului. Masurat: produsele de vara comandate dupa 1 iunie au
# vandut 0,3% in prima luna, fata de 4,0% la cele nesezoniere.
CUTOFF = {"vara": (6, 1), "craciun": (7, 1)}
VARA = re.compile(r"gradin|plaja|piscin|sezlong|racor|ventilator|aer condi|umbrel|camping", re.I)
CRACIUN = re.compile(r"craciun|brad|ornament|instalatie lumin|cadou", re.I)


def secret(k):
    """Intai din mediu (cronul de pe VPS incarca .env), apoi din KB."""
    v = os.environ.get(k) or ""
    if not v:
        uv = "/root/.local/bin/uv" if os.path.exists("/root/.local/bin/uv") else "uv"
        v = subprocess.run([uv, "run", KB, "secret-get", k],
                           capture_output=True, text=True).stdout.strip()
    if not v.startswith(("postgres://", "postgresql://")):
        raise SystemExit("nu am obtinut %s (nici din mediu, nici din KB: %r)" % (k, v[:60]))
    return re.sub(r"[?&]schema=[^&]*", "", v)      # Prisma pune ?schema=public, psycopg2 il refuza


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


def viteza_order_hub(skus):
    """Bucati plecate in ultimele 30 de zile, din Order Hub (sursa de adevar pe comenzi).

    Rulez printr-un heredoc pe stdin, nu cu `python3 -c`: comanda trece prin ssh, apoi prin
    docker exec, si ghilimelele imbricate se pierd pe drum — prima varianta esua tacut si garda
    cadea pe metrics, adica exact pe sursa incompleta pe care voiam s-o evitam.
    """
    cod = (
        "import os, psycopg2\n"
        "d = os.environ['DATABASE_URL'].replace('+asyncpg', '')\n"
        "c = psycopg2.connect(d); u = c.cursor()\n"
        "u.execute(\"SELECT upper(li.sku), sum(li.quantity)::int FROM line_items li \"\n"
        "          \"JOIN orders o ON o.id = li.order_id \"\n"
        "          \"WHERE o.created_at > now() - interval '30 days' AND li.sku IS NOT NULL \"\n"
        "          \"GROUP BY 1\")\n"
        "print(chr(10).join('%s|%d' % (a, b) for a, b in u.fetchall()))\n")
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=15", "root@161.97.69.226",
             "docker exec -i -w /app orderhub-web python3 -"],
            input=cod, capture_output=True, text=True, timeout=180)
        cerute = {x.upper() for x in skus}
        out = {}
        for linie in r.stdout.splitlines():
            if "|" not in linie:
                continue
            s, q = linie.rsplit("|", 1)
            if s.strip().upper() in cerute:
                out[s.strip().upper()] = int(q)
        return (out, "Order Hub") if out else ({}, "")
    except Exception:
        return {}, ""


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

    # ⚠ Viteza NU se ia din `metrics.order_line_items`: acolo LIPSESC magazine intregi
    # (MagDeal.ro, De la Bucsa, Bonhaus.pl/.hu, Portal, Orice Redus) — masurat pe HA, aug+sep 2026:
    # 29.698 bucati in metrics fata de 44.629 in Order Hub, adica 33% lipsa. Pe HA viteza reala e
    # de 1,58-1,68x cea din metrics, deci garda ar supraestima lunile de stoc si ar bloca pe nedrept.
    # Sursa buna e Order Hub; metrics ramane doar ca rezerva, cu avertisment in raport.
    viteza, sursa_viteza = viteza_order_hub(skus)
    if not viteza:
        sursa_viteza = "metrics (REZERVA — lipsesc magazine, cifrele sunt sub realitate)"
        met = conectare("DATABASE_URL_METRICS"); m = met.cursor()
        m.execute("""SELECT upper(sku), sum(quantity)::int FROM order_line_items
                     WHERE upper(sku) = ANY(%s) AND "createdAt" > now() - interval '30 days'
                     GROUP BY 1""", (skus,))
        viteza = {s: int(q or 0) for s, q in m.fetchall()}
        met.close()
    for s, q in viteza.items():
        if s in out:
            out[s]["viteza30"] = int(q or 0)
    for s in out:
        out[s]["sursa_viteza"] = sursa_viteza
    met = conectare("DATABASE_URL_METRICS"); m = met.cursor()
    m.execute("""SELECT upper(sku), 0 FROM order_line_items WHERE false""", ())
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
    met = conectare("DATABASE_URL_METRICS"); m = met.cursor()
    m.execute("SELECT DISTINCT upper(sku) FROM variants WHERE upper(sku) = ANY(%s)", (skus,))
    vandabile = {r[0] for r in m.fetchall()}
    for s in out:
        out[s]["componenta"] = s not in vandabile
    met.close()

    # Stocul fizic din scanerul de depozit (toate statiile). Baza sta pe 84.46.242.181; daca
    # rulam CHIAR pe serverul ala (cum face umbra din cron), o citim direct — altfel am incerca
    # ssh catre noi insine si am pica cu "Permission denied (publickey)".
    LOCAL = "/root/Scripturi/data/locations.db"
    randuri = []
    if os.path.exists(LOCAL):
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % LOCAL, uri=True, timeout=30)
        randuri = [(s, q) for s, q in c.execute(
            "select sku, sum(qty) from shelf_items where qty>0 group by 1")]
        c.close()
    else:
        cod = ("import sqlite3\n"
               "c = sqlite3.connect('file:/root/Scripturi/data/locations.db?mode=ro', uri=True)\n"
               "print(chr(10).join('%s|%d' % (s, q) for s, q in "
               "c.execute('select sku, sum(qty) from shelf_items where qty>0 group by 1')))\n")
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", "root@84.46.242.181", "python3", "-"],
                           input=cod, capture_output=True, text=True, timeout=120)
        if not r.stdout.strip():
            raise SystemExit("nu am putut citi stocul din scaner: %s" % (r.stderr or "").strip()[:200])
        randuri = [(l.rsplit("|", 1)[0], l.rsplit("|", 1)[1]) for l in r.stdout.splitlines() if "|" in l]
    for s, q in randuri:
        s = str(s).strip().upper()
        if s in out:
            out[s]["stoc"] = int(float(q))
    return out


def verifica(linii, azi=None, blacklist=()):
    azi = azi or datetime.date.today()
    fond = date_de_fond([l["sku"] for l in linii])
    hot = hotness([l["sku"] for l in linii])
    rez = []
    for l in linii:
        s = l["sku"].upper(); q = int(l["qty"])
        f = fond.get(s, {})
        motive, avertismente_marime = [], []
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
        if not componenta and q >= PRAG_BUCATI:
            # Marimea singura NU blocheaza un produs care chiar se vinde: raftul metalic din
            # TOM-075 are 739 buc/luna, deci 1.000 de bucati inseamna 1,4 luni — comanda sanatoasa.
            # Blocheaza doar impreuna cu lunile de stoc; altfel ramane avertisment, fiindca peste
            # 500 de bucati pariezi ca ritmul de azi tine luni intregi (cazul lavetelor).
            if luni is not None and luni > PRAG_LUNI:
                motive.append("%d bucati intr-o singura linie (peste %d) SI %.1f luni de stoc: "
                              "istoricul arata sell-through 9-15%% peste pragul asta, fata de "
                              "26-41%% sub el" % (q, PRAG_BUCATI, luni))
            else:
                avertismente_marime.append(
                    "%d bucati intr-o singura linie: te bazezi pe ritmul de acum (%d/luna) pentru "
                    "%.1f luni. Daca ritmul scade la jumatate, stocul tine de doua ori mai mult"
                    % (q, viteza, luni or 0))
        if s in blacklist:
            motive.append("SKU pe blacklist (stop la re-comanda)")
        if l.get("refuz") is not None and l["refuz"] > PRAG_REFUZ:
            motive.append("refuz %.0f%%, peste pragul de %.0f%%" % (l["refuz"], PRAG_REFUZ))
        h = (hot or {}).get(s)
        avertismente = list(avertismente_marime)
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
