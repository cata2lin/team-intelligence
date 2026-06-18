# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""Find a legal-entity string (default "ARONA SRL") across ALL team Shopify stores and
noindex the SPECIFIC pages that display it — so the company name drops out of Google
search results WITHOUT removing the legally-required disclosure from the site.

Two subcommands:
  scan    read-only: per store, fetch the auto-generated /policies/* pages, the homepage
          footer, and every admin /pages/*, and report WHERE the term appears. Distinguishes
          FOOTER (site-wide -> cannot be noindexed) from dedicated policy/legal pages.
  apply   inject the team-standard <meta robots noindex,nofollow> conditional into
          layout/theme.liquid for each matching page path (dry-run by default; --apply to PUT).
          Snippet format is byte-identical to noindex_page.py, so any single path is
          reversible with:  uv run noindex_page.py --prefix <P> --path <PATH> --remove --apply

Usage:
  uv run noindex_legal_all.py scan                         # recon all stores
  uv run noindex_legal_all.py scan  --term "arona"         # custom term (case-insensitive)
  uv run noindex_legal_all.py apply                         # DRY-RUN: show planned insertions
  uv run noindex_legal_all.py apply --apply                # WRITE to live themes (backs up each)
  uv run noindex_legal_all.py apply --apply --only EST,GT   # restrict to some prefixes
  uv run noindex_legal_all.py verify                        # Googlebot-UA cache-busted re-check

Token resolution: prefers `core.stores.get_store` (resolves OAuth-rotation tokens when run
on the VPS) and falls back to the KB secret SHOPIFY_STORES_CSV. **OAuth-rotation stores
(e.g. NUB/Nubra) have a dead static token by design** -> run this ON THE VPS (see SKILL.md §3),
where get_store hands back the live access token.

WHY noindex and not "delete the name": Romanian/EU law REQUIRES displaying the company's
legal identification (name, CUI, J-number, address) on the storefront. noindex keeps that
disclosure on the site (crawlable) but removes the page from search results — the correct,
legal way to keep "ARONA SRL" out of Google. Never robots.txt-Disallow these pages (Google
must crawl them to SEE the noindex). FOOTER occurrences are site-wide -> they CANNOT be
noindexed (you'd deindex the whole shop) and should NOT be removed (legal) -> only flagged.
"""
import os, sys, re, csv, json, argparse, datetime, subprocess
import requests

API_DEFAULT = os.environ.get("SHOPIFY_API_VERSION", "2026-01")
UA = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
KB = os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
POLICY_HANDLES = ["contact-information", "refund-policy", "privacy-policy", "terms-of-service",
                  "shipping-policy", "legal-notice", "subscription-policy"]
ROBOTS_RE = re.compile(r'<meta[^>]+name=["\']robots["\'][^>]*noindex', re.I)


def kb_get(key):
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", key],
                              capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def load_stores():
    """Yield {prefix, shop, token}. Prefer core.stores (OAuth-aware); fall back to KB CSV."""
    try:
        sys.path.insert(0, os.getcwd())
        from core.stores import list_stores  # type: ignore
        rows = list_stores()
        if rows:
            return rows
    except Exception:
        pass
    out = []
    csv_text = kb_get("SHOPIFY_STORES_CSV")
    if csv_text:
        for r in csv.DictReader(csv_text.splitlines()):
            p = (r.get("prefix") or "").strip()
            if p:
                out.append({"prefix": p, "shop": (r.get("shop") or "").strip(),
                            "token": (r.get("token") or "").strip()})
    return out


def gql(shop, token, query, api):
    r = requests.post(f"https://{shop}/admin/api/{api}/graphql.json",
                      headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                      json={"query": query}, timeout=30)
    return r.status_code, r.json()


def get(url):
    try:
        r = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
        return r.status_code, r.text
    except Exception as e:
        return None, f"ERR {e}"


def text_ctx(html, term_re):
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    out = []
    for m in term_re.finditer(txt):
        a, b = max(0, m.start() - 55), min(len(txt), m.end() + 55)
        c = txt[a:b].strip()
        if c not in out:
            out.append(c)
    return out[:3]


def snippet(path):
    return ("{%- if request.path contains '" + path + "' -%}"
            '<meta name="robots" content="noindex, nofollow">{%- endif -%}')


def scan_store(s, term_re, api):
    """Return dict: prefix, domain, policy_paths[], page_paths[], footer(bool), ctx[]."""
    pfx, shop, token = s["prefix"], s["shop"], s.get("token", "")
    res = {"prefix": pfx, "shop": shop, "domain": None, "policy_paths": [], "page_paths": [],
           "footer": False, "ctx": [], "note": None}
    if not (shop and token):
        res["note"] = "no shop/token"
        return res
    st, j = gql(shop, token, "{ shop { primaryDomain { host } } }", api)
    if st != 200 or (j.get("data") or {}).get("shop") is None:
        res["note"] = f"admin {st} (OAuth-rotation store? run on VPS)"
        return res
    dom = ((j["data"]["shop"].get("primaryDomain") or {}).get("host")) or ""
    res["domain"] = dom
    if not dom:
        res["note"] = "no primary domain"
        return res
    # homepage footer (site-wide)
    hs, hh = get(f"https://{dom}/")
    if hs == 200 and term_re.search(hh):
        res["footer"] = True
        res["ctx"] = text_ctx(hh, term_re)
    # auto-generated policy pages
    for h in POLICY_HANDLES:
        ps, ph = get(f"https://{dom}/policies/{h}")
        if ps == 200 and term_re.search(ph):
            res["policy_paths"].append(f"/policies/{h}")
    # admin pages (custom /pages/*)
    st2, j2 = gql(shop, token, '{ pages(first:250){ edges{ node{ handle title body } } } }', api)
    if st2 == 200 and not j2.get("errors"):
        for e in (((j2.get("data") or {}).get("pages") or {}).get("edges") or []):
            n = e["node"]
            if term_re.search(n.get("body") or "") or term_re.search(n.get("title") or ""):
                # encoding-safe path: ASCII prefix up to first non-ascii char in the handle
                h = n["handle"]
                m = re.match(r"^[\x00-\x7f]+", h)
                ascii_prefix = m.group(0) if m else h
                res["page_paths"].append(f"/pages/{ascii_prefix}")
    return res


def cmd_scan(stores, term_re, api):
    print(f"{'PFX':7}{'DOMAIN':24}{'FOOTER':9}PAGES THAT DISPLAY THE TERM")
    print("-" * 100)
    flagged_footer = []
    for s in stores:
        r = scan_store(s, term_re, api)
        paths = r["policy_paths"] + r["page_paths"]
        foot = "SITE-WIDE" if r["footer"] else "-"
        if r["footer"]:
            flagged_footer.append((r["prefix"], r["domain"]))
        extra = f"  [{r['note']}]" if r["note"] else ""
        print(f"{r['prefix']:7}{(r['domain'] or '?'):24}{foot:9}{', '.join(paths) if paths else '(none)'}{extra}")
    if flagged_footer:
        print("\n⚠ FOOTER (site-wide) — term appears on EVERY page; cannot be noindexed and must")
        print("  stay for legal compliance. Only the dedicated pages above get noindexed:")
        for p, d in flagged_footer:
            print(f"    - {p} ({d})")


def cmd_apply(stores, term_re, api, do_apply, only):
    total = 0
    for s in stores:
        if only and s["prefix"].upper() not in only:
            continue
        r = scan_store(s, term_re, api)
        paths = r["policy_paths"] + r["page_paths"]
        if r["note"] and not paths:
            print(f"\n{r['prefix']}: {r['note']} — skipped")
            continue
        if not paths:
            print(f"\n{r['prefix']} ({r['domain']}): no pages with the term")
            continue
        shop, token = s["shop"], s["token"]
        H = {"X-Shopify-Access-Token": token}
        base = f"https://{shop}/admin/api/{api}"
        themes = requests.get(f"{base}/themes.json", headers=H, timeout=30).json().get("themes", [])
        main_t = next((t for t in themes if t.get("role") == "main"), None)
        if not main_t:
            print(f"\n{r['prefix']}: NO main theme!"); continue
        tid = main_t["id"]
        val = requests.get(f"{base}/themes/{tid}/assets.json", headers=H,
                           params={"asset[key]": "layout/theme.liquid"}, timeout=30).json()["asset"]["value"]
        orig = val
        print(f"\n{r['prefix']} ({r['domain']})  theme={tid} ({main_t.get('name')}){'  [FOOTER site-wide]' if r['footer'] else ''}")
        to_add = [p for p in paths if snippet(p) not in val]
        for p in paths:
            print(f"   {'+ ADD ' if p in to_add else '= have'} {p}")
        if not to_add:
            print("   (nothing to do)"); continue
        total += len(to_add)
        for p in to_add:
            i = val.lower().find("<head>")
            if i == -1:
                print(f"   !! no <head> — skipped {r['prefix']}"); val = orig; break
            ins = i + len("<head>")
            val = val[:ins] + "\n    " + snippet(p) + val[ins:]
        if do_apply and val != orig:
            bak = f"theme.liquid.{r['prefix']}.{datetime.datetime.now():%Y%m%d-%H%M%S}.bak"
            open(bak, "w").write(orig)
            put = requests.put(f"{base}/themes/{tid}/assets.json",
                               headers={**H, "Content-Type": "application/json"},
                               json={"asset": {"key": "layout/theme.liquid", "value": val}}, timeout=60)
            ok = put.status_code in (200, 201)
            print(f"   PUT {put.status_code} {'OK' if ok else put.text[:200]}  (backup {bak})")
    print(f"\nTOTAL paths to add: {total}  ({'APPLIED' if do_apply else 'DRY-RUN — add --apply'})")


def cmd_verify(stores, term_re, api):
    import random
    n = random.randint(100000, 999999)
    for s in stores:
        r = scan_store(s, term_re, api)
        paths = r["policy_paths"] + r["page_paths"]
        if not paths:
            continue
        dom = r["domain"]
        hs, _ = get(f"https://{dom}/?cb={n}")
        line = f"{r['prefix']:7}home={hs} "
        for p in paths:
            st, html = get(f"https://{dom}{p}?cb={n}")
            ok = st == 200 and bool(ROBOTS_RE.search(html))
            line += f"| {p.split('/')[-1][:18]}:{'OK' if ok else (str(st)+'?')} "
        print(line)
    print("\n(NB: storefront edge-cache can lag minutes after a theme edit. The authoritative")
    print(" check is the theme.liquid API read-back, not the cached storefront HTML.)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "apply", "verify"])
    ap.add_argument("--term", default="arona", help="case-insensitive term to find (default: arona)")
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--apply", action="store_true", help="(apply) actually PUT to live themes")
    ap.add_argument("--only", default="", help="comma-separated prefixes to limit to, e.g. EST,GT")
    a = ap.parse_args()
    term_re = re.compile(re.escape(a.term), re.I)
    stores = load_stores()
    if not stores:
        sys.exit("no stores resolved (need core.stores or KB SHOPIFY_STORES_CSV)")
    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    if a.cmd == "scan":
        cmd_scan([s for s in stores if not only or s["prefix"].upper() in only], term_re, a.api)
    elif a.cmd == "apply":
        cmd_apply(stores, term_re, a.api, a.apply, only)
    else:
        cmd_verify([s for s in stores if not only or s["prefix"].upper() in only], term_re, a.api)


if __name__ == "__main__":
    main()
