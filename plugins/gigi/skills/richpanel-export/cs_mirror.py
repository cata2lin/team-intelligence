# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_mirror.py — biblioteca COMUNĂ a oglinzii READ-ONLY a helpdesk-ului Richpanel.

Faza curentă = DOAR CAPTARE. ⛔ Nicio scriere în Richpanel (fără reply, draft, notă, tag,
close). `assert_read_only()` + `MirrorMCP.call()` refuză explicit uneltele de scriere.

Ce oferă (folosit de pull / parity / rapoarte):
  • open_db()            — SQLite WAL + schemă idempotentă (rp_ticket / rp_message /
                           rp_attachment / parity_daily / sync_run)
  • upsert_message()     — ⚠️ REGULA DE ÎNGHEȚ: un text captat NU se pierde niciodată.
  • classify_author()    — agent REAL vs bot canned al widgetului ("operator")
  • RateLimiter          — pacer pe DEADLINE (nu sleep fix) + pilotaj pe x-ratelimit-remaining
  • sanitize()           — repară/scoate surrogate-urile rupte (emoji Facebook) care crapă SQLite
  • MirrorMCP            — rp.MCP + pacer + capturarea headerelor + gardă read-only

  uv run cs_mirror.py selftest      # dovada: schemă + regula de îngheț + pacer + sanitize
  uv run cs_mirror.py schema        # creează/actualizează schema în baza reală
  uv run cs_mirror.py stats         # ce are oglinda azi
"""
import argparse, ast, collections, datetime, hashlib, html, json, os, re, sqlite3, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # stațiile CS/depozit = Windows cp1252

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DEFAULT = os.environ.get("CS_MIRROR_DB") or \
    "/Users/gheorghebeschea/Downloads/Scripturi/data/cs_mirror.db"

# ─────────────────────────────────────────────────────────────────────── schemă
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS rp_ticket(
        id TEXT PRIMARY KEY,
        conversation_no INTEGER, channel TEXT, status TEXT, priority TEXT, assignee_id TEXT,
        to_id TEXT, to_email TEXT, from_id TEXT, from_email TEXT,
        customer_id TEXT, customer_name TEXT, customer_email TEXT, customer_phone TEXT,
        tag_names TEXT, subject TEXT, first_message TEXT, comment_count INTEGER,
        created_at TEXT, updated_at TEXT, closed_at TEXT, first_responded_at TEXT,
        store_resolved TEXT, fetched_at TEXT, msg_total_at_fetch INTEGER)""",
    """CREATE TABLE IF NOT EXISTS rp_message(
        ticket_id TEXT NOT NULL, msg_id TEXT NOT NULL, idx INTEGER, created_at TEXT,
        type TEXT, is_private INTEGER, is_ai INTEGER,
        author_id TEXT, author_name TEXT, is_agent INTEGER, is_operator_bot INTEGER,
        channel TEXT, text TEXT, text_len INTEGER, truncated INTEGER, fetched_at TEXT,
        PRIMARY KEY (ticket_id, msg_id))""",
    """CREATE TABLE IF NOT EXISTS rp_attachment(
        ticket_id TEXT NOT NULL, msg_id TEXT NOT NULL, url TEXT NOT NULL, fetched_at TEXT,
        PRIMARY KEY (ticket_id, msg_id, url))""",
    """CREATE TABLE IF NOT EXISTS parity_daily(
        day TEXT NOT NULL, channel TEXT NOT NULL, rp_count INTEGER, mirror_count INTEGER,
        matched INTEGER, missing_sample TEXT, coverage_pct REAL, run_at TEXT,
        PRIMARY KEY (day, channel))""",
    """CREATE TABLE IF NOT EXISTS sync_run(
        job TEXT, started_at TEXT, ended_at TEXT, ok INTEGER, items INTEGER,
        errors INTEGER, note TEXT)""",
    # ── cutiile Gmail (gmail_sync.py) — CAPTARE READ-ONLY, scope gmail.readonly ──
    # message_id = Message-ID RFC822 CU parantezele unghiulare, exact ca `rp_ticket.id`
    # (masurat: id-ul de tichet RP E Message-ID-ul pe 99,74% din emailuri) → jonctiunea
    # oglinda-Gmail ↔ oglinda-Richpanel e directa, fara normalizari inventate.
    """CREATE TABLE IF NOT EXISTS gm_message(
        message_id TEXT PRIMARY KEY,
        mailbox TEXT, gmail_id TEXT, thread_id TEXT, direction TEXT,
        from_addr TEXT, to_addr TEXT, subject TEXT, date_utc TEXT,
        in_reply_to TEXT, refs TEXT,
        is_automated INTEGER, auto_reason TEXT,
        body_text TEXT, body_len INTEGER, has_attachments INTEGER,
        labels TEXT, fetched_at TEXT)""",
    # DOAR metadate — atasamentele NU se descarca (costul de stocare se decide dupa
    # ce masuram cat ocupa; `attachment_id` permite descarcarea ulterioara, daca vrem).
    """CREATE TABLE IF NOT EXISTS gm_attachment(
        message_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT,
        size_bytes INTEGER, attachment_id TEXT, fetched_at TEXT,
        PRIMARY KEY (message_id, filename))""",
    # ce e in cutie dar NU in Richpanel = pierderea helpdesk-ului, cuantificata
    """CREATE TABLE IF NOT EXISTS gm_gap(
        message_id TEXT PRIMARY KEY, day TEXT, mailbox TEXT, subject TEXT,
        from_addr TEXT, reason TEXT, found_at TEXT)""",
    # starea incrementalului: historyId per cutie (users.history.list)
    """CREATE TABLE IF NOT EXISTS gm_state(
        mailbox TEXT PRIMARY KEY, history_id TEXT, last_sync_at TEXT, last_ok_at TEXT,
        messages_total INTEGER, aliases TEXT, note TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_ticket_updated ON rp_ticket(updated_at)",
    "CREATE INDEX IF NOT EXISTS ix_ticket_channel ON rp_ticket(channel)",
    "CREATE INDEX IF NOT EXISTS ix_msg_ticket     ON rp_message(ticket_id)",
    "CREATE INDEX IF NOT EXISTS ix_msg_agent      ON rp_message(is_agent)",
    "CREATE INDEX IF NOT EXISTS ix_msg_created    ON rp_message(created_at)",
    "CREATE INDEX IF NOT EXISTS ix_run_job        ON sync_run(job, started_at)",
    "CREATE INDEX IF NOT EXISTS ix_gm_mailbox     ON gm_message(mailbox, date_utc)",
    "CREATE INDEX IF NOT EXISTS ix_gm_date        ON gm_message(date_utc)",
    "CREATE INDEX IF NOT EXISTS ix_gm_thread      ON gm_message(thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_gm_auto        ON gm_message(is_automated)",
    "CREATE INDEX IF NOT EXISTS ix_gm_dir         ON gm_message(direction)",
    "CREATE INDEX IF NOT EXISTS ix_gap_day        ON gm_gap(day, mailbox)",
]


def open_db(path=DB_DEFAULT):
    """Deschide (și creează) oglinda în WAL. Idempotent — se poate rula oricând."""
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    db = sqlite3.connect(path, timeout=120)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=120000")   # pull + parity pot rula în paralel
    db.execute("PRAGMA journal_mode=WAL")      # cititorii (rapoartele CS) nu blochează scriitorul
    db.execute("PRAGMA synchronous=NORMAL")    # sigur în WAL, de câteva ori mai rapid la insert
    for stmt in SCHEMA:
        db.execute(stmt)
    db.commit()
    return db


# ────────────────────────────────────────────────────────────────────── sanitize
_SURR_PAIR = re.compile("[\ud800-\udbff][\udc00-\udfff]")
_SURR_LONE = re.compile("[\ud800-\udfff]")


def sanitize(s):
    """Emoji din Facebook vin cu surrogate-uri (UTF-16) rupte: sqlite3 aruncă
    UnicodeEncodeError la scriere și pierdem TOT mesajul. Perechile valide le recompunem
    în emoji-ul real; ce rămâne orfan îl înlocuim. Scoatem și NUL (rupe TEXT-ul)."""
    if s is None or not isinstance(s, str):
        return s
    if "\x00" in s:
        s = s.replace("\x00", "")
    if not _SURR_LONE.search(s):
        return s
    s = _SURR_PAIR.sub(lambda m: m.group().encode("utf-16", "surrogatepass").decode("utf-16"), s)
    return _SURR_LONE.sub("�", s)


def iso_ts(v):
    """Richpanel dă când ISO, când epoch (s sau ms). Normalizăm la ISO UTC, ca sortarea
    pe `created_at` (text) să fie corectă."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        t = float(v)
        if t > 1e11:            # milisecunde
            t /= 1000.0
        return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()
    return str(v)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ───────────────────────────────────────────────── autor: agent REAL vs bot canned
