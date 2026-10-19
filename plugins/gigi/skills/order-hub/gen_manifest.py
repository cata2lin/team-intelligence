# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Generează manifestul Second Brain pentru gigi:order-hub din schema Order Hub (GET /api/cs/actiuni).

Fiecare acțiune Order Hub devine o pereche: `<nume>` = PROBA (allow, nu scrie) și `<nume>-apply` = EXECUȚIA (ask, cere
confirmarea din probă). Niciun parametru nu e boolean: Second Brain pune flag-ul pentru orice valoare nevidă, deci un
„false" ar deveni adevărat. Tipurile Order Hub care nu sunt întregi ajung text, iar clientul le convertește după schemă.

  uv run gen_manifest.py schema.json > gigi-order-hub.json
  uv run gen_manifest.py --verifica schema.json gigi-order-hub.json    # cod 1 = manifestul e vechi: regenerează-l

Înainte de publicare: `--verifica` pe fiecare manifest din folder. Un manifest rămas din altă versiune a clientului
declara --comanda la comanda-noua (clientul nou o respinge cu „parametru") și vechile coduri de ieșire de la -apply.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from order_hub import descrie_campuri  # noqa: E402  (aceeași descriere ca `order_hub.py actiuni`)

CLIENT_TIMEOUT = 110   # order_hub.TIMEOUT
TIMEOUT_PROBA = CLIENT_TIMEOUT + 20
TIMEOUT_EXECUTIE = CLIENT_TIMEOUT + 40


def _campuri(c):
    """„tip: procent|suma, valoare: număr 0-100000, eticheta?: text" — câmpurile unui obiect, cu tipul fiecăruia
    (enum-urile cu valorile lor, intervalele numerelor); „?" = opțional. Fără tipuri, agentul trimitea „10" ca text sau
    „Procent" și primea 400 la prima încercare."""
    return descrie_campuri(c)


def _lista(spec):
    el = spec.get("elemente") or {}
    if el.get("tip") == "obiect":
        return "listă JSON de obiecte [{%s}]" % _campuri(el.get("campuri"))
    if el.get("tip") == "enum":
        return "listă separată prin virgulă, din: " + "/".join(map(str, el.get("valori") or []))
    return "listă separată prin virgulă (sau listă JSON)"


def _param(spec, nume, obligatoriu):
    tip = spec.get("tip")
    p = {"flag": "--" + nume.replace("_", "-"), "type": "integer" if tip == "int" else "string"}
    if obligatoriu:
        p["required"] = True
    descr = {"enum": "una din: " + "/".join(map(str, spec.get("valori") or [])),
             "bool": "da sau nu", "lista": _lista(spec), "obiect": "obiect JSON {%s}" % _campuri(spec.get("campuri")),
             "num": "număr", "int": "întreg" + (" %s-%s" % (spec.get("min"), spec.get("max"))
                                              if spec.get("min") is not None else "")}.get(tip, "text")
    implicit = spec.get("implicit")
    if isinstance(implicit, bool):
        implicit = "da" if implicit else "nu"
    if implicit is not None and implicit != "":
        descr += " (implicit: %s)" % implicit
    p["description"] = descr
    return p


def actiuni_manifest(schema):
    out = {
        "actiuni": {"risk": "allow", "fixed_args": ["actiuni"], "timeout_s": TIMEOUT_PROBA, "params": {},
                    "description": "CITIRE: acțiunile Order Hub disponibile și parametrii lor."},
        "comanda": {"risk": "allow", "fixed_args": ["comanda"], "timeout_s": TIMEOUT_PROBA,
                    "description": "CITIRE: fișa comenzii din Order Hub (status, etichete, hold-uri, tichet, facturi, "
                                   "ce se poate face acum). Statusurile se iau DOAR de aici.",
                    "params": {"comanda": {"flag": "--comanda", "type": "string", "required": True},
                               "magazin": {"flag": "--magazin", "type": "string"}}},
        "operatie": {"risk": "allow", "fixed_args": ["operatie"], "timeout_s": TIMEOUT_PROBA,
                     "description": "CITIRE: rezultatul unei execuții după un timeout (operatie_id din răspuns).",
                     "params": {"id": {"flag": "--id", "type": "integer", "required": True}}},
    }
    for a in sorted(schema, key=lambda x: x["nume"]):
        n, tinta, et = a["nume"], a.get("tinta", "comanda"), a.get("eticheta") or a["nume"]
        # doar câmpurile comune pe care le primește acțiunea (`comune` din schemă): comanda-noua (pe magazin) n-are
        # `comanda`, iar Order Hub ar răspunde 400 „Câmp necunoscut" dacă agentul ar completa-o
        primite = set(a.get("comune") or ("comanda", "magazin", "utilizator", "motiv", "confirmare", "peste"))
        comune = {"utilizator": {"flag": "--utilizator", "type": "string", "required": True,
                                 "description": "numele omului de la CS, cum îl spune el"},
                  "comanda": {"flag": "--comanda", "type": "string", "required": tinta == "comanda"},
                  "magazin": {"flag": "--magazin", "type": "string", "required": tinta == "magazin"}}
        comune = {k: v for k, v in comune.items() if k in primite}
        proprii = {k: _param(v, k, bool(v.get("obligatoriu"))) for k, v in (a.get("parametri") or {}).items()}
        proba = {**comune, **({"motiv": {"flag": "--motiv", "type": "string"}} if "motiv" in primite else {}),
                 **proprii}
        executie = {**comune, "motiv": {"flag": "--motiv", "type": "string", "required": True},
                    "confirmare": {"flag": "--confirmare", "type": "string", "required": True},
                    "peste": {"flag": "--peste", "type": "string",
                              "description": "exact codurile din peste_necesar, separate prin virgulă"},
                    **proprii}
        out[n] = {"risk": "allow", "fixed_args": [n], "timeout_s": TIMEOUT_PROBA, "params": proba,
                  "description": "PROBĂ, nu scrie: %s. Întoarce planul, blocajele (imposibil / politica), "
                                 "peste_necesar și confirmarea. Execuția: %s-apply." % (et, n)}
        out[n + "-apply"] = {"risk": "ask", "fixed_args": [n, "--apply"], "timeout_s": TIMEOUT_EXECUTIE,
                             "params": executie,
                             "description": "EXECUȚIE, scrie: %s. Doar după acordul omului, cu confirmarea din probă "
                                            "și peste = exact peste_necesar. Cod 0 făcut · 2 nimic scris (și comanda "
                                            "ocupată): proba din nou · 3 refuz sau plafon: spui omului · 4 parțial, nu "
                                            "repeta · 5 necunoscut / în lucru / Order Hub se oprește: repetă IDENTIC "
                                            "sau `operatie --id`, nu proba." % et}
    for act in out.values():   # gardă: niciun boolean, --apply doar în fixed_args, confirmare doar la -apply
        for p in act["params"].values():
            assert p["type"] in ("string", "integer"), p
    for a in schema:           # gardă: niciun câmp comun pe care acțiunea nu-l primește (400 „Câmp necunoscut")
        primite = set(a.get("comune") or ())
        if primite:
            for n in (a["nume"], a["nume"] + "-apply"):
                comune_aici = set(out[n]["params"]) & {"comanda", "magazin", "utilizator", "motiv", "confirmare",
                                                       "peste"}
                assert comune_aici <= primite, (n, comune_aici - primite)
    return out


def manifest(schema):
    acts = schema.get("actiuni") if isinstance(schema, dict) else schema
    return {"skill": "gigi:order-hub",
            "exec": {"entrypoint": "order_hub.py", "secrets": ["OH_CS_SB_TOKEN"], "actions": actiuni_manifest(acts)}}


def verifica(cale_schema, cale_manifest):
    """0 = manifestul din fișier e exact ce generează schema acum; 1 = vechi sau ilizibil (spune ce acțiuni diferă)."""
    nou = manifest(json.load(open(cale_schema, encoding="utf-8")))
    try:
        vechi = json.load(open(cale_manifest, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("%s: necitit (%s) · regenerează-l" % (cale_manifest, type(e).__name__))
        return 1
    if vechi == nou:
        print("%s: la zi" % cale_manifest)
        return 0
    an, av = nou["exec"]["actions"], ((vechi.get("exec") or {}).get("actions") or {}) if isinstance(vechi, dict) else {}
    diferite = sorted(n for n in set(an) | set(av) if an.get(n) != av.get(n))
    print("%s: VECHI (%s) · uv run gen_manifest.py %s > %s" % (
        cale_manifest, ", ".join(diferite) or "antet", cale_schema, cale_manifest))
    return 1


def main():
    if sys.argv[1:2] == ["--verifica"]:
        sys.exit(verifica(sys.argv[2], sys.argv[3]))
    print(json.dumps(manifest(json.load(open(sys.argv[1], encoding="utf-8"))), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
