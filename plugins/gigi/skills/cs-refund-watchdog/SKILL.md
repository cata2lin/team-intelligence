---
name: cs-refund-watchdog
description: Catches PROMISED-but-NOT-EXECUTED refunds — the single most expensive Customer-Service bug, because an unreturned refund means legal/regulatory risk (ANPC complaint), a card chargeback, and a 1-star review. Two read-only sources. PHASE 1 (metrics.orders, every store) — orders paid by CARD (shopify_payments) that were CANCELLED but are still financialStatus=PAID with totalRefunded=0: the money was taken, the order voided, nothing returned. PHASE 2 (grandia RMA system) — return requests with a refundAmount owed that were sent to payment (status AWAITING_REFUND / DELIVERED / IN_PROGRESS / NEW) but never paid (no paidAt, paidAmount=0, no rma_payments row): a refund formally promised and still owed. Outputs each case with amount, age in days, order, customer, source (card/RMA), grouped by store, with the total RON at risk and the oldest cases to attack first. Use for "refunds not paid", "money taken but not refunded", "ANPC risk", "chargeback risk", "refund watchdog", "outstanding refunds", "RMA not refunded", "comenzi anulate fara refund", "retururi neplatite", "refund-uri neefectuate". Read-only.
---

# CS — Refund Watchdog (refund-uri promise dar neefectuate)

Prinde cel mai scump bug de Customer Service: clientul a dat banii (card) sau i s-a promis returul (RMA), dar banii NU s-au întors. Fiecare astfel de caz = risc ANPC + chargeback + recenzie 1 stea. Citește din două surse, total read-only.

## Cum rulezi
```bash
uv run cs_refund_watchdog.py                 # tot (card pe toate magazinele + RMA Grandia)
uv run cs_refund_watchdog.py --store EST     # doar un magazin (faza card)
uv run cs_refund_watchdog.py --phase card    # doar faza 1 (card anulat, neretur)
uv run cs_refund_watchdog.py --phase rma     # doar faza 2 (RMA Grandia neplătit)
uv run cs_refund_watchdog.py --min-age 7     # doar cazuri mai vechi de 7 zile
uv run cs_refund_watchdog.py --min-amount 200
uv run cs_refund_watchdog.py --json          # pt automatizare / briefing
```

## Cum funcționează
- **FAZA 1 (card)** — `metrics.orders`: `cancelledAt` not null + `'shopify_payments'` în `paymentGatewayNames` + `financialStatus='PAID'` + `totalRefunded=0`. Magazinul se deduce din prefixul comenzii (EST, GRAND, NUB…). Vechimea = zile de la anulare.
- **FAZA 2 (RMA)** — `grandia.rma_requests` (type=RETURN): `refundAmount>0` și status în `AWAITING_REFUND / DELIVERED / IN_PROGRESS / NEW`, dar `paidAt` null, `paidAmount=0` și niciun rând în `rma_payments`. **AWAITING_REFUND = cel mai sever** (trimis la plată, neplătit). Vechimea = de la `sentToPaymentAt` (fallback `deliveredAt` → `approvedAt` → `createdAt`).
- Sortare pe vechime/severitate; sumar cu **total RON în risc** per sursă și per magazin + top „cele mai vechi de atacat primele".

## Note
- Faza RMA e momentan doar Grandia (acolo există sistemul de RMA în Postgres). `--store` care nu conține „gran" sare automat faza RMA.
- Cazurile AWAITING_REFUND sunt prioritatea 0: clientul a returnat marfa și i s-a confirmat refundul — orice întârziere acolo e direct expunere ANPC.
- Faza card prinde de obicei comenzi anulate manual de CS fără să fi apăsat și „Refund" în Shopify — exact scurgerea pe care o vânează skill-ul.
