# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0"]
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

def load():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {"facebook": [], "tiktok": []}

def product_of(platform, account, campaign, ad=""):
    """Campaign → product group: AUTO HA-<digits>, else first matching Nomenclator rule, else 'Unmapped'."""
    m = re.search(r"(HA-\d+)", str(campaign or ""), re.IGNORECASE)
    if m: return m.group(1).upper()
    for r in load().get(platform, []):
        pat = _norm(r["pattern"])
        if not pat: continue
        target = {"ACCOUNT": account, "CAMPAIGN_KEYWORD": campaign, "AD_KEYWORD": ad}.get(r["map_type"], "")
        if pat in _norm(target): return r["product_group"]
    return "Unmapped"

def is_test(campaign):
    return "test" in _norm(campaign)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "test":
        plat = "tiktok" if "tiktok" in sys.argv else "facebook"
        acc = sys.argv[2] if len(sys.argv) > 2 else ""
        camp = sys.argv[3] if len(sys.argv) > 3 else ""
        print("product:", product_of(plat, acc, camp), "| TEST" if is_test(camp) else "| SALES")
