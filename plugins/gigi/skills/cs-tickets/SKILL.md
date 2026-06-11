---
name: cs-tickets
description: Operate the Richpanel helpdesk (the team's CS inbox for all Arona brands) via the Richpanel MCP, tied to our own order/deliverability data. Triage open tickets (by channel/age/agent, oldest-unanswered first), look up a customer's conversations by email/phone/order, DRAFT and send replies, add private notes, assign/close/snooze/tag, and pull CS analytics (volume, first-response-time, backlog, CSAT, per-agent, per-channel). For order questions it combines the ticket with gigi:cs-order-status / gigi:cs-customer-360 so the reply has the real order + AWB status. Use for "answer this ticket", "triage CS inbox", "find this customer's tickets", "draft a reply", "CS backlog / response time", "tichete", "raspunde clientului", "raspuns timp", "agent workload". Requires the Richpanel MCP connector (https://mcp.richpanel.com/mcp) to be connected.
---

# CS — Tichete Richpanel (operare prin MCP + datele noastre)

> ## 🔒 MOD TESTARE — DOAR DRAFT (regulă tare, activă acum)
> **NU se trimite NICIODATĂ mesaj live la client.** Tot ce ar fi un răspuns către client se face EXCLUSIV ca **`create_draft`** (sau `add_private_note` intern). **Este INTERZIS** `mcp__richpanel__send_message` și orice închidere/răspuns care ajunge la client (inclusiv auto-close pe tichete cu client care așteaptă răspuns). Citit + draft + analytics + triaj intern = OK; trimitere live = NU.
> Pe mașina asta `send_message` e și blocat prin permission `deny` în settings.json.
> **Go-live viitor:** ca să se permită trimiterea reală, se scoate `mcp__richpanel__send_message` din `permissions.deny` ȘI se elimină acest banner. Până atunci: doar draft, de testat.

Helpdesk-ul Richpanel acoperă TOATE brandurile (org `nocturna954`; email-uri contact@esteban.ro / george-talent.ro / grandia.ro / magdeal.ro etc.). MCP-ul e un connector Claude — **Claude apelează uneltele `mcp__richpanel__*`** (nu un script `uv`).

## Realitatea inboxului (de știut)
- Volum uriaș: ~18-19k tichete noi/lună, **~2.500 backlog deschis**, prim-răspuns ~5h, **CSAT nesetat** (0).
- Cel mai mare canal = **comentarii pe reclame Facebook** (`facebook_feed_comment`), apoi `email`. Comentariile FB au rata cea mai mică de închidere.
- Multe „tichete" sunt notificări Judge.me (recenzii) și mesaje din formularul de contact Shopify — de triat/închis în masă, nu necesită răspuns real.
- Agenți: nume afișate ≠ persoana reală; identifică după EMAIL (ex. ralucadiaconu… = Raluca, staverdaniela… = Daniela). Folosește `list_users`.

## Unelte Richpanel (MCP) și când le folosești
- **Triaj:** `list_conversations` (status=open, channel=, startDate/endDate, sortKey=updatedAt) → grupează pe canal/vechime; cele mai vechi neasignate/nerăspunse primele. `list_tags` + filtru `tagIds`.
- **Caută client/comandă:** `search_conversations_by_customer`, `get_customer_by_email_or_phone`. Pentru contextul comenzii (status livrare + AWB), rulează **`gigi:cs-order-status --order <nr>`** sau **`gigi:cs-customer-360 --phone <tel>`** și pune răspunsul pe baza lor.
- **Citește firul:** `get_conversation` (mode=audit pt mesaje paginate).
- **Răspunde (DOAR DRAFT în mod testare):** `create_draft` — întotdeauna; `add_private_note` pt notițe interne. **NU folosi `send_message`** (interzis acum, vezi bannerul). Agentul uman revizuiește și trimite draftul din Richpanel.
- **Gestionează:** `assign_conversation`, `update_conversation_status` (close), `snooze_conversation`, `add_tags_to_conversation`/`remove_tags_from_conversation`/`create_tag`.
- **Analytics:** `query_analytics` (metrics: new_conversations, closed_conversations, backlog, frt, csat; dimensions: agent|channel|team|tags; startDate/endDate) + `get_available_metrics`. `list_ai_closure_candidates` pt tichete închidibile automat.

## Workflow-uri tipice
- **WISMO (unde e coletul):** găsește tichetul → extrage nr comandă din subiect → `gigi:cs-order-status --order EST… --reply` → pune răspunsul ca `create_draft`.
- **Refuz / colet întors:** identifică din `gigi:cs-order-status` (Refuzata) → folosește mesajul de win-back din `gigi:cs-refused-recovery`.
- **Curățenie backlog:** `list_ai_closure_candidates` + tichete Judge.me/recenzii → `update_conversation_status=closed` în masă (după confirmare).
- **Performanță agenți (completă):** `query_analytics dimension=agent` (volum, FRT, închise) combinat cu `gigi:cs-agent-performance` (comenzi plasate + profit) = imaginea totală per agent.

## Reguli
- **MOD TESTARE: NU `send_message` deloc — doar `create_draft`.** Niciun mesaj live la client. Nicio închidere în masă pe tichete cu client care așteaptă, fără confirmare.
- Pentru cifre de comandă/profit folosește skill-urile noastre (sursa de adevăr), nu inventa.
