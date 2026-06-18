# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
competitor_ads.py — Competitive Creative Intelligence (v1: Google).

Vezi ce reclame rulează un competitor, găsește-i CELE MAI BUNE creative (rank pe
longevitate = cel mai bun proxy public de performanță: brandurile omoară repede ce
nu merge și scalează câștigătorii), ANALIZEAZĂ-le cu un model vision (unghi/hook/
ofertă/format/CTA), COMPARĂ cu ad-urile noastre și primește RECOMANDĂRI de creative.

Sursă v1: Google Ads Transparency Center (RPC intern, fără auth) — reutilizează
logica din `gigi:ads-transparency`. Meta Ad Library + TikTok se adaugă ulterior.
Vision/recomandări via Gemini (KB: GEMINI_API_KEY / GOOGLE_AI_API_KEY).

Usage:
  uv run competitor_ads.py best rasheed.ro --top 10
  uv run competitor_ads.py analyze rasheed.ro --top 6 --vs esteban.ro
  uv run competitor_ads.py best notino.ro evero.ro parfumat.ro     # batch
"""
from __future__ import annotations
import argparse, base64, json, os, re, subprocess, sys
from datetime import datetime, timezone
import requests

RPC = "https://adstransparency.google.com/anji/_/rpc/SearchService/SearchCreatives?authuser="
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
KB_DEFAULT = os.path.expanduser(
    "~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
NOW = datetime.now(tz=timezone.utc)

def secret(key):
    v = os.environ.get(key)
    if v:
        return v
    kb = os.environ.get("KB_PY") or KB_DEFAULT
    if os.path.exists(kb):
        try:
            return subprocess.run(["uv", "run", kb, "secret-get", key],
                                  capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            return ""
    return ""

# ───────────────────────── Google Transparency RPC (derivat din gigi:ads-transparency) ─────────────────────────
def query_domain(domain, region, limit):
    freq = {"2": limit, "3": {"8": [region], "12": {"1": domain, "2": True}},
            "7": {"1": 1, "2": 0, "3": region}}
    r = requests.post(RPC, headers={"content-type": "application/x-www-form-urlencoded",
                                    "x-same-domain": "1", "user-agent": UA},
                      data={"f.req": json.dumps(freq)}, timeout=40)
    if r.status_code != 200:
        raise RuntimeError(f"RPC {r.status_code}: {r.text[:160]}")
    return (r.json() or {}).get("1", []) or []

def _iso(unix):
    try:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None

def _collect_text(o, acc, depth=0):
    if depth > 6 or len(acc) > 8:
        return
    if isinstance(o, str):
        s = o.strip()
        if (len(s) > 3 and re.search(r"[a-zA-ZăâîșțĂÂÎȘȚ]", s)
                and not re.search(r"https?:|content\.js|fletch|googlesyndication|ACiVB|displayads", s)):
            acc.append(s[:140])
    elif isinstance(o, list):
        for x in o: _collect_text(x, acc, depth + 1)
    elif isinstance(o, dict):
        for v in o.values(): _collect_text(v, acc, depth + 1)

def creatives(domain, region, limit):
    """Listă de creative cu longevitate (days active) + URL imagine / text."""
    out = []
    for c in query_domain(domain, region, limit):
        fs = (c.get("6") or {}).get("1"); ls = (c.get("7") or {}).get("1")
        cc = c.get("3") or {}
        kind, img, text, cid = "text", None, None, None
        if isinstance(cc.get("3"), dict) and cc["3"].get("2"):
            kind = "image"
            m = re.search(r'src="([^"]+)"', cc["3"]["2"]); img = m.group(1) if m else None
        elif isinstance(cc.get("1"), dict) and cc["1"].get("4"):
            kind = "html/video"
            m = re.search(r"creativeId%3D(\d+)|creativeId=(\d+)", cc["1"]["4"])
            cid = (m.group(1) or m.group(2)) if m else None
        else:
            acc = []; _collect_text(cc, acc); text = " | ".join(acc[:4]) if acc else None
        days = (int(ls) - int(fs)) // 86400 if (fs and ls) else 0
        active_now = bool(ls and (NOW - datetime.fromtimestamp(int(ls), tz=timezone.utc)).days <= 7)
        out.append({"adv": c.get("12") or "?", "format": c.get("4"), "kind": kind,
                    "first": _iso(fs), "last": _iso(ls), "days": days, "active": active_now,
                    "img": img, "text": text, "cid": cid})
    return out

def rank_best(crs):
    """Cele mai bune = active acum, ordonate pe longevitate (zile), apoi cu imagine întâi."""
    return sorted(crs, key=lambda c: (c["active"], c["days"], c["kind"] == "image"), reverse=True)

# ───────────────────────── Gemini (vision + text) ─────────────────────────
GEMINI_MODELS = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash"]

def _gemini(parts):
    key = secret("GEMINI_API_KEY") or secret("GOOGLE_AI_API_KEY")
    if not key:
        raise RuntimeError("lipsește GEMINI_API_KEY/GOOGLE_AI_API_KEY")
    last = ""
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        r = requests.post(url, json={"contents": [{"parts": parts}]}, timeout=120)
        if r.status_code == 200:
            try:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                last = str(r.json())[:160]
        else:
            last = f"{r.status_code}: {r.text[:120]}"
    raise RuntimeError(f"Gemini eșuat ({last})")

def vision_parts(prompt, image_urls):
    parts = [{"text": prompt}]
    for u in image_urls:
        try:
            b = requests.get(u, timeout=20, headers={"user-agent": UA}).content
            parts.append({"inline_data": {"mime_type": "image/jpeg",
                                          "data": base64.b64encode(b).decode()}})
        except Exception:
            continue
    return parts

# ───────────────────────── commands ─────────────────────────
def _norm(d):
    return re.sub(r"^https?://", "", d).replace("www.", "").split("/")[0]

def cmd_best(a):
    for dom in a.domains:
        dom = _norm(dom)
        try:
            crs = creatives(dom, a.region, a.limit)
        except Exception as e:
            print(f"\n● {dom} — eroare: {e}"); continue
        if not crs:
            print(f"\n● {dom} — NU rulează pe Google (0 creative)"); continue
        best = rank_best(crs)[:a.top]
        adv = max({c["adv"] for c in crs}, key=lambda x: sum(1 for c in crs if c["adv"] == x))
        nact = sum(1 for c in crs if c["active"])
        print(f"\n● {dom} — {len(crs)} creative ({nact} active acum) · advertiser: {adv}")
        print(f"  TOP {len(best)} după longevitate (zile active = proxy de câștigător):")
        for c in best:
            tag = "🟢" if c["active"] else "⚪"
            line = f"  {tag} {c['days']:>4}z [{c['first']}→{c['last']}] {c['kind']:9}"
            if c["img"]: line += f" {c['img']}"
            elif c["text"]: line += f" „{c['text'][:60]}\""
            elif c["cid"]: line += f" creativeId {c['cid']}"
            print(line)

def cmd_analyze(a):
    dom = _norm(a.domain)
    crs = creatives(dom, a.region, a.limit)
    if not crs:
        sys.exit(f"{dom} — NU rulează pe Google (0 creative)")
    best = rank_best(crs)[:a.top]
    imgs = [c["img"] for c in best if c["img"]][:a.top]
    nact = sum(1 for c in crs if c["active"])
    print(f"\n══ ANALIZĂ CREATIVE · {dom} ══")
    print(f"  {len(crs)} creative ({nact} active) · top {len(best)} pe longevitate · {len(imgs)} imagini de analizat")
    # context textual pentru LLM
    ctx = "\n".join(f"- {c['days']}z activ [{c['kind']}] " + (c['text'] or c['img'] or c['cid'] or '')
                    for c in best)
    # 1) analiză vision pe imaginile câștigătoare
    analysis = ""
    if imgs:
        prompt = (f"Ești strateg de performance marketing. Analizează aceste {len(imgs)} reclame "
                  f"care rulează de cel mai mult timp pentru competitorul {dom} (longevitatea = au "
                  f"performat, altfel le-ar fi oprit). Pentru FIECARE imagine: unghiul/mesajul, hook-ul "
                  f"vizual, oferta/promo, formatul, CTA-ul. Apoi PATTERN-URILE comune (ce repetă = ce "
                  f"funcționează). Răspunde scurt, în română, cu bullet-uri.")
        try:
            analysis = _gemini(vision_parts(prompt, imgs))
            print("\n— Ce funcționează la ei (vision pe creativele câștigătoare) —")
            print(analysis)
        except Exception as e:
            print(f"\n  (analiză vision indisponibilă: {e})")
    # 2) comparativ cu ad-urile noastre
    ours_ctx = ""
    if a.vs:
        our = _norm(a.vs)
        ocrs = creatives(our, a.region, a.limit)
        obest = rank_best(ocrs)[:a.top]
        onact = sum(1 for c in ocrs if c["active"])
        print(f"\n— Noi ({our}) — {len(ocrs)} creative ({onact} active), top longevitate:")
        for c in obest[:8]:
            print(f"   {'🟢' if c['active'] else '⚪'} {c['days']:>4}z [{c['kind']}] " + (c['text'] or c['img'] or ''))
        ours_ctx = "\n".join(f"- {c['days']}z [{c['kind']}] " + (c['text'] or c['img'] or '') for c in obest)
    # 3) recomandări
    rec_prompt = (f"Pe baza analizei creativelor câștigătoare ale competitorului {dom}:\n\n{analysis}\n\n"
                  f"Creativele competitorului (top longevitate):\n{ctx}\n\n"
                  + (f"Creativele NOASTRE ({_norm(a.vs)}):\n{ours_ctx}\n\n" if a.vs else "")
                  + "Dă 5 RECOMANDĂRI concrete de creative de testat la noi (unghi + hook + format), "
                  + ("scoțând în evidență GAP-urile față de competitor. " if a.vs else "")
                  + "Scurt, acționabil, în română.")
    try:
        print("\n— Recomandări de creative —")
        print(_gemini([{"text": rec_prompt}]))
    except Exception as e:
        print(f"  (recomandări indisponibile: {e})")

def main():
    ap = argparse.ArgumentParser(description="Competitive Creative Intelligence (Google v1)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("best", help="cele mai bune creative ale unui competitor (rank pe longevitate)")
    b.add_argument("domains", nargs="+")
    b.add_argument("--region", type=int, default=2642); b.add_argument("--limit", type=int, default=100)
    b.add_argument("--top", type=int, default=10); b.set_defaults(fn=cmd_best)
    an = sub.add_parser("analyze", help="best + analiză vision + comparativ + recomandări")
    an.add_argument("domain")
    an.add_argument("--vs", default="", help="domeniul nostru pt comparativ, ex: esteban.ro")
    an.add_argument("--region", type=int, default=2642); an.add_argument("--limit", type=int, default=100)
    an.add_argument("--top", type=int, default=6); an.set_defaults(fn=cmd_analyze)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
