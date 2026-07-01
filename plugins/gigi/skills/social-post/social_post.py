# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31","psycopg2-binary>=2.9"]
# ///
"""Postează pe Facebook Page + Instagram pentru orice brand ARONA, cu tokenul The Wow Grid SU
(system user, nu expiră; scopes pages_manage_posts + instagram_content_publish).

  uv run social_post.py list                                         # brandurile + paginile disponibile
  uv run social_post.py post --brand gento --text "..."              # DRY-RUN (nu postează)
  uv run social_post.py post --brand gento --text "..." --apply      # postează pe FB + IG
  uv run social_post.py post --brand nubra --image poza.jpg --text "..." --to both --apply
  uv run social_post.py post --brand gt --link https://... --text "..." --to fb --apply
  uv run social_post.py post --brand grandia --text "..." --schedule "2026-07-02 10:00" --to fb --apply

IG cere imagine (poză + caption); dacă --image e fișier local → e urcat pe Vercel Blob (URL public) automat.
FB acceptă text / poză / link / programare. Dry-run by default: postarea pe pagini publice e IREVERSIBILĂ.
"""
import os, sys, re, time, json, argparse, subprocess, datetime, mimetypes
import requests

def kb(k):
    v = os.environ.get(k)
    if v: return v
    c = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
    if not os.path.exists(c):
        c = os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
    try:
        return subprocess.run(["uv","run",c,"secret-get",k], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""

G = "https://graph.facebook.com/v20.0"
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())

def su_token():
    t = os.environ.get("FB_SYSTEM_TOKEN")
    if t: return t
    import psycopg2
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    u = kb("DATABASE_URL_METRICS"); p = urlsplit(u); OK = {"host","port","dbname","user","password","sslmode","connect_timeout"}
    if p.query: u = urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in OK]),p.fragment))
    cx = psycopg2.connect(u); cx.set_session(readonly=True); c = cx.cursor()
    c.execute("SELECT \"accessToken\" FROM meta_access_tokens WHERE label LIKE 'The Wow Grid SU%'")
    r = c.fetchone(); cx.close()
    return r[0] if r else ""

def pages(tok):
    out, url = [], f"{G}/me/accounts"
    params = {"fields":"name,access_token,instagram_business_account{id,username}","limit":100,"access_token":tok}
    while url:
        j = requests.get(url, params=params, timeout=40).json()
        if "error" in j: sys.exit("EROARE /me/accounts: " + j["error"].get("message",""))
        out += j.get("data", [])
        url = j.get("paging", {}).get("next"); params = None
    return out

def resolve(tok, brand):
    ps = pages(tok); nb = norm(brand)
    exact = [p for p in ps if norm(p["name"]) == nb]
    part  = [p for p in ps if nb in norm(p["name"]) or norm(p["name"]).startswith(nb)]
    cand = exact or part
    if not cand:
        sys.exit(f"Niciun brand potrivit cu '{brand}'. Rulează `list` pt paginile disponibile.")
    if len(cand) > 1:
        names = ", ".join(p["name"] for p in cand)
        sys.exit(f"Ambiguu '{brand}' → {names}. Fii mai specific.")
    return cand[0]

def blob_upload(path):
    """Urcă imaginea pe Vercel Blob → URL public (IG cere image_url public)."""
    tok = kb("BLOB_READ_WRITE_TOKEN_TOM") or kb("BLOB_READ_WRITE_TOKEN_SCENTUM")
    if not tok: sys.exit("Lipsește BLOB_READ_WRITE_TOKEN pt hosting IG.")
    data = open(path, "rb").read()
    ct = mimetypes.guess_type(path)[0] or "image/jpeg"
    name = "social/" + re.sub(r"[^A-Za-z0-9._-]","_", os.path.basename(path))
    r = requests.put(f"https://blob.vercel-storage.com/{name}",
                     headers={"authorization":"Bearer "+tok, "x-api-version":"7",
                              "x-content-type":ct, "x-add-random-suffix":"1"},
                     data=data, timeout=90).json()
    if not r.get("url"): sys.exit("Vercel Blob upload eșuat: " + json.dumps(r)[:200])
    return r["url"]

def is_url(s): return bool(s) and s.lower().startswith(("http://","https://"))
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi"}
def is_video(s): return bool(s) and os.path.splitext(s)[1].lower() in VIDEO_EXT