_TRUE = {"true", "1", "yes", "y", "t"}


def _flag(v):
    if isinstance(v, str):
        return v.strip().lower() in _TRUE
    return bool(v)


def classify_author(msg):
    """(is_agent, is_operator_bot).

    ⚠️ 45% din mesajele cu author_is_workspace_agent=true sunt AUTOMATISME canned ale
    widgetului („Sunteți deja client?"), toate cu author.id == "operator". Agentul REAL are
    author_is_workspace_agent=true ȘI author.id != "operator". Boturile NU se aruncă — se
    marchează, altfel statistica „ce răspunde CS" e poluată masiv."""
    a = msg.get("author") if isinstance(msg.get("author"), dict) else {}
    ws = msg.get("author_is_workspace_agent")
    if ws is None:
        ws = a.get("is_workspace_agent", msg.get("is_workspace_agent"))
    ws = _flag(ws)
    aid = str(a.get("id") or msg.get("author_id") or "").strip().lower()
    aname = str(a.get("name") or msg.get("author_name") or "").strip().lower()
    is_bot = ws and (aid == "operator" or (not aid and aname == "operator"))
    return (bool(ws and not is_bot), bool(is_bot))


# ────────────────────────────────────────────────────── REGULA DE ÎNGHEȚ (miezul)
# Comentariile/mesajele se ȘTERG la sursă (mediana 1,4h pe Esteban). Textul congelat de
# oglindă e singurul lucru pe care nici Richpanel, nici Graph nu-l mai pot da înapoi.
TOMBSTONES = re.compile(
    r"^\s*(\[?\s*)?("
    r"this message (was|has been) (deleted|removed|unsent)"
    r"|message (was |has been )?(deleted|removed|unsent)"
    r"|this (content|comment) (is )?(no longer available|was deleted|unavailable)"
    r"|content (is )?(unavailable|not available)"
    r"|deleted|removed|unsent"
    r"|acest mesaj a fost (sters|șters|eliminat)"
    r"|mesaj (sters|șters)"
    r"|comentariu (sters|șters)"
    # ⚠️ Lista NU poate fi exhaustiva — sursa isi schimba formularea fara sa anunte. Adaugam
    # tipare generice; orice placeholder neprins care e mai LUNG decat originalul ar sterge
    # text real (dovedit: "Message unavailable" a inlocuit 'Files Attached').
    r"|(message|content|comment|media|photo|video|attachment)s? (is |are )?(unavailable|not available|no longer available)"
    r"|(unavailable|not available|no longer available)"
    r"|acest (mesaj|comentariu|continut|conținut) (nu mai )?(este |e )?(disponibil|indisponibil)"
    r"|(continut|conținut) (indisponibil|nedisponibil)"
    r")\s*(\]?)\s*[.!]*\s*$", re.I)


def is_tombstone(text):
    """True dacă textul e un „mesaj șters" — placeholder, nu conținut."""
    return bool(text) and bool(TOMBSTONES.match(text.strip()))


# Expresia care decide dacă PĂSTRĂM textul deja captat. Rulează în SQL, în ACELAȘI statement
# cu insertul → e atomică; nu depinde de citirea de dinainte (care ar putea fi stale).
_KEEP = """(
    LENGTH(COALESCE(rp_message.text,'')) > 0
    AND NOT (:old_tomb = 1 AND :tomb = 0 AND LENGTH(COALESCE(:text,'')) > 0)
    AND (   LENGTH(COALESCE(:text,'')) = 0
         OR :tomb = 1
         OR LENGTH(:text) <= LENGTH(rp_message.text) )
)"""

_MSG_COLS = ("ticket_id", "msg_id", "idx", "created_at", "type", "is_private", "is_ai",
             "author_id", "author_name", "is_agent", "is_operator_bot", "channel",
             "text", "text_len", "truncated", "fetched_at")

_MSG_SQL = f"""
INSERT INTO rp_message ({','.join(_MSG_COLS)})
VALUES ({','.join(':' + c for c in _MSG_COLS)})
ON CONFLICT(ticket_id, msg_id) DO UPDATE SET
    text            = CASE WHEN {_KEEP} THEN rp_message.text            ELSE :text            END,
    text_len        = CASE WHEN {_KEEP} THEN rp_message.text_len        ELSE :text_len        END,
    truncated       = CASE WHEN {_KEEP} THEN rp_message.truncated       ELSE :truncated       END,
    is_agent        = CASE WHEN {_KEEP} THEN rp_message.is_agent        ELSE :is_agent        END,
    is_operator_bot = CASE WHEN {_KEEP} THEN rp_message.is_operator_bot ELSE :is_operator_bot END,
    author_id       = COALESCE(NULLIF(:author_id,''),   rp_message.author_id),
    author_name     = COALESCE(NULLIF(:author_name,''), rp_message.author_name),
    idx             = COALESCE(:idx,        rp_message.idx),
    created_at      = COALESCE(rp_message.created_at, :created_at),
    type            = COALESCE(NULLIF(:type,''),    rp_message.type),
    is_private      = COALESCE(:is_private, rp_message.is_private),
    is_ai           = COALESCE(:is_ai,      rp_message.is_ai),
    channel         = COALESCE(NULLIF(:channel,''), rp_message.channel),
    fetched_at      = :fetched_at
"""

_TEXT_KEYS = ("text", "body", "message", "content", "plain_text", "body_text", "comment",
              "body_html", "html")
_TAG_RE = re.compile(r"<[^>]+>")
# HTML REAL, nu „pret <100 lei>" — altfel am tăia conținut dintr-un mesaj de client.
_HTML_RE = re.compile(r"</\w+>|<br\s*/?>|<(p|div|span|a|img|table|tr|td|ul|ol|li|strong|em|b|i|h\d)\b",
                      re.I)


def _pick_text(msg):
    for k in _TEXT_KEYS:
        v = msg.get(k)
        if isinstance(v, dict):
            v = v.get("text") or v.get("body") or v.get("html")
        if isinstance(v, str) and v.strip():
            if k in ("body_html", "html") or _HTML_RE.search(v):
                stripped = html.unescape(_TAG_RE.sub(" ", v))
                stripped = re.sub(r"[ \t]{2,}", " ", stripped).strip()
                v = stripped or v      # dacă stripping-ul golește tot, păstrăm originalul
            return v
    return ""


def _msg_id(msg, ticket_id, idx, text, created):
    for k in ("id", "msg_id", "message_id", "_id", "uuid"):
        v = msg.get(k)
        if v not in (None, ""):
            return str(v)
    # Fără id de la sursă: cheie stabilă din CONȚINUT, nu din poziție — pozițiile se mută când
    # se șterge un mesaj, iar pe poziție am dubla rândurile la fiecare re-tragere.
    h = hashlib.sha1(f"{ticket_id}|{created}|{text[:200]}".encode("utf-8", "replace")).hexdigest()
    return "h:" + h[:16]


def normalize_message(msg, ticket_id, idx=None, max_chars=None, fetched_at=None):
    """Mesaj brut din get_conversation(mode=audit) → rândul de oglindă."""
    if msg.get("_normalized"):
        return msg
    text = sanitize(_pick_text(msg)) or ""
    created = iso_ts(msg.get("created_at") or msg.get("createdAt") or msg.get("timestamp")
                     or msg.get("time"))
    is_agent, is_bot = classify_author(msg)
    a = msg.get("author") if isinstance(msg.get("author"), dict) else {}
    trunc = msg.get("truncated")
    if trunc is None:
        trunc = bool(max_chars) and len(text) >= int(max_chars)
    return {
        "_normalized": True,
        "ticket_id": str(ticket_id),
        "msg_id": _msg_id(msg, ticket_id, idx, text, created),
        "idx": idx,
        "created_at": created,
        "type": msg.get("type") or msg.get("message_type") or "",
        "is_private": int(_flag(msg.get("is_private") or msg.get("private")
                                or msg.get("is_note") or msg.get("internal"))),
        "is_ai": int(_flag(msg.get("is_ai") or msg.get("ai_generated") or msg.get("is_bot_reply"))),
        "author_id": sanitize(str(a.get("id") or msg.get("author_id") or "")),
        "author_name": sanitize(str(a.get("name") or msg.get("author_name") or "")),
        "is_agent": int(is_agent),
        "is_operator_bot": int(is_bot),
        "channel": msg.get("channel") or msg.get("source") or "",
        "text": text,
        "text_len": len(text),
        "truncated": int(bool(trunc)),
        "fetched_at": fetched_at or now_iso(),
    }


