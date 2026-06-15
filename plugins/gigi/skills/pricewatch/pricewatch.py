# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""
Competitor price monitor — keep a watchlist of competitor product URLs, extract
the current price + availability (JSON-LD first, then meta/selector fallbacks),
store an append-only price history, and flag changes / who undercuts us.

Local SQLite at ~/.cache/arona-pricewatch/prices.db. Pure stdlib + requests/bs4.
For hardened sites (eMAG/Notino with anti-bot) a simple fetch may be blocked —
fall back to Firecrawl/proxy (see library:scraper-construction).

Usage:
    uv run pricewatch.py add  --url <competitor product url> [--label "Sauvage 100ml @ Notino"] [--our 199]
    uv run pricewatch.py check                      # fetch all, show current price + Δ vs last + flags
    uv run pricewatch.py list
    uv run pricewatch.py history --url <url>
"""
import argparse, datetime as dt, json, os, re, sqlite3, sys
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
DB = os.path.expanduser("~/.cache/arona-pricewatch/prices.db")

def _db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS watch(url TEXT PRIMARY KEY, label TEXT, our_price REAL, added TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS hist(url TEXT, ts TEXT, price REAL, currency TEXT, availability TEXT, title TEXT)")
    return c

def _num(s):
    if s is None: return None
    s = str(s).replace("\xa0", " ").strip()
    m = re.search(r"\d[\d.,]*", s)
    if not m: return None
    v = m.group(0)
    # normalize: drop thousands sep, use . decimal
    if "," in v and "." in v: v = v.replace(".", "").replace(",", ".")
    elif "," in v: v = v.replace(",", ".")
    try: return float(v)
    except ValueError: return None

def extract(url):
    r = requests.get(url, headers=UA, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")[:70]
    price = currency = avail = None
    # 1) JSON-LD Product/Offer
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            objs = obj.get("@graph", [obj]) if isinstance(obj, dict) else [obj]
            for o in objs:
                if not isinstance(o, dict): continue
                offers = o.get("offers")
                if not offers: continue
                off = offers[0] if isinstance(offers, list) else offers
                if isinstance(off, dict):
                    price = price or _num(off.get("price") or off.get("lowPrice"))
                    currency = currency or off.get("priceCurrency")
                    avail = avail or (off.get("availability") or "").split("/")[-1]
    # 2) meta / itemprop fallbacks
    if price is None:
        for sel in [("meta", {"property": "product:price:amount"}), ("meta", {"property": "og:price:amount"}),
                    ("meta", {"itemprop": "price"}), (None, {"itemprop": "price"})]:
            t = soup.find(sel[0], attrs=sel[1]) if sel[0] else soup.find(attrs=sel[1])
            if t:
                price = _num(t.get("content") or t.get_text())
                if price: break
    if currency is None:
        t = soup.find("meta", attrs={"property": "product:price:currency"}) or soup.find("meta", attrs={"property": "og:price:currency"})
        currency = (t.get("content") if t else None) or "RON"
    return {"price": price, "currency": currency, "availability": avail or "?", "title": title, "status": r.status_code}

def extract_listing(url):
    r = requests.get(url, headers=UA, timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")
    name = desc = None; images = []; price = None; currency = "RON"; avail = "?"
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try: data = json.loads(tag.string or "{}")
        except Exception: continue
        for obj in (data if isinstance(data, list) else [data]):
            for o in (obj.get("@graph", [obj]) if isinstance(obj, dict) else [obj]):
                if not isinstance(o, dict): continue
                t = o.get("@type", "")
                if ("Product" in (t if isinstance(t, str) else "".join(t))) or o.get("offers"):
                    name = name or o.get("name")
                    desc = desc or o.get("description")
                    img = o.get("image")
                    if img: images += [i.get("url") if isinstance(i, dict) else i for i in (img if isinstance(img, list) else [img])]
                    off = o.get("offers"); off = off[0] if isinstance(off, list) else off
                    if isinstance(off, dict):
                        price = price or _num(off.get("price") or off.get("lowPrice"))
                        currency = off.get("priceCurrency") or currency
                        if avail == "?": avail = (off.get("availability") or "?").split("/")[-1]
    if not name: name = (soup.title.get_text(strip=True) if soup.title else "")
    if not desc:
        m = soup.find("meta", attrs={"name": "description"}); desc = (m.get("content") if m else "") or ""
    for m in soup.find_all("meta", attrs={"property": "og:image"}):
        if m.get("content"): images.append(m["content"])
    images = [i for i in dict.fromkeys(images) if i]
    bullets = [li.get_text(" ", strip=True) for li in soup.select("[class*=desc] li, [class*=product] li, .rte li, [itemprop=description] li")]
    bullets = [b for b in dict.fromkeys(bullets) if 3 < len(b) < 160][:10]
    dtext = BeautifulSoup(desc or "", "html.parser").get_text(" ", strip=True)
    return {"name": (name or "")[:80], "price": price, "currency": currency, "availability": avail,
            "desc_words": len(dtext.split()), "desc": dtext, "images": images, "bullets": bullets}

def cmd_listing(args):
    e = extract_listing(args.url)
    print(f"\nListing — {args.url}")
    print(f"  titlu: {e['name']}")
    print(f"  preț:  {e['price']} {e['currency']} ({e['availability']})")
    print(f"  poze:  {len(e['images'])}")
    for u in e["images"][:10]: print(f"     {u}")
    print(f"  descriere: {e['desc_words']} cuvinte")
    if e["bullets"]:
        print(f"  bullets/specs ({len(e['bullets'])}):")
        for b in e["bullets"]: print(f"     • {b[:90]}")
    print(f"  extras descriere: {e['desc'][:240]}")

def cmd_compare(args):
    comp = extract_listing(args.comp); our = extract_listing(args.our_url)
    print(f"\nListing compare — al nostru vs concurent")
    print(f"  {'':<14}{'AL NOSTRU':>12}{'CONCURENT':>12}")
    print(f"  {'poze':<14}{len(our['images']):>12}{len(comp['images']):>12}")
    print(f"  {'descriere (cuv)':<14}{our['desc_words']:>12}{comp['desc_words']:>12}")
    print(f"  {'bullets/specs':<14}{len(our['bullets']):>12}{len(comp['bullets']):>12}")
    print(f"  {'preț':<14}{str(our['price']):>12}{str(comp['price']):>12}")
    gaps = []
    if len(comp["images"]) > len(our["images"]): gaps.append(f"concurentul are {len(comp['images'])} poze vs {len(our['images'])} — adaugă imagini (unghiuri/lifestyle/scale)")
    if comp["desc_words"] > our["desc_words"] * 1.3: gaps.append(f"descriere concurent {comp['desc_words']} cuv vs {our['desc_words']} — extinde + adaugă beneficii/utilizare")
    if len(comp["bullets"]) > len(our["bullets"]): gaps.append(f"concurentul listează {len(comp['bullets'])} bullets/specs vs {len(our['bullets'])} — adaugă tabel de specificații")
    print("\n  Ce să îmbunătățim la listingul nostru:")
    for g in gaps or ["(listingul nostru e cel puțin la fel de bogat ca al concurentului)"]:
        print(f"    - {g}")

def cmd_add(args):
    c = _db()
    e = extract(args.url)
    c.execute("INSERT OR REPLACE INTO watch VALUES (?,?,?,?)",
              (args.url, args.label or e["title"], args.our, dt.datetime.now().isoformat(timespec="seconds")))
    if e["price"]:
        c.execute("INSERT INTO hist VALUES (?,?,?,?,?,?)",
                  (args.url, dt.datetime.now().isoformat(timespec="seconds"), e["price"], e["currency"], e["availability"], e["title"]))
    c.commit()
    print(f"adăugat: {args.label or e['title']} — {e['price']} {e['currency']} ({e['availability']})" if e["price"]
          else f"adăugat dar PREȚ NEEXTRAS (status {e['status']}; site cu anti-bot? → Firecrawl/proxy): {args.url}")

def cmd_check(args):
    c = _db()
    items = c.execute("SELECT url,label,our_price FROM watch").fetchall()
    if not items: sys.exit("Watchlist gol. Adaugă: pricewatch.py add --url ...")
    print(f"\nPrice check — {len(items)} produse  ({dt.date.today()})")
    print(f"  {'preț':>9}{'Δ ult.':>9}{'al nostru':>10}  produs")
    for url, label, our in items:
        prev = c.execute("SELECT price FROM hist WHERE url=? ORDER BY ts DESC LIMIT 1", (url,)).fetchone()
        e = extract(url)
        p = e["price"]
        if p is None:
            print(f"  {'—':>9}{'':>9}{'':>10}  ⚠️ neextras (anti-bot?) {label[:40]}"); continue
        c.execute("INSERT INTO hist VALUES (?,?,?,?,?,?)", (url, dt.datetime.now().isoformat(timespec="seconds"), p, e["currency"], e["availability"], e["title"]))
        d = (p - prev[0]) if prev and prev[0] else 0
        dtxt = f"{d:+.0f}" if d else "="
        flag = ""
        if d < 0: flag = "  🔻 a scăzut"
        if our and p < our: flag += f"  🔴 ne subcotează (noi {our:.0f})"
        elif our and p > our: flag += f"  🟢 mai scumpi (noi {our:.0f})"
        av = "" if e["availability"] in ("InStock", "?") else f"  [{e['availability']}]"
        print(f"  {p:>7,.0f}{e['currency'][:3]:>2}{dtxt:>9}{(str(int(our)) if our else '—'):>10}  {label[:38]}{flag}{av}")
    c.commit()
    print("\n  🔻 = preț scăzut vs ultima verificare · 🔴 = sub prețul nostru. Rulează periodic (cron) + alertă în ClickUp.")

def cmd_list(args):
    c = _db()
    for url, label, our, added in c.execute("SELECT url,label,our_price,added FROM watch ORDER BY label"):
        print(f"  {label[:45]:<45} our={our or '—'}  {url[:50]}")

def cmd_history(args):
    c = _db()
    rows = c.execute("SELECT ts,price,currency,availability FROM hist WHERE url=? ORDER BY ts DESC LIMIT 20", (args.url,)).fetchall()
    print(f"\nIstoric preț — {args.url}")
    for ts, p, cur, av in rows:
        print(f"  {ts[:16]}  {p:>8,.0f} {cur}  {av}")

def main():
    ap = argparse.ArgumentParser(description="Competitor price monitor.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("--url", required=True); a.add_argument("--label"); a.add_argument("--our", type=float); a.set_defaults(fn=cmd_add)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    h = sub.add_parser("history"); h.add_argument("--url", required=True); h.set_defaults(fn=cmd_history)
    ls = sub.add_parser("listing", help="extract a listing (title/desc/images/bullets)"); ls.add_argument("--url", required=True); ls.set_defaults(fn=cmd_listing)
    cp = sub.add_parser("compare", help="our listing vs a competitor's → improvement gaps"); cp.add_argument("--our-url", dest="our_url", required=True); cp.add_argument("--comp", required=True); cp.set_defaults(fn=cmd_compare)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
