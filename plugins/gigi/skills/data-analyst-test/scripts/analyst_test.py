#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "pandas", "numpy"]
# ///
"""
data-analyst-test — generează o probă practică de Data Analyst din date REALE AWBprint.

Subcomenzi:
  build    Extrage + anonimizează 30k AWB RO din AWBprint, modelează data de livrare, scrie CSV + CERINTE.
  traps    Injectează cele 6 capcane AI (dubluri, variante scriere, casing, contradicții, timp negativ, virgulă) + manifest.
  analyze  Calculează baremul (7 analize, naiv vs corect) → tipărește + scrie BAREM.
  all      build → traps → analyze.

Schema AWBprint (vezi SKILL.md): NU există `status` → folosește `aggregated_status`.
Județ = shipping_address->>'province'. Produs = line_items[0].inventory_item.sku. Cost = orders.transport_cost.
Date reale: frisbo_created_at + fulfilled_at (shipment_status_date e goală → data livrare se MODELEAZĂ).

Rulează din /Users/gheorghebeschea/Downloads/Scripturi (ca kb.py secret-get să meargă). Read-only pe DB.
"""
import sys, os, subprocess, csv, random, datetime, json, argparse, unicodedata
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"

def secret(key):
    r = subprocess.run(["/bin/zsh", "-lc", f"uv run '{KB}' secret-get {key}"],
                       capture_output=True, text=True)
    return r.stdout.strip()

def deacc(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s)) if unicodedata.category(c) != "Mn")

STATUS_RO = {"delivered": "livrat", "back_to_sender": "refuzat_retur", "refused": "refuzat",
             "unsuccessful_delivery": "livrare_esuata", "lost_in_transit": "pierdut",
             "returning_to_sender": "in_retur", "customer_pickup": "ridicat_personal", "in_transit": "in_tranzit"}
DELIVERED = {"delivered", "customer_pickup"}
SUCCESS_RO = {"livrat", "ridicat_personal"}
# model timp de tranzit (zile) pe curier + județe rapide/lente
BASE = {"DPD": 2, "dpd_ro": 2, "Econt": 2, "Sameday": 1, "Packeta": 3}
REMOTE = {"Suceava", "Botoșani", "Maramureș", "Satu Mare", "Tulcea", "Vaslui",
          "Bistrița-Năsăud", "Caraș-Severin", "Mehedinți", "Sălaj"}
FAST = {"București", "Ilfov", "Brașov", "Cluj", "Prahova", "Sibiu"}


def csv_path(out): return os.path.join(out, "dataset_awb_30000.csv")


# ── Order Hub (implicit din 24-oct-2026): AWBprint nu mai e deschis laptopurilor ──────────────────────────────────
# `/api/cs/livrabilitate` dă o comandă pe rând: starea din COLETE, județul, curierul, transportul real FĂRĂ TVA, SKU-urile
# distincte (fără cantități) și ziua comenzii. Nu dă data expedierii → o modelăm (ziua comenzii + 0/1 zile), ca data de
# livrare. Starea se traduce în vocabularul AWBprint (Refuzata → back_to_sender, refuzul dominant), ca restul probei
# (capcane, barem) să rămână neschimbat. Întoarcere: --source awb (doar pe serverul de scripturi).
OH_BAZA = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
OH_CHEI = ("OH_TEAM_READ_TOKEN", "OH_PROFIT_CITIRE_TOKEN", "OH_CS_SB_TOKEN")
OH_STARE = {"Livrata": "delivered", "Refuzata": "back_to_sender", "In curs de livrare": "in_transit"}
OH_MAGAZIN = {"EST": "esteban.ro", "GT": "georgetalent.ro", "OFER": "ofertelezilei.ro", "BON": "casaofertelor.ro",
              "LUX": "nocturnalux.ro", "NOC": "nocturna.ro", "GEN": "gento.ro", "ROSSI": "rossinails.ro",
              "APR": "apreciat.ro", "RED": "reduceribune.ro", "CARP": "carpetto.ro", "GRAND": "grandia.ro",
              "MAG": "magdeal.ro", "NUBRA": "nubra.ro", "BUC": "delabucsa.ro", "LAB": "labnoir.ro",
              "ORC": "oriceredus.ro", "ART": "artevita.ro"}


