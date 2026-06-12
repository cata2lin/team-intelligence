---
name: cs-stock-answer
description: Instant Customer-Service answer to the ~640 presale stock questions a month — "is it in stock?", "when does it come back?", "will it ever come back?" — without manually digging through 16 Shopify admins. Searches the LIVE per-store stock (analytics_products, ~5.200 SKUs across every Arona store) by SKU or product-title, shows availability PER STORE (so CS can cross-sell — "it's sold out at GT but in stock at Nubra"), and when a product is out of stock everywhere it pulls the restock ETA from the TOM warehouse (purchase_order_items type=RESTOCK): ORDERED → "back ~date", SHIPPED/RECEIVED → "back in a few days", CANCELLED → "not coming back, offer an alternative". Output is a 2-3 line status plus a ready-to-send customer message in Romanian (and short CZ/PL/BG). Use for "is product X on stock", "stoc produs", "mai e pe stoc", "cand revine pe stoc", "when back in stock", "restock ETA", "will it come back", "out of stock answer", "presale stock question", "ce stoc avem la". Read-only.
---

# CS — Răspuns instant la întrebări de stoc (presale)

Răspunde pe loc la „mai e pe stoc? / când revine? / mai revine?" fără să cauți manual în 16 admin-uri.
Caută stocul LIVE pe toate magazinele, arată disponibilitatea PE MAGAZIN (up-sell cross-magazin) și, dacă e epuizat peste tot, scoate ETA-ul de reaprovizionare din TOM. Îți dă și mesajul gata de trimis clientului.

## Cum rulezi
```bash
uv run cs_stock_answer.py --sku gt-140                     # după SKU (exact sau parțial)
uv run cs_stock_answer.py --product "aparat foto instant"  # după cuvânt-cheie din titlu
uv run cs_stock_answer.py --sku HA-0094 --store Magdeal     # filtrează / preferă un magazin
uv run cs_stock_answer.py --product "incalzitor diesel" --json   # pt automatizare
```

## Ce primești
- **Disponibilitate per magazin** (prefix → nume): stoc, preț, SKU. Dacă produsul există în mai multe magazine, le vezi pe toate → poți redirecționa clientul („nu mai e la GT, dar e la Nubra").
- **VERDICT** + **mesaj client** (RO + scurt CZ/PL/BG, gata de copy-paste):
  - `PE STOC` → „Da, e pe stoc, se poate comanda acum."
  - `REVINE ÎN CÂTEVA ZILE` (restock SHIPPED/RECEIVED) → reasigurare + ofertă de anunțare.
  - `REVINE ~ data` (restock ORDERED) → ETA aproximativ = `orderedAt + ~15 zile` (lead mediu AIR măsurat din TOM).
  - `NU mai revine` (restock CANCELLED + cancelReason) → oferă alternativă.
  - `verdict necunoscut` → nicio reaprovizionare găsită în TOM → verifică manual / oferă alternativă.

## Cum funcționează (surse)
- **Stoc live**: SQLite pe VPS `data/product_analytics.db` → `analytics_products(sku, title, price, prefix, inventory_qty)` (~5.200 SKU). Pe `--sku` caută exact + LIKE; pe `--product` caută LIKE pe titlu. Grupează pe titlu → pe magazin (prefix).
- **ETA restock** (doar dacă e epuizat peste tot): Postgres TOM → `purchase_order_items` (`type='RESTOCK'`) JOIN `products`. Potrivire: întâi `externalSku`/`products.sku` exact, apoi `externalTitle ILIKE` pe cuvintele-cheie din titlu (SKU-urile Shopify per-variantă ≠ SKU-urile interne TOM, deci titlul e puntea sigură). Verdictul ia statusul cu cea mai apropiată sosire (SHIPPED > RECEIVED > ORDERED > NEW > CANCELLED).

## Note / limite
- Matching-ul TOM e best-effort pe titlu pt produsele fizice (Grandia/Magdeal/etc.); la parfumuri titlurile diferă între magazine, deci pe perfumuri ETA poate lipsi — atunci verdictul e „verifică manual".
- Lead-time-ul e o aproximare simplă (nu desfacem `po_item_events`); SHIPPED arată deja `receivedQty` în TOM (intră pe stoc în câteva zile).
- READ-ONLY total. Nu scrie nimic în Postgres / Shopify / Richpanel. Niciun secret în output.
