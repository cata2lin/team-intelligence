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
import os, sys, json, re, glob, argparse, datetime, subprocess
from pathlib import Path
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

# ---------------- FX: convert any account currency → RON, per day (dynamic, from AWBprint.exchange_rates) ----------------
_RATES = None
def rates():
    global _RATES
    if _RATES is None:
        _RATES = {"RON":1.0,"USD":4.55,"EUR":5.24,"PLN":1.23,"HUF":0.01,"CZK":0.22}
        try:
            v = _kb_secret("CURRENCY_RATES_RON")
            if v: _RATES.update({k.upper(): float(x) for k, x in json.loads(v).items()})
        except Exception: pass
    return _RATES
def _rate(cur): return rates().get((cur or "RON").upper(), 1.0)
def _kb_secret(key):
    try:
        kb = Path.home()/".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
        return subprocess.run(["uv","run",str(kb),"secret-get",key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception:
        return ""
def fx_index(currencies, start, end):
    """{(CURRENCY, date): rate→RON} from AWBprint.exchange_rates, forward-filled (per-day, dynamic)."""
    from collections import defaultdict
    need = sorted({(c or "RON").upper() for c in currencies if (c or "RON").upper() != "RON"})
    if not need: return {}
    dsn = os.environ.get("DATABASE_URL_AWBPRINT") or _kb_secret("DATABASE_URL_AWBPRINT")
    if not dsn: return {}
    s = datetime.date.fromisoformat(start); e = datetime.date.fromisoformat(end)
    try:
        cx = psycopg2.connect(_clean(dsn)); cx.set_session(readonly=True)
        with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""SELECT currency, rate_date, rate, multiplier FROM exchange_rates
                         WHERE currency = ANY(%s) AND rate_date BETWEEN %s AND %s ORDER BY currency, rate_date""",
                      (need, s - datetime.timedelta(days=10), e))
            qr = c.fetchall()
    except Exception as ex:
        sys.stderr.write(f"[meta] FX AWBprint indisponibil ({str(ex)[:80]}); curs fix din KB\n"); return {}
    by = defaultdict(list)
    for r in qr: by[r["currency"]].append((r["rate_date"], float(r["rate"]) / float(r["multiplier"] or 1)))
    out = {}
    for cur, series in by.items():
        last=None; i=0; d=s - datetime.timedelta(days=10)
        while d <= e:
            while i < len(series) and series[i][0] <= d: last=series[i][1]; i+=1
            if last is not None and s <= d <= e: out[(cur, d)] = last
            d += datetime.timedelta(days=1)
    return out
def conv(amount, cur, day, idx):
    cu = (cur or "RON").upper()
    if cu == "RON": return amount
    r = idx.get((cu, day)) if day else None
    return amount * (r if r else _rate(cu))
def _pdate(s):
    try: return datetime.date.fromisoformat((s or "")[:10])
    except Exception: return None

def daterange(rng):
    """→ (start, end) ISO dates (so we control both the API time_range and the FX lookup)."""
    rng = (rng or "last_30d").strip().lower()
    if "," in rng:
        s, u = rng.split(",", 1); return s.strip(), u.strip()
    t = datetime.date.today()
    if rng == "today": return t.isoformat(), t.isoformat()
    if rng == "yesterday": d=t-datetime.timedelta(1); return d.isoformat(), d.isoformat()
    if rng == "this_month": return t.replace(day=1).isoformat(), t.isoformat()
    if rng == "last_month":
        prev_last = t.replace(day=1) - datetime.timedelta(1)
        return prev_last.replace(day=1).isoformat(), prev_last.isoformat()
    if rng == "maximum": return (t - datetime.timedelta(days=1095)).isoformat(), t.isoformat()
    n = {"last_7d":7,"last_14d":14,"last_28d":28,"last_30d":30,"last_90d":90}.get(rng, 30)
    return (t - datetime.timedelta(n)).isoformat(), t.isoformat()

def insights(brand, level, rng, breakdowns=None, extra_fields="", daily=True):
    accts = accounts_for(brand)
    if not accts: sys.exit(f"no active Meta account matching '{brand}'")
    namef = {"account":"account_name","campaign":"campaign_name","adset":"adset_name","ad":"ad_name"}[level]
    fields = f"{namef},spend,impressions,clicks,ctr,cpm,reach,actions,action_values,purchase_roas{extra_fields}"
    start, end = daterange(rng)
    rows = []
    for a in accts:
        p = {"level": level, "fields": fields, "time_range": json.dumps({"since": start, "until": end}),
             "limit": "500", "access_token": a["tok"]}
        if daily: p["time_increment"] = "1"   # daily → per-day FX (skip for heavy multi-campaign accounts)
        if breakdowns: p["breakdowns"] = breakdowns
        for r in graph(f"https://graph.facebook.com/{VER}/{a['aid']}/insights", p):
            r["_acct"] = a["nm"]; r["_cur"] = a["cur"]; rows.append(r)
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
    start, end = daterange(a.range)
    accts, rows = insights(a.brand, a.level, a.range)
    idx = fx_index([x["cur"] for x in accts], start, end)
    agg = {}
    for r in rows:
        nm = r.get(namef, "?"); m = metricize(r); dd = _pdate(r.get("date_start"))
        g = agg.setdefault(nm, dict(spend=0,impr=0,clk=0,purch=0,rev=0))
        g["spend"] += conv(m["spend"], r["_cur"], dd, idx); g["rev"] += conv(m["rev"], r["_cur"], dd, idx)
        g["impr"] += m["impr"]; g["clk"] += m["clk"]; g["purch"] += m["purch"]
    out = []
    for nm,g in agg.items():
        roas = g["rev"]/g["spend"] if g["spend"] else 0
        out.append(dict(name=nm, **g, roas=roas, cpa=(g["spend"]/g["purch"] if g["purch"] else 0),
                        ctr=(g["clk"]/g["impr"]*100 if g["impr"] else 0), cpm=(g["spend"]/g["impr"]*1000 if g["impr"] else 0)))
    keyf = {"roas":lambda x:-x["roas"],"purchases":lambda x:-x["purch"],"spend":lambda x:-x["spend"],"cpa":lambda x:(x["cpa"] or 9e9)}
    out.sort(key=keyf.get(a.sort, keyf["spend"]))
    print(f"# {a.brand} · {a.level} · {a.range} · sort={a.sort} · RON ({'FX/zi' if idx else 'curs fix'})  ({len(accts)} cont/uri)")
    print(f"{'nume':40} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6} {'CTR%':>5} {'CPM':>6} {'impr':>8}")
    tot = dict(spend=0,purch=0,rev=0,impr=0)
    for o in out[:a.limit]:
        print(f"{o['name'][:40]:40} {o['spend']:>9.0f} {o['purch']:>6.0f} {o['rev']:>9.0f} {o['roas']:>5.2f} {o['cpa']:>6.1f} {o['ctr']:>5.2f} {o['cpm']:>6.1f} {o['impr']:>8.0f}")
        for k in tot: tot[k]+=o[k]
    print(f"{'TOTAL (afișate)':40} {tot['spend']:>9.0f} {tot['purch']:>6.0f} {tot['rev']:>9.0f} {(tot['rev']/tot['spend'] if tot['spend'] else 0):>5.2f} {(tot['spend']/tot['purch'] if tot['purch'] else 0):>6.1f}")

def cmd_breakdown(a):
    start, end = daterange(a.range)
    accts, rows = insights(a.brand, a.level, a.range, breakdowns=a.by)
    idx = fx_index([x["cur"] for x in accts], start, end)
    keys = [k.strip() for k in a.by.split(",")]
    agg = {}
    for r in rows:
        bk = " · ".join(str(r.get(k,"?")) for k in keys); m = metricize(r); dd = _pdate(r.get("date_start"))
        g = agg.setdefault(bk, dict(spend=0,impr=0,purch=0,rev=0))
        g["spend"] += conv(m["spend"], r["_cur"], dd, idx); g["rev"] += conv(m["rev"], r["_cur"], dd, idx)
        g["impr"] += m["impr"]; g["purch"] += m["purch"]
    out = sorted(agg.items(), key=lambda kv:-kv[1]["spend"])
    print(f"# {a.brand} · breakdown {a.by} · {a.level} · {a.range} · RON")
    print(f"{'segment':34} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6}")
    for bk,g in out:
        roas=g["rev"]/g["spend"] if g["spend"] else 0; cpa=g["spend"]/g["purch"] if g["purch"] else 0
        print(f"{bk[:34]:34} {g['spend']:>9.0f} {g['purch']:>6.0f} {g['rev']:>9.0f} {roas:>5.2f} {cpa:>6.1f}")

def cmd_trend(a):
    accts = accounts_for(a.brand)
    if not accts: sys.exit("no account")
    start, end = daterange(a.range)
    idx = fx_index([x["cur"] for x in accts], start, end)
    agg = {}
    for ac in accts:
        p = {"level":"account","fields":"spend,actions,action_values,impressions,clicks","time_increment":"1","limit":"500","access_token":ac["tok"], "time_range": json.dumps({"since":start,"until":end})}
        for r in graph(f"https://graph.facebook.com/{VER}/{ac['aid']}/insights", p):
            dt=r.get("date_start"); m=metricize(r); dd=_pdate(dt); g=agg.setdefault(dt, dict(spend=0,purch=0,rev=0,clk=0,impr=0))
            g["spend"]+=conv(m["spend"],ac["cur"],dd,idx); g["rev"]+=conv(m["rev"],ac["cur"],dd,idx)
            g["purch"]+=m["purch"]; g["clk"]+=m["clk"]; g["impr"]+=m["impr"]
    print(f"# {a.brand} · trend zilnic · {a.range} · RON")
    print(f"{'zi':12} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6}")
    for dt in sorted(agg):
        g=agg[dt]; roas=g["rev"]/g["spend"] if g["spend"] else 0; cpa=g["spend"]/g["purch"] if g["purch"] else 0
        print(f"{dt:12} {g['spend']:>9.0f} {g['purch']:>6.0f} {g['rev']:>9.0f} {roas:>5.2f} {cpa:>6.1f}")

def cmd_creatives(a):
    start, end = daterange(a.range)
    accts, rows = insights(a.brand, "ad", a.range, extra_fields="")
    idx = fx_index([x["cur"] for x in accts], start, end)
    ad_meta = {}  # ad_name -> {spend,purch,rev} (RON)
    for r in rows:
        nm=r.get("ad_name","?"); m=metricize(r); dd=_pdate(r.get("date_start")); g=ad_meta.setdefault(nm, dict(spend=0,purch=0,rev=0))
        g["spend"]+=conv(m["spend"],r["_cur"],dd,idx); g["rev"]+=conv(m["rev"],r["_cur"],dd,idx); g["purch"]+=m["purch"]
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
    print(f"# {a.brand} · creative-uri (ad-level) · {a.range} · sort={a.sort} · RON")
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

def cmd_products(a):
    """Spend per PRODUCT (Nomenclator mapping), split VÂNZARE vs TEST — for multi-product accounts (Reflexino/Magdeal)."""
    import prodmap
    start, end = daterange(a.range)
    accts, rows = insights(a.brand, "campaign", a.range, daily=False)   # range-aggregated (heavy accounts)
    idx = fx_index([x["cur"] for x in accts], start, end)
    agg = {}
    for r in rows:
        cname = r.get("campaign_name",""); m = metricize(r); dd = _pdate(r.get("date_start"))
        pg = prodmap.product_of("facebook", r["_acct"], cname, "")
        spend = conv(m["spend"], r["_cur"], dd, idx); rev = conv(m["rev"], r["_cur"], dd, idx)
        g = agg.setdefault(pg, dict(s_sp=0,s_pu=0,s_rv=0,t_sp=0))
        if prodmap.is_test(cname): g["t_sp"] += spend
        else: g["s_sp"] += spend; g["s_pu"] += m["purch"]; g["s_rv"] += rev
    out = sorted(agg.items(), key=lambda kv: -(kv[1]["s_sp"]+kv[1]["t_sp"]))
    print(f"# {a.brand} · produse (FB) · {a.range} · RON   [VÂNZARE | TEST]")
    print(f"{'produs':24} {'spend':>9} {'achiz':>6} {'venit':>9} {'ROAS':>5} {'CPA':>6} | {'TEST':>8}")
    T = dict(s=0,p=0,r=0,t=0)
    for pg,g in out:
        roas = g["s_rv"]/g["s_sp"] if g["s_sp"] else 0; cpa = g["s_sp"]/g["s_pu"] if g["s_pu"] else 0
        print(f"{pg[:24]:24} {g['s_sp']:>9.0f} {g['s_pu']:>6.0f} {g['s_rv']:>9.0f} {roas:>5.2f} {cpa:>6.1f} | {g['t_sp']:>8.0f}")
        T['s']+=g['s_sp']; T['p']+=g['s_pu']; T['r']+=g['s_rv']; T['t']+=g['t_sp']
    print(f"{'TOTAL vânzare':24} {T['s']:>9.0f} {T['p']:>6.0f} {T['r']:>9.0f} {(T['r']/T['s'] if T['s'] else 0):>5.2f} {'':>6} | {T['t']:>8.0f}")

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
    s=sub.add_parser("products"); s.add_argument("brand"); s.add_argument("--range", default="last_30d")
    # --- mutations (dry-run by default; add --apply to execute) ---
    for nm in ("pause","activate"):
        s=sub.add_parser(nm); s.add_argument("brand"); s.add_argument("id", help="campaign / adset / ad id"); s.add_argument("--apply", action="store_true")
    s=sub.add_parser("budget"); s.add_argument("brand"); s.add_argument("id", help="campaign (CBO) or adset (ABO) id"); s.add_argument("--daily", required=True); s.add_argument("--lifetime", action="store_true"); s.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"accounts":cmd_accounts,"report":cmd_report,"breakdown":cmd_breakdown,"trend":cmd_trend,"creatives":cmd_creatives,
     "list":cmd_list,"products":cmd_products,"pause":cmd_pause,"activate":cmd_activate,"budget":cmd_budget}[a.cmd](a)

if __name__ == "__main__":
    main()
