# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client","google-auth","requests"]
# ///
"""Download nail photos from Drive for the 7 in-stock colors lacking one, upload to Shopify Files, patch colors.json."""
import io, json, time, csv, subprocess, requests, pathlib
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

HERE = pathlib.Path(__file__).parent
KEY = "/Users/gheorghebeschea/Downloads/Scripturi/google_credentials.json"
creds = service_account.Credentials.from_service_account_file(KEY, scopes=["https://www.googleapis.com/auth/drive"]).with_subject("gheorghe.beschea@overheat.agency")
drive = build("drive", "v3", credentials=creds, cache_discovery=False)

# color -> Drive file id (chosen hi-res swatch). Xmas Tree searched live.
CHOSEN = {
 "Feelin Cozy":     "1ue4JYqSYR9ITzA3Vj3ofMibxTlF9mU0l",
 "Raspberry":       "1s8NahRnciBfR9zs1NuV23OSNC5LP-Gq0",
 "Its Summertime":  "19mQaA-FGPiFihzrnlNL_tCjek8Us833U",
 "P.S. I love you": "1hGnTpDZMH3mRqd3kqMV0Ax62jjrxouFf",
 "Naughty Hero":    "1CSUQDfx2t1vcYL1yMyRVuJw0P8YMBFA-",
}
# find Xmas Tree swatch
q="name contains 'Xmas Tree' and mimeType contains 'image' and trashed=false"
xt=drive.files().list(q=q,fields="files(id,name,imageMediaMetadata(width,height))",pageSize=10,
      includeItemsFromAllDrives=True,supportsAllDrives=True,corpora="allDrives").execute().get("files",[])
xt=[f for f in xt if (f.get("imageMediaMetadata") or {}).get("width",0)>=800]
if xt: CHOSEN["Xmas Tree"]=xt[0]["id"]; print("Xmas Tree ->",xt[0]["name"])
else: print("Xmas Tree: fără swatch clar pe Drive → rămâne borcan")

def dl(fid):
    req=drive.files().get_media(fileId=fid,supportsAllDrives=True)
    buf=io.BytesIO(); d=MediaIoBaseDownload(buf,req)
    done=False
    while not done: _,done=d.next_chunk()
    return buf.getvalue()

# Shopify
def kb(k): return subprocess.run(["uv","run","/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py","secret-get",k],capture_output=True,text=True).stdout
shop=tok=None
for r in csv.reader(kb("SHOPIFY_STORES_CSV").splitlines()):
    if r and r[0]=="ROSSI": shop,tok=r[1],r[2]
H={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}
GQL=f"https://{shop}/admin/api/2026-01/graphql.json"
def gql(q,v=None):
    return requests.post(GQL,headers=H,json={"query":q,"variables":v or {}},timeout=60).json()

def upload(name, data):
    fn=name.lower().replace(" ","-").replace(".","").replace("'","")+".png"
    st=gql("""mutation($i:[StagedUploadInput!]!){stagedUploadsCreate(input:$i){stagedTargets{url resourceUrl parameters{name value}} userErrors{message}}}""",
        {"i":[{"filename":fn,"mimeType":"image/png","resource":"FILE","httpMethod":"POST"}]})
    tgt=st["data"]["stagedUploadsCreate"]["stagedTargets"][0]
    form=[(p["name"],p["value"]) for p in tgt["parameters"]]
    r=requests.post(tgt["url"],data=form,files={"file":(fn,data,"image/png")},timeout=90)
    if r.status_code not in (200,201,204): print("  upload fail",r.status_code,r.text[:150]); return None
    cf=gql("""mutation($f:[FileCreateInput!]!){fileCreate(files:$f){files{... on MediaImage{id image{url}}} userErrors{message}}}""",
        {"f":[{"originalSource":tgt["resourceUrl"],"contentType":"IMAGE"}]})
    errs=cf["data"]["fileCreate"]["userErrors"]
    if errs: print("  fileCreate err",errs); return None
    fid=cf["data"]["fileCreate"]["files"][0]["id"]
    # poll for CDN url
    for _ in range(15):
        d=gql("{node(id:\"%s\"){... on MediaImage{image{url}}}}"%fid)
        u=(((d.get("data") or {}).get("node") or {}).get("image") or {}).get("url")
        if u: return u
        time.sleep(2)
    return None

urls={}
for color,fid in CHOSEN.items():
    try:
        data=dl(fid); u=upload(color,data)
        if u: urls[color]=u; print(f"  ✓ {color}: {u.split('/')[-1][:40]}")
        else: print(f"  ✗ {color}: upload/url fail")
    except Exception as e: print(f"  ✗ {color}: {type(e).__name__} {str(e)[:80]}")

# patch colors.json
cols=json.load(open(HERE/"colors.json"))
patched=0
for c in cols:
    if c["name"] in urls:
        c["img"]=urls[c["name"]]+"&width=150"; c["nail"]=True; patched+=1
json.dump(cols,open(HERE/"colors.json","w"),ensure_ascii=False,indent=0)
print(f"\ncolors.json patched: {patched} culori acum cu poză-unghie din Drive")
