---
name: cs-360
description: "Profil 360° pentru Customer Service — un singur punct de intrare peste client, conversație și comandă. Moduri: `customer` (telefon/email/nume → toate comenzile din toate magazinele, LTV, refuzuri, flag REFUZNIC SERIAL), `conversation` (profil SCRIPTAT zero-LLM al unei conversații Richpanel: client+comandă+categorie+sentiment+acțiune; `--llm` pt sinteză), `order`/`wismo` (order/AWB/telefon/email → status complet + tracking AWB live + răspuns gata). Înlocuiește cs-customer-360 + cs-profile + cs-conversation-profile + cs-order-status. Triggers: cine e clientul, profil client, 360 client, unde e comanda, WISMO, status comandă, profil conversație, refuznic serial, LTV client."
argument-hint: "customer --phone|--email|--name  ·  conversation --conv <id> [--llm]  ·  wismo --order|--awb|--phone"
---

# cs-360 — profil 360° CS (client · conversație · comandă), un punct de intrare

> Unifică **cs-customer-360** + **cs-profile** + **cs-conversation-profile** + **cs-order-status**. Dispatcher-ul
> `scripts/cs360.py` DELEAGĂ la scripturile testate în locul lor (logica neatinsă; rezolvă rp_db/kb prin `__file__`).
> Toate merg pe DB/AWBprint/Richpanel — **NU consumă rația API Shopify**.

## Moduri
```bash
# CLIENT: telefon/email/nume → toate comenzile (toate magazinele), LTV livrat, refuzuri, flag REFUZNIC SERIAL
uv run scripts/cs360.py customer --phone 0748620192      # merge și 40748…/+40748… (ultimele 9 cifre)
uv run scripts/cs360.py customer --name "Rebeca Kiss"
uv run scripts/cs360.py customer --email ana@gmail.com

# CONVERSAȚIE (Richpanel): profil 360 SCRIPTAT (zero-LLM, gratis, instant) — client+comandă+categorie+sentiment+acțiune
uv run scripts/cs360.py conversation --conv <conv_id>          # scriptat (implicit)
uv run scripts/cs360.py conversation --conv <conv_id> --llm    # sinteză LLM (paraphrase; același conținut)

# WISMO / status comandă: order/AWB/telefon/email → comandă+plată+fulfillment+livrabilitate + tracking AWB LIVE
uv run scripts/cs360.py wismo --order GT45911
uv run scripts/cs360.py wismo --awb 81298289998
uv run scripts/cs360.py order --phone 0748620192 --reply       # +răspuns RO gata de trimis
```

## Când folosești ce
- „cine e clientul / ce mai are / e refuznic?" → **customer** (după telefon/nume/email — normalizează formatul singur).
- „unde e comanda / status / tracking?" (WISMO) → **wismo** (după order#/AWB/telefon).
- „spune-mi tot despre tichetul X" → **conversation** (profil scriptat pe o conversație Richpanel legată).
- Orchestrare profil complet: `wismo --order X` (afli telefonul+statusul) → `customer --phone <al lui>` (istoricul) → `gigi:cs-tickets` (tichetele).

## Note
- `customer`/`wismo` NU caută raw în AWBprint — sursa e `metrics.orders` (telefon = ultimele 9 cifre).
- ⚠️ xConnector API NU caută după telefon/nume; pt asta = DOAR `customer`. Vezi `shared/CS.md`.
- Cele 4 skill-uri vechi rămân funcționale (deprecate) — folosește `cs-360`.
