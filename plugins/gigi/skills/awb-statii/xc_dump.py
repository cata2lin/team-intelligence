import sys, datetime
sys.path.insert(0,"/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector")
import xconnector as X
want=sys.argv[1].split(","); dfrom=sys.argv[2]; dto=datetime.date.today().isoformat()
print("order,awb,skus")
for sh in X.load_shops():
    if not any(w in sh["shopDomain"] for w in want): continue
    for o in X.XC(sh["apiKey"]).orders(dfrom,dto,{}):
        doc=X.awb_doc(o); trk=X.doc_tracking(doc) if doc else ""
        if not trk: continue
        print("%s,%s,%s" % (o.get("orderName"), trk, "|".join(o.get("skus") or [])))
