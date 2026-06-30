# /// script
# requires-python=">=3.10"
# dependencies=["pg8000","pypdf"]
# ///
"""RECONCILIERE COD pe ADEVARUL CURIERULUI (DPD live), NU pe status-ul AWBprint (care minte in ambele sensuri).
Candidati = comenzi Shopify FULFILLED + plata PENDING + vechi (COD expediat dar neincasat = ori returnat, ori livrat-neincasat).
Pt fiecare verific LIVE pe DPD si:
  - DPD returnat (Back to Sender / Return to Sender / refused) -> orderCancel DIRECT (refund=false, restock=false,
    notify=TRUE) + tag 'anulata'. NU ating fulfillment-ul: comanda ramane FULFILLED (= dovada ca a plecat). NICIODATA fulfillmentCancel.
  - DPD livrat (Delivered, NU back) -> orderMarkAsPaid (COD incasat, capture ratat de fluxul normal)
  - in-tranzit / fara raspuns DPD / non-DPD (Packeta/Sameday) -> LAS (nu ating)
Dry-run by default. Reversibil prin natura (nu strica nimic la dry). Vezi memoria [[awbprint-status-unreliable]].
  --apply         scrie · --shops EST,MAG (prefix) restrange · --limit N per magazin
  --before DATE   doar comenzi create inainte de YYYY-MM-DD (default: azi-14 zile, ca sa NU atinga comenzi inca in tranzit)"""
import os, importlib.util, time, json, argparse, sys, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_C=["/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py",
    os.path.join(os.path.dirname(__file__), "..", "..", "xconnector", "xconnector.py"),
    "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py"]
_XC=next((p for p in _C if os.path.exists(p)), _C[1])
spec=importlib.util.spec_from_file_location("xc", _XC); spec.loader.exec_module(xc:=importlib.util.module_from_spec(spec))
ap=argparse.ArgumentParser(); ap.add_argument("--apply",action="store_true"); ap.add_argument("--shops",default=""); ap.add_argument("--limit",type=int,default=0); ap.add_argument("--before",default="")
a=ap.parse_args()
toks={t["shopDomain"]:t["adminToken"] for t in xc.load_shopify_tokens()}
pref={t["shopDomain"]:t.get("prefix","?") for t in xc.load_shopify_tokens()}
want=[s.strip().upper() for s in a.shops.split(",") if s.strip()]
def gql(dom,q,v=None): return xc.shopify_gql(dom,toks[dom],q,v)
CUT=a.before or (datetime.date.today()-datetime.timedelta(days=14)).isoformat()
Q='status:open fulfillment_status:fulfilled financial_status:pending created_at:<%s'%CUT
def is_ret(dl): return ("back to sender" in dl) or ("return to sender" in dl) or ("returnat" in dl) or ("refus" in dl)
def is_del(dl): return ("delivered" in dl) and ("back" not in dl)
GTOT=dict(seen=0, returned_cancelled=0, delivered_paid=0, left=0, err=0)
print("SWEEP DPD-truth · fulfilled+pending+<%s · %s · shops=%s"%(CUT,"APPLY" if a.apply else "DRY", want or "TOATE"),flush=True)
for dom,st in toks.items():
    if want and pref.get(dom,"").upper() not in want: continue
    # gather
    cands=[]; cur=None
    while True:
        after=', after:"%s"'%cur if cur else ""
        d=gql(dom,'query{ orders(first:50%s, query:%s){ edges{ cursor node{ id name fulfillments(first:6){ id status trackingInfo{ number } } } } pageInfo{ hasNextPage } } }'%(after,json.dumps(Q)))
        o=((d.get("data") or {}).get("orders") or {}); es=o.get("edges") or []
        for e in es:
            n=e["node"]; trk=None; act=[]
            for f in (n.get("fulfillments") or []):
                if f.get("status") in ("SUCCESS","OPEN","IN_PROGRESS","PENDING"): act.append(f["id"])
                for t in (f.get("trackingInfo") or []):
                    if (t or {}).get("number"): trk=t["number"]
            cands.append((n["name"],n["id"],trk,act))
        if not (o.get("pageInfo") or {}).get("hasNextPage") or not es: break
        cur=es[-1]["cursor"]
        if a.limit and len(cands)>=a.limit: cands=cands[:a.limit]; break
    if not cands: continue
    # DPD check (doar numere DPD-RO)
    dpdnums=[c[2] for c in cands if c[2] and str(c[2]).isdigit() and len(str(c[2]))>=10]
    dpd=xc.dpd_track_sync(dpdnums) if dpdnums else {}
    st_shop=dict(returned_cancelled=0, delivered_paid=0, left=0, err=0)
    print("\n══ %s · %d candidați (%d DPD) ══"%(pref.get(dom,dom),len(cands),len(dpdnums)),flush=True)
    for name,oid,trk,act in cands:
        GTOT["seen"]+=1
        desc=(dpd.get(trk) or "") if (trk and str(trk).isdigit() and len(str(trk))>=10) else ""
        dl=desc.lower()
        if is_ret(dl):
            # orderCancel DIRECT — NU atinge fulfillment-ul (refuzatele au PLECAT, raman FULFILLED = dovada expedierii)
            if not a.apply: st_shop["returned_cancelled"]+=1; GTOT["returned_cancelled"]+=1; continue
            rc=gql(dom,'mutation($id:ID!){ orderCancel(orderId:$id, reason:OTHER, refund:false, restock:false, notifyCustomer:true, staffNote:"anulare refuz COD verificat DPD"){ orderCancelUserErrors{ message } } }',{"id":oid})
            errs=((rc.get("data") or {}).get("orderCancel") or {}).get("orderCancelUserErrors")
            if errs is not None and not errs:
                gql(dom,'mutation($id:ID!,$t:[String!]!){ tagsAdd(id:$id, tags:$t){ userErrors{ message } } }',{"id":oid,"t":["anulata"]})
                st_shop["returned_cancelled"]+=1; GTOT["returned_cancelled"]+=1
            else:
                # NU forta fulfillmentCancel (asta ar strica statusul) — daca da 'outstanding fulfillments', sar + loghez
                st_shop["err"]+=1; GTOT["err"]+=1; print("  ⏭ %s NEanulat (NU forțez unfulfill): %s"%(name,errs),flush=True)
            time.sleep(0.25)
        elif is_del(dl):
            if not a.apply: st_shop["delivered_paid"]+=1; GTOT["delivered_paid"]+=1; continue
            rp=gql(dom,'mutation($id:ID!){ orderMarkAsPaid(input:{id:$id}){ userErrors{ message } } }',{"id":oid})
            ue=((rp.get("data") or {}).get("orderMarkAsPaid") or {}).get("userErrors")
            if ue is not None and not ue: st_shop["delivered_paid"]+=1; GTOT["delivered_paid"]+=1
            else: st_shop["err"]+=1; GTOT["err"]+=1; print("  ❌ %s markpaid: %s"%(name,ue),flush=True)
            time.sleep(0.25)
        else:
            st_shop["left"]+=1; GTOT["left"]+=1
    print("  %s → returnat-anulat=%d livrat-paid=%d lasate=%d err=%d"%(pref.get(dom,dom),st_shop["returned_cancelled"],st_shop["delivered_paid"],st_shop["left"],st_shop["err"]),flush=True)
print("\nGTOT:", json.dumps(GTOT,ensure_ascii=False),flush=True)
