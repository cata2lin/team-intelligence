---
name: apps-script-deploy
description: Read and WRITE Google Apps Script (.gs) code for the team programmatically — deploy/patch a script project's source via the Apps Script API, using the shared looker-sheets service account + domain-wide delegation (impersonating the owner). No more manual copy-paste into the Apps Script editor. Safe by design — push is DRY-RUN by default, always backs up the current code first, and read-back-verifies after writing. Use for "push apps script", "deploy gs code", "edit/update Google Apps Script programmatically", "patch the daily report script", "pune codul in apps script", "actualizeaza scriptul Raport Zilnic", "modifica functia adaugaRandZilnic2", "Apps Script API", "container-bound / standalone script source".
argument-hint: "list | get --script-id <id> | push --script-id <id> --as owner@domain --file Code=new.gs [--apply]"
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
#    syntax-check it, then push  (WRITE needs --as <owner> impersonation)
node --check Code.js                          # rename .gs->.js for the check; syntax only
uv run scripts/gas_deploy.py push --script-id <ID> --as gheorghe.beschea@overheat.agency --file Code=Code_new.gs           # DRY-RUN
uv run scripts/gas_deploy.py push --script-id <ID> --as gheorghe.beschea@overheat.agency --file Code=Code_new.gs --apply   # write
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
- **Running a function** via the API (`scripts.run`) needs the script deployed as an API executable
  with a matching GCP project + scopes; this skill covers *code deploy*, not execution — run from the
  editor / a trigger after pushing.
