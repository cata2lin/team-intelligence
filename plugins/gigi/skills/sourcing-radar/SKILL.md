---
name: sourcing-radar
description: Radar de SOURCING / descoperire de produse din motorul de competitive-intelligence (arona-bi, 50+ site-uri RO scrape-uite zilnic, 213k produse cu viteză de vânzare inferată live) — „ce se vinde cel mai repede la competiție, ca să decid ce să aduc/lansez (mai ales Grandia/home-garden/commodity)". Rank pe ads30_cal (viteză inferată din scăderile de stoc). Filtre: --search (cuvânt-cheie, ex covor/raft/gradina), --parser, --vendor, preț, viteză. ANTI-ZGOMOT esențial: exclude dinamic site-urile cu STOC PLACEHOLDER (jysk/eiluminat/souqshop cu stoc 1000-1mld → viteză gunoi). Opțional Google Sheet partajat. Folosește pentru „ce produse să aduc/lansez", „ce se vinde la competiție", „produse trending la concurență", „sourcing", „idei de produse noi", „ce covoare/rafturi/produse de gradină se vând", „demand radar", „winning products competiție", „ce să bag în Grandia". NU e pricewatch (ăla = listă URL-uri urmărite manual pe prețuri); ăsta minează tot motorul pentru descoperire.
---

# Sourcing radar (descoperire de produse din competiție)

Cel mai mare activ neexploatat al echipei: **arona-bi** scrape-uiește zilnic 50+ site-uri RO
(127M rânduri preț + 127M stoc, ~13 luni istoric) și pre-calculează `mv_best_sellers_ranked`
(213k produse cu **viteză de vânzare inferată** `ads30_cal`, din scăderile reale de stoc).
Acest skill îl minează ca să găsești **ce se vinde cel mai repede la competiție** → idei de
sourcing (mai ales Grandia/home-garden/commodity).

## Cum rulezi
```bash
uv run sourcing_radar.py                               # top fast-movers (site-uri cu stoc real)
uv run sourcing_radar.py --search "covor|presul"       # ce covoare se vând (spațiu Covoria/Carpetto)
uv run sourcing_radar.py --search "raft|depozit|gradina" --min-price 30 --max-price 200
uv run sourcing_radar.py --parser vevor --min-vel 20   # doar de pe un site, viteză mare
uv run sourcing_radar.py --days 7 --sheet              # ce s-a vândut în ultimele 7z → Google Sheet
```

| Flag | Default | Ce face |
|---|---|---|
| `--search` | — | regex POSIX în numele produsului (ex: `covor\|presul\|mochet`) |
| `--parser` / `--vendor` | — | filtru pe un site / vendor |
| `--min-vel` | 0 | viteză minimă (ads30_cal) |
| `--min-price` / `--max-price` | — | interval de preț |
| `--days` | 14 | doar produse vândute în ultimele N zile (relativ la ultima zi din date) |
| `--max-stock` | 5000 | plafon stoc/rând (taie outlierii de placeholder) |
| `--placeholder-stock` | 500 | parserii cu median latest_stock peste asta = placeholder, excluși |
| `--include-placeholder` | — | NU exclude site-urile placeholder |
| `--include-vivre` | — | include Vivre (stoc netrack-uit; default exclus) |
| `--limit` | 40 / `--sheet` | câte rânduri / scrie un Google Sheet partajat |

## ANTI-ZGOMOT (de ce e cheia)
Viteza `ads30_cal` se inferă din scăderile de stoc → site-urile cu **stoc placeholder** (constant
uriaș) dau viteză = gunoi. Profil real măsurat: jysk median stoc **1016**, eiluminat **945**,
souqshop **10906** (max 1,2 mld!) vs site-uri reale (Bonami median 3, aosom 43, vevor 41). Skill-ul
**exclude dinamic** parserii cu median latest_stock > `--placeholder-stock` (default 500). **Vivre**
(115k produse, dar stoc=0 → netrack-uit) e exclus default; `--include-vivre` îl bagă (doar viteză).

## Capcane / v2
- `ads30_cal` e o ESTIMARE din stoc, nu vânzări reale — bună pt ranking relativ, nu cifre absolute.
- **v2 (neimplementat):** matching fuzzy cu catalogul nostru (AWBprint/Shopify) ca să separe „avem deja"
  de „de lansat", + alerte de stockout/price-cut la competiție (din stock_history/price_history).
  arona-bi NU are copie a catalogului nostru → matching-ul cere join cross-DB.
- Conexiune: secret KB `DATABASE_URL_ARONA_BI`. Google Sheet: `GOOGLE_OAUTH_TOKEN_JSON`.
