---
name: cs-ghost-shipments
description: Detects "ghost shipments" for Customer Service — a shipping label exists (AWB issued, often printed) but the courier NEVER scanned the parcel, so it is still in the warehouse while the customer waits (angriest WISMO tickets). Source = Orders (Order Hub), all stores including eMAG / OLX / Trendyol: the CS queue /api/cs/neplecate-vechi plus the per-parcel guards /api/cs/printate-neculese (printed, nobody picked it, courier never saw it) and /api/cs/culese-nepredate (picked, courier never scanned it). No profit engine, no AWBprint. Output per store, sorted by age then value, with order, age, reason, station, paid flag, AWB and a suggested action. Use for "ghost shipments", "label printed but not shipped", "etichetă printată dar coletul n-a plecat", "colete fantomă", "printate neculese", "culese dar nepredate", "pre-pickup WISMO", "comenzi care n-au plecat din depozit". Read-only.
---

# CS — Colete fantomă (etichetă printată, coletul n-a plecat)

Eticheta s-a printat (AWB emis), Shopify a trimis clientului mail „s-a expediat" — dar curierul NU l-a scanat niciodată la ridicare. Coletul stă în depozit. Acestea generează cele mai furioase tichete WISMO. Acoperă gaura PRE-PICKUP pe care `cs-proactive-delays` (doar in-tranzit) o ratează.

## Cum rulezi
```bash
uv run cs_ghost_shipments.py                  # fantome >3 zile, toate magazinele
uv run cs_ghost_shipments.py --days 5         # prag vechime 5 zile
uv run cs_ghost_shipments.py --store Esteban  # un singur magazin
uv run cs_ghost_shipments.py --json           # pt automatizare
```

## Cum funcționează
- **Sursa = Orders (Order Hub)**, din 24-oct-2026, trei rute citite cu aceeași cheie (env `OH_TEAM_READ_TOKEN` → `OH_PROFIT_CITIRE_TOKEN` → `OH_CS_SB_TOKEN`, altfel din KB):
  - `GET /api/cs/neplecate-vechi` — coada de lucru CS (comenzile neplecate mai vechi de N zile, cu motivul);
  - `GET /api/cs/printate-neculese?ore=` — etichetă printată, nimeni n-a cules-o, curierul n-a văzut-o, pe TOATE magazinele;
  - `GET /api/cs/culese-nepredate?ore=` — culeasă, curierul n-a scanat-o; marketplace-ul exclus doar fără nicio citire la curier.
  Pragul gărzilor = `12 × --days` ore LUCRĂTOARE (luni–vineri, 08–20). Rândul gărzii (pe colet) bate rândul cozii pe aceeași comandă.
  De ce: coada CS sare marketplace-urile, deci o comandă eMAG plătită, printată și neculeasă din 21-oct și una culeasă, lăsată la ridicarea Sameday, nu apăreau (cazurile: jurnalul intern din 24-oct).
- FANTOMĂ = comandă neplecată cu etichetă vie: `culeasa_nepredata` (culeasă, curierul n-a scanat-o), `eticheta_in_teanc` (printată, pierdută din teanc), `eticheta_neprintata`, `awb_nepreluat`.
- FĂRĂ TRACKING = fără etichetă vie, dar marcată expediată în Shopify.
- Coletele pierdute în tranzit nu-s „neplecate" → `cs-proactive-delays`.
- Contact (nume/telefon/oraș) din `metrics.orders`. Output per magazin, sortat după vechime apoi valoare; acțiune sugerată pe motivul OH + sumar.
- Calea veche (`profit_orders` prin SSH + AWBprint) doar explicit: `--source awb` / `CS_GHOST_SURSA=awb`. Dacă OH nu răspunde → eroare, nu cifra veche. Test: `uv run test_sursa_oh.py`.

## Note
- Aproape toate fantomele sunt DPD-RO cu „Shipment data received" — semnal curat de etichetă nescanată.
- Cele foarte vechi (>7 zile) = aproape sigur uitate în depozit → re-expediere/refund + scuze, nu doar mesaj.
