# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""
On-site CRO (conversion) auditor for Shopify pages — the thing nobody checks while
we pour organic + paid traffic onto the site. Scores conversion blockers on a
product/collection/home page and gives prioritized, RO + COD-aware fixes.

Pure stdlib + requests/bs4, no keys. Romanian e-commerce, COD-heavy: "plata la
livrare / ramburs" and "livrare gratuită" are major trust levers here.

Usage:
    uv run cro.py audit --url https://esteban.ro/products/<handle>
    uv run cro.py audit --url https://esteban.ro/collections/dama
"""
import argparse, re, sys
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; AronaCRO/1.0)"}

def has(text, *terms):
    t = text.lower(); return any(x in t for x in terms)

def cmd_audit(args):
    url = args.url
    r = requests.get(url, headers=UA, timeout=30)
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    body = soup.get_text(" ", strip=True)
    low = body.lower()
    ptype = "product" if "/products/" in url else "collection" if "/collections/" in url else "home"

    sig = {}  # name -> (ok 0/1, weight, note)
    # CTA
    cta = soup.find_all(["button", "input"], string=re.compile(r"(adaug|cumpăr|cumpar|add to cart|comand)", re.I))
    cta += soup.find_all(attrs={"name": re.compile("add", re.I)})
    cta += [b for b in soup.find_all(["button","a"]) if has(b.get_text(), "adaugă în coș","adauga in cos","cumpără","cumpara","comandă","comanda")]
    sig["cta_addtocart"] = (1 if cta else 0, 18 if ptype=="product" else 6, "buton Add-to-cart/Comandă prezent" if cta else "NU găsesc buton clar de Add-to-cart/Comandă")
    # price
    price = bool(soup.find(attrs={"class": re.compile("price", re.I)})) or bool(re.search(r"\d+[.,]?\d*\s*(lei|ron)", low))
    sig["price_visible"] = (1 if price else 0, 10 if ptype=="product" else 4, "preț vizibil" if price else "preț neclar/lipsă")
    # reviews / social proof
    rev = bool(re.search(r'aggregateRating|ratingValue|review', html, re.I)) or has(low, "recenzi", "review", "stele", "★")
    sig["social_proof"] = (1 if rev else 0, 14, "recenzii/social proof prezente" if rev else "FĂRĂ recenzii/rating vizibil (social proof = conversie)")
    # COD trust (RO lever)
    cod = has(low, "ramburs", "plata la livrare", "plătești la livrare", "platesti la livrare")
    sig["cod_trust"] = (1 if cod else 0, 12, "menționează plata la livrare/ramburs" if cod else "NU menționează plata la livrare (ramburs) — pârghie majoră de încredere în RO")
    # free shipping
    fs = has(low, "livrare gratuit", "transport gratuit")
    sig["free_shipping"] = (1 if fs else 0, 8, "livrare gratuită menționată" if fs else "fără mențiune de livrare gratuită / prag")
    # returns / guarantee
    ret = has(low, "retur", "garanți", "garanti", "14 zile", "30 de zile")
    sig["returns_guarantee"] = (1 if ret else 0, 8, "politică retur/garanție vizibilă" if ret else "fără retur/garanție vizibilă (reduce frica de cumpărare)")
    # urgency / scarcity
    urg = has(low, "ultimele", "stoc limitat", "doar", "se termină", "în stoc") or bool(re.search(r"\d+\s*(buc|în stoc|rămas)", low))
    sig["urgency"] = (1 if urg else 0, 6, "urgență/scarcity prezentă" if urg else "fără urgență/scarcity (stoc, 'ultimele X')")
    # images
    imgs = len(soup.find_all("img"))
    sig["images"] = (1 if imgs >= 3 else 0, 6 if ptype=="product" else 2, f"{imgs} imagini" + ("" if imgs>=3 else " — prea puține pt produs"))
    # description depth (product)
    wc = len(body.split())
    sig["description"] = (1 if wc >= 120 else 0, 6 if ptype=="product" else 2, f"{wc} cuvinte conținut" + ("" if wc>=120 else " — descriere subțire"))
    # email capture (popup-urile se încarcă prin JS — caut și loaderul Klaviyo/popup în HTML brut)
    cap = (bool(soup.find("input", attrs={"type": "email"})) or has(low, "newsletter", "abonează", "aboneaza", "10% reducere", "-10%")
           or has(html, "klaviyo", "_learnq", "klaviyoonsite", "static.klaviyo", "/onsite/", "privy", "justuno", "omnisend"))
    note = "captură email/popup prezentă (input sau loader Klaviyo/onsite)" if cap else "popup de email nu apare în HTML static (se încarcă prin JS — verifică vizual, poate exista)"
    sig["email_capture"] = (1 if cap else 0, 6, note)
    # mobile viewport
    mv = bool(soup.find("meta", attrs={"name": "viewport"}))
    sig["mobile_viewport"] = (1 if mv else 0, 4, "viewport mobil ok" if mv else "lipsă meta viewport (mobil)")

    total_w = sum(w for _, w, _ in sig.values())
    got = sum(w for ok, w, _ in sig.values() if ok)
    score = round(100 * got / total_w)
    print(f"\nCRO audit — {url}\n  tip pagină: {ptype}   SCOR: {score}/100   ({r.status_code}, {imgs} imagini, {wc} cuvinte)\n" + "="*64)
    for k, (ok, w, note) in sorted(sig.items(), key=lambda x: -x[1][1]):
        print(f"  {'✅' if ok else '❌'} [{w:>2}] {k:<18} {note}")
    fixes = [note for ok, w, note in (v for v in sig.values()) if not ok]
    print(f"\n  Fix-uri prioritare (după impact):")
    for ok, w, note in sorted(sig.values(), key=lambda x: -x[1]):
        if not ok: print(f"    - {note}")
    if score >= 80: print("    (pagina e solidă pe semnalele CRO de bază)")

def main():
    ap = argparse.ArgumentParser(description="On-site CRO auditor (RO/COD-aware).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="CRO audit of a page"); a.add_argument("--url", required=True); a.set_defaults(fn=cmd_audit)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
