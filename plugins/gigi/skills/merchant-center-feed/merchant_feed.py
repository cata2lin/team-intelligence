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
    uv run merchant_feed.py --account-issues 5813605780        # ACCOUNT-level issues (Misrepresentation / suspension detector), read-only
    uv run merchant_feed.py --set-business-info 5813605780 --name "ARONA SRL" --cs-uri https://ofertelezilei.ro/pages/contact   # dry-run; add --apply to write

Lecții feed (iun 2026, lansări CZ + Casa Ofertelor):
- `product_view` query CERE `product_view.id` în SELECT (altfel 400 "expected to have id").
- "Missing shipping" (`missing_shipping_no_account_shipping_exist`) → contul n-are livrare. Fix prin
  API: GET `accounts/v1/accounts/{A}/shippingSettings` (ia `etag`) → POST `…:insert` cu un service
  (deliveryCountries, currencyCode, rateGroups.singleValue.flatRate.amountMicros). Etag-ul e obligatoriu.
- "Pending initial review" (`pending_initial_policy_review_*`) = se curăță singur în ~3 zile.
- Issue de CONT "Website needs improvement" (`policy_enforcement_account_disapproval`,
  `accounts/v1/accounts/{A}/issues?languageCode=..`) la magazine deals = MISREPRESENTATION (countdown/
  stoc fals/garanție preț — vezi memoria mc-deals-store-misrepresentation). **Request review = DOAR din UI**
  Merchant Center (API-ul `accounts_v1` are doar `issues.list`, fără declanșare review).
- "Produse pe canalul Shopify dar feed gol/parțial": verifică `datasources/v1/accounts/{A}/dataSources`
  (sursa "Shopify App API") + `products/v1/accounts/{A}/products` (produsele EFECTIVE, nu reportul care
  lagăie). OfferId `shopify_ZZ_...` = market ne-setat în app-ul Shopify → produse nesincronizate.
