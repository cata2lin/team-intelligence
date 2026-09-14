# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "google-api-python-client>=2.100",
#   "google-auth>=2.30",
# ]
# ///
"""
gmail_sync.py — CAPTAREA cutiilor CS din Gmail în ACEEAȘI oglindă (cs_mirror.db).

⛔ Faza curentă = DOAR CITIRE. Scope UNIC: `gmail.readonly`. Nu se trimite, nu se marchează
   citit, nu se etichetează, nu se șterge. Garda `assert_read_only()` refuză explicit orice
   metodă Gmail care nu e de citire — nu descoperim în producție ce am uitat să interzicem.

    uv run gmail_sync.py --recent 3                       # ultimele N zile, toate cutiile CS
    uv run gmail_sync.py --mailbox contact@esteban.ro --recent 1
    uv run gmail_sync.py --since 2026-08-10 --until 2026-08-18   # fereastră fixă
    uv run gmail_sync.py --stats
    uv run gmail_sync.py --reconcile --days 7             # ce e în cutie dar NU în Richpanel

De ce există (fapte MĂSURATE, nu presupuneri):
  • Richpanel trimite răspunsurile prin infrastructura LUI (customerdesk.io/SES), deci
    cutiile Gmail conțin în principal INBOUND — plus răspunsurile pe care cineva le-a dat
    manual din Gmail, ocolind helpdesk-ul. Alea din urmă sunt invizibile pentru orice raport
    de CS: `--reconcile` le scoate la iveală.
  • 28 de adrese în Richpanel → 21 răspund → doar **17 cutii FIZICE**: 4 adrese sunt ALIASURI
    (contact@bonhaus.hu/hr + contact@nocturna.pl = contact@trynocturna.eu; contact@ofertelezilei.ro
    = contact@casaofertelor.ro; reclamatii@aronagroup.ro = facturi@aronagroup.ro). Dovadă dură:
    dump 7 zile bonhaus.hu vs trynocturna.eu = 36 vs 36 mesaje, suprapunere 100,0%.
    ⇒ cutiile se DEDUBLEAZĂ prin `users.getProfile().emailAddress`, altfel tragem aceeași
    cutie de 4 ori (dubluri + cotă irosită).
  • ~65% din INBOX e AUTOMAT (Judge.me, curieri, Shopify, Klaviyo). NU se aruncă — se
    MARCHEAZĂ (`is_automated` + `auto_reason`), altfel poluează orice statistică de CS.
  • TRASH = 0 pe toate cele 17 cutii → contează doar INBOX / SENT / SPAM.

Scrierea în oglindă trece NUMAI prin `cs_mirror.upsert_gm_*` → regula de ÎNGHEȚ (un text
captat nu se pierde niciodată) se aplică automat, la fel ca la mesajele Richpanel.
"""
import argparse
import base64
import collections
import datetime
import email.utils
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # stațiile CS = Windows cp1252

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cs_mirror as cm  # noqa: E402  (schemă + îngheț + pacer + sanitize)

from google.oauth2 import service_account          # noqa: E402
from googleapiclient.discovery import build        # noqa: E402
from googleapiclient.errors import HttpError       # noqa: E402

# ⛔ UN SINGUR scope, cel mai mic posibil. Delegarea de domeniu e acordată doar pentru el.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SA_SECRET = "GOOGLE_SA_LOOKER_SHEETS_JSON"
# ⚠️ Fara asta, pe VPS toate cutiile ies "inaccesibile" cu FileNotFoundError pe o cale de Mac —
# eroare care NU seamana cu "cale gresita", ci cu "delegarea nu merge". Rezolvam relativ la script.
_HERE = os.path.dirname(os.path.abspath(__file__))
KB_SCRIPTS = os.environ.get("KB_SCRIPTS") or os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "core", "scripts"))
RP_LEGACY_DB = os.environ.get("RICHPANEL_DB") or os.path.normpath(
    os.path.join(_HERE, "..", "..", "..", "..", "..", "data", "richpanel_tickets.db"))

LABELS_DEFAULT = "INBOX,SENT,SPAM"   # TRASH = 0 pe toate cele 17 cutii (măsurat, 7 zile)
MAX_BODY = 20000                     # caractere păstrate din corp (îngheț: mai lung câștigă)
GET_BATCH = 200                      # câte mesaje se scriu într-un commit

# Cutii care APAR în Richpanel dar NU sunt Customer Service (măsurat, 7 zile):
#   rossinails: 688/776 INBOX de la dpd.ro, 335/336 SENT către dpd.ro = reclamații la curier
#   facturi@aronagroup.ro: 2.348 SENT în 7 zile = facturier automat
# Nu le capturăm implicit (ar dubla volumul cu zgomot); `--all-boxes` le include.
DISCOVERY_ROW = "__discovery__"      # marcaj: descoperirea cutiilor s-a făcut COMPLET

NON_CS = {"contact@rossinails.ro": "reclamații la curier (98% DPD)",
          "facturi@aronagroup.ro": "facturier (2.348 SENT/7 zile)"}


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ─────────────────────────────────────────────────────── gardă READ-ONLY (Gmail)
# Simetric cu `cs_mirror.assert_read_only`: allowlist, nu blocklist. Orice metodă
# necunoscută e refuzată AICI, nu descoperită după ce a modificat o cutie.
GMAIL_READ_METHODS = {"users.getProfile", "users.messages.list", "users.messages.get",
                      "users.history.list", "users.labels.list", "users.threads.get"}


def assert_read_only(method):
    if method not in GMAIL_READ_METHODS:
        raise PermissionError(
            f"gmail_sync e READ-ONLY: metoda '{method}' nu e în lista de citire")
    return method


