---
name: scentum
description: Operează Scentum ERP (app-ul de producție parfumuri — Esteban, Nubra, George Talent, Lab Noir, Artevita, Zafra, Niche, Blink, EU) din terminal, prin serviciile CANONICE ale aplicației (aceeași logică ca UI-ul: validări, audit, numerotare — NU SQL brut). Face TOATE mutațiile disponibile în Scentum — generează "Necesar Producție" din Forecast (velocity → PR-{BRAND}-{n} DRAFT), aprobă/anulează, adaugă/șterge linii, marchează fabricat, creează/aprobă/anulează Livrări și face Recepția (push stoc în Shopify). Use pentru "fă un necesar", "necesar producție", "generează necesar Esteban/Nubra", "cât să producem", "forecast producție", "aprobă necesarul", "marchează fabricat", "creează livrare", "recepționează livrarea", "Scentum", "PR-EST", "production requirement", "Andreea → Vali".
argument-hint: "necesar generate --brand ESTEBAN [--yes] | necesar approve --id X --yes | livrare receive --id X --yes --confirm-shopify"
---

# scentum
> Author: **Gigi**. Operează Scentum ERP din CLI, prin serviciile canonice ale aplicației.

## Ce e Scentum
ERP-ul de **producție parfumuri** (repo privat `contact546/scentum`, Next.js + Prisma + Postgres).
Lanțul central: **Forecast** → **Necesar Producție** (`ProductionRequirement`, Andreea → Vali) →
**Livrare** → **Recepție** (stocul intră în Shopify).

> ⚠️ **Mutațiile NU sunt pe HTTP.** API-ul expune doar PDF/upload/sync. Scrierile trăiesc în
> **server actions** (`src/app/actions/*`) → **servicii** (`src/lib/services/*`). De aceea CLI-ul
> rulează **în interiorul repo-ului**, importând serviciile — așa păstrezi logica de business
> (validări, tranziții de stare, audit, numerotare). **Nu scrie SQL brut în Scentum.**

## Setup (o dată pe mașină)
```bash
gh repo clone contact546/scentum ~/Downloads/scentum && cd ~/Downloads/scentum
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
# ⬇️ FOLOSEȘTE userul cu drepturi minime (scentum_rw), NU DSN-ul de superuser:
printf 'DATABASE_URL=%s\n' "$(uv run "$KB" secret-get DATABASE_URL_SCENTUM_RW)" > .env   # NU se comite
npm install && npx prisma generate
cp <skill-dir>/scripts/*.ts scripts/          # scentum-cli.ts + generate-necesar.ts
```

> 🔑 **Ca să SCRII în Scentum îți trebuie `DATABASE_URL_SCENTUM_RW`** (rol `scentum_rw`: SELECT/INSERT/
> UPDATE/DELETE, fără superuser, fără DDL). ⚠️ **MCP-ul `postgres-scentum` e READ-ONLY prin design** —
> din el NU poți scrie niciodată; mutațiile se fac DOAR cu CLI-ul de mai jos.
> `DATABASE_URL_SCENTUM` (superuser) e doar pentru admin/migrații — nu-l împrăștia.

