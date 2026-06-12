---
name: richpanel-auto-triage
description: Auto-triage for new OPEN Richpanel conversations — proposes a store TAG + category + PRIORITY (VIP customer LTV≥1000, ANPC/legal escalation = URGENT, retur/problema/anulare = HIGH). Today 99.7% of conversations have no tags, so routing is blind; this fixes that. DRY-RUN by default (shows exactly what it would tag/prioritize, writes nothing); with --apply it writes to Richpanel ONLY tags + priority (internal routing) — NEVER sends a message to a customer. Store from FB/IG page→store map + order-number prefix; category from rules; VIP from metrics.orders LTV. Use for "triaj automat tichete", "tag magazin pe conversatii", "prioritizare tichete CS", "auto-tag Richpanel", "rutare tichete". Default read-only.
---

# richpanel-auto-triage — triaj automat (tag magazin + categorie + prioritate)

⚠️ **DRY-RUN implicit.** Arată ce AR tagui/prioritiza, fără să scrie. Doar cu `--apply` scrie tag+prioritate în Richpanel (operații interne) — **niciodată mesaj la client**.

## Cum rulezi
```bash
uv run richpanel_auto_triage.py                 # DRY-RUN — ce ar tagui/prioritiza
uv run richpanel_auto_triage.py --limit 100
uv run richpanel_auto_triage.py --json
uv run richpanel_auto_triage.py --apply         # scrie tag magazin+categorie + prioritate (niciun mesaj la client)
```

## Cum decide
- **Magazin:** pagina FB/IG (`to.id` → hartă) → prefix nr comandă → domeniu email contact.
- **Categorie:** reguli pe subiect+mesaj (anulare/retur/problemă/modificare/WISMO/plată/presale/general).
- **Prioritate:** ANPC/juridic → **URGENT**; client VIP (LTV ≥1000 RON din `metrics.orders`) → **HIGH**; retur/problemă/anulare → **HIGH**; restul NORMAL.

## Note
- v1 cu reguli compacte de categorie (se mai rafinează); pt clasificare completă vezi `gigi:richpanel-export`.
- Necesită `RICHPANEL_MCP_TOKEN` + `DATABASE_URL_METRICS`. NICIODATĂ `send_message`.
