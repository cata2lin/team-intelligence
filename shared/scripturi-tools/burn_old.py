# /// script
# requires-python=">=3.10"
# dependencies=["requests"]
# ///
"""Scoate din coada de print a depozitului etichetele VECHI (anulate) ramase in xConnector.
Descarcarea unei etichete o marcheaza `downloaded` => iese din coada TUTUROR statiilor.
Se descarca STRICT etichetele al caror `t=` NU e AWB-ul curent al comenzii."""
import sqlite3, os, json, sys, re, requests
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
cur = json.load(open("awb_map.json"))
db = sqlite3.connect(os.path.expanduser("~/.arona_print_queue.db"))
apply = "--apply" in sys.argv
tot = 0
for n, awb_curent in cur.items():
    r = db.execute("select label_urls from queue where order_name=?", (n,)).fetchone()
    if not r:
        continue
    for u in json.loads(r[0] or "[]"):
        m = re.search(r"[?&]t=(\d+)", u)
        if not m:
            print("  ?? nu pot citi tracking-ul din", n, u[:60]); continue
        t = m.group(1)
        if t == awb_curent:
            continue                      # eticheta BUNA — nu o atingem
        tot += 1
        if not apply:
            print(f"  {n}: as scoate {t} (curent {awb_curent})")
            continue
        try:
            resp = requests.get(u, timeout=60)
            print(f"  {n}: scos {t} -> HTTP {resp.status_code} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"  {n}: EROARE la {t}: {e}")
print(("AS SCOATE " if not apply else "SCOASE ") + str(tot) + " etichete vechi")
