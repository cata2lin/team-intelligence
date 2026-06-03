---
name: nas
description: Access the team NAS (shared file storage) for reading and writing files, exports, datasets, images, and documents. Your personal folder is $NAS_ROOT. Use whenever the user wants to save, read, or share files on the NAS.
---

# nas

> Author: **Arona core**. The NAS is for files; the database is for everything else.

Your files live in your own folder on the Synology NAS:
- `$NAS_ROOT` = `\\<NAS_HOST>\<NAS_SHARE>\ClaudeShared\<you>` on Windows, or
  `~/nas/<NAS_SHARE>/ClaudeShared/<you>` on macOS.
- Inside it: **`$NAS_ROOT/data`** (working files) and **`$NAS_ROOT/exports`**
  (generated outputs). The sibling **`_shared`** folder is for team-wide files.

## Connecting
It connects automatically at the start of every session (a SessionStart hook
runs `nas_connect.py` using your NAS login stored in the knowledge base). To
(re)connect or repair manually, or to print your path:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/nas_connect.py"     # prints your $NAS_ROOT
```
Your NAS login lives in the DB (`nas_credentials`), so it works on **any** machine
you onboard — set/change it by re-running onboarding.

## Using it
Read and write under `$NAS_ROOT` like any folder. When you create or import a
file, record it so the team knows it exists:
```bash
kb.py file-add --location nas --path "$NAS_ROOT/data/report.xlsx" --category xlsx --action created
```

## Rules
- Files only on the NAS. **No secrets** on the NAS (those live in the DB).
- Keep your work under your own `$NAS_ROOT`; use `_shared` for things the whole
  team needs.
