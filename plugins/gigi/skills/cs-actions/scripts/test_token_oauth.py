# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test de regresie: magazinele cu marcaj OAUTH în stores.csv își emit tokenul (6-oct-2026).

Pe ORC, SK, HU, MD, DUPBG, BUC (și LAB) rândul din SHOPIFY_STORES_CSV poartă `OAUTH:<APP>_CLIENT_ID+SECRET`, nu un
token. cs_actions trimitea marcajul ca token, iar Shopify răspundea 401 pe orice operație. Fără rețea: stores.csv,
KB-ul și Shopify sunt dubluri. Verifică:
  1. un rând cu token static merge ca înainte, fără nicio emitere;
  2. un rând cu marcaj emite tokenul o singură dată (client_credentials, spre domeniul magazinului) și îl refolosește;
  3. secretele app-ului vin din KB, iar fără KB din env (Second Brain n-are kb.py, le injectează în env);
  4. un domeniu care nu e `*.myshopify.com`, un marcaj stricat sau secrete lipsă opresc scriptul ÎNAINTE de orice
     cerere, deci secretele nu pleacă nicăieri; un refuz Shopify oprește scriptul fără să arate secretul;
  5. prefixul unei comenzi se potrivește cu rândul care îl continuă, doar dacă e unul singur (TSTX → TSTXBG);
  6. nici secretul, nici tokenul emis nu apar în ieșire.
Magazinele, secretele și tokenurile sunt inventate.

  uv run test_token_oauth.py
