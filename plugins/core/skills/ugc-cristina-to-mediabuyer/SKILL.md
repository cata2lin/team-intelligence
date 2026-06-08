---
name: ugc-cristina-to-mediabuyer
description: Hand off UGC video files from Cristina's Google Drive brand folders into the 'Media Buying' Google Sheet for Alex (media buyer): dedupe, skip images, add smart-chip links + status. Use when moving new UGC Drive files into the media-buying sheet.
---

> Author: **Arona core**. Ported from assistant v2.
>
> **Team setup:** no script — the agent performs the Drive→Sheet moves directly via the Google Sheets/Drive v4 APIs per the steps below. Auth uses the OAuth Desktop creds at `~/.config/gcp/` (a combined sheets+drive token at `~/.config/gcp/sheets-drive-token.json`).

# Skill: hand off UGC from Cristina (influencer mgr) to Alex (media buyer)

Cristina drops UGC into Drive (one folder per brand, sub-folder per month).
Alex never gets the Drive folder — he works only off a Google Sheet, so
nothing slips through. This skill = move new Drive files into that sheet.

## The sheet

- **File**: <https://docs.google.com/spreadsheets/d/1PXaZ-Mlcw6JQKW5vvsosJs1h9hFCRRhVfCH921e6hWU/edit>
- **Spreadsheet ID**: `1PXaZ-Mlcw6JQKW5vvsosJs1h9hFCRRhVfCH921e6hWU`
- **Tab (always)**: `Media Buying` (sheetId / gid `390414388`)
- 3 frozen header rows; 22 columns.

## Drive sources

| Brand label in sheet | Drive folder ID |
|---|---|
| `Esteban` (esteban.ro) | `12CtNwig1i33RJ9S7crYhUGAXEPU_ogJu` |
| `GT` / `George Talent` (georgetalent.ro) | `1tiJQfwvyGR9jXQIDj_XQ6Ejfuey_9LbQ` |
| `Nubra` (nubra.ro) | `1dSwRkQjexM3pqpZau4y0FUu0v_QGKI1F` |

Each project folder has **monthly sub-folders** (e.g. `Mai 2026`,
`Aprilie-2026`, `2025-Dec`). Names are inconsistent — keep the exact
subfolder name as-is for column H.

Other brands seen in the sheet (`Belasil`, `Laveta Magica RO/CZ/INT`, `Gento`,
`Genți`, `Covoare`, `Stemma`, `Revii`, `Arona`, `Sosete masaj`, `Nose Strips`)
— folder IDs TBD; ask Iulian.

## Credentials

OAuth desktop creds at `~/.config/gcp/`. Combined sheets+drive token at
`~/.config/gcp/sheets-drive-token.json` with scopes:

```
https://www.googleapis.com/auth/spreadsheets
https://www.googleapis.com/auth/drive
```

(The original `sheets-token.json` is sheets-only; mint the combined one with
`google_auth_oauthlib.flow.InstalledAppFlow.run_local_server` if missing.)

## Block layout — exact rules

A new batch is appended **at the top** (just under the 3 frozen header rows),
above the previous most-recent block, separated by a blank row.

| Row in block | A | B | C | D | E | F | G | H |
|---|---|---|---|---|---|---|---|---|
| 1 (brand) | | `Esteban` (or `GT`, `Nubra`, …) | | | | | | |
| 2 (date) | | `<DD> <RO month> — <N> fisiere` | | | | | | |
| 3..N+2 (files, newest first) | | | filename (ext-stripped) | smart-chip → Drive file | | dropdown status | | subfolder name (e.g. `Mai 2026`) |
| N+3 (separator) | empty | | | | | | | |

### Column rules

- **B (brand row)**: brand label (e.g. `Esteban`).
- **B (date row)**: `<DD> <RO month> — <N> fisiere` where `<DD> <RO month>`
  is *today's date* (the day Iulian/agent runs the handoff), not the file's
  date. RO months: Ianuarie, Februarie, Martie, Aprilie, Mai, Iunie, Iulie,
  August, Septembrie, Octombrie, Noiembrie, Decembrie.
- **C**: stripped filename. Strip these extensions case-insensitively if
  present: `mp4 mov jpeg jpg png heic webp m4v avi mkv`.
- **D**: smart-chip → Drive file. **Display name is rendered by Google** from
  the linked file's metadata; do not set it. Cell value is `"@"` plus a
  `chipRuns` entry.

  ```json
  {
    "userEnteredValue": {"stringValue": "@"},
    "chipRuns": [{
      "startIndex": 0,
      "chip": {"richLinkProperties": {
        "uri": "https://drive.google.com/file/d/<FILE_ID>/view?usp=drivesdk",
        "mimeType": "<drive mimeType>"
      }}
    }]
  }
  ```

