# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""
Campaign->product_group rules live in the KB (SharedClaude DB), key 'ad_campaign_rules'
in kb_meta — team-shared source of truth (not just the WMS sheet / local JSON).

  uv run kb_rules.py seed       # load current Nomenclator + gap-closing rules into KB
  uv run kb_rules.py show       # print rule counts from KB
  uv run kb_rules.py coverage [last_14d]   # live FB coverage USING KB rules
"""
import os, sys, json, re, unicodedata, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
KEY = "ad_campaign_rules"

# ---- NEW rules that close the live-coverage gap (2026-06-18) ----
NEW_RULES = {
    "facebook": [
        {"product_group": "Nubra", "map_type": "ACCOUNT", "pattern": "Nubra"},
        {"product_group": "Covoare", "map_type": "CAMPAIGN_KEYWORD", "pattern": "COVOARE"},
        # Grandia new-product test campaigns ({HAN}/{HA} NEW <produs>)
        {"product_group": "Patut co-sleeper", "map_type": "CAMPAIGN_KEYWORD", "pattern": "CO-SLEEPER"},
        {"product_group": "Patut co-sleeper", "map_type": "CAMPAIGN_KEYWORD", "pattern": "COSLEEP"},
        {"product_group": "Raft metalic", "map_type": "CAMPAIGN_KEYWORD", "pattern": "RAFT METALIC"},
        {"product_group": "Oglinda LED", "map_type": "CAMPAIGN_KEYWORD", "pattern": "OGLINDĂ LED"},
        {"product_group": "Aplica perete", "map_type": "CAMPAIGN_KEYWORD", "pattern": "APLICA PERETE"},
        {"product_group": "Aplica perete", "map_type": "CAMPAIGN_KEYWORD", "pattern": "APLICĂ PERETE"},
        {"product_group": "Pompa electrica", "map_type": "CAMPAIGN_KEYWORD", "pattern": "POMPA ELECTRICA"},
        {"product_group": "Magazie gradina", "map_type": "CAMPAIGN_KEYWORD", "pattern": "MAGAZI"},
        {"product_group": "Aparat fitness", "map_type": "CAMPAIGN_KEYWORD", "pattern": "APARAT FITNESS"},
        {"product_group": "Roti pivotante", "map_type": "CAMPAIGN_KEYWORD", "pattern": "ROTI PIVOTANTE"},
        {"product_group": "Rampe protectie", "map_type": "CAMPAIGN_KEYWORD", "pattern": "RAMPE"},
        {"product_group": "Lustre LED", "map_type": "CAMPAIGN_KEYWORD", "pattern": "LUSTRE"},
        # Bonhaus/Magdeal — produsul e in numele campaniei
        {"product_group": "Perie par animale", "map_type": "CAMPAIGN_KEYWORD", "pattern": "PERIE ANIMALE"},
        {"product_group": "Perie par animale", "map_type": "CAMPAIGN_KEYWORD", "pattern": "PERIE CU ABURI"},
        {"product_group": "Suport pantofi", "map_type": "CAMPAIGN_KEYWORD", "pattern": "FIX PANTOFI"},
        {"product_group": "Cleme", "map_type": "CAMPAIGN_KEYWORD", "pattern": "FIX CEARCEAF"},
        {"product_group": "Sort", "map_type": "CAMPAIGN_KEYWORD", "pattern": "SORT FLORAL"},
        {"product_group": "Covoras magic", "map_type": "CAMPAIGN_KEYWORD", "pattern": "COVORAS MAGIC"},
        # Grandia — alte produse noi testate pe FB
        {"product_group": "Banca antrenament", "map_type": "CAMPAIGN_KEYWORD", "pattern": "BANCA DE ANTRENAMENT"},
        {"product_group": "Scaune bar", "map_type": "CAMPAIGN_KEYWORD", "pattern": "SCAUNE BAR"},
        {"product_group": "Camera supraveghere", "map_type": "CAMPAIGN_KEYWORD", "pattern": "CAMERA SUPRAVEGHERE"},
        {"product_group": "Camera supraveghere", "map_type": "CAMPAIGN_KEYWORD", "pattern": "SUPRAVEGHERE"},
        {"product_group": "Drujba electrica", "map_type": "CAMPAIGN_KEYWORD", "pattern": "DRUJBA"},
        {"product_group": "Organizator cabluri", "map_type": "CAMPAIGN_KEYWORD", "pattern": "ORGANIZATOR DE CABLURI"},
        {"product_group": "Oglinda baie LED", "map_type": "CAMPAIGN_KEYWORD", "pattern": "OGLINDA BAIE LED"},
        {"product_group": "Panou de control", "map_type": "CAMPAIGN_KEYWORD", "pattern": "PANOU DE CONTROL"},
        {"product_group": "Biblioteca", "map_type": "CAMPAIGN_KEYWORD", "pattern": "BIBLIOTECA"},
        {"product_group": "Folie marmura", "map_type": "CAMPAIGN_KEYWORD", "pattern": "FOLIE MARMURA"},
        {"product_group": "Schimbator de caldura", "map_type": "CAMPAIGN_KEYWORD", "pattern": "SCHIMBATOR DE CALDURA"},
        {"product_group": "Cutie metalica", "map_type": "CAMPAIGN_KEYWORD", "pattern": "CUTIE METALICA"},
        {"product_group": "Mese cafea", "map_type": "CAMPAIGN_KEYWORD", "pattern": "MESE CAFEA"},
        {"product_group": "Releu inteligent", "map_type": "CAMPAIGN_KEYWORD", "pattern": "RELEU"},
        {"product_group": "Uscator de maini", "map_type": "CAMPAIGN_KEYWORD", "pattern": "USCATOR DE MAINI"},
        {"product_group": "Bicicleta fitness", "map_type": "CAMPAIGN_KEYWORD", "pattern": "BICICLETA FITNESS"},
        {"product_group": "Baterie bucatarie", "map_type": "CAMPAIGN_KEYWORD", "pattern": "BATERIE BUCATARIE"},
        {"product_group": "Set greutati", "map_type": "CAMPAIGN_KEYWORD", "pattern": "SET GREUTATI"},
        {"product_group": "Raft metalic", "map_type": "CAMPAIGN_KEYWORD", "pattern": "RAFTURI METALICE"},
    ],
    "tiktok": [
        {"product_group": "Nubra", "map_type": "ACCOUNT", "pattern": "Nubra"},
        {"product_group": "Covoare", "map_type": "CAMPAIGN_KEYWORD", "pattern": "COVOARE"},
        # campanii catalog "ALL PRODUCTS" (brand-level) pe conturile partajate
        {"product_group": "Grandia", "map_type": "CAMPAIGN_KEYWORD", "pattern": "GRANDIA"},
        {"product_group": "Magdeal", "map_type": "CAMPAIGN_KEYWORD", "pattern": "MAGDEAL"},
        {"product_group": "Reduceri bune", "map_type": "CAMPAIGN_KEYWORD", "pattern": "REDUCERI BUNE"},
        {"product_group": "Ofertele Zilei", "map_type": "CAMPAIGN_KEYWORD", "pattern": "OFERTELEZILEI"},
        {"product_group": "Ofertele Zilei", "map_type": "CAMPAIGN_KEYWORD", "pattern": "OFERTELE ZILEI"},
    ],
}


def kb_secret(k):
    return subprocess.run(["uv", "run", str(KB), "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()


def _kbconn():
    import psycopg2
    return psycopg2.connect(os.environ["KB_DATABASE_URL"], connect_timeout=12)


def load_kb_rules():
    cx = _kbconn(); cur = cx.cursor()
    cur.execute("SELECT value FROM kb_meta WHERE key=%s", (KEY,))
    r = cur.fetchone(); cx.close()
    return json.loads(r[0]) if r and r[0] else {"facebook": [], "tiktok": []}


def _dedup(rules):
    seen, out = set(), []
    for r in rules:
        k = (r.get("map_type", ""), r.get("pattern", "").strip().lower(), r.get("product_group", ""))
        if k not in seen and r.get("pattern", "").strip():
            seen.add(k); out.append(r)
    return out


def seed():
    base = {p: json.loads((HERE / "prod_rules.json").read_text()).get(p, []) for p in ("facebook", "tiktok")}
    existing = load_kb_rules()  # MERGE, don't wipe team-added rules already in KB
    merged = {p: _dedup(base.get(p, []) + NEW_RULES.get(p, []) + existing.get(p, [])) for p in ("facebook", "tiktok")}
    payload = {"version": 1, "updated_by": os.environ.get("EMPLOYEE_HANDLE", "gigi"),
               "source": "WMS Nomenclator + gap-closing rules 2026-06-18", **merged}
    cx = _kbconn(); cur = cx.cursor()
    cur.execute("""INSERT INTO kb_meta (key, value, updated_at) VALUES (%s,%s,now())
                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""",
                (KEY, json.dumps(payload, ensure_ascii=False)))
    cx.commit(); cx.close()
    print(f"KB[{KEY}] seeded: fb={len(merged['facebook'])} tt={len(merged['tiktok'])} reguli "
          f"(+{len(NEW_RULES['facebook'])} fb / +{len(NEW_RULES['tiktok'])} tt noi)")


def show():
    r = load_kb_rules()
    print(f"KB[{KEY}]: fb={len(r.get('facebook',[]))} tt={len(r.get('tiktok',[]))} | sursa: {r.get('source','?')}")


# ---- product_of reading from KB rules (mirrors prodmap.product_of) ----
def _norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def product_of_kb(rules, platform, account, campaign, ad=""):
    # AUTO: an HA-<digits> code in the campaign OR the ad name → that SKU
    m = re.search(r"(HA-\d+)", f"{campaign} {ad}", re.IGNORECASE)
    if m:
        return m.group(1).upper()
    for r in rules.get(platform, []):
        pat = _norm(r["pattern"])
        if not pat:
            continue
        target = {"ACCOUNT": account, "CAMPAIGN_KEYWORD": campaign, "AD_KEYWORD": ad,
                  "CAMPAIGN_AND_AD": f"{campaign} && {ad}"}.get(r["map_type"], "")
        if pat in _norm(target):
            return r["product_group"]
    return "Unmapped"


def coverage(rng, level="ad"):
    """level='ad' → two-stage: try campaign rule, else AD-level (AD_KEYWORD / HA-code in ad name)."""
    import meta
    rules = load_kb_rules()
    start, end = meta.daterange(rng)
    BRANDS = ["Bonhaus", "Reduceri bune", "Magdeal", "Esteban", "Gento", "Covoria", "Carpetto",
              "Nubra", "George Talent", "Grandia", "Belasil", "Apreciat", "Ofertele Zilei"]
    print(f"Acoperire FB live cu reguli KB, nivel={level} ({start}→{end})")
    print(f"{'brand':16}{'spend':>10}{'mapat%':>8}{'  via ad':>9}")
    print("-" * 44)
    g_t = g_m = g_ad = 0.0
    unmapped = []
    namef = "ad_name" if level == "ad" else "campaign_name"
    for brand in BRANDS:
        try:
            accts = meta.accounts_for(brand)
        except SystemExit:
            continue
        tot = mp = via_ad = 0.0
        for ac in accts:
            fields = f"campaign_name,{namef},spend" if level == "ad" else "campaign_name,spend"
            for r in meta.graph(f"https://graph.facebook.com/{meta.VER}/{ac['aid']}/insights",
                                {"level": level, "fields": fields,
                                 "time_range": json.dumps({"since": start, "until": end}),
                                 "limit": "800", "access_token": ac["tok"]}):
                sp = float(r.get("spend", 0)) * meta._rate(ac["cur"])
                camp = r.get("campaign_name", ""); adn = r.get("ad_name", "") if level == "ad" else ""
                tot += sp
                g = product_of_kb(rules, "facebook", ac["nm"], camp)            # campaign-level
                if g == "Unmapped" and level == "ad":
                    g = product_of_kb(rules, "facebook", ac["nm"], camp, adn)   # ad-level fallback
                    if g != "Unmapped":
                        via_ad += sp
                if g != "Unmapped":
                    mp += sp
                elif sp > 50:
                    unmapped.append((round(sp), brand, (camp[:40] + " || " + adn[:40]).strip()))
        if tot:
            g_t += tot; g_m += mp; g_ad += via_ad
            print(f"{brand[:16]:16}{tot:>10.0f}{mp/tot*100:>7.0f}%{via_ad/tot*100:>8.0f}%")
    print("-" * 44)
    print(f"{'TOTAL':16}{g_t:>10.0f}{(g_m/g_t*100 if g_t else 0):>7.0f}%{(g_ad/g_t*100 if g_t else 0):>8.0f}%")
    print("\nrămase unmapped (spend>50):")
    for sp, b, c in sorted(unmapped, reverse=True)[:25]:
        print(f"  {sp:>7} [{b}] {c[:70]}")


def addiag(brand, rng="last_14d"):
    """Print FULL campaign || ad names (untruncated) for campaign-level-unmapped ads — to see PIDs."""
    import meta
    rules = load_kb_rules()
    start, end = meta.daterange(rng)
    accts = meta.accounts_for(brand)
    rows = []
    for ac in accts:
        for r in meta.graph(f"https://graph.facebook.com/{meta.VER}/{ac['aid']}/insights",
                            {"level": "ad", "fields": "campaign_name,ad_name,spend",
                             "time_range": json.dumps({"since": start, "until": end}),
                             "limit": "800", "access_token": ac["tok"]}):
            sp = float(r.get("spend", 0)) * meta._rate(ac["cur"])
            camp = r.get("campaign_name", ""); adn = r.get("ad_name", "")
            if product_of_kb(rules, "facebook", ac["nm"], camp) == "Unmapped" and sp > 30:
                has_pid = "PID" if re.search(r"\[PID:\d+\]", adn) else ("HA" if re.search(r"HA-\d+", adn, re.I) else "")
                rows.append((round(sp), has_pid, camp, adn))
    print(f"{brand}: ad-uri unmapped la nivel campanie (spend>30)\n{'spend':>7} {'sig':3} campanie || ad")
    for sp, sig, c, a in sorted(rows, reverse=True)[:30]:
        print(f"{sp:>7} {sig:3} {c}  ||  {a}")
    npid = sum(1 for _, s, _, _ in rows if s)
    print(f"\n{npid}/{len(rows)} ad-uri unmapped au PID/HA in nume")


def unmapped(rng="last_14d", min_sp=20):
    """Full list of what stays UNMAPPED after KB rules (campaign + ad level): account, campaign, ad, spend."""
    import meta
    rules = load_kb_rules()
    start, end = meta.daterange(rng)
    BRANDS = ["Bonhaus", "Reduceri bune", "Magdeal", "Esteban", "Gento", "Covoria", "Carpetto",
              "Nubra", "George Talent", "Grandia", "Belasil", "Apreciat", "Ofertele Zilei"]
    rows = []
    for brand in BRANDS:
        try:
            accts = meta.accounts_for(brand)
        except SystemExit:
            continue
        for ac in accts:
            agg = {}
            for r in meta.graph(f"https://graph.facebook.com/{meta.VER}/{ac['aid']}/insights",
                                {"level": "ad", "fields": "campaign_name,ad_name,spend",
                                 "time_range": json.dumps({"since": start, "until": end}),
                                 "limit": "800", "access_token": ac["tok"]}):
                sp = float(r.get("spend", 0)) * meta._rate(ac["cur"])
                camp = r.get("campaign_name", ""); adn = r.get("ad_name", "")
                g = product_of_kb(rules, "facebook", ac["nm"], camp)
                if g == "Unmapped":
                    g = product_of_kb(rules, "facebook", ac["nm"], camp, adn)
                if g == "Unmapped":
                    k = (brand, ac["nm"], camp, adn)
                    agg[k] = agg.get(k, 0) + sp
            for (b, acc, c, a), sp in agg.items():
                if sp >= min_sp:
                    rows.append((round(sp), b, acc, c, a))
    tot = sum(r[0] for r in rows)
    print(f"UNMAPPED rămase ({start}→{end}), spend≥{min_sp} RON — total {round(tot)} RON / {len(rows)} ad-uri\n")
    print(f"{'spend':>7}  {'brand':10} {'ad_account':16} campanie  ||  ad")
    print("-" * 100)
    for sp, b, acc, c, a in sorted(rows, reverse=True):
        print(f"{sp:>7}  {b[:10]:10} {acc[:16]:16} {c}  ||  {a}")


def _country(acct, adname):
    a = acct.lower()
    for c in ("cz", "pl", "bg", "sk", "hu", "hr"):
        if a.endswith(c) or f"_{c}" in adname.lower() or adname.lower().startswith(c + "_"):
            return c.upper()
    return "RO"

def _lavete_kind(campaign, ad):
    t = _norm(f"{campaign} {ad}")
    if "abraziv" in t: return "Lavete abrazive"
    if "magic" in t or "laveta" in t: return "Lavete magice"
    return None

def country(rng="last_14d"):
    """HA/lavete spend split by COUNTRY (Bonhaus country accounts) × product (magice/abrazive/alt-HA/neident)."""
    import meta
    from collections import defaultdict
    start, end = meta.daterange(rng)
    agg = defaultdict(lambda: defaultdict(float))
    for brand in ["Bonhaus", "Bonhaus RO", "Bonhaus CZ", "Bonhaus PL", "Bonhaus BG", "Bonhaus SK"]:
        try: accts = meta.accounts_for(brand)
        except SystemExit: continue
        for ac in accts:
            for r in meta.graph(f"https://graph.facebook.com/{meta.VER}/{ac['aid']}/insights",
                                {"level": "ad", "fields": "campaign_name,ad_name,spend",
                                 "time_range": json.dumps({"since": start, "until": end}),
                                 "limit": "800", "access_token": ac["tok"]}):
                sp = float(r.get("spend", 0)) * meta._rate(ac["cur"])
                camp = r.get("campaign_name", ""); adn = r.get("ad_name", "")
                if "{HA" not in camp and not re.search(r"HA-?\d", camp + adn, re.I):
                    continue  # only the HA home line
                ctry = _country(ac["nm"], adn)
                kind = _lavete_kind(camp, adn) or ("alt HA" if re.search(r"HA-\d", camp+adn, re.I) else "neidentificat")
                agg[ctry][kind] += sp
    kinds = ["Lavete magice", "Lavete abrazive", "alt HA", "neidentificat"]
    print(f"HA/lavete spend pe TARI ({start}→{end}), RON\n")
    print(f"{'tara':6}" + "".join(f"{k:>17}" for k in kinds) + f"{'TOTAL':>10}")
    print("-" * 80)
    gt = defaultdict(float)
    for ctry in sorted(agg):
        row = agg[ctry]; tot = sum(row.values())
        print(f"{ctry:6}" + "".join(f"{round(row.get(k,0)):>17}" for k in kinds) + f"{round(tot):>10}")
        for k in kinds: gt[k] += row.get(k, 0)
    print("-" * 80)
    print(f"{'TOT':6}" + "".join(f"{round(gt[k]):>17}" for k in kinds) + f"{round(sum(gt.values())):>10}")


def coverage100(rng="2026-05-20,2026-06-03"):
    """Reconciled coverage: KB name-rules + AWBprint per-SKU HA line ({HA*}-agency campaigns).
    Default window = covered by AWBprint sku_ad_spend_daily (stops 3 iun)."""
    import meta, os, psycopg2
    rules = load_kb_rules()
    start, end = meta.daterange(rng)
    BRANDS = ["Bonhaus", "Bonhaus RO", "Bonhaus CZ", "Bonhaus PL", "Bonhaus BG", "Bonhaus SK",
              "Reduceri bune", "Magdeal", "Esteban", "Gento", "Covoria", "Carpetto",
              "Nubra", "George Talent", "Grandia", "Belasil", "Apreciat", "Ofertele Zilei"]
    print(f"Coverage RECONCILIAT (nume KB + AWBprint HA) {start}→{end}")
    print(f"{'brand':16}{'spend':>10}{'nume%':>7}{'+HA-awb%':>9}{'=total%':>9}")
    print("-" * 51)
    g_t = g_name = g_ha = 0.0
    real_unmapped = []
    for brand in BRANDS:
        try: accts = meta.accounts_for(brand)
        except SystemExit: continue
        tot = nm = ha = 0.0
        for ac in accts:
            for r in meta.graph(f"https://graph.facebook.com/{meta.VER}/{ac['aid']}/insights",
                                {"level": "ad", "fields": "campaign_name,ad_name,spend",
                                 "time_range": json.dumps({"since": start, "until": end}),
                                 "limit": "800", "access_token": ac["tok"]}):
                sp = float(r.get("spend", 0)) * meta._rate(ac["cur"])
                camp = r.get("campaign_name", ""); adn = r.get("ad_name", "")
                tot += sp
                g = product_of_kb(rules, "facebook", ac["nm"], camp, adn)
                if g != "Unmapped":
                    nm += sp
                elif re.search(r"\{HA[A-Z]?\}", camp):   # agency-HA tag → mapped per-SKU by AWBprint
                    ha += sp
                elif sp > 50:
                    real_unmapped.append((round(sp), brand, camp, adn))
        if tot:
            g_t += tot; g_name += nm; g_ha += ha
            print(f"{brand[:16]:16}{tot:>10.0f}{nm/tot*100:>6.0f}%{ha/tot*100:>8.0f}%{(nm+ha)/tot*100:>8.0f}%")
    print("-" * 51)
    cov = (g_name + g_ha) / g_t * 100 if g_t else 0
    print(f"{'TOTAL':16}{g_t:>10.0f}{g_name/g_t*100:>6.0f}%{g_ha/g_t*100:>8.0f}%{cov:>8.0f}%")
    # corroborate the HA credit against AWBprint sku_ad_spend_daily for the same window
    try:
        dsn = os.environ["DATABASE_URL_AWBPRINT"]
        dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
        dsn = re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")
        cx = psycopg2.connect(dsn); c = cx.cursor()
        c.execute("SELECT round(sum(amount_fb_ron)), count(distinct sku) FROM sku_ad_spend_daily WHERE date BETWEEN %s AND %s", (start, end))
        fbtot, nsku = c.fetchone(); cx.close()
        print(f"\nAWBprint HA per-SKU în fereastră: FB {fbtot} RON pe {nsku} SKU-uri (sursa care acoperă creditul HA de {round(g_ha)} RON)")
    except Exception as e:
        print("AWBprint corroborare indisponibilă:", str(e)[:60])
    print(f"\nREAL unmapped rămas (non-HA, spend>50): {round(sum(x[0] for x in real_unmapped))} RON / {len(real_unmapped)} ad-uri")
    for sp, b, c_, a in sorted(real_unmapped, reverse=True)[:12]:
        print(f"  {sp:>6} [{b}] {c_[:42]} || {a[:24]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "seed": seed()
    elif cmd == "coverage100": coverage100(sys.argv[2] if len(sys.argv) > 2 else "2026-05-20,2026-06-03")
    elif cmd == "show": show()
    elif cmd == "coverage": coverage(sys.argv[2] if len(sys.argv) > 2 else "last_14d")
    elif cmd == "addiag": addiag(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "last_14d")
    elif cmd == "unmapped": unmapped(sys.argv[2] if len(sys.argv) > 2 else "last_14d")
    elif cmd == "country": country(sys.argv[2] if len(sys.argv) > 2 else "last_14d")
