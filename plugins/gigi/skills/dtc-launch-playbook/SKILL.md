---
name: dtc-launch-playbook
description: "Playbook for launching a NEW DTC store / brand on ARONA rails with a small budget. Covers the MONEY/TIME/SKILL decision framework (you must pay in at least one), the 4 profit levers (AOV via bundles, LTV via repeat, low CPA, low COGS via private-label), ORGANIC-FIRST (TikTok) validation vs paid, the phased 0-3 / 3-6 / 6+ roadmap (validate -> semi-passive -> brand/asset), and the find-product -> validate-COD-economics -> find-&-verify-supplier flow — including RO supplier verification (firm registry CUI/TVA on termene.ro/risco.ro/listafirme) and cosmetics compliance (CPNP + RO/EU responsible-person label, Reg. CE 1223/2009). Use for 'plan to launch a new store/brand', 'bootstrap ecommerce plan', 'how much budget do I need', 'organic-first validation', 'cum fac bani dintr-un magazin nou', 'ce buget imi trebuie', 'how do I make a business from this', 'dropshipping vs stoc'."
user-invokable: true
tested_date: 2026-06-26
tested_with: claude-code v2.x
---

# DTC Launch Playbook (bootstrap, ARONA rails)

<!-- Created: 2026-06-26 -->

How to take someone from "I want a store" to a concrete, survivable launch. Method only — plug in the actual product/market later.

## 1. Pin the goal + the currency (do this FIRST)
Every business is paid in one of three currencies — **you cannot pay zero in all three**:
| Currency | How you pay | Best when |
|---|---|---|
| 💰 Money | buy growth (ads, stock) | have capital, want speed |
| ⏱️ Time | grind organic content, do it all | little money, patient |
| 🛠️ Skill | sell your expertise (service) | want fast cash, accept it's not passive/an asset |
Ask: how much **money** (at-risk cap), how many **hours/week**, how much **hands-on work**. That answers "which model", not the product.
Also pin the goal: passive cash / long-term independence / fast money. "Passive NOW + independent LONG-TERM" = **one vehicle, two horizons** (semi-passive cash in months, brand/asset after).

## 2. Reframe traps that keep people from starting
- **"It must be my passion"** → wrong filter for a cash+independence business. Right filter: *"a business I understand and can WIN at."* Excitement usually follows competence + early wins.
- **"Too cheap / depends on CPA"** → fix with **bundles** (AOV) + **repeat/LTV**. See `cod-product-validator`.
- **"Big ad investment"** → only if you pay in money. Time-rich founders go **organic-first** (ads = supplement from profit).
- **"It takes too long"** → true of any real asset; but money + signal come in **weeks** (see roadmap). The ramp is the price of an asset that pays for years.

## 3. Dropship vs stock (COD-RO reality)
Pure perpetual dropshipping in COD-RO is usually ~breakeven/negative (supplier COGS 60%+ + refusal + ads) AND the ramburs **float** (courier remits ~2-3 weeks after you pay costs) ties up more capital than inventory does. Winning shapes:
- **Validate cheap → STOCK the winner** (COGS drops, control quality, fast local delivery = low refusal).
- **Prepay / custom** products kill float + refusal (but lower cold conversion).
- Never run China/EU-prepay as **live COD** (slow delivery = lethal refusal); use them as back-end supply.

## 4. The launch flow
1. **Find product** — clean-signal demand from `arona-bi` (`gigi:sourcing-radar`); exclude placeholder/cloned parsers (e.g. `atmag`), require stable `ads7≈ads30≈ads90`.
2. **Validate economics** — run each candidate through `cod-product-validator` (contribution, breakeven CPA, verdict). Kill anything SKIP/RISCANT.
3. **Find & verify supplier** — see §5.
4. **Launch organic-first** — build store (`gigi:shopify-stores`), bundle offers (`gigi:cro`), TikTok/UGC engine; small paid test on the aged ARONA ad account.
5. **Scale the winner** — reorder deep on the 1-2 that sell, drop the rest; retention (`gigi:klaviyo`); externalize fulfillment (3PL) + CS.

## 5. Find & verify a supplier (RO)
- **Hit 3-4 in parallel** (never bottleneck on one inbox); WhatsApp channels answer fastest.
- **Verify the firm is REAL**: CUI + active + VAT payer on `termene.ro` / `risco.ro` / `listafirme.ro`. Reject blog-only / unverifiable names.
- **Cosmetics compliance (hard gate)**: require in writing **CPNP notification + RO/EU responsible-person label** (Reg. CE 1223/2009). No proof → don't buy (grey-market import = ANPC fine + unsellable).
- Ask every supplier: wholesale price/SKU + volume tiers, real MOQ, delivery time + shipping, current stock, COD/AWB terms.
- Order **shallow-and-wide first** (validate winners) → **deep on the winner** later. Fast local restock makes a stockout a good signal, not a disaster.

## 6. Budget split (small cap, organic-first example)
Stock (small test lot) · setup (Shopify/domain/free apps) · product-for-UGC-creators (barter) · a modest ads test · reserve/restock. Ads are a **supplement**, not the engine — time + skill are the fuel. Real downside if it fails ≈ ads + creator product (stock is recoverable).

## 7. Roadmap (two horizons)
- **M0-3 (grind):** setup, content, find winner. First sales in ~2-3 weeks; winner signal in ~4-6 weeks. Expect test/small-loss, not profit.
- **M3-6 (semi-passive):** scale the winner, externalize ops, retention → cash with less hands-on.
- **M6+ (brand/independence):** private-label the winner (COGS↓, margin↑), widen the range → an asset that compounds and can be sold.

## Related
`cod-product-validator` (validate economics) · `gigi:sourcing-radar` (find products) · `gigi:shopify-stores` · `gigi:cro` · `gigi:klaviyo` · `gigi:tiktok-content` / `gigi:social` · Google Ads launch playbook.
