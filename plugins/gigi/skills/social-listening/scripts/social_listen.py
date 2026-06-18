# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "google-auth>=2.0", "psycopg2-binary>=2.9"]
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

# Conturile IG Business pe care le DEȚINEM (descoperite via business_management; vezi `ig-discover`).
# `ig` = id-ul contului brandului (pt mențiuni-tag „cine ne-a tăguit"). Pt hashtag-search e nevoie
# doar de UN cont IG al nostru ca apelant (IG_CALLER) — nu trebuie să fie chiar al brandului.
IG_ACCOUNTS = {
    "belasil": "17841471596135156", "grandia": "17841474188574596",
    "gt": "17841475742630890", "bonhaus": "17841455560909948",
    "magdeal": "17841476114459735", "nocturna": "17841468835767811",
    "rossi": "17841407896729965",
    # nubra, esteban: conturile IG nu-s atașate la BM-ul accesibil → adaugă-le ca asset în Business
    # Manager (sau dă handle-ul pt business_discovery). Hashtag-search merge oricum (folosește IG_CALLER).
}
IG_CALLER = "17841474188574596"  # @grandia.ro — apelantul implicit pt ig_hashtag_search

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

# ───────────────────────── PROBE 1b: Google News + forumuri (DataForSEO) ─────────────────────────
FORUM_HINTS = ("reddit", "quora", "forum", "tpu.ro", "softpedia", "okazii", "trustpilot",
               "reclamatii", "discuss", "comunitate", "grup", "tiktok", "instagram", "facebook", "youtube")

