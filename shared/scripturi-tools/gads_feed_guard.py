#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests", "python-dotenv", "httpx"]
# ///
"""
gads_feed_guard.py — ține feedul Google sincron cu stocul real, în AMBELE direcții.

DE CE EXISTĂ (incident 18-aug-2026, Grandia):
  Procedura de „scoate produsul epuizat din Google" excludea produsul din canalul Google în
  Shopify. Excluderea NU șterge oferta din Merchant Center — o lasă ORFANĂ: app-ul de feed nu-i
  mai trimite actualizări, fișa îngheață pe ultima valoare (availability=IN_STOCK) și Google
  licitează la infinit pe un produs care nu există. GD-MOB-1938930 a ars 3.579 lei în 40 de zile
  cu ZERO comenzi. În plus, procedura era o UȘĂ CU SENS UNIC: 251 de excluderi în jurnal și
  niciun „included" — 72 de produse cu marfă rămăseseră invizibile în Shopping.

CE FACE, ZILNIC:
  SCOATE  — produs indisponibil, dar Merchant Center zice IN_STOCK
            => toggle de publicare pe canalul Google (unpublish + republish) forțează feedul să
               retrimită fișa; availability trece pe OUT_OF_STOCK în ~3 minute.
               NU se atinge nicio campanie => zero risc de reset al învățării PMax.
  PUNE    — produs vandabil, dar scos de pe canalul Google
            => îl repune. Verifică întâi în jurnalul de evenimente că a fost scos de procedură
               (nu deliberat din alt motiv).

REGULI DE PROIECTARE (fiecare plătită cu o greșeală reală):
  1. Adevărul indisponibilității e `availableForSale`, NICIODATĂ `inventoryQuantity`. La
     tracked=false cantitatea e cifră moartă (kiturile Belasil arată −7.048 și se vând normal);
     cu filtrul pe cantitate, 12 din 12 alarme Belasil erau false.
  2. Eșecul TĂCUT e interzis. Dacă o sursă nu răspunde, scriptul iese cu cod ≠ 0 și spune care.
     Un raport „0 probleme" produs de un fetch picat e exact modul în care incidentul a trăit
     două luni nedetectat.
  3. Dry-run implicit. Scrie doar cu --apply.
"""
import argparse, json, os, re, subprocess, sys, time
import datetime as dt
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.environ.get("SCRIPTURI_DIR", "/root/Scripturi"))

RO = dt.timezone(dt.timedelta(hours=3))
API = "2026-01"

# magazin Shopify -> cont Merchant Center
MAGAZINE = {
    "GRAN": "5677157050", "EST": "5676783307", "BELA": "5582663665",
    "OFER": "5813605780", "CZ": "5815161322", "CARP": "5810819833",
    "GEN": "5583322058", "BON": "5639173332",
}


class SursaIndisponibila(RuntimeError):
    """O sursă de adevăr nu a răspuns. Guard-ul se oprește — nu raportează 'curat'."""


def _kb(cheie):
    """Secretele: întâi mediul (cronul face `. .env`), apoi fișierul .env, apoi KB.
    Ordinea contează — un subprocess `uv run kb.py` din interiorul unui `uv run` e lent
    și poate eșua, iar guard-ul ar pica pe ceva ce era la îndemână."""
    v = os.environ.get(cheie)
    if v:
        return v.strip()
    for env in (os.path.join(os.environ.get("SCRIPTURI_DIR", "/root/Scripturi"), ".env"),):
        try:
            with open(env, encoding="utf-8") as f:
                for linie in f:
                    if linie.startswith(cheie + "="):
                        return linie.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    for cale in ("/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py",
                 os.path.expanduser("~/.claude/plugins/cache/team-intelligence/core/32b9408f9798/scripts/kb.py")):
        if os.path.exists(cale):
            try:
                r = subprocess.run(["uv", "run", cale, "secret-get", cheie],
                                   capture_output=True, text=True, timeout=90)
                if r.returncode == 0 and r.stdout.strip():
                    return r.stdout.strip()
            except Exception:
                pass
    raise SursaIndisponibila(f"secretul {cheie} nu a putut fi citit (nici env, nici .env, nici KB)")


# ─────────────────────────── Merchant Center ───────────────────────────
def token_merchant():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "grant_type": "refresh_token",
        "client_id": _kb("YOUTUBE_OAUTH_CLIENT_ID"),
        "client_secret": _kb("YOUTUBE_OAUTH_CLIENT_SECRET"),
        "refresh_token": _kb("MERCHANT_OAUTH_REFRESH_TOKEN")})
    tok = r.json().get("access_token")
    if not tok:
        raise SursaIndisponibila(f"OAuth Merchant a eșuat: HTTP {r.status_code} {r.text[:200]}")
    return tok


