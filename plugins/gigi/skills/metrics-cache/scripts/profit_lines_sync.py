# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx>=0.27"]
# ///
"""
Populează `profit_order_lines` (SQLite profitability.db) cu datele PER-LINIE care lipsesc din engine:
(month, prefix, order_name, sku, qty, line_revenue, line_cogs). Venit pe linie = discountedTotalSet (net,
gestionează 2+1 free → linia gratis = 0). status_category NU se stochează aici — se ia prin JOIN din
profit_orders la raportare (nu recalculăm logica de status). ADITIV: tabel nou, nu atinge profit_orders/engine.

  cd /root/Scripturi && .venv/bin/python profit_lines_sync.py 2026-05 [PREFIX|all] [--proba]

COGS pe linie = aceeași regulă ca engine-ul (profit_core.cost_unitar_linie): override > lot Order Hub cu
navlu (dacă profit_settings.cogs_sursa='oh') > costPerItem Shopify. --proba scrie în /tmp/profit_lines_proba.db.
"""
import sys, os, asyncio, sqlite3
from collections import defaultdict
sys.path.insert(0, "/root/Scripturi"); os.chdir("/root/Scripturi")
from datetime import datetime, timezone, timedelta
import httpx
from core.stores import list_stores
from api.profitability import _fetch_variant_costs, DEFAULT_API_VERSION, DATA_DIR
import profit_core as pc

DB = str(DATA_DIR / "profitability.db")
EXT_GQL = """query($q:String!,$cursor:String){
  orders(first:100, query:$q, after:$cursor){
    pageInfo{hasNextPage endCursor}
    edges{node{ name createdAt
      lineItems(first:100){edges{node{ quantity sku
        discountedTotalSet{shopMoney{amount}} variant{id} }}}
    }}
  }
}"""


def _sursa_cost():
    """(cogs_sursa, overrides) — aceleași setări pe care le citește engine-ul, ca liniile și comenzile să
    aibă același cost. cogs_sursa='oh' ⇒ lotul Order Hub (cu navlu) bate Shopify, prin profit_core."""
    c = sqlite3.connect(DB)
    try:
        r = c.execute("SELECT value FROM profit_settings WHERE key='cogs_sursa'").fetchone()
        sursa = (r[0] if r else "engine") or "engine"
    except sqlite3.Error:
        sursa = "engine"
    try:
        ov = {k: v for k, v in c.execute("SELECT sku, unit_cost FROM profit_cogs_override")}
    except sqlite3.Error:
        ov = {}
    c.close()
    return sursa, ov


def _oh_token():
    return pc.cheie_oh_citire()


def _ensure():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS profit_order_lines (
        month TEXT, prefix TEXT, order_name TEXT, sku TEXT,
        qty INTEGER, line_revenue REAL, line_cogs REAL,
        PRIMARY KEY (month, prefix, order_name, sku))""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pol_msku ON profit_order_lines(month, sku)")
    c.commit(); c.close()


