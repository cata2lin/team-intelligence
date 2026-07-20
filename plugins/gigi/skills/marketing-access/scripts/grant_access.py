#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["google-auth>=2.28", "requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
grant_access.py — Adaugă (sau doar listează) un user pe conturile de marketing ale echipei:
  • GA4 (Google Analytics 4)  → rol Editor/Analyst/Viewer/Admin, ACCES IMEDIAT
  • Google Ads                → rol Standard/Admin/Read-only, prin INVITAȚIE pe email (userul o acceptă)

Dry-run implicit (doar enumeră). Adaugă `--apply` ca să execute.
Secretele NU se printează — DSN/token doar prin env / din DB.

Utilizare:
  # GA4 — listează conturile, apoi adaugă (impersonează owner-ul prin DWD):
  uv run grant_access.py ga4  --email nou@agentie.ro --role editor
  uv run grant_access.py ga4  --email nou@agentie.ro --role editor --apply

  # Google Ads — necesită DSN-ul metrics în env:
  DATABASE_URL_METRICS="$(kb.py secret-get DATABASE_URL_METRICS)" \
    uv run grant_access.py gads --email nou@agentie.ro --role STANDARD
  DATABASE_URL_METRICS=... uv run grant_access.py gads --email nou@agentie.ro --role STANDARD --apply

Config specific echipei (identificatori, NU secrete):
  --key      calea cheii SA GA4 (implicit google_credentials.json din Scripturi)
  --subject  owner GA4 de impersonat prin DWD (implicit gheorghe.beschea@overheat.agency)
  DWD client ID pt scope GA4:  105430525977895660493   (Workspace admin → API controls)
  MCC Google Ads:              7467110480 (NOVOS DIGITAL) — din google_ads_connections
