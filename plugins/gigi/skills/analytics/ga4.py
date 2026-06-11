# /// script
# requires-python = ">=3.9"
# dependencies = ["google-auth>=2.0", "requests>=2.31"]
# ///
"""
GA4 puller for the team — traffic, channel economics (revenue/CVR), organic
trend, and top organic landing pages, straight from the Google Analytics Data API.

Credentials: the shared `looker-sheets` service account JSON, fetched from the
knowledge base secret `GA4_SA_JSON` (never hard-coded). The service account must
be a Viewer on the target GA4 property.

Usage (export the secret once, then run):
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
    uv run ga4.py properties                          # list properties the SA can see + IDs
    uv run ga4.py channels  --brand esteban           # session mix (sessions/users/conversions), last 90d
    uv run ga4.py economics --brand esteban            # sessions + CVR + revenue + rev/session per channel
    uv run ga4.py economics --all --start 2026-03-01 --end 2026-06-10
    uv run ga4.py landing   --brand esteban            # top landing pages for Organic Search
    uv run ga4.py landing   --brand grandia --channel "Paid Shopping" --limit 20
    uv run ga4.py trend     --brand grandia            # monthly Organic Search
    uv run ga4.py trend     --brand nubra --weekly --channels "Organic Search,Organic Social"
"""
import argparse, datetime as dt, json, os, subprocess, sys
from collections import defaultdict
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
ALL_BRANDS = [("Grandia", "510760223"), ("Esteban", "510626424"),
              ("George Talent", "541255080"), ("Nubra", "541249929")]

# ---------- credentials ----------
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
        sys.exit("No GA4 credentials. Set GA4_SA_JSON (kb.py secret-get GA4_SA_JSON) and re-run.")
    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    creds.refresh(Request())
    return creds

# ---------- HTTP ----------
def _post(url, headers, body, tries=6):
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

def _get(url, headers, params, tries=6):
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

def _report(creds, pid, body):
    return _post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
                 {"Authorization": f"Bearer {creds.token}"}, body)

def _rows(j):
    return [([d["value"] for d in r.get("dimensionValues", [])],
             [m["value"] for m in r.get("metricValues", [])]) for r in (j or {}).get("rows", [])]

def _default_range():
    end = dt.date.today() - dt.timedelta(days=1)
    return (end - dt.timedelta(days=89)).isoformat(), end.isoformat()

def _range(args):
    d = _default_range()
    return args.start or d[0], args.end or d[1]

def _resolve_pid(args):
    if getattr(args, "property", None):
        return args.property
    if getattr(args, "brand", None):
        pid = BRANDS.get(args.brand.lower())
        if not pid:
            sys.exit(f"Unknown brand '{args.brand}'. Known: {', '.join(sorted(set(BRANDS)))}")
        return pid
    sys.exit("Pass --property <id>, --brand <name>, or --all.")

# ---------- commands ----------
def cmd_properties(creds, args):
    j = _get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
             {"Authorization": f"Bearer {creds.token}"}, {"pageSize": 200})
    summ = j.get("accountSummaries", [])
    if not summ:
        print("No properties visible — the SA isn't a Viewer on any property yet."); return
    for acc in summ:
        print(f"\nAccount: {acc.get('displayName')} ({acc.get('account')})")
        for p in acc.get("propertySummaries", []):
            print(f"  {p.get('displayName'):<24} -> {p.get('property')}")

def _channels_for(creds, pid, label, start, end, metrics):
    j = _report(creds, pid, {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": m} for m in metrics],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}]})
    rr = _rows(j)
    print(f"\n{'='*66}\n{label}  (property {pid})   {start}..{end}\n{'='*66}")
    if not rr:
        print("  (no data in range)"); return
    total = sum(int(m[0]) for _, m in rr) or 1
    head = f"{'Channel':<22}{'Sessions':>11}{'Share':>8}" + "".join(f"{m:>12}" for m in metrics[1:])
    print(head)
    org_s = org_so = 0
    for dims, mets in rr:
        ch = dims[0]; s = int(mets[0])
        if ch == "Organic Search": org_s = s
        if ch == "Organic Social": org_so = s
        line = f"{ch:<22}{s:>11,}{100*s/total:>7.1f}%" + "".join(f"{float(v):>12,.0f}" for v in mets[1:])
        print(line)
    print("-"*66 + f"\n{'TOTAL':<22}{total:>11,}")
    print(f"Organic Search: {100*org_s/total:.1f}%  |  Organic (search+social): {100*(org_s+org_so)/total:.1f}%")

def cmd_channels(creds, args):
    start, end = _range(args)
    metrics = args.metrics.split(",") if args.metrics else ["sessions", "totalUsers", "keyEvents"]
    if args.all:
        for label, pid in ALL_BRANDS:
            _channels_for(creds, pid, label, start, end, metrics)
    else:
        _channels_for(creds, _resolve_pid(args), args.brand or args.property, start, end, metrics)

def _economics_for(creds, pid, label, start, end):
    j = _report(creds, pid, {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "ecommercePurchases"}, {"name": "purchaseRevenue"}],
        "orderBys": [{"metric": {"metricName": "purchaseRevenue"}, "desc": True}]})
    rr = _rows(j)
    print(f"\n{'='*82}\n{label}  (property {pid})   {start}..{end}   [channel economics]\n{'='*82}")
    if not rr:
        print("  (no data in range)"); return
    tot_s = sum(int(m[0]) for _, m in rr) or 1
    tot_r = sum(float(m[2]) for _, m in rr) or 1
    print(f"{'Channel':<20}{'Sessions':>10}{'Sess%':>7}{'Purch':>8}{'CVR%':>7}{'Revenue':>13}{'Rev%':>7}{'Rev/sess':>10}")
    for d, m in rr:
        s = int(m[0]); pur = int(float(m[1])); rev = float(m[2])
        cvr = 100*pur/s if s else 0; rps = rev/s if s else 0
        print(f"{d[0]:<20}{s:>10,}{100*s/tot_s:>6.1f}%{pur:>8,}{cvr:>6.2f}%{rev:>13,.0f}{100*rev/tot_r:>6.1f}%{rps:>10,.2f}")
    org = {d[0]: (int(m[0]), float(m[2])) for d, m in rr}
    o_rev = sum(org.get(c, (0, 0))[1] for c in ("Organic Search", "Organic Social", "Organic Shopping"))
    print("-"*82 + f"\nTOTAL sessions {tot_s:,} | revenue {tot_r:,.0f} | "
          f"Organic revenue {o_rev:,.0f} ({100*o_rev/tot_r:.1f}%)")

