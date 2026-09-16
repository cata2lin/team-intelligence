# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000"]
# ///
"""
cs_photo.py — modulul CANONIC de „vedere" a pozelor pentru un tichet Richpanel.
Folosit ca CLI ȘI importat de alte scripturi (ex. cs-draft-reply/cs_auto_draft.py) ca să vadă pozele.

Vede DOUĂ tipuri de poze, ambele pentru CONTEXT:
  (a) poza pe care o LASĂ CLIENTUL (atașament în mesaj) — defect/dovadă livrare/etichetă/screenshot;
      MCP-ul taie bytes-ii inline dar dă URL-ul (bucket public S3 richpanel-data) → descarcă + descrie vizual.
  (b) poza RECLAMEI/POSTĂRII pe care comentează clientul (tichete FB/IG comment) — ce PRODUS e în reclamă;
      fără token de pagină: HTTP GET cu UA `facebookexternalhit/1.1` → og:image → descarcă + descrie vizual.

REGISTRU: fiecare postare descrisă o dată se SALVEAZĂ (post_id → produs/magazin/poză) într-un SQLite;
când apare un comentariu pe o postare NOUĂ, se completează; pe una știută, se refolosește (fără re-cost).

CLI:
  uv run cs_photo.py --conv 277664                 # poze client + (dacă e comentariu) reclama
  uv run cs_photo.py --conv 277744 --json
  uv run cs_photo.py --conv 277664 --save ./poze
  uv run cs_photo.py --conv 274972 --no-describe
  uv run cs_photo.py --registry-list               # ce postări avem salvate
  uv run cs_photo.py --registry-build --scan 200    # populează registrul din comentariile recente (incremental)
  uv run cs_photo.py --catalog-build                # instantaneu de catalog din Shopify (piețe străine)
  uv run cs_photo.py --catalog-list                 # ce branduri are instantaneul + monedă + vechime

Necesită: RICHPANEL_MCP_TOKEN (atașamente + listă), OPENAI_API_KEY (descriere vizuală).
Registru: env FB_POST_DB (default lângă script); pune-l pe o cale partajată (NAS) pt registru de echipă.
NU scrie nimic în Richpanel (read-only).
"""
import shutil
import os, json, base64, sqlite3, datetime, re, time, unicodedata, urllib.request, urllib.parse, urllib.error, subprocess, argparse, sys

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")
COMMENT_CHANNELS = ("facebook_feed_comment", "instagram_comment", "facebook_comment", "instagram_feed_comment")
FB_CRAWLER_UA = "facebookexternalhit/1.1"
FB_POST_DB = os.environ.get("FB_POST_DB") or os.path.join(HERE, "fb_post_registry.sqlite")

# Magazine MONO-PRODUS: produsul e CUNOSCUT din brand (parfumuri etc.) → NU mai rezolvăm reclama (zero LLM).
# Rezolvarea reclamei contează la DEALS (Grandia/Casa Ofertelor/Magdeal…), unde reclama poate fi orice produs.
# Configurabil prin env AD_SKIP_BRANDS (listă separată prin virgulă, suprascrie default-ul).
# NB: Gento NU e aici — vinde genți unde diferă culorile/modelele → reclama (poza) chiar ajută → keep.
MONO_PRODUCT_BRANDS = [b.strip() for b in os.environ.get(
    "AD_SKIP_BRANDS",
    "esteban,george talent,gt parfumuri,nubra,belasil,lab noir,labnoir").split(",") if b.strip()]


# Literele pe care NFKD NU le descompune (n-au accent COMBINABIL, litera e alta): fără ele
# „protišmykových" se pliază, dar „łatwy"/„đak" nu, iar pe piața poloneză jumătate din cuvinte
# rămâneau nepliate — adică nepotrivibile cu titlul din catalog.
_DEACC_EXTRA = {"ł": "l", "đ": "d", "ø": "o", "ı": "i", "ß": "s", "æ": "a", "œ": "o", "ð": "d", "þ": "t"}


def _deacc(s):
    t = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()
    return "".join(_DEACC_EXTRA.get(c, c) for c in t)


# ACEEAȘI pliere, dar exprimată ca pereche pt `translate()` în SQL — ca titlul din Postgres și
# cuvântul-cheie din Python să ajungă în EXACT aceeași formă. Tabelul vechi avea DOAR diacriticele
# ROMÂNEȘTI („ăâîșțşţ"), deci pe piețele străine cele două părți se comparau în alfabete diferite:
# „protišmykových" (titlu) vs „protismykovych" (cuvânt) → zero potriviri, pe un catalog care EXISTĂ.
# ⚠️ Chirilica intră și ea: NFKD desface „й" în „и"+breve, deci fără perechea й→и partea SQL ar fi
# rămas cu „й" iar cea Python cu „и".
_FOLD_SRC = ("ăâîșțşţáàäãåçčćďéèêëěęğíìïĺľńñňóòôöõőŕřśšťúùûüůűýÿźżž"
             "ąėįųāēīōūşțğıœæðþłđøйѝ")
_FOLD_DST = "".join((_deacc(c) or c)[:1] for c in _FOLD_SRC)
assert len(_FOLD_SRC) == len(_FOLD_DST), "tabelul de pliere trebuie să aibă aceeași lungime pe ambele părți"


def is_mono_product(name):
    """True dacă numele magazinului/paginii e un brand mono-produs (skip rezolvare reclamă)."""
    n = _deacc(name)
    return any(b and _deacc(b) in n for b in MONO_PRODUCT_BRANDS)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enc_url(u):
    """Percent-encode path/query (pozele WhatsApp au spații în nume → urllib crapă pe URL neîncodat)."""
    p = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), urllib.parse.quote(p.query, safe="=&%"), p.fragment))


def _uv():
    """Calea ABSOLUTĂ către `uv` — vezi cs_auto_draft.py: un cron pornește cu PATH minimal, unde
    `uv` (instalat în ~/.local/bin) nu există, iar `secret()` întoarce tăcut gol."""
    c = os.environ.get("UV_BIN") or shutil.which("uv")
    if c:
        return c
    for p in (os.path.expanduser("~/.local/bin/uv"), "/usr/local/bin/uv", "/opt/homebrew/bin/uv",
              "/root/.local/bin/uv", "/usr/bin/uv"):
        if os.path.exists(p):
            return p
    return "uv"


