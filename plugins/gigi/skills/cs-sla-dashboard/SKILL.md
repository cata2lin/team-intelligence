---
name: cs-sla-dashboard
description: Live SLA dashboard for the Richpanel Customer Service helpdesk — reads analytics directly (business-hours metrics) and shows where CS is falling behind. New-conversation VOLUME + BACKLOG per CHANNEL (email, facebook comments, messenger, instagram…), MEDIAN first-response time (FRT, p50) and % UNATTENDED per channel AND per AGENT, plus auto-flagged OVERLOADED agents. All Richpanel durations are in MILLISECONDS (_bh = business hours only); the skill converts ms→readable hours. Modes: default summary, --channel, --agent, --json, and an internal --triage (DRY-RUN by default; --apply only sets priority/tag, NEVER messages a customer). Use for "CS SLA dashboard", "first response time", "FRT median", "backlog per canal", "cati agenti sunt supraincarcati", "unattended conversations", "messenger backlog", "agent workload Richpanel", "raport SLA suport clienti". Read-only by default.
---

# cs-sla-dashboard — dashboard SLA Richpanel (citire live, business hours)

Răspunde la „cum stă SLA-ul la suport?": volum nou + backlog per canal, FRT median
(prima reacție) + % neatinse per canal și per agent, și care agenți sunt supraîncărcați.
Citește LIVE din `query_analytics` (MCP Richpanel) — nu depinde de export-ul local.

## Cum rulezi
```bash
uv run cs_sla_dashboard.py                  # sumar: canale + agenți + alerte
uv run cs_sla_dashboard.py --days 7         # alt interval (default 30 zile)
uv run cs_sla_dashboard.py --channel        # detaliu pe fiecare canal
uv run cs_sla_dashboard.py --agent          # detaliu pe fiecare agent
uv run cs_sla_dashboard.py --json           # ieșire JSON (pt automatizări / alte skill-uri)
```

## Ce arată (exemplu real, 30 zile)
- ~18.500 conversații noi, backlog ~2.900, FRT median **6,9h** (business hours).
- Top canal pe volum: **Facebook comentarii** (~11k noi, backlog ~1.5k).
- **Messenger (chat)**: ~65% neatinse, backlog aproape cât volumul → semnal roșu.
- **Cristina Sava** face ~63% din închideri; ceilalți agenți au backlog mare și FRT median 12–16h.
- Alertele scot automat agenții supraîncărcați și canalele cu backlog/neatinse peste prag.

## Metrici și unități (Richpanel)
- `new_conversations`, `backlog`, `closed_conversations`, `unattended_new_conversations` = numărători.
- `p50_first_response_time_bh` = FRT **median** (statistic de dashboard, nu se reconstruiește din medii).
- `first_response_time_bh` = FRT **medie** (afișat doar la --agent, ca referință).
- **TOATE duratele sunt în MILISECUNDE**, sufix `_bh` = doar ore de program (business hours).
  Skill-ul convertește ms→ore/zile citibil.
- „% neatinse" = `unattended_new_conversations / new_conversations` pe canal/agent.

## Praguri de alertă (în cod, ușor de ajustat)
- Agent supraîncărcat: volum nou ≥1000, sau ≥25% neatinse (și ≥50 absolut), sau backlog ≥500.
- Canal roșu: ≥70% neatinse (vol ≥200), sau backlog ≥1000.

## Triaj intern (opțional, gated) — NICIODATĂ mesaj la client
```bash
uv run cs_sla_dashboard.py --triage           # DRY-RUN: ce tichete vechi AR marca + ce prioritate
uv run cs_sla_dashboard.py --triage --apply    # scrie DOAR: prioritate HIGH + tag intern
```
- `--triage` alege canalele cu backlog mare + multe neatinse, listează cele mai vechi
  conversații deschise și arată ce AR face.
- **DEFAULT = DRY-RUN**: nu scrie nimic în Richpanel.
- Doar cu `--apply` scrie efectiv, și **doar operații interne**: prioritate `HIGH` +
  tag-ul intern `sla-backlog-urgent`. **Niciodată nu trimite mesaj/răspuns la client** —
  nu există cale către `send_message`/`create_draft` în acest skill.

## Note
- Token: `RICHPANEL_MCP_TOKEN` din KB (apel JSON-RPC direct la `mcp.richpanel.com/mcp`).
- Pentru detaliu pe MAGAZIN, frustrare/escaladări sau citit conversații, folosește
  `gigi:cs-quality-audit` / `gigi:richpanel-export` (DB local cu categorii).
- Companion read-only de SLA pe lângă cele de calitate (`cs-quality-audit`) și
  comentarii (`cs-comment-intelligence`).