def assert_scopes():
    """Plasă peste plasă: dacă cineva adaugă vreodată un scope de scriere, oprim din prima."""
    bad = [s for s in SCOPES if not s.endswith("gmail.readonly")]
    if bad:
        raise PermissionError(f"scope INTERZIS în faza de captare: {bad}")


# ─────────────────────────────────────────────────────────────────────── auth
_SA_LOCK = threading.Lock()
_SA_INFO = None
_TL = threading.local()   # googleapiclient NU e thread-safe: un service per FIR


def sa_info():
    """Service account din KB. ⚠️ valoarea NU se printează niciodată."""
    global _SA_INFO
    with _SA_LOCK:
        if _SA_INFO is None:
            out = subprocess.run(["uv", "run", "kb.py", "secret-get", SA_SECRET],
                                 cwd=KB_SCRIPTS, capture_output=True, text=True,
                                 check=True).stdout
            i, j = out.find("{"), out.rfind("}")
            if i < 0 or j < 0:
                raise RuntimeError(f"secretul {SA_SECRET} nu arată a JSON")
            _SA_INFO = json.loads(out[i:j + 1])
        return _SA_INFO


def svc(mailbox):
    assert_scopes()
    cache = getattr(_TL, "svc", None)
    if cache is None:
        cache = _TL.svc = {}
    if mailbox not in cache:
        creds = service_account.Credentials.from_service_account_info(
            sa_info(), scopes=SCOPES).with_subject(mailbox)
        cache[mailbox] = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return cache[mailbox]


def err_str(e):
    if isinstance(e, HttpError):
        try:
            d = json.loads(e.content.decode())
            err = d.get("error", {})
            msg = err.get("message") if isinstance(err, dict) else err
            return f"HTTP {e.resp.status} {msg or d}"
        except Exception:
            return f"HTTP {e.resp.status}"
    return f"{type(e).__name__}: {str(e)[:160]}"


def _http_reason(e):
    try:
        d = json.loads(e.content.decode())
        errs = d.get("error", {}).get("errors") or []
        return (errs[0].get("reason") if errs else d.get("error", {}).get("status")) or ""
    except Exception:
        return ""


RETRY_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded",
                 "backendError", "internalError", "RESOURCE_EXHAUSTED", "UNAVAILABLE"}


class Pacer:
    """Pacerul Gmail = `cs_mirror.RateLimiter`, dar pe SECUNDĂ, nu pe minut.

    Limita Gmail e de 250 unități de cotă /utilizator/secundă, iar `messages.get` costă 5
    ⇒ 50 apeluri/s teoretic. Ținta implicită e 20/s (100 unități/s = 40% din buget), ca
    să rămână loc pentru orice altceva atinge aceleași cutii. Cota fiind PER UTILIZATOR,
    fiecare cutie primește propriul pacer.

    `RateLimiter.wait()` mută stare partajată ⇒ îl serializăm cu un lock (tragem cu mai
    multe fire pe aceeași cutie)."""

    def __init__(self, rps=20.0):
        self.rl = cm.RateLimiter(rpm=max(1, int(rps * 60)))
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            return self.rl.wait()

    def penalize(self, s):
        with self.lock:
            self.rl.penalize(s)

    def stats(self):
        return self.rl.stats()


def api(pacer, method, req, tries=6, none_on_404=False):
    """Un apel Gmail, păzit: allowlist + pacer + backoff pe 429/403 rateLimit/5xx.

    ⚠️ `none_on_404` DOAR pentru `messages.get` (mesaj șters între list și get). Global ar
    fi o bombă: `history.list` întoarce tot 404 când cursorul e prea vechi, iar înghițit
    aici ar arăta ca „n-a apărut nimic nou" — am pierde tăcut tot ce era de tras."""
    assert_read_only(method)
    last = None
    for attempt in range(tries):
        pacer.wait()
        try:
            return req.execute()
        except HttpError as e:
            last = e
            st, reason = e.resp.status, _http_reason(e)
            if st == 404 and none_on_404:
                return None                      # mesaj șters între list și get
            if st == 429 or (st == 403 and reason in RETRY_REASONS) or st in (500, 502, 503, 504):
                pacer.penalize(min(64, 2 ** attempt))
                continue
            raise
        except (TimeoutError, OSError) as e:     # rețea: googleapiclient le aruncă brut
            last = e
            pacer.penalize(min(30, 2 ** attempt))
            continue
    raise last if last else RuntimeError(f"{method}: eșec fără excepție")


# ────────────────────────────────────────────────── cutii: aliasurile se pliază
def cs_addresses():
    """Adresele de email din Richpanel, după volum. Sursa listei, nu o listă inventată."""
    if not os.path.exists(RP_LEGACY_DB):
        return []
    db = sqlite3.connect(f"file:{RP_LEGACY_DB}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT lower(trim(to_email)) a, COUNT(*) n FROM tickets"
        " WHERE channel LIKE 'email%' AND to_email IS NOT NULL AND to_email <> ''"
        "   AND to_email NOT LIKE '%customerdesk.io'"     # = infrastructura Richpanel, nu Google
        " GROUP BY 1 ORDER BY n DESC").fetchall()
    db.close()
    return rows


