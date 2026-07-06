#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# ///
"""InstaPress catalog query — filtrează catalogul de site-uri advertoriale (instapress.ro)
salvat pe NAS, ca să găsești UNDE publici: nișă, dofollow, sub ce preț, cu ce DR/trafic.
Catalogul: $NAS_ROOT/data/instapress/instapress_catalog_full.json (2461 site RO, 2456 dofollow).
Refresh catalog: vezi SKILL.md (login browser + fetch marketplace.php)."""
import os, json, argparse, sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def find_catalog():
    nas = os.environ.get("NAS_ROOT", os.path.expanduser("~/nas/IT_Dev/ClaudeShared/gigi"))
    for p in (os.path.join(nas, "data", "instapress", "instapress_catalog_full.json"),
              "instapress_catalog_full.json"):
        if os.path.exists(p):
            return p
    sys.exit("catalog negăsit; setează NAS_ROOT sau rulează refresh (vezi SKILL.md)")


def price_of(r, typ):
    for o in r.get("offers", []):
        if o.get("type") == typ:
            return o.get("total")
    return None


def main():
    ap = argparse.ArgumentParser(description="Interoghează catalogul InstaPress")
    ap.add_argument("--niche", help="filtru pe categorie (substring: Frumus, Modă, Știri, Sănăt, Mod de viata, Casă…)")
    ap.add_argument("--type", default="SEO", help="tip articol pt preț (SEO/HOMEPAGE/…); default SEO")
    ap.add_argument("--max-price", type=float)
    ap.add_argument("--min-dr", type=int, default=0)
    ap.add_argument("--dofollow", action="store_true", help="doar site-uri dofollow")
    ap.add_argument("--sort", choices=["dr", "price", "traffic"], default="dr")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--domain", help="caută un domeniu (substring)")
    a = ap.parse_args()

    rows = json.load(open(find_catalog(), encoding="utf-8"))
    out = []
    for r in rows:
        price = price_of(r, a.type)
        if a.type and price is None:
            continue
        if a.dofollow and r.get("linkType") != "dofollow":
            continue
        if (r.get("dr") or 0) < a.min_dr:
            continue
        if a.max_price and price and price > a.max_price:
            continue
        if a.niche and not any(a.niche.lower() in c.lower() for c in r.get("categories", [])):
            continue
        if a.domain and a.domain.lower() not in (r.get("domain") or "").lower():
            continue
        out.append((r, price))

    keyf = {"dr": lambda x: -(x[0].get("dr") or 0),
            "price": lambda x: (x[1] if x[1] is not None else 9e9),
            "traffic": lambda x: -(x[0].get("traffic") or 0)}[a.sort]
    out.sort(key=keyf)

    print(f"{len(out)} site-uri (tip {a.type}) | niche={a.niche} dofollow={a.dofollow} "
          f"min_dr={a.min_dr} max_price={a.max_price} sort={a.sort}\n")
    print(f"{'domain':32}{'DR':>4}{'trafic':>11}{'pret':>10}  categorii")
    for r, price in out[:a.limit]:
        pr = (str(round(price)) + " RON") if price else "-"
        print(f"{(r.get('domain') or '')[:32]:32}{r.get('dr') or 0:>4}"
              f"{str(r.get('traffic') or 0):>11}{pr:>10}  {'|'.join(r.get('categories', []))}")


if __name__ == "__main__":
    main()
