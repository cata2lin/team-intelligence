---
name: shopify-stores
description: How to programmatically access ANY of the team's Shopify stores (Esteban, GT, Nubra, Grandia, Bonhaus, Rossi, … ~21 shops) for both reads and mutations via the Admin GraphQL API — resolve the right shop domain + access token (including the OAuth token-rotation stores where the static token is dead by design), run queries and mutations with rate-limit-safe backoff, read/write theme assets, and do it without ever leaking a token. Includes a full mutation cookbook (reference/mutations.md)- products, variants & prices, inventory, media, collections, tags & metafields, order update/cancel/edit, refunds, fulfillment, draft orders, discounts (BXGY 2+1, codes), publications, blog articles, webhooks, bulk operations. Use whenever a script/task needs to talk to a Shopify store's Admin API, you hit a 401 / "Invalid API key or access token", you need a fresh token for an OAuth store (e.g. Nubra), or you need ANY mutation (change price/stock, add/remove order items, refund, create draft order, create discount, publish/unpublish, post a blog article, bulk export).
---

# shopify-stores

> Author: **Gigi**. Shared with the whole team via the `gigi` plugin.

Everything you need to **reach a team Shopify store from code** — pick the shop,
get a *working* token, run reads + mutations, and survive the rate limiter. Encodes
the traps that cost real time (the dead static tokens, the OAuth-rotation stores,
the asset-API throttle).

## Golden rules (read first)

1. **Tokens are secrets. Never print one** into chat, logs, git, or a skill. Pipe
   the value straight into the process (`kb.py secret-get … | python -`). When you
   must show one, show only `…{last4}`.
2. **Don't trust the static token blindly.** Most stores use a static custom-app
   token (`shpat_*`) in `stores.csv`, **but some stores rotate via OAuth** and
   their static token is **dead on purpose**. A `401 "Invalid API key or access
   token"` almost always means "this store is an OAuth store — go get the live
   token" (see §3), *not* "the store is down".
3. **`core/stores.py::get_store(prefix)` is the single source of truth.** It reads
   `stores.csv` *and* transparently swaps in a fresh OAuth token for rotation
   stores. Prefer it over re-reading the CSV yourself.
4. **Always read `userErrors` on a mutation.** A `200 OK` with a non-empty
   `userErrors` array means the mutation did **nothing**. No exception is raised.
5. **Respect the rate limiter** (§6). The Admin GraphQL bucket and (especially) the
   REST **Asset API** (~2 req/s) will 429 you fast if you fan out.

## 1. The store registry — prefix → shop → public domain

