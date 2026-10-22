---
name: arona-scentum
description: "MCP-ul arona-scentum — citiri TIPATE din Scentum ERP (producția de parfumuri: Esteban, Nubra, GT, Lab Noir, Blink, Duppo, Zafra…), READ ONLY, în loc de SQL brut prin postgres-scentum. Stocul de esențe (și ambalaje/alcool/bază) cu semnal de recomandă (CRITIC <14 zile, COMANDĂ <74), fișa unei arome (stoc pe lanț esență→bază→produs finit, maturare, magazine, rețeta/BOM, necesar, livrări), livrările LIV-n (skipped / în ledger), necesarele de producție, loturile la maturat, consumul pe perioadă, comenzile la furnizor (PO ESENTE/AMBALAJE) și un SELECT liber păzit. Use when: 'cât stoc de esență avem', 'ce esențe trebuie comandate', 'fișa aromei 73', 'Good Girl în Scentum', 'ce e în LIV-88', 'livrările de săptămâna asta', 'necesarul EST-OCT', 'ce mai e de produs', 'ce iese de la maturat', 'consum esențe pe octombrie', 'PO esențe deschise'. Triggers: scentum, esență, esente, maturare, LIV-, necesar, plan producție, rețetă, BOM, consum esențe, PO esențe."
category: dev-tools
version: 1.0.1
---

# arona-scentum — MCP READ ONLY peste Scentum ERP

Server: `mcp_server.py` (FastMCP, stdio, `psycopg2`). Register:
`claude mcp add --scope user arona-scentum -- uv run <abs path>/plugins/gigi/skills/arona-scentum/mcp_server.py`

**De ce:** în 60 de zile echipa a rulat ~3.400 de query-uri brute prin `postgres-scentum` (Andreea 2.773,
Iulian 609, Anne 61). Tool-urile de aici acoperă întrebările repetate, cu schema deja încărcată și ieșire scurtă.

Conexiunea: secretul `DATABASE_URL_SCENTUM` (același ca `postgres-scentum`, rol fără superuser), luat prin
`core/scripts/arona_pg.py` (env întâi, apoi KB), niciodată printat. Fiecare query = tranzacție **READ ONLY**,
`statement_timeout 30s`, sesiune în UTC. Orele afișate sunt în **ora României** (DB-ul ține UTC fără fus).
**Scrierile** (necesar, livrare, recepție, PO, maturare) NU se fac de aici: `gigi:scentum` (CLI-ul app-ului).
Stocul împins în magazine după o recepție: `andreea:receptie-livrari-erp`.

## Tool-uri
| Tool | Ce întoarce |
|---|---|
| `esente_stoc(cauta="", tip="ESSENCE", doar_de_comandat=false, limita=80)` | stoc din loturi, consum/zi (60z și 14z), zile de acoperire, cât e deja pe PO deschis, **semnal** CRITIC/COMANDĂ/OK + `de comandat` (până la 74 de zile, minus PO). `tip` = ESSENCE/PACKAGING/ALCOHOL/PERFUME_BASE |
| `produs_scentum(sku_sau_nume)` | fișa aromei: stoc pe lanț (esență kg → bază L pe brand, cu ce e la maturat și când e gata → produs finit buc), variante în magazine (SKU, barcode, stoc oglindă, statut vânzări, buc/zi), rețeta/BOM, necesar deschis, ultimele livrări |
| `livrari_recente(zile=14, produs="", livrare="")` | LIV-n: branduri, linii, bucăți, recepționate/eșuate, **skipped** vs **în ledger**, SmartBill; cu `livrare` = liniile |
| `plan_productie(status="", brand="", necesar="")` | necesarele active (sau `status`/`toate`): cerut, produs, livrat, **rest de produs**; cu `necesar` = liniile |
| `maturare(zile_ramase=7, brand="", cauta="", include_maturate=false)` | loturile de bază care ies din maturare în ≤ N zile (+ cele gata, neîmbuteliate), sumar L la maturat / gata, ciorne deschise |
| `consum_esente(de_la="", pana_la="", zile=30, cauta="", tip="ESSENCE")` | consumul real din mișcări postate: maturare / producție / consum direct, ajustări minus separat, valoare la cost |
| `comenzi_furnizor(tip="", status="", cauta="", po="")` | PO ESENTE/AMBALAJE deschise: comandat / recepționat / rest, valoare + monedă, TOM (container, ETA); cu `po` = liniile |
| `scentum_sql(select, limita=200)` | SELECT liber, păzit: o singură instrucțiune SELECT/WITH, fără comentarii, fără cuvinte de scriere/DDL/SET/INTO, fără funcții de sistem (pg_read_file, pg_sleep, dblink, set_config…), învelit în `SELECT * FROM (…) LIMIT n` (≤ 1000), READ ONLY |

## Definiții (ca să reproduci cifrele)
- **Stoc** = `sum(inventory_lots.remaining_qty)` pe produs, în unitatea de bază (esență KG, bază L, produs finit PCS).
- **Consum** = `-qty` din `inventory_movements` cu `movement_status='POSTED'` și tip `MATURATION_ISSUE`,
  `PRODUCTION_ISSUE`, `CONSUMPTION`. `ADJUSTMENT_MINUS` = corecție de inventar, afișată separat, NU e consum.
- **Praguri** = cele din tabloul fabricii (`src/lib/services/factory-health.service.ts`): acoperire țintă **74 zile**
  (lead furnizor + maturare + tampon), critic **14 zile**, consum mediu pe **60 zile**.
  `de comandat = ceil(consum/zi × 74 − stoc − rest pe PO deschise)`. Forecastul complet de esențe (pe vânzări,
  cu lead/maturare separat) e în app: „Forecast Esențe” (`essence-forecast.service.ts`); semnalul de aici e cel
  de pe tabloul fabricii.
- **PO deschis** = status `DRAFT`, `APPROVED`, `PARTIALLY_RECEIVED`; rest = `quantity_ordered − quantity_received`.
- **Maturare**: `matures_on = receipt_date + maturation_days` (setări: 14). `zile_ramase` negativ = gata de atâtea zile.
- **Livrare**: `skipped == linii` → stocul e de împins (recepție „fără sincronizare”); `skipped 0` și
  `in_ledger == linii` → deja împinsă. `in_ledger` = intrarea în ledgerul masterului stock-sync, nu dovada scrierii
  în Shopify (vezi `andreea:receptie-livrari-erp`).
- **Necesar**: `rest de produs` = `requested − manufactured` doar pe liniile `OPEN`.

## Capcane
- Valorile PO: `total_cost_ron` ține de fapt suma în `currency` (ex. EUR la esențe) — tool-ul arată `valoare` + `moneda`.
- `products.supplier_id` e gol la majoritatea esențelor: furnizorul afișat vine din ultimul lot recepționat.
- Stocul din magazine din fișă e **oglinda** Scentum (`shopify_inventory_levels`), nu Shopify live. Pentru stocul
  real pe magazine/stații: MCP `arona-stock`.
- `scentum_sql` trimite SQL-ul **brut**, fără `vars` (`_q(..., None)`). Așa trebuie să rămână: psycopg2 face
  interpolare `%` ori de câte ori `vars` nu e None (**inclusiv tuplul gol**), așa că un `LIKE 'CUT-%'` sau
  `ILIKE '%print%'` era citit ca placeholder și întorcea `IndexError: tuple index out of range`
  (reparat 22-oct-2026). Tool-urile tipate rămân pe `_q(sql, (param,))` — acolo interpolarea e dorită.
  Regresia e prinsă de `uv run test_mcp_server.py` (nu cere baza de date).
