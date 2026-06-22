# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
meta_ads.py — reclamele unui competitor din META Ad Library (Facebook/Instagram), API OFICIAL.

Folosește `ads_archive` (Graph API) cu un token de cont confirmat (KB: META_ADLIB_TOKEN — trebuie
USER token de pe cont care a făcut facebook.com/ID; app/system tokens NU merg, dau 2332002).
Caută reclamele unei pagini/brand în UE (default RO), le ordonează pe LONGEVITATE (zile active =
proxy de câștigător) și — cu analyze — analizează copy-ul cu Gemini + recomandări.

Usage:
  KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
  export META_ADLIB_TOKEN="$(uv run "$KB" secret-get META_ADLIB_TOKEN)"
  uv run meta_ads.py best "answear" --country RO --top 10
  uv run meta_ads.py analyze "answear" --vs "nubra"
"""
from __future__ import annotations
import argparse, datetime as dt, os, re, subprocess, sys
import requests

G = "https://graph.facebook.com/v21.0"
KB = os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
NOW = dt.datetime.now(dt.timezone.utc)

def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

def search(brand, country, want, token):
    """ads_archive paginat → reclame ale paginilor care se potrivesc cu brand-ul."""
    fields = ("id,page_id,page_name,ad_delivery_start_time,ad_delivery_stop_time,"
              "ad_creative_bodies,ad_creative_link_titles,ad_creative_link_captions,"
              "publisher_platforms,ad_snapshot_url,languages")
    params = {"access_token": token, "ad_type": "ALL", "ad_reached_countries": f'["{country}"]',
              "search_terms": brand, "ad_active_status": "ALL", "limit": 100, "fields": fields}
    url = f"{G}/ads_archive"; out = []
    for _ in range(6):   # până la 600 reclame
        j = requests.get(url, params=params, timeout=60).json()
        if "error" in j:
            raise RuntimeError(j["error"].get("message", "")[:120])
        out += j.get("data", [])
        nxt = (j.get("paging") or {}).get("next")
        if not nxt or len(out) >= 600:
            break
        url, params = nxt, {}
    nb = _norm(brand)
    # reține doar paginile al căror nume se potrivește cu brandul (taie zgomotul ca Auchan/IQads)
    return [a for a in out if nb in _norm(a.get("page_name")) or _norm(a.get("page_name")) in nb]

def _d(s):
    try:
        return dt.datetime.fromisoformat((s or "")[:10]).replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None

def parse(ads):
    out = []
    for a in ads:
        st, sp = _d(a.get("ad_delivery_start_time")), _d(a.get("ad_delivery_stop_time"))
        end = sp or NOW
        days = (end - st).days if st else 0
        active = sp is None or sp > NOW
        body = (a.get("ad_creative_bodies") or a.get("ad_creative_link_titles") or [""])[0]
        out.append({"page": a.get("page_name", "?"), "page_id": a.get("page_id"),
                    "start": a.get("ad_delivery_start_time", "")[:10], "days": max(days, 0), "active": active,
                    "platforms": ",".join((a.get("publisher_platforms") or [])).lower(),
                    "text": (body or "").replace("\n", " ")[:90], "snap": a.get("ad_snapshot_url", "")})
    return sorted(out, key=lambda c: (c["active"], c["days"]), reverse=True)

def _need_token():
    t = secret("META_ADLIB_TOKEN")
    if not t:
        sys.exit("lipsește META_ADLIB_TOKEN (USER token de cont confirmat — facebook.com/ID).")
    return t

def cmd_best(a):
    token = _need_token()
    for brand in a.advertisers:
        try:
            crs = parse(search(brand, a.country, a.top, token))
        except Exception as e:
            print(f"\n● {brand} — eroare: {e}"); continue
        if not crs:
            print(f"\n● {brand} — nicio reclamă în Meta Ad Library ({a.country})"); continue
        pages = sorted({c["page"] for c in crs})
        nact = sum(1 for c in crs if c["active"])
        print(f"\n● Meta · {brand} — {len(crs)} reclame ({nact} active) · pagini: {', '.join(pages[:3])}")
        print(f"  TOP {min(a.top,len(crs))} după longevitate:")
        for c in crs[:a.top]:
            tag = "🟢" if c["active"] else "⚪"
            print(f"  {tag} {c['days']:>4}z [din {c['start']}] {c['platforms'][:18]:18} „{c['text']}\"")

def cmd_analyze(a):
    token = _need_token()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import competitor_ads as ca
    crs = parse(search(a.advertiser, a.country, a.top, token))
    if not crs:
        sys.exit(f"{a.advertiser} — nicio reclamă în Meta Ad Library ({a.country})")
    top = crs[:a.top]
    print(f"\n══ ANALIZĂ META · {a.advertiser} ══  ({len(crs)} reclame, top {len(top)} pe longevitate)")
    copies = "\n".join(f"- ({c['days']}z) {c['text']}" for c in top if c["text"])
    prompt = (f"Ești strateg de performance marketing pe Meta (FB/IG). Acestea sunt reclamele "
              f"competitorului {a.advertiser} care rulează de cel mai mult timp (longevitatea = au "
              f"performat). Copy-uri:\n{copies}\n\nDă: unghiurile/mesajele dominante, hook-urile, ofertele, "
              "formatele, apoi PATTERN-URILE comune (ce repetă = ce funcționează). Scurt, română, bullet-uri.")
    analysis = ""
    try:
        analysis = ca._gemini([{"text": prompt}])
        print("\n— Ce funcționează la ei pe Meta —\n" + analysis)
    except Exception as e:
        print(f"  (analiză indisponibilă: {e})")
    ours = ""
    if a.vs:
        ocrs = parse(search(a.vs, a.country, a.top, token))[:a.top]
        if ocrs:
            print(f"\n— Noi ({a.vs}) — top {len(ocrs)} pe longevitate:")
            for c in ocrs[:8]:
                print(f"   {'🟢' if c['active'] else '⚪'} {c['days']:>3}z „{c['text'][:60]}\"")
            ours = "\n".join(f"- ({c['days']}z) {c['text']}" for c in ocrs)
    rec = (f"Pe baza analizei reclamelor Meta câștigătoare ale {a.advertiser}:\n{analysis}\n\n"
           + (f"Reclamele NOASTRE ({a.vs}):\n{ours}\n\n" if ours else "")
           + "Dă 5 recomandări concrete de reclame Meta de testat la noi (unghi + hook + format), "
           + ("evidențiind gap-urile față de competitor. " if ours else "") + "Scurt, acționabil, română.")
    try:
        print("\n— Recomandări de creative Meta —\n" + ca._gemini([{"text": rec}]))
    except Exception as e:
        print(f"  (recomandări indisponibile: {e})")

def main():
    ap = argparse.ArgumentParser(description="Meta Ad Library — reclamele unui competitor (API oficial)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("best", help="reclamele unui brand, rank pe longevitate")
    b.add_argument("advertisers", nargs="+"); b.add_argument("--country", default="RO")
    b.add_argument("--top", type=int, default=10); b.set_defaults(fn=cmd_best)
    an = sub.add_parser("analyze", help="best + analiză copy (Gemini) + comparativ + recomandări")
    an.add_argument("advertiser"); an.add_argument("--vs", default=""); an.add_argument("--country", default="RO")
    an.add_argument("--top", type=int, default=8); an.set_defaults(fn=cmd_analyze)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
