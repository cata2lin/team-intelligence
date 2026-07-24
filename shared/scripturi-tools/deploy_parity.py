"""
deploy_parity.py — gardă de PARITATE cod git ↔ fișierele flat rulate pe VPS.

DE CE: VPS-ul rulează un AMESTEC — fișiere copiate de mână în /root/Scripturi/ (flat) + un checkout
git. Nu există un „deploy" curat, așa că fișierele o iau razna în tăcere: într-o singură zi (24-iul)
am găsit din întâmplare 2 divergențe care erau bombe — profit_by_sku cu un fix de COGS doar pe VPS,
și tot pipeline-ul WMS rescris pe VPS dar cu git-ul stale care l-ar fi șters la următorul deploy.
Asta e cauza-rădăcină, nu lipsa de tool-uri. Tool-ul ăsta o face VIZIBILĂ și reparabilă.

Compară fiecare fișier din git (origin/main) care are un GEMEN flat în /root/Scripturi/ și raportează:
IDENTIC / DIFERĂ / LIPSEȘTE. Auto-descoperă (nu manifest hardcodat care se învechește).

Rulează pe VPS (are checkout-ul git + fișierele flat local — fără SSH). Cron-abil, email pe drift NOU.

  deploy_parity.py check                         # tabel paritate (exit 1 dacă e drift)
  deploy_parity.py check --email X --key ...      # + email DOAR pe drift nou (tranziție)
  deploy_parity.py deploy --apply                 # copiază git(origin/main) → flat pt cele DIFERITE (cu .bak)
                                                  # ⚠ direcția „git = adevăr"; NU rula orbește dacă VPS e înainte
"""
import os, sys, glob, hashlib, argparse, subprocess, sqlite3, shutil
from datetime import date, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.environ.get("PARITY_REPO", "/root/Scripturi/team-intelligence")
FLAT = os.environ.get("PARITY_FLAT", "/root/Scripturi")
REF = os.environ.get("PARITY_REF", "origin/main")
PF_DB = "/root/Scripturi/data/profitability.db"
# directoarele din git ale căror scripturi se deployează flat în /root/Scripturi/
SCAN_DIRS = [
    "plugins/gigi/skills/metrics-cache/scripts",
    "shared/scripturi-tools",
    "plugins/core/scripts",
]
IGNORE = {"__init__.py", "kb.py", "kb_env.py", "pg_mcp_launch.py", "mcp_env_launch.py",
          "sqlite_ssh_mcp_launch.py", "ro_sqlite_mcp.py"}  # launchere MCP, nu se deployează flat