def oh_curier(c):
    c = (c or "").lower()
    return "DPD" if "dpd" in c else "Sameday" if "sameday" in c else "FAN Courier" if "fan" in c else (c or "necunoscut")


def oh_get(path):
    import urllib.request
    tok = next((os.environ[k].strip() for k in OH_CHEI if (os.environ.get(k) or "").strip()), "") \
        or secret("OH_TEAM_READ_TOKEN")
    if not tok:
        sys.exit("Order Hub: lipsește cheia de citire (env %s sau KB OH_TEAM_READ_TOKEN). "
                 "Calea veche: --source awb (doar pe serverul de scripturi)." % " / ".join(OH_CHEI))
    req = urllib.request.Request(OH_BAZA + path, headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        sys.exit("Order Hub %s nu răspunde (%s). Nu cad pe AWBprint." % (path.split("?")[0],
                 "HTTP %s" % e.code if getattr(e, "code", None) else type(e).__name__))


def oh_rows(size, luni):
    """Același tuplu ca SELECT-ul AWBprint, din ultimele `luni` luni ÎNCHEIATE, doar RO, cu SKU și stare rezolvată/pe drum."""
    import re
    azi = datetime.date.today().replace(day=1)
    lista, z = [], azi
    for _ in range(luni):
        z = (z - datetime.timedelta(days=1)).replace(day=1)
        lista.append(z.strftime("%Y-%m"))
    rows = []
    for luna in lista:
        d = oh_get("/api/cs/livrabilitate?luna=" + luna)
        col = {c: i for i, c in enumerate(d["coloane"])}
        for x in d["randuri"]:
            st = OH_STARE.get(x[col["stare"]])
            jud = (x[col["judet"]] or "").strip()
            skus = [k for k in (x[col["skus"]] or "").split(";") if k.strip()]
            if not st or x[col["tara"]] != "Romania" or not jud or jud == "?" or not skus:
                continue
            m = re.match(r"^[A-Za-z]+", x[col["comanda"]] or "")
            p = (m.group(0) if m else "").upper()
            t = x[col["transport_fara_tva"]]
            rows.append((x[col["comanda"]], st, jud, skus[0].strip().lower(), OH_MAGAZIN.get(p, p.lower()),
                         oh_curier(x[col["curier"]]), round(float(t) * 1.21, 2) if t else None,   # gross, ca în dicționar
                         float(x[col["total"]] or 0), len(skus), x[col["zi"]]))
    random.shuffle(rows)
    out = []
    for oid, st, jud, sku, mag, cur, tc, tp, ic, zi in rows[:size]:
        fc = datetime.datetime.strptime(zi, "%Y-%m-%d")
        out.append((oid, st, jud, sku, mag, cur, tc, tp, ic, fc, fc + datetime.timedelta(days=random.randint(0, 1))))
    return out


def cmd_build(args):
    random.seed(42)
    os.makedirs(args.out, exist_ok=True)
    if args.source == "oh":
        rows = oh_rows(args.size, args.luni)
    else:
        rows = awb_rows(args.size)
    _build_din(args, rows)


def awb_rows(size):
    import psycopg2
    try:
        cx = psycopg2.connect(secret("DATABASE_URL_AWBPRINT"))
    except Exception as e:
        sys.exit("AWBprint nu se deschide de aici (%s); --source awb merge doar pe serverul de scripturi (84.46)."
                 % type(e).__name__)
    cx.set_session(readonly=True); c = cx.cursor()
    Q = """SELECT o.id, o.aggregated_status, o.shipping_address->>'province' AS judet,
      lower(trim(o.line_items->0->'inventory_item'->>'sku')) AS sku, s.name AS magazin, o.courier_name,
      o.transport_cost, o.total_price, o.item_count, o.frisbo_created_at, o.fulfilled_at
    FROM orders o LEFT JOIN stores s ON s.uid=o.store_uid
    WHERE o.shipping_address->>'country_code'='RO' AND o.shipping_address->>'province' IS NOT NULL
      AND o.line_items->0->'inventory_item'->>'sku' IS NOT NULL AND o.frisbo_created_at IS NOT NULL
      AND o.aggregated_status IN ('delivered','back_to_sender','refused','unsuccessful_delivery',
                                  'lost_in_transit','returning_to_sender','customer_pickup','in_transit')
    ORDER BY random() LIMIT %s"""
    c.execute(Q, (size,)); rows = c.fetchall()
    cx.close()
    return rows


def _build_din(args, rows):

    def transit(cur, jud):
        b = BASE.get(cur, 3); b += 2 if jud in REMOTE else (0 if jud in FAST else 1)
        return max(1, b + random.randint(0, 2))

    recs = []
    for i, (oid, st, jud, sku, mag, cur, tc, tp, ic, fc, ff) in enumerate(rows, 1):
        ship = ff or fc
        livr = ""
        if st in DELIVERED and ship:
            livr = (ship + datetime.timedelta(days=transit(cur, jud))).date().isoformat()
        recs.append({"awb_id": f"AWB{i:06d}", "status_livrare": STATUS_RO.get(st, st), "judet": jud,
                     "produs_sku": sku or "", "magazin": mag or "", "curier": cur or "necunoscut",
                     "cost_transport": round(tc, 2) if tc is not None else "",
                     "valoare_comanda": round(tp, 2) if tp is not None else "", "nr_produse": ic or 1,
                     "data_comanda": fc.date().isoformat() if fc else "",
                     "data_expediere": ff.date().isoformat() if ff else "", "data_livrare": livr})

    # ~20 anomalii de cost evidente (peste cele naturale), reține ID-urile
    inj = []
    didx = [i for i, r in enumerate(recs) if r["status_livrare"] == "livrat" and r["cost_transport"] != ""]
    for val in ([0.0]*4 + [-8.5, -12.0, -5.0] + [499.0, 750.0, 999.0, 880.0] + [0.01, 0.05]
                + [315.0, 260.0, 410.0, 199.99, 340.0, 288.0, 222.0]):
        j = random.choice(didx); didx.remove(j); recs[j]["cost_transport"] = val; inj.append(recs[j]["awb_id"])

    with open(csv_path(args.out), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0].keys())); w.writeheader(); w.writerows(recs)
    with open(os.path.join(args.out, "_anomalii_injectate.txt"), "w") as f:
        f.write("\n".join(sorted(inj)))
    write_cerinte(args.out)
    print(f"✓ build: {csv_path(args.out)} · {len(recs)} rânduri · "
          f"{sum(1 for r in recs if r['data_livrare'])} cu dată livrare · {len(inj)} anomalii cost")


