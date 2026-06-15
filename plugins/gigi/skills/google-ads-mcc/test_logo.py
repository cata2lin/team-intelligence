# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
import os, sys, json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API="v24"; CID="7566352958"; CAMP="customers/7566352958/campaigns/22478321481"
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
def search(qq):
    return requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/googleAds:search",headers=H,json={"query":qq},timeout=60).json().get("results",[])
# find the logo image assets + their dimensions
print("=== Belasil logo image assets ===")
for row in search("SELECT asset.resource_name, asset.name, asset.image_asset.full_size.width_pixels, asset.image_asset.full_size.height_pixels FROM asset WHERE asset.type='IMAGE' AND asset.name LIKE '%Logo%'"):
    a=row["asset"]; fs=(a.get("imageAsset") or {}).get("fullSize") or {}
    print(" ",a["resourceName"],a.get("name"),fs.get("widthPixels"),"x",fs.get("heightPixels"))
# try to link the square logo as campaign LOGO, full error
logo=search("SELECT asset.resource_name FROM asset WHERE asset.type='IMAGE' AND asset.name='Belasil Logo 1x1' LIMIT 1")
if logo:
    res=logo[0]["asset"]["resourceName"]
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{CID}/campaignAssets:mutate",headers=H,
        json={"operations":[{"create":{"campaign":CAMP,"asset":res,"fieldType":"LOGO"}}],"validateOnly":True},timeout=60)
    print("\n=== link LOGO validateOnly:",rr.status_code,"===")
    print(rr.text[:1200])
