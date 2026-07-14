# metrics.arona.ro — full route reference (47 routes, zero server actions)

`metrics.py routes` prints the short map. This file adds request-body shapes. Every route below
is guarded by the `metrics_session` cookie unless noted. IDs on link/sync routes are **internal cuids**
(`MetaAdAccount.id` / `TikTokAdAccount.id` / `GoogleAdsCustomerAccount.id`), NOT platform ids.

## Auth
- `POST /api/auth/login` `{email, password, rememberMe?}` → sets `metrics_session` (30d w/ rememberMe). Public.
- `POST /api/auth/logout` · `GET /api/auth/session` → current user or 401.

## Brands
- `GET /api/brands` · `POST /api/brands` `{name, slug (^[a-z0-9-]+$), notes?}`
- `GET /api/brands/{id}` · `PATCH {name?,notes?,isActive?,isPaused?,color?,logoUrl?,categoryId?}` · `DELETE` (cascade)
- `POST /api/brands/{id}/sync` `{entities?:["shop","locations","products","orders","analytics"], bootstrap?, bootstrapDays?(1-365,def7), analyticsDays?}` — **async (Inngest), safe for backfills**
- `POST/DELETE /api/brands/{id}/connect-shopify` `{domain(*.myshopify.com), clientId, clientSecret, apiVersion?}`
- `GET/POST/PATCH/DELETE /api/brands/{id}/meta-accounts` — `{adAccountId, campaignFilter?}`
- `GET/POST/PATCH/DELETE /api/brands/{id}/tiktok-accounts` — `{adAccountId, campaignFilter?}`
- `GET/POST/PATCH/DELETE /api/brands/{id}/google-ads-accounts` — `{customerAccountId, campaignFilter?}` (POST auto-fires 30d backfill if account has 0 rows)
- `POST/DELETE /api/brands/{id}/logo` (multipart `file` ≤2MB)

## Categories / Stores
- `GET/POST /api/categories` `{name}`
- `GET/POST /api/stores` `{domain, clientId, clientSecret, apiVersion?, brandId}` · `GET/PATCH/DELETE /api/stores/{id}`

## Meta
- `GET/POST /api/meta/tokens` `{label, accessToken(≥20)}` (verifies + discovers ad accounts)
- `PATCH/DELETE /api/meta/tokens/{id}` `{action: verify|toggle-active|update-label|rediscover-accounts, label?}`
- `POST /api/meta/sync` `{adAccountId?, daysBack?(1-365)}` — **synchronous; big backfills time out**
- `GET /api/meta/oauth/url` · `POST /api/meta/oauth/exchange` `{code, redirectUri}`
- `POST/DELETE /api/meta/accounts/{id}/brands` `{brandId, campaignFilter?}`

## TikTok
- `GET/DELETE /api/tiktok/tokens` (delete via `?id=`)
- `POST /api/tiktok/oauth/exchange` `{authCode}` · `POST /api/tiktok/oauth/refresh` `{tokenId, rediscover?}`
- `POST /api/tiktok/discover-bcs` `{tokenId}` · `POST /api/tiktok/sync` `{adAccountId?, daysBack?}` — **synchronous**
- `POST/DELETE /api/tiktok/accounts/{id}/brands` `{brandId, campaignFilter?}`
- Invoices: `GET /api/tiktok/invoices` `?start=&end=&bcId=` · `/list-bcs` · `/download?bcId=&invoiceId=` · `POST /export-drive` `{monthLabel, items:[...]}` · `GET/PUT /settings`

## Google Ads
- `GET/PUT/DELETE /api/google-ads/connection` PUT `{label, developerToken, loginCustomerId, oauthClientId?, oauthClientSecret?, refreshToken?}`
- `POST /api/google-ads/oauth/exchange` `{code, redirectUri, connectionId?}`
- `POST /api/google-ads/discover` (MCC → upsert child accounts) · `GET /api/google-ads/accounts-list`
- `POST /api/google-ads/sync` `{customerAccountId?, daysBack?}` — **synchronous** · `POST /api/google-ads/test` (read-only)

## Integrations
- `POST /api/integrations/shopify/analytics/sync` `{brandId, daysBack?(1-90)}` (async)
- `GET /api/integrations/shopify/export` `?entity=&brandId=&q=` (CSV ≤10k rows; entity ∈ products|variants|locations|inventory-levels|inventory-snapshots|orders|line-items|analytics-daily|analytics-traffic)

## Admin (require `platform:admin`)
- `GET/POST /api/admin/users` `{email,name,password(≥8),roleIds?}` · `PUT/DELETE /api/admin/users/{id}`

## Cron jobs (Inngest, every ~10min) — trigger the underlying sync via the routes above
Shopify orders(hourly)/products/analytics(10m)/inventory-snapshot(2am)/reconciliation(6h);
Meta+Google+TikTok ads(10m); TikTok token refresh(12h); sweep-stale-sync-runs(1m).
An ad account with **no brand mapping syncs nothing** → the "spend reads 0" bug.
