---
name: apps-script-deploy
description: Create, read and WRITE Google Apps Script (.gs) code for the team programmatically — create a new script project (standalone or bound to a Sheet/Doc), and deploy/patch an existing project's source via the Apps Script API, using the shared looker-sheets service account + domain-wide delegation (impersonating the owner). No more manual copy-paste into the Apps Script editor. Safe by design — push/create are DRY-RUN by default, push always backs up the current code first, and read-back-verifies after writing. Use for "push apps script", "deploy gs code", "create a new apps script", "creaza un google apps script nou", "edit/update Google Apps Script programmatically", "patch the daily report script", "pune codul in apps script", "actualizeaza scriptul Raport Zilnic", "modifica functia adaugaRandZilnic2", "Apps Script API", "container-bound / standalone script source".
argument-hint: "list | get --script-id <id> | lint --file Code=new.gs | push --script-id <id> --as owner@domain --file Code=new.gs [--apply] | verify --sheet-id <id> --tab 'Raport Zilnic 2' | create --title T --as owner@domain [--parent <sheetId>] [--apply] | trash --script-id <id> --as owner@domain [--apply]"
---

# apps-script-deploy
> Author: **Gigi**. Push/patch Apps Script code from the terminal — no editor paste.

## What it does
Reads and writes the source files of a Google Apps Script project through the **Apps Script API**
(`script.googleapis.com`), authenticating with the team **`looker-sheets`** service account
(key in KB secret `GA4_SA_JSON`) via **domain-wide delegation** (impersonating the project owner).
You give it the `scriptId` + the new file content; it backs up, swaps only the files you pass
(preserving the manifest + everything else), writes, and verifies the read-back.

## Auth (do this first — never print the key)
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
```

## Usage
```bash
# 1. find projects the SA can see (Drive)
uv run scripts/gas_deploy.py list

# 2. read + back up a project's code  (READ works with the plain SA)
uv run scripts/gas_deploy.py get --script-id <ID> --out backup.json

# 3. edit the .gs locally (surgical string edits on the backed-up source are safest),
#    syntax-check + LINT it, then push  (WRITE needs --as <owner> impersonation)
node --check Code.js                          # rename .gs->.js for the check; syntax only
uv run scripts/gas_deploy.py lint --file Code=Code_new.gs                                                                  # bug-urile care au picat prod
uv run scripts/gas_deploy.py push --script-id <ID> --as gheorghe.beschea@overheat.agency --file Code=Code_new.gs           # DRY-RUN (lint-ul ruleaza automat)
uv run scripts/gas_deploy.py push --script-id <ID> --as gheorghe.beschea@overheat.agency --file Code=Code_new.gs --apply   # write

# 3b. dupa deploy: a scris scriptul valori REALE in foaie? (verificare prin EFECT)
uv run scripts/gas_deploy.py verify --sheet-id <SHEET_ID> --tab "Raport Zilnic 2" --rows 20 --key-cols C,E,F,G --last-col W
#    ...sau direct din push:  push ... --apply --verify-sheet <SHEET_ID> --verify-tab "Raport Zilnic 2" --verify-cols C,E,F,G

# 4. create a NEW project (standalone, or bound to a Sheet/Doc via --parent <driveFileId>)
uv run scripts/gas_deploy.py create --title "My Script" --as gheorghe.beschea@overheat.agency --file Code=code.gs           # DRY-RUN
uv run scripts/gas_deploy.py create --title "My Script" --as gheorghe.beschea@overheat.agency --file Code=code.gs --apply   # creates + prints scriptId + editor URL
uv run scripts/gas_deploy.py create --title "Bound to sheet" --as <owner> --parent <SPREADSHEET_ID> --file Code=code.gs --apply

# 5. trash a project (Drive trash — reversible ~30 days; DRY-RUN default, refuses non-script files)
uv run scripts/gas_deploy.py trash --script-id <ID> --as gheorghe.beschea@overheat.agency           # DRY-RUN
uv run scripts/gas_deploy.py trash --script-id <ID> --as gheorghe.beschea@overheat.agency --apply
```
`--file NAME=path` → `NAME` is the file name **inside the project, no extension** (e.g. `Code`, `appsscript`).
Repeat `--file` for multiple files. Pass `appsscript=manifest.json` only if you intend to change the manifest.

## One-time setup required (per domain)
1. **Domain-wide delegation** for the SA: Workspace Admin → Security → Access and data control →
   API controls → Domain-wide delegation → add the SA **Client/Unique ID** with scope
   `https://www.googleapis.com/auth/script.projects`. (Get the ID: `kb.py secret-get GA4_SA_JSON` → `client_id`.)
2. **Per-user toggle** on the owner's account: open `https://script.google.com/home/usersettings`
   (logged in as the owner) → **Google Apps Script API = ON**.
3. Apps Script API enabled in the GCP project (`rising-hallway-462906-g7`).

