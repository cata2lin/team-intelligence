# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""tiktok_organic.py — analitică pe postările ORGANICE TikTok ale unui brand prin
TikTok Display API (v2 /video/list/). Vezi ce conținut organic funcționează (views/like/
comment/share per video) — completează gigi:tiktok-ads (care e DOAR plătit).

⚠️ SCOPE: cere `video.list` (+ `user.info.basic`). Tokenurile noastre din
`metrics.tiktok_access_tokens` sunt ADS/Business API → NU au video.list. Nevoie de app
TikTok for Developers + OAuth per brand, salvat în KB ca `TIKTOK_CONTENT_<BRAND>_TOKEN`
(același token ca tiktok_post.py dacă OAuth-ul cere ambele scope-uri).

  uv run tiktok_organic.py --brand NUBRA            # ultimele postări + statistici
  uv run tiktok_organic.py --brand NUBRA --top 20
"""
import os, sys, json, argparse, subprocess
import requests
BASE = "https://open.tiktokapis.com/v2"
def kb(k):
    v = os.environ.get(k)
    if v: return v
    kbpy = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
    out = subprocess.run(["uv", "run", kbpy, "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()
    if not out: sys.exit(f"lipsește {k} (OAuth content/display per brand — vezi references/posting-analytics.md)")
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True); ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    tok = kb(f"TIKTOK_CONTENT_{a.brand.upper()}_TOKEN")
    H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    fields = "id,title,video_description,view_count,like_count,comment_count,share_count,create_time"
    vids, cursor = [], None
    for _ in range(10):
        body = {"max_count": 20}
        if cursor: body["cursor"] = cursor
        r = requests.post(f"{BASE}/video/list/?fields={fields}", headers=H, json=body, timeout=40)
        if r.status_code != 200:
            print(f"FAIL {r.status_code}: {json.dumps(r.json())[:300]}\n→ tokenul nu are video.list? (re-auth necesar)"); return
        d = r.json().get("data", {})
        vids += d.get("videos", [])
        if not d.get("has_more") or len(vids) >= a.top: break
        cursor = d.get("cursor")
    vids = sorted(vids, key=lambda v: v.get("view_count", 0), reverse=True)[:a.top]
    print(f"\nTikTok organic [{a.brand}] — top {len(vids)} după views:\n")
    print(f"{'views':>8} {'likes':>7} {'cmt':>5} {'share':>6}  titlu")
    for v in vids:
        t = (v.get("title") or v.get("video_description") or "")[:50]
        print(f"{v.get('view_count',0):>8} {v.get('like_count',0):>7} {v.get('comment_count',0):>5} {v.get('share_count',0):>6}  {t}")
    if vids:
        tot = sum(v.get("view_count",0) for v in vids); eng = sum(v.get("like_count",0)+v.get("comment_count",0)+v.get("share_count",0) for v in vids)
        print(f"\nTotal {tot:,} views | engagement rate ~{100*eng/max(tot,1):.1f}%")

if __name__ == "__main__":
    main()
