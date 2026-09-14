"""
fix_surprise.py — repara comenzile plecate FARA parfum surpriza (incident cutover OH, 20-aug-2026).

Flux per comanda: void AWB -> anuleaza fulfillment-ul Shopify -> order edit (+1 parfum-surpriza, 0 lei)
-> AWB nou. RELUABIL: fiecare pas se sare daca e deja facut (adevarul se citeste din Shopify, nu din OH).
Se ruleaza DOAR pe colete care n-au plecat (verificat separat, live la DPD).

    /opt/venv/bin/python /tmp/fix_surprise.py --file /tmp/orders.txt [--apply]
    optional: --voided /tmp/voided.txt  (AWB-uri deja anulate la curier — void-ul lor poate esua)
"""
import argparse, asyncio, json, sys, os, time
sys.path.insert(0, "/app")
os.chdir("/app")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import models
from database import AsyncSessionLocal
from services import shopify_service as ss
from services import order_edit as oe
from services import courier_actions as ca
from services.couriers import get_courier_service
from services.cron_parity import surprise as surp

_Q = """
query f($id: ID!) { order(id: $id) {
  name sourceName displayFulfillmentStatus
  fulfillments(first: 20) { id status trackingInfo { number } }
  lineItems(first: 60) { nodes { sku title quantity currentQuantity } }
} }
"""
_CANCEL_M = """
mutation c($id: ID!) { fulfillmentCancel(id: $id) {
  fulfillment { id status } userErrors { field message } } }
"""

def _stare(sh):
    """(nr_parfumuri, are_surpriza) din liniile LIVE ale comenzii."""
    n, are = 0, False
    for li in ((sh.get("lineItems") or {}).get("nodes") or []):
        sku = (li.get("sku") or "").strip()
        title = (li.get("title") or "").lower()
        q = li.get("currentQuantity", li.get("quantity")) or 0
        if not sku or q <= 0:
            continue
        if "surpriz" in sku.lower() or "surpriz" in title:
            are = True; continue
        if "cutie" in sku.lower():
            continue
        n += q
    return n, are

async def load_order(db, name):
    return (await db.execute(
        select(models.Order)
        .options(selectinload(models.Order.line_items),
                 selectinload(models.Order.shipments),
                 selectinload(models.Order.store))
        .where(models.Order.name == name)
    )).unique().scalars().first()

