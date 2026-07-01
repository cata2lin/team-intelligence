# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.100","google-auth>=2.23","google-genai>=0.3","requests>=2.31"]
# ///
"""Source reels for a brand from its Google Drive CREATIVE folder, Gemini-vet them
(perfume/on-brand, quality, no burned foreign text), caption them, upload to Blob,
append to queue.json. Dedup-aware via posted_registry.json + existing queue srcs.

Usage: uv run pick_drive_brand.py "Nubra" "Lab Noir"   [--per 6]
"""
import os, sys, json, time, subprocess, io, re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai

QDIR = os.path.dirname(os.path.abspath(__file__))
SP_DIR = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/social-post"
sys.path.insert(0, SP_DIR)
import social_post as sp  # blob_upload
KB = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
CRE = "1pjDE3spDnpRuLUtTUzNUPx9XRyPA_gBP"

def secret(k):
    return subprocess.run(["/bin/zsh","-lc",f"uv run '{KB}' secret-get {k}"],capture_output=True,text=True).stdout.strip()

CTX = {
 "Nubra": "Nubra = parfumuri dama/barbati, positioning value-first: 'miros de lux la pret accesibil', 'cel mai mic pret garantat'. Voce prietenoasa, accesibila.",
 "Lab Noir": "Lab Noir = parfumerie artizanala, 'parfumuri cu gust', reinterpretari sofisticate (niciodata dupe ieftine). Voce eleganta, de nisa.",
 "Grandia": "Grandia = produse pentru casa si gradina (home & garden), practice, raport pret-calitate. Voce accesibila, orientata pe beneficiu.",
 "Carpetto": "Carpetto = covoare si textile pentru casa, design interior. Voce calda, orientata pe ambient si confort.",
 "Esteban": "Esteban = parfumuri dama/barbati inspirate de branduri de lux, lux accesibil. Voce eleganta.",
 "George Talent": "GT by George Talent = parfumuri, energie de influencer, 'miroase scump'. Voce tanara, cool.",
 "Gento": "Gento = genti si accesorii dama. Voce fashion, orientata pe stil.",
 "Belasil": "Belasil = produse de curatenie / detergent / lavete. Voce practica, demonstrativa (before-after).",
 "Nocturna": "Nocturna = pijamale si lenjerie de noapte, confort si eleganta. Voce calda, intima.",
}

sa = service_account.Credentials.from_service_account_info(
    json.loads(secret("GOOGLE_SA_LOOKER_SHEETS_JSON")),
    scopes=["https://www.googleapis.com/auth/drive.readonly"]).with_subject("gheorghe.beschea@overheat.agency")
DRV = build("drive","v3",credentials=sa,cache_discovery=False)
GEM = genai.Client(api_key=secret("GEMINI_API_KEY"))

def ls(fid):
    items=[]; tok=None
    while True:
        r=DRV.files().list(q=f"'{fid}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,mimeType,videoMediaMetadata(durationMillis))",
            pageSize=1000,includeItemsFromAllDrives=True,supportsAllDrives=True,pageToken=tok,orderBy="folder,name").execute()
        items+=r.get("files",[]); tok=r.get("nextPageToken")
        if not tok: break
    return items

def brand_folder(name):
    for x in ls(CRE):
        if x["mimeType"].endswith(".folder") and name.upper() in x["name"].upper():
            return x["id"]
    return None

def collect(name):
    """Videos 8-60s, prefer the edited 'CREATIVE' subfolder over raw."""
    root=brand_folder(name)
    if not root: return []
    subs={x["name"].upper():x["id"] for x in ls(root) if x["mimeType"].endswith(".folder")}
    order=[fid for nm,fid in subs.items() if nm=="CREATIVE"] + \
          [fid for nm,fid in subs.items() if nm not in ("CREATIVE","CREATIVE STATICE")]
    vids=[]
    for fid in order:
        for x in ls(fid):
            if "video" in x["mimeType"]:
                d=(x.get("videoMediaMetadata") or {}).get("durationMillis")
                dur=int(d)/1000 if d else None
                if dur and 8<=dur<=60:
                    vids.append({"id":x["id"],"name":x["name"],"dur":round(dur,1)})
    return vids

