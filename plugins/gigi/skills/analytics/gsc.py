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
import argparse, datetime as dt, json, os, subprocess, sys, urllib.parse
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

    args = ap.parse_args()
    args.fn(load_creds(), args)

if __name__ == "__main__":
    main()
