---
name: scentum
description: Operează Scentum ERP (app-ul de producție parfumuri — Esteban, Nubra, George Talent, Lab Noir, Artevita, Zafra…) din terminal, prin serviciile CANONICE ale aplicației (aceeași logică ca UI-ul, nu SQL brut). Acum — generează DIRECT un "Necesar Producție" (rulează Forecastul velocity → creează Necesarul DRAFT PR-{BRAND}-{n}, cantități sugerate). Use pentru "fă un necesar", "necesar producție", "generează necesar Esteban/Nubra", "cât să producem", "forecast producție", "production requirement", "Scentum", "PR-EST", "necesar de la Andreea pentru Vali".
argument-hint: "generate-necesar --brand ESTEBAN [--yes] [--lookback 60 --forecast-days 60]"
---

# scentum
> Author: **Gigi**. Operează Scentum ERP din CLI, prin serviciile canonice ale aplicației.

## Ce e Scentum
ERP-ul de **producție parfumuri** (repo privat `contact546/scentum`, Next.js + Prisma + Postgres).
Fluxul central: **Forecast** → **Necesar Producție** (`ProductionRequirement`, Andreea → Vali) →
**Livrare** → recepție în Shopify.

> ⚠️ Mutațiile NU sunt expuse pe HTTP (API-ul are doar PDF/upload/sync). Ele trăiesc în
> **server actions** (`src/app/actions/*`) care cheamă **servicii** (`src/lib/services/*`).
> De aceea orice script trebuie să ruleze **în interiorul repo-ului**, importând serviciile —
> așa păstrezi logica de business (validări, audit, numerotare), nu scrii SQL brut.

## Setup (o dată pe mașină)
```bash
gh repo clone contact546/scentum ~/Downloads/scentum && cd ~/Downloads/scentum
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
printf 'DATABASE_URL=%s\n' "$(uv run "$KB" secret-get DATABASE_URL_SCENTUM)" > .env   # NU se comite (.gitignore)
npm install && npx prisma generate
```
Apoi copiază scriptul din acest skill în repo:
```bash
cp <skill-dir>/scripts/generate-necesar.ts ~/Downloads/scentum/scripts/
```

## Necesar Producție — generează direct
```bash
cd ~/Downloads/scentum
npx tsx scripts/generate-necesar.ts --brand ESTEBAN                 # DRY-RUN (nu creează nimic)
npx tsx scripts/generate-necesar.ts --brand ESTEBAN --yes           # creează Necesarul DRAFT
# opțiuni: --lookback 60 --forecast-days 60 --lead 14 --round 50 --min 1 --title "..." --notes "..."
```
Branduri: `ESTEBAN · NUBRA · GEORGE-TALENT · LAB-NOIR · ART · ZAFRA · NICHE · BLINK · EU`.

Ce face: rulează `ForecastService.runForBrand` → citește liniile din `ForecastRow` → afișează tabelul
(SKU · vândute · stoc · în producție · viteză/zi · **SUGERAT**) → cu `--yes` cheamă
`ProductionRequirementService.create({brandId, forecastRunId, items})` → **DRAFT `PR-{BRAND}-{n}`**.

## Formula (canonică, din `prisma/schema.prisma`)
```
velocity        = netUnitsSold(lookback) / inStockDays      (corectat pe zile OOS; fallback /lookbackDays)
forecastDemand  = ceil(velocity × forecastDays)
raw             = max(0, forecastDemand − onHand − pendingIncoming)
suggestedQty    = roundUpToUnit(raw, brand.productionRoundingUnit)   // default 50
```
`pendingIncoming` = `OPEN.requestedQty + COMPLETED.manufacturedQty` din Necesarele ne-terminale.

## Notes / capcane
- **DRY-RUN implicit** — creează Necesarul doar cu `--yes`. (Dar dry-run-ul RULEAZĂ forecastul, deci
  scrie un `ForecastRun` — la fel ca butonul din UI. E o analiză, nu o mutație de business.)
- **Gardă STRICT**: dacă brandul are variante **nemapate** pe `ScentMaster`, forecastul e blocat —
  scriptul refuză și-ți listează variantele de mapat (Mapare Produse în UI).
- Necesarul se creează **DRAFT** — aprobarea/producția rămân în fluxul normal (Vali).
- Numerotare `PR-{BRAND}-{n}`; Necesar multi-brand = `NP-{n}` (dacă `brandId` e omis).
- Secretul DB: KB `DATABASE_URL_SCENTUM`. Nu-l printa, nu comite `.env`.
- **Mutații suplimentare** (aprobare, anulare, mark-manufactured, livrări, recepție) — vezi serviciile
  `production-requirement.service.ts`, `delivery.service.ts`, `delivery-receive.service.ts`. (CLI-ul
  pentru ele vine în pasul următor al acestui skill.)
