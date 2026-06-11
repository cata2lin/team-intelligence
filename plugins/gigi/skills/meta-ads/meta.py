# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Read Meta (Facebook/Instagram) Ads performance for any team brand — accounts, reports
(account/campaign/adset/ad), creatives, demographic/placement breakdowns, daily trend.
Token + ad accounts come from the `metrics` DB; READ-ONLY. Usage:
  DATABASE_URL_METRICS=... uv run meta.py accounts belasil
  uv run meta.py report belasil --level campaign --range last_30d --sort roas
  uv run meta.py report esteban --level ad --range last_7d --sort purchases --limit 20
  uv run meta.py creatives belasil --range last_90d [--match-folder "/path"]
  uv run meta.py breakdown belasil --by age,gender --range last_30d
  uv run meta.py trend belasil --range last_14d
"""
import os, sys, json, re, glob, argparse
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests

VER = "v23.0"
PURCH = ("omni_purchase", "offsite_conversion.fb_pixel_purchase", "purchase")  # priority order
_PG_OK = {"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}

def _clean(dsn):
    p = urlsplit(dsn)
    if not p.query: return dsn
    keep = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() in _PG_OK]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(keep), p.fragment))

def _db():
    dsn = os.environ.get("DATABASE_URL_METRICS")
    if not dsn: sys.exit("set DATABASE_URL_METRICS (kb.py secret-get DATABASE_URL_METRICS)")
    cx = psycopg2.connect(_clean(dsn)); cx.set_session(readonly=True); return cx

def accounts_for(brand):
    """Resolve a brand to its active Meta ad accounts + tokens.
    Primary: the canonical Mapping sheet (exact FB account names — e.g. Magdeal→'Reflexino').
    Fallback: name-ILIKE (when the brand isn't in the cached map)."""
    names = None
    try:
        import brandmap
        _bf, entry = brandmap.resolve(brand)
        if entry and entry.get("facebook"):
            names = [n.lower() for n in entry["facebook"]]
    except Exception:
        names = None
    cx = _db()
    with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        if names:
            c.execute('''SELECT DISTINCT a."metaAccountId" aid, a.currency cur, a.name nm, t."accessToken" tok
                         FROM meta_ad_accounts a JOIN meta_access_tokens t ON t.id = a."tokenId"
                         WHERE a."isActive" AND t."isActive" AND lower(a.name) = ANY(%s)
                         ORDER BY a.name''', (names,))
            rows = [dict(r) for r in c.fetchall()]
            if rows: return rows
        c.execute('''SELECT DISTINCT a."metaAccountId" aid, a.currency cur, a.name nm, t."accessToken" tok
                     FROM meta_ad_accounts a JOIN meta_access_tokens t ON t.id = a."tokenId"
                     LEFT JOIN brand_meta_ad_accounts ba ON ba."adAccountId" = a.id
                     LEFT JOIN brands b ON b.id = ba."brandId"
                     WHERE a."isActive" AND t."isActive"
                       AND (a.name ILIKE %s OR b.name ILIKE %s)
                     ORDER BY a.name''', (f"%{brand}%", f"%{brand}%"))
        return [dict(r) for r in c.fetchall()]

def graph(url, params):
    out = []
    while True:
        r = requests.get(url, params=params, timeout=90)
        if r.status_code != 200:
            sys.stderr.write(f"[meta] {r.status_code}: {r.text[:200]}\n"); return out
        d = r.json(); out += d.get("data", [])
        nxt = d.get("paging", {}).get("next")
        if not nxt: break
        url, params = nxt, None
    return out

def pick(items, keys):
    if not items: return 0.0
    idx = {i.get("action_type"): float(i.get("value", 0)) for i in items}
    for k in keys:
        if k in idx: return idx[k]
    return 0.0

def daterange(rng):
    rng = (rng or "last_30d").strip()
    if "," in rng:
        s, u = rng.split(",", 1); return {"time_range": json.dumps({"since": s.strip(), "until": u.strip()})}
    return {"date_preset": rng.lower()}

def insights(brand, level, rng, breakdowns=None, extra_fields=""):
    accts = accounts_for(brand)
    if not accts: sys.exit(f"no active Meta account matching '{brand}'")
    namef = {"account":"account_name","campaign":"campaign_name","adset":"adset_name","ad":"ad_name"}[level]
    fields = f"{namef},spend,impressions,clicks,ctr,cpm,reach,actions,action_values,purchase_roas{extra_fields}"
    rows = []
    for a in accts:
        p = {"level": level, "fields": fields, "limit": "500", "access_token": a["tok"], **daterange(rng)}
        if breakdowns: p["breakdowns"] = breakdowns
        for r in graph(f"https://graph.facebook.com/{VER}/{a['aid']}/insights", p):
            r["_acct"] = a["nm"]; rows.append(r)
    return accts, rows

def metricize(r):
    spend = float(r.get("spend", 0)); impr = float(r.get("impressions", 0)); clk = float(r.get("clicks", 0))
    purch = pick(r.get("actions"), PURCH); rev = pick(r.get("action_values"), PURCH)
    roas = pick(r.get("purchase_roas"), PURCH) or (rev/spend if spend else 0)
    return dict(spend=spend, impr=impr, clk=clk, purch=purch, rev=rev, roas=roas,
                cpa=(spend/purch if purch else 0), ctr=float(r.get("ctr",0) or 0), cpm=float(r.get("cpm",0) or 0))

# ---------------- commands ----------------
def cmd_accounts(a):
    accts = accounts_for(a.brand)
    if not accts: print("(niciun cont)"); return
    print(f"Conturi Meta pentru '{a.brand}':")
    for x in accts: print(f"  {x['aid']:24} {x['cur']:>4}  {x['nm']}")

def cmd_report(a):
    namef = {"account":"account_name","campaign":"campaign_name","adset":"adset_name","ad":"ad_name"}[a.level]
    accts, rows = insights(a.brand, a.level, a.range)
    agg = {}
    for r in rows:
        nm = r.get(namef, "?"); m = metricize(r); g = agg.setdefault(nm, dict(spend=0,impr=0,clk=0,purch=0,rev=0))
        for k in ("spend","impr","clk","purch","rev"): g[k]+=m[k]
    out = []
    for nm,g in agg.items():
        roas = g["rev"]/g["spend"] if g["spend"] else 0
        out.append(dict(name=nm, **g, roas=roas, cpa=(g["spend"]/g["purch"] if g["purch"] else 0),
                        ctr=(g["clk"]/g["impr"]*100 if g["impr"] else 0), cpm=(g["spend"]/g["impr"]*1000 if g["impr"] else 0)))
    keyf = {"roas":lambda x:-x["roas"],"purchases":lambda x:-x["purch"],"spend":lambda x:-x["spend"],"cpa":lambda x:(x["cpa"] or 9e9)}
    out.sort(key=keyf.get(a.sort, keyf["spend"]))
    cur = accts[0]["cur"]
    print(f"# {a.brand} · {a.level} · {a.range} · sort={a.sort} · {cur}  ({len(accts)} cont/uri)")
    print(f"{'nume':40} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6} {'CTR%':>5} {'CPM':>6} {'impr':>8}")
    tot = dict(spend=0,purch=0,rev=0,impr=0)
    for o in out[:a.limit]:
        print(f"{o['name'][:40]:40} {o['spend']:>9.0f} {o['purch']:>6.0f} {o['rev']:>9.0f} {o['roas']:>5.2f} {o['cpa']:>6.1f} {o['ctr']:>5.2f} {o['cpm']:>6.1f} {o['impr']:>8.0f}")
        for k in tot: tot[k]+=o[k]
    print(f"{'TOTAL (afișate)':40} {tot['spend']:>9.0f} {tot['purch']:>6.0f} {tot['rev']:>9.0f} {(tot['rev']/tot['spend'] if tot['spend'] else 0):>5.2f} {(tot['spend']/tot['purch'] if tot['purch'] else 0):>6.1f}")

def cmd_breakdown(a):
    accts, rows = insights(a.brand, a.level, a.range, breakdowns=a.by)
    keys = [k.strip() for k in a.by.split(",")]
    agg = {}
    for r in rows:
        bk = " · ".join(str(r.get(k,"?")) for k in keys); m = metricize(r)
        g = agg.setdefault(bk, dict(spend=0,impr=0,purch=0,rev=0))
        for k in ("spend","impr","purch","rev"): g[k]+=m[k]
    out = sorted(agg.items(), key=lambda kv:-kv[1]["spend"])
    print(f"# {a.brand} · breakdown {a.by} · {a.level} · {a.range} · {accts[0]['cur']}")
    print(f"{'segment':34} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6}")
    for bk,g in out:
        roas=g["rev"]/g["spend"] if g["spend"] else 0; cpa=g["spend"]/g["purch"] if g["purch"] else 0
        print(f"{bk[:34]:34} {g['spend']:>9.0f} {g['purch']:>6.0f} {g['rev']:>9.0f} {roas:>5.2f} {cpa:>6.1f}")

def cmd_trend(a):
    accts = accounts_for(a.brand)
    if not accts: sys.exit("no account")
    agg = {}
    for ac in accts:
        p = {"level":"account","fields":"spend,actions,action_values,impressions,clicks","time_increment":"1","limit":"500","access_token":ac["tok"], **daterange(a.range)}
        for r in graph(f"https://graph.facebook.com/{VER}/{ac['aid']}/insights", p):
            dt=r.get("date_start"); m=metricize(r); g=agg.setdefault(dt, dict(spend=0,purch=0,rev=0,clk=0,impr=0))
            for k in ("spend","purch","rev","clk","impr"): g[k]+=m[k]
    print(f"# {a.brand} · trend zilnic · {a.range} · {accts[0]['cur']}")
    print(f"{'zi':12} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6}")
    for dt in sorted(agg):
        g=agg[dt]; roas=g["rev"]/g["spend"] if g["spend"] else 0; cpa=g["spend"]/g["purch"] if g["purch"] else 0
        print(f"{dt:12} {g['spend']:>9.0f} {g['purch']:>6.0f} {g['rev']:>9.0f} {roas:>5.2f} {cpa:>6.1f}")

def cmd_creatives(a):
    accts, rows = insights(a.brand, "ad", a.range, extra_fields="")
    # add creative resolution: ad -> video_id / image, and video title
    ad_meta = {}  # ad_name -> {spend,purch,rev}
    for r in rows:
        nm=r.get("ad_name","?"); m=metricize(r); g=ad_meta.setdefault(nm, dict(spend=0,purch=0,rev=0))
        for k in ("spend","purch","rev"): g[k]+=m[k]
    out=[dict(name=nm, **g, roas=(g["rev"]/g["spend"] if g["spend"] else 0), cpa=(g["spend"]/g["purch"] if g["purch"] else 0)) for nm,g in ad_meta.items()]
    out=[o for o in out if o["spend"]>=float(a.min_spend)]
    out.sort(key=lambda x:-x["roas"] if a.sort=="roas" else -x["purch"])
    files={}
    if a.match_folder:
        def norm(s): return re.sub(r"[^a-z0-9]","", re.sub(r"\.(mp4|mov|m4v|webm)$","",(s or "").lower()))
        for f in glob.glob(os.path.join(a.match_folder,"*")):
            if f.lower().endswith((".mp4",".mov",".m4v",".webm")): files[norm(os.path.basename(f))]=os.path.basename(f)
        def match(nm):
            n=norm(nm)
            return next((v for k,v in files.items() if len(n)>=5 and (n in k or k in n)), "")
    print(f"# {a.brand} · creative-uri (ad-level) · {a.range} · sort={a.sort} · {accts[0]['cur']}")
    print(f"{'ROAS':>5} {'achiz':>6} {'spend':>9} {'CPA':>6}  reclamă" + ("  -> fișier" if a.match_folder else ""))
    for o in out[:a.limit]:
        fm = ("  -> "+match(o["name"])) if a.match_folder else ""
        print(f"{o['roas']:>5.2f} {o['purch']:>6.0f} {o['spend']:>9.0f} {o['cpa']:>6.1f}  {o['name'][:34]:34}{fm}")

def cmd_list(a):
    edge = {"campaign":"campaigns","adset":"adsets","ad":"ads"}[a.level]
    fields = "id,name,effective_status" + (",daily_budget,lifetime_budget" if a.level in ("campaign","adset") else "")
    accts = accounts_for(a.brand)
    if not accts: sys.exit(f"no account matching '{a.brand}'")
    print(f"# {a.brand} · {a.level}s  (id · status · buget/zi · nume)")
    for ac in accts:
        for x in graph(f"https://graph.facebook.com/{VER}/{ac['aid']}/{edge}",
                       {"fields": fields, "effective_status": json.dumps(["ACTIVE","PAUSED"]), "limit": "200", "access_token": ac["tok"]}):
            bud = x.get("daily_budget") or x.get("lifetime_budget") or ""
            if bud: bud = f"{int(bud)/100:.0f}"
            print(f"  {x['id']:20} {x.get('effective_status','?'):10} {str(bud):>6}  {x.get('name','')[:48]}")

# ---------------- mutations (writes — DRY-RUN by default, --apply to execute) ----------------
def find_owner(brand, obj_id):
    """Find which of the brand's accounts owns an object id, returning (account, info)."""
    for ac in accounts_for(brand):
        r = requests.get(f"https://graph.facebook.com/{VER}/{obj_id}",
                         params={"fields": "id,name,effective_status,account_id", "access_token": ac["tok"]}, timeout=30)
        if r.status_code == 200:
            return ac, r.json()
    return None, None

def _mutate(obj_id, token, params, apply):
    body = dict(params); body["access_token"] = token
    if not apply:
        body["execution_options"] = json.dumps(["validate_only"])  # Meta server-side dry-run
    return requests.post(f"https://graph.facebook.com/{VER}/{obj_id}", data=body, timeout=30)

def _do_status(a, status):
    ac, info = find_owner(a.brand, a.id)
    if not ac: sys.exit(f"obiectul {a.id} nu apare în conturile '{a.brand}' (sau token fără acces)")
    print(f"obiect {a.id}: '{info.get('name')}' · status acum {info.get('effective_status')} · cont {ac['nm']}")
    r = _mutate(a.id, ac["tok"], {"status": status}, a.apply)
    print(("APLICAT" if a.apply else "DRY-RUN (validate_only)"), "→", status, "|",
          "OK" if r.status_code == 200 else f"{r.status_code} {r.text[:260]}")

def cmd_pause(a): _do_status(a, "PAUSED")
def cmd_activate(a): _do_status(a, "ACTIVE")

def cmd_budget(a):
    ac, info = find_owner(a.brand, a.id)
    if not ac: sys.exit(f"obiectul {a.id} nu apare în conturile '{a.brand}'")
    minor = int(round(float(a.daily) * 100))  # account-currency minor units (cents/bani)
    field = "lifetime_budget" if a.lifetime else "daily_budget"
    print(f"obiect {a.id}: '{info.get('name')}' · cont {ac['nm']} ({ac['cur']}) · {field} nou: {a.daily} {ac['cur']}")
    r = _mutate(a.id, ac["tok"], {field: minor}, a.apply)
    print(("APLICAT" if a.apply else "DRY-RUN (validate_only)"), "→", field, a.daily, "|",
          "OK" if r.status_code == 200 else f"{r.status_code} {r.text[:260]}")

def main():
    ap = argparse.ArgumentParser(description="Meta Ads performance + gated mutations (creds from metrics DB)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s=sub.add_parser("accounts"); s.add_argument("brand")
    s=sub.add_parser("report"); s.add_argument("brand"); s.add_argument("--level", default="campaign", choices=["account","campaign","adset","ad"]); s.add_argument("--range", default="last_30d"); s.add_argument("--sort", default="spend", choices=["spend","roas","purchases","cpa"]); s.add_argument("--limit", type=int, default=25)
    s=sub.add_parser("breakdown"); s.add_argument("brand"); s.add_argument("--by", required=True, help="age,gender,publisher_platform,platform_position,country,region,impression_device"); s.add_argument("--level", default="account", choices=["account","campaign","adset","ad"]); s.add_argument("--range", default="last_30d")
    s=sub.add_parser("trend"); s.add_argument("brand"); s.add_argument("--range", default="last_14d")
    s=sub.add_parser("list"); s.add_argument("brand"); s.add_argument("--level", default="campaign", choices=["campaign","adset","ad"])
    s=sub.add_parser("creatives"); s.add_argument("brand"); s.add_argument("--range", default="last_90d"); s.add_argument("--sort", default="roas", choices=["roas","purchases"]); s.add_argument("--limit", type=int, default=20); s.add_argument("--min-spend", default="150"); s.add_argument("--match-folder", default="")
    # --- mutations (dry-run by default; add --apply to execute) ---
    for nm in ("pause","activate"):
        s=sub.add_parser(nm); s.add_argument("brand"); s.add_argument("id", help="campaign / adset / ad id"); s.add_argument("--apply", action="store_true")
    s=sub.add_parser("budget"); s.add_argument("brand"); s.add_argument("id", help="campaign (CBO) or adset (ABO) id"); s.add_argument("--daily", required=True); s.add_argument("--lifetime", action="store_true"); s.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"accounts":cmd_accounts,"report":cmd_report,"breakdown":cmd_breakdown,"trend":cmd_trend,"creatives":cmd_creatives,
     "list":cmd_list,"pause":cmd_pause,"activate":cmd_activate,"budget":cmd_budget}[a.cmd](a)

if __name__ == "__main__":
    main()
