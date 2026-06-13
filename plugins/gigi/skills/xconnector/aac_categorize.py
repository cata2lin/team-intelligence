# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Categorisează TOATE comenzile fără AWB cu adresă WRONG/UNKNOWN pe GT, după ce le poate face aac:
near_miss_correctable (rar) / reference / rural_no_street / no_house_number / street_unconfirmed /
no_match / other. READ-ONLY (by-id + match-address)."""
import json, sys, re, datetime
import xconnector as X

SHOP = "ix5bxc-hr.myshopify.com"
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
sh = [s for s in X.load_shops() if s["shopDomain"] == SHOP][0]
xc = X.XC(sh["apiKey"])
dto = datetime.date.today().isoformat()
dfrom = (datetime.date.today() - datetime.timedelta(days=DAYS)).isoformat()
bad = [o for o in xc.orders(dfrom, dto) if not X.has_awb(o) and o.get("addressStatus") in ("WRONG", "UNKNOWN")]

REF = ("camin", "cămin", "campus", "santier", "șantier", "internat", "cantina", "cantină")


def sc(m, f):
    x = m.get(f)
    return (x.get("score") or 0) if isinstance(x, dict) else 0


buckets = {}
rows = []
for o in bad:
    d = xc.by_id(o["orderId"])
    ad = d.get("shippingAddress") or {}
    a1 = (ad.get("address1") or ""); a2 = (ad.get("address2") or "")
    blob = (a1 + " " + a2).lower()
    ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
                   "city": ad.get("city") or "", "address1": a1, "address2": a2})
    msl = ms if isinstance(ms, list) else (ms.get("matchers") or ms.get("matches") or [])
    has_num = bool(re.search(r"\b\d+[A-Za-z]?\b", a1))
    if not msl:
        b = "no_match"
    else:
        m = msl[0]
        tok = m.get("tokenizedAddress") or {}
        st = sc(m, "streetName"); comm = sc(m, "commune")
        strong = [x for x in msl if all(sc(x, f) >= 0.95 for f in ("zipCode", "county", "city")) and sc(x, "streetName") >= 0.90]
        if any(r in blob for r in REF):
            b = "reference"
        elif len(strong) == 1 and has_num and bool(tok.get("streetNumber")):
            b = "near_miss_correctable"
        elif comm >= 0.95 and st < 0.5:
            b = "rural_no_street"
        elif not has_num:
            b = "no_house_number"
        elif st < 0.95:
            b = "street_unconfirmed"
        else:
            b = "other"
    buckets[b] = buckets.get(b, 0) + 1
    rows.append((o.get("orderName"), b, a1[:42]))

print("GT — %d comenzi fără AWB cu adresă WRONG/UNKNOWN (%dz)\n" % (len(bad), DAYS))
for b, n in sorted(buckets.items(), key=lambda x: -x[1]):
    print("  %-24s %d" % (b, n))
print()
for name, b, a1 in sorted(rows, key=lambda r: r[1]):
    print("  %-9s %-24s %s" % (name, b, a1))
