#!/usr/bin/env python3
"""Sync profit_orders pt o luna DIRECT prin run_profitability (fara app web / scripts_cli.py).
Backup de siguranta + verificare scadere. Usage: profit_orders_sync.py YYYY-MM

OGLINDA în git a /root/Scripturi/profit_orders_sync.py (84.46.242.181). Deploy: scp în /root/Scripturi/.

GARDA PE MAGAZIN (22-oct-2026): pragul vechi era 15% pe TOT tabelul — Esteban a pierdut 9.622 de comenzi
(19.384 → 9.762, 13–21 oct, ~1,14 M lei venit) și scriptul a scris „✅ OK", fiindcă 59.705 → 52.085 e doar −12,8%.
Acum, după rulare, fiecare prefix se compară cu backup-ul: un magazin care pierde peste PRAG_MAGAZIN din comenzi
(și avea cel puțin MIN_COMENZI) e RESTAURAT din backup pe loc, iar scriptul iese cu 2 (cronul nu mai pinguie
heartbeat-ul → data_health semnalează). Rândurile restaurate poartă statusul din backup; tracking-ul următor le
reîmprospătează. `_bak_po_<lună>` rămâne starea de dinainte de rulare, ca până acum."""
import asyncio, sys, os, json, sqlite3
os.chdir("/root/Scripturi"); sys.path.insert(0, "/root/Scripturi")
DB = "/root/Scripturi/data/profitability.db"
PRAG_MAGAZIN = 0.10      # un prefix care scade cu peste 10% față de backup = eșec de descărcare, nu realitate
MIN_COMENZI = 100        # sub atâtea comenzi în backup, o scădere e zgomot (magazin închis, comenzi anulate)

def total(month):
    c = sqlite3.connect(DB); n = c.execute("SELECT count(*), sum(status_category='Livrata') FROM profit_orders WHERE month=?", (month,)).fetchone(); c.close(); return n

def pe_prefix(c, tabel, month):
    return dict(c.execute(f"SELECT prefix, count(*) FROM {tabel} WHERE month=? GROUP BY prefix", (month,)).fetchall())

def restaureaza_prefixe(month, tag, pierdute):
    """Pune la loc din backup prefixele pierdute: șterge ce a rămas din ele în tabela vie și copiază rândurile din backup."""
    c = sqlite3.connect(DB)
    cols = [r[1] for r in c.execute("PRAGMA table_info(profit_orders)") if r[1] != "id"]
    lista = ", ".join(cols)
    with c:
        for p in pierdute:
            c.execute("DELETE FROM profit_orders WHERE month=? AND prefix=?", (month, p))
            c.execute(f"INSERT INTO profit_orders ({lista}) SELECT {lista} FROM {tag} WHERE month=? AND prefix=?", (month, p))
    dupa = pe_prefix(c, "profit_orders", month); c.close()
    return {p: dupa.get(p, 0) for p in pierdute}

async def sync(month):
    from api.profitability import run_profitability, RunRequest
    tag = "_bak_po_" + month.replace("-", "")
    c = sqlite3.connect(DB); c.execute(f"DROP TABLE IF EXISTS {tag}")
    c.execute(f"CREATE TABLE {tag} AS SELECT * FROM profit_orders WHERE month=?", (month,)); c.commit()
    inainte = pe_prefix(c, tag, month); c.close()
    b_tot, b_liv = total(month); print(f"[{month}] backup '{tag}' | INAINTE: {b_tot} comenzi, {b_liv} livrate", flush=True)
    req = RunRequest(month=month, resync_shopify=True, force=True)
    resp = await run_profitability(req)
    it = getattr(resp, "body_iterator", resp)
    async for chunk in it:
        s = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in s.splitlines():
            line = line.strip()
            if not line.startswith("data:"): continue
            try: d = json.loads(line[5:].strip())
            except Exception: continue
            t = d.get("type"); m = d.get("message", "")
            if t in ("phase", "error") or "salvat" in m or "Tracking" in m or "complet" in m.lower() or "EȘUAT" in m:
                print(f"  [{t}] {m[:130]}", flush=True)
    a_tot, a_liv = total(month)
    print(f"[{month}] DUPA: {a_tot} comenzi, {a_liv} livrate  (inainte {b_tot}/{b_liv})", flush=True)

    # garda pe magazin: ce a scăzut peste prag față de backup se pune la loc din backup
    c = sqlite3.connect(DB); dupa = pe_prefix(c, "profit_orders", month); c.close()
    pierdute = sorted(p for p, n in inainte.items()
                      if n >= MIN_COMENZI and dupa.get(p, 0) < n * (1 - PRAG_MAGAZIN))
    if pierdute:
        for p in pierdute:
            print(f"[{month}] ⚠️ {p}: {inainte[p]} → {dupa.get(p, 0)} comenzi (−{100 - 100 * dupa.get(p, 0) / inainte[p]:.0f}%) "
                  f"— descărcare incompletă, RESTAUREZ din {tag}", flush=True)
        rest = restaureaza_prefixe(month, tag, pierdute)
        print(f"[{month}] restaurate din backup: " + ", ".join(f"{p}={n}" for p, n in rest.items()), flush=True)
        a_tot, a_liv = total(month)
        print(f"[{month}] ⚠️⚠️ EȘEC pe {len(pierdute)} magazin(e) — rândurile vechi puse la loc ({a_tot} comenzi). "
              f"Cauza e în descărcarea Shopify (vezi logul de mai sus).", flush=True)
        return 2
    if a_tot < b_tot * 0.85:
        print(f"[{month}] ⚠️⚠️ SCADERE MARE — un magazin probabil a esuat. Restaureaza din {tag}!", flush=True)
        return 2
    print(f"[{month}] ✅ OK (+{a_liv-b_liv} livrate re-numarate)", flush=True)
    return 0

if __name__ == "__main__":
    rc = asyncio.run(sync(sys.argv[1] if len(sys.argv) > 1 else "2026-06"))
    sys.exit(rc or 0)