def upsert_message(db, ticket_id, msg, idx=None, max_chars=None, fetched_at=None):
    """Scrie un mesaj în oglindă. Întoarce 'inserted' | 'updated' | 'unchanged' | 'frozen'.

    ⚠️ REGULA DE ÎNGHEȚ: un text existent NU e suprascris NICIODATĂ cu unul gol, cu un
    „mesaj șters" (tombstone), sau cu unul mai SCURT. Singura excepție, voită: dacă ce
    aveam era chiar un tombstone și acum vine text real, îl luăm."""
    row = normalize_message(msg, ticket_id, idx, max_chars, fetched_at)
    prev = db.execute("SELECT text FROM rp_message WHERE ticket_id=? AND msg_id=?",
                      (row["ticket_id"], row["msg_id"])).fetchone()
    old = (prev["text"] if prev else None) or ""
    p = {k: row[k] for k in _MSG_COLS}
    p["tomb"] = int(is_tombstone(row["text"]))
    p["old_tomb"] = int(is_tombstone(old))
    db.execute(_MSG_SQL, p)
    if prev is None:
        return "inserted"
    new = db.execute("SELECT text FROM rp_message WHERE ticket_id=? AND msg_id=?",
                     (row["ticket_id"], row["msg_id"])).fetchone()["text"] or ""
    if new == old:
        return "unchanged" if old == row["text"] else "frozen"
    return "updated"


def upsert_attachments(db, ticket_id, msg_id, msg_or_urls, fetched_at=None):
    """Atașamentele (URL-uri S3) — pur insert-or-ignore: un link captat nu se pierde."""
    urls = msg_or_urls
    if isinstance(msg_or_urls, dict):
        urls = []
        for att in (msg_or_urls.get("attachments") or msg_or_urls.get("files") or []):
            if isinstance(att, str):
                urls.append(att)
            elif isinstance(att, dict):
                u = att.get("url") or att.get("href") or att.get("src") or att.get("link")
                if u:
                    urls.append(u)
    n = 0
    for u in urls or []:
        if not u:
            continue
        db.execute("INSERT OR IGNORE INTO rp_attachment(ticket_id,msg_id,url,fetched_at)"
                   " VALUES (?,?,?,?)",
                   (str(ticket_id), str(msg_id), sanitize(str(u)), fetched_at or now_iso()))
        n += 1
    return n


# ─────────────────────────────────────────────────── Gmail: aceeasi regula de INGHET
# Textul unui email captat NU se pierde niciodata — exact ca la mesajele Richpanel. Rulam
# predicatul in ACELASI statement cu insertul (atomic), nu pe o citire de dinainte.
_GM_KEEP = """(
    LENGTH(COALESCE(gm_message.body_text,'')) > 0
    AND NOT (:old_tomb = 1 AND :tomb = 0 AND LENGTH(COALESCE(:body_text,'')) > 0)
    AND (   LENGTH(COALESCE(:body_text,'')) = 0
         OR :tomb = 1
         OR LENGTH(:body_text) <= LENGTH(gm_message.body_text) )
)"""

_GM_COLS = ("message_id", "mailbox", "gmail_id", "thread_id", "direction", "from_addr",
            "to_addr", "subject", "date_utc", "in_reply_to", "refs", "is_automated",
            "auto_reason", "body_text", "body_len", "has_attachments", "labels", "fetched_at")

_GM_SQL = f"""
INSERT INTO gm_message ({','.join(_GM_COLS)})
VALUES ({','.join(':' + c for c in _GM_COLS)})
ON CONFLICT(message_id) DO UPDATE SET
    body_text  = CASE WHEN {_GM_KEEP} THEN gm_message.body_text ELSE :body_text END,
    body_len   = CASE WHEN {_GM_KEEP} THEN gm_message.body_len  ELSE :body_len  END,
    -- cutia RAMANE prima observata: acelasi Message-ID poate ajunge in doua cutii (Cc),
    -- iar a doua trecere nu are voie sa rescrie proprietarul (l-am pierde din rapoarte).
    mailbox    = COALESCE(NULLIF(gm_message.mailbox,''), :mailbox),
    gmail_id   = COALESCE(NULLIF(gm_message.gmail_id,''), :gmail_id),
    thread_id  = COALESCE(NULLIF(:thread_id,''),  gm_message.thread_id),
    direction  = COALESCE(NULLIF(gm_message.direction,''), :direction),
    from_addr  = COALESCE(NULLIF(gm_message.from_addr,''), :from_addr),
    to_addr    = COALESCE(NULLIF(gm_message.to_addr,''),   :to_addr),
    -- subiectul si datele se completeaza, dar nu se sterg cu gol (vezi bug-ul rp: subject
    -- si first_message NU erau acoperite de inghet si o a doua tragere le golea)
    subject     = CASE WHEN LENGTH(COALESCE(:subject,'')) >= LENGTH(COALESCE(gm_message.subject,''))
                       THEN :subject ELSE gm_message.subject END,
    date_utc    = COALESCE(gm_message.date_utc, :date_utc),
    in_reply_to = COALESCE(NULLIF(:in_reply_to,''), gm_message.in_reply_to),
    refs        = COALESCE(NULLIF(:refs,''),        gm_message.refs),
    -- clasificarea se poate IMBUNATATI (reguli noi) → se rescrie liber; e derivata, nu date
    is_automated = :is_automated,
    auto_reason  = :auto_reason,
    -- „a avut atasamente" e un fapt: o data adevarat, ramane adevarat
    has_attachments = MAX(COALESCE(gm_message.has_attachments,0), COALESCE(:has_attachments,0)),
    labels      = :labels,
    fetched_at  = :fetched_at
"""


def upsert_gm_message(db, row, fetched_at=None):
    """Scrie un email in oglinda. Intoarce 'inserted' | 'updated' | 'unchanged' | 'frozen'.

    ⚠️ ACEEASI REGULA DE INGHET ca la `upsert_message`: un body captat NU e suprascris
    niciodata cu unul gol, cu un placeholder („mesaj indisponibil") sau cu unul mai SCURT
    (ex.: o a doua trecere care cere doar `format=metadata`). Exceptia voita ramane: daca
    ce aveam era chiar un placeholder si acum vine text real, il luam.

    Eticheta cutiei (`mailbox`) ramane a PRIMEI observatii — un Cc catre a doua cutie nu
    are voie sa mute emailul dintr-un magazin in altul."""
    p = {c: row.get(c) for c in _GM_COLS}
    p["message_id"] = str(p["message_id"] or "").strip()
    if not p["message_id"]:
        raise ValueError("upsert_gm_message: message_id gol")
    for c in ("mailbox", "gmail_id", "thread_id", "direction", "from_addr", "to_addr",
              "subject", "date_utc", "in_reply_to", "refs", "auto_reason", "body_text",
              "labels"):
        p[c] = sanitize(p[c]) if isinstance(p[c], str) else p[c]
    p["body_text"] = p["body_text"] or ""
    p["body_len"] = len(p["body_text"])
    p["is_automated"] = int(bool(p["is_automated"]))
    p["has_attachments"] = int(bool(p["has_attachments"]))
    p["fetched_at"] = fetched_at or p.get("fetched_at") or now_iso()

    prev = db.execute("SELECT body_text, labels FROM gm_message WHERE message_id=?",
                      (p["message_id"],)).fetchone()
    old = (prev["body_text"] if prev else None) or ""
    if prev is not None:
        # etichetele se REUNESC: un mesaj mutat azi din SPAM in INBOX nu are voie sa stearga
        # dovada ca fusese in SPAM (altfel „de ce n-a raspuns nimeni" devine nedemonstrabil)
        p["labels"] = ",".join(sorted(set(
            [x for x in (prev["labels"] or "").split(",") if x] +
            [x for x in (p["labels"] or "").split(",") if x])))
    p["tomb"] = int(is_tombstone(p["body_text"]))
    p["old_tomb"] = int(is_tombstone(old))
    db.execute(_GM_SQL, p)
    if prev is None:
        return "inserted"
    new = db.execute("SELECT body_text FROM gm_message WHERE message_id=?",
                     (p["message_id"],)).fetchone()["body_text"] or ""
    if new == old:
        return "unchanged" if old == p["body_text"] else "frozen"
    return "updated"


def upsert_gm_attachments(db, message_id, atts, fetched_at=None):
    """DOAR metadate (nume/mime/marime/attachment_id) — continutul NU se descarca."""
    n = 0
    for a in atts or []:
        fn = sanitize(str(a.get("filename") or "")).strip()
        if not fn:
            continue
        db.execute("INSERT INTO gm_attachment(message_id,filename,mime,size_bytes,"
                   "attachment_id,fetched_at) VALUES (?,?,?,?,?,?) "
                   "ON CONFLICT(message_id,filename) DO UPDATE SET "
                   "  mime=COALESCE(NULLIF(excluded.mime,''), gm_attachment.mime),"
                   "  size_bytes=MAX(COALESCE(gm_attachment.size_bytes,0),"
                   "                 COALESCE(excluded.size_bytes,0)),"
                   "  attachment_id=COALESCE(NULLIF(excluded.attachment_id,''),"
                   "                         gm_attachment.attachment_id)",
                   (str(message_id), fn, sanitize(str(a.get("mime") or "")),
                    int(a.get("size_bytes") or 0),
                    sanitize(str(a.get("attachment_id") or "")), fetched_at or now_iso()))
        n += 1
    return n


