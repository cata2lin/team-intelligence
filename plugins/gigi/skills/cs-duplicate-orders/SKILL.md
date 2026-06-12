---
name: cs-duplicate-orders
description: Catch DUPLICATE orders for Customer Service — the same customer placing two orders minutes apart (double-tap on checkout, came back and re-ordered) so the team CANCELS the duplicate BEFORE it ships. Every shipped duplicate costs round-trip transport plus an almost-certain COD refusal (the customer never pays twice). Groups by normalized phone (last 9 digits of phone/shippingPhone) + brand, looks at non-cancelled orders placed within 24h of each other, and ranks them by confidence — EXACT (identical line-item signature sku×qty), PROBABIL (same totalPrice within 2h), POSIBIL (within 24h). Returns the suspicious pairs with minutes-apart, order value, masked phone, customer name, confidence level and a ready-to-send confirmation message per market. Use for "duplicate orders", "double orders", "same customer ordered twice", "comenzi duble", "comenzi duplicate", "dublura comanda", "cancel duplicate before shipping", "duplicate order detection". Read-only.
---

# CS — Comenzi duble (de anulat înainte de expediere)

Același client plasează 2 comenzi la minute distanță (a apăsat de două ori, sau s-a întors și a mai comandat o dată). Dacă pleacă amândouă la curier: plătești transport dus+retur pe dublură + clientul refuză aproape sigur (nu plătește de două ori). Le prinzi ÎNAINTE de expediere și anulezi dublura. ~2-3/zi de verificat.

## Cum rulezi
```bash
uv run cs_duplicate_orders.py                    # ultimele 24h, toate magazinele
uv run cs_duplicate_orders.py --hours 48         # fereastră mai largă între cele două comenzi
uv run cs_duplicate_orders.py --store Esteban    # un singur magazin (nume brand)
uv run cs_duplicate_orders.py --store Esteban --draft   # + mesajul de confirmare gata de trimis
uv run cs_duplicate_orders.py --json             # pt automatizare
```

## Cum funcționează
- Grupează `metrics.orders` pe TELEFON normalizat (`COALESCE(phone, "shippingPhone")`, ultimele 9 cifre) + `brandId`, doar comenzi NECANCELATE (`cancelledAt IS NULL`), perechi CONSECUTIVE la `<--hours` una de alta. Filtrează pe ultimele 30 zile (nu trage toată tabela).
- Semnătura de produse = `order_line_items` (sku × quantity, sortat) per comandă — diferențiază dublura accidentală de "a mai vrut să adauge ceva".
- 3 niveluri de încredere, sortate EXACT → PROBABIL → POSIBIL, apoi după cât de apropiate-s în timp:
  - **EXACT** — semnătură line items identică → aproape sigur dublu accidental.
  - **PROBABIL** — `totalPrice` identic + sub 2h.
  - **POSIBIL** — sub 24h, dar valori/produse diferite.
- Mesaj de confirmare RO/CZ/PL/BG (după prefixul comenzii). Read-only total — citește în tranzacție Postgres READ ONLY, nu scrie nicăieri.

## Note
- EXACT = verifică și anulează dublura prima. POSIBIL poate fi legitim (a comandat altceva după) — verifică manual.
- Telefonul e afișat mascat (ultimele 4 cifre). Fără secrete în output.
