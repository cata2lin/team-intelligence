---
name: daily-ops-briefing
description: One-command morning operations briefing for the whole Arona business — yesterday's and month-to-date revenue, ad spend, contribution profit, MER and orders across all brands (top brands listed), plus the day's ACTION LIST with live counts and the exact skill to run for each: refused orders to recover, risky COD orders to confirm before shipping, parcels stuck in transit, and open RMAs. Use for "morning briefing", "daily ops digest", "how are we doing today", "what needs attention today", "brief de dimineata", "raport zilnic operatiuni", "daily standup numbers". Read-only.
---

# Brief operațiuni de dimineață (un singur comand)

Tot ce contează dimineața + lista de acțiuni a zilei, într-un loc.

## Cum rulezi
```bash
uv run daily_ops_briefing.py
```

## Ce arată
- **Ieri + MTD** (toate brandurile, din `daily_perf.db`): venit, reclame, contribuție (venit−COGS−transport−reclame), MER, comenzi. + top branduri ieri.
- **Acțiunile zilei** cu cifre + skill-ul de rulat:
  - 🔴 Refuzate de recuperat → `gigi:cs-refused-recovery`
  - 🟡 COD de confirmat → `gigi:cod-confirmation`
  - 🟠 Colete blocate → `gigi:cs-proactive-delays`
  - 🔵 RMA deschise → `gigi:returns-rma-report`
  - 🟣 Tichete CS (Richpanel) — *placeholder; se completează când se conectează Richpanel*

## Extensie Richpanel (în curând)
Există un MCP Richpanel (connector Claude: `https://mcp.richpanel.com/mcp`) cu volum tichete / timp de răspuns / CSAT / workload agenți. Când e conectat, linia 🟣 se completează cu numărul de tichete deschise + timp mediu de răspuns (Claude întreabă MCP-ul Richpanel și inserează cifrele în brief).
