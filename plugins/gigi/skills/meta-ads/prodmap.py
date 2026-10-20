# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0", "psycopg2-binary>=2.9"]
# ///
"""Map an ad campaign → product group, and flag TEST vs SALES — for multi-product "deals" accounts
(e.g. Reflexino = Magdeal's FB account, where each campaign sells a different product and TEST campaigns
are kept separate from real sales). Rules come from the team's Nomenclator sheet (same logic as the
ARONA profitability `apply_mapping`):
  1. AUTO: a `HA-<digits>` code in the campaign name → that SKU.
  2. else the Nomenclator rules (ACCOUNT / CAMPAIGN_KEYWORD / AD_KEYWORD; accent-insensitive substring).
TEST vs SALES: a campaign whose name contains "TEST" is TEST; everything else is SALES (vânzare).
  uv run prodmap.py sync                       # refresh prod_rules.json from the sheet
  uv run prodmap.py test "<account>" "<campaign>"   # show product_group + test/sales
"""
import os, sys, json, re, subprocess, unicodedata
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "prod_rules.json"
_KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"

def _kb(key):
    try:
        return subprocess.run(["uv","run",str(_KB),"secret-get",key], capture_output=True, text=True, timeout=45).stdout.strip()
    except Exception:
        return ""
def _sheet_id(): return _kb("NOMENCLATOR_SHEET_ID") or "12L1KlG4EXxe6OAeZROEeDipy-72iuUzdaMoP_y-g5I0"
def _creds():
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    sa = _kb("GA4_SA_JSON")
    if sa: return Credentials.from_service_account_info(json.loads(sa), scopes=scopes)
    return Credentials.from_service_account_file(os.environ.get("GOOGLE_SA_JSON","/Users/gheorghebeschea/Downloads/Scripturi/google_credentials.json"), scopes=scopes)

def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())

def sync():
    from googleapiclient.discovery import build
    api = build("sheets", "v4", credentials=_creds()).spreadsheets(); SID = _sheet_id()
    out = {"facebook": [], "tiktok": []}
    for tab, plat in (("Nomenclator", "facebook"), ("Nomenclator Tiktok", "tiktok")):
        rows = api.values().get(spreadsheetId=SID, range=f"'{tab}'").execute().get("values", [])
        if len(rows) < 2: continue
        hdr = [str(x).upper() for x in rows[0]]
        def col(*names, default=0):
            return next((hdr.index(n) for n in names if n in hdr), default)
        pg, ti, pi = col("PRODUCT_GROUP", default=0), col("FB_MAP_TYPE","TT_MAP_TYPE","MAP_TYPE", default=1), col("FB_PATTERN","TT_PATTERN","PATTERN", default=2)
        for r in rows[1:]:
            if len(r) > pi and r[pg].strip() and r[pi].strip():
                out[plat].append({"product_group": r[pg].strip(), "map_type": r[ti].strip().upper(), "pattern": r[pi].strip()})
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"synced fb={len(out['facebook'])} tt={len(out['tiktok'])} reguli -> {CACHE}")
    return out

_RULES = None
def _kb_rules():
    """Read campaign→group rules from the KB (kb_meta['ad_campaign_rules']) — the team-shared source.
    Returns None if the KB is unreachable so callers fall back to the local prod_rules.json cache."""
    url = os.environ.get("KB_DATABASE_URL")
    if not url: return None
    try:
        import psycopg2
        cx = psycopg2.connect(url, connect_timeout=10)
        with cx.cursor() as c:
            c.execute("SELECT value FROM kb_meta WHERE key='ad_campaign_rules'")
            r = c.fetchone()
        cx.close()
        d = json.loads(r[0]) if r and r[0] else None
        return d if d and (d.get("facebook") or d.get("tiktok")) else None
    except Exception as e:
        sys.stderr.write(f"[prodmap] reguli KB indisponibile ({type(e).__name__}); folosesc cache local\n")
        return None

