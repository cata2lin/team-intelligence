---
name: cs-conversation-profile
description: Builds a clear 360° PROFILE of a single Richpanel conversation with ALL the data linked together — who the customer is (history, VIP/serial-refuser flags, LTV, # orders), the relevant order (products, delivery status, AWB, courier), what they want / the problem (category + summary), the SENTIMENT IN CONTEXT (negative/neutral/positive + intensity + the real reason, tied to the order/delivery — not just keywords on bare text), and the recommended action per ARONA procedure. An LLM reads the FULL transcript + the unified identity (from gigi:customer-identity) and synthesizes the profile. This is the contextual upgrade over plain sentiment: sentiment + customer + orders + products + status, all connected. Use for "profil conversatie", "vedere completa pe un tichet", "cine e clientul si ce vrea", "sentiment in context", "conversation 360", "rezuma tichetul cu tot contextul", "ce s-a intamplat pe conversatia X". Read-only.
---

# cs-conversation-profile — profil 360° al unei conversații (date LEGATE)

Răspunde la „cine e, ce a cumpărat, care e statusul, ce vrea, cât de supărat și ce facem" — totul într-un singur profil, citit de LLM din transcript + identitatea unificată.

## Cum rulezi
```bash
uv run cs_conversation_profile.py --conv 265078
uv run cs_conversation_profile.py --conv 265078 --json
```

## Ce produce (exemplu real)
```
👤 CLIENT: Raluca Diaconu, 0725…, REFUZNIC, 4 comenzi.
📦 COMANDA: EST137112 (Esteban) | Livrată | curier dpd-ro AWB 8125… | produse: …
❓ PROBLEMA: retur + retrimitere — a primit produs greșit.
😶 SENTIMENT: negativ, intensitate 2 — nemulțumit că a primit altceva decât a comandat.
✅ ACȚIUNE: cere poză → comandă nouă cu produsul corect + parfum cadou (procedura ARONA).
```

## Cum leagă datele
- `get_conversation` (transcript complet) + `gigi:customer-identity --conv --json` (client + comenzi + status livrare + AWB + curier + produse + tichete anterioare + flag refuznic).
- LLM (ANTHROPIC_API_KEY→Claude / OPENAI_API_KEY→gpt, model `PROFILE_MODEL`) sintetizează — fundamentat DOAR pe date, fără inventat.

## De refolosit cu
- `cs-sentiment` (sentiment în masă) · `cs-draft-reply` (după profil, scrie draftul) · `customer-identity` (datele brute).
