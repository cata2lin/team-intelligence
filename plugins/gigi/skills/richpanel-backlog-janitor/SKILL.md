---
name: richpanel-backlog-janitor
description: Cleans the Richpanel backlog safely — identifies non-actionable Facebook/Instagram ad comments to AUTO-CLOSE (noise / testimonials / neutral with no question) and open WISMO tickets to SNOOZE until their delivery ETA. NEVER closes a lead or a complaint (hard veto). DRY-RUN by default (shows exactly what it would close/snooze with counts); only writes to Richpanel with --apply, and even then only status/snooze (internal ops) — NEVER sends a message to a customer. Reuses the comment classification (lead/complaint/testimonial/noise) from cs-comment-intelligence. The FB ad-comment backlog is the biggest noise sink (~11k/30d) — this keeps the queue focused on real work. Use for "curata backlog Richpanel", "auto-close comentarii", "backlog janitor", "snooze WISMO", "inchide comentarii non-actionabile". Default read-only.
---

# richpanel-backlog-janitor — curăță backlog-ul (în siguranță)

⚠️ **DRY-RUN implicit.** Arată ce AR închide/snooze, fără să scrie nimic. Doar cu `--apply` execută (status/snooze intern, **niciodată mesaj la client**).

## Cum rulezi
```bash
uv run richpanel_backlog_janitor.py                 # DRY-RUN — ce ar curăța (auto-close + snooze)
uv run richpanel_backlog_janitor.py --type close    # doar candidații de auto-close
uv run richpanel_backlog_janitor.py --type snooze    # doar WISMO de snooze-uit
uv run richpanel_backlog_janitor.py --json
uv run richpanel_backlog_janitor.py --apply         # EXECUTĂ (închide/snooze în Richpanel — niciun mesaj la client)
```

## Reguli (sigure)
- **AUTO-CLOSE** doar comentarii FB/IG clasificate ca **zgomot / testimonial / neutru fără întrebare**.
- **VETO** — nu închide NICIODATĂ un **lead** (intenție de cumpărare) sau o **reclamație**: alea rămân acționabile.
- **SNOOZE** — WISMO deschise până la ETA estimat (auto-reopen).
- Clasificare reciclată din `gigi:cs-comment-intelligence`.

## De refolosit cu
- `cs-comment-intelligence` (lead-urile/reclamațiile de tratat) · `cs-sla-dashboard` (vezi backlog-ul) · `cs-order-status` (ETA pt snooze).