async def fix_one(db, name, apply=False, voided=frozenset()):
    o = await load_order(db, name)
    if not o:
        return {"order": name, "ok": False, "step": "load", "msg": "nu exista in OH"}
    store = o.store
    gid = "gid://shopify/Order/%s" % str(o.shopify_order_id).split("/")[-1]

    sh = (await ss._gql(store, _Q, {"id": gid})).get("order") or {}
    if "draft" in (sh.get("sourceName") or "").lower():
        return {"order": name, "ok": False, "step": "guard", "msg": "comanda din DRAFT (CS) - sar"}
    n, are_surpriza = _stare(sh)
    if not are_surpriza and n % 3 != 2:
        return {"order": name, "ok": False, "step": "guard",
                "msg": "nu indeplineste regula (%d parfumuri)" % n}

    variant = await surp.variant_surpriza(store, "parfum-surpriza")
    if not variant:
        return {"order": name, "ok": False, "step": "guard", "msg": "magazinul n-are parfum-surpriza activ"}

    ships = list(o.shipments or [])
    ff_active = [f for f in (sh.get("fulfillments") or []) if (f.get("status") or "").upper() == "SUCCESS"]
    if are_surpriza and ff_active and any(s.awb for s in ships):
        return {"order": name, "ok": True, "step": "deja-reparat",
                "msg": "are surpriza + AWB activ (%s)" % ",".join(s.awb for s in ships if s.awb)}
    plan = {"order": name, "parfumuri": n, "are_surpriza": are_surpriza,
            "awb": [s.awb for s in ships if s.awb],
            "ff_active": [f["id"].split("/")[-1] for f in ff_active]}
    if not apply:
        return {**plan, "ok": True, "step": "dry"}

    # ── 1. AWB vechi: anuleaza la curier + sterge shipment-ul din OH ───
    for s in ships:
        if s.awb:
            svc = get_courier_service(s.courier or s.account_key or "")
            if not svc:
                return {**plan, "ok": False, "step": "void", "msg": "curier necunoscut %s" % s.courier}
            # `xconnector-<prefix>` nu e cont DPD → DPD raspunde "Wrong username format". Numele
            # curierului („DPD Romania") cade pe aliasul de vendor din base.get_credentials → dpd-ro.
            vkey = (s.account_key or "")
            if vkey.lower().startswith("xconnector"):
                vkey = s.courier or vkey
            try:
                v = await svc.void_awb(db, s.awb, vkey)
                good = bool(v.success)
                msg = v.message or ""
            except Exception as e:
                good, msg = False, str(e)
            if not good and s.awb not in voided and "cancel" not in msg.lower():
                return {**plan, "ok": False, "step": "void", "msg": "void esuat %s: %s" % (s.awb, msg[:120])}
        await db.delete(s)
    await db.flush()

    # ── 2. fulfillment Shopify (altfel AWB nou = 422 "no open fulfillment") ─
    for f in ff_active:
        r = await ss._gql(store, _CANCEL_M, {"id": f["id"]})
        errs = ((r.get("fulfillmentCancel") or {}).get("userErrors")) or []
        if errs:
            await db.commit()
            return {**plan, "ok": False, "step": "fulfillmentCancel",
                    "msg": "; ".join(e.get("message", "?") for e in errs)}

    # ── 3. order edit: +1 x parfum-surpriza (sarit daca e deja acolo) ──
    if not are_surpriza:
        try:
            beg = await oe.edit_begin(store, o.shopify_order_id)
            await oe.edit_commit(store, beg["calc_order_id"], {
                "add_variants": [{"variant_id": variant, "quantity": 1}],
                "notify": False,
                "staff_note": "Parfum surpriza (recuperare incident cutover OH 20-aug)",
            })
        except Exception as e:
            await db.commit()
            return {**plan, "ok": False, "step": "order_edit", "msg": str(e)[:160]}
    # linia si in OH, ca lista depozitului s-o vada
    if not any("surpriz" in (li.sku or "").lower() for li in (o.line_items or [])):
        db.add(models.LineItem(order_id=o.id, sku="parfum-surpriza",
                               title="Parfum surpriza", quantity=1))
    o.fulfilled_at = None
    await db.flush()

    # ── 4. AWB nou ─────────────────────────────────────────────────────
    o2 = await load_order(db, name)
    # Contul LIVE al magazinului (xconnector-<prefix>). `dpdromania` din shipments e doar eticheta
    # backfill-ului din istoricul xConnector — nu exista ca CourierAccount, deci nu se poate crea cu el.
    acct = "xconnector-" + (store.domain or "").split(".")[0]
    # xConnector LIMITEAZA rafalele si intoarce gol (vezi comentariul din couriers/xconnector.py:
    # „la o tura de 18 comenzi = 36 apeluri in rafala -> xConnector limiteaza si intoarce gol").
    # Simptomul e inselator: „comanda nu exista (inca) in xConnector" / „no open fulfillment orders".
    # Reincercam cu pauza — altfel comanda ramane FARA eticheta, ceea ce e mai rau decat starea initiala.
    last = ""
    vechi = {s_.awb for s_ in ships if s_.awb}
    for incercare in range(4):
        # Cronul auto-AWB al OH poate face eticheta intre timp (comanda e unfulfilled, fara AWB) —
        # daca a aparut deja una NOUA, nu mai cerem a doua (duplicat de eticheta = colet dublu taxat).
        deja = [s_.awb for s_ in (o2.shipments or []) if s_.awb and s_.awb not in vechi]
        if deja:
            await db.commit()
            return {**plan, "ok": True, "step": "gata-auto", "awb_nou": deja[0]}
        try:
            r = await ca._create_one(db, store, o2, acct, {})
            await db.commit()
            return {**plan, "ok": True, "step": "gata", "awb_nou": r.get("awb"),
                    "incercari": incercare + 1}
        except Exception as e:
            last = str(e)[:220]
            await db.rollback()
            o2 = await load_order(db, name)
            if incercare < 3:
                await asyncio.sleep(25)
    await db.commit()
    return {**plan, "ok": False, "step": "awb_nou", "msg": last}

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="")
    ap.add_argument("--voided", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    names = [x.strip() for x in a.names.split(",") if x.strip()]
    if a.limit:
        names = names[:a.limit]
    voided = frozenset(x.strip() for x in a.voided.split(",") if x.strip())
    out = []
    for nm in names:
        async with AsyncSessionLocal() as db:
            try:
                r = await fix_one(db, nm, a.apply, voided)
            except Exception as e:
                await db.rollback()
                r = {"order": nm, "ok": False, "step": "exceptie", "msg": repr(e)[:220]}
        out.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        await asyncio.sleep(8.0)          # xConnector limiteaza rafalele
    print("\n=== %d/%d ok ===" % (sum(1 for r in out if r.get("ok")), len(out)))
    for r in out:
        if not r.get("ok"):
            print("  ESUAT %s [%s] %s" % (r["order"], r.get("step"), r.get("msg")))

asyncio.run(main())
