---
name: arona-stock
description: "MCP-ul arona-stock — întrebările de STOC prin tool-uri tipate, live: bazinul stock-sync (sync.arona.ro) pe depozite Bartolomeu / Uzina 2 / Teliu și pe magazine, rafturile AWB Arona per stație, erorile care îngheață bazine, sănătatea unui magazin (magazin-ok), marfa blocată (stoc-blocat), ponderile per magazin (ponderi-stoc, dry-run), planul de reaprovizionare, plus SCRIERI cu dry-run: RECEPȚIA unui container pe o stație și MUTAREA de marfă între stații fără dublă numărare. Use when: 'băgă în stoc containerul C53 la Teliu', 'recepția pe C54, 55, 56', '1844 -400 0115-240 au fost mutate de la Uzina la Bartolomeu, scade-le de la Uzina', 'ce a fost adăugat la Bartolomeu azi trebuie scăzut din Uzina', 'depozitul are marfă dar magazinul arată 0', 'se vinde pe stoc 0', 'unde e fizic produsul', 'ce e pe Uzina 2', 'de ce e înghețat bazinul', 'e ok magazinul X', 'marfă invizibilă', 'cât stoc primește fiecare magazin', 'cât reaprovizionez'. Triggers: stoc produs, stoc statie, stock-sync, bazin, ponderi, magazin ok, stoc blocat, reorder, receptie container, mutare stoc, scade din Uzina."
category: operations
version: 1.1.2
---

# arona-stock — MCP pentru stoc

Server: `mcp_server.py` (FastMCP, stdio). Register:
`claude mcp add --scope user arona-stock -- uv run <abs path>/plugins/gigi/skills/arona-stock/mcp_server.py`

| Tool | Ce răspunde | Sursă |
|---|---|---|
| `stoc_produs(sku)` | bazin + per depozit + NUMĂRAT? / ESTIMAT (Teliu) + per magazin (unități, depozit, regulă) + listinguri nelegate + rafturi fizice | `scripts/stoc_live.py produs` |
| `stoc_statie(statie)` | ce e pe rafturile unei stații, bucket-uri LOC-*, poziții negative | `scripts/stoc_live.py statie` |
| `stoc_erori(cod)` | erori DESCHISE stock-sync (bazine înghețate, COUNTED_STORE_RISE_IGNORED…) | `scripts/stoc_live.py erori` |
| `magazin_ok(magazin)` | problemă de business + cauză tehnică pe fiecare variantă | `/root/Scripturi/magazin_ok.py` (SB `gigi:magazin-ok`) |
| `stoc_blocat(magazine)` | masteri cu stoc fără niciun listing MATCHED + cauza | `scripts/stoc_blocat.py` (copie SB `gigi:stoc-blocat`) |
| `ponderi_stoc(..., apply=false)` | ponderi PRODUCT_STORE pe vânzări — DRY-RUN implicit | `/root/Scripturi/ponderi_stoc.py` (versiunea din cron) |
| `stoc_bucket_vs_shopify(cod)` | numărat pe un bucket vs Shopify live vs master | `/root/Scripturi/station_report.py` |
| `reorder_plan(brand)` | cât și când comanzi | `../reorder-planner/scripts/reorder_planner.py` (local) |
| `receptie_container(container, statie, lista, include_grandia, apply=false)` | recepția unui container pe „Fără raft · Uzina 2/Teliu" — **dry-run implicit** | `scripts/stoc_scriere.py receptie` |
| `mutare_stoc(lista, din, in_, rafturi, apply=false)` | mutare între stații fără dublă numărare — **dry-run implicit** | `scripts/stoc_scriere.py mutare` |

Totul rulează pe serverul de scripturi prin `core/scripts/arona_ssh.py` (cheie SSH); scripturile din `scripts/`
pleacă pe stdin (`python -`), deci nu se copiază nimic pe server. Doar citire, cu excepția `ponderi_stoc`, `receptie_container` și `mutare_stoc` cu `apply=true`.

## Scrieri: recepție + mutare (`scripts/stoc_scriere.py`, pe serverul de scripturi)
Ținta = **numărătoarea din AWB Arona** (`data/locations.db`), NU stock-sync și NU Shopify: la produsele numărate
(D-023) cifra din Shopify e ignorată, iar masterul se actualizează singur la `awb_to_stocksync` (L–V 15:30 / 23:30).
Rulează întâi FĂRĂ apply, arată tabelul ownerului, abia apoi `apply=true` (+ `force=true` doar dacă o gardă oprește
și ownerul confirmă). Exemple:
- „băgă în stoc C53 la Teliu" → `receptie_container("C53", "Teliu")` (+ `include_grandia=true` dacă e marfă Grandia).
- „recepția pe C54, 55, 56" → `receptie_container("C54, 55, 56", "Uzina 2")`.
- „1844 -400 0115-240 mutate de la Uzina la Bartolomeu" → `mutare_stoc("1844 -400 0115-240", "Uzina", "Bartolomeu")`.
- „ce s-a pus azi la Bartolomeu, scade din Uzina" → `mutare_stoc("azi", "Uzina", "Bartolomeu")`.

