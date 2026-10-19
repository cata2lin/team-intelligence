---
name: arona-stock
description: "MCP-ul arona-stock — întrebările de STOC prin tool-uri tipate, live: bazinul stock-sync (sync.arona.ro) pe depozite Bartolomeu / Uzina 2 / Teliu și pe magazine, rafturile AWB Arona per stație, erorile care îngheață bazine, sănătatea unui magazin (magazin-ok), marfa blocată (stoc-blocat), ponderile per magazin (ponderi-stoc, dry-run) și planul de reaprovizionare. Use when: 'depozitul are marfă dar magazinul arată 0', 'se vinde pe stoc 0', 'unde e fizic produsul', 'ce e pe Uzina 2', 'de ce e înghețat bazinul', 'e ok magazinul X', 'marfă invizibilă', 'cât stoc primește fiecare magazin', 'cât reaprovizionez'. Triggers: stoc produs, stoc statie, stock-sync, bazin, ponderi, magazin ok, stoc blocat, reorder."
category: operations
version: 1.0.0
---

# arona-stock — MCP pentru stoc

Server: `mcp_server.py` (FastMCP, stdio). Register:
`claude mcp add --scope user arona-stock -- uv run <abs path>/plugins/gigi/skills/arona-stock/mcp_server.py`

| Tool | Ce răspunde | Sursă |
|---|---|---|
| `stoc_produs(sku)` | bazin + per depozit + NUMĂRAT? + per magazin (unități, depozit, regulă) + listinguri nelegate + rafturi fizice | `scripts/stoc_live.py produs` |
| `stoc_statie(statie)` | ce e pe rafturile unei stații, bucket-uri LOC-*, poziții negative | `scripts/stoc_live.py statie` |
| `stoc_erori(cod)` | erori DESCHISE stock-sync (bazine înghețate, COUNTED_STORE_RISE_IGNORED…) | `scripts/stoc_live.py erori` |
| `magazin_ok(magazin)` | problemă de business + cauză tehnică pe fiecare variantă | `/root/Scripturi/magazin_ok.py` (SB `gigi:magazin-ok`) |
| `stoc_blocat(magazine)` | masteri cu stoc fără niciun listing MATCHED + cauza | `scripts/stoc_blocat.py` (copie SB `gigi:stoc-blocat`) |
| `ponderi_stoc(..., apply=false)` | ponderi PRODUCT_STORE pe vânzări — DRY-RUN implicit | `/root/Scripturi/ponderi_stoc.py` (versiunea din cron) |
| `stoc_bucket_vs_shopify(cod)` | numărat pe un bucket vs Shopify live vs master | `/root/Scripturi/station_report.py` |
| `reorder_plan(brand)` | cât și când comanzi | `../reorder-planner/scripts/reorder_planner.py` (local) |

Totul rulează pe serverul de scripturi prin `core/scripts/arona_ssh.py` (cheie SSH); scripturile din `scripts/`
pleacă pe stdin (`python -`), deci nu se copiază nimic pe server. Doar citire, cu excepția `ponderi_stoc(apply=true)`.

## Capcane (de ce cifrele diferă)
- **Trei cifre**: raftul (AWB Arona, fizic) ≠ masterul stock-sync ≠ ce arată Shopify. Un produs NUMĂRAT
  (`countedWarehouses` nevid) își ia stocul din raft: cifra bătută în Shopify e ignorată; marfa se pune pe raft.
- Bazinul e (produs × DEPOZIT): un magazin care nu expune depozitul cu marfa primește 0, oricât procent ar avea.
- `magazin_ok` ia vânzările 30z din AWBprint (în urmă față de Order Hub) — pentru comenzi/statusuri folosește OH.

## Neexpuse (TODO — scrieri fără dry-run testat)
Ajustare master / recepție (`POST /v1/master-products/:id/stock-adjustments`), mutare între depozite,
`station_master.py` / `station_place.py` (anterioare regulii D-023: pe produsele numărate scrierea în Shopify e ignorată),
`gigi:stock-pool-repair`, `gigi:stock-correct-from-count` (scrie direct în Shopify, model vechi), revert ponderi.
