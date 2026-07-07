#!/usr/bin/env python3
# Creeaza/imbogateste un item Wikidata pt un brand ARONA (entity signal pt Knowledge Graph + AI-search).
# Login = contul echipei (clientlogin, NU action=login). Creds: ~/Downloads/credentials/wikidata.txt (format "user pass: parola").
# Idempotent: cauta item existent dupa website (P856); daca exista, doar adauga claims lipsa.
#
# Ex: python3 wikidata_brand.py --label "Nubra" --domain nubra.ro \
#       --desc-en "Romanian online perfume store" --desc-ro "magazin online de parfumuri" \
#       --p31 Q4382945 --fb nubra.ro --ig nubra.ro --tiktok nubra.ro --linkedin nubra-parfumuri \
#       --ref https://<presa-tertiara-despre-brand>  --apply
import os, re, sys, json, argparse, urllib.parse, urllib.request, http.cookiejar, pathlib

API="https://www.wikidata.org/w/api.php"
# QID-uri utile: online shop=Q4382945, brand=Q431289, business=Q4830453; tara Romania=Q218; industrie e-commerce=Q484847
PROPS_SOCIAL={"fb":"P2013","ig":"P2003","tiktok":"P7085","linkedin":"P4264"}  # toate external-id (string)

def creds():
    raw=pathlib.Path(os.path.expanduser("~/Downloads/credentials/wikidata.txt")).read_text(encoding="utf-8").strip()
    m=re.match(r'^(.*?)\s*,?\s*pass:\s*(.*)$', raw, re.I|re.S)
    return m.group(1).strip().rstrip(",").strip(), m.group(2).strip()

def session():
    cj=http.cookiejar.CookieJar()
    op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders=[("User-Agent","ARONA-SEO/1.0 (contact@arona.ro)")]
    return op

def call(op, post=None, **p):
    p["format"]="json"
    if post is None: return json.load(op.open(API+"?"+urllib.parse.urlencode(p)))
    p.update(post); return json.load(op.open(API, urllib.parse.urlencode(p).encode()))

def login(op):
    user,pw=creds()
    lt=call(op, action="query", meta="tokens", type="login")["query"]["tokens"]["logintoken"]
    cl=call(op, post={"action":"clientlogin","logintoken":lt,"username":user,"password":pw,"loginreturnurl":"https://www.wikidata.org/"})["clientlogin"]
    if cl.get("status")!="PASS": sys.exit("login FAIL: "+str(cl))
    csrf=call(op, action="query", meta="tokens", type="csrf")["query"]["tokens"]["csrftoken"]
    return user, csrf

def find_existing(op, label, domain):
    r=call(op, action="wbsearchentities", search=label, language="en", type="item", limit=10)
    for x in r.get("search",[]):
        desc=(x.get("description") or "").lower()
        if any(k in desc for k in ("shop","store","brand","magazin","company","e-commerce","perfum")):
            # verifica website
            e=call(op, action="wbgetentities", ids=x["id"], props="claims")["entities"][x["id"]]
            for c in e.get("claims",{}).get("P856",[]):
                v=c["mainsnak"].get("datavalue",{}).get("value","")
                if domain in str(v): return x["id"]
    return None

def item_string_claim(pid, val):
    return {"mainsnak":{"snaktype":"value","property":pid,"datavalue":{"value":val,"type":"string"}},"type":"statement","rank":"normal"}
def item_entity_claim(pid, qid, ref_url=None):
    c={"mainsnak":{"snaktype":"value","property":pid,"datavalue":{"value":{"entity-type":"item","numeric-id":int(qid[1:])},"type":"wikibase-entityid"}},"type":"statement","rank":"normal"}
    if ref_url: c["references"]=[{"snaks":{"P854":[{"snaktype":"value","property":"P854","datavalue":{"value":ref_url,"type":"string"}}]}}]
    return c

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--label", required=True); ap.add_argument("--domain", required=True)
    ap.add_argument("--desc-en", required=True); ap.add_argument("--desc-ro", required=True)
    ap.add_argument("--p31", default="Q4382945")   # online shop
    ap.add_argument("--country", default="Q218"); ap.add_argument("--industry", default="Q484847")
    ap.add_argument("--fb"); ap.add_argument("--ig"); ap.add_argument("--tiktok"); ap.add_argument("--linkedin")
    ap.add_argument("--ref", help="URL de presa tertiara despre brand (referinta anti-stergere)")
    ap.add_argument("--apply", action="store_true", help="fara asta = dry-run")
    a=ap.parse_args()
    op=session(); user,csrf=login(op)
    qid=find_existing(op, a.label, a.domain)
    core=[item_entity_claim("P31", a.p31, a.ref),
          item_string_claim("P856", f"https://{a.domain}"),
          item_entity_claim("P17", a.country),
          item_entity_claim("P452", a.industry)]
    socials=[item_string_claim(PROPS_SOCIAL[k], getattr(a,k)) for k in PROPS_SOCIAL if getattr(a,k)]
    plan={"labels":{"en":{"language":"en","value":a.label},"ro":{"language":"ro","value":a.label}},
          "descriptions":{"en":{"language":"en","value":a.desc_en},"ro":{"language":"ro","value":a.desc_ro}},
          "claims":core+socials}
    if not a.apply:
        print("DRY-RUN. Item existent:", qid or "(niciunul, se creeaza nou)")
        print(json.dumps(plan, ensure_ascii=False, indent=1)[:1200]); return
    if qid:
        r=call(op, post={"action":"wbeditentity","id":qid,"data":json.dumps({"claims":core+socials}),
                         "summary":f"Enrich {a.label} (website, country, industry, social profiles)","token":csrf,"bot":"0"})
    else:
        r=call(op, post={"action":"wbeditentity","new":"item","data":json.dumps(plan),
                         "summary":f"Create item for {a.label} ({a.desc_en})","token":csrf,"bot":"0"})
    if "entity" in r:
        q=r["entity"]["id"]; print(f"OK: {q} -> https://www.wikidata.org/wiki/{q}")
    else:
        print("EROARE:", json.dumps(r)[:600])

if __name__=="__main__": main()
