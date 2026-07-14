---
name: tom
description: Operate TOM (tom.arona.ro — repo contact546/tom), ARONA's purchase-order + inbound-container tracker for the Guangzhou sourcing team, from the CLI. TOM ingests POs from the source apps (Grandia, Scentum/perfume, VIGO, ARONA-BI) over a signed HMAC API, then Tom's team works each line item order→receive→ship and groups shipped lines into shipments (the sea containers). This skill does two things: (1) READS straight from the DB — list POs, per-line statuses + quantities, shipments, product master, the audit trail, and the "ghost" detector for lines CANCELLED with a note ("Use of tables in multiple sizes") that were PRODUCED and shipped anyway; (2) WRITES as a source app over the signed /api/v1 HMAC API — create a PO, amend lines (while NEW), cancel a PO or specific lines, and enrich the product master (keys from the team KB). Use for "list POs in TOM", "what's in TOM-039", "which lines were cancelled but produced", "create/amend/cancel a PO in TOM", "look up a product's TOM price/awbUid", "the container contents". CRITICAL: TOM is NOT the source of truth for what's physically in a container — a cancelled line can still be produced; the real packing list is the KDocs container file (see gigi:inbound-containers). The internal order/receive/ship lifecycle is Guangzhou-side (Next.js server actions, not an API) and stays in the TOM web UI.
argument-hint: "pos [--source VIGO] | po TOM-039 | ghost | product <sku> | po-get GRANDIA <sourcePoId> | po-cancel GRANDIA <id> --scope ITEMS --lines a,b --reason OUT_OF_STOCK --yes"
---

# tom — operate tom.arona.ro from the CLI
> Author: **Gigi**. Reads TOM's DB and drives its signed source-app API. See [[container-pipeline-kdocs]].

## What this is
`contact546/tom` = a **line-item-centric PO + container tracker** for ARONA's Guangzhou sourcing.
Source apps push POs in over a **signed HMAC `/api/v1`** API; Tom's team then works each line
`NEW → ORDERED → RECEIVED → SHIPPED` and groups shipped lines into **shipments** (the containers).

## Two surfaces, and what this CLI covers
- **✅ Reads** — direct DB (read-only): POs, per-line statuses/qty, shipments, product master, events,
  and the **ghost detector**.
- **✅ Writes as a source app** — the signed `/api/v1` HMAC API: **create / amend / cancel a PO**, and
  **product-upsert**. Keys come from the team KB (`TOM_<SOURCE>_KEY_ID` / `_SECRET`).
- **⛔ NOT exposed** — the internal line lifecycle (order/receive/ship, close-short, reopen, undo) and
  all shipment ops live **only in Next.js server actions** (per-build action IDs, cookie-gated) — they
  are Guangzhou-side and stay in the TOM web UI. Don't try to drive them from a CLI.

## Setup (once)
KB has: `DATABASE_URL_TOM` (reads) + per-source HMAC keys `TOM_GRANDIA_*`, `TOM_PERFUME_*` (=SCENTUM),
`TOM_ARONA_BI_*`, `TOM_VIGO_*`. Base URL `https://tom.arona.ro` (override `TOM_BASE`).
Source → key map: GRANDIA→GRANDIA · SCENTUM→PERFUME · ARONA-BI→ARONA_BI · VIGO→VIGO.

## Run
```bash
cd scripts
uv run --no-project --with requests --with psycopg2-binary tom.py <cmd>
```

## Commands
```bash
# READS (read-only DB)
tom.py pos [--source VIGO] [--status NEW]     # list POs + derived status + #lines
tom.py po TOM-039                              # PO detail: every line's status, qty, cancelNote, shipment
tom.py ghost                                   # ⭐ lines CANCELLED-with-a-note = maybe produced anyway
tom.py shipments                               # containers (code, name, status, #lines)
tom.py product GD-IL-6658                       # product master (sku/barcode/supplier/lastPriceUsd/awbUid)
tom.py events <sourceLineId>                    # the immutable audit trail of a line
tom.py sql "select status,count(*) from purchase_order_items group by 1"

# WRITES as a source app (signed HMAC /api/v1). DRY-RUN unless --yes.
tom.py po-get GRANDIA <sourcePoId>              # read a PO via the API (proves your signing works)
tom.py po-create VIGO --json '{"source_po_id":"vg-9","type":"RESTOCK","items":[{...}]}' --yes
tom.py po-amend  GRANDIA <sourcePoId> --json '{"items":[{"source_line_id":"l1","action":"UPDATE",...}]}' --yes
tom.py po-cancel GRANDIA <sourcePoId> --scope ITEMS --lines l1,l2 --reason OUT_OF_STOCK --yes
tom.py product-upsert GRANDIA --json '{"products":[{"sku":"X","unit_cost_usd":3.2}]}' --yes
```

## The one thing to never forget (the trap)
A line can go **NEW → CANCELLED directly** with a free-text `cancelNote` like *"Use of tables in
multiple sizes"* — 0 ordered qty, no shipment link, invisible to any "what's in this container"
query — **yet the factory produced the goods and they ship inside the container.** So:
- **TOM's shipment lines are a LOWER BOUND, never the packing list.** The real content is the **KDocs
  container file** ([[container-pipeline-kdocs]], skill `gigi:inbound-containers`).
- Always surface `cancelNote` (the CLI does). `tom.py ghost` lists exactly these lines — today it
  flags **TOM-014 (VIGO): 48 lines** of "Use of tables in multiple sizes".
- The PO status is **derived and treats CANCELLED lines as satisfied**, so TOM-014 reads
  `PARTIALLY_SHIPPED` while 48/61 lines are ghost-cancelled. Don't trust the header status alone.

## Safety (enforced)
- HMAC writes are **DRY-RUN unless `--yes`** (it prints the payload it would sign).
- `{source}` in the URL **must equal** the key's source app (a GRANDIA key can't touch a SCENTUM PO).
- Amend/cancel only apply to lines still `NEW` (SHIPPED lines are refused). Cancel = **soft** (status
  CANCELLED + reason/note), never a delete. Reads are read-only. Secrets never printed.

## HMAC signature (for reference)
`canonical = METHOD\nPATH\nUNIX_TS\nSHA256_HEX(body)` · `sig = HMAC_SHA256_hex(secret, canonical)` ·
headers `X-Tom-Key`, `X-Tom-Timestamp` (±300s), `X-Tom-Signature`, `Idempotency-Key` (uuid, every POST).

**Related:** [[container-pipeline-kdocs]], [[awb-tom-po-integration]], [[scentum-erp-cli]], [[bi-grandia]], [[inventorysync-app]]
