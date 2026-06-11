---
name: grandia-product-marketing
description: Răspunde la întrebări despre marketingul și profitabilitatea Grandia PER PRODUS și PER CATEGORIE — cât a cheltuit un produs sau o categorie pe Facebook (împărțit în DIRECT vs CATEGORIE-alocat) și pe Google, vânzări/bucăți/COGS, profit net după reclama proprie, ROAS, și ce SKU-uri câștigă/pierd din reclame. Folosește pentru orice întrebare gen „P&L per produs Grandia", „cât a cheltuit produsul X pe FB", „ce produse Grandia pierd bani pe reclame", „CPA per categorie", „marketing direct vs pe categorie la Grandia". Citește LIVE din Postgres-ul Grandia; nu scrie niciodată nimic.
---

# Grandia — marketing & profit PER PRODUS (cu discernământ direct vs categorie)

Atribuie cheltuiala de reclame a Grandiei **pe produs**, distingând:
- **DIRECT** — campania FB rulată pe un produs anume → tot spend-ul la acel produs.
- **CATEGORIE** — campania FB rulată pe o categorie → spend-ul se **împarte pe produsele tipului**.
- **UNTRACKED** — catalog întreg („ALL ACTIVE") / grupă întreagă (iluminat, mobilier) / gunoi → nealocat pe produs (raportat separat).

Maparea campanie→produs/categorie trăiește **în acest skill** (clasificare după numele campaniei), NU în baza de date — pentru că parser-ul aplicației Grandia mapează după `[PID:xxx]` din numele ad-ului, nu din tabelul de mapări. Aici răspunzi la cerere, live.

## Cum rulezi
```bash
KB="${CLAUDE_PLUGIN_ROOT%/*/*}/core/scripts/kb.py"   # sau ../../../core/scripts/kb.py
cd "<acest folder>"
uv run grandia_pmkt.py summary  --month 2026-05                  # totaluri: Google / FB-direct / FB-categorie / untracked
uv run grandia_pmkt.py pnl      --month 2026-05 --losers         # SKU-uri pe pierdere (sau --winners)
uv run grandia_pmkt.py product  "oglinda baie led" --month 2026-05   # detaliu pe un produs
uv run grandia_pmkt.py category "Oglinzi LED" --month 2026-05    # total + CPA + ROAS pe categorie
```
Secretul `DATABASE_URL_GRANDIA` se ia automat din KB (sau din env). Conexiunea e read-only ca intenție (doar SELECT-uri).

## Cum se calculează (ca să fie credibil)
- **FB** = `fbads_raw_spend_rows` (sourceType STANDARD), dedup `MAX(spend)` per `reportDate`+`fbAdId` = spend real per campanie. Fiecare campanie e clasificată direct/categorie/untracked după nume. **O singură sursă FB → fără dublă numărare.**
- **Google** = `gads_daily_product_spend` (deja per produs).
- **Vânzări/COGS** = `OrderLineItem` (join pe `productId = Product.shopifyGid`) + `Variant.costPerItem`. TVA scos (÷1.21) la venit și COGS, ca în motorul de profit.
- **Categorie** = spend-ul campaniei împărțit EGAL pe produsele active ale tipului (`productType`). (FB nu ne dă breakdown-ul real per produs pentru astea.)

## Limitări de știut
- Grupele „toate" (iluminat interior, mobilier, rafturi+biblioteci) și catalogul „ALL ACTIVE" → **UNTRACKED** (nu se pot împărți pe un singur `productType`; sunt raportate separat în `summary`).
- Split-ul pe categorie e EGAL pe produsele tipului (aproximare; breakdown-ul real FB nu există pentru campaniile non-DPA).
- Maparea numelor de campanie e ~85-90% automată; câteva nume ambigue pot cădea pe UNTRACKED.

## De extins (idei)
- Split categorie ponderat pe vânzări (în loc de egal).
- Alocarea UNTRACKED (catalog) pe produse proporțional cu venitul.
- Aceeași logică pentru alte branduri dacă apar tabele FB per-produs.
