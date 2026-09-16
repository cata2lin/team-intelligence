# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Teste de REGRESIE pentru FAPTELE DE PRODUS pe piețele străine (bg/sk/hu/pl).

Rulează OFFLINE, pe un catalog SINTETIC (`CS_CATALOG_DB` într-un fișier temporar) și pe propoziții
inventate — niciun dat real de client, niciun număr de comandă, niciun preț de magazin real.
Acoperă exact defectele măsurate pe 307 tichete străine din oglindă, unde ZERO primeau vreun preț:

  1. pliere de diacritice DOAR românească → titlul din catalog și cuvântul-cheie ajungeau în
     alfabete diferite („protišmykových" vs „protismykovych") pe toate piețele CE;
  2. clasa de litere `[a-zăâîșț]` → un text CHIRILIC dădea ZERO cuvinte-cheie;
  3. plafonul de 6 cuvinte-cheie umplut integral de descrierea ROMÂNEASCĂ a reclamei, deci
     copy-ul în limba pieței (singurul care se potrivește cu titlul) nu ajungea niciodată la căutare;
  4. stocul NEGATIV (magazin care nu urmărește stocul) ajuns în context ca cifră reală;
  5. pagini FB mapate pe branduri inexistente → brandul rezultat n-are nici catalog, nici rută.

  uv run teste_catalog_piete_straine.py
