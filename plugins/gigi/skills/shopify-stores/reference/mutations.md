# Shopify Admin GraphQL — mutation cookbook (API 2026-01)

Copy-paste mutations for the team stores. Run any of them with the helper:

```bash
python3 scripts/shopify_gql.py --prefix GT --query '<mutation>' --vars '<json>'
```

**Universal rule:** a `200 OK` response with a non-empty `userErrors` array means
the mutation did **nothing**. Always check it. All IDs are GIDs
(`gid://shopify/Product/123…`).

---

## 1. Products

**Update core fields** (title, descriptionHtml, status, vendor, seo, handle):

```graphql
mutation($input: ProductUpdateInput!){
  productUpdate(product: $input){
    product{ id title status } userErrors{ field message } } }
```
```json
{"input":{"id":"gid://shopify/Product/…","title":"Nou","status":"ACTIVE",
  "seo":{"title":"Meta title","description":"Meta desc"}}}
```
⚠ `seo:{}` **replaces** (send BOTH title+description always). ⚠ `tags` here
**replaces ALL tags** — for add/remove use `tagsAdd`/`tagsRemove` (§6).

**Create** (options first, variants separately via §2):

```graphql
mutation($product: ProductCreateInput!){
  productCreate(product: $product){
    product{ id handle variants(first:5){ nodes{ id } } } userErrors{ field message } } }
```
```json
{"product":{"title":"Parfum X","status":"DRAFT","vendor":"GT",
  "productOptions":[{"name":"Mărime","values":[{"name":"50ml"},{"name":"100ml"}]}]}}
```

**Upsert everything in one call** (sync pipelines — product + options + variants
+ prices, idempotent by handle):

```graphql
mutation($input: ProductSetInput!){
  productSet(input: $input){ product{ id } userErrors{ field message } } }
```

**Duplicate / delete / archive:**

```graphql
mutation($productId: ID!, $newTitle: String!){
  productDuplicate(productId:$productId, newTitle:$newTitle, includeImages:true){
    newProduct{ id } userErrors{ field message } } }

mutation($input: ProductDeleteInput!){
  productDelete(input:$input){ deletedProductId userErrors{ field message } } }
```
Archive = `productUpdate` with `"status":"ARCHIVED"` (safer than delete).

---

## 2. Variants & pricing

**Bulk update price / compareAt / barcode / SKU on existing variants:**

```graphql
mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!){
  productVariantsBulkUpdate(productId:$productId, variants:$variants){
    productVariants{ id price } userErrors{ field message } } }
```
```json
{"productId":"gid://shopify/Product/…","variants":[
  {"id":"gid://shopify/ProductVariant/…","price":"65.00","compareAtPrice":"99.00"},
  {"id":"gid://shopify/ProductVariant/…","inventoryItem":{"sku":"gt-99","tracked":true}}]}
```

**Create / delete variants:**

```graphql
mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!){
  productVariantsBulkCreate(productId:$productId, variants:$variants){
    productVariants{ id sku } userErrors{ field message } } }

mutation($productId: ID!, $variantsIds: [ID!]!){
  productVariantsBulkDelete(productId:$productId, variantsIds:$variantsIds){
    userErrors{ field message } } }
```
Variant create takes `optionValues:[{optionName,name}]`, `price`,
`inventoryItem:{sku,tracked}`, `inventoryQuantities:[{locationId,name:"available",quantity}]`.

**Inventory policy** (allow/deny overselling): `productVariantsBulkUpdate` with
`"inventoryPolicy":"DENY"` or `"CONTINUE"`.

---

## 3. Inventory

Get IDs first: variant → `inventoryItem{ id }`; locations →
`{ locations(first:10){ nodes{ id name } } }`.

**Set absolute quantity** (compare-and-set safe):

```graphql
mutation($input: InventorySetQuantitiesInput!){
  inventorySetQuantities(input:$input){
    inventoryAdjustmentGroup{ reason } userErrors{ field message } } }
```
```json
{"input":{"name":"available","reason":"correction","ignoreCompareQuantity":true,
  "quantities":[{"inventoryItemId":"gid://shopify/InventoryItem/…",
                 "locationId":"gid://shopify/Location/…","quantity":762}]}}
```
For race-safe sync set `ignoreCompareQuantity:false` + pass `compareQuantity`
(fails with userError if stock moved since you read it — re-read and retry).

**Adjust by delta:**

```graphql
mutation($input: InventoryAdjustQuantitiesInput!){
  inventoryAdjustQuantities(input:$input){ userErrors{ field message } } }
```
```json
{"input":{"name":"available","reason":"correction",
  "changes":[{"inventoryItemId":"…","locationId":"…","delta":-3}]}}
```

**Track / untrack an item:** `inventoryItemUpdate(id, input:{tracked:true})`.