## CLI — toate mutațiile
**Mutațiile sunt DRY-RUN implicit** → scriu doar cu `--yes`. Recepția cere în plus `--confirm-shopify`.
```bash
cd ~/Downloads/scentum
npx tsx scripts/scentum-cli.ts                      # help

# CITIRE (fără efecte)
npx tsx scripts/scentum-cli.ts brands
npx tsx scripts/scentum-cli.ts necesar list [--brand ESTEBAN] [--status DRAFT]
npx tsx scripts/scentum-cli.ts necesar show --id <prId>
npx tsx scripts/scentum-cli.ts livrare list|show --id <id>
npx tsx scripts/scentum-cli.ts livrare eligible     # linii Necesar COMPLETED, libere de livrare

# NECESAR
npx tsx scripts/scentum-cli.ts necesar generate --brand ESTEBAN [--lookback 60 --forecast-days 60 --round 50] --yes
npx tsx scripts/scentum-cli.ts necesar create   --brand NUBRA --title "..." --yes     # DRAFT gol
npx tsx scripts/scentum-cli.ts necesar add-line --id <prId> --variant <variantId> --qty 50 --yes
npx tsx scripts/scentum-cli.ts necesar set-qty  --item <itemId> --qty 80 --yes
npx tsx scripts/scentum-cli.ts necesar remove-line --item <itemId> --yes
npx tsx scripts/scentum-cli.ts necesar approve  --id <prId> --yes
npx tsx scripts/scentum-cli.ts necesar cancel   --id <prId> --reason "..." --yes
npx tsx scripts/scentum-cli.ts necesar mark     --item <itemId> --qty 40 --yes    # fabricat (parțial OK)
npx tsx scripts/scentum-cli.ts necesar cancel-line --item <itemId> --reason "..." --yes

# LIVRARE
npx tsx scripts/scentum-cli.ts livrare create   [--items id1,id2] [--notes "..."] --yes
npx tsx scripts/scentum-cli.ts livrare add-item --id <deliveryId> --pri-item <necesarItemId> --yes
npx tsx scripts/scentum-cli.ts livrare remove-item --item <deliveryItemId> --yes
npx tsx scripts/scentum-cli.ts livrare approve  --id <deliveryId> --yes
npx tsx scripts/scentum-cli.ts livrare cancel   --id <deliveryId> --reason "..." --yes
npx tsx scripts/scentum-cli.ts livrare receive  --id <deliveryId> --yes --confirm-shopify   # ⚠️ scrie în Shopify
```
Branduri: `ESTEBAN · NUBRA · GEORGE-TALENT · LAB-NOIR · ART · ZAFRA · NICHE · BLINK · EU`.

## Formula Forecast (canonică, din `prisma/schema.prisma`)
```
velocity        = netUnitsSold(lookback) / inStockDays     (corectat pe zile OOS; fallback /lookbackDays)
forecastDemand  = ceil(velocity × forecastDays)
raw             = max(0, forecastDemand − onHand − pendingIncoming)
suggestedQty    = roundUpToUnit(raw, brand.productionRoundingUnit)     // default 50
```
`pendingIncoming` = `OPEN.requestedQty + COMPLETED.manufacturedQty` din Necesarele ne-terminale.

## Notes / capcane
- **DRY-RUN implicit pe TOATE mutațiile.** Nimic nu se scrie fără `--yes`.
- **⚠️ Recepția (`livrare receive`) e IREVERSIBILĂ din Scentum** — adaugă stoc în Shopify
  (`inventoryAdjustQuantities`); corecția se face doar din Shopify admin. De aceea cere
  `--yes` **și** `--confirm-shopify`.
- **Gardă STRICT la forecast**: dacă brandul are variante **nemapate** pe `ScentMaster`, forecastul
  e blocat — CLI-ul refuză și listează variantele de mapat (Mapare Produse în UI).
- **Stări:** editabil în `DRAFT / APPROVED / PARTIALLY_PRODUCED`; terminale (read-only):
  `PRODUCED / CLOSED / CANCELLED`. Numerotare `PR-{BRAND}-{n}` (multi-brand: `NP-{n}`).
- `necesar generate` rulează un **ForecastRun** real chiar și în dry-run (e o analiză, ca butonul din
  UI) — dar **nu** creează Necesarul fără `--yes`.
- Secret DB: KB **`DATABASE_URL_SCENTUM`**. Nu-l printa; nu comite `.env`.
- Alte servicii disponibile pentru extindere: `purchase-orders`, `receptions`, `maturation`,
  `production`, `recipes`, `movements`, `batches` (vezi `src/lib/services/` și `src/app/actions/`).