**Recepție** — liniile din `container_expected` (sync din foaia „Tom - recepții containere", lunea 07:12) sau `lista=`
lipită. Sare: Grandia (o recepționează depozitul; `include_grandia`), linii „fără sku", ambalaje necunoscute în
catalog; cod de bare cu două produse (cu/fără 0 în față) = AMBIGUU → oprire. Pe fiecare SKU scade (1) ce e deja
recepționat (MOTIV „Receptie Cxx" de oricine + jurnalul `receipts`) și (2) ce a pus deja depozitul de la sosire —
pe orice „Fără raft" sau pe rafturile stației, plus zilele ≥50 buc pe rafturile altei stații (C55: 3.002 la
Bartolomeu pe 23-sep). Data sosirii = `container_meta.arrived_at`, altfel din titlul tabului („C54-24Sept").
Scrie pe `LOC-<STAȚIE>` cu user `receptie-owner` + MOTIV + rând `receipts` + `arrived_at`. Bartolomeu = refuzat.

**Mutare** — spre **Bartolomeu**: DOAR scădere la sursă, cât a apărut nou pe rafturile Bartolomeu în fereastră;
nescanatul rămâne la sursă (scăzut acum ar dispărea de pe site; `fara_scanare=true` la cererea ownerului). Spre
**Uzina 2 / Teliu**: adaugă pe „Fără raft" al destinației (minus ce a scanat ea) și scade la sursă, în aceeași
tranzacție, adăugarea prima. Nu se scade de două ori ce a trecut prin aplicație („din Fără raft · Uzina 2",
transfer între stații) sau printr-o mutare anterioară cu MOTIV. Scăderile fără motiv de la sursă sunt doar ATENȚIE
(nu se împerechează automat — memoria scaderea-fara-motiv). Sursa = „Fără raft" apoi „Depozit mic"; altfel
`rafturi="ha-1844=RF-…"`. Avertizează când SKU-ul ajunge la 0 (zero_auto pune masterul pe 0 în ~5 min).

**Gărzi comune**: plan calculat sub `BEGIN IMMEDIATE` (exact ce se scrie), backup consistent
`data/locations.db.bak-<op>-<ts>`, jurnal `data/arona_stock_scrieri.jsonl` (cheie de idempotență); aceeași listă
în 24h e refuzată fără `force`. Dovedit pe date reale (dry-run, 19-oct): C64 și C54 recunoscute ca recepționate,
C53 recunoscut ca pus la Teliu pe 8-sep, lista 17-sep „1844 -400 0115-240" recunoscută ca deja scăzută; aplicarea
testată doar pe o COPIE a bazei.

## Capcane (de ce cifrele diferă)
- **Trei cifre**: raftul (AWB Arona, fizic) ≠ masterul stock-sync ≠ ce arată Shopify. Un produs NUMĂRAT
  (`countedWarehouses` nevid) își ia stocul din raft: cifra bătută în Shopify e ignorată; marfa se pune pe raft.
- **Teliu = ESTIMAT (neinventariat)**, nu numărat (ownerul, 21-oct-2026): rămâne vandabil și rămâne în
  `countedWarehouses` (regula D-023 din stock-sync neschimbată — scoaterea lui ar muta cantități), dar `stoc_produs`
  îl afișează „ESTIMAT (neinventariat)". Nu-l folosi ca dovadă de stoc numărat.
- Bazinul e (produs × DEPOZIT): un magazin care nu expune depozitul cu marfa primește 0, oricât procent ar avea.
- `magazin_ok` ia vânzările 30z din AWBprint (în urmă față de Order Hub) — pentru comenzi/statusuri folosește OH.
- `magazin_ok`: stock-sync poate ține unități pe un rând de depozit `null` (fără depozit); apare ca `FARA DEPOZIT` în `marfa_in_stock_sync` / „DEPOZIT GRESIT” (reparat 21-oct, înainte crăpa cu TypeError, ex. Apreciat).

## Neexpuse (TODO — scrieri fără dry-run testat)
Ajustare master direct în stock-sync (`POST /v1/master-products/:id/stock-adjustments`) — recepția și mutarea se fac
acum în AWB Arona (mai sus), iar jobul publică;
`station_master.py` / `station_place.py` (anterioare regulii D-023: pe produsele numărate scrierea în Shopify e ignorată),
`gigi:stock-pool-repair`, `gigi:stock-correct-from-count` (scrie direct în Shopify, model vechi), revert ponderi.