def download(fid, path):
    req=DRV.files().get_media(fileId=fid, supportsAllDrives=True)
    with open(path,"wb") as fh:
        dl=MediaIoBaseDownload(fh, req, chunksize=8*1024*1024)
        done=False
        while not done: _,done=dl.next_chunk()

PROMPT="""Esti editor social media pentru brandul {b}. Context: {ctx}
Uita-te la ACEST videoclip integral. Raspunde DOAR cu un JSON valid, fara alt text:
{{"text_ars": bool (are text/subtitrari/watermark arse pe el?),
"continut": "descriere scurta a ce se vede",
"calitate": "buna"|"medie"|"slaba",
"pe_brand": bool (e despre parfum / se potriveste brandului?),
"ok_de_postat": bool (gata de postat ca reel organic: calitate buna, vertical/ok, FARA watermark alt brand/TikTok/logo competitor),
"caption": "caption RO scurt in vocea brandului, cu hook in prima fraza + un CTA (max 300 caractere)",
"hashtags": ["#h1","#h2","#h3"]}}"""

def vet(path, brand):
    for attempt in range(4):
        try:
            f=GEM.files.upload(file=path)
            while f.state.name=="PROCESSING":
                time.sleep(3); f=GEM.files.get(name=f.name)
            if f.state.name!="ACTIVE":
                return None
            r=GEM.models.generate_content(model="gemini-2.5-flash",
                contents=[f, PROMPT.format(b=brand, ctx=CTX.get(brand,""))])
            try: GEM.files.delete(name=f.name)
            except Exception: pass
            t=r.text.strip()
            m=re.search(r"\{.*\}", t, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as e:
            if "429" in str(e) or "RESOURCE" in str(e):
                time.sleep(20*(attempt+1)); continue
            print(f"   vet err: {str(e)[:120]}", flush=True); return None
    return None

def main():
    raw=sys.argv[1:]; per=6
    if "--per" in raw:
        pi=raw.index("--per"); per=int(raw[pi+1]); raw=raw[:pi]+raw[pi+2:]
    args=[a for a in raw if not a.startswith("--")]
    q=json.load(open(f"{QDIR}/queue.json"))
    reg=json.load(open(f"{QDIR}/posted_registry.json")) if os.path.exists(f"{QDIR}/posted_registry.json") else {"posted":[]}
    posted_srcs={p.get("src") for p in reg["posted"]}
    for brand in args:
        existing={r.get("src") for r in q["brands"].get(brand,[])}
        cands=[c for c in collect(brand) if c["name"] not in existing and c["name"] not in posted_srcs]
        # prefer 10-35s
        cands.sort(key=lambda c: (not (10<=c["dur"]<=35), c["dur"]))
        print(f"\n[{brand}] {len(cands)} candidati; vetez pana la {per} bune...", flush=True)
        kept=q["brands"].setdefault(brand,[])
        tmp=f"/tmp/_pd_{brand.replace(' ','_')}.mp4"
        n_ok=0
        for c in cands:
            if n_ok>=per: break
            try: download(c["id"], tmp)
            except Exception as e: print(f"   dl fail {c['name']}: {str(e)[:80]}",flush=True); continue
            v=vet(tmp, brand)
            if not v: continue
            # Trust Gemini's ok_de_postat (it excludes FOREIGN watermarks). Burned brand-own
            # text is fine for edited CREATIVE reels — do NOT reject on text_ars here.
            ok = v.get("ok_de_postat") and v.get("pe_brand")
            print(f"   {'✅' if ok else '❌'} {c['name'][:32]} {c['dur']}s cal={v.get('calitate')} text_ars={v.get('text_ars')} ok_post={v.get('ok_de_postat')}",flush=True)
            if not ok: continue
            url=sp.blob_upload(tmp)
            cap=v.get("caption","").strip()
            tags=" ".join(v.get("hashtags",[]))
            kept.append({"url":url,"caption":(cap+("\n\n"+tags if tags else "")).strip(),
                         "dur":c["dur"],"src":c["name"],"posted":False,"posted_at":None})
            n_ok+=1
        if brand not in q["rotation"]: q["rotation"].append(brand)
        print(f"[{brand}] adaugat {n_ok} reels in coada.",flush=True)
    json.dump(q, open(f"{QDIR}/queue.json","w"), ensure_ascii=False, indent=1)
    print("\ngata. coada actualizata.",flush=True)

if __name__=="__main__":
    main()
