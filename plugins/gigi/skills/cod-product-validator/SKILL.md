---
name: cod-product-validator
description: "Validate a product's REAL Cash-on-Delivery (COD) unit economics for the RO/EE market BEFORE you source, launch or scale it. Given sell price, supplier cost (or a category estimate), country, parcel size and payment type (COD vs prepay) it computes ex-VAT contribution per DELIVERED order, transport allocated over refusals, breakeven CPA, margin %, COGS% and a TEST / RISCANT / SKIP verdict — plus the BUNDLE (AOV-lift) math that fixes thin single-product margins. Mirrors the canonical profit_core logic (VAT per country, transport on ALL shipped incl. refused, refusal & COGS benchmarks per category). Use for 'does this product make money on COD', 'breakeven CPA', 'validate a dropship / sourcing product', 'profit per order', 'is the margin too thin', 'single vs bundle economics', 'ce CPA imi permit', 'merita produsul asta pe ramburs'."
user-invokable: true
tested_date: 2026-06-26
tested_with: claude-code v2.x
---

# COD Product Validator (RO/EE)

<!-- Created: 2026-06-26 -->

The #1 mistake in COD ecommerce is falling for a "winning product" whose margin **does not survive the refusal rate**. This validator answers, in one shot: *does this product make money on ramburs, and what CPA can I afford?*

## Why COD is different (the model)
- Revenue counts only on **DELIVERED** orders, **ex-VAT** (VAT per country).
- **Transport is paid on ALL shipped parcels** (incl. refused) → allocate it over delivered: `transport/(1-refuz)`.
- **COGS on delivered only** (refused goods return and are re-shipped — non-perishable).
- `Contributie/livrat = venit_ex − COGS_ex − transport/(1−refuz)`.
- `CPA breakeven = contributie` → the max you can pay on ads per delivered order and still break even.

## Run it
```
python3 validate.py --price 99 --cost 45 --country RO --size mic --pay cod
python3 validate.py --price 269 --category skincare --size mic          # cost auto from category
python3 validate.py --price 140 --cost 60 --pay prepay                  # prepay = ~0 refusal, no float
```
Outputs: verdict, **CPA breakeven**, contribution/order, margin%, COGS%, refusal, + risk flags. Benchmarks (VAT, transport, refusal/COGS per category) mirror `profit_core` and are editable defaults at the top of `validate.py`.

## Verdict thresholds
- **SKIP** — contribution ≤ 0 (loses money before ads even start).
- **RISCANT** — breakeven CPA < ~22 lei (only works with disciplined CPA + organic).
- **TESTEAZA** — breakeven CPA ≥ ~22 lei (has marketing oxygen).

## The lever that fixes "too cheap / CPA-dependent": BUNDLE
Transport is the SAME on a bundle as on a single (one parcel), and CPA is ~the same per ORDER regardless of order value — so a higher AOV has far more headroom. Validate the **bundle price** (routine/set), not the single SKU. Example: a 99-lei single may break even at CPA ~29; a 269-lei bundle breaks even at CPA ~99.

## The 4 profit levers (pull all of them)
1. **AOV up** → bundles/sets.
2. **LTV / repeat** → consumables + email/SMS; judge CPA against lifetime value, not the first order.
3. **CPA down** → good creative + an aged ad account.
4. **COGS down** → volume / private-label.

## Finding candidates worth validating (clean signal)
Pull demand from `arona-bi` (`gigi:sourcing-radar`), but **clean the noise first**:
- Exclude placeholder/unreliable parsers (e.g. `atmag` shows CLONED velocity across SKUs — 61% zero-stock; also nuoderm/pepita/vivre/jysk/souqshop/reducio/eiluminat/faunusplant).
- Require a **stable signal**: `ads7 ≈ ads30 ≈ ads90` (natural stock decay), `latest_stock` 5–600. Spikes = restock artifacts, not demand.
Then run each candidate through this validator before committing stock.

## Related
`gigi:sourcing-radar` (find products) · `gigi:fulfillment-analytics` / `gigi:deliverability-monitor` (real refusal rates) · `profit_core.py` (canonical economics) · `dtc-launch-playbook` (turn a validated product into a launch).
