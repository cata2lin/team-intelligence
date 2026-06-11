# /// script
# requires-python = ">=3.9"
# dependencies = ["google-auth>=2.0", "requests>=2.31"]
# ///
"""
GA4 puller for the team — sessions / users / conversions by channel, for any
brand property, straight from the Google Analytics Data API.

Credentials: the shared `looker-sheets` service account JSON, fetched from the
knowledge base secret `GA4_SA_JSON` (never hard-coded). The service account must
be a Viewer on the target GA4 property.

Usage (export the secret once, then run):
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
    uv run ga4.py properties                      # list every property the SA can see + IDs
    uv run ga4.py channels --brand esteban        # channel mix (sessions/users/conversions) last 90d
    uv run ga4.py channels --property 510626424 --start 2026-03-01 --end 2026-06-10
    uv run ga4.py trend   --brand grandia         # monthly Organic Search trend
    uv run ga4.py channels --all                  # loop all known brands
"""
import argparse, datetime as dt, json, os, subprocess, sys
from google.oauth2 import service_account
from google.auth.transport.requests import Request
import requests

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]

# brand -> GA4 property id. GT/Nubra GA4 were installed Jun 2026 (little history yet —
# use the Shopify source for them until they accumulate; see SKILL.md).
BRANDS = {
    "esteban":       "510626424",
    "grandia":       "510760223",
    "nubra":         "541249929",
    "george-talent": "541255080",
    "gt":            "541255080",
}

# ---------- credentials ----------
def _find_kb():
    cands = [
        os.environ.get("KB_PY"),
        os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py"),
    ]
    for c in cands:
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
        for p in ("google_credentials.json",
                  os.path.expanduser("~/Downloads/Scripturi/google_credentials.json")):
            if os.path.exists(p):
                raw = open(p).read(); break
    if not raw:
        sys.exit("No GA4 credentials. Set GA4_SA_JSON (kb.py secret-get GA4_SA_JSON) and re-run.")
    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    creds.refresh(Request())
    return creds

# ---------- HTTP with retries ----------
def _post(url, headers, body, tries=5):
    for attempt in range(1, tries + 1):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=120)
        except requests.exceptions.RequestException as e:
            sys.stderr.write(f"  (retry {attempt}/{tries} {type(e).__name__})\n"); continue
        if r.status_code in (429, 500, 502, 503, 504):
            sys.stderr.write(f"  (retry {attempt}/{tries} HTTP {r.status_code})\n"); continue
        if r.status_code != 200:
            sys.exit(f"GA4 API error {r.status_code}: {r.text[:500]}")
        return r.json()
    sys.exit("GA4 API failed after retries (transient 5xx).")

def _get(url, headers, params, tries=5):
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=120)
        except requests.exceptions.RequestException as e:
            sys.stderr.write(f"  (retry {attempt}/{tries} {type(e).__name__})\n"); continue
        if r.status_code in (429, 500, 502, 503, 504):
            sys.stderr.write(f"  (retry {attempt}/{tries} HTTP {r.status_code})\n"); continue
        if r.status_code != 200:
            sys.exit(f"GA4 API error {r.status_code}: {r.text[:500]}")
        return r.json()
    sys.exit("GA4 API failed after retries (transient 5xx).")

def _rows(j):
    out = []
    for row in (j or {}).get("rows", []):
        out.append(([d["value"] for d in row.get("dimensionValues", [])],
                    [m["value"] for m in row.get("metricValues", [])]))
    return out

def _default_range():
    end = dt.date.today() - dt.timedelta(days=1)
    return (end - dt.timedelta(days=89)).isoformat(), end.isoformat()

# ---------- commands ----------
def cmd_properties(creds, args):
    H = {"Authorization": f"Bearer {creds.token}"}
    j = _get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", H, {"pageSize": 200})
    summ = j.get("accountSummaries", [])
    if not summ:
        print("No properties visible — the SA isn't a Viewer on any property yet."); return
    for acc in summ:
        print(f"\nAccount: {acc.get('displayName')} ({acc.get('account')})")
        for p in acc.get("propertySummaries", []):
            print(f"  {p.get('displayName'):<24} -> {p.get('property')}")