def oferte_merchant(tok, cont):
    """{variant_id: {availability, versionNumber, titlu}} pentru un cont Merchant Center."""
    out, pag = {}, None
    while True:
        p = {"pageSize": 1000}
        if pag:
            p["pageToken"] = pag
        r = requests.get(f"https://merchantapi.googleapis.com/products/v1/accounts/{cont}/products",
                         headers={"Authorization": f"Bearer {tok}"}, params=p, timeout=90)
        if r.status_code != 200:
            raise SursaIndisponibila(f"Merchant API cont {cont}: HTTP {r.status_code} {r.text[:200]}")
        d = r.json()
        for pr in d.get("products", []):
            m = re.search(r"_(\d+)$", pr.get("offerId") or "")
            if not m:
                continue
            vn = pr.get("versionNumber")
            a = pr.get("productAttributes") or {}
            out[m.group(1)] = {
                "avail": a.get("availability"),
                "titlu": str(a.get("title") or "")[:60],
                "fisa": dt.datetime.fromtimestamp(int(vn) / 1_000_000, dt.timezone.utc).astimezone(RO) if vn else None,
            }
        pag = d.get("nextPageToken")
        if not pag:
            return out


# ─────────────────────────── Shopify ───────────────────────────
def gql(mag, q, v=None):
    r = requests.post(f'https://{mag["shop"]}/admin/api/{API}/graphql.json',
                      headers={"X-Shopify-Access-Token": mag["token"], "Content-Type": "application/json"},
                      json={"query": q, "variables": v or {}}, timeout=90)
    if r.status_code != 200:
        raise SursaIndisponibila(f'Shopify {mag["shop"]}: HTTP {r.status_code} {r.text[:200]}')
    d = r.json()
    if "errors" in d:
        raise SursaIndisponibila(f'Shopify {mag["shop"]}: {json.dumps(d["errors"])[:300]}')
    return d["data"]


def publicatie_google(mag):
    d = gql(mag, "{ publications(first:40){ edges{ node{ id name } } } }")
    for e in d["publications"]["edges"]:
        if "Google" in e["node"]["name"]:
            return e["node"]["id"]
    return None


def produse_shopify(mag, pub_google):
    """[(product_gid, variant_id, sku, titlu, vandabil, pe_canal)] — toate produsele ACTIVE."""
    Q = """query($c:String){ products(first:100, after:$c, query:"status:active"){
      pageInfo{ hasNextPage endCursor }
      edges{ node{ id title publishedOnPublication(publicationId:"%s")
        variants(first:1){ edges{ node{ id sku price availableForSale } } } } } } }""" % pub_google
    cur, out = None, []
    while True:
        d = gql(mag, Q, {"c": cur})["products"]
        for e in d["edges"]:
            n = e["node"]
            ve = n["variants"]["edges"]
            if not ve:
                continue
            v = ve[0]["node"]
            out.append((n["id"], v["id"].split("/")[-1], v.get("sku"), n["title"][:56],
                        bool(v["availableForSale"]), bool(n["publishedOnPublication"]),
                        float(v.get("price") or 0)))
        if not d["pageInfo"]["hasNextPage"]:
            return out
        cur = d["pageInfo"]["endCursor"]


def excluderi_din_jurnal(mag, de_la, max_pagini=40):
    """{product_id: data} — produse scoase de pe canalul Google, din jurnalul Shopify."""
    url = f'https://{mag["shop"]}/admin/api/{API}/events.json'
    H = {"X-Shopify-Access-Token": mag["token"]}
    par = {"limit": 250, "filter": "Product", "created_at_min": de_la}
    pag, out, n = url, {}, 0
    while pag and n < max_pagini:
        n += 1
        for i in range(4):
            r = requests.get(pag, headers=H, params=par if pag == url else None, timeout=90)
            if r.status_code == 200:
                break
            time.sleep(3 * (i + 1))
        else:
            raise SursaIndisponibila(f'jurnal evenimente {mag["shop"]}: HTTP {r.status_code}')
        for e in r.json().get("events", []):
            m = re.sub(r"<[^>]+>", "", (e.get("message") or "").replace("&amp;", "&"))
            if "excluded a product from Google" in m:
                out[str(e.get("subject_id"))] = e["created_at"][:10]
        pag, par = None, None
        for part in r.headers.get("Link", "").split(","):
            if 'rel="next"' in part:
                pag = part.split(";")[0].strip(" <>")
    return out, n >= max_pagini


