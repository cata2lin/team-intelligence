---
name: richpanel-export
description: Bulk-export the Richpanel helpdesk history into a local SQLite (the official Richpanel API is disabled on the account — this speaks JSON-RPC directly to the MCP endpoint using RICHPANEL_MCP_TOKEN from the KB). Phase 1 exports conversation summaries (subject, first message, channel, agent, customer, timestamps), auto-detects the STORE and the ORDER NUMBER, and CATEGORIZES every ticket by rules (livrare/WISMO, retur, schimb/swap, anulare, modificare comanda, problema produs, refuz livrare, plata/factura, presale, comanda noua, recenzie, spam-automat, comentariu social). Resumable day-by-day. Use for "export Richpanel history", "categorize CS tickets", "ticket history analysis", "istoricul tichetelor", "categorii tichete CS", "ce fel de tichete primim", and as the data layer for the CS documentation / bad-answer audit project. Read-only on Richpanel.
---

# Richpanel — export istoric + categorisire tichete

API-ul oficial Richpanel e dezactivat pe cont, dar token-ul MCP (KB: `RICHPANEL_MCP_TOKEN`) permite apeluri JSON-RPC directe la `https://mcp.richpanel.com/mcp` → export programatic complet.

## Cum rulezi
```bash
uv run richpanel_export.py pull --from 2026-05-12 --to 2026-06-11   # export interval (resumabil)
uv run richpanel_export.py stats                                    # ce avem: categorii/canale/magazine
uv run richpanel_export.py categorize                               # re-rulează regulile pe ce e în DB
```
DB local: `Scripturi/data/richpanel_tickets.db` (tabel `tickets` + `pull_log`). Backfill mare → rulează în fundal; zilele complete se sar la re-rulare.

## Ce extrage per tichet
id, nr conversație, subiect, **primul mesaj**, status, canal, agent (assignee), client (nume/email), **magazin** (din emailul destinație contact@<domeniu> sau prefixul comenzii), **nr comandă** (regex EST/GT/GRAND/... din subiect+mesaj), timestamps, + JSON-ul brut.

## Categorii (reguli pe subiect+prim mesaj, fără diacritice)
`livrare_wismo` · `retur` · `schimb_swap` · `anulare` · `modificare_comanda` · `problema_produs` · `refuz_livrare` · `plata_factura` · `presale_intrebare` · `comanda_noua` · `recenzie_feedback` · `spam_automat` (Judge.me etc.) · `comentariu_social` (comentarii FB/IG la reclame) · `altele`.

## Capcane API (învățate empiric)
- **`endDate` e EXCLUSIV** — pt o singură zi cere `[zi, zi+1)`. start=end → 0 rezultate.
- `sortKey=createdAt` + `order=asc` → 0 rezultate (folosește sortul default).
- `status=all` nu merge — cere separat `open` + `closed`.
- Emoji din Facebook au surrogates rupte → sanitizează stringurile înainte de SQLite.
- Max 50/pagină; paginează cât `len==50`; pauză ~0,4s între pagini.

## Faza 2 (de făcut, planul în memorie)
- `get_conversation` (mode=audit) pe eșantioane per categorie → cum s-a răspuns, timpi, calitate → **documentația CS** + raport „unde s-a răspuns prost".
- Mapare pagini Facebook (`to.id`) → magazin pt comentariile social (acum „necunoscut").
- Îmbogățire cu comanda clientului (metrics.orders + profit_orders) și LLM pe categria `altele`.
