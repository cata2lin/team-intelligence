---
name: print-queue
description: Coada de PRINT pe STAȚIE (depozit / uzina2) — ce etichete AWB are de printat STAȚIA TA, per SKU / magazin / cantitate / categorie, live din xConnector. Fiecare stație vede DOAR magazinele ei (`--machine depozit|uzina2`), nu coada întregii firme. Operatorul întreabă în limbaj natural, agentul rulează `pull` (refresh ~15s) + `plan` (instant) și spune NUMĂRUL; la print, `open` descarcă etichetele filtrate și le DESCHIDE ÎN CHROME (NU printează singur — operatorul apasă Ctrl+P). Folosește pentru „câte AWB-uri am de printat", „ce am de printat azi", „coada de print", „etichete de printat pe uzina 2", „câte HA de printat", „parfumuri de 3 pe Esteban", „deschide de printat pe Ofertele/MagDeal/Bonhaus", „print depozit". Read-only by default; `open` descarcă etichetele (le scoate din coadă server-side) + le marchează printat.
argument-hint: "pull --machine uzina2 | plan --machine uzina2 --by-sku | open --sku HA --machine uzina2"
---

# print-queue — coada de print, per STAȚIE

> ## 🔴 PE STAȚIE: ia coada din SECOND BRAIN, nu rula scriptul local
>
> **Dacă ești Claude-ul unei stații de depozit (uzina2 / depozit), NU rula `print_queue.py` local** și nu
> încerca să repari cheile de acolo. `~/.aac/input.json` de pe stații ține chei xConnector vechi; ele
> **expiră la 90 de zile** (ultima oară pe 22-sep-2026, 19 din 23 de magazine deodată) și primești `401`.
> Serverul are mereu cheile proaspete — întreabă-l pe el:
>
> ```
> run_skill("depozit:print-queue", action="pull", params={"machine": "uzina2"})   # reîmprospătează, ~15s
> run_skill("depozit:print-queue", action="plan", params={"machine": "uzina2"})   # instant, din cache
> ```
> (MCP-ul `second-brain`; înlocuiește cu `"depozit"` pe cealaltă stație.) Rulează `pull` ÎNTÂI, apoi `plan`.
> Dacă `plan` răspunde „Nimic in DB pt filtru", n-ai rulat `pull`.
>
> ### ⚠️ Filtrul de magazin merge pe DOMENIU, nu pe numele brandului
> `shop="grandia"` întoarce **zero** și pare că n-ai nimic de printat. `shop="n12w89-yy"` întoarce 39.
> Tradu întotdeauna numele spus de operator:
>
> | brand | domeniu | | brand | domeniu |
> |---|---|---|---|---|
> | Grandia | `n12w89-yy` | | Apreciat | `8e3700-d9` |
> | Ofertele Zilei | `ofertelezilei` | | Covoria | `bb4nmc-pb` |
> | Reduceri Bune | `audusp-rf` | | MagDeal | `covoareauto-ro` |
> | Casa Ofertelor | `bonhaus` | | Carpetto | `nxfer1-n4` |
> | Gento | `cn54vk-uz` | | Nocturna / Lux | `1eee37-2d` / `de51c5-b8` |
> | Orice Redus | `oriceredus` | | Esteban / GT | `6f9e22-9d` / `ix5bxc-hr` |
> | Ce Pat Ai | `ce-pat-ai` | | Nubra / Lab Noir | `bmuwvv-jy` / `31k0py-bi` |
> | Bonhaus CZ / PL | `vthuzq-7j` / `f0yrmh-ia` | | Bonhaus BG / HU / SK | `ux1x6n-n2` / `63e901-2f` / `16w7xv-0w` |
>
> Alte filtre, la fel: `params={"machine":"uzina2","sku":"HA-0002"}` · `{"items": 3}`.
>
> ### Pe CATEGORII (așa lucrează depozitul)
> ```
> run_skill("depozit:print-queue", action="plan", params={"machine":"uzina2","by_category": true})
> ```
> Dă categoriile (AȘTERNUTURI / OGLINZI / COVOARE / BAIE / HA / LAVETE / MIXT), fiecare cu bucățile și
> cu **în câte PDF-uri se taie** — exact planul de print. Formă reală:
> ```
> == BAIE: 18 buc ==
>    BAIE-ALBASTRU_x1     9 buc
>    BAIE-ROSU_x1         5 buc
>    DIVERSE              4 buc
>    ...
>    AȘTERNUTURI  12 (1 PDF) · OGLINZI 11 (2 PDF) · COVOARE 16 (3 PDF) · BAIE 18 (3 PDF) · MIXT 112 (6 PDF)
> ```
> Un SKU primește PDF propriu de la `threshold` bucăți în sus (implicit **3**); restul intră în `DIVERSE`.
> Schimbi pragul cu `{"threshold": 5}`. Variante înrudite: `{"by_qty": true}` (parfumuri, grupat pe
> cantitate) și `{"by_sku": true}` (detergenți, FĂRĂ/CU lavete).
>
> ⚠️ Prin Second Brain poți **planifica** pe categorii, dar nu poți **printa** pe categorii — vezi mai jos.
>
> ### Ce NU merge (încă) prin Second Brain
> **`open`** — ar descărca etichetele PE SERVER, le-ar marca `downloaded` (deci ies din coada tuturor
> stațiilor) și PDF-urile tot n-ar ajunge la operator. Deci prin SB **doar numeri și planifici**.
> Pentru etichetele propriu-zise e nevoie de modul-server al scriptului (`PRINT_QUEUE_SERVER`, cere ZIP de
> la VPS) — vezi „MOD SERVER" mai jos. Până e configurat pe stație, escaladează la Gigi.
>
> **De ce așa:** stația nu trebuie să țină secrete. Fiecare cheie pusă pe un laptop de tură e o pană
> programată peste 90 de zile, într-un loc unde nimeni nu se uită. Vezi [[xconnector-chei-expira-la-90-zile]].