- **F**: status dropdown. Values are `Deschis`, `Progress →`, `Done ✓`,
  `Critic`, `Important!`, `Blocat`, `Hold`. Default for new rows:
  - `Important!` if the filename contains `paid` (case-insensitive).
  - `Deschis` otherwise.
- **H**: subfolder name the file came from (e.g. `Mai 2026`, `2025-Dec`).
  Empty if at the brand-folder root.

### Ordering

Within a batch, sort files **descending by Drive `createdTime`**. Newest at
the top of the file block.

## Filtering (what NOT to add)

Apply these filters after listing the Drive folder, **before** writing rows:

1. **Skip images**: any file whose `mimeType` starts with `image/`. Cristina
   uses Drive images mostly as raw assets / iteration; Alex handles videos
   only.
2. **Skip if already in the sheet by fileId**: extract fileIds from existing
   chip URIs in column D across the entire `Media Buying` tab.
3. **Skip if a same-name file already exists anywhere in the sheet**.
   Cristina re-uploads the same content under different fileIds. Match on a
   normalized name:

   ```python
   def norm(s):
       s = s.strip().lower()
       for ext in ('.mp4','.mov','.jpeg','.jpg','.png','.heic','.webp','.m4v','.avi','.mkv'):
           if s.endswith(ext): s = s[:-len(ext)]
       return re.sub(r'\s+', ' ', s).strip()
   ```

4. **Skip duplicates within the batch itself** (same normalized name; keep
   the first / newest).
5. **Brand-specific skip-list of legacy subfolders** that pre-date this
   workflow — these are already represented in the sheet under the old
   ad-hoc format. Currently:
   - `Esteban` → `2025 - Mai - Sep`
   - `GT`, `Nubra` → ask Iulian before each brand's first run.

## Status dropdown

There is **already** a data-validation rule attached to existing F-column
cells. Reuse it via `copyPaste` with `pasteType: PASTE_DATA_VALIDATION` from
any existing F cell that has the dropdown (e.g. `F452` or any old row).

Do **not** build a new `setDataValidation` rule from scratch — even when the
condition list is byte-identical, copying preserves Iulian's future tweaks.

```python
{
  'copyPaste': {
    'source': {'sheetId': TAB_ID,
               'startRowIndex': src_row-1, 'endRowIndex': src_row,
               'startColumnIndex': 5, 'endColumnIndex': 6},
    'destination': {'sheetId': TAB_ID,
                    'startRowIndex': first_file_row-1,
                    'endRowIndex': last_file_row,
                    'startColumnIndex': 5, 'endColumnIndex': 6},
    'pasteType': 'PASTE_DATA_VALIDATION',
    'pasteOrientation': 'NORMAL'
  }
}
```

## API quirks (lessons from Esteban)

- **Drive smart chips: hard limit of 10 per `batchUpdate` call.** Split chip
  writes into chunks of 10. `insertDimension` for blank rows can be one call.
  Sleep ~0.5 s between chip chunks to avoid 429s.
- **Drive `createdTime` is unreliable for legacy bulk dumps**: when an old
  archive is bulk-imported on a single day, every file gets the same recent
  `createdTime`. If a folder name says it's old (e.g. `2025 - Mai - Sep`)
  but every file's `createdTime` clusters in one day, treat the folder as
  legacy and skip-list it.
- **Date label on its own row.** Don't merge the date label onto the first
  file row; if that file is then deleted by dedupe, the date is gone.
- **Append `— <N> fisiere` to the date row** so Iulian/Alex can see the
  batch size at a glance.
- **Insert position**: under the 3 frozen header rows. The previous "newest"
  block sits below your block, separated by one blank row.
- **Always dry-run first**: print the planned row block (B, C, D display, F,
  H) and the counts of `to_keep` vs `to_delete`. Never write without showing
  the plan first.

## Workflow

```text
1. List brand's Drive folder recursively (skip legacy subfolders).
2. Read sheet columns C/D once → build (existing fileIds) and
   (existing normalized names) sets.
3. Filter Drive files: drop images, drop fileId-matches, drop name-matches,
   dedupe within batch.
4. Sort survivors desc by createdTime → N rows.
5. Compose row block:
   - row 1: brand label in B
   - row 2: "<today RO date> — <N> fisiere" in B
   - rows 3..N+2: file rows (C=stripped name, D=chip, F=Deschis/Important!,
     H=subfolder)
   - row N+3: blank separator
6. insertDimension N+3 blank rows under header row 3.
7. updateCells for B/C/F/H columns — one big request, no chip limit.
8. updateCells for chip column D in chunks of <= 10.
9. copyPaste F-column DV from an existing template cell over the new range.
10. Print summary.
```

## Don't

- Don't insert real `HYPERLINK()` formulas — keep smart chips.
- Don't reorder existing rows.
- Don't delete or modify rows below the new block.
- Don't share the Drive folders directly with Alex.
- Don't build the F-column dropdown via `setDataValidation` — copy from a
  template cell.
- Don't include images.
