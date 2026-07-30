# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000"]
# ///
"""giveup_cleanup.py — curăță `.cron_giveup` de order-urile care DEJA au AWB sau sunt ANULATE (sursă de adevăr:
AWBprint). De rulat SĂPTĂMÂNAL (cron). Fără el, `.cron_giveup` se acumulează (ajunsese la 400 order-uri, ~90%
obsolet: shipped/anulate) → held-sweep-ul sare degeaba peste ele + reguli noi nu re-încearcă nimic.

Rulează DIN folderul xconnector (unde e `.cron_giveup`). Dry-run by default; `--apply` scrie (cu backup).
Verificarea „are AWB / e anulat" se face rapid din AWBprint (`orders.tracking_number` / `aggregated_status`),
NU cu 400 de lookup-uri xConnector. Credențiale: `DATABASE_URL_AWBPRINT` din env (setat de `.env.xconnector`
pe VPS) sau din KB. Read-only pe AWBprint; scrie DOAR fișierul local `.cron_giveup`."""
import os, sys, subprocess, datetime, ssl
from urllib.parse import urlparse, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
GIVEUP = os.path.join(HERE, ".cron_giveup")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")


def _url():
    u = os.environ.get("DATABASE_URL_AWBPRINT") or ""
    if not u.startswith("postgres"):
        try:
            u = subprocess.run(["uv", "run", KB, "secret-get", "DATABASE_URL_AWBPRINT"],
                               capture_output=True, text=True, timeout=40).stdout.strip()
        except Exception:
            u = ""
    return u


def _connect():
    import pg8000
    u = urlparse(_url())
    if not (u.scheme or "").startswith("postgres"):
        raise RuntimeError("DATABASE_URL_AWBPRINT lipsă/invalid")
    kw = dict(user=unquote(u.username or ""), password=unquote(u.password or ""),
              host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    last = None
    for c in (ctx, None):
        try:
            return pg8000.connect(ssl_context=c, **kw)
        except Exception as e:
            last = e
    raise RuntimeError("conectare AWBprint eșuată: %r" % last)


def _obsolete(names):
    """order-urile din `names` care au AWB (tracking_number) SAU sunt anulate — de scos din giveup."""
    conn = _connect()
    try:
        cur = conn.cursor()
        ph = ",".join(["%s"] * len(names))
        cur.execute(
            "SELECT order_number FROM orders WHERE order_number IN (" + ph + ") "
            "AND ((tracking_number IS NOT NULL AND tracking_number <> '') OR aggregated_status = 'cancelled')",
            names)
        return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(GIVEUP):
        print("(fără .cron_giveup — nimic de făcut)"); return
    names = [l.strip() for l in open(GIVEUP, encoding="utf-8") if l.strip()]
    if not names:
        print("giveup gol"); return
    try:
        obs = _obsolete(names)
    except Exception as e:
        # fail-safe: dacă DB pică, NU ating fișierul (mai bine giveup umflat decât șters greșit)
        print("EROARE (nu ating fișierul): %r" % e); sys.exit(1)
    keep = [n for n in names if n not in obs]
    removed = len(names) - len(keep)
    print("giveup: %d → %d  (scos %d cu AWB/anulate)" % (len(names), len(keep), removed))
    if not apply:
        print("[DRY-RUN] adaugă --apply ca să scrii"); return
    if not removed:
        print("nimic de scos"); return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        with open(GIVEUP, encoding="utf-8") as f:
            bak = f.read()
        with open(GIVEUP + ".bak_cleanup_" + ts, "w", encoding="utf-8") as f:
            f.write(bak)
    except Exception:
        pass
    with open(GIVEUP, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + ("\n" if keep else ""))
    print("✅ scris (%d rămân) · backup .bak_cleanup_%s" % (len(keep), ts))


if __name__ == "__main__":
    main()
