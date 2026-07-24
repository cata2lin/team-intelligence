"""
mapping_admin.py — consola DB-NATIVE de mapare campanie/SKU -> grup pentru CPA per produs.

DE CE: maparea CPA (ce spend intra pe ce produs) e o metrica sensibila pe bani. Sursa de adevar =
DB (profitability.db), NU sheet-ul. Acest tool face maparea CLARA, AUDITABILA si EDITABILA din DB:
  - vezi regulile (rules), grupurile (groups)
  - explica de ce o campanie cade pe un grup (resolve)  -> "sa stii cum se face"
  - auditeaza acoperirea: nemapat + coverage% (audit = calea WMS/DB; audit-cache = calea cache/metrics)
  - adauga / sterge reguli persistente in DB (add-rule / rm-rule) fara sa atingi sheet-ul

NU MINTE: importa direct logica reala de potrivire din wms_marketing.py (_load_nomen/_group_of/
_SKU_IN_CAMP/_load_fx). Ce vezi aici = exact ce aloca profit_by_sku pe calea WMS (>= cutover).

Precedenta reala (calea WMS, in wms_marketing._group_of + priority 0):
  (0) cod SKU EXACT 'HA-####' in campanie/ad, daca e SKU vandut -> direct pe acel SKU
  (1) CAMPAIGN_KEYWORD  (substring normalizat fara diacritice in 'campanie + ad'; cel mai LUNG castiga)
  (2) ACCOUNT           (egalitate exacta pe nume cont, lowercase)
  ! AD_KEYWORD / CAMPAIGN_AND_AD din wms_nomen sunt INERTE pe calea WMS (wms_marketing nu le incarca).

Rulare: cd /root/Scripturi && set -a; . /root/ad-spend/run.env; . .env; set +a
        .venv/bin/python mapping_admin.py <cmd> ...
"""
import os, re, sys, json, argparse, sqlite3
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB_DEFAULT = os.environ.get("PROFITABILITY_DB", "/root/Scripturi/data/profitability.db")

# reuse EXACT matching logic (single source of truth) — nu re-implementam
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wms_marketing as wm  # noqa: E402

ACTIVE_TYPES = ("ACCOUNT", "CAMPAIGN_KEYWORD")          # onorate de calea WMS
INERT_TYPES = ("AD_KEYWORD", "CAMPAIGN_AND_AD")         # in wms_nomen (din sheet) dar ignorate de wms_marketing


def _clean_dsn(dsn):
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def _metrics_cur():
    import psycopg2
    return psycopg2.connect(_clean_dsn(os.environ["DATABASE_URL_METRICS"])).cursor()


def _conn(db):
    c = sqlite3.connect(db)
    c.execute("PRAGMA busy_timeout=8000;")
    return c


def _ha_skus(conn):
    return set(s.strip().upper() for (s,) in conn.execute(
        "SELECT DISTINCT sku FROM profit_order_lines WHERE sku LIKE 'HA-%'") if s)


def _explain(acc, key, ha, plat, account, campaign, ad):
    """Oglinda lui wms_marketing (priority 0 + _group_of) DAR intoarce (grup, regula_text)."""
    text = ((campaign or "") + " " + (ad or "")).upper()
    exact = next((m for m in wm._SKU_IN_CAMP.findall(text) if m in ha), None)
    if exact:
        return exact, "(0) cod SKU exact in campanie/ad: %s" % exact
    c = wm._norm((campaign or "") + " " + (ad or ""))
    for p, g in key[plat]:
        if p and p in c:
            return g, "(1) CAMPAIGN_KEYWORD '%s'" % p
    a = (account or "").strip().lower()
    for p, g in acc[plat]:
        if a == p:
            return g, "(2) ACCOUNT '%s'" % p
    return None, "NEMAPAT (nicio regula)"


