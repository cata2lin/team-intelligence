import json,os,sys,glob
sys.stdout.reconfigure(encoding="utf-8",errors="replace")
prices=json.load(open("prices.json",encoding="utf-8"))
# invoice usd sets per container
invset={}
for f in glob.glob("inv_C*.json"):
    c=f[4:-5]
    try: invset[c]=set(round(x[1],4) for x in json.load(open(f)))
    except: pass
for f in glob.glob("inv_desc_C*.json"):
    c=f[9:-5]
    try: invset.setdefault(c,set()).update(round(x[1],4) for x in json.load(open(f)))
    except: pass
ASTP={1.5,1.8,2.0}
ok=0; bad=[]; noinv=set(); anom=[]
for sku,p in prices.items():
    cont=str(p.get("container",""))
    usd=p.get("usd"); cg=p.get("cogs")
    # anomaly check
    if cg is not None and (cg<=0 or cg>600): anom.append((sku,cg,cont))
    # only single-container invoice-derived
    if usd is None: continue
    if any(t in cont for t in ["iunie","EST","MANUAL","GENTO","mai","apr"]): continue
    conts=cont.split("+")
    matched=False
    for cc in conts:
        cc=cc.strip()
        if cc in invset:
            if round(usd,4) in invset[cc] or round(usd,4) in ASTP: matched=True; break
        else: noinv.add(cc)
    if matched: ok+=1
    elif any(cc.strip() in invset for cc in conts): bad.append((sku,usd,cont))
print(f"OK (usd confirmat in factura): {ok}")
print(f"MISMATCH (usd NU e in factura containerului): {len(bad)}")
for b in bad[:20]: print("   ",b)
print(f"\nContainere fara inv_*.json local (nu pot verifica): {sorted(noinv)}")
print(f"\nAnomalii cogs (<=0 sau >600): {len(anom)}")
for a in anom[:20]: print("   ",a)