def _norm_items(items):
    """Aplatizează rezultatele SERP/News: top_stories & discussions_and_forums au sub-items în .items."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        raw = it.get("items")
        subs = raw if (isinstance(raw, list) and raw and all(isinstance(x, dict) for x in raw)) else [it]
        for s in subs:
            if not isinstance(s, dict):
                continue
            out.append({"domain": (s.get("domain") or s.get("source") or ""),
                        "title": (s.get("title") or s.get("snippet") or ""),
                        "ts": (s.get("timestamp") or "")[:10],
                        "type": s.get("type") or it.get("type")})
    return out

def probe_news(brand, conf, days):
    _hr(f"GOOGLE NEWS + FORUMURI — „{conf['name']}\"  (DataForSEO, RO)")
    base = conf["terms"][0]
    ctx = (conf.get("context") or [None])[0]
    nkw = f"{base} {ctx}" if (conf.get("ambiguous") and ctx) else base   # dezambiguizare pt nume generice
    out = {}
    # — Google News —
    try:
        res = _dfs_post("/v3/serp/google/news/live/advanced",
                        [{"keyword": nkw, "location_name": "Romania", "language_name": "Romanian", "depth": 30}])
        items = (res[0].get("items") or []) if res else []
        news = [n for n in _norm_items(items) if n["domain"] and not _owned(n["domain"])]
        print(f"\n  Google News pe „{nkw}\": {len(news)} articole terțe")
        for n in news[:12]:
            print(f"   • [{n['ts'] or '????'}] {n['domain'][:24]:24} {n['title'][:60]}")
        out["news"] = len(news)
    except Exception as e:
        if "no search result" in str(e).lower():
            print(f"\n  Google News pe „{nkw}\": 0 articole (fără știri)")
            out["news"] = 0
        else:
            print(f"  news n/a — {e}")
    # — Forumuri / discuții —
    try:
        fkw = f"{base} (forum OR pareri OR review OR experienta)"
        res = _dfs_post("/v3/serp/google/organic/live/advanced",
                        [{"keyword": fkw, "location_name": "Romania", "language_name": "Romanian", "depth": 30}])
        items = (res[0].get("items") or []) if res else []
        forums = []
        for n in _norm_items(items):
            dom = n["domain"]
            if not dom or _owned(dom):
                continue
            if n["type"] == "discussions_and_forums" or any(h in dom for h in FORUM_HINTS):
                forums.append(n)
        print(f"\n  Forumuri / discuții pe „{base}\": {len(forums)}")
        for n in forums[:12]:
            print(f"   • {n['domain'][:24]:24} {n['title'][:62]}")
        out["forums"] = len(forums)
    except Exception as e:
        print(f"  forumuri n/a — {e}")
    return out or None

# ───────────────────────── PROBE 1c: YouTube (video mentions) ─────────────────────────
def _yt_oauth_token():
    """Access token readonly via refresh (clientul YouTube existent), dacă există un refresh readonly."""
    cid, csec = secret("YOUTUBE_OAUTH_CLIENT_ID"), secret("YOUTUBE_OAUTH_CLIENT_SECRET")
    rt = secret("YOUTUBE_READONLY_REFRESH_TOKEN")
    if not (cid and csec and rt):
        return None
    try:
        r = requests.post("https://oauth2.googleapis.com/token",
                          data={"client_id": cid, "client_secret": csec,
                                "refresh_token": rt, "grant_type": "refresh_token"}, timeout=30)
        return r.json().get("access_token")
    except Exception:
        return None

def probe_youtube(brand, conf, days):
    _hr(f"YOUTUBE — „{conf['name']}\"  (video-uri care ne pomenesc)")
    base = conf["terms"][0]
    ctx = (conf.get("context") or [None])[0]
    q = f"{base} {ctx}" if (conf.get("ambiguous") and ctx) else base
    # cheia dedicată; rezervă: GADS_GOOGLE_API_KEY (merge dacă YouTube Data API v3 e activat pe proiectul ei)
    key = secret("YOUTUBE_API_KEY") or secret("GADS_GOOGLE_API_KEY")
    tok = None if key else _yt_oauth_token()
    if not key and not tok:
        print("  n/a — lipsește YOUTUBE_API_KEY.")
        print("       Activare (2 min, gratis): Google Cloud Console → activează «YouTube Data API v3»")
        print("       → Create credentials → API key →  kb.py secret-set YOUTUBE_API_KEY <key>")
        print("       (alt: re-auth OAuth `youtube.readonly` cu clientul existent → YOUTUBE_READONLY_REFRESH_TOKEN)")
        return None
    params = {"part": "snippet", "q": q, "type": "video", "order": "date", "maxResults": 20,
              "regionCode": "RO", "relevanceLanguage": "ro",
              "publishedAfter": (dt.datetime.utcnow() - dt.timedelta(days=max(days, 30))).strftime("%Y-%m-%dT%H:%M:%SZ")}
    headers = {}
    if key:
        params["key"] = key
    else:
        headers["Authorization"] = f"Bearer {tok}"
    try:
        j = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, headers=headers, timeout=30).json()
        if j.get("error"):
            print(f"  n/a — {j['error'].get('message','')[:130]}")
            return None
        items = j.get("items") or []
        rel = [i for i in items if base.lower() in
               ((i.get("snippet", {}).get("title", "") + " " + i.get("snippet", {}).get("description", "")).lower())]
        show = rel or items
        print(f"  video-uri pe „{q}\": {len(items)}  (relevante pe nume: {len(rel)})")
        for i in show[:12]:
            s = i.get("snippet", {})
            print(f"   • [{s.get('publishedAt','')[:10]}] {s.get('channelTitle','')[:22]:22} {s.get('title','')[:52]}")
        return {"videos": len(items), "relevant": len(rel)}
    except Exception as e:
        print(f"  n/a — {e}")
        return None

# ───────────────────────── PROBE 2: Reddit (OAuth) ─────────────────────────
def probe_reddit(brand, conf, days):
    _hr(f"REDDIT — „{conf['name']}\"  (ultimele ~{days} zile)")
    term = conf["terms"][0]
    q = f'"{term}"'
    if conf.get("ambiguous") and conf.get("context"):
        q = f'"{term}" ({" OR ".join(conf["context"])})'
    cid, csec = secret("REDDIT_CLIENT_ID"), secret("REDDIT_CLIENT_SECRET")
    ua = "arona-social-listening/1.0 (by u/arona)"
    children = None
    if cid and csec:
        try:
            tr = requests.post("https://www.reddit.com/api/v1/access_token",
                               auth=(cid, csec), data={"grant_type": "client_credentials"},
                               headers={"User-Agent": ua}, timeout=30)
            tk = tr.json().get("access_token")
            if tk:
                r = requests.get("https://oauth.reddit.com/search",
                                 params={"q": q, "sort": "new", "limit": 25, "t": "year"},
                                 headers={"User-Agent": ua, "Authorization": f"bearer {tk}"}, timeout=30)
                if r.status_code == 200:
                    children = (r.json().get("data") or {}).get("children") or []
                else:
                    print(f"  (Reddit search HTTP {r.status_code})")
            else:
                print(f"  (token Reddit eșuat: {str(tr.json())[:80]})")
        except Exception as e:
            print(f"  (OAuth Reddit eșuat: {e})")
    else:
        print("  n/a — lipsesc REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET (JSON-ul public e blocat 403).")
        print("       Activare (2 min, gratis): reddit.com/prefs/apps → Create app → tip «script» →")
        print("       kb.py secret-set REDDIT_CLIENT_ID <id>  ;  kb.py secret-set REDDIT_CLIENT_SECRET <secret>")
        return None
    try:
        if children is None:
            print("  n/a — Reddit a respins cererea (verifică REDDIT_CLIENT_ID/SECRET).")
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

# ───────────────────────── PROBE 4: Instagram (hashtag + cine ne taghează) ─────────────────────────
IG_VER = "v21.0"
IG_APP = "arona.ro (app_id 35528853310046610)"

def _ig_get(path, params, tok):
    p = dict(params); p["access_token"] = tok
    return requests.get(f"https://graph.facebook.com/{IG_VER}/{path}", params=p, timeout=30).json()

def probe_instagram(brand, conf, days):
    _hr(f"INSTAGRAM — „{conf['name']}\"  (hashtag + cine ne-a tăguit, Graph API)")
    tok = secret("IG_GRAPH_TOKEN")
    key = brand.lower().strip()
    own_ig = IG_ACCOUNTS.get(key) or secret("IG_BUSINESS_ID")
    caller = own_ig or IG_CALLER
    if not tok:
        print("  n/a — lipsește IG_GRAPH_TOKEN (token cu scope instagram_basic).")
        print(f"       Tokenurile actuale sunt ADS-ONLY (App „{IG_APP}\", scope ads_read/business_management).")
        print("       Activare (fără App Review, ești admin pe conturi):")
        print(f"         1. App «arona.ro» → adaugă produsul Instagram Graph API + conturile IG ca assets")
        print("         2. generează token cu: instagram_basic, instagram_manage_insights, pages_show_list,")
        print("            pages_read_engagement (+ instagram_manage_comments pt tag-uri)")
        print("         3. kb.py secret-set IG_GRAPH_TOKEN <token>   (apoi acest probe pornește singur)")
        if own_ig:
            print(f"       Contul IG al brandului e deja știut: id {own_ig}.")
        return None
    tag = conf["terms"][0].replace(" ", "").replace(".", "").replace("'", "")
    out = {}
    # 1) hashtag — postări publice care folosesc #brand (apelant = un cont IG al nostru)
    try:
        s = _ig_get("ig_hashtag_search", {"user_id": caller, "q": tag}, tok)
        if s.get("error"):
            raise RuntimeError(s["error"].get("message", "")[:100])
        hid = ((s.get("data") or [{}])[0] or {}).get("id")
        if hid:
            m = _ig_get(f"{hid}/recent_media",
                        {"user_id": caller, "limit": 25,
                         "fields": "caption,permalink,timestamp,like_count,comments_count"}, tok)
            items = m.get("data") or []
            print(f"\n  #{tag} · postări recente: {len(items)}")
            for it in items[:12]:
                cap = (it.get("caption") or "").replace("\n", " ")[:78]
                print(f"   • [{(it.get('timestamp') or '')[:10]}] ❤{it.get('like_count',0):<4} 💬{it.get('comments_count',0):<3} {cap}")
            out["hashtag_media"] = len(items)
        else:
            print(f"  #{tag}: fără date / hashtag negăsit.")
    except Exception as e:
        print(f"  hashtag n/a — {e}")
    # 2) cine ne-a tăguit (@brand) — postări de influenceri/UGC care ne menționează direct
    if own_ig:
        try:
            m = _ig_get(f"{own_ig}/tags",
                        {"limit": 25, "fields": "username,caption,permalink,timestamp,like_count,comments_count"}, tok)
            if m.get("error"):
                raise RuntimeError(m["error"].get("message", "")[:100])
            items = m.get("data") or []
            print(f"\n  cine ne-a tăguit @{key}: {len(items)} postări")
            for it in items[:12]:
                cap = (it.get("caption") or "").replace("\n", " ")[:60]
                print(f"   • @{(it.get('username') or '?')[:20]:20} ❤{it.get('like_count',0):<4} {cap}")
            out["tags"] = len(items)
        except Exception as e:
            print(f"  tag-uri n/a — {e}")
    else:
        print(f"\n  (cont IG al brandului „{key}\" necunoscut → tag-urile @ nu se pot citi; "
              f"adaugă-l în IG_ACCOUNTS sau ca asset în BM)")
    return out or None

def cmd_ig_discover(args):
    """Inventariază conturile IG pe care le DEȚINEM, via tokenul de ads (business_management) din DB.
    Rulează ACUM (nu cere instagram_basic) — îți dă id-urile pt IG_ACCOUNTS."""
    dsn = secret("DATABASE_URL_METRICS")
    if not dsn:
        sys.exit("lipsește DATABASE_URL_METRICS (kb.py secret-get DATABASE_URL_METRICS)")
    try:
        import psycopg2
    except ImportError:
        sys.exit("psycopg2 indisponibil")
    dsn = dsn.replace("postgresql+psycopg2", "postgresql").split("?")[0]
    cur = psycopg2.connect(dsn).cursor()
    cur.execute('SELECT "accessToken" FROM meta_access_tokens WHERE "isActive" IS NOT FALSE')
    seen = {}
    for (tok,) in cur.fetchall():
        bj = _ig_get("me/businesses", {"fields": "name,id", "limit": 50}, tok)
        for b in (bj.get("data") or []):
            j = _ig_get(f"{b['id']}/owned_instagram_accounts", {"fields": "username,id,followers_count"}, tok)
            for ig in (j.get("data") or []):
                seen[ig["id"]] = (ig.get("username"), ig.get("followers_count"), b.get("name"))
    print(f"Conturi IG deținute (via business_management): {len(seen)}")
    for igid, (u, f, biz) in sorted(seen.items(), key=lambda kv: -(kv[1][1] or 0)):
        print(f"  @{(u or '?'):24} id={igid}  {f or '?':>7}f   [BM: {biz}]")
    print("\nPune id-urile relevante în IG_ACCOUNTS (în acest script).")

# ───────────────────────── main ─────────────────────────
PROBES = {"mentions": probe_mentions, "news": probe_news, "youtube": probe_youtube,
          "reddit": probe_reddit, "gsc": probe_gsc, "instagram": probe_instagram}

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
    nw = summary.get("news") or {}
    if nw.get("news") is not None or nw.get("forums") is not None:
        bits.append(f"{nw.get('news',0)} articole News + {nw.get('forums',0)} fire pe forumuri")
    yt = summary.get("youtube") or {}
    if yt.get("relevant"):
        bits.append(f"{yt['relevant']} video-uri YouTube relevante")
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
    sub.add_parser("ig-discover", help="inventariază conturile IG deținute (via token ads, fără instagram_basic)").set_defaults(fn=cmd_ig_discover)
    args = ap.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
