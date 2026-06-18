# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "google-auth>=2.0"]
# ///
"""
social_listen.py — Social listening RO pentru brandurile Arona.

CAUTĂ ACTIV mențiuni despre un brand pe web/social și măsoară buzz-ul, din surse pe
care le avem deja (fără tool plătit de mention-monitoring, fără cod extern neverificat):

  • web/social mentions   → DataForSEO Content Analysis (mențiuni reale pe bloguri,
                            forumuri, news, social, ecommerce, CU sentiment + dată).
                            Fallback: Google RO organic SERP (pagini terțe care ne pomenesc).
  • Reddit                → căutare publică (free, fără cheie) pe numele brandului.
  • branded search        → Google Search Console: căutări pe numele brandului,
                            fereastra curentă vs precedentă = semnalul „se vorbește/caută despre noi".
  • (best effort) Instagram hashtag → Graph API, dacă există token IG în KB.

Fiecare sondă degradează grațios (printează `n/a` + motivul) dacă-i lipsește creditul/credențialul.
READ-ONLY peste tot.

Creds din KB (env-first): DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD, GA4_SA_JSON,
(opțional) IG_GRAPH_TOKEN + IG_BUSINESS_ID.

Usage:
  KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
  export DATAFORSEO_LOGIN="$(uv run "$KB" secret-get DATAFORSEO_LOGIN)"
  export DATAFORSEO_PASSWORD="$(uv run "$KB" secret-get DATAFORSEO_PASSWORD)"
  export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
  uv run social_listen.py scan nubra --days 7
  uv run social_listen.py scan esteban --days 14 --only mentions,reddit
  uv run social_listen.py brands              # ce branduri sunt configurate
"""
import argparse, datetime as dt, json, os, subprocess, sys
import requests

KB_DEFAULT = os.path.expanduser(
    "~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")

# ───────────────────────── brand config ─────────────────────────
# `terms`   = ce căutăm (numele + variante). `context` = cuvinte de dezambiguizare
# pentru nume generice (ex. „nubra" = și un brand internațional de sutiene → adaugă „parfum").
# `owned`   = domeniile NOASTRE (le filtrăm din „mențiuni" ca să rămână doar terții).
BRANDS = {
    "nubra":   {"name": "Nubra", "site": "nubra.ro", "terms": ["nubra"],
                "context": ["parfum", "parfumuri"], "ambiguous": True},
    "esteban": {"name": "Maison d'Esteban", "site": "esteban.ro",
                "terms": ["maison d'esteban", "esteban.ro", "maison desteban"], "context": ["parfum"]},
    "gt":      {"name": "George Talent", "site": "george-talent.ro",
                "terms": ["george talent", "george-talent"], "context": ["parfum"]},
    "grandia": {"name": "Grandia", "site": "grandia.ro", "terms": ["grandia.ro", "grandia"],
                "context": ["gradina", "casa"], "ambiguous": True},
    "belasil": {"name": "Belasil", "site": "belasil.ro", "terms": ["belasil"], "context": []},
    "gento":   {"name": "Gento", "site": "gento.ro", "terms": ["gento.ro"], "context": ["geanta", "genti"], "ambiguous": True},
    "covoria": {"name": "Covoria", "site": "covoria.ro", "terms": ["covoria"], "context": ["covor", "covoare"]},
}
# toate domeniile noastre — pt filtrare „doar terți"
OWNED_DOMAINS = {b["site"] for b in BRANDS.values()} | {
    "esteban.ro", "george-talent.ro", "nubra.ro", "grandia.ro", "belasil.ro",
    "gento.ro", "covoria.ro", "myshopify.com", "arona.ro", "labnoir.ro",
}

def cfg(brand):
    b = brand.lower().strip()
    if b in BRANDS:
        return b, BRANDS[b]
    # fallback: brand necunoscut → caută numele așa cum e dat
    return b, {"name": brand, "site": "", "terms": [brand], "context": [], "ambiguous": True}

