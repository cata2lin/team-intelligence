# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""
Google Merchant Center feed health — which products are DISAPPROVED / not eligible
for Google Shopping & Performance Max, and why. Disapprovals = lost Shopping/PMax
impressions (critical for Grandia, which leans on PMax). Read-only.

Uses the new Merchant API (merchantapi.googleapis.com/reports/v1, product_view) with
a HUMAN OAuth token (KB: MERCHANT_OAUTH_REFRESH_TOKEN + YOUTUBE_OAUTH_CLIENT_ID/SECRET) —
the SA can't self-register the GCP project, so a human registered it once.

Usage:
    uv run merchant_feed.py --store grandia        # status + disapproved products + reasons
    uv run merchant_feed.py --all
"""
import argparse, os, subprocess, sys
from collections import Counter
import requests

ACCOUNTS = {"grandia": "5677157050", "esteban": "5676783307", "belasil": "5582663665"}

def _kb(name):
    v = os.environ.get(name)
    if v: return v
    for c in [os.environ.get("KB_PY"),
              os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")]:
        if c and os.path.exists(c):
            try: return subprocess.run(["uv", "run", os.path.abspath(c), "secret-get", name], capture_output=True, text=True, timeout=60).stdout.strip()
            except Exception: pass
    sys.exit(f"Missing {name}")

def _token():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "grant_type": "refresh_token", "client_id": _kb("YOUTUBE_OAUTH_CLIENT_ID"),
        "client_secret": _kb("YOUTUBE_OAUTH_CLIENT_SECRET"), "refresh_token": _kb("MERCHANT_OAUTH_REFRESH_TOKEN")})
    j = r.json()
    if "access_token" not in j: sys.exit(f"OAuth refresh failed: {j.get('error_description') or j}")
    return j["access_token"]

def _feed(tok, acct):
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    q = "SELECT offer_id, id, title, aggregated_reporting_context_status, item_issues FROM product_view"
    status = Counter(); disapproved = []; issues = Counter(); n = 0; page = None
    for _ in range(20):
        body = {"query": q, "pageSize": 1000}
        if page: body["pageToken"] = page
        r = requests.post(f"https://merchantapi.googleapis.com/reports/v1/accounts/{acct}/reports:search", headers=H, json=body, timeout=120)
        if r.status_code != 200: sys.exit(f"Merchant API {r.status_code}: {r.text[:200]}")
        d = r.json()
        for row in d.get("results", []):
            pv = row.get("productView", {}); n += 1
            st = pv.get("aggregatedReportingContextStatus", "?"); status[st] += 1
            if st not in ("ELIGIBLE", "ELIGIBLE_LIMITED"):
                reasons = []
                for it in pv.get("itemIssues", []):
                    code = (it.get("type") or {}).get("code") or "?"
                    sev = (it.get("severity") or {}).get("aggregatedSeverity") or ""
                    issues[code] += 1
                    reasons.append(f"{code}({sev})")
                disapproved.append((pv.get("offerId") or pv.get("offer_id"), (pv.get("title") or "")[:46], reasons))
        page = d.get("nextPageToken")
        if not page: break
    return n, status, disapproved, issues

def run(store, tok):
    acct = ACCOUNTS.get(store.lower(), store)
    n, status, disapproved, issues = _feed(tok, acct)
    bad = sum(v for k, v in status.items() if k not in ("ELIGIBLE", "ELIGIBLE_LIMITED"))
    print(f"\n{'='*70}\nMerchant Center feed — {store} ({acct}): {n} produse\n{'='*70}")
    print(f"  status: " + ", ".join(f"{k}={v}" for k, v in status.most_common()))
    print(f"  ⚠️ {bad} produse NU rulează în Shopping/PMax ({100*bad/max(n,1):.0f}%)")
    if issues:
        print(f"  top motive: " + ", ".join(f"{c}×{n_}" for c, n_ in issues.most_common(6)))
    for off, title, reasons in disapproved[:25]:
        print(f"    [{off}] {title} — {', '.join(reasons[:3])}")
    if len(disapproved) > 25: print(f"    … +{len(disapproved)-25} more")

def main():
    ap = argparse.ArgumentParser(description="Merchant Center feed health.")
    ap.add_argument("--store"); ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    tok = _token()
    if a.all:
        for s in ACCOUNTS: run(s, tok)
    elif a.store:
        run(a.store, tok)
    else:
        sys.exit("--store <grandia|esteban|belasil> or --all")

if __name__ == "__main__":
    main()