---

## 4. Media & files

**Attach image to product by URL** (Shopify downloads it — easiest path):

```graphql
mutation($productId: ID!, $media: [CreateMediaInput!]!){
  productCreateMedia(productId:$productId, media:$media){
    media{ ... on MediaImage { id image{ url } } }
    mediaUserErrors{ field message } } }
```
```json
{"productId":"…","media":[{"originalSource":"https://…/img.jpg",
  "alt":"Parfum GT N°2","mediaContentType":"IMAGE"}]}
```

**Upload to Files (CDN)**: `fileCreate(files:[{originalSource:"https://…", alt}])`
— also accepts external URLs. For local files do `stagedUploadsCreate` → POST the
file to the returned URL → `fileCreate` with the staged `resourceUrl`.

**Image alt text** (SEO): `fileUpdate(files:[{id, alt}])` or set `alt` in
`productCreateMedia`.

---

## 5. Collections

```graphql
mutation($input: CollectionInput!){
  collectionCreate(input:$input){ collection{ id handle } userErrors{ field message } } }
```
Manual: `{"input":{"title":"Promoții","products":["gid://shopify/Product/…"]}}`.
Smart: `{"input":{"title":"GT bărbați","ruleSet":{"appliedDisjunctively":false,
"rules":[{"column":"TAG","relation":"EQUALS","condition":"barbati"}]}}}`.

Add/remove products on a manual collection (async):

```graphql
mutation($id: ID!, $productIds: [ID!]!){
  collectionAddProductsV2(id:$id, productIds:$productIds){
    job{ id } userErrors{ field message } } }
```
(`collectionRemoveProducts` is the inverse; `collectionUpdate` for title/seo/image.
Collections also need `publishablePublish` to show in a sales channel!)

---

## 6. Tags & metafields (work on Product, Order, Customer, DraftOrder, …)

**Add / remove tags — additive, the safe way:**

```graphql
mutation($id: ID!, $tags: [String!]!){
  tagsAdd(id:$id, tags:$tags){ userErrors{ field message } } }

mutation($id: ID!, $tags: [String!]!){
  tagsRemove(id:$id, tags:$tags){ userErrors{ field message } } }
```

**Metafields** (up to 25 per call, any owner type):

```graphql
mutation($metafields: [MetafieldsSetInput!]!){
  metafieldsSet(metafields:$metafields){
    metafields{ key } userErrors{ field message } } }
```
```json
{"metafields":[{"ownerId":"gid://shopify/Product/…","namespace":"custom",
  "key":"inspiratie","type":"single_line_text_field","value":"Tom Ford"}]}
```
`type` must match the metafield definition exactly
(`single_line_text_field`, `multi_line_text_field`, `number_integer`,
`boolean`, `json`, `list.single_line_text_field`, …).

---

## 7. Orders — update, cancel, close, mark paid

**Note / tags / custom attributes:**

```graphql
mutation($input: OrderInput!){
  orderUpdate(input:$input){ order{ id } userErrors{ field message } } }
```
```json
{"input":{"id":"gid://shopify/Order/…","note":"verificat",
  "customAttributes":[{"key":"sursa","value":"cs"}]}}
```
⚠ `tags` and `customAttributes` here **replace** — for tags prefer `tagsAdd`.

**Cancel** (refund/restock decisions are explicit):

```graphql
mutation($orderId: ID!, $reason: OrderCancelReason!, $refund: Boolean!, $restock: Boolean!){
  orderCancel(orderId:$orderId, reason:$reason, refund:$refund, restock:$restock,
              notifyCustomer:false){
    job{ id } orderCancelUserErrors{ field message } } }
```
Reasons: `CUSTOMER`, `DECLINED`, `FRAUD`, `INVENTORY`, `STAFF`, `OTHER`.

**Mark COD as paid:** `orderMarkAsPaid(input:{id})`.
**Close / reopen:** `orderClose(input:{id})` / `orderOpen(input:{id})`.

---

## 8. Order editing (add/remove items on an EXISTING order)

This is how the surprise-perfume Flow works — works even if the product is
unpublished or out of stock. Three steps, all on the **calculated order**:

```graphql
mutation($id: ID!){ orderEditBegin(id:$id){
  calculatedOrder{ id lineItems(first:50){ nodes{ id quantity title } } }
  userErrors{ field message } } }

mutation($id: ID!, $variantId: ID!, $quantity: Int!){
  orderEditAddVariant(id:$id, variantId:$variantId, quantity:$quantity,
                      allowDuplicates:false){
    calculatedLineItem{ id } userErrors{ field message } } }

mutation($id: ID!, $lineItemId: ID!, $quantity: Int!){
  orderEditSetQuantity(id:$id, lineItemId:$lineItemId, quantity:$quantity, restock:true){
    calculatedLineItem{ id quantity } userErrors{ field message } } }

mutation($id: ID!){ orderEditCommit(id:$id, notifyCustomer:false,
                                    staffNote:"fix surpriza dubla"){
  order{ id } userErrors{ field message } } }
```

