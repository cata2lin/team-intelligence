# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""
check_token_expiry.py — alertă PROACTIVĂ pe expirarea token-urilor de ad-spend din metrics.

DE CE: tot sync-ul Meta al companiei trece printr-UN singur token OAuth („Sabina Radu") cu 100+
conturi atârnate (Esteban, GT, Nubra, Belasil, Bonhaus, Grandia, Reflexino…). Când expiră, TOT
spend-ul Meta se oprește tăcut și profitul/P&L-ul devine greșit pe toate brandurile (incident
2026-06-19). bi-data-integrity-check prinde abia DUPĂ ce datele se învechesc; ăsta avertizează
ÎNAINTE, pe baza coloanei expiresAt.

CE FACE: citește `meta_access_tokens` (și `tiktok_access_tokens` dacă există) și flaghează
token-urile ACTIVE care au EXPIRAT sau expiră în următoarele N zile (--days, implicit 7), cu numărul
de conturi afectate. Exit code 2 = ceva deja expirat, 1 = expiră curând, 0 = ok → ușor de pus pe cron.

READ-ONLY. Nu printează niciodată valoarea token-ului. Conexiune din ENV DATABASE_URL_METRICS
(ca run_cache.sh) sau, fallback, din secretul KB prin arona_pg.

RULARE:
  export DATABASE_URL_METRICS="$(grep -m1 ^DATABASE_URL_METRICS= /root/Scripturi/.env | cut -d= -f2-)"
  /root/Scripturi/.venv/bin/python check_token_expiry.py --days 7
  # cron cu email (trimite DOAR când e ceva de raportat), via SA looker-sheets (gmail.modify):
  /root/Scripturi/.venv/bin/python check_token_expiry.py --days 7 \
      --email gheorghe.beschea@overheat.agency --key /root/Scripturi/google_credentials.json
"""
import os, sys, re, argparse, datetime


def _send_email(to, subject, body, key, sender):
    """Trimite alertul prin SA looker-sheets (impersonare Workspace, scope gmail.modify).
    Lazy-import ca scriptul să ruleze pur (fără google libs) când NU se cere --email."""
    import base64
    from email.mime.text import MIMEText
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/gmail.modify"]).with_subject(sender)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = MIMEText(body, _charset="utf-8")
    msg["to"] = to; msg["from"] = sender; msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()

def _dsn():
    dsn = os.environ.get("DATABASE_URL_METRICS")
    if not dsn:
        try:
            sys.path.insert(0, "/root/Scripturi")
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import arona_pg  # type: ignore
            dsn = arona_pg.secret("DATABASE_URL_METRICS")
        except Exception:
            pass
    if not dsn:
        sys.exit("Lipsește DATABASE_URL_METRICS (din ENV sau KB).")
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def _table_exists(cur, name):
    cur.execute("SELECT to_regclass(%s)", (name,))
    return cur.fetchone()[0] is not None


def main():
    ap = argparse.ArgumentParser(description="Alertă proactivă pe expirarea token-urilor de ads (metrics).")
    ap.add_argument("--days", type=int, default=7, help="prag de avertizare înainte de expirare (zile)")
    ap.add_argument("--email", help="adresă unde trimite alertul (DOAR dacă există ceva de raportat)")
    ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency",
                    help="expeditor impersonat de SA (default gheorghe.beschea@overheat.agency)")
    ap.add_argument("--key", default="google_credentials.json", help="cheia SA pt trimiterea email-ului")
    a = ap.parse_args()

    import psycopg2
    conn = psycopg2.connect(_dsn()); conn.set_session(readonly=True)
    cur = conn.cursor()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)  # naive UTC (col e timestamp wo tz)
    soon = now + datetime.timedelta(days=a.days)

    alerts = []   # (severity, platform, label, expiresAt, accounts)
    # Meta: un token, multe conturi → afișăm câte conturi atârnă
    if _table_exists(cur, "public.meta_access_tokens"):
        cur.execute("""
            SELECT t.label, t."metaUserName", t."expiresAt", t."isActive",
                   (SELECT COUNT(*) FROM meta_ad_accounts a WHERE a."tokenId"=t.id) AS accts,
                   (SELECT COUNT(*) FROM meta_ad_accounts a WHERE a."tokenId"=t.id AND a."isActive") AS active_accts
            FROM meta_access_tokens t
            WHERE t."isActive" AND t."expiresAt" IS NOT NULL
        """)
        for label, user, exp, active, accts, active_accts in cur.fetchall():
            if exp is None:
                continue
            if exp <= now:
                sev = "EXPIRAT"
            elif exp <= soon:
                sev = "EXPIRĂ CURÂND"
            else:
                continue
            alerts.append((sev, "meta", label or user or "(meta token)", exp, active_accts or accts or 0))

    # TikTok: dacă tabela are coloană de expirare
    if _table_exists(cur, "public.tiktok_access_tokens"):
        cur.execute("""SELECT column_name FROM information_schema.columns
                       WHERE table_schema='public' AND table_name='tiktok_access_tokens'""")
        cols = {r[0] for r in cur.fetchall()}
        expcol = next((c for c in ("expiresAt", "expires_at", "expiry", "accessTokenExpiresAt") if c in cols), None)
        if expcol:
            cur.execute(f'SELECT id, "{expcol}" FROM tiktok_access_tokens WHERE "{expcol}" IS NOT NULL')
            for tid, exp in cur.fetchall():
                if isinstance(exp, (int, float)):   # epoch
                    exp = datetime.datetime.fromtimestamp(exp, datetime.timezone.utc).replace(tzinfo=None)
                if exp <= now:
                    alerts.append(("EXPIRAT", "tiktok", str(tid), exp, "?"))
                elif exp <= soon:
                    alerts.append(("EXPIRĂ CURÂND", "tiktok", str(tid), exp, "?"))

    conn.close()

    if not alerts:
        print(f"[token-expiry] OK — niciun token activ expirat sau care expiră în {a.days} zile.")
        return 0

    worst = 0
    lines = [f"[token-expiry] ⚠ {len(alerts)} token(uri) de atenționat (prag {a.days} zile):"]
    for sev, platform, label, exp, accts in sorted(alerts, key=lambda x: x[3]):
        days = (exp - now).days
        when = "expirat de %d zile" % (-days) if days < 0 else "expiră în %d zile" % days
        lines.append(f"  [{sev}] {platform}: «{label}» — {when} (expiresAt {exp:%Y-%m-%d %H:%M} UTC) → {accts} conturi afectate")
        worst = max(worst, 2 if sev == "EXPIRAT" else 1)
    lines.append("  Fix: re-autorizare OAuth (re-login) pe contul de mai sus, apoi backfill de la data blocării → azi.")
    report = "\n".join(lines)
    print(report)

    if a.email:
        try:
            sev_tag = "EXPIRAT" if worst >= 2 else "expiră curând"
            _send_email(a.email, f"[ALERTĂ] Token ads {sev_tag} — sync Meta în pericol", report, a.key, a.sender)
            print(f"[token-expiry] email trimis către {a.email}")
        except Exception as e:
            print(f"[token-expiry] EROARE trimitere email: {type(e).__name__}: {e}", file=sys.stderr)
            return 3
    return worst


if __name__ == "__main__":
    sys.exit(main())