def cmd_traps(args):
    import pandas as pd
    random.seed(7)
    p = csv_path(args.out)
    df = pd.read_csv(p, dtype={"cost_transport": object})
    df["cost_transport"] = pd.to_numeric(df.cost_transport, errors="coerce")
    manifest = {}

    def pick(mask, k):
        ids = df[mask].awb_id.tolist(); random.shuffle(ids); return ids[:k]

    # 1: dubluri AWB
    dup_ids = pick(df.status_livrare == "livrat", 45)
    dups = df[df.awb_id.isin(dup_ids)].copy()
    manifest["1_dubluri_awb"] = {"n": len(dup_ids), "ids": sorted(dup_ids),
                                 "prinde": "awb_id.duplicated() → 45; dedup pe awb_id"}
    # 2: variante scriere județ
    ct = {}
    for cty, kind in [("Cluj", "lower"), ("Iași", "deacc"), ("Prahova", "space")]:
        idx = df.index[df.judet == cty].tolist(); random.shuffle(idx); sel = idx[:int(len(idx)*0.4)]
        for i in sel:
            df.at[i, "judet"] = {"lower": cty.lower(), "deacc": deacc(cty), "space": cty+" "}[kind]
        ct[cty] = {"varianta": {"lower": cty.lower(), "deacc": deacc(cty), "space": cty+"·spațiu"}[kind], "randuri": len(sel)}
    manifest["2_variante_judet"] = {"detalii": ct, "prinde": "normalizează (strip+deacc+lower) înainte de groupby"}
    # 3: status casing
    st = pick(df.status_livrare == "livrat", 50)
    for j, i in enumerate(st):
        df.loc[df.awb_id == i, "status_livrare"] = "Livrat" if j % 2 else "livrat "
    manifest["3_status_casing"] = {"n": 50, "ids": sorted(st), "prinde": "filtru =='livrat' le ratează; normalizează status"}
    # 4: contradicții
    ref = pick(df.status_livrare == "refuzat_retur", 15)
    for i in ref:
        df.loc[df.awb_id == i, "data_livrare"] = df.loc[df.awb_id == i, "data_expediere"].values[0]
    liv = [x for x in pick(df.status_livrare == "livrat", 60) if x not in st and x not in dup_ids][:15]
    for i in liv:
        df.loc[df.awb_id == i, "data_livrare"] = ""
    manifest["4_contradictii"] = {"refuzat_cu_data": sorted(ref), "livrat_fara_data": sorted(liv),
                                  "prinde": "cross-check status↔dată"}
    # 5: timp negativ
    neg = [x for x in pick(df.status_livrare == "livrat", 40) if x not in st+dup_ids+liv][:12]
    for i in neg:
        exp = pd.to_datetime(df.loc[df.awb_id == i, "data_expediere"].values[0])
        df.loc[df.awb_id == i, "data_livrare"] = (exp - pd.Timedelta(days=random.randint(2, 6))).date().isoformat()
    manifest["5_timp_negativ"] = {"n": 12, "ids": sorted(neg), "prinde": "data_livrare<data_expediere → zile<0; filtrează"}
    # 6: virgulă zecimală
    com = [x for x in pick(df.cost_transport.notna(), 6) if x not in dup_ids][:6]
    df["cost_transport"] = df.cost_transport.map(lambda v: f"{v:.2f}" if pd.notna(v) else "")
    for i in com:
        v = df.loc[df.awb_id == i, "cost_transport"].values[0]
        if v:
            df.loc[df.awb_id == i, "cost_transport"] = v.replace(".", ",")
    manifest["6_virgula_zecimala"] = {"n": 6, "ids": sorted(com), "prinde": "coloana devine text; str.replace(',', '.')"}

    dups2 = dups.assign(cost_transport=dups.cost_transport.map(lambda v: f"{v:.2f}" if pd.notna(v) else ""))
    out = pd.concat([df, dups2], ignore_index=True)
    out.to_csv(p, index=False)
    json.dump(manifest, open(os.path.join(args.out, "_capcane_AI.json"), "w"), ensure_ascii=False, indent=2)
    print(f"✓ traps: {len(out)} rânduri · {out.awb_id.nunique()} awb_id unice · 6 capcane → _capcane_AI.json")


