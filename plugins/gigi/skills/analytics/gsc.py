# /// script
# requires-python = ">=3.9"
# dependencies = ["google-auth>=2.0", "requests>=2.31"]
# ///
"""
Google Search Console puller for the team — the REAL SEO data GA4 can't give:
search QUERIES (keywords), impressions, clicks, CTR and average POSITION, per site.

Credentials: the shared `looker-sheets` service account JSON from the KB secret
`GA4_SA_JSON` (scope webmasters.readonly). The SA must be a *Full* user on each
Search Console property (Settings -> Users and permissions in Search Console).

Usage:
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
    uv run gsc.py sites                          # every site the SA can read + permission
    uv run gsc.py queries --brand esteban        # top search queries, last 28 days
    uv run gsc.py queries --site grandia.ro --days 90 --limit 40
    uv run gsc.py pages   --brand esteban        # top landing pages from Google search
    uv run gsc.py summary --brand esteban        # totals (clicks/impr/ctr/position)
    uv run gsc.py summary --all                  # totals for every known site
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, urllib.parse
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import requests

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# brand -> Search Console site (all Domain properties = sc-domain:)
SITES = {
    "esteban": "sc-domain:esteban.ro", "grandia": "sc-domain:grandia.ro",
    "nubra": "sc-domain:nubra.ro", "gt": "sc-domain:george-talent.ro",
    "george-talent": "sc-domain:george-talent.ro", "belasil": "sc-domain:belasil.ro",
    "gento": "sc-domain:gento.ro", "covoria": "sc-domain:covoria.ro",
    "carpetto": "sc-domain:carpetto.ro", "labnoir": "sc-domain:labnoir.ro",
    "apreciat": "sc-domain:apreciat.ro", "casa-ofertelor": "sc-domain:casaofertelor.ro",
    "oriceredus": "sc-domain:oriceredus.ro", "reduceribune": "sc-domain:reduceribune.ro",
    "bonhaus-bg": "sc-domain:bonhaus.bg", "bonhaus-cz": "sc-domain:bonhaus.cz",
    "bonhaus-pl": "sc-domain:bonhaus.pl", "nocturna-bg": "sc-domain:nocturna.bg",
}

# brand-name tokens for the brand vs non-brand split (fuzzy, so typos like
# "estaban"/"numbra"/"berasil" still count as brand search, not SEO wins).
BRAND_TOKENS = {
    "esteban": ["esteban", "maison esteban"], "grandia": ["grandia"], "nubra": ["nubra"],
    "gt": ["georgetalent", "george talent", "gt"], "george-talent": ["georgetalent", "george talent", "gt"],
    "belasil": ["belasil"], "gento": ["gento"], "covoria": ["covoria"], "carpetto": ["carpetto"],
    "labnoir": ["labnoir", "lab noir"], "apreciat": ["apreciat"],
    "casa-ofertelor": ["casaofertelor", "casa ofertelor"], "oriceredus": ["oriceredus", "orice redus"],
    "reduceribune": ["reduceribune", "reduceri bune"], "nocturna-bg": ["nocturna"],
    "bonhaus-bg": ["bonhaus"], "bonhaus-cz": ["bonhaus"], "bonhaus-pl": ["bonhaus"],
}

def _norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())

def _lev(a, b):
    if a == b: return 0
    if not a or not b: return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def _tokens_for(brand_key, site):
    if brand_key and brand_key.lower() in BRAND_TOKENS:
        return BRAND_TOKENS[brand_key.lower()]
    stem = site.replace("sc-domain:", "").replace("https://", "").replace("http://", "").split("/")[0].split(".")[0]
    return [stem, stem.replace("-", " "), stem.replace("-", "")]

def _is_brand(query, tokens):
    ql = query.lower(); comp = _norm(query)
    for t in tokens:
        tn = _norm(t)
        if t in ql or (tn and tn in comp): return True
        if tn and len(comp) <= len(tn) + 3 and _lev(comp, tn) <= 2: return True
    for w in re.findall(r"[a-z0-9]+", ql):
        if len(w) >= 4:
            for t in tokens:
                tn = _norm(t)
                if tn and abs(len(w) - len(tn)) <= 2 and _lev(w, tn) <= 2: return True
    return False

def _pct(cur, prev):
    if prev == 0: return "  —  " if cur == 0 else " new "
    return f"{100*(cur-prev)/prev:+5.0f}%"

def _find_kb():
    for c in [os.environ.get("KB_PY"),
              os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")]:
        if c and os.path.exists(c):
            return os.path.abspath(c)
    return None

def load_creds():
    raw = os.environ.get("GA4_SA_JSON")
    if not raw:
        kb = _find_kb()
        if kb:
            try:
                raw = subprocess.run(["uv", "run", kb, "secret-get", "GA4_SA_JSON"],
                                     capture_output=True, text=True, timeout=60).stdout.strip()
            except Exception:
                raw = ""
    if not raw:
        for p in ("google_credentials.json", os.path.expanduser("~/Downloads/Scripturi/google_credentials.json")):
            if os.path.exists(p):
                raw = open(p).read(); break
    if not raw:
        sys.exit("No GA4_SA_JSON credential. Set it (kb.py secret-get GA4_SA_JSON) and re-run.")
    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    creds.refresh(Request())
    return creds

def _req(method, url, token, body=None, tries=5):
    for attempt in range(1, tries + 1):
        try:
            r = requests.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                 json=body, timeout=120)
        except requests.exceptions.RequestException as e:
            sys.stderr.write(f"  (retry {attempt}/{tries} {type(e).__name__})\n"); continue
        if r.status_code in (429, 500, 502, 503, 504):
            sys.stderr.write(f"  (retry {attempt}/{tries} HTTP {r.status_code})\n"); continue
        if r.status_code != 200:
            sys.exit(f"GSC API error {r.status_code}: {r.text[:400]}")
        return r.json()
    sys.exit("GSC API failed after retries.")

def _site(args):
    if getattr(args, "site", None):
        s = args.site
        if not s.startswith(("sc-domain:", "http")):
            s = "sc-domain:" + s
        return s
    if getattr(args, "brand", None):
        s = SITES.get(args.brand.lower())
        if not s:
            sys.exit(f"Unknown brand '{args.brand}'. Known: {', '.join(sorted(set(SITES)))}")
        return s
    sys.exit("Pass --site <domain> or --brand <name> (or --all for summary).")

def _range(days):
    end = dt.date.today() - dt.timedelta(days=3)   # GSC data lags ~2-3 days
    return (end - dt.timedelta(days=days - 1)).isoformat(), end.isoformat()

def _query(token, site, start, end, dimensions, limit=25):
    url = f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query"
    body = {"startDate": start, "endDate": end, "rowLimit": limit}
    if dimensions:
        body["dimensions"] = dimensions
    return _req("POST", url, token, body)

def cmd_sites(creds, args):
    j = _req("GET", "https://www.googleapis.com/webmasters/v3/sites", creds.token)
    sites = j.get("siteEntry", [])
    print(f"{len(sites)} sites:")
    for s in sorted(sites, key=lambda x: x.get("siteUrl", "")):
        print(f"  {s.get('permissionLevel',''):<18} {s.get('siteUrl')}")

def _print_rows(j, dim_label, start, end, site):
    rows = j.get("rows", [])
    print(f"\n{site}   {start}..{end}   ({len(rows)} rows)")
    print(f"  {'clicks':>8}{'impr':>9}{'CTR':>7}{'pos':>7}  {dim_label}")
    for r in rows:
        k = r.get("keys", ["—"])[0]
        print(f"  {int(r['clicks']):>8,}{int(r['impressions']):>9,}{100*r['ctr']:>6.1f}%{r['position']:>7.1f}  {k[:70]}")

def cmd_queries(creds, args):
    s = _site(args); start, end = _range(args.days)
    _print_rows(_query(creds.token, s, start, end, ["query"], args.limit), "query", start, end, s)

def cmd_pages(creds, args):
    s = _site(args); start, end = _range(args.days)
    _print_rows(_query(creds.token, s, start, end, ["page"], args.limit), "page", start, end, s)

def _summary_for(token, site, start, end):
    j = _query(token, site, start, end, None, 1)
    rows = j.get("rows", [])
    if not rows:
        print(f"  {site:<32} (no data)"); return
    m = rows[0]
    print(f"  {site:<32}{int(m['clicks']):>10,}{int(m['impressions']):>11,}{100*m['ctr']:>7.1f}%{m['position']:>8.1f}")

def cmd_summary(creds, args):
    start, end = _range(args.days)
    print(f"Search Console totals   {start}..{end}")
    print(f"  {'site':<32}{'clicks':>10}{'impr':>11}{'CTR':>8}{'pos':>8}")
    if args.all:
        for site in sorted(set(SITES.values())):
            _summary_for(creds.token, site, start, end)
    else:
        _summary_for(creds.token, _site(args), start, end)

def _sa_query(token, site, body):
    url = f"https://www.googleapis.com/webmasters/v3/sites/{urllib.parse.quote(site, safe='')}/searchAnalytics/query"
    return _req("POST", url, token, body)

def cmd_rank(creds, args):
    site = _site(args); start, end = _range(args.days)
    if args.contains:
        body = {"startDate": start, "endDate": end, "dimensions": ["query"], "rowLimit": args.limit,
                "dimensionFilterGroups": [{"filters": [{"dimension": "query", "operator": "contains",
                                                         "expression": args.contains.lower()}]}]}
        rows = _sa_query(creds.token, site, body).get("rows", [])
        rows.sort(key=lambda r: -r["impressions"])
        print(f"\n{site}   query CONTAINS '{args.contains}'   {start}..{end}   ({len(rows)} keywords)")
        print(f"  {'pos':>5}{'clicks':>8}{'impr':>8}{'CTR':>7}  query")
        for r in rows:
            print(f"  {r['position']:>5.1f}{int(r['clicks']):>8,}{int(r['impressions']):>8,}{100*r['ctr']:>6.1f}%  {r['keys'][0][:55]}")
        return
    kws = [k.strip() for k in (args.query or "").split("|") if k.strip()]
    if not kws:
        sys.exit('Pass --query "kw1|kw2" (exact) or --contains "term" (keyword group).')
    print(f"\n{site}   poziția noastră pe cuvinte cheie   {start}..{end}")
    print(f"  {'pos':>5}{'clicks':>7}{'impr':>7}{'CTR':>7}  keyword  ->  ranking page")
    for kw in kws:
        body = {"startDate": start, "endDate": end, "dimensions": ["query", "page"], "rowLimit": 25,
                "dimensionFilterGroups": [{"filters": [{"dimension": "query", "operator": "equals",
                                                        "expression": kw.lower()}]}]}
        rows = _sa_query(creds.token, site, body).get("rows", [])
        if not rows:
            print(f"  {'—':>5}{0:>7}{0:>7}{'—':>7}  {kw}  ->  (nu apărem / 0 impresii)")
            continue
        b = max(rows, key=lambda r: r["impressions"])
        page = b["keys"][1].replace("https://", "").replace("http://", "")
        print(f"  {b['position']:>5.1f}{int(b['clicks']):>7,}{int(b['impressions']):>7,}{100*b['ctr']:>6.1f}%  {kw}  ->  {page[:46]}")

def _totals(token, site, s, e):
    r = _query(token, site, s, e, None, 1).get("rows", [])
    return r[0] if r else {"clicks": 0, "impressions": 0, "ctr": 0, "position": 0}

def _brand_split(token, site, s, e, tokens):
    nb_c = nb_i = b_c = 0; nb = []
    for r in _query(token, site, s, e, ["query"], 25000).get("rows", []):
        c = int(r["clicks"]); i = int(r["impressions"]); q = r["keys"][0]
        if _is_brand(q, tokens):
            b_c += c
        else:
            nb_c += c; nb_i += i; nb.append((c, i, r["position"], q))
    nb.sort(reverse=True)
    return nb_c, nb_i, b_c, nb

def cmd_wow(creds, args):
    n = args.days
    end = dt.date.today() - dt.timedelta(days=3)             # GSC lag
    Ls, Le = (end - dt.timedelta(days=n - 1)).isoformat(), end.isoformat()
    Ps, Pe = (end - dt.timedelta(days=2 * n - 1)).isoformat(), (end - dt.timedelta(days=n)).isoformat()
    tok = creds.token
    if args.all:
        print(f"Search Console WoW — last{n}d ({Ls}..{Le}) vs prior{n}d   [cur/prev Δ]")
        print(f"  {'site':<24}{'clicks':>22}{'impressions':>22}{'nb-clicks':>20}{'pos':>13}")
        seen = {}
        for bk, site in SITES.items():
            seen.setdefault(site, bk)
        for site, bk in sorted(seen.items()):
            cL, cP = _totals(tok, site, Ls, Le), _totals(tok, site, Ps, Pe)
            toks = _tokens_for(bk, site)
            nbL = _brand_split(tok, site, Ls, Le, toks)[0]; nbP = _brand_split(tok, site, Ps, Pe, toks)[0]
            print(f"  {site.replace('sc-domain:',''):<24}"
                  f"{int(cL['clicks']):>6,}/{int(cP['clicks']):<6,}{_pct(cL['clicks'],cP['clicks']):>6}"
                  f"{int(cL['impressions']):>7,}/{int(cP['impressions']):<7,}{_pct(cL['impressions'],cP['impressions']):>6}"
                  f"{nbL:>5,}/{nbP:<5,}{_pct(nbL,nbP):>5}{cL['position']:>6.1f}/{cP['position']:<5.1f}")
        return
    site = _site(args); toks = _tokens_for(args.brand, site)
    cL, cP = _totals(tok, site, Ls, Le), _totals(tok, site, Ps, Pe)
    nbcL, nbiL, bcL, nbL = _brand_split(tok, site, Ls, Le, toks)
    nbcP, nbiP, bcP, _ = _brand_split(tok, site, Ps, Pe, toks)
    print(f"\n{site}   last{n}d ({Ls}..{Le}) vs prior{n}d ({Ps}..{Pe})")
    print(f"  clicks       {int(cL['clicks']):>8,} vs {int(cP['clicks']):>8,}  {_pct(cL['clicks'],cP['clicks'])}")
    print(f"  impressions  {int(cL['impressions']):>8,} vs {int(cP['impressions']):>8,}  {_pct(cL['impressions'],cP['impressions'])}")
    print(f"  CTR          {100*cL['ctr']:>7.1f}% vs {100*cP['ctr']:>6.1f}%")
    better = 'better' if cL['position'] < cP['position'] else 'worse' if cL['position'] > cP['position'] else 'flat'
    print(f"  avg position {cL['position']:>8.1f} vs {cP['position']:>8.1f}  ({better})")
    print(f"  -- non-brand (the real SEO signal; brand-typos folded into brand) --")
    print(f"  nb clicks    {nbcL:>8,} vs {nbcP:>8,}  {_pct(nbcL,nbcP)}")
    print(f"  nb impr      {nbiL:>8,} vs {nbiP:>8,}  {_pct(nbiL,nbiP)}")
    print(f"  brand clicks {bcL:>8,} vs {bcP:>8,}  {_pct(bcL,bcP)}  (demand, not SEO)")
    days = [(r["keys"][0][5:], int(r["clicks"])) for r in _query(tok, site, Ps, Le, ["date"], 1000).get("rows", [])]
    if days:
        print("  daily clicks: " + " ".join(f"{d}:{c}" for d, c in days))
    print("  top non-brand queries (last window):")
    for c, i, pos, q in nbL[:10]:
        print(f"    {c:>5,} cl {i:>6,} imp  pos {pos:>4.1f}  {q[:55]}")

def _opps_for(token, site, tokens, days, min_impr):
    start, end = _range(days)
    rows = _sa_query(token, site, {"startDate": start, "endDate": end, "dimensions": ["query"], "rowLimit": 25000}).get("rows", [])
    strike, lowctr = [], []
    for r in rows:
        c = int(r["clicks"]); i = int(r["impressions"]); pos = r["position"]; ctr = r["ctr"]; q = r["keys"][0]
        if _is_brand(q, tokens) or i < min_impr:
            continue
        if 5 <= pos <= 20:
            strike.append((i, pos, c, ctr, q))
        if pos <= 5 and ctr < 0.20:
            lowctr.append((i, pos, c, ctr, q))
    strike.sort(reverse=True); lowctr.sort(reverse=True)
    return strike, lowctr

def cmd_opportunities(creds, args):
    targets = ([(bk, s) for s, bk in {v: k for k, v in SITES.items()}.items()] if args.all
              else [(args.brand, _site(args))])
    for bk, site in sorted(targets, key=lambda x: x[1]):
        toks = _tokens_for(bk, site)
        strike, lowctr = _opps_for(creds.token, site, toks, args.days, args.min_impr)
        print(f"\n{'='*70}\n{site}   non-brand SEO opportunities   (last {args.days}d, impr>={args.min_impr})\n{'='*70}")
        print(f"  STRIKING DISTANCE (pos 5-20 → push to page 1), by impressions:")
        if not strike: print("    (none)")
        for i, pos, c, ctr, q in strike[:15]:
            print(f"    pos {pos:>4.1f}  {i:>6,} imp  {c:>4,} cl  {100*ctr:>4.1f}%  {q[:52]}")
        print(f"  LOW CTR despite TOP rank (pos<=5, CTR<20% → rewrite title/meta):")
        if not lowctr: print("    (none)")
        for i, pos, c, ctr, q in lowctr[:8]:
            print(f"    pos {pos:>4.1f}  {i:>6,} imp  {100*ctr:>4.1f}% CTR  {q[:52]}")

def cmd_index(creds, args):
    site = _site(args)
    urls = [u.strip() for u in (args.url or "").split("|") if u.strip()]
    if not urls:
        sys.exit('Pass --url "https://site/page|https://site/page2"')
    print(f"\nIndex status — {site}")
    print(f"  {'coverage':<40}{'last crawl':<12} url")
    for u in urls:
        body = {"inspectionUrl": u, "siteUrl": site, "languageCode": "ro"}
        ok = False
        for _ in range(4):
            r = requests.post("https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                              headers={"Authorization": f"Bearer {creds.token}"}, json=body, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504): continue
            ok = True; break
        if not ok or r.status_code != 200:
            print(f"  ERR {r.status_code}: {r.text[:90]}  {u}"); continue
        idx = r.json().get("inspectionResult", {}).get("indexStatusResult", {})
        print(f"  {idx.get('coverageState','?')[:38]:<40}{(idx.get('lastCrawlTime','—') or '—')[:10]:<12} {u[:60]}")

def main():
    ap = argparse.ArgumentParser(description="Pull Google Search Console (keywords/pages/position) for the team.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sites", help="list sites the SA can read").set_defaults(fn=cmd_sites)

    def common(p, all_opt=False):
        p.add_argument("--brand"); p.add_argument("--site")
        if all_opt: p.add_argument("--all", action="store_true")
        p.add_argument("--days", type=int, default=28)

    q = sub.add_parser("queries", help="top search queries (keywords)"); common(q)
    q.add_argument("--limit", type=int, default=25); q.set_defaults(fn=cmd_queries)
    pg = sub.add_parser("pages", help="top landing pages from Google search"); common(pg)
    pg.add_argument("--limit", type=int, default=25); pg.set_defaults(fn=cmd_pages)
    sm = sub.add_parser("summary", help="totals clicks/impr/ctr/position"); common(sm, all_opt=True)
    sm.set_defaults(fn=cmd_summary)
    ww = sub.add_parser("wow", help="week-over-week: last N days vs prior N, brand vs non-brand split")
    ww.add_argument("--brand"); ww.add_argument("--site"); ww.add_argument("--all", action="store_true")
    ww.add_argument("--days", type=int, default=7)
    ww.set_defaults(fn=cmd_wow)
    rk = sub.add_parser("rank", help="our position for exact keywords (--query \"a|b\") or a group (--contains)")
    rk.add_argument("--brand"); rk.add_argument("--site")
    rk.add_argument("--query"); rk.add_argument("--contains")
    rk.add_argument("--days", type=int, default=28); rk.add_argument("--limit", type=int, default=50)
    rk.set_defaults(fn=cmd_rank)
    op = sub.add_parser("opportunities", help="non-brand SEO quick-wins: striking-distance + low-CTR keywords")
    op.add_argument("--brand"); op.add_argument("--site"); op.add_argument("--all", action="store_true")
    op.add_argument("--days", type=int, default=28); op.add_argument("--min-impr", type=int, default=30, dest="min_impr")
    op.set_defaults(fn=cmd_opportunities)
    ix = sub.add_parser("index", help="URL index status (is a page indexed?) via GSC URL Inspection")
    ix.add_argument("--brand"); ix.add_argument("--site"); ix.add_argument("--url", required=True)
    ix.set_defaults(fn=cmd_index)

    args = ap.parse_args()
    args.fn(load_creds(), args)

if __name__ == "__main__":
    main()