# ------------------------------------------------------------------ rules
def cmd_rules(a):
    conn = _conn(a.db)
    rows = list(conn.execute(
        "SELECT platform,map_type,pattern,product_group,'sheet' AS src FROM wms_nomen "
        "UNION ALL SELECT platform,map_type,pattern,product_group,'extra' FROM wms_nomen_extra"))
    conn.close()
    if a.platform:
        rows = [r for r in rows if r[0] == a.platform]
    by = defaultdict(list)
    for plat, mt, pat, grp, src in rows:
        by[(plat, mt)].append((pat, grp, src))
    print("REGULI campanie->grup (wms_nomen + wms_nomen_extra)  [%d total]" % len(rows))
    for (plat, mt) in sorted(by, key=lambda k: (k[0], k[1])):
        tag = "  ACTIV" if mt in ACTIVE_TYPES else "  INERT pe calea WMS (ignorat de wms_marketing)"
        items = by[(plat, mt)]
        print("\n[%s] %s  (%d)%s" % (plat.upper(), mt, len(items), tag))
        for pat, grp, src in sorted(items, key=lambda x: (x[1] or "", x[0] or "")):
            mark = "*" if src == "extra" else " "
            print("   %s %-38s -> %s" % (mark, (pat or "")[:38], grp))
    print("\n  (* = din wms_nomen_extra, editabil in DB via add-rule/rm-rule)")


# ------------------------------------------------------------------ groups
def cmd_groups(a):
    conn = _conn(a.db)
    cnt = defaultdict(int)
    for grp, in conn.execute("SELECT grp FROM wms_product_group WHERE grp IS NOT NULL AND grp<>''"):
        cnt[grp] += 1
    ex = defaultdict(int)
    for grp, in conn.execute("SELECT grp FROM wms_product_group_extra WHERE grp IS NOT NULL AND grp<>''"):
        ex[grp] += 1
    conn.close()
    allg = sorted(set(cnt) | set(ex))
    print("GRUPURI de produs (%d)  |  SKU in Product Group (sheet)  +  SKU in _extra (brand/orders)" % len(allg))
    for g in allg:
        print("   %-24s  %4d  + %d" % (g, cnt.get(g, 0), ex.get(g, 0)))


# ------------------------------------------------------------------ resolve
def cmd_resolve(a):
    conn = _conn(a.db)
    acc, key = wm._load_nomen(conn)
    ha = _ha_skus(conn)
    conn.close()
    grp, rule = _explain(acc, key, ha, a.platform, a.account or "", a.campaign or "", a.ad or "")
    print("platform : %s" % a.platform)
    print("account  : %s" % (a.account or ""))
    print("campaign : %s" % (a.campaign or ""))
    print("ad       : %s" % (a.ad or ""))
    print("-> GRUP  : %s" % (grp or "NEMAPAT"))
    print("-> regula: %s" % rule)


# ------------------------------------------------------------------ audit (WMS path, DB)
def cmd_audit(a):
    conn = _conn(a.db)
    acc, key = wm._load_nomen(conn)
    ha = _ha_skus(conn)
    fx = wm._load_fx(_metrics_cur())
    lo, hi = a.frm, a.to
    plats = [a.platform] if a.platform else ["fb", "tt"]
    q = ("SELECT source,date,account,campaign,ad_name,spend_usd FROM wms_ad_spend "
         "WHERE date>=? AND date<=?" + (" AND source=?" if a.platform else ""))
    args = (lo, hi, a.platform) if a.platform else (lo, hi)
    tot = 0.0; mapped = 0.0; test = 0.0
    unm = defaultdict(float)   # (account, campaign) -> ron nemapat
    grp_ron = defaultdict(float)
    for src, date, account, campaign, ad, spend in conn.execute(q, args):
        ron = (spend or 0) * wm._usd_ron(fx, date)
        if ron <= 0:
            continue
        tot += ron
        grp, _ = _explain(acc, key, ha, src, account, campaign, ad)
        if grp is None:
            unm[(account or "", campaign or "")] += ron
        elif str(grp).strip().lower() == "test":
            test += ron; mapped += ron
        else:
            mapped += ron; grp_ron[grp] += ron
    conn.close()
    unm_tot = tot - mapped
    print("== AUDIT MAPARE — calea WMS (wms_ad_spend, DB)  %s .. %s  [%s] ==" % (lo, hi, "+".join(plats)))
    print("  spend total   : %12.0f RON" % tot)
    print("  MAPAT         : %12.0f RON  (%.1f%%)  din care 'test' %.0f" % (mapped, 100*mapped/tot if tot else 0, test))
    print("  NEMAPAT       : %12.0f RON  (%.1f%%)" % (unm_tot, 100*unm_tot/tot if tot else 0))
    print("\n  TOP %d perechi (cont, campanie) NEMAPATE — aici adaugi reguli:" % a.top)
    for (account, campaign), ron in sorted(unm.items(), key=lambda x: -x[1])[:a.top]:
        print("   %9.0f RON | %-22s | %s" % (ron, account[:22], campaign[:60]))
    print("\n  TOP grupuri MAPATE (unde a cazut spend-ul):")
    for grp, ron in sorted(grp_ron.items(), key=lambda x: -x[1])[:12]:
        print("   %9.0f RON | %s" % (ron, grp))


