# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.100","google-auth>=2.23","google-genai>=0.3","requests>=2.31","psycopg2-binary>=2.9"]
# ///
"""HA deals pipeline: for each deals store, find HA-#### SKUs that are ACTIVE + IN STOCK
in Shopify (user rule: verify in store every time), match to the HA Drive library's
'CREATIVE DENISA' reels, vet, and append to queue for posting to that deals brand.

Usage: uv run pick_ha_deals.py            # all deals stores
       uv run pick_ha_deals.py "Ofertele Zilei"
"""
import subprocess, json, sys, os, io, re, time, csv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
import requests

QDIR=os.path.dirname(os.path.abspath(__file__))
SP_DIR="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/social-post"
sys.path.insert(0,SP_DIR); import social_post as sp
sys.path.insert(0,QDIR); import vetting_store as vs
KB="/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
HA_FOLDERS=["1CdUfqKisb22urOr8seDxik4wvEAXJQLw","1z8kFoaV6NFcuR-THt_S5jqVGpcuauuvR"]

def secret(k): return subprocess.run(["/bin/zsh","-lc",f"uv run '{KB}' secret-get {k}"],capture_output=True,text=True).stdout.strip()

# deals brand (Metricool label) -> Shopify prefix in SHOPIFY_STORES_CSV
STORE={"Ofertele Zilei":"OFER","Magdeal":"MAG","Reduceri bune":"RED","Casa Ofertelor":"BON"}

sa=service_account.Credentials.from_service_account_info(json.loads(secret("GOOGLE_SA_LOOKER_SHEETS_JSON")),
    scopes=["https://www.googleapis.com/auth/drive.readonly"]).with_subject("gheorghe.beschea@overheat.agency")
DRV=build("drive","v3",credentials=sa,cache_discovery=False)
GEM=genai.Client(api_key=secret("GEMINI_API_KEY"))
CSV=list(csv.reader(secret("SHOPIFY_STORES_CSV").splitlines()))
SHOP={r[0]:(r[1],r[2]) for r in CSV[1:] if len(r)>=3}

def drv_ls(fid):
    items=[];tok=None
    while True:
        r=DRV.files().list(q=f"'{fid}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,videoMediaMetadata(durationMillis))",
            pageSize=1000,includeItemsFromAllDrives=True,supportsAllDrives=True,pageToken=tok).execute()
        items+=r.get("files",[]);tok=r.get("nextPageToken")
        if not tok:break
    return items

def ha_drive_map():
    """{HA-#### : folder_id} across both HA drives (top-level product folders)."""
    m={}
    for fid in HA_FOLDERS:
        for x in drv_ls(fid):
            if x["mimeType"].endswith(".folder"):
                mm=re.match(r"(HA-?\d{3,4})",x["name"].upper().replace(" ",""))
                if mm: m.setdefault(mm.group(1).replace("HA","HA-").replace("HA--","HA-"),x["id"])
    return m

