# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Live campaign coverage: which CURRENT FB campaigns map to a product_group vs are Unmapped
(= new launches not yet in the Nomenclator). Read-only.
  uv run coverage.py [last_14d] [brand1,brand2,...]
"""
import sys, json
import meta, prodmap

VER = meta.VER
RNG = sys.argv[1] if len(sys.argv) > 1 else "last_14d"
BRANDS = (sys.argv[2].split(",") if len(sys.argv) > 2 else
          ["Bonhaus", "Reduceri bune", "Magdeal", "Esteban", "Gento", "Covoria",
           "Carpetto", "Nubra", "George Talent", "Grandia", "Belasil", "Nocturna",
           "Apreciat", "Ce Pat Ai", "Ofertele Zilei", "Rossi Nails"])
start, end = meta.daterange(RNG)

unmapped_all = []
print(f"Acoperire campanii FB live ({start} → {end})\n")
print(f"{'brand':16} {'spend RON':>10} {'mapat%':>7} {'#camp':>6} {'#unmap':>7}")
print("-" * 56)
g_tot = g_map = 0.0
for brand in BRANDS:
    try:
        accts = meta.accounts_for(brand)
    except SystemExit:
        accts = []
    if not accts:
        continue
    tot = mapped = 0.0
    ncamp = nun = 0
    for ac in accts:
        rows = meta.graph(f"https://graph.facebook.com/{VER}/{ac['aid']}/insights", {
            "level": "campaign", "fields": "campaign_name,spend",
            "time_range": json.dumps({"since": start, "until": end}),
            "limit": "500", "access_token": ac["tok"]})
        for r in rows:
            sp = float(r.get("spend", 0)) * meta._rate(ac["cur"])
            camp = r.get("campaign_name", "")
            tot += sp; ncamp += 1
            grp = prodmap.product_of("facebook", ac["nm"], camp)
            if grp and grp != "Unmapped" and not prodmap.is_test(camp):
                mapped += sp
            else:
                nun += 1
                if sp > 0:
                    unmapped_all.append((round(sp), brand, ac["nm"], camp))
    if tot:
        g_tot += tot; g_map += mapped
        print(f"{brand[:16]:16} {tot:>10.0f} {mapped/tot*100:>6.0f}% {ncamp:>6} {nun:>7}")

print("-" * 56)
print(f"{'TOTAL':16} {g_tot:>10.0f} {(g_map/g_tot*100 if g_tot else 0):>6.0f}%")
print(f"\nTop campanii UNMAPPED (cu spend) — astea-s lansările noi fără regulă:")
for sp, brand, acc, camp in sorted(unmapped_all, reverse=True)[:30]:
    print(f"  {sp:>7} RON  [{brand}/{acc}]  {camp[:60]}")
