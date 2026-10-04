# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test de regresie: `cs_actions.py cancel` întreabă Order Hub ÎNTÂI; `modify` avertizează pe eticheta veche (2-oct-2026).

`cancel` făcea direct orderCancel în Shopify, fără nicio etichetă anulată: pe o comandă cu AWB făcut de Order Hub
eticheta rămânea vie la curier (incidentul din 29-sep-2026). Fără rețea — Shopify, Order Hub și KB sunt dubluri,
iar răspunsurile Order Hub au formele reale ale rutei /api/depozit/anulare. `get_order` e cel adevărat, peste o
dublură de GraphQL care răspunde doar cu ce cere query-ul. Verifică:
  1. comanda cunoscută de Order Hub: anulează el; de aici se pun doar tagurile, și doar după ce Shopify arată
     comanda anulată — o citire picată după anulare nu oprește scriptul și nu se dă drept „neanulată";
  2. refuzurile (plecat, Shopify a refuzat după anularea etichetei) ies cu cod 3, nu scriu nimic și nu se arată ca
     reușită; starea comenzii se afirmă doar unde e cunoscută; o comandă deja anulată nu primește tagul anulat-cs;
  3. orderCancel direct doar la 404 `necunoscuta`, și doar pe o comandă fără etichetă; fără un răspuns valid de la
     Order Hub --apply iese cu cod 2; un răspuns pierdut după o cerere de execuție e stare necunoscută;
  4. `modify` spune că eticheta rămâne cu datele vechi când comanda are AWB — și când Order Hub nu răspunde;
  5. la Order Hub ajunge numele canonic o['name'] („123456 --store TST” → TST123456), iar o căutare care întoarce altă
     comandă („TST 123456” → TST100200) oprește cancel și modify cu cod 2, fără nicio scriere;
  6. kb.py se găsește și când scriptul e pornit din alt folder.
Comenzile, magazinul și cheia sunt inventate.

  uv run test_cancel_oh.py
