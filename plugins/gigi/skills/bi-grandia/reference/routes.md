# bi.grandia.ro — route reference (190 routes, zero server actions)

`bi.py routes` prints the short map. This adds body shapes + the auth reality.

## AUTH REALITY (important)
`middleware.ts` explicitly waves `/api/admin/*` through with **no session check**, so ~130 routes
have no auth in the handler. Only ~60 routes call `getSession()` — the ones marked 🔒 below (PO, RMA,
forecasts, users, dev-requests, team-tasks). Login = `POST /api/auth/login {email,password,rememberMe?}`
→ `grandia_session` cookie (180d w/ rememberMe). The CLI logs in so the 🔒 routes work.

## Purchase Orders (+ TOM) 🔒
- `GET/POST /api/admin/purchase-orders` POST `{locationId, items:[{variantId, quantityOrdered(≥1), unitCost(≥0)}], orderDate?, estimatedArrival?, notes?, title?, priority?}`
- `GET/PATCH /api/admin/purchase-orders/{id}` PATCH (a) `{action: approve|cancel|complete|preview-approve|preview-cancel}` — approve/cancel WRITE incoming inventory to Shopify; (b) field edit (no action) `{orderDate?,estimatedArrival?,locationId?,notes?,title?,priority?,items?}`
- `POST /api/admin/purchase-orders/auto-generate` (from restock needs)
- `POST /api/admin/purchase-orders/{id}/send-to-tom | amend-tom | refresh-from-tom`
- `GET/POST /api/admin/purchase-orders/receptions` POST `{locationId, items:[{variantId, quantityReceived(≥1)}], containerNumber?, receivedDate?, notes?}`
- `PATCH /api/admin/purchase-orders/receptions/{id}` `{action: complete|allocate|preview-complete}` (complete syncs on-hand to Shopify)
- Reads: `/by-product`, `/products`, `/locations`, `/stats`, `/sync-logs`

## Returns / RMA 🔒
- `POST /api/admin/returns/requests/{id}/approve` `{generateAwb?=true, credentialSet?="RO", serviceId, totalWeight, parcelsCount?=1, swapServiceId?, swapParcelsCount?}`
- `POST .../generate-awb` (same body, no generateAwb) · `POST .../awb/{awbId}/cancel`
- `POST .../actions` `{action: deliver|close}` · `POST .../cancel` `{reason}`
- `PATCH .../refund-amount` `{refundAmount≥0}` · `.../bank-details` `{iban,accountHolder}` · `.../pickup-address` · `.../invoice-number`
- `POST .../send-to-payment` · `.../mark-paid` `{amount>0, note?}` · `.../refund-shopify` (REAL Shopify refund)
- `GET/POST .../bulk-pay` · `GET/POST /api/admin/returns/settings` `{companyAddress}`

## Sync / jobs (🔓 no auth)
- `POST /api/admin/actions` `{action: bootstrap|incremental|snapshot|fulfillments}`
- `POST /api/admin/scheduler` `{action:"trigger", jobId}` — jobs: incremental-sync, reconciliation-sync, daily-inventory-snapshot, bootstrap-sync, ga4-daily-sync, ga4-refresh-views, gads-daily-sync, gads-refresh-views, fbads-daily-sync, fbads-refresh-views, reports-daily-pipeline, pricing-daily-pipeline
- `POST /api/admin/{fbads,gads,ga4}/sync` `{action:"sync-yesterday"|"sync-date"|"sync-range", date?/startDate?/endDate?}` · `/refresh-views` · `/config`

## Courier (DPD) 🔓
- `POST /api/admin/courier/awb` `{orderId, type:"RETURN"|"SWAP", serviceId, credentialSet?="RO", parcelsCount?, totalWeight?, codAmount?, ...overrides}` · `DELETE ?id=<shipmentDbId>` · `GET /services` (for serviceId)

## Pricing / Catalog-quality / Images (write to Shopify) 🔓
- `POST /api/admin/pricing/{productId}/apply` `{newPrice, userId, pricingAction?, reasonCode?}` · `POST /api/admin/pricing/run-pipeline`
- `.../pricing/products/{id}/competitors` GET/POST/PATCH/DELETE `{competitorName, competitorUrl, cssSelector?}`
- `POST /api/admin/catalog-quality/audit {productId}` · `/audit-bulk {productIds?|all?|onlyUnaudited?}` · `/improve {resultId, additionalInstructions?}` · `/push-improvements {productId,title?,descriptionHtml?}` (→Shopify) · `/generate-images {resultId,step}` · `/push-images {productId,images}` (→Shopify)
- `POST /api/admin/image-optimization/analyze|optimize|alt-text {productId|imageId}` (→Shopify)

## Forecasts / dev-requests / team-tasks / users / settings 🔒
- `GET/PATCH /api/admin/forecasts/config` `{leadTimeDays, daysOfStock, forecastingDays, orderingFrequency}` · `GET /api/admin/forecasts/restock`
- `GET/POST /api/admin/dev-requests` `{title,description,type,priority,contextPage}` · `PATCH {id} {status?,priority?}`
- `GET/POST /api/admin/team-tasks` `{title,description,priority,assigneeId,dueDate}` · `PATCH {id}`
- `GET/POST /api/admin/users` (admin) `{email,name,password,roleIds?}` · `PUT/DELETE {id}`
- `GET/POST /api/admin/settings/{ai,branding,smtp,tom,email-notifications}`

## Reads (🔓)
`/api/admin/products/reports · /overview · /funnel-overview · /search?q= · /trends · /status-changes`
`/api/admin/reports/{marketing-performance,category-roi,dead-stock,slow-movers,revenue-by-source-medium,inventory-turnover,...}`
`/api/admin/{fbads,gads,ga4}/{overview,metrics,raw,sync-runs,unmapped}`
