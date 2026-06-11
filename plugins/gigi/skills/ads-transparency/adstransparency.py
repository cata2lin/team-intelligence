# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
adstransparency.py — competitive intel from Google Ads Transparency Center.

Hits the Transparency Center's internal RPC (no auth, no browser) to list the
Google ads any advertiser runs, filtered by DOMAIN and REGION. Returns: advertiser
legal entity, number of active creatives, format/content breakdown, first/last
shown dates, and sample creatives (image URLs, text, video/HTML creative IDs).

Usage:
  uv run adstransparency.py rasheed.ro
  uv run adstransparency.py rasheed.ro evero.ro parfumat.ro --region 2642
  uv run adstransparency.py esteban.ro --format json --limit 100 --samples 6

Region codes (the Transparency Center's internal geo anchor):
  2642 = RO (default). For another country, open adstransparency.google.com for
  that region once and read the number in the SearchCreatives request payload.
"""
from __future__ import annotations
import argparse, json, re, sys
from datetime import datetime, timezone
import requests

RPC = "https://adstransparency.google.com/anji/_/rpc/SearchService/SearchCreatives?authuser="
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

def query_domain(domain: str, region: int, limit: int) -> list[dict]:
    freq = {"2": limit, "3": {"8": [region], "12": {"1": domain, "2": True}},
            "7": {"1": 1, "2": 0, "3": region}}
    r = requests.post(RPC, headers={"content-type": "application/x-www-form-urlencoded",
                                    "x-same-domain": "1", "user-agent": UA},
                      data={"f.req": json.dumps(freq)}, timeout=40)
    if r.status_code != 200:
        sys.exit(f"RPC {r.status_code}: {r.text[:200]}")
    try:
        return (r.json() or {}).get("1", []) or []
    except Exception:
        sys.exit("Could not parse RPC response (rate-limited or format change?).")

def _iso(unix) -> str | None:
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
            acc.append(s[:120])
    elif isinstance(o, list):
        for x in o: _collect_text(x, acc, depth + 1)
    elif isinstance(o, dict):
        for v in o.values(): _collect_text(v, acc, depth + 1)

def summarize(domain: str, arr: list[dict], limit: int, n_samples: int) -> dict:
    advertisers, formats, ctypes = {}, {}, {"image": 0, "html_video": 0, "text_other": 0}
    first = last = None
    samples = []
    for c in arr:
        adv = c.get("12") or "?"
        advertisers[adv] = advertisers.get(adv, 0) + 1
        f = c.get("4"); formats[f] = formats.get(f, 0) + 1
        fs = (c.get("6") or {}).get("1"); ls = (c.get("7") or {}).get("1")
        if fs and (first is None or int(fs) < int(first)): first = fs
        if ls and (last is None or int(ls) > int(last)): last = ls
        cc = c.get("3") or {}
        if isinstance(cc.get("3"), dict) and cc["3"].get("2"):
            ctypes["image"] += 1
            if len(samples) < n_samples:
                m = re.search(r'src="([^"]+)"', cc["3"]["2"]); dim = re.search(r'height="(\d+)" width="(\d+)"', cc["3"]["2"])
                v = (m.group(1) if m else "")
                if dim: v += f" [{dim.group(2)}x{dim.group(1)}]"
                samples.append({"kind": "image", "value": v})
        elif isinstance(cc.get("1"), dict) and cc["1"].get("4"):
            ctypes["html_video"] += 1
            if len(samples) < n_samples:
                m = re.search(r"creativeId%3D(\d+)|creativeId=(\d+)", cc["1"]["4"])
                samples.append({"kind": "html/video", "value": "creativeId " + ((m.group(1) or m.group(2)) if m else "?")})
        else:
            ctypes["text_other"] += 1
            if len(samples) < n_samples:
                acc = []; _collect_text(cc, acc)
                if acc: samples.append({"kind": "text", "value": " | ".join(acc[:4])})
    return {"domain": domain, "returned": len(arr), "capped": len(arr) >= limit,
            "advertisers": advertisers, "content_types": ctypes, "format_codes": formats,
            "first_seen": _iso(first), "last_seen": _iso(last), "samples": samples}

def print_summary(s: dict):
    runs = s["returned"] > 0
    print(f"\n● {s['domain']} — {'NU rulează (0 anunțuri)' if not runs else ('rulează: ' + str(s['returned']) + ('+' if s['capped'] else '') + ' creative')}")
    if not runs: return
    advs = ", ".join(f"{k} ({v})" for k, v in sorted(s["advertisers"].items(), key=lambda x: -x[1]))
    print(f"   advertiser: {advs}")
    ct = s["content_types"]
    print(f"   conținut: imagine {ct['image']} · html/video {ct['html_video']} · text/alt {ct['text_other']}  |  format codes {s['format_codes']}")
    print(f"   activ: {s['first_seen']} → {s['last_seen']}")
    for sm in s["samples"]:
        print(f"     - [{sm['kind']}] {sm['value']}")

def main():
    ap = argparse.ArgumentParser(description="Google Ads Transparency Center — competitor ad intel")
    ap.add_argument("domains", nargs="+", help="domenii, ex: rasheed.ro evero.ro")
    ap.add_argument("--region", type=int, default=2642, help="cod regiune (RO=2642)")
    ap.add_argument("--limit", type=int, default=100, help="max creative/domeniu")
    ap.add_argument("--samples", type=int, default=5)
    ap.add_argument("--format", choices=["table", "json"], default="table")
    a = ap.parse_args()
    out = []
    for d in a.domains:
        d = re.sub(r"^https?://", "", d).replace("www.", "").split("/")[0]
        arr = query_domain(d, a.region, a.limit)
        s = summarize(d, arr, a.limit, a.samples)
        out.append(s)
        if a.format == "table": print_summary(s)
    if a.format == "json":
        print(json.dumps(out, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
