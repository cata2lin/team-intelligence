---
name: cs-profile
description: Scripted (NO-LLM, free, instant) 360° profile of a Richpanel conversation — assembles the 5 pillars from data + rules: WHO the customer is (name, contact, serial-refuser flag, # orders, LTV), the relevant ORDER (status, AWB, courier, products), the CATEGORY + the customer's verbatim request, the stored SENTIMENT + intensity, and the recommended ACTION from the validated ARONA playbook (category→procedure table: WISMO→tracking link, retur→offer alternative first, anulare→cancel AWB in xConnector then Shopify, etc.). For already-linked tickets it needs neither MCP nor an LLM — pure DB + metrics lookups, so it runs on the whole history for $0. The LLM sibling gigi:cs-conversation-profile only adds a polished natural-language paraphrase; this gives the same information at zero cost. Use for "profil tichet", "profil 360 rapid", "cine e clientul si ce vrea pe tichetul X", "ce sa fac pe tichetul asta", "conversation profile fara cost". Read-only.
---

# cs-profile — profil 360° SCRIPTAT (gratis, instant, fără LLM)

Aceeași informație ca profilul LLM, dar din **date + reguli** — zero cost, rulabil pe tot istoricul.

## Cum rulezi
```bash
uv run cs_profile.py --conv 265078
uv run cs_profile.py --conv 265078 --json
```

## Cei 5 piloni (toți din date/reguli)
- 👤 **Client** — nume, contact, flag refuznic, #comenzi, LTV (din link + metrics + profit_orders).
- 📦 **Comandă** — `match_order` → status livrare, AWB, curier, produse.
- ❓ **Categorie** + **mesajul clientului verbatim** (în loc de parafrază LLM).
- 😶 **Sentiment** + intensitate (stocate, rule-based).
- ✅ **Acțiune** — tabel `categorie → procedură` din **playbook-ul validat** (retur→alternativă întâi; anulare→AWB xConnector apoi Shopify; produs spart→poză+retrimitere+cadou; vina noastră→acceptă returul).

## vs `cs-conversation-profile` (LLM)
- `cs-profile` (asta): gratis, instant, pe tot — „ce vrea" = mesajul real.
- `cs-conversation-profile`: ~$0.0005-0.002/profil, adaugă o parafrază șlefuită — pt când vrei prozа frumoasă pe un tichet anume.

Necesită `DATABASE_URL_METRICS` + SSH la Scripturi (profit_orders). Read-only.
