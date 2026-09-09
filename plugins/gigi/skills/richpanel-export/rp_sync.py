# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
rp_sync.py — CAPTAREA propriu-zisă a oglinzii CS (Richpanel → cs_mirror.db).

⛔ Faza curentă = DOAR CITIRE. Nicio scriere în Richpanel: tot traficul trece prin
   `cs_mirror.MirrorMCP`, care refuză orice unealtă de scriere (`assert_read_only`).

    uv run rp_sync.py --from 2026-08-25 --to 2026-09-01     # felii pe ZI (endDate exclusiv)
    uv run rp_sync.py --recent 3                            # ultimele N zile
    uv run rp_sync.py --recent 1 --max-rpm 30               # în orele de program (08-18)
    uv run rp_sync.py --stats                               # ce avem în oglindă

Fluxul, în două etaje:
  1. ENUMERARE — pentru fiecare ZI, `list_conversations` separat pe OPEN și pe CLOSED
     (status="all" NU merge), per_page=50, paginare până când `returned < per_page`.
     ⚠️ `has_more` MINTE (măsurat: a raportat false cu 50/50 rânduri returnate și pagini
     rămase) → singurul criteriu de oprire e `returned < per_page`.
     ⚠️ `endDate` e EXCLUSIV → o zi se cere ca [zi, zi+1).
     Feliile pe zi ocolesc și plafonul de offset (~pagina 50 / ~2.500 rânduri per filtru).
  2. FIR COMPLET — `get_conversation(mode="audit", include_private_notes=true)`, care e
     singurul mod ce întoarce răspunsurile agenților, notele private și atașamentele;
     paginat pe `messages_page.next_cursor` până la capăt (o conversație a avut 875 mesaje).

