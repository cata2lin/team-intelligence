# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Live TikTok campaign coverage using the KB Nomenclator rules (prodmap reads KB).
  DATABASE_URL_METRICS=... uv run tk_coverage.py [last_14d|since,until]
"""
import sys, tiktok, prodmap

RNG = sys.argv[1] if len(sys.argv) > 1 else "last_14d"
BRANDS = ["Bonhaus", "Bonhaus CZ", "Reduceri bune", "Magdeal", "Esteban", "Gento", "Covoria",
          "Carpetto", "Nubra", "George Talent", "Grandia", "Belasil", "Apreciat", "Nocturna",
          "Nocturna Lux", "Ofertele Zilei", "Rossi Nails", "Ce Pat Ai"]
start, end = tiktok.daterange(RNG)
print(f"Acoperire TikTok live cu reguli KB ({start}→{end})\n{'brand':16}{'spend':>10}{'mapat%':>8}")
print("-" * 36)
g_t = g_m = 0.0
unmapped = []
for brand in BRANDS:
    try:
        accts, rows = tiktok.report_rows(brand, "campaign", start, end)
    except SystemExit:
        continue
    tot = mp = 0.0
    for r in rows:
        if not tiktok._passes(r, "campaign"):
            continue
        m = r.get("metrics", {})
        sp = tiktok._f(m, "spend") * tiktok._rate(r["_cur"])
        camp = m.get("campaign_name", "")
        tot += sp
        g = prodmap.product_of("tiktok", r["_acct"], camp)
        if g != "Unmapped" and not prodmap.is_test(camp):
            mp += sp
        elif sp > 30:
            unmapped.append((round(sp), brand, r["_acct"], camp))
    if tot:
        g_t += tot; g_m += mp
        print(f"{brand[:16]:16}{tot:>10.0f}{mp/tot*100:>7.0f}%")
print("-" * 36)
print(f"{'TOTAL':16}{g_t:>10.0f}{(g_m/g_t*100 if g_t else 0):>7.0f}%")
print("\nUnmapped TikTok (spend>30):")
for sp, b, acc, c in sorted(unmapped, reverse=True)[:25]:
    print(f"  {sp:>7} [{b}/{acc}] {c[:55]}")
