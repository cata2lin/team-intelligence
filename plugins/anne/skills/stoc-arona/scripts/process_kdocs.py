# /// script
# requires-python=">=3.9"
# dependencies=[]
# ///
import os,sys,json,re
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
D=os.path.dirname(os.path.abspath(__file__))
C=sys.argv[1]; SHEET=sys.argv[2]; FX=float(sys.argv[3]); DATE=sys.argv[4]
kd=json.load(open(os.path.join(D,"kdocs_1314.json"),encoding="utf-8"))[SHEET]
inv=json.load(open(os.path.join(D,f"inv_desc_{C}.json"),encoding="utf-8"))
# model -> price from invoice
mp={}
for qty,usd,desc in inv:
    m=re.search(r"model\s*0*(\d+)",desc)
    if m: mp[m.group(1)]=usd
from collections import defaultdict
qp=defaultdict(set)
for qty,usd,desc in inv: qp[qty].add(usd)
stock={}
for fn in os.listdir(D):
    if fn.startswith("store_") and fn.endswith(".json"):
        for row in json.load(open(os.path.join(D,fn),encoding="utf-8")):
            if len(row)>=4 and row[2]: stock[str(row[2]).strip()]={"store":row[0],"qty":row[3]}
prices=json.load(open(os.path.join(D,"prices.json"),encoding="utf-8"))
def resolve(sku):
    if not sku: return None
    if sku in stock: return sku
    if sku.isdigit():
        cd=[s for s in stock if s.endswith(sku)]; return cd[0] if len(cd)==1 else None
    return None
added=[];unm=[];amb=[]
for r in kd:
    if not isinstance(r,list) or len(r)<3: continue
    model=str(r[0]).strip()
    if not model or not re.match(r"^\d+$",model): continue
    full=str(r[3]).strip() if len(r)>3 else ""
    qraw=str(r[2]); qm=re.search(r"\d+",qraw); qty=int(qm.group()) if qm else None
    # price: model in invoice, else qty-unique
    usd=mp.get(model)
    if usd is None and qty is not None:
        pr=qp.get(qty,set())
        if len(pr)==1: usd=next(iter(pr))
    if usd is None: amb.append((model,qty)); continue
    s = (full if full.startswith("GD-") and full in stock else None) or resolve(model)
    if not s: unm.append((full or model,qty)); continue
    cn=round(usd*FX,4)
    if s in prices:
        old=prices[s].get("cogs") or round(prices[s]["usd"]*prices[s].get("fx",4.358),4)
        if abs(old-cn)>0.01:
            prices[s]={"usd":usd,"fx":FX,"cogs":round((old+cn)/2,2),"container":f"{prices[s].get('container')}+{C}","date":DATE}
    else:
        prices[s]={"usd":usd,"fx":FX,"container":C,"date":DATE}; added.append((s,usd))
json.dump(prices,open(os.path.join(D,"prices.json"),"w",encoding="utf-8"),ensure_ascii=False)
print(f"{C} ({SHEET}): {len(kd)} kdocs | NOI {len(added)} | fara pret {len(amb)} | nepot(nu-s in stoc) {len(unm)}")
for s,u in added: print(f"   +{s:22} {u}$")