UV = _uv()


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    try:
        return subprocess.run([UV, "run", KB, "secret-get", k], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "cs-photo", "version": "1"}}})

    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        b = urllib.request.urlopen(urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h), timeout=60).read().decode()
        ln = [l for l in b.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(b)

    def call(self, name, args):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        txt = r["result"]["content"][0]["text"]
        try:
            return json.loads(txt)
        except Exception:
            return {"_text": txt}


# ───────────────────────── VEDERE VIZUALĂ (model multimodal) ─────────────────────────
SYS_CLIENT = ("Ești asistent CS ARONA. Descrie pe SCURT (1-2 fraze, factual, în română) ce arată poza trimisă de client într-un tichet: "
              "produs defect/spart/deteriorat (zi exact ce e rupt/lipsă), dovadă de livrare (AWB, SMS/email curier, ce status), etichetă/colet, "
              "captură de ecran (ce text/aplicație). Dacă e relevant pentru o reclamație (defect/retur/livrare), spune clar ce DOVEDEȘTE.")
SYS_AD = ("Ești asistent CS ARONA. Aceasta e POZA RECLAMEI/POSTĂRII pe care comentează un client. Descrie pe SCURT (1 frază, în română) "
          "ce PRODUS se promovează: ce e (categorie + obiect concret), caracteristici vizibile, și orice ofertă/preț scris în imagine. "
          "Fără speculații — doar ce se vede. Scopul: să știm la ce produs se referă comentariul.")


def _post_json(url, body, headers, tries=4):
    """POST JSON cu retry+backoff pe 429/5xx + timeout/URLError (rate-limit LLM)."""
    data = json.dumps(body).encode()
    delay = 2
    for k in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=90).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and k < tries - 1:
                time.sleep(delay); delay *= 2; continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if k < tries - 1:
                time.sleep(delay); delay *= 2; continue
            raise


