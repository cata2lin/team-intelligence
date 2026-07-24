#!/usr/bin/env python3
"""Sync profit_orders pt o luna DIRECT prin run_profitability (fara app web / scripts_cli.py).
Backup de siguranta + verificare scadere. Usage: profit_orders_sync.py YYYY-MM"""
import asyncio, sys, os, json, sqlite3
os.chdir("/root/Scripturi"); sys.path.insert(0, "/root/Scripturi")
DB = "/root/Scripturi/data/profitability.db"

def total(month):
    c = sqlite3.connect(DB); n = c.execute("SELECT count(*), sum(status_category='Livrata') FROM profit_orders WHERE month=?", (month,)).fetchone(); c.close(); return n

async def sync(month):
    from api.profitability import run_profitability, RunRequest
    tag = "_bak_po_" + month.replace("-", "")
    c = sqlite3.connect(DB); c.execute(f"DROP TABLE IF EXISTS {tag}")
    c.execute(f"CREATE TABLE {tag} AS SELECT * FROM profit_orders WHERE month=?", (month,)); c.commit(); c.close()
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
            if t in ("phase", "error") or "salvat" in m or "Tracking" in m or "complet" in m.lower():
                print(f"  [{t}] {m[:130]}", flush=True)
    a_tot, a_liv = total(month)
    print(f"[{month}] DUPA: {a_tot} comenzi, {a_liv} livrate  (inainte {b_tot}/{b_liv})", flush=True)
    if a_tot < b_tot * 0.85:
        print(f"[{month}] ⚠️⚠️ SCADERE MARE — un magazin probabil a esuat. Restaureaza din {tag}!", flush=True)
        return 2
    print(f"[{month}] ✅ OK (+{a_liv-b_liv} livrate re-numarate)", flush=True)
    return 0

if __name__ == "__main__":
    rc = asyncio.run(sync(sys.argv[1] if len(sys.argv) > 1 else "2026-06"))
    sys.exit(rc or 0)
