# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
kw_ideas.py — Google Ads Keyword Planner (generateKeywordIdeas) via the team MCC.
Returns REAL avg monthly searches (Romania, RO) for SEO research. Read-only.

Usage:
  DATABASE_URL_METRICS=... uv run kw_ideas.py --customer 9069610821 \
     --seeds "mobilier,canapea,lustra led" [--url https://grandia.ro/...] [--page]
Geo Romania = 2642, language RO = 1032 (Keyword Planner uses 1032 for Romanian).
"""
from __future__ import annotations
import argparse, json, os, sys, time
import requests
from gads import get_connection, access_token, _digits, API

GEO_RO = "geoTargetConstants/2642"
LANG_RO = "languageConstants/1032"  # Romanian (Keyword Planner)

def _headers(c, tok):
    return {"Authorization": f"Bearer {tok}", "developer-token": c["dev"],
            "login-customer-id": _digits(c["mcc"]), "Content-Type": "application/json"}

def generate(c, customer_id, seeds=None, url=None, page_url=False):
    tok = access_token(c)
    ep = f"https://googleads.googleapis.com/{API}/customers/{_digits(customer_id)}:generateKeywordIdeas"
    body = {
        "language": LANG_RO,
        "geoTargetConstants": [GEO_RO],
        "keywordPlanNetwork": "GOOGLE_SEARCH",
        "includeAdultKeywords": False,
        "pageSize": 1000,
    }
    if seeds and url:
        body["keywordAndUrlSeed"] = {"url": url, "keywords": seeds}
    elif seeds:
        body["keywordSeed"] = {"keywords": seeds}
    elif url and page_url:
        body["urlSeed"] = {"url": url}
    elif url:
        body["siteSeed"] = {"site": url}
    out = []
    page = None
    while True:
        if page:
            body["pageToken"] = page
        r = requests.post(ep, headers=_headers(c, tok), json=body, timeout=90)
        if r.status_code != 200:
            sys.stderr.write(f"API {r.status_code}: {r.text[:800]}\n")
            sys.exit(1)
        d = r.json()
        out += d.get("results", []) or []
        page = d.get("nextPageToken")
        if not page:
            break
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", required=True)
    ap.add_argument("--seeds", default="", help="comma-separated seed keywords")
    ap.add_argument("--url", default="")
    ap.add_argument("--page", action="store_true", help="use urlSeed (single page) instead of siteSeed")
    ap.add_argument("--mcc")
    ap.add_argument("--min", type=int, default=0, help="min avg monthly searches filter")
    ap.add_argument("--format", default="json", choices=["json", "tsv"])
    a = ap.parse_args()
    c = get_connection(a.mcc)
    seeds = [s.strip() for s in a.seeds.split(",") if s.strip()] or None
    res = generate(c, a.customer, seeds=seeds, url=(a.url or None), page_url=a.page)
    rows = []
    for r in res:
        m = r.get("keywordIdeaMetrics") or {}
        avg = m.get("avgMonthlySearches")
        try:
            avg = int(avg) if avg is not None else 0
        except Exception:
            avg = 0
        if avg < a.min:
            continue
        rows.append({
            "kw": r.get("text", ""),
            "vol": avg,
            "comp": m.get("competition", ""),
            "low_bid": (int(m.get("lowTopOfPageBidMicros", 0)) / 1e6) if m.get("lowTopOfPageBidMicros") else None,
            "high_bid": (int(m.get("highTopOfPageBidMicros", 0)) / 1e6) if m.get("highTopOfPageBidMicros") else None,
        })
    rows.sort(key=lambda x: x["vol"], reverse=True)
    if a.format == "tsv":
        print("kw\tvol\tcomp")
        for r in rows:
            print(f"{r['kw']}\t{r['vol']}\t{r['comp']}")
    else:
        print(json.dumps(rows, ensure_ascii=False))
    sys.stderr.write(f"# {len(rows)} keywords (>= {a.min} vol)\n")

if __name__ == "__main__":
    main()