def publica(mag, pid, pub, on=True):
    m = "publishablePublish" if on else "publishableUnpublish"
    d = gql(mag, """mutation($id:ID!,$in:[PublicationInput!]!){ %s(id:$id,input:$in){
      userErrors{ field message } } }""" % m, {"id": pid, "in": [{"publicationId": pub}]})
    ue = d[m]["userErrors"]
    if ue:
        raise RuntimeError(f"{m} {pid}: {ue}")


# ─────────────────────────── logica ───────────────────────────
def verifica_magazin(pfx, cont, tok, de_la, aplica, jurnal):
    from core.stores import get_store
    mag = get_store(pfx)
    pub = publicatie_google(mag)
    if not pub:
        return {"magazin": pfx, "sarit": "nu are canal Google"}

    oferte = oferte_merchant(tok, cont)
    produse = produse_shopify(mag, pub)
    if not produse:
        raise SursaIndisponibila(f"{pfx}: Shopify a întors 0 produse active — imposibil")

    scoate, pune, revenite, cadouri = [], [], [], []
    # Jurnalul e necesar DOAR pentru direcția „pune". Dacă pică (Shopify dă 500 intermitent),
    # degradăm parțial: renunțăm la „pune" pe magazinul ăsta, dar „scoate" merge mai departe —
    # altfel o eroare tranzitorie ar lăsa produse epuizate să ardă buget încă o zi.
    excl, trunchiat, jurnal_ok = {}, False, True
    if any(vand and not canal for _, _, _, _, vand, canal, _p in produse):
        try:
            excl, trunchiat = excluderi_din_jurnal(mag, de_la)
        except SursaIndisponibila as e:
            jurnal_ok = False
            print(f"  [{pfx}] jurnal indisponibil ({e}) — sar peste direcția «pune»", file=sys.stderr)

    for pid, vid, sku, titlu, vandabil, pe_canal, pret in produse:
        of = oferte.get(vid)
        # SCOATE: indisponibil în Shopify, dar Merchant Center îl crede disponibil
        if not vandabil and of and of["avail"] == "IN_STOCK":
            scoate.append({"sku": sku, "titlu": titlu, "pid": pid,
                           "fisa": of["fisa"].strftime("%Y-%m-%d %H:%M") if of["fisa"] else None})
        # PUNE: vandabil, dar scos de pe canal de procedura de epuizare
        elif vandabil and not pe_canal:
            # Produsele CADOU (preț 0) sunt scoase de pe canale INTENȚIONAT — „Parfum surpriză"
            # e adăugat de un Flow după checkout, nu se cumpără și nu se face reclamă la el.
            # Fără regula asta, guard-ul l-a repus o dată în Shopping (18-aug-2026).
            if pret <= 0:
                cadouri.append({"sku": sku, "titlu": titlu, "pret": pret})
                continue
            if not jurnal_ok:
                revenite.append({"sku": sku, "titlu": titlu, "pid": pid, "scos": None})
                continue
            (pune if pid.split("/")[-1] in excl else revenite).append(
                {"sku": sku, "titlu": titlu, "pid": pid, "scos": excl.get(pid.split("/")[-1])})

    aplicate = {"scoase": 0, "puse": 0, "erori": []}
    if aplica:
        for it in scoate:
            try:
                publica(mag, it["pid"], pub, on=False)
                time.sleep(3)
                publica(mag, it["pid"], pub, on=True)
                aplicate["scoase"] += 1
            except Exception as e:
                aplicate["erori"].append(f'{it["sku"]}: {e}')
        for it in pune:
            try:
                publica(mag, it["pid"], pub, on=True)
                aplicate["puse"] += 1
            except Exception as e:
                aplicate["erori"].append(f'{it["sku"]}: {e}')

    return {"magazin": pfx, "cont": cont, "produse_active": len(produse),
            "oferte_merchant": len(oferte), "jurnal_trunchiat": trunchiat,
            "de_scos": scoate, "de_pus": pune,
            "scoase_din_alt_motiv": revenite, "cadouri_sarite": cadouri,
            "jurnal_indisponibil": not jurnal_ok, "aplicat": aplicate}