- `id` = the **calculatedOrder** GID from `orderEditBegin`, NOT the order GID.
- `lineItemId` = the **CalculatedLineItem** GID (from begin/addVariant), not LineItem.
- **`allowDuplicates:false` = built-in idempotency**: adding a variant already on
  the order returns a userError instead of stacking it. Any gift-adding
  automation should keep it false (the qty>1 surprise bug = exactly this guard
  missing).
- Remove a line = `orderEditSetQuantity` to `0`. Discount a line =
  `orderEditAddLineItemDiscount(id, lineItemId, discount:{percentValue:100})`.
- Nothing changes until `orderEditCommit`.

---

## 9. Refunds

Calculate first (`refundCreate` is exact-amount, no guessing):

```graphql
query($id: ID!){ order(id:$id){ suggestedRefund(refundLineItems:[{lineItemId:"…",quantity:1}]){
  amountSet{ shopMoney{ amount } } suggestedTransactions{ gateway parentTransaction{ id } amountSet{ shopMoney{ amount } } } } } }

mutation($input: RefundInput!){
  refundCreate(input:$input){ refund{ id } userErrors{ field message } } }
```
```json
{"input":{"orderId":"…","notify":true,
  "refundLineItems":[{"lineItemId":"gid://shopify/LineItem/…","quantity":1,"restockType":"RETURN","locationId":"…"}],
  "transactions":[{"orderId":"…","gateway":"…","kind":"REFUND","amount":"65.00","parentId":"gid://shopify/OrderTransaction/…"}]}}
```
Refund without `transactions` = restock-only (no money moves). COD orders
usually have no capturable transaction — refund is bookkeeping only.

---

## 10. Fulfillment

Fulfillments hang off **fulfillment orders**, not the order:

```graphql
query($id: ID!){ order(id:$id){ fulfillmentOrders(first:5){ nodes{ id status
  assignedLocation{ name } lineItems(first:20){ nodes{ id remainingQuantity } } } } } }

mutation($fulfillment: FulfillmentInput!){
  fulfillmentCreate(fulfillment:$fulfillment){
    fulfillment{ id status } userErrors{ field message } } }
```
```json
{"fulfillment":{"lineItemsByFulfillmentOrder":[{"fulfillmentOrderId":"gid://shopify/FulfillmentOrder/…"}],
  "trackingInfo":{"number":"AWB123","company":"DPD"},"notifyCustomer":true}}
```
Update tracking later: `fulfillmentTrackingInfoUpdate(fulfillmentId, trackingInfoInput:{…})`.
⚠ Our stores fulfill via **Frisbo** — don't create manual fulfillments on
Frisbo-managed orders unless you intend to bypass it.

---

## 11. Draft orders (the UGC / manual-order pattern)

```graphql
mutation($input: DraftOrderInput!){
  draftOrderCreate(input:$input){ draftOrder{ id name } userErrors{ field message } } }
```
```json
{"input":{"email":"client@x.ro",
  "lineItems":[{"variantId":"gid://shopify/ProductVariant/…","quantity":3}],
  "appliedDiscount":{"value":100,"valueType":"PERCENTAGE","title":"UGC gift"},
  "shippingAddress":{"address1":"…","city":"…","countryCode":"RO","firstName":"…","lastName":"…","phone":"…"},
  "tags":["ugc"],"note":"comandă influencer"}}
```

Complete it (COD / no payment yet → `paymentPending:true`):

```graphql
mutation($id: ID!){
  draftOrderComplete(id:$id, paymentPending:true){
    draftOrder{ order{ id name } } userErrors{ field message } } }
```
⚠ `paymentPending:false` (default) marks the order **paid** — wrong for COD.
Draft orders accept `customAttributes` per line and custom (non-catalog) items:
`{"title":"Custom","originalUnitPrice":"10.00","quantity":1}`.

---

## 12. Discounts

**The 2+1 (Buy X Get Y) — what „Oferta 2+1 Gratis" is:**

```graphql
mutation($automaticBxgyDiscount: DiscountAutomaticBxgyInput!){
  discountAutomaticBxgyCreate(automaticBxgyDiscount:$automaticBxgyDiscount){
    automaticDiscountNode{ id } userErrors{ field message } } }
```
```json
{"automaticBxgyDiscount":{"title":"Oferta 2+1 Gratis","startsAt":"2026-06-01T00:00:00Z",
  "customerBuys":{"value":{"quantity":"2"},"items":{"collections":{"add":["gid://shopify/Collection/…"]}}},
  "customerGets":{"value":{"discountOnQuantity":{"quantity":"1","effect":{"percentage":1.0}}},
                  "items":{"collections":{"add":["gid://shopify/Collection/…"]}}},
  "usesPerOrderLimit":"10"}}
```