def _send_email(to, subject, body, key, sender):
    import base64
    from email.mime.text import MIMEText
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/gmail.modify"]).with_subject(sender)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = MIMEText(body, _charset="utf-8")
    msg["to"] = to; msg["from"] = sender; msg["subject"] = subject
    svc.users().messages().send(
        userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()


def _md5(b):
    return hashlib.md5(b).hexdigest() if b is not None else None


def _git_blob(path):
    """Conținutul unui fișier din git la REF (bytes) sau None dacă nu există acolo."""
    r = subprocess.run(["git", "-C", REPO, "show", "%s:%s" % (REF, path)],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def discover():
    """→ list de (git_path, flat_path, basename) pt fișierele git cu geamăn flat. Semnalează coliziuni.
    Listăm din REF (git ls-tree), NU din working tree — checkout-ul VPS poate fi în urmă și n-ar
    vedea fișierele noi (data_health/reconcile/mapping_admin adăugate după HEAD-ul local)."""
    seen = {}
    out = []
    for d in SCAN_DIRS:
        r = subprocess.run(["git", "-C", REPO, "ls-tree", "-r", "--name-only", REF, "--", d],
                           capture_output=True, text=True)
        for rel in sorted(r.stdout.splitlines()):
            if not (rel.endswith(".py") or rel.endswith(".sh")):
                continue
            b = os.path.basename(rel)
            if b in IGNORE:
                continue
            flat = os.path.join(FLAT, b)
            if not os.path.exists(flat):
                continue  # nu se deployează flat → nu ne interesează
            if b in seen:
                sys.stderr.write("[parity] ⚠ coliziune basename %s: %s ȘI %s — verific ambele\n" % (b, seen[b], rel))
            seen[b] = rel
            out.append((rel, flat, b))
    return out


def check():
    rows = []
    for rel, flat, b in discover():
        gblob = _git_blob(rel)
        try:
            with open(flat, "rb") as f:
                fblob = f.read()
        except OSError:
            fblob = None
        gm, fm = _md5(gblob), _md5(fblob)
        if gblob is None:
            st = "GIT-LIPSĂ"
        elif fblob is None:
            st = "FLAT-LIPSĂ"
        elif gm == fm:
            st = "IDENTIC"
        else:
            st = "DIFERĂ"
        rows.append((st, b, rel))
    return rows


def _state():
    cx = sqlite3.connect(PF_DB); cx.execute("PRAGMA busy_timeout=8000;")
    cx.execute("CREATE TABLE IF NOT EXISTS parity_state (basename TEXT PRIMARY KEY, status TEXT, seen_date TEXT)")
    return cx


def main():
    ap = argparse.ArgumentParser(description="Gardă de paritate cod git ↔ VPS flat")
    sub = ap.add_subparsers(dest="cmd")
    pc = sub.add_parser("check", help="raport paritate")
    pc.add_argument("--email"); pc.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    pc.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    pc.add_argument("--all", action="store_true", help="arată și IDENTIC")
    pd = sub.add_parser("deploy", help="copiază git(origin/main) → flat pt cele DIFERITE")
    pd.add_argument("--apply", action="store_true", help="chiar copiază (altfel dry-run)")
    pd.add_argument("--only", help="doar acest basename")
    a = ap.parse_args()
    cmd = a.cmd or "check"

    subprocess.run(["git", "-C", REPO, "fetch", "-q", "origin"], capture_output=True)
    rows = check()
    drift = [r for r in rows if r[0] in ("DIFERĂ", "FLAT-LIPSĂ")]

    if cmd == "deploy":
        todo = [r for r in rows if r[0] == "DIFERĂ" and (not a.only or r[1] == a.only)]
        if not todo:
            print("Nimic de deployat (nicio divergență DIFERĂ)."); return 0
        for st, b, rel in todo:
            dst = os.path.join(FLAT, b)
            print("%s  %s → %s" % ("APLIC" if a.apply else "DRY", rel, dst))
            if a.apply:
                shutil.copy2(dst, dst + ".bak_" + datetime.utcnow().strftime("%Y%m%d_%H%M%S"))
                with open(dst, "wb") as f:
                    f.write(_git_blob(rel))
        print("\n⚠ Direcția a fost git→VPS. Dacă VPS avea un fix necomis, e în .bak — verifică." if a.apply
              else "\n(dry-run — adaugă --apply)")
        return 0

    # check
    out = ["PARITATE COD git(%s) ↔ VPS flat — %s UTC" % (REF, datetime.utcnow().strftime("%Y-%m-%d %H:%M")), ""]
    if drift:
        out.append("🔴 DIVERGENȚE (%d):" % len(drift))
        out += ["  %-11s %-26s %s" % (st, b, rel) for st, b, rel in drift]
    else:
        out.append("🟢 Toate fișierele flat sunt identice cu git (%d verificate)." % len(rows))
    if a.__dict__.get("all"):
        out += ["", "IDENTIC:"] + ["  %s" % b for st, b, _ in rows if st == "IDENTIC"]
    out += ["", "Ce DIFERĂ = fișier flat pe VPS ≠ git. Poate fi VPS-înainte (fix necomis → adu-l în git) sau "
            "git-înainte (nedeployat → deploy_parity.py deploy). Verifică direcția înainte de deploy."]
    report = "\n".join(out)
    print(report)

    # email DOAR pe drift NOU (basename care tocmai a devenit divergent) — nu re-alerta zilnic
    newly = []
    if a.__dict__.get("email"):
        cx = _state(); today = date.today().isoformat()
        prev = dict((b, s) for b, s in cx.execute("SELECT basename, status FROM parity_state"))
        cur = {b: st for st, b, _ in rows}
        for st, b, _ in drift:
            if prev.get(b) not in ("DIFERĂ", "FLAT-LIPSĂ"):
                newly.append(b)
        cx.execute("DELETE FROM parity_state")
        cx.executemany("INSERT INTO parity_state VALUES (?,?,?)", [(b, cur[b], today) for b in cur])
        cx.commit(); cx.close()
        if newly:
            try:
                _send_email(a.email, "[parity] ARONA — 🔴 %d fișier(e) au divergat git↔VPS" % len(newly),
                            report + "\n\nNOI: " + ", ".join(newly), a.key, a.sender)
                print("\n[email] trimis (%d noi)" % len(newly))
            except Exception as e:
                print("\n[email] EȘUAT: %s: %s" % (type(e).__name__, e))
        else:
            print("\n[email] niciun drift NOU → fără email")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
