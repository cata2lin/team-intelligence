# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""Sync the team's canonical brand→account mapping (Google Sheet 'CPA și financiar' → 'Mapping' tab)
into a local brand_map.json, and resolve a brand to its accounts. The mapping is the source of truth:
- Facebook/Google accounts are dedicated per brand (exact names — NOT name-ILIKE; e.g. Magdeal→FB 'Reflexino').
- TikTok can run several brands on ONE shared account (col 'Cont multiplu', e.g. 'ROSSI Nails Romania'),
  split by a campaign-name token (col 'Campanie', e.g. 'APRECIAT','GT','COVORIA').
Usage:
  GOOGLE_SA_JSON=/path/google_credentials.json uv run brandmap.py sync     # refresh the cache
  uv run brandmap.py show <brand>                                          # print resolved accounts
"""
import os, sys, json, subprocess
from pathlib import Path

TAB = "Mapping"
CACHE = Path(__file__).resolve().parent / "brand_map.json"
_KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
# columns: 0 Brand final · 1 Conturi Facebook · 2 Conturi Tiktok · 3 Shopify · 4 Conturi Google · 5 Campanie(token) · 6 Cont multiplu(tiktok shared)

def _kb(key):
    """Fetch a secret/config value from the SharedClaude KB (never hardcode creds)."""
    try:
        r = subprocess.run(["uv", "run", str(_KB), "secret-get", key], capture_output=True, text=True, timeout=45)
        return (r.stdout or "").strip()
    except Exception:
        return ""

def _sheet_id():
    return _kb("MAPPING_SHEET_ID") or "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"

def _creds():
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    sa = _kb("GA4_SA_JSON")                                    # SA JSON straight from the KB, in-memory
    if sa:
        return Credentials.from_service_account_info(json.loads(sa), scopes=scopes)
    path = os.environ.get("GOOGLE_SA_JSON", "/Users/gheorghebeschea/Downloads/Scripturi/google_credentials.json")
    return Credentials.from_service_account_file(path, scopes=scopes)   # fallback only

def _split(s):
    return [x.strip() for x in (s or "").replace("\n", ",").split(",") if x.strip()]

def sync():
    from googleapiclient.discovery import build
    api = build("sheets", "v4", credentials=_creds()).spreadsheets()
    rows = api.values().get(spreadsheetId=_sheet_id(), range=f"'{TAB}'").execute().get("values", [])
    def g(r, i): return r[i].strip() if i < len(r) and r[i] else ""
    out = {}
    for r in rows[1:]:
        brand = g(r, 0)
        if not brand: continue
        out[brand] = {
            "facebook": _split(g(r, 1)),
            "tiktok": _split(g(r, 2)),
            "google": g(r, 4),
            "campaign_token": g(r, 5),         # for shared TikTok accounts
            "tiktok_shared": g(r, 6),          # the multi-brand TikTok account name
        }
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"synced {len(out)} branduri -> {CACHE}")
    return out

def load():
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}

def resolve(brand):
    """Return the mapping entry whose 'Brand final' best matches `brand` (case-insensitive).
    Match priority: exact Brand-final == ; brand-final contains arg or arg contains brand-final ;
    campaign_token == arg. Returns (brand_final, entry) or (None, None)."""
    m = load()
    if not m: return None, None
    b = brand.strip().lower()
    for k in m:                                   # exact
        if k.lower() == b: return k, m[k]
    cand = [(k, v) for k, v in m.items() if b in k.lower() or k.lower() in b]
    if len(cand) == 1: return cand[0]
    for k, v in m.items():                         # campaign token (e.g. 'gt' -> George Talent)
        if v.get("campaign_token", "").lower() == b: return k, v
    if cand:                                        # ambiguous contains -> shortest name
        cand.sort(key=lambda kv: len(kv[0])); return cand[0]
    return None, None

def _tt_owners():
    """account-name(lower) -> set of brands that own it (via their TikTok list, or a brand-name match)."""
    m = load(); owners = {}
    for ob, ov in m.items():
        for acc in ov.get("tiktok", []):
            owners.setdefault(acc.strip().lower(), set()).add(ob)
        owners.setdefault(ob.strip().lower(), set()).add(ob)   # a brand name doubles as an account name
    return owners

def tiktok_accounts(brand):
    """[{name, campaign_filter}] for a brand. campaign_filter (the 'Campanie' token, e.g. 'GT') is set
    for any account ALSO owned by another brand — a shared/borrowed account where only campaigns whose
    name contains the token belong to this brand. Dedicated accounts get None (all campaigns count)."""
    bf, e = resolve(brand)
    if not e: return []
    tok = e.get("campaign_token", "") or ""
    owners = _tt_owners()
    out = []
    for acc in e.get("tiktok", []):
        borrowed = any(ob != bf for ob in owners.get(acc.strip().lower(), set()))
        out.append({"name": acc, "campaign_filter": (tok if borrowed and tok else None)})
    return out

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        sync()
    elif cmd == "show":
        bf, e = resolve(sys.argv[2])
        print(json.dumps({bf: e}, ensure_ascii=False, indent=1) if e else f"'{sys.argv[2]}' negăsit în mapping")
