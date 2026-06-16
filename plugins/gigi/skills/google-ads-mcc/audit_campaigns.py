# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Audit complet: campanii → asset groups → ads pentru Esteban și Belasil.
Arată statusul (ENABLED/PAUSED/REMOVED) și ce conțin.

Conturi:
  Esteban  cid=5229815058
  Belasil  cid=7566352958

PMax ACTIVE (ENABLED):
  Esteban: camp 23924430848 "Performance Max" → Bărbați, Unisex, Damă
  Belasil: camp 22478321481 "All Products"    → [ALS] P.Max

PMax INACTIVE:
  Esteban: camp 23918558286 "Campaign #1"          PAUSED → AG1 (6720307893)
  Esteban: camp 23923794365 "Performance Max-2"    PAUSED → Performance Max-2 AG
  Belasil: camp 22478291976 "Campaign #1"          REMOVED → AG1 (6570921716) ← NU mai serveste
  Belasil: camp 23312943064 "Allsoft PMax Laveta"  PAUSED → Laveta AG (6638306494)

Run: DATABASE_URL_METRICS=<dsn> uv run audit_campaigns.py [--cid 5229815058]
"""
import os, sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"

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

# filter by --cid if provided
filter_cid = None
for i, a in enumerate(sys.argv):
    if a == "--cid" and i+1 < len(sys.argv):
        filter_cid = sys.argv[i+1]

ACCOUNTS = [
    ("5229815058", "Esteban (parfumuri)"),
    ("7566352958", "Belasil (detergent)"),
]
if filter_cid:
    ACCOUNTS = [(c,n) for c,n in ACCOUNTS if c == filter_cid]

def search(cid, q):
    r = requests.post(
        f"https://googleads.googleapis.com/{API}/customers/{cid}/googleAds:search",
        headers=H, json={"query": q}, timeout=30)
    if r.status_code != 200:
        print(f"  [WARN] query failed: {r.text[:200]}")
        return []
    return r.json().get("results", [])

STATUS_ICON = {"ENABLED": "▶", "PAUSED": "⏸", "REMOVED": "✗"}

for cid, brand in ACCOUNTS:
    print(f"\n{'='*65}")
    print(f"  {brand}  |  cid={cid}")
    print(f"{'='*65}")

    # ── 1. Campanii ──────────────────────────────────────────────────
    camps = search(cid,
        "SELECT campaign.id, campaign.name, campaign.status, "
        "campaign.advertising_channel_type, campaign.bidding_strategy_type "
        "FROM campaign ORDER BY campaign.advertising_channel_type, campaign.name")
    camp_map = {}
    print(f"\n  CAMPANII ({len(camps)}):")
    for r2 in camps:
        c2 = r2.get("campaign", {})
        ctype = c2.get("advertisingChannelType","?")
        status = c2.get("status","?")
        icon = STATUS_ICON.get(status, "?")
        rn = f"customers/{cid}/campaigns/{c2.get('id')}"
        camp_map[rn] = {"name": c2.get("name"), "status": status, "type": ctype}
        print(f"    {icon} [{status:7s}] {ctype:20s}  id={c2.get('id'):15s}  {c2.get('name')}")

    # ── 2. Asset Groups (PMax) ───────────────────────────────────────
    ags = search(cid,
        "SELECT asset_group.id, asset_group.name, asset_group.status, "
        "asset_group.ad_strength, asset_group.campaign "
        "FROM asset_group")
    if ags:
        print(f"\n  ASSET GROUPS / AD SETS ({len(ags)}):")
        for r2 in ags:
            ag = r2.get("assetGroup", {})
            camp_rn = ag.get("campaign", "")
            camp = camp_map.get(camp_rn, {})
            c_status = camp.get("status", "?")
            c_name   = camp.get("name", "?")
            ag_status = ag.get("status", "?")
            strength  = ag.get("adStrength", "?")
            ic = STATUS_ICON.get(c_status, "?")
            ia = STATUS_ICON.get(ag_status, "?")
            active = "  ← ACTIV" if c_status == "ENABLED" and ag_status == "ENABLED" else ""
            print(f"    Camp{ic}[{c_status:7s}] '{c_name[:30]}'")
            print(f"      AG{ia}[{ag_status:7s}] id={ag.get('id')}  '{ag.get('name')}'  strength={strength}{active}")

    # ── 3. Ads (Search / Display) ─────────────────────────────────────
    ads = search(cid,
        "SELECT ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, "
        "ad_group_ad.ad.final_urls, "
        "ad_group.id, ad_group.name, ad_group.status, "
        "campaign.id, campaign.name, campaign.status "
        "FROM ad_group_ad")
    if ads:
        print(f"\n  ADS — ad_group_ads ({len(ads)}):")
        for r2 in ads:
            ada  = r2.get("adGroupAd", {})
            ag2  = r2.get("adGroup", {})
            c2   = r2.get("campaign", {})
            c_st = c2.get("status","?")
            ag_st= ag2.get("status","?")
            a_st = ada.get("status","?")
            ic   = STATUS_ICON.get(c_st, "?")
            iag  = STATUS_ICON.get(ag_st, "?")
            ia   = STATUS_ICON.get(a_st, "?")
            urls = ada.get("ad",{}).get("finalUrls",["?"])
            active = "  ← ACTIV" if c_st=="ENABLED" and ag_st=="ENABLED" and a_st=="ENABLED" else ""
            print(f"    Camp{ic}[{c_st:7s}] '{c2.get('name','?')[:25]}'  "
                  f"AG{iag}[{ag_st:7s}] '{ag2.get('name','?')[:20]}'  "
                  f"Ad{ia}[{a_st:7s}] id={ada.get('ad',{}).get('id')}  "
                  f"{urls[0] if urls else '?'[:40]}{active}")

print("\nLEGENDĂ: ▶=ENABLED  ⏸=PAUSED  ✗=REMOVED  ← ACTIV = campanie+ag+ad toate ENABLED")
