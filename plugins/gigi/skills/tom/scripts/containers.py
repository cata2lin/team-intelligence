# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "google-api-python-client", "google-auth", "google-auth-oauthlib"]
# ///
"""Leaga PO-urile din TOM de CONTAINERE (sheet 'Tom - receptii containere').

Sheet-ul e sursa pentru ce vine si ce a SOSIT (steagul `Received` per tab). TOM nu are
containerele HA deloc (`shipments` = toate DRAFT, doar Grandia).

Reguli de identificare (taburile au asezari DIFERITE, nu presupune pozitii fixe):
  cod container <- din NUMELE tabului ("C50-25 Aug" -> C50), nu dintr-o celula
  Received      <- celula de sub eticheta "Received", oriunde ar fi ea
  coloane linii <- dupa numele din capul de tabel ("SKU", "Cantitate", "Status")
  potrivire SKU <- egalitate exacta, SAU sufix numeric de >=4 cifre ("20247" = "GD-DEP-20247").
                   NICIODATA pe cifra finala a unui nume ("set-6-textil-crem-5" nu e "5").
"""
import subprocess, psycopg2, json, collections, re, datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
SHEET = "1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU"
def secret(k):
    return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()

cred = Credentials.from_authorized_user_info(json.loads(secret("GOOGLE_OAUTH_TOKEN_JSON")),
                                             secret("GOOGLE_OAUTH_SCOPES").split())
api = build("sheets", "v4", credentials=cred).spreadsheets()
tabs = [s["properties"]["title"] for s in api.get(spreadsheetId=SHEET).execute()["sheets"]]

LUNI = {"ian":1,"jan":1,"feb":2,"mar":3,"apr":4,"mai":5,"may":5,"iun":6,"jun":6,
        "iul":7,"jul":7,"aug":8,"sep":9,"oct":10,"noi":11,"nov":11,"dec":12}
def data_din_titlu(tab):
    """Data de receptie e in NUMELE tabului ('C48- 05 Aug', 'C46 04.08', 'C45 27 iulie').
    Celula `Date` din tab e ALTA data (ETD) si le contrazice — nu o folosi ca sosire.
    Taie intai prefixul cu numarul containerului, altfel 'C33-08 Mai' se citeste ca 3 august."""
    rest = re.sub(r"^\s*c?\s*\d{1,3}\s*[-\u2013:]?\s*", "", tab.strip().lower(), count=1)
    def mk(d, mo):
        try: return datetime.date(2026, mo, d)
        except ValueError: return None
    m = re.match(r"(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)", rest)
    if m: return mk(int(m.group(1)), int(m.group(2)))
    m = re.match(r"(\d{1,2})\s+([a-z]{3})", rest)
    if m and m.group(2)[:3] in LUNI: return mk(int(m.group(1)), LUNI[m.group(2)[:3]])
    return None

def code_of(tab):
    m = re.match(r"^\s*C?\s*(\d{1,3})\b", tab.strip(), re.I)
    return "C%s" % m.group(1) if m else None

cont_tabs = [t for t in tabs if code_of(t) and t.strip().lower() != "master ha"]
rngs = ["'%s'!A1:J400" % t for t in cont_tabs]
vals = {}
for i in range(0, len(rngs), 40):
    r = api.values().batchGet(spreadsheetId=SHEET, ranges=rngs[i:i+40],
                              valueRenderOption="UNFORMATTED_VALUE").execute()
    for rng, res in zip(rngs[i:i+40], r["valueRanges"]):
        vals[rng.split("'")[1]] = res.get("values", [])

containers, nesigure = {}, []
for t, g in vals.items():
    recv, dt = None, None
    for i, row in enumerate(g[:20]):
        for j, x in enumerate(row):
            lab = str(x).strip().lower()
            if lab == "received":
                try: recv = str(g[i+1][j]).strip().lower()
                except Exception: recv = None
            if lab == "date":
                for k in (j+1, j+2):
                    try: v = g[i][k]
                    except Exception: continue
                    if isinstance(v, (int, float)) and 40000 < v < 60000:
                        dt = datetime.date(1899,12,30) + datetime.timedelta(days=int(v)); break
    hdr = cols = None
    for i, row in enumerate(g[:40]):
        low = [str(x).strip().lower() for x in row]
        if "sku" in low and any("cantitate" in x for x in low):
            hdr = i
            cols = {"sku": low.index("sku"),
                    "qty": next(j for j, x in enumerate(low) if "cantitate" in x),
                    "st": next((j for j, x in enumerate(low) if x == "status"), None)}
            break
    lines = []
    if hdr is not None:
        for row in g[hdr+1:]:
            if len(row) <= cols["qty"]: continue
            sku = str(row[cols["sku"]]).strip() if len(row) > cols["sku"] else ""
            qty = row[cols["qty"]]
            if not sku or not isinstance(qty, (int, float)) or qty <= 0: continue
            st = str(row[cols["st"]]).strip() if cols["st"] is not None and len(row) > cols["st"] else ""
            lines.append((sku, int(qty), st))
    if recv is None or hdr is None:
        nesigure.append("%s(%s)" % (code_of(t), "fara Received" if recv is None else "fara tabel"))
    # ADEVARUL (confirmat de owner 26-aug-2026): au sosit TOATE pana la C50, exceptie C49.
    # Steagul `Received` din foaie e INCOMPLET (C16-C24 nu-l au deloc, C49 e pe False corect
    # dar altele sosite au ramas nebifate) — nu-l folosi ca sursa.
    nr = int(re.sub(r"\D", "", code_of(t)))
    sosit = nr <= 50 and nr != 49
    containers[code_of(t)] = {"tab": t, "received": sosit, "flag_foaie": recv, "stie": True,
                              "data": data_din_titlu(t), "etd": dt, "lines": lines}