def cmd_analyze(args):
    import pandas as pd, numpy as np
    p = csv_path(args.out)
    raw = pd.read_csv(p, dtype={"cost_transport": object})
    raw["st_norm"] = raw.status_livrare.str.strip().str.lower()
    df = raw.drop_duplicates("awb_id").copy()  # capcana 1: dedup pe awb_id
    concl = df[df.st_norm != "in_tranzit"].copy()
    # capcana 2: normalizează județul (deacc+strip+title) ca variantele de scriere să nu spargă grupările
    concl["judet"] = concl.judet.map(lambda s: deacc(str(s)).strip().title())
    concl["magazin"] = concl.magazin.astype(str).str.strip()
    concl["ok"] = concl.st_norm.isin(SUCCESS_RO)
    N, ok = len(concl), int(concl.ok.sum())
    lines = []
    def P(s): print(s); lines.append(s)
    P("="*66); P("BAREM — probă Data Analyst (corect = după curățare)"); P("="*66)
    P(f"\n1. LIVRABILITATE GENERALĂ: {ok/N*100:.2f}%  (livrate {ok} / concluzionate {N}; "
      f"in_tranzit excluse {int((df.st_norm=='in_tranzit').sum())})")

    def rate(col, minn=50, k=5):
        g = concl.groupby(col).agg(n=("ok", "size"), livr=("ok", "sum"))
        g["rata"] = (g.livr/g.n*100).round(2); g = g[g.n >= minn].sort_values("rata")
        return g
    gj = rate("judet"); P(f"2. PE JUDEȚ (min50): worst {gj.head(3).rata.to_dict()} | best {gj.tail(3).rata.to_dict()}")
    gp = rate("produs_sku"); P(f"3. PE PRODUS (min50): worst {gp.head(3).rata.to_dict()} | best {gp.tail(3).rata.to_dict()}")
    gm = rate("magazin"); P(f"4. PE MAGAZIN (min50): worst {gm.head(3).rata.to_dict()} | best {gm.tail(3).rata.to_dict()}")

    c = pd.to_numeric(raw.cost_transport.str.replace(",", ".", regex=False), errors="coerce")
    cc = raw.assign(cost=c).dropna(subset=["cost"])
    neg = cc[cc.cost < 0]; zero = cc[cc.cost == 0]; tiny = cc[(cc.cost > 0) & (cc.cost < 1)]; high = cc[cc.cost > 60]
    clean = pd.concat([neg, zero, tiny, high]).drop_duplicates("awb_id")
    P(f"5. ANOMALII COST (definiție domeniu, ~12-13 RON normal): neg {len(neg)} · zero {len(zero)} · <1 {len(tiny)} · "
      f">60 {len(high)} → TOTAL ~{len(clean)}. ⚠️ IQR orb ar marca ~3000 (capcană).")

    d = df.dropna(subset=["data_livrare"]).copy(); d = d[d.data_livrare.astype(str).str.len() > 3]
    d["zile"] = (pd.to_datetime(d.data_livrare, errors="coerce") - pd.to_datetime(d.data_expediere, errors="coerce")).dt.days
    corr = d[(d.zile >= 0) & (d.zile <= 30)]
    P(f"6. TIMP LIVRARE: media CORECTĂ {corr.zile.mean():.2f} zile (n={len(corr)}) | "
      f"zile<0 de filtrat: {int((d.zile<0).sum())} | naiv-cu-negative {d.zile.mean():.2f}")

    concl2 = concl.copy(); concl2["refuz"] = concl2.st_norm.isin({"refuzat_retur", "refuzat"})
    tv = concl2.groupby("magazin").refuz.agg(["mean", "size"]); tv = tv[tv["size"] >= 50].sort_values("mean", ascending=False)
    P(f"7. TIPARE REFUZ: global {concl2.refuz.mean()*100:.2f}% | top magazine "
      + str({k: f'{v*100:.1f}%' for k, v in tv["mean"].head(3).items()}))

    # capcane: naiv vs corect
    P("\n🪤 CAPCANE (naiv vs corect):")
    P(f"   dubluri: total naiv {len(raw)} vs unic {raw.awb_id.nunique()} ({len(raw)-raw.awb_id.nunique()})")
    P(f"   județe: naiv {raw.judet.nunique()} vs normalizat {raw.judet.map(lambda s: deacc(s).strip().lower()).nunique()}")
    P(f"   status 'livrat': exact {(raw.status_livrare=='livrat').sum()} vs normalizat {(raw.st_norm=='livrat').sum()}")
    P(f"   cost text (virgulă): {raw.cost_transport.notna().sum()-pd.to_numeric(raw.cost_transport,errors='coerce').notna().sum()} nu se parsează brut")

    write_barem(args.out, "\n".join(lines))
    print(f"\n✓ analyze: BAREM scris în {args.out}")


