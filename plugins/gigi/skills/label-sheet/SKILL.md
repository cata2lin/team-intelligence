---
name: label-sheet
description: "Generate a print-ready PDF sheet of EQUAL labels from a list of words/short texts — any label size (e.g. 3x2 cm), A4 portrait or landscape, uniform font, cut borders, auto-wrap for long names, fits as many per page as possible (or one-page mode). Use for 'fă etichete', 'pune cuvinte pe A4', 'sheet de etichete', 'print labels with these words', 'etichete 2x3 cm', 'label sheet for perfume notes / ingredients', 'cate incap pe A4'."
argument-hint: "[--w 30 --h 20] [--landscape] [--one-page] [--copies N] [--words-file f.txt]"
---

# label-sheet — etichete egale pe A4 (PDF de printat)

Pune o listă de cuvinte/texte scurte în **etichete EGALE** pe A4, ca PDF de printat + tăiat. Font **uniform** pe toate (cel mai mare la care intră toate; cuvintele lungi se rup pe 2 rânduri la același font), contur subțire de tăiere, text centrat.

```bash
uv run scripts/labels_sheet.py                                  # lista default (note de parfum), 3x2cm, portrait
uv run scripts/labels_sheet.py --w 20 --h 30                    # alta dimensiune eticheta (mm): 2 lat x 3 inalt
uv run scripts/labels_sheet.py --landscape                     # pagina A4 pe lung (297x210)
uv run scripts/labels_sheet.py --one-page                      # micsoreaza inaltimea cat sa intre TOATE pe O pagina
uv run scripts/labels_sheet.py --words-file lista.txt          # cuvinte din fisier (unul/rand)
uv run scripts/labels_sheet.py --copies 6                      # 6 bucati din FIECARE cuvant (grupate)
uv run scripts/labels_sheet.py --fill                          # repeta lista pana umple pagini complete
uv run scripts/labels_sheet.py --out /cale/fisier.pdf          # unde scrie PDF-ul
uv run scripts/labels_sheet.py --per-label                     # font diferit per eticheta (default = UNIFORM)
```

## Opțiuni
- `--w` / `--h` = lățime / înălțime etichetă în **mm** (default 30x20). Etichetele sunt mereu **egale** (grilă de celule identice).
- `--landscape` = pagina A4 culcată (297x210). Combinat cu dimensiunea etichetei decide câte intră (ex: pe landscape, etichete 20x30 → 14x7 = **98/pagină**).
- `--one-page` = reduce ușor înălțimea etichetei cât să încapă TOATE cuvintele pe o singură pagină (păstrează lățimea).
- `--copies N` = N exemplare din fiecare cuvânt, grupate. `--fill` = repetă lista ciclic până la pagini complete.
- Calculează singur **câte etichete intră pe pagină** și pe câte pagini.

## Note
- **Print la „Actual size" / 100%** (NU „Fit to page"), altfel dimensiunea fizică nu mai e cea cerută.
- Font uniform by default (toate textele aceeași mărime); `--per-label` revine la auto-fit individual.
- Dependențe declarate inline (PEP723 `reportlab`) — rulează cu `uv run`, fără setup.
- Editezi lista default direct în script (`DEFAULT_WORDS`) sau dai `--words-file`.