def resolve_mailboxes(db, want=None, rediscover=False, all_boxes=False):
    """adresă cerută → CUTIE FIZICĂ (`getProfile().emailAddress`).

    ⚠️ 21 de adrese accesibile ≠ 21 de cutii: 4 sunt aliasuri. Fără pasul ăsta am trage
    aceeași cutie de patru ori. Rezultatul se memorează în `gm_state` (probarea celor
    inaccesibile costă ~1s fiecare) — `--rediscover` o reface."""
    pacer = Pacer(rps=10)
    if want:
        groups, bad = {}, []
        for addr in want:
            try:
                p = api(pacer, "users.getProfile", svc(addr).users().getProfile(userId="me"))
                real = (p.get("emailAddress") or addr).lower()
                groups.setdefault(real, {"aliases": set(), "messages_total": p.get("messagesTotal")})
                groups[real]["aliases"].add(addr)
            except Exception as e:
                bad.append((addr, err_str(e)))
        return groups, bad

    # ⚠️ cache-ul e valid DOAR dacă a existat o descoperire COMPLETĂ. Fără marcaj, o rulare
    # anterioară cu `--mailbox X` ar fi lăsat un singur rând în gm_state, iar rularea „pe
    # toate cutiile" ar fi tras tăcut o singură cutie.
    done = db.execute("SELECT 1 FROM gm_state WHERE mailbox=?", (DISCOVERY_ROW,)).fetchone()
    cached = {r["mailbox"]: r for r in db.execute(
        "SELECT * FROM gm_state WHERE mailbox <> ?", (DISCOVERY_ROW,))}
    if cached and done and not rediscover:
        groups = {m: {"aliases": set(json.loads(r["aliases"] or "[]")),
                      "messages_total": r["messages_total"]} for m, r in cached.items()}
    else:
        groups, bad = {}, []
        addrs = cs_addresses()
        log(f"descopăr cutiile: {len(addrs)} adrese de email în Richpanel")
        for addr, n in addrs:
            try:
                p = api(pacer, "users.getProfile", svc(addr).users().getProfile(userId="me"))
                real = (p.get("emailAddress") or addr).lower()
                g = groups.setdefault(real, {"aliases": set(),
                                             "messages_total": p.get("messagesTotal")})
                g["aliases"].add(addr)
            except Exception as e:
                bad.append((addr, err_str(e)))
        log(f"  {len(groups)} cutii FIZICE pentru {len(addrs)} adrese"
            f" · {len(bad)} inaccesibile")
        for addr, msg in bad:
            log(f"    inaccesibilă: {addr} → {msg[:80]}")
        for real, g in groups.items():
            cm.gm_state_set(db, real, messages_total=g["messages_total"],
                            aliases=json.dumps(sorted(g["aliases"])))
        cm.gm_state_set(db, DISCOVERY_ROW, ok=True, aliases=json.dumps([a for a, _ in addrs]),
                        note=f"{len(addrs)} adrese → {len(groups)} cutii · {len(bad)} inaccesibile")
    if not all_boxes:
        for m in list(groups):
            if m in NON_CS:
                log(f"  sar peste {m} — {NON_CS[m]} (folosește --all-boxes ca s-o incluzi)")
                groups.pop(m)
    return groups, []


# ─────────────────────────────────────────────── automat vs uman (marcăm, nu aruncăm)
AUTO_DOMAINS = {
    # recenzii / e-commerce / marketing
    "judge.me": "judge.me", "shopify.com": "shopify", "shopifyemail.com": "shopify",
    "klaviyo.com": "klaviyo", "klaviyomail.com": "klaviyo", "themarketer.com": "marketing",
    "mailchimp.com": "marketing", "sendgrid.net": "marketing", "hellorep.ai": "bot",
    # curieri
    "dpd.ro": "curier", "dpd.com": "curier", "sameday.ro": "curier", "cargus.ro": "curier",
    "urgentcargus.ro": "curier", "fancourier.ro": "curier", "gls-group.eu": "curier",
    "gls-group.com": "curier", "packeta.com": "curier", "packeta.ro": "curier",
    "econt.com": "curier", "speedy.bg": "curier", "dhl.com": "curier", "ppl.cz": "curier",
    "inpost.pl": "curier", "nova-poshta.ua": "curier", "dragonstar.ro": "curier",
    # platforme / furnizori de app-uri / notificări
    "google.com": "platformă", "youtube.com": "platformă", "facebookmail.com": "platformă",
    "mail.instagram.com": "platformă", "instagram.com": "platformă",
    "service.tiktok.com": "platformă", "shop.tiktok.com": "platformă",
    "tiktokglobalshop.com": "platformă", "email.apple.com": "platformă",
    "apple.com": "platformă", "email.samsung.com": "platformă", "metricool.com": "platformă",
    "pinterest.com": "platformă", "trendyol.com": "platformă", "trendyolmail.com": "platformă",
    "payu.com": "platformă", "payu.ro": "platformă", "stripe.com": "platformă",
    "smartbill.ro": "platformă", "romarg.com": "platformă", "tailscale.com": "platformă",
    "anthropic.com": "platformă", "omegatheme.com": "app", "essential-apps.com": "app",
    "powerfulform.com": "app", "secomapp.com": "app", "vitals.co": "app",
    "consentik.com": "app", "easy-sales.com": "app", "gorgias.com": "app",
    "richpanel.com": "helpdesk", "customerdesk.io": "helpdesk",
}
AUTO_LOCALPARTS = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|mailer[-_.]?daemon|postmaster|"
    r"bounce[sd]?|notification[s]?|notify|alert[s]?|automat(ed|ic)?|system|robot|bot|"
    r"newsletter|mailer|noreplay|auto)", re.I)