async def sync_store(cl, st, ym, start, end, sursa="engine", overrides=None, surse=None):
    qs = (start - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    qe = (end + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    q = f"status:any created_at:>={qs} created_at:<{qe}"
    shop, token, prefix = st["shop"], st["token"], st["prefix"]
    url = f"https://{shop}/admin/api/{DEFAULT_API_VERSION}/graphql.json"
    H = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    cursor = None; vids = set(); raw = []; ok = True
    while True:
        r = None; j = None
        for i in range(8):
            try:
                r = await cl.post(url, headers=H, json={"query": EXT_GQL, "variables": {"q": q, "cursor": cursor}}, timeout=60)
                if r.status_code == 429: await asyncio.sleep(2 * (i + 1)); continue
                j = r.json()
                # THROTTLED vine cu HTTP 200 și `errors` în corp: tratat ca „gata", lăsa luna PARȚIALĂ fără nicio
                # eroare (23-oct: GRAN 643 de linii în loc de ~3.800). Reîncercăm; dacă persistă → fetch incomplet.
                if j.get("errors"):
                    await asyncio.sleep(3 * (i + 1)); j = None; continue
                break
            except Exception: await asyncio.sleep(1)
        if j is None:
            ok = False; break        # toate retry-urile au eșuat → fetch INCOMPLET, nu scriem luna
        try:
            d = (j.get("data") or {}).get("orders") or {}
        except Exception:
            ok = False; break
        for ed in d.get("edges") or []:
            n = ed.get("node") or {}
            if (n.get("createdAt", "")[:7]) != ym: continue
            agg = {}
            for le in (n.get("lineItems") or {}).get("edges") or []:
                ln = le.get("node") or {}
                vid = str((ln.get("variant") or {}).get("id") or "");
                if vid: vids.add(vid)
                sku = (ln.get("sku") or "").strip()
                lr = float(((ln.get("discountedTotalSet") or {}).get("shopMoney") or {}).get("amount") or 0)
                qty = int(ln.get("quantity") or 0)
                agg.setdefault((sku, vid), [0, 0.0]);  agg[(sku, vid)][0] += qty; agg[(sku, vid)][1] += lr
            raw.append((n.get("name"), (n.get("createdAt") or "")[:10], agg))
        pi = d.get("pageInfo") or {}
        if not pi.get("hasNextPage"): break
        cursor = pi.get("endCursor")
    if not ok:
        return None                  # NU scrie luna parțial (evită pierderea silențioasă — ca build_cache)
    vcost = await _fetch_variant_costs(cl, shop, token, list(vids)) if vids else {}
    loturi = {}
    if sursa == "oh":
        coduri = sorted({(sku or (vcost.get(vid, {}).get("sku") or "")).strip()
                         for _, _, agg in raw for (sku, vid) in agg} - {""})
        try:
            loturi = await asyncio.to_thread(pc.cost_loturi_oh, coduri, _oh_token(), shop)
        except RuntimeError as e:
            # comutatorul e pe OH: liniile scrise pe Shopify ar arăta ca o lună întreagă fără cost transport
            # maritim, fără niciun semn. Mai bine păstrăm liniile de ieri și spunem de ce.
            print(f"  {prefix}: ⚠ Order Hub fără loturi ({str(e)[:80]}) — păstrez liniile existente")
            return None
    # G1: agregă pe (comandă, SKU) — același SKU pe 2 variante NU mai face coliziune PK; linii fără SKU → UNKNOWN_SKU.
    perkey = defaultdict(lambda: [0, 0.0, 0.0])   # (name,key) -> qty, line_revenue, line_cogs
    for name, zi, agg in raw:
        for (sku, vid), (qty, lr) in agg.items():
            key = sku or (vcost.get(vid, {}).get("sku") or "") or "UNKNOWN_SKU"
            # Aceeași regulă ca engine-ul (profit_core.cost_unitar_linie): override > lot cu navlu > Shopify.
            uc, sursa_l = pc.cost_unitar_linie(key if key != "UNKNOWN_SKU" else "", zi or None,
                                               loturi, overrides, vcost.get(vid, {}).get("unit_cost"))
            if surse is not None:
                surse[sursa_l] += qty
            d = perkey[(name, key)]; d[0] += qty; d[1] += lr; d[2] += (uc or 0) * qty
    return [(ym, prefix, name, key, q, round(lr, 2), round(cg, 2)) for (name, key), (q, lr, cg) in perkey.items()]


PROBA_DB = "/tmp/profit_lines_proba.db"


def _proba_scrie(ym, prefix, rows):
    """--proba: liniile calculate merg într-un DB separat, pentru comparația pe SKU; cel real rămâne neatins."""
    c = sqlite3.connect(PROBA_DB)
    c.execute("""CREATE TABLE IF NOT EXISTS profit_order_lines (month TEXT, prefix TEXT, order_name TEXT,
                 sku TEXT, qty INTEGER, line_revenue REAL, line_cogs REAL, PRIMARY KEY (month, prefix, order_name, sku))""")
    c.execute("DELETE FROM profit_order_lines WHERE month=? AND prefix=?", (ym, prefix))
    c.executemany("INSERT OR REPLACE INTO profit_order_lines VALUES (?,?,?,?,?,?,?)", rows)
    c.commit(); c.close()


async def main(ym, which):
    y, m = ym.split("-"); m = int(m)
    start = datetime(int(y), m, 1, tzinfo=timezone.utc)
    end = datetime(int(y) + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)
    stores = list_stores()
    if which != "all":
        stores = [s for s in stores if s["prefix"] == which]
    _ensure()
    sursa, overrides = _sursa_cost()
    if sursa == "oh":
        try:
            pc.cere_cheie_oh("profit_lines_sync (cogs_sursa=oh)")
        except pc.CheieOHLipsa as e:
            sys.exit("EROARE: %s" % e)
    proba = "--proba" in sys.argv
    print(f"sursa COGS = {sursa} · {len(overrides)} override-uri" + (" · PROBĂ: nu scriu" if proba else ""))
    async with httpx.AsyncClient() as cl:
        for st in stores:
            surse = defaultdict(int)
            try:
                rows = await sync_store(cl, st, ym, start, end, sursa, overrides, surse)
            except Exception as e:
                print(f"  {st['prefix']}: ERR {type(e).__name__} {str(e)[:80]}"); continue
            if rows is None:
                print(f"  {st['prefix']}: ⚠ fetch INCOMPLET — păstrez datele existente (NU șterg/scriu)"); continue
            if proba:
                _proba_scrie(ym, st["prefix"], rows)
                print(f"  {st['prefix']}: {len(rows)} linii (probă) · buc pe sursă {dict(surse)}")
                continue
            c = sqlite3.connect(DB)
            c.execute("DELETE FROM profit_order_lines WHERE month=? AND prefix=?", (ym, st["prefix"]))
            c.executemany("INSERT OR REPLACE INTO profit_order_lines VALUES (?,?,?,?,?,?,?)", rows)
            c.commit(); c.close()
            print(f"  {st['prefix']}: {len(rows)} linii")
    print(f"DONE {ym} ({which})")


if __name__ == "__main__":
    _arg = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(_arg[0], _arg[1] if len(_arg) > 1 else "all"))
