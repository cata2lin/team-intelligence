# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Extend an RSA's headlines to 15 (append distinct lines, preserve the rest). --apply to execute.
Updates ad.responsive_search_ad.headlines via ads:mutate (full list replace)."""
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API=os.environ.get("GADS_API_VERSION","v21")

# (cid, ad_id, [the FULL 15-headline list: 12 existing + 3 new])
RSAS=[
 ("5229815058","812341025225",[  # Esteban Search-Brand
   "Maison d'Esteban Oficial","Site-ul Oficial Esteban","2+1 Gratis la Toate","Peste 120 de Arome",
   "50 ml de la 45 lei","Persistă Peste 12 Ore","Transport Gratuit 150 lei","Comandă de la Sursă",
   "Calitate Superioară","Livrare în 1-2 Zile","Parfumuri Inspirate","Vezi Toată Colecția",
   "Parfumuri Bărbați & Damă","Mii de Recenzii Pozitive","Cadou la Fiecare Comandă"]),
 ("7566352958","749275561807",[  # Belasil Brand Protect
   "Belasil - Detergen Profesional","Comanda online detergent rufe","Detergent de rufe profesional",
   "Detergent Lichid - 5 Litri","Oferte in fiecare zi","Dero Profesional - rufe albe",
   "Formulă concentrată - Belasil","Balsam de rufe inclus","Comanda acum online","Transport rapid si sigur",
   "Sigur pentru pielea sensibila","Prospetime care dureaza zile",
   "200 de Spălări pe Bidon","Doar 0,49 lei pe Spălare","Direct de la Producător"]),
]
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
apply="--apply" in sys.argv
overlimit=[h for _,_,hs in RSAS for h in hs if len(h)>30]
if overlimit: print("⚠ peste 30 char:",overlimit)
for cid,adid,hs in RSAS:
    body={"operations":[{"update":{"resourceName":f"customers/{cid}/ads/{adid}","responsiveSearchAd":{"headlines":[{"text":h} for h in hs]}},"updateMask":"responsive_search_ad.headlines"}],"validateOnly":(not apply)}
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{cid}/ads:mutate",headers=H,json=body,timeout=60)
    print(("APLICAT" if apply else "DRY-RUN"),f"ad {adid}: {len(hs)} headlines |",rr.status_code, "ok" if rr.status_code==200 else rr.text[:240])