def _resolve_pid(args):
    if args.property:
        return args.property
    if args.brand:
        pid = BRANDS.get(args.brand.lower())
        if not pid:
            sys.exit(f"Unknown brand '{args.brand}'. Known: {', '.join(sorted(set(BRANDS)))}")
        return pid
    sys.exit("Pass --property <id>, --brand <name>, or --all.")

def _channels_for(creds, pid, label, start, end, metrics):
    H = {"Authorization": f"Bearer {creds.token}"}
    j = _post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport", H, {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": m} for m in metrics],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
    })
    rr = _rows(j)
    print(f"\n{'='*66}\n{label}  (property {pid})   {start}..{end}\n{'='*66}")
    if not rr:
        print("  (no data — GA4 has no sessions for this property in range)"); return
    total = sum(int(m[0]) for _, m in rr) or 1
    head = f"{'Channel':<22}{'Sessions':>11}{'Share':>8}"
    for m in metrics[1:]:
        head += f"{m:>12}"
    print(head)
    org_s = org_so = 0
    for dims, mets in rr:
        ch = dims[0]; s = int(mets[0])
        if ch == "Organic Search": org_s = s
        if ch == "Organic Social": org_so = s
        line = f"{ch:<22}{s:>11,}{100*s/total:>7.1f}%"
        for v in mets[1:]:
            line += f"{float(v):>12,.0f}"
        print(line)
    print("-"*66)
    print(f"{'TOTAL':<22}{total:>11,}")
    print(f"Organic Search: {100*org_s/total:.1f}%  |  Organic (search+social): {100*(org_s+org_so)/total:.1f}%")

def cmd_channels(creds, args):
    start, end = args.start or _default_range()[0], args.end or _default_range()[1]
    metrics = args.metrics.split(",") if args.metrics else ["sessions", "totalUsers", "keyEvents"]
    if args.all:
        for b, pid in [("Grandia","510760223"),("Esteban","510626424"),
                       ("George Talent","541255080"),("Nubra","541249929")]:
            _channels_for(creds, pid, b, start, end, metrics)
    else:
        _channels_for(creds, _resolve_pid(args), args.brand or args.property, start, end, metrics)

def cmd_trend(creds, args):
    pid = _resolve_pid(args)
    start, end = args.start or _default_range()[0], args.end or _default_range()[1]
    H = {"Authorization": f"Bearer {creds.token}"}
    j = _post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport", H, {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "yearMonth"}],
        "metrics": [{"name": "sessions"}],
        "dimensionFilter": {"filter": {"fieldName": "sessionDefaultChannelGroup",
                                       "stringFilter": {"value": args.channel}}},
        "orderBys": [{"dimension": {"dimensionName": "yearMonth"}}],
    })
    print(f"{args.channel} by month ({pid}, {start}..{end}):")
    print("  " + ", ".join(f"{d[0]}={int(m[0]):,}" for d, m in _rows(j)) or "  (none)")

def main():
    ap = argparse.ArgumentParser(description="Pull GA4 traffic by channel for the team.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("properties", help="list properties the SA can read + IDs")
    sp.set_defaults(fn=cmd_properties)
    sc = sub.add_parser("channels", help="sessions/users/conversions by channel group")
    sc.add_argument("--brand"); sc.add_argument("--property"); sc.add_argument("--all", action="store_true")
    sc.add_argument("--start"); sc.add_argument("--end")
    sc.add_argument("--metrics", help="comma list; default sessions,totalUsers,keyEvents")
    sc.set_defaults(fn=cmd_channels)
    st = sub.add_parser("trend", help="monthly trend for one channel (default Organic Search)")
    st.add_argument("--brand"); st.add_argument("--property")
    st.add_argument("--start"); st.add_argument("--end")
    st.add_argument("--channel", default="Organic Search")
    st.set_defaults(fn=cmd_trend)
    args = ap.parse_args()
    creds = load_creds()
    args.fn(creds, args)

if __name__ == "__main__":
    main()