# ───────────────────────── creds ─────────────────────────
def secret(name):
    v = os.environ.get(name)
    if v:
        return v
    kb = os.environ.get("KB_PY") or KB_DEFAULT
    if os.path.exists(kb):
        try:
            return subprocess.run(["uv", "run", kb, "secret-get", name],
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            return ""
    return ""

# ───────────────────────── helpers ─────────────────────────
def _owned(domain):
    d = (domain or "").lower().lstrip("www.")
    return any(d == o or d.endswith("." + o) for o in OWNED_DOMAINS)

def _hr(title):
    print("\n" + "═" * 72)
    print(f"  {title}")
    print("═" * 72)

def _pct(cur, prev):
    if not prev:
        return "—" if not cur else "+∞"
    return f"{(cur-prev)/prev*100:+.0f}%"

# ───────────────────────── PROBE 1: web/social mentions (DataForSEO) ─────────────────────────
DFS = "https://api.dataforseo.com"

def _dfs_post(path, payload):
    auth = (secret("DATAFORSEO_LOGIN"), secret("DATAFORSEO_PASSWORD"))
    if not auth[0] or not auth[1]:
        raise RuntimeError("lipsesc DATAFORSEO_LOGIN/PASSWORD")
    r = requests.post(DFS + path, auth=auth, json=payload, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    if d.get("status_code") != 20000:
        raise RuntimeError(d.get("status_message"))
    task = (d.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        raise RuntimeError(task.get("status_message"))
    return task.get("result") or []

def probe_mentions(brand, conf, days):
    _hr(f"WEB / SOCIAL MENTIONS — „{conf['name']}\"  (ultimele {days} zile, via DataForSEO Content Analysis)")
    kw = conf["terms"][0]
    out = {}
    # (a) summary: total mențiuni + distribuție sentiment — sondă independentă
    try:
        res = _dfs_post("/v3/content_analysis/summary/live", [{"keyword": kw, "internal_list_limit": 5}])
        s = res[0] if res else {}
        total = s.get("total_count") or 0
        senti = (s.get("sentiment_connotations") or {})
        rating = (s.get("rating") or {})
        out["total"] = total
        print(f"  mențiuni totale indexate (tot timpul): {total:,}")
        if senti:
            order = ["positive", "neutral", "negative", "love", "joy", "anger", "fear", "sadness"]
            parts = [f"{k}={senti[k]}" for k in order if senti.get(k)]
            if parts:
                print(f"  sentiment:  " + "  ".join(parts))
        if rating.get("value"):
            print(f"  rating mediu: {rating.get('value'):.2f}  (din {rating.get('votes_count',0)} voturi)")
    except Exception as e:
        print(f"  (summary indisponibil: {e})")

    # (b) mențiuni recente concrete — Google RO SERP (localizat) e sursa cea mai CURATĂ pt brandurile
    # RO: content_analysis e un index GLOBAL plin de omonime (NuBra sutiene, valea Nubra, Esteban
    # Paris Parfums FR...). SERP RO scoate profile/postări terțe reale (Instagram/Facebook/forumuri).
    try:
        res = _dfs_post("/v3/serp/google/organic/live/regular",
                        [{"keyword": kw, "location_name": "Romania", "language_name": "Romanian", "depth": 30}])
        items = (res[0].get("items") or []) if res else []
        third = [it for it in items if it.get("type") == "organic" and not _owned(it.get("domain", ""))]
        socials = [it for it in third if any(s in (it.get("domain") or "")
                   for s in ("instagram", "facebook", "tiktok", "youtube", "reddit"))]
        print(f"\n  mențiuni/profile TERȚE în Google RO pe „{kw}\": {len(third)}  "
              f"(din care {len(socials)} pe social)")
        for it in third[:15]:
            mark = " 📱" if it in socials else "   "
            print(f"  {mark}#{it.get('rank_absolute'):>2} {(it.get('domain') or '')[:26]:26} {(it.get('title') or '')[:64]}")
        out["serp_thirdparty"] = len(third); out["serp_social"] = len(socials)
    except Exception as e:
        print(f"\n  mențiuni recente n/a — {e}")

    if conf.get("ambiguous"):
        print(f"\n  ⚠ nume ambiguu — „{kw}\" prinde și brand(uri) omonim(e); cifrele „totale\" sunt umflate. "
              f"Pentru curățenie filtrează pe context: {', '.join(conf.get('context') or []) or '—'} sau pe nubra.ro.")
    return out or None

# ───────────────────────── PROBE 2: Reddit (free) ─────────────────────────
def probe_reddit(brand, conf, days):
    _hr(f"REDDIT — „{conf['name']}\"  (căutare publică, ultimele ~{days} zile)")
    term = conf["terms"][0]
    q = f'"{term}"'
    if conf.get("ambiguous") and conf.get("context"):
        q = f'"{term}" ({" OR ".join(conf["context"])})'
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    children = None
    for host in ("https://www.reddit.com", "https://old.reddit.com"):
        try:
            r = requests.get(f"{host}/search.json",
                             params={"q": q, "sort": "new", "limit": 25, "t": "year"},
                             headers={"User-Agent": ua, "Accept": "application/json"}, timeout=30)
            if r.status_code == 200:
                children = (r.json().get("data") or {}).get("children") or []
                break
        except Exception:
            continue
    try:
        if children is None:
            print("  n/a — Reddit a blocat cererea (403/429 de pe IP server). "
                  "Volumul RO pe Reddit e oricum mic; rulează din altă rețea dacă vrei să forțezi.")
            return None
        cutoff = (dt.datetime.utcnow() - dt.timedelta(days=days)).timestamp()
        recent = []
        for c in children:
            d = c.get("data") or {}
            created = d.get("created_utc") or 0
            recent.append({
                "sub": d.get("subreddit", ""), "title": (d.get("title") or "")[:90],
                "url": "https://reddit.com" + (d.get("permalink") or ""),
                "score": d.get("score", 0), "n": d.get("num_comments", 0),
                "fresh": created >= cutoff,
                "date": dt.datetime.utcfromtimestamp(created).date().isoformat() if created else "",
            })
        fresh = [x for x in recent if x["fresh"]]
        print(f"  rezultate (an): {len(recent)}  ·  în fereastra de {days} zile: {len(fresh)}")
        for x in (recent[:10]):
            tag = "🆕" if x["fresh"] else "  "
            print(f"   {tag} [{x['date']}] r/{x['sub'][:18]:18} ↑{x['score']:<4} 💬{x['n']:<3} {x['title']}")
        return {"total": len(recent), "fresh": len(fresh)}
    except Exception as e:
        print(f"  n/a — {e}")
        return None

# ───────────────────────── PROBE 3: branded search (GSC) ─────────────────────────
GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

def _gsc_token():
    raw = secret("GA4_SA_JSON")
    if not raw:
        raise RuntimeError("lipsește GA4_SA_JSON")
    from google.oauth2 import service_account
    import google.auth.transport.requests as gar
    creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=GSC_SCOPES)
    creds.refresh(gar.Request())
    return creds.token

def _gsc_query(token, site, start, end):
    url = f"https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(site, safe='')}/searchAnalytics/query"
    body = {"startDate": start, "endDate": end, "dimensions": ["query"], "rowLimit": 1000}
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} ({site}): {r.text[:120]}")
    return r.json().get("rows", [])

def probe_gsc(brand, conf, days):
    _hr(f"BRANDED SEARCH — „{conf['name']}\"  (Google Search Console, {days}z vs {days}z anterioare)")
    if not conf.get("site"):
        print("  n/a — fără site configurat")
        return None
    try:
        token = _gsc_token()
    except Exception as e:
        print(f"  n/a — {e}")
        return None
    end = dt.date.today() - dt.timedelta(days=3)            # GSC are lag ~2-3 zile
    cur_start = end - dt.timedelta(days=days - 1)
    prev_end = cur_start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=days - 1)
    toks = [t.lower() for t in conf["terms"]] + [conf["name"].lower().split()[0]]
    def branded(rows):
        clk = imp = 0
        for row in rows:
            qd = (row.get("keys") or [""])[0].lower()
            if any(t in qd for t in toks):
                clk += row.get("clicks", 0); imp += row.get("impressions", 0)
        return clk, imp
    site_variants = [f"sc-domain:{conf['site']}", f"https://{conf['site']}/", f"https://www.{conf['site']}/"]
    for site in site_variants:
        try:
            cur = branded(_gsc_query(token, site, cur_start.isoformat(), end.isoformat()))
            prv = branded(_gsc_query(token, site, prev_start.isoformat(), prev_end.isoformat()))
            print(f"  property: {site}")
            print(f"  branded clicks:      {cur[0]:>6}   (anterior {prv[0]:>6})   {_pct(cur[0], prv[0])}")
            print(f"  branded impresii:    {cur[1]:>6}   (anterior {prv[1]:>6})   {_pct(cur[1], prv[1])}")
            chg = (cur[1] - prv[1]) / prv[1] if prv[1] else 0
            level = "spike" if chg > 0.30 else "uptick" if chg > 0.15 else "flat"
            if level == "spike":
                print("  🔺 SPIKE de interes pe numele brandului (>+30% impresii) — cineva vorbește/caută brandul.")
            elif level == "uptick":
                print("  🔼 ușoară creștere de interes pe nume (+15–30%) — consistent cu mai multe vânzări, nu neapărat viral.")
            return {"site": site, "cur": cur, "prev": prv, "spike": level == "spike", "level": level, "chg": chg}
        except Exception:
            continue
    print(f"  n/a — niciun property accesibil ({', '.join(site_variants)}); SA looker-sheets nu e Full user pe proprietate?")
    return None