def load():
    """Rules from KB first (live, team-shared), else the local prod_rules.json cache. Memoised.
    PRODMAP_RULES_JSON=<file> overrides both (dry-run a rule change before writing it to the KB)."""
    global _RULES
    if _RULES is None:
        override = os.environ.get("PRODMAP_RULES_JSON")
        if override:
            _RULES = json.loads(Path(override).read_text())
        else:
            _RULES = _kb_rules() or (json.loads(CACHE.read_text()) if CACHE.exists()
                                     else {"facebook": [], "tiktok": []})
    return _RULES


# Groups that are a whole brand or catalog, not one product.
BRAND_GROUPS = {"Esteban", "George Talent", "Nubra", "Belasil", "Labnoir", "Lab Noir", "Magdeal",
                "Ofertele Zilei", "Reduceri bune", "Casa Ofertelor", "DUPPO", "Artevita", "Test", "Iluminat"}

_ORDERED = {}
def _ordered(platform):
    """Keyword rules before account rules, longest pattern first (as the WMS mapping does):
    first-match in sheet order let an ACCOUNT rule ('Reduceri bune' → Covorase baie) swallow
    campaigns that name another product (covor pufos, mașina de cusut)."""
    rules = load().get(platform, [])
    key = (platform, id(rules))
    if key not in _ORDERED:
        kw = [r for r in rules if r.get("map_type") != "ACCOUNT"]
        acct = [r for r in rules if r.get("map_type") == "ACCOUNT"]
        # A product named in the campaign beats a brand/catalog keyword in the same name
        # ("OGLINDA ACRILICA #5 - OFERTELE ZILEI ALINA" is the mirror, not the whole catalog).
        kw.sort(key=lambda r: (r["product_group"] in BRAND_GROUPS, -len(_norm(r.get("pattern")))))
        _ORDERED[key] = kw + acct
    return _ORDERED[key]


# HA code anywhere in campaign/ad: "HA-0217", "HA- 1322"; not "{HAC}" or "SHA-256"-like.
_HA_RE = re.compile(r"(?<![A-Za-z0-9])HA-\s?(\d{3,5})(?!\d)", re.IGNORECASE)


def ha_code(text):
    m = _HA_RE.search(text or "")
    return f"HA-{m.group(1)}" if m else None

def _pat_matches(pat, target):
    """Pattern-urile SCURTE (≤3 car., ex 'LA','LM','GT') se potrivesc DOAR pe word-boundary — altfel 'la' prinde
    'lavete', 'alex' etc. ca substring și un cont întreg cade fals pe un produs (bug 'Lavete abrazive' CZ 79k
    fantomă). Pattern-urile lungi rămân substring (sunt destul de specifice). Vezi [[mapping-tiktok-attribution]]."""
    if not pat:
        return False
    if len(pat) <= 3:
        return re.search(r"(?<![a-z0-9])" + re.escape(pat) + r"(?![a-z0-9])", target) is not None
    return pat in target

def product_of(platform, account, campaign, ad=""):
    """Campaign/ad → product group: AUTO HA-<digits> (in campaign OR ad), else the matching rule
    (keywords before accounts, longest first), else 'Unmapped'. A rule with an "account" field only
    applies on that ad account (a word like 'caserolă' is a different product per store)."""
    code = ha_code(f"{campaign or ''} {ad or ''}")
    if code: return code
    acct_n = _norm(account)
    for r in _ordered(platform):
        pat = _norm(r["pattern"])
        if not pat: continue
        if r.get("account") and _norm(r["account"]) != acct_n: continue
        target = {"ACCOUNT": account, "CAMPAIGN_KEYWORD": campaign, "AD_KEYWORD": ad,
                  "CAMPAIGN_AND_AD": f"{campaign} && {ad}"}.get(r["map_type"], "")
        if _pat_matches(pat, _norm(target)): return r["product_group"]
    return "Unmapped"

def is_test(campaign):
    # "NEW TEST STRATEGY" (Artevita) is a sales campaign structure, not a product test.
    return bool(re.search(r"\btest\b(?!\s+strateg)", _norm(campaign)))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "test":
        plat = "tiktok" if "tiktok" in sys.argv else "facebook"
        acc = sys.argv[2] if len(sys.argv) > 2 else ""
        camp = sys.argv[3] if len(sys.argv) > 3 else ""
        print("product:", product_of(plat, acc, camp), "| TEST" if is_test(camp) else "| SALES")
