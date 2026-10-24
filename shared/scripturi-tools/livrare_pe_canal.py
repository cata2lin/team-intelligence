#!/usr/bin/env python3
"""Rata de livrare pe CANAL de marketing și pe MAGAZIN × lună, cu mixul de spend — raportul permanent (24-oct-2026).

De ce: ownerul vrea rata de livrare ținută permanent și pe canal („refuzurile vin din TikTok”), plus comparația între
magazine: magazinele care cheltuie mai mult pe o platformă livrează mai prost? (testul Apreciat 35% vs Crocomag 15%).
Analiza din 24-oct (jurnal/2026-10-24-refuz-pe-canal-tiktok-meta.md) a dat corelație ~0 între ponderea TikTok și
livrare; raportul ăsta o ține la zi.

Surse:
  · starea + canalul pe comandă: Orders `GET /api/cs/livrabilitate?luna=` (starea din COLETE; coloana `canal` din
    tabela comanda_canal, regula services/canal_marketing = shared/scripturi-tools/canal_marketing.py);
  · magazinul comenzii: prefixul din profit_orders (după numele comenzii), altfel literele numelui;
  · spend pe magazin × platformă × lună: metrics cache.daily_ad_spend_ron (store_name = domeniul public, ca în
    profit_core.PREFIX_AWB_DOMAIN). Contul TikTok `tt.cmeecf…` nu e mapat pe niciun magazin: apare separat.
Rata de livrare = livrate / (livrate + refuzate), doar comenzi cu stare finală. Comenzile anulate NU intră în rată,
dar refuzurile din iulie–august care apar ca anulate sunt o limită cunoscută a datelor vechi.
Comparația săptămânală e pe săptămâni MATURE (încheiate de cel puțin VARSTA zile), ca să nu pară mai bună săptămâna
curentă (refuzurile vin ultimele). Alarmă: un canal care scade cu peste PRAG_ALARMA puncte față de media pe 4 săptămâni.

  livrare_pe_canal.py [--luni 4] [--out data/briefing/livrare-pe-canal.md]
Funcțiile `rata`, `pe_saptamana`, `alarme` sunt pure (testate).
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi")

OH_URL = os.environ.get("OH_URL", "https://orderhub.arona.ro")
DB = "/root/Scripturi/data/profitability.db"
VARSTA = 14
PRAG_ALARMA = 5.0
CANALE = ("tiktok", "meta", "google", "direct", "altul", "necunoscut", "necunoscut_magazin_inchis")
# marketplace-urile sunt rânduri proprii; „costul” lor e comisionul marketplace-ului, nu spend de reclame
MARKETPLACE = ("emag", "trendyol", "olx", "okazii", "pepita", "cel")
TOATE = CANALE + MARKETPLACE
PLATFORMA_CANAL = {"tiktok": "tiktok", "meta": "meta", "facebook": "meta", "google": "google"}
_LITERE = re.compile(r"^#?([A-Za-z]+)")


def rata(liv, ref):
    return round(100.0 * liv / (liv + ref), 1) if (liv + ref) else None


def _luni(n, azi=None):
    azi = azi or date.today()
    y, m, out = azi.year, azi.month, []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return sorted(out)


def _oh(luna, tok):
    req = urllib.request.Request(OH_URL + "/api/cs/livrabilitate?" + urllib.parse.urlencode({"luna": luna}),
                                 headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    col = d["coloane"]
    return [dict(zip(col, r)) for r in d["randuri"]]


def pe_saptamana(randuri, azi=None, varsta=VARSTA):
    """{(luni_saptamanii, canal): [livrate, refuzate]} doar pe săptămânile încheiate de ≥ varsta zile."""
    azi = azi or date.today()
    out = collections.defaultdict(lambda: [0, 0])
    for r in randuri:
        zi = date.fromisoformat(r["zi"])
        lun = zi - timedelta(days=zi.weekday())
        if lun + timedelta(days=6 + varsta) > azi:
            continue
        k = (lun.isoformat(), r.get("canal") or "necunoscut")
        if r["stare"] == "Livrata":
            out[k][0] += 1
        elif r["stare"] == "Refuzata":
            out[k][1] += 1
    return out


def alarme(sapt, min_n=30, prag=PRAG_ALARMA):
    """[(canal, rata ultima săptămână matură, media pe 4 săptămâni dinainte, delta)] — doar scăderi > prag."""
    saptamani = sorted({s for s, _ in sapt})
    if len(saptamani) < 5:
        return [], saptamani
    ult, ant = saptamani[-1], saptamani[-5:-1]
    out = []
    for c in TOATE:
        l, f = sapt.get((ult, c), [0, 0])
        if l + f < min_n:
            continue
        r_ult = rata(l, f)
        prev = [rata(*sapt[(s, c)]) for s in ant if sum(sapt.get((s, c), [0, 0])) >= min_n]
        if not prev:
            continue
        med = round(sum(prev) / len(prev), 1)
        if r_ult is not None and med - r_ult > prag:
            out.append((c, r_ult, med, round(r_ult - med, 1)))
    return out, saptamani


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luni", type=int, default=4)
    ap.add_argument("--out", default="/root/Scripturi/data/briefing/livrare-pe-canal.md")
    a = ap.parse_args()
    tok = (os.environ.get("OH_PROFIT_CITIRE_TOKEN") or "").strip()
    if not tok:
        sys.exit("lipsește OH_PROFIT_CITIRE_TOKEN")
    import profit_core as pc
    luni = _luni(a.luni)
    randuri = []
    for l in luni:
        randuri += _oh(l, tok)
    cx = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    pfx = {n: p for n, p in cx.execute("SELECT order_name, prefix FROM profit_orders WHERE month >= ?", (luni[0],))}
    cx.close()
    for r in randuri:
        m = _LITERE.match(r["comanda"] or "")
        r["prefix"] = pfx.get(r["comanda"]) or (m.group(1).upper() if m else "?")
        r["luna"] = r["zi"][:7]

    # spend pe magazin (domeniu) × platformă × lună
    import psycopg2
    d = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", os.environ["DATABASE_URL_METRICS"])
    d = re.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")
    mc = psycopg2.connect(d); cur = mc.cursor()
    cur.execute("SELECT store_name, platform, to_char(date, 'YYYY-MM'), SUM(spend_ron) FROM cache.daily_ad_spend_ron "
                "WHERE date >= %s GROUP BY 1, 2, 3", (luni[0] + "-01",))
    spend = collections.defaultdict(float)
    for dom, plat, l, s in cur.fetchall():
        spend[(dom, PLATFORMA_CANAL.get((plat or "").lower(), plat), l)] += float(s or 0)
    mc.close()
    dom_pfx = dict(pc.PREFIX_AWB_DOMAIN)

    # A. magazin × lună: mix de spend, rata de livrare, cost pe livrată
    agg = collections.defaultdict(lambda: [0, 0])
    can_lun = collections.defaultdict(lambda: [0, 0])
    for r in randuri:
        k = (r["prefix"], r["luna"])
        if r["stare"] == "Livrata":
            agg[k][0] += 1; can_lun[(r.get("canal") or "necunoscut", r["luna"])][0] += 1
        elif r["stare"] == "Refuzata":
            agg[k][1] += 1; can_lun[(r.get("canal") or "necunoscut", r["luna"])][1] += 1
    linii = ["## Rata de livrare pe canal și pe magazin (Orders + spend din metrics)",
             "*Rată = livrate / (livrate + refuzate), din colete. Comparația curată între canale = ACELAȘI SKU pe magazine "
             "cu mix de spend diferit; o rată mai mică pe un magazin poate veni din produse, nu din canal.*", ""]
    sapt = pe_saptamana(randuri)
    al, saptamani = alarme(sapt)
    if al:
        linii.append("🔴 **Alarmă livrare pe canal:** " + "; ".join(
            "%s %.1f%% vs %.1f%% media 4 săpt. (%+.1f pp)" % x for x in al))
    elif saptamani:
        ult = saptamani[-1]
        bucati = []
        for c in TOATE:
            l, f = sapt.get((ult, c), [0, 0])
            if l + f >= 30:
                bucati.append("%s %.1f%%" % (c, rata(l, f)))
        linii.append("🟢 Livrare pe canal, săpt. matură %s: %s (nicio scădere > %.0f pp față de media pe 4 săpt.)"
                     % (ult, ", ".join(bucati) or "—", PRAG_ALARMA))
    i_atrib = len(linii)
    linii += ["", "| Canal | " + " | ".join(luni) + " | Cost/livrată (ultima lună completă) |",
              "|---|" + "---:|" * (len(luni) + 1)]
    l_compl = luni[-2] if len(luni) > 1 else luni[-1]
    sp_can = collections.defaultdict(float)
    for (dom, can, l), s in spend.items():
        if l == l_compl:
            sp_can[can] += s
    # costul pe livrată pe canal are sens doar când majoritatea livratelor au canal (backfill-ul pe 6 luni)
    # acoperirea atribuirii se socotește doar pe comenzile din magazinele Shopify (marketplace-urile au canal sigur)
    liv_l = sum(v[0] for (c, l), v in can_lun.items() if l == l_compl and c not in MARKETPLACE)
    acoperire = (1 - sum(can_lun.get((n, l_compl), [0, 0])[0] for n in ("necunoscut", "necunoscut_magazin_inchis"))
                 / liv_l) if liv_l else 0
    linii.insert(i_atrib, "\n*Atribuire pe magazinele Shopify, %s: %.0f%% din livrate au canal (marketplace-urile sunt "
                 "rânduri proprii, în afara procentului; „necunoscut_magazin_inchis” = magazin fără token Shopify).*"
                 % (l_compl, 100 * acoperire))
    for c in TOATE:
        if c in MARKETPLACE and not any(can_lun.get((c, l)) for l in luni):
            continue
        cel = []
        for l in luni:
            lv, rf = can_lun.get((c, l), [0, 0])
            cel.append("%s%% (%d)" % (rata(lv, rf), lv + rf) if lv + rf else "—")
        lv = can_lun.get((c, l_compl), [0, 0])[0]
        if c in MARKETPLACE:
            cost = "comision necunoscut"      # Orders nu ține comisionul marketplace-ului
        else:
            cost = ("%.0f lei" % (sp_can[c] / lv)) if (c in sp_can and lv and acoperire >= 0.7) else (
                "— (atribuire %.0f%%)" % (100 * acoperire) if c in sp_can else "—")
        linii.append("| %s | %s | %s |" % (c, " | ".join(cel), cost))
    linii += ["", "| Magazin | Lună | TikTok % | Meta % | Google % | Livrare % | Cost/livrată |", "|---|---|---:|---:|---:|---:|---:|"]
    for (p, l), (lv, rf) in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if lv + rf < 50 or l == luni[-1]:
            continue
        dom = dom_pfx.get(p)
        s = {c: spend.get((dom, c, l), 0.0) for c in ("tiktok", "meta", "google")} if dom else {}
        tot = sum(s.values())
        pr = lambda c: ("%.0f" % (100 * s[c] / tot)) if tot else "—"
        linii.append("| %s | %s | %s | %s | %s | %.1f | %s |" % (p, l, pr("tiktok"), pr("meta"), pr("google"), rata(lv, rf),
                                                                ("%.0f lei" % (tot / lv)) if (tot and lv) else "—"))
    nemapat = sum(v for (dom, c, l), v in spend.items() if dom and dom.startswith("tt.") and l == l_compl)
    if nemapat:
        linii.append("\n*Spend TikTok nemapat pe magazin (cont `tt.cmeecf…`) în %s: %.0f lei — nu intră în costul pe livrată.*"
                     % (l_compl, nemapat))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write("\n".join(linii) + "\n")
    print("\n".join(linii[:6]))
    return 1 if al else 0


if __name__ == "__main__":
    sys.exit(main())
