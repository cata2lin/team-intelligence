# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""Upload local videos to YouTube (unlisted) for the Belasil channel and print video IDs.
Auth from env: YOUTUBE_OAUTH_CLIENT_ID, YOUTUBE_OAUTH_CLIENT_SECRET, YOUTUBE_BELASIL_REFRESH_TOKEN.

  uv run yt_upload.py --check
  uv run yt_upload.py /path/to/clip.mp4 --title "Belasil - demo gel"
  uv run yt_upload.py --dir "/Users/.../Video Belasil"
"""
import os, sys, argparse, glob
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def creds():
    return Credentials(None, refresh_token=os.environ["YOUTUBE_BELASIL_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_OAUTH_CLIENT_ID"], client_secret=os.environ["YOUTUBE_OAUTH_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token", scopes=["https://www.googleapis.com/auth/youtube.upload"])

def upload(yt, path, title, privacy="unlisted"):
    body={"snippet":{"title":title[:100],"description":"Belasil — produse de curățenie. https://belasil.ro","categoryId":"22"},
          "status":{"privacyStatus":privacy,"selfDeclaredMadeForKids":False}}
    media=MediaFileUpload(path, chunksize=8*1024*1024, resumable=True, mimetype="video/*")
    req=yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp=None
    while resp is None:
        status, resp = req.next_chunk()
    return resp["id"]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("file", nargs="?"); ap.add_argument("--title"); ap.add_argument("--dir")
    ap.add_argument("--privacy", default="unlisted"); ap.add_argument("--check", action="store_true")
    a=ap.parse_args()
    c=creds()
    if a.check:
        c.refresh(Request()); print("AUTH OK — access token obținut (len", len(c.token), "), scope upload valid."); return
    yt=build("youtube","v3",credentials=c, cache_discovery=False)
    files=[]
    if a.dir:
        for ext in ("*.mp4","*.mov","*.m4v","*.webm"):
            files+=sorted(glob.glob(os.path.join(a.dir, ext)))
    elif a.file:
        files=[a.file]
    else:
        sys.exit("dă un fișier, --dir, sau --check")
    print(f"de urcat: {len(files)} fișiere")
    for f in files:
        title=a.title or os.path.splitext(os.path.basename(f))[0]
        vid=upload(yt,f,title,a.privacy)
        print(f"  ✓ {os.path.basename(f)}  ->  https://youtu.be/{vid}  (id {vid})")

if __name__=="__main__":
    main()
