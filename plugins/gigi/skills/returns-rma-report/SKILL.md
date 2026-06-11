---
name: returns-rma-report
description: Analyze Grandia returns & exchanges (rma_requests) — open-RMA pipeline (NEW/IN_PROGRESS/AWAITING_REFUND, oldest-stuck/SLA breaches), refund totals, return-reason breakdown, and worst-returned products/SKUs. Use for 'how many returns are open', 'which RMAs are stuck waiting for refund', 'total refunded this month', 'why are people returning', 'which products get returned most'. Triggers: returns, RMA, refunds, exchange, return reasons, AWAITING_REFUND, retur, why returned.
---

# returns-rma-report

Analiza retururilor și schimburilor magazinului **Grandia** direct din tabela
`rma_requests` (DB Grandia, **read-only**). Răspunde la întrebări operaționale:
câte retururi sunt deschise, ce RMA-uri stau blocate la rambursare, cât s-a
rambursat, de ce returnează clienții și ce produse/SKU se returnează cel mai des.

## How to run

```bash
cd plugins/gigi/skills/returns-rma-report
uv run returns_rma_report.py                  # toate secțiunile (default)
uv run returns_rma_report.py --pipeline       # RMA deschise pe status + blocaje AWAITING_REFUND
uv run returns_rma_report.py --reasons        # motive (count + RON rambursat), RETURN vs EXCHANGE
uv run returns_rma_report.py --products        # top produse/SKU returnate
uv run returns_rma_report.py --month 2026-06   # filtrează pe lună (createdAt)
uv run returns_rma_report.py --pipeline --sla 12   # prag zile pt AWAITING_REFUND blocat
uv run returns_rma_report.py --products --limit 25
```

Fără niciun flag rulează toate cele trei secțiuni + sumarul de rambursări.
Flag-urile `--pipeline / --reasons / --products` se pot combina; `--month`,
`--sla`, `--limit` se aplică peste oricare.

## How it works

- Se conectează la Postgres-ul Grandia cu `pg8000` (SSL). URL-ul vine întâi din
  `DATABASE_URL_GRANDIA` (env), altfel din knowledge base (`kb.py secret-get`).
  Doar `SELECT`, nu scrie nimic.
- **--pipeline**: numără RMA-urile cu status „deschis" (`NEW`, `IN_PROGRESS`,
  `DELIVERED`, `AWAITING_REFUND`), cu vârsta în zile (min/max/medie din
  `createdAt`) și suma de rambursat. Apoi listează RMA-urile `AWAITING_REFUND`
  mai vechi decât pragul `--sla` (default 7 zile), de la cel mai vechi, cu
  `requestNumber`, comandă, motiv, RON și dacă s-a trimis deja la plată
  (`sentToPaymentAt`). Adaugă un sumar de rambursări: total deja plătit
  (`paidAt`/`paidAmount`) vs total în așteptare.
- **--reasons**: split `RETURN` vs `EXCHANGE` (din `type`) + breakdown pe
  `reason` cu număr și RON rambursat (`refundAmount`). Notă: la `EXCHANGE`
  `refundAmount` e 0 (e schimb, nu rambursare).
- **--products**: join `rma_requests → "Order"(id) → "OrderLineItem"(orderId) →
  "Product"(shopifyGid)`, grupat pe SKU, top după număr de RMA-uri distincte,
  cu split RETURN/EXCHANGE.
- `--month YYYY-MM` filtrează totul pe `createdAt` în luna respectivă.

## Limitations

- `rma_requests` **nu** are tabel propriu de linii returnate — modulul
  `--products` atribuie returul **tuturor liniilor comenzii** asociate RMA-ului.
  Dacă o comandă are mai multe produse, toate apar ca „returnate", deci cifrele
  de SKU sunt o aproximare (un indicator de tendință, nu un decont exact al
  articolului fizic returnat).
- „Vârsta" RMA-ului se calculează din `createdAt` până la `now()`; nu există
  istoric de tranziții de status, deci „zile în status" = zile de la deschidere.
- Sumele sunt în RON, brut (cum sunt în `refundAmount`/`paidAmount`); fără TVA-
  adjust sau transport.
- Doar Grandia (singurul magazin cu tabela `rma_requests`).