def active_ha_skus(prefix):
    dom,tok=SHOP.get(prefix,(None,None))
    if not tok: return set()
    q="""{products(first:250,query:"status:active"){edges{node{variants(first:10){edges{node{sku inventoryQuantity}}}}}}}"""
    r=requests.post(f"https://{dom}/admin/api/2024-10/graphql.json",
                    headers={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"},
                    json={"query":q},timeout=40)
    out=set()
    for e in r.json().get("data",{}).get("products",{}).get("edges",[]):
        for v in e["node"]["variants"]["edges"]:
            sku=(v["node"].get("sku") or "").upper().replace(" ","")
            qty=v["node"].get("inventoryQuantity") or 0
            m=re.match(r"(HA-?\d{3,4})",sku)
            if m and qty>0:
                out.add(m.group(1).replace("HA","HA-").replace("HA--","HA-"))
    return out

def reels_in(folder_id):
    for x in drv_ls(folder_id):
        if x["mimeType"].endswith(".folder") and "DENISA" in x["name"].upper():
            vs=[y for y in drv_ls(x["id"]) if "video" in y["mimeType"]]
            return vs
    return []

def download(fid,path):
    req=DRV.files().get_media(fileId=fid,supportsAllDrives=True)
    with open(path,"wb") as fh:
        dl=MediaIoBaseDownload(fh,req,chunksize=8*1024*1024); done=False
        while not done: _,done=dl.next_chunk()

PROMPT="""Esti editor social media pentru un magazin de DEALS (produse practice pentru casa la pret bun). Uita-te la ACEST videoclip. Raspunde DOAR cu JSON:
{{"continut":"ce produs se vede","calitate":"buna"|"medie"|"slaba","ok_de_postat":bool (calitate buna, FARA watermark alt magazin/TikTok/logo competitor),"caption":"caption RO scurt, orientat pe beneficiu+oferta, cu hook si CTA (max 300c)","hashtags":["#h1","#h2","#h3"]}}"""

def vet(path):
    for a in range(3):
        try:
            f=GEM.files.upload(file=path)
            while f.state.name=="PROCESSING": time.sleep(3); f=GEM.files.get(name=f.name)
            if f.state.name!="ACTIVE": return None
            r=GEM.models.generate_content(model="gemini-2.5-flash",contents=[f,PROMPT])
            try: GEM.files.delete(name=f.name)
            except: pass
            m=re.search(r"\{.*\}",r.text,re.S); return json.loads(m.group(0)) if m else None
        except Exception as e:
            if "429" in str(e): time.sleep(15*(a+1)); continue
            print("   vet err",str(e)[:90]); return None

def main():
    args=sys.argv[1:]; per=2
    if "--per" in args:
        pi=args.index("--per"); per=int(args[pi+1]); args=args[:pi]+args[pi+2:]
    brands=[a for a in args if not a.startswith("--")] or list(STORE)
    print("scanez biblioteca HA din Drive...",flush=True)
    hamap=ha_drive_map(); print(f"  {len(hamap)} produse HA in Drive",flush=True)
    q=json.load(open(f"{QDIR}/queue.json"))
    reg=json.load(open(f"{QDIR}/posted_registry.json"))
    posted_srcs={p.get("src") for p in reg["posted"]}
    for brand in brands:
        skus=active_ha_skus(STORE[brand])
        avail=[s for s in skus if s in hamap]
        print(f"\n[{brand}] active+stoc HA: {len(skus)}  cu reel in Drive: {len(avail)}",flush=True)
        kept=q["brands"].setdefault(brand,[])
        existing={r.get("src") for r in kept}
        tmp=f"/tmp/_ha_{brand.replace(' ','_')}.mp4"; n=0
        for sku in sorted(avail):
            if n>=per: break
            for v in reels_in(hamap[sku])[:2]:
                if n>=per: break
                name=f"{sku}/{v['name']}"; ref=v["id"]
                if name in existing or name in posted_srcs: continue
                cache=vs.cached(ref)                       # already scanned+understood?
                if cache is not None:
                    if cache.get("ok_de_postat") and cache.get("blob_url"):
                        full=cache.get("_caption_full") or (cache.get("caption","")+("\n\n"+" ".join(cache.get("hashtags",[])) if cache.get("hashtags") else "")).strip()
                        kept.append({"url":cache["blob_url"],"caption":full,"dur":None,"src":name,"sku":sku,"posted":False,"posted_at":None})
                        print(f"   ✅ {name[:34]} (cache)",flush=True); n+=1
                    else: print(f"   ❌ {name[:34]} (cache)",flush=True)
                    continue
                try: download(v["id"],tmp)
                except Exception as e: print(f"   dl fail {name}:{str(e)[:60]}"); continue
                an=vet(tmp)
                if not an: continue
                ok=an.get("ok_de_postat"); blob=sp.blob_upload(tmp) if ok else None
                if ok:
                    cap=an.get("caption","").strip(); tags=" ".join(an.get("hashtags",[]))
                    full=(cap+("\n\n"+tags if tags else "")).strip(); an["_caption_full"]=full
                    kept.append({"url":blob,"caption":full,"dur":None,"src":name,"sku":sku,"posted":False,"posted_at":None})
                    print(f"   ✅ {name[:34]}  (SKU {sku} pe stoc)",flush=True); n+=1
                else: print(f"   ❌ {name[:34]}",flush=True)
                vs.save(ref, brand, name, None, an, blob, "ok" if ok else "rejected")  # save EVERY scan
        if brand not in q["rotation"]: q["rotation"].append(brand)
        print(f"[{brand}] adaugat {n} reels",flush=True)
    json.dump(q,open(f"{QDIR}/queue.json","w"),ensure_ascii=False,indent=1)
    print("\ngata HA deals.",flush=True)

if __name__=="__main__": main()