> 🏷️ **Numele canonic de echipă = `depozit:print-queue`** (Second Brain). ACESTA (`plugins/gigi/skills/print-queue/`)
> e SURSA DE COD — aici se fac update-urile (PR), stațiile le iau prin plugin-update / `git pull` (NU editați copii
> locale pe stații). Rămâne în plugin-ul `gigi` fiindcă `print_queue.py` importă `xconnector.py` ca frate. `gigi:print-queue`
> (vechiul index `metrics.print_queue`) e DEPRECAT → tombstone spre `depozit:print-queue`.

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

## Unde ruleaza logica (din 8-sep-2026: pe VPS)

Cu `PRINT_QUEUE_SERVER` setat, `pull`/`plan`/`open` **nu mai calculeaza nimic pe laptop** — cer
`/api/print-queue` de pe VPS, care ruleaza **exact acest fisier** server-side. Comenzile si ce vede
operatorul raman identice; se schimba doar unde se ia decizia.

De ce: cat timp rula local, (1) orice schimbare de regula cerea update de plugin pe fiecare statie,
si (2) nu se vedea ce printeaza nimeni — evidenta statea in `~/.arona_print_queue.db`, pe laptopul
lor. Acum apelurile si etichetele descarcate se scriu central; `GET /api/print-queue/activity?days=7`
arata cine ce a cerut si ce a iesit la print.

**Fara variabila setata, totul merge exact ca inainte (local)** — o statie neconfigurata nu se rupe.
`open` are acelasi efect ireversibil ca inainte: descarcarea scoate eticheta din coada TUTUROR
statiilor, deci se cheama doar la print real.

| **`print_queue.py`** | **Stația.** `pull/plan/open/printed/reprint`, filtrat pe `--machine`. Live din xConnector, cache în SQLite local (`~/.arona_print_queue.db`). | laptopul din depozit / uzina2 |
| `print_queue_central.py` | **Centralul.** `sync/query/print/printed` → construiește `metrics.print_queue` (Postgres) pentru raportare. Cron 01:00 via `print_queue_nightly.sh`. | VPS |

> Stația folosește **întotdeauna `print_queue.py`**. Centralul e pentru cronul de noapte — nu-l rula pe stație.

## Setup stație (o singură dată)
```bash
# Windows (PowerShell), apoi terminal NOU:
setx PRINT_MACHINE uzina2        # sau: depozit
setx PRINT_QUEUE_SERVER https://scripts.arona.ro     # decizia se ia pe VPS, nu aici
setx PRINT_QUEUE_USER   uzina2-svc                   # sau: depozit-svc
setx PRINT_QUEUE_PASS   <din seif: PRINT_QUEUE_PASS_UZINA2 / _DEPOZIT>
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