# subiecte de expediere/curierat și cereri automate de review, RO/EN/CZ/HU/PL/BG
AUTO_SUBJECTS = re.compile(
    r"(shipment\s*-\s*\d{6,}|\bawb\b|\[casenumber:|delivery status notification|"
    r"undelivered mail returned|mail delivery (failed|subsystem)|returned to sender|"
    r"out of office|automatic reply|autoresponder|răspuns automat|raspuns automat|"
    r"how would you rate|cum (ți-|ti-)?a[i]? (părut|parut)|lasă o recenzie|lasa o recenzie|"
    r"review request|verified reviews|invitation to review|"
    r"comanda ta .*(confirmat|expediat)|order #?\d+ (confirmed|placed|shipped)|"
    r"factura .*(smartbill|generat)|your (order|package|parcel) (is|has been))", re.I)
DSN_LOCAL = re.compile(r"^(mailer[-_.]?daemon|postmaster)", re.I)


def classify(from_addr, subject, hdr):
    """(is_automated, motiv). NU aruncăm nimic — 65% din INBOX e automat, iar dacă l-am
    șterge n-am mai putea niciodată demonstra ce era zgomot și ce era client.

    Ordinea contează: semnalele de ANTET (standarde RFC) bat lista de domenii, care e
    mereu incompletă. Domeniile se compară pe sufix de domeniu (`x.google.com` da,
    `gmail.com` NU — e domeniu de consumator, adică exact clientul nostru)."""
    local, _, dom = (from_addr or "").partition("@")
    dom = dom.lower().strip()
    local = local.lower().strip()
    subj = subject or ""

    if DSN_LOCAL.match(local) or "delivery status notification" in subj.lower():
        return 1, "bounce"
    # Expeditorul CUNOSCUT bate semnalele generice de antet: altfel `List-Unsubscribe`
    # înghite provenienta si totul devine „listă" (măsurat: Judge.me raporta 0, desi era
    # cel mai mare emitător automat). Motivul trebuie sa ramana ACTIONABIL, nu doar corect.
    for d, tag in AUTO_DOMAINS.items():
        if dom == d or dom.endswith("." + d):
            return 1, tag
    a = (hdr.get("auto-submitted") or "").strip().lower()
    if a and a != "no":
        return 1, "auto-submitted"
    prec = (hdr.get("precedence") or "").strip().lower()
    if prec in ("bulk", "list", "junk", "auto_reply"):
        return 1, f"precedence:{prec}"
    if hdr.get("list-unsubscribe") or hdr.get("list-id"):
        return 1, "listă"
    if hdr.get("x-autoreply") or hdr.get("x-autorespond"):
        return 1, "autoreply"
    if AUTO_LOCALPARTS.match(local):
        return 1, "noreply"
    if AUTO_SUBJECTS.search(subj):
        return 1, "subiect"
    return 0, ""


# ──────────────────────────────────────────────────────────── extragerea corpului
_STYLE_RE = re.compile(r"(?is)<(script|style|head)[^>]*>.*?</\1>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]{2,}")


