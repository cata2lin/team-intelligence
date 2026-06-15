# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""
Crawl-based internal-link audit for a store: BFS-crawl from the homepage (seeded
with the sitemap), build the internal link graph, compute internal PageRank,
click-depth, and inbound counts, and flag orphan / under-linked / too-deep pages.

Pure stdlib + requests/bs4, no API keys. Fixes go to gigi:shopify-seo's Admin-API
fixers; this is the DISCOVERY half (where the link equity is and isn't flowing).

Usage:
    uv run linkgraph.py audit --site https://esteban.ro --max 150
    uv run linkgraph.py audit --site esteban.ro --max 300 --threads 10
"""
import argparse, re, sys, xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urldefrag, urlparse
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; AronaLinkGraph/1.0)"}

def norm(base, host):
    if not base.startswith("http"):
        base = "https://" + base
    return base.rstrip("/")

def same_host(u, host):
    try: return urlparse(u).netloc.replace("www.", "") == host
    except Exception: return False

def clean(u):
    u, _ = urldefrag(u)
    return u.rstrip("/")

def get_sitemap_urls(base, host, cap):
    urls = set()
    queue = [base + "/sitemap.xml"]
    seen = set()
    while queue and len(urls) < cap * 3:
        sm = queue.pop()
        if sm in seen: continue
        seen.add(sm)
        try:
            r = requests.get(sm, headers=UA, timeout=20)
            if r.status_code != 200: continue
            root = ET.fromstring(r.content)
        except Exception:
            continue
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//s:loc", ns):
            u = (loc.text or "").strip()
            if not u:
                continue
            if urlparse(u).path.lower().endswith(".xml"):   # sub-sitemap (Shopify adds ?from=&to=)
                queue.append(u)
            elif same_host(u, host):
                urls.add(clean(u))
    return urls

def fetch_links(url, host):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return url, r.status_code, []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for a in soup.find_all("a", href=True):
            u = clean(urljoin(url, a["href"]))
            if same_host(u, host) and not re.search(r"\?|/cart|/account|/cdn/|\.(jpg|png|webp|css|js|pdf)$", u):
                out.append(u)
        return url, r.status_code, list(set(out))
    except Exception:
        return url, 0, []

def pagerank(out_links, nodes, d=0.85, it=30):
    N = len(nodes);
    if not N: return {}
    pr = {n: 1.0 / N for n in nodes}
    inbound = defaultdict(list)
    for src, outs in out_links.items():
        for t in outs:
            if t in pr: inbound[t].append(src)
    for _ in range(it):
        new = {}
        for n in nodes:
            s = 0.0
            for src in inbound.get(n, []):
                o = len(out_links.get(src, [])) or 1
                s += pr[src] / o
            new[n] = (1 - d) / N + d * s
        pr = new
    return pr

def cmd_audit(args):
    base = norm(args.site, "")
    host = urlparse(base).netloc.replace("www.", "")
    cap = args.max
    print(f"Crawl + internal-link audit — {base} (cap {cap} pages)", file=sys.stderr)
    sitemap = get_sitemap_urls(base, host, cap)
    print(f"  sitemap: {len(sitemap)} URLs", file=sys.stderr)

    # BFS from homepage, capped
    out_links = {}; depth = {base: 0}
    q = deque([base]); visited = set()
    with ThreadPoolExecutor(max_workers=args.threads) as ex:
        while q and len(visited) < cap:
            batch = []
            while q and len(batch) < args.threads and len(visited) + len(batch) < cap:
                u = q.popleft()
                if u in visited: continue
                visited.add(u); batch.append(u)
            if not batch: break
            for url, status, links in ex.map(lambda u: fetch_links(u, host), batch):
                out_links[url] = links
                for l in links:
                    if l not in depth: depth[l] = depth.get(url, 0) + 1
                    if l not in visited and len(visited) + len(q) < cap * 2:
                        q.append(l)
    # inbound counts over crawled graph
    inbound = defaultdict(int)
    for src, outs in out_links.items():
        for t in set(outs):
            inbound[t] += 1
    nodes = set(out_links) | {t for outs in out_links.values() for t in outs}
    pr = pagerank(out_links, nodes)

    orphans = sorted(u for u in sitemap if inbound.get(u, 0) == 0)
    under = sorted((inbound.get(u, 0), u) for u in sitemap if 0 < inbound.get(u, 0) < 3)
    deep = sorted((depth.get(u, 99), u) for u in sitemap if depth.get(u, 99) > 3)
    top_pr = sorted(((pr.get(u, 0), u) for u in nodes), reverse=True)[:10]

    print(f"\n{'='*70}\nInternal-link audit — {base}\n{'='*70}")
    print(f"  crawled {len(visited)} pages | sitemap {len(sitemap)} URLs | graph {len(nodes)} nodes")
    print(f"\n  ORPHANS (in sitemap, 0 internal inbound links) — {len(orphans)}:")
    for u in orphans[:20]: print(f"    {u}")
    if len(orphans) > 20: print(f"    … +{len(orphans)-20} more")
    print(f"\n  UNDER-LINKED (<3 inbound) — {len(under)}:")
    for c, u in under[:15]: print(f"    {c} inbound  {u}")
    print(f"\n  TOO DEEP (>3 clicks from home) — {len(deep)}:")
    for dep, u in deep[:15]: print(f"    depth {dep}  {u}")
    print(f"\n  TOP internal PageRank (where link equity concentrates):")
    for v, u in top_pr: print(f"    {v:.4f}  {u}")
    print(f"\n  → Fix orphans/under-linked by adding contextual internal links from high-PR pages"
          f"\n    (collections, blog hub) to the buried product/collection pages above.")

def main():
    ap = argparse.ArgumentParser(description="Crawl-based internal-link audit.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="crawl + internal-link graph audit")
    a.add_argument("--site", required=True); a.add_argument("--max", type=int, default=150)
    a.add_argument("--threads", type=int, default=8); a.set_defaults(fn=cmd_audit)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
