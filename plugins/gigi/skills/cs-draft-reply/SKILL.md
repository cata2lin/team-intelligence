---
name: cs-draft-reply
description: Generates a ready-to-review DRAFT reply for a Richpanel ticket using an LLM, grounded in ARONA's real CS procedures + the customer's actual data (orders, delivery status, AWB tracking link, products). Reads the conversation, resolves the customer via gigi:customer-identity (orders + deliverability + AWB), and writes a polite reply in the customer's language following the team playbook (WISMO→DPD tracking link, retur→return form + 14-day refund, broken perfume→free resend+gift, etc.). ⚠️ DRAFT ONLY — never sends a live message to the customer; with --create-draft it saves the draft in Richpanel for an agent to review/edit/send manually. Uses ANTHROPIC_API_KEY (Claude) if present in the KB, else OPENAI_API_KEY. Use for "draft reply", "raspunde la tichet", "genereaza un raspuns", "draft CS", "suggested reply", "schiteaza un raspuns pentru clientul". The live-send is intentionally disabled (team rule).
---

# cs-draft-reply — schiță de răspuns la tichet (LLM + date reale, DOAR DRAFT)

⚠️ **Nu trimite niciodată mesaj live la client.** Doar afișează sau (cu `--create-draft`) salvează un DRAFT în Richpanel pe care agentul îl verifică și trimite manual. Regula echipei: trimiterea automată e dezactivată; acest skill respectă asta (folosește `create_draft`, niciodată `send_message`).

## Cum rulezi
```bash
uv run cs_draft_reply.py --conv 265761                 # afișează draftul propus
uv run cs_draft_reply.py --conv 265761 --create-draft  # + îl salvează ca DRAFT în Richpanel (NU trimite)
```

## Ce face
1. `get_conversation` → citește mesajele clientului.
2. `gigi:customer-identity --conv` → comenzile lui + livrabilitate + **AWB** + produse.
3. LLM cu playbook-ul CS ARONA (proceduri reale per categorie) + datele clientului → **draft în limba clientului**, cu link de tracking/formular real, ton politicos, semnătură.
4. Opțional, salvează draftul în Richpanel (`create_draft`).

## LLM
- `ANTHROPIC_API_KEY` (Claude) dacă există în KB, altfel `OPENAI_API_KEY`. Model: env `DRAFT_MODEL` (default `gpt-4o-mini`).
- Instruit să **NU inventeze** AWB/date/prețuri — folosește doar datele primite; dacă lipsesc, cere-le politicos.

## Exemplu real (conv #265761)
Client întreabă de ridicare personală → draftul refuză politicos, citează comanda reală EST182490 (status Netrimis) + linkul de tracking DPD cu AWB-ul real. Tot draft, neтrimis.

## Note
- Playbook-ul (proceduri per categorie) e în skill — sincron cu „Documentația CS" din ClickUp. Actualizează-l când se schimbă procedurile.
- Necesită `RICHPANEL_MCP_TOKEN`, o cheie LLM, și (prin customer-identity) `DATABASE_URL_METRICS` + SSH la Scripturi.
