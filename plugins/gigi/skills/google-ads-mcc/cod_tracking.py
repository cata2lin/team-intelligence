# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31", "google-auth>=2.0"]
# ///
"""cod_tracking.py — repară tracking-ul de conversii pentru magazinele cu COD FORM (Releasit/EasySell etc.).

PROBLEMA: app-ul Shopify „Google & YouTube" trage pixelul de purchase DOAR pe checkout-ul nativ /
pagina de thank-you. Magazinele COD folosesc un FORMULAR custom (Releasit „COD Form & Upsells",
EasySell ș.a.) care OCOLEȘTE checkout-ul nativ → purchase-ul nu se trimite niciodată → Google Ads
arată 0 conversii deși există comenzi reale (simptom: Page View/View Item se trackuiesc, Purchase = 0).
Max Conversions rămâne ORB (fără semnal) și nu poate optimiza.

FIX-ul (acest tool):
  1. creează/găsește o conversie WEBPAGE PURCHASE dedicată „COD Purchase" (a NOASTRĂ, nu cea app-managed),
  2. o face primary + se asigură că goal-ul PURCHASE e biddable (Max Conversions optimizează pe ea),
  3. scoate valorile de pus în tab-ul „Conversion/Pixel tracking" al app-ului de COD form:
       • Google Ads Conversion ID = AW-<conversion_tracking_id>
       • Purchase Label          = label-ul acțiunii (din tag_snippets)
       • GA4 Measurement ID       = G-… (din property-ul GA4 al magazinului)
Releasit/EasySell au câmp „Google Ads" built-in → trag singure gtag('event','conversion', send_to,
value, currency, transaction_id) pe thank-you-ul inline. NU trebuie cod în temă.

Capcană atribuire: Releasit prinde UTM, NU gclid → atribuirea = cookie auto-tagging Google, same-session
(merge pt majoritatea comenzilor în aceeași zi). Pt 100% etanș: captură gclid + Offline Conversion Import.

  uv run cod_tracking.py --cid 4069952156 --ga4 carpetto            # raportează (creează cu --apply)
  uv run cod_tracking.py --cid 4069952156 --ga4 carpetto --apply
"""
import os, re, sys, json, argparse
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

def _kb(k):
    import subprocess
    kb = Path(__file__).resolve().parents[2] / "core" / "scripts" / "kb.py"
    return subprocess.run(["uv", "run", str(kb), "secret-get", k], capture_output=True, text=True).stdout.strip()

_OK = {"host","port","dbname","user","password","sslmode","connect_timeout","application_name","channel_binding"}
def clean(d):
    p = urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in _OK]),p.fragment))

# ── detecție COD form: semnături în storefront-ul public ──
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
COD_SIGNS = [
    ("Releasit COD Form", ["_RSI_COD_FORM_SETTINGS", "releasit-cod-form", "rsi-cod-form-do-not-change", "releasit"]),
    ("EasySell COD Form", ["easysell", "tycoonwebsolutions", "_es_cod_form", "es-cod-form"]),
    ("Zipify OCU/COD",     ["zipify", "one click upsell"]),
]
def _fetch(url):
    try: return requests.get(url, headers=_UA, timeout=20).text
    except Exception: return ""
def detect_cod_form(url):
    """întoarce (are_cod_form, nume_app). Detecția app-ului e fiabilă; dacă pixelul Google Ads e
    deja configurat în el verifici manual în app (HTML-ul minificat nu permite o citire sigură a items_array)."""
    html = _fetch(url)
    m = re.search(r'/products/[\w%\-]+', html)         # mai mult semnal dintr-o pagină de produs
    if m: html += _fetch(url.rstrip("/") + m.group(0))
    low = html.lower()
    for app, sigs in COD_SIGNS:
        if any(s.lower() in low for s in sigs):
            return (True, app)
    if ("ramburs" in low or "plata la livrare" in low) and ("cod-form" in low or "cod_form" in low):
        return (True, "COD form (generic)")
    return (False, None)
def resolve_public_url(search):
    for x in search("SELECT asset_group.final_urls FROM asset_group WHERE asset_group.status='ENABLED'") + \
             search("SELECT campaign.tracking_url_template, campaign.final_url_suffix FROM campaign WHERE campaign.status='ENABLED' LIMIT 1"):
        urls = (x.get("assetGroup") or {}).get("finalUrls") or []
        if urls:
            p = urlsplit(urls[0]); return f"{p.scheme}://{p.netloc}"
    return None

