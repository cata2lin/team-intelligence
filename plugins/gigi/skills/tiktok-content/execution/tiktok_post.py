# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""tiktok_post.py — postează un video pe contul TikTok ORGANIC al unui brand prin
TikTok Content Posting API (v2). NU SaaS plătit (upload-post) — API-ul oficial TikTok.

⚠️ SCOPE: cere `video.publish` (+ `video.upload` pt FILE_UPLOAD). Tokenurile noastre din
`metrics.tiktok_access_tokens` sunt BUSINESS/ADS API (scope-uri numerice) → NU au video.publish.
Trebuie un app TikTok for Developers (Login Kit + Content Posting API) + OAuth per cont brand,
salvat în KB ca `TIKTOK_CONTENT_<BRAND>_TOKEN`. (Ca re-auth-ul de management de la YouTube.)
⚠️ AUDIT: un app NEAUDITAT poate posta DOAR `SELF_ONLY` (privat, vizibil doar ție) — pt postare
publică, app-ul trebuie trecut prin „URL ownership + content review" la TikTok.

  uv run tiktok_post.py --brand NUBRA --video-url https://.../clip.mp4 --title "..." --privacy SELF_ONLY
  uv run tiktok_post.py --brand NUBRA --file /path/clip.mp4 --title "..." --privacy PUBLIC_TO_EVERYONE
  uv run tiktok_post.py --brand NUBRA --check        # doar verifică tokenul/scope-ul
"""
import os, sys, json, time, argparse, subprocess
import requests

BASE = "https://open.tiktokapis.com/v2"
def kb(k):
    v = os.environ.get(k)
    if v: return v
    here = os.path.dirname(os.path.abspath(__file__))
    kbpy = os.path.join(here, "..", "..", "..", "core", "scripts", "kb.py")
    out = subprocess.run(["uv", "run", kbpy, "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()
    if not out: sys.exit(f"lipsește secretul {k} (OAuth content-posting per brand — vezi references/posting-analytics.md)")
    return out

def token(brand): return kb(f"TIKTOK_CONTENT_{brand.upper()}_TOKEN")
def H(tok): return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"}

def creator_info(tok):
    r = requests.post(f"{BASE}/post/publish/creator_info/query/", headers=H(tok), timeout=30)
    return r.status_code, r.json()

def post_pull(tok, url, title, privacy):
    body = {"post_info": {"title": title, "privacy_level": privacy, "disable_comment": False},
            "source_info": {"source": "PULL_FROM_URL", "video_url": url}}
    r = requests.post(f"{BASE}/post/publish/video/init/", headers=H(tok), json=body, timeout=60)
    return r.status_code, r.json()

def post_file(tok, path, title, privacy):
    size = os.path.getsize(path)
    body = {"post_info": {"title": title, "privacy_level": privacy, "disable_comment": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}
    r = requests.post(f"{BASE}/post/publish/video/init/", headers=H(tok), json=body, timeout=60)
    j = r.json()
    if r.status_code != 200 or not j.get("data", {}).get("upload_url"): return r.status_code, j
    up = j["data"]["upload_url"]
    with open(path, "rb") as f: data = f.read()
    requests.put(up, headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size-1}/{size}", "Content-Length": str(size)}, data=data, timeout=300)
    return 200, j

def status(tok, publish_id):
    r = requests.post(f"{BASE}/post/publish/status/fetch/", headers=H(tok), json={"publish_id": publish_id}, timeout=30)
    return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", required=True)
    ap.add_argument("--video-url"); ap.add_argument("--file"); ap.add_argument("--title", default="")
    ap.add_argument("--privacy", default="SELF_ONLY", choices=["SELF_ONLY", "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR"])
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    tok = token(a.brand)
    sc, info = creator_info(tok)
    if sc != 200:
        print(f"AUTH/scope FAIL [{a.brand}] {sc}: {json.dumps(info)[:300]}\n→ tokenul nu are video.publish? (re-auth content-posting necesar)"); return
    print(f"AUTH OK [{a.brand}] creator: {info.get('data',{}).get('creator_username','?')} | max video {info.get('data',{}).get('max_video_post_duration_sec','?')}s")
    if a.check: return
    if a.video_url: sc, j = post_pull(tok, a.video_url, a.title, a.privacy)
    elif a.file:    sc, j = post_file(tok, a.file, a.title, a.privacy)
    else: sys.exit("dă --video-url sau --file")
    pid = j.get("data", {}).get("publish_id")
    if not pid: print(f"INIT FAIL {sc}: {json.dumps(j)[:300]}"); return
    print(f"publish_id: {pid} — aștept procesarea…")
    for _ in range(20):
        time.sleep(5); st = status(tok, pid).get("data", {}).get("status", "?")
        print("  status:", st)
        if st in ("PUBLISH_COMPLETE", "FAILED"): break
    print("Gata." if st == "PUBLISH_COMPLETE" else f"Status final: {st}")

if __name__ == "__main__":
    main()