def main():
    ap = argparse.ArgumentParser(description="Ține feedul Google sincron cu stocul, în ambele direcții.")
    ap.add_argument("--magazine", default="all", help="lista de prefixe separate prin virgulă, sau 'all'")
    ap.add_argument("--zile-jurnal", type=int, default=120, help="cât înapoi citim jurnalul de excluderi")
    ap.add_argument("--apply", action="store_true", help="scrie efectiv; fără el = dry-run")
    ap.add_argument("--json", dest="ca_json", action="store_true")
    ap.add_argument("--stare", default=os.environ.get("GADS_GUARD_STATE",
                    "/root/Scripturi/data/gads_feed_guard.json"),
                    help="fișierul de stare pe care îl citește data_health.py")
    a = ap.parse_args()

    tinte = list(MAGAZINE.items()) if a.magazine == "all" else [
        (p.strip().upper(), MAGAZINE[p.strip().upper()]) for p in a.magazine.split(",")]
    de_la = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.zile_jurnal)).strftime("%Y-%m-%dT%H:%M:%SZ")

    tok = token_merchant()
    rez, esecuri = [], []
    for pfx, cont in tinte:
        try:
            rez.append(verifica_magazin(pfx, cont, tok, de_la, a.apply, None))
        except SursaIndisponibila as e:
            esecuri.append(f"{pfx}: {e}")
        except Exception as e:
            esecuri.append(f"{pfx}: neașteptat: {type(e).__name__}: {e}")

    # Starea pe disc: data_health.py judecă REZULTATUL, nu dacă a rulat jobul.
    try:
        os.makedirs(os.path.dirname(a.stare), exist_ok=True)
        with open(a.stare, "w", encoding="utf-8") as f:
            json.dump({
                "moment": dt.datetime.now(RO).isoformat(),
                "aplicat": a.apply,
                "esecuri": esecuri,
                # „ramase" = ce NU s-a putut repara. După un apply reușit e 0 — altfel
                # data_health ar fi roșu în fiecare zi tocmai fiindcă reparația a funcționat.
                "magazine": [{"magazin": r["magazin"],
                              "de_scos": len(r.get("de_scos", [])),
                              "de_pus": len(r.get("de_pus", [])),
                              "ramase_scos": len(r.get("de_scos", [])) - r.get("aplicat", {}).get("scoase", 0),
                              "ramase_pus": len(r.get("de_pus", [])) - r.get("aplicat", {}).get("puse", 0),
                              "jurnal_trunchiat": r.get("jurnal_trunchiat", False),
                              "jurnal_indisponibil": r.get("jurnal_indisponibil", False),
                              "erori": r.get("aplicat", {}).get("erori", []),
                              "scoase": r.get("aplicat", {}).get("scoase", 0),
                              "puse": r.get("aplicat", {}).get("puse", 0)}
                             for r in rez if not r.get("sarit")],
            }, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"[avertisment] nu am putut scrie starea în {a.stare}: {e}", file=sys.stderr)

    if a.ca_json:
        print(json.dumps({"rezultate": rez, "esecuri": esecuri,
                          "moment": dt.datetime.now(RO).isoformat()}, ensure_ascii=False, indent=1))
    else:
        print(f"{'MAGAZIN':<8}{'produse':>9}{'oferte':>8}{'DE SCOS':>9}{'DE PUS':>8}   note")
        print("-" * 78)
        for r in rez:
            if r.get("sarit"):
                print(f'{r["magazin"]:<8}{"-":>9}{"-":>8}{"-":>9}{"-":>8}   {r["sarit"]}')
                continue
            note = []
            if r.get("jurnal_indisponibil"):
                note.append("JURNAL INDISPONIBIL — «pune» sărit")
            if r["jurnal_trunchiat"]:
                note.append("jurnal trunchiat")
            if r.get("cadouri_sarite"):
                note.append(f'{len(r["cadouri_sarite"])} cadouri (preț 0) sărite')
            if r["scoase_din_alt_motiv"]:
                note.append(f'{len(r["scoase_din_alt_motiv"])} scoase din alt motiv (neatinse)')
            if r["aplicat"]["erori"]:
                note.append(f'{len(r["aplicat"]["erori"])} erori')
            print(f'{r["magazin"]:<8}{r["produse_active"]:>9}{r["oferte_merchant"]:>8}'
                  f'{len(r["de_scos"]):>9}{len(r["de_pus"]):>8}   {"; ".join(note)}')
        for r in rez:
            for it in r.get("de_scos", []):
                print(f'  SCOT  {r["magazin"]:<6}{str(it["sku"]):<24} fișă {it["fisa"]}  {it["titlu"]}')
            for it in r.get("de_pus", []):
                print(f'  PUN   {r["magazin"]:<6}{str(it["sku"]):<24} scos {it["scos"]}  {it["titlu"]}')
        if a.apply:
            print(f'\nAPLICAT: {sum(r.get("aplicat",{}).get("scoase",0) for r in rez)} scoase · '
                  f'{sum(r.get("aplicat",{}).get("puse",0) for r in rez)} puse')
        else:
            print("\n[DRY-RUN] --apply pentru a scrie.")

    if esecuri:
        print("\nSURSE INDISPONIBILE (guard-ul NU poate garanta că e curat):", file=sys.stderr)
        for e in esecuri:
            print("  " + e, file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
