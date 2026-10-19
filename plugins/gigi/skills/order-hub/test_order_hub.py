# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Teste pentru order_hub.py, fără rețea: un Order Hub fals pe 127.0.0.1 servește schema și răspunsuri de formă reală.
Verifică: tipurile din schemă (fără ghicit), --apply fără confirmare nu trimite nimic, codurile de ieșire (făcut,
nimic scris, refuz, parțial, necunoscut), „netrimis" vs „trimis fără răspuns", redirect neurmat, cheia nu apare.
Comenzile, cheia și confirmările sunt inventate.

  uv run test_order_hub.py
"""
import io
import json
import os
import socket
import sys
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CHEIE = "ohsvc.cs-sb." + "s" * 40
os.environ["OH_CS_SB_TOKEN"] = CHEIE
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import order_hub as O  # noqa: E402

SCHEMA = {"actiuni": [
    {"nume": "awb-nou", "familie": "awb", "tinta": "comanda",
     "parametri": {"colete": {"tip": "int", "min": 1, "max": 99, "obligatoriu": False},
                   "ridicare": {"tip": "enum", "valori": ["da", "nu"], "implicit": "da", "obligatoriu": False}}},
    {"nume": "taguri", "familie": "extra", "tinta": "comanda",
     "parametri": {"adauga": {"tip": "lista", "elemente": {"tip": "text"}, "obligatoriu": False},
                   "restock": {"tip": "bool", "obligatoriu": False}}},
    {"nume": "ciudat", "familie": "extra", "tinta": "comanda", "parametri": {"x": {"tip": "nou", "obligatoriu": False}}},
    {"nume": "schimb", "familie": "comenzi", "tinta": "comanda",
     "parametri": {"produse": {"tip": "lista", "obligatoriu": True,
                               "elemente": {"tip": "obiect", "campuri": {"sku": {"tip": "text"},
                                                                       "cantitate": {"tip": "int"}}}}}},
    {"nume": "comanda-noua", "familie": "comenzi", "tinta": "magazin",
     "comune": ["magazin", "utilizator", "motiv", "dry_run", "confirmare", "peste"],
     "parametri": {"reducere": {"tip": "obiect", "obligatoriu": False, "campuri": {
         "tip": {"tip": "enum", "valori": ["procent", "suma"], "obligatoriu": True},
         "valoare": {"tip": "num", "min": 0, "max": 100000, "obligatoriu": True},
         "eticheta": {"tip": "text", "obligatoriu": False}}}}},
    {"nume": "rambursare", "familie": "bani", "tinta": "comanda",
     "parametri": {"suma": {"tip": "num", "obligatoriu": False, "min": 0.01, "max": 1000000},
                   "greutate_kg": {"tip": "num", "obligatoriu": False}}},
]}
PRIMITE = []
RASPUNS = {}
OPERATII = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _trimite(self, status, corp):
        b = json.dumps(corp).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        assert self.headers.get("Authorization") == "Bearer " + CHEIE
        if self.path == "/api/cs/actiuni":
            return self._trimite(200, SCHEMA)
        if self.path.startswith("/api/cs/operatie/") and self.path.rsplit("/", 1)[-1] in OPERATII:
            return self._trimite(200, OPERATII[self.path.rsplit("/", 1)[-1]])
        self._trimite(404, {"detail": {"rezultat": "necunoscuta"}})

    def do_POST(self):
        corp = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        PRIMITE.append((self.path, corp))
        r = RASPUNS.get("next")
        if r == "dormi":
            time.sleep(3)
            return self._trimite(200, {"ok": True})
        if r == "redirect":
            self.send_response(302); self.send_header("Location", "http://altundeva.invalid/"); self.end_headers(); return
        status, d = r
        self._trimite(status, d)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
O.BASE = "http://127.0.0.1:%d" % srv.server_address[1]
FAILS = []


def check(nume, ok, extra=""):
    print(("✅ " if ok else "❌ ") + nume + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        FAILS.append(nume)


def run(*argv, raspuns=None):
    RASPUNS["next"] = raspuns
    PRIMITE.clear()
    out = io.StringIO()
    with redirect_stdout(out):
        try:
            cod = O.main(list(argv))
        except SystemExit as e:
            cod = e.code
    return cod, out.getvalue()


BAZA = ("awb-nou", "--comanda", "TST1001", "--utilizator", "Ana", "--motiv", "test")
PROBA_OK = (200, {"ok": True, "rezultat": "previzualizare", "scris": "nimic", "actiune": "awb-nou",
                  "plan": ["awb: nou"], "blocaje": [], "peste_necesar": [], "confirmare": "v1.c0nf"})

# 1. probă: tipurile din schemă
cod, out = run(*BAZA, "--colete", "2", "--ridicare", "nu", raspuns=PROBA_OK)
check("1 probă: cod 0", cod == 0, (cod, out))
check("1 colete trimis ca int, ridicare ca enum, dry_run true",
      PRIMITE and PRIMITE[0][1] == {"utilizator": "Ana", "dry_run": True, "comanda": "TST1001", "motiv": "test",
                                    "colete": 2, "ridicare": "nu"}, PRIMITE)
check("1 ruta e /api/cs/awb-nou", PRIMITE and PRIMITE[0][0] == "/api/cs/awb-nou")
check("1 ultima linie e pentru mașină", out.strip().splitlines()[-1].startswith("OH_REZULTAT rezultat=previzualizare"))
cod, out = run(*BAZA, "--colete", "2.5", raspuns=PROBA_OK)
check("1 int greșit: cod 2, nimic trimis", cod == 2 and PRIMITE == [], (cod, PRIMITE))
cod, out = run(*BAZA, "--ridicare", "poate", raspuns=PROBA_OK)
check("1 enum greșit: cod 2, nimic trimis", cod == 2 and PRIMITE == [])
cod, out = run("taguri", "--comanda", "TST1", "--utilizator", "Ana", "--adauga", "a, b", "--restock", "nu",
               raspuns=(200, {"ok": True, "rezultat": "previzualizare", "blocaje": []}))
check("1 listă și bool din da/nu", PRIMITE and PRIMITE[0][1].get("adauga") == ["a", "b"]
      and PRIMITE[0][1].get("restock") is False, PRIMITE)
cod, out = run("taguri", "--comanda", "TST1", "--utilizator", "Ana", "--restock", "false-ish", raspuns=PROBA_OK)
check("1 bool greșit: cod 2", cod == 2 and PRIMITE == [])
cod, out = run("ciudat", "--comanda", "TST1", "--utilizator", "Ana", "--x", "1", raspuns=PROBA_OK)
check("1 tip necunoscut în schemă: cod 2, nu ghicește", cod == 2 and PRIMITE == [])

cod, out = run("schimb", "--comanda", "TST1", "--utilizator", "Ana", "--produse",
               '[{"sku": "TST-A", "cantitate": 2}, {"sku": "TST-B", "cantitate": 1}]', raspuns=PROBA_OK)
check("1 listă JSON de obiecte trimisă ca atare", PRIMITE and PRIMITE[0][1].get("produse") ==
      [{"sku": "TST-A", "cantitate": 2}, {"sku": "TST-B", "cantitate": 1}], PRIMITE)
cod, out = run("schimb", "--comanda", "TST1", "--utilizator", "Ana", "--produse", "TST-A,2", raspuns=PROBA_OK)
check("1 listă de obiecte fără JSON: cod 2, nimic trimis", cod == 2 and PRIMITE == [], (cod, out))
cod, out = run("schimb", "--comanda", "TST1", "--utilizator", "Ana", "--produse", '[{"sku": "TST-A"}, 3]',
               raspuns=PROBA_OK)
check("1 element care nu e obiect: cod 2", cod == 2 and PRIMITE == [], (cod, out))
cod, out = run("taguri", "--comanda", "TST1", "--utilizator", "Ana", "--adauga", '["a,b", "c"]',
               raspuns=(200, {"ok": True, "rezultat": "previzualizare", "blocaje": []}))
check("1 listă JSON de texte păstrează virgula din element", PRIMITE and PRIMITE[0][1].get("adauga") == ["a,b", "c"],
      PRIMITE)

cod, out = run("schimb", "--comanda", "TST1", "--utilizator", "Ana", "--produse",
               str([{"sku": "TST-A", "cantitate": 2}]), raspuns=PROBA_OK)
check("1 listă dată ca structură (str() din Second Brain) se citește", PRIMITE and PRIMITE[0][1].get("produse") ==
      [{"sku": "TST-A", "cantitate": 2}], (cod, out, PRIMITE))
cod, out = run("schimb", "--comanda", "TST1", "--utilizator", "Ana", "--produse", "[__import__('os')]",
               raspuns=PROBA_OK)
check("1 text care nu e literal nu se evaluează: cod 2, nimic trimis", cod == 2 and PRIMITE == [], (cod, out))

# 2. execuția cere confirmare
cod, out = run(*BAZA, "--apply", raspuns=PROBA_OK)
check("2 --apply fără --confirmare: cod 2, nimic trimis", cod == 2 and PRIMITE == [])
cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", "--peste", "tichet_dublura,hold_oh",
               raspuns=(200, {"ok": True, "rezultat": "creat", "scris": "tot", "operatie_id": 7}))
check("2 execuție: dry_run false, confirmare și peste ca listă",
      PRIMITE and PRIMITE[0][1].get("dry_run") is False and PRIMITE[0][1].get("confirmare") == "v1.c0nf"
      and PRIMITE[0][1].get("peste") == ["tichet_dublura", "hold_oh"], PRIMITE)
check("2 făcut: cod 0", cod == 0, (cod, out))
cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", "--peste", str(["tichet_dublura", "hold_oh"]),
               raspuns=(200, {"ok": True, "rezultat": "creat", "scris": "tot", "operatie_id": 8}))
check("2 peste dat ca structură (str() din Second Brain) devine lista de coduri",
      PRIMITE and PRIMITE[0][1].get("peste") == ["tichet_dublura", "hold_oh"], PRIMITE)

# 3. codurile de ieșire
for nume, r, asteptat in [
    ("parțial", (200, {"ok": False, "rezultat": "eroare", "scris": "partial"}), 4),
    ("în lucru", (200, {"ok": False, "rezultat": "in_lucru", "scris": "necunoscut", "operatie_id": 9}), 5),
    ("necunoscut", (200, {"ok": False, "rezultat": "eroare", "scris": "necunoscut"}), 5),
    ("refuz OH", (200, {"ok": False, "rezultat": "imposibil", "scris": "nimic"}), 3),
    ("plan schimbat", (200, {"ok": False, "rezultat": "plan_schimbat", "scris": "nimic"}), 2),
    ("expirată", (200, {"ok": False, "rezultat": "expirata", "scris": "nimic"}), 2),
    ("400", (400, {"detail": {"rezultat": "parametri_schimbati"}}), 2),
    ("403", (403, {"detail": "Cheie de serviciu: fără dreptul «cs.awb»"}), 2),
    ("404", (404, {"detail": {"rezultat": "necunoscuta"}}), 2),
    ("409", (409, {"detail": {"rezultat": "ambigua", "candidati": []}}), 2),
    ("503", (503, {"detail": {"rezultat": "migrare_lipsa"}}), 2),
]:
    cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", raspuns=r)
    check(f"3 {nume}: cod {asteptat}", cod == asteptat, (cod, out[-200:]))
cod, out = run(*BAZA, raspuns=(200, {"ok": True, "rezultat": "previzualizare",
                                     "blocaje": [{"cod": "are_awb", "clasa": "imposibil"}]}))
check("3 probă cu blocaj imposibil: cod 3", cod == 3, cod)

# 4. rețea
cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", raspuns=(502, {}))
check("4 502 la execuție: cod 5 (poate a ajuns)", cod == 5, cod)
cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", raspuns="redirect")
check("4 redirect neurmat: cod 2", cod == 2, (cod, out[-200:]))
vechi_timeout = O.TIMEOUT
O.TIMEOUT = 1
cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", raspuns="dormi")
O.TIMEOUT = vechi_timeout
check("4 timeout după trimitere: cod 5, repetă identic", cod == 5 and "IDENTIC" in out, (cod, out))
s = socket.socket(); s.bind(("127.0.0.1", 0)); port_mort = s.getsockname()[1]; s.close()
vechi_base, O.BASE = O.BASE, "http://127.0.0.1:%d" % port_mort
cod, out = run(*BAZA, raspuns=PROBA_OK)
O.BASE = vechi_base
check("4 conexiune refuzată: cod 2, netrimis", cod == 2 and "netrimis" in out, (cod, out))

# 5. cheia
cod, out = run(*BAZA, raspuns=PROBA_OK)
check("5 cheia nu apare în ieșire", CHEIE not in out and "s" * 40 not in out)
os.environ["OH_CS_SB_TOKEN"] = "nu-e-cheie"
O._kb_path = lambda: None
cod, out = run(*BAZA, raspuns=PROBA_OK)
os.environ["OH_CS_SB_TOKEN"] = CHEIE
check("5 cheie stricată: cod 2, nimic trimis", cod == 2 and PRIMITE == [], (cod, PRIMITE))

# 6. recenzia finală: codurile după plic, `operatie`, adresa, afișarea, sumele, tipurile imbricate, argparse
for nume, r, apply_, asteptat in [
    ("500 cu plic scris necunoscut, execuție", (500, {"ok": False, "rezultat": "eroare", "scris": "necunoscut"}), True, 5),
    ("500 fără plic, execuție", (500, {"_text": "Internal Server Error"}), True, 5),
    ("500 cu plic, probă", (500, {"ok": False, "rezultat": "eroare", "scris": "necunoscut"}), False, 2),
    ("in_lucru nimic scris (lacăt ocupat)", (200, {"ok": False, "rezultat": "in_lucru", "scris": "nimic"}), True, 2),
    ("in_lucru necunoscut", (200, {"ok": False, "rezultat": "in_lucru", "scris": "necunoscut"}), True, 5),
    ("indisponibil 200", (200, {"ok": False, "rezultat": "indisponibil", "scris": "nimic"}), True, 5),
    ("indisponibil 503", (503, {"ok": False, "rezultat": "indisponibil", "scris": "nimic"}), True, 5),
    ("plafon", (200, {"ok": False, "rezultat": "plafon", "scris": "nimic"}), True, 3),
    ("400 plic sus", (400, {"ok": False, "rezultat": "invalida", "scris": "nimic"}), True, 2),
]:
    argv = BAZA + (("--apply", "--confirmare", "v1.c0nf") if apply_ else ())
    cod, out = run(*argv, raspuns=r)
    ultima = out.strip().splitlines()[-1]
    check(f"6 {nume}: cod {asteptat}", cod == asteptat and ultima.endswith("comanda=-") and
          ("iesire=%d" % asteptat) in ultima, (cod, ultima))

OPERATII.update({
    "7": {"ok": False, "rezultat": "in_lucru", "scris": "necunoscut", "stare": "in_zbor", "operatie_id": 7},
    "8": {"ok": False, "rezultat": "refacere_partiala", "scris": "partial", "stare": "gata", "operatie_id": 8},
    "9": {"ok": True, "rezultat": "creat", "scris": "tot", "stare": "gata", "operatie_id": 9, "comanda": "TST1"},
    "10": {"ok": False, "rezultat": "eroare", "scris": "necunoscut", "stare": "gata", "operatie_id": 10},
})
for oid, asteptat in (("7", 5), ("8", 4), ("9", 0), ("10", 5)):
    cod, out = run("operatie", "--id", oid)
    ultima = out.strip().splitlines()[-1]
    check(f"6 operatie {oid}: cod {asteptat} cu OH_REZULTAT", cod == asteptat and ultima.startswith("OH_REZULTAT")
          and ("operatie=%s" % oid) in ultima, (cod, out[-300:]))
cod, out = run("operatie", "--id", "x")
check("6 operatie --id greșit: cod 2 cu OH_REZULTAT", cod == 2 and out.strip().splitlines()[-1].startswith(
    "OH_REZULTAT rezultat=parametru"), (cod, out))

vechi_base = O.BASE
for baza, permis, bun in (("http://example.invalid", "", False), ("https://altundeva.example", "", False),
                          ("https://staging.example", "staging.example", True), ("https://orderhub.arona.ro", "", True),
                          ("http://orderhub.arona.ro", "orderhub.arona.ro", False), ("http://localhost:1", "", True)):
    os.environ["OH_CS_BASE_ALLOW"] = permis
    try:
        with redirect_stdout(io.StringIO()):
            O.baza_sigura(baza)
        ok = True
    except SystemExit:
        ok = False
    check(f"6 adresa {baza}: {'permisă' if bun else 'refuzată'}", ok is bun)
os.environ.pop("OH_CS_BASE_ALLOW", None)
O.BASE = "http://example.invalid"
cod, out = run(*BAZA, raspuns=PROBA_OK)
O.BASE = vechi_base
check("6 http spre altă gazdă: cod 2, nimic trimis, cheia nu apare", cod == 2 and PRIMITE == [] and CHEIE not in out
      and "OH_REZULTAT rezultat=baza" in out, (cod, out))

cod, out = run(*BAZA, "--apply", "--confirmare", "v1.c0nf", raspuns=(409, {
    "ok": False, "rezultat": "ambigua", "scris": "nimic", "mesaj": "comanda: în mai multe magazine · repetă cu «magazin»",
    "candidati": [{"comanda": "TST1001", "magazin": "Esteban Test", "order_id": 1},
                  {"comanda": "TST1001", "magazin": "Nubra Test", "order_id": 2}]}))
check("6 409 ambigua: candidații apar", cod == 2 and "Esteban Test" in out and "Nubra Test" in out, out)
cod, out = run(*BAZA, raspuns=(200, {"ok": False, "rezultat": "in_lucru", "scris": "nimic",
                                     "lucrat_de": {"autor": "Ana Test", "actiune": "salveaza", "de": "acum 1 min"}}))
check("6 lucrat_de apare", "Ana Test" in out, out)

for v, trimis in (("1.000", None), ("1,000", None), ("12.345", None), ("1.234,50", None), ("12,50", 12.5),
                  ("1000", 1000.0), ("12.5", 12.5)):
    cod, out = run("rambursare", "--comanda", "TST1", "--utilizator", "Ana", "--suma", v, raspuns=PROBA_OK)
    if trimis is None:
        check(f"6 suma {v!r}: cod 2, nimic trimis", cod == 2 and PRIMITE == [], (cod, PRIMITE))
    else:
        check(f"6 suma {v!r}: trimisă {trimis}", PRIMITE and PRIMITE[0][1].get("suma") == trimis, PRIMITE)
cod, out = run("rambursare", "--comanda", "TST1", "--utilizator", "Ana", "--greutate-kg", "1.250", raspuns=PROBA_OK)
check("6 alt număr (nu sumă) păstrează zecimalele", PRIMITE and PRIMITE[0][1].get("greutate_kg") == 1.25, PRIMITE)

check("6 tipurile imbricate", O.descrie_tip(SCHEMA["actiuni"][4]["parametri"]["reducere"]) ==
      "{tip: procent|suma, valoare: număr 0-100000, eticheta?: text}",
      O.descrie_tip(SCHEMA["actiuni"][4]["parametri"]["reducere"]))
cod, out = run("actiuni")
check("6 `actiuni` arată tipurile imbricate", "tip: procent|suma" in out and "cantitate?: întreg" in out, out)

cod, out = run("taguri", "--comanda", "TST1", "--utilizator", "Ana", "--adauga", "-urgent", "--motiv", "-10%",
               raspuns=(200, {"ok": True, "rezultat": "previzualizare", "blocaje": []}))
check("6 valori care încep cu „-”", PRIMITE and PRIMITE[0][1].get("adauga") == ["-urgent"]
      and PRIMITE[0][1].get("motiv") == "-10%", (cod, PRIMITE, out))
cod, out = run("taguri", "--comanda", "TST1", "--utilizator", "Ana", "--motiv", raspuns=PROBA_OK)
check("6 argument lipsă: cod 2 cu OH_REZULTAT, nimic trimis", cod == 2 and PRIMITE == [] and
      out.strip().splitlines()[-1].startswith("OH_REZULTAT rezultat=parametru"), (cod, out))
cod, out = run("comanda-noua", "--magazin", "Magazin Test", "--comanda", "TST1", "--utilizator", "Ana",
               raspuns=PROBA_OK)
check("6 comanda-noua nu primește --comanda: cod 2, nimic trimis", cod == 2 and PRIMITE == [], (cod, out))
cod, out = run("comanda-noua", "--magazin", "Magazin Test", "--utilizator", "Ana", "--reducere",
               '{"tip": "procent", "valoare": 10}', raspuns=PROBA_OK)
check("6 comanda-noua fără --comanda: corpul n-are `comanda`", PRIMITE and "comanda" not in PRIMITE[0][1], PRIMITE)

import gen_manifest as G  # noqa: E402
m = G.actiuni_manifest(SCHEMA["actiuni"])
check("6 manifestul comenzii noi n-are --comanda", "comanda" not in m["comanda-noua"]["params"]
      and "comanda" not in m["comanda-noua-apply"]["params"] and m["comanda-noua"]["params"]["magazin"]["required"])
check("6 manifestul spune tipurile câmpurilor", "tip: procent|suma" in
      m["comanda-noua"]["params"]["reducere"]["description"], m["comanda-noua"]["params"]["reducere"])
check("6 manifestul -apply: 2 = proba din nou, 5 = repetă identic", "2 nimic scris" in m["awb-nou-apply"]["description"]
      and "5 necunoscut" in m["awb-nou-apply"]["description"])

# manifestul publicat = ce generează schema acum; unul rămas din altă versiune a clientului e prins înainte de publicare
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as d:
    cs_, cm_ = os.path.join(d, "schema.json"), os.path.join(d, "manifest.json")
    def _scrie(cale, obj):
        with open(cale, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    _scrie(cs_, SCHEMA)
    _scrie(cm_, G.manifest(SCHEMA))
    with redirect_stdout(io.StringIO()):
        la_zi = G.verifica(cs_, cm_)
    vechi_m = G.manifest(SCHEMA)
    vechi_m["exec"]["actions"]["comanda-noua"]["params"]["comanda"] = {"flag": "--comanda", "type": "string"}
    _scrie(cm_, vechi_m)
    buf = io.StringIO()
    with redirect_stdout(buf):
        vechi = G.verifica(cs_, cm_)
    check("6 --verifica: manifest la zi = 0, unul vechi = 1 și numește acțiunea", la_zi == 0 and vechi == 1
          and "comanda-noua" in buf.getvalue(), (la_zi, vechi, buf.getvalue()))

srv.shutdown()
print()
print("PICĂ: " + ", ".join(FAILS) if FAILS else "Toate verificările au trecut.")
sys.exit(1 if FAILS else 0)