# --- TOM ---
cn = psycopg2.connect(secret("DATABASE_URL_TOM").split("?")[0]); cn.set_session(readonly=True)
c = cn.cursor()
c.execute('''SELECT p."tomNumber", p.status::text, i."externalSku", i.status::text, i."requestedQty",
                    p."createdAt"::date
             FROM purchase_order_items i JOIN purchase_orders p ON p.id=i."poId"''')
tom = c.fetchall(); cn.close()

exact = collections.defaultdict(list)
numeric = collections.defaultdict(list)
for tn, pst, sku, lst, req, cre in tom:
    u = str(sku).strip().upper()
    exact[u].append((tn, pst, sku, lst, req, cre))
    m = re.search(r"(?:^|-)(\d{4,})$", u)
    if m: numeric[m.group(1)].append((tn, pst, sku, lst, req, cre))

def match(sku):
    u = str(sku).strip().upper()
    if u in exact: return exact[u]
    if re.fullmatch(r"\d{4,}", u): return numeric.get(u, [])
    return []

sosite = sorted(k for k, v in containers.items() if v["received"])
astept = sorted(k for k, v in containers.items() if v["stie"] and not v["received"] and v["lines"])
print("CONTAINERE: %d   SOSITE: %d   in asteptare: %d" % (len(containers), len(sosite), len(astept)))
print("  sosite :", ", ".join("%s(%s)" % (k, containers[k]["data"] or "?") for k in sosite))
print("  in drum:", ", ".join(astept))
dez = [k for k, v in containers.items() if v["flag_foaie"] is not None and (v["flag_foaie"] == "true") != v["received"]]
if dez: print("  ⚠ foaia zice altceva la:", ", ".join(sorted(dez, key=lambda x: int(re.sub(r"\D","",x)))))
if nesigure: print("  ⚠ nedeterminate:", ", ".join(sorted(set(nesigure))))

print("\n=== A. A SOSIT, dar TOM nu zice SHIPPED (status ramas in urma) ===")
g = collections.defaultdict(list); incert = set()
for code in sosite:
    d = containers[code]["data"]
    for sku, qty, st in containers[code]["lines"]:
        # candidati = liniile TOM cu acel SKU, din PO-uri create INAINTE de sosire
        cand = [m for m in match(sku)
                if d is None or m[5] is None or d >= m[5]]
        if not cand: continue
        if d is None: incert.add(code)
        # daca marfa e deja explicata de o linie SHIPPED, nu acuza alt PO pentru ea
        if any(m[3] == "SHIPPED" for m in cand): continue
        restante = [m for m in cand if m[3] != "CANCELLED"]
        amb = len({m[0] for m in restante}) > 1
        for tn, pst, tsku, lst, req, cre in restante:
            g[tn].append((code, tsku, qty, lst, amb))
for tn in sorted(g):
    sigur = [x for x in g[tn] if not x[4]]
    print("  %-9s %d linii (%d fara ambiguitate):" % (tn, len(g[tn]), len(sigur)))
    for code, sku, qty, lst, amb in sorted(g[tn])[:12]:
        print("        %-5s %-24s x%-7d TOM: %-9s %s" % (code, sku, qty, lst, "AMBIGUU" if amb else "sigur"))
if not g: print("  (nimic)")
if incert: print("  ⚠ fara data de receptie:", ", ".join(sorted(incert)))

print("\n=== B. TOM zice SHIPPED, dar containerul nu a sosit ===")
n = collections.defaultdict(list)
for code in astept:
    for sku, qty, st in containers[code]["lines"]:
        for tn, pst, tsku, lst, req, cre in match(sku):
            if lst == "SHIPPED": n[tn].append((code, tsku, qty))
for tn in sorted(n):
    print("  %-9s %d linii: %s" % (tn, len(n[tn]), ", ".join("%s/%s" % (a, b) for a, b, _ in sorted(n[tn])[:6])))
if not n: print("  (nimic)")

print("\n=== C. A SOSIT si NU exista in TOM ===")
tot = 0
for code in sosite:
    lipsa = [(s, q, st) for s, q, st in containers[code]["lines"] if not match(s)]
    if lipsa:
        tot += sum(q for _, q, _ in lipsa)
        nou = sum(1 for _, _, st in lipsa if st.upper().startswith("NOU"))
        print("  %-5s %3d pozitii (%d marcate NOU), %7d buc" % (code, len(lipsa), nou, sum(q for _, q, _ in lipsa)))
print("  TOTAL %d buc sosite fara PO in TOM" % tot)