**Code discount:** `discountCodeBasicCreate(basicCodeDiscount:{title, code,
startsAt, customerSelection:{all:true}, customerGets:{value:{percentage:0.15},
items:{all:true}}, appliesOncePerCustomer:true})`.

**Pause / resume:** `discountAutomaticDeactivate(id)` / `discountAutomaticActivate(id)`.
Update: `discountAutomaticBxgyUpdate(id, automaticBxgyDiscount:{…})` (send only
changed fields — this one merges).

---

## 13. Sales-channel publication

See SKILL.md §4 — `publishablePublish` / `publishableUnpublish` with
`[{publicationId}]`. Discover IDs via `{ publications(first:25){ nodes{ id name } } }`.
Works on Products AND Collections. Verify storefront with `/products/<handle>.js`
(404 = off).

---

## 14. Blog articles

```graphql
query{ blogs(first:10){ nodes{ id title } } }

mutation($article: ArticleCreateInput!){
  articleCreate(article:$article){ article{ id handle } userErrors{ field message } } }
```
```json
{"article":{"blogId":"gid://shopify/Blog/…","title":"Top parfumuri vară",
  "body":"<p>…</p>","summary":"…","tags":["ghid"],"isPublished":true,
  "author":{"name":"GT"},"image":{"url":"https://…","altText":"…"}}}
```
`articleUpdate(id, article:{…})` to edit. SEO meta on articles = `metafieldsSet`
with namespace `global`, keys `title_tag` / `description_tag`.

---

## 15. Webhooks

```graphql
mutation($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!){
  webhookSubscriptionCreate(topic:$topic, webhookSubscription:$webhookSubscription){
    webhookSubscription{ id } userErrors{ field message } } }
```
`{"topic":"ORDERS_CREATE","webhookSubscription":{"callbackUrl":"https://…","format":"JSON"}}`
List: `{ webhookSubscriptions(first:50){ nodes{ id topic endpoint{ __typename } } } }`.
Delete: `webhookSubscriptionDelete(id)`. Verify deliveries with the `X-Shopify-Hmac-Sha256` header.

---

## 16. Bulk operations (read or mutate at scale)

**Bulk read** (no pagination, results as JSONL file):

```graphql
mutation{ bulkOperationRunQuery(query:"""
  { orders(query:"created_at:>=2026-06-01"){ edges{ node{ id name
      lineItems{ edges{ node{ sku quantity } } } } } } }
"""){ bulkOperation{ id status } userErrors{ field message } } }
```
Poll `{ currentBulkOperation{ status url errorCode } }` until `COMPLETED`,
download `url` (JSONL; child rows carry `__parentId`). **One bulk op per shop at
a time** — check before starting.

**Bulk mutate:** `bulkOperationRunMutation(mutation:"…", stagedUploadPath:"…")`
with a JSONL of variables uploaded via `stagedUploadsCreate`.

---

## 17. Theme assets (REST — the one thing GraphQL doesn't do)

```bash
# read
GET /admin/api/2026-01/themes/{theme_id}/assets.json?asset[key]=snippets/x.liquid
# write (creates or overwrites!)
PUT /admin/api/2026-01/themes/{theme_id}/assets.json
     {"asset":{"key":"snippets/x.liquid","value":"…"}}
# delete
DELETE /admin/api/2026-01/themes/{theme_id}/assets.json?asset[key]=snippets/x.liquid
```
⚠ PUT overwrites with no undo/history — fetch and save the current value first.
⚠ ~2 req/s on this API. Edit the **main** theme only deliberately (`role:"main"`).

---

## 18. Gotcha index

| Trap | Truth |
|------|-------|
| `productUpdate.tags` | replaces ALL tags → use `tagsAdd`/`tagsRemove` |
| `seo:{}` partial | wipes the field you omitted → always send both |
| `orderUpdate.customAttributes` | replaces the whole list |
| `draftOrderComplete` default | marks PAID → `paymentPending:true` for COD |
| `orderEditAddVariant` | `allowDuplicates:false` = free idempotency guard |
| `refundCreate` w/o transactions | restock-only, no money refunded |
| `metafieldsSet.type` mismatch | userError — match the definition exactly |
| Smart collection + `collectionAddProductsV2` | userError — only manual collections |
| New product invisible | you forgot `publishablePublish` + `status:ACTIVE` |
| Bulk op "already running" | one per shop — poll `currentBulkOperation` first |
| Asset PUT | no undo — back up the old value first |
| 200 OK but nothing changed | you didn't read `userErrors` |