def html_to_text(s):
    s = _STYLE_RE.sub(" ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = html.unescape(_TAG_RE.sub(" ", s))
    s = _WS_RE.sub(" ", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _b64(data):
    if not data:
        return b""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _charset(mime_hdrs):
    for h in mime_hdrs or []:
        if h.get("name", "").lower() == "content-type":
            m = re.search(r'charset="?([\w\-]+)"?', h.get("value", ""), re.I)
            if m:
                return m.group(1)
    return "utf-8"


def walk(payload):
    """Părțile MIME în ORDINEA din mesaj (un stack simplu le-ar inversa și ar amesteca
    paragrafele unui corp multipart)."""
    out, stack = [], [payload or {}]
    while stack:
        p = stack.pop(0)
        out.append(p)
        stack = list(p.get("parts") or []) + stack
    return out


def extract_body(payload, max_chars=MAX_BODY):
    """text/plain dacă există, altfel text/html curățat. Atașamentele: DOAR metadate."""
    plain, htm, atts = [], [], []
    for p in walk(payload):
        mime = (p.get("mimeType") or "").lower()
        fn = p.get("filename") or ""
        body = p.get("body") or {}
        if fn:
            atts.append({"filename": fn, "mime": mime, "size_bytes": body.get("size") or 0,
                         "attachment_id": body.get("attachmentId") or ""})
            continue
        data = body.get("data")
        if not data:
            continue
        try:
            raw = _b64(data).decode(_charset(p.get("headers")), errors="replace")
        except Exception:
            raw = _b64(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            plain.append(raw)
        elif mime == "text/html":
            htm.append(raw)
    text = "\n".join(plain).strip() or html_to_text("\n".join(htm))
    text = re.sub(r"\n{3,}", "\n\n", (text or "").replace("\r\n", "\n")).strip()
    return text[:max_chars], atts


def norm_mid(mid):
    """Cheia de legătură cu Richpanel = Message-ID-ul RFC822 CU parantezele unghiulare
    (măsurat: `rp_ticket.id` E Message-ID-ul pe 99,74% din emailuri)."""
    m = (mid or "").strip()
    if not m:
        return ""
    m = m.split()[0] if " " in m else m
    if not m.startswith("<"):
        m = "<" + m
    if not m.endswith(">"):
        m = m + ">"
    return m


def match_key(mid):
    """Pentru COMPARAȚIE (nu pentru stocare): fără paranteze, litere mici. Un „gap" fals
    e greșeala scumpă aici — nu vrem să raportăm pierderi din cauza unei majuscule."""
    return (mid or "").strip().strip("<>").strip().lower()


def to_iso(internal_date_ms, date_hdr):
    if internal_date_ms:
        try:
            return datetime.datetime.fromtimestamp(
                int(internal_date_ms) / 1000.0, datetime.timezone.utc).isoformat()
        except Exception:
            pass
    try:
        d = email.utils.parsedate_to_datetime(date_hdr)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.astimezone(datetime.timezone.utc).isoformat()
    except Exception:
        return None


def addr_of(v):
    a = email.utils.parseaddr(v or "")[1]
    return a.lower().strip()


HDRS = ["Message-ID", "From", "To", "Cc", "Subject", "Date", "In-Reply-To", "References",
        "Delivered-To", "List-Id", "List-Unsubscribe", "Precedence", "Auto-Submitted",
        "Return-Path", "Reply-To", "X-Autoreply", "X-Autorespond"]


def to_row(m, mailbox, aliases):
    """Mesaj Gmail brut → rândul de oglindă. Fără atașamente descărcate."""
    hdr = {h["name"].lower(): h["value"]
           for h in (m.get("payload") or {}).get("headers", [])}
    labels = m.get("labelIds") or []
    mid = norm_mid(hdr.get("message-id"))
    if not mid:
        # fără Message-ID nu există legătură cu Richpanel, dar mesajul NU se pierde:
        # cheie stabilă din cutie + id-ul Gmail (marcat, ca să nu pară RFC822)
        mid = f"<gmail-{mailbox}-{m.get('id')}@no-message-id>"
    frm = addr_of(hdr.get("from"))
    to = addr_of(hdr.get("to")) or addr_of(hdr.get("delivered-to"))
    subject = (hdr.get("subject") or "").strip()
    body, atts = extract_body(m.get("payload"))
    is_auto, why = classify(frm, subject, hdr)
    direction = "out" if ("SENT" in labels or frm in aliases) else "in"
    return {
        "message_id": mid, "mailbox": mailbox, "gmail_id": m.get("id"),
        "thread_id": m.get("threadId"), "direction": direction,
        "from_addr": frm, "to_addr": to, "subject": subject,
        "date_utc": to_iso(m.get("internalDate"), hdr.get("date")),
        "in_reply_to": norm_mid(hdr.get("in-reply-to")),
        "refs": (hdr.get("references") or "")[:2000],
        "is_automated": is_auto, "auto_reason": why,
        "body_text": body, "body_len": len(body),
        "has_attachments": 1 if atts else 0, "labels": ",".join(labels),
    }, atts


# ────────────────────────────────────────────────────────── enumerarea mesajelor
def list_by_query(pacer, s, q, labels):
    ids, calls = set(), 0
    for lab in labels:
        tok = None
        while True:
            kw = dict(userId="me", maxResults=500, labelIds=[lab])
            if q:
                kw["q"] = q
            if tok:
                kw["pageToken"] = tok
            r = api(pacer, "users.messages.list", s.users().messages().list(**kw)) or {}
            calls += 1
            ids.update(x["id"] for x in r.get("messages", []))
            tok = r.get("nextPageToken")
            if not tok:
                break
    return ids, calls


def list_by_history(pacer, s, start_history_id):
    """Incremental REAL: doar ce s-a ADĂUGAT de la ultimul cursor.

    Întoarce None dacă istoricul e prea vechi (404) — Gmail păstrează history-ul limitat,
    iar atunci singurul lucru corect e să cădem pe interogarea după dată. Nu inventăm
    „n-a fost nimic nou"."""
    ids, tok, calls = set(), None, 0
    while True:
        kw = dict(userId="me", startHistoryId=str(start_history_id),
                  historyTypes=["messageAdded"], maxResults=500)
        if tok:
            kw["pageToken"] = tok
        try:
            r = api(pacer, "users.history.list", s.users().history().list(**kw)) or {}
        except HttpError as e:
            if e.resp.status == 404:
                return None, calls
            raise
        calls += 1
        for h in r.get("history", []):
            for ma in h.get("messagesAdded", []):
                msg = ma.get("message") or {}
                lab = msg.get("labelIds") or []
                if "DRAFT" in lab or "CHAT" in lab:
                    continue
                if msg.get("id"):
                    ids.add(msg["id"])
        tok = r.get("nextPageToken")
        if not tok:
            break
    return ids, calls


# ──────────────────────────────────────────────────────────────── captarea unei cutii
def sync_mailbox(db, mailbox, aliases, a, st):
    pacer = Pacer(rps=a.rps)
    s = svc(mailbox)
    labels = [x.strip().upper() for x in a.labels.split(",") if x.strip()]
    prof = api(pacer, "users.getProfile", s.users().getProfile(userId="me")) or {}
    # cursorul se ia ÎNAINTE de enumerare: ce apare în timpul rulării intră data viitoare,
    # nu se pierde. (Invers — luat la final — ar sări peste tot ce a venit între timp.)
    head_history = prof.get("historyId")
    state = cm.gm_state_get(db, mailbox)

    ids, mode, calls = None, "", 0
    if state.get("history_id") and not a.full and not (a.since or a.until):
        ids, calls = list_by_history(pacer, s, state["history_id"])
        if ids is None:
            log(f"  {mailbox}: historyId {state['history_id']} e prea vechi (404) → cad pe dată")
            st["history_expired"] += 1
        else:
            mode = f"history de la {state['history_id']}"
    if ids is None:
        q = build_query(a)
        ids, c2 = list_by_query(pacer, s, q, labels)
        calls += c2
        mode = f"query '{q}' pe {'+'.join(labels)}"
    log(f"  {mailbox}: {len(ids)} mesaje ({mode})")

    ids = sorted(ids)
    fetched_at = cm.now_iso()
    n_err = 0
    for start in range(0, len(ids), GET_BATCH):
        chunk = ids[start:start + GET_BATCH]

        def one(mid):
            try:
                # ⚠️ googleapiclient NU e thread-safe: fiecare FIR își ia propriul service
                # (`svc` ține un cache în threading.local), altfel răspunsurile se amestecă.
                th = svc(mailbox)
                return api(pacer, "users.messages.get",
                           th.users().messages().get(userId="me", id=mid, format="full"),
                           none_on_404=True), None
            except Exception as e:
                return None, err_str(e)

        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            results = list(ex.map(one, chunk))
        for m, err in results:
            if err:
                n_err += 1
                st["errors"] += 1
                if st["errors"] <= 5:
                    log(f"    ! {err}")
                continue
            if m is None:
                st["vanished"] += 1
                continue
            lab = m.get("labelIds") or []
            if "DRAFT" in lab or "CHAT" in lab:
                st["skipped_draft"] += 1
                continue
            row, atts = to_row(m, mailbox, aliases)
            verdict = cm.upsert_gm_message(db, row, fetched_at=fetched_at)
            st[verdict] += 1
            st["messages"] += 1
            st["auto" if row["is_automated"] else "human"] += 1
            st[row["direction"]] += 1
            if "SPAM" in lab:
                st["spam"] += 1
            if atts:
                st["attachments"] += cm.upsert_gm_attachments(db, row["message_id"], atts,
                                                              fetched_at)
        db.commit()
        if len(ids) > GET_BATCH:
            log(f"    …{min(start + GET_BATCH, len(ids))}/{len(ids)}")

    st["api_calls"] += pacer.stats()["calls"]
    st["http_429"] += pacer.stats()["http_429"]
    # cursorul avansează DOAR dacă trecerea a mers: altfel am sări definitiv peste ce n-am tras
    cm.gm_state_set(db, mailbox, history_id=head_history if n_err == 0 else None,
                    messages_total=prof.get("messagesTotal"),
                    aliases=json.dumps(sorted(aliases)), ok=(n_err == 0),
                    note=f"{mode} · {len(ids)} id · {n_err} erori")
    return len(ids)


def build_query(a):
    if a.since or a.until:
        parts = []
        if a.since:
            parts.append("after:" + a.since.replace("-", "/"))
        if a.until:
            parts.append("before:" + a.until.replace("-", "/"))
        return " ".join(parts)
    return f"newer_than:{a.recent}d"


def sync(db, a):
    groups, _ = resolve_mailboxes(db, want=a.mailbox, rediscover=a.rediscover,
                                  all_boxes=a.all_boxes)
    if not groups:
        log("nicio cutie de captat")
        return {}
    st = collections.Counter()
    t0 = time.time()
    log(f"captare din {len(groups)} cutii · {build_query(a)} · ținta {a.rps}/s/cutie")
    with cm.sync_run(db, "gmail", note=build_query(a)) as run:
        for mailbox in sorted(groups):
            aliases = set(groups[mailbox]["aliases"]) | {mailbox}
            try:
                n = sync_mailbox(db, mailbox, aliases, a, st)
                st["mailboxes"] += 1
                run.items += n
            except Exception as e:
                st["errors"] += 1
                run.errors += 1
                log(f"  {mailbox}: EȘEC {err_str(e)}")
        st["elapsed_s"] = round(time.time() - t0, 1)
        run.note = build_query(a) + " | " + json.dumps(
            {k: st[k] for k in ("mailboxes", "messages", "auto", "human", "in", "out",
                                "errors", "elapsed_s")})
    return st


def report(st):
    if not st:
        return
    el = st.get("elapsed_s") or 0.0
    m = st["messages"]
    print("\n" + "=" * 72)
    print(f"cutii captate       {st['mailboxes']}")
    print(f"mesaje procesate    {m}   (noi {st['inserted']} · îmbogățite {st['updated']}"
          f" · neschimbate {st['unchanged']} · ÎNGHEȚATE {st['frozen']})")
    print(f"  AUTOMATE          {st['auto']}" + (f"  ({100.0 * st['auto'] / m:.1f}%)" if m else ""))
    print(f"  umane             {st['human']}" + (f"  ({100.0 * st['human'] / m:.1f}%)" if m else ""))
    print(f"  inbound / outbound {st['in']} / {st['out']}")
    print(f"  din care în SPAM  {st['spam']}")
    print(f"atașamente (doar metadate) {st['attachments']}")
    print(f"apeluri API         {st['api_calls']}   HTTP 429: {st['http_429']}")
    if st["history_expired"]:
        print(f"⚠️ historyId expirat pe {st['history_expired']} cutii → au căzut pe dată")
    if st["vanished"] or st["skipped_draft"]:
        print(f"sărite: {st['vanished']} dispărute (404) · {st['skipped_draft']} draft/chat")
    print(f"erori               {st['errors']}")
    print(f"durată              {el}s"
          + (f"   ({m / el:.1f} mesaje/s)" if el > 0 and m else ""))


# ───────────────────────────────────────────────────────────────────── reconcile
def rp_index(db, use_legacy=True):
    """Tot ce ȘTIE Richpanel, ca set de chei de comparație + acoperirea pe ZILE.

    Două niveluri de acoperire, fiindcă dovezile sunt de două calități:
      • cs_mirror (rp_ticket + rp_message) = id-uri PER MESAJ → absența e DOVADĂ.
      • richpanel_tickets.db (export vechi) = doar id-ul CONVERSAȚIEI → un mesaj din
        interiorul unui fir n-are cum să apară acolo, deci absența e doar PROBABILĂ.
    Fără distincția asta am raporta „pierderi" care sunt de fapt limitări ale indexului."""
    keys, days_full, days_legacy = set(), set(), set()
    for (v,) in db.execute("SELECT id FROM rp_ticket"):
        keys.add(match_key(v))
    for (v,) in db.execute("SELECT msg_id FROM rp_message"):
        keys.add(match_key(v))
    for (d, n) in db.execute(
            "SELECT substr(created_at,1,10) d, COUNT(*) FROM rp_ticket"
            " WHERE channel LIKE 'email%' AND created_at IS NOT NULL GROUP BY 1"):
        days_full.add(d)
    n_legacy = 0
    if use_legacy and os.path.exists(RP_LEGACY_DB):
        ldb = sqlite3.connect(f"file:{RP_LEGACY_DB}?mode=ro", uri=True)
        for (v, d) in ldb.execute("SELECT id, substr(created_at,1,10) FROM tickets"
                                  " WHERE channel LIKE 'email%'"):
            keys.add(match_key(v))
            if d:
                days_legacy.add(d)
            n_legacy += 1
        ldb.close()
    keys.discard("")
    return keys, days_full, days_legacy, n_legacy


def reconcile(db, a):
    keys, days_full, days_legacy, n_legacy = rp_index(db, use_legacy=not a.no_legacy)
    log(f"index Richpanel: {len(keys)} Message-ID"
        f" (oglindă {len(days_full)} zile per-mesaj · export vechi {n_legacy} tichete,"
        f" {len(days_legacy)} zile doar per-conversație)")

    where, args = ["date_utc IS NOT NULL"], []
    if a.mailbox:
        where.append("mailbox IN (%s)" % ",".join("?" * len(a.mailbox)))
        args += list(a.mailbox)
    if a.since or a.until:          # aceeași fereastră ca la captare, ca să nu existe două reguli
        if a.since:
            where.append("substr(date_utc,1,10) >= ?")
            args.append(a.since)
        if a.until:
            where.append("substr(date_utc,1,10) < ?")     # EXCLUSIV, ca `before:` din Gmail
            args.append(a.until)
    elif a.days:
        cut = (datetime.date.today() - datetime.timedelta(days=a.days - 1)).isoformat()
        where.append("substr(date_utc,1,10) >= ?")
        args.append(cut)
    rows = db.execute("SELECT message_id, mailbox, subject, from_addr, direction,"
                      " is_automated, substr(date_utc,1,10) d FROM gm_message"
                      " WHERE " + " AND ".join(where), args).fetchall()
    st = collections.Counter()
    per_day = collections.defaultdict(collections.Counter)
    with cm.sync_run(db, "gmail_reconcile", note=f"days={a.days}") as run:
        for r in rows:
            st["checked"] += 1
            d = r["d"]
            per_day[d]["gmail"] += 1
            if match_key(r["message_id"]) in keys:
                st["in_rp"] += 1
                per_day[d]["in_rp"] += 1
                db.execute("DELETE FROM gm_gap WHERE message_id=?", (r["message_id"],))
                continue
            if d in days_full:
                reason = "lipsa_in_rp"
            elif d in days_legacy:
                reason = "lipsa_in_rp_probabil"
            else:
                st["rp_necaptat"] += 1        # ziua n-a fost trasă din RP ⇒ NU e o pierdere
                per_day[d]["necaptat"] += 1
                continue
            cm.record_gap(db, r["message_id"], d, r["mailbox"], r["subject"],
                          r["from_addr"], reason)
            st[reason] += 1
            st["gap_" + r["direction"]] += 1
            st["gap_auto" if r["is_automated"] else "gap_human"] += 1
            per_day[d]["gap"] += 1
            run.items += 1
        db.commit()
        run.note = json.dumps(dict(st))

    print("\n" + "=" * 72)
    print(f"verificate           {st['checked']} emailuri din oglinda Gmail")
    print(f"  găsite în Richpanel {st['in_rp']}"
          + (f"  ({100.0 * st['in_rp'] / st['checked']:.1f}%)" if st["checked"] else ""))
    print(f"  LIPSĂ, dovedit      {st['lipsa_in_rp']}   (zile în care oglinda RP are"
          f" id-uri PER MESAJ)")
    print(f"  lipsă, probabil     {st['lipsa_in_rp_probabil']}   (zile acoperite doar de"
          f" exportul vechi, cu id-uri de conversație)")
    print(f"  nejudecabile        {st['rp_necaptat']}   (zile fără NICIO acoperire RP —"
          f" nu e pierdere, e necaptat)")
    print(f"\nDin cele {st['lipsa_in_rp'] + st['lipsa_in_rp_probabil']} lipsă:")
    print(f"  umane {st['gap_human']} · automate {st['gap_auto']}")
    print(f"  inbound {st['gap_in']} · OUTBOUND {st['gap_out']}"
          f"   ← outbound lipsă = răspuns trimis din Gmail, pe lângă helpdesk")
    if per_day:
        print("\n  zi          gmail   în_RP   gap  necaptat")
        for d in sorted(per_day)[-14:]:
            c = per_day[d]
            print(f"  {d}  {c['gmail']:>6} {c['in_rp']:>7} {c['gap']:>5} {c['necaptat']:>9}")
    top = db.execute("SELECT mailbox, COUNT(*) n FROM gm_gap GROUP BY 1 ORDER BY n DESC"
                     " LIMIT 10").fetchall()
    if top:
        print("\n  gap-uri pe cutie:")
        for r in top:
            print(f"    {r['mailbox']:<30} {r['n']:>6}")
    return st


# ─────────────────────────────────────────────────────────────────────── stats
def stats_cmd(db):
    n = db.execute("SELECT COUNT(*) FROM gm_message").fetchone()[0]
    if not n:
        print("oglinda Gmail e goală — rulează `uv run gmail_sync.py --recent 1`")
        return
    lo, hi = db.execute("SELECT MIN(substr(date_utc,1,10)), MAX(substr(date_utc,1,10))"
                        " FROM gm_message").fetchone()
    na = db.execute("SELECT COUNT(*) FROM gm_message WHERE is_automated=1").fetchone()[0]
    print(f"oglinda Gmail: {n} emailuri · {lo} → {hi}")
    print(f"  automate {na} ({100.0 * na / n:.1f}%) · umane {n - na} ({100.0 * (n - na) / n:.1f}%)")
    for lbl, cond in (("inbound", "direction='in'"), ("outbound", "direction='out'"),
                      ("în SPAM", "labels LIKE '%SPAM%'"), ("cu atașamente",
                                                            "has_attachments=1"),
                      ("corp GOL", "body_len=0")):
        c = db.execute("SELECT COUNT(*) FROM gm_message WHERE " + cond).fetchone()[0]
        print(f"    {lbl:<14} {c:>7}  ({100.0 * c / n:.1f}%)")
    print(f"  atașamente (metadate): "
          f"{db.execute('SELECT COUNT(*) FROM gm_attachment').fetchone()[0]}")
    print("\n  cutie                          total   automat    uman   in    out   ultimul")
    for r in db.execute(
            "SELECT mailbox, COUNT(*) n, SUM(is_automated) a,"
            " SUM(direction='in') i, SUM(direction='out') o, MAX(date_utc) last"
            " FROM gm_message GROUP BY 1 ORDER BY n DESC"):
        print(f"    {r['mailbox']:<28} {r['n']:>6} {r['a']:>9} {r['n'] - r['a']:>7}"
              f" {r['i']:>5} {r['o']:>6}   {(r['last'] or '')[:10]}")
    print("\n  de ce automat (top):")
    for r in db.execute("SELECT auto_reason, COUNT(*) n FROM gm_message WHERE is_automated=1"
                        " GROUP BY 1 ORDER BY n DESC LIMIT 12"):
        print(f"    {r['auto_reason'] or '?':<20} {r['n']:>6}")
    g = db.execute("SELECT COUNT(*) FROM gm_gap").fetchone()[0]
    if g:
        print(f"\n  gm_gap: {g} emailuri în cutie dar NU în Richpanel")
        for r in db.execute("SELECT reason, COUNT(*) n FROM gm_gap GROUP BY 1 ORDER BY n DESC"):
            print(f"    {r['reason']:<24} {r['n']:>6}")
    print("\n  starea incrementalului (historyId per cutie):")
    for r in db.execute("SELECT * FROM gm_state WHERE mailbox <> ? ORDER BY mailbox",
                        (DISCOVERY_ROW,)):
        hid = r["history_id"] or "-"
        print(f"    {r['mailbox']:<28} history={str(hid):<12} ok={(r['last_ok_at'] or '-')[:16]}"
              f"  {(r['note'] or '')[:44]}")
    print("\n  ultimele rulări:")
    for r in db.execute("SELECT job,started_at,ok,items,errors,note FROM sync_run"
                        " WHERE job LIKE 'gmail%' ORDER BY started_at DESC LIMIT 5"):
        print(f"    {r['started_at']}  {r['job']:<17} ok={r['ok']} items={r['items']}"
              f" err={r['errors']}  {(r['note'] or '')[:70]}")


def main():
    ap = argparse.ArgumentParser(
        description="Captarea cutiilor CS din Gmail în oglinda CS (READ-ONLY, gmail.readonly).")
    ap.add_argument("--recent", type=int, default=1, help="ultimele N zile (implicit 1)")
    ap.add_argument("--since", help="fereastră fixă: prima zi (YYYY-MM-DD)")
    ap.add_argument("--until", help="fereastră fixă: ultima zi EXCLUSIV (YYYY-MM-DD)")
    ap.add_argument("--mailbox", action="append",
                    help="doar cutia asta (se poate repeta); implicit toate cutiile CS")
    ap.add_argument("--labels", default=LABELS_DEFAULT,
                    help=f"etichete de parcurs (implicit {LABELS_DEFAULT}; TRASH e 0 peste tot)")
    ap.add_argument("--full", action="store_true",
                    help="ignoră historyId și refă fereastra după dată")
    ap.add_argument("--rediscover", action="store_true",
                    help="re-descoperă cutiile fizice (getProfile pe toate adresele)")
    ap.add_argument("--all-boxes", action="store_true",
                    help="include și cutiile NON-CS (rossinails=curier, facturi=facturier)")
    ap.add_argument("--rps", type=float, default=20.0,
                    help="apeluri/secundă per cutie (implicit 20 = 100 din 250 unități/s)")
    ap.add_argument("--workers", type=int, default=4, help="fire de tras per cutie")
    ap.add_argument("--reconcile", action="store_true",
                    help="ce e în cutie dar NU în Richpanel → gm_gap")
    ap.add_argument("--days", type=int, default=7,
                    help="fereastra pentru --reconcile (ignorată dacă dai --since/--until)")
    ap.add_argument("--no-legacy", action="store_true",
                    help="nu folosi richpanel_tickets.db ca index suplimentar")
    ap.add_argument("--stats", action="store_true", help="doar raportează oglinda Gmail")
    ap.add_argument("--db", default=cm.DB_DEFAULT)
    a = ap.parse_args()
    assert_scopes()

    db = cm.open_db(a.db)
    try:
        if a.stats:
            stats_cmd(db)
        elif a.reconcile:
            reconcile(db, a)
        else:
            report(sync(db, a))
    finally:
        db.close()


if __name__ == "__main__":
    main()