def ga4_measurement_id(hint):
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gar
        here = Path(__file__).resolve(); cred=None
        for up in range(0, 8):
            c = here.parents[up] / "google_credentials.json"
            if c.exists():
                cred = service_account.Credentials.from_service_account_file(str(c), scopes=["https://www.googleapis.com/auth/analytics.readonly"]); break
        if cred is None:
            cred = service_account.Credentials.from_service_account_info(json.loads(_kb("GA4_SA_JSON")), scopes=["https://www.googleapis.com/auth/analytics.readonly"])
        cred.refresh(gar.Request()); AH={"Authorization":f"Bearer {cred.token}"}
        asum = requests.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200", headers=AH, timeout=30).json()
        prop=None
        for a in asum.get("accountSummaries", []):
            for p in a.get("propertySummaries", []):
                if hint.lower() in p.get("displayName","").lower(): prop=p["property"]
        if not prop: return "(property GA4 negăsit pt hint '%s')" % hint
        ds = requests.get(f"https://analyticsadmin.googleapis.com/v1beta/{prop}/dataStreams", headers=AH, timeout=30).json()
        for s in ds.get("dataStreams", []):
            if s.get("webStreamData",{}).get("measurementId"): return s["webStreamData"]["measurementId"]
        return "(stream web fără measurementId)"
    except Exception as e:
        return f"(GA4 err: {str(e)[:80]})"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cid", required=True, help="customer id Google Ads (fără liniuțe)")
    ap.add_argument("--ga4", default="", help="hint nume property GA4 (ex 'carpetto')")
    ap.add_argument("--name", default="COD Purchase", help="numele acțiunii de conversie")
    ap.add_argument("--url", default="", help="URL public storefront (altfel îl rezolv din final_urls contului)")
    ap.add_argument("--force", action="store_true", help="continuă setup-ul chiar dacă NU detectez COD form")
    ap.add_argument("--apply", action="store_true", help="creează acțiunea dacă lipsește (altfel doar raportează)")
    a = ap.parse_args(); CID=a.cid
    url = os.getenv("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
    cx = psycopg2.connect(clean(url)); cx.set_session(readonly=True)
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" oid,"oauthClientSecret" sec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
    tok = requests.post("https://oauth2.googleapis.com/token", data={"grant_type":"refresh_token","client_id":r["oid"],"client_secret":r["sec"],"refresh_token":r["rt"]}, timeout=20).json()["access_token"]
    MCC = "".join(ch for ch in str(r["mcc"]) if ch.isdigit())
    H = {"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":MCC,"Content-Type":"application/json"}
    def search(q): return requests.post(f"https://googleads.googleapis.com/v21/customers/{CID}/googleAds:search",headers=H,json={"query":q},timeout=40).json().get("results",[])
    def mut(svc, ops): return requests.post(f"https://googleads.googleapis.com/v21/customers/{CID}/{svc}:mutate",headers=H,json={"operations":ops,"validateOnly":False},timeout=40)

    # ── 0) detectează dacă magazinul ARE COD form (altfel fix-ul nu se aplică) ──
    purl = a.url or resolve_public_url(search)
    if purl:
        has, app = detect_cod_form(purl)
        if has:
            print(f"🔍 COD form detectat: {app}  ({purl}) → pune valorile de mai jos în tab-ul Conversion tracking al app-ului.")
        else:
            print(f"🔍 {purl}: NU am detectat COD form cunoscut → magazin pare cu checkout NATIV.")
            print("   Pixelul standard (app Google & YouTube) ar trebui să meargă; tool-ul ăsta probabil nu e necesar.")
            if not a.force:
                print("   (dacă totuși vrei să continui, rulează cu --force)"); return
    else:
        print("🔍 (n-am putut rezolva URL-ul public — dă --url <storefront> ca să detectez COD form-ul)")

    aw = search("SELECT customer.conversion_tracking_setting.conversion_tracking_id FROM customer")[0]["customer"]["conversionTrackingSetting"]["conversionTrackingId"]
    ex = search(f"SELECT conversion_action.resource_name, conversion_action.primary_for_goal FROM conversion_action WHERE conversion_action.name='{a.name}'")
    if ex:
        ca_rn = ex[0]["conversionAction"]["resourceName"]; print(f"✓ '{a.name}' există: {ca_rn}")
    elif a.apply:
        op = [{"create":{"name":a.name,"category":"PURCHASE","type":"WEBPAGE","status":"ENABLED","primaryForGoal":True,
               "countingType":"ONE_PER_CLICK","valueSettings":{"defaultValue":0,"alwaysUseDefaultValue":False},
               "clickThroughLookbackWindowDays":30,"attributionModelSettings":{"attributionModel":"GOOGLE_ADS_LAST_CLICK"}}}]
        cr = mut("conversionActions", op).json()
        if cr.get("error"): sys.exit("EROARE create: "+json.dumps(cr["error"])[:300])
        ca_rn = cr["results"][0]["resourceName"]; print(f"✓ creat '{a.name}': {ca_rn}")
    else:
        print(f"'{a.name}' nu există — rulează cu --apply ca s-o creez."); return
    # label din tag snippets
    label=None
    for x in search(f"SELECT conversion_action.tag_snippets FROM conversion_action WHERE conversion_action.resource_name='{ca_rn}'"):
        for sn in x["conversionAction"].get("tagSnippets",[]):
            m = re.search(r"AW-\d+/([\w-]+)", (sn.get("eventSnippet","") or "") + " " + (sn.get("globalSiteTag","") or ""))
            if m: label=m.group(1)
    # asigură PURCHASE/WEBSITE biddable
    for x in search("SELECT customer_conversion_goal.origin, customer_conversion_goal.biddable, customer_conversion_goal.resource_name FROM customer_conversion_goal WHERE customer_conversion_goal.category='PURCHASE'"):
        g=x["customerConversionGoal"]
        if g.get("origin")=="WEBSITE" and not g.get("biddable") and a.apply:
            mut("customerConversionGoals",[{"update":{"resourceName":g["resourceName"],"biddable":True},"updateMask":"biddable"}]); print("→ PURCHASE/WEBSITE setat biddable")
    mid = ga4_measurement_id(a.ga4) if a.ga4 else "(dă --ga4 <hint>)"
    print("\n══════════ DE PUS ÎN COD FORM (Releasit/EasySell → Conversion tracking) ══════════")
    print(f"  Google Ads Conversion ID:  AW-{aw}")
    print(f"  Purchase Label:            {label or '(rulează cu --apply / verifică tag_snippets)'}")
    print(f"  send_to:                   AW-{aw}/{label}")
    print(f"  GA4 Measurement ID:        {mid}")
    print("\nReleasit/EasySell trag singure gtag conversion pe thank-you-ul inline. Conversiile apar în 24-48h.")

if __name__ == "__main__":
    main()
