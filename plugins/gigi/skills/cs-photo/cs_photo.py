# /// script
# requires-python = ">=3.10"
# dependencies = []
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

Necesită: RICHPANEL_MCP_TOKEN (atașamente + listă), OPENAI_API_KEY (descriere vizuală).
Registru: env FB_POST_DB (default lângă script); pune-l pe o cale partajată (NAS) pt registru de echipă.
NU scrie nimic în Richpanel (read-only).
"""
import os, json, base64, sqlite3, datetime, re, time, urllib.request, urllib.parse, urllib.error, subprocess, argparse, sys

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")
COMMENT_CHANNELS = ("facebook_feed_comment", "instagram_comment", "facebook_comment", "instagram_feed_comment")
FB_CRAWLER_UA = "facebookexternalhit/1.1"
FB_POST_DB = os.environ.get("FB_POST_DB") or os.path.join(HERE, "fb_post_registry.sqlite")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enc_url(u):
    """Percent-encode path/query (pozele WhatsApp au spații în nume → urllib crapă pe URL neîncodat)."""
    p = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), urllib.parse.quote(p.query, safe="=&%"), p.fragment))


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=30).stdout.strip()
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


def ad_for_ticket(ticket, describe_ad=True, use_cache=True):
    """Rezolvă RECLAMA pe care comentează clientul (doar tichete FB/IG comment).
    Întoarce {post_id,page_id,store,product,image,url,cached} sau None. Folosește + completează registrul."""
    if (ticket.get("channel") or "") not in COMMENT_CHANNELS:
        return None
    pages, post_id = extract_fb_post(ticket)
    if not post_id:
        return None
    if use_cache:
        cached = reg_get(post_id)
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
    pg = og.get("page_id") or (pages or [""])[0]
    # salvăm întotdeauna og_image/store/copy/url; produsul+sursa DOAR dacă am reușit (altfel se reîncearcă next run)
    reg_put({"post_id": post_id, "page_id": pg, "store": og.get("title", ""), "product": product if good else "",
             "post_copy": copy, "source": source if good else "", "og_image": og.get("image", ""), "post_url": og.get("url", "")})
    res = {"post_id": post_id, "page_id": pg, "store": og.get("title", ""), "product": product if good else "",
           "image": og.get("image", ""), "url": og.get("url", ""), "copy": copy, "source": source if good else "", "cached": False}
    if not good:
        res["error"] = product or "fără descriere"
    return res


def ad_block(ticket, describe_ad=True):
    """Bloc text pt context (sau '') — reclama pe care comentează clientul."""
    ad = ad_for_ticket(ticket, describe_ad=describe_ad)
    if not ad or not ad.get("product"):
        return ""
    extra = (" | Text reclamă: %s" % ad["copy"]) if ad.get("copy") else ""
    return ("RECLAMA/POSTAREA pe care comentează clientul (folosește-o ca să identifici PRODUSUL și să răspunzi la obiect): %s%s%s" % (
        ad["product"], (" [magazin: %s]" % ad["store"]) if ad.get("store") else "", extra))


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
    a = ap.parse_args()

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
        if ad.get("product"):
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
