"""
fix_partial.py — inchide comenzile ramase PARTIALLY_FULFILLED dupa adaugarea parfumului surpriza.

De ce raman partial: eticheta o cere xConnector, iar el fulfill-uieste din COPIA LUI a comenzii.
Daca ceri eticheta imediat dupa order edit, copia inca n-are linia noua => linia de surpriza ramane
neonorata. Procedura (owner): anuleaza AWB-ul, ~15s mai tarziu pune un TAG pe comanda — modificarea
declanseaza re-sincronizarea in xConnector — apoi cere eticheta din nou, acum pe copia completa.

    /opt/venv/bin/python /tmp/fix_partial.py --names EST244519,GT57376 --apply
"""
import argparse, asyncio, json, sys, os
sys.path.insert(0, "/app")
os.chdir("/app")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import models
from database import AsyncSessionLocal
from services import shopify_service as ss
from services import courier_actions as ca
from services.couriers import get_courier_service

TAG = "awb-refacut"

_Q = """
query f($id: ID!) { order(id: $id) {
  name displayFulfillmentStatus tags
  fulfillments(first: 20) { id status trackingInfo { number } }
  lineItems(first: 60) { nodes { sku currentQuantity } } } }
"""
_CANCEL_M = """mutation c($id: ID!){ fulfillmentCancel(id:$id){ userErrors{ message } } }"""
_TAG_M = """mutation t($id: ID!, $tags: [String!]!){ tagsAdd(id:$id, tags:$tags){ userErrors{ message } } }"""

async def load_order(db, name):
    return (await db.execute(
        select(models.Order)
        .options(selectinload(models.Order.line_items),
                 selectinload(models.Order.shipments),
                 selectinload(models.Order.store))
        .where(models.Order.name == name)
    )).unique().scalars().first()

async def fix_one(db, name, apply=False):
    o = await load_order(db, name)
    if not o:
        return {"order": name, "ok": False, "step": "load", "msg": "nu exista in OH"}
    store = o.store
    gid = "gid://shopify/Order/%s" % str(o.shopify_order_id).split("/")[-1]
    sh = (await ss._gql(store, _Q, {"id": gid})).get("order") or {}

    stare = sh.get("displayFulfillmentStatus")
    surp = any("surpriz" in (li.get("sku") or "").lower()
               for li in ((sh.get("lineItems") or {}).get("nodes") or []) if (li.get("currentQuantity") or 0) > 0)
    if stare == "FULFILLED":
        return {"order": name, "ok": True, "step": "deja-complet", "msg": "FULFILLED"}
    if not surp:
        return {"order": name, "ok": False, "step": "guard", "msg": "n-are linia de surpriza"}

    ff = [f for f in (sh.get("fulfillments") or []) if (f.get("status") or "").upper() == "SUCCESS"]
    ships = list(o.shipments or [])
    plan = {"order": name, "stare": stare, "awb": [s.awb for s in ships if s.awb],
            "ff": [f["id"].split("/")[-1] for f in ff]}
    if not apply:
        return {**plan, "ok": True, "step": "dry"}

    # 1. anuleaza AWB-ul la curier + curata shipment-urile
    tratate = set()          # sync-ul de fundal reimporta acelasi AWB pe randuri multiple
    for s in ships:
        if s.awb and s.awb not in tratate:
            tratate.add(s.awb)
            svc = get_courier_service(s.courier or s.account_key or "")
            # Cheia de credentiale, doua cazuri:
            #  - rand din BACKFILL (courier="DPD Romania", account_key="xconnector-…"): serviciul
            #    rezolvat e DPD, dar cheia xconnector nu-i cont DPD -> "Wrong username format".
            #    Trimitem NUMELE curierului, care cade pe aliasul de vendor -> dpd-ro.
            #  - rand facut de OH (courier="XCONNECTOR", account_key="xconnector-6f9e22-9d"):
            #    serviciul E xconnector si cheia lui e chiar cea buna — nu o inlocui, altfel iese
            #    "No credentials found for account 'XCONNECTOR'".
            vkey = (s.account_key or "")
            if getattr(svc, "name", "") != "xconnector" and vkey.lower().startswith("xconnector"):
                vkey = s.courier or vkey
            try:
                v = await svc.void_awb(db, s.awb, vkey)
                good, msg = bool(v.success), (v.message or "")
            except Exception as e:
                good, msg = False, str(e)
            if not good and not any(w in msg.lower() for w in
                                    ("cancel", "anulat", "not found", "nu exist")):
                return {**plan, "ok": False, "step": "void", "msg": "%s: %s" % (s.awb, msg[:120])}
        await db.delete(s)
    await db.flush()
    await db.commit()

    # 2. fulfillment-ul din Shopify (altfel xConnector vede „no open fulfillment orders")
    for f in ff:
        r = await ss._gql(store, _CANCEL_M, {"id": f["id"]})
        errs = ((r.get("fulfillmentCancel") or {}).get("userErrors")) or []
        if errs:
            return {**plan, "ok": False, "step": "fulfillmentCancel",
                    "msg": "; ".join(e.get("message", "?") for e in errs)}

    # 3. ~15s dupa anulare, TAG-ul: modificarea comenzii forteaza xConnector sa-si resincronizeze
    #    copia (acum cu linia de surpriza SI cu fulfillment order-ele redeschise).
    await asyncio.sleep(15)
    r = await ss._gql(store, _TAG_M, {"id": gid, "tags": [TAG]})
    errs = ((r.get("tagsAdd") or {}).get("userErrors")) or []
    if errs:
        return {**plan, "ok": False, "step": "tag", "msg": "; ".join(e.get("message", "?") for e in errs)}
    await asyncio.sleep(25)          # lasam webhook-ul sa ajunga la xConnector

    # 4. eticheta noua — acum pe copia completa
    o2 = await load_order(db, name)
    vechi = set(plan["awb"])
    acct = "xconnector-" + (store.domain or "").split(".")[0]
    last = ""
    for i in range(4):
        deja = [s_.awb for s_ in (o2.shipments or []) if s_.awb and s_.awb not in vechi]
        if deja:
            await db.commit()
            return {**plan, "ok": True, "step": "gata-auto", "awb_nou": deja[0]}
        try:
            r = await ca._create_one(db, store, o2, acct, {})
            await db.commit()
            break
        except Exception as e:
            last = str(e)[:200]
            await db.rollback()
            o2 = await load_order(db, name)
            if i == 3:
                await db.commit()
                return {**plan, "ok": False, "step": "awb_nou", "msg": last}
            await asyncio.sleep(25)

    # 5. verifica EFECTUL: comanda trebuie sa fie acum FULFILLED (surpriza inclusa in fulfillment)
    await asyncio.sleep(6)
    sh2 = (await ss._gql(store, _Q, {"id": gid})).get("order") or {}
    return {**plan, "ok": True, "step": "gata", "awb_nou": r.get("awb"),
            "stare_finala": sh2.get("displayFulfillmentStatus")}

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    for nm in [x.strip() for x in a.names.split(",") if x.strip()]:
        async with AsyncSessionLocal() as db:
            try:
                r = await fix_one(db, nm, a.apply)
            except Exception as e:
                await db.rollback()
                r = {"order": nm, "ok": False, "step": "exceptie", "msg": repr(e)[:200]}
        print(json.dumps(r, ensure_ascii=False), flush=True)
        await asyncio.sleep(6)

asyncio.run(main())
