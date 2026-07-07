#!/usr/bin/env python3
"""cod-product-validator — validate a product's REAL COD unit economics (RO/EE market).

Answers: does this product make money on Cash-on-Delivery, and what CPA can I afford?
Mirrors the canonical profit_core logic (VAT per country, transport on ALL shipped,
refusal & COGS benchmarks per category). All benchmarks are editable defaults.

Usage:
  python3 validate.py --price 99 --cost 45 --country RO --size mic --pay cod
  python3 validate.py --price 269 --category skincare --country RO --size mic   # cost auto from category
  python3 validate.py --price 140 --cost 60 --pay prepay                        # prepay = ~0 refusal, no float
"""
import argparse

# ── Canonical benchmarks (mirror profit_core; edit as data evolves) ──
VAT = {"RO": .21, "BG": .20, "CZ": .21, "PL": .23, "HU": .27, "SK": .23, "HR": .25}
TRANSPORT = {"mic": 11, "mediu": 17, "mare": 25}          # ex-VAT RON / parcel
CAT = {  # category -> (COGS % of sell price, typical COD refusal rate on cold traffic)
    "skincare": (.50, .13), "cosmetice": (.50, .14), "gadget-electronic": (.40, .22),
    "gadget-casa": (.35, .18), "pet": (.35, .18), "jucarii": (.45, .15),
    "moda": (.40, .25), "accesorii": (.30, .16), "suplimente": (.30, .18),
    "parfum": (.26, .12), "altele": (.45, .18),
}


def validate(price, cost=None, category="altele", country="RO", size="mic", pay="cod", refuz=None):
    vat = VAT.get(country, .21)
    ccogs, crefuz = CAT.get(category, CAT["altele"])
    if cost is None:
        cost = price * ccogs
    if refuz is None:
        refuz = .03 if pay == "prepay" else crefuz
    tr = TRANSPORT.get(size, 11)

    sell_ex, cost_ex = price / (1 + vat), cost / (1 + vat)
    tr_deliv = tr / (1 - refuz)                    # transport paid on ALL shipped, allocated to delivered
    contrib = sell_ex - cost_ex - tr_deliv          # contribution / delivered order, BEFORE marketing
    margin = contrib / sell_ex if sell_ex else 0
    cogs_pct = cost_ex / sell_ex if sell_ex else 0
    be_cpa = contrib                                # breakeven CPA per delivered order

    if contrib <= 0:
        verdict = "SKIP — pierzi bani inainte de reclame"
    elif be_cpa < 12:
        verdict = "RISCANT — marja prea subtire pt CPA pe trafic rece"
    elif be_cpa < 22:
        verdict = "RISCANT — merge doar cu CPA disciplinat + organic"
    else:
        verdict = "TESTEAZA — are oxigen de marketing"

    flags = []
    if refuz >= .20:
        flags.append(f"refuz mare ({refuz*100:.0f}%): pe COD fiecare refuz = transport dus-intors pierdut")
    if cogs_pct > .55:
        flags.append(f"COGS ridicat ({cogs_pct*100:.0f}%): putin spatiu de reclama (tinta <45%)")
    if tr_deliv / sell_ex > .18:
        flags.append(f"transport mananca {tr_deliv/sell_ex*100:.0f}% din venit: colet prea mare pt pret")
    if price < 50:
        flags.append("AOV mic (<50): greu de scos profit dupa refuz -> gandeste BUNDLE ca sa urci cosul")
    if pay == "prepay":
        flags.append("prepay: fara float de ramburs si fara refuz -> capital minim")
    return dict(sell_ex=sell_ex, cost_ex=cost_ex, tr_deliv=tr_deliv, contrib=contrib,
                margin=margin, cogs_pct=cogs_pct, be_cpa=be_cpa, refuz=refuz, verdict=verdict, flags=flags)


def main():
    ap = argparse.ArgumentParser(description="Validate a product's COD unit economics (RO/EE).")
    ap.add_argument("--price", type=float, required=True, help="sell price incl. VAT (RON)")
    ap.add_argument("--cost", type=float, help="supplier cost/unit incl. VAT (RON); omit to estimate from --category")
    ap.add_argument("--category", default="altele", choices=list(CAT))
    ap.add_argument("--country", default="RO", choices=list(VAT))
    ap.add_argument("--size", default="mic", choices=list(TRANSPORT))
    ap.add_argument("--pay", default="cod", choices=["cod", "prepay"])
    ap.add_argument("--refuz", type=float, help="override refusal rate as fraction (e.g. 0.20)")
    a = ap.parse_args()
    r = validate(a.price, a.cost, a.category, a.country, a.size, a.pay, a.refuz)
    print(f"\n  VERDICT: {r['verdict']}")
    print(f"  CPA breakeven      : {r['be_cpa']:.1f} lei  (cat poti plati pe reclame/comanda livrata)")
    print(f"  Contributie/colet  : {r['contrib']:.1f} lei  (inainte de ads)")
    print(f"  Marja (pre-ads)    : {r['margin']*100:.0f}%   |  COGS {r['cogs_pct']*100:.0f}%  |  refuz {r['refuz']*100:.0f}%")
    print(f"  Venit ex-TVA {r['sell_ex']:.1f}  |  transport/livrat {r['tr_deliv']:.1f}")
    for f in r["flags"]:
        print(f"   - {f}")
    print()


if __name__ == "__main__":
    main()