"""
import io
import json
import os
import sys
from contextlib import redirect_stdout

for _s in (sys.stdout, sys.stderr):   # pe o consolă cp1252, „─ ✅ ⛔" din numele verificărilor nu trebuie să oprească testul
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cs_actions as C  # noqa: E402

OH = C._oh_client()
FAILS = []


def check(nume, ok, extra=""):
    print(("  ok   " if ok else "  PICAT ") + nume + (("  — " + str(extra)) if extra and not ok else ""))
    if not ok:
        FAILS.append(nume)


CHEIE = "ohsvc.cs." + "x" * 40
BAZA = {"comanda": "TST1", "order_id": 1, "magazin": "Magazin Test", "pasi": [], "plan": []}
PROBA = dict(BAZA, ok=True, rezultat="previzualizare", mesaj="", anulata=False, plan=[
    "AWB de anulat: 70000000001 (DPD, cont dpd-ro-arona)", "Comandă: se anulează în magazin", "Stoc: se repune",
    "Bani: plătită cu cardul · rambursare + storno factură", "Tag: anulat-depozit"])
FACUT = dict(BAZA, ok=True, rezultat="anulata", mesaj="Comandă: TST1 · ANULATĂ · AWB anulat: 70000000001", anulata=True,
             pasi=[{"pas": "void", "ok": True, "text": "AWB anulat: 70000000001"},
                   {"pas": "anulare", "ok": True, "text": "Comandă: anulată în magazin; stoc repus"}])
PLECAT = dict(BAZA, ok=False, rezultat="plecat", mesaj="Colet: 70000000001 (in_transit) · plecat la curier · nimic făcut",
              anulata=False, plecate=["70000000001 (in_transit)"])
# magazin cu aplicația Order Hub închisă în Shopify: eticheta se anulează, comanda rămâne deschisă, tichet CS
SHOPIFY_REFUZ = dict(BAZA, ok=False, rezultat="shopify_refuz", anulata=False, etichete_anulate=["70000000001"],
                     mesaj="AWB anulat: 70000000001 · Comandă: TST1 · NEANULATĂ (Shopify: 403) · tichet CS: deschis",
                     pasi=[{"pas": "void", "ok": True, "text": "AWB anulat: 70000000001"},
                           {"pas": "anulare", "ok": False, "text": "Comandă: NEANULATĂ (Shopify: 403)"}])
EROARE = dict(BAZA, ok=False, rezultat="eroare", anulata=False,
              mesaj="Anulare: eroare (x) · stare: de verificat în Order Hub")
ANULATA_CU_ETICHETA = dict(BAZA, ok=False, rezultat="refuzat", anulata=True,
                           mesaj="Comandă: TST1 · anulată · AWB: NEANULAT (70000000001: refuzat de curier) · colet: probabil la curier")
ETICHETE = dict(BAZA, ok=True, rezultat="etichete_anulate", anulata=True,
                mesaj="Comandă: TST1 · deja anulată · AWB anulat: 70000000001")
DEJA_PROBA = dict(BAZA, ok=False, rezultat="deja_anulata", anulata=True, plan=["AWB de anulat: niciunul viu"],
                  mesaj="Comandă: TST1 · deja anulată · AWB viu: niciunul · nimic de făcut")
BANI = dict(FACUT, pasi=FACUT["pasi"] + [{"pas": "bani", "ok": False, "text": "Bani: rambursare neconfirmată · factură: nestornată"}])
NECUNOSCUTA = (404, {"detail": {"rezultat": "necunoscuta", "mesaj": "Cod: TST1 · necunoscut în Order Hub"}})
HOLD_PROBA = dict(BAZA, ok=True, rezultat="previzualizare", mesaj="", anulata=False, plan=[
    "AWB de anulat: 70000000001 (DPD, cont dpd-ro-arona)", "Comandă: rămâne · hold (nu pleacă acum)"])
HOLD_FARA = dict(BAZA, ok=True, rezultat="previzualizare", mesaj="", anulata=False, plan=[
    "AWB de anulat: niciunul viu", "Comandă: rămâne · hold (nu pleacă acum)"])

RASPUNS, CERERI, MUTATII, REST, TAGURI, CITIRI = [None], [], [], [], [], [0]
LUME_0 = {"nume": "TST1", "anulata": False, "tracking": ["70000000001"], "confirma": True, "tag_pica": False, "citire_pica": None,
          "confirma_dupa": 0, "anulata_de_la": None, "nota": "", "taguri": []}
DEJA_OK = dict(BAZA, ok=True, rezultat="deja_anulata", anulata=True,
               mesaj="Comandă: TST1 · deja anulată · AWB viu: niciunul",
               pasi=[{"pas": "void", "ok": True, "text": "AWB: niciunul viu"},
                     {"pas": "anulare", "ok": True, "text": "Comandă: era deja anulată"}])
FRISBO = dict(BAZA, ok=False, rezultat="refuzat", anulata=False,
              mesaj="Etichetă: 70000000001 · a depozitului Frisbo · anulare: doar la Frisbo")
STRAINA = dict(BAZA, ok=False, rezultat="eticheta_necunoscuta", anulata=False,
               mesaj="Etichetă: 80000000012 · necunoscută în Order Hub · comanda: TST1 · nimic făcut")
LUME = dict(LUME_0)


def fake_oh(method, url, headers, body=None, timeout=90):
    CERERI.append((url[len(OH.BASE):], dict(body), headers.get("Authorization")))
    impus = RASPUNS[0](url[len(OH.BASE):], body) if callable(RASPUNS[0]) else RASPUNS[0]
    if impus is not None:
        s, corp = impus
    elif headers.get("Authorization") != "Bearer " + CHEIE:
        s, corp = 401, {"detail": "Cheie de serviciu: invalidă"}
    else:
        s, corp = 200, (PROBA if body["dry_run"] else FACUT)
    if s == 200 and isinstance(corp, dict) and corp.get("rezultat") == "anulata" and corp.get("ok") and LUME["confirma"]:
        LUME["anulata_de_la"] = CITIRI[0] + LUME["confirma_dupa"]   # Order Hub a anulat-o și în Shopify
    return s, (corp if isinstance(corp, str) else json.dumps(corp))


def fake_sgql(prefix, query, variables=None):
    if "orders(first:1" in query:      # sub get_order cel adevărat: răspunsul conține doar ce cere query-ul
        CITIRI[0] += 1
        if LUME["citire_pica"] and CITIRI[0] > 1:     # prima citire merge; cele de după anulare pică
            if LUME["citire_pica"] == "exit":
                sys.exit("GraphQL: throttled")        # ca sgql adevărat pe o eroare GraphQL
            return {}                                 # ca sgql adevărat pe un 5xx necitit → KeyError în get_order
        anulata = LUME["anulata"] or (LUME["anulata_de_la"] is not None and CITIRI[0] > LUME["anulata_de_la"])
        nod = {"id": "gid://shopify/Order/1", "name": LUME["nume"], "cancelledAt": "2026-10-01T10:00:00Z" if anulata else None,
               "displayFinancialStatus": "PENDING", "displayFulfillmentStatus": "FULFILLED", "lineItems": {"edges": []}}
        if "note tags" in query:
            nod.update(note=LUME["nota"], tags=list(LUME["taguri"]))
        if "fulfillments(first:10){ status trackingInfo(first:5){ number } }" in query:
            nod["fulfillments"] = ([{"status": "CANCELLED", "trackingInfo": [{"number": "70000000009"}]},
                                    {"status": "ERROR", "trackingInfo": [{"number": "70000000008"}]}]
                                   + [{"status": "SUCCESS", "trackingInfo": [{"number": t}]} for t in LUME["tracking"]])
        return {"orders": {"edges": [{"node": nod}]}}
    if "tagsAdd" in query and LUME["tag_pica"]:
        sys.exit("GraphQL: throttled")
    MUTATII.append("orderCancel" if "orderCancel" in query else "tagsAdd")
    if "tagsAdd" in query:
        TAGURI.append(list((variables or {}).get("t") or []))
    return {"orderCancel": {"userErrors": []}, "tagsAdd": {"userErrors": []}}


OH._http = fake_oh
C.sgql = fake_sgql
C.srest = lambda prefix, method, path, body=None: REST.append((method, path)) or (200, {})
KB = {}
C._kb_secret = lambda name: KB.get(name, "")
C.time.sleep = lambda s: None


def ruleaza(*args, op="cancel", raspuns=None, cheie=CHEIE, **lume):
    del CERERI[:], MUTATII[:], REST[:], TAGURI[:]
    CITIRI[0] = 0
    LUME.clear(); LUME.update(LUME_0); LUME.update(lume)
    RASPUNS[0] = raspuns
    os.environ.pop(OH.TOKEN_ENV, None)
    if cheie:
        os.environ[OH.TOKEN_ENV] = cheie
    sys.argv = ["cs_actions.py", op, "--agent", "Raluca", "--order", "TST1", "--store", "TST"] + list(args)
    buf, cod = io.StringIO(), 0
    with redirect_stdout(buf):
        try:
            C.main()
        except SystemExit as e:
            cod = e.code if isinstance(e.code, int) else (0 if e.code is None else "text: %s" % e.code)
    return cod, buf.getvalue()


print("1. comanda cunoscută de Order Hub")
cod, out = ruleaza("--note", "clientul a sunat")
cale, corp, auth = CERERI[0]
check("probă: /api/depozit/anulare cu dry_run, agentul, motivul, nota, eticheta VIE din Shopify, restock implicit", cod == 0
      and cale == "/api/depozit/anulare" and auth == "Bearer " + CHEIE and corp["dry_run"] is True
      and corp["actiune"] == "anulare" and corp["cod"] == corp["comanda"] == "TST1" and corp["utilizator"] == "Raluca"
      and corp["motiv"] == "CS: customer" and corp["nota"] == "clientul a sunat" and corp["awb"] == "70000000001"
      and corp["restock"] is True and not MUTATII, corp)
check("probă: planul Order Hub (inclusiv banii) și magazinul sunt arătate", "prin ORDER HUB" in out and "[DRY-RUN]" in out
      and "(Magazin Test)" in out and "rambursare + storno" in out and "--apply ca să anulezi" in out, out)
cod, out = ruleaza(raspuns=(200, FACUT))
check("o probă nu scrie nimic, orice ar răspunde Order Hub", not MUTATII and CERERI[0][1]["dry_run"] is True, MUTATII)
cod, out = ruleaza("--apply", "--no-restock", "--refund")
check("--apply: Order Hub anulează; după confirmarea din Shopify se pun doar tagurile (agentul + anulat-cs)", cod == 0
      and CERERI[0][1]["dry_run"] is False and CERERI[0][1]["restock"] is False and MUTATII == ["tagsAdd"]
      and TAGURI == [["Raluca", "anulat-cs"]] and "✅ ANULAT TST1" in out and "--refund nu se aplică" in out
      and "⚠" not in out, (cod, MUTATII, TAGURI, out))
cod, out = ruleaza("--apply", confirma_dupa=1)
check("Shopify arată anularea abia la a doua citire: tot confirmată, cu taguri", cod == 0 and "✅ ANULAT TST1" in out
      and MUTATII == ["tagsAdd"], (cod, MUTATII, out))
KB[OH.TOKEN_ENV] = CHEIE
cod, out = ruleaza(cheie="")
KB.clear()
check("cheia se ia din KB când nu e în env", cod == 0 and CERERI and CERERI[0][2] == "Bearer " + CHEIE, CERERI)
cod, out = ruleaza("--apply", confirma=False)
check("Order Hub zice „anulata”, Shopify nu: fără ✅, fără taguri, cu avertisment", cod == 0 and not MUTATII
      and "✅ ANULAT" not in out and "Shopify o arată încă NEANULATĂ" in out and "Tagurile nu s-au pus" in out,
      (cod, MUTATII, out))
for nume, cum in (("răspuns necitit (KeyError)", "gol"), ("eroare GraphQL (sys.exit)", "exit")):
    cod, out = ruleaza("--apply", citire_pica=cum)
    check("citirea de confirmare pică după ce Order Hub a anulat — %s: se spune, fără traceback și fără „neanulată”" % nume,
          cod == 0 and not MUTATII and CERERI[0][1]["dry_run"] is False and "Shopify n-a putut fi citit" in out
          and "Tagurile nu s-au pus" in out and "✅ ANULAT" not in out and "NEANULATĂ" not in out, (cod, MUTATII, out))
cod, out = ruleaza("--apply", raspuns=(200, BANI))
check("anulată, dar rambursarea neconfirmată: avertisment lângă reușită", cod == 0 and "✅ ANULAT TST1" in out
      and "pași neconfirmați" in out and "rambursare neconfirmată" in out, out)
cod, out = ruleaza("--apply", tag_pica=True)
check("tagurile pică după anulare: reușita e deja spusă", "✅ ANULAT TST1" in out and cod != 0 and not MUTATII, (cod, out))

print("2. refuzuri și comenzi deja anulate")
cod, out = ruleaza("--apply", raspuns=(200, PLECAT))
check("colet plecat: cod 3, nicio scriere în Shopify, nu apare ca anulată", cod == 3 and not MUTATII
      and "NU s-a anulat" in out and "✅" not in out, (cod, out))
cod, out = ruleaza(raspuns=(200, PLECAT))
check("același refuz la probă: cod 0", cod == 0 and "NU s-a anulat" in out and not MUTATII, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, SHOPIFY_REFUZ))
check("Shopify refuză după anularea etichetei: cod 3, fără taguri, comanda rămasă deschisă e spusă", cod == 3
      and not MUTATII and "NU s-a anulat" in out and "✅ ANULAT" not in out and "DESCHISĂ în Shopify" in out
      and "tichet CS: deschis" in out, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, EROARE))
check("Order Hub răspunde „eroare”: stare necunoscută, nu „comanda NU s-a anulat”", cod == 3 and not MUTATII
      and "NU s-a anulat" not in out and "NECUNOSCUTĂ" in out, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, ANULATA_CU_ETICHETA), anulata=True)
check("comandă deja anulată, cu o etichetă pe care curierul n-o mai anulează: nu se spune „NU s-a anulat”", cod == 3
      and not MUTATII and "NU s-a anulat" not in out and "ERA deja anulată" in out, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, ETICHETE), anulata=True)
check("comandă deja anulată cu etichetă vie: Order Hub îi anulează eticheta, fără tagul anulat-cs", cod == 0
      and len(CERERI) == 1 and not MUTATII and "era deja anulată" in out and "✅ ANULAT" not in out, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, DEJA_OK))
check("reîncercare: Order Hub zice „deja anulată”, Shopify o arată deschisă → ⚠, nu reușită curată", cod == 0
      and "Shopify o arată încă NEANULATĂ" in out and not MUTATII, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, DEJA_OK), anulata=True)
check("…iar cu Shopify anulat: fără ⚠", cod == 0 and "⚠" not in out and not MUTATII, (cod, out))
cod, out = ruleaza("--apply", raspuns=(200, DEJA_OK), anulata=True, nota="client nou\n[ANULAT depozit · Raluca] CS: customer")
check("reîncercare după o anulare neconfirmată a aceluiași agent, acum confirmată: tagurile ei se pun", cod == 0
      and TAGURI == [["Raluca", "anulat-cs"]] and "anularea de dinainte" in out, (cod, TAGURI, out))
cod, out = ruleaza("--apply", raspuns=(200, DEJA_OK), anulata=True, nota="[ANULAT depozit · Oana] CS: customer")
check("…dar nu pe anularea altui agent", cod == 0 and not TAGURI, (cod, TAGURI, out))
cod, out = ruleaza("--apply", raspuns=(200, DEJA_OK), anulata=True, nota="[ANULAT depozit · Raluca] CS: customer",
                   taguri=["Raluca", "anulat-cs"])
check("…și nu de două ori", cod == 0 and not TAGURI, (cod, TAGURI, out))
straina = lambda cale, c: (200, STRAINA) if c.get("awb") == "80000000012" else None  # noqa: E731
cod, out = ruleaza("--apply", raspuns=straina, tracking=["70000000001", "80000000012"])
check("a doua etichetă din Shopify e străină lui Order Hub: nimic anulat, cod 3", cod == 3 and not MUTATII
      and all(c[1]["dry_run"] is True for c in CERERI) and "are în Shopify și eticheta 80000000012" in out
      and CERERI[-1][1]["awb"] == "70000000001", (cod, CERERI, out))
cod, out = ruleaza(raspuns=(200, DEJA_PROBA), anulata=True)
check("probă pe o comandă deja anulată: se spune, fără semn de refuz", cod == 0 and "era deja anulată" in out
      and "⛔" not in out, out)

print("3. orderCancel direct doar la necunoscuta")
cod, out = ruleaza("--apply", raspuns=NECUNOSCUTA, tracking=[])
check("necunoscuta, comandă fără etichetă vie (fulfillment-ul anulat nu contează): orderCancel + taguri, ca până acum",
      cod == 0 and MUTATII == ["orderCancel", "tagsAdd"] and len(CERERI) == 1 and CERERI[0][1]["awb"] == "", (cod, MUTATII, out))
cod, out = ruleaza("--apply", raspuns=NECUNOSCUTA)
check("necunoscuta, dar comanda are AWB: nu se anulează de aici, trimite la xconnector.py order-cancel", cod == 3
      and not MUTATII and "are AWB (70000000001)" in out and "order-cancel" in out, (cod, MUTATII, out))
for nume, kw in (("cheie lipsă", dict(cheie="")), ("cheie greșită (401)", dict(cheie="ohsvc.cs." + "y" * 40)),
                 ("403", dict(raspuns=(403, {"detail": "Cheie de serviciu: fără dreptul «depozit»"}))),
                 ("404 de rută", dict(raspuns=(404, {"detail": "Not Found"}))),
                 ("404 cu alt rezultat", dict(raspuns=(404, {"detail": {"rezultat": "alta"}}))),
                 ("409", dict(raspuns=(409, {"detail": "Cod: TST1 · 2 comenzi în Order Hub"})))):
    cod, out = ruleaza("--apply", tracking=[], **kw)
    check("%s: --apply refuzat cu cod 2, nicio scriere" % nume, cod == 2 and not MUTATII
          and "cancel refuzat, nu s-a scris nimic" in out, (cod, MUTATII, out))
for nume, r in (("500", (500, "Internal Server Error")), ("timeout", ("ERR", "TimeoutError: timed out"))):
    cod, out = ruleaza("--apply", raspuns=r)
    check("%s după o cerere de execuție: stare necunoscută, nu „nu s-a scris nimic”" % nume, cod == 2 and not MUTATII
          and len(CERERI) == 1 and "POATE să fi fost executată" in out and "nu s-a scris nimic" not in out, (cod, out))
cod, out = ruleaza("--apply", cheie="")
check("cheie lipsă: nicio cerere către Order Hub", not CERERI)
cod, out = ruleaza(cheie="", tracking=[])
check("fără răspuns, probă: planul vechi cu avertisment", cod == 0 and not MUTATII and "Fără un răspuns valid" in out
      and "DRY-RUN" in out, out)
cod, out = ruleaza("--apply", cheie="", anulata=True)
check("fără răspuns, pe o comandă deja anulată: tot cod 2 — etichetele rămase n-au putut fi verificate", cod == 2
      and not MUTATII, (cod, out))

print("4. modify")
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", op="modify", raspuns=(200, HOLD_PROBA))
check("probă pe o comandă cu AWB: eticheta rămâne cu datele vechi → awb-regen (întrebarea e proba unei opriri)", cod == 0
      and not REST and not MUTATII and CERERI[0][0] == "/api/depozit/anulare" and CERERI[0][1]["actiune"] == "hold"
      and CERERI[0][1]["dry_run"] is True and CERERI[0][1]["comanda"] == CERERI[0][1]["cod"] == "TST1"
      and "are deja AWB (70000000001 (DPD, cont dpd-ro-arona))" in out and "awb-regen --order TST1" in out, out)
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", "--apply", op="modify", raspuns=(200, HOLD_PROBA))
check("--apply: adresa se schimbă, avertismentul rămâne, Order Hub primește doar probe", cod == 0 and len(REST) == 1
      and MUTATII == ["tagsAdd"] and TAGURI == [["Raluca", "modificata-cs"]] and "are deja AWB" in out
      and all(c[1]["dry_run"] is True for c in CERERI), out)
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", op="modify", raspuns=(200, HOLD_FARA), tracking=[])
check("comandă fără AWB nicăieri: fără avertismentul de etichetă", cod == 0 and "are deja AWB" not in out
      and "⚠ Order Hub" not in out, out)
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", op="modify", raspuns=(200, HOLD_FARA))
check("Order Hub n-are etichetă, dar Shopify arată una (făcută de mână): se anunță", cod == 0
      and "are deja AWB (70000000001)" in out, out)
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", op="modify", raspuns=(200, FRISBO))
check("eticheta depozitului Frisbo: se spune ce zice Order Hub, fără sfatul de refacere", cod == 0
      and "a depozitului Frisbo" in out and "awb-regen" not in out, out)
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", op="modify", raspuns=(200, PLECAT))
check("colet plecat: spune că modificarea nu mai ajunge pe el", "Coletul a plecat (70000000001 (in_transit))" in out, out)
for nume, kw in (("Order Hub nu răspunde", dict(cheie="")), ("Order Hub n-o cunoaște", dict(raspuns=NECUNOSCUTA))):
    cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", "--apply", op="modify", **kw)
    check("%s: modify merge, iar eticheta pe care o arată Shopify e anunțată" % nume, cod == 0 and len(REST) == 1
          and "are deja AWB (70000000001)" in out, (cod, out))
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", "--apply", op="modify", tracking=[], cheie="")
check("Order Hub nu răspunde și Shopify nu arată nicio etichetă: ⚠ „nu se știe”, ca să nu se confirme automat",
      cod == 0 and len(REST) == 1 and "⚠ Order Hub n-a răspuns" in out and "nu se știe" in out, (cod, out))
cod, out = ruleaza("--address", "Str Noua 9", "--city", "Cluj", "--apply", op="modify", tracking=[], raspuns=NECUNOSCUTA)
check("Order Hub n-o cunoaște și Shopify nu arată nicio etichetă: fără avertisment", cod == 0 and len(REST) == 1
      and "⚠" not in out.replace("⚠ TST1 e FULFILLED", ""), (cod, out))

print("5. numele canonic al comenzii (căutarea Shopify e largă, Order Hub caută doar numele exact)")


def oh_exact(cale, c):
    """Ca `gaseste` din Order Hub: o comandă se găsește doar după numele exact (cu sau fără „#”)."""
    if c["cod"].lstrip("#").upper() != "TST123456":
        return 404, {"detail": {"rezultat": "necunoscuta", "mesaj": "Cod: %s · necunoscut în Order Hub" % c["cod"]}}
    return 200, dict(PROBA if c["dry_run"] else FACUT, comanda="TST123456")


for intrare in (["--order", "123456"], ["--order", "TST-123456"], ["--order", "tst123456"]):
    cod, out = ruleaza(*(intrare + ["--apply"]), raspuns=oh_exact, nume="TST123456", tracking=[])
    check("cancel %s --store TST: la Order Hub ajunge TST123456, el anulează; fără orderCancel direct" % " ".join(intrare),
          cod == 0 and CERERI and all(c[1]["cod"] == c[1]["comanda"] == "TST123456" for c in CERERI)
          and CERERI[-1][1]["dry_run"] is False and MUTATII == ["tagsAdd"] and "ANULEZ TST123456" in out
          and "✅ ANULAT TST123456" in out and "nu cunoaște" not in out, (cod, CERERI, MUTATII, out))
cod, out = ruleaza("--order", "123456", raspuns=oh_exact, nume="TST123456", tracking=[])
check("…și proba arată numele canonic în rândul ANULEZ", cod == 0 and "[DRY-RUN] ANULEZ TST123456" in out
      and not MUTATII, out)
for intrare, alta in ((["--order", "TST 123456"], "TST100200"), (["--order", "23456"], "TST123456")):
    for op, extra in (("cancel", []), ("modify", ["--address", "Str Noua 9", "--city", "Cluj"])):
        for aplica in ([], ["--apply"]):
            cod, out = ruleaza(*(intrare + extra + aplica), op=op, raspuns=oh_exact, nume=alta, tracking=[])
            check("%s %s%s: Shopify întoarce %s, nu comanda cerută → cod 2, nimic la Order Hub, nimic scris"
                  % (op, " ".join(intrare), " --apply" if aplica else "", alta), cod == 2 and not CERERI and not MUTATII
                  and not REST and ("comanda %s, care NU e cea cerută" % alta) in out, (cod, CERERI, MUTATII, REST, out))
cod, out = ruleaza("--order", "123456", "--address", "Str Noua 9", "--city", "Cluj", "--apply", op="modify",
                   raspuns=lambda cale, c: (200, HOLD_PROBA) if c["cod"] == "TST123456" else NECUNOSCUTA, nume="TST123456")
check("modify 123456: proba la Order Hub pe TST123456, MODIFIC cu numele canonic, sfatul de refacere tot pe el", cod == 0
      and CERERI and all(c[1]["cod"] == "TST123456" for c in CERERI) and "MODIFIC TST123456" in out
      and "✅ MODIFICAT TST123456" in out and "awb-regen --order TST123456" in out, (cod, CERERI, out))


print("5b. aceleași cifre, alt prefix")
cod, out = ruleaza("--order", "TSX123456", raspuns=oh_exact, nume="TST123456", tracking=[])
check("R6 cancel: aceleași cifre, alt prefix (TSX123456 → TST123456): cod 2, nimic la Order Hub", cod == 2
      and not CERERI and not MUTATII and "NU e cea cerută" in out, (cod, CERERI, out[-300:]))

print("6. kb.py")
getcwd = C.os.getcwd
C.os.getcwd = lambda: os.path.join(os.path.abspath(os.sep), "nu-exista-%d" % os.getpid())   # niciun strămoș cu repo-ul
try:
    kb = C._kb_path()
finally:
    C.os.getcwd = getcwd
check("kb.py se găsește și din alt folder (plugins/core/scripts)", bool(kb) and os.path.exists(kb)
      and kb.replace("\\", "/").endswith("plugins/core/scripts/kb.py"), kb)

print()
if FAILS:
    print("PICATE: %d — %s" % (len(FAILS), "; ".join(FAILS)))
    sys.exit(1)
print("TOATE TREC")
