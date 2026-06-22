---
name: multi-brand-pnl
description: All-in P&L for ANY or ALL of the 16+ Arona brands (Esteban, GT, Nubra, Bonhaus RO/CZ/PL/BG, Ofertele Zilei, Reduceri bune, Magdeal, Belasil, Gento, Carpetto, Covoria, Nocturna, Rossi Nails, Apreciat...). DEFAULT = REAL net profit from the canonical Scripturi profitability engine (cache.brand_pnl_monthly): revenue = DELIVERED orders only, EX-VAT, minus COGS + transport + marketing — MONTHLY granularity. One brand, the portfolio ranked by net profit/margin, or a consolidated company-wide P&L. The old daily_perf view (gross-with-VAT, all orders, FB/Google/TikTok split, daily + --today) is available via --estimat but OVERSTATES profit. Use for 'real profit per brand', 'P&L all brands', 'company-wide profit', 'which brands are profitable', 'what % of profit is perfumes', 'brand profitability ranking'.
---

# multi-brand-pnl

> 🗺️ **Profitabilitate** — pipeline CANONIC + „unde găsesc ce“: `shared/HARTA.md`. Per-SKU/categorie = `metrics-cache/profit_by_sku.py` (transport real + marketing CPA); logica unică = `profit_core.py`.
> 🆕 **`--range --from YYYY-MM-DD --to YYYY-MM-DD`** = profit REAL pe o FEREASTRĂ EXACTĂ de zile (nu lună întreagă): rulează engine-ul cu `from_date/to_date`, marketing însumat DOAR pe fereastră (fix 2026-06), + grad de așezare livrare (livrate/plecate, „în curs"). Ex: `--brands nubra --from 2026-06-01 --to 2026-06-15 --range`. ⚠️ Fereastră recentă = livrare neașezată → profit livrat încă incomplet.

P&L "all-in" pentru oricare sau toate brandurile Arona.

**DEFAULT = profit REAL** din engine-ul canonic de profitabilitate Scripturi
(`cache.brand_pnl_monthly`): venit = comenzi **LIVRATE**, **fără TVA**, minus COGS +
transport + marketing → **PROFIT NET**, marjă, MER, CPA, AOV. Granularitate **LUNARĂ**
(profitul real se știe doar după ce livrarea se așază; luna curentă e incompletă).

`--estimat` / `--today` = sursa veche `cache.daily_brand_pnl` (oglinda daily_perf): venit
**brut cu TVA, toate comenzile**, split FB/Google/TikTok, zilnic. **Supraestimează** profitul
— folosește doar pt tendință zilnică / defalcare pe platforme, nu pt profitul real.

## How to run

Din directorul skill-ului (`uv` instaleaza dependintele din blocul PEP723):

```bash
# snapshot executiv: ieri + month-to-date, o linie per brand
uv run multi_brand_pnl.py --today

# tot portofoliul, clasat dupa profitul de contributie
uv run multi_brand_pnl.py --brands all --from 2026-06-01 --to 2026-06-11

# cateva branduri (alias-uri acceptate: gt, oz, rossi...)
uv run multi_brand_pnl.py --brands esteban,gt,nubra,belasil --from 2026-06-01 --to 2026-06-11

# un singur P&L consolidat pe toata compania
uv run multi_brand_pnl.py --brands all --from 2026-06-01 --to 2026-06-11 --consolidated
```

Argumente:
- `--brands all|csv` — implicit `all`. CSV cu nume sau alias (`gt`=George Talent,
  `oz`=Ofertele Zilei, `rossi`=Rossi Nails). Matching pe substring case-insensitive,
  deci `bonhaus` prinde toate geo-urile, `nocturna` prinde toate variantele.
- `--from` / `--to` — `YYYY-MM-DD`. Implicit = luna curenta pana azi.
- `--consolidated` — un singur P&L agregat pe toate brandurile selectate.
- `--today` — snapshot: ieri + MTD, ignora `--from/--to/--brands` (intotdeauna toate).

## How it works

- Sursa = `data/daily_perf.db` (SQLite) de pe VPS-ul Scripturi
  (`root@84.46.242.181`), tabela `daily_perf`, alimentata zilnic din **'Raport
  Zilnic 2'**. Coloane: `date, brand, orders, revenue, fb_spend, tk_spend,
  google_spend, total_spend, cogs, transport, profit, roas, cpa, aov`.
- Pe VPS nu exista binar `sqlite3` CLI, asa ca scriptul ruleaza un **heredoc
  Python** prin `ssh` peste `/root/Scripturi/.venv/bin/python3`, face un singur
  `SELECT ... GROUP BY brand` (READ-ONLY) si intoarce JSON; agregarea finala si
  formatarea tabelului se fac local.
- Profit de contributie = `revenue − cogs − transport − total_spend` (verificat:
  coincide cu coloana `profit` stocata). MER = ROAS = `revenue / total_spend`
  (spend-ul include toate canalele de ads). CPA = `total_spend / orders`,
  AOV = `revenue / orders`. Clasarea e dupa contributie descrescator; tabelul
  afiseaza si linia TOTAL si numarul de branduri profitabile.

## Limitations

- Cifrele sunt **cu TVA** (venit brut din Raport Zilnic 2), nu net de TVA.
- Acuratetea depinde de prospetimea `daily_perf.db`; ziua curenta poate fi
  partiala pana ruleaza sync-ul. `--today` foloseste ieri ca ultima zi completa.
- Necesita acces SSH la VPS (`ssh -o BatchMode=yes root@84.46.242.181`).
- Brandurile cu 0 venit / 0 spend / 0 comenzi in interval sunt omise din tabel.
- Matching pe substring: un alias scurt (ex. `esteban`) poate prinde si un brand
  inrudit activ (ex. `Esteban Parfum`), afisat ca rand separat — nu se dubleaza.
- Read-only: scriptul nu scrie niciodata in nicio baza.
