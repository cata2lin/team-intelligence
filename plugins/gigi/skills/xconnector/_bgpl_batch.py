
import datetime, json
import xconnector as x
toks={t["shopDomain"]:t for t in x.load_shopify_tokens()}; ok=x.load_here_ok()
dfrom=(datetime.date.today()-datetime.timedelta(days=30)).isoformat(); dto=datetime.date.today().isoformat()
def do_shop(prefix,label):
    sh=next(s for s in x.load_shops() if s["shopDomain"].startswith(prefix)); st=toks.get(sh["shopDomain"])
    xc=x.XC(sh["apiKey"]); con,cons=x.pick_connector(xc,type("A",(),{"cmd":"fulfill"})())
    targets=[o for o in xc.orders(dfrom,dto) if not x.has_awb(o) and o.get("orderName") in ok]
    print("### %s (%s): %d comenzi here_ok fara AWB"%(label,sh["shopDomain"],len(targets)),flush=True)
    made=closed=fail=0
    for i,o in enumerate(targets):
        name=o.get("orderName")
        try:
            ocon=x.route_connector(sh,st,name,cons,con); pc=x.order_parcel_count(st["shopDomain"],st["adminToken"],name)
            body={"orderId":o.get("orderId"),"connectorId":(ocon or {}).get("id"),"parcelCount":pc,"parcelType":"PARCEL","notifyCustomer":False}
            ok2,s,d=x._create_label(xc,body)
        except Exception as e:
            fail+=1; continue
        if ok2: made+=1
        else:
            msg=(d.get("errorMessage") if isinstance(d,dict) else "") or ""
            if "fulfillment" in msg.lower() or s==422: closed+=1
            else: fail+=1
        if (i+1)%25==0: print("  %s ...%d/%d (made=%d closed=%d fail=%d)"%(label,i+1,len(targets),made,closed,fail),flush=True)
    print("%s DONE — AWB=%d | closed-FO=%d | fail=%d"%(label,made,closed,fail),flush=True)
do_shop("ux1x6n-n2","BG")
do_shop("f0yrmh-ia","PL")
print("BGPL BATCH DONE",flush=True)