"""
import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cs_actions as C  # noqa: E402

FAILS = []
SECRET = "csec-inventat-0123456789"
MINTED = "shpat_emis_inventat_abcdef"
CSV = ("prefix,shop,token\n"
       "TST,tst-static.myshopify.com,shpat_static_inventat\n"
       "TSTXBG,tstx-bg.myshopify.com,OAUTH:SHOPIFY_TESTAPP_CLIENT_ID+SECRET\n"
       "TSTO,tsto.myshopify.com,OAUTH:SHOPIFY_TESTAPP_CLIENT_ID+SECRET\n"
       "TSTOK,tstok.myshopify.com,shpat_alt_static\n"
       "RAU,evil.example.com,OAUTH:SHOPIFY_TESTAPP_CLIENT_ID+SECRET\n"
       "STRICAT,tsts.myshopify.com,OAUTH:nu e un marcaj\n"
       "LIPSA,tstl.myshopify.com,OAUTH:SHOPIFY_LIPSA_CLIENT_ID+SECRET\n")


def check(nume, ok, extra=""):
    print(("✅ " if ok else "❌ ") + nume + (f"  [{extra}]" if extra and not ok else ""))
    if not ok:
        FAILS.append(nume)


class Http:
    def __init__(self, status=200, token=MINTED):
        self.calls, self.status, self.token = [], status, token

    def __call__(self, method, url, headers, body=None, timeout=40):
        self.calls.append((method, url, dict(body or {})))
        if self.status != 200:
            return self.status, {"errors": "invalid_client"}
        return 200, {"access_token": self.token, "scope": "write_orders", "expires_in": 86399}


def reset(kb=None, env=None, http=None):
    C._STORES, C._EMISE = None, {}
    C._stores_csv = lambda: CSV
    kb = kb or {}
    C._kb_path = lambda: "/kb.py" if kb else None
    C.subprocess.run = lambda args, **k: type("R", (), {"stdout": kb.get(args[-1], "")})()
    for k in [k for k in os.environ if k.startswith("SHOPIFY_TESTAPP_") or k.startswith("SHOPIFY_LIPSA_")]:
        del os.environ[k]
    os.environ.update(env or {})
    C._http = http or Http()
    return C._http


def run(fn):
    out, err = io.StringIO(), io.StringIO()
    code = None
    with redirect_stdout(out), redirect_stderr(err):
        try:
            res = fn()
        except SystemExit as e:
            res, code = None, e.code
    return res, code, out.getvalue() + err.getvalue() + (str(code) if code else "")


KB_OK = {"SHOPIFY_TESTAPP_CLIENT_ID": "cid-inventat", "SHOPIFY_TESTAPP_CLIENT_SECRET": SECRET}

# 1. token static: neschimbat, fără emitere
h = reset(kb=KB_OK)
res, code, out = run(lambda: C.store_of("TST"))
check("1 token static întors ca atare", res == ("tst-static.myshopify.com", "shpat_static_inventat"), res)
check("1 fără nicio cerere de emitere", h.calls == [], h.calls)

# 2. marcaj: emitere o dată, spre domeniul magazinului, apoi din cache
h = reset(kb=KB_OK)
res, code, out = run(lambda: C.store_of("TSTO"))
check("2 marcajul devine tokenul emis", res == ("tsto.myshopify.com", MINTED), res)
check("2 cererea merge la /admin/oauth/access_token al magazinului",
      len(h.calls) == 1 and h.calls[0][1] == "https://tsto.myshopify.com/admin/oauth/access_token", h.calls)
check("2 corpul e client_credentials cu secretele app-ului",
      h.calls and h.calls[0][2] == {"client_id": "cid-inventat", "client_secret": SECRET,
                                    "grant_type": "client_credentials"}, h.calls)
run(lambda: C.store_of("TSTO"))
check("2 a doua cerere refolosește tokenul (o singură emitere)", len(h.calls) == 1, len(h.calls))

# 3. fără KB, secretele vin din env (Second Brain)
h = reset(kb=None, env={"SHOPIFY_TESTAPP_CLIENT_ID": "cid-env", "SHOPIFY_TESTAPP_CLIENT_SECRET": SECRET})
res, code, out = run(lambda: C.store_of("TSTO"))
check("3 fără kb.py: secretele din env, token emis", res == ("tsto.myshopify.com", MINTED), (res, out))
check("3 env folosit în cerere", h.calls and h.calls[0][2].get("client_id") == "cid-env", h.calls)
# KB întâi: un env vechi nu bate KB-ul
h = reset(kb=KB_OK, env={"SHOPIFY_TESTAPP_CLIENT_ID": "cid-vechi", "SHOPIFY_TESTAPP_CLIENT_SECRET": "vechi"})
run(lambda: C.store_of("TSTO"))
check("3 cu KB: KB-ul bate env-ul", h.calls and h.calls[0][2].get("client_id") == "cid-inventat", h.calls)

# 4. gărzile: nimic nu pleacă
for prefix, nume in (("RAU", "domeniu care nu e myshopify"), ("STRICAT", "marcaj stricat"),
                     ("LIPSA", "secrete lipsă")):
    h = reset(kb=KB_OK)
    res, code, out = run(lambda p=prefix: C.store_of(p))
    check(f"4 {nume}: oprește cu mesaj", code is not None and "Token neemis" in out, out)
    check(f"4 {nume}: nicio cerere trimisă", h.calls == [], h.calls)
h = reset(kb=KB_OK, http=Http(status=401))
res, code, out = run(lambda: C.store_of("TSTO"))
check("4 refuz Shopify: oprește cu statusul", code is not None and "401" in out, out)
check("4 refuz Shopify: secretul nu apare", SECRET not in out, out)

# 5. prefixul canonic
reset(kb=KB_OK)
check("5 TSTX → TSTXBG (un singur rând îl continuă)", run(lambda: C.prefix_of_order("TSTX2426"))[0] == "TSTXBG")
check("5 prefix exact rămâne exact (TSTO, deși TSTOK îl continuă)", run(lambda: C.prefix_of_order("TSTO1"))[0] == "TSTO")
res, code, out = run(lambda: C.store_of("TS"))
check("5 ambiguu (TS → TST/TSTXBG/…): negăsit, nu ghicit", code is not None and "negăsit" in out, out)
h = reset(kb=KB_OK)
res, code, out = run(lambda: C.store_of("TSTX"))
check("5 store_of pe prefixul scurt emite pentru rândul lung", res == ("tstx-bg.myshopify.com", MINTED), res)

# 6. nimic secret în ieșire, pe toate căile de mai sus
check("6 tokenul emis nu se printează", MINTED not in out)

print()
print("PICĂ: " + ", ".join(FAILS) if FAILS else "Toate verificările au trecut.")
sys.exit(1 if FAILS else 0)
