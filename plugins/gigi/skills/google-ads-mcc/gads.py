# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
gads.py — Google Ads API v20 via the team MCC.

Reads the MCC connection (developer token, login-customer-id, OAuth client +
refresh token) from the `metrics` DB (table google_ads_connections), refreshes
an access token, and runs reports / mutations against ANY customer linked under
the MCC. Secrets are read from the DB and used in-process only — never printed.

Auth/DSN: pass the metrics DSN via env DATABASE_URL_METRICS, e.g.
    DATABASE_URL_METRICS=$(uv run "$KB" secret-get DATABASE_URL_METRICS) \\
      uv run gads.py report --preset campaigns --customer 5229815058 --range TODAY
"""
from __future__ import annotations
import argparse, json, os, sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

API = "v21"
TOKEN_URL = "https://oauth2.googleapis.com/token"
_PG_OK = {"host","hostaddr","port","dbname","user","password","sslmode","sslrootcert",
          "sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}

def _clean_dsn(dsn: str) -> str:
    p = urlsplit(dsn)
    if not p.query: return dsn
    kept = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() in _PG_OK]
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(kept),p.fragment))

def _digits(s) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())

def get_connection(mcc: str | None = None) -> dict:
    dsn = os.environ.get("DATABASE_URL_METRICS")
    if not dsn:
        sys.exit("Set DATABASE_URL_METRICS (e.g. via: kb.py secret-get DATABASE_URL_METRICS).")
    conn = psycopg2.connect(_clean_dsn(dsn)); conn.set_session(readonly=True)
    q = ('SELECT "developerToken" dev, "loginCustomerId" mcc, "oauthClientId" cid, '
         '"oauthClientSecret" csec, "refreshToken" rt '
         'FROM google_ads_connections WHERE "isActive"=true')
    args = ()
    if mcc:
        q += ' AND "loginCustomerId"=%s'; args = (mcc,)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(q, args); rows = cur.fetchall()
    conn.close()
    if not rows:
        sys.exit("No active google_ads_connections found in metrics DB.")
    return dict(rows[0])

def access_token(c: dict) -> str:
    r = requests.post(TOKEN_URL, data={"grant_type":"refresh_token","client_id":c["cid"],
        "client_secret":c["csec"],"refresh_token":c["rt"]}, timeout=20)
    r.raise_for_status(); return r.json()["access_token"]

def _headers(c: dict, tok: str) -> dict:
    return {"Authorization":f"Bearer {tok}","developer-token":c["dev"],
            "login-customer-id":_digits(c["mcc"]),"Content-Type":"application/json"}

def search(c: dict, customer_id: str, query: str) -> list[dict]:
    tok = access_token(c)
    url = f"https://googleads.googleapis.com/{API}/customers/{_digits(customer_id)}/googleAds:search"
    out=[]; page=None
    while True:
        body={"query":query}
        if page: body["pageToken"]=page
        r=requests.post(url, headers=_headers(c,tok), json=body, timeout=60)
        if r.status_code!=200: sys.exit(f"Google Ads API {r.status_code}: {r.text[:700]}")
        d=r.json(); out+=d.get("results",[]) or []; page=d.get("nextPageToken")
        if not page: break
    return out

def mutate(c: dict, customer_id: str, service: str, operations: list, apply: bool, partial: bool=False) -> dict:
    tok = access_token(c)
    url = f"https://googleads.googleapis.com/{API}/customers/{_digits(customer_id)}/{service}:mutate"
    body={"operations":operations,"validateOnly":(not apply),"partialFailure":partial}
    r=requests.post(url, headers=_headers(c,tok), json=body, timeout=60)
    if r.status_code!=200: sys.exit(f"Google Ads API {r.status_code}: {r.text[:900]}")
    return r.json()

# ---- report presets (GAQL). {r} = date range macro ----
PRESETS = {
 "campaigns": ("SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,"
    " metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,"
    " metrics.conversions_value, metrics.average_cpc FROM campaign WHERE segments.date DURING {r}"
    " ORDER BY metrics.cost_micros DESC",
    ["campaign.name","campaign.status","metrics.impressions","metrics.clicks","metrics.costMicros",
     "metrics.conversions","metrics.conversionsValue"]),
 "ad_groups": ("SELECT campaign.name, ad_group.name, ad_group.status, metrics.impressions, metrics.clicks,"
    " metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM ad_group"
    " WHERE segments.date DURING {r} ORDER BY metrics.cost_micros DESC",
    ["campaign.name","ad_group.name","metrics.impressions","metrics.clicks","metrics.costMicros",
     "metrics.conversions","metrics.conversionsValue"]),
 "keywords": ("SELECT ad_group.name, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,"
    " metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,"
    " metrics.conversions_value FROM keyword_view WHERE segments.date DURING {r}"
    " ORDER BY metrics.cost_micros DESC",
    ["ad_group.name","adGroupCriterion.keyword.text","adGroupCriterion.keyword.matchType",
     "metrics.clicks","metrics.costMicros","metrics.conversions","metrics.conversionsValue"]),
 "search_terms": ("SELECT search_term_view.search_term, metrics.impressions, metrics.clicks,"
    " metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM search_term_view"
    " WHERE segments.date DURING {r} ORDER BY metrics.cost_micros DESC",
    ["searchTermView.searchTerm","metrics.clicks","metrics.costMicros","metrics.conversions","metrics.conversionsValue"]),
 "ads": ("SELECT campaign.name, ad_group.name, ad_group_ad.ad.id, ad_group_ad.status,"
    " ad_group_ad.policy_summary.approval_status, metrics.impressions, metrics.clicks,"
    " metrics.conversions FROM ad_group_ad WHERE segments.date DURING {r}",
    ["campaign.name","ad_group.name","adGroupAd.ad.id","adGroupAd.status",
     "adGroupAd.policySummary.approvalStatus","metrics.impressions","metrics.conversions"]),
 "accounts": ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code,"
    " customer_client.manager, customer_client.status FROM customer_client",
    ["customerClient.id","customerClient.descriptiveName","customerClient.currencyCode",
     "customerClient.manager","customerClient.status"]),
}

def _get(d: dict, path: str):
    cur=d
    for k in path.split("."):
        if not isinstance(cur,dict): return ""
        cur=cur.get(k)
        if cur is None: return ""
    return cur

def _fmt(path, val):
    if path.endswith("costMicros") or path.endswith("Micros"):
        try: return f"{float(val)/1e6:.2f}"
        except: return val
    return val

def print_rows(results, cols, fmt):
    rows=[{c:_fmt(c,_get(r,c)) for c in cols} for r in results]
    if fmt=="json":
        print(json.dumps(rows, ensure_ascii=False, indent=1)); return
    short=[c.split(".")[-1] for c in cols]
    widths=[max(len(short[i]), *(len(str(row[cols[i]])) for row in rows)) if rows else len(short[i]) for i in range(len(cols))]
    line=lambda vals: "  ".join(str(v).ljust(widths[i]) for i,v in enumerate(vals))
    print(line(short)); print("  ".join("-"*w for w in widths))
    for row in rows: print(line([row[c] for c in cols]))
    print(f"\n{len(rows)} rânduri")

def main():
    ap=argparse.ArgumentParser(description="Google Ads API via team MCC")
    sub=ap.add_subparsers(dest="cmd", required=True)
    r=sub.add_parser("report", help="run a report")
    r.add_argument("--customer", required=True); r.add_argument("--preset", choices=list(PRESETS))
    r.add_argument("--query", help="raw GAQL (overrides --preset)")
    r.add_argument("--range", default="LAST_7_DAYS"); r.add_argument("--format", default="table", choices=["table","json"])
    r.add_argument("--mcc")
    a=sub.add_parser("accounts", help="list child accounts under the MCC")
    a.add_argument("--mcc"); a.add_argument("--format", default="table", choices=["table","json"])
    sb=sub.add_parser("set-budget", help="change a campaign daily budget (dry-run unless --apply)")
    sb.add_argument("--customer", required=True); sb.add_argument("--campaign", required=True)
    sb.add_argument("--daily", required=True, type=float, help="RON/day"); sb.add_argument("--apply", action="store_true"); sb.add_argument("--mcc")
    ss=sub.add_parser("set-status", help="enable/pause a campaign (dry-run unless --apply)")
    ss.add_argument("--customer", required=True); ss.add_argument("--campaign", required=True)
    ss.add_argument("--status", required=True, choices=["ENABLED","PAUSED"]); ss.add_argument("--apply", action="store_true"); ss.add_argument("--mcc")
    st=sub.add_parser("set-troas", help="set target ROAS on a Max-conv-value (PMax) OR TARGET_ROAS (Shopping) campaign (dry-run unless --apply)")
    st.add_argument("--customer", required=True); st.add_argument("--campaign", required=True)
    st.add_argument("--roas", type=float, required=True, help="multiplier, e.g. 4.8 = 480%%"); st.add_argument("--apply", action="store_true"); st.add_argument("--mcc")
    sc=sub.add_parser("set-tcpa", help="switch to Max conversions + target CPA (dry-run unless --apply)")
    sc.add_argument("--customer", required=True); sc.add_argument("--campaign", required=True)
    sc.add_argument("--cpa", type=float, required=True, help="RON"); sc.add_argument("--apply", action="store_true"); sc.add_argument("--mcc")
    ng=sub.add_parser("add-negatives", help="add campaign-level negative keywords (dry-run unless --apply)")
    ng.add_argument("--customer", required=True); ng.add_argument("--campaign", required=True)
    ng.add_argument("--terms", required=True, help="comma-separated negative terms")
    ng.add_argument("--match", default="PHRASE", choices=["EXACT","PHRASE","BROAD"])
    ng.add_argument("--apply", action="store_true"); ng.add_argument("--mcc")
    ak=sub.add_parser("add-keywords", help="add positive keywords to an ad group (dry-run unless --apply)")
    ak.add_argument("--customer", required=True); ak.add_argument("--adgroup", required=True)
    ak.add_argument("--terms", required=True, help="comma-separated"); ak.add_argument("--match", default="PHRASE", choices=["EXACT","PHRASE","BROAD"])
    ak.add_argument("--apply", action="store_true"); ak.add_argument("--mcc")
    sn=sub.add_parser("add-shared-negative", help="add a negative keyword to a SHARED negative-keyword list "
                      "(sharedSet) — covers Shopping + PMax + all campaigns using the list, dry-run unless --apply")
    sn.add_argument("--customer", required=True); sn.add_argument("--shared-set", required=True, help="sharedSet id")
    sn.add_argument("--text", required=True, help="the negative keyword text")
    sn.add_argument("--match", default="PHRASE", choices=["EXACT","PHRASE","BROAD"])
    sn.add_argument("--apply", action="store_true"); sn.add_argument("--mcc")
    kw=sub.add_parser("set-keyword-status", help="enable/pause a keyword (ad group criterion) — by --resource OR --campaign+--text (dry-run unless --apply)")
    kw.add_argument("--customer", required=True)
    kw.add_argument("--resource", help="adGroupCriteria resourceName (customers/X/adGroupCriteria/AG~CRIT)")
    kw.add_argument("--campaign", help="campaign id — with --text, looks the keyword up in it")
    kw.add_argument("--text", help="keyword text to find (used with --campaign)")
    kw.add_argument("--match", choices=["EXACT","PHRASE","BROAD"], help="optional: restrict --text lookup to this match type")
    kw.add_argument("--status", required=True, choices=["ENABLED","PAUSED"])
    kw.add_argument("--apply", action="store_true"); kw.add_argument("--mcc")
    la=sub.add_parser("link-account", help="send an MCC manager-link invitation (PENDING) to a client account (dry-run unless --apply)")
    la.add_argument("--client", required=True, help="client customer id to invite under the MCC")
    la.add_argument("--apply", action="store_true"); la.add_argument("--mcc")
    cs=sub.add_parser("create-search", help="create a PAUSED Search campaign atomically (budget+campaign+geo/lang+adGroup+keywords+RSA) (dry-run unless --apply)")
    cs.add_argument("--customer", required=True)
    cs.add_argument("--name", required=True, help="campaign name")
    cs.add_argument("--budget", required=True, type=float, help="daily budget, account currency")
    cs.add_argument("--geo", required=True, help="geoTargetConstant id, e.g. 2616 Poland / 2642 Romania")
    cs.add_argument("--lang", required=True, help="languageConstant id, e.g. 1030 Polish / 1032 Romanian")
    cs.add_argument("--keywords", required=True, help="comma-separated")
    cs.add_argument("--match", default="PHRASE", choices=["EXACT","PHRASE","BROAD"])
    cs.add_argument("--headlines", required=True, help="|-separated, each <=30 chars")
    cs.add_argument("--descriptions", required=True, help="|-separated, each <=90 chars")
    cs.add_argument("--final-url", required=True, dest="final_url")
    cs.add_argument("--apply", action="store_true"); cs.add_argument("--mcc")
    cp=sub.add_parser("create-pmax", help="create a PAUSED Shopping-led Performance Max campaign (budget+campaign+merchantId+assetGroup+listingGroup root) (dry-run unless --apply)")
    cp.add_argument("--customer", required=True)
    cp.add_argument("--name", required=True, help="campaign name")
    cp.add_argument("--budget", required=True, type=float, help="daily budget, account currency")
    cp.add_argument("--merchant", required=True, help="Merchant Center id (shoppingSetting.merchantId)")
    cp.add_argument("--geo", required=True, help="geoTargetConstant id, e.g. 2642 Romania")
    cp.add_argument("--asset-group", dest="asset_group", help="asset group name (default: --name)")
    cp.add_argument("--final-url", required=True, dest="final_url")
    cp.add_argument("--apply", action="store_true"); cp.add_argument("--mcc")
    args=ap.parse_args()

    if args.cmd=="report":
        c=get_connection(args.mcc)
        if args.query: q=args.query; cols=None
        else:
            if not args.preset: sys.exit("--preset or --query required")
            q,cols=PRESETS[args.preset]; q=q.replace("{r}",args.range)
        res=search(c, args.customer, q)
        if cols: print_rows(res, cols, args.format)
        else: print(json.dumps(res, ensure_ascii=False, indent=1))
    elif args.cmd=="accounts":
        c=get_connection(args.mcc); q,cols=PRESETS["accounts"]
        res=search(c, _digits(c["mcc"]), q); print_rows(res, cols, args.format)
    elif args.cmd=="set-budget":
        c=get_connection(args.mcc)
        # find the campaign's budget resource
        rows=search(c,args.customer,f"SELECT campaign.id, campaign.name, campaign.campaign_budget FROM campaign WHERE campaign.id={_digits(args.campaign)}")
        if not rows: sys.exit("campaign not found")
        budget_res=_get(rows[0],"campaign.campaignBudget")
        ops=[{"updateMask":"amountMicros","update":{"resourceName":budget_res,"amountMicros":int(round(args.daily*1e6))}}]
        out=mutate(c,args.customer,"campaignBudgets",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN (validateOnly) — adaugă --apply ca să execuți"))
        print(f"  buget {args.daily} RON/zi pe {budget_res}"); print(json.dumps(out,ensure_ascii=False))
    elif args.cmd=="set-status":
        c=get_connection(args.mcc)
        res_name=f"customers/{_digits(args.customer)}/campaigns/{_digits(args.campaign)}"
        ops=[{"updateMask":"status","update":{"resourceName":res_name,"status":args.status}}]
        out=mutate(c,args.customer,"campaigns",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  status={args.status} pe {res_name}"); print(json.dumps(out,ensure_ascii=False))
    elif args.cmd=="set-troas":
        c=get_connection(args.mcc)
        res_name=f"customers/{_digits(args.customer)}/campaigns/{_digits(args.campaign)}"
        # read the campaign's bidding strategy first — tROAS lives on a DIFFERENT field
        # depending on it: PMax/Max-conv-value → maximizeConversionValue.targetRoas,
        # standalone TARGET_ROAS (typ. Shopping) → targetRoas.targetRoas.
        rows=search(c,args.customer,f"SELECT campaign.id, campaign.name, campaign.bidding_strategy_type FROM campaign WHERE campaign.id={_digits(args.campaign)}")
        if not rows: sys.exit("campaign not found")
        strat=_get(rows[0],"campaign.biddingStrategyType"); cname=_get(rows[0],"campaign.name")
        if strat=="MAXIMIZE_CONVERSION_VALUE":
            ops=[{"updateMask":"maximizeConversionValue.targetRoas","update":{"resourceName":res_name,"maximizeConversionValue":{"targetRoas":args.roas}}}]
        elif strat=="TARGET_ROAS":
            ops=[{"updateMask":"targetRoas.targetRoas","update":{"resourceName":res_name,"targetRoas":{"targetRoas":args.roas}}}]
        else:
            sys.exit(f"campania „{cname}\" e pe strategia {strat}, set-troas nu se aplică "
                     "(doar MAXIMIZE_CONVERSION_VALUE sau TARGET_ROAS)")
        out=mutate(c,args.customer,"campaigns",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  tROAS={args.roas} ({int(args.roas*100)}%) pe {res_name} [{strat}]"); print(json.dumps(out,ensure_ascii=False)[:300])
    elif args.cmd=="set-tcpa":
        c=get_connection(args.mcc)
        res_name=f"customers/{_digits(args.customer)}/campaigns/{_digits(args.campaign)}"
        ops=[{"updateMask":"maximizeConversions.targetCpaMicros","update":{"resourceName":res_name,"maximizeConversions":{"targetCpaMicros":int(round(args.cpa*1e6))}}}]
        out=mutate(c,args.customer,"campaigns",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  tCPA={args.cpa} RON pe {res_name}"); print(json.dumps(out,ensure_ascii=False)[:300])
    elif args.cmd=="add-negatives":
        c=get_connection(args.mcc)
        res_camp=f"customers/{_digits(args.customer)}/campaigns/{_digits(args.campaign)}"
        terms=[t.strip() for t in args.terms.split(",") if t.strip()]
        ops=[{"create":{"campaign":res_camp,"negative":True,"keyword":{"text":t,"matchType":args.match}}} for t in terms]
        out=mutate(c,args.customer,"campaignCriteria",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  {len(terms)} negative ({args.match}) pe {res_camp}: {', '.join(terms)}")
        print(json.dumps(out,ensure_ascii=False)[:400])
    elif args.cmd=="add-keywords":
        c=get_connection(args.mcc)
        ag=f"customers/{_digits(args.customer)}/adGroups/{_digits(args.adgroup)}"
        terms=[t.strip() for t in args.terms.split(",") if t.strip()]
        ops=[{"create":{"adGroup":ag,"status":"ENABLED","keyword":{"text":t,"matchType":args.match}}} for t in terms]
        out=mutate(c,args.customer,"adGroupCriteria",ops,args.apply,partial=True)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  {len(terms)} keywords ({args.match}) -> {ag}")
        print(json.dumps(out,ensure_ascii=False)[:300])
    elif args.cmd=="add-shared-negative":
        c=get_connection(args.mcc)
        cid=_digits(args.customer); sset=_digits(args.shared_set)
        res_set=f"customers/{cid}/sharedSets/{sset}"
        ops=[{"create":{"sharedSet":res_set,"keyword":{"text":args.text,"matchType":args.match}}}]
        out=mutate(c,args.customer,"sharedCriteria",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  negativ „{args.text}\" ({args.match}) -> sharedSet {sset}")
        print(json.dumps(out,ensure_ascii=False)[:400])
        if args.apply:
            # re-verify: confirm the keyword is now in the shared set
            chk=search(c,cid,"SELECT shared_criterion.resource_name, shared_criterion.keyword.text,"
                " shared_criterion.keyword.match_type, shared_criterion.type"
                f" FROM shared_criterion WHERE shared_set.id={sset}"
                " AND shared_criterion.type='KEYWORD'")
            hit=[r for r in chk if _get(r,"sharedCriterion.keyword.text")==args.text]
            print(f"  RE-VERIFICARE: {len(chk)} criterii în set; „{args.text}\" prezent: {'DA' if hit else 'NU'}")
            for r in hit:
                print(f"    {_get(r,'sharedCriterion.resourceName')} "
                      f"[{_get(r,'sharedCriterion.keyword.matchType')}]")
    elif args.cmd=="set-keyword-status":
        c=get_connection(args.mcc)
        if args.resource:
            resources=[args.resource]
        elif args.campaign and args.text:
            q=("SELECT ad_group_criterion.resource_name, ad_group_criterion.keyword.text,"
               " ad_group_criterion.keyword.match_type FROM ad_group_criterion"
               f" WHERE campaign.id={_digits(args.campaign)} AND ad_group_criterion.type='KEYWORD'"
               f" AND ad_group_criterion.keyword.text='{args.text}'")
            if args.match: q+=f" AND ad_group_criterion.keyword.match_type='{args.match}'"
            rows=search(c,args.customer,q)
            resources=[_get(r,"adGroupCriterion.resourceName") for r in rows]
            if not resources: sys.exit(f"niciun keyword „{args.text}\" (match {args.match or 'orice'}) în campania {args.campaign}")
        else:
            sys.exit("dă --resource SAU --campaign + --text")
        ops=[{"updateMask":"status","update":{"resourceName":r,"status":args.status}} for r in resources]
        out=mutate(c,args.customer,"adGroupCriteria",ops,args.apply)
        print(("APLICAT" if args.apply else "DRY-RUN — adaugă --apply ca să execuți"))
        print(f"  status={args.status} pe {len(resources)} keyword(s):")
        for r in resources: print(f"    {r}")
        print(json.dumps(out,ensure_ascii=False)[:400])
    elif args.cmd=="link-account":
        c=get_connection(args.mcc); tok=access_token(c)
        mcc=_digits(c["mcc"]); cid=_digits(args.client)
        # customerClientLinks:mutate takes a SINGLE `operation` (not an operations[] array)
        url=f"https://googleads.googleapis.com/{API}/customers/{mcc}/customerClientLinks:mutate"
        body={"operation":{"create":{"clientCustomer":f"customers/{cid}","status":"PENDING"}},"validateOnly":(not args.apply)}
        r=requests.post(url, headers=_headers(c,tok), json=body, timeout=40)
        if r.status_code!=200:
            txt=r.text
            if "ALREADY_MANAGED" in txt:
                print(f"DEJA LEGAT — client {cid} e deja sub MCC {mcc} (ALREADY_MANAGED). Nimic de trimis.")
                return
            sys.exit(f"Google Ads API {r.status_code}: {txt[:900]}")
        print(("APLICAT" if args.apply else "DRY-RUN (validateOnly) — adaugă --apply ca să trimiți invitația"))
        print(f"  invitație manager-link (PENDING) MCC {mcc} -> client {cid}")
        print(json.dumps(r.json(),ensure_ascii=False)[:400])
        print("  clientul acceptă în Admin → Access & security → Managers (NOVOS DIGITAL 746-711-0480)")
    elif args.cmd=="create-search":
        c=get_connection(args.mcc); tok=access_token(c)
        cid=_digits(args.customer)
        rn=lambda n: f"customers/{cid}/{n}"
        kws=[k.strip() for k in args.keywords.split(",") if k.strip()]
        heads=[h.strip() for h in args.headlines.split("|") if h.strip()]
        descs=[d.strip() for d in args.descriptions.split("|") if d.strip()]
        # atomic build with temp resource names (negative ids): budget -> campaign ->
        # geo + language criteria -> ad group -> keyword criteria -> RSA ad.
        ops=[
         {"campaignBudgetOperation":{"create":{"resourceName":rn("campaignBudgets/-1"),
            "amountMicros":str(int(round(args.budget*1e6))),"deliveryMethod":"STANDARD","explicitlyShared":False}}},
         {"campaignOperation":{"create":{"resourceName":rn("campaigns/-2"),"name":args.name,"status":"PAUSED",
            "advertisingChannelType":"SEARCH","campaignBudget":rn("campaignBudgets/-1"),"maximizeConversions":{},
            "containsEuPoliticalAdvertising":"DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            "networkSettings":{"targetGoogleSearch":True,"targetSearchNetwork":False,
                "targetContentNetwork":False,"targetPartnerSearchNetwork":False}}}},
         {"campaignCriterionOperation":{"create":{"campaign":rn("campaigns/-2"),
            "location":{"geoTargetConstant":f"geoTargetConstants/{_digits(args.geo)}"}}}},
         {"campaignCriterionOperation":{"create":{"campaign":rn("campaigns/-2"),
            "language":{"languageConstant":f"languageConstants/{_digits(args.lang)}"}}}},
         {"adGroupOperation":{"create":{"resourceName":rn("adGroups/-3"),"name":args.name,
            "campaign":rn("campaigns/-2"),"status":"ENABLED","type":"SEARCH_STANDARD"}}},
        ]
        for kw in kws:
            ops.append({"adGroupCriterionOperation":{"create":{"adGroup":rn("adGroups/-3"),
                "status":"ENABLED","keyword":{"text":kw,"matchType":args.match}}}})
        # NOTE: businessName is NOT a valid field on responsiveSearchAd — do not add it.
        ops.append({"adGroupAdOperation":{"create":{"adGroup":rn("adGroups/-3"),"status":"ENABLED",
            "ad":{"finalUrls":[args.final_url],
                  "responsiveSearchAd":{"headlines":[{"text":h} for h in heads],
                                        "descriptions":[{"text":d} for d in descs]}}}}})
        url=f"https://googleads.googleapis.com/{API}/customers/{cid}/googleAds:mutate"
        body={"mutateOperations":ops,"validateOnly":(not args.apply)}
        r=requests.post(url, headers=_headers(c,tok), json=body, timeout=90)
        if r.status_code!=200: sys.exit(f"Google Ads API {r.status_code}: {r.text[:900]}")
        print(("APLICAT" if args.apply else "DRY-RUN (validateOnly) — adaugă --apply ca să creezi campania"))
        print(f"  Search campaign „{args.name}\" (PAUSED) pe {cid} | buget {args.budget}/zi | geo {args.geo} lang {args.lang}")
        print(f"  {len(kws)} keyword(s) ({args.match}) | {len(heads)} headlines | {len(descs)} descriptions")
        for x in r.json().get("mutateOperationResponses",[]):
            for k,v in x.items():
                rres=v.get("resourceName","")
                if rres.endswith(f"campaigns/-2") or "/campaigns/" in rres and "Budget" not in k: print(f"  CAMPAIGN: {rres}")
                if "/adGroups/" in rres and "Criteri" not in k and "Ad" not in k: print(f"  AD GROUP: {rres}")
    elif args.cmd=="create-pmax":
        c=get_connection(args.mcc); tok=access_token(c)
        cid=_digits(args.customer)
        rn=lambda n: f"customers/{cid}/{n}"
        ag_name=args.asset_group or args.name
        # Shopping-led PMax skeleton (assets/creative added afterwards in UI or via API):
        # budget -> campaign(merchantId) -> geo criterion -> asset group -> listing group root UNIT_INCLUDED.
        ops=[
         {"campaignBudgetOperation":{"create":{"resourceName":rn("campaignBudgets/-1"),
            "amountMicros":str(int(round(args.budget*1e6))),"deliveryMethod":"STANDARD","explicitlyShared":False}}},
         {"campaignOperation":{"create":{"resourceName":rn("campaigns/-2"),"name":args.name,"status":"PAUSED",
            "advertisingChannelType":"PERFORMANCE_MAX","campaignBudget":rn("campaignBudgets/-1"),
            "maximizeConversionValue":{},"shoppingSetting":{"merchantId":_digits(args.merchant)},
            "containsEuPoliticalAdvertising":"DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING","urlExpansionOptOut":False}}},
         {"campaignCriterionOperation":{"create":{"campaign":rn("campaigns/-2"),
            "location":{"geoTargetConstant":f"geoTargetConstants/{_digits(args.geo)}"}}}},
         {"assetGroupOperation":{"create":{"resourceName":rn("assetGroups/-3"),"name":ag_name,
            "campaign":rn("campaigns/-2"),"finalUrls":[args.final_url],"status":"ENABLED"}}},
         {"assetGroupListingGroupFilterOperation":{"create":{"assetGroup":rn("assetGroups/-3"),
            "type":"UNIT_INCLUDED","listingSource":"SHOPPING"}}},
        ]
        url=f"https://googleads.googleapis.com/{API}/customers/{cid}/googleAds:mutate"
        body={"mutateOperations":ops,"validateOnly":(not args.apply)}
        r=requests.post(url, headers=_headers(c,tok), json=body, timeout=90)
        if r.status_code!=200: sys.exit(f"Google Ads API {r.status_code}: {r.text[:900]}")
        print(("APLICAT" if args.apply else "DRY-RUN (validateOnly) — adaugă --apply ca să creezi campania"))
        print(f"  PMax „{args.name}\" (PAUSED) pe {cid} | buget {args.budget}/zi | MC {args.merchant} | geo {args.geo}")
        print("  ⚠️ skeleton fără assets — adaugă creative/imagini/text pe asset group înainte de enable")
        for x in r.json().get("mutateOperationResponses",[]):
            for k,v in x.items():
                rres=v.get("resourceName","")
                if "/campaigns/" in rres and "Budget" not in k and "Criteri" not in k: print(f"  CAMPAIGN: {rres}")
                if "/assetGroups/" in rres and "ListingGroup" not in k: print(f"  ASSET GROUP: {rres}")

if __name__=="__main__":
    main()
