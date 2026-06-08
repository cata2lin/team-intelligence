---
name: frisbo-api
description: Complete reference for the Frisbo Store-View / Fulfillment-Monitor API (ingest.apis.store-view.frisbo.dev, OpenAPI 3.1). Use whenever reading, syncing, parsing, or debugging anything that calls Frisbo, receives Frisbo order/product data, or maps Frisbo statuses. Covers every endpoint, every order/product/shipment field, all status enums (aggregated vs raw shipment vs fulfillment), notes/tags/metafields, pagination, and sync best practices. The authoritative machine-readable spec is bundled as openapi.json next to this file.
---

# Frisbo Store-View API — Complete Reference

> Authoritative machine-readable spec: **`openapi.json`** in this skill folder (OpenAPI 3.1, 12 endpoints, 157 schemas). This document annotates and explains it. When a detail here is ambiguous, the JSON wins.

Frisbo is the 3PL/fulfillment provider. The **Store-View / Fulfillment-Monitoring API** is *read-mostly from the merchant's side*: you push orders/products in, and you pull back the **fulfillment + courier state** Frisbo has computed. It is the source AWB Print Manager syncs orders from.

## 0. Connection

> **Team setup (Arona intelligence center):** the Frisbo JWT(s) live in the shared secret store, not a local `.env`. Fetch with the `core:fetch-secret` skill: `kb.py secret-get FRISBO_API_TOKEN` (the primary JWT) or `kb.py secret-get FRISBO_ORG_TOKENS` (a JSON array, one JWT per organization — iterate for multi-org sync). Use the raw token as `Authorization: Bearer <token>`.

| | |
|---|---|
| **Base URL** | `https://ingest.apis.store-view.frisbo.dev` |
| **Auth** | `Authorization: Bearer <token>` on every request. Missing/invalid → `401`. |
| **Auth scheme** | OAuth2 password flow, `tokenUrl: /api/v1/users/login`. In practice each **organization** is given a long-lived JWT by the Frisbo account manager. |
| **Multi-org** | One JWT per Frisbo *organization*. The JWT payload embeds `organization_uid`. To sync N orgs, iterate tokens. Decode `token.split('.')[1]` (base64) → `{organization_uid}`. |
| **Rate limit** | 20 req/sec (per Frisbo docs / `FRISBO_RATE_LIMIT`). Use a token-bucket limiter; there is no documented `Retry-After`, so back off on `5xx`/timeouts. |
| **Content-Type** | `application/json` for POST bodies. |
| **Errors** | `422` → `HTTPValidationError {detail:[{loc,msg,type}]}`. `500` → `GeneralResponseModelError {success:false,data:[]}`. Error bodies for orders also surface in `errors[]` on the order itself. |
| **Selling channels** | `shopify`, `magento2`, `woocommerce`, `standard_api_v1`, `easysales_ro`, `gomag_ro`. |

---

## 1. Endpoints (12)

### Orders
| Method | Path | Purpose | Response schema |
|---|---|---|---|
| GET | `/orders/search` | **List/sync orders** (paginated). | `SearchOrdersResponseOUT` |
| GET | `/orders/order/{order_uid}` | **Full single order** (richest object). | `OrdersOrderResponseOUT` → `Order` |
| POST | `/orders/order` | Create/update one order. | `CreteOrderResponseAPIOUTData` → `OrderResponseOut` |
| POST | `/orders` | Create/update **bulk** (≤250). | `CreteOrdersResponseAPIOUTData` |
| GET | `/orders/order/{order_uid}/shipment` | One shipment (label) + order. | `OrdersShipmentResponseOUT` |
| GET | `/orders/order/{order_uid}/shipments` | **All** shipments/labels. | `OrdersShipmentsResponseOUT` |
| GET | `/orders/order/{order_uid}/print_shipment` | Retrieve/print the label + order. | `OrdersPrintShipmentResponseOUT` |
| POST | `/orders/order/{order_uid}/regenerate_shipment` | Recreate the courier label. Body `{order_uid, parcel_count}`. | `boolean` |
| GET | `/orders/order/{order_uid}/mark_waiting_for_courier` | Transition → waiting_for_courier. | `OrdersMarkInWaitingForCourierResponseOUT` |
| GET | `/orders/order/{order_uid}/mark_waiting_for_pickup` | Transition → waiting_for_pickup. | `OrdersMarkInWaitingForPickupResponseOUT` |

