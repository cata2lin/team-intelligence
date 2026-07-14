---
name: scentum
description: Operează Scentum ERP (app-ul de producție parfumuri — Esteban, Nubra, George Talent, Lab Noir, Artevita, Zafra, Niche, Blink, EU) din terminal, prin acțiunile și serviciile CANONICE ale aplicației (aceeași logică ca UI-ul — validări, audit, numerotare; NU SQL brut). Acoperă TOATE cele ~119 mutații + 65 citiri din Scentum, nu doar producția — Necesar Producție din Forecast (velocity → PR-{BRAND}-{n}), aprobare/anulare/marcat fabricat, Livrări + Recepție (push stoc în Shopify), comenzi furnizor (PO), recepții, maturare, rețete, loturi, mișcări de stoc, produse, furnizori, utilizatori. Use pentru "fă un necesar", "necesar producție", "generează necesar Esteban/Nubra", "cât să producem", "forecast producție", "aprobă necesarul", "marchează fabricat", "creează livrare", "recepționează livrarea", "comandă furnizor", "PO", "maturare", "rețetă", "lot", "Scentum", "PR-EST", "vreau să scriu în Scentum", "Andreea → Vali".
argument-hint: "actions [modul] | sig <mod>.<fn> | call <mod>.<fn> --json '{...}' --yes | necesar generate --brand ESTEBAN --yes"
---

# scentum
> Author: **Gigi**. Operează Scentum ERP din CLI — **orice mutație**, prin codul canonic al app-ului.

## Ce e Scentum
ERP-ul de **producție parfumuri** (repo privat `contact546/scentum`, Next.js 15 + Prisma + Postgres).
Lanțul central: **Forecast** → **Necesar Producție** (`ProductionRequirement`, Andreea → Vali) →
**Livrare** → **Recepție** (stocul intră în Shopify). Plus: PO furnizori, recepții, maturare, rețete,
loturi, mișcări de stoc.

> ⚠️ **Mutațiile NU sunt pe HTTP.** API-ul expune doar PDF/upload/sync. Scrierile trăiesc în
> **server actions** (`src/app/actions/*`) → **servicii** (`src/lib/services/*`). De aceea CLI-ul
> rulează **în interiorul repo-ului**, importând acțiunile — așa păstrezi logica de business
> (validări, tranziții de stare, audit, numerotare). **Nu scrie SQL brut în Scentum.**
> ⚠️ MCP-ul `postgres-scentum` e **READ-ONLY prin design** — din el nu poți scrie NICIODATĂ.

## Setup (o dată pe mașină)
```bash
gh repo clone contact546/scentum ~/Downloads/scentum && cd ~/Downloads/scentum
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
# DSN cu drepturi minime (rol scentum_rw), NU superuserul:
printf 'DATABASE_URL=%s\n' "$(uv run "$KB" secret-get DATABASE_URL_SCENTUM_RW)" > .env
printf 'SCENTUM_USER=%s\n' "emailul.tau@…"  >> .env      # ⬅️ emailul TĂU din Scentum (users)
npm install && npx prisma generate
cp -r <skill-dir>/scripts/_shims <skill-dir>/scripts/*.ts scripts/
cp <skill-dir>/scripts/tsconfig.cli.json .
```

> 🔑 **Două lucruri îți trebuie ca să scrii:**
> 1. **`DATABASE_URL_SCENTUM_RW`** (KB) — rol `scentum_rw`: SELECT/INSERT/UPDATE/DELETE, fără
>    superuser, fără DDL. (`DATABASE_URL_SCENTUM` = superuser, doar admin/migrații — nu-l împrăștia.)
> 2. **`SCENTUM_USER`** = emailul tău de utilizator Scentum. Acțiunile scriu `session.user.id` în
>    audit (createdBy/updatedBy) — deci mutațiile apar **pe numele tău**, exact ca din UI. Fără el,
>    CLI-ul refuză. Rolul (ADMIN/OPERATOR) e citit din DB și respectat (`requireRole`).

## Cum rulezi (flag-ul de tsconfig e OBLIGATORIU)
```bash
cd ~/Downloads/scentum
npx tsx --tsconfig tsconfig.cli.json scripts/scentum-cli.ts <comandă>
```
`tsconfig.cli.json` mapează, **doar pentru CLI**, `next/cache` / `next/navigation` / `server-only` /
`@/lib/auth*` pe shim-urile din `scripts/_shims/` (în terminal n-ai request Next → `headers()` ar crăpa).
**Nu muta path-urile astea în `tsconfig.json`** — strici build-ul Next.

## Toate mutațiile — dispatcher generic
```bash
CLI="npx tsx --tsconfig tsconfig.cli.json scripts/scentum-cli.ts"

$CLI actions                      # toate modulele + funcțiile (✏️ mutație / 📖 citire) — ~119 ✏️ + 65 📖
$CLI actions purchase-orders      # doar un modul
$CLI sig  purchase-orders.createPurchaseOrder      # ce JSON cere (semnătura + tipurile, din sursă)
$CLI call purchase-orders.createPurchaseOrder --json '{"type":"...","items":[…]}' --yes
```
**Citirile** (`get*/list*/search*/count*/…`) rulează direct. **Mutațiile sunt DRY-RUN implicit** —
scriu doar cu `--yes`. Reguli de aur: rulezi întâi `sig`, apoi `call` fără `--yes` (vezi ce-ar face),
abia apoi cu `--yes`.

## Scurtături tipate (producție — fluxul zilnic)
```bash
$CLI brands
$CLI necesar list [--brand ESTEBAN] [--status DRAFT]   ·  $CLI necesar show --id <prId>
$CLI necesar generate --brand ESTEBAN [--lookback 60 --forecast-days 60 --round 50] --yes
$CLI necesar create|add-line|set-qty|remove-line|approve|cancel|mark|cancel-line …  --yes
$CLI livrare list|show|eligible
$CLI livrare create|add-item|remove-item|approve|cancel …  --yes
$CLI livrare receive --id <deliveryId> --yes --confirm-shopify   # ⚠️ scrie stoc în Shopify
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
- **Stări Necesar:** editabil în `DRAFT / APPROVED / PARTIALLY_PRODUCED`; terminale (read-only):
  `PRODUCED / CLOSED / CANCELLED`. Numerotare `PR-{BRAND}-{n}` (multi-brand: `NP-{n}`).
- `necesar generate` rulează un **ForecastRun** real chiar și în dry-run (e o analiză, ca butonul din
  UI) — dar **nu** creează Necesarul fără `--yes`.
- **Acțiunile nu validează cu Zod** peste tot — un JSON greșit dă `TypeError`, nu un mesaj frumos.
  De-aia `sig` există: citește-l înainte de `call`.
- 12 din 38 de module de acțiuni scriu **direct cu Prisma** (fără service layer) — inclusiv
  `purchase-orders`. De aceea dispatcher-ul merge pe **actions**, nu pe servicii: altfel ai rata ~40%
  din suprafață.
- Secrete: KB (`DATABASE_URL_SCENTUM_RW`). Nu le printa; **nu comite `.env`**.