def vision(img_bytes, ctype, ctx, system=SYS_CLIENT):
    b64 = base64.b64encode(img_bytes).decode()
    ok = secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("VISION_MODEL", "gpt-4o-mini"), "temperature": 0, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": "Context: " + (ctx or "—")},
                {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (ctype or "image/jpeg", b64)}}]}]}
        try:
            return _post_json("https://api.openai.com/v1/chat/completions", body,
                              {"Authorization": "Bearer " + ok, "content-type": "application/json"})["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return "(eroare descriere: %s)" % str(e)[:80]
    ak = secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 300, "system": system,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Context: " + (ctx or "—")},
                    {"type": "image", "source": {"type": "base64", "media_type": ctype, "data": b64}}]}]}
        try:
            return _post_json("https://api.anthropic.com/v1/messages", body,
                              {"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})["content"][0]["text"].strip()
        except Exception as e:
            return "(eroare descriere: %s)" % str(e)[:80]
    return "(fără cheie LLM — nu pot descrie)"


# ───────────────────────── (a) POZELE CLIENTULUI (atașamente) ─────────────────────────
def client_photos(msgs, ctx, max_imgs=4, min_bytes=12000, include_agent=False, save_dir=None, describe_imgs=True, conv=""):
    """Pozele trimise de client (sau agent dacă include_agent). Dedup pe nume + skip imagini mici (logo/semnătură).
    Întoarce listă de dict: {who,name,url,bytes?,saved?,desc?,error?}."""
    items, seen = [], set()
    for m in msgs:
        who = "AGENT" if m.get("author_is_workspace_agent") else ("AI" if m.get("is_ai") else "CLIENT")
        for at in (m.get("attachments") or []):
            u = at.get("url") or at.get("href") or at.get("downloadUrl") or ""
            base = u.lower().split("?")[0]
            if not u or not base.endswith(IMG_EXT):
                continue
            key = base.split("/")[-1]
            if key in seen:
                continue
            seen.add(key)
            items.append({"who": who, "url": u, "name": urllib.parse.unquote(u.split("/")[-1].split("?")[0])})
    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
    out, n_described = [], 0
    for at in items:
        try:
            data = urllib.request.urlopen(enc_url(at["url"]), timeout=60).read()
        except Exception as e:
            at["error"] = "download: %s" % str(e)[:80]
            out.append(at)
            continue
        at["bytes"] = len(data)
        if save_dir:
            p = os.path.join(save_dir, "%s_%s" % (conv or "img", at["name"]))
            open(p, "wb").write(data)
            at["saved"] = p
        if describe_imgs and (include_agent or at["who"] == "CLIENT") and at["bytes"] >= min_bytes and n_described < max_imgs:
            ext = (os.path.splitext(at["name"])[1] or ".jpg").lower()
            ctype = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
            at["desc"] = vision(data, ctype, ctx, SYS_CLIENT)
            n_described += 1
        out.append(at)
    return out


def client_photos_block(msgs, ctx, **kw):
    """Bloc text pt context (sau '') — pozele clientului, descrise."""
    descr = [o for o in client_photos(msgs, ctx, **kw) if o.get("desc")]
    if not descr:
        return ""
    lines = ["  [%d] %s" % (i + 1, o["desc"]) for i, o in enumerate(descr)]
    return ("POZE TRIMISE DE CLIENT (conținutul REAL al imaginilor — le-am VĂZUT; folosește-le ca dovadă, NU intră sub anti-halucinare; "
            "NU cere altă poză dacă clientul a trimis deja):\n" + "\n".join(lines))


# ───────────────────────── (b) RECLAMA/POSTAREA de la comentariu ─────────────────────────
def extract_fb_post(ticket):
    """(page_id_candidates, post_id) dintr-un tichet FB/IG comment. Structura id = {page}_{post}_{post}_{comment} → post=segs[-2]."""
    cid = str(ticket.get("id") or "")
    segs = cid.split("_")
    if len(segs) < 2:
        return [], ""
    post_id = segs[-2]
    to = ticket.get("to") or {}
    page_to = (to.get("id") if isinstance(to, dict) else "") or ""
    cands = [c for c in (page_to, segs[0]) if c]
    # dedup păstrând ordinea
    pages = list(dict.fromkeys(cands))
    return pages, post_id


def _extract_copy(html):
    """COPY-ul (textul) postării fără token: din slug-ul og:url (FB pune începutul mesajului acolo), fallback <title>."""
    m = re.search(r'og:url"\s+content="([^"]*)"', html)
    if m:
        mm = re.search(r'/posts/(.+?)/\d+/?$', m.group(1))
        if mm:
            slug = " ".join(urllib.parse.unquote(mm.group(1)).strip().lstrip("-").replace("-", " ").split())
            if len(slug) >= 8:
                return slug[:220]
    m = re.search(r'<title[^>]*>([^<]+)</title>', html)
    if m:
        return " ".join(m.group(1).split())[:220]
    return ""


def fb_post_og(page_ids, post_id):
    """og:image/og:title + COPY-ul postării, fără token (UA crawler). Întoarce {} dacă nu merge."""
    if not post_id:
        return {}
    for page in page_ids or [""]:
        url = ("https://www.facebook.com/%s/posts/%s/" % (page, post_id)) if page else ("https://www.facebook.com/%s/" % post_id)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": FB_CRAWLER_UA})
            html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")
        except Exception:
            continue

        def og(prop):
            m = re.search(r'og:%s"\s+content="([^"]*)"' % prop, html)
            return (m.group(1).replace("&amp;", "&").strip() if m else "")
        img, title, copy = og("image"), og("title"), _extract_copy(html)
        if img or copy:
            return {"url": url, "image": img, "title": title, "desc": og("description"), "copy": copy, "page_id": page}
    return {}


SYS_COPY = ("Ești asistent CS ARONA. Din TEXTUL (copy-ul) unei reclame, spune în 1 frază scurtă (română) ce PRODUS se promovează "
            "(obiect concret + categorie) și orice ofertă/preț menționat. Doar din text — nu inventa. "
            "Dacă textul nu spune clar produsul, scrie: (produs neclar din copy).")


def text_product(copy, store=""):
    """Identifică produsul DOAR din copy-ul (textul) reclamei — fallback când nu reușim din poză."""
    if not copy:
        return ""
    msg = "Magazin: %s. Copy reclamă: %s" % (store or "?", copy)
    ok = secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("TEXT_MODEL", "gpt-4o-mini"), "temperature": 0,
                "messages": [{"role": "system", "content": SYS_COPY}, {"role": "user", "content": msg}]}
        try:
            return _post_json("https://api.openai.com/v1/chat/completions", body,
                              {"Authorization": "Bearer " + ok, "content-type": "application/json"})["choices"][0]["message"]["content"].strip()
        except Exception:
            return ""
    ak = secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"), "max_tokens": 150, "system": SYS_COPY,
                "messages": [{"role": "user", "content": msg}]}
        try:
            return _post_json("https://api.anthropic.com/v1/messages", body,
                              {"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})["content"][0]["text"].strip()
        except Exception:
            return ""
    return ""


# ── registru SQLite (post_id → ce e) ──
def _reg():
    c = sqlite3.connect(FB_POST_DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS posts(
        post_id TEXT PRIMARY KEY, page_id TEXT, store TEXT, product TEXT, post_copy TEXT, source TEXT,
        og_image TEXT, post_url TEXT, first_seen TEXT, last_seen TEXT, seen_count INTEGER DEFAULT 1)""")
    for col in ("post_copy TEXT", "source TEXT"):   # migrare DB-uri vechi
        try:
            c.execute("ALTER TABLE posts ADD COLUMN " + col)
        except sqlite3.OperationalError:
            pass
    return c


def reg_get(post_id):
    c = _reg()
    r = c.execute("SELECT post_id,page_id,store,product,post_copy,source,og_image,post_url,seen_count FROM posts WHERE post_id=?", (post_id,)).fetchone()
    c.close()
    if not r:
        return None
    keys = ("post_id", "page_id", "store", "product", "post_copy", "source", "og_image", "post_url", "seen_count")
    return dict(zip(keys, r))


def reg_put(rec):
    c = _reg()
    now = _now()
    c.execute("""INSERT INTO posts(post_id,page_id,store,product,post_copy,source,og_image,post_url,first_seen,last_seen,seen_count)
        VALUES(?,?,?,?,?,?,?,?,?,?,1)
        ON CONFLICT(post_id) DO UPDATE SET last_seen=excluded.last_seen, seen_count=seen_count+1,
            store=COALESCE(NULLIF(excluded.store,''),store),
            product=COALESCE(NULLIF(excluded.product,''),product),
            post_copy=COALESCE(NULLIF(excluded.post_copy,''),post_copy),
            source=COALESCE(NULLIF(excluded.source,''),source),
            og_image=COALESCE(NULLIF(excluded.og_image,''),og_image),
            post_url=COALESCE(NULLIF(excluded.post_url,''),post_url)""",
              (rec["post_id"], rec.get("page_id", ""), rec.get("store", ""), rec.get("product", ""),
               rec.get("post_copy", ""), rec.get("source", ""), rec.get("og_image", ""), rec.get("post_url", ""), now, now))
    c.commit()
    c.close()


def reg_list():
    c = _reg()
    rows = c.execute("SELECT post_id,store,product,source,seen_count,last_seen FROM posts ORDER BY last_seen DESC").fetchall()
    c.close()
    return rows


def _skip_res(post_id, page_id, store, image="", url="", copy="", cached=False):
    return {"post_id": post_id, "page_id": page_id, "store": store, "product": "", "image": image, "url": url,
            "copy": copy, "source": "skip", "skipped": True, "cached": cached,
            "note": "magazin mono-produs (produs cunoscut din brand) — reclama sărită"}


def ad_for_ticket(ticket, describe_ad=True, use_cache=True, store_hint="", skip_mono=True):
    """Rezolvă RECLAMA pe care comentează clientul (doar tichete FB/IG comment).
    Întoarce {post_id,page_id,store,product,image,url,copy,source,cached} sau None. Folosește + completează registrul.
    skip_mono: sare magazinele MONO-PRODUS (parfumuri etc.) — produsul e cunoscut din brand → zero LLM (vezi MONO_PRODUCT_BRANDS)."""
    if (ticket.get("channel") or "") not in COMMENT_CHANNELS:
        return None
    pages, post_id = extract_fb_post(ticket)
    if not post_id:
        return None
    # SKIP mono-produs din hint (dacă apelantul știe magazinul) — zero HTTP, zero LLM
    if skip_mono and store_hint and is_mono_product(store_hint):
        return _skip_res(post_id, (pages or [""])[0], store_hint)
    if use_cache:
        cached = reg_get(post_id)
        if cached and (cached.get("source") == "skip" or (skip_mono and is_mono_product(cached.get("store")))):
            return _skip_res(post_id, cached.get("page_id"), cached.get("store"),
                             cached.get("og_image"), cached.get("post_url"), cached.get("post_copy"), cached=True)
        if cached and cached.get("product"):
            reg_put({"post_id": post_id})  # bump last_seen/seen_count
            return {"post_id": post_id, "page_id": cached.get("page_id"), "store": cached.get("store"),
                    "product": cached.get("product"), "image": cached.get("og_image"), "url": cached.get("post_url"),
                    "copy": cached.get("post_copy"), "source": cached.get("source"), "cached": True}
    og = fb_post_og(pages, post_id)
    copy = og.get("copy", "")
    if not og.get("image") and not copy:
        return {"post_id": post_id, "page_id": (pages or [""])[0], "store": "", "product": "", "image": "", "url": "",
                "copy": "", "cached": False, "error": "postare negăsită (fără og:image/copy)"}
    pg = og.get("page_id") or (pages or [""])[0]
    # SKIP mono-produs din og:title (după fetch, dacă n-am avut hint) — zero LLM; salvăm markerul ca să nu re-fetch
    if skip_mono and is_mono_product(og.get("title", "")):
        reg_put({"post_id": post_id, "page_id": pg, "store": og.get("title", ""), "product": "",
                 "post_copy": copy, "source": "skip", "og_image": og.get("image", ""), "post_url": og.get("url", "")})
        return _skip_res(post_id, pg, og.get("title", ""), og.get("image", ""), og.get("url", ""), copy)
    product, source = "", ""
    if describe_ad and og.get("image"):   # 1) încearcă din POZĂ (cu copy-ul ca context)
        try:
            data = urllib.request.urlopen(enc_url(og["image"]), timeout=45).read()
            vctx = ("Reclama [%s]. Copy: %s" % (og.get("title", ""), copy))[:400]
            product = vision(data, "image/jpeg", vctx, SYS_AD)
            source = "poză"
        except Exception as e:
            product = "(eroare descriere reclamă: %s)" % str(e)[:60]
    good = bool(product) and not product.startswith("(eroare") and not product.startswith("(fără") and "(produs neclar" not in product
    if not good and describe_ad and copy:   # 2) FALLBACK: din COPY-ul (textul) postării
        p2 = text_product(copy, og.get("title", ""))
        if p2 and "(produs neclar" not in p2:
            product, source, good = p2, "copy", True
    # salvăm întotdeauna og_image/store/copy/url; produsul+sursa DOAR dacă am reușit (altfel se reîncearcă next run)
    reg_put({"post_id": post_id, "page_id": pg, "store": og.get("title", ""), "product": product if good else "",
             "post_copy": copy, "source": source if good else "", "og_image": og.get("image", ""), "post_url": og.get("url", "")})
    res = {"post_id": post_id, "page_id": pg, "store": og.get("title", ""), "product": product if good else "",
           "image": og.get("image", ""), "url": og.get("url", ""), "copy": copy, "source": source if good else "", "cached": False}
    if not good:
        res["error"] = product or "fără descriere"
    return res


_CATALOG_STOP = {"produsul", "produs", "produse", "promovat", "promoveaza", "este", "sunt", "pentru", "reclama", "care",
                 "avand", "culoare", "culori", "design", "poza", "ofera", "unui", "unei", "dintr", "intr", "negru",
                 "negre", "alb", "albe", "prezentat", "prezentata", "model", "modele", "inferior", "superior", "imagine",
                 # vocabular de MARKETING: nu descrie obiectul, dar mânca sloturi de cuvinte-cheie și —
                 # cât timp pragul era pozițional — omora tot catalogul când reclama începea cu el
                 "oferta", "oferte", "reducere", "reduceri", "promotie", "promotii", "super", "doar", "numai",
                 "gratuit", "gratuita", "gratis", "livrare", "livrarea", "transport", "comanda", "comandati",
                 "acum", "astazi", "lichidare", "limitat", "limitata", "pret", "pretul", "preturi", "stoc"}
# ATRIBUTE (material/formă/însușire): potrivesc titluri din categorii complet diferite („electric" leagă
# un lunchbox de un clește de mufe), deci nu pot fi ele OBIECTUL care confirmă potrivirea.
_CATALOG_ATTR = {"electric", "electrica", "electrice", "ceramic", "ceramica", "ceramice", "inox", "metal",
                 "metalic", "plastic", "silicon", "lemn", "sticla", "textil", "mare", "mari", "mica", "mici",
                 "pliabil", "pliabila", "universal", "universala", "profesional", "profesionala", "dublu",
                 "dubla", "triplu", "portabil", "portabila", "reglabil", "reglabila", "antiaderent", "inteligent",
                 # ACȚIUNEA/SCOPUL nu e obiectul: „burete de curățare" prindea „Aspirator pentru
                 # Curățarea Urechilor" de îndată ce pragul a devenit semantic (orice cuvânt, nu primul).
                 "curatare", "curatat", "curata", "curatenie", "spalare", "spalat", "ingrijire",
                 "intretinere", "depozitare", "pastrare", "organizare", "utilizat", "utilizare",
                 "folosit", "folosire", "destinat", "destinata"}


_MURL = None


def _metrics_conn():
    """Conexiune la metrics (sau None) — potrivirea cu catalogul, lista de branduri, moneda."""
    global _MURL
    if _MURL is None:
        _MURL = secret("DATABASE_URL_METRICS") or ""
    if not _MURL:
        return None
    try:
        import pg8000.dbapi
        u = urllib.parse.urlparse(_MURL)
        return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                    password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                    port=u.port or 5432, database=(u.path or "/").lstrip("/").split("?")[0])
    except Exception:
        return None


def _close(conn):
    try:
        conn.close()
    except Exception:
        pass


_BRANDS = None


def metrics_brands():
    """{nume deaccentuat: nume real} pt toate brandurile din metrics (o dată per proces)."""
    global _BRANDS
    if _BRANDS is None:
        _BRANDS = {}
        conn = _metrics_conn()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT btrim(name) FROM brands")
                _BRANDS = {_deacc(r[0]): r[0] for r in cur.fetchall() if r[0]}
            except Exception:
                pass
            _close(conn)
    return _BRANDS


# Moneda e a MAGAZINULUI, nu „lei": un preț bulgăresc etichetat „lei" e o minciună către client.
# Sursa = moneda ULTIMEI comenzi a brandului în metrics (prinde și schimbările de monedă — BG a
# trecut BGN→EUR în iul-2026). NU folosim brands."nativeCurrency": minte (Bonhaus RO scrie acolo
# EUR, dar vinde în lei). Fallback: piața din sufixul numelui, apoi lei.
CUR_LABEL = {"RON": "lei", "BGN": "лв.", "CZK": "Kč", "HUF": "Ft", "PLN": "zł", "MDL": "MDL", "EUR": "EUR"}
MARKET_CUR = {"bg": "EUR", "cz": "CZK", "sk": "EUR", "hu": "HUF", "pl": "PLN", "hr": "EUR", "md": "MDL"}
_CUR = {}


def brand_currency(store):
    """Eticheta de monedă a magazinului („lei", „Kč", „EUR"…). Cache per proces."""
    key = _deacc(store)
    if key in _CUR:
        return _CUR[key]
    # Moneda DECLARATĂ de magazin (instantaneul Shopify) bate orice deducție: pe piețele străine
    # n-avem comenzi în metrics de unde s-o citim, iar ghicitul din sufixul numelui a dat deja
    # „BGN" pe Bulgaria după ce magazinul trecuse pe EUR.
    _m = snapshot_meta(store)
    if _m.get("currency"):
        _CUR[key] = CUR_LABEL.get(_m["currency"].upper(), _m["currency"].upper())
        return _CUR[key]
    code = ""
    conn = _metrics_conn()
    if conn:
        try:
            cur = conn.cursor()
            cur.execute('SELECT o.currency FROM orders o JOIN brands b ON b.id=o."brandId" '
                        'WHERE lower(btrim(b.name))=lower(btrim(%s)) AND o.currency IS NOT NULL '
                        'ORDER BY o."createdAt" DESC LIMIT 1', (store,))
            r = cur.fetchone()
            code = (r[0] or "") if r else ""
        except Exception:
            pass
        _close(conn)
    if not code:
        m = re.search(r"[ .](bg|cz|sk|hu|pl|hr|md)$", key)
        code = MARKET_CUR.get(m.group(1), "RON") if m else "RON"
    _CUR[key] = CUR_LABEL.get(code.upper(), code.upper())
    return _CUR[key]


def _brand_from_title(og_title):
    """Normalizează og:title („Ofertele-Zilei.ro", „MagDeal.ro") → nume brand pt metrics.brands (case-insensitiv la query).
    Sufixul de ȚARĂ NU se aruncă dacă există brand separat pe piața aia („Bonhaus.sk" → „Bonhaus SK"):
    altfel clienta slovacă primea în context produse ROMÂNEȘTI la prețuri în lei."""
    t = re.sub(r"\s+by\s+.*$", "", og_title or "", flags=re.I)   # „... by George Talent"
    cc = re.search(r"\.(ro|com|cz|pl|bg|hu|sk|hr)\b", t, flags=re.I)
    t = re.sub(r"\.(ro|com|cz|pl|bg|hu|sk|hr)\b", "", t, flags=re.I)
    base = " ".join(re.sub(r"[-_.]+", " ", t).split())
    if cc and cc.group(1).lower() not in ("ro", "com"):
        hit = metrics_brands().get(_deacc(base + " " + cc.group(1)))
        if hit:
            return hit
        # Piață STRĂINĂ fără brand propriu în metrics (măsurat: există „Bonhaus BG/CZ/PL/SK", dar NU
        # „Bonhaus HU"/„Bonhaus HR" și niciun „Duppo") → NU cădea pe brandul ROMÂNESC: i-am servi unui
        # maghiar catalogul RO, în lei. Mai bine NICIUN catalog decât catalogul altei piețe.
        return ""
    return base


_MCONN = [None]


def _metrics_rows(store, kw, limit):
    """Rândurile (titlu, preț, stoc, sku, scor) din warehouse-ul metrics, sau [] dacă n-are brandul.

    Conexiunea se ȚINE deschisă pe proces: `catalog_match` poate întreba de două ori per tichet (o
    dată pe descrierea în română, o dată pe copy-ul în limba pieței), iar o conexiune Postgres NOUĂ
    per întrebare costă mai mult decât interogarea. La orice eroare se aruncă și se redeschide."""
    conn = _MCONN[0]
    if conn is None:
        conn = _MCONN[0] = _metrics_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        like = ["%" + w + "%" for w in kw]
        # `translate` (nu extensia `unaccent`, care nu e instalată) — titlul pliat ÎN SQL cu ACELAȘI
        # tabel ca `_deacc` din Python (vezi _FOLD_SRC/_FOLD_DST), ca ambele părți să se compare în
        # aceeași formă, indiferent de alfabetul pieței.
        _t = "translate(lower(p.title),%s,%s)"
        score = " + ".join(["(" + _t + " LIKE %s)::int" for _ in kw])
        where_or = " OR ".join([_t + " LIKE %s" for _ in kw])
        sql = ('SELECT p.title, v.price, v."inventoryQuantity", v.sku, (%s) AS sc '
               'FROM products p JOIN variants v ON v."productId"=p.id LEFT JOIN brands b ON b.id=p."brandId" '
               'WHERE lower(btrim(b.name))=lower(btrim(%%s)) AND (%s) ORDER BY sc DESC, v.price::numeric LIMIT %%s' % (score, where_or))
        args = []
        for w in like:
            args += [_FOLD_SRC, _FOLD_DST, w]
        args.append(store)
        for w in like:
            args += [_FOLD_SRC, _FOLD_DST, w]
        args.append(limit * 4)
        cur.execute(sql, args)
        return cur.fetchall()
    except Exception:
        _close(conn)
        _MCONN[0] = None
        return []


# ── CATALOG LOCAL (instantaneu Shopify) — pt brandurile pe care metrics NU le are ──
# De ce instantaneu și nu Shopify LIVE la fiecare tichet: regula CS („rația Shopify") spune că
# lookup-urile CS nu lovesc Shopify live. Un instantaneu = UN singur pull per magazin, apoi zero
# HTTP pe tichet. De ce nu „adăugăm brandurile în metrics": nu e în mâna acestui skill, iar dovada
# că nici nu se întâmplă singur e „Bonhaus PL" — rând de brand în `brands` de luni de zile și ZERO
# produse; un rând de brand fără sincronizare de produse nu dă niciun preț.
CATALOG_DB = os.environ.get("CS_CATALOG_DB") or os.path.join(HERE, "cs_catalog.sqlite")
# brand (exact numele din PAGE_STORE/store_name) -> prefixul magazinului Shopify (stores.csv).
CATALOG_SHOPIFY = {"Duppo BG": "DUPBG", "Bonhaus PL": "PL", "Bonhaus HU": "HU", "Bonhaus SK": "SK",
                   "Bonhaus BG": "BONBG", "Bonhaus CZ": "CZ", "Duppo Moldova": "MD"}
_SNAP = {}
_SNAP_META = {}


def _snap_conn():
    c = sqlite3.connect(CATALOG_DB, timeout=20)
    c.execute("""CREATE TABLE IF NOT EXISTS catalog(
        brand TEXT, title TEXT, title_pliat TEXT, price TEXT, stock INTEGER, sku TEXT,
        PRIMARY KEY(brand, sku, title))""")
    c.execute("""CREATE TABLE IF NOT EXISTS catalog_meta(
        brand TEXT PRIMARY KEY, shop TEXT, currency TEXT, n INTEGER, updated_at TEXT,
        uniform_price TEXT)""")
    if "uniform_price" not in {r[1] for r in c.execute("PRAGMA table_info(catalog_meta)")}:
        c.execute("ALTER TABLE catalog_meta ADD COLUMN uniform_price TEXT")
    return c


def snapshot_meta(store):
    """{shop, currency, n, updated_at, uniform_price} pt brandul din instantaneu, sau {}.
    Cache per proces: se cheamă o dată per tichet, iar `_snap_conn` face CREATE TABLE de fiecare dată."""
    if not store:
        return {}
    if _deacc(store) in _SNAP_META:
        return _SNAP_META[_deacc(store)]
    try:
        c = _snap_conn()
        r = c.execute("SELECT shop, currency, n, updated_at, uniform_price FROM catalog_meta "
                      "WHERE lower(brand)=lower(?)", (store,)).fetchone()
        c.close()
    except Exception:
        return {}
    _SNAP_META[_deacc(store)] = m = ({"shop": r[0], "currency": r[1], "n": r[2], "updated_at": r[3],
                                      "uniform_price": r[4]} if r else {})
    return m


def _snapshot_rows(store, kw, limit):
    """Aceleași rânduri ca `_metrics_rows`, dar din instantaneul local. Scorul se face în Python
    (catalogul unui magazin străin are sute de rânduri, nu milioane) cu ACELAȘI `_deacc`."""
    key = _deacc(store)
    if key not in _SNAP:
        try:
            c = _snap_conn()
            _SNAP[key] = c.execute(
                "SELECT title, title_pliat, price, stock, sku FROM catalog WHERE lower(brand)=lower(?)",
                (store,)).fetchall()
            c.close()
        except Exception:
            _SNAP[key] = []
    rows = []
    for title, pliat, price, stoc, sku in _SNAP[key]:
        sc = sum(1 for w in kw if w in (pliat or ""))
        if sc:
            rows.append((title, price, stoc, sku, sc))
    rows.sort(key=lambda r: (-r[4], float(r[1] or 0)))
    return rows[:limit * 4]


def _cuvinte_cheie(text):
    """Cuvintele-cheie (deaccentuate, fără vocabular de stop) dintr-un text, maximum 6."""
    stop = {_deacc(s) for s in _CATALOG_STOP}
    seen, kw = set(), []
    # Clasa de litere e UNICODE, nu „a-z + diacriticele ROMÂNEȘTI". Cu vechea clasă, un text
    # bulgăresc („четка за коса") dădea ZERO cuvinte-cheie → catalogul era mort pe BG chiar și pe
    # brandurile care ÎL AU în metrics; iar pe sk/pl/hu/cz cuvintele se rupeau la prima literă
    # străină („protišmykových" → „proti" + „mykov"). `[^\W\d_]` = orice literă, în orice alfabet.
    for w in re.findall(r"[^\W\d_]{4,}", text, re.UNICODE):
        # CUVINTELE INTRĂ DEACCENTUATE. Catalogul e scris FĂRĂ diacritice („Set 6 Genti din Piele
        # Ecologica"), reclama CU („genți", „tacâmuri") → LIKE '%genți%' pe titlu dădea ZERO.
        # Măsurat pe metrics: „genți" → 0 rânduri, „genti" → 8 titluri REALE pe Apreciat; pe 30 de
        # reclame reale catalogul potrivea 3 (10%). Titlul se deaccentuează în SQL (translate),
        # deci ambele părți se compară în ACEEAȘI formă — lista de STOP era deja deaccentuată,
        # doar cuvintele nu erau, așa că filtrul funcționa și potrivirea nu.
        wl = _deacc(w)
        if wl in stop or wl in seen:
            continue
        seen.add(wl)
        kw.append(wl)
    return kw[:6]


def _filtreaza(rows, kw, limit):
    """Pragul de încredere aplicat rândurilor unei surse. [] = nicio potrivire credibilă."""
    # PRAG DE ÎNCREDERE — un catalog GREȘIT e mai rău decât lipsa lui (un lunchbox electric
    # „potrivit" cu un clește de mufe fiindcă ambele zic „electric"): cerem ≥2 cuvinte potrivite
    # ȘI ca OBIECTUL (primul cuvânt de conținut din descrierea reclamei) să apară în titlu.
    # Pragul e SEMANTIC, nu POZIȚIONAL: cerem ca măcar un cuvânt de CONȚINUT (obiectul — nu un
    # atribut, nu un cuvânt de marketing) să apară în titlu. Varianta „primul cuvânt din descriere"
    # pierdea TOT catalogul dacă reclama începea cu „Ofertă la …" (3 din 12 formulări RO reale).
    heads = [_deacc(w)[:5] for w in kw if _deacc(w) not in _CATALOG_ATTR]
    out, seen_t = [], set()
    for title, price, stoc, sku, sc in rows:
        dt = _deacc(title)
        if title in seen_t or sc < 2 or not any(h in dt for h in heads):
            continue
        seen_t.add(title)
        # STOCUL se dă clientului doar dacă e POZITIV. Măsurat pe Duppo BG: toate cele 119
        # produse active au `inventoryQuantity` NEGATIV (−5 … −34) — magazin care nu urmărește
        # stocul, nu marfă care lipsește. „(stoc −17)" în context e o cifră falsă pe care
        # modelul o poate repeta clientului; „fără cifră" e adevărul.
        try:
            stoc = int(stoc) if stoc is not None and int(stoc) > 0 else None
        except (TypeError, ValueError):
            stoc = None
        out.append({"title": title, "price": price, "stock": stoc, "sku": sku, "score": sc})
        if len(out) >= limit:
            break
    return out


def catalog_match(store, text, limit=3, text2=""):
    """Potrivește produsul din reclamă cu CATALOGUL → preț+stoc reale. store = nume brand.
    Sursa 1 = warehouse-ul metrics; sursa 2 (piețe străine) = instantaneul Shopify local.

    `text2` = a DOUA sursă de cuvinte-cheie, căutată separat. Pe piețele străine cele două părți ale
    contextului sunt în LIMBI DIFERITE — descrierea reclamei o scrie modelul în ROMÂNĂ, iar copy-ul
    postării (și titlul din catalog) sunt în limba pieței. Lipite într-un singur text, plafonul de 6
    cuvinte-cheie se umplea integral cu partea ROMÂNEASCĂ, care nu se potrivește cu un titlu bulgăresc:
    măsurat pe Bonhaus BG, reclama „електрическа кутия за храна 3 в 1" NU prindea produsul cu ACELAȘI
    nume din catalog, fiindcă niciun cuvânt bulgăresc nu ajungea în cele 6 sloturi."""
    if not store:
        return []
    for t in (text, text2):
        kw = _cuvinte_cheie(t)
        if not kw:
            continue
        # metrics întâi; instantaneul local dacă warehouse-ul n-are brandul SAU n-are nimic credibil
        # (pe piețele străine catalogul din metrics e și incomplet, și amestecat cu titluri ROMÂNEȘTI).
        out = _filtreaza(_metrics_rows(store, kw, limit), kw, limit)
        if not out:
            out = _filtreaza(_snapshot_rows(store, kw, limit), kw, limit)
        if out:
            return out
    return []


# PREȚUL scris în PROPRIA noastră reclamă. Măsurat: 39 din 403 copy-uri reale îl conțin
# („covor pufos premiumcele mai mici prețuri 169 leicomandă pe…"), iar pe Magdeal — cel mai mare
# magazin de pe comentarii publice, 328 din 1.306 — catalogul din metrics are ZERO produse, deci
# copy-ul e SINGURA sursă de preț. Moneda se ia din copy (clientul o citește chiar acolo), nu se
# presupune. Fără cifra asta modelul n-are ce spune sub postare și cade pe „scrieți-ne în privat".
_COPY_PRICE_RE = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)\s?(lei|ron|kč|kc|лв|lv|ft|zł|zl|eur|€)",
    re.I)


def copy_price(copy):
    """Prețul explicit din textul reclamei („169 lei") sau ''. Doar cifră + monedă, fără ghicit."""
    m = _COPY_PRICE_RE.search(copy or "")
    if not m:
        return ""
    return "%s %s" % (m.group(1).strip(), m.group(2))


def catalog_block(store, text):
    """Bloc de CATALOG pentru un canal FĂRĂ reclamă (mesaj privat / e-mail): produsul despre care
    întreabă clientul + prețul REAL, în moneda pieței. '' dacă nu avem nimic cert.

    Pe canal public produsul vine din POSTAREA comentată (`ad_block`); pe privat nu există postare,
    deci singura sursă de fapte despre produs e ce scrie clientul — pe piețele străine, în limba lui."""
    if not store or not text:
        return ""
    cat = catalog_match(store, text)
    if cat:
        cur = brand_currency(store)
        return ("PRODUS în CATALOG (preț/stoc REALE — folosește-le): " + "; ".join(
            "%s — %s %s%s" % (c["title"][:55], c["price"], cur,
                              (" (stoc %s)" % c["stock"]) if c.get("stock") is not None else "")
            for c in cat))
    m = snapshot_meta(store)
    if m.get("uniform_price"):
        return ("PREȚ ÎN CATALOG: %s %s — la %s TOATE cele %d produse au ACELAȘI preț, deci cifra e "
                "sigură indiferent de model." % (m["uniform_price"], brand_currency(store), store, m.get("n") or 0))
    return ""


def ad_block(ticket, describe_ad=True, store_hint=""):
    """Bloc text pt context (sau '') — reclama pe care comentează clientul + PRODUSUL din catalog (preț/stoc). '' pe mono-produs (sărite)."""
    ad = ad_for_ticket(ticket, describe_ad=describe_ad, store_hint=store_hint)
    if not ad or ad.get("skipped") or not ad.get("product"):
        return ""
    brand = store_hint or _brand_from_title(ad.get("store", ""))
    cat = catalog_match(brand, ad["product"] + " " + (ad.get("copy") or ""), text2=ad.get("copy") or "")
    cat_txt = ""
    if cat:
        cur = brand_currency(brand)   # prețul e în moneda MAGAZINULUI, nu în lei
        cat_txt = " | PRODUS în CATALOG (preț/stoc REALE — folosește-le): " + "; ".join(
            "%s — %s %s%s" % (c["title"][:55], c["price"], cur, (" (stoc %s)" % c["stock"]) if c.get("stock") is not None else "") for c in cat)
    # fallback de PREȚ: dacă n-avem catalog (brand fără produse în metrics — Magdeal, Casa
    # Ofertelor — sau potrivire sub prag), luăm prețul din propria reclamă, ETICHETAT ca atare.
    if not cat_txt:
        _p = copy_price(ad.get("copy") or "")
        if _p:
            cat_txt = (" | PREȚ ÎN RECLAMĂ (scris de noi în postarea pe care o vede clientul — "
                       "poți să-l dai PUBLIC): %s" % _p)
    # ultima plasă de PREȚ, doar pe magazinele cu preț UNIC în tot catalogul (vezi `catalog_build`):
    # acolo cifra e certă chiar dacă nu știm EXACT care produs e în reclamă.
    if not cat_txt:
        _m = snapshot_meta(brand)
        if _m.get("uniform_price"):
            cat_txt = (" | PREȚ ÎN CATALOG: %s %s — la %s TOATE cele %d produse au ACELAȘI preț, "
                       "deci cifra e sigură indiferent de model (poți să o dai PUBLIC)"
                       % (_m["uniform_price"], brand_currency(brand), brand, _m.get("n") or 0))
    extra = (" | Text reclamă: %s" % ad["copy"]) if ad.get("copy") else ""
    return ("RECLAMA/POSTAREA pe care comentează clientul (identifică PRODUSUL și răspunde la obiect PUBLIC, INCLUSIV PREȚUL din CATALOG / din RECLAMĂ dacă apare): %s%s%s%s" % (
        ad["product"], (" [magazin: %s]" % ad["store"]) if ad.get("store") else "", cat_txt, extra))


# ───────────────────────── constructorul instantaneului Shopify ─────────────────────────
def _stores_csv():
    """Textul stores.csv: env (cale SAU conținut), apoi ./stores.csv, apoi secretul KB."""
    env = os.environ.get("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    if os.path.exists("stores.csv"):
        return open("stores.csv", encoding="utf-8-sig").read()
    return secret("SHOPIFY_STORES_CSV") or ""


def _shop_of(prefix):
    """(domeniu myshopify, token static) pt un prefix din stores.csv."""
    import csv as _csv, io as _io
    for row in _csv.DictReader(_io.StringIO(_stores_csv())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            return ((row.get("shop") or "").strip().replace("https://", "").strip("/"),
                    (row.get("token") or "").strip())
    return "", ""


def _mint(shop):
    """Token `client_credentials` pt magazinele ale căror app-uri îl emit la cerere (~24h).
    Întâi app-urile din `SHOPIFY_READ_APPS` (ro-deals / bonhaus-intl), apoi app-ul ARONA."""
    try:
        cfg = json.loads(secret("SHOPIFY_READ_APPS") or "{}")
    except Exception:
        cfg = {}
    perechi = [(a.get("client_id"), a.get("client_secret")) for a in (cfg.get("apps") or [])
               if shop in (a.get("stores") or {}).values()]
    perechi.append((secret("SHOPIFY_ARONA_CLIENT_ID"), secret("SHOPIFY_ARONA_CLIENT_SECRET")))
    for cid, csec in perechi:
        if not (cid and csec):
            continue
        try:
            body = json.dumps({"client_id": cid, "client_secret": csec,
                               "grant_type": "client_credentials"}).encode()
            req = urllib.request.Request("https://%s/admin/oauth/access_token" % shop, data=body,
                                         headers={"content-type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=25).read())["access_token"]
        except Exception:
            continue
    return ""


def _shop_gql(shop, token, query, variables=None):
    ver = os.environ.get("SHOPIFY_API_VERSION") or "2026-01"
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request("https://%s/admin/api/%s/graphql.json" % (shop, ver), data=body,
                                 headers={"X-Shopify-Access-Token": token, "content-type": "application/json"})
    out = json.loads(urllib.request.urlopen(req, timeout=60).read())
    if out.get("errors"):
        raise RuntimeError(json.dumps(out["errors"], ensure_ascii=False)[:200])
    return out["data"]


_Q_CATALOG = """query($c:String){
  shop{ currencyCode }
  products(first:100, after:$c, query:"status:active"){
    pageInfo{ hasNextPage endCursor }
    nodes{ title variants(first:10){ nodes{ price sku inventoryQuantity } } }
  }
}"""


def catalog_build(store, prefix=""):
    """Reface instantaneul local de catalog pentru UN brand, din magazinul lui Shopify.
    Întoarce (n_produse, monedă) sau (0, '') dacă magazinul nu răspunde."""
    prefix = prefix or CATALOG_SHOPIFY.get(store, "")
    if not prefix:
        raise SystemExit("Brandul %r n-are magazin Shopify în CATALOG_SHOPIFY." % store)
    shop, token = _shop_of(prefix)
    if not shop:
        raise SystemExit("Prefixul %r nu e în stores.csv." % prefix)
    cursor, randuri, cur_code = None, [], ""
    for _ in range(40):                       # plafon de siguranță: 4.000 de produse
        try:
            d = _shop_gql(shop, token, _Q_CATALOG, {"c": cursor})
        except Exception:
            token = _mint(shop)               # tokenul din CSV e doar un MARCAJ pe magazinele client_credentials
            if not token:
                return 0, ""
            d = _shop_gql(shop, token, _Q_CATALOG, {"c": cursor})
        cur_code = (d.get("shop") or {}).get("currencyCode") or cur_code
        for p in d["products"]["nodes"]:
            vs = (p.get("variants") or {}).get("nodes") or []
            preturi = [v["price"] for v in vs if v.get("price") is not None]
            stocuri = [v.get("inventoryQuantity") for v in vs if isinstance(v.get("inventoryQuantity"), int)]
            randuri.append((store, p["title"], _deacc(p["title"]),
                            min(preturi, key=lambda x: float(x)) if preturi else None,
                            max(stocuri) if stocuri else None,
                            (vs[0].get("sku") if vs else "") or ""))
        if not d["products"]["pageInfo"]["hasNextPage"]:
            break
        cursor = d["products"]["pageInfo"]["endCursor"]
    # PREȚ UNIC: unele magazine străine vând TOT catalogul la același preț (măsurat pe Duppo BG:
    # 113 produse active, toate 12,00 EUR). Acolo „cât costă?" — întrebarea de sub aproape fiecare
    # reclamă — are un răspuns CERT chiar și când potrivirea pe titlu nu prinde produsul exact.
    preturi = {p for _, _, _, p, _, _ in randuri if p}
    unic = preturi.pop() if len(preturi) == 1 else None
    c = _snap_conn()
    c.execute("DELETE FROM catalog WHERE lower(brand)=lower(?)", (store,))
    c.executemany("INSERT OR REPLACE INTO catalog(brand,title,title_pliat,price,stock,sku) VALUES(?,?,?,?,?,?)", randuri)
    c.execute("INSERT OR REPLACE INTO catalog_meta(brand,shop,currency,n,updated_at,uniform_price) "
              "VALUES(?,?,?,?,?,?)", (store, shop, cur_code, len(randuri), _now(), unic))
    c.commit(); c.close()
    _SNAP.pop(_deacc(store), None); _CUR.pop(_deacc(store), None); _SNAP_META.pop(_deacc(store), None)
    return len(randuri), cur_code


# ───────────────────────── CLI ─────────────────────────
def _get_ticket(mcp, conv):
    key = "id" if not str(conv).isdigit() else "conversation_number"
    cv = mcp.call("get_conversation", {key: str(conv), "mode": "audit", "max_messages": 30, "max_message_chars": 300})
    tk = cv.get("ticket") or {}
    msgs = (cv.get("messages_page") or {}).get("messages") or cv.get("messages") or []
    return tk, msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", default=None, help="nr conversație Richpanel (sau id)")
    ap.add_argument("--save", default=None, help="director unde să salveze imaginile clientului")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-describe", action="store_true", help="doar descarcă/listează, fără descriere LLM")
    ap.add_argument("--all", action="store_true", help="descrie și pozele trimise de AGENT")
    ap.add_argument("--no-ad", action="store_true", help="nu rezolva reclama de la comentariu")
    ap.add_argument("--no-cache", action="store_true", help="ignoră registrul, re-descrie reclama")
    ap.add_argument("--registry-list", action="store_true", help="afișează postările salvate în registru")
    ap.add_argument("--registry-build", action="store_true", help="populează registrul din comentariile recente (incremental)")
    ap.add_argument("--channel", default="facebook_feed_comment", help="canal pt --registry-build")
    ap.add_argument("--scan", type=int, default=200, help="câte comentarii recente să scaneze la --registry-build")
    ap.add_argument("--catalog-build", nargs="?", const="all", default=None, metavar="BRAND",
                    help="reface instantaneul local de catalog din Shopify (implicit: toate brandurile din CATALOG_SHOPIFY)")
    ap.add_argument("--catalog-list", action="store_true", help="ce branduri are instantaneul local + moneda + vechimea")
    a = ap.parse_args()

    if a.catalog_list:
        c = _snap_conn()
        randuri = c.execute("SELECT brand, shop, currency, n, updated_at FROM catalog_meta ORDER BY brand").fetchall()
        c.close()
        print("\U0001f4e6 Instantaneu catalog (%s) — %d branduri\n" % (CATALOG_DB, len(randuri)))
        for b, shop, cur, n, upd in randuri:
            print("  \u2022 %-14s %-5s %4d produse  (%s, %s)" % (b, cur, n, shop, upd))
        lipsa = [b for b in CATALOG_SHOPIFY if b not in {r[0] for r in randuri}]
        if lipsa:
            print("\n  \u26a0\ufe0f  f\u0103r\u0103 instantaneu: %s" % ", ".join(sorted(lipsa)))
        return

    if a.catalog_build:
        branduri = sorted(CATALOG_SHOPIFY) if a.catalog_build == "all" else [a.catalog_build]
        for b in branduri:
            try:
                n, cur = catalog_build(b)
            except SystemExit as e:
                print("  \u26d4 %-14s %s" % (b, e)); continue
            print(("  \u2705 %-14s %4d produse, moneda %s" % (b, n, cur)) if n
                  else ("  \u26d4 %-14s magazinul nu r\u0103spunde (token?)" % b))
        return

    if a.registry_list:
        rows = reg_list()
        print("📚 Registru postări (%s) — %d înregistrări\n" % (FB_POST_DB, len(rows)))
        for pid, store, product, source, n, last in rows:
            print("  • %s [%s ×%s%s] %s" % (pid, store or "?", n, ("/" + source if source else ""), (product or "")[:90]))
        return

    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))

    if a.registry_build:
        seen, new, reused = set(), 0, 0
        res = mcp.call("list_conversations", {"channel": a.channel, "limit": a.scan, "status": "OPEN"})
        tickets = res.get("tickets") or res.get("conversations") or []
        print("🔄 Scanez %d comentarii (%s) → completez registrul…\n" % (len(tickets), a.channel))
        for t in tickets:
            _, post_id = extract_fb_post(t)
            if not post_id or post_id in seen:
                continue
            seen.add(post_id)
            cached = reg_get(post_id)
            if cached and cached.get("product"):
                reused += 1
                continue
            ad = ad_for_ticket(t, describe_ad=not a.no_describe, use_cache=False)
            if ad and ad.get("product"):
                new += 1
                print("  + %s [%s] %s" % (post_id, ad.get("store") or "?", (ad["product"] or "")[:80]))
            else:
                print("  ⚠️ %s — %s" % (post_id, (ad or {}).get("error", "nedescris")))
            if not a.no_describe:
                time.sleep(0.4)   # anti-burst rate-limit LLM
        print("\n✓ postări noi: %d | deja știute: %d | total unice scanate: %d" % (new, reused, len(seen)))
        return

    if not a.conv:
        ap.error("dă --conv N, sau --registry-list / --registry-build")

    tk, msgs = _get_ticket(mcp, a.conv)
    ctx = " ".join(((tk.get("subject") or "") + " " + (tk.get("first_message") or "")).split())[:300]
    photos = client_photos(msgs, ctx, include_agent=a.all, save_dir=a.save,
                           describe_imgs=not a.no_describe, conv=str(a.conv))
    ad = None if a.no_ad else ad_for_ticket(tk, describe_ad=not a.no_describe, use_cache=not a.no_cache)

    if a.json:
        print(json.dumps({"conv": a.conv, "subject": ctx, "channel": tk.get("channel"),
                          "client_photos": photos, "ad": ad}, ensure_ascii=False, indent=1))
        return

    print("📷 Tichet #%s [%s] — %s" % (a.conv, tk.get("channel") or "?", ctx[:70]))
    cli = sum(1 for o in photos if o["who"] == "CLIENT")
    print("   %d imagine(i) atașată(e) (%d de la client)." % (len(photos), cli))
    if ad:
        if ad.get("skipped"):
            print("   📢 RECLAMA: sărită — magazin mono-produs [%s] (produs cunoscut din brand, zero LLM)" % (ad.get("store") or "?"))
        elif ad.get("product"):
            tag = "din registru" if ad.get("cached") else "NOU → salvat"
            src = (" via %s" % ad.get("source")) if ad.get("source") else ""
            print("   📢 RECLAMA comentată (%s%s) [%s]: %s" % (tag, src, ad.get("store") or "?", ad["product"]))
            if ad.get("copy"):
                print("      text reclamă: %s" % ad["copy"][:120])
        elif ad.get("error"):
            print("   📢 reclama: %s" % ad["error"])
    print()
    for i, o in enumerate(photos, 1):
        size = ("%d KB" % (o["bytes"] // 1024)) if o.get("bytes") else o.get("error", "")
        print("  [%d] %s · %s · %s" % (i, o.get("who"), o.get("name"), size))
        if o.get("saved"):
            print("      salvat: %s" % o["saved"])
        if o.get("desc"):
            print("      👁  %s" % o["desc"])
    print()


if __name__ == "__main__":
    main()