### Inventory items (products)
| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/inventory_items/search` | List products (paginated). | `InventoryItemsOUT` |
| POST | `/inventory_items/inventory_item` | Create/update a product. | `InventoryItemsOUTAPI` |

### `GET /orders/search` — parameters
All optional, all query params:
| Param | Type | Notes |
|---|---|---|
| `skip` | int (default 0) | pagination offset |
| `limit` | int (default 0) | page size. **Hard server cap = 100.** Pass 100. |
| `created_at_start` / `created_at_end` | date-time | filter by order creation |
| `updated_at_start` / `updated_at_end` | date-time | **filter by last update — use this for sync** (catches status changes on old orders) |
| `reference` | string | order reference / name |
| `phone_number` | string | customer phone |
| `email_address` | string | customer email |
| `managed_by` | enum `frisbo`/`others` | |
| `aggregated_status_keys[]` | string (repeatable) | filter by aggregated status |
| `display_priorities[]` | string (repeatable) | UI priority 0–5 |
| `store_uids[]` | string (repeatable) | **⚠ KNOWN BUG: passing many store_uids returns ~0 orders.** Do NOT filter by store on the wire when syncing multiple stores — fetch all, filter store-side in Python. |

**Response `SearchOrdersResponseOUT`:**
```
{
  "orders": [OrderResponseOut, ...],      // this page
  "count": <int>,                          // TOTAL matching orders (all pages) — use for completeness checks
  "display_statistics": {display_priority_0..5: int},
  "statuses_statistics": [ {key: <aggregated_status>, count: int}, ... ]  // PRE-AGGREGATED counts per status for the whole filtered set
}
```
> `count` and `statuses_statistics` are gold for sync/report verification: you can get authoritative per-status totals for a period **without paging every order**.

### `GET /inventory_items/search` — parameters
`name`, `skip` (0), `limit` (default **250**), `inventory_level_owner` (`all`/`frisbo`/`other`), `store_uids[]`, `days_left[]`.

---

## 2. The Order object — full field map

There are **two** order shapes. Know which endpoint gives which:

| Field | `OrderResponseOut` (search, bulk create) | `Order` (GET single) |
|---|:---:|:---:|
| uid, organization_uid, store_uid | ✅ | ✅ |
| created_at, updated_at, imported_at | ✅ | ✅ |
| aggregated_status, aggregated_courier | ✅ | ✅ |
| shipment_status, fulfillment_status, financial_status, payout_status | ✅ | ✅ |
| state | ✅ | ✅ |
| line_items | ✅ | ✅ |
| prices, payment | ✅ | ✅ |
| shipping_address, shipping_courier | ✅ | ✅ |
| reference | ✅ | ✅ |
| **tags** | ✅ | ✅ |
| **notes** (selling_channel string) | ✅ | ✅ |
| **statuses_history** (incl. raw_shipment_statuses) | ✅ | ✅ |
| sla | ✅ | ✅ |
| metadata (key/value array) | ✅ | ✅ |
| **note** (free-text string) | ❌ | ✅ |
| **comments** (count/status/team) | ❌ | ✅ |
| **messages** (count/status/team) | ❌ | ✅ |
| **metafields** (number_of_parcels, exchange_package) | ❌ | ✅ |
| reverse_logistics | ❌ | ✅ |
| fulfillments[], fulfillment_conditions, shipping_conditions | ❌ | ✅ |
| inventory_status, selling_channel, stage, managed_by, display_priority | ❌ | ✅ |
| workflow_process, module_processes, errors, es_version | ❌ (errors ✅) | ✅ |

> **Implication for sync:** `/orders/search` now returns **tags, notes(selling_channel), statuses_history, metadata** — enough for test-order filtering by tag and raw-status cross-checks. But `metafields.number_of_parcels`, free-text `note`, and `comments`/`messages` require a per-order `GET /orders/order/{uid}`.

### 2.1 Key nested objects

**`tags`** = `OrderTags`:
```
{ "selling_channel": [ { "key": "<string>", "value": "<string|null>" }, ... ] }
```
Shopify tags arrive here. A tag may be `{key:"test", value:null}` or carry a value. (Scripturi's `tag=test` exclusion maps to: any `tags.selling_channel[].key == "test"`.)

**`notes`** = `OrderNotes`: `{ "selling_channel": "<string|null>" }` — the order note from the storefront.
**`note`** (single-order only): free-text string.

**`metadata`** = array of `{key, value}` (both string|null). Free-form key/value bag.

**`metafields`** (single-order only) = array of `{kind, value}` where `kind ∈ {number_of_parcels, exchange_package}`:
- `number_of_parcels` → `{number_of_parcels: int}` (the "nr_cutii" / box count).
- `exchange_package` → `{exchange_package: bool}`.

**`prices`** = `OrderPrices`: `subtotal_price`, `total_discounts`, `total_price`, `total_tax`, `total_tip_received` (all numbers, in `payment.currency`).

**`payment`** = `OrderPayment`: `currency` (ISO-4217 enum), `gateway_names[]` (payment gateway strings — COD detection: gateway starting with `"plat"`/"Plată ramburs" = cash-on-delivery), `bank_deposit`/`bank_deposit_value`, `cash_on_delivery`/`cash_on_delivery_value`.

**`line_items[]`** = `OrderLineItem`: `inventory_item {sku, uid, title_1, title_2}`, `price`, `quantity`, `reserved_quantity`, `missing_quantity`, `has_missing`, `requires_shipping`, `total_discount`, `discount_allocations[]`, `tax_lines[] {title, price, rate}`, `taxable`, `gift_card`, `properties[] {name,value}`, `last_operation` (nothing/create/update), `managed_by`. **SKU lives at `line_items[].inventory_item.sku`** (may be null; `title_1` is the product name).

**`aggregated_courier`** = `OrderAggregatedCourierData`: `name`, `key`/`subkey` (courier id), `tracking_number`, `is_known`, `has_courier`, `left` (true if it physically left the merchant location).

**`shipping_address`** = `OrderShippingAddress`: address1/2, city, company, country, **country_code** (ISO 3166-1), email, first_name, last_name, name, phone, province, province_code, zip.

**`statuses_history`** = `OrderStatusesHistory`:
```
{ aggregated_status: [OrderAggregatedStatus...],
  shipment_status:   [OrderShipmentStatus...],
  raw_shipment_statuses: [OrderShipmentStatus...],   // <-- the unprocessed courier statuses
  fulfillment_status:[OrderFulfillmentStatus...],
  sla_statuses:      [OrderSLAStatus...] }