# ------------------------------------------------------------------ audit-cache (metrics path)
def cmd_audit_cache(a):
    cur = _metrics_cur()
    lo, hi = a.frm, a.to
    cur.execute(
        "SELECT CASE WHEN sku LIKE 'UNMAPPED:%%' THEN 'UNMAPPED' ELSE 'mapped' END cls, platform, "
        "round(sum(spend_ron)::numeric,0) FROM cache.product_ad_spend "
        "WHERE date>=%s AND date<=%s GROUP BY 1,2 ORDER BY 2,1", (lo, hi))
    rows = cur.fetchall()
    tot = sum(float(r[2]) for r in rows)
    unm = sum(float(r[2]) for r in rows if r[0] == "UNMAPPED")
    print("== AUDIT MAPARE — calea CACHE (cache.product_ad_spend, metrics)  %s .. %s ==" % (lo, hi))
    print("  spend total   : %12.0f RON" % tot)
    print("  NEMAPAT       : %12.0f RON  (%.1f%%)" % (unm, 100*unm/tot if tot else 0))
    for cls, plat, ron in rows:
        print("     %-8s %-8s %12.0f RON" % (cls, plat, float(ron)))
    print("\n  TOP bucket-uri UNMAPPED:<brand> (unde lipsesc reguli):")
    cur.execute(
        "SELECT sku, round(sum(spend_ron)::numeric,0) r FROM cache.product_ad_spend "
        "WHERE date>=%s AND date<=%s AND sku LIKE 'UNMAPPED:%%' GROUP BY 1 ORDER BY 2 DESC LIMIT %s",
        (lo, hi, a.top))
    for sku, ron in cur.fetchall():
        print("   %9.0f RON | %s" % (float(ron), sku))


# ------------------------------------------------------------------ by-account (split TARA -> magazin)
# Atribuire cont->magazin din MAPAREA AUTORITATIVA a echipei: sheet „CPA si financiar", tab „Mapping"
# (Brand · Conturi FB · Conturi TT · ... · Campanie=TOKEN). Rezolva conturile PARTAJATE prin TOKEN:
# un cont folosit de mai multe branduri (ex ROSSI Nails = GT/Magdeal/Reduceri) → brandul dupa token in campanie.
MAPPING_SS = "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"
# brand (lower) -> prefix magazin in profit_order_lines
BRAND_PREFIX = {
    "ofertele zilei": "OFER", "magdeal": "MAG", "george talent": "GT", "esteban": "EST",
    "reduceri bune": "RED", "bonhaus ro": "BON", "bonhaus cz": "CZ", "bonhaus pl": "PL",
    "bonhaus bg": "BONBG", "bonhaus": "BON", "nubra": "NUB", "gento": "GEN", "belasil": "BELA",
    "grandia": "GRAN", "nocturna": "NOC", "nocturna lux": "LUX", "rossi nails": "ROSSI",
    "carpetto": "CARP", "apreciat": "APR", "covoria": "COV", "lab noir": "LAB", "ce pat ai": "CEPAT",
    "genti promo": "GENTI", "esteban parfum": "EST",
}
PREFIX_COUNTRY = {"CZ": "CZ", "PL": "PL", "BONBG": "BG", "NOCBG": "BG", "BG": "BG"}
COUNTRY_ORDER = ["RO", "CZ", "BG", "PL", "SK", "HR", "HU", "?"]
PREFIX_LABEL = {"OFER": "Ofertele", "CZ": "Bonhaus CZ", "BON": "Bonhaus RO", "PL": "Bonhaus PL",
                "BONBG": "Bonhaus BG", "RED": "Reduceri", "MAG": "Magdeal", "NUB": "Nubra",
                "EST": "Esteban", "GT": "George Talent", "GEN": "Gento", "BELA": "Belasil",
                "GRAN": "Grandia", "APR": "Apreciat", "COV": "Covoria", "LAB": "Labnoir",
                "LUX": "Nocturna Lux", "NOC": "Nocturna", "ROSSI": "Rossi", "CARP": "Carpetto",
                "CEPAT": "Ce Pat Ai", "GENTI": "Genti Promo", "?": "(necunoscut)"}
