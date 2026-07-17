---
name: print-queue
description: Coada de PRINT pentru DEPOZIT — ce etichete AWB sunt de printat, per SKU / magazin / cantitate / tip, din xConnector, INSTANT dintr-un index. Un cron la 1 noaptea salvează în `metrics.print_queue` tot ce-i de printat PÂNĂ IERI (NU ziua curentă); dimineața depozitul întreabă instant și DESCHIDE etichetele filtrate ÎN CHROME (NU printează singur — operatorul apasă Ctrl+P), iar comenzile deschise se marchează PRINTAT în DB. Folosește pentru „ce am de printat", „câte HA de printat", „coada de print depozit", „etichete de printat", „parfumuri de 3 pe Esteban", „deschide de printat pe Ofertele/MagDeal/Bonhaus", „print depozit", „de printat pe RO", „câte de printat pe fiecare magazin". Interogare SEMANTICĂ: magazin (esteban/ofertele/magdeal/bonhaus…), țară (RO/INTL), tip (deals/parfumuri/covoare/unghii), SKU (HA-…), cantitate (buc/comandă). Read-only by default; deschiderea în Chrome (`print --open`) descarcă etichetele (marchează downloaded server-side) + le marchează printat.
argument-hint: "sync --apply | query --sku HA --country RO --by-sku | print --store esteban --items 3 --open"
---

# print-queue — coada de print pt depozit (din xConnector, indexată)

> Author: **Gigi**. Separare rapidă a etichetelor de printat, per SKU × magazin × cantitate.

## ⚠️ PENTRU AGENT (Claude) — operatorul din depozit NU rulează comenzi, DOAR vorbește
Când operatorul cere ceva în limbaj natural, **TU rulezi comanda potrivită** (cu tool-ul tău) și-i arăți
**rezultatul clar** (numere, magazine, SKU-uri). NU-i arăta comanda și NU-i cere s-o ruleze el. Mapare:
| Operatorul zice | Tu rulezi |
|---|---|
| „ce am de printat azi?" | `query --country RO --by-store` |
| „câte HA de printat pe RO?" | `query --sku HA --country RO --by-sku` |
| „printează HA pe RO" | `print --sku HA --country RO --open` → se deschide Chrome pe mașina depozitului → el apasă Ctrl+P |
| „parfumuri de 3 pe Esteban" | `query --store esteban --items 3 --by-sku` (sau `print … --open` dacă zice „printează") |
| „deschide de printat pe Ofertele" | `print --store ofertele --open` |
| „câte s-au printat azi?" | `printed --country RO` |
- **`print … --open` se rulează LOCAL pe mașina depozitului** (deschide Chrome ACOLO, unde e imprimanta).
- Dacă indexul pare vechi (cronul de noapte n-a rulat), rulează întâi `sync --apply` (~20s), apoi întrebarea.

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

# CÂTE S-AU PRINTAT (de la baseline; cu cronul de noapte = „printate azi")
uv run "$S" printed --country RO                      # câte din coadă au fost deja descărcate (printate)
```

## Rutină depozit (dimineața)
```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/print-queue/print_queue.py"
uv run "$S" query --country RO --by-store     # 1. ce am de printat azi, pe magazine
uv run "$S" print  --sku HA --country RO --open   # 2. deschide lotul în Chrome → Ctrl+P
uv run "$S" printed --country RO              # 3. câte s-au printat (control)
```
> Cronul de la **01:00** (`print_queue_nightly.sh`) construiește coada peste noapte → dimineața pașii 1-2 sunt instant.
> „Câte s-au printat azi" NU e un câmp în xConnector (nu are timestamp pe descărcare) → se calculează ca DIFERENȚĂ
> față de baseline-ul de la 1 noaptea (comenzile care au ieșit din coadă = printate azi).

## Reguli importante
- **NU printează singur** — DESCHIDE PDF-uri în Chrome (merged cu `pypdf`, fără SumatraPDF/qpdf); operatorul apasă Ctrl+P. Depozitul e pe Windows → `chrome`.
- **Loturi de max 250** — dacă sunt multe etichete, sparge în loturi de **250** (un PDF/lot, deschis separat — Chrome/imprimanta nu duc un PDF uriaș). `--batch N` schimbă mărimea.
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
