# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""Upload local videos to a brand's YouTube channel (unlisted) and print video IDs.

PER-BRAND: fiecare brand are canalul lui = un refresh token separat în KB
(`YOUTUBE_<BRAND>_REFRESH_TOKEN`). App-ul OAuth e comun (`YOUTUBE_OAUTH_CLIENT_ID/_SECRET`).
Întâi rulează `yt_oauth.py` logat cu contul canalului brandului → salvează tokenul în KB.

Auth din env: YOUTUBE_OAUTH_CLIENT_ID, YOUTUBE_OAUTH_CLIENT_SECRET,
              YOUTUBE_<BRAND>_REFRESH_TOKEN  (BRAND = --brand, uppercase)

  uv run yt_upload.py --brand GT --check
  uv run yt_upload.py --brand GT --dir "/path/to/_George Talent/Ads" --url https://george-talent.ro
  uv run yt_upload.py --brand NUBRA /path/clip.mp4 --title "Nubra - demo" --url https://nubra.ro
"""
import os, sys, argparse, glob
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# default descriere/URL per brand (poți suprascrie cu --url / --desc)
BRANDS = {
    "BELASIL": ("Belasil — produse de curățenie.", "https://belasil.ro"),
    "GT":      ("GT Parfumuri by George Talent — parfumuri premium.", "https://george-talent.ro"),
    "NUBRA":   ("Nubra — parfumuri, miros de lux la preț accesibil.", "https://nubra.ro"),
    "ESTEBAN": ("Maison d'Esteban — experiență de designer accesibilă.", "https://esteban.ro"),
}

def creds(brand):
    env = f"YOUTUBE_{brand}_REFRESH_TOKEN"
    if env not in os.environ:
        sys.exit(f"lipsește {env} în env — rulează yt_oauth.py logat cu canalul {brand} și salvează-l în KB")
    return Credentials(None, refresh_token=os.environ[env],
        client_id=os.environ["YOUTUBE_OAUTH_CLIENT_ID"], client_secret=os.environ["YOUTUBE_OAUTH_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token", scopes=["https://www.googleapis.com/auth/youtube.upload"])

def upload(yt, path, title, desc, privacy="unlisted"):
    body={"snippet":{"title":title[:100],"description":desc,"categoryId":"22"},
          "status":{"privacyStatus":privacy,"selfDeclaredMadeForKids":False}}
    media=MediaFileUpload(path, chunksize=8*1024*1024, resumable=True, mimetype="video/*")
    req=yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp=None
    while resp is None:
        status, resp = req.next_chunk()
    return resp["id"]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--brand", default="BELASIL", help="GT / NUBRA / BELASIL / ESTEBAN … (alege canalul = YOUTUBE_<BRAND>_REFRESH_TOKEN)")
    ap.add_argument("file", nargs="?"); ap.add_argument("--title"); ap.add_argument("--dir")
    ap.add_argument("--url", help="URL în descriere (default per brand)")
    ap.add_argument("--desc", help="descriere completă (suprascrie default-ul)")
    ap.add_argument("--privacy", default="unlisted"); ap.add_argument("--check", action="store_true")
    a=ap.parse_args()
    brand=a.brand.upper()
    ddesc, durl = BRANDS.get(brand, (f"{brand}.", ""))
    desc = a.desc or f"{ddesc} {a.url or durl}".strip()
    c=creds(brand)
    if a.check:
        c.refresh(Request()); print(f"AUTH OK [{brand}] — access token obținut (len {len(c.token)}), scope upload valid."); return
    yt=build("youtube","v3",credentials=c, cache_discovery=False)
    files=[]
    if a.dir:
        for ext in ("*.mp4","*.mov","*.m4v","*.webm"):
            files+=sorted(glob.glob(os.path.join(a.dir, ext)))
    elif a.file:
        files=[a.file]
    else:
        sys.exit("dă un fișier, --dir, sau --check")
    print(f"[{brand}] de urcat: {len(files)} fișiere")
    for f in files:
        title=a.title or os.path.splitext(os.path.basename(f))[0]
        vid=upload(yt,f,title,desc,a.privacy)
        print(f"  ✓ {os.path.basename(f)}  ->  https://youtu.be/{vid}  (id {vid})")

if __name__=="__main__":
    main()
