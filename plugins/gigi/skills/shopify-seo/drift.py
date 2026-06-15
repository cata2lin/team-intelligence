# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""
SEO drift baseline — snapshot the SEO-critical fields of pages into local SQLite,
then compare later to catch SILENT regressions (a theme update / app that quietly
drops a title, canonical, schema, or flips noindex). Complements GSC week-over-week.

Local DB: ~/.cache/arona-seo/drift.db. Pure stdlib + requests/bs4, no keys.

Usage:
    uv run drift.py baseline --url https://esteban.ro/collections/dama
    uv run drift.py baseline --site esteban.ro --max 40        # snapshot top pages from sitemap
    uv run drift.py compare  --url https://esteban.ro/collections/dama   # diff vs last snapshot
    uv run drift.py history  --url https://esteban.ro/collections/dama
"""
import argparse, hashlib, json, os, sqlite3, sys, datetime as dt
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; AronaDrift/1.0)"}
DB = os.path.expanduser("~/.cache/arona-seo/drift.db")

def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS snap(
        url TEXT, ts TEXT, status INT, title TEXT, meta_desc TEXT, canonical TEXT,
        robots TEXT, h1 TEXT, h2_count INT, jsonld TEXT, og_title TEXT, og_image TEXT,
        word_count INT, hash TEXT)""")
    return c

def snapshot(url):
    try:
        r = requests.get(url, headers=UA, timeout=30)
    except Exception as e:
        return {"url": url, "status": 0, "error": str(e)}
    soup = BeautifulSoup(r.text, "html.parser")
    def meta(name=None, prop=None):
        t = soup.find("meta", attrs={"name": name} if name else {"property": prop})
        return (t.get("content") or "").strip() if t else ""
    can = soup.find("link", rel="canonical")
    ld_types = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            d = json.loads(tag.string or "{}")
            for o in (d if isinstance(d, list) else [d]):
                if isinstance(o, dict) and o.get("@type"): ld_types.append(o["@type"])
        except Exception:
            pass
    h1 = " | ".join(h.get_text(" ", strip=True) for h in soup.find_all("h1"))
    body_words = len((soup.get_text(" ", strip=True)).split())
    s = {
        "url": url, "ts": dt.datetime.now().isoformat(timespec="seconds"), "status": r.status_code,
        "title": (soup.title.get_text(strip=True) if soup.title else ""),
        "meta_desc": meta(name="description"), "canonical": (can.get("href") if can else ""),
        "robots": meta(name="robots"), "h1": h1, "h2_count": len(soup.find_all("h2")),
        "jsonld": ",".join(sorted(set(map(str, ld_types)))), "og_title": meta(prop="og:title"),
        "og_image": meta(prop="og:image"), "word_count": body_words,
    }
    s["hash"] = hashlib.sha256(json.dumps([s["title"], s["meta_desc"], s["canonical"], s["robots"], s["jsonld"]], ensure_ascii=False).encode()).hexdigest()[:16]
    return s

def sitemap_urls(domain, cap):
    base = domain if domain.startswith("http") else "https://" + domain
    base = base.rstrip("/"); urls = []; queue = [base + "/sitemap.xml"]; seen = set()
    while queue and len(urls) < cap:
        sm = queue.pop()
        if sm in seen: continue
        seen.add(sm)
        try:
            root = ET.fromstring(requests.get(sm, headers=UA, timeout=20).content)
        except Exception:
            continue
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//s:loc", ns):
            u = (loc.text or "").strip()
            if urlparse(u).path.lower().endswith(".xml"): queue.append(u)
            elif u: urls.append(u)
    return urls[:cap]

def cmd_baseline(args):
    c = _db()
    urls = [args.url] if args.url else sitemap_urls(args.site, args.max)
    n = 0
    for u in urls:
        s = snapshot(u)
        if s.get("status") in (0, None):
            print(f"  skip {u} ({s.get('error','no status')})"); continue
        c.execute("INSERT INTO snap VALUES (:url,:ts,:status,:title,:meta_desc,:canonical,:robots,:h1,:h2_count,:jsonld,:og_title,:og_image,:word_count,:hash)", s)
        n += 1
    c.commit()
    print(f"baseline: {n} pagini salvate în {DB}")

CRIT = {"title", "canonical", "status", "robots", "jsonld"}
def cmd_compare(args):
    c = _db()
    cur_ = c.execute("SELECT * FROM snap WHERE url=? ORDER BY ts DESC LIMIT 1", (args.url,))
    rows = cur_.fetchall()
    if not rows:
        print("Nicio bază anterioară — rulează 'baseline' întâi."); return
    cols = [d[0] for d in cur_.description]
    prev = dict(zip(cols, rows[0]))
    cur = snapshot(args.url)
    print(f"\nDrift — {args.url}\n  bază: {prev['ts']}  →  acum")
    changed = False
    for f in ["status", "title", "meta_desc", "canonical", "robots", "h1", "h2_count", "jsonld", "og_title", "og_image", "word_count"]:
        a, b = prev.get(f), cur.get(f)
        if str(a) != str(b):
            changed = True
            lvl = "🔴 CRITIC" if f in CRIT else ("🟡 WARN" if f in ("meta_desc","h1","og_title","og_image") else "ℹ️  info")
            if f == "word_count" and a and b and abs(int(b)-int(a)) > 0.3*int(a): lvl = "🟡 WARN"
            print(f"  {lvl}  {f}:")
            print(f"      era : {str(a)[:90]}")
            print(f"      acum: {str(b)[:90]}")
    # special: noindex flip
    if "noindex" in (cur.get("robots") or "").lower() and "noindex" not in (prev.get("robots") or "").lower():
        print("  🔴 CRITIC  pagina a devenit NOINDEX!")
    if not changed:
        print("  ✓ niciun drift (identic cu baza)")
    else:
        print("\n  → salvează noua stare cu 'baseline' dacă schimbările sunt intenționate.")

def cmd_history(args):
    c = _db()
    rows = c.execute("SELECT ts,status,title,canonical,robots,hash FROM snap WHERE url=? ORDER BY ts DESC LIMIT 15", (args.url,)).fetchall()
    print(f"\nIstoric snapshots — {args.url} ({len(rows)})")
    for ts, st, title, can, rob, h in rows:
        print(f"  {ts}  [{st}] {h}  {title[:50]}")

def main():
    ap = argparse.ArgumentParser(description="SEO drift baseline (local SQLite).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline", help="snapshot a URL or a site's sitemap"); b.add_argument("--url"); b.add_argument("--site"); b.add_argument("--max", type=int, default=40); b.set_defaults(fn=cmd_baseline)
    cp = sub.add_parser("compare", help="diff a URL vs its last snapshot"); cp.add_argument("--url", required=True); cp.set_defaults(fn=cmd_compare)
    h = sub.add_parser("history", help="list snapshots for a URL"); h.add_argument("--url", required=True); h.set_defaults(fn=cmd_history)
    args = ap.parse_args()
    if args.cmd == "baseline" and not args.url and not args.site:
        sys.exit("baseline needs --url or --site")
    args.fn(args)

if __name__ == "__main__":
    main()
