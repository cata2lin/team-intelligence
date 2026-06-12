# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
richpanel_pipeline.py — LANȚUL complet de date Richpanel, într-o comandă. Asta e BAZA pe care
citesc toate operațiunile (triage, draft, profile, comment-intelligence, quality-audit, sentiment…).

Rulează în ordine, fiecare pas robust + idempotent:
  1) PULL       — export tichete noi (incremental sau interval)
  2) CATEGORIZE — re-aplică regulile de categorisire pe tot
  3) LINK       — leagă tichet → client + magazin (resolved_store/match_order/contact)
  4) DEEP       — (opțional) extrage contact/comandă din corpul conversațiilor sociale
  5) SENTIMENT  — scrie sentiment + intensitate pe fiecare tichet CS

  uv run richpanel_pipeline.py --recent 2          # zilnic: trage ultimele 2 zile + re-enrich tot
  uv run richpanel_pipeline.py --from 2024-10-15 --to 2026-06-12   # backfill complet
  uv run richpanel_pipeline.py --no-pull           # doar re-enrich pe ce e deja în DB
  uv run richpanel_pipeline.py --recent 2 --deep   # + extragere profundă (lent, MCP)
Rulează cu caffeinate pt joburi lungi. Resumabil (pull-ul sare zilele done).
"""
import os, sys, subprocess, argparse, time, sqlite3, importlib.util, collections, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
SENT = os.path.join(HERE, "..", "cs-sentiment", "cs_sentiment.py")
COMMENT = os.path.join(HERE, "..", "cs-comment-intelligence", "cs_comment_intelligence.py")
QUALITY = os.path.join(HERE, "..", "cs-quality-audit", "cs_quality_audit.py")
NOISE = {"comentariu_social", "spam_automat", "recenzie_feedback", "salut_fara_continut", "formular_contact"}


def _load_mod(path):
    os.environ["RICHPANEL_DB"] = DB
    spec = importlib.util.spec_from_file_location("m_" + os.path.basename(path).replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _writer():
    w = sqlite3.connect(DB, timeout=180)
    w.execute("PRAGMA busy_timeout=180000")
    return w


# cum rulează scripturile-copil: pe PC „uv run" (gestionează dep-urile inline);
# pe VPS setezi CS_PIPELINE_RUNNER="/root/Scripturi/.venv/bin/python3" (pg8000 e deja în venv).
RUNNER = os.environ.get("CS_PIPELINE_RUNNER", "uv run").split()


def run(label, args):
    print("\n━━━━━━ %s ━━━━━━" % label, flush=True)
    t = time.time()
    rc = subprocess.call(RUNNER + args)
    ok = "✅ OK" if rc == 0 else "⚠️ rc=%d" % rc
    print("  [%s] %s în %.0fs" % (label, ok, time.time() - t), flush=True)
    return rc


def store_sentiment():
    print("\n━━━━━━ 5) SENTIMENT ━━━━━━", flush=True)
    t = time.time()
    os.environ["RICHPANEL_DB"] = DB
    spec = importlib.util.spec_from_file_location("cs_sent", SENT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ro = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=60)
    rows = ro.execute("SELECT id, resolved_store, COALESCE(first_message,'')||' '||COALESCE(subject,''), category, channel FROM tickets").fetchall()
    ro.close()
    w = sqlite3.connect(DB, timeout=180)
    w.execute("PRAGMA busy_timeout=180000")
    for col in ("sentiment", "sent_intensity"):
        try:
            w.execute("ALTER TABLE tickets ADD COLUMN %s TEXT" % col)
        except Exception:
            pass
    n = 0
    dist = collections.Counter()
    for tid, store, text, cat, channel in rows:
        if cat in NOISE or "comment" in (channel or ""):
            continue
        s, inten = m.sentiment(text, store or "")
        w.execute("UPDATE tickets SET sentiment=?, sent_intensity=? WHERE id=?", (s, str(inten), tid))
        dist[s] += 1; n += 1
        if n % 5000 == 0:
            w.commit()
    w.commit(); w.close()
    print("  [SENTIMENT] ✅ %d tichete CS | %s în %.0fs" % (n, dict(dist), time.time() - t), flush=True)


def store_comment_type():
    print("\n━━━━━━ 6) COMMENT-INTELLIGENCE (lead/reclamație/testimonial) ━━━━━━", flush=True)
    t = time.time()
    m = _load_mod(COMMENT)
    items = m.load()  # doar comentarii, cu clasificarea în 'type'
    w = _writer()
    try:
        w.execute("ALTER TABLE tickets ADD COLUMN comment_type TEXT")
    except Exception:
        pass
    dist = collections.Counter(); n = 0
    for it in items:
        w.execute("UPDATE tickets SET comment_type=? WHERE id=?", (it["type"], it["id"]))
        dist[it["type"]] += 1; n += 1
        if n % 5000 == 0:
            w.commit()
    w.commit(); w.close()
    print("  [COMMENT] ✅ %d comentarii | %s în %.0fs" % (n, dict(dist), time.time() - t), flush=True)


def store_quality_flags():
    print("\n━━━━━━ 7) QUALITY-FLAGS (frustrare/escaladare/lent/fricțiune) ━━━━━━", flush=True)
    t = time.time()
    m = _load_mod(QUALITY)
    items = m.load()  # doar CS real, cu câmpurile pt flags
    w = _writer()
    try:
        w.execute("ALTER TABLE tickets ADD COLUMN quality_flags TEXT")
    except Exception:
        pass
    n = nflag = 0
    for it in items:
        fl = ",".join(m.flags(it))
        w.execute("UPDATE tickets SET quality_flags=? WHERE id=?", (fl or None, it.get("id") or it.get("no")))
        n += 1
        if fl:
            nflag += 1
        if n % 5000 == 0:
            w.commit()
    w.commit(); w.close()
    print("  [QUALITY] ✅ %d CS verificate, %d cu flag-uri în %.0fs" % (n, nflag, time.time() - t), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm"); ap.add_argument("--to", dest="to")
    ap.add_argument("--recent", type=int, help="trage ultimele N zile")
    ap.add_argument("--no-pull", action="store_true", help="sari peste export (doar re-enrich)")
    ap.add_argument("--deep", action="store_true", help="rulează și extragerea profundă (lent)")
    ap.add_argument("--deep-all", action="store_true", help="deep pe TOATE tichetele, nu doar sociale")
    a = ap.parse_args()
    t0 = time.time()
    print("═" * 64)
    print("  RICHPANEL PIPELINE — baza de date pt toate operațiunile")
    print("═" * 64)

    if not a.no_pull:
        if a.recent:
            to = datetime.date.today().isoformat()
            frm = (datetime.date.today() - datetime.timedelta(days=a.recent)).isoformat()
        else:
            frm, to = a.frm, a.to
        if frm and to:
            run("1) PULL %s→%s" % (frm, to), [os.path.join(HERE, "richpanel_export.py"), "pull", "--from", frm, "--to", to])
        else:
            print("  (fără --recent / --from --to → sar peste PULL)")

    run("2) CATEGORIZE", [os.path.join(HERE, "richpanel_export.py"), "categorize"])
    run("3) LINK", [os.path.join(HERE, "richpanel_link.py")])
    if a.deep or a.deep_all:
        deep_args = [os.path.join(HERE, "richpanel_deep.py")]
        if a.deep_all:
            deep_args.append("--all")
        run("4) DEEP", deep_args)
    else:
        print("\n  (4) DEEP sărit — adaugă --deep / --deep-all dacă vrei)")
    store_sentiment()
    store_comment_type()
    store_quality_flags()
    run("8) SYNC → metrics.richpanel_tickets (Postgres partajat)", [os.path.join(HERE, "richpanel_sync.py")])

    print("\n" + "═" * 64)
    print("  ✅ PIPELINE complet în %.0f min. Baza e enrichată — toate skill-urile citesc de aici." % ((time.time() - t0) / 60))
    print("═" * 64)


if __name__ == "__main__":
    main()
