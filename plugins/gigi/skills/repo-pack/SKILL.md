---
name: repo-pack
description: "Pack an entire code directory into ONE compact Markdown file (file tree + each file's contents) so it can be handed to the LLM in a single read instead of opening dozens of files — big token/credit saver for code tasks. Respects .gitignore, skips node_modules/.venv/.git/dist/binaries, caps file + total size, estimates tokens. Pure-Python analog of 'repomix'. Use before asking the model to understand/refactor/review a codebase, when onboarding an unfamiliar repo, or to bundle a project (e.g. a Shopify app) into context. Triggers: 'pack the repo', 'da-mi tot codul intr-un fisier', 'repomix', 'bundle the codebase', 'context din tot proiectul', 'summarize this repo', 'review the whole app'."
argument-hint: "<dir> [--stdout] [--include \"*.py,*.ts\"] [--exclude \"test/*\"] [--max-total-tok 120000]"
---

# repo-pack — repo → un singur Markdown (economie de tokeni)

Împachetează un director de cod într-un fișier compact: **arbore de fișiere + conținutul fiecăruia** în blocuri fenced. În loc să deschizi 30 de fișiere (fiecare cu overhead), dai LLM-ului UN context. Analog cu `repomix`, dar pur Python (`uv run`, fără npm).

```bash
uv run scripts/repo_pack.py ./my-app --stdout                     # tot repo-ul la stdout
uv run scripts/repo_pack.py ./my-app                              # scrie my-app_packed.md
uv run scripts/repo_pack.py ./my-app --include "*.py,*.ts,*.tsx"  # doar anumite tipuri
uv run scripts/repo_pack.py ./my-app --exclude "tests/*,*.test.ts"
uv run scripts/repo_pack.py ./my-app --max-total-tok 60000        # plafon de tokeni (omite restul + avertizeaza)
uv run scripts/repo_pack.py ./my-app --max-file-kb 100            # sare fisierele > 100KB
```

## Ce sare automat
`.git`, `node_modules`, `.venv`/`venv`, `dist`/`build`/`.next`, `__pycache__`, `site-packages`, `target`, `coverage`, foldere ascunse, și extensii binare (imagini, PDF, arhive, media, fonturi, .onnx/.bin/.lock…). Respectă `.gitignore` (dacă `pathspec` e disponibil — declarat inline).

## Când îl folosești (regula de aur)
- **ÎNAINTE** de „înțelege/refactorizează/review pe tot proiectul" → pack o dată, apoi lucrezi din UN context. Evită re-citirea aceluiași fișier de mai multe ori.
- Onboarding pe un repo necunoscut (ex. un app din `shopify-app-factory`).
- Combinat cu `--include` ca să dai LLM-ului doar stratul relevant (ex. doar `app/routes/*` + `app/models/*`).
- **Plafon**: `--max-total-tok` te ține sub un buget; ce nu încape e omis explicit (nu tăcut) ca să restrângi cu `--include`.

## Note
- Dependență inline `pathspec` (pt `.gitignore`); rulează cu `uv run`. Fără ea, tot merge (doar fără gitignore).
- Detectează binarul (byte-uri null) și-l sare, chiar dacă extensia scapă filtrului.
- Companion: `gigi:markitdown` (docs→md), `gigi:data-slice` (fișiere de date mari).
