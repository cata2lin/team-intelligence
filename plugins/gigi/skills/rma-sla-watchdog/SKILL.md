---
name: rma-sla-watchdog
description: SLA breach detector for the Grandia returns/exchanges (RMA) pipeline — finds RMAs STUCK at each stage instead of just listing them. Flags (1) NEW unapproved older than N days, (2) IN_PROGRESS where the return AWB is still NEW past N days from approval (the customer never dropped off the parcel → chase them), (3) AWAITING_REFUND past N days (money promised, not paid), and (4) EXCHANGE marked DELIVERED with no SWAP courier shipment (the replacement never left). PLUS the AWB form-error queue (rma_logs action='awb_failed') — customers blocked by the DPD form failure (dominant cause: "Localitate nevalida"), with a cause breakdown and the top failing localities to drive a product fix (locality→DPD mapping). Output groups breaches by stage with age + refund amount + customer contact, and a separate form-errors section. Use for "which RMAs are stuck/blocked", "returns SLA breaches", "RMA watchdog", "customers blocked by the return AWB error", "which return localities fail in DPD", "exchanges that never shipped", "refunds overdue", "retururi blocate", "rambursari intarziate". Read-only, Grandia only.
---

# rma-sla-watchdog

Extinde `returns-rma-report` (care doar **listează** RMA-urile deschise pe status)
cu **detecție de BLOCAJ per etapă** a pipeline-ului de retururi/schimburi **Grandia**
+ **coada de erori la generarea AWB**. Spune nu doar „câte RMA-uri sunt deschise", ci
**care au depășit SLA, pe ce etapă, de cât timp, cu ce sumă și pe cine să suni**.

Tot ce face e **read-only** (doar `SELECT` în DB-ul Grandia). Nu scrie nimic nicăieri.

## Cum rulezi

```bash
cd plugins/gigi/skills/rma-sla-watchdog
uv run rma_sla_watchdog.py                  # tot raportul: breach-uri + erori formular
uv run rma_sla_watchdog.py --breaches       # doar breach-urile de SLA pe etape
uv run rma_sla_watchdog.py --errors         # doar coada de erori AWB + top localități
uv run rma_sla_watchdog.py --errors --open-only   # doar erorile pt RMA-uri ÎNCĂ deschise
uv run rma_sla_watchdog.py --new-sla 2 --awb-sla 5 --refund-sla 3   # praguri custom (zile)
uv run rma_sla_watchdog.py --json           # ieșire JSON pt automatizare
```

Praguri implicite: `NEW` neaprobat > **2 zile**, AWB încă NEW > **5 zile** de la
aprobare, `AWAITING_REFUND` > **3 zile**. Etapa EXCHANGE-fără-SWAP nu are prag (orice
schimb livrat fără colet de schimb e blocaj).

## Etapele de breach

1. **NEW neaprobat** > `--new-sla` zile de la `createdAt` (cerere de retur care zace netriată).
2. **IN_PROGRESS cu AWB încă NEW** > `--awb-sla` zile de la `approvedAt` — AWB-ul de retur
   (`rma_awbs.status`) rămâne `NEW` cât timp clientul **n-a predat** coletul la curier → de dat chase.
3. **AWAITING_REFUND** > `--refund-sla` zile (de la `sentToPaymentAt`, altfel `createdAt`) —
   bani promiși clientului, neplătiți încă; arată dacă s-a trimis deja la plată.
4. **EXCHANGE DELIVERED fără colet SWAP** — retur de tip schimb marcat livrat, dar nu există
   `courier_shipments` cu `type='SWAP'` pe comanda lui → produsul de schimb **n-a plecat** spre client.

Fiecare etapă e grupată separat, cu numărul de RMA, suma totală de refund, vechimea celui mai
vechi și, pe rând, `requestNumber`, comanda, zile, tip (RETURN/EXCHANGE), RON și contact
(nume · telefon, sau email dacă lipsește telefonul).

## Coada de erori formular (awb_failed)

Din `rma_logs` cu `action='awb_failed'` (erorile reale la generarea AWB prin DPD). Arată:
- **agregat pe cauză** — cine pică formularul (cauza dominantă: `Expeditor Localitate:
  Localitate nevalida`, adică localitatea de ridicare nu e recunoscută de DPD);
- **top localități care pică** (din `pickupCity`/`pickupCounty`) — semnalul durabil pentru
  **fixul de produs**: o mapare localitate→DPD în formular ar elimina majoritatea erorilor;
- **detaliu pe rând** cu RMA, status curent, localitate, cauză, dată;
- câte RMA-uri sunt **încă deschise** (clientul chiar blocat acum) vs retried/rezolvate ulterior.
  `--open-only` păstrează doar erorile pe RMA-uri încă deschise.

## Cum funcționează

- Postgres Grandia cu `pg8000` (SSL). URL din `DATABASE_URL_GRANDIA` (env) sau din knowledge
  base (`kb.py secret-get`). Doar `SELECT`.
- Legături: `rma_awbs.requestId → rma_requests.id`; `courier_shipments.orderId →
  rma_requests.orderId` (id-ul intern Grandia al comenzii, **nu** orderName); `rma_logs.requestId
  → rma_requests.id`. Coloanele camelCase sunt citate cu ghilimele duble în SQL.

## Limitări

- Doar **Grandia** are aceste tabele RMA.
- „Vechimea" se calculează din timestamp-ul relevant al etapei până la `now()`; nu există
  istoric complet de tranziții de status.
- Cauza erorii se extrage din textul `rma_logs.message` (după DPD); câmpurile `details.city`
  sunt adesea null, de aceea localitatea se ia din `rma_requests.pickupCity`.
- Sumele sunt `refundAmount` brut (RON); la EXCHANGE e de regulă 0 (e schimb, nu rambursare).
