---
name: multi-brand-pnl
description: Live all-in P&L for ANY or ALL of the 16+ Arona brands (Esteban, GT, Nubra, Bonhaus RO/CZ/PL/BG, Ofertele Zilei, Reduceri bune, Magdeal, Belasil, Gento, Carpetto, Covoria, Nocturna, Rossi Nails, Apreciat...) for a date range — revenue, FB/Google/TikTok ad spend, COGS, transport, contribution profit, ROAS, MER, CPA, AOV — from the Scripturi daily_perf.db (sourced from 'Raport Zilnic 2'). One brand, the portfolio ranked by profit/margin, or a consolidated company-wide P&L, plus a one-line --today snapshot. Use for 'P&L all brands', 'company-wide profit', 'which brands are profitable this month', 'brand profitability ranking', 'portfolio MER/ROAS', 'exec snapshot'.
---

# multi-brand-pnl

P&L "all-in" live pentru oricare sau toate brandurile Arona, pe un interval de date.
Pentru fiecare brand: venit, ads (FB+Google+TikTok), COGS, transport, **profit de
contributie** (= venit − COGS − transport − total_spend), MER, ROAS, CPA, AOV.

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
