# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""gclid_capture.py — injectează captura de gclid în tema Shopify (prerechizita #1 pt Offline Conversions).

Un snippet în `layout/theme.liquid` care citește gclid/gbraid/wbraid din URL la aterizare → localStorage (90z)
→ `/cart/update.js` (o dată/sesiune) → ajunge în `order.customAttributes`. De acolo job-ul de Offline Conversions
îl potrivește cu comanda livrată + marja reală (brand_pnl) → upload prin Data Manager API.

Idempotent (sare dacă `ar_gclid` e deja în temă). DRY-RUN by default. Reutilizează `shopify_gql.py` (creds din KB).
Auto-tagging Google trebuie ON pe cont (adaugă ?gclid= în landing). ⚠️ Colectează FORWARD (comenzile vechi n-au gclid).

Usage:
  uv run gclid_capture.py --prefix GRAN                 # DRY (arată ce ar face)
  uv run gclid_capture.py --prefix GRAN --apply
  uv run gclid_capture.py --stores EST,GT,BELA,GEN --apply
"""
import os, sys, json, argparse, importlib.util, subprocess, requests
HERE=os.path.dirname(os.path.abspath(__file__))
SGQL=os.path.join(HERE,"..","shopify-stores","scripts","shopify_gql.py")
sp=importlib.util.spec_from_file_location("shopify_gql",SGQL); sg=importlib.util.module_from_spec(sp)
_a=sys.argv; sys.argv=["shopify_gql"]; sp.loader.exec_module(sg); sys.argv=_a
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
API=getattr(sg,"API_VERSION","2026-01")
MARKER="ar_cid_set"   # literal REAL din snippet (NU 'ar_gclid' — ăla e 'ar_'+k concatenat, nu apare în sursă)
SNIPPET="""  {%- comment -%} ARONA: captură gclid/gbraid/wbraid → atribut comandă (Offline Conversions marja reală) {%- endcomment -%}
  <script>
  (function(){
    try{
      var qp=new URLSearchParams(location.search), keys=['gclid','gbraid','wbraid'], now=Date.now(), TTL=90*864e5;
      keys.forEach(function(k){var v=qp.get(k); if(v){try{localStorage.setItem('ar_'+k,v);localStorage.setItem('ar_'+k+'_t',now);}catch(e){}}});
      var attrs={}; keys.forEach(function(k){var v=localStorage.getItem('ar_'+k),ts=parseInt(localStorage.getItem('ar_'+k+'_t')||'0',10); if(v&&(now-ts)<TTL)attrs[k]=v;});
      if(Object.keys(attrs).length && !sessionStorage.getItem('ar_cid_set')){
        fetch('/cart/update.js',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({attributes:attrs})})
          .then(function(){try{sessionStorage.setItem('ar_cid_set','1');}catch(e){}}).catch(function(){});
      }
    }catch(e){}
  })();
  </script>
"""
ap=argparse.ArgumentParser()
ap.add_argument("--prefix"); ap.add_argument("--stores", help="csv de prefixe")
ap.add_argument("--apply", action="store_true")
a=ap.parse_args()
prefixes=[p.strip().upper() for p in (a.stores.split(",") if a.stores else ([a.prefix] if a.prefix else [])) if p.strip()]
if not prefixes: sys.exit("Dă --prefix X sau --stores A,B,C")
def _req(method,url,tok,**kw):
    h={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}
    return requests.request(method,url,headers=h,timeout=60,**kw)
for pfx in prefixes:
    try:
        shop,tok=sg.resolve_store(pfx)
    except Exception as e:
        print(f"⚠️ {pfx}: resolve eșuat ({str(e)[:70]}) → SKIP"); continue
    base=f"https://{shop}/admin/api/{API}"
    r=_req("GET",f"{base}/themes.json",tok)
    if r.status_code!=200: print(f"⚠️ {pfx}: themes {r.status_code} → SKIP"); continue
    main=next((t for t in r.json().get("themes",[]) if t.get("role")=="main"),None)
    if not main: print(f"⚠️ {pfx}: fără temă main → SKIP"); continue
    tid=main["id"]
    ra=_req("GET",f"{base}/themes/{tid}/assets.json",tok,params={"asset[key]":"layout/theme.liquid"})
    if ra.status_code!=200: print(f"⚠️ {pfx}: get theme.liquid {ra.status_code} → SKIP"); continue
    val=ra.json().get("asset",{}).get("value","")
    if MARKER in val:
        print(f"✅ {pfx} (tema {tid} {main.get('name','')[:20]}): DEJA are captura → skip"); continue
    if val.count("</body>")!=1:
        print(f"⚠️ {pfx}: </body> apare {val.count('</body>')}× → SKIP (injectare manuală)"); continue
    newval=val.replace("</body>", SNIPPET+"</body>", 1)
    if not a.apply:
        print(f"DRY {pfx} (tema {tid} {main.get('name','')[:20]}): ar injecta {len(SNIPPET)} chars înainte de </body>"); continue
    rp=_req("PUT",f"{base}/themes/{tid}/assets.json",tok,data=json.dumps({"asset":{"key":"layout/theme.liquid","value":newval}}))
    if rp.status_code in (200,201):
        # verify
        rv=_req("GET",f"{base}/themes/{tid}/assets.json",tok,params={"asset[key]":"layout/theme.liquid"})
        ok=MARKER in rv.json().get("asset",{}).get("value","") if rv.status_code==200 else False
        print(f"{'✅' if ok else '⚠️'} {pfx} (temă {tid}): injectat {'+ verificat' if ok else '(PUT ok, verificare incertă)'}")
    else:
        print(f"❌ {pfx}: PUT {rp.status_code} {rp.text[:120]}")
