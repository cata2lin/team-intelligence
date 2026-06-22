# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Attach uploaded YouTube videos to a PMax asset group as VIDEO assets (2 steps; dry-run unless --apply).

PER-BRAND: dă CID-ul contului, asset group-ul și lista de videouri (id YouTube + nume).
Videourile trebuie să fie deja pe canalul brandului (vezi yt_upload.py).

  # din fișier JSON: [["videoId","nume"], ...]
  uv run attach_videos.py --cid 5031005158 --ag customers/5031005158/assetGroups/6724106956 --videos gt_videos.json
  uv run attach_videos.py ... --videos gt_videos.json --apply
  # sau inline: --video ID:Nume (repetabil)
  uv run attach_videos.py --cid 5031005158 --ag .../assetGroups/6724106956 --video abc123:"GT - calitate pret"
"""
import os, sys, json, time, argparse
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v21"
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    if not p.query: return d
    k=[(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(k),p.fragment))

ap=argparse.ArgumentParser()
ap.add_argument("--cid", required=True, help="customer id (fără liniuțe)")
ap.add_argument("--ag", required=True, help="resourceName asset group: customers/<cid>/assetGroups/<id>")
ap.add_argument("--videos", help="fișier JSON: [[\"videoId\",\"nume\"], ...]")
ap.add_argument("--video", action="append", default=[], help="ID:Nume (repetabil)")
ap.add_argument("--apply", action="store_true")
a=ap.parse_args()
CID=a.cid; AG=a.ag; apply=a.apply

VIDEOS=[]
if a.videos:
    VIDEOS+= [tuple(x) for x in json.load(open(a.videos))]
for v in a.video:
    vid,_,nm=v.partition(":"); VIDEOS.append((vid, nm or vid))
if not VIDEOS: sys.exit("dă --videos <fișier.json> sau --video ID:Nume")

cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
def post(service, ops, partial=False):
    body={"operations":ops,"validateOnly":(not apply),"partialFailure":partial}
    return requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/{service}:mutate",headers=H,json=body,timeout=60)

print(f"CID {CID} | AG {AG} | {len(VIDEOS)} videouri | {'APPLY' if apply else 'DRY-RUN'}")
# step 1: create the YouTube video assets
aops=[{"create":{"name":n,"youtubeVideoAsset":{"youtubeVideoId":v}}} for v,n in VIDEOS]
r1=post("assets",aops,partial=True)
print("STEP1 assets:",r1.status_code)
if r1.status_code!=200: print(r1.text[:800]); sys.exit(1)
res=r1.json().get("results",[])
names=[x.get("resourceName") for x in res if x and x.get("resourceName")]
print("  asset resource names:",len(names))
if not apply:
    print("  (dry-run: validateOnly nu întoarce resourceName real; rulează cu --apply)"); sys.exit(0)

# step 2: link assets to the asset group ca YOUTUBE_VIDEO, unul câte unul cu retry.
# (field type-ul corect = YOUTUBE_VIDEO; "VIDEO" dă FIELD_TYPE_INCOMPATIBLE_WITH_ASSET_TYPE.
#  link în batch lovește des CONCURRENT_MODIFICATION pe asset group nou → one-by-one + backoff.)
ok=0
for rn in names:
    for attempt in range(4):
        r2=post("assetGroupAssets",[{"create":{"assetGroup":AG,"asset":rn,"fieldType":"YOUTUBE_VIDEO"}}])
        if r2.status_code==200:
            ok+=1; break
        t=r2.text
        if "CONCURRENT_MODIFICATION" in t and attempt<3:
            time.sleep(1.5); continue
        if "already exists" in t.lower() or "DUPLICATE" in t:
            ok+=1; break
        print("  ⚠️",t[:200]); break
    time.sleep(0.5)
print(f"STEP2 link: {ok}/{len(names)} atașate ca YOUTUBE_VIDEO")
