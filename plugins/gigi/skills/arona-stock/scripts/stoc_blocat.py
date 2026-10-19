# -*- coding: utf-8 -*-
"""
stoc_blocat.py — marfa care EXISTA in stock-sync dar pe care NICIUN magazin n-o vede.

Gaseste masterii cu globalAvailable > 0 care n-au NICIUN listing MATCHED, le stabileste
CAUZA (nu doar simptomul) si scoate un CSV cu ce e de facut pe fiecare rand.

Cele 4 cauze, in ordinea gravitatii:
  LEGAT GRESIT — magazinul e legat de ALT produs decat cel care tine marfa.
  DRAFT        — produsul e in draft pe magazin; stock-sync nu potriveste un produs non-ACTIVE,
                 deci marfa noua n-are unde sa fie impinsa si produsul ramane invizibil (bucla).
  DUBLURA      — acelasi produs are 2 masteri cu barcode diferit; magazinul e legat de cel gol.
  NELISTAT     — nu exista deloc ca produs pe magazinele verificate.

Copie in git a skill-ului SB gigi:stoc-blocat (+ --top), trimisa pe server prin ssh/stdin de MCP-ul arona-stock.
Rulare (PE serverul de scripturi, unde exista api/scan.py + tokenurile Shopify):
  .venv/bin/python stoc_blocat.py --magazine EST,GT --nume "No\\.?\\s*\\d" --out /root/blocat.csv
  .venv/bin/python stoc_blocat.py --parfumuri            # presetul de parfumuri (Esteban + GT)
  .venv/bin/python stoc_blocat.py --magazine TOATE       # tot catalogul, fara filtru de nume

DOAR CITIRE. Nu scrie nimic nici in stock-sync, nici in Shopify.
"""
import argparse
import collections
import csv
import os
import re
import sys
import urllib.parse

