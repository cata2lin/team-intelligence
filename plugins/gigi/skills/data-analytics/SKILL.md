---
name: data-analytics
description: "Customer analytics on the REAL delivered-order data (AWBprint) for any ARONA store — RFM segmentation (Champions / Loyal / At-Risk / Lost), cohort retention (do they re-buy?), LTV per cohort, and demand forecast (orders + revenue next N weeks). Identity = customer_email, money = DELIVERED orders only (COD refusals are not revenue). Use when the user asks about customer segments, RFM, who to win back, retention, repeat-purchase rate, churn, customer lifetime value (LTV), cohorts, or a sales/demand forecast."
user-invokable: true
---

> **ARONA (gigi) — de ce pe livrate, nu pe Shopify.** La noi vânzarea reală = coletul
> **LIVRAT** (COD): un refuz la ușă NU e venit. De-aia analiza de clienți se face pe comenzile **livrate din
> Order Hub** (starea din colete), nu pe comenzile brute din Shopify (care includ refuzurile). Monetarul e în
> **moneda magazinului** (RON la .ro, CZK la .cz, …) — de-aia rulezi PER magazin, nu amestecat.
> Vezi [[customer-service-tool]], [[fulfillment-analytics-skill]].

# data-analytics — analitică de CLIENȚI (RFM · Cohort · LTV · Forecast)

Răspunde la întrebările de tip „pe cine recâștig?", „revin clienții?", „cât valorează un
client?", „ce vânzări urmează?" — direct din datele de livrare reale, fără să lovești API-ul
Shopify și fără să confunzi comenzile refuzate cu venit.

## Când o folosești
- „Care clienți sunt **Champions** / pe cine pierd (**At-Risk**)?" → `rfm`
- „**Revin** clienții după prima comandă? Care e rata de **repeat**?" → `cohort`
- „Cât valorează un client (**LTV**) pe cohortă?" → `ltv`
- „Ce **vânzări/comenzi** urmează săptămânile viitoare?" → `forecast`

## Sursa (din 24-oct-2026): Order Hub, toate patru analizele, de oriunde
- `GET /api/cs/livrabilitate?luna=AAAA-LL&cu_client=true` (o lună pe cerere, în buclă). Cheia: env
  `OH_TEAM_READ_TOKEN` (→ `OH_PROFIT_CITIRE_TOKEN` → `OH_CS_SB_TOKEN`), altfel din KB. OH căzut = eroare, nu cifra veche.
- **Identitatea clientului = pseudonimul de TELEFON** (`client_telefon`, ultimele 9 cifre, sha256 cu sare peste
  indexul orb OH), cu `client_email` doar ca rezervă. NU emailul, ca pe AWBprint: pseudonimul de email e unic pe
  COMANDĂ până în iunie-2026 (0 clienți comuni între luni), deci ar ieși zero recumpărări înainte de iulie.
- Fereastra: `--since AAAA-LL` (implicit 2024-06). Lunile încheiate de peste 90 de zile stau în cache local
  (`~/.cache/arona/data-analytics`, doar pseudonime); `--fara-cache` le descarcă din nou. Prima rulare pe tot
  istoricul durează câteva minute (~9 MB/lună).
- Calea veche (AWBprint, `customer_email`) doar explicit: `--source awb` / `DATA_ANALYTICS_SURSA=awb`, și doar pe
  84.46 (de pe laptop AWBprint e refuzat).
- **Comparat 24-oct, de la 2025-04, OH vs AWBprint:**
  - esteban.ro: 193.566 vs 189.912 clienți; LTV cohorta 2025-11: 193 vs 188 lei, repeat 28,9% vs 26,9%;
    retenția M1 pe cohorte: 4–6% în ambele.
  - grandia.ro: 25.246 vs 20.432 clienți — AWBprint sare cele 4.974 de livrate fără email (19%), OH le prinde după
    telefon. LTV pe cohortă la ±5%, repeat la ±1 pp.
  - bonhaus.cz: 70.322 vs 51.879 clienți — OH are comenzile CZ din aprilie–septembrie 2025, AWBprint le are doar
    din octombrie-2025, iar statusurile intl din AWBprint sunt înghețate din 27-sep. Pe cohortele comune: LTV la
    ±1%, repeat la ±1 pp.
  - Segmentele RFM diferă mai mult decât totalurile: ~85% dintre clienți au o singură comandă, deci NTILE pe
    frecvență împarte la întâmplare egalitățile (și Postgres o face). Judecă pe cohortă/LTV, nu pe segmentul exact.
  - Belasil / Covoria nu sunt în OH.
- Test: `uv run scripts/test_sursa_oh.py`.

## Cum rulezi
```bash
cd plugins/gigi/skills/data-analytics/scripts

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
