# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""GDPR + ANPC (RO consumer-law) compliance for Shopify stores.

  # one store (by stores.csv prefix or *.myshopify.com domain):
  uv run compliance.py --store OFER --gdpr --anpc \
      --company "ARONA SRL" --cui 37247302 --regcom J51/151/2017 \
      --address "str. Dunărea, nr. 9, Călărași" --apply

  # every RO store in stores.csv (domains ending .ro), same entity:
  uv run compliance.py --all-ro --gdpr --anpc \
      --company "ARONA SRL" --cui 37247302 --regcom J51/151/2017 \
      --address "str. Dunărea, nr. 9, Călărași" --apply

What it does (idempotent):
  --gdpr  -> creates a RO "Ștergere date personale (GDPR)" page (/pages/stergere-date)
             with right-to-erasure + consent-withdrawal + Meta-data + ANSPDCP/ANPC,
             unless the store already has a data-deletion/gdpr page.
  --anpc  -> ensures the Terms of Service policy contains the trader identification
             (company/CUI/Reg.Com/address) + ANPC/SAL/SOL links; CREATES a full RO
             Terms policy if none exists. Only re-adds the parts that are missing.

Notes / gotchas (see reference/pitfalls.md):
  - The legal entity is PER STORE — never copy one company's CUI to another store
    without confirmation. Pass it explicitly; --all-ro applies the SAME entity to
    every .ro store (use only when you've confirmed they share it).
  - International stores (.cz/.pl/.bg) are NOT under ANPC (other jurisdictions) —
    --all-ro skips non-.ro domains; give them GDPR only.
  - `shopPolicyUpdate` input takes `type` (not `id`); setting body on a type that
    has none CREATES that policy.
  - Custom/page-builder footers (GemPages, custom-liquid) may not render the footer
    menu, so footer ANPC buttons need the Theme Customizer; the Terms link still
    carries the legal content.
"""
import sys, os, argparse, csv, io, time
sys.path.insert(0, os.path.dirname(__file__))
from shopify_lib import Store, secret

SOL = "https://ec.europa.eu/consumers/odr/main/index.cfm?event=main.home2.show&lng=RO"

def gdpr_body(brand, dom, email):
    return f"""<h2>Ștergerea datelor personale &amp; Retragerea consimțământului (GDPR)</h2>
<p>La <strong>{brand}</strong> ({dom}) respectăm dreptul tău la protecția datelor, conform Regulamentului (UE) 2016/679 (GDPR). Aici afli cum îți exerciți drepturile, inclusiv <strong>dreptul de a fi uitat</strong> (ștergerea datelor) și <strong>retragerea consimțământului</strong>.</p>
<h3>Drepturile tale</h3><ul><li>Acces la date</li><li>Rectificare</li><li>Ștergere („dreptul de a fi uitat")</li><li>Restricționare și opoziție</li><li>Portabilitate</li><li>Retragerea consimțământului oricând</li></ul>
<h3>Datele primite prin Facebook / Meta</h3><p>Dacă ai interacționat cu noi prin Facebook/Instagram (Login, Pixel, API Meta), poți cere ștergerea datelor primite pe aceste căi.</p>
<h3>Cum trimiți o cerere</h3><p>Trimite un email la <a href="mailto:{email}">{email}</a> cu „Cerere GDPR" și include numele tău, email-ul/telefonul de la comandă și dreptul dorit. Răspundem în max. <strong>30 de zile</strong> (putem păstra date impuse de lege, ex. facturi).</p>
<h3>Reclamații</h3><p><a href="https://www.dataprotection.ro" target="_blank" rel="nofollow">ANSPDCP</a> și <a href="https://anpc.ro" target="_blank" rel="nofollow">ANPC</a>. Contact: <a href="mailto:{email}">{email}</a></p>"""

def ident_block(dom, company, cui, regcom, address):
    return (f'<h3>Identificarea comerciantului</h3><p>Website-ul {dom} este operat de <strong>{company}</strong>, '
            f'societate cu răspundere limitată, cu sediul social în {address}, înregistrată la Registrul Comerțului '
            f'sub nr. <strong>{regcom}</strong>, cod fiscal (CUI) <strong>{cui}</strong>.</p>')

ANPC_BLOCK = ('<h3>Soluționarea litigiilor (ANPC)</h3><p>Pentru reclamații te poți adresa <strong>ANPC</strong> – '
              '<a href="https://anpc.ro/" target="_blank" rel="nofollow">anpc.ro</a>, inclusiv '
              '<a href="https://anpc.ro/ce-este-sal/" target="_blank" rel="nofollow">SAL</a>, precum și platformei de '
              f'<a href="{SOL}" target="_blank" rel="nofollow">Soluționare Online a Litigiilor (SOL)</a>.</p>')

def full_terms(dom, company, cui, regcom, address):
    return (f'<h2>Termeni și condiții</h2><p>Prezentul document stabilește condițiile de utilizare a site-ului {dom} și de cumpărare a produselor.</p>'
            + ident_block(dom, company, cui, regcom, address)
            + '<h3>Comenzi și prețuri</h3><p>Prețurile sunt exprimate în lei și includ TVA. Comanda reprezintă un contract la distanță, confirmat prin email/telefon.</p>'
              '<h3>Plată și livrare</h3><p>Livrare prin curier; plata la livrare (ramburs) sau cu cardul. Termenele și costurile de livrare sunt afișate la finalizarea comenzii și în politica de livrare/retur.</p>'
              '<h3>Dreptul de retragere</h3><p>Conform OUG 34/2014, consumatorul are dreptul de a se retrage din contract în 14 zile, fără a invoca un motiv (cu excepțiile prevăzute de lege).</p>'
              '<h3>Garanție și protecția datelor</h3><p>Produsele beneficiază de garanție legală de conformitate. Prelucrarea datelor este descrisă în Politica de confidențialitate.</p>'
            + ANPC_BLOCK
            + '<h3>Legea aplicabilă</h3><p>Prezenții termeni sunt guvernați de legea română.</p>')

def do_store(s, A):
    dom = s.public
    info = s.gql("{shop{name contactEmail email}}")["shop"]
    # Prefer an email whose DOMAIN matches this store's public domain. Rebranded
    # stores carry a stale contactEmail from the old brand (e.g. Casa Ofertelor's
    # contactEmail is contact@bonhaus.ro while shop.email is contact@casaofertelor.ro)
    # — using it would print the wrong brand on the GDPR page.
    base = dom.split(".")[0].replace("-", "").lower()
    cand = [e for e in (info.get("contactEmail"), info.get("email")) if e]
    email = next((e for e in cand if base[:6] in e.lower().replace("-", "")), None) \
        or (cand[0] if cand else None) or f"contact@{dom}"
    out = []
    if A.gdpr:
        pages = s.rest("GET", "pages.json?limit=250")["pages"]
        if any(any(k in (p["handle"] + (p["title"] or "")).lower() for k in ("stergere-date", "ștergere", "data-delet", "gdpr")) for p in pages):
            out.append("GDPR:exists")
        elif A.apply:
            s.rest("POST", "pages.json", {"page": {"title": "Ștergere date personale (GDPR)", "handle": "stergere-date",
                                                   "body_html": gdpr_body(info["name"], dom, email), "published": True}})
            out.append("GDPR:created")
        else:
            out.append("GDPR:would-create")
    if A.anpc:
        if not (A.company and A.cui and A.regcom and A.address):
            out.append("ANPC:SKIP(need --company/--cui/--regcom/--address)")
        else:
            pols = s.gql('{shop{shopPolicies{type body}}}')["shop"]["shopPolicies"]
            tos = next((p for p in pols if p["type"] == "TERMS_OF_SERVICE"), None)
            body = (tos["body"] if tos else "") or ""
            acts = []
            if not body.strip():
                new = full_terms(dom, A.company, A.cui, A.regcom, A.address); acts = ["terms-created"]
            else:
                new = body
                if A.cui not in body:
                    new += "\n" + ident_block(dom, A.company, A.cui, A.regcom, A.address); acts.append("+trader-id")
                if "anpc.ro" not in body.lower():
                    new += "\n" + ANPC_BLOCK; acts.append("+anpc")
            if not acts:
                out.append("ANPC:ok")
            elif A.apply:
                r = s.gql('mutation($p:ShopPolicyInput!){shopPolicyUpdate(shopPolicy:$p){userErrors{message}}}',
                          {"p": {"type": "TERMS_OF_SERVICE", "body": new}})
                ue = r.get("shopPolicyUpdate", {}).get("userErrors")
                out.append("ANPC:" + (",".join(acts) if not ue else f"ERR {ue}"))
            else:
                out.append("ANPC:would " + ",".join(acts))
    return out

ap = argparse.ArgumentParser()
ap.add_argument("--store", help="stores.csv prefix or *.myshopify.com domain")
ap.add_argument("--all-ro", action="store_true", help="every .ro store in stores.csv")
ap.add_argument("--gdpr", action="store_true")
ap.add_argument("--anpc", action="store_true")
ap.add_argument("--company"); ap.add_argument("--cui"); ap.add_argument("--regcom"); ap.add_argument("--address")
ap.add_argument("--apply", action="store_true")
A = ap.parse_args()

targets = []
if A.all_ro:
    rows = list(csv.reader(io.StringIO(secret("SHOPIFY_STORES_CSV"))))
    targets = [(r[0].strip(), r[1].strip(), r[2].strip()) for r in rows[1:] if r and len(r) >= 3]
elif A.store:
    targets = [(A.store, A.store, None)]
else:
    sys.exit("pass --store <prefix|domain> or --all-ro")

for pref, shop, tok in targets:
    try:
        s = Store.from_csv(pref) if (tok or A.all_ro) else Store(shop)
        if A.all_ro and not s.public.endswith(".ro"):
            print(f"{pref:7} {s.public:24} skip (non-RO; ANPC N/A — da-i doar --gdpr separat)")
            if A.gdpr:
                pass  # for intl you may still want GDPR; re-run with --store
            continue
        print(f"{pref:7} {s.public:24} {do_store(s, A)}")
        time.sleep(0.2)
    except Exception as e:
        print(f"{pref:7} {shop:24} ERR {str(e)[:50]}")
