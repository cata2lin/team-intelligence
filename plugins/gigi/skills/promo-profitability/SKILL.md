---
name: promo-profitability
description: "Does the COD promo actually make money? Segments DELIVERED orders by units-per-order (1 / 2 / 3 = the 2+1 free promo / 4+) and shows net contribution per order at each tier — ex-VAT, with COGS charged on EVERY physical unit (including the free one) plus real transport. Reveals whether the buy-2-get-1-free / multi-buy bundle tier is profitable or eating margin. Use when the user asks if a promo is profitable, does 2+1 make money, bundle margin, multi-buy profitability, is the free-gift offer worth it, or promo contribution per order."
user-invokable: true
---

> **ARONA (gigi) — promo-ul COD e businessul, deci trebuie să facă bani.** La parfumuri (EST/GT/NUB)
> oferta standard e **2+1 gratis** → la GT ~81% din comenzile livrate au 3 buc. Skill-ul răspunde direct:
> nivelul „3 buc (2+1)" încă produce contribuție pozitivă după COGS+transport, sau mănâncă profitul?
> Sursă (din 24-oct-2026): **Order Hub, comenzi LIVRATE** (`/api/cs/livrabilitate`: total + transport real
> fără TVA; `/api/cs/comenzi-deschise`: bucățile pe comandă, ≤120 zile). OH nu ține prețul pe linie, deci
> `gross = Σ(qty × prețul de listă ACTUAL al variantei din Shopify)` — unitatea gratis intră la preț de listă
> ⇒ `COGS = cogs_pct × gross` taxează fiecare unitate fizică. `--source awb` (sau env `PROMO_PROFIT_SURSA=awb`)
> = AWBprint, doar pe server, întoarcere explicită. Vezi [[releaseit-cod-promo-model]] (cum sunt construite promo-urile),
> [[data-analytics-skill]] (clienți) și pipeline-ul canonic de profit (`profit_by_sku.py`) pt COGS real.

# promo-profitability — face promo-ul COD bani? (contribuție per nivel)

## Când o folosești
„2+1 chiar e profitabil?", „bundle-ul de 3 mănâncă marja?", „cât rămâne net pe comandă la 2+1 vs 1 buc?",
„merită cadoul gratis la magazinul X?".

## Cum rulezi
```bash
cd plugins/gigi/skills/promo-profitability/scripts
# cheia OH: env OH_TEAM_READ_TOKEN, altfel din KB (singură); tokenul Shopify din stores.csv (KB)
uv run promo_profit.py --store georgetalent.ro
uv run promo_profit.py --store esteban.ro --cogs-pct 0.28 --days 120
```
- `--store` (obligatoriu, domeniul public sau o parte din el: `georgetalent`, `grandia.ro`) · `--days` (default 90, ≤120 pe OH) · `--cogs-pct` (default 0.32 = COGS ca % din valoarea
  de listă) · `--vat` (default 1.21 RO; CZ/PL diferă).

## Ce calculează (per nivel: 1 / 2 / 3 (2+1) / 4+ buc)
- **net/cmd** = `total_price` mediu (venit COD autoritativ).
- **disc%** = discount efectiv = `(gross_listă − total) / gross_listă` (OH); pe awb `Σ discount_allocations / gross`.
- **COGS/cmd** = `cogs_pct × gross / vat` (pe TOATE unitățile, inclusiv cea gratis).
- **transp** = transportul real mediu (OH `shipments.cost_transport`, fără TVA, afișat cu TVA ×vat ca înainte).
- **CONTRIB/cmd** = `net/vat − COGS − transport/vat` (ex-TVA) · **marjă** = contrib / (net ex-TVA).
- 🟢 marjă ≥10% · 🟡 sub 10% · 🔴 PIERDERE.

## Cum citești
- Te uiți la rândul **„3 buc (2+1)"** (sau nivelul dominant): dacă e 🟢, promo-ul e sănătos; dacă e 🟡/🔴,
  oferta dă prea mult gratis pt economia produsului → ajustează (prag, preț, transport plătit).
- Compară marja pe niveluri: dacă marja scade brusc de la 1→3 buc, unitatea gratis e prea scumpă.
- `%` arată cât din volum e pe fiecare nivel (unde e de fapt businessul).

## OH vs AWBprint (comparat 24-oct-2026, 90 de zile)
- GT: 18.513 vs 18.585 livrate; aceleași procente pe niveluri (6/0/79/15%), net/cmd și COGS/cmd identice
  (3 buc: 150 / 50). Transportul: 13 vs 4 lei/cmd — AWBprint `orders.transport_cost` e gol din 28-aug, deci
  contribuția „3 buc" scade 71 → 64 (cifra OH e cea reală). disc% 21% vs 31%: altă formulă (vezi sus).
- Grandia: 9.420 vs 9.597; net/cmd 184/235/282/395 vs 183/236/281/402; transport 17–30 vs 6–7.

## Capcane
- Prețul de listă e cel ACTUAL din Shopify: un SKU cu preț schimbat în fereastră mută puțin COGS-ul estimat.
- **COGS = estimare** (`--cogs-pct` din listă), NU COGS real per-SKU. Valoarea ABSOLUTĂ a marjei e aproximativă;
  COMPARAȚIA între niveluri e robustă. Pt COGS exact rulează `profit_by_sku.py` ([[profitability-breakeven-model]]).
- Pe **delivered** (venit real) — refuzurile COD nu intră (corect: nu sunt venit), dar nici costul lor de transport
  nu e aici; pt impactul refuzului → [[deliverability-monitor]] / fulfillment.
- `total_price` poate include transport adăugat la comenzi sub prag → disc% ușor sub-estimat pe acele comenzi.
- TVA implicit RO (1.21); pt CZ/PL/BG pune `--vat` corect.