# ───────────────────────── PROBE 4: Instagram hashtag (best effort) ─────────────────────────
def probe_instagram(brand, conf, days):
    _hr(f"INSTAGRAM HASHTAG — #{conf['terms'][0].replace(' ', '').replace('.', '')}  (best-effort, Graph API)")
    tok = secret("IG_GRAPH_TOKEN")
    igid = secret("IG_BUSINESS_ID")
    if not tok or not igid:
        print("  n/a — lipsește IG_GRAPH_TOKEN / IG_BUSINESS_ID în KB.")
        print("       (ca să-l activăm: token Graph cu instagram_basic+instagram_manage_insights pe un cont IG Business)")
        return None
    tag = conf["terms"][0].replace(" ", "").replace(".", "").replace("'", "")
    try:
        s = requests.get(f"https://graph.facebook.com/v21.0/ig_hashtag_search",
                         params={"user_id": igid, "q": tag, "access_token": tok}, timeout=30).json()
        hid = ((s.get("data") or [{}])[0] or {}).get("id")
        if not hid:
            print(f"  hashtag #{tag} negăsit / fără date.")
            return None
        m = requests.get(f"https://graph.facebook.com/v21.0/{hid}/recent_media",
                         params={"user_id": igid, "fields": "caption,permalink,timestamp,like_count,comments_count",
                                 "access_token": tok, "limit": 30}, timeout=30).json()
        items = m.get("data") or []
        print(f"  postări recente cu #{tag}: {len(items)}")
        for it in items[:10]:
            cap = (it.get("caption") or "")[:80].replace("\n", " ")
            print(f"   • [{(it.get('timestamp') or '')[:10]}] ❤{it.get('like_count',0):<4} 💬{it.get('comments_count',0):<3} {cap}")
        return {"recent_media": len(items)}
    except Exception as e:
        print(f"  n/a — {e}")
        return None

