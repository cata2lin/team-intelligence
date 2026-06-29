# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""gads_upload_conversions.py — server-side Enhanced Conversions via DATA MANAGER API (Grandia & co).

DE CE Data Manager API (NU ConversionUploadService): Google a deprecat UploadClickConversions pt
integrări noi (iun-2026) → cere `datamanager.googleapis.com/v1/events:ingest`. Match pe identitate
(email hash SHA-256), nu pe gclid (gclid nu e capturat — Shopify taie query string-ul).

AUTH: token cu scope `datamanager` (DATAMANAGER_REFRESH_TOKEN în KB + YOUTUBE_OAUTH_CLIENT_ID/SECRET).
Fără developer token. Cont GATA legal (acceptedCustomerDataTerms=true).

MODURI:
- `delivered` (DEFAULT, clean): ingest DOAR comenzile LIVRATE = venit real. Semnal întârziat dar corect.
- `placed`: ingest toate intratele non-terminal-negative (semnal rapid). NB: retractarea refuzurilor pe
  Data Manager API = `events:remove` (de adăugat — vezi --retract, momentan doar ingest).

Idempotent (SQLite per orderId). DRY-RUN by default; scrie real doar cu --apply.

  uv run gads_upload_conversions.py --store grandia --customer 9069610821 --login-customer 7467110480 \
       --conversion-action 7666059809 --mode delivered --days 7            # dry-run
  ... --apply                                                              # ingest real

