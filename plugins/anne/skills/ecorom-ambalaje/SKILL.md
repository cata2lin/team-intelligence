---
name: ecorom-ambalaje
description: Declaratia lunara Ecorom (OIREP ambalaje) — cantitatea de CARTON (ambalaj secundar si de transport) de declarat pentru containerele sosite intr-o luna, calculata ca BRUT − NET din sheet-urile Google "Container NN Analysis" (tab principal NN, coloane Gross Weight / Net weight / Total Boxes), per container + total, cu reconciliere fata de randul "Total excel". Spune si exact unde se introduce in portalul Ecorom (rand Hartie → Ambalaj secundar si de transport → in flux comercial). Foloseste pentru "declaratia Ecorom", "cat carton declar la Ecorom", "ambalaje luna X", "brut minus net pe containere", "raportare OIREP".
---

# ecorom-ambalaje

> Autor: **Anne**. Metoda ei de declarare a ambalajelor la Ecorom: **carton = brut − net**
> din sheet-urile de analiza a containerelor. Formula e decisa (10-sep-2026); nu propune
> alternative (ex. nr. cutii × greutate medie) — doar calculeaza si livreaza cifra.

## Ce face
1. Primeste linkurile/ID-urile sheet-urilor „Container NN Analysis" ale containerelor sosite in luna.
2. In fiecare sheet ia tab-ul principal (titlu numeric `52` sau `C47`), gaseste header-ul cu
   `Gross Weight` / `Net weight` / `Total Boxes` / `Packaging` si aduna liniile de produs
   (linia e recunoscuta dupa `Packaging` de forma `N box = …`).
3. Reconciliaza suma liniilor cu randul `Total excel` din sheet (avertizeaza daca difera >1 kg).
4. Scoate tabelul per container (brut / net / carton / cutii) + TOTAL + instructiunea de portal.

## Rulare
```bash
cd plugins/anne/skills/ecorom-ambalaje/scripts
uv run ecorom_ambalaje.py --luna august --sheet <url1> <url2> ...
uv run ecorom_ambalaje.py --luna august --sheet-file linkuri.txt      # un URL/ID pe linie, # = comentariu
uv run ecorom_ambalaje.py --sheet <id> --json                          # output masina
```
Autentificare: token-ul OAuth Google Sheets de la `~/.config/gcp/sheets-token.json`
(acelasi ca `core:export-to-google-sheet`).

## Incadrarea in portalul Ecorom (se uita usor)
- Randul **Hartie** → coloana **Ambalaj secundar si de transport** → **in flux comercial** = totalul de carton (kg).
- NU la „primar / flux municipal" — portalul da avertisment rosu „cantitati introduse pe alt flux decat cel estimat".
- „Din care reutilizabil" = gol; restul randurilor goale. Salveaza.

## Referinta
- August 2026 = C46–C52 (7 containere): brut 97.340 / net 94.690 → **2.650 kg** carton, 10.755 cutii.
- Sheet-urile de containere: vezi `gigi:inbound-containers` si memoriile `containere-54-55-analiza`, `container-53-analysis`.
- Capcana cunoscuta: furnizorii scriu des brut = net (carton 0 pe container) — se semnaleaza in tabel (⚠), dar cifra ramane brut − net.
