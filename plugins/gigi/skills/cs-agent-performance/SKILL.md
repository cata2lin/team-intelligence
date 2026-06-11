---
name: cs-agent-performance
description: Customer-Service agent performance AND profitability — orders each CS agent PLACED in Shopify (CS tags Raluca/Oana/Andra/Anna/OanaO, the same tags the Scripturi CS tool uses), with placed / delivered / refused counts, delivered revenue, and the actual PROFIT per agent computed with the Scripturi all-in formula (delivered revenue − COGS − transport, VAT-stripped). Note this currently measures only agent-placed orders (manual orders); full ticket throughput comes once Richpanel is connected. Use for "CS agent performance", "profit per CS agent", "how many orders did Raluca place", "which agent is most profitable", "agent workload", "CS team report", "performanta agenti CS", "profitabilitate pe agent". Read-only.
---

# CS — Performanță agenți (comenzi plasate)

Câte comenzi a plasat fiecare agent CS în Shopify + rata lor de livrare. Util pt Anne (management echipă CS).

## Cum rulezi
```bash
uv run cs_agent_performance.py --days 30
uv run cs_agent_performance.py --month 2026-05
```

## Ce arată
Per agent (tag CS din `profit_orders.tags`): comenzi plasate, livrate, refuzate, valoare livrată, % livrare. Tag-urile vin din `profit_settings.cs_tags` (Raluca/Oana/Andra/Anna/OanaO).

## ⚠️ Limitare (importantă)
Deocamdată = **doar comenzile plasate de agent** (comenzi manuale în Shopify, prin tool-ul CS din aplicația Scripturi). NU acoperă tichetele/conversațiile (timp de răspuns, rezolvări). **Throughput-ul complet de tichete vine când se trage Richpanel** — atunci skill-ul se extinde cu metrici de tichete.