```
Each status entry is `{key:<enum>, date:<datetime>}`; `aggregated_status` entries also carry `priority`.

**`reverse_logistics`** (single-order): `kind` (returning_to_sender/return_items), and `initiated/in_transit/received/processed` each `{value:bool, date}`.

---

## 3. Statuses — the heart of it

Frisbo computes an order's **`aggregated_status`** from the raw courier/fulfillment signals. Each candidate status has a **priority** (`OrderAggregatedStatusPriority`); the highest-priority applicable status wins and becomes `aggregated_status.key`. The full unprocessed courier statuses are kept in `statuses_history.raw_shipment_statuses`.

> **This is the key cross-program insight:** AWB Print uses Frisbo's pre-computed **`aggregated_status`**. Scripturi maps the **raw courier status text** itself. They should land on the same outcome *if* the category mappings agree — `statuses_history.raw_shipment_statuses` lets you verify the raw→aggregated mapping order-by-order.

### 3.1 `aggregated_status.key` — all 53 values
Grouped by what they mean for an order's outcome:

**Delivered / success**
`delivered`, `customer_pickup`, `personal_pickup`, `in_parcel_locker`

**In transit / on its way (left warehouse, not yet final)**
`in_transit`, `out_for_delivery`, `redirected`, `deferred_delivery`, `fulfilled`, `sending`

**Returned / refused (came back)**
`refused`, `unsuccessful_delivery`, `returning_to_sender`, `received_by_sender`, `back_to_sender`, `incorrect_address`, `returned`, `shipment_refunded`

**Lost**
`lost`, `lost_in_transit`, `lost_in_warehouse`

**Cancelled**
`cancelled`, `shipping_canceled`, `fulfillment_cancelled`

**Not shipped yet / in warehouse pipeline**
`not_generated`, `generating_awb`, `generated_awb`, `waiting_for_courier`, `waiting_for_pickup`, `ready_for_picking`, `in_picking`, `pending_fulfillment`, `not_fulfilled`, `partial`, `on_hold`, `out_of_stock`, `initializing_fulfillment`, `awaiting_fulfillment_initialization`, `awaiting_fulfillment_order`, `awaiting_fulfillment_hold_release`, `initializing_shipment_generation`, `awaiting_shipment_generation_initialization`, `awaiting_shipment_generation_order`, `awaiting_shipment_generation_hold_release`, `awaiting_shipment_generation_hold_release_incorrect_address`

**Inventory states**
`inventory_fulfillment_center_inventory_enough_quantity`, `inventory_fulfillment_center_inventory_insufficient_quantity`, `inventory_merchant_inventory_enough_quantity`, `inventory_merchant_inventory_insufficient_quantity`, `inventory_merchant_inventory_pending`

**Errors / unknown**
`errors_miscellaneous_errors`, `errors_incorrect_shipping_address`, `unknown`

> ⚠ This is **richer than the ~17 values** historically seen in production. New/rarer ones (`personal_pickup`, `lost_in_transit`, `lost_in_warehouse`, `shipment_refunded`, `shipping_canceled`, `fulfillment_cancelled`, `sending`, `in_picking`, the `awaiting_*` family) must each map to a deliverability/P&L category — never let them fall through to a silent "other" without intent.

### 3.2 `shipment_status.key` (`OrderShipmentStatusOption`) — raw courier-level
`not_generated`, `generating_awb`, `generated_awb`, `not_created`, `creating_awb`, `created_awb`, `canceled`, `refused`, `personal_pickup`, `customer_pickup`, `out_for_delivery`, `in_parcel_locker`, `redirected`, `in_transit`, `incorrect_address`, `unsuccessful_delivery`, `delivered`, `returning_to_sender`, `received_by_sender`, `deferred_delivery`, `shipment_refunded`

### 3.3 `fulfillment_status.key` (`OrderFulfillmentStatusOption`)
`pending_fulfillment`, `sending`, `ready_for_picking`, `in_picking`, `waiting_for_courier`, `out_of_stock`, `cancelled`, `back_to_sender`, `fulfilled`, `not_fulfilled`, `returned`, `partial`, `on_hold`

### 3.4 `financial_status` (`OrderFinancialStatus`)
`pending`, `paid`, `authorized`, `partially_paid`, `partially_refunded`, `refunded`, `voided`. (Once paid, Frisbo does not change it.)

### 3.5 `payout_status` (`OrderPayoutStatusOption`)
`not_applicable`, `not_collected`, `collected`, `at_frisbo`, `at_merchant`. (COD cash reconciliation.)

### 3.6 `state` (`OrderState`) / `stage` (`OrderStage`)
state: `active`, `draft`, `closed`, `cancelled`, `deleted`, `frozen`. stage: `demo`, `live`.

### 3.7 `ShipmentEventKey` (per-parcel tracking events)
`generated_awb`, `canceled`, `error`, `redirected`, `in_transit`, `returning_to_sender`, `incorrect_address`, `partial_delivery`, `personal_pickup`, `customer_pickup`, `out_for_delivery`, `in_parcel_locker`, `refused`, `unsuccessful_delivery`, `delivered`, `received_by_sender`, `deferred_delivery`, `shipment_refunded`

### 3.8 Recommended deliverability buckets (for `aggregated_status`)
Mirrors the deliverability formula `shipped = delivered + in_transit + out_for_delivery + returned + refused` (cancelled & not-shipped excluded):

| Bucket | aggregated_status keys |
|---|---|
| **delivered** | delivered, customer_pickup, personal_pickup, in_parcel_locker |
| **in_transit** | in_transit, out_for_delivery, redirected, deferred_delivery, fulfilled, sending |
| **returned / refused** | refused, unsuccessful_delivery, returning_to_sender, received_by_sender, back_to_sender, incorrect_address, returned, lost, lost_in_transit, lost_in_warehouse, shipment_refunded |
| **cancelled** | cancelled, shipping_canceled, fulfillment_cancelled |
| **not_shipped (excluded from `shipped`)** | not_generated, generating_awb, generated_awb, waiting_for_courier, waiting_for_pickup, ready_for_picking, in_picking, pending_fulfillment, not_fulfilled, partial, on_hold, out_of_stock, all `awaiting_*`/`initializing_*`, all `inventory_*` |
| **other/errors** | unknown, errors_* |

---

## 4. Couriers

**`GeneratingCouriers`** (Frisbo can generate the AWB): `urgent_cargus`, `dpd_ro`, `dhl_de`, `inpost_pl`, `econt_bg`, `nemo_express_ro`, `gls_ro`, `gls_de`, `sameday_ro`, `sameday_hu`, `fan_courier_ro`, `fan_courier_md`, `shipvam_gr`, `curier_rapid_md`.

**`TrackingOnlyCouriers`** (tracking only, ~65): acs_gr, brt_it, cargus_ro, correos_es, correos_express, colis_prive_fr, courier_manager, ctt_pt, deutsche_post_de, dhl, direct_link_mmp, dpd_cz, dpd_de, dpd_fr, dpd_hr, dpd_hu, dpd_it, dpd_pl, dpd_ro, dpd_uk, dpd_sk, econt_bg, evri_uk, exelot, fan_courier_md, fan_courier_ro, fedex_us, fox_post_hu, geis_cz, gls_at, gls_cz, gls_de, gls_hr, gls_hu, gls_pl, gls_ro, gls_es, gls_si, gls_sk, inpost_pl, kurier123_sk, la_poste_fr, mrw_es, nemo_express_ro, paack, packeta, parcel_force_uk, post_at, posta_cz, postis_ro, poste_it, post_nl, postnord, ppl_cz, raben, royal_mail_uk, sameday_hu, sameday_ro, sps_sk, team_courier_ro, tnt, tipsa_es, ups, xp_courier_gr.

---

## 5. Shipments & labels

`Shipment` (from `/shipment`, `/shipments`, `/print_shipment`):
- `uid`, `courier_id`, `created_at`, `tracking_only`, `retailer_uid`
- `identifiers[]` `{key, value}` — e.g. `tracking_number`, `shipment_uid`
- `documents[]` `{external_id, labels[] {format, download_url}, is_return, is_redirect, created_at}` — **AWB PDF label URLs**
- `events` `{latest_event: ShipmentEvent, processed: [ShipmentEvent]}` — tracking history. `ShipmentEvent {date, id, key (ShipmentEventKey), returning, redirected, reason_status}`
- `details` `{address_from, address_to, parcels[] {weight,width,height,length,content,...}, payment {paid_by, cash_on_delivery, cash_on_delivery_value, declared_value, insured_value, repayment_method}, options, pickup}`

> Label PDFs are at `documents[].labels[].download_url` (often S3 presigned — do **not** send the Frisbo `Authorization` header to non-Frisbo hosts or they 403).

`POST /orders/order/{uid}/regenerate_shipment` body: `{order_uid, parcel_count}` → returns `true`. Generates a brand-new courier label with `parcel_count` parcels.

---

## 6. Inventory items (products)

`InventoryItem` (from `/inventory_items/search`):
- `uid`, `organization_uid`, `title_1`/`title_2`/`title_3`, `state` (active/draft/archived/deleted/replaced), `created_at`/`updated_at`/`imported_at`
- `codes[]` `{key: barcode|sku|hs, value}` — **SKU, barcode, HS code live here**
- `dimensions` `{weight(g), height/width/length(mm)}`
- `requires_shipping`, `quantity_tracked`, `selling_policy` (deny/continue_), `managed_by` (frisbo/others)
- `images[]` `{src, position}`
- `computed_inventory_levels[]` `{owner (frisbo/other), available, incoming, committed, returned, defects}`
- `aggregated_inventory_levels` `{all, other, frisbo}` each `{available, sales, has_sales, has_available, days_left, committed}`
- `selling_channels_store_uids[]`, `selling_channels_inventory_items_unique_keys[]`

---

## 7. Sync best practices (what makes sync robust / non-stale)

1. **Filter by `updated_at_start`, not `created_at`.** Status changes (delivered, returned, …) update `updated_at` but not `created_at`. A `created_at`-windowed sync **misses status changes on older orders** → stale statuses. This is the #1 cause of stale orders.
2. **Overlap the window.** Start each incremental sync slightly before the *previous run's start time* (e.g. −15 min), not its end time, to avoid a gap equal to the run duration.
3. **Tiered cadence** so nothing is ever far out of date: a short incremental (minutes, `updated_at >= last_start−15m`) + periodic wider absolute sweeps (e.g. last 7d / 30d / 90d) that re-touch older orders whose courier status is still moving.
4. **Page to exhaustion.** `limit=100` (server cap), `skip += 100` until a page returns `< 100`. Cross-check against the response **`count`** — if you fetched fewer than `count`, you stopped early.
5. **Use `statuses_statistics`** from a `search` call to know the authoritative per-status totals for a filter *without* paging — ideal for verifying a sync/report matches Frisbo.
6. **Do store filtering Python-side.** The `store_uids[]` filter is buggy (returns ~0 for many uids). Fetch org-wide, bucket by `store_uid` locally.
7. **Upsert by `uid`.** Coalesce nulls (a partial payload must not wipe good fields). Use per-order savepoints so one bad order doesn't abort the batch; commit in batches.
8. **For fields search omits** (`metafields.number_of_parcels`, free-text `note`, `comments`/`messages`), do a targeted `GET /orders/order/{uid}` only when needed.

---

## 8. Mapping to AWB Print Manager

| Frisbo field | AWB `Order` column |
|---|---|
| `uid` | `uid` |
| `store_uid` | `store_uid` |
| `aggregated_status.key` | `aggregated_status` |
| `shipment_status.key` | `shipment_status` |
| `fulfillment_status.key` | `fulfillment_status` |
| `financial_status` | `financial_status` |
| `payment.gateway_names[0]` | `payment_gateway` |
| `prices.total_price` / `subtotal_price` / `total_discounts` | `total_price` / `subtotal_price` / `total_discounts` |
| `payment.currency` | `currency` |
| `created_at` | `frisbo_created_at` |
| `line_items` | `line_items` (JSON) |
| `shipping_address` | `shipping_address` (JSON) |
| `aggregated_courier` / `shipments[].documents[].labels[]` | `courier_name`, `tracking_number`, `awb_pdf_url`, `OrderAwb` rows |
| **`tags.selling_channel[]`** | *(new)* → add `Order.tags`; enables test-order exclusion (`key=="test"`) to match Scripturi |
| **`notes.selling_channel` / `note`** | *(new)* → add `Order.note` |
| **`metafields` number_of_parcels** | maps to `package_count` (needs single-order GET) |
| `statuses_history.raw_shipment_statuses` | *(new)* lets AWB cross-check raw→aggregated like Scripturi |

The AWB Frisbo client + parser live in `backend/app/services/frisbo/` (client.py, parser.py) and `sync_service.py`. The legacy `frisbo_response.json` sample predates the tags/notes update — trust `openapi.json` here, not that file.