# ------- documente -------
def write_cerinte(out):
    txt = """# Probă practică — Data Analyst (livrabilitate colete)

## Context
Lucrezi cu date reale de expediere dintr-un grup de magazine online din România care livrează preponderent
ramburs (COD) prin curier. Fiecare rând = un AWB (o expediere). Setul e anonimizat (fără nume/telefon/adresă;
păstrăm doar județul). Sarcina: scoate imaginea de sănătate a livrării și tiparele care pierd bani.
Folosește ce unealtă vrei (Excel/Sheets, Python, R, SQL, BI). Contează corectitudinea, claritatea, raționamentul.

## Setul de date — `dataset_awb_30000.csv` (UTF-8, virgulă separator)
| Coloană | Semnificație |
|---|---|
| awb_id | id unic expediere (anonimizat) |
| status_livrare | rezultat: livrat / ridicat_personal / refuzat_retur / refuzat / livrare_esuata / pierdut / in_retur / in_tranzit |
| judet | județ destinație (RO) |
| produs_sku | codul produsului principal |
| magazin | magazinul expeditor |
| curier | curierul |
| cost_transport | cost transport (RON, cu TVA) |
| valoare_comanda | valoarea comenzii (RON) |
| nr_produse | câte produse are comanda |
| data_comanda / data_expediere / data_livrare | date (YYYY-MM-DD); data_livrare goală dacă nu a ajuns |

> ⚠️ Datele sunt „murdare", ca în realitate. Înainte de calcul, VERIFICĂ integritatea: pot exista înregistrări
> duplicate, inconsistențe de scriere (aceeași categorie scrisă diferit), valori contradictorii, formate numerice
> mixte, comenzi în tranzit, valori lipsă. Curățarea corectă și explicarea deciziilor fac parte din notă — un
> răspuns care ia datele „ca atare" (ex. copiat direct dintr-un tool AI, fără verificare) va da cifre greșite.

## Ce livrezi (7 analize — dă și rata, și numărul absolut, + o concluzie scurtă)
1. Livrabilitate generală (definește ce e „succes" și ce excluzi). 2. pe județ (atenție la volume mici).
3. pe produs (SKU). 4. pe magazin. 5. anomalii de costuri (explică metoda + câte). 6. timpul mediu de livrare
(în general + pe segmente; pe câte comenzi se poate calcula). 7. tipare de refuz (factori cu impact + recomandare).

## 🔎 Explică PAS CU PAS ce faci (obligatoriu — se punctează separat)
Pentru fiecare analiză, scrie pe scurt pașii: (1) ce date am folosit (ce coloane/rânduri, ce am exclus și de ce);
(2) cum am curățat datele (ce probleme am găsit — duplicate, valori lipsă, inconsistențe, formate greșite, valori
imposibile — și ce am făcut cu ele); (3) ce am calculat și cum (definiția/formula); (4) ce decizii am luat și de ce
(praguri, ce e „succes"); (5) ce am observat. Un răspuns cu doar cifre, fără pași explicați, PIERDE puncte.

## Format & evaluare
Fișier de analiză (să se vadă cum ai ajuns la cifre) + explicația pas cu pas + rezumat 1 pagină cu 3–5 recomandări.
Notăm: corectitudine · **explicarea pașilor** · **curățarea datelor** · raționament (valori lipsă/tranzit/volume mici)
· detectarea anomaliilor · insight de business · claritate/reproductibilitate. Timp estimat: 3–5 ore.
"""
    open(os.path.join(out, "CERINTE_Proba_Data_Analyst.md"), "w", encoding="utf-8").write(txt)


