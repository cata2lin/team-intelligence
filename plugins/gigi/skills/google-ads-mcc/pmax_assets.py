# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31","pillow>=10"]
# ///
"""Add asset group creative (PL copy + images) to Bonhaus PL PMax + enable. validateOnly unless --apply."""
import os, sys, io, base64, re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
from PIL import Image, ImageDraw, ImageFont

API="v21"
CID="6858257397"
ASSET_GROUP=f"customers/{CID}/assetGroups/6728753344"
CAMPAIGN=f"customers/{CID}/campaigns/24007250520"
APPLY="--apply" in sys.argv
UA={"User-Agent":"Mozilla/5.0"}

_OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
def clean(d):
    p=urlsplit(d); return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _OK]),p.fragment))

# ---- auth (same source as gads.py: metrics google_ads_connections) ----
conn=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); conn.set_session(readonly=True)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true')
    c=dict(cur.fetchone())
conn.close()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":c["cid"],"client_secret":c["csec"],"refresh_token":c["rt"]},timeout=20).json()["access_token"]
HDR={"Authorization":f"Bearer {tok}","developer-token":c["dev"],"login-customer-id":re.sub(r"\D","",c["mcc"]),"Content-Type":"application/json"}

# ---- images ----
def fetch(url):
    return Image.open(io.BytesIO(requests.get(url,headers=UA,timeout=30).content)).convert("RGB")
def canvas(img, W, H):
    c=Image.new("RGB",(W,H),(255,255,255))
    im=img.copy(); im.thumbnail((int(W*0.82),int(H*0.82)), Image.LANCZOS)
    c.paste(im,((W-im.width)//2,(H-im.height)//2)); return c
def b64(img, q=85):
    b=io.BytesIO(); img.save(b,"JPEG",quality=q); return base64.b64encode(b.getvalue()).decode()
def logo_img():
    W=H=1200; c=Image.new("RGB",(W,H),(20,28,44)); d=ImageDraw.Draw(c)
    try: f=ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc",150)
    except Exception: f=ImageFont.load_default()
    t="BONHAUS"; bb=d.textbbox((0,0),t,font=f); d.text(((W-(bb[2]-bb[0]))//2,(H-(bb[3]-bb[1]))//2-20),t,font=f,fill=(255,255,255))
    return c

P1="https://cdn.shopify.com/s/files/1/0917/9342/4727/files/15.png?v=1783284763"
P2="https://cdn.shopify.com/s/files/1/0917/9342/4727/files/gallery_1776282924652.png?v=1783275900"
img1=fetch(P1); img2=fetch(P2)
SUF=sys.argv[sys.argv.index("--suf")+1] if "--suf" in sys.argv else "a"
IMAGES=[  # (name, base64, field_type)
 (f"PL_mkt_1{SUF}", b64(canvas(img1,1200,628)), "MARKETING_IMAGE"),
 (f"PL_mkt_2{SUF}", b64(canvas(img2,1200,628)), "MARKETING_IMAGE"),
 (f"PL_sq_1{SUF}",  b64(canvas(img1,1200,1200)),"SQUARE_MARKETING_IMAGE"),
 (f"PL_sq_2{SUF}",  b64(canvas(img2,1200,1200)),"SQUARE_MARKETING_IMAGE"),
 (f"PL_logo{SUF}",  b64(logo_img()),            "LOGO"),
]

# ---- Polish copy (enforced lengths) ----
BUSINESS="Bonhaus"
HEAD=["Bonhaus – gadżety do domu","Najlepsze ceny online","Szybka dostawa 24/48h",
      "Sprytne gadżety i nowości","Zakupy online w Bonhaus","Wysoka jakość, niska cena"]
LONG=["Bonhaus – sprytne gadżety i akcesoria do domu w najlepszych cenach online",
      "Odkryj praktyczne nowości do domu z szybką dostawą w całej Polsce"]
DESC=["Sprytne gadżety i akcesoria do domu.","Zamów online praktyczne nowości w super cenach. Dostawa 24/48h.",
      "Wysoka jakość i najlepsze ceny. Sprawdź ofertę Bonhaus.","Tysiące zadowolonych klientów w Polsce."]
def chk(lst,n):
    for s in lst: assert len(s)<=n, f"too long ({len(s)}>{n}): {s}"
chk(HEAD,30); chk(LONG,90); chk(DESC,90); assert len(BUSINESS)<=25

# ---- assemble asset list: (create_op, field_type) ----
ASSETS=[({"textAsset":{"text":BUSINESS}},"BUSINESS_NAME")]
for h in HEAD:  ASSETS.append(({"textAsset":{"text":h}},"HEADLINE"))
for h in LONG:  ASSETS.append(({"textAsset":{"text":h}},"LONG_HEADLINE"))
for dd in DESC: ASSETS.append(({"textAsset":{"text":dd}},"DESCRIPTION"))
for name,data,field in IMAGES: ASSETS.append(({"name":name,"imageAsset":{"data":data}},field))

import re as _re, json as _json
def post(service, ops, apply, partial=False, tolerant=False):
    url=f"https://googleads.googleapis.com/{API}/customers/{CID}/{service}:mutate"
    body={"operations":ops,"validateOnly":(not apply),"partialFailure":partial}
    r=requests.post(url,headers=HDR,json=body,timeout=120)
    if r.status_code!=200:
        codes=_re.findall(r'"[a-zA-Z]+Error":\s*"[A-Z_]+"', r.text)
        print(f"  ✗ {service} HTTP {r.status_code} | errors: {sorted(set(codes))}")
        if tolerant: return None
        print(r.text[:600]); sys.exit(1)
    return r.json()

print(f"APPLY — {len(ASSETS)} assets ({len(HEAD)}H/{len(LONG)}LH/{len(DESC)}D/{len(IMAGES)}img)")

# Brand Guidelines is ENABLED + immutable → business name & logo go at CAMPAIGN level.
BRAND_FIELDS={"BUSINESS_NAME","LOGO","LANDSCAPE_LOGO"}

# STEP 1 — create assets, collect real resource names (order preserved)
res=post("assets",[{"create":op} for op,_ in ASSETS],apply=True,partial=False)
names=[x.get("resourceName") for x in res["results"]]
print(f"  step1: {len(names)} assets create")

# STEP 2a — brand assets (business name + logo) at CAMPAIGN level
camp_ops=[{"create":{"campaign":CAMPAIGN,"asset":rn,"fieldType":ft}}
          for rn,(_,ft) in zip(names,ASSETS) if ft in BRAND_FIELDS]
post("campaignAssets",camp_ops,apply=True)
print(f"  step2a: {len(camp_ops)} brand assets → campaign")

# STEP 2b — creative assets at ASSET GROUP level
grp_ops=[{"create":{"assetGroup":ASSET_GROUP,"asset":rn,"fieldType":ft}}
         for rn,(_,ft) in zip(names,ASSETS) if ft not in BRAND_FIELDS]
post("assetGroupAssets",grp_ops,apply=True)
print(f"  step2b: {len(grp_ops)} creative → asset group")

# STEP 3 — enable campaign
r3=post("campaigns",[{"update":{"resourceName":CAMPAIGN,"status":"ENABLED"},"updateMask":"status"}],apply=True,tolerant=True)
print("  step3 ENABLE:", "→ ENABLED" if r3 else "eșuat (vezi erori)")