Scrierea în oglindă se face NUMAI prin `cs_mirror.upsert_*` → regula de îngheț (un text
captat nu se pierde niciodată) se aplică automat.
"""
import argparse
import datetime
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # stațiile CS = Windows cp1252

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cs_mirror as cm  # noqa: E402  (biblioteca comună: schemă + îngheț + pacer + gardă)

PER_PAGE = 50            # maximul acceptat de list_conversations
MAX_PAGES_DAY = 200      # plasă de siguranță: 10.000 tichete/zi/status (realul e ~200)
MAX_THREAD_PAGES = 60    # 60 × 50 = 3.000 mesaje; recordul măsurat e 875
MAX_MSG_CHARS = 6000     # maximul acceptat de get_conversation (mai mult = trunchiere)


# ─────────────────────────────────────────────────────────── adaptoare de payload
def _flat_channel(v):
    """`channel` vine uneori ca dict — la mesaje ÎNTOTDEAUNA: {"channel": "facebook"}.
    Dat așa mai departe, sqlite3 aruncă InterfaceError și pierdem tot mesajul."""
    if isinstance(v, dict):
        return v.get("channel") or v.get("name") or v.get("type") or ""
    return v or ""


def prep_ticket(t):
    """Sumarul de tichet, adaptat la ce așteaptă cs_mirror.upsert_ticket."""
    t = dict(t)
    t["channel"] = _flat_channel(t.get("channel"))
    # `tags` = UUID-uri, `tag_names` = numele reale („magazin-grandia"). Coloana oglinzii se
    # numește tag_names, deci punem NUMELE — UUID-urile n-ar fi utilizabile în niciun raport.
    if t.get("tag_names"):
        t["tags"] = t["tag_names"]
    return t


def prep_msg(m):
    return dict(m, channel=_flat_channel(m.get("channel")))


def day_range(d_from, d_to):
    """[d_from, d_to) — aceeași convenție ca `endDate` din API, ca să nu existe două reguli."""
    a = datetime.date.fromisoformat(d_from)
    b = datetime.date.fromisoformat(d_to)
    while a < b:
        yield a
        a += datetime.timedelta(days=1)


# ────────────────────────────────────────────────────────────── 1) ENUMERAREA zilei
def enumerate_day(mcp, day, statuses, log):
    """Toate tichetele CREATE în ziua `day` (filtrul API e pe created_at — verificat:
    au ieșit tichete cu updated_at în afara ferestrei, dar create în ea).

    Întoarce {id: ticket_sumar} — dedublat, fiindcă sortarea e pe updatedAt și un tichet
    atins în timpul paginării poate reapărea pe pagina următoare.
    """
    ds = day.isoformat()
    de = (day + datetime.timedelta(days=1)).isoformat()      # endDate EXCLUSIV
    out = {}
    for status in statuses:
        page = 1
        while page <= MAX_PAGES_DAY:
            d = mcp.call("list_conversations", {
                "status": status, "startDate": ds, "endDate": de,
                "per_page": PER_PAGE, "page": page})
            ts = (d.get("tickets") or []) if isinstance(d, dict) else []
            for t in ts:
                if t.get("id"):
                    out[str(t["id"])] = t
            # NU `has_more` (minte). Singurul semnal de încredere: pagina neplină.
            if len(ts) < PER_PAGE:
                break
            page += 1
        else:
            log(f"    ⚠️ {ds}/{status}: am atins plafonul de {MAX_PAGES_DAY} pagini — "
                f"ziua poate fi INCOMPLETĂ")
    return out


# ──────────────────────────────────────────────────── 2) ce mai trebuie tras cu adevărat
def plan_day(db, tickets):
    """Împarte tichetele zilei în (de_tras, sărite) — fără niciun apel de rețea.

    Sărim un tichet doar dacă avem deja în oglindă cel puțin `comment_count` mesaje ȘI
    `updated_at` e neschimbat față de ce am captat. Aceeași regulă acoperă două lucruri:

    1. RELUAREA după o cădere (cerința 4): ce e deja complet nu se re-trage. O rulare
       întreruptă la jumătate costă, la reluare, doar restul.
    2. OPTIMIZAREA OBLIGATORIE: doar 36,9% din tichete (97.955/265.195) au ≥2 mesaje.
       Un tichet cu comment_count<=1 al cărui unic mesaj îl avem deja = apel irosit; la
       scara arhivei ar fi 63% din apeluri aruncate pe fereastră.
       ⚠️ De ce NU se poate sări din PRIMA (adică sintetizând mesajul din sumar): am
       măsurat că `first_message` din list_conversations e TRUNCHIAT la ~300 caractere
       (se termină literal cu „...[truncated 1020 chars]") și că id-ul primului mesaj NU
       e deductibil din id-ul tichetului (la facebook_feed_comment sunt diferite). Un
       rând sintetizat ar intra cu text ciuntit și cu o cheie inventată, deci ar rămâne
       dublură pe veci lângă mesajul real. Îl tragem O SINGURĂ dată, apoi îl sărim
       la fiecare rulare ulterioară.

    ⚠️ Se apelează ÎNAINTE de upsert-ul sumarelor zilei — altfel `updated_at` din oglindă
    e deja cel proaspăt și comparația de mai jos ar ieși mereu „neschimbat".
    ⚠️ `comment_count` are DOUĂ convenții, măsurate pe aceleași tichete:
       • cel din `list_conversations` (pe care îl primim aici) numără DOAR mesajele
         publice;
       • cel din `get_conversation.ticket` INCLUDE notele private, iar cs_mirror păstrează
         maximul celor două → valoarea STOCATĂ e cea umflată.
    De aceea comparăm mărul cu mărul: numărul din listă vs rândurile PUBLICE (is_private=0).
    A compara numărul stocat cu rândurile publice ar declara „incomplete" toate tichetele
    care au măcar o notă internă (26% din ele) și le-ar re-trage la infinit.
    """
    ids = list(tickets)
    prev = {}
    for i in range(0, len(ids), 400):                    # SQLite: limită de variabile
        chunk = ids[i:i + 400]
        q = ("SELECT t.id, t.updated_at,"
             " (SELECT COUNT(*) FROM rp_message m WHERE m.ticket_id = t.id"
             "    AND COALESCE(m.is_private,0) = 0) AS have"
             " , t.msg_total_at_fetch"
             " FROM rp_ticket t WHERE t.id IN (%s)" % ",".join("?" * len(chunk)))
        for r in db.execute(q, chunk):
            prev[r["id"]] = (r["updated_at"] or "", r["have"], r["msg_total_at_fetch"])
    todo, skipped = [], []
    for tid, t in tickets.items():
        cc = int(t.get("comment_count") or 0)
        p = prev.get(tid)
        # ⚠️ „Complet" NU se poate decide doar din numarul de mesaje PUBLICE. Daca o tragere
        # anterioara a cazut la mijlocul paginarii, tichetul ramane cu un prefix comis
        # (ex. 8 publice din 300 de mesaje) iar `have >= cc` il declara complet ⇒ restul
        # nu se mai cere NICIODATA cat timp updated_at nu se schimba. 130 de tichete din
        # arhiva au >50 de mesaje, deci paginare obligatorie. Poarta suplimentara:
        # tichetul e „complet" doar daca tragerea lui s-a INCHEIAT curat (msg_total_at_fetch
        # setat si >= totalul asteptat).
        done_total = (p[2] if p and len(p) > 2 else None)
        clean = done_total is not None and int(done_total or 0) >= max(1, cc)
        if p and clean and p[1] >= max(1, cc) and p[0] == (t.get("updated_at") or ""):
            skipped.append(tid)
        else:
            todo.append(tid)
    return todo, skipped


# ───────────────────────────────────────────────────────────── 3) firul unei conversații
def fetch_thread(mcp, db, tid, stats, fetched_at):
    """get_conversation(mode=audit) + paginare pe message_cursor → oglindă.

    mode="audit" + include_private_notes=true e singura combinație care întoarce firul
    COMPLET: mesaje client + răspunsuri agent (author_is_workspace_agent) + note private
    + atașamente (URL-uri S3).
    """
    cursor, pages, n_msg = None, 0, 0
    ticket_obj = None
    while pages < MAX_THREAD_PAGES:
        args = {"conversation_id": tid, "mode": "audit", "max_messages": 50,
                "max_message_chars": MAX_MSG_CHARS, "include_private_notes": True}
        if cursor is not None:
            args["message_cursor"] = cursor
        d = mcp.call("get_conversation", args) or {}
        pages += 1
        ticket_obj = d.get("ticket") or ticket_obj
        msgs = d.get("messages") or []
        for m in msgs:
            m = prep_msg(m)
            idx = m.get("index")
            st = cm.upsert_message(db, tid, m, idx=idx, max_chars=MAX_MSG_CHARS,
                                   fetched_at=fetched_at)
            stats[st] = stats.get(st, 0) + 1
            n_msg += 1
            row = cm.normalize_message(m, tid, idx, MAX_MSG_CHARS, fetched_at)
            if row["is_agent"]:
                stats["agent_msgs"] += 1
            if row["is_operator_bot"]:
                stats["bot_msgs"] += 1
            if row["is_private"]:
                stats["private_notes"] += 1
            stats["attachments"] += cm.upsert_attachments(db, tid, row["msg_id"], m,
                                                          fetched_at=fetched_at)
        page = d.get("messages_page") or {}
        nxt = page.get("next_cursor")
        if not nxt or nxt == cursor:      # cursor care nu avansează = buclă infinită
            break
        cursor = nxt
    else:
        stats["truncated_threads"] += 1
    if ticket_obj:
        # Obiectul din get_conversation e mai bogat decât sumarul (closed_at,
        # first_responded_at, telefonul clientului) → îl scriem peste, cu totalul de mesaje.
        cm.upsert_ticket(db, prep_ticket(ticket_obj), msg_total=n_msg, fetched_at=fetched_at)
    stats["thread_pages"] += pages
    return n_msg


# ──────────────────────────────────────────────────────────────────────── orchestrare
def _log(msg):
    """Jurnal cu FLUSH. Fără el, `uv run rp_sync.py > pull.log` nu arată nimic ore în șir
    (stdout redirectat = buffer de 8 KB) și un job lung pare mort — exact felul de eșec
    tăcut pe care oglinda trebuie să-l evite."""
    print(msg, flush=True)


def sync(db, mcp, d_from, d_to, statuses, limit=None, log=_log):
    stats = {"days": 0, "tickets_seen": 0, "threads_fetched": 0, "threads_skipped": 0,
             "messages": 0, "inserted": 0, "updated": 0, "unchanged": 0, "frozen": 0,
             "agent_msgs": 0, "bot_msgs": 0, "private_notes": 0, "attachments": 0,
             "thread_pages": 0, "truncated_threads": 0, "errors": 0}
    t0 = time.time()
    note = f"{d_from}..{d_to} statuses={'+'.join(statuses)} rpm={mcp.limiter.target_rpm()}"
    with cm.sync_run(db, "rp_sync", note=note) as run:
        for day in day_range(d_from, d_to):
            ds = day.isoformat()
            try:
                tickets = enumerate_day(mcp, day, statuses, log)
            except Exception as e:
                # O zi picată NU oprește restul: e re-tratabilă la următoarea rulare.
                stats["errors"] += 1
                run.errors += 1
                log(f"  {ds}: EROARE la enumerare ({type(e).__name__}: {str(e)[:90]})")
                continue
            # Planificăm ÎNAINTE de a scrie sumarele: plan_day compară `updated_at` din
            # oglindă cu cel proaspăt, iar upsert-ul l-ar face identic (vezi plan_day).
            todo, skipped = plan_day(db, tickets)
            fetched_at = cm.now_iso()
            for t in tickets.values():
                cm.upsert_ticket(db, prep_ticket(t), fetched_at=fetched_at)
            db.commit()
            stats["days"] += 1
            stats["tickets_seen"] += len(tickets)
            stats["threads_skipped"] += len(skipped)
            if limit is not None:
                todo = todo[:max(0, limit - stats["threads_fetched"])]
            log(f"  {ds}: {len(tickets)} tichete · {len(todo)} fire de tras · "
                f"{len(skipped)} deja complete")
            for i, tid in enumerate(todo, 1):
                try:
                    n = fetch_thread(mcp, db, tid, stats, cm.now_iso())
                    stats["messages"] += n
                    stats["threads_fetched"] += 1
                    run.items += 1
                except Exception as e:
                    stats["errors"] += 1
                    run.errors += 1
                    log(f"    ! {tid[:28]}…: {type(e).__name__}: {str(e)[:80]}")
                db.commit()      # commit per tichet: un Ctrl-C pierde cel mult unul
                if i % 25 == 0:
                    rl = mcp.limiter.stats()
                    log(f"    …{i}/{len(todo)} fire · {stats['messages']} mesaje · "
                        f"{rl['calls']} apeluri · rămase={rl['remaining']} · "
                        f"429={rl['http_429']}")
            if limit is not None and stats["threads_fetched"] >= limit:
                log(f"  (--limit {limit} atins, mă opresc)")
                break
        stats["elapsed_s"] = round(time.time() - t0, 1)
        rl = mcp.limiter.stats()
        stats.update(api_calls=rl["calls"], http_429=rl["http_429"],
                     slowdowns=rl["slowdowns"], slept_s=rl["slept_s"],
                     rpm_target=rl["rpm_target"])
        run.note = note + " | " + json.dumps(
            {k: stats[k] for k in ("tickets_seen", "threads_fetched", "threads_skipped",
                                   "messages", "api_calls", "http_429", "elapsed_s")})
    return stats


def report(stats):
    el = stats.get("elapsed_s") or 0.0
    print("\n" + "=" * 72)
    print(f"zile procesate      {stats['days']}")
    print(f"tichete enumerate   {stats['tickets_seen']}")
    print(f"fire trase          {stats['threads_fetched']}"
          f"   (sărite ca deja complete: {stats['threads_skipped']})")
    print(f"mesaje procesate    {stats['messages']}"
          f"   (noi {stats['inserted']} · îmbogățite {stats['updated']} ·"
          f" neschimbate {stats['unchanged']} · ÎNGHEȚATE {stats['frozen']})")
    print(f"  de la agenți REALI  {stats['agent_msgs']}")
    print(f"  boți canned         {stats['bot_msgs']}")
    print(f"  note private        {stats['private_notes']}")
    print(f"  atașamente          {stats['attachments']}")
    print(f"apeluri API         {stats['api_calls']}  (ținta {stats['rpm_target']}/min, "
          f"pauze {stats['slept_s']}s)")
    print(f"HTTP 429            {stats['http_429']}   încetiniri din headere: "
          f"{stats['slowdowns']}")
    print(f"erori               {stats['errors']}")
    print(f"durata              {el}s"
          + (f"   ({stats['api_calls'] / (el / 60.0):.1f} apeluri/min efectiv)"
             if el > 0 else ""))
    if stats["truncated_threads"]:
        print(f"⚠️ {stats['truncated_threads']} fire au atins plafonul de "
              f"{MAX_THREAD_PAGES} pagini")


def stats_cmd(db):
    n_t, n_m = (db.execute("SELECT COUNT(*) FROM rp_ticket").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM rp_message").fetchone()[0])
    print(f"oglinda: {n_t} tichete · {n_m} mesaje · "
          f"{db.execute('SELECT COUNT(*) FROM rp_attachment').fetchone()[0]} atașamente")
    lo, hi = db.execute("SELECT MIN(substr(created_at,1,10)), MAX(substr(created_at,1,10))"
                        " FROM rp_ticket").fetchone()
    print(f"  interval tichete: {lo} → {hi}")
    for lbl, cond in (("agenți reali", "is_agent=1"), ("boți canned", "is_operator_bot=1"),
                      ("note private", "is_private=1"), ("mesaje goale", "text_len=0")):
        n = db.execute("SELECT COUNT(*) FROM rp_message WHERE " + cond).fetchone()[0]
        print(f"    {lbl:<14} {n:>8}"
              + (f"  ({100.0 * n / n_m:.1f}%)" if n_m else ""))
    # `comment_count` stocat e maximul dintre convenția „doar publice" (list_conversations)
    # și „inclusiv note private" (get_conversation) → se compară cu TOTALUL captat, nu cu
    # partea publică; altfel orice tichet cu o notă internă ar apărea fals ca incomplet.
    inc = db.execute(
        "SELECT COUNT(*) FROM rp_ticket t WHERE COALESCE(t.comment_count,0) >"
        " (SELECT COUNT(*) FROM rp_message m WHERE m.ticket_id=t.id)").fetchone()[0]
    never = db.execute("SELECT COUNT(*) FROM rp_ticket t WHERE NOT EXISTS"
                       "(SELECT 1 FROM rp_message m WHERE m.ticket_id=t.id)").fetchone()[0]
    print(f"  tichete cu firul INCOMPLET (de re-tras): {inc}")
    print(f"  tichete fără NICIUN mesaj captat:        {never}")
    print("  zile (ultimele 10):")
    for r in db.execute("SELECT substr(created_at,1,10) d, COUNT(*) n FROM rp_ticket"
                        " WHERE created_at IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 10"):
        print(f"    {r['d']}  {r['n']:>5}")
    print("  ultimele rulări:")
    for r in db.execute("SELECT job,started_at,ended_at,ok,items,errors,note FROM sync_run"
                        " ORDER BY started_at DESC LIMIT 5"):
        print(f"    {r['started_at']}  {r['job']:<8} ok={r['ok']} fire={r['items']} "
              f"err={r['errors']}  {(r['note'] or '')[:90]}")


def main():
    ap = argparse.ArgumentParser(
        description="Captarea oglinzii CS Richpanel (READ-ONLY).")
    ap.add_argument("--from", dest="d_from", help="prima zi, inclusiv (YYYY-MM-DD)")
    ap.add_argument("--to", dest="d_to", help="ultima zi, EXCLUSIV (ca endDate din API)")
    ap.add_argument("--recent", type=int, help="ultimele N zile (inclusiv azi)")
    ap.add_argument("--max-rpm", type=int, default=None,
                    help="plafon de cereri/minut (implicit: 30 în 08-18, 50 noaptea)")
    ap.add_argument("--reserve", type=int, default=12,
                    help="cereri lăsate CS-ului live din x-ratelimit-remaining (implicit 12)")
    ap.add_argument("--limit", type=int, default=None,
                    help="oprește-te după N fire trase (probe/ferestre mici)")
    ap.add_argument("--statuses", default="OPEN,CLOSED",
                    help='status=all NU merge — se cer separat (implicit "OPEN,CLOSED")')
    ap.add_argument("--stats", action="store_true", help="doar raportează oglinda")
    ap.add_argument("--db", default=cm.DB_DEFAULT)
    a = ap.parse_args()

    db = cm.open_db(a.db)
    if a.stats:
        stats_cmd(db)
        db.close()
        return
    if a.recent:
        today = datetime.date.today()
        a.d_from = (today - datetime.timedelta(days=a.recent - 1)).isoformat()
        a.d_to = (today + datetime.timedelta(days=1)).isoformat()
    if not (a.d_from and a.d_to):
        ap.error("dă --from/--to sau --recent N (sau --stats)")

    limiter = cm.RateLimiter(rpm=a.max_rpm, reserve=a.reserve)
    mcp = cm.MirrorMCP(limiter=limiter)          # gardă read-only + pacer pe deadline
    statuses = [s.strip().upper() for s in a.statuses.split(",") if s.strip()]
    _log(f"captare {a.d_from} → {a.d_to} (exclusiv) · statusuri {'+'.join(statuses)} · "
         f"ținta {limiter.target_rpm()} cereri/min · baza {a.db}")
    try:
        st = sync(db, mcp, a.d_from, a.d_to, statuses, limit=a.limit)
        report(st)
    finally:
        db.close()


if __name__ == "__main__":
    main()
