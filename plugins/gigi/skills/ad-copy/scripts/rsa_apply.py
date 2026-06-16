# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""RSA copy apply — full-replace a Responsive Search Ad's headlines (≤30) + descriptions (≤90) in one
update. Use to push a diversified, keyword-relevant set onto an ad whose ad strength is AVERAGE
because its 15 headlines are too similar (the #1 cause). Validates char limits; dry-run unless --apply.

Edit the RSAS list (cid, ad_id, headlines[15], descriptions[≤4]) then:
    uv run rsa_apply.py                # dry-run + char check
    uv run rsa_apply.py --apply
"""
import os, sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API=os.environ.get("GADS_API_VERSION","v21")

# --- the differentiated sets. Belasil Non-Brand: 3 ad groups, each on its OWN keyword theme. ---
CANTITATE=["Detergent Bidon 10 Litri","Detergent Lichid 5 Litri","200 de Spălări pe Bidon",
 "Bidon Mare, Mai Economic","Detergent Rufe 5L și 10L","Doar 0,49 lei pe Spălare",
 "Stoc de Detergent pe Luni","Detergent 10 Litri Concentrat","Balsam Inclus în Bidon",
 "Transport Gratuit la 150 lei","Direct de la Producător","4,7/5 din 1.250+ Recenzii",
 "Comandă Bidonul Acum","Mai Mult Detergent, Preț Mic","Spală Alb și Color"]
IEFTIN=["Detergent Ieftin și Bun","Detergent de la Producător","Doar 0,49 lei pe Spălare",
 "Detergent Rufe Ieftin","Preț de Producător, -36%","Cel mai Bun Raport Preț",
 "Fără Intermediari, Preț Corect","10L la 99 lei, de la 255","Detergent Lichid Bun, Ieftin",
 "Calitate Premium, Preț Mic","200 de Spălări pe Bidon","4,7/5 din 1.250+ Recenzii",
 "Transport Gratuit la 150 lei","Comandă de la Sursă","Balsam Inclus, Fără Costuri"]
GEL=["Detergent Gel Concentrat","Detergent Lichid Rufe","Gel Dens, Super Concentrat",
 "Detergent Concentrat Rufe","Gel Delicat cu Hainele","200 de Spălări dintr-un Bidon",
 "Detergent Lichid Concentrat","Doar 0,49 lei pe Spălare","Parfum de Lungă Durată",
 "Balsam Inclus în Gel","Spală Alb și Color","Direct de la Producător",
 "4,7/5 din 1.250+ Recenzii","Transport Gratuit la 150 lei","Comandă Gelul Concentrat"]
D_CANT=["Bidon de 5L sau 10L: 200 de spălări, doar 0,49 lei pe spălare. Stoc pe luni întregi.",
 "Detergent lichid în bidon mare, direct de la producător. 10L la 99 lei, de la 255 lei.",
 "Balsam inclus, spală alb și color, manual și automat. Transport gratuit peste 150 lei.",
 "4,7/5 din peste 1.250 de recenzii. Garanție retur 14 zile. 5 parfumuri la alegere."]
D_IEFT=["Detergent ieftin și bun, de la producător, fără intermediari. Doar 0,49 lei pe spălare.",
 "Preț de producător -36%: 10L la 99 lei (de la 255). Calitate premium la preț mic.",
 "200 de spălări pe bidon, balsam inclus. Spală alb și color. Transport gratuit la 150 lei.",
 "4,7/5 din peste 1.250 de recenzii. Garanție retur 14 zile. 5 parfumuri la alegere."]
D_GEL=["Detergent gel concentrat: dens, delicat cu hainele. 200 de spălări, 0,49 lei pe spălare.",
 "Gel concentrat pentru rufe, balsam inclus. Parfum de lungă durată, spală alb și color.",
 "De la producător, fără intermediari. 10L la 99 lei. Transport gratuit peste 150 lei.",
 "4,7/5 din peste 1.250 de recenzii. Garanție retur 14 zile. 5 parfumuri la alegere."]
RSAS=[
 ("7566352958","812348011800",CANTITATE,D_CANT),   # Detergent cantitate (5-10L)
 ("7566352958","812447673722",IEFTIN,D_IEFT),       # Detergent ieftin / producator
 ("7566352958","812377283662",GEL,D_GEL),           # Detergent gel / lichid
]
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
# validate
bad=[(h,len(h)) for _,_,hs,ds in RSAS for h in hs if len(h)>30]+[(d,len(d)) for _,_,hs,ds in RSAS for d in ds if len(d)>90]
if bad: sys.exit("⚠ peste limită: "+str(bad[:5]))
for cid,ad,hs,ds in RSAS:
    if len(hs)!=len(set(hs)): sys.exit(f"ad {ad}: headline-uri duplicate")
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
apply="--apply" in sys.argv
for cid,ad,hs,ds in RSAS:
    body={"operations":[{"update":{"resourceName":f"customers/{cid}/ads/{ad}",
            "responsiveSearchAd":{"headlines":[{"text":h} for h in hs],"descriptions":[{"text":d} for d in ds]}},
            "updateMask":"responsive_search_ad.headlines,responsive_search_ad.descriptions"}],"validateOnly":(not apply)}
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{cid}/ads:mutate",headers=H,json=body,timeout=60)
    print(("APLICAT" if apply else "DRY-RUN"),f"ad {ad}: {len(hs)}H/{len(ds)}D |",rr.status_code,"ok" if rr.status_code==200 else rr.text[:240])
