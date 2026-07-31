---
name: print-queue
description: Coada de PRINT pe STAȚIE (depozit / uzina2) — ce etichete AWB are de printat STAȚIA TA, per SKU / magazin / cantitate / categorie, live din xConnector. Fiecare stație vede DOAR magazinele ei (`--machine depozit|uzina2`), nu coada întregii firme. Operatorul întreabă în limbaj natural, agentul rulează `pull` (refresh ~15s) + `plan` (instant) și spune NUMĂRUL; la print, `open` descarcă etichetele filtrate și le DESCHIDE ÎN CHROME (NU printează singur — operatorul apasă Ctrl+P). Folosește pentru „câte AWB-uri am de printat", „ce am de printat azi", „coada de print", „etichete de printat pe uzina 2", „câte HA de printat", „parfumuri de 3 pe Esteban", „deschide de printat pe Ofertele/MagDeal/Bonhaus", „print depozit". Read-only by default; `open` descarcă etichetele (le scoate din coadă server-side) + le marchează printat.
argument-hint: "pull --machine uzina2 | plan --machine uzina2 --by-sku | open --sku HA --machine uzina2"
---

# print-queue — coada de print, per STAȚIE

> Author: **Gigi**. Separare rapidă a etichetelor de printat, per stație × SKU × magazin × cantitate.

## ⚠️ PENTRU AGENT (Claude) — operatorul NU rulează comenzi, DOAR vorbește
Când operatorul cere ceva în limbaj natural, **TU rulezi comanda** și-i arăți **rezultatul clar**
(numere, magazine, SKU-uri). NU-i arăta comanda și NU-i cere s-o ruleze el.

**Stația se deduce singură** din `PRINT_MACHINE` (setat o dată pe laptop) — nu întreba operatorul
pe ce mașină e. Dacă variabila lipsește, folosește `--machine <stația>` explicit.

| Operatorul zice | Tu rulezi |
|---|---|
| „câte AWB-uri am de printat?" | `pull` apoi `plan --by-store` → spui **numărul total + pe magazine** |
| „ce am de printat azi?" | `pull` apoi `plan --by-category` |
| „câte HA de printat?" | `plan --sku HA --by-sku` |
| „parfumuri de 3 pe Esteban" | `plan --shop esteban --items 3 --by-sku` |
| „printează HA" | `open --sku HA` → se deschide Chrome pe stație → el apasă Ctrl+P |
| „deschide de printat pe Ofertele" | `open --shop ofertele` |
| „câte s-au printat?" | `printed` |
| „mai scoate o dată lotul ăla" | `reprint --batch <nume>` |

- **`pull` = refresh (~15s), `plan` = instant.** Rulează `pull` o dată la începutul sesiunii, apoi
  `plan` de câte ori vrei. Dacă operatorul cere un număr și n-ai făcut `pull` în sesiunea asta, fă-l întâi.
- **`open` se rulează LOCAL pe stație** (deschide Chrome ACOLO, unde e imprimanta).

## Două scripturi — nu le confunda
| Fișier | Rol | Unde rulează |
|---|---|---|
| **`print_queue.py`** | **Stația.** `pull/plan/open/printed/reprint`, filtrat pe `--machine`. Live din xConnector, cache în SQLite local (`~/.arona_print_queue.db`). | laptopul din depozit / uzina2 |
| `print_queue_central.py` | **Centralul.** `sync/query/print/printed` → construiește `metrics.print_queue` (Postgres) pentru raportare. Cron 01:00 via `print_queue_nightly.sh`. | VPS |

> Stația folosește **întotdeauna `print_queue.py`**. Centralul e pentru cronul de noapte — nu-l rula pe stație.

## Setup stație (o singură dată)
```bash
# Windows (PowerShell), apoi terminal NOU:
setx PRINT_MACHINE uzina2        # sau: depozit
```
`print_queue.py` își găsește singur `xconnector.py` dacă folderele stau unul lângă altul
(`../xconnector/xconnector.py`) — layout-ul normal din marketplace. Dacă e în altă parte:
`setx XCONNECTOR_PY C:\cale\catre\xconnector.py`.

> ⚠️ `xconnector.py` **nu rulează singur** — importă 23 de module-frate (`address_rules.py`,
> `*_nomenclator.py`…). Copiază tot folderul `xconnector/`, nu doar fișierul.

## Comenzi
```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/print-queue/print_queue.py"

# 1. REFRESH coada stației (~15s). Implicit: de la 1 ale lunii până azi.
uv run "$S" pull                              # magazinele stației (din PRINT_MACHINE)
uv run "$S" pull --machine uzina2             # explicit
uv run "$S" pull --days 7                     # altă fereastră
uv run "$S" pull --all                        # include și etichetele deja descărcate

# 2. CE E DE PRINTAT (instant, din cache)
uv run "$S" plan --by-store                   # total + pe magazine  ← răspunsul la „câte am de printat"
uv run "$S" plan --by-category                # pe categorii de produs
uv run "$S" plan --sku HA --by-sku            # câte HA, per SKU
uv run "$S" plan --shop esteban --items 3     # parfumuri de 3 pe Esteban

# 3. PRINT — deschide în CHROME ce-i filtrat; NU printează singur (operatorul apasă Ctrl+P)
uv run "$S" open --sku HA                     # descarcă fresh → merge PDF → Chrome → marchează PRINTAT
uv run "$S" open --shop esteban --items 3
uv run "$S" open --sku HA --no-open           # pregătește PDF-ul fără să deschidă Chrome

# 4. CONTROL
uv run "$S" printed                           # ce s-a printat
uv run "$S" reprint --batch <nume>            # re-deschide un lot deja printat
```

## Reguli importante
- **NU printează singur** — DESCHIDE PDF-uri în Chrome (merged cu `pypdf`); operatorul apasă Ctrl+P.
  Stațiile sunt pe Windows → `chrome`.
- **`open` = mutație**: descarcă eticheta (xConnector o marchează `downloaded` → **iese din coada
  tuturor stațiilor**) + o marchează printat. `plan` = zero efecte.
- **Fiecare stație vede doar magazinele ei.** Magazinele împărțite între stații apar la ambele —
  de aceea `open` re-interoghează comanda FRESH și sare peste ce-a descărcat deja cealaltă stație.
- **Loturi de max 250** (`--batch N`) — Chrome/imprimanta nu duc un PDF uriaș.
- **Semantic**: `--shop` prinde nume/alias (esteban, ofertele, magdeal, bonhaus, gt…),
  `--sku` = prefix (HA prinde HA-*), `--items` = bucăți/comandă, `--threshold` = pragul de „multe bucăți".

## Sursa cozii = xConnector (NU AWBprint)
Coada „de printat" = etichetă AWB `downloaded=false` din **xConnector**. AWBprint (`is_printed`/`awb_pdf_url`)
e fluxul vechi **Frisbo** — NU-l folosi pt asta. Config xConnector: KB `XCONNECTOR_SHOPS` / `~/.aac/input.json`.

## Rulare de pe server (fără fișiere pe stație)
Skill-ul e executabil și prin Second Brain (`gigi:print-queue`, acțiunile `pull`/`plan`/`printed` = tier
`allow`; `open`/`reprint` = `ask`). Util pentru ÎNTREBĂRI („câte am de printat"). **Printul real trebuie
rulat local** — Chrome trebuie să se deschidă lângă imprimantă.