def cmd_economics(creds, args):
    start, end = _range(args)
    if args.all:
        for label, pid in ALL_BRANDS:
            _economics_for(creds, pid, label, start, end)
    else:
        _economics_for(creds, _resolve_pid(args), args.brand or args.property, start, end)

def cmd_landing(creds, args):
    pid = _resolve_pid(args); start, end = _range(args)
    j = _report(creds, pid, {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "landingPage"}],
        "metrics": [{"name": "sessions"}, {"name": "keyEvents"}, {"name": "purchaseRevenue"}],
        "dimensionFilter": {"filter": {"fieldName": "sessionDefaultChannelGroup",
                                       "stringFilter": {"value": args.channel}}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": args.limit})
    print(f"\nTop {args.limit} landing pages — {args.channel}  ({pid}, {start}..{end})")
    print(f"  {'sessions':>9}{'keyEv':>8}{'revenue':>12}  landing page")
    for d, m in _rows(j):
        print(f"  {int(m[0]):>9,}{int(float(m[1])):>8,}{float(m[2]):>12,.0f}  {d[0][:68]}")

def cmd_trend(creds, args):
    pid = _resolve_pid(args); start, end = _range(args)
    channels = [c.strip() for c in (args.channels or "Organic Search").split(",")]
    j = _report(creds, pid, {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}]})
    bucket = defaultdict(lambda: defaultdict(int))
    for d, m in _rows(j):
        ch = d[1]
        if ch not in channels:
            continue
        day = dt.date(int(d[0][:4]), int(d[0][4:6]), int(d[0][6:8]))
        key = (day - dt.timedelta(days=day.weekday())).isoformat() if args.weekly else d[0][:6]
        bucket[key][ch] += int(m[0])
    period = "week" if args.weekly else "month"
    print(f"\n{period.title()} trend — {', '.join(channels)}  ({pid}, {start}..{end})")
    print(f"  {period:<12}" + "".join(f"{c[:14]:>15}" for c in channels))
    for k in sorted(bucket):
        print(f"  {k:<12}" + "".join(f"{bucket[k][c]:>15,}" for c in channels))

def main():
    ap = argparse.ArgumentParser(description="Pull GA4 traffic, economics & organic insights for the team.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("properties", help="list properties the SA can read + IDs").set_defaults(fn=cmd_properties)

    def add_common(p, all_opt=True):
        p.add_argument("--brand"); p.add_argument("--property")
        if all_opt: p.add_argument("--all", action="store_true")
        p.add_argument("--start"); p.add_argument("--end")

    sc = sub.add_parser("channels", help="session mix by channel group"); add_common(sc)
    sc.add_argument("--metrics", help="comma list; default sessions,totalUsers,keyEvents"); sc.set_defaults(fn=cmd_channels)

    se = sub.add_parser("economics", help="sessions + CVR + revenue + rev/session per channel"); add_common(se)
    se.set_defaults(fn=cmd_economics)

    sl = sub.add_parser("landing", help="top landing pages for a channel (default Organic Search)"); add_common(sl, all_opt=False)
    sl.add_argument("--channel", default="Organic Search"); sl.add_argument("--limit", type=int, default=15)
    sl.set_defaults(fn=cmd_landing)

    st = sub.add_parser("trend", help="monthly/weekly trend for one or more channels"); add_common(st, all_opt=False)
    st.add_argument("--channels", help="comma list; default 'Organic Search'")
    st.add_argument("--weekly", action="store_true", help="weekly buckets instead of monthly")
    st.set_defaults(fn=cmd_trend)

    args = ap.parse_args()
    args.fn(load_creds(), args)

if __name__ == "__main__":
    main()
