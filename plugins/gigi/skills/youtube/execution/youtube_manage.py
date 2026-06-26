# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""youtube_manage.py — WRITE-BACK automation pentru canalele brand ARONA.

Capacitatea de "youtube-automation" pe infra NOASTRĂ: NU Composio/Rube, ci OAuth-ul
nostru per-brand din KB (`YOUTUBE_<BRAND>_REFRESH_TOKEN` + `YOUTUBE_OAUTH_CLIENT_ID/_SECRET`).

Acțiuni:
  list    — toate videourile unui canal (id, titlu, privacy, lungime descriere)
  update  — modifică titlu/descriere/tags/vizibilitate pe un video
  apply   — batch dintr-un JSON {video_id:{title,description,tags,privacy}}
  check   — testează auth + DACĂ scope-ul permite videos.update (write)

  uv run youtube_manage.py check  --brand OFERTELE
  uv run youtube_manage.py list   --brand OFERTELE --json
  uv run youtube_manage.py update --brand OFERTELE --video ABC --title "..." --desc "..." --privacy public
  uv run youtube_manage.py apply  --brand OFERTELE --plan plan.json --privacy public

Brand = unul din token-urile din KB: BELASIL CARPETTO GENTO GT NUBRA OFERTELE.
videos.update cere scope de MANAGEMENT (youtube / youtube.force-ssl); `check` îți spune
dacă token-ul curent (creat poate doar cu upload+readonly) îl are sau e nevoie de re-auth.
"""
import os, sys, json, argparse, subprocess
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/youtube.readonly"]

def _kb(key):
    v = os.environ.get(key)
    if v: return v
    here = Path(__file__).resolve()
    kb = here.parents[4] / "core" / "scripts" / "kb.py"
    if kb.exists():
        out = subprocess.run(["uv", "run", str(kb), "secret-get", key],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        if out: return out
    sys.exit(f"lipsește secretul {key}")

def creds(brand):
    rt = _kb(f"YOUTUBE_{brand}_REFRESH_TOKEN")
    return Credentials(None, refresh_token=rt,
        client_id=_kb("YOUTUBE_OAUTH_CLIENT_ID"), client_secret=_kb("YOUTUBE_OAUTH_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token")  # scopes=None → folosește cele acordate

def yt(brand):
    c = creds(brand); c.refresh(Request())
    return build("youtube", "v3", credentials=c, cache_discovery=False), c

def list_videos(svc):
    ch = svc.channels().list(part="contentDetails,snippet", mine=True).execute()
    if not ch.get("items"): return None, []
    chan = ch["items"][0]
    up = chan["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        r = svc.playlistItems().list(part="contentDetails", playlistId=up, maxResults=50, pageToken=page).execute()
        ids += [it["contentDetails"]["videoId"] for it in r.get("items", [])]
        page = r.get("nextPageToken")
        if not page: break
    vids = []
    for i in range(0, len(ids), 50):
        r = svc.videos().list(part="snippet,status,statistics", id=",".join(ids[i:i+50])).execute()
        for v in r.get("items", []):
            sn, st = v["snippet"], v["status"]
            vids.append({"id": v["id"], "title": sn.get("title", ""),
                         "description": sn.get("description", ""), "tags": sn.get("tags", []),
                         "categoryId": sn.get("categoryId", "22"), "privacy": st.get("privacyStatus"),
                         "views": int(v.get("statistics", {}).get("viewCount", 0))})
    return chan["snippet"]["title"], vids

def update_video(svc, video_id, title=None, description=None, tags=None, privacy=None, category=None):
    cur = svc.videos().list(part="snippet,status", id=video_id).execute()["items"][0]
    sn, st = cur["snippet"], cur["status"]
    if title is not None:       sn["title"] = title[:100]
    if description is not None: sn["description"] = description
    if tags is not None:        sn["tags"] = tags
    if category is not None:    sn["categoryId"] = category
    body = {"id": video_id, "snippet": sn}
    parts = "snippet"
    if privacy is not None:
        st["privacyStatus"] = privacy; body["status"] = st; parts = "snippet,status"
    return svc.videos().update(part=parts, body=body).execute()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "update", "apply", "check"])
    ap.add_argument("--brand", required=True)
    ap.add_argument("--video"); ap.add_argument("--title"); ap.add_argument("--desc")
    ap.add_argument("--tags", help="csv"); ap.add_argument("--privacy", choices=["public", "unlisted", "private"])
    ap.add_argument("--category", default=None); ap.add_argument("--plan"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    brand = a.brand.upper()
    svc, c = yt(brand)
    tags = a.tags.split(",") if a.tags else None

    if a.action == "check":
        name, vids = list_videos(svc)
        print(f"AUTH OK [{brand}] canal '{name}' — {len(vids)} videouri (READ ok).")
        if vids:
            t = vids[0]
            try:
                update_video(svc, t["id"], description=t["description"])  # no-op write
                print("WRITE OK — scope de management valid, pot optimiza (update titlu/descriere/vizibilitate).")
            except HttpError as e:
                msg = str(e)
                if "insufficient" in msg.lower() or "scope" in msg.lower() or e.resp.status == 403:
                    print("WRITE BLOCAT — token-ul are doar upload+readonly. Re-auth necesar cu scope 'youtube' (yt_oauth.py).")
                else:
                    print(f"WRITE eroare: {msg[:200]}")
        return

    if a.action == "list":
        name, vids = list_videos(svc)
        if a.json: print(json.dumps({"channel": name, "videos": vids}, ensure_ascii=False))
        else:
            print(f"[{brand}] '{name}' — {len(vids)} videouri:")
            for v in vids: print(f"  {v['id']} | {v['privacy']:8} | {v['views']:>5} v | {v['title'][:60]}")
        return

    if a.action == "update":
        r = update_video(svc, a.video, a.title, a.desc, tags, a.privacy, a.category)
        print(f"✓ {a.video} actualizat → {r['snippet']['title'][:60]} | {r.get('status',{}).get('privacyStatus','')}")
        return

    if a.action == "apply":
        plan = json.load(open(a.plan))
        ok = 0
        for vid, meta in plan.items():
            try:
                update_video(svc, vid, meta.get("title"), meta.get("description"),
                             meta.get("tags"), a.privacy or meta.get("privacy"), meta.get("category"))
                ok += 1; print(f"  ✓ {vid} → {(meta.get('title') or '')[:55]}")
            except HttpError as e:
                print(f"  ✗ {vid}: {str(e)[:160]}")
        print(f"\n{ok}/{len(plan)} actualizate [{brand}]")

if __name__ == "__main__":
    main()
