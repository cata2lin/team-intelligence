---
name: cs-order-status
description: "Where is my order?" (WISMO) resolved instantly for Customer Service. Paste an order name / phone / email / AWB and get the full picture in one place — order, customer, value, payment status, fulfillment, deliverability category, the AWB + last courier status (with a best-effort LIVE refresh via the awb-track skill), and a ready-to-send reply in Romanian tailored to the order's state (delivered / in-transit / refused / cancelled / not-shipped). Use for "where is my order", "status comanda", "unde e coletul", "is order X delivered", "track customer order", "WISMO", "raspuns client comanda". Read-only.
---

# CS — Status comandă instant (WISMO)

Cel mai frecvent tichet la COD. În loc să caute în 3 locuri, agentul lipește order/telefon/email/AWB și primește tot + răspunsul gata de trimis.

## Cum rulezi
```bash
uv run cs_order_status.py --order EST179388            # după numărul comenzii
uv run cs_order_status.py --awb 81300336310            # după AWB (rezolvă comanda)
uv run cs_order_status.py --phone 0748620192           # ultimele comenzi după telefon
uv run cs_order_status.py --email client@gmail.com     # după email
uv run cs_order_status.py --order EST179388 --reply    # + răspunsul gata de trimis
```

## Ce arată
- Comandă + client (nume, oraș, valoare, dată) din `metrics.orders`.
- Plată + fulfillment + **livrabilitate** (`status_category`: Livrata/Refuzata/In curs/Netrimisa/Anulata) din `profit_orders` (VPS).
- AWB + ultimul status curier; cu `--reply` un **răspuns în română** potrivit stării (livrat / în drum / refuzat→win-back / anulat→reluare).
- Tracking LIVE best-effort prin skill-ul `awb-track` (dacă răspunde la timp).
- Read-only.

## Note
- `--phone` caută pe ultimele 9 cifre (tolerant la prefix +40/0). Întoarce ultimele ~10 comenzi.
- Pentru tracking 100% live pe loturi, folosește direct `gigi:awb-track`.