# ───────────────────────── main ─────────────────────────
PROBES = {"mentions": probe_mentions, "reddit": probe_reddit, "gsc": probe_gsc, "instagram": probe_instagram}

def cmd_scan(args):
    key, conf = cfg(args.brand)
    only = [x.strip() for x in args.only.split(",")] if args.only else list(PROBES)
    print(f"\n🎧 SOCIAL LISTENING · {conf['name']}  ·  fereastră {args.days} zile  ·  sonde: {', '.join(only)}")
    summary = {}
    for name in only:
        if name in PROBES:
            try:
                summary[name] = PROBES[name](args.brand, conf, args.days)
            except Exception as e:
                print(f"\n[{name}] eroare neașteptată: {e}")
                summary[name] = None
    # verdict scurt
    _hr("VERDICT")
    g = summary.get("gsc") or {}
    level = g.get("level")
    m = summary.get("mentions") or {}
    fresh_reddit = (summary.get("reddit") or {}).get("fresh") if summary.get("reddit") else 0
    serp_third = m.get("serp_thirdparty")
    serp_social = m.get("serp_social")
    bits = []
    if level == "spike":
        bits.append(f"🔺 branded search în SPIKE ({g.get('chg',0)*100:+.0f}% impresii) — puseu organic real")
    elif level == "uptick":
        bits.append(f"🔼 branded search ușor în creștere ({g.get('chg',0)*100:+.0f}% impresii) — consistent cu mai multe vânzări, nu viral")
    elif summary.get("gsc"):
        bits.append("branded search stabil (fără puseu organic)")
    if serp_third is not None:
        extra = f" ({serp_social} pe social)" if serp_social else ""
        bits.append(f"{serp_third} domenii terțe ne pomenesc în Google RO{extra}")
    if fresh_reddit:
        bits.append(f"{fresh_reddit} fire Reddit proaspete")
    print("  " + ("\n  ".join("• " + b for b in bits) if bits else "semnal insuficient din sursele disponibile."))
    print()

def cmd_brands(args):
    print("Branduri configurate:")
    for k, v in BRANDS.items():
        amb = "  ⚠ambiguu" if v.get("ambiguous") else ""
        print(f"  {k:10} {v['name']:22} {v['site']:18}{amb}")

def main():
    ap = argparse.ArgumentParser(description="Social listening RO pentru brandurile Arona.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="caută mențiuni + buzz pentru un brand")
    sc.add_argument("brand")
    sc.add_argument("--days", type=int, default=7)
    sc.add_argument("--only", default="", help="subset de sonde: mentions,reddit,gsc,instagram")
    sc.set_defaults(fn=cmd_scan)
    sub.add_parser("brands", help="listează brandurile configurate").set_defaults(fn=cmd_brands)
    args = ap.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