sys.path.insert(0, "/root/Scripturi")
sys.path.insert(0, "/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api.scan import _ss_get  # noqa: E402
import xconnector as xc  # noqa: E402

# Seria-exemplu din manualul EAN-13 (590 = Polonia) + seria de placeholder interna. Un barcode
# de aici pe un master care tine marfa inseamna aproape sigur receptie pe cod inventat, nu real.
PLACEHOLDER = ("5901234", "2000000")
# limita API: /v1/... refuza limit > 100 (HTTPError sec, fara mesaj)
PAGINA = 100


def pagineaza(baza, cheie, maxp=500):
    out, cur, p = [], None, 0
    while p < maxp:
        u = "%s?limit=%d" % (baza, PAGINA)
        if cur:
            u += "&cursor=" + urllib.parse.quote(cur)
        d = _ss_get(u) or {}
        lot = d.get(cheie) or []
        out.extend(lot)
        cur = d.get("nextCursor")
        p += 1
        if not cur or not lot:
            break
    return out


NRSKU = re.compile(r"^(?:zn-|gt-|est-)?(\d+)$", re.I)
# Numele masterilor sunt scrise inconsecvent: "No. 152", "Nr. 60" si chiar "Know. 152"
# (un find-replace a stricat 4 nume). Daca nu le prinzi pe toate, doi masteri ai aceluiasi
# produs par "legati gresit" unul fata de altul — fals pozitiv.
NRNUME = re.compile(r"\b(?:No|Nr|Know)\.?\s*(\d+)", re.I)


def cheie_produs(text, din_sku=False):
    """Numarul care identifica produsul la noi (parfumuri: 'No. 131' <-> sku '131'/'zn-131')."""
    s = str(text or "").strip().lower()
    m = NRSKU.match(s) if din_sku else NRNUME.search(s)
    return (m.group(1).lstrip("0") or "0") if m else ""


def catalog_shopify(prefixe):
    """Tot catalogul magazinelor cerute, o singura data (nu una-doua interogari per produs)."""
    tk = {(t.get("prefix") or "").upper(): t for t in xc.load_shopify_tokens()}
    Q = """query($c:String){ products(first:100, after:$c){ pageInfo{hasNextPage endCursor}
      edges{ node{ title status variants(first:30){ edges{ node{ sku barcode inventoryQuantity } } } } } } }"""
    cat = {}
    for pr in prefixe:
        x = tk.get(pr)
        if not x:
            print("  ! nu am token pentru %s — sar peste" % pr)
            continue
        prod, cur = [], None
        while True:
            # shopify_gql intoarce PLICUL GraphQL: raspunsul e sub cheia "data", nu la radacina.
            d = xc.shopify_gql(x["shopDomain"], x["adminToken"], Q, {"c": cur})
            if not isinstance(d, dict) or d.get("errors"):
                print("  ! %s: %s" % (pr, str(d)[:140]))
                break
            pp = (d.get("data") or {}).get("products") or {}
            prod += [e["node"] for e in pp.get("edges") or []]
            if not (pp.get("pageInfo") or {}).get("hasNextPage"):
                break
            cur = pp["pageInfo"]["endCursor"]
        cat[pr] = prod
        print("  %s: %d produse (%s)" % (pr, len(prod),
              ", ".join("%s=%d" % kv for kv in collections.Counter(p["status"] for p in prod).most_common())))
    return cat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--magazine", default="EST,GT",
                    help="prefixe Shopify de verificat, separate prin virgula; TOATE = toate cu token")
    ap.add_argument("--nume", default="", help="regex pe numele masterului (gol = toti masterii)")
    ap.add_argument("--parfumuri", action="store_true", help="preset: parfumuri pe Esteban + GT")
    ap.add_argument("--min-stoc", type=int, default=1, help="ignora masterii sub atata stoc")
    ap.add_argument("--out", default="stoc_blocat.csv")
    ap.add_argument("--top", type=int, default=0, help="arata si primele N randuri (0 = doar sumarul)")
    a = ap.parse_args()

    filtru = re.compile(a.nume or (r"\bNo\.?\s*\d" if a.parfumuri else r""), re.I)
    prefixe = ["EST", "GT"] if a.parfumuri else [p.strip().upper() for p in a.magazine.split(",") if p.strip()]
    if prefixe == ["TOATE"]:
        prefixe = sorted({(t.get("prefix") or "").upper() for t in xc.load_shopify_tokens() if t.get("prefix")})

    print("1/3 citesc stock-sync…")
    M = pagineaza("/v1/master-products", "masterProducts")
    L = pagineaza("/v1/listings", "listings")
    print("    %d masteri, %d listing-uri" % (len(M), len(L)))

    tinta = [m for m in M if (m.get("globalAvailable") or 0) >= a.min_stoc and filtru.search(m.get("name") or "")]
    legat = {l["masterProductId"] for l in L if l.get("matchStatus") == "MATCHED" and l.get("masterProductId")}
    orfani = [m for m in tinta if m["id"] not in legat]
    print("    %d masteri cu stoc dupa filtru; %d FARA listing potrivit (%d buc)"
          % (len(tinta), len(orfani), sum(m.get("globalAvailable") or 0 for m in orfani)))
    if not orfani:
        print("nimic blocat. gata.")
        return

    print("2/3 citesc catalogul Shopify (%s)…" % ",".join(prefixe))
    cat = catalog_shopify(prefixe)

    # index: numarul produsului -> variantele de pe magazine; si barcode -> varianta
    pe_nr, pe_bc = collections.defaultdict(list), {}
    for pr, prods in cat.items():
        for pn in prods:
            for ve in (pn.get("variants") or {}).get("edges") or []:
                v = ve["node"]
                rec = (pr, pn["status"], v.get("sku"), v.get("barcode"), v.get("inventoryQuantity"), pn["title"])
                k = cheie_produs(v.get("sku"), din_sku=True)
                if k:
                    pe_nr[k].append(rec)
                if v.get("barcode"):
                    pe_bc[str(v["barcode"])] = rec

    # de ce master e legat magazinul, pentru acelasi numar de produs
    mid = {m["id"]: m for m in M}
    pe_nr_listing = collections.defaultdict(set)
    for l in L:
        k = cheie_produs(l.get("sku"), din_sku=True)
        if k and l.get("matchStatus") == "MATCHED" and l.get("masterProductId"):
            pe_nr_listing[k].add(l["masterProductId"])

    print("3/3 clasific…")
    R = []
    for m in orfani:
        n = cheie_produs(m.get("name"))
        shop = pe_nr.get(n or "") or ([pe_bc[m["barcode"]]] if m.get("barcode") in pe_bc else [])
        stat = sorted({r[1] for r in shop})
        # TOTI masterii de care e legat magazinul pentru acelasi numar. Ordonat determinist:
        # un set are ordine arbitrara, deci "alt[0]" dadea alt verdict la fiecare rulare.
        alti = sorted((mid[x] for x in pe_nr_listing.get(n or "", set()) if x in mid),
                      key=lambda z: (-(z.get("globalAvailable") or 0), z.get("id") or ""))
        gresiti = [z for z in alti if cheie_produs(z.get("name")) != n]
        alt = alti[0] if alti else None
        if not shop:
            cauza, act, pri = "NELISTAT", "de listat pe magazin (produs nou)", 3
        elif gresiti:
            cauza, pri = "LEGAT GRESIT", 0
            act = "magazinul e legat de '%s' — corecteaza legatura" % (gresiti[0].get("name") or "?")[:40]
        elif stat == ["DRAFT"]:
            cauza, act, pri = "DRAFT", "republica produsul in Shopify -> se leaga singur", 1
        elif alt is not None:
            cauza, pri = "DUBLURA", 2
            act = ("acelasi produs are 2 masteri: magazinul e pe '%s' (stoc %s), marfa pe asta"
                   " — uneste-i / pune acelasi barcode" % ((alt.get("name") or "?")[:40], alt.get("globalAvailable")))
        else:
            cauza, act, pri = "NEPOTRIVIT", "produs activ dar barcode nepotrivit — verifica varianta", 2
        R.append({"prio": pri, "cheie": n, "master_cu_stocul": (m.get("name") or "")[:80],
                  "stoc_depozit": m.get("globalAvailable") or 0, "barcode_master": m.get("barcode") or "",
                  "barcode_placeholder": "DA" if str(m.get("barcode") or "").startswith(PLACEHOLDER) else "",
                  "magazin": "+".join(sorted({r[0] for r in shop})), "status_shopify": "+".join(stat),
                  "barcode_shopify": "+".join(sorted({str(r[3]) for r in shop if r[3]}))[:60],
                  "stoc_shopify": sum(r[4] or 0 for r in shop), "cauza": cauza, "de_facut": act})

    R.sort(key=lambda r: (r["prio"], -r["stoc_depozit"]))
    for r in R:
        r.pop("prio")
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(R[0].keys()))
        w.writeheader()
        w.writerows(R)

    c, s = collections.Counter(), collections.Counter()
    for r in R:
        c[r["cauza"]] += 1
        s[r["cauza"]] += r["stoc_depozit"]
    print("\nTOTAL: %d produse, %d bucati blocate\n" % (len(R), sum(r["stoc_depozit"] for r in R)))
    for k, _ in c.most_common():
        print("  %-13s %4d produse %8d buc" % (k, c[k], s[k]))
    ph = [r for r in R if r["barcode_placeholder"]]
    if ph:
        print("\n  ! %d dintre ei au barcode de PLACEHOLDER (%s…) pe masterul cu marfa — %d buc."
              % (len(ph), "/".join(PLACEHOLDER), sum(r["stoc_depozit"] for r in ph)))
        print("    Asta nu-s N greseli, e o receptie facuta pe coduri inventate. Verifica evenimentul, nu randurile.")
    if a.top:
        print("\nPrimele %d (ordonate pe gravitate, apoi stoc):" % min(a.top, len(R)))
        for r in R[:a.top]:
            print("  %-13s %7d  %-40s %-8s %s" % (r["cauza"], r["stoc_depozit"], r["master_cu_stocul"][:40],
                  r["magazin"][:8] or "-", r["de_facut"][:80]))
    print("\nCSV: %s" % os.path.abspath(a.out))


if __name__ == "__main__":
    main()