"""
import argparse, os, subprocess, sys
from collections import Counter
import requests

# Poți da fie un alias, fie direct merchant ID. Aliasuri cunoscute:
ACCOUNTS = {"grandia": "5677157050", "esteban": "5676783307", "belasil": "5582663665",
            "casaofertelor": "5639173332", "bonhaus_ro": "5639173332", "ofertele": "5813605780",
            "bonhaus_cz": "5815161322"}

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
    status = Counter(); disapproved = []; issues = Counter(); limited = Counter(); n = 0; page = None
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
            elif st == "ELIGIBLE_LIMITED":   # eligible but reach-limited — surface WHY (e.g. pending review, missing GTIN)
                for it in pv.get("itemIssues", []):
                    code = (it.get("type") or {}).get("code") or "?"
                    sev = (it.get("severity") or {}).get("aggregatedSeverity") or ""
                    limited[f"{code}({sev})"] += 1
        page = d.get("nextPageToken")
        if not page: break
    return n, status, disapproved, issues, limited

def run(store, tok):
    acct = ACCOUNTS.get(store.lower(), store)
    n, status, disapproved, issues, limited = _feed(tok, acct)
    bad = sum(v for k, v in status.items() if k not in ("ELIGIBLE", "ELIGIBLE_LIMITED"))
    print(f"\n{'='*70}\nMerchant Center feed — {store} ({acct}): {n} produse\n{'='*70}")
    print(f"  status: " + ", ".join(f"{k}={v}" for k, v in status.most_common()))
    print(f"  ⚠️ {bad} produse NU rulează în Shopping/PMax ({100*bad/max(n,1):.0f}%)")
    if limited:
        print(f"  🟡 ELIGIBLE_LIMITED (eligibile, reach redus) — motive: " + ", ".join(f"{c}×{v}" for c, v in limited.most_common(6)))
        print(f"     (ex: pending_initial_policy_review_* = review nou de cont/feed, se rezolvă singur în ~3 zile; missing GTIN → identifier_exists=no)")
    if issues:
        print(f"  top motive: " + ", ".join(f"{c}×{n_}" for c, n_ in issues.most_common(6)))
    for off, title, reasons in disapproved[:25]:
        print(f"    [{off}] {title} — {', '.join(reasons[:3])}")
    if len(disapproved) > 25: print(f"    … +{len(disapproved)-25} more")

_SEV = {"CRITICAL": "🛑 CRITICAL", "ERROR": "🛑 CRITICAL", "SUGGESTION": "💡 SUGGESTION",
        "INFO": "ℹ️ INFO", "": "•"}

def account_issues(store, tok):
    """ACCOUNT-LEVEL issues (Misrepresentation / suspension detector). Read-only.
    Merchant API accounts/v1 issues.list: GET accounts/{A}/issues?languageCode=en"""
    acct = ACCOUNTS.get(store.lower(), store)
    H = {"Authorization": f"Bearer {tok}"}
    r = requests.get(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}/issues?languageCode=en", headers=H, timeout=60)
    if r.status_code != 200: sys.exit(f"Merchant API {r.status_code}: {r.text[:300]}")
    issues = r.json().get("accountIssues", [])
    print(f"\n{'='*70}\nAccount-level issues — {store} ({acct})\n{'='*70}")
    if not issues:
        print("  ✅ no account-level issues (account healthy)")
        return
    for it in issues:
        title = it.get("title") or "(untitled)"
        sev = (it.get("severity") or "").upper()
        detail = it.get("detail") or ""
        # impacted destinations (Shopping ads / Free listings / …)
        dests = []
        for imp in it.get("impactedDestinations", []):
            reg = imp.get("regionCode") or imp.get("reportingContext") or ""
            for ic in imp.get("impacts", []):
                d = ic.get("reportingContext") or reg or "?"
                if d not in dests: dests.append(d)
        if not dests:
            for imp in it.get("impactedDestinations", []):
                d = imp.get("reportingContext") or imp.get("regionCode") or "?"
                if d not in dests: dests.append(d)
        doc = it.get("documentationUri") or ""
        head = f"⚠️ {title} ({_SEV.get(sev, sev or '•')})"
        print(f"\n  {head}")
        if detail: print(f"    {detail}")
        if dests: print(f"    → impacted: {', '.join(dests)}")
        if doc: print(f"    docs: {doc}")
    print(f"\n  (Misrepresentation / 'website needs improvement' = account disapproval — request review DOAR din UI Merchant Center; API accounts_v1 are doar issues.list.)")

def _get_biz(tok, acct):
    """Read current account (accountName) + businessInfo (address, customerService)."""
    H = {"Authorization": f"Bearer {tok}"}
    ra = requests.get(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}", headers=H, timeout=30)
    rb = requests.get(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}/businessInfo", headers=H, timeout=30)
    acc = ra.json() if ra.status_code == 200 else {"_err": f"{ra.status_code}: {ra.text[:120]}"}
    biz = rb.json() if rb.status_code == 200 else {"_err": f"{rb.status_code}: {rb.text[:120]}"}
    return acc, biz

def _fmt_addr(addr):
    if not addr: return "(none)"
    return " | ".join(str(x) for x in [
        ", ".join(addr.get("addressLines", [])), addr.get("locality"), addr.get("administrativeArea"),
        addr.get("postalCode"), addr.get("regionCode")] if x)

def set_business_info(store, tok, args):
    """Write businessInfo (accountName + address + customerService). Dry-run by default.
    ⚠️ businessInfo.phone is OUTPUT-ONLY and businessIdentity is RO-country-gated — both skipped."""
    acct = ACCOUNTS.get(store.lower(), store)
    acc_cur, biz_cur = _get_biz(tok, acct)
    cur_name = acc_cur.get("accountName") or "(none)"
    cur_addr = (biz_cur.get("address") or {})
    cur_cs = (biz_cur.get("customerService") or {})

    # --- build the desired new values (only overwrite what was passed) ---
    new_name = args.name if args.name else cur_name
    new_addr = dict(cur_addr)
    if args.street:  new_addr["addressLines"] = [args.street]
    if args.city:    new_addr["locality"] = args.city
    if args.region:  new_addr["administrativeArea"] = args.region
    if args.postal:  new_addr["postalCode"] = args.postal
    if args.country: new_addr["regionCode"] = args.country
    elif not new_addr.get("regionCode"): new_addr["regionCode"] = "RO"
    new_cs = dict(cur_cs)
    if args.cs_email: new_cs["email"] = args.cs_email
    if args.cs_uri:   new_cs["uri"] = args.cs_uri

    changes_name = args.name is not None
    addr_touched = any([args.street, args.city, args.region, args.postal, args.country])
    cs_touched = any([args.cs_email, args.cs_uri])

    print(f"\n{'='*70}\nSet business info — {store} ({acct}){'  [DRY-RUN]' if not args.apply else '  [APPLY]'}\n{'='*70}")
    print(f"  accountName:   {cur_name!r}  →  {new_name!r}")
    print(f"  address:       {_fmt_addr(cur_addr)}")
    print(f"            →    {_fmt_addr(new_addr)}")
    print(f"  cs email:      {(cur_cs.get('email') or '(none)')!r}  →  {(new_cs.get('email') or '(none)')!r}")
    print(f"  cs uri:        {(cur_cs.get('uri') or '(none)')!r}  →  {(new_cs.get('uri') or '(none)')!r}")
    print(f"  ⏭️  skipped (not writable here): businessInfo.phone (OUTPUT-ONLY), businessIdentity (RO country-gated)")

    if not (changes_name or addr_touched or cs_touched):
        print("  (nothing to change — pass --name/--street/--city/--region/--postal/--cs-email/--cs-uri)")
        return
    if not args.apply:
        print("\n  DRY-RUN — nothing written. Re-run with --apply to save.")
        return

    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    # 1) accountName via PATCH accounts/{A}?updateMask=accountName
    if changes_name:
        r1 = requests.patch(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}?updateMask=accountName",
                            headers=H, json={"accountName": new_name}, timeout=30)
        ok = r1.status_code == 200
        print(f"\n  PATCH accountName [{r1.status_code}] {'✅' if ok else '❌ ' + r1.text[:200]}")
    # 2) businessInfo via PATCH accounts/{A}/businessInfo?updateMask=address,customerService
    masks = []; body = {}
    if addr_touched: masks.append("address"); body["address"] = new_addr
    if cs_touched:   masks.append("customerService"); body["customerService"] = new_cs
    if masks:
        r2 = requests.patch(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}/businessInfo?updateMask={','.join(masks)}",
                            headers=H, json=body, timeout=30)
        ok = r2.status_code == 200
        print(f"  PATCH businessInfo ({','.join(masks)}) [{r2.status_code}] {'✅' if ok else '❌ ' + r2.text[:300]}")

def set_return_policy(store, tok, args):
    """Create an online return policy for a Merchant Center account (Merchant API). Dry-run unless --apply.
    Fixes the 'missing return policy / return cost' MC warning that limits/disapproves products.
    Return cost via --return-fee (0 = free returns). Uses the same MERCHANT_OAUTH write token."""
    acct = ACCOUNTS.get(store.lower(), store)
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    rc = requests.get(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}/onlineReturnPolicies", headers=H, timeout=40)
    cur = rc.json().get("onlineReturnPolicies", []) if rc.status_code == 200 else []
    if not args.uri or not args.country or not args.currency:
        sys.exit("--set-return-policy needs --country <CC> --currency <CUR> --uri <return-policy-page-url> (optional --days N --return-fee X --label L)")
    fee = int(round(float(args.return_fee) * 1e6))
    body = {
        "label": args.label or f"{store}-returns",
        "countries": [args.country.upper()],
        "policy": {"type": "NUMBER_OF_DAYS_AFTER_DELIVERY", "days": str(args.days)},
        "returnMethods": ["BY_MAIL"], "itemConditions": ["NEW", "USED"],
        "returnShippingFee": {"type": "FIXED", "fixedFee": {"amountMicros": str(fee), "currencyCode": args.currency.upper()}},
        "returnPolicyUri": args.uri, "processRefundDays": args.days,
        "acceptExchange": True, "returnLabelSource": "IN_THE_PACKAGE",
    }
    print(f"\n{'='*70}\nSet return policy — {store} ({acct}){'  [DRY-RUN]' if not args.apply else '  [APPLY]'}\n{'='*70}")
    print(f"  current policies: {len(cur)}  ({[p.get('countries') for p in cur]})")
    print(f"  NEW: country={args.country.upper()} · {args.days} days · return-fee={args.return_fee} {args.currency.upper()} ({'FREE' if fee==0 else 'customer pays'}) · uri={args.uri}")
    if not args.apply:
        print("\n  DRY-RUN — nothing written. Re-run with --apply."); return
    r = requests.post(f"https://merchantapi.googleapis.com/accounts/v1/accounts/{acct}/onlineReturnPolicies", headers=H, json=body, timeout=60)
    ok = r.status_code == 200
    print(f"\n  POST onlineReturnPolicy [{r.status_code}] {'✅ ' + r.json().get('name','') if ok else '❌ ' + r.text[:300]}")
    if ok: print("  ℹ️  Apare în MC UI (Settings → Shipping and returns) în minute–ore (propagare).")

def main():
    ap = argparse.ArgumentParser(description="Merchant Center feed health + account issues + business info.")
    ap.add_argument("--store"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--account-issues", metavar="MERCHANT", help="print ACCOUNT-level issues (Misrepresentation/suspension detector). Read-only.")
    ap.add_argument("--set-business-info", metavar="MERCHANT", help="write accountName + businessInfo.address + customerService. Dry-run unless --apply.")
    ap.add_argument("--name"); ap.add_argument("--street"); ap.add_argument("--city")
    ap.add_argument("--region"); ap.add_argument("--postal")
    ap.add_argument("--country", help="ISO region code for businessInfo.address.regionCode (default RO when address is set)")
    ap.add_argument("--cs-email", dest="cs_email"); ap.add_argument("--cs-uri", dest="cs_uri")
    ap.add_argument("--set-return-policy", metavar="MERCHANT", help="create an online return policy (fixes missing return policy/cost). Needs --country --currency --uri. Dry-run unless --apply.")
    ap.add_argument("--days", type=int, default=14, help="return window days (--set-return-policy; default 14)")
    ap.add_argument("--return-fee", dest="return_fee", default="0", help="return shipping fee amount (--set-return-policy; 0 = free returns)")
    ap.add_argument("--currency", help="currency for the return fee, e.g. CZK/RON/PLN")
    ap.add_argument("--uri", help="URL of the store's return/refund policy page")
    ap.add_argument("--label", help="return policy label (default <store>-returns)")
    ap.add_argument("--apply", action="store_true", help="actually write (--set-business-info / --set-return-policy); off = dry-run")
    a = ap.parse_args()
    tok = _token()
    if a.account_issues:
        account_issues(a.account_issues, tok)
    elif a.set_business_info:
        set_business_info(a.set_business_info, tok, a)
    elif a.set_return_policy:
        set_return_policy(a.set_return_policy, tok, a)
    elif a.all:
        for s in ACCOUNTS: run(s, tok)
    elif a.store:
        run(a.store, tok)
    else:
        sys.exit("--store <grandia|esteban|belasil> | --all | --account-issues <merchant> | --set-business-info <merchant> ...")

if __name__ == "__main__":
    main()