_CC = re.compile(r"\b(CZ|PL|BG|SK|HR|HU)\b")


def _brand_country(brand):
    m = _CC.search((brand or "").upper())
    return m.group(1) if m else "RO"


def _load_store_mapping():
    """Citeste tab-ul „Mapping" (CPA si financiar) → [(brand, conturi_set_lower, token_norm)].
    Sursa AUTORITATIVA a echipei pt cont→brand + token de disambiguare pe conturi partajate."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    sa = json.loads(os.environ["GA4_SA_JSON"])
    cr = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    api = build("sheets", "v4", credentials=cr, cache_discovery=False).spreadsheets()
    rows = api.values().get(spreadsheetId=MAPPING_SS, range="'Mapping'!A:G").execute().get("values", [])
    out = []
    for r in rows[1:]:
        def g(i): return (r[i].strip() if i < len(r) and r[i] else "")
        brand = g(0)
        if not brand:
            continue
        accts = set()
        for col in (1, 2):  # FB + TT
            for x in re.split(r"[,\n]", g(col)):
                x = x.strip().lower()
                if x:
                    accts.add(x)
        out.append((brand, accts, wm._norm(g(5))))
    return out


def _store_resolver():
    """Returneaza f(account,campaign,ad) -> (tara, prefix). Foloseste maparea autoritativa:
    brandul = contul se potriveste SI (token gol SAU token in campanie); token ne-gol = prioritar (conturi partajate)."""
    try:
        mp = _load_store_mapping()
    except Exception as e:
        sys.stderr.write("[by-account] Mapping (CPA si financiar) indisponibil (%s: %s) -> tot '?'\n" % (type(e).__name__, str(e)[:80]))
        return lambda account, campaign, ad: ("?", "?")
    def resolve(account, campaign, ad):
        a = (account or "").strip().lower()
        t = wm._norm((campaign or "") + " " + (ad or ""))
        cand = [(brand, tok) for brand, accts, tok in mp if a in accts]
        if not cand:
            return ("?", "?")
        # 1) token ne-gol care se potriveste in campanie (cel mai specific — conturi partajate)
        hit = [(b, tok) for b, tok in cand if tok and tok in t]
        if hit:
            hit.sort(key=lambda x: -len(x[1])); brand = hit[0][0]
        else:
            # 2) un singur brand pe cont (dedicat) → el; altfel brand cu token GOL; altfel necunoscut
            notok = [b for b, tok in cand if not tok]
            if len(cand) == 1:
                brand = cand[0][0]
            elif len(notok) == 1:
                brand = notok[0]
            else:
                return ("?", "?")   # cont partajat, campanie fara token → NU ghicim
        return (_brand_country(brand), BRAND_PREFIX.get(brand.strip().lower(), "?"))
    return resolve


def _months(lo, hi):
    out = []; y, m = int(lo[:4]), int(lo[5:7]); ey, em = int(hi[:4]), int(hi[5:7])
    while (y, m) <= (ey, em):
        out.append("%04d-%02d" % (y, m)); m += 1
        if m > 12: y += 1; m = 1
    return out


def cmd_by_account(a):
    """SPLIT ierarhic TARA (RO/CZ/BG/PL) -> MAGAZIN. Acelasi produs (ex lavete) vandut pe mai multe
    conturi/tari: il vezi intai pe tara (RO adunat), apoi pe magazin in RO. spend + comenzi + CPA/comanda."""
    conn = _conn(a.db)
    acc, key = wm._load_nomen(conn); ha = _ha_skus(conn); fx = wm._load_fx(_metrics_cur())
    store_of = _store_resolver()               # maparea autoritativa (CPA si financiar / Mapping)
    lo, hi = a.frm, a.to
    spend = defaultdict(float)                 # (group, country, prefix) -> ron
    for src, date, account, campaign, ad, sp in conn.execute(
        "SELECT source,date,account,campaign,ad_name,spend_usd FROM wms_ad_spend WHERE date>=? AND date<=?", (lo, hi)):
        ron = (sp or 0) * wm._usd_ron(fx, date)
        if ron <= 0: continue
        grp, _ = _explain(acc, key, ha, src, account, campaign, ad)
        if grp is None or str(grp).strip().lower() == "test": continue
        if a.group and str(grp).strip().lower() != a.group.strip().lower(): continue
        ctry, pfx = store_of(account, campaign, ad)
        spend[(grp, ctry, pfx)] += ron
    months = _months(lo, hi); ph = ",".join("?" * len(months))
    g2sku = defaultdict(set)
    for sku, grp in conn.execute("SELECT sku, grp FROM wms_product_group WHERE grp IS NOT NULL AND grp<>''"):
        g2sku[grp.strip()].add((sku or "").strip())
    def orders_by_prefix(grp):
        sk = [s for s in g2sku.get(grp, set()) if s]
        if not sk: return {}
        q = ("SELECT prefix, COUNT(DISTINCT prefix||'|'||order_name) FROM profit_order_lines "
             "WHERE month IN (%s) AND sku IN (%s) GROUP BY prefix" % (ph, ",".join("?" * len(sk))))
        return {p: n for p, n in conn.execute(q, months + sk)}
    def cc(pfx): return PREFIX_COUNTRY.get(pfx, "RO")
    groups = sorted(set(g for g, _, _ in spend), key=lambda g: -sum(v for (gg, _, _), v in spend.items() if gg == g))
    print("== SPLIT TARA -> MAGAZIN  %s .. %s  (magazin din maparea AUTORITATIVA 'CPA si financiar'/Mapping: cont+token) ==" % (lo, hi))
    for g in groups:
        obp = orders_by_prefix(g)                                     # prefix -> comenzi
        detail = defaultdict(float)                                   # (country, prefix) -> spend
        for (gg, ctry, pfx), v in spend.items():
            if gg == g: detail[(ctry, pfx)] += v
        tot = sum(detail.values())
        print("\n### %s  (spend total %.0f RON)" % (g, tot))
        by_ctry = defaultdict(float)
        for (ctry, pfx), v in detail.items(): by_ctry[ctry] += v
        for ctry in sorted(by_ctry, key=lambda c: (COUNTRY_ORDER.index(c) if c in COUNTRY_ORDER else 99)):
            oc_ctry = sum(n for p, n in obp.items() if cc(p) == ctry)
            sp_ctry = by_ctry[ctry]
            cpa = ("%.1f" % (sp_ctry / oc_ctry)) if oc_ctry else "—"
            print("  ── TARA %-3s   spend %9.0f   comenzi %6d   CPA %s" % (ctry, sp_ctry, oc_ctry, cpa))
            for (c2, pfx), v in sorted([(k, v) for k, v in detail.items() if k[0] == ctry], key=lambda x: -x[1]):
                oc = obp.get(pfx, 0); c = ("%.1f" % (v / oc)) if oc else "—"
                print("       %-16s %-7s %9.0f   com %6d   CPA %s" % (PREFIX_LABEL.get(pfx, pfx)[:16], pfx, v, oc, c))
    conn.close()


# ------------------------------------------------------------------ lint (mis-mapare)
def cmd_lint(a):
    """Depisteaza reguli PROST facute (sa nu fie 'mapat prost'): conflicte, keyword-uri prea scurte/greedy,
    reguli umbrite (substring al alteia mai lunga), conturi mapate ca intreg (risc multi-produs)."""
    conn = _conn(a.db)
    rows = list(conn.execute(
        "SELECT platform,map_type,pattern,product_group FROM wms_nomen "
        "UNION ALL SELECT platform,map_type,pattern,product_group FROM wms_nomen_extra"))
    conn.close()
    issues = 0
    # 1) CONFLICT: acelasi (platform, tip, pattern) -> grupuri diferite
    seen = defaultdict(set)
    for plat, mt, pat, grp in rows:
        seen[(plat, mt, (pat or "").strip().upper())].add(grp)
    print("== LINT MAPARE (mis-mapare) ==")
    print("\n[1] CONFLICTE (acelasi pattern -> grupuri diferite) — CRITIC:")
    for (plat, mt, pat), grps in sorted(seen.items()):
        if len(grps) > 1:
            issues += 1; print("   ! [%s] %s '%s' -> %s" % (plat, mt, pat, sorted(grps)))
    if not any(len(g) > 1 for g in seen.values()):
        print("   (niciun conflict)")
    # 2) keyword-uri scurte (risc greedy substring)
    print("\n[2] CAMPAIGN_KEYWORD prea SCURTE (<5 car) — risc sa prinda campanii nelegate:")
    short = [(p, pat, g) for p, mt, pat, g in rows if mt == "CAMPAIGN_KEYWORD" and len((pat or "").strip()) < 5]
    for p, pat, g in sorted(short):
        issues += 1; print("   ~ [%s] '%s' -> %s" % (p, pat, g))
    if not short:
        print("   (niciunul)")
    # 3) reguli UMBRITE: un keyword e substring al altuia mai lung (acelasi platform) -> cel scurt poate sa nu prinda
    print("\n[3] KEYWORD UMBRIT (substring al altuia mai lung, acelasi platform) — poate nu se declanseaza:")
    kw = defaultdict(list)
    for p, mt, pat, g in rows:
        if mt == "CAMPAIGN_KEYWORD":
            kw[p].append((wm._norm(pat), g))
    shadow = 0
    for p, lst in kw.items():
        for a1, g1 in lst:
            for a2, g2 in lst:
                if a1 != a2 and a1 and a2 and a1 in a2 and g1 != g2:
                    shadow += 1; issues += 1
                    print("   ~ [%s] '%s'->%s e substring al '%s'->%s" % (p, a1, g1, a2, g2))
    if not shadow:
        print("   (niciunul relevant)")
    # 4) ACCOUNT rules (intreg contul -> un grup): risc daca contul e multi-produs
    print("\n[4] ACCOUNT rules (intreg contul -> un grup) — verifica ca fiecare cont e mono-categorie:")
    for p, mt, pat, g in sorted([r for r in rows if r[1] == "ACCOUNT"]):
        print("   . [%s] cont '%s' -> %s" % (p, pat, g))
    print("\n  TOTAL semnale: %d (conflictele = CRITIC; restul = de verificat manual)" % issues)


# ------------------------------------------------------------------ add-rule / rm-rule
def _ensure_extra(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS wms_nomen_extra "
                 "(platform TEXT, product_group TEXT, map_type TEXT, pattern TEXT)")


def cmd_add_rule(a):
    mt = a.type.upper()
    if mt not in ACTIVE_TYPES:
        print("REFUZ: map_type '%s' e INERT pe calea WMS (wms_marketing onoreaza doar %s). "
              "Foloseste ACCOUNT sau CAMPAIGN_KEYWORD ca regula sa AIBA efect." % (mt, "/".join(ACTIVE_TYPES)))
        sys.exit(2)
    if a.platform not in ("fb", "tt"):
        print("REFUZ: platform trebuie fb sau tt."); sys.exit(2)
    conn = _conn(a.db); _ensure_extra(conn)
    exists = conn.execute("SELECT COUNT(*) FROM wms_nomen_extra WHERE platform=? AND map_type=? AND "
                          "upper(pattern)=upper(?)", (a.platform, mt, a.pattern)).fetchone()[0]
    if exists:
        print("Exista deja o regula %s/%s pattern='%s'. Nimic de facut." % (a.platform, mt, a.pattern))
        conn.close(); return
    if not a.apply:
        print("DRY-RUN (fara --apply). Ar adauga: [%s] %s '%s' -> %s"
              % (a.platform, mt, a.pattern, a.group))
        conn.close(); return
    conn.execute("INSERT INTO wms_nomen_extra (platform,product_group,map_type,pattern) VALUES (?,?,?,?)",
                 (a.platform, a.group, mt, a.pattern))
    conn.commit(); conn.close()
    print("ADAUGAT in wms_nomen_extra: [%s] %s '%s' -> %s" % (a.platform, mt, a.pattern, a.group))


def cmd_rm_rule(a):
    conn = _conn(a.db); _ensure_extra(conn)
    where = "platform=? AND upper(pattern)=upper(?)"; args = [a.platform, a.pattern]
    if a.type:
        where += " AND map_type=?"; args.append(a.type.upper())
    found = conn.execute("SELECT platform,map_type,pattern,product_group FROM wms_nomen_extra WHERE " + where, args).fetchall()
    if not found:
        print("Nicio regula extra care sa se potriveasca."); conn.close(); return
    for r in found:
        print("  match: [%s] %s '%s' -> %s" % (r[0], r[1], r[2], r[3]))
    if not a.apply:
        print("DRY-RUN (fara --apply). Ar sterge %d regula(i)." % len(found)); conn.close(); return
    conn.execute("DELETE FROM wms_nomen_extra WHERE " + where, args)
    conn.commit(); conn.close()
    print("STERS %d regula(i) din wms_nomen_extra." % len(found))


def main():
    ap = argparse.ArgumentParser(description="Consola DB-native de mapare CPA per produs.")
    ap.add_argument("--db", default=DB_DEFAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rules", help="listeaza regulile campanie->grup"); p.add_argument("--platform", choices=["fb", "tt"]); p.set_defaults(f=cmd_rules)
    p = sub.add_parser("groups", help="listeaza grupurile + nr SKU"); p.set_defaults(f=cmd_groups)
    p = sub.add_parser("resolve", help="explica ce grup ia o campanie (logica WMS)")
    p.add_argument("--platform", choices=["fb", "tt"], required=True); p.add_argument("--account", default="")
    p.add_argument("--campaign", default=""); p.add_argument("--ad", default=""); p.set_defaults(f=cmd_resolve)
    p = sub.add_parser("audit", help="acoperire mapare pe calea WMS (wms_ad_spend, DB)")
    p.add_argument("--from", dest="frm", required=True); p.add_argument("--to", required=True)
    p.add_argument("--platform", choices=["fb", "tt"]); p.add_argument("--top", type=int, default=20); p.set_defaults(f=cmd_audit)
    p = sub.add_parser("audit-cache", help="acoperire mapare pe calea cache (metrics)")
    p.add_argument("--from", dest="frm", required=True); p.add_argument("--to", required=True)
    p.add_argument("--top", type=int, default=20); p.set_defaults(f=cmd_audit_cache)
    p = sub.add_parser("by-account", help="split TARA->magazin: spend+comenzi+CPA per produs pe cont/tara")
    p.add_argument("--from", dest="frm", required=True); p.add_argument("--to", required=True)
    p.add_argument("--group", help="filtreaza pe un grup (ex 'Lavete magice')"); p.set_defaults(f=cmd_by_account)
    p = sub.add_parser("lint", help="depisteaza reguli prost facute (conflicte/greedy/umbrite/account)"); p.set_defaults(f=cmd_lint)
    p = sub.add_parser("add-rule", help="adauga regula persistenta (wms_nomen_extra)")
    p.add_argument("--platform", required=True); p.add_argument("--type", required=True)
    p.add_argument("--pattern", required=True); p.add_argument("--group", required=True)
    p.add_argument("--apply", action="store_true"); p.set_defaults(f=cmd_add_rule)
    p = sub.add_parser("rm-rule", help="sterge regula din wms_nomen_extra")
    p.add_argument("--platform", required=True); p.add_argument("--pattern", required=True)
    p.add_argument("--type"); p.add_argument("--apply", action="store_true"); p.set_defaults(f=cmd_rm_rule)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