def post_fb(pg, text, image, link, sched_epoch, apply):
    pid, ptok = pg["id"], pg["access_token"]
    if image and is_video(image):
        ep = f"{G}/{pid}/videos"; body = {"description": text or "", "access_token": ptok}
        files = None
        if is_url(image): body["file_url"] = image
        else: files = {"source": (os.path.basename(image), open(image,"rb"))}
        kind = "video"
    elif image:
        ep = f"{G}/{pid}/photos"; body = {"caption": text or "", "access_token": ptok}
        files = None
        if is_url(image): body["url"] = image
        else: files = {"source": (os.path.basename(image), open(image,"rb"))}
        kind = "photo"
    elif link:
        ep = f"{G}/{pid}/feed"; body = {"message": text or "", "link": link, "access_token": ptok}; files=None; kind="link"
    else:
        ep = f"{G}/{pid}/feed"; body = {"message": text or "", "access_token": ptok}; files=None; kind="text"
    if sched_epoch:
        body["published"] = "false"; body["scheduled_publish_time"] = str(sched_epoch)
    if not apply:
        print(f"   [DRY] FB {kind} → {pg['name']} | text={ (text or '')[:60]!r}" + (f" | {'PROGRAMAT' if sched_epoch else 'ACUM'}"))
        return None
    r = requests.post(ep, data=body, files=files, timeout=120).json()
    if "error" in r: print("   ❌ FB:", r["error"].get("message","")); return None
    print(f"   ✅ FB {kind} postat → {pg['name']} | id={r.get('id') or r.get('post_id')}")
    return r

def post_ig(pg, text, media, apply):
    ig = pg.get("instagram_business_account")
    if not ig: print(f"   ⚠ {pg['name']} n-are Instagram legat — skip IG"); return None
    igid, ptok = ig["id"], pg["access_token"]
    if not media: print("   ⚠ IG cere poză sau video — skip IG"); return None
    vid = is_video(media)
    if not apply:
        print(f"   [DRY] IG {'REEL' if vid else 'poză'} → @{ig.get('username','?')} | media={'URL' if is_url(media) else 'fișier→Blob'} | caption={(text or '')[:60]!r}")
        return None
    url = media if is_url(media) else blob_upload(media)
    if vid:  # REEL — apare și pe feed-ul IG (share_to_feed) => cross-post cu FB video în aceeași comandă
        data = {"media_type":"REELS","video_url":url,"caption":text or "","share_to_feed":"true","access_token":ptok}
    else:
        data = {"image_url":url,"caption":text or "","access_token":ptok}
    c = requests.post(f"{G}/{igid}/media", data=data, timeout=120).json()
    if "error" in c: print("   ❌ IG container:", c["error"].get("message","")); return None
    cid = c["id"]
    for _ in range(40 if vid else 20):  # video = encoding mai lung
        st = requests.get(f"{G}/{cid}", params={"fields":"status_code","access_token":ptok}, timeout=30).json().get("status_code")
        if st == "FINISHED": break
        if st == "ERROR": print("   ❌ IG container ERROR (video invalid/prea mare?)"); return None
        time.sleep(4)
    r = requests.post(f"{G}/{igid}/media_publish", data={"creation_id":cid,"access_token":ptok}, timeout=90).json()
    if "error" in r: print("   ❌ IG publish:", r["error"].get("message","")); return None
    print(f"   ✅ IG {'reel' if vid else 'poză'} postat → @{ig.get('username','?')} | id={r.get('id')}")
    return r

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pp = sub.add_parser("post")
    pp.add_argument("--brand", required=True)
    pp.add_argument("--text", default="")
    pp.add_argument("--image", default="")
    pp.add_argument("--link", default="")
    pp.add_argument("--to", choices=["fb","ig","both"], default="both")
    pp.add_argument("--schedule", default="")  # "YYYY-MM-DD HH:MM" (FB only)
    pp.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    tok = su_token()
    if not tok: sys.exit("Lipsește tokenul The Wow Grid SU.")
    if a.cmd == "list":
        print("Branduri / pagini disponibile:")
        for p in sorted(pages(tok), key=lambda x: x["name"]):
            ig = p.get("instagram_business_account")
            print(f"  {p['name']:34} FB✅  {'IG @'+ig['username'] if ig and ig.get('username') else ('IG '+ig['id'] if ig else 'IG —')}")
        return
    pg = resolve(tok, a.brand)
    sched_epoch = None
    if a.schedule:
        dt = datetime.datetime.strptime(a.schedule, "%Y-%m-%d %H:%M")
        sched_epoch = int(dt.timestamp())
    if not (a.text or a.image or a.link): sys.exit("Nimic de postat: dă --text și/sau --image/--link.")
    print(f"{'APLIC' if a.apply else 'DRY-RUN'} — brand: {pg['name']}")
    if a.to in ("fb","both"):
        post_fb(pg, a.text, a.image, a.link, sched_epoch, a.apply)
    if a.to in ("ig","both") and not a.link:
        post_ig(pg, a.text, a.image, a.apply)
    if not a.apply:
        print("\n(DRY-RUN — nimic postat. Adaugă --apply ca să postezi REAL pe paginile publice.)")

if __name__ == "__main__":
    main()