## Notes / gotchas (hard-won)
- **READ vs WRITE asymmetry:** `getContent` works with the plain SA; `updateContent` fails with
  `403 "User has not enabled the Apps Script API"` unless you impersonate (`--as`) an owner who has
  the per-user toggle ON. The "User" in that error = the impersonated principal, not the SA.
- **`updateContent` REPLACES the whole project** — this tool re-sends every existing file and only
  swaps the source of the ones you pass, so the manifest & other files survive. Never push a single
  file with a raw API call.
- **DWD propagation** takes a few minutes after you save it; `unauthorized_client` = DWD scope not
  (yet) authorized for the SA.
- **Edit safely:** prefer surgical string replacements on the backed-up source (assert each match
  count) over retyping the whole file; `node --check` (file renamed to `.js`) catches syntax errors
  before you push. Apps Script uses `;` arg-separators only if the *spreadsheet/script locale* is
  non-US — don't reformat locale-sensitive formula strings blindly.
- **Find the scriptId:** `list` (Drive) shows standalone scripts; for a *container-bound* script use
  the editor → Project Settings → Script ID. Known team scripts: `Daily Gross profit`
  (`11sZUIg_O48pyPKfmvEKnWwDkkmpKCyRa8Osp6wD8XdLp-VKDMCcWrvZZ`, holds `adaugaRandZilnic2` / Raport
  Zilnic 2), `Raport azi` (`1ttVcW2sdJuZmh00VJtgHgXLPk8U7_XVzLZXD9h5N4ITQO5V7cwxn6Xgx`).
- **`create`** makes a standalone project, or a **bound** one with `--parent <driveFileId>` (the Sheet/
  Doc/Form id) — the impersonated owner must be able to edit that container. New projects start with a
  default `Code` + `appsscript`; the tool overwrites `Code` (and adds any extra `--file`).
- **Delete = `trash`:** the Apps Script API can't delete a project, so `trash` moves the Drive file to
  trash (reversible ~30 days). It needs DWD also authorized for `https://www.googleapis.com/auth/drive`
  (already added alongside `script.projects`). `trash` is DRY-RUN by default and refuses anything that
  isn't an `application/vnd.google-apps.script` file (won't touch a Sheet/Doc by mistake).
- **Remote RUN is NOT possible** (probat, nu presupus): `scripts.run`, `processes.list` și
  `deployments.list` dau toate **403** cu delegarea noastră — `scripts.run` ar cere ca scriptul să fie
  legat de proiectul GCP al SA-ului **plus** un deployment „API executable" (schimbare invazivă în
  fiecare script de producție), iar `processes`/`deployments` ar cere scope-uri DWD noi. Execuția
  rămâne pe seama trigger-ului / editorului. **De aceea verificarea utilă e prin EFECT** (`verify`).

## `lint` — cele 7 bug-uri care CHIAR au picat producția
Rulează automat înainte de fiecare `push` (`--no-lint` ca să sari, `--force` ca să pushezi peste ERROR).
| Nivel | Regulă | De ce |
|---|---|---|
| **ERROR** | `getSheetById(` | **nu există** în Apps Script → scriptul „nu scrie nimic" |
| **ERROR** | range sursă cu limită (`$C$2:$C$8408`, sau `'$2:$' + col + '$' + lastRow`) | limita e înghețată la rulare; datele zilei se sincronizează DUPĂ ce scrii rândul → cad dincolo de limită → **0 la tot**. Range-ul deschis (`'sheet'!$C$2:$C`) e OK — lint-ul face diferența |
| WARN | `SpreadsheetApp.flush()` | recalc sincron pe foaie mare = „rulează la infinit" |
| WARN | `ARRAYFORMULA` pe coloană întreagă | recalc greu pe fiecare rând |
| WARN | `LET(` cu `FILTER/ARRAYFORMULA/UNIQUE` | LET merge doar pe **scalari**; cu array-uri s-a rupt și a dat 0 pe FB/TikTok |
| WARN | `setValue()` în buclă | 1 apel/celulă → adună și scrie o dată cu `setValues()` |
| WARN | citire din Sheet în buclă | N round-trip-uri |

## `verify` — a scris scriptul valori reale?
Citește ultimele N rânduri din tab (Sheets API, SA-ul e Editor) și taie exit-code 1 dacă găsește:
- **rând recent cu TOATE metricile 0** — semnătura exactă a incidentului „0 la tot";
- **erori de formulă** (`#REF!`, `#N/A`, …);
- (informativ) **range-uri sursă mărginite** rămase în formulele live — inofensive pe rânduri vechi
  (datele au intrat deja), periculoase pe rândul zilei.
Alarmează doar la rând *complet* zero, nu la o metrică zero per brand — altfel un brand oprit
intenționat (ex. Grandia pe pauză la FB) ar da alarmă falsă în fiecare zi.
