# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""order_hub.py — clientul Second Brain pentru API-ul Order Hub pentru agenți (/api/cs).

Order Hub ține tokenurile tuturor magazinelor, gărzile și auditul; clientul doar trimite cererea. Orice acțiune are
două trepte: PROBA (nu scrie nimic: planul, blocajele, confirmarea) și EXECUȚIA (--apply, cu confirmarea din probă).

  order_hub.py actiuni                                   # ce acțiuni există, cu parametrii lor
  order_hub.py comanda --comanda GT000001                 # fișa comenzii din Order Hub
  order_hub.py operatie --id 123                         # ce s-a întâmplat cu o execuție (după un timeout)
  order_hub.py neplecate --sku HA-0001 [--magazin grandia] [--limita 200] [--json]   # comenzile neplecate pe SKU
  order_hub.py <actiune> --comanda X --utilizator Raluca --motiv "..." [--<param> valoare ...]            # probă
  order_hub.py <actiune> ... --peste cod1,cod2 --confirmare <din probă> --apply                           # execuție

Coduri de ieșire: 0 făcut / proba fără blocaje · 2 nimic scris (cerere greșită, cheie, comandă necunoscută,
plan schimbat, confirmare expirată, oprit, comanda ocupată „in_lucru" cu nimic scris) — corectezi / proba din nou ·
3 refuz Order Hub, nimic scris (și plafonul atins: spui omului, nu reîncerci) · 4 scris parțial (verifică în Order Hub,
nu repeta) · 5 necunoscut: cererea a plecat dar răspunsul s-a pierdut, e în lucru sau Order Hub se oprește — repetă
IDENTIC (aceeași confirmare), nu proba din nou. Ultima linie e pentru mașină: OH_REZULTAT rezultat=… scris=… iesire=…

Cheia: env OH_CS_SB_TOKEN (Second Brain o injectează), altfel KB (kb.py). Nu se printează niciodată.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
GAZDA = "orderhub.arona.ro"
GAZDA_ENV = "OH_CS_BASE_ALLOW"      # altă gazdă HTTPS, numită explicit (ex. un staging)
_LOCALE = ("127.0.0.1", "localhost", "::1")
TOKEN_ENV = "OH_CS_SB_TOKEN"
TIMEOUT = 110          # sub cele 120 s ale nginx: Order Hub răspunde singur „in_lucru" pe la 100 s
COMUNE = ("comanda", "magazin", "utilizator", "motiv", "peste", "confirmare")

for _s in (sys.stdout, sys.stderr):   # stațiile CS sunt pe Windows (cp1252)
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Netrimis(Exception):
    """Cererea n-a ajuns la Order Hub (DNS, conexiune refuzată): sigur nu s-a scris nimic."""


class FaraRaspuns(Exception):
    """Cererea a plecat, răspunsul nu: poate s-a executat. Se repetă IDENTIC, nu se face probă nouă."""


class _FaraRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # un redirect ar duce antetul Authorization la altă adresă
        return None


def _kb_path():
    d = os.getcwd()
    for _ in range(8):
        c = os.path.join(d, "team-intelligence", "plugins", "core", "scripts", "kb.py")
        if os.path.exists(c):
            return c
        d = os.path.dirname(d)
    return None


def cheie():
    v = os.getenv(TOKEN_ENV, "").strip()
    if not v and _kb_path():
        try:
            v = subprocess.run(["uv", "run", _kb_path(), "secret-get", TOKEN_ENV], capture_output=True,
                               text=True, timeout=30).stdout.strip()
        except Exception:
            v = ""
    if not re.match(r"^ohsvc\.[a-z0-9][a-z0-9_-]{1,31}\.\S{32,}$", v):
        raise SystemExit(_iesire(2, "cheie", "%s lipsește sau n-are forma ohsvc.<id>.<secret>" % TOKEN_ENV))
    return v


def baza_sigura(base=None):
    """Adresa Order Hub, verificată ÎNAINTE ca cheia (toate scopurile cs.*, banii inclusiv) să plece: doar HTTPS, doar
    orderhub.arona.ro (sau gazda din OH_CS_BASE_ALLOW); HTTP doar pe 127.0.0.1/localhost, pentru teste. Un OH_CS_BASE
    greșit trimitea cheia în clar, la prima cerere, înaintea oricărui redirect."""
    b = BASE if base is None else base
    try:
        u = urllib.parse.urlsplit(b)
        gazda = (u.hostname or "").lower()
    except ValueError:
        u, gazda = None, ""
    if u is not None and u.scheme == "http" and gazda in _LOCALE:
        return b
    if u is not None and u.scheme == "https" and gazda and (
            gazda == GAZDA or gazda == os.environ.get(GAZDA_ENV, "").strip().lower()):
        return b
    raise SystemExit(_iesire(2, "baza", "OH_CS_BASE: doar https://%s (altă gazdă HTTPS: %s=<gazda>) · nimic trimis"
                             % (GAZDA, GAZDA_ENV)))


def http(method, cale, corp=None, token=""):
    """(status, json). Netrimis = nimic scris; FaraRaspuns = stare necunoscută."""
    baza_sigura()
    data = json.dumps(corp).encode() if corp is not None else None
    req = urllib.request.Request(BASE + cale, data=data, method=method,
                                 headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                                          **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.build_opener(_FaraRedirect).open(req, timeout=TIMEOUT) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (502, 504) and method == "POST":
            raise FaraRaspuns("HTTP %s de la proxy" % e.code)   # cererea poate să fi ajuns la Order Hub
        return e.code, _json(e.read())
    except urllib.error.URLError as e:
        # „Nimic scris" doar când e sigur (adresă negăsită, conexiune refuzată). Orice altceva poate să fi plecat:
        # o repetare IDENTICĂ e mereu sigură, fiindcă Order Hub o recunoaște după confirmare.
        motiv = getattr(e, "reason", e)
        if isinstance(motiv, (ConnectionRefusedError, OSError)) and not isinstance(motiv, (TimeoutError, ConnectionResetError)):
            raise Netrimis(type(motiv).__name__)
        raise FaraRaspuns(type(motiv).__name__)
    except (TimeoutError, ConnectionResetError, OSError) as e:   # după trimitere: RemoteDisconnected, timeout la citire
        raise FaraRaspuns(type(e).__name__)


def _json(b):
    try:
        return json.loads(b.decode("utf-8") or "{}")
    except Exception:
        return {"_text": b.decode("utf-8", "replace")[:300]}


# ───────────────────────── schema (GET /api/cs/actiuni) ─────────────────────────
BANI = ("suma",)        # parametrii-sumă: fără ambiguitatea separatorului de mii


def descrie_tip(spec):
    """Tipul unui parametru, cu tot ce cere Order Hub înăuntru: „procent|suma", „{tip: procent|suma, valoare: număr
    0-100000, eticheta?: text}", „[{sku?: text, cantitate: întreg 1-999}]". În JSON-ul imbricat bool = true/false."""
    spec = spec or {}
    tip = spec.get("tip")
    if tip == "enum":
        return "|".join(map(str, spec.get("valori") or []))
    if tip == "obiect":
        return "{%s}" % descrie_campuri(spec.get("campuri")) if spec.get("campuri") else "obiect JSON"
    if tip == "lista":
        return "[%s]" % descrie_tip(spec.get("elemente") or {"tip": "text"})
    nume = {"int": "întreg", "num": "număr", "text": "text", "bool": "true/false"}.get(tip, str(tip))
    if tip in ("int", "num") and (spec.get("min") is not None or spec.get("max") is not None):
        nume += " %s-%s" % ("" if spec.get("min") is None else spec["min"], "" if spec.get("max") is None else spec["max"])
    return nume


def descrie_campuri(c):
    return ", ".join("%s%s: %s" % (k, "" if (v or {}).get("obligatoriu") else "?", descrie_tip(v))
                     for k, v in (c or {}).items())
def schema(token):
    s, d = http("GET", "/api/cs/actiuni", token=token)
    if s != 200:
        raise SystemExit(_iesire(2, "schema", "GET /api/cs/actiuni: HTTP %s %s" % (s, _detaliu(d))))
    acts = d.get("actiuni") if isinstance(d, dict) else d
    return {a["nume"]: a for a in (acts or [])}


def converteste(spec, nume, v):
    """Valoarea din linia de comandă → tipul JSON declarat de Order Hub. Fără ghicit: un tip necunoscut e o eroare."""
    tip = (spec or {}).get("tip")
    if tip == "int":
        if not re.fullmatch(r"-?\d+", v.strip()):
            raise ValueError("%s: număr întreg, nu %r" % (nume, v))
        return int(v)
    if tip == "num":
        s = v.strip()
        if nume in BANI and not re.fullmatch(r"\d+(?:[.,]\d{1,2})?", s):
            # „1.000" / „1,000" (o mie, cu separator de mii) ar fi plecat 1.0; „12.345" rotunjit în tăcere
            raise ValueError("%s: sumă cu cel mult 2 zecimale, fără separator de mii (ex. 1000 sau 12.50), nu %r"
                             % (nume, v))
        try:
            return float(s.replace(",", "."))
        except ValueError:
            raise ValueError("%s: număr, nu %r" % (nume, v)) from None
    if tip == "bool":
        if v.strip().lower() in ("da", "true"):
            return True
        if v.strip().lower() in ("nu", "false"):
            return False
        raise ValueError("%s: da sau nu, nu %r" % (nume, v))
    if tip == "enum":
        val = (spec.get("valori") or [])
        if v not in val:
            raise ValueError("%s: una din %s, nu %r" % (nume, "/".join(map(str, val)), v))
        return v
    if tip == "text":
        return v
    if tip == "lista":
        el = spec.get("elemente") or {"tip": "text"}
        s = v.strip()
        if s.startswith("["):
            arr = structura(s, nume, list)
            return [din_json(el, nume, x) for x in arr]
        if el.get("tip") == "obiect":   # un obiect JSON are virgule: împărțirea după virgulă l-ar rupe
            raise ValueError('%s: listă JSON de obiecte, ex. [{"sku": "X", "cantitate": 1}]' % nume)
        return [converteste(el, nume, x.strip()) for x in s.split(",") if x.strip()]
    if tip == "obiect":
        return structura(v.strip(), nume, dict)
    raise ValueError("%s: tip necunoscut %r în schema Order Hub" % (nume, tip))


def structura(s, nume, fel):
    """O listă / un obiect dat ca text: JSON, sau forma Python în care Second Brain transformă o valoare structurată
    (`str(value)`: ghilimele simple, True/False/None). `ast.literal_eval` citește doar literali, nu rulează cod."""
    try:
        o = json.loads(s)
    except ValueError:
        try:
            o = ast.literal_eval(s)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            raise ValueError("%s: %s JSON invalid%s" % (nume, "listă" if fel is list else "obiect",
                                                       "ă" if fel is list else "")) from None
    if not isinstance(o, fel):
        raise ValueError("%s: %s JSON" % (nume, "listă" if fel is list else "obiect"))
    return o


def din_json(spec, nume, x):
    """Un element dintr-o listă JSON: tipul JSON trebuie să fie deja cel declarat (fără conversii ghicite)."""
    tip = (spec or {}).get("tip")
    ok = {"obiect": lambda: isinstance(x, dict),
          "text": lambda: isinstance(x, str),
          "int": lambda: isinstance(x, int) and not isinstance(x, bool),
          "num": lambda: isinstance(x, (int, float)) and not isinstance(x, bool),
          "bool": lambda: isinstance(x, bool),
          "enum": lambda: x in (spec.get("valori") or [])}.get(tip)
    if ok is None:
        raise ValueError("%s: tip necunoscut %r în schema Order Hub" % (nume, tip))
    if not ok():
        raise ValueError("%s: element %s, nu %r" % (nume, tip, x))
    return x


# ───────────────────────── ieșire ─────────────────────────
def _detaliu(d):
    if isinstance(d, dict):
        x = d.get("detail", d)
        if isinstance(x, dict):
            return x.get("mesaj") or x.get("rezultat") or json.dumps(x, ensure_ascii=False)[:300]
        return str(x)[:300]
    return str(d)[:300]


def _iesire(cod, rezultat, mesaj, scris="nimic", confirmare="-", operatie="-", comanda="-"):
    print("%s: %s" % (rezultat, mesaj))
    print("OH_REZULTAT rezultat=%s scris=%s iesire=%d confirmare=%s operatie=%s comanda=%s"
          % (rezultat, scris, cod, confirmare or "-", operatie or "-", comanda or "-"))
    return cod


def plic(d):
    """Plicul /api/cs: la nivelul de sus (Order Hub, pe orice cod) sau sub `detail` (un proxy / o versiune veche)."""
    if isinstance(d, dict) and isinstance(d.get("detail"), dict):
        return d["detail"]
    return d if isinstance(d, dict) else {}


def cod_din(status, d, apply):
    """Codul de ieșire din răspunsul Order Hub (vezi docstring). Corpul decide întâi, pe orice cod HTTP: un 500 al
    rutei poate veni DUPĂ ce execuția a scris (plicul spune atunci scris „necunoscut")."""
    dd = plic(d)
    rez, scris = dd.get("rezultat"), dd.get("scris")
    if rez is None:                                 # nu e un plic /api/cs
        return 5 if apply and status >= 500 and status != 503 else 2
    if scris == "partial":
        return 4
    if rez == "in_lucru":
        # „nimic" = comanda ocupată (lacăt), nimic scris: proba din nou; altfel execuția încă rulează
        return 2 if scris == "nimic" else 5
    if rez == "indisponibil":
        return 5                                    # Order Hub se oprește / coada e plină: nimic scris, repetă identic
    if scris == "necunoscut":
        return 5 if apply else 2                    # o probă nu scrie niciodată
    if rez == "plafon":
        return 3                                    # o probă nouă nu ajută: spui omului
    if status != 200:
        return 2
    if rez in ("oprit", "plan_schimbat", "expirata", "peste_nepotrivit", "peste_oprit", "parametri_schimbati"):
        return 2
    if not apply:   # proba
        return 0 if d.get("ok") and not any(b.get("clasa") == "imposibil" for b in d.get("blocaje") or []) else 3
    if d.get("ok"):
        return 0
    return 3 if scris in (None, "nimic") else 4


_AFISATE = {"ok", "rezultat", "scris", "mesaj", "comanda", "order_id", "magazin", "actiune", "plan", "blocaje",
            "peste_necesar", "pasi", "contract", "confirmare", "expira_la", "operatie_id", "repetat"}


def afiseaza(d):
    if not isinstance(d, dict):
        print(str(d)[:500]); return
    print("%s %s · %s" % ("✅" if d.get("ok") else "⛔", d.get("actiune") or "", d.get("rezultat")))
    for k in ("comanda", "magazin", "scris", "operatie_id", "repetat", "expira_la"):
        if d.get(k) not in (None, "", [], False):
            print("  %s: %s" % (k, d[k]))
    for m in str(d.get("mesaj") or "").splitlines():
        print("  " + m)
    for l in d.get("plan") or []:
        print("  plan: " + str(l))
    for b in d.get("blocaje") or []:
        print("  blocaj: %s (%s) · %s" % (b.get("cod"), b.get("clasa"), b.get("detaliu", "")))
    if d.get("peste_necesar"):
        print("  peste_necesar: " + ",".join(d["peste_necesar"]))
    for p in d.get("pasi") or []:
        print("  pas: %s %s · %s" % ("ok" if p.get("ok") else "NU", p.get("pas"), p.get("text", "")))
    # câmpurile familiei și ale refuzului: `candidati` (409 ambigua: ce dai la --magazin), `lucrat_de` (cine ține
    # comanda), `comanda_noua`, sume, AWB-uri — compacte, ca agentul să le aibă
    for k in sorted(d):
        if k not in _AFISATE and d[k] not in (None, "", [], {}):
            print("  %s: %s" % (k, json.dumps(d[k], ensure_ascii=False, default=str)[:600]))
    if d.get("confirmare"):
        print("  confirmare: " + d["confirmare"])


def afiseaza_neplecate(d, limita_rand=200):
    """Rezumatul compact al GET /api/cs/neplecate: totalul, pe magazin, apoi o linie pe comandă."""
    print("Neplecate cu %s (SKU-uri căutate: %s): %s comenzi · %s bucăți%s"
          % (d.get("sku"), ", ".join(d.get("skuri_cautate") or []), d.get("total_comenzi"), d.get("total_bucati"),
             " · magazin: %s" % d["magazin"]["nume"] if d.get("magazin") else ""))
    print("  definiție: %s" % d.get("definitie"))
    if d.get("mesaj"):
        print("  " + str(d["mesaj"]))
    for m in d.get("pe_magazin") or []:
        print("  %-22s %4d comenzi · %4d buc · fără AWB %d · AWB nepreluat %d · pe hold %d"
              % (m.get("magazin"), m.get("comenzi", 0), m.get("bucati", 0), m.get("fara_awb", 0),
                 m.get("awb_nepreluat", 0), m.get("pe_hold", 0)))
    for c in (d.get("comenzi") or [])[:limita_rand]:
        h = c.get("hold") or {}
        t = h.get("tichet_cs") or {}
        motiv = (" · hold" if h.get("pe_hold") else "") + ((" · CS: %s" % t.get("eticheta")) if t.get("eticheta") else "")
        print("  %-12s %-20s %s · %s · %s buc%s%s" % (
            c.get("comanda"), (c.get("magazin") or "")[:20], (c.get("creata_la") or "")[:16].replace("T", " "),
            c.get("stare"), c.get("bucati"), (" · AWB " + ",".join(c.get("awb") or [])) if c.get("awb") else "", motiv))
    if d.get("trunchiat"):
        print("  … listă trunchiată la %s comenzi (totalul de sus e complet; --limita până la 1000)" % d.get("limita"))


# ───────────────────────── comenzi ─────────────────────────
class ArgumenteGresite(Exception):
    pass


class Parser(argparse.ArgumentParser):
    """argparse fără ieșirea lui (usage pe stderr, cod 2, FĂRĂ linia OH_REZULTAT): eroarea trece prin `_iesire`."""

    def error(self, message):
        raise ArgumenteGresite(message)


def lipeste_valori(argv, cu_valoare):
    """`--flag -urgent` → `--flag=-urgent`: argparse ia o valoare care începe cu „-" drept opțiune („expected one
    argument"). Doar pentru flag-urile cu valoare și doar când valoarea nu e ea însăși un flag cunoscut."""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in cu_valoare and i + 1 < len(argv) and argv[i + 1].startswith("-") \
                and argv[i + 1].split("=", 1)[0] not in cu_valoare | {"--apply", "-h", "--help"}:
            out.append(a + "=" + argv[i + 1])
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0
    op = argv[0]
    try:
        token = cheie()
        if op == "actiuni":
            for n, a in sorted(schema(token).items()):
                print("%-18s %-8s %s" % (n, a.get("familie"), descrie_campuri(a.get("parametri"))))
            return 0
        if op == "comanda":
            ap = Parser(prog="order_hub.py comanda")
            ap.add_argument("--comanda", required=True); ap.add_argument("--magazin")
            a = ap.parse_args(lipeste_valori(argv[1:], {"--comanda", "--magazin"}))
            q = {"comanda": a.comanda, **({"magazin": a.magazin} if a.magazin else {})}
            s, d = http("GET", "/api/cs/comanda?" + urllib.parse.urlencode(q), token=token)
            print(json.dumps(d, ensure_ascii=False, indent=1)[:20000])
            return 0 if s == 200 else _iesire(2, "fisa", "HTTP %s %s" % (s, _detaliu(d)))
        if op == "neplecate":
            ap = Parser(prog="order_hub.py neplecate")
            ap.add_argument("--sku", required=True); ap.add_argument("--magazin")
            ap.add_argument("--limita", type=int, default=200); ap.add_argument("--json", action="store_true")
            a = ap.parse_args(lipeste_valori(argv[1:], {"--sku", "--magazin", "--limita"}))
            q = {"sku": a.sku, "limita": a.limita, **({"magazin": a.magazin} if a.magazin else {})}
            s, d = http("GET", "/api/cs/neplecate?" + urllib.parse.urlencode(q), token=token)
            if s != 200 or not isinstance(d, dict) or not d.get("ok"):
                return _iesire(2, plic(d).get("rezultat") or "neplecate", "HTTP %s %s" % (s, _detaliu(d)))
            if a.json:
                print(json.dumps(d, ensure_ascii=False, indent=1))
            else:
                afiseaza_neplecate(d)
            return 0
        if op == "operatie":
            ap = Parser(prog="order_hub.py operatie")
            ap.add_argument("--id", required=True, type=int)
            a = ap.parse_args(argv[1:])
            s, d = http("GET", "/api/cs/operatie/%d" % a.id, token=token)
            dd = d.get("raspuns", d) if isinstance(d, dict) and isinstance(d.get("raspuns"), dict) else plic(d)
            afiseaza(dd)
            if s != 200:
                return _iesire(2, "operatie", "HTTP %s %s" % (s, _detaliu(d)))
            # rezultatul PĂSTRAT al execuției, cu aceleași coduri ca execuția: „in_zbor" = încă necunoscut (5), parțial
            # = 4; un 0 aici spunea „făcut" pentru o scriere necunoscută sau încă în curs
            cod = 5 if dd.get("stare") == "in_zbor" else cod_din(s, dd, True)
            return _iesire(cod, dd.get("rezultat") or "operatie", "operatie %s · stare %s" % (a.id, dd.get("stare")),
                           scris=dd.get("scris") or "-", operatie=dd.get("operatie_id") or a.id,
                           comanda=dd.get("comanda"))
        return actiune(op, argv[1:], token)
    except ArgumenteGresite as e:
        return _iesire(2, "parametru", "%s · nimic trimis" % e)
    except Netrimis as e:
        return _iesire(2, "netrimis", "Order Hub: %s · nimic trimis" % e)
    except FaraRaspuns as e:
        return _iesire(5, "fara_raspuns", "%s · cererea a plecat: repetă IDENTIC (aceeași confirmare)" % e,
                       scris="necunoscut")


def actiune(nume, argv, token):
    sch = schema(token)
    a = sch.get(nume)
    if not a:
        return _iesire(2, "actiune_necunoscuta", "%s · vezi: order_hub.py actiuni" % nume)
    ap = Parser(prog="order_hub.py " + nume)
    # doar câmpurile comune pe care le primește acțiunea (comanda-noua, pe magazin, n-are `comanda`: Order Hub ar
    # răspunde 400 „Câmp necunoscut")
    comune = [c for c in COMUNE if c in (a.get("comune") or COMUNE)]
    for c in comune:
        ap.add_argument("--" + c)
    ap.add_argument("--agent", dest="utilizator_alias")
    ap.add_argument("--apply", action="store_true")
    for p in (a.get("parametri") or {}):
        ap.add_argument("--" + p.replace("_", "-"), dest="p_" + p)
    cu_valoare = {"--" + c for c in comune} | {"--agent"} | {"--" + p.replace("_", "-") for p in a.get("parametri") or {}}
    x = ap.parse_args(lipeste_valori(argv, cu_valoare))
    corp = {"utilizator": (getattr(x, "utilizator", None) or x.utilizator_alias or "").strip(), "dry_run": not x.apply}
    for c in ("comanda", "magazin", "motiv", "confirmare"):
        if getattr(x, c, None):
            corp[c] = getattr(x, c)
    if getattr(x, "peste", None):
        try:
            corp["peste"] = ([str(s).strip() for s in structura(x.peste.strip(), "peste", list)]
                             if x.peste.strip().startswith("[") else
                             [s.strip() for s in x.peste.split(",") if s.strip()])
        except ValueError as e:
            return _iesire(2, "parametru", str(e))
    for p, spec in (a.get("parametri") or {}).items():
        v = getattr(x, "p_" + p)
        if v is not None:
            try:
                corp[p] = converteste(spec, p, v)
            except ValueError as e:
                return _iesire(2, "parametru", str(e))
    if not corp["utilizator"]:
        return _iesire(2, "utilizator", "dă --utilizator <numele omului de la CS>")
    if x.apply and not getattr(x, "confirmare", None):
        return _iesire(2, "confirmare", "--apply cere --confirmare din probă · nimic trimis")
    s, d = http("POST", "/api/cs/" + nume, corp, token=token)
    dd = plic(d)
    afiseaza(dd if dd else d)
    cod = cod_din(s, d, x.apply)
    return _iesire(cod, dd.get("rezultat") or ("http_%s" % s), "HTTP %s" % s,
                   scris=dd.get("scris") or ("nimic" if cod == 2 else "-"),
                   confirmare=dd.get("confirmare"), operatie=dd.get("operatie_id"), comanda=dd.get("comanda"))


if __name__ == "__main__":
    sys.exit(main())
