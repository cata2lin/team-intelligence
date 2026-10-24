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
ZILE_FEREASTRA = 30        # fereastra vitezei; listat mai recent = produs nou (regula 3)
FEREASTRA_LUNGA = 90       # cand a lipsit stocul, viteza se socoteste pe zilele cu stoc din ultimele 90
MIN_ZILE_CU_STOC = 14      # sub atatea zile cu stoc, viteza simpla pe 90 de zile, marcata nesigura


def _mii(n):
    return format(int(round(n)), ",d").replace(",", ".")


def _zec(x):
    return ("%.1f" % x).replace(".", ",")
# Masurat pe istoric (23-oct): sub 500 de bucati sell-through-ul la 90 de zile e 26-41%, peste
# 500 cade la 9-15%. 44 de linii peste prag inseamna 817.337 lei, jumatate din capitalul cohortei,
# la 12% sell-through median. Regula asta prinde cazul pe care lunile de stoc il lasa sa treaca:
# cele 35.000 de lavete (149.940 lei blocati) erau "3 luni de stoc" la ritmul de atunci.
PRAG_BUCATI = 500
PRAG_REFUZ = 20.0          # %
# Linie scumpa pe un produs lent: AVERTISMENT, nu blocaj (masurat in umbra pe 23-oct). Un produs
# lent e deja blocat de regula lunilor; textul spune doar cati bani stau in spatele ei.
PRAG_VALOARE_LEI = 20000.0
# Ferestrele de sezon se refera la DATA COMENZII, nu a vanzarii. Decizia Orchestratorului, 24-oct-2026
# (regula 1 din `reguli-comanda-furnizor-2027`):
#   * VARA: marfa trebuie sa fie pe raft pana la 15 martie, deci PO-ul pleaca pana la 1 noiembrie.
#     Blocat intre 2 noiembrie si 31 august; intre 1 septembrie si 1 noiembrie trece liber.
#     (Versiunea de pana acum bloca 1 iunie – 31 decembrie si lasa liber ianuarie – mai, adica
#     exact comenzile care ajung prea tarziu.)
#   * CRACIUN: pe raft pana la 1 noiembrie, deci PO pana la 1 iulie. Blocat 2 iulie – 31 decembrie.
# Fereastra = (luna, zi) de inceput si de sfarsit, ambele INCLUSE; poate trece peste Anul Nou.
FEREASTRA_BLOCATA = {"vara": ((11, 2), (8, 31)), "craciun": ((7, 2), (12, 31))}
CUTOFF = {"vara": (11, 1), "craciun": (7, 1)}   # ultima zi in care se poate comanda (pentru afisare)
VARA = re.compile(r"gradin|plaja|piscin|sezlong|racor|ventilator|aer condi|umbrel|camping", re.I)
# Craciun = DOAR termeni expliciti. „cadou" singur NU e Craciun: prindea seturile cadou, cardurile
# cadou si produsele „cadou" din oferte, care se vand tot anul. Textul se compara fara diacritice.
CRACIUN = re.compile(r"craciun|christmas|xmas|\bbrad(ut|uti|ul)?\b|\bmos\b|mos craciun|ornament"
                     r"|\bglob(uri|urile)?\b|instalati[ae] lumin|luminit[ae] (de )?brad", re.I)


