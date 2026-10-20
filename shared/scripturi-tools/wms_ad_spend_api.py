# -*- coding: utf-8 -*-
"""wms_ad_spend_api.py — alimentează `wms_ad_spend` DIRECT din API-urile Meta + TikTok,
la nivel de AD. ÎNLOCUIEȘTE complet sheet-ul WMS (care a murit: conectorul FB e blocat pe
„Refreshing...", raportul TikTok pe „Report paused: Unexpected failure").

DE CE la nivel de AD și nu de campanie: maparea produs↔spend se face pe `campanie + ad_name`
(conturile sunt PARTAJATE între branduri, iar produsul e adesea doar în numele reclamei).
Tabelele din warehouse (`*_campaign_insights_daily`) NU au ad_name, deci nu sunt suficiente.

DE CE scrie în `wms_ad_spend` și nu în `cache.product_ad_spend`: forma asta păstrează
`account/campaign/ad_name`, de care depind `mapping_admin by-account` (splitul pe ȚARĂ/magazin)
și `wms_marketing._group_of`. Nimic din aval nu se schimbă.

MONEDĂ: `spend_usd` e citit în aval ca USD și înmulțit cu cursul zilei (`wms_marketing._usd_ron`).
Conturile facturează în valute diferite (USD/EUR/PLN/...), deci convertim întâi în RON cu FX-ul
canonic per zi, apoi împărțim la cursul USD al aceleiași zile ⇒ dus-întors exact.

  python wms_ad_spend_api.py --since 2026-07-23                 # dry-run
  python wms_ad_spend_api.py --since 2026-07-23 --apply         # scrie
  python wms_ad_spend_api.py --days 2 --apply                   # cron zilnic/orar
Cere env: ARONA_SKILLS_DIR=/root/ad-spend/skills + DATABASE_URL_METRICS (din run.env/.env).
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi")

import ad_spend_live as A          # reuse: metrics_conn, load_fb_mapping, resolve_brand, _month_chunks
import wms_marketing as wm         # reuse: _load_fx / _usd_ron  (același curs ca în aval)
import meta                        # noqa: E402  (pe sys.path via ARONA_SKILLS_DIR)
import tiktok                      # noqa: E402

DB = os.environ.get("PROFITABILITY_DB", "/root/Scripturi/data/profitability.db")
RENAMED = set()   # conturi Meta al caror nume live difera de cel din DB


def fetch_meta(start, end, fx, ok):
    """rânduri (date, account, campaign, ad_name, spend_usd) la nivel de AD, din Graph API.
    `ok` primește (source, cont, de_la, până_la) pentru fiecare fereastră citită CU SUCCES."""
    out = []
    entries = A.load_fb_mapping()
    mc = A.metrics_conn(); c = mc.cursor()
    c.execute('SELECT a.name, a."metaAccountId", a.currency, t."accessToken" '
              'FROM meta_ad_accounts a JOIN meta_access_tokens t ON t.id=a."tokenId" '
              'WHERE a."isActive" AND t."isActive"')
    accts = c.fetchall(); mc.close()
    idx = meta.fx_index({cur for _, _, cur, _ in accts}, start, end)
    # NU filtrăm pe brand: `wms_ad_spend` e ingestie BRUTĂ, iar maparea produs/țară se face în aval
    # pe (account, campaign, ad_name). Un filtru de brand aici ar ascunde spend real — exact bug-ul
    # care făcea feed-ul cu 12,7% mai mic decât warehouse-ul. Doar LOGĂM conturile nerecunoscute.
    kept = unknown = renamed = 0
    for nm_db, aid, cur, tok in accts:
        kept += 1
        # ⚠️ `meta_ad_accounts.name` poate fi STALE (conturi cu lastSyncAt=None nu s-au reîmprospătat
        # niciodată). Ex. real: DB zicea 'Pointmag', live în Meta era 'Magdeal 2'. Numele contului e
        # CHEIA de mapare produs/magazin ⇒ îl luăm LIVE, altfel regulile se rup tăcut la redenumire.
        nm = nm_db
        try:
            info = meta.graph_one("https://graph.facebook.com/%s/%s" % (meta.VER, aid),
                                  {"fields": "name", "access_token": tok}) \
                if hasattr(meta, "graph_one") else None
            if not info:
                import json as _j, urllib.request as _u
                info = _j.loads(_u.urlopen("https://graph.facebook.com/%s/%s?fields=name&access_token=%s"
                                           % (meta.VER, aid, tok), timeout=30).read())
            live = (info or {}).get("name")
            if live and live != nm_db:
                renamed += 1
                RENAMED.add(live)
                sys.stderr.write("[meta] cont REDENUMIT: DB=%r -> live=%r (folosesc live)\n" % (nm_db, live))
            nm = live or nm_db
        except Exception as e:
            sys.stderr.write("[meta] nu pot citi numele live pt %s (%s) — folosesc DB %r\n" % (aid, str(e)[:60], nm_db))
        if not A.resolve_brand(nm, entries):
            unknown += 1
            sys.stderr.write("[meta] cont fără brand în Mapping (îl iau oricum): %s\n" % nm)
        for cs, ce in A._month_chunks(start, end):
            try:
                rows = meta.graph(
                    "https://graph.facebook.com/%s/%s/insights" % (meta.VER, aid),
                    {"level": "ad", "fields": "campaign_name,ad_name,spend", "time_increment": "1",
                     "time_range": json.dumps({"since": cs, "until": ce}),
                     "limit": "500", "access_token": tok})
            except Exception as e:
                sys.stderr.write("[meta] %s %s..%s EȘEC: %s\n" % (nm, cs, ce, str(e)[:120]))
                continue
            ok.add(("fb", nm, cs, ce))
            for r in rows:
                d = (r.get("date_start") or "")[:10]
                ron = meta.conv(float(r.get("spend", 0) or 0), cur, meta._pdate(d), idx)
                if ron <= 0:
                    continue
                rate = wm._usd_ron(fx, d) or 0
                if rate <= 0:
                    continue
                out.append(("fb", d, nm, r.get("campaign_name", "") or "",
                            r.get("ad_name", "") or "", ron / rate))
    sys.stderr.write("[meta] conturi=%d (fără brand: %d, redenumite: %d), rânduri=%d\n" % (kept, unknown, renamed, len(out)))
    return out


def fetch_tiktok(start, end, fx, ok):
    """idem TikTok. Conturile PARTAJATE sunt iterate de mai multe branduri -> dedup obligatoriu, pe ad_id
    (NU pe nume: reclame diferite cu acelasi nume in aceeasi campanie se pierdeau)."""
    out, seen = [], set()
    brands = list(json.loads((A.SKILLS / "meta-ads/brand_map.json").read_text()).keys())
    idx = tiktok.fx_index({"USD", "EUR", "PLN", "HUF", "CZK", "RON"}, start, end)
    for brand in brands:
        for cs, ce in A._month_chunks(start, end):
            try:
                _accts, rows = tiktok.report_rows(brand, "ad", cs, ce)
            except SystemExit:
                break
            except Exception as e:
                sys.stderr.write("[tt] %s %s..%s: %s\n" % (brand, cs, ce, str(e)[:100]))
                continue
            # doar conturile care chiar au intors date: un advertiser fara permisiune (40001) apare in _accts
            # dar n-are randuri, iar stergerea lui ar goli istoricul
            for acct_ in {(r.get("_acct") or "").strip() for r in rows}:
                ok.add(("tt", acct_, cs, ce))
            for r in rows:
                m = r.get("metrics", {}) or {}
                d = (r.get("dimensions", {}) or {}).get("stat_time_day", "")[:10]
                acct = (r.get("_acct") or "").strip()
                camp = m.get("campaign_name", "") or ""
                adn = m.get("ad_name", "") or ""
                k = (acct, d, (r.get("dimensions", {}) or {}).get("ad_id") or (camp, adn))
                if k in seen:
                    continue
                seen.add(k)
                try:
                    day = datetime.date.fromisoformat(d)
                except Exception:
                    day = None
                ron = tiktok.conv(tiktok._f(m, "spend"), r.get("_cur"), day, idx)
                if ron <= 0:
                    continue
                rate = wm._usd_ron(fx, d) or 0
                if rate <= 0:
                    continue
                out.append(("tt", d, acct, camp, adn, ron / rate))
    sys.stderr.write("[tt] rânduri=%d (dedup pe cont×zi×campanie×ad)\n" % len(out))
    return out


RESIDUAL_AD = "(rest campanie: reclame sterse sau fara acces)"


def tiktok_residual(rows, start, end, fx, ok, conn):
    """TikTok raporteaza pe reclama MAI PUTIN decat pe campanie (reclame sterse; conturi fara permisiune pe
    nivel de reclama, ex. Covoria.ro). Completam din warehouse (`tiktok_campaign_insights_daily`) diferenta pe
    cont x zi x campanie, PLAFONATA la diferenta pe cont x zi, ca un nume de campanie scris altfel sa nu
    produca dubluri. Randurile au ad_name = RESIDUAL_AD (maparea cade pe campanie / cont)."""
    ad = defaultdict(float); ad_day = defaultdict(float)
    for src, d, acct, camp, _n, sp in rows:
        if src == "tt":
            ad[(acct, d, camp)] += sp; ad_day[(acct, d)] += sp
    # conturile care NU se rescriu acum (n-au intors date, ex. fara permisiune) isi pastreaza randurile vechi:
    # le numaram ca baza, altfel completarea s-ar adauga peste ele (numarat de doua ori)
    rewritten = {o[1] for o in ok if o[0] == "tt"}
    for acct, d, camp, sp in conn.execute(
            "SELECT account, date, campaign, SUM(spend_usd) FROM wms_ad_spend WHERE source='tt' "
            "AND date BETWEEN ? AND ? AND ad_name <> ? GROUP BY 1, 2, 3", (start, end, RESIDUAL_AD)):
        if acct not in rewritten:
            ad[(acct, d, camp)] += sp or 0.0; ad_day[(acct, d)] += sp or 0.0
    mc = A.metrics_conn(); c = mc.cursor()
    c.execute('SELECT a.name, c.date::date::text, c."campaignName", SUM(c."spendRon") '
              'FROM tiktok_campaign_insights_daily c JOIN tiktok_ad_accounts a ON a.id = c."adAccountId" '
              'WHERE c.date::date BETWEEN %s AND %s GROUP BY 1, 2, 3', (start, end))
    wh = defaultdict(dict)
    for acct, d, camp, ron in c.fetchall():
        rate = wm._usd_ron(fx, d) or 0
        if rate > 0 and ron:
            wh[(acct.strip(), d)][camp or ""] = float(ron) / rate
    mc.close()
    out = []
    for (acct, d), camps in wh.items():
        gap = sum(camps.values()) - ad_day.get((acct, d), 0.0)
        if gap <= 0.5:
            continue
        diffs = {cn: v - ad.get((acct, d, cn), 0.0) for cn, v in camps.items()}
        diffs = {cn: x for cn, x in diffs.items() if x > 0.01}
        tot = sum(diffs.values())
        if tot <= 0:
            continue
        k = min(1.0, gap / tot)
        out += [("tt", d, acct, cn, RESIDUAL_AD, x * k) for cn, x in diffs.items()]
    sys.stderr.write("[tt] completare din warehouse: %d randuri, %.0f USD\n" % (len(out), sum(r[5] for r in out)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--platform", choices=["meta", "tiktok", "both"], default="both")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    end = a.until or datetime.date.today().isoformat()
    start = a.since or (datetime.date.today() - datetime.timedelta(a.days)).isoformat()
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout=15000;")
    fx = wm._load_fx(A.metrics_conn().cursor())

    rows, ok = [], set()
    if a.platform in ("meta", "both"):
        rows += fetch_meta(start, end, fx, ok)
    if a.platform in ("tiktok", "both"):
        rows += fetch_tiktok(start, end, fx, ok)
        rows += tiktok_residual(rows, start, end, fx, ok, conn)
    # Reclame diferite cu ACELASI nume in aceeasi campanie (ex. 41 de reclame "AD" in ALEX STRATEGY,
    # Bonhaus CZ) au aceeasi cheie in tabel: le ADUNAM, altfel INSERT OR REPLACE pastra doar ultima
    # (aug-sep 2026: Bonhaus CZ avea 25k $ din 56k $ reali).
    if a.since and RENAMED:
        # Recalcul pe ISTORIC: conturile redenumite au zilele vechi scrise sub alt nume (de care depinde maparea).
        # Nu le rescriem: altfel le-am avea sub ambele nume (numarate de doua ori) sau ar pierde maparea.
        rows = [r for r in rows if not (r[0] == "fb" and r[2] in RENAMED)]
        ok = {o for o in ok if not (o[0] == "fb" and o[1] in RENAMED)}
        print("   conturi redenumite, lăsate neatinse la recalculul istoric: %s" % ", ".join(sorted(RENAMED)))
    agg = defaultdict(float)
    for s_, d_, ac_, c_, n_, sp_ in rows:
        agg[(s_, d_, ac_, c_, n_)] += sp_

    per = defaultdict(lambda: [0, 0.0])
    for src, d, _a, _c, _n, sp in rows:
        per[(src, d)][0] += 1
        per[(src, d)][1] += sp
    print("\n== wms_ad_spend_api  %s .. %s  (%d rânduri) ==" % (start, end, len(rows)))
    for (src, d), (n, sp) in sorted(per.items()):
        print("   %-3s %s  rânduri=%-6d spend_usd=%.0f" % (src, d, n, sp))

    if not a.apply:
        old = defaultdict(float)
        for src_, acct_, cs_, ce_ in ok:
            for (v,) in conn.execute("SELECT COALESCE(SUM(spend_usd),0) FROM wms_ad_spend WHERE source=? AND account=? AND date BETWEEN ? AND ?",
                                     (src_, acct_, max(cs_, start), min(ce_, end))):
                old[(src_, acct_)] += v
        new = defaultdict(float)
        for (s_, d_, ac_, c_, n_), sp_ in agg.items():
            new[(s_, ac_)] += sp_
        print("\n   cont: acum în tabel -> după rescriere (USD)")
        for k in sorted(set(old) | set(new), key=lambda k: -abs(new.get(k, 0) - old.get(k, 0)))[:25]:
            print("   %-3s %-28s %10.0f -> %10.0f" % (k[0], k[1][:28], old.get(k, 0), new.get(k, 0)))
        print("   TOTAL %.0f -> %.0f" % (sum(old.values()), sum(new.values())))
        print("\nDRY-RUN — nimic scris. (--apply pentru rescriere în wms_ad_spend)")
        return 0
    if not rows:
        print("\n0 rânduri — NU ating tabela (fail-safe: nu golesc istoricul).")
        return 0

    now = datetime.datetime.now().isoformat(timespec="seconds")
    # Fiecare cont×zi citit CU SUCCES se RESCRIE complet: sterg ce era, scriu ce zice API-ul acum.
    # Altfel o campanie REDENUMITA (ex. barcode adaugat in nume) ramanea si sub numele vechi pe aceleasi
    # zile => spend NUMARAT DE DOUA ORI. Ferestrele esuate nu se ating (fail-safe).
    deleted = 0
    try:
        conn.execute("BEGIN")
        for src_, acct_, cs_, ce_ in sorted(ok):
            cur_ = conn.execute("DELETE FROM wms_ad_spend WHERE source=? AND account=? AND date BETWEEN ? AND ?",
                                (src_, acct_, max(cs_, start), min(ce_, end)))
            deleted += cur_.rowcount
        conn.executemany(
            "INSERT OR REPLACE INTO wms_ad_spend "
            "(source,date,account,campaign,ad_name,spend_usd,fetched_at) VALUES (?,?,?,?,?,?,?)",
            [k + (sp, now) for k, sp in agg.items()])
        conn.commit()
    except Exception:
        conn.rollback(); raise
    print("   sterse (rescrise) %d rânduri vechi pe %d ferestre cont×perioadă" % (deleted, len(ok)))
    for src in ("fb", "tt"):
        mx = conn.execute("SELECT MAX(date), COUNT(*) FROM wms_ad_spend WHERE source=?", (src,)).fetchone()
        print("   [%s] max_date=%s total_rânduri=%s" % (src, mx[0], mx[1]))
    print("APLICAT: %d rânduri (%d chei după adunarea reclamelor cu același nume)." % (len(rows), len(agg)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
