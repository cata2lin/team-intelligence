---
name: cs-proactive-delays
description: Proactive Customer-Service on delayed shipments — the parcels stuck too long in transit (in-transit for more than N days) that haven't arrived yet. CS contacts the customer PROACTIVELY, before they ask, which prevents "where is my order" tickets AND prevents refusals (an anxious customer refuses on delivery). Sorted oldest-first, with the courier's last status and a ready-to-send reassurance message per market. Use for "stuck shipments", "delayed orders", "parcels in transit too long", "colete blocate", "comenzi întârziate", "proactive delivery follow-up", "which orders are late". Read-only.
---

# CS — Colete blocate / întârziate (contact proactiv)

Coletele care stau prea mult în tranzit → le prinzi ÎNAINTE ca clientul să se enerveze și să refuze. Previne tichete WISMO + previne refuzuri.

## Cum rulezi
```bash
uv run cs_proactive_delays.py --stuck-days 6            # colete în tranzit de >6 zile
uv run cs_proactive_delays.py --brand Grandia --draft  # + mesajul de reasigurare per limbă
uv run cs_proactive_delays.py --stuck-days 10 --json    # pt automatizare
```

## Cum funcționează
- `profit_orders.status_category='In curs de livrare'` cu `created_at` mai vechi de N zile (încă neajunse) din `data/profitability.db` (VPS).
- Sortate descrescător după vechime (cele blocate de 30-50+ zile = probabil pierdute → de investigat/refund).
- Contact din `metrics.orders`; mesaj de reasigurare RO/CZ/PL/BG. Read-only.

## Note
- Coletele foarte vechi (>30z „In curs") sunt de obicei pierdute/blocate la curier — de escaladat la curier sau refund, nu doar mesaj.
