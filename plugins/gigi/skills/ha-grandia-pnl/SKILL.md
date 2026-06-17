---
name: ha-grandia-pnl
description: NET P&L per linie de business din engine-ul de profitabilitate (profit_orders, VPS) — compară HA (linia de SKU-uri HA-* importate pe container, vândute COD prin deals stores) vs Grandia (magazinul grandia.ro = prefix GRAN) sau orice alt prefix, la NET cu TVA + transport + marketing. Folosește formula canonică din api/profitability.py. Exclude comenzile de test. Use pentru "profitabilitate HA vs Grandia", "net pe linie HA", "cat profit a facut HA fata de Grandia", "P&L net container deals", "profit_orders profitabilitate", "rentabilitate HA pe luna".
argument-hint: "--months 2026-04,2026-05 [--prefixes GRAN,EST,GT] [--no-ha]"
---

# ha-grandia-pnl
> Author: **Gigi**. NET P&L per linie (HA vs Grandia / orice prefix), din engine-ul de profitabilitate.

## Ce face
Calculează profitul **NET** (nu brut) pe linie de business, cu metodologia canonică a echipei:
**NET = (venit_livrat − COGS − transport) / 1,21 − marketing.** Compară implicit **HA** (SKU `HA-*`)
cu **Grandia** (prefix `GRAN`), dar `--prefixes` acceptă orice magazin (EST, GT, OFER…).

## Auth (secrete în KB, nu se printează)
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export PROFIT_SSH_HOST="$(uv run "$KB" secret-get PROFIT_SSH_HOST)"
export PROFIT_SSH_USER="$(uv run "$KB" secret-get PROFIT_SSH_USER)"
export PROFIT_SSH_PASS="$(uv run "$KB" secret-get PROFIT_SSH_PASS)"
```

## Usage
```bash
uv run scripts/ha_grandia_pnl.py --months 2026-04,2026-05            # HA vs Grandia
uv run scripts/ha_grandia_pnl.py --months 2026-05 --prefixes GRAN,EST,GT
```

## Metodologie (canonică — vezi api/profitability.py în Scripturi)
- **HA** = `skus LIKE 'HA-%'` (regex canonic `HA-\d{3,5}`, definit în `api/product_analytics.py`; listă în sheet „Master HA"). NU e brand — se vinde prin deals stores (prefix MAG/OFER/RED/BON).
- **Grandia** = prefix `GRAN` (= shop n12w89-yy). Disjunct de HA.
- **Exclude mereu** comenzile cu tag `test` (teste de funnel ≈ status „Lipsă AWB", neexpediate). Grandia ~0; HA ~12%.
- **TVA RO 21%** se scade din venit/COGS/transport (toate intră brute, cu TVA); marketing e net.
- **Transport** = colete plecate (Livrata+In curs+Refuzata) × cost/colet din `profit_transport_costs` (Grandia 25, HA 13).
- **Marketing**: pt prefix → `profit_marketing_override` (= `daily_perf`, sincronizat zilnic). HA nu e prefix → se **ALOCĂ**: pt fiecare deals-prefix, `(HA plecate / total plecate) × override-ul prefixului`.

## Sursele de date (înregistrate în KB resources)
- **profit_orders** (SQLite pe VPS 84.46.242.181, via SSH) — comenzi cu revenue/cogs/status/skus/prefix/tags.
- **daily_perf.db** — marketing per brand (proaspăt). **product_analytics.db** — adspend per-SKU HA (FB/TK), dar INCOMPLET (FB din 22-apr, TK din 15-mai) → de aceea marketing HA se alocă, nu se ia de acolo.
- **Containere care vin:** sheet „Tom - receptii containere" (id `1PjlFq31...`, tab „Master HA").
- **Valori inventar lunar:** sheet id `1Pke-2fMv8...` — **un tab pe lună** (1 iunie, 1 noiembrie…), per-SKU `Cantitate × Cogs = Valoare stoc` + sumar per magazin. (Pentru capitalul blocat — neinclus încă în NET; pe roadmap.)

## Avertismente
- **Contribuție pre-overhead** (fără salarii/fixe).
- **Marketing HA e ALOCAT** (per-SKU incomplet) — dacă spend-ul real HA e mai mare, net-ul HA scade.
- **Nu include costul capitalului blocat** în stoc de container (vezi sheet inventar).
- Rezultat de referință (apr+mai 2026): HA net ~182k (19%) vs Grandia ~107k (8,5%) — la NET, HA mai profitabil; pe brut/colet Grandia părea mai bun (transport 25 + marketing greu o trag în jos).
