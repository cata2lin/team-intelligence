# /// script
# requires-python = ">=3.10"
# dependencies = ["playwright>=1.40", "requests>=2.31"]
# ///
"""
tiktok_ads.py — reclamele unui competitor din TikTok Ad Library (UE/RO).

TikTok nu are domeniu→ads ca Google; caută pe ADVERTISER. API-ul intern
(library.tiktok.com/api/v1/{suggestion,search}) e semnat (header x-ccl-str generat
de JS-ul paginii) → îl conducem prin Playwright + Chrome-ul de sistem (channel=chrome,
fără download de chromium). Fluxul: nume → biz_id (suggestion) → reclame (search) →
rank pe longevitate (last-first shown).

Necesită Chrome instalat. Prima rulare: `uv run --with playwright playwright install chrome` NU e
nevoie dacă ai Google Chrome (folosim channel="chrome").

Usage:
  uv run tiktok_ads.py best "rasheed" --top 10
  uv run tiktok_ads.py best "RASHEED GenTech LLC" --region RO --json
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime, timezone

def _quote(s):
    import urllib.parse
    return urllib.parse.quote(s)

def fetch(name, region, limit, want=None):
    """Rezolvă numele → biz_id (suggestion), apoi caută reclamele acelui advertiser.
    `want` = nume exact preferat (pt dezambiguizare); altfel ia advertiserul CU reclame."""
    from playwright.sync_api import sync_playwright
    cap = {"sugg": None}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.on("response", lambda r: cap.__setitem__("sugg", _safe_json(r))
              if ("/api/v1/suggestion" in r.url and r.status == 200) else None)
        pg.goto(f"https://library.tiktok.com/ads?region={region}", wait_until="domcontentloaded", timeout=45000)
        box = pg.get_by_role("textbox").first
        box.wait_for(state="visible", timeout=20000); box.click(); box.type(name, delay=60)
        for _ in range(20):
            if cap["sugg"]:
                break
            pg.wait_for_timeout(300)
        advs = (((cap["sugg"] or {}).get("data") or {}).get("adv_names")) or []
        if not advs:
            b.close(); return None, []
        # candidați: exact match întâi, apoi în ordine — căutăm la fiecare până găsim reclame
        order = sorted(advs, key=lambda a: (a["name"].lower() != (want or name).lower()))
        end = int(time.time() * 1000); start = end - 365 * 86400 * 1000
        for cand in order[:4]:
            biz_id, advname = cand["ids"], cand["name"]
            sres = {"d": None}
            sp = b.new_page()   # pagină NOUĂ → load complet → auto-search din URL
            sp.on("response", lambda r, s=sres: s.__setitem__("d", _safe_json(r))
                  if ("/api/v1/search" in r.url and r.status == 200) else None)
            # query_type=2 = filtrare pe ADVERTISER (altfel numele e tratat ca keyword → feed generic)
            url = (f"https://library.tiktok.com/ads?region={region}&start_time={start}&end_time={end}"
                   f"&adv_name={_quote(advname)}&adv_biz_ids={biz_id}&query_type=2&sort_type=last_shown_date,desc")
            sp.goto(url, wait_until="networkidle", timeout=45000)
            for _ in range(20):
                if sres["d"]:
                    break
                sp.wait_for_timeout(300)
            sp.close()
            data = ((sres["d"] or {}).get("data")) or []
            if data:
                b.close(); return advname, data
        b.close(); return order[0]["name"], []

def _safe_json(r):
    try:
        return r.json()
    except Exception:
        return None

def _day(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return "?"

def parse(ads):
    now = datetime.now(tz=timezone.utc)
    out = []
    for a in ads:
        fs, ls = a.get("first_shown_date"), a.get("last_shown_date")
        days = int((int(ls) - int(fs)) / 86400000) if (fs and ls) else 0
        active = bool(ls and (now - datetime.fromtimestamp(int(ls) / 1000, tz=timezone.utc)).days <= 7)
        vids = a.get("videos") or []
        out.append({"id": a.get("id"), "title": (a.get("title") or "").replace("\n", " ")[:90],
                    "first": _day(fs), "last": _day(ls), "days": days, "active": active,
                    "audience": a.get("estimated_audience") or "",
                    "cover": (vids[0].get("cover_img") if vids else (a.get("image_urls") or [None])[0]),
                    "video": (vids[0].get("video_url") if vids else None)})
    return out

def cmd_best(a):
    advname, ads = fetch(a.advertiser, a.region, a.limit)
    if advname is None:
        print(f"\n● {a.advertiser} — niciun advertiser găsit în TikTok Ad Library ({a.region})")
        return
    crs = sorted(parse(ads), key=lambda c: (c["active"], c["days"]), reverse=True)
    if a.json:
        print(json.dumps({"advertiser": advname, "creatives": crs}, ensure_ascii=False, indent=1)); return
    nact = sum(1 for c in crs if c["active"])
    print(f"\n● TikTok · {advname} — {len(crs)} reclame ({nact} active acum)")
    print(f"  TOP {min(a.top, len(crs))} după longevitate:")
    for c in crs[:a.top]:
        tag = "🟢" if c["active"] else "⚪"
        print(f"  {tag} {c['days']:>4}z [{c['first']}→{c['last']}] aud~{c['audience']:<8} „{c['title']}\"")
        if c["cover"]:
            print(f"        {c['cover'][:100]}")

def cmd_analyze(a):
    """best + analiză vision (Gemini, pe thumbnail + copy) + comparativ + recomandări."""
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import competitor_ads as ca   # reutilizează helper-ele Gemini
    advname, ads = fetch(a.advertiser, a.region, a.limit)
    if advname is None or not ads:
        print(f"\n● {a.advertiser} — niciun advertiser cu reclame în TikTok Ad Library"); return
    crs = sorted(parse(ads), key=lambda c: (c["active"], c["days"]), reverse=True)[:a.top]
    covers = [c["cover"] for c in crs if c["cover"]][:a.top]
    titles = [f"- ({c['days']}z) {c['title']}" for c in crs if c["title"]]
    print(f"\n══ ANALIZĂ TIKTOK · {advname} ══  ({len(crs)} top creative, {len(covers)} thumbnail-uri)")
    analysis = ""
    prompt = (f"Ești strateg de performance marketing pe TikTok. Analizează reclamele TikTok ale "
              f"competitorului {advname} care rulează de cel mai mult timp (longevitatea = au performat). "
              f"Ai thumbnail-urile video + copy-urile.\nCOPY-URI:\n" + "\n".join(titles) +
              "\n\nPentru fiecare: unghi/mesaj, hook (primele secunde sugerate de thumbnail+copy), ofertă, "
              "format (UGC/produs/demo). Apoi PATTERN-URILE comune (ce repetă = ce funcționează). Scurt, română, bullet-uri.")
    try:
        analysis = ca._gemini(ca.vision_parts(prompt, covers))
        print("\n— Ce funcționează la ei pe TikTok —\n" + analysis)
    except Exception as e:
        print(f"  (analiză vision indisponibilă: {e})")
    ours_ctx = ""
    if a.vs:
        oadv, oads = fetch(a.vs, a.region, a.limit)
        if oads:
            ocrs = sorted(parse(oads), key=lambda c: (c["active"], c["days"]), reverse=True)[:a.top]
            print(f"\n— Noi ({oadv}) — top {len(ocrs)} pe longevitate:")
            for c in ocrs[:8]:
                print(f"   🟢 {c['days']:>3}z „{c['title'][:64]}\"")
            ours_ctx = "\n".join(f"- ({c['days']}z) {c['title']}" for c in ocrs)
    rec = (f"Pe baza analizei reclamelor TikTok câștigătoare ale {advname}:\n{analysis}\n\n"
           + (f"Reclamele NOASTRE ({a.vs}):\n{ours_ctx}\n\n" if ours_ctx else "")
           + "Dă 5 recomandări concrete de reclame TikTok de testat la noi (unghi + hook în primele 3s + format), "
           + ("evidențiind gap-urile față de competitor. " if ours_ctx else "") + "Scurt, acționabil, română.")
    try:
        print("\n— Recomandări de creative TikTok —\n" + ca._gemini([{"text": rec}]))
    except Exception as e:
        print(f"  (recomandări indisponibile: {e})")

def main():
    ap = argparse.ArgumentParser(description="TikTok Ad Library — reclamele unui competitor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("best", help="reclamele unui advertiser, rank pe longevitate")
    b.add_argument("advertiser", help="numele advertiserului (ex: rasheed)")
    b.add_argument("--region", default="RO"); b.add_argument("--limit", type=int, default=50)
    b.add_argument("--top", type=int, default=10); b.add_argument("--json", action="store_true")
    b.set_defaults(fn=cmd_best)
    an = sub.add_parser("analyze", help="best + vision + comparativ + recomandări")
    an.add_argument("advertiser")
    an.add_argument("--vs", default="", help="advertiserul nostru pt comparativ, ex: nubra")
    an.add_argument("--region", default="RO"); an.add_argument("--limit", type=int, default=50)
    an.add_argument("--top", type=int, default=6); an.set_defaults(fn=cmd_analyze)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
