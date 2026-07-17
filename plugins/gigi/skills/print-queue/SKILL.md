---
name: print-queue
description: Coada de PRINT pentru DEPOZIT — ce etichete AWB sunt de printat, per SKU / magazin / cantitate / tip, din xConnector, INSTANT dintr-un index. Un cron la 1 noaptea salvează în `metrics.print_queue` tot ce-i de printat PÂNĂ IERI (NU ziua curentă); dimineața depozitul întreabă instant și DESCHIDE etichetele filtrate ÎN CHROME (NU printează singur — operatorul apasă Ctrl+P), iar comenzile deschise se marchează PRINTAT în DB. Folosește pentru „ce am de printat", „câte HA de printat", „coada de print depozit", „etichete de printat", „parfumuri de 3 pe Esteban", „deschide de printat pe Ofertele/MagDeal/Bonhaus", „print depozit", „de printat pe RO", „câte de printat pe fiecare magazin". Interogare SEMANTICĂ: magazin (esteban/ofertele/magdeal/bonhaus…), țară (RO/INTL), tip (deals/parfumuri/covoare/unghii), SKU (HA-…), cantitate (buc/comandă). Read-only by default; deschiderea în Chrome (`print --open`) descarcă etichetele (marchează downloaded server-side) + le marchează printat.
argument-hint: "sync --apply | query --sku HA --country RO --by-sku | print --store esteban --items 3 --open"
---

# print-queue — coada de print pt depozit (din xConnector, indexată)

> Author: **Gigi**. Separare rapidă a etichetelor de printat, per SKU × magazin × cantitate.

## Cum funcționează (2 pași)
1. **Noaptea (cron 01:00, `print_queue_nightly.sh`)** — interoghează xConnector pe TOATE magazinele
   ÎN PARALEL (doar comenzi NEexpediate = coada reală), filtrează etichetele AWB nedescărcate,
   explodează pe SKU și salvează în **`metrics.print_queue`** tot ce-i de printat **PÂNĂ IERI**
   (nu ziua curentă — comenzile de azi încă intră). ~20s.
2. **Dimineața (depozitul)** — întreabă INSTANT din index și deschide în Chrome ce vrea.

## Comenzi
```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/print-queue/print_queue.py"

# REFRESH manual (dacă vrei acum, nu aștepți cronul). Interval: --days N sau --from/--to.
uv run "$S" sync --apply
uv run "$S" sync --apply --from 2026-07-10 --to 2026-07-14

# CE E DE PRINTAT (instant, semantic)
uv run "$S" query --country RO --by-store            # cât e de printat pe fiecare magazin RO
uv run "$S" query --sku HA --country RO --by-sku     # câte HA pe RO, per SKU (de la multe la puține)
uv run "$S" query --store esteban --items 3 --by-sku # parfumuri de 3 pe Esteban
uv run "$S" query --type deals --country RO --by-store

# PRINT — deschide în CHROME ce-i filtrat; NU printează singur (operatorul apasă Ctrl+P)
uv run "$S" print --sku HA --country RO              # DRY-RUN: ce s-ar deschide
uv run "$S" print --sku HA --country RO --open       # descarcă fresh → merge PDF (pypdf) → Chrome → marchează PRINTAT
uv run "$S" print --store esteban --items 3 --open   # parfumurile de 3 pe Esteban
```

## Reguli importante
- **NU printează singur** — doar DESCHIDE un PDF unic (merged cu `pypdf`, fără SumatraPDF/qpdf) în Chrome;
  operatorul apasă Ctrl+P. Depozitul e pe Windows → deschide `chrome`.
- **`--open` = mutație**: descarcă eticheta (xConnector o marchează `downloaded` → iese din coada tuturor)
  + o marchează `printed_at` în DB. Fără `--open` = DRY-RUN, zero efecte.
- **La print re-interoghează comanda FRESH** (AWB-ul se poate schimba între noapte și dimineață) — nu
  folosește URL-ul vechi din index. Comenzile deja descărcate între timp = marcate printat, nu re-descărcate.
- **Semantic**: `--store` prinde nume/alias (esteban, ofertele, magdeal, bonhaus, gt…), `--country RO|INTL`
  (INTL = doar Bonhaus CZ/PL/BG), `--type deals|parfumuri|covoare|unghii`, `--sku` = prefix (HA prinde HA-*),
  `--items` = bucăți/comandă.

## Sursa cozii = xConnector (NU AWBprint)
Coada „de printat" = etichetă AWB `downloaded=false` din **xConnector**. AWBprint (`is_printed`/awb_pdf_url) e
fluxul vechi **Frisbo** — NU-l folosi pt asta. Config xConnector: KB `XCONNECTOR_SHOPS` / `~/.aac/input.json`.

## Complementar cu `agentic-label-batch` (pachet aac-pilot)
Acela = motorul de download+merge cu opțiuni avansate (grupuri, `--complexity-split`). Skill-ul ăsta adaugă
**DESCOPERIREA** (ce SKU-uri + câte, gap-ul `no-sku-discovery-endpoint`) + indexul persistent + print direct în
Chrome. Notă: xConnector OrderDTO expune acum `skus`/`totalItemsCount` (MODE A live, #2140 merged).