Each store is keyed by a short **prefix**. The mapping lives in `stores.csv`
(columns: `prefix,shop,token`). Canonical, freshest copy = KB secret
`SHOPIFY_STORES_CSV` (pull with `kb.py secret-get SHOPIFY_STORES_CSV` — contains
all tokens, so **pipe it, don't print it**).

Perfume stores (the ones that run the 2+1 + surprise-perfume offer):

| Prefix | myshopify | Public domain | Token type |
|--------|-----------|---------------|-----------|
| `EST`  | `6f9e22-9d.myshopify.com` | esteban.ro | static `shpat_*` |
| `GT`   | `ix5bxc-hr.myshopify.com` | george-talent.ro | static `shpat_*` |
| `NUB`  | `bmuwvv-jy.myshopify.com` | nubra.ro | **OAuth rotation** (see §3) |

Other prefixes (GRAND, BON, BONBG, CZ, PL, ROSSI, GEN, CARP, COV, MAG, OFER, RED,
LUX, NOC, BG, APR, PAT, BELA …) are in `stores.csv`. Brand-name ↔ prefix map is in
`core/brands.py` (`BRAND_TO_PREFIX`). Note the alias `GRAND`(Shopify) → `GRAN`(csv).

```python
from core.stores import get_store, list_stores
s = get_store("GT")          # {'prefix':'GT','shop':'ix5bxc-hr.myshopify.com','token':'shpat_…'}
all_stores = list_stores()   # [{prefix, shop, token}, ...] — OAuth tokens already resolved
```

## 2. Calling the Admin API

- **Version:** `2026-01` (latest). Endpoint:
  `https://{shop}/admin/api/2026-01/graphql.json`
- **Headers:** `X-Shopify-Access-Token: {token}`, `Content-Type: application/json`
- REST still exists for a few things (themes/assets, script_tags): same host,
  `/admin/api/2026-01/<resource>.json`.

```python
import requests
def gql(shop, token, query, variables=None):
    r = requests.post(f"https://{shop}/admin/api/2026-01/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=30)
    return r.json()
```

Find an order by name / a product by handle:

```graphql
{ orders(first:1, query:"name:GT43709"){ edges{ node{ id name tags
    lineItems(first:50){ edges{ node{ title sku quantity
      variant{ id product{ handle } }
      discountedUnitPriceSet{ shopMoney{ amount } } } } } } } } }

{ productByHandle(handle:"parfum-surpriza"){ id status onlineStoreUrl
    variants(first:10){ edges{ node{ sku price inventoryPolicy } } } } }
```

## 3. OAuth token-rotation stores (the #1 gotcha) — currently **Nubra**

Some stores authenticate through the **ARONA app** with rotating OAuth tokens
instead of a static custom-app token. For these:

- The token in `stores.csv` / `SHOPIFY_STORES_CSV` is **stale by design** → any
  direct call 401s. (Nubra's dead one ends `…40ed`.)
- The **live** access/refresh tokens live in a SQLite DB **`data/shopify_tokens.db`**
  (table `shopify_tokens`), managed by `core/shopify_token_manager.py`. The static
  token is auto-synced back to `stores.csv` on refresh.
- That DB lives **on the production dashboard server**, *not* on your laptop. Your
  local `data/shopify_tokens.db` is empty. → run token-dependent code **on the
  server** (the Scripturi VPS, app at `/root/Scripturi`, `.venv`; host is in
  `vps_deployment_guide.md` / KB). The token never leaves the box:

```bash
ssh root@<scripturi-vps> 'cd /root/Scripturi && .venv/bin/python -' <<'PY'
from core.stores import get_store
s = get_store("NUB")          # resolves the live OAuth token via the token manager
shop, token = s["shop"], s["token"]
print("shop", shop, "token …", token[-4:])   # never print the whole token
# ... do your gql() / mutation here ...
PY
```

- `get_valid_token(prefix)` auto-refreshes when within 5 min of expiry; `get_store`
  falls back to the last stored access token if the refresh call fails.
- **Known live issue (Jun 2026):** Nubra's *refresh* returns `401` on
  `/admin/oauth/access_token` (refresh token / client creds stale). The **stored
  access token still works**, but won't renew forever — Nubra's ARONA app needs a
  fresh OAuth re-auth (`exchange_code`) at some point. If Nubra suddenly 401s
  everywhere, that's why.

## 4. Mutations — the patterns you'll actually use

Every mutation: send the mutation + variables, then **check `userErrors`**.

> **Full cookbook → [`reference/mutations.md`](reference/mutations.md)** — 18
> copy-paste sections: products (create/update/`productSet` upsert/duplicate),
> variants & pricing (`productVariantsBulkUpdate/Create/Delete`), inventory
> (`inventorySetQuantities` with compare-and-set, `inventoryAdjustQuantities`),
> media & files (attach image by URL), collections (manual + smart rules),
> tags & metafields, order update/cancel/markAsPaid, **order editing**
> (begin→add→commit, the idempotency trick), refunds (`suggestedRefund` first),
> fulfillment (fulfillment orders + Frisbo warning), draft orders (UGC pattern,
> `paymentPending:true` for COD), discounts (the BXGY 2+1, code discounts),
> publications, blog articles, webhooks, bulk operations, theme-asset writes
> (REST PUT), and a gotcha index. Below: the three used most.

**Sales-channel publication** (publish / unpublish a product per channel). Used to
make a product **non-purchasable on the storefront** while keeping it usable by
back-office order editing / Flow:

```graphql
# 1) discover publication IDs + which are on:
{ productByHandle(handle:"parfum-surpriza"){ id
    resourcePublications(first:25){ edges{ node{ isPublished publication{ id name } } } } } }

# 2) remove from the cart-capable storefronts (Online Store + Shop):
mutation($id:ID!,$input:[PublicationInput!]!){
  publishableUnpublish(id:$id, input:$input){
    publishable{ ... on Product{ handle availablePublicationsCount{ count } } }
    userErrors{ field message } } }
# variables: { "id":"gid://shopify/Product/…",
#   "input":[{"publicationId":"gid://shopify/Publication/<OnlineStore>"},
#            {"publicationId":"gid://shopify/Publication/<Shop>"}] }
```
`publishablePublish` is the exact inverse (fully reversible). Verify the result on
the **storefront**, not just the API: `GET https://<domain>/products/<handle>.js`
should return **HTTP 404** once it's off the Online Store.

**Tags:** `tagsAdd(id, tags:[…])` / `tagsRemove`. For bulk tagging across many
orders with proper throttling, reuse `shopify_tag_orders_parallel.py` (workers +
429/`throttleStatus` backoff + GraphQL `tagsAdd`).

**Order editing** (add / change / remove line items after an order exists):
`orderEditBegin` → `orderEditAddVariant` / `orderEditSetQuantity` /
`orderEditAddCustomItem` → `orderEditCommit`. **Key facts:** (a) order editing adds
a variant **regardless of its sales-channel publication or stock** — so a product
can be hidden/unpublished from the storefront and still be added to orders
server-side (this is exactly how the surprise-perfume Flow keeps working after you
unpublish it from the Online Store); (b) `orderEditAddVariant` has
`allowDuplicates:false` by default — a **free idempotency guard** that errors
instead of stacking a variant already on the order. Full step-by-step in
`reference/mutations.md` §8.

**Product / variant updates:** `productUpdate`, `productVariantsBulkUpdate`,
`publishablePublish`. ⚠ `seo:{}` on `productUpdate` **replaces, not merges** — see
the `shopify-seo` skill. ⚠ `productUpdate.tags` also replaces — use `tagsAdd`.

## 5. Reading the theme (when you need to find storefront logic)

```python
# themes (REST): find role == "main"
GET /admin/api/2026-01/themes.json
# list asset keys, then fetch one asset at a time:
GET /admin/api/2026-01/themes/{theme_id}/assets.json                 # all keys
GET /admin/api/2026-01/themes/{theme_id}/assets.json?asset[key]=assets/cart-drawer.js
```
Grep all `.js/.liquid/.json` asset bodies for your keyword. Lesson learned: a
storefront cart that "auto-adds a gift" often does **not** live in the theme at all
— if grep comes up empty, the logic is server-side (a Shopify **Flow**, an app, or
the COD form). Flows are **not** readable or editable via API — only in
admin → Apps → Flow.

### noindex a single page (keep it off Google)
Shopify has **no per-page noindex toggle** in admin. The only API way is a
conditional `<meta robots>` in `layout/theme.liquid`. Use **`scripts/noindex_page.py`**:
```bash
uv run noindex_page.py --prefix BON --path /policies/contact-information          # dry-run
uv run noindex_page.py --prefix BON --path /policies/contact-information --apply   # add
uv run noindex_page.py --prefix BON --path /policies/contact-information --remove --apply
```
It injects `{%- if request.path contains '<PATH>' -%}<meta name="robots" content="noindex, nofollow">{%- endif -%}`
after `<head>` (idempotent, backs the file up). Works for the auto-generated
**contact policy page** `/policies/contact-information` (the ARONA-SRL page) too —
that page renders through the theme like any storefront page.
- **`noindex` ≠ `nofollow` ≠ robots.txt `Disallow`.** Only `noindex` removes a page
  from search results. `nofollow` just stops link-following; robots.txt `Disallow`
  can *prevent* deindexing (Google can't crawl the page to see the noindex). So keep
  the page **crawlable** and rely on `noindex`.
- Deindex is not instant — it drops on the next crawl (days–weeks). For speed, file a
  temporary removal in Google Search Console (team has GSC via `gigi:analytics`).

## 6. Rate limits (don't get 429'd)

- **GraphQL**: cost-based leaky bucket. Read `extensions.cost.throttleStatus`
  (`currentlyAvailable`, `restoreRate`); sleep when low. On HTTP 429 honour
  `Retry-After`.
- **REST Asset API ≈ 2 req/s.** Lesson learned this session: fetching ~400 theme
  assets at 12 concurrent workers got mass-429'd and silently dropped most files.
  At ~3 req/s **with retry/backoff** all 369 came back. Go slow + retry on 429.
- A ready-made, battle-tested limiter (RateLimiter, adaptive REST sleep, GraphQL
  throttle sleep) is in `shopify_tag_orders_parallel.py` — copy it.

## 7. Worked example — block manual storefront adds of a $0 gift (EST/GT/NUB)

Real task from this skill's origin: a $0 "Parfum surpriză" gift product was
`available:true` on the Online Store, so it could be added to the cart manually
(on top of the Flow that adds it server-side after checkout). Fix, per store:

1. `get_store(prefix)` → shop+token (run on the **server** for `NUB`).
2. Find the `$0` surprise product (`title:*surpriz* OR sku:surpriza*`), grab its
   `Online Store` + `Shop` publication IDs.
3. `publishableUnpublish` those two publications (keep POS/feeds; keep `status:
   ACTIVE` so the Flow can still add it via order editing).
4. Verify: `GET https://<domain>/products/<handle>.js` → **404**.

Result: customers can no longer add it; the post-checkout Flow is unaffected.
Reversible any time with `publishablePublish`.

## Quick reference

| Need | Do |
|------|----|
| A store's shop+token | `core.stores.get_store("<PFX>")` |
| Fresh token for an OAuth store (NUB) | run on the **server** via `get_store` |
| 401 "Invalid API key" | it's an OAuth-rotation store → §3 |
| Run a query/mutation | `scripts/shopify_gql.py --prefix GT --query '…'` |
| Make product non-addable | `publishableUnpublish` Online Store + Shop, verify `.js` = 404 |
| Bulk tag orders | reuse `shopify_tag_orders_parallel.py` |
| Edit an order's lines | `orderEditBegin`→…→`orderEditCommit` (ignores publication/stock) |
| Any other mutation (prices, stock, refunds, drafts, discounts, blog, webhooks, bulk) | `reference/mutations.md` — copy-paste cookbook |
| 429s | slow to ~2–3 req/s, retry, read `throttleStatus` |
| Read/search/**write** theme asset files | `scripts/shopify_theme.py` |
| Import a `.docx` (+ photos) as a blog article | `scripts/publish_blog.py` |

## Theme assets & blog import

**`scripts/shopify_theme.py`** — read / search / **write** theme asset files (companion to `shopify_gql.py`, reuses its store/token resolution; adds the write verbs it lacks). All cmds need `--prefix <STORE>` + `--theme <ID>` except `themes`:
```bash
uv run scripts/shopify_theme.py themes --prefix GRAN                       # list themes → main/live id + copies
uv run scripts/shopify_theme.py get snippets/meta-tags.liquid --prefix GRAN --theme <ID>
uv run scripts/shopify_theme.py grep "BreadcrumbList" --prefix GRAN --theme <ID>
uv run scripts/shopify_theme.py put sections/foo.liquid --file /tmp/foo.liquid --prefix GRAN --theme <ID>   # WRITES
```
**SAFETY:** editing the **main** theme edits the LIVE storefront. Duplicate the live theme (admin → Online Store → Themes → Duplicate), edit the COPY, preview with `?preview_theme_id=<ID>`, publish only on explicit confirmation (`PUT themes/<id>.json {"theme":{"id":<id>,"role":"main"}}`; revert by republishing the old one). **Curl on a storefront URL hits Shopify's bot/edge cache → unreliable for QA**; after a theme edit the public URL can serve stale HTML for minutes — verify in a real logged-out browser or via `?preview_theme_id` (preview bypasses the page cache).

**`scripts/publish_blog.py`** — publish an **existing** `.docx` (+ photo `.zip`) as a blog article on any store: uploads images to the Shopify CDN, inlines them per H2, sets SEO meta + an ASCII handle. Defaults to **DRAFT**; `--publish` for live.
```bash
uv run scripts/publish_blog.py --prefix GRAN --blog news --docx a.docx --zip photos.zip --tags "a,b" [--publish]
```
> To *generate* brand-voice articles use `core:<store>-articles`. publish_blog.py *imports* externally-written deliverables (e.g. an SEO agency's .docx) verbatim — a different job.

**Liquid / Shopify traps that cost real time:**
- `{% stylesheet %}`/`{% javascript %}` work only in **sections**, not snippets → use a plain `<style>` in a snippet.
- Can't filter inside a bracket lookup: `collections['brand-' | append: h]` errors → assign the handle to a var first, then `collections[var]`.
- Metaobjects: type/handle via `metaobject.system.type` / `.system.handle` (**not** `metaobject.type`); fields by key `metaobject.<key>`; enumerate a type with `shop.metaobjects.<type>.values` (needs storefront access on the definition).
- The theme's `.collection-wrapper` product grid is sized off `--page-width` (full page) → **0-height cards** inside a narrow column; for custom layouts render your **own** simple grid (`repeat(auto-fill,minmax(...))`).
- Section `schema.name` ≤ **25 chars** (count bytes — avoid diacritics/`·`); range settings enforce `step` (e.g. an int-step scale rejects `1.25`).
- New article/asset handles keep diacritics → handleize to ASCII (ă→a, ș→s, ț→t, î/â→i/a) for clean URLs.
- Inline body images must live on the CDN: `stagedUploadsCreate` → multipart POST to the target → `fileCreate(contentType:IMAGE)` → poll `node…image.url`. Requesting `image_url: width: N` **can't upscale** past the source resolution (small source = blurry when displayed large).
- Smart collections do **not** auto-reindex after a bulk `metafieldsSet` → re-apply the ruleSet (`collectionUpdate` same rule) to force re-evaluation.