"""
import importlib.util, os, sqlite3, sys, tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="cs-catalog-test-")
os.environ["CS_CATALOG_DB"] = os.path.join(_TMP, "catalog.sqlite")


def _incarca(cale, nume):
    spec = importlib.util.spec_from_file_location(nume, cale)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _incarca(os.path.join(HERE, "..", "cs-photo", "cs_photo.py"), "cs_photo_test")
cp._metrics_rows = lambda store, kw, limit: []      # OFFLINE: warehouse-ul nu se atinge în teste

# ── catalog SINTETIC (nume și prețuri inventate, în limbile piețelor) ──
CATALOG = {
    "Piata SK": ("EUR", [("Protišmyková podložka do kúpeľne", "9.99", 5),
                         ("Prenosný šijací stroj", "19.99", None)]),
    "Piata BG": ("EUR", [("Електрическа кутия за храна", "12.99", 3),
                         ("Комплект микрофибърни кърпи", "8.99", None)]),
    "Piata PL": ("PLN", [("Zestaw rurek termokurczliwych", "54.99", None)]),
    "Piata unic": ("EUR", [("№ 1 — Alfa", "12.00", -17), ("№ 2 — Beta", "12.00", -4)]),
}
_c = cp._snap_conn()
for brand, (cur, produse) in CATALOG.items():
    for titlu, pret, stoc in produse:
        _c.execute("INSERT INTO catalog(brand,title,title_pliat,price,stock,sku) VALUES(?,?,?,?,?,?)",
                   (brand, titlu, cp._deacc(titlu), pret, stoc, "TEST-" + titlu[:6]))
    preturi = {p for _, p, _ in produse}
    _c.execute("INSERT INTO catalog_meta(brand,shop,currency,n,updated_at,uniform_price) VALUES(?,?,?,?,?,?)",
               (brand, "test.myshopify.com", cur, len(produse), "2026-01-01T00:00:00Z",
                preturi.pop() if len(preturi) == 1 else None))
_c.commit()
_c.close()

CAD = _incarca(os.path.join(HERE, "cs_auto_draft.py"), "cs_auto_draft_catalog_test")

esec = []


def cer(nume, cond, detaliu=""):
    print(("✅ " if cond else "⛔ ") + nume + ("" if cond else "  ← " + detaliu))
    if not cond:
        esec.append(nume)


# 1. plierea e IDENTICĂ pe ambele părți (Python și tabelul dat lui translate() în SQL)
cer("tabelul de pliere are aceeași lungime pe ambele părți",
    len(cp._FOLD_SRC) == len(cp._FOLD_DST))
cer("plierea SQL coincide cu _deacc pentru fiecare literă din tabel",
    all(cp._deacc(a) == b for a, b in zip(cp._FOLD_SRC, cp._FOLD_DST)),
    str([(a, b) for a, b in zip(cp._FOLD_SRC, cp._FOLD_DST) if cp._deacc(a) != b][:5]))
cer("literele fără accent combinabil se pliază (ł→l, đ→d)",
    cp._deacc("Łatwy đak") == "latwy dak", cp._deacc("Łatwy đak"))

# 2. cuvinte-cheie în ORICE alfabet
cer("text CHIRILIC produce cuvinte-cheie", bool(cp._cuvinte_cheie("електрическа кутия за храна")))
cer("cuvintele slovace NU se rup la litera accentuată",
    "protismykova" in cp._cuvinte_cheie("Protišmyková podložka"),
    str(cp._cuvinte_cheie("Protišmyková podložka")))

# 3. copy-ul în limba pieței ajunge la căutare chiar când descrierea ROMÂNEASCĂ ocupă tot plafonul
ro = "Produsul promovat este o cutie pentru mancare cald destinata incalzirii alimentelor rapide"
bg = "електрическа кутия за храна практична за затопляне"
cer("fără a doua sursă de cuvinte, descrierea românească sufocă potrivirea",
    cp.catalog_match("Piata BG", ro + " " + bg) == [])
r = cp.catalog_match("Piata BG", ro + " " + bg, text2=bg)
cer("cu a doua sursă (copy-ul pieței) produsul se potrivește",
    bool(r) and r[0]["title"].startswith("Електрическа"), str(r))
r_sk = cp.catalog_match("Piata SK", "Protišmyková podložka do kúpeľne")
cer("potrivirea merge pe alfabet latin-extins (sk)", bool(r_sk), str(r_sk))

# 4. stocul se dă DOAR dacă e pozitiv
cer("stocul pozitiv se păstrează", r and r[0]["stock"] == 3, str(r))
r_neg = cp.catalog_match("Piata unic", "Alfa parfum Alfa")
cer("stocul negativ NU ajunge în context",
    all(x["stock"] is None for x in r_neg), str(r_neg))

# 5. prețul UNIC pe magazin: doar când chiar toate produsele au același preț
cer("prețul unic e recunoscut când e unic", cp.snapshot_meta("Piata unic").get("uniform_price") == "12.00")
cer("prețul unic NU se inventează pe un catalog cu prețuri diferite",
    cp.snapshot_meta("Piata SK").get("uniform_price") is None)
cer("blocul de catalog pe canal privat dă prețul unic când nu știm modelul",
    "12.00" in cp.catalog_block("Piata unic", "cat costa va rog"),
    cp.catalog_block("Piata unic", "cat costa va rog"))

# 6. moneda e a PIEȚEI, niciodată „lei"
cer("moneda vine din magazin, nu din numele brandului",
    cp.brand_currency("Piata PL") == "zł" and cp.brand_currency("Piata BG") == "EUR",
    cp.brand_currency("Piata PL") + "/" + cp.brand_currency("Piata BG"))

# 7. fiecare pagină FB străină duce la un brand cu limbă, țară ȘI rută de contact
for pid in ("814175968452902", "516792924847762", "425006144024872", "700342149818211", "680369271815957"):
    b = CAD.PAGE_STORE.get(pid, "")
    cer("pagina %s → brand cu limbă+țară+rută (%s)" % (pid, b or "NEMAPAT"),
        bool(b) and b in CAD.STORE_LANG and b in CAD.STORE_CC
        and (b in CAD.STORE_URL or b in CAD.STORE_PHONE),
        "lang=%s cc=%s url=%s tel=%s" % (b in CAD.STORE_LANG, b in CAD.STORE_CC,
                                         b in CAD.STORE_URL, b in CAD.STORE_PHONE))

# 8. brandurile pentru care ținem instantaneu de catalog trebuie să existe ca magazine reale
for b in cp.CATALOG_SHOPIFY:
    cer("brandul cu instantaneu %r are rută de contact" % b,
        b in CAD.STORE_URL or b in CAD.STORE_PHONE)

print("\n%s" % ("TOATE testele au trecut" if not esec else "EȘUATE: " + ", ".join(esec)))
sys.exit(1 if esec else 0)
