---
name: cs-procedures
description: Learns the ARONA Customer-Service procedures DESCRIPTIVELY from real resolved tickets — for a category, it samples well-resolved tickets, reads only the REAL agents' replies (excludes the Richpanel bot/operator and auto-filled form text), and uses an LLM to document the de-facto procedure: the steps, the real canonical response templates, and crucially WHEN/WHY the team declines or discourages (ARONA is COD and does NOT encourage returns — for personal-hygiene / unsealed perfumes the return is refused; the skill captures that as policy, it does not impose outside assumptions). Use this to document the playbook AND as the team's OWN standard against which to find genuine agent mistakes (never assume procedures — learn them here first). Use for "invata procedurile CS", "documenteaza procedurile din tichete", "cum trateaza echipa returul/WISMO/anularea", "playbook CS real", "ce e procedura corecta la ...". Read-only.
---

# cs-procedures — învață procedurile CS din tichete reale (descriptiv)

NU presupune procedurile. Le **învață** din cum tratează DE FAPT echipa, citind doar replicile agenților reali.

## Cum rulezi
```bash
uv run cs_procedures.py --category retur                 # procedura de-facto pt o categorie
uv run cs_procedures.py --category all --out playbook.md  # toate categoriile → fișier
uv run cs_procedures.py --category problema_produs --limit 20
```

## Ce produce (per categorie)
- **Procedura de-facto** (pașii reali).
- **Replici-șablon reale** ale agenților (cu link-uri unde apar).
- **Politica de refuz/descurajare** — CÂND și CUM refuză/oferă alternativă (ex. retur: ARONA nu-l încurajează → alternative; igienă desigilată → refuz).
- **Edge case-uri**.

## Cum distinge corect
- 🟥 CLIENT = `author=null` · 🤖 BOT/auto = `is_ai` SAU `author="operator"` (exclus) · 🟦 AGENT REAL = cei 6 (Cristina/Diana/Irina/Martina/Alexandra/Mariana). Doar replicile agenților reali contează.

## De ce contează
- E **standardul ECHIPEI** — folosește-l ÎNAINTE de a căuta greșeli (altfel marchezi drept „eroare" un agent care urmează corect politica, ex. refuzul returului la parfumuri desigilate).
- LLM: ANTHROPIC_API_KEY→Claude / OPENAI_API_KEY→gpt (model `PROC_MODEL`). Necesită `RICHPANEL_MCP_TOKEN`.