def fara_diacritice(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", str(t or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def in_fereastra(azi, fereastra):
    """True daca data comenzii cade in fereastra (luna, zi)-(luna, zi), capete incluse."""
    (a, b) = fereastra
    x = (azi.month, azi.day)
    return a <= x <= b if a <= b else (x >= a or x <= b)


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


OH_API = os.environ.get("OH_BASE_URL", "https://orderhub.arona.ro").rstrip("/")


def _secret_text(k):
    """Un secret oarecare (token), intai din mediu, apoi din KB. Gol daca nu se poate."""
    v = os.environ.get(k) or ""
    if not v:
        uv = "/root/.local/bin/uv" if os.path.exists("/root/.local/bin/uv") else "uv"
        try:
            v = subprocess.run([uv, "run", KB, "secret-get", k], capture_output=True,
                               text=True, timeout=60).stdout.strip()
        except Exception:
            v = ""
    return v if v and " " not in v else ""


def viteza_order_hub_api(skus):
    """Aceeasi viteza, prin poarta de citire a Order Hub (`/api/cs/vanzari-pe-sku`, scop cs.citire).

    De ce exista (24-oct): pe VPS-ul unde ruleaza umbra si serviciul de garda (84.46.242.181) nu
    avem ssh catre serverul Order Hub, deci varianta prin docker exec esua TACUT si garda cadea pe
    metrics — sursa care pierde o treime din bucati. Poarta HTTP merge de oriunde, cu un token de
    citire, fara date personale. Definitia e a Order Hub: comanda PLASATA, neanulata, fara demo.
    Daca raspunsul e taiat la plafonul de randuri, NU il folosesc: produsele lente ar disparea din
    lista si ar parea fara vanzari, adica exact cazul in care garda ar bloca pe nedrept.
    """
    import urllib.request
    tok = _secret_text("OH_TEAM_READ_TOKEN")
    if not tok:
        return {}, ""
    try:
        req = urllib.request.Request(OH_API + "/api/cs/vanzari-pe-sku?zile=30",
                                     headers={"Authorization": "Bearer " + tok})
        d = json.load(urllib.request.urlopen(req, timeout=60))
    except Exception:
        return {}, ""
    if not d.get("ok") or d.get("taiat"):
        return {}, ""
    cerute = {x.upper() for x in skus}
    out = {}
    for r in d.get("randuri") or []:
        s = str(r.get("sku") or "").strip().upper()
        if s in cerute:
            out[s] = out.get(s, 0) + int(r.get("bucati") or 0)
    # Raspuns valid fara niciunul dintre SKU-uri = zero vanzari reale, nu sursa lipsa.
    return out, "Order Hub"


_TOTALURI = {}


def oh_totaluri(zile):
    """{SKU: bucati} plasate, neanulate, in ultimele `zile` zile (Bucuresti, azi inclus), din poarta
    Orders. None daca poarta nu raspunde sau raspunsul e taiat. Tinut 1 ora pe proces."""
    import urllib.request
    cheie = (int(zile), datetime.datetime.now().strftime("%Y%m%d%H"))
    if cheie in _TOTALURI:
        return _TOTALURI[cheie]
    tok = _secret_text("OH_TEAM_READ_TOKEN")
    out = None
    if tok:
        try:
            req = urllib.request.Request(OH_API + "/api/cs/vanzari-pe-sku?zile=%d" % int(zile),
                                         headers={"Authorization": "Bearer " + tok})
            d = json.load(urllib.request.urlopen(req, timeout=60))
            if d.get("ok") and not d.get("taiat"):
                out = {}
                for r in d.get("randuri") or []:
                    k = str(r.get("sku") or "").strip().upper()
                    out[k] = out.get(k, 0) + int(r.get("bucati") or 0)
        except Exception:
            out = None
    _TOTALURI[cheie] = out
    return out


def viteza_pe_zile_cu_stoc(zile_zero, total, fereastra=FEREASTRA_LUNGA, min_zile=MIN_ZILE_CU_STOC):
    """Viteza pe 30 de zile cand stocul a fost 0 in fereastra de 30 (Orchestrator, 24-oct-2026).

    `zile_zero` = cate zile in urma (0 = azi) a fost stocul 0, in ultimele `fereastra` zile.
    `total(k)` = bucati plasate neanulate in ultimele k zile. Vanzarile din zilele fara stoc se scad
    (T(b+1) - T(a) pentru fiecare sir a..b de zile la rand), raman doar zilele cu stoc:
        viteza = vandut in zilele cu stoc / zile cu stoc x 30.
    Sub `min_zile` zile cu stoc: viteza simpla pe `fereastra` zile, marcata nesigura.
    Intoarce None cand n-a fost stoc 0 in ultimele 30 de zile (ramane viteza obisnuita).
    """
    zz = sorted({int(i) for i in zile_zero if 0 <= int(i) < fereastra})
    if not any(i < 30 for i in zz):
        return None
    cu_stoc = fereastra - len(zz)
    t_total = total(fereastra)
    if cu_stoc < min_zile:
        return {"viteza": t_total / float(fereastra) * 30, "metoda": "90z", "zile_cu_stoc": cu_stoc,
                "nesigura": True}
    vandut_fara_stoc, a = 0, None
    for k, i in enumerate(zz):
        if a is None:
            a = i
        if k + 1 == len(zz) or zz[k + 1] != i + 1:
            vandut_fara_stoc += total(i + 1) - (total(a) if a > 0 else 0)
            a = None
    return {"viteza": max(0, t_total - vandut_fara_stoc) / float(cu_stoc) * 30, "metoda": "zile_cu_stoc",
            "zile_cu_stoc": cu_stoc, "nesigura": False}


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


def zile_stoc_zero(valori_recente_intai):
    """Cate instantanee la rand, incepand cu cel mai recent, au stoc 0."""
    n = 0
    for q in valori_recente_intai:
        if q > 0:
            break
        n += 1
    return n


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
    # Categoria din catalogul TOM (product_types): un produs pus in categoria de Craciun e de
    # Craciun chiar daca titlul nu o spune.
    t.execute("""SELECT upper(p.sku), coalesce(pt.name, '') FROM products p
                 LEFT JOIN product_types pt ON pt.id = p."productTypeId"
                 WHERE upper(p.sku) = ANY(%s)""", (skus,))
    for s, cat in t.fetchall():
        if s in out:
            out[s]["categorie"] = cat or ""
    tom.close()

    # ⚠ Viteza NU se ia din `metrics.order_line_items`: acolo LIPSESC magazine intregi
    # (MagDeal.ro, De la Bucsa, Bonhaus.pl/.hu, Portal, Orice Redus) — masurat pe HA, aug+sep 2026:
    # 29.698 bucati in metrics fata de 44.629 in Order Hub, adica 33% lipsa. Pe HA viteza reala e
    # de 1,58-1,68x cea din metrics, deci garda ar supraestima lunile de stoc si ar bloca pe nedrept.
    # Sursa buna e Order Hub; metrics ramane doar ca rezerva, cu avertisment in raport.
    viteza, sursa_viteza = viteza_order_hub_api(skus)
    if not sursa_viteza:
        viteza, sursa_viteza = viteza_order_hub(skus)
    if not sursa_viteza:
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
    # Varsta listarii = prima data cand SKU-ul a aparut pe ORICARE magazin (Shopify createdAt al
    # variantei). NU `publishedAt`: se muta la fiecare republicare (lampile 51650: listate in
    # ianuarie, „publicate" pe 20-oct).
    m.execute("""SELECT upper(v.sku), min(coalesce(v."shopifyCreatedAt", p."shopifyCreatedAt", v."createdAt"))::date
                 FROM variants v LEFT JOIN products p ON p.id = v."productId"
                 WHERE upper(v.sku) = ANY(%s) GROUP BY 1""", (skus,))
    for s, d in m.fetchall():
        if s in out:
            out[s]["listat_la"] = d
    # Zile la rand cu stoc 0 pana azi (instantaneele zilnice de stoc din Shopify, suma pe magazine).
    # Cat timp n-a fost marfa, nu s-a putut vinde: viteza pe 30 de zile iese mai mica decat cererea.
    m.execute("""SELECT upper(sku), "snapshotDate", sum(greatest(coalesce("quantityAvailable", 0), 0))
                 FROM inventory_daily_snapshots
                 WHERE upper(sku) = ANY(%s) AND "snapshotDate" >= current_date - 89
                 GROUP BY 1, 2 ORDER BY 1, 2 DESC""", (skus,))
    serie, zero = {}, {}
    azi_d = datetime.date.today()
    for s, d, q in m.fetchall():
        serie.setdefault(s, []).append(int(q or 0))
        if int(q or 0) <= 0:
            zero.setdefault(s, []).append((azi_d - d).days)
    for s, valori in serie.items():
        if s in out:
            out[s]["zile_stoc_zero"] = zile_stoc_zero(valori)
    met.close()
    # Viteza pe zilele cu stoc (Orchestrator 24-oct): fara stoc nu s-a putut vinde, deci zilele alea
    # nu intra la numitor. Doar cu sursa Orders si doar unde stocul a fost 0 in ultimele 30 de zile.
    for s, zz in zero.items():
        f = out.get(s)
        if not f or f.get("viteza_din_kit") or not str(f.get("sursa_viteza") or "").startswith("Order Hub"):
            continue
        totaluri = {}

        def total(k, _s=s):
            if k not in totaluri:
                t = oh_totaluri(k)
                if t is None:
                    raise LookupError("poarta Orders indisponibila")
                totaluri[k] = t
            return totaluri[k].get(_s, 0)
        try:
            r = viteza_pe_zile_cu_stoc(zz, total)
        except LookupError:
            r = None
        if r:
            f["viteza30_simpla"] = f.get("viteza30")
            f["viteza30"] = int(round(r["viteza"]))
            f["viteza_metoda"] = r["metoda"]
            f["zile_cu_stoc_90"] = r["zile_cu_stoc"]
            f["viteza_nesigura"] = r["nesigura"]

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


_CURS = {}


def curs_usd_ron():
    """Ultimul curs BNR USD->RON din metrics.fx_rates, tinut o ora. None daca nu se poate citi:
    atunci avertismentul de valoare nu apare, in loc sa fie calculat pe un curs inventat."""
    acum = datetime.datetime.now()
    if _CURS.get("t") and (acum - _CURS["t"]).total_seconds() < 3600:
        return _CURS.get("v")
    v = None
    try:
        met = conectare("DATABASE_URL_METRICS"); m = met.cursor()
        m.execute("""SELECT rate FROM fx_rates WHERE "fromCurrency"='USD' AND "toCurrency"='RON'
                     ORDER BY "rateDate" DESC LIMIT 1""")
        r = m.fetchone(); met.close()
        v = float(r[0]) if r else None
    except Exception:
        v = None
    _CURS.update(t=acum, v=v)
    return v


def verifica(linii, azi=None, blacklist=()):
    azi = azi or datetime.date.today()
    if any(l.get("valoare_lei") is None and l.get("valoare_usd") for l in linii):
        curs = curs_usd_ron()
        if curs:
            for l in linii:
                if l.get("valoare_lei") is None and l.get("valoare_usd"):
                    l["valoare_lei"] = float(l["valoare_usd"]) * curs
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
        # REGULA 2 (poarta principala, decizia ownerului 22/23-oct): cantitate <= viteza_30z x 4 luni,
        # viteza din Order Hub, pe SKU. Calibrarea de 4 luni din back-test e exact raportul asta.
        luni_comandate = (q / viteza) if viteza else None
        # PRODUS NOU (Orchestrator 24-oct): fara vanzari, SAU listat de mai putin de 30 de zile (fereastra
        # vitezei nu e plina, deci viteza e subestimata) -> regula 3 (test 200-300), NICIODATA regula 2.
        listat = f.get("listat_la")
        if isinstance(listat, str):
            listat = datetime.date.fromisoformat(listat[:10])
        zile_listat = (azi - listat).days if listat else None
        nou_listat = zile_listat is not None and zile_listat < ZILE_FEREASTRA
        nou = (viteza == 0) or nou_listat
        rezumat = None
        tip = None
        if componenta:
            pass                      # ambalaj / materie prima: se judeca pe consum, nu aici
        elif nou:
            if q > PRAG_NOU:
                de_ce = ("listat de %d zile (sub fereastra de %d)" % (zile_listat, ZILE_FEREASTRA)
                         if nou_listat else "fara vanzari in ultimele 30 de zile")
                motive.append("regula 3: produs nou — %s — si comanda de %d buc, peste testul de %d"
                              " — daca e COMPONENTA DE KIT, judec-o pe viteza kitului si semneaza motivul"
                              % (de_ce, q, PRAG_NOU))
                rezumat = "%s buc pe un produs nou (%s), peste testul de %d buc" % (_mii(q), de_ce, PRAG_NOU)
                tip = "nou"
        elif q > viteza * PRAG_LUNI:
            motive.append("regula 2: comanzi %d buc = %.1f luni de vanzari (viteza %d/luna in Order Hub)"
                          " — peste %d buc (viteza x %.0f luni)"
                          % (q, luni_comandate, viteza, int(viteza * PRAG_LUNI), PRAG_LUNI))
            rezumat = "%s buc = %s luni de vânzări (%s/lună în Orders), peste plafonul de %.0f luni" % (
                _mii(q), _zec(luni_comandate), _mii(viteza), PRAG_LUNI)
            tip = "luni"
        elif luni > PRAG_LUNI:
            # Regula 2 trece, dar cu raftul si ce e pe drum ajungi tot peste 4 luni.
            motive.append("%.1f luni de stoc dupa comanda (stoc %d + pe drum %d + %d, viteza %d/luna) "
                          "— peste pragul de %.0f" % (luni, f.get("stoc") or 0, f.get("pe_drum") or 0,
                                                      q, viteza, PRAG_LUNI))
            rezumat = ("%s buc + %s pe raft + %s pe drum = %s luni de stoc (%s/lună în Orders), peste plafonul "
                       "de %.0f luni" % (_mii(q), _mii(f.get("stoc") or 0), _mii(f.get("pe_drum") or 0),
                                         _zec(luni), _mii(viteza), PRAG_LUNI))
            tip = "stoc"
        nume = fara_diacritice((f.get("nume") or "") + " " + (l.get("nume") or ""))
        categorie = fara_diacritice((f.get("categorie") or "") + " " + (l.get("categorie") or ""))
        if VARA.search(nume) and in_fereastra(azi, FEREASTRA_BLOCATA["vara"]):
            tip = tip or "vara"
            motive.append("produs de vara comandat intre 2 noiembrie si 31 august: nu mai ajunge pe raft "
                          "pana la 15 martie (se comanda 1 septembrie – 1 noiembrie)")
        if CRACIUN.search(nume + " " + categorie) and in_fereastra(azi, FEREASTRA_BLOCATA["craciun"]):
            tip = tip or "craciun"
            motive.append("produs de Craciun comandat dupa 1 iulie: nu mai ajunge pe raft pana la 1 noiembrie")
        if not componenta and not nou and q >= PRAG_BUCATI:
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
            tip = tip or "blacklist"
            motive.append("SKU pe blacklist (stop la re-comanda)")
        if l.get("refuz") is not None and l["refuz"] > PRAG_REFUZ:
            tip = tip or "refuz"
            motive.append("refuz %.0f%%, peste pragul de %.0f%%" % (l["refuz"], PRAG_REFUZ))
        h = (hot or {}).get(s)
        avertismente = list(avertismente_marime)
        val = l.get("valoare_lei")
        if val is not None and float(val) >= PRAG_VALOARE_LEI and luni is not None and luni > PRAG_LUNI:
            avertismente.append("linie de %s lei pe un produs care se misca incet: la produsele scumpe "
                                "si lente cantitatea mica pacaleste regula pe bucati"
                                % format(round(float(val)), ",d").replace(",", "."))
        if h in ("SUPER COLD", "COLD"):
            avertismente.append("pagina eMAG e %s: cererea pe piata e mica" % h)
        elif h is None and not componenta:
            avertismente.append("produsul n-are pagina pe eMAG, deci nu avem semnal de cerere de acolo")
        if motive and not rezumat:
            rezumat = motive[0]
        rez.append({"sku": s, "qty": q, "nume": (f.get("nume") or "")[:50],
                    "rezumat": rezumat, "tip_blocaj": tip if motive else None,
                    "produs_nou": bool(nou and not componenta),
                    "zile_stoc_zero": f.get("zile_stoc_zero"),
                    "viteza30_simpla": f.get("viteza30_simpla"), "viteza_metoda": f.get("viteza_metoda") or "30z",
                    "zile_cu_stoc_90": f.get("zile_cu_stoc_90"), "viteza_nesigura": bool(f.get("viteza_nesigura")),
                    "valoare_lei": round(float(val)) if val is not None else None,
                    "zile_de_la_listare": zile_listat,
                    "luni_comandate": round(luni_comandate, 1) if luni_comandate else None,
                    "componenta": bool(componenta), "viteza_din_kit": f.get("viteza_din_kit"),
                    "stoc": f.get("stoc"), "pe_drum": f.get("pe_drum"), "viteza30": viteza,
                    "luni_dupa_comanda": round(luni, 1) if luni else None,
                    "sursa_viteza": f.get("sursa_viteza"),
                    "blocat": bool(motive), "motive": motive,
                    "hotness": h, "avertismente": avertismente})
    return rez


PRAGURI = {"luni": PRAG_LUNI, "produs_nou_bucati": PRAG_NOU, "bucati_pe_linie": PRAG_BUCATI,
           "refuz_pct": PRAG_REFUZ, "valoare_lei_avertisment": PRAG_VALOARE_LEI,
           "comanda_vara_pana_la": "%02d-%02d" % CUTOFF["vara"],
           "comanda_craciun_pana_la": "%02d-%02d" % CUTOFF["craciun"],
           "blocat_vara": "%02d-%02d..%02d-%02d" % (FEREASTRA_BLOCATA["vara"][0] + FEREASTRA_BLOCATA["vara"][1]),
           "blocat_craciun": "%02d-%02d..%02d-%02d" % (FEREASTRA_BLOCATA["craciun"][0] + FEREASTRA_BLOCATA["craciun"][1])}


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