⚠️ eventTimestamp = data PLASĂRII (frisbo_created_at, în lookback). Valoare = total_price/vat (ex-TVA RON).
"""
import os, sys, hashlib, sqlite3, argparse, json, datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, requests

TERMINAL_NEG = {"back_to_sender", "returning_to_sender", "refused", "cancelled"}
DM_INGEST = "https://datamanager.googleapis.com/v1/events:ingest"

def _load_env():
    """Încarcă .env în os.environ (parse Python, robust la valori cu spații/caractere — NU shell source)."""
    for p in ("/root/Scripturi/.env", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
        if not os.path.exists(p): continue
        for line in open(p, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ: os.environ[k] = v
_load_env()

def kb(k):
    v = os.environ.get(k)
    if v: return v
    import subprocess
    for c in ("/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py",
              os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              "/root/Scripturi/kb.py"):
        if os.path.exists(c):
            return subprocess.run(["uv","run",c,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
    return ""
def awb_conn():
    url = os.getenv("DATABASE_URL_AWBPRINT") or kb("DATABASE_URL_AWBPRINT")
    p = urlsplit(url); OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: url = urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    c = psycopg2.connect(url); c.set_session(readonly=True); return c
def sha(s): return hashlib.sha256((s or "").strip().lower().encode()).hexdigest()

def dm_token():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "grant_type":"refresh_token", "client_id":kb("YOUTUBE_OAUTH_CLIENT_ID"),
        "client_secret":kb("YOUTUBE_OAUTH_CLIENT_SECRET"), "refresh_token":kb("DATAMANAGER_REFRESH_TOKEN")})
    j = r.json()
    if "access_token" not in j: sys.exit(f"OAuth datamanager refresh failed: {j.get('error_description') or j}")
    return j["access_token"]

def db_open(path):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE IF NOT EXISTS conv(order_id TEXT PRIMARY KEY, uploaded_ts TEXT, status_at_upload TEXT, value REAL)")
    db.commit(); return db

def fetch(store, days, vat):
    cx = awb_conn()
    with cx.cursor() as c:
        c.execute("""SELECT o.id, o.customer_email, o.total_price, o.currency, o.frisbo_created_at, o.aggregated_status
            FROM orders o JOIN stores s ON s.uid=o.store_uid
            WHERE s.name ILIKE %s AND o.customer_email<>'' AND o.customer_email IS NOT NULL
              AND o.total_price>0 AND o.frisbo_created_at >= CURRENT_DATE - %s
            ORDER BY o.frisbo_created_at""", [f"%{store}%", days])
        rows = c.fetchall()
    return [dict(orderId=str(oid), email=sha(email), value=round(float(price)/vat,2), currency=(cur_ or "RON"),
                 ts=created.strftime("%Y-%m-%dT%H:%M:%S+03:00"), status=status)
            for oid,email,price,cur_,created,status in rows]

def chunks(x, n=2000):
    for i in range(0, len(x), n): yield x[i:i+n]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True); ap.add_argument("--customer", required=True)
    ap.add_argument("--login-customer", default=None, help="MCC id ca loginAccount — OMITE dacă ai acces DIRECT pe operating account (ex Grandia)")
    ap.add_argument("--conversion-action", required=True, help="conversion action id (productDestinationId)")
    ap.add_argument("--mode", choices=["delivered","placed"], default="delivered")
    ap.add_argument("--days", type=int, default=7); ap.add_argument("--vat", type=float, default=1.21)
    ap.add_argument("--marker-db", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "gads_uploaded.sqlite"))
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--validate-only", action="store_true", help="trimite validateOnly:true (testează fără a scrie)")
    a = ap.parse_args()

    orders = fetch(a.store, a.days, a.vat)
    db = db_open(a.marker_db); done = {r[0] for r in db.execute("SELECT order_id FROM conv")}
    if a.mode == "delivered":
        fresh = [o for o in orders if o["status"]=="delivered" and o["orderId"] not in done]
    else:
        fresh = [o for o in orders if o["status"] not in TERMINAL_NEG and o["orderId"] not in done]
    if a.limit: fresh = fresh[:a.limit]

    digits = lambda s: "".join(ch for ch in str(s) if ch.isdigit())
    dest = {"operatingAccount":{"accountType":"GOOGLE_ADS","accountId":digits(a.customer)},
            "productDestinationId":a.conversion_action}
    if a.login_customer:  # doar dacă accesezi prin MCC; OMITE pt acces direct (ex Grandia)
        dest["loginAccount"]={"accountType":"GOOGLE_ADS","accountId":digits(a.login_customer)}
    def ev(o): return {"eventTimestamp":o["ts"], "transactionId":o["orderId"],
                       "conversionValue":o["value"], "currency":o["currency"], "eventSource":"WEB",
                       "userData":{"userIdentifiers":[{"emailAddress":o["email"]}]}}

    print(f"=== Data Manager ingest [{a.mode}] — {a.store} → {digits(a.customer)}/conv {a.conversion_action} · {a.days}z ===")
    print(f"candidați: {len(fresh)} | valoare ex-TVA: {sum(o['value'] for o in fresh):,.0f} RON")
    if fresh:
        s=fresh[0]; print(f"  sample event: order={s['orderId']} val={s['value']} ts={s['ts']} status={s['status']} email={s['email'][:10]}…")
    if not a.apply:
        print("DRY-RUN — nimic trimis. Adaugă --apply."); return
    if not fresh: print("nimic de trimis."); return

    tok = dm_token(); H={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}
    acc=0
    for batch in chunks(fresh):
        body={"destinations":[dest], "encoding":"HEX",
              "events":[ev(o) for o in batch],
              "consent":{"adUserData":"CONSENT_GRANTED","adPersonalization":"CONSENT_GRANTED"}}
        if a.validate_only: body["validateOnly"]=True
        r=requests.post(DM_INGEST, headers=H, json=body, timeout=120)
        if r.status_code!=200:
            print("INGEST HTTP",r.status_code, r.text[:600]); sys.exit(1)
        resp=r.json()
        if not a.validate_only:
            for o in batch:
                db.execute("INSERT OR REPLACE INTO conv(order_id,uploaded_ts,status_at_upload,value) VALUES(?,datetime('now'),?,?)",(o["orderId"],o["status"],o["value"]))
            db.commit()
        acc+=len(batch)
        tag="VALIDAT" if a.validate_only else "trimis"
        print(f"  batch {len(batch)} {tag} | requestId={resp.get('requestId','?')} | {('ATENTIE: '+json.dumps(resp)[:300]) if 'error' in json.dumps(resp).lower() else 'ok'}")
    print(f"INGEST gata: {acc} evenimente {'VALIDATE' if a.validate_only else 'TRIMISE'}.")

if __name__=="__main__": main()