def gm_state_get(db, mailbox):
    r = db.execute("SELECT * FROM gm_state WHERE mailbox=?", (mailbox,)).fetchone()
    return dict(r) if r else {}


def gm_state_set(db, mailbox, history_id=None, messages_total=None, aliases=None,
                 note=None, ok=False):
    """historyId per cutie. ⚠️ se scrie DOAR dupa o trecere reusita — altfel o rulare
    cazuta la jumatate ar avansa cursorul si am pierde definitiv mesajele netrase."""
    cur = gm_state_get(db, mailbox)
    db.execute(
        "INSERT INTO gm_state(mailbox,history_id,last_sync_at,last_ok_at,messages_total,"
        "aliases,note) VALUES (?,?,?,?,?,?,?) ON CONFLICT(mailbox) DO UPDATE SET"
        " history_id=excluded.history_id, last_sync_at=excluded.last_sync_at,"
        " last_ok_at=excluded.last_ok_at, messages_total=excluded.messages_total,"
        " aliases=excluded.aliases, note=excluded.note",
        (mailbox,
         str(history_id) if history_id else cur.get("history_id"),
         now_iso(),
         now_iso() if ok else cur.get("last_ok_at"),
         messages_total if messages_total is not None else cur.get("messages_total"),
         aliases if aliases is not None else cur.get("aliases"),
         note if note is not None else cur.get("note")))
    db.commit()


def record_gap(db, message_id, day, mailbox, subject, from_addr, reason, found_at=None):
    """Un email din cutie care NU se regaseste in Richpanel. Verdictul se poate IMBUNATATI
    (din „probabil" in „sigur") pe masura ce oglinda RP se umple → upsert, nu insert."""
    db.execute("INSERT INTO gm_gap(message_id,day,mailbox,subject,from_addr,reason,found_at)"
               " VALUES (?,?,?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET"
               " day=excluded.day, mailbox=excluded.mailbox, subject=excluded.subject,"
               " from_addr=excluded.from_addr, reason=excluded.reason,"
               " found_at=excluded.found_at",
               (str(message_id), day, mailbox, sanitize(subject or ""),
                sanitize(from_addr or ""), reason, found_at or now_iso()))


# ──────────────────────────────────────────────────────────────────── magazin
def _dict_literal(path, name, fallback):
    """Citește un dicționar-constantă dintr-un alt skill FĂRĂ să-l importe (auto-triage
    importă pg8000; oglinda rămâne zero-dependency). O singură sursă de adevăr, fără drift."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
                return ast.literal_eval(node.value)
    except Exception:
        pass
    return dict(fallback)


PAGE_STORE = _dict_literal(
    os.path.join(HERE, "..", "richpanel-auto-triage", "richpanel_auto_triage.py"),
    "PAGE_STORE", {"775068272350568": "Magdeal", "426248277236834": "Esteban"})
ORDER_PFX = _dict_literal(
    os.path.join(HERE, "richpanel_export.py"), "ORDER_PFX",
    {"EST": "Esteban", "GT": "George Talent", "MAG": "Magdeal"})
STORE_BY_EMAIL = _dict_literal(
    os.path.join(HERE, "richpanel_export.py"), "STORE_BY_EMAIL",
    {"esteban.ro": "Esteban", "magdeal.ro": "Magdeal"})
ORDER_RE = re.compile(r"\b(" + "|".join(sorted((re.escape(k) for k in ORDER_PFX), key=len,
                                               reverse=True)) + r")[ -]?(\d{4,7})\b", re.I)


def resolve_store(t):
    """Magazinul unui tichet = PAGINA pe care a venit (`to.id`), NU brandul din Richpanel
    (ăla e STALE — vezi memoria fb-page-store-map). Fallback: domeniul de e-mail, apoi
    prefixul comenzii din text."""
    to = t.get("to") if isinstance(t.get("to"), dict) else {}
    s = PAGE_STORE.get(str(to.get("id") or ""))
    if s:
        return s
    mail = (to.get("email") or "").lower()
    for dom, name in STORE_BY_EMAIL.items():
        if dom and dom in mail:
            return name
    m = ORDER_RE.search((t.get("subject") or "") + " " + (t.get("first_message") or ""))
    if m:
        return ORDER_PFX.get(m.group(1).upper())
    return None


# ──────────────────────────────────────────────────────────────────── rp_ticket
_T_COLS = ("id", "conversation_no", "channel", "status", "priority", "assignee_id",
           "to_id", "to_email", "from_id", "from_email", "customer_id", "customer_name",
           "customer_email", "customer_phone", "tag_names", "subject", "first_message",
           "comment_count", "created_at", "updated_at", "closed_at", "first_responded_at",
           "store_resolved", "fetched_at", "msg_total_at_fetch")

# Și aici: niciun câmp de conținut nu se golește la o re-tragere sărăcăcioasă.
_T_SQL = f"""
INSERT INTO rp_ticket ({','.join(_T_COLS)})
VALUES ({','.join(':' + c for c in _T_COLS)})
ON CONFLICT(id) DO UPDATE SET
    conversation_no    = COALESCE(:conversation_no, rp_ticket.conversation_no),
    channel            = COALESCE(NULLIF(:channel,''),  rp_ticket.channel),
    status             = COALESCE(NULLIF(:status,''),   rp_ticket.status),
    priority           = COALESCE(NULLIF(:priority,''), rp_ticket.priority),
    assignee_id        = COALESCE(NULLIF(:assignee_id,''), rp_ticket.assignee_id),
    to_id              = COALESCE(NULLIF(:to_id,''),    rp_ticket.to_id),
    to_email           = COALESCE(NULLIF(:to_email,''), rp_ticket.to_email),
    from_id            = COALESCE(NULLIF(:from_id,''),  rp_ticket.from_id),
    from_email         = COALESCE(NULLIF(:from_email,''), rp_ticket.from_email),
    customer_id        = COALESCE(NULLIF(:customer_id,''), rp_ticket.customer_id),
    customer_name      = COALESCE(NULLIF(:customer_name,''), rp_ticket.customer_name),
    customer_email     = COALESCE(NULLIF(:customer_email,''), rp_ticket.customer_email),
    customer_phone     = COALESCE(NULLIF(:customer_phone,''), rp_ticket.customer_phone),
    tag_names          = COALESCE(NULLIF(:tag_names,'[]'), rp_ticket.tag_names),
    -- ⚠️ subject si first_message NU erau acoperite de inghet: o a doua tragere cu
    -- "This message was deleted" a otravit 81/81 tichete REALE in test (48% din tichete au
    -- first_message <=24 caractere, deci sunt expuse). Aplicam ACEEASI regula ca la mesaje:
    -- nu suprascrie cu gol, cu tombstone, sau cu ceva mai scurt/egal.
    subject            = CASE WHEN LENGTH(COALESCE(:subject,'')) = 0
                               OR :subject_tomb = 1
                               OR (LENGTH(COALESCE(rp_ticket.subject,'')) > 0
                                   AND LENGTH(:subject) <= LENGTH(rp_ticket.subject))
                              THEN rp_ticket.subject ELSE :subject END,
    first_message      = CASE WHEN LENGTH(COALESCE(:first_message,'')) = 0
                               OR :fm_tomb = 1
                               OR (LENGTH(COALESCE(rp_ticket.first_message,'')) > 0
                                   AND LENGTH(:first_message) <= LENGTH(rp_ticket.first_message))
                              THEN rp_ticket.first_message ELSE :first_message END,
    comment_count      = MAX(COALESCE(:comment_count,0), COALESCE(rp_ticket.comment_count,0)),
    created_at         = COALESCE(rp_ticket.created_at, :created_at),
    updated_at         = COALESCE(NULLIF(:updated_at,''), rp_ticket.updated_at),
    closed_at          = COALESCE(NULLIF(:closed_at,''), rp_ticket.closed_at),
    first_responded_at = COALESCE(NULLIF(:first_responded_at,''), rp_ticket.first_responded_at),
    store_resolved     = COALESCE(NULLIF(:store_resolved,''), rp_ticket.store_resolved),
    fetched_at         = :fetched_at,
    msg_total_at_fetch = MAX(COALESCE(:msg_total_at_fetch,0),
                             COALESCE(rp_ticket.msg_total_at_fetch,0))
