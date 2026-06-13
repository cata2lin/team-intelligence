# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Probe aac: pt un batch mic de comenzi fără AWB cu adresă WRONG/UNKNOWN pe un magazin,
scoate by-id (shippingAddress + originalCustomerAddress + status + #history) + TOȚI matcherii
validatorului (zip/county/city/commune/street {value,score} + tokenizedAddress). READ-ONLY."""
import json, sys, datetime
import xconnector as X

SHOP = "ix5bxc-hr.myshopify.com"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 90

sh = [s for s in X.load_shops() if s["shopDomain"] == SHOP][0]
xc = X.XC(sh["apiKey"])
dto = datetime.date.today().isoformat()
dfrom = (datetime.date.today() - datetime.timedelta(days=DAYS)).isoformat()
orders = xc.orders(dfrom, dto)
bad = [o for o in orders if not X.has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]
print("TOTAL fără AWB + adresă WRONG/UNKNOWN (listing, %dz): %d  — arăt primele %d" % (DAYS, len(bad), N))


def fv(m, f):
    x = m.get(f)
    if isinstance(x, dict):
        return "%s(%.3f)" % (x.get("value"), x.get("score") or 0)
    return str(x)


for o in bad[:N]:
    d = xc.by_id(o["orderId"])
    ad = d.get("shippingAddress") or {}
    st = d.get("addressStatus") or o.get("addressStatus")
    hist = d.get("addressValidationHistory") or []
    print("\n" + "=" * 74)
    print("%s  id=%s  status(by-id)=%s  #history=%d" % (o.get("orderName"), o["orderId"], st, len(hist)))
    print("  ship: %s" % json.dumps({k: ad.get(k) for k in ("address1", "address2", "city", "province", "zip", "country") if ad.get(k) is not None}, ensure_ascii=False))
    oc = d.get("originalCustomerAddress") or {}
    if oc:
        print("  orig: %s" % json.dumps({k: oc.get(k) for k in ("address1", "address2", "city", "province", "zip") if oc.get(k) is not None}, ensure_ascii=False))
    ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                   "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
    msl = ms if isinstance(ms, list) else (ms.get("matchers") or ms.get("matches") or [])
    print("  matchers: %d" % len(msl))
    for m in msl[:6]:
        tok = m.get("tokenizedAddress") or {}
        print("    zip=%s county=%s city=%s commune=%s street=%s | tok=%s"
              % (fv(m, "zipCode"), fv(m, "county"), fv(m, "city"), fv(m, "commune"), fv(m, "streetName"),
                 json.dumps({k: tok.get(k) for k in ("streetType", "streetName", "streetNumber") if tok.get(k)}, ensure_ascii=False)))