def write_barem(out, computed):
    header = "# BAREM / Soluție — Probă Data Analyst (document intern — NU se dă aplicanților)\n\n" \
             "> Cifrele de mai jos sunt calculate LIVE pe fișierul curent. Toleranță ±0,3pp.\n" \
             "> Real din Order Hub (implicit; sau AWBprint cu --source awb): status, județ, produs, magazin, cost, valoare, " \
             "data comenzii. Pe OH data expedierii e modelată (comanda + 0/1 zile).\n" \
             "> Modelat: data_livrare (real nu e stocat curat). Injectat: 20 anomalii cost + 6 capcane AI.\n\n"
    traps = """
## 🪤 Capcane AI (naiv vs corect) — vezi `_capcane_AI.json`
1. Dubluri AWB (45) → naiv 30.045 / corect dedup 30.000.
2. Variante scriere județ (Cluj/cluj, Iași/Iasi, Prahova/Prahova␣) → naiv ~45 „județe" / corect 42.
3. Status casing (50× Livrat/livrat␣) → filtru =='livrat' subestimează livrabilitatea.
4. Contradicții (15 refuzat CU dată + 15 livrat FĂRĂ dată) → cross-check status↔dată.
5. Timp negativ (12 rânduri data_livrare<data_expediere) → filtrează zile<0.
6. Format numeric mixt (6 costuri cu virgulă) → coloana devine text; curăță str.replace(',', '.').

Semn că a picat (probabil AI copiat): raportează „30.045 AWB", „45 județe", livrabilitate <84,5% nejustificat,
sau media de livrare trasă de negative. Corect după curățare = revine la barem.

## Rubrică granulară (100p) — punctăm diverse lucruri
**A. Metodologie & explicație pas cu pas (15):** explică pașii/analiză 6 · definiții clare 4 · reproductibil 3 · rezumat 2.
**B. Cele 7 analize (48):** livrabilitate 3 + exclude in_tranzit 3 · județ 6 · produs 6 · magazin 3 + pattern deals-vs-brand 4 ·
anomalii cost 9 (evită capcana IQR!) · timp livrare 7 · tipare refuz 7.
**C. Curățare & capcane AI (25):** dubluri 5 · variante județ 5 · status casing 4 · contradicții 3 · timp negativ 4 · virgulă 4.
**D. Insight & recomandări (12):** recomandare pe segment risc 5 · leagă analizele 3 · prioritizează 2 · realiste (nu „schimbă curierul") 2.
Ghid: ≥85 excelent · 70-84 bun · 55-69 mediu · <55 slab.
🚩 Steaguri roșii (AI copiat): raportează „30.045 AWB"/„45 județe"/„~3.000 anomalii", rate 100% pe volume mici,
media livrare trasă de negative fără să observe, renunță la coloana de cost în loc s-o curețe, zero explicație a pașilor.
"""
    open(os.path.join(out, "BAREM_Solutie_Data_Analyst.md"), "w", encoding="utf-8").write(
        header + "```\n" + computed + "\n```\n" + traps)


def main():
    ap = argparse.ArgumentParser(description="Generează probă practică Data Analyst din AWBprint.")
    ap.add_argument("cmd", choices=["build", "traps", "analyze", "all"])
    ap.add_argument("--size", type=int, default=30000)
    ap.add_argument("--out", default=os.path.expanduser("~/Downloads/Proba_Data_Analyst"))
    ap.add_argument("--source", choices=["oh", "awb"], default=None,
                    help="oh = Order Hub (implicit); awb = AWBprint, doar pe serverul de scripturi (sau ANALYST_TEST_SURSA=awb)")
    ap.add_argument("--luni", type=int, default=3, help="oh: câte luni încheiate se eșantionează")
    args = ap.parse_args()
    if args.source is None:
        args.source = "awb" if (os.environ.get("ANALYST_TEST_SURSA") or "").strip().lower() == "awb" else "oh"
    args.out = os.path.expanduser(args.out)
    if args.cmd in ("build", "all"):
        cmd_build(args)
    if args.cmd in ("traps", "all"):
        cmd_traps(args)
    if args.cmd in ("analyze", "all"):
        cmd_analyze(args)


if __name__ == "__main__":
    main()