"""


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def upsert_ticket(db, t, msg_total=None, fetched_at=None):
    """Sumarul conversației (din list_conversations sau get_conversation)."""
    frm = t.get("from") if isinstance(t.get("from"), dict) else {}
    to = t.get("to") if isinstance(t.get("to"), dict) else {}
    cust = t.get("customer") if isinstance(t.get("customer"), dict) else {}
    tags = t.get("tags") or t.get("tag_names") or []
    names = [x.get("name") if isinstance(x, dict) else str(x) for x in tags]
    _subj = sanitize(str(t.get("subject") or ""))
    _fm = sanitize(str(t.get("first_message") or t.get("firstMessage") or ""))
    p = {
        # marcaje de tombstone pt inghetul de la nivel de tichet (vezi clauzele CASE de mai sus)
        "subject_tomb": 1 if is_tombstone(_subj) else 0,
        "fm_tomb": 1 if is_tombstone(_fm) else 0,
        "id": str(t.get("id")),
        "conversation_no": t.get("conversation_no") or t.get("conversationNo"),
        "channel": t.get("channel") or "",
        "status": t.get("status") or "",
        "priority": t.get("priority") or "",
        "assignee_id": str(t.get("assignee_id") or t.get("assigneeId") or ""),
        "to_id": str(to.get("id") or ""),
        "to_email": sanitize(str(to.get("email") or "")),
        "from_id": str(frm.get("id") or ""),
        "from_email": sanitize(str(frm.get("email") or "")),
        "customer_id": str(cust.get("id") or ""),
        "customer_name": sanitize(str(cust.get("name") or "")),
        "customer_email": sanitize(str(cust.get("email") or "")),
        "customer_phone": sanitize(str(cust.get("phone") or frm.get("phone") or "")),
        "tag_names": json.dumps([sanitize(n) for n in names if n], ensure_ascii=False),
        "subject": sanitize(t.get("subject") or ""),
        "first_message": sanitize(t.get("first_message") or ""),
        "comment_count": t.get("comment_count"),
        "created_at": iso_ts(t.get("created_at")),
        "updated_at": iso_ts(t.get("updated_at")) or "",
        "closed_at": iso_ts(_first(t, "closed_at", "closedAt", "resolved_at")) or "",
        "first_responded_at": iso_ts(_first(t, "first_responded_at", "firstRespondedAt",
                                            "first_response_at")) or "",
        "store_resolved": resolve_store(t) or "",
        "fetched_at": fetched_at or now_iso(),
        "msg_total_at_fetch": msg_total,
    }
    db.execute(_T_SQL, p)
    return p["id"]


def record_parity(db, day, channel, rp_count, mirror_count, matched, missing_sample=None,
                  run_at=None):
    """Paritate zilnică oglindă vs Richpanel. `missing_sample` = listă de id-uri (JSON)."""
    if not isinstance(missing_sample, str):
        missing_sample = json.dumps(missing_sample or [], ensure_ascii=False)
    cov = round(100.0 * (matched or 0) / rp_count, 2) if rp_count else None
    db.execute("INSERT OR REPLACE INTO parity_daily"
               "(day,channel,rp_count,mirror_count,matched,missing_sample,coverage_pct,run_at)"
               " VALUES (?,?,?,?,?,?,?,?)",
               (day, channel or "all", rp_count, mirror_count, matched, missing_sample,
                cov, run_at or now_iso()))
    db.commit()
    return cov


class _Run:
    def __init__(self):
        self.items = 0
        self.errors = 0
        self.note = ""


class sync_run:
    """with sync_run(db, 'pull') as r: r.items += 1 … — jurnalul rulărilor, inclusiv când
    crapă (ok=0 + excepția în `note`). Fără el, un job mort e invizibil."""

    def __init__(self, db, job, note=""):
        self.db, self.job = db, job
        self.run = _Run()
        self.run.note = note

    def __enter__(self):
        self.started = now_iso()
        self.db.execute("INSERT INTO sync_run(job,started_at,ok,items,errors,note)"
                        " VALUES (?,?,?,?,?,?)", (self.job, self.started, 0, 0, 0, self.run.note))
        self.db.commit()
        return self.run

    def __exit__(self, exc_type, exc, tb):
        note = self.run.note
        if exc is not None:
            note = f"{type(exc).__name__}: {exc}"[:400] + (" | " + note if note else "")
        self.db.execute("UPDATE sync_run SET ended_at=?, ok=?, items=?, errors=?, note=?"
                        " WHERE job=? AND started_at=?",
                        (now_iso(), 0 if exc else 1, self.run.items,
                         self.run.errors + (1 if exc else 0), note, self.job, self.started))
        self.db.commit()
        return False


# ──────────────────────────────────────────────────────────────── rate limiting
class RateLimiter:
    """Pacer pe DEADLINE, nu sleep fix.

    ⚠️ sleep(1.1) după fiecare apel NU dă 55/min: cu latență p50 de 0,34-0,42 s perioada
    reală devine ~1,6 s → 37/min (măsurat). Aici fiecare cerere are un SLOT programat:
    următorul slot = slotul curent + interval, indiferent cât a durat apelul.

    Limita HARD e 60 req/min pe token și e PARTAJATĂ cu CS-ul live → 30/min în 08-18,
    ~50/min noaptea, și încetinim singuri când x-ratelimit-remaining scade sub rezervă
    (implicit 12, care rămâne a CS-ului live)."""

    DAY_RPM, NIGHT_RPM, DAY_HOURS = 30, 50, range(8, 18)

    def __init__(self, rpm=None, reserve=12, clock=time.monotonic, sleep=time.sleep,
                 hour_fn=None):
        self.fixed_rpm = rpm
        self.reserve = reserve
        self.clock, self.sleep = clock, sleep
        self.hour_fn = hour_fn or (lambda: datetime.datetime.now().hour)
        self._next = clock()
        self._hold_until = 0.0
        self._win = collections.deque()
        self.cap = None            # x-ratelimit-limit
        self.remaining = None      # x-ratelimit-remaining
        self.reset_in = None       # secunde până la resetarea ferestrei
        self.n_calls = self.n_slow = self.n_429 = 0
        self.slept = 0.0

    def target_rpm(self):
        rpm = self.fixed_rpm or (self.DAY_RPM if self.hour_fn() in self.DAY_HOURS
                                 else self.NIGHT_RPM)
        if self.cap:
            rpm = min(rpm, max(1, int(self.cap) - self.reserve))
        return max(1, rpm)

    def wait(self):
        """Blochează până la slotul următor. Întoarce câte secunde a dormit."""
        rpm = self.target_rpm()
        interval = 60.0 / rpm
        now = self.clock()
        due = max(now, self._next, self._hold_until)
        # plasă de siguranță peste pacer: fereastra glisantă de 60 s
        if len(self._win) >= rpm and self._win[0] + 60.0 > due:
            due = self._win[0] + 60.0
        slept = 0.0
        if due > now:
            slept = due - now
            self.sleep(slept)
            self.slept += slept
        self._win.append(due)
        while self._win and self._win[0] <= due - 60.0:
            self._win.popleft()
        self._next = due + interval     # ← din SLOT, nu din „acum": latența nu se mai adună
        self.n_calls += 1
        return slept

    def observe(self, headers):
        """Pilotează pe headere (Retry-After, x-ratelimit-limit/remaining/reset)."""
        if not headers:
            return
        try:
            items = headers.items() if hasattr(headers, "items") else headers
            h = {str(k).lower(): v for k, v in items}
        except Exception:
            return
        now = self.clock()

        def num(*names):
            for n in names:
                v = h.get(n)
                if v not in (None, ""):
                    try:
                        return float(str(v).strip())
                    except ValueError:
                        return None
            return None

        ra = num("retry-after")
        if ra is not None:
            self.penalize(ra)
        self.cap = num("x-ratelimit-limit", "ratelimit-limit") or self.cap
        rem = num("x-ratelimit-remaining", "ratelimit-remaining")
        rst = num("x-ratelimit-reset", "ratelimit-reset")
        if rst is not None:
            # unele API-uri dau epoch absolut, altele secunde rămase
            self.reset_in = max(0.0, rst - time.time()) if rst > 1e6 else max(0.0, rst)
        if rem is None:
            return
        self.remaining = rem
        left = self.reset_in if self.reset_in is not None else 60.0
        if rem <= self.reserve:
            self._hold_until = max(self._hold_until, now + left)   # rezerva e a CS-ului live
            self.n_slow += 1
        elif rem < self.reserve * 2:
            # întindem cererile rămase peste tot ce a mai rămas din fereastră
            self._next = max(self._next, now + left / max(1.0, rem - self.reserve))
            self.n_slow += 1

    def penalize(self, seconds):
        """429 sau Retry-After explicit."""
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            s = 5.0
        self.n_429 += 1
        self._hold_until = max(self._hold_until, self.clock() + max(1.0, s))

    def stats(self):
        return {"calls": self.n_calls, "rpm_target": self.target_rpm(),
                "slept_s": round(self.slept, 1), "remaining": self.remaining,
                "limit": self.cap, "slowdowns": self.n_slow, "http_429": self.n_429}


# ───────────────────────────────────────────────────────── gardă READ-ONLY + MCP
READ_TOOLS = {"get_conversation", "list_conversations", "search_conversations_by_customer",
              "get_customer_by_email_or_phone", "get_user", "list_users", "list_tags",
              "list_teams", "query_analytics", "get_available_metrics",
              "list_ai_closure_candidates"}
WRITE_TOOLS = {"add_private_note", "create_draft", "send_message", "add_tags_to_conversation",
               "remove_tags_from_conversation", "create_tag", "assign_conversation",
               "snooze_conversation", "update_conversation", "update_conversation_status"}


def assert_read_only(tool):
    """Faza de captare: oglinda NU scrie în Richpanel. Orice unealtă de scriere (sau
    necunoscută) e refuzată AICI, nu descoperită în producție."""
    if tool in WRITE_TOOLS:
        raise PermissionError(f"cs_mirror e READ-ONLY: unealta de SCRIERE '{tool}' e interzisă")
    if tool not in READ_TOOLS:
        raise PermissionError(f"cs_mirror e READ-ONLY: unealta '{tool}' nu e în lista de citire")
    return tool


def _rp():
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import rp
    return rp


class MirrorMCP:
    """rp.MCP (client MCP + retry + secretul din KB) + pacer + capturarea headerelor +
    gardă read-only. Singurul lucru rescris față de rp.MCP e transportul `_post`, fiindcă
    rp.MCP aruncă răspunsul HTTP, iar nouă ne trebuie x-ratelimit-remaining ca să pilotăm."""

    def __init__(self, limiter=None):
        rp = _rp()
        self.limiter = limiter or RateLimiter()
        self.last_headers = {}
        self.mcp = rp.MCP.__new__(rp.MCP)       # instanță rp.MCP, dar cu transportul nostru
        self.mcp._post = self._post
        self.mcp.tok = rp.secret("RICHPANEL_MCP_TOKEN")
        self.mcp.sid = None
        self.mcp.n = 0
        self.mcp._init()

    def _post(self, body, tries=5):
        import json as _json
        import urllib.error, urllib.request
        rp = _rp()
        h = {"Authorization": f"Bearer {self.mcp.tok}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.mcp.sid:
            h["Mcp-Session-Id"] = self.mcp.sid
        req = urllib.request.Request(rp.MCP_URL, data=_json.dumps(body).encode(), headers=h)
        txt = ""
        for i in range(tries):
            self.limiter.wait()
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    self.mcp.sid = r.headers.get("Mcp-Session-Id") or self.mcp.sid
                    self.last_headers = {k.lower(): v for k, v in r.headers.items()}
                    txt = r.read().decode("utf-8", "replace")
                self.limiter.observe(self.last_headers)
                break
            except urllib.error.HTTPError as e:
                self.last_headers = {k.lower(): v for k, v in (e.headers or {}).items()}
                if e.code == 429:
                    self.limiter.penalize(self.last_headers.get("retry-after", 5))
                self.limiter.observe(self.last_headers)
                if e.code not in (429, 500, 502, 503, 504) or i == tries - 1:
                    raise
                time.sleep(2 ** i * 1.5)
            except (TimeoutError, OSError):
                if i == tries - 1:
                    raise
                time.sleep(2 ** i * 1.5)
        out = None
        for line in txt.splitlines():
            if line.startswith("data:"):
                out = _json.loads(line[5:].strip())
        return out if out is not None else (_json.loads(txt) if txt.strip() else None)

    def call(self, tool, args=None):
        assert_read_only(tool)
        return self.mcp.call(tool, args or {})


# ───────────────────────────────────────────────────────────────────── selftest
def _chk(results, name, cond, detail=""):
    results.append((bool(cond), name, detail))
    print(f"  {'OK  ' if cond else 'PICA'} {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


class FakeClock:
    """Ceas injectabil: testăm pacerul fără să dormim cu adevărat."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def selftest(db_path=None):
    R = []
    path = db_path or os.path.join(os.environ.get("TMPDIR", "/tmp"), "cs_mirror_selftest.db")
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(path + suf):
            os.remove(path + suf)

    print("\n1) SCHEMA + WAL")
    db = open_db(path)
    open_db(path).close()                        # a doua oară: idempotent
    tabs = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    _chk(R, "cele 9 tabele există", {"rp_ticket", "rp_message", "rp_attachment",
                                     "parity_daily", "sync_run", "gm_message", "gm_attachment",
                                     "gm_gap", "gm_state"} <= tabs, ", ".join(sorted(tabs)))
    idx = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'"
                                    " AND name LIKE 'ix_%'")}
    need = {"ix_ticket_updated", "ix_ticket_channel", "ix_msg_ticket", "ix_msg_agent",
            "ix_msg_created"}
    _chk(R, "indecșii ceruți există", need <= idx, ", ".join(sorted(need & idx)))
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    _chk(R, "journal_mode = wal", mode == "wal", mode)
    cols = [r[1] for r in db.execute("PRAGMA table_info(rp_ticket)")]
    _chk(R, "rp_ticket are cele 25 de coloane cerute",
         len(cols) == 25 and "msg_total_at_fetch" in cols, f"{len(cols)} coloane")
    mcols = [r[1] for r in db.execute("PRAGMA table_info(rp_message)")]
    _chk(R, "rp_message are cele 16 coloane cerute", len(mcols) == 16, f"{len(mcols)} coloane")

    print("\n2) REGULA DE INGHET (miezul oglinzii)")
    T, M = "conv_1", "m_1"
    orig = "Buna ziua, coletul GT45911 nu a ajuns si as vrea sa stiu unde este. Multumesc!"

    def got():
        return db.execute("SELECT * FROM rp_message WHERE ticket_id=? AND msg_id=?",
                          (T, M)).fetchone()

    st = upsert_message(db, T, {"id": M, "text": orig, "created_at": "2026-08-30T10:00:00Z",
                                "author_is_workspace_agent": False,
                                "author": {"id": "cus_7", "name": "Ana"}}, idx=0)
    _chk(R, "prima captare scrie textul", st == "inserted" and got()["text"] == orig,
         f"status={st} len={got()['text_len']}")

    st = upsert_message(db, T, {"id": M, "text": "", "author_is_workspace_agent": False,
                                "author": {"id": "cus_7"}}, idx=0)
    _chk(R, "text GOL nu suprascrie (inghet)", st == "frozen" and got()["text"] == orig,
         f"status={st}")

    st = upsert_message(db, T, {"id": M, "text": None, "author": {"id": "cus_7"}}, idx=0)
    _chk(R, "text None nu suprascrie", got()["text"] == orig, f"status={st}")

    st = upsert_message(db, T, {"id": M, "text": "Buna ziua, coletul nu a ajuns.",
                                "author": {"id": "cus_7"}}, idx=0)
    _chk(R, "text mai SCURT nu suprascrie", st == "frozen" and got()["text"] == orig,
         f"status={st} len ramas={got()['text_len']}")

    # tombstone-ul e MAI LUNG decât textul original => dovedește că regula nu e doar „lungime"
    T2, M2 = "conv_2", "m_2"
    upsert_message(db, T2, {"id": M2, "text": "ok", "author": {"id": "cus_9"}}, idx=0)
    st = upsert_message(db, T2, {"id": M2, "text": "This message was deleted",
                                 "author": {"id": "cus_9"}}, idx=0)
    row2 = db.execute("SELECT text FROM rp_message WHERE ticket_id=? AND msg_id=?",
                      (T2, M2)).fetchone()
    _chk(R, "tombstone (mai LUNG decat textul) nu suprascrie",
         st == "frozen" and row2["text"] == "ok", f"status={st} ramas={row2['text']!r}")

    for tomb in ("Acest mesaj a fost sters", "[deleted]", "Content unavailable",
                 "Message removed."):
        upsert_message(db, T2, {"id": M2, "text": tomb, "author": {"id": "cus_9"}}, idx=0)
    row2 = db.execute("SELECT text FROM rp_message WHERE ticket_id=? AND msg_id=?",
                      (T2, M2)).fetchone()
    _chk(R, "variantele de tombstone (RO/EN) nu suprascriu", row2["text"] == "ok",
         f"ramas={row2['text']!r}")

    longer = orig + " Am incercat sa sun la curier dar nu raspunde nimeni."
    st = upsert_message(db, T, {"id": M, "text": longer, "author": {"id": "cus_7"}}, idx=0)
    _chk(R, "text mai LUNG (re-fetch cu max_message_chars mare) SE scrie",
         st == "updated" and got()["text"] == longer,
         f"status={st} {len(orig)}->{got()['text_len']} caractere")

    st = upsert_message(db, T, {"id": M, "text": longer, "author": {"id": "cus_7"}}, idx=0)
    _chk(R, "re-tragere identica = 'unchanged'", st == "unchanged", f"status={st}")

    T3, M3 = "conv_3", "m_3"
    upsert_message(db, T3, {"id": M3, "text": "This message was deleted",
                            "author": {"id": "cus_1"}}, idx=0)
    st = upsert_message(db, T3, {"id": M3, "text": "textul real, recuperat mai tarziu",
                                 "author": {"id": "cus_1"}}, idx=0)
    r3 = db.execute("SELECT text FROM rp_message WHERE ticket_id=? AND msg_id=?",
                    (T3, M3)).fetchone()
    _chk(R, "tombstone vechi <- text real: SE actualizeaza",
         st == "updated" and r3["text"].startswith("textul real"), f"status={st}")

    _chk(R, "text_len ramane sincronizat cu textul inghetat",
         got()["text_len"] == len(got()["text"]) == len(longer))

    db.commit()
    db.close()
    db = open_db(path)                            # înghețul e PERSISTENT, nu doar în memorie
    _chk(R, "textul inghetat supravietuieste reconectarii la baza",
         db.execute("SELECT text FROM rp_message WHERE ticket_id=? AND msg_id=?",
                    (T, M)).fetchone()["text"] == longer)
    n_rows = db.execute("SELECT COUNT(*) FROM rp_message").fetchone()[0]
    _chk(R, "nicio dublare de randuri (PK ticket_id,msg_id)", n_rows == 3,
         f"{n_rows} randuri dupa 14 upsert-uri")

    print("\n3) classify_author — agent REAL vs bot canned")
    agent = {"author_is_workspace_agent": True, "author": {"id": "usr_88", "name": "Raluca"}}
    bot = {"author_is_workspace_agent": True, "author": {"id": "operator", "name": "Operator"},
           "text": "Sunteti deja client?"}
    client = {"author_is_workspace_agent": False, "author": {"id": "cus_5", "name": "Ion"}}
    _chk(R, "agent real -> (True, False)", classify_author(agent) == (True, False))
    _chk(R, "bot 'operator' -> (False, True)", classify_author(bot) == (False, True))
    _chk(R, "client -> (False, False)", classify_author(client) == (False, False))
    _chk(R, "flagul ca string 'true' e inteles",
         classify_author({"author_is_workspace_agent": "true",
                          "author": {"id": "usr_2"}}) == (True, False))
    upsert_message(db, "conv_4", {**bot, "id": "b1"}, idx=0)
    upsert_message(db, "conv_4", {**agent, "id": "a1", "text": "Verific si revin"}, idx=1)
    n_agent = db.execute("SELECT COUNT(*) FROM rp_message WHERE is_agent=1").fetchone()[0]
    n_bot = db.execute("SELECT COUNT(*) FROM rp_message WHERE is_operator_bot=1").fetchone()[0]
    _chk(R, "botul e PASTRAT dar marcat (nu aruncat)", n_bot == 1 and n_agent == 1,
         f"agenti={n_agent} boti={n_bot}")

    print("\n4) sanitize — surrogate rupte din Facebook")
    broken = "Super produs 😀 recomand \ud83d!"
    err = ""
    try:
        db.execute("INSERT INTO rp_attachment VALUES ('x','x',?,'now')", (broken,))
        raw_ok = True
    except (UnicodeEncodeError, sqlite3.ProgrammingError, sqlite3.InterfaceError) as e:
        raw_ok, err = False, type(e).__name__
    _chk(R, "textul brut CHIAR crapa sqlite3 (de-asta exista sanitize)", not raw_ok,
         err if not raw_ok else "a intrat fara eroare?!")
    clean = sanitize(broken)
    db.execute("INSERT OR REPLACE INTO rp_attachment VALUES ('x','x',?,'now')", (clean,))
    db.commit()
    _chk(R, "perechea valida e recompusa in emoji real", "\U0001f600" in clean, repr(clean))
    _chk(R, "surrogate-ul orfan e inlocuit, restul textului ramane",
         "�" in clean and clean.startswith("Super produs") and clean.endswith("!"))
    _chk(R, "NUL eliminat", sanitize("a\x00b") == "ab")
    _chk(R, "textul curat trece neatins", sanitize("bună ziua") == "bună ziua")

    print("\n5) RateLimiter — pacer pe deadline")
    ck = FakeClock()
    rl = RateLimiter(rpm=60, clock=ck, sleep=ck.sleep)
    rl.wait()                       # slot 0 la t=0
    ck.t += 0.40                    # latența reală măsurată (p50 0,34-0,42 s)
    rl.wait()
    _chk(R, "latenta NU se aduna la interval (t=1.0, nu 1.4)", abs(ck.t - 1.0) < 1e-9,
         f"t={ck.t:.2f}s (naiv ar fi 1.40 => 43/min in loc de 60/min)")
    for _ in range(8):
        ck.t += 0.40
        rl.wait()
    eff = (rl.n_calls - 1) / (ck.t / 60.0)
    _chk(R, "10 cereri la ritm de 60/min ies exact 60/min", abs(eff - 60) < 0.5,
         f"{eff:.1f}/min in {ck.t:.1f}s")

    ck2 = FakeClock()
    rl2 = RateLimiter(clock=ck2, sleep=ck2.sleep, hour_fn=lambda: 10)
    _chk(R, "ziua (08-18) plafon 30/min", rl2.target_rpm() == 30)
    rl2.hour_fn = lambda: 3
    _chk(R, "noaptea ~50/min", rl2.target_rpm() == 50)
    rl2.observe({"x-ratelimit-limit": "60", "x-ratelimit-remaining": "40",
                 "x-ratelimit-reset": "30"})
    _chk(R, "capul din x-ratelimit-limit, minus rezerva", rl2.target_rpm() == 48,
         f"limit=60 rezerva=12 -> {rl2.target_rpm()}/min")
    t0 = ck2.t
    rl2.observe({"x-ratelimit-remaining": "5", "x-ratelimit-reset": "25"})
    rl2.wait()
    _chk(R, "sub rezerva (5 < 12) asteapta resetul ferestrei", ck2.t - t0 >= 25,
         f"a dormit {ck2.t - t0:.0f}s")
    t0 = ck2.t
    rl2.penalize(7)
    rl2.wait()
    _chk(R, "429 / Retry-After respectat", ck2.t - t0 >= 7, f"a dormit {ck2.t - t0:.0f}s")
    _chk(R, "stats() raporteaza 429 si incetinirile",
         rl2.stats()["http_429"] == 1 and rl2.stats()["slowdowns"] >= 1, str(rl2.stats()))

    print("\n6) garda READ-ONLY (faza de captare)")
    blocked = []
    for w in ("create_draft", "add_private_note", "update_conversation_status",
              "add_tags_to_conversation", "send_message"):
        try:
            assert_read_only(w)
        except PermissionError:
            blocked.append(w)
    _chk(R, "toate uneltele de scriere sunt refuzate", len(blocked) == 5, ", ".join(blocked))
    _chk(R, "citirile trec", assert_read_only("get_conversation") == "get_conversation")
    try:
        assert_read_only("inventata_de_cineva")
        unknown_blocked = False
    except PermissionError:
        unknown_blocked = True
    _chk(R, "unealta necunoscuta = refuzata (allowlist)", unknown_blocked)

    print("\n7) ticket / atasamente / paritate / sync_run")
    ms = int(datetime.datetime(2026, 8, 30, 9, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    t = {"id": "conv_1", "conversation_no": 312879, "channel": "facebook_feed_comment",
         "status": "OPEN", "subject": "Unde e comanda GT45911?",
         "first_message": "Buna, unde e coletul meu GT45911?",
         "to": {"id": "775068272350568", "email": "info@magdeal.ro"},
         "from": {"id": "fb_1", "email": "ana@gmail.com"},
         "customer": {"id": "c1", "name": "Ana", "email": "ana@gmail.com", "phone": "0700000000"},
         "tags": ["wismo"], "comment_count": 3, "created_at": ms,
         "updated_at": "2026-08-30T12:00:00Z"}
    upsert_ticket(db, t, msg_total=5)
    row = db.execute("SELECT * FROM rp_ticket WHERE id='conv_1'").fetchone()
    _chk(R, "magazinul vine din to.id (nu din brandul stale)", row["store_resolved"] == "Magdeal",
         row["store_resolved"])
    _chk(R, "epoch ms -> ISO UTC", (row["created_at"] or "").startswith("2026-08-30T09:00"),
         row["created_at"])
    upsert_ticket(db, {**t, "subject": "", "first_message": "", "comment_count": 0}, msg_total=2)
    row2 = db.execute("SELECT * FROM rp_ticket WHERE id='conv_1'").fetchone()
    _chk(R, "re-tragere saracacioasa nu goleste subject/first_message",
         row2["subject"] == t["subject"] and row2["first_message"] == t["first_message"])
    _chk(R, "msg_total_at_fetch tine maximul vazut", row2["msg_total_at_fetch"] == 5,
         str(row2["msg_total_at_fetch"]))
    _chk(R, "magazin din prefixul comenzii cand nu stim pagina",
         resolve_store({"to": {"id": "999"}, "subject": "problema la EST12345"}) == "Esteban")
    n = upsert_attachments(db, "conv_1", "m_1",
                           {"attachments": [{"url": "https://s3/a.jpg"}, "https://s3/b.jpg"]})
    upsert_attachments(db, "conv_1", "m_1", {"attachments": [{"url": "https://s3/a.jpg"}]})
    n_att = db.execute("SELECT COUNT(*) FROM rp_attachment WHERE ticket_id='conv_1'").fetchone()[0]
    _chk(R, "atasamente dedublate pe (ticket,msg,url)", n_att == 2, f"{n} primite, {n_att} in baza")
    cov = record_parity(db, "2026-08-30", "facebook_feed_comment", 120, 118, 118, ["x1", "x2"])
    _chk(R, "parity_daily calculeaza acoperirea", abs(cov - 98.33) < 0.01, f"{cov}%")
    with sync_run(db, "selftest") as run:
        run.items = 7
    r = db.execute("SELECT * FROM sync_run WHERE job='selftest'").fetchone()
    _chk(R, "sync_run: ok=1, items=7, ended_at completat",
         r["ok"] == 1 and r["items"] == 7 and bool(r["ended_at"]))
    try:
        with sync_run(db, "selftest_fail") as run:
            run.items = 2
            raise RuntimeError("MCP 429 simulat")
    except RuntimeError:
        pass
    r = db.execute("SELECT * FROM sync_run WHERE job='selftest_fail'").fetchone()
    _chk(R, "o rulare care crapa ramane ok=0 + exceptia in note",
         r["ok"] == 0 and r["errors"] == 1 and "429" in (r["note"] or ""), r["note"])

    print("\n8) Gmail: ACEEASI regula de inghet pe gm_message")
    GID = "<CAG8fQB7v+UKodiknQnbJnzdyQx30X0SvOdPS5fC2KVKaxmdQ1w@mail.gmail.com>"
    body = ("Buna ziua, am comandat parfumul EST12345 saptamana trecuta si nu a ajuns. "
            "Puteti sa verificati va rog? Multumesc, Ana")

    def gm():
        return db.execute("SELECT * FROM gm_message WHERE message_id=?", (GID,)).fetchone()

    base = {"message_id": GID, "mailbox": "contact@esteban.ro", "gmail_id": "199a1",
            "thread_id": "t1", "direction": "in", "from_addr": "ana@gmail.com",
            "to_addr": "contact@esteban.ro", "subject": "Unde e comanda EST12345?",
            "date_utc": "2026-08-30T09:00:00+00:00", "in_reply_to": "", "refs": "",
            "is_automated": 0, "auto_reason": "", "body_text": body, "body_len": len(body),
            "has_attachments": 1, "labels": "INBOX,UNREAD"}
    st = upsert_gm_message(db, base)
    _chk(R, "email nou -> inserted", st == "inserted" and gm()["body_text"] == body, st)
    st = upsert_gm_message(db, {**base, "body_text": ""})
    _chk(R, "body GOL nu suprascrie (inghet)", st == "frozen" and gm()["body_text"] == body,
         f"{st} / {len(gm()['body_text'] or '')} car.")
    st = upsert_gm_message(db, {**base, "body_text": body[:20]})
    _chk(R, "body mai SCURT nu suprascrie (inghet)",
         st == "frozen" and gm()["body_text"] == body, st)
    _chk(R, "body_len ramane sincronizat cu textul inghetat", gm()["body_len"] == len(body),
         str(gm()["body_len"]))
    st = upsert_gm_message(db, {**base, "body_text": body + " P.S.: e cadou."})
    _chk(R, "body mai BOGAT il inlocuieste", st == "updated" and gm()["body_text"].endswith("cadou."))
    st = upsert_gm_message(db, {**base, "body_text": "[Message unavailable]"})
    _chk(R, "placeholder nu suprascrie textul real", st == "frozen" and "comandat" in gm()["body_text"])
    # acelasi Message-ID vazut si in a doua cutie (Cc) — proprietarul NU se muta
    upsert_gm_message(db, {**base, "mailbox": "contact@grandia.ro", "gmail_id": "zzz",
                           "body_text": "", "has_attachments": 0, "labels": "SPAM"})
    r8 = gm()
    _chk(R, "un Cc in a doua cutie nu muta emailul din prima",
         r8["mailbox"] == "contact@esteban.ro" and r8["gmail_id"] == "199a1", r8["mailbox"])
    _chk(R, "etichetele se REUNESC (dovada trecerii prin SPAM nu se pierde)",
         set((r8["labels"] or "").split(",")) == {"INBOX", "UNREAD", "SPAM"}, r8["labels"])
    _chk(R, "has_attachments o data adevarat ramane adevarat", r8["has_attachments"] == 1)
    upsert_gm_attachments(db, GID, [{"filename": "poza.jpg", "mime": "image/jpeg",
                                     "size_bytes": 91234, "attachment_id": "ANGjdJ"},
                                    {"filename": "", "mime": "image/png"}])
    upsert_gm_attachments(db, GID, [{"filename": "poza.jpg", "mime": "image/jpeg",
                                     "size_bytes": 91234, "attachment_id": "ANGjdJ"}])
    na = db.execute("SELECT COUNT(*) FROM gm_attachment WHERE message_id=?", (GID,)).fetchone()[0]
    _chk(R, "atasamente: doar METADATE, dedublate pe (mesaj,nume)", na == 1, f"{na} randuri")
    gm_state_set(db, "contact@esteban.ro", history_id="55501", messages_total=87320, ok=True)
    gm_state_set(db, "contact@esteban.ro", note="fallback pe data")   # rulare fara historyId nou
    s8 = gm_state_get(db, "contact@esteban.ro")
    _chk(R, "gm_state pastreaza historyId cand rularea nu-l avanseaza",
         s8["history_id"] == "55501" and s8["note"] == "fallback pe data", str(s8.get("history_id")))
    record_gap(db, GID, "2026-08-30", "contact@esteban.ro", "Unde e comanda EST12345?",
               "ana@gmail.com", "lipsa_in_rp_probabil")
    record_gap(db, GID, "2026-08-30", "contact@esteban.ro", "Unde e comanda EST12345?",
               "ana@gmail.com", "lipsa_in_rp")
    g8 = db.execute("SELECT COUNT(*) n, MAX(reason) r FROM gm_gap").fetchone()
    _chk(R, "verdictul de gap se IMBUNATATESTE, nu se dubleaza",
         g8["n"] == 1 and g8["r"] == "lipsa_in_rp", f"{g8['n']} randuri, {g8['r']}")

    db.close()
    ok = sum(1 for c, _, _ in R if c)
    print("\n" + "=" * 72)
    print(f"{ok}/{len(R)} verificari trecute · baza de test: {path}")
    for c, name, det in R:
        if not c:
            print(f"  PICAT: {name} — {det}")
    return 0 if ok == len(R) else 1


def stats(db):
    n_t = db.execute("SELECT COUNT(*) FROM rp_ticket").fetchone()[0]
    n_m = db.execute("SELECT COUNT(*) FROM rp_message").fetchone()[0]
    print(f"oglinda: {n_t} tichete · {n_m} mesaje")
    for lbl, cond in (("agenti reali", "is_agent=1"), ("boti canned", "is_operator_bot=1"),
                      ("note private", "is_private=1")):
        n = db.execute("SELECT COUNT(*) FROM rp_message WHERE " + cond).fetchone()[0]
        print(f"  {lbl:<14} {n}")
    for r in db.execute("SELECT job,started_at,ended_at,ok,items,errors FROM sync_run"
                        " ORDER BY started_at DESC LIMIT 5"):
        print(f"  {r['job']:<16} {r['started_at']} ok={r['ok']} items={r['items']}"
              f" err={r['errors']}")


def main():
    ap = argparse.ArgumentParser(description="oglinda CS Richpanel — biblioteca + selftest")
    ap.add_argument("mode", nargs="?", default="selftest",
                    choices=["selftest", "schema", "stats"])
    ap.add_argument("--db", default=DB_DEFAULT)
    a = ap.parse_args()
    if a.mode == "selftest":
        rc = selftest()
        print("\n— acum si schema bazei REALE —")
        db = open_db(a.db)
        names = sorted(r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"))
        print(f"OK: {a.db}\n  tabele: " + ", ".join(names))
        db.close()
        sys.exit(rc)
    db = open_db(a.db)
    if a.mode == "schema":
        print(f"schema OK in {a.db}")
        for r in db.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                            " ORDER BY type,name"):
            print(f"  {r['type']:<6} {r['name']}")
    else:
        stats(db)
    db.close()


if __name__ == "__main__":
    main()
