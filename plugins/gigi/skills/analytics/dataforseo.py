# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""
DataForSEO — the PAID data layer that fills our SERP + backlinks + competitor gaps.
Live Google RO SERP (who ranks), a domain's ranked keywords (mine competitors),
and backlink/referring-domain summaries. Pay-as-you-go — each call costs money.

Creds in KB secrets DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD (Basic auth). No IP
whitelist needed. Default market: Romania / Romanian.

Usage:
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export DATAFORSEO_LOGIN="$(uv run "$KB" secret-get DATAFORSEO_LOGIN)"
    export DATAFORSEO_PASSWORD="$(uv run "$KB" secret-get DATAFORSEO_PASSWORD)"
    uv run dataforseo.py serp --keyword "parfumuri barbati"        # who ranks top in Google RO
    uv run dataforseo.py keywords --domain notino.ro --limit 40    # what a competitor ranks for (keyword mining)
    uv run dataforseo.py backlinks --domain esteban.ro             # backlinks + referring domains summary
    uv run dataforseo.py balance                                   # account balance
"""
import argparse, os, subprocess, sys
import requests

BASE = "https://api.dataforseo.com"
LOC, LANG = "Romania", "Romanian"

def _cred(name):
    v = os.environ.get(name)
    if v: return v
    for c in [os.environ.get("KB_PY"),
              os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")]:
        if c and os.path.exists(c):
            try:
                return subprocess.run(["uv", "run", os.path.abspath(c), "secret-get", name],
                                      capture_output=True, text=True, timeout=60).stdout.strip()
            except Exception:
                pass
    sys.exit(f"Missing {name} (set it or kb.py secret-get {name}).")

def _post(path, payload):
    auth = (_cred("DATAFORSEO_LOGIN"), _cred("DATAFORSEO_PASSWORD"))
    r = requests.post(BASE + path, auth=auth, json=payload, timeout=120)
    if r.status_code != 200:
        sys.exit(f"DataForSEO HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    if d.get("status_code") != 20000:
        sys.exit(f"DataForSEO error: {d.get('status_message')}")
    task = (d.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        sys.exit(f"DataForSEO task error: {task.get('status_message')}")
    return task.get("result") or []

def cmd_balance(args):
    auth = (_cred("DATAFORSEO_LOGIN"), _cred("DATAFORSEO_PASSWORD"))
    d = requests.get(BASE + "/v3/appendix/user_data", auth=auth, timeout=30).json()
    m = ((d.get("tasks") or [{}])[0].get("result") or [{}])[0].get("money") or {}
    print(f"balance: {m.get('balance')} {m.get('currency') or ''}  (top-up at dataforseo.com if low)")

def cmd_serp(args):
    res = _post("/v3/serp/google/organic/live/regular",
                [{"keyword": args.keyword, "location_name": LOC, "language_name": LANG, "depth": args.depth}])
    items = (res[0].get("items") or []) if res else []
    print(f"\nGoogle RO SERP — '{args.keyword}'  (top {args.depth})")
    print(f"  {'#':>3}  domain / url")
    for it in items:
        if it.get("type") != "organic": continue
        dom = it.get("domain", "")
        ours = "  ← AL NOSTRU" if any(s in dom for s in ("esteban","george-talent","nubra","grandia","belasil")) else ""
        print(f"  {it.get('rank_absolute'):>3}  {dom}{ours}")
        print(f"       {(it.get('title') or '')[:80]}")

def cmd_keywords(args):
    res = _post("/v3/dataforseo_labs/google/ranked_keywords/live",
                [{"target": args.domain, "location_name": LOC, "language_name": LANG, "limit": args.limit,
                  "order_by": ["keyword_data.keyword_info.search_volume,desc"]}])
    items = (res[0].get("items") or []) if res else []
    print(f"\nKeywords ranked by {args.domain}  (top {args.limit} by volume)")
    print(f"  {'vol/mo':>8}{'pos':>5}  keyword")
    for it in items:
        kd = it.get("keyword_data") or {}
        kw = kd.get("keyword", "")
        vol = (kd.get("keyword_info") or {}).get("search_volume") or 0
        pos = (((it.get("ranked_serp_element") or {}).get("serp_item") or {}).get("rank_absolute")) or "?"
        print(f"  {vol:>8,}{str(pos):>5}  {kw[:58]}")

def cmd_backlinks(args):
    res = _post("/v3/backlinks/summary/live", [{"target": args.domain}])
    s = res[0] if res else {}
    print(f"\nBacklinks summary — {args.domain}")
    print(f"  backlinks:         {s.get('backlinks', 0):,}")
    print(f"  referring domains: {s.get('referring_domains', 0):,}")
    print(f"  referring IPs:     {s.get('referring_ips', 0):,}")
    print(f"  rank (0-1000):     {s.get('rank', 0)}")
    print(f"  broken backlinks:  {s.get('broken_backlinks', 0):,}")

def main():
    ap = argparse.ArgumentParser(description="DataForSEO — SERP / competitor keywords / backlinks (paid).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("balance", help="account balance").set_defaults(fn=cmd_balance)
    sp = sub.add_parser("serp", help="live Google RO organic SERP for a keyword")
    sp.add_argument("--keyword", required=True); sp.add_argument("--depth", type=int, default=20); sp.set_defaults(fn=cmd_serp)
    kw = sub.add_parser("keywords", help="keywords a domain ranks for (mine competitors)")
    kw.add_argument("--domain", required=True); kw.add_argument("--limit", type=int, default=40); kw.set_defaults(fn=cmd_keywords)
    bl = sub.add_parser("backlinks", help="backlinks + referring domains summary")
    bl.add_argument("--domain", required=True); bl.set_defaults(fn=cmd_backlinks)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
