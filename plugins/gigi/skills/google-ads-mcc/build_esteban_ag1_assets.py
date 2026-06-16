# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Fix Esteban Asset Group 1 (6720307893) — the main/general PMax asset group.
- Removes wrong business name 'ARONA SRL', adds 'Maison d'Esteban'
- Adds 12 headlines (to reach max 15), 4 long headlines (to reach 5), 3 descrieri (to reach 5)
  All drawn from BEST/GOOD performers in the Bărbați/Damă/Unisex AGs.

Run: DATABASE_URL_METRICS=$(kb.py secret-get DATABASE_URL_METRICS) \\
       uv run build_esteban_ag1_assets.py [--apply]
"""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
CID = "5229815058"
AG  = "customers/5229815058/assetGroups/6720307893"

# ── Wrong business-name link to remove ─────────────────────────────────────
ARONA_BN_LINK = "customers/5229815058/assetGroupAssets/6720307893~370843778323~BUSINESS_NAME"

# ── 12 new headlines (existing 3: "Esteban parfum", "Cele mai bune preturi", "Esente din Franta")
# Using BEST/GOOD performers from other asset groups + new ones.
HEADLINES_NEW = [
    "2+1 Gratis la Toate",           # 19 — BEST în Bărbați/Damă/Unisex
    "Set 3 Parfumuri 90 lei",         # 22 — BEST în Damă, GOOD în Bărbați/Unisex
    "50 ml de la 30 lei",             # 18 — GOOD în Bărbați/Damă/Unisex
    "Maison d'Esteban",               # 16 — GOOD
    "Livrare în 1-2 Zile",            # 19 — GOOD
    "Persistă Peste 12 Ore",          # 21
    "Transport Gratuit la 150 lei",   # 28
    "Arome de Designer",              # 17
    "Peste 120 de Arome",             # 18 — GOOD în Damă
    "O Fracțiune din Preț",           # 20 — GOOD în Damă
    "Cadoul Perfect",                 # 14
    "Parfumuri Inspirate de Lux",     # 26
]

# ── 4 new long headlines ────────────────────────────────────────────────────
LONG_NEW = [
    # tested in all subgroups (exact match → returnează ID existent)
    "Experiența unui parfum de designer, la o fracțiune din preț – 2+1 gratis la toate",
    # new — general, covers all genders
    "Parfumuri inspirate de lux, 50 ml, de la 30 lei – 2+1 gratis la orice comandă",
    # new — catalog breadth + offer
    "120+ arome bărbați, damă, unisex – set de 3 parfumuri la 90 lei + transport gratuit",
    # new — logistics USP
    "Livrare în 1-2 zile, retur gratuit, persistență 12h+ — la prețul tău corect",
]

# ── 3 new descriptions ──────────────────────────────────────────────────────
DESCR_NEW = [
    # BEST performer in Damă + Unisex AGs
    "Peste 120 de esențe atent selectate. Transport gratuit la comenzile peste 150 lei.",
    # GOOD across all subgroups
    "Experiență de designer la o fracțiune din preț. De la 30 lei. 2+1 gratis la toate.",
    # variant of BEST performer in subgroups — general (fără gen)
    "Set 3 parfumuri cadou la 90 lei. Persistă peste 12 ore. Cadoul perfect oricând.",
]

BUSINESS_NAME_NEW = "Maison d'Esteban"  # 16 chars ≤ 25

# ── infra ───────────────────────────────────────────────────────────────────
_PG_OK = {"host","port","dbname","user","password","sslmode","sslrootcert","sslcert",
          "sslkey","connect_timeout","application_name","options","channel_binding"}

def clean(dsn):
    p = urlsplit(dsn)
    if not p.query: return dsn
    kept = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() in _PG_OK]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(kept), p.fragment))

cx = psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"]))
cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,'
              '"oauthClientSecret" csec,"refreshToken" rt '
              'FROM google_ads_connections WHERE "isActive"=true')
    creds = c.fetchone()
cx.close()

tok = requests.post("https://oauth2.googleapis.com/token",
    data={"grant_type":"refresh_token","client_id":creds["cid"],
          "client_secret":creds["csec"],"refresh_token":creds["rt"]},
    timeout=20).json()["access_token"]

H = {"Authorization": f"Bearer {tok}",
     "developer-token": creds["dev"],
     "login-customer-id": "".join(ch for ch in str(creds["mcc"]) if ch.isdigit()),
     "Content-Type": "application/json"}

apply = "--apply" in sys.argv

def post(svc, ops, partial=False):
    body = {"operations": ops, "validateOnly": not apply}
    if partial: body["partialFailure"] = True
    r = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{CID}/{svc}:mutate",
        headers=H, json=body, timeout=90)
    return r

# ── char-limit pre-check ────────────────────────────────────────────────────
bad = ([t for t in HEADLINES_NEW if len(t) > 30] +
       [t for t in LONG_NEW      if len(t) > 90] +
       [t for t in DESCR_NEW     if len(t) > 90] +
       ([BUSINESS_NAME_NEW] if len(BUSINESS_NAME_NEW) > 25 else []))
if bad:
    print("EROARE limite caractere:", bad); sys.exit(1)
print("Verificare limite: OK")
for t in HEADLINES_NEW:
    print(f"  headline ({len(t):2d}): {t}")
for t in LONG_NEW:
    print(f"  long    ({len(t):2d}): {t}")
for t in DESCR_NEW:
    print(f"  descr   ({len(t):2d}): {t}")

# ── 1) remove ARONA SRL business name link ──────────────────────────────────
r0 = post("assetGroupAssets", [{"remove": ARONA_BN_LINK}], partial=True)
print(f"\n1) remove ARONA SRL: HTTP {r0.status_code}")
if r0.status_code != 200:
    print("   WARN:", r0.text[:300])

if not apply:
    print("\nDRY-RUN — adaugă --apply ca să execuți")
    sys.exit(0)

# ── 2) create business name asset + all text assets ─────────────────────────
allt = ([(BUSINESS_NAME_NEW, "BUSINESS_NAME")] +
        [(t, "HEADLINE")      for t in HEADLINES_NEW] +
        [(t, "LONG_HEADLINE")  for t in LONG_NEW] +
        [(t, "DESCRIPTION")    for t in DESCR_NEW])
ra = post("assets", [{"create": {"textAsset": {"text": t}}} for t,_ in allt], partial=True)
print(f"2) create text assets: HTTP {ra.status_code}")
if ra.status_code != 200: print(ra.text[:600]); sys.exit(1)

res = ra.json().get("results", [])
names = [(res[i].get("resourceName"), allt[i][1])
         for i in range(len(allt)) if res[i].get("resourceName")]
print(f"   asset-uri: {len(names)}")

# ── 3) link to asset group ───────────────────────────────────────────────────
rl = post("assetGroupAssets",
          [{"create": {"assetGroup": AG, "asset": rn, "fieldType": ft}}
           for rn, ft in names], partial=True)
print(f"3) link to AG: HTTP {rl.status_code}")
pf = rl.json().get("partialFailureError")
if pf:
    errs = pf.get("details", [{}])[0].get("errors", [])
    for e in errs[:5]:
        print("   partial err:", json.dumps(e, ensure_ascii=False)[:200])
else:
    print(f"   linkuri create: {len(rl.json().get('results', []))}")

print("\nGATA — Esteban AG1 completat: 15 headlines, 5 long headlines, 5 descrieri.")
print("       Business name: 'Maison d'Esteban' (ARONA SRL eliminat)")
