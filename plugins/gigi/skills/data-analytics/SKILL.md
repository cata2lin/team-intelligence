---
name: data-analytics
description: "Customer analytics on the REAL delivered-order data (AWBprint) for any ARONA store — RFM segmentation (Champions / Loyal / At-Risk / Lost), cohort retention (do they re-buy?), LTV per cohort, and demand forecast (orders + revenue next N weeks). Identity = customer_email, money = DELIVERED orders only (COD refusals are not revenue). Use when the user asks about customer segments, RFM, who to win back, retention, repeat-purchase rate, churn, customer lifetime value (LTV), cohorts, or a sales/demand forecast."
user-invokable: true
---

> **ARONA (gigi) — de ce pe AWBprint, nu pe Shopify.** La noi vânzarea reală = coletul
> **LIVRAT** (COD): un refuz la ușă NU e venit. De-aia analiza de clienți se face pe
> **AWBprint** (`orders.aggregated_status='delivered'`), nu pe comenzile brute din Shopify
> (care includ refuzurile). Identitatea clientului = **`customer_email`** (355k clienți unici,
> prezent pe ~443k comenzi). Monetarul e în **moneda magazinului** (RON la .ro, CZK la .cz, …) —
> de-aia rulezi PER magazin, nu amestecat. Sursa: secret `DATABASE_URL_AWBPRINT` din KB,
> conexiune **read-only**. Vezi [[customer-service-tool]], [[fulfillment-analytics-skill]].

# data-analytics — analitică de CLIENȚI (RFM · Cohort · LTV · Forecast)

Răspunde la întrebările de tip „pe cine recâștig?", „revin clienții?", „cât valorează un
client?", „ce vânzări urmează?" — direct din datele de livrare reale, fără să lovești API-ul
Shopify și fără să confunzi comenzile refuzate cu venit.

## Când o folosești
- „Care clienți sunt **Champions** / pe cine pierd (**At-Risk**)?" → `rfm`
- „**Revin** clienții după prima comandă? Care e rata de **repeat**?" → `cohort`
- „Cât valorează un client (**LTV**) pe cohortă?" → `ltv`
- „Ce **vânzări/comenzi** urmează săptămânile viitoare?" → `forecast`

## Cum rulezi
```bash
cd plugins/gigi/skills/data-analytics/scripts
# secretul vine din KB automat; sau exportă-l o dată ca să fie instant:
export DATABASE_URL_AWBPRINT="$(uv run ../../../../core/scripts/kb.py secret-get DATABASE_URL_AWBPRINT)"

uv run data_analytics.py all      --store esteban.ro          # toate 4 analizele
uv run data_analytics.py rfm      --store esteban.ro
uv run data_analytics.py cohort   --store esteban.ro --months 12
uv run data_analytics.py ltv      --store esteban.ro
uv run data_analytics.py forecast --store esteban.ro --weeks 8
```
- `--store` = filtru `ILIKE` pe domeniul public (`esteban.ro`, `bonhaus.cz`, `casaofertelor.ro`,
  `georgetalent.ro`, …). Default `esteban.ro`.
- `--store ""` = TOATE magazinele — **atenție: amestecă monede** (RON+CZK+…), folosește doar pt
  numărători, nu pt valoare.
- `--months` (cohort, implicit 12) · `--weeks` (forecast, implicit 8).

## Ce calculează (definiții)
- **RFM** — fiecare client primește scor 1-5 pe **R**ecency (ultima comandă), **F**requency
  (nr comenzi livrate), **M**onetary (venit livrat) prin `NTILE(5)`. Combinate în segmente
  acționabile:
  - 🏆 **Champions** (R≥4, F+M mare) — cei mai buni, recenți + valoroși → tratează-i VIP.
  - 💚 **Loyal** — revin constant.
  - ✨ **New/Promising** — recenți, încă o comandă → nurture.
  - ⚠️ **At-Risk (valoroși, dispar)** — au cheltuit mult dar n-au mai comandat → **ținta #1 de win-back** (Klaviyo flow / SMS).
  - 💀 **Lost/Hibernating** — vechi + rari → cost mic de reactivare.
  - 😐 **Need attention** — mijlocul.
- **Cohort retention** — grupează clienții după **luna primei comenzi livrate**, apoi % care
  recumpără în M1, M2, … M6. M0=100% prin definiție. Arată dacă produsul ține clientul.
- **LTV per cohortă** — pe fiecare cohortă (lună primă comandă): nr clienți, comenzi/client,
  **venit mediu livrat/client (LTV)**, **Repeat%** (clienți cu 2+ comenzi). Cohortele vechi au
  LTV mai mare (au avut timp să recumpere) — citește-le ca plafon de maturitate.
- **Forecast cerere** — comenzi + venit livrat pe săptămână (52 săpt), aruncă săptămâna parțială
  curentă, medie mobilă (MA8) + trend (ultimele 4 săpt vs precedentele 4) amortizat → prognoză
  N săptămâni. Model simplu, fără sezonalitate fină (pt aia = model dedicat).

## Cum legi rezultatele de acțiuni
- **At-Risk mare** → exportă segmentul și pornește un flow de win-back în [[klaviyo]] (gigi:klaviyo)
  / SMS; corelează cu [[cs-refused-recovery]] dacă pleacă de la refuzuri.
- **Repeat% mic / retenție plată** → problemă de produs/post-purchase, nu de achiziție;
  cross-check [[product-quality-radar]] și [[cross-sell]].
- **LTV pe cohortă** → plafonul realist de **CAC**; compară cu breakeven din
  [[profitability-breakeven-model]] / `gigi:fulfillment-analytics`.
- **Forecast** → planificare stoc ([[stock-restock-alerts]]) și buget ads.

## Capcane (citește)
- **NU folosi Shopify brut** pt clienți — include refuzurile COD = venit fals. Sursa corectă =
  AWBprint `delivered`. Vezi [[metrics-warehouse-orders-incomplete]].
- **Monedă per magazin** — nu compara valoarea RON cu CZK; rulează separat pe fiecare.
- **`customer_email` gol** e exclus (filtru `<>''`) — un client fără email apare ca anonim, nu se
  leagă comenzile lui; acoperirea e ~bună dar nu 100%.
- **Refund parțial nu se scade** — `total_price` e valoarea comenzii livrate; pt P&L real vezi
  pipeline-ul de profitabilitate (`profit_by_sku.py`), nu acest tool (ăsta e despre CLIENȚI).
- Read-only garantat (`set_session(readonly=True)`).
