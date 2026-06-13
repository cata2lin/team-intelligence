# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""aac pe O comandă (după orderName): by-id + TOȚI matcherii + evaluarea porților aac
(candidat unic ≥0.95 pe core + număr păstrat + /zip-code confirmă) + payload would-apply.
Cu `--apply` cheamă ai-correct-address (DOAR dacă trece porțile) + verifică post-apply. """
import json, sys, re, datetime
import xconnector as X

SHOP = "ix5bxc-hr.myshopify.com"
NAME = sys.argv[1]
APPLY = "--apply" in sys.argv
sh = [s for s in X.load_shops() if s["shopDomain"] == SHOP][0]
xc = X.XC(sh["apiKey"])
dto = datetime.date.today().isoformat()
dfrom = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
o = next((x for x in xc.orders(dfrom, dto) if x.get("orderName") == NAME), None)
if not o:
    print("Nu găsesc", NAME); sys.exit(1)


def fv(m, f):
    x = m.get(f)
    return (x.get("value"), x.get("score") or 0) if isinstance(x, dict) else (None, 0)


d = xc.by_id(o["orderId"])
ad = d.get("shippingAddress") or {}
st = d.get("addressStatus")
print("%s id=%s status=%s has_awb=%s" % (NAME, o["orderId"], st, X.has_awb(o)))
print("ship:", json.dumps(ad, ensure_ascii=False))
ms = xc.match({"country": "Romania", "zipCode": ad.get("zip") or "", "county": ad.get("province") or "",
               "city": ad.get("city") or "", "address1": ad.get("address1") or "", "address2": ad.get("address2") or ""})
msl = ms if isinstance(ms, list) else (ms.get("matchers") or ms.get("matches") or [])
print("matchers:", len(msl))
for m in msl:
    print("  zip=%s%.3f county=%s%.3f city=%s%.3f commune=%s%.3f street=%s%.3f tok=%s" % (
        fv(m, "zipCode")[0], fv(m, "zipCode")[1], fv(m, "county")[0], fv(m, "county")[1],
        fv(m, "city")[0], fv(m, "city")[1], fv(m, "commune")[0], fv(m, "commune")[1],
        fv(m, "streetName")[0], fv(m, "streetName")[1], json.dumps(m.get("tokenizedAddress") or {}, ensure_ascii=False)))

# porțile aac (conservator)
ST_MIN = 0.90  # relaxat (aprobat de user; ex GT43675 „Trestioara" 0.919 vs „Trestioarei" = aceeași stradă, genitiv)
strong = [m for m in msl if all(fv(m, f)[1] >= 0.95 for f in ("zipCode", "county", "city")) and fv(m, "streetName")[1] >= ST_MIN]
print("\nPORȚI AAC (street relaxat la %.2f):" % ST_MIN)
print("  candidați zip/oraș/județ≥0.95 + street≥%.2f: %d" % (ST_MIN, len(strong)))
if len(strong) != 1:
    print("  → manual_review (nu exact 1 candidat tare)"); sys.exit(0)
m = strong[0]
nums = re.findall(r"\b(\d+[A-Za-z]?)\b", ad.get("address1") or "")
tok = m.get("tokenizedAddress") or {}
num = (tok.get("streetNumber") or "").strip() or (nums[0] if len(nums) == 1 else "")
print("  număr casă:", num or "INCERT", "| din original:", nums)
if not num or (nums and num not in nums):
    print("  → manual_review (număr incert)"); sys.exit(0)
zc = X.zip_confirm(xc, fv(m, "zipCode")[0])
print("  /zip-code confirmă:", bool(zc), (json.dumps(zc, ensure_ascii=False)[:120] if zc else ""))
if not zc:
    print("  → manual_review (zip neconfirmat)"); sys.exit(0)
print("  ✅ TRECE toate porțile → auto-corectabil")
st2, applied, detail = X.correct_address(xc, o, SHOP, apply=APPLY)
print("\nREZULTAT:", st2, "→", detail)
print("appliedShippingAddress:", json.dumps(applied, ensure_ascii=False))
if APPLY and st2 == "corrected":
    import time
    for i in range(4):
        time.sleep(6)
        d2 = xc.by_id(o["orderId"])
        print("  post-apply status:", d2.get("addressStatus"))
        if d2.get("addressStatus") == "VALID":
            break