"""
import os, sys, argparse, json
import requests

DEFAULT_KEY = "/Users/gheorghebeschea/Downloads/Scripturi/google_credentials.json"
DEFAULT_SUBJECT = "gheorghe.beschea@overheat.agency"
DWD_CLIENT_ID = "105430525977895660493"

GA4_ROLES = {"editor": "predefinedRoles/editor", "analyst": "predefinedRoles/analyst",
             "viewer": "predefinedRoles/viewer", "admin": "predefinedRoles/admin"}
GADS_ROLES = {"ADMIN", "STANDARD", "READ_ONLY", "EMAIL_ONLY"}


# ─────────────────────────── GA4 ───────────────────────────

def _ga4_creds(key, scopes, subject=None):
    from google.oauth2 import service_account
    import google.auth.transport.requests as gart
    c = service_account.Credentials.from_service_account_file(key, scopes=scopes)
    if subject:
        c = c.with_subject(subject)
    c.refresh(gart.Request())
    return c


def cmd_ga4(a):
    role = GA4_ROLES.get(a.role.lower())
    if not role:
        sys.exit(f"Rol GA4 invalid: {a.role}. Alege: {', '.join(GA4_ROLES)}")

    # 1) listez conturile pe care le vede SA-ul (readonly, fără impersonare)
    ro = _ga4_creds(a.key, ["https://www.googleapis.com/auth/analytics.readonly"])
    H = {"Authorization": f"Bearer {ro.token}"}
    accts, tok = [], None
    while True:
        u = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200"
        if tok:
            u += f"&pageToken={tok}"
        j = requests.get(u, headers=H, timeout=30).json()
        for s in j.get("accountSummaries", []):
            accts.append((s["account"], s.get("displayName", "")))
        tok = j.get("nextPageToken")
        if not tok:
            break
    accts.sort(key=lambda x: x[1].lower())
    print(f"GA4 — {len(accts)} conturi vizibile:")
    for acc, name in accts:
        print(f"  {acc:<16} {name}")

    if not a.apply:
        print(f"\n[DRY] Rulează cu --apply ca să adaug {a.email} ({a.role}) pe toate.")
        return

    # 2) impersonez owner-ul (DWD) cu scope manage.users și creez binding-urile
    try:
        wr = _ga4_creds(a.key, ["https://www.googleapis.com/auth/analytics.manage.users"], subject=a.subject)
    except Exception as e:
        print("❌ Nu pot obține token impersonat:", str(e)[:200])
        print(f">>> Autorizează în Workspace (admin.google.com → Security → API controls → Domain-wide "
              f"delegation) clientul {DWD_CLIENT_ID} pt scope "
              f"https://www.googleapis.com/auth/analytics.manage.users, apoi reia.")
        sys.exit(1)
    HW = {"Authorization": f"Bearer {wr.token}", "Content-Type": "application/json"}
    print(f"\n=== GA4: adaug {a.email} ({a.role}) — impersonez {a.subject} ===")
    ok = already = fail = 0
    for acc, name in accts:
        r = requests.post(f"https://analyticsadmin.googleapis.com/v1alpha/{acc}/accessBindings",
                          headers=HW, json={"user": a.email, "roles": [role]}, timeout=30)
        if r.status_code == 200:
            ok += 1; print(f"  ✅ {name}")
        elif "exist" in r.text.lower() or r.status_code == 409:
            already += 1; print(f"  ⏭️  {name} (avea deja)")
        else:
            fail += 1; print(f"  ❌ {name}: HTTP {r.status_code} {r.text[:150]}")
    print(f"\n=== GA4 BILANȚ: {ok} adăugate, {already} aveau deja, {fail} eșuate ===")


# ─────────────────────────── Google Ads ───────────────────────────

def _gads_creds():
    import psycopg2, psycopg2.extras
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    ok = {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout",
          "application_name", "options", "channel_binding"}

    def clean(d):
        p = urlsplit(d)
        return d if not p.query else urlunsplit((p.scheme, p.netloc, p.path,
            urlencode([(x, y) for x, y in parse_qsl(p.query, keep_blank_values=True) if x.lower() in ok]), p.fragment))

    dsn = os.environ.get("DATABASE_URL_METRICS")
    if not dsn:
        sys.exit("Setează DATABASE_URL_METRICS (din KB) în env pentru Google Ads.")
    cx = psycopg2.connect(clean(dsn)); cx.set_session(readonly=True)
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,'
                  '"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true')
        r = c.fetchone()
    if not r:
        sys.exit("Nicio conexiune Google Ads activă în google_ads_connections.")
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "refresh_token", "client_id": r["cid"],
        "client_secret": r["csec"], "refresh_token": r["rt"]}, timeout=20).json()["access_token"]
    mcc = "".join(ch for ch in str(r["mcc"]) if ch.isdigit())
    return tok, r["dev"], mcc


def cmd_gads(a):
    role = a.role.upper()
    if role not in GADS_ROLES:
        sys.exit(f"Rol Google Ads invalid: {a.role}. Alege: {', '.join(GADS_ROLES)}")
    tok, dev, mcc = _gads_creds()
    API = "v21"
    H = {"Authorization": f"Bearer {tok}", "developer-token": dev,
         "login-customer-id": mcc, "Content-Type": "application/json"}

    q = ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code "
         "FROM customer_client WHERE customer_client.status = 'ENABLED' AND customer_client.manager = FALSE")
    sr = requests.post(f"https://googleads.googleapis.com/{API}/customers/{mcc}/googleAds:search",
                       headers=H, json={"query": q}, timeout=60)
    if sr.status_code != 200:
        sys.exit(f"Enumerare eșuată: HTTP {sr.status_code} {sr.text[:300]}")
    accts = sorted(((str(r["customerClient"]["id"]), r["customerClient"].get("descriptiveName", ""))
                    for r in sr.json().get("results", [])), key=lambda x: x[1].lower())
    print(f"Google Ads — MCC {mcc} — {len(accts)} conturi active (ENABLED, non-manager):")
    for cid, name in accts:
        print(f"  {cid:<12} {name}")

    if not a.apply:
        print(f"\n[DRY] Rulează cu --apply ca să trimit invitații {role} către {a.email}.")
        return

    print(f"\n=== Google Ads: invit {a.email} ({role}) ===")
    ok = already = fail = 0
    for cid, name in accts:
        r = requests.post(
            f"https://googleads.googleapis.com/{API}/customers/{cid}/customerUserAccessInvitations:mutate",
            headers=H, json={"operation": {"create": {"emailAddress": a.email, "accessRole": role}}}, timeout=30)
        if r.status_code == 200:
            ok += 1; print(f"  ✅ {name} ({cid})")
        elif any(k in r.text.upper() for k in ("ALREADY", "PENDING", "EXISTS")):
            already += 1; print(f"  ⏭️  {name} ({cid}) — deja invitat/are acces")
        elif "EMAIL_DOMAIN_POLICY_VIOLATED" in r.text:
            fail += 1; print(f"  ⛔ {name} ({cid}) — domeniu nepermis (Admin → Access and security → Domains: adaugă domeniul)")
        else:
            fail += 1; print(f"  ❌ {name} ({cid}): HTTP {r.status_code} {r.text[:160]}")
    print(f"\n=== Google Ads BILANȚ: {ok} trimise, {already} deja, {fail} eșuate ===")
    if a.apply and ok:
        print(f">>> {a.email} trebuie să ACCEPTE invitațiile pe email ca accesul să devină activ.")


def main():
    p = argparse.ArgumentParser(description="Adaugă un user pe conturile GA4 + Google Ads ale echipei.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("ga4", help="adaugă user pe conturile GA4 (acces imediat)")
    g.add_argument("--email", required=True)
    g.add_argument("--role", default="editor", help="editor|analyst|viewer|admin")
    g.add_argument("--subject", default=DEFAULT_SUBJECT, help="owner GA4 de impersonat (DWD)")
    g.add_argument("--key", default=DEFAULT_KEY, help="cheia SA GA4 (JSON)")
    g.add_argument("--apply", action="store_true")
    g.set_defaults(func=cmd_ga4)

    d = sub.add_parser("gads", help="invită user pe conturile active Google Ads (cere acceptare)")
    d.add_argument("--email", required=True)
    d.add_argument("--role", default="STANDARD", help="STANDARD|ADMIN|READ_ONLY|EMAIL_ONLY")
    d.add_argument("--apply", action="store_true")
    d.set_defaults(func=cmd_gads)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
