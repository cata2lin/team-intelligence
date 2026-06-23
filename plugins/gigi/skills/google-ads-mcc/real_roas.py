# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31", "google-auth>=2.0"]
# ///
"""real_roas.py — ROAS REAL per brand: spend Google vs venit atribuit Google de GA4 (model neutru),
ca sa nu decidem pe cifra UMFLATA pe care o raporteaza Google Ads (Shopping App last-click supra-crediteaza).

  ROAS pretins  = conversions_value (Google Ads) / cost
  ROAS REAL     = venit GA4 din canalele Google (Paid Search + Paid Shopping + Cross-network) / cost
  Umflare       = pretins / real

GA4 = sursa neutra. UTM-urile (utm_source=google) + gclid fac sesiunile Google sa cada pe aceste canale.
Capcana: campania BRAND „culege" cerere creata de Meta/organic → nici GA4 nu e 100% incremental, dar e
mult mai aproape de adevar decat cifra Google. Pt incremental real = test geo / pauza brand (separat).

  uv run real_roas.py                 # toate brandurile, 30 zile
  uv run real_roas.py --days 7
  uv run real_roas.py --brand Esteban
"""
import os, sys, argparse, json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
from google.oauth2 import service_account
import google.auth.transport.requests as gar

# brand -> cont Google Ads + indiciu nume property GA4 (auto-rezolvat din accountSummaries)
BRANDS = {
    "Esteban":        {"cid": "5229815058", "ga4": ["esteban", "maison"]},
    "Grandia":        {"cid": "9069610821", "ga4": ["grandia"]},
    "Belasil":        {"cid": "7566352958", "ga4": ["belasil"]},
    "Gento":          {"cid": "8148962111", "ga4": ["gento"]},
    "Carpetto":       {"cid": "4069952156", "ga4": ["carpetto"]},
    "GT":             {"cid": "5031005158", "ga4": ["george talent", "george-talent", "gt parfum"]},
    "Nubra":          {"cid": "7585902074", "ga4": ["nubra"]},
    "Ofertele Zilei": {"cid": "4778636466", "ga4": ["ofertele zilei"]},
}
GOOGLE_CHANNELS = ["Paid Search", "Paid Shopping", "Cross-network"]   # = Google Ads in GA4 (advertiser doar pe Google)

def _kb(k):
    here = Path(__file__).resolve()
    kb = here.parents[2] / "core" / "scripts" / "kb.py"
    import subprocess
    return subprocess.run(["uv", "run", str(kb), "secret-get", k], capture_output=True, text=True).stdout.strip()

_OK = {"host","port","dbname","user","password","sslmode","connect_timeout","application_name","channel_binding"}
def clean(d):
    p = urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in _OK]),p.fragment))

def sa_creds():
    here = Path(__file__).resolve()
    for up in range(0, 8):
        c = here.parents[up] / "google_credentials.json"
        if c.exists():
            return service_account.Credentials.from_service_account_file(str(c), scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    raw = _kb("GA4_SA_JSON")
    return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/analytics.readonly"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--brand", help="doar un brand")
    a = ap.parse_args()
    DUR = {7:"LAST_7_DAYS",14:"LAST_14_DAYS",30:"LAST_30_DAYS"}.get(a.days, "LAST_30_DAYS")
    ga4_start = f"{a.days}daysAgo"

    url = os.getenv("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
    cx = psycopg2.connect(clean(url)); cx.set_session(readonly=True)
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" oid,"oauthClientSecret" os_,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r = c.fetchone()
    gtok = requests.post("https://oauth2.googleapis.com/token", data={"grant_type":"refresh_token","client_id":r["oid"],"client_secret":r["os_"],"refresh_token":r["rt"]}, timeout=20).json()["access_token"]
    MCC = "".join(ch for ch in str(r["mcc"]) if ch.isdigit())
    GH = {"Authorization": f"Bearer {gtok}", "developer-token": r["dev"], "login-customer-id": MCC, "Content-Type": "application/json"}

    # GA4: property-urile accesibile SA-ului
    cred = sa_creds(); cred.refresh(gar.Request()); AH = {"Authorization": f"Bearer {cred.token}", "Content-Type": "application/json"}
    asum = requests.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200", headers=AH, timeout=30).json()
    props = {}
    for acc in asum.get("accountSummaries", []):
        for p in acc.get("propertySummaries", []):
            props[p.get("displayName","").lower()] = p.get("property")
    def find_prop(hints):
        for h in hints:
            for nm, prop in props.items():
                if h in nm: return prop
        return None

    def gads(cid):
        rr = requests.post(f"https://googleads.googleapis.com/v21/customers/{cid}/googleAds:searchStream", headers=GH,
                           json={"query": f"SELECT metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM customer WHERE segments.date DURING {DUR}"}, timeout=60).json()
        rows = [x for ch in rr for x in ch.get("results", [])] if isinstance(rr, list) else []
        if not rows: return (0.0, 0.0, 0.0)
        m = rows[0]["metrics"]; return (float(m.get("costMicros",0))/1e6, float(m.get("conversions",0)), float(m.get("conversionsValue",0)))

    def ga4_google_rev(prop):
        body = {"dateRanges":[{"startDate":ga4_start,"endDate":"yesterday"}],
                "dimensions":[{"name":"sessionDefaultChannelGroup"}],
                "metrics":[{"name":"purchaseRevenue"},{"name":"transactions"}]}
        gr = requests.post(f"https://analyticsdata.googleapis.com/v1beta/{prop}:runReport", headers=AH, json=body, timeout=30).json()
        rev = tx = 0.0
        for row in gr.get("rows", []):
            ch = row["dimensionValues"][0]["value"]
            if ch in GOOGLE_CHANNELS:
                rev += float(row["metricValues"][0]["value"]); tx += float(row["metricValues"][1]["value"])
        return rev, tx

    items = {a.brand: BRANDS[a.brand]} if a.brand and a.brand in BRANDS else BRANDS
    print(f"\nROAS REAL per brand — {a.days} zile (spend Google vs venit GA4-Google)\n")
    print(f"{'Brand':16} {'Spend':>7} {'ROAS pretins':>13} {'ROAS REAL':>10} {'Umflare':>8}  GA4")
    print("-"*72)
    for name, cfg in items.items():
        cost, conv, val = gads(cfg["cid"])
        claimed = val/cost if cost else 0
        prop = find_prop(cfg["ga4"])
        if not prop:
            print(f"{name:16} {cost:7,.0f} {claimed:12.1f}x {'—':>10} {'—':>8}  ⚠ fara acces GA4")
            continue
        rev, tx = ga4_google_rev(prop)
        real = rev/cost if cost else 0
        infl = claimed/real if real else 0
        print(f"{name:16} {cost:7,.0f} {claimed:12.1f}x {real:9.1f}x {infl:7.1f}x  ✓ {rev:,.0f} RON / {tx:.0f} cmd")
    print("\nNota: ROAS REAL = GA4 last-click (Paid Search+Shopping+Cross-network). Inca supra-crediteaza")
    print("campania BRAND (culege cerere creata de Meta/organic). Incremental real = test geo/pauza brand.")

if __name__ == "__main__":
    main()
