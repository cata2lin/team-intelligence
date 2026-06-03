# Shopify Admin GraphQL API — Products, Variants, Options, Media

Reference for SyncApp. Covers the Product / ProductVariant / ProductOption / Media objects, every read query and mutation we use or might need, input types, error codes, pagination, search syntax, rate limits, and the SyncApp-specific implications.

Source of truth: shopify.dev/docs/api/admin-graphql/latest. API version pinned: **2026-04** (April26).

For inventory-level mutations (`inventorySetOnHandQuantities`, `inventorySetQuantities`, `inventoryAdjustQuantities`) and location queries see the sibling skill file `inventory-locations.md`. For webhooks see `webhooks.md`.

---

## Table of contents

1. [Mental model](#mental-model)
2. [Object: Product](#object-product)
3. [Object: ProductVariant](#object-productvariant)
4. [Object: ProductOption](#object-productoption)
5. [Object: ProductOptionValue](#object-productoptionvalue)
6. [Object: Media (interface) and MediaImage](#object-media-interface-and-mediaimage)
7. [Object: InventoryItem](#object-inventoryitem)
8. [Object: InventoryLevel](#object-inventorylevel)
9. [Object: Image](#object-image)
10. [Enums (status, policy, sort keys, error codes)](#enums)
11. [Queries](#queries)
12. [Mutations](#mutations)
13. [Input types reference](#input-types-reference)
14. [Search syntax (`query:` parameter)](#search-syntax-query-parameter)
15. [Pagination model](#pagination-model)
16. [Rate limits and query cost](#rate-limits-and-query-cost)
17. [`@idempotent` directive](#idempotent-directive)
18. [SyncApp angles](#syncapp-angles)

---

## Mental model

```
Shop
└── Product (gid://shopify/Product/N)
    ├── ProductOption (Color, Size — max 3 per product)
    │   └── ProductOptionValue (Red, Blue — max ~2048 distinct combos)
    ├── Media (MediaImage | Video | ExternalVideo | Model3d)
    └── ProductVariant (gid://shopify/ProductVariant/N — max 2048 per product)
        ├── selectedOptions [{name, value}]   // resolved option tuple
        ├── price, compareAtPrice, sku, barcode, taxable
        └── inventoryItem (gid://shopify/InventoryItem/N)
            ├── tracked, requiresShipping, measurement, harmonizedSystemCode
            └── inventoryLevels (per Location)
                └── quantities [{name, quantity}]  // available, on_hand, committed, ...
```

The chain `Variant → InventoryItem → InventoryLevels → quantities` is what every sync engine has to walk. SyncApp uses **`variant.barcode`** as the cross-store join key — two variants in different shops with the same non-empty barcode form one `BarcodeGroup`.

Naming: Shopify GraphQL uses GIDs (`gid://shopify/<Type>/<numeric_id>`); REST and webhooks use bare numeric ids. Webhook payloads use snake_case; GraphQL uses camelCase.

---

## Object: Product

`type Product implements Node & HasMetafields & HasPublishedTranslations & Publishable & HasMetafieldDefinitions & Navigable & OnlineStorePreviewable & CommentEventSubject & ResourceWithMetafields`

Access scope: `read_products` (queries) / `write_products` (mutations).

### Identity and basics

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | `gid://shopify/Product/<n>` |
| `legacyResourceId` | `UnsignedInt64!` | REST id, numeric |
| `defaultCursor` | `String!` | Pagination cursor, ascending by id |
| `title` | `String!` | Customer-facing name |
| `handle` | `String!` | URL slug — lowercase, hyphens; unique per shop |
| `description` | `String!` | Plain text (HTML stripped) |
| `descriptionHtml` | `HTML!` | Raw HTML body |
| `productType` | `String!` | Merchant-defined category |
| `vendor` | `String!` | Merchant-defined brand/supplier |
| `tags` | `[String!]!` | Searchable labels; **mutation replaces the whole list** |
| `status` | `ProductStatus!` | `ACTIVE` / `DRAFT` / `ARCHIVED` (and `UNLISTED` from 2025-10) |
| `templateSuffix` | `String` | Liquid theme template override |
| `giftCardTemplateSuffix` | `String` | Liquid template for gift cards |
| `isGiftCard` | `Boolean!` | True when this product is a gift card |
| `requiresSellingPlan` | `Boolean!` | True if only buyable as subscription |

### Inventory roll-ups (computed by Shopify)

| Field | Type | Notes |
|---|---|---|
| `totalInventory` | `Int!` | Sum across all variants and locations |
| `tracksInventory` | `Boolean!` | True if any variant has `inventoryItem.tracked: true` |
| `hasOnlyDefaultVariant` | `Boolean!` | True for single-variant products with the synthetic default option |
| `hasOutOfStockVariants` | `Boolean!` | At least one variant is OOS somewhere |
| `hasVariantsThatRequiresComponents` | `Boolean!` | Bundle parent indicator |

### Time

| Field | Type | Notes |
|---|---|---|
| `createdAt` | `DateTime!` | ISO 8601 |
| `updatedAt` | `DateTime!` | ISO 8601 — bumped by any field write |
| `publishedAt` | `DateTime` | Null if never published to online store |

### Storefront URLs

| Field | Type | Notes |
|---|---|---|
| `onlineStoreUrl` | `URL` | Null if not published or no online store |
| `onlineStorePreviewUrl` | `URL` | Preview URL even in DRAFT |

### Structure (connections / lists)

| Field | Type | Notes |
|---|---|---|
| `options(first: Int)` | `[ProductOption!]!` | Returns up to `first` options (max 3 currently). NOT paginated. |
| `variants(first/last/after/before/sortKey/reverse/query)` | `ProductVariantConnection!` | Paginated, sortable by `POSITION` (default), `ID`, `TITLE`, `SKU`, `INVENTORY_QUANTITY`. |
| `variantsCount` | `Count` | Lightweight `{ count, precision }` |
| `media(first/last/after/before/sortKey/query)` | `MediaConnection!` | Mixed image/video/3d/external |
| `mediaCount` | `Count` | |
| `featuredMedia` | `Media` | Primary media (first in position) |
| `collections(first/last/after/before/sortKey/reverse/query)` | `CollectionConnection!` | |
| `category` | `TaxonomyCategory` | Shopify Standard Product Taxonomy node |
| `bundleComponents` | `ProductBundleComponentConnection!` | When this product is a bundle parent |
| `productComponents` | `ProductComponentTypeConnection!` | |
| `productParents` | `ProductConnection!` | Bundle parents that include this product |

### Pricing

| Field | Type | Notes |
|---|---|---|
| `priceRangeV2` | `ProductPriceRangeV2!` | `{ minVariantPrice: MoneyV2, maxVariantPrice: MoneyV2 }` |
| `compareAtPriceRange` | `ProductCompareAtPriceRange` | Null if no compare-at on any variant |
| `contextualPricing(context: ContextualPricingContext!)` | `ProductContextualPricing!` | Market/B2B catalog pricing |

### Metadata

| Field | Type | Notes |
|---|---|---|
| `metafield(namespace, key)` | `Metafield` | Single |
| `metafields(first/after/...)` | `MetafieldConnection!` | Filterable by namespace |
| `metafieldDefinitions(first/...)` | `MetafieldDefinitionConnection!` | **deprecated** — use the standalone `metafieldDefinitions` root query |
| `seo` | `SEO!` | `{ title: String, description: String }` |
| `translations(locale: String!, marketId: ID)` | `[Translation!]!` | Published translations |

### Publication

| Field | Type | Notes |
|---|---|---|
| `resourcePublications(onlyPublished, first/...)` | `ResourcePublicationConnection!` | Channels this product is on |
| `resourcePublicationsV2(catalogType, ...)` | `ResourcePublicationV2Connection!` | Adds B2B catalog filtering |
| `resourcePublicationsCount` | `Count` | |
| `unpublishedPublications(first/...)` | `PublicationConnection!` | Channels it could be on but isn't |
| `publishedOnPublication(publicationId: ID!)` | `Boolean!` | Per-channel check |
| `publishedInContext(context: ContextualPublicationContext!)` | `Boolean!` | Market-specific |
| `availablePublicationsCount` | `Count` | |
| `feedback` | `ResourceFeedback` | Setup-checklist messages from apps |

### Selling plans / subscriptions

| Field | Type | Notes |
|---|---|---|
| `sellingPlanGroups(first/...)` | `SellingPlanGroupConnection!` | |
| `sellingPlanGroupsCount` | `Count` | |

### Audit

| Field | Type | Notes |
|---|---|---|
| `events(first/.../query)` | `EventConnection!` | Action/creator history |
| `restrictedForResource(calculatedOrderId)` | `RestrictedForResource` | Order-edit eligibility check |

### Deprecated fields on Product (do not use in new code)

| Deprecated | Replacement |
|---|---|
| `bodyHtml` | `descriptionHtml` |
| `customProductType` | `productType` |
| `descriptionPlainSummary` | `description` |
| `featuredImage` | `featuredMedia` |
| `images(...)` | `media(...)` (filter `mediaContentType: IMAGE`) |
| `priceRange` | `priceRangeV2` |
| `productPublications`, `publications`, `publicationCount` | `resourcePublications` / `resourcePublicationsCount` |
| `publishedOnChannel`, `publishedOnCurrentChannel`, `publishedOnCurrentPublication` | `publishedOnPublication` |
| `sellingPlanGroupCount` (Int!) | `sellingPlanGroupsCount: Count` |
| `standardizedProductType` | `category` |
| `storefrontId` | use `id` |
| `totalVariants` (Int!) | `variantsCount: Count` |
| `unpublishedChannels` | `unpublishedPublications` |
| `metafieldDefinitions` (on Product) | top-level `metafieldDefinitions` query |
| `productCategory` | `category` |

Scalar types used: `ID`, `String`, `Int`, `Boolean`, `DateTime`, `URL`, `HTML`, `Money`, `MoneyV2`, `UnsignedInt64`.

---

## Object: ProductVariant

`type ProductVariant implements Node & HasMetafields & HasPublishedTranslations & LegacyInteroperability & Navigable`

Access scope: `read_products` (queries) / `write_products` (mutations).

### Identity

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | `gid://shopify/ProductVariant/<n>` |
| `legacyResourceId` | `UnsignedInt64!` | REST id |
| `defaultCursor` | `String!` | |
| `title` | `String!` | "Default Title" for single-option products |
| `displayName` | `String!` | "<product title> - <variant title>" |
| `position` | `Int!` | 1-indexed order within product |
| `product` | `Product!` | Back-reference |
| `selectedOptions` | `[SelectedOption!]!` | `[{ name, value, optionValue }]` — flattened option tuple |

### Identifiers SyncApp cares about

| Field | Type | Notes |
|---|---|---|
| `sku` | `String` | Case-sensitive; required for fulfillment-service binding |
| **`barcode`** | `String` | **SyncApp's cross-store join key.** Free-form; null/empty means "ungrouped". Two variants in *different* shops sharing a non-empty `barcode` form a single `BarcodeGroup` in SyncApp. |
| `taxCode` | `String` | Avalara/tax-platform code (deprecated for most uses) |

### Pricing

| Field | Type | Notes |
|---|---|---|
| `price` | `Money!` | Shop's default currency |
| `compareAtPrice` | `Money` | Strike-through price; null means no sale |
| `taxable` | `Boolean!` | |
| `unitPrice` | `MoneyV2` | EU-style per-100g pricing |
| `unitPriceMeasurement` | `UnitPriceMeasurement` | |
| `showUnitPrice` | `Boolean!` | |
| `contextualPricing(context: ContextualPricingContext!)` | `ProductVariantContextualPricing!` | Per-market, per-catalog |
| `presentmentPrices(first/.../presentmentCurrencies)` | `ProductVariantPricePairConnection!` | **deprecated** — use `contextualPricing` |

### Inventory

| Field | Type | Notes |
|---|---|---|
| `inventoryItem` | `InventoryItem!` | **Critical**: this is the bridge to per-location stock. See the InventoryItem section. |
| `inventoryQuantity` | `Int` | Total *sellable* across all locations. Null when `inventoryItem.tracked: false`. |
| `inventoryPolicy` | `ProductVariantInventoryPolicy!` | `DENY` (default — prevent overselling) or `CONTINUE` (allow backorders) |
| `sellableOnlineQuantity` | `Int!` | What the online store would let a buyer buy right now |
| `availableForSale` | `Boolean!` | Composite: published + has stock or `CONTINUE` policy |

### Media

| Field | Type | Notes |
|---|---|---|
| `media(first/.../sortKey)` | `MediaConnection!` | Variant-specific assets |
| `image` | `Image` | **deprecated** — use `media` filtered by image content type |

### Bundles

| Field | Type | Notes |
|---|---|---|
| `requiresComponents` | `Boolean!` | True ⇒ this variant is bundle-only, hidden from non-bundle channels |
| `productVariantComponents(first/...)` | `ProductVariantComponentConnection!` | The components if this is a bundle variant |
| `productParents(first/.../query)` | `ProductConnection!` | Bundle products that reference this variant |

### Subscriptions

| Field | Type | Notes |
|---|---|---|
| `sellingPlanGroups(first/...)` | `SellingPlanGroupConnection!` | |
| `sellingPlanGroupsCount` | `Count` | |

### Metadata

| Field | Type | Notes |
|---|---|---|
| `metafield(namespace, key)` | `Metafield` | |
| `metafields(first/.../namespace/keys)` | `MetafieldConnection!` | |

### Fulfillment / delivery

| Field | Type | Notes |
|---|---|---|
| `deliveryProfile` | `DeliveryProfile` | |

### Audit

| Field | Type | Notes |
|---|---|---|
| `createdAt`, `updatedAt` | `DateTime!` | |
| `events(first/.../query)` | `EventConnection!` | |
| `translations(locale, marketId)` | `[Translation!]!` | |

### Deprecated on ProductVariant

| Deprecated | Replacement |
|---|---|
| `image` | `media` |
| `taxCode` | usually unused; tax decided at order time |
| `presentmentPrices` | `contextualPricing` |
| `storefrontId` | `id` |
| `sellingPlanGroupCount` (Int!) | `sellingPlanGroupsCount: Count` |
| `metafieldDefinitions` (on ProductVariant) | top-level `metafieldDefinitions` |

---

## Object: ProductOption

`type ProductOption implements Node & HasPublishedTranslations`

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | `gid://shopify/ProductOption/<n>` |
| `name` | `String!` | "Color", "Size", "Material" |
| `position` | `Int!` | 1-indexed; must be sequential across a product's options |
| `values` | `[String!]!` | Just the names of values referenced by at least one variant |
| `optionValues` | `[ProductOptionValue!]!` | Full objects — **includes values not assigned to any variant** |
| `linkedMetafield` | `LinkedMetafield` | If this option is sourced from a metafield definition |
| `translations(locale, marketId)` | `[Translation!]!` | |

`values` vs `optionValues`:
- `values: [String!]!` — strings, only those bound to at least one live variant.
- `optionValues: [ProductOptionValue!]!` — objects, **also includes orphaned values** (defined but no variant uses them). Use this when reconciling option deletion.

Max 3 options per product; max 2048 variants per product (the cap is on the combinatorial count, not the option list).

---

## Object: ProductOptionValue

`type ProductOptionValue implements Node & HasPublishedTranslations`

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `name` | `String!` | "Red", "Large", "Cotton" |
| `hasVariants` | `Boolean!` | False ⇒ orphaned value, safe to delete |
| `swatch` | `ProductOptionValueSwatch` | UI hint — color hex + an image |
| `linkedMetafieldValue` | `String` | If sourced from a metafield |
| `translations(locale, marketId)` | `[Translation!]!` | |

`ProductOptionValueSwatch`: object with `color: String` (hex) and `image: MediaImage`. Used by the storefront to render color/material chips.

---

## Object: Media (interface) and MediaImage

`Media` is a GraphQL **interface**. The concrete types that implement it are:

- **`MediaImage`** — uploaded image asset
- **`Video`** — uploaded video, multi-resolution
- **`ExternalVideo`** — YouTube / Vimeo embed
- **`Model3d`** — GLB/USDZ 3D model

Common fields on the `Media` interface:

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `alt` | `String` | Accessibility text |
| `mediaContentType` | `MediaContentType!` | `IMAGE` / `VIDEO` / `EXTERNAL_VIDEO` / `MODEL_3D` |
| `mediaErrors` | `[MediaError!]!` | Processing failures |
| `mediaWarnings` | `[MediaWarning!]!` | Non-fatal issues |
| `preview` | `MediaPreviewImage` | A safe-to-display still for any media kind |
| `status` | `MediaStatus!` | `UPLOADED` / `PROCESSING` / `READY` / `FAILED` |

Querying media on Product/Variant: always select against the interface and use inline fragments per concrete type:

```graphql
media(first: 20) {
  nodes {
    id
    alt
    mediaContentType
    status
    preview { image { url width height } }
    ... on MediaImage {
      image { url width height altText }
      mimeType
      originalSource { url fileSize }
    }
    ... on Video {
      sources { url format mimeType width height }
      duration
    }
    ... on ExternalVideo {
      host
      originUrl
      embedUrl
    }
    ... on Model3d {
      sources { url format mimeType filesize }
      boundingBox { size }
    }
  }
}
```

### MediaImage

`type MediaImage implements File & Media & Node & HasMetafields`

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `alt` | `String` | |
| `image` | `Image` | **Null until status is READY.** Always check before reading `.url`. |
| `mediaContentType` | `MediaContentType!` | Always `IMAGE` for this type |
| `mediaErrors` | `[MediaError!]!` | |
| `mediaWarnings` | `[MediaWarning!]!` | |
| `preview` | `MediaPreviewImage` | |
| `status` | `MediaStatus!` | |
| `createdAt`, `updatedAt` | `DateTime!` | |
| `mimeType` | `String` | "image/jpeg", "image/png", "image/webp", ... |
| `originalSource` | `MediaImageOriginalSource` | `{ url, fileSize }` of the uploaded asset before CDN transforms |
| `fileStatus` | `FileStatus!` | File-layer status mirror |
| `fileErrors` | `[FileError!]!` | |

When creating a product with media, the upload is **asynchronous**: the mutation returns immediately with `status: PROCESSING`, and Shopify CDN-processes the image. Re-query the product later (or rely on the `media/finalize` webhook) to get a `READY` status and an `image.url`.

---

## Object: InventoryItem

`type InventoryItem implements Node & LegacyInteroperability`

The bridge between `ProductVariant` and per-location `InventoryLevel`s.

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | `gid://shopify/InventoryItem/<n>` |
| `legacyResourceId` | `UnsignedInt64!` | REST id |
| `sku` | `String` | Mirrors the variant SKU |
| `tracked` | `Boolean!` | If false, Shopify doesn't track stock and `inventoryQuantity` returns null |
| `trackedEditable` | `EditableProperty!` | `{ value, locked }` — sometimes locked by 3PL apps |
| `requiresShipping` | `Boolean!` | False for digital, gift cards |
| `measurement` | `InventoryItemMeasurement!` | `{ weight: Weight { value, unit } }` |
| `harmonizedSystemCode` | `String` | 6–13 digit HS tariff code for customs |
| `countryCodeOfOrigin` | `CountryCode` | ISO 3166-1 alpha-2 (`US`, `CN`, `RO`, ...) |
| `provinceCodeOfOrigin` | `String` | ISO 3166-2 (`CA-ON`, `US-CA`) |
| `countryHarmonizedSystemCodes(first/...)` | `CountryHarmonizedSystemCodeConnection!` | Per-destination overrides |
| `unitCost` | `MoneyV2` | Requires "View product costs" permission |
| `duplicateSkuCount` | `Int!` | How many other items share this SKU |
| `inventoryLevel(locationId: ID!)` | `InventoryLevel` | The level at one location |
| `inventoryLevels(first/last/after/before/reverse/includeInactive/query)` | `InventoryLevelConnection!` | All locations |
| `inventoryHistoryUrl` | `URL` | Admin UI deeplink |
| `locationsCount` | `Count` | Locations stocking this item |
| `variant` | `ProductVariant!` | **deprecated** — use `variants` |
| `variants(first/...)` | `ProductVariantConnection` | Inventory items are 1:1 with variants today but the schema is forward-looking |
| `createdAt`, `updatedAt` | `DateTime!` | |

### `inventoryLevels` connection query syntax

The `query` argument supports:

- `created_at:>2024-01-01`
- `id:gid://shopify/InventoryLevel/N` or numeric
- `inventory_group_id`
- `inventory_item_id`
- `updated_at:>=2024-01-01T00:00:00Z`

`includeInactive: true` is required to surface locations where the item is disconnected. Important for SyncApp because reconciliation has to know about disconnected levels.

---

## Object: InventoryLevel

`type InventoryLevel implements Node`

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | `gid://shopify/InventoryLevel/<n>` |
| `item` | `InventoryItem!` | |
| `location` | `Location!` | |
| `quantities(names: [String!]!)` | `[InventoryQuantity!]!` | **`names` is required** — pick from `available`, `on_hand`, `committed`, `reserved`, `damaged`, `safety_stock`, `quality_control`, `incoming` |
| `createdAt`, `updatedAt` | `DateTime!` | |
| `isActive` | `Boolean!` | Connected at this location |
| `canDeactivate` | `Boolean!` | Whether the location can be disconnected (false if there are unfulfilled orders) |
| `deactivationAlert` | `String` | Human-readable reason it can't be deactivated |
| `scheduledChanges(first/...)` | `InventoryScheduledChangeConnection!` | **deprecated** — use the `inventoryScheduledChanges` query |

### Quantity names

| Name | Meaning |
|---|---|
| `available` | Sellable right now (`on_hand` minus `committed`, `reserved`, etc.) |
| `on_hand` | Physical count at the location |
| `committed` | Reserved by paid orders awaiting fulfillment |
| `reserved` | Held by checkouts, draft orders, or apps |
| `damaged` | Defective stock |
| `safety_stock` | App-managed buffer below which `available` should not drop |
| `quality_control` | Awaiting inspection |
| `incoming` | In transit (PO / transfer) |

`available + committed + reserved + damaged + quality_control = on_hand` is the invariant Shopify maintains.

### Example: get a variant's per-location stock in one query

```graphql
query VariantStock($id: ID!) {
  productVariant(id: $id) {
    id
    sku
    barcode
    inventoryItem {
      id
      tracked
      inventoryLevels(first: 50) {
        edges {
          node {
            id
            location { id name isActive }
            quantities(names: ["available", "on_hand", "committed"]) {
              name
              quantity
            }
          }
        }
      }
    }
  }
}
```

SyncApp's `app/graphql/queries.ts` does roughly this on import.

---

## Object: Image

`type Image implements HasMetafields & HasPublishedTranslations`

| Field | Type | Notes |
|---|---|---|
| `id` | `ID` | |
| `altText` | `String` | |
| `height` | `Int` | Null for non-Shopify-CDN images |
| `width` | `Int` | Null for non-Shopify-CDN images |
| `url(transform: ImageTransformInput)` | `URL!` | If `transform` omitted, returns original |
| `metafield(namespace, key)` | `Metafield` | |
| `metafields(first/...)` | `MetafieldConnection!` | |

### Deprecated on Image

| Deprecated | Replacement |
|---|---|
| `src` | `url` |
| `originalSrc` | `url` (no transform) |
| `transformedSrc(maxWidth, maxHeight, crop, scale, preferredContentType)` | `url(transform: ImageTransformInput)` |

### `ImageTransformInput`

| Field | Type | Range |
|---|---|---|
| `maxWidth` | `Int` | 1–5760 |
| `maxHeight` | `Int` | 1–5760 |
| `scale` | `Int` | 1–3 (retina) |
| `crop` | `CropRegion` | `CENTER` / `TOP` / `BOTTOM` / `LEFT` / `RIGHT` |
| `preferredContentType` | `ImageContentType` | `WEBP` / `JPG` / `PNG` |

---

## Enums

### `ProductStatus`

| Value | Meaning |
|---|---|
| `ACTIVE` | Ready to sell; can be published to channels |
| `DRAFT` | Not ready; not visible on any channel. Default for new products. |
| `ARCHIVED` | No longer being sold; hidden from channels but kept for order history |
| `UNLISTED` | (2025-10+) Active but unsearchable; only reachable via direct link |

### `ProductVariantInventoryPolicy`

| Value | Meaning |
|---|---|
| `DENY` | Default. Block checkout when `available <= 0`. |
| `CONTINUE` | Allow backorders / pre-orders past zero. Cart succeeds even if stock is zero. |

### `MediaContentType`

| Value | Notes |
|---|---|
| `IMAGE` | Concrete type `MediaImage` |
| `VIDEO` | Uploaded video; concrete type `Video` |
| `EXTERNAL_VIDEO` | YouTube/Vimeo embed; concrete type `ExternalVideo` |
| `MODEL_3D` | GLB/USDZ; concrete type `Model3d` |

### `MediaStatus`

| Value | Notes |
|---|---|
| `UPLOADED` | Stored, not yet validated |
| `PROCESSING` | Shopify is transcoding / generating thumbnails |
| `READY` | All derivatives generated; `image.url` returns a usable URL |
| `FAILED` | Processing failed; check `mediaErrors` |

### `ProductSortKeys` (products query)

`ID`, `TITLE`, `VENDOR`, `PRODUCT_TYPE`, `INVENTORY_TOTAL`, `UPDATED_AT`, `CREATED_AT`, `PUBLISHED_AT`, `RELEVANCE`.

Default: `ID`. `RELEVANCE` only meaningful when a `query:` argument is provided.

### `ProductVariantSortKeys` (productVariants query)

`ID`, `SKU`, `TITLE`, `INVENTORY_QUANTITY`, `INVENTORY_LEVELS_AVAILABLE`, `INVENTORY_MANAGEMENT`, `UPDATED_AT`, `RELEVANCE`, `NAME`, `POPULAR`, `FULL_TITLE`, `POSITION`.

### `CollectionSortKeys`

`ID` (default), `TITLE`, `UPDATED_AT`, `RELEVANCE`.

### `ProductVariantsBulkCreateUserErrorCode`

Returned by `productVariantsBulkCreate.userErrors[].code`:

| Code | Trigger |
|---|---|
| `CANNOT_SET_NAME_FOR_LINKED_OPTION_VALUE` | Tried to override the name of an option value sourced from a metafield |
| `GREATER_THAN_OR_EQUAL_TO` | Variant price (or other numeric) must be ≥ 0 |
| `INVALID` | Generic invalid input |
| `INVALID_INPUT` | Generic invalid input — usually a malformed ID |
| `INVENTORY_QUANTITIES_LIMIT_EXCEEDED` | Inventory input exceeds 50,000 units across the mutation |
| `MUST_BE_FOR_THIS_PRODUCT` | The provided ID belongs to a different product |
| `NEED_TO_ADD_OPTION_VALUES` | Provided fewer option values than the product has options |
| `NEGATIVE_PRICE_VALUE` | Price < 0 |
| `NO_KEY_ON_CREATE` | A field that's only valid for updates was supplied |
| `NOT_DEFINED_FOR_SHOP` | E.g. tax code not enabled on the shop |
| `OPTION_VALUES_FOR_NUMBER_OF_UNKNOWN_OPTIONS` | Provided more option values than the product has options |
| `PRODUCT_DOES_NOT_EXIST` | `productId` not found |
| `PRODUCT_SUSPENDED` | Product is in a suspended state |
| `SUBSCRIPTION_VIOLATION` | The shop's plan has hit its SKU limit |
| `TOO_MANY_INVENTORY_LOCATIONS` | More than 10 (or plan limit) locations in one mutation |
| `TRACKED_VARIANT_LOCATION_NOT_FOUND` | Provided a `locationId` that doesn't exist on the shop |
| `UNSUPPORTED_COMBINED_LISTING_PARENT_OPERATION` | Tried to add variants to a combined-listing parent |
| `VARIANT_ALREADY_EXISTS` | A variant with the same option values already exists |
| `VARIANT_ALREADY_EXISTS_CHANGE_OPTION_VALUE` | Same as above, with hint to change option values |

### `ProductVariantsBulkUpdateUserErrorCode`

Returned by `productVariantsBulkUpdate.userErrors[].code`:

| Code | Trigger |
|---|---|
| `BLANK` | A required field is empty |
| `CANNOT_SET_NAME_FOR_LINKED_OPTION_VALUE` | Tried to rename a metafield-linked value |
| `CANNOT_SPECIFY_BOTH` | Mutually exclusive fields both provided (e.g. `mediaId` + `mediaSrc`) |
| `GREATER_THAN_OR_EQUAL_TO` | Price < 0 |
| `INVALID_INPUT` | Generic invalid input |
| `INVALID_VALUE` | Metafield value fails its definition's validation |
| `INVENTORY_QUANTITIES_LIMIT_EXCEEDED` | > 50,000 units across mutation |
| `MUST_BE_FOR_THIS_PRODUCT` | Variant id belongs to a different product |
| `MUST_SPECIFY_ONE_OF_PAIR` | One of a required-pair fields is missing |
| `NEED_TO_ADD_OPTION_VALUES` | Variant has fewer option values than the product has options |
| `NEGATIVE_PRICE_VALUE` | Price < 0 |
| `NO_INVENTORY_QUANTITIES_ON_VARIANTS_UPDATE` | **You cannot update inventoryQuantities via this mutation** — use `inventorySetOnHandQuantities` / `inventorySetQuantities` |
| `NOT_DEFINED_FOR_SHOP` | E.g. tax code |
| `OPTION_DOES_NOT_EXIST` | Reference to a non-existent option |
| `OPTION_VALUE_DOES_NOT_EXIST` | Reference to a non-existent option value |
| `OPTION_VALUE_NAME_TOO_LONG` | > 255 chars |
| `OPTION_VALUES_FOR_NUMBER_OF_UNKNOWN_OPTIONS` | More option values than product options |
| `PRODUCT_DOES_NOT_EXIST` | `productId` not found |
| `PRODUCT_SUSPENDED` | Product suspended |
| `PRODUCT_VARIANT_DOES_NOT_EXIST` | One of the provided variant IDs doesn't exist |
| `PRODUCT_VARIANT_ID_MISSING` | A variant entry without an `id` |
| `SUBSCRIPTION_VIOLATION` | Plan SKU limit reached |
| `TOO_LONG` | Field exceeds max length |
| `TOO_SHORT` | Field below min length |
| `UNSUPPORTED_COMBINED_LISTING_PARENT_OPERATION` | |
| `VARIANT_ALREADY_EXISTS` | New option values clash with an existing variant |

---

## Queries

### `product(id: ID!): Product`

Single product by GID.

```graphql
query ProductShow($id: ID!) {
  product(id: $id) {
    id
    title
    handle
    status
    productType
    vendor
    tags
    descriptionHtml
    updatedAt
    variantsCount { count precision }
    options(first: 3) {
      id
      name
      position
      values
    }
    variants(first: 100) {
      nodes {
        id
        title
        sku
        barcode
        price
        compareAtPrice
        inventoryPolicy
        selectedOptions { name value }
        inventoryItem {
          id
          tracked
          requiresShipping
        }
      }
    }
  }
}
```

Returns `null` if the product doesn't exist or isn't visible to this app.

Cost: 1 (object) + sum of selected connections. Adding `variants(first: 100)` with simple fields runs ~10–30 actual cost in practice. Use the response `extensions.cost.actualQueryCost` to tune.

### `products(...): ProductConnection!`

```
products(
  first: Int
  last: Int
  after: String
  before: String
  reverse: Boolean = false
  sortKey: ProductSortKeys = ID
  query: String
  savedSearchId: ID
): ProductConnection!
```

`ProductConnection` exposes `edges { node, cursor }`, `nodes` (shortcut, no cursors), and `pageInfo { hasNextPage, hasPreviousPage, startCursor, endCursor }`.

Page size: max `first/last` is 250; the practical cap before throttling is closer to 50–100 if you expand variants/media.

`query:` accepts the search syntax in the [Search syntax](#search-syntax-query-parameter) section. Common product fields:

```
status:ACTIVE
status:active,draft
vendor:Nike
vendor:'Nike OR Adidas'
product_type:snowboard
title:"The Minimal Snowboard"
title:green*               (prefix wildcard)
handle:the-minimal-snowboard
sku:XYZ-12345
barcode:'ABC-abc-1234'
tag:on_sale
tag_not:archived
collection_id:108179161409
category_id:sg-4-17-2-17
created_at:>2024-01-01
updated_at:>=2024-01-01T00:00:00Z
published_at:>2024-01-01
inventory_total:>150
inventory_total:0
tracks_inventory:true
out_of_stock_somewhere:true
price:>100.00
is_price_reduced:true
gift_card:true
published_status:published    (or hidden, unpublished, online_store:visible, etc.)
publishable_status:580111-approved   (numeric shop id - approved/needs_review/...)
```

Combine with `AND` (implicit), `OR`, `NOT`, parentheses:

```
status:ACTIVE AND vendor:Nike AND -tag:archived
(vendor:Nike OR vendor:Adidas) AND status:ACTIVE AND created_at:>2024-01-01
```

Example with full pagination loop:

```graphql
query ListProducts($cursor: String) {
  products(first: 50, after: $cursor, sortKey: UPDATED_AT, reverse: true,
           query: "status:ACTIVE updated_at:>=2024-01-01") {
    edges {
      cursor
      node {
        id
        handle
        title
        updatedAt
        variants(first: 100) {
          nodes { id sku barcode }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
```

Sample response:

```json
{
  "data": {
    "products": {
      "edges": [
        {
          "cursor": "eyJsYXN0X2lkIjoxMjN9",
          "node": {
            "id": "gid://shopify/Product/123",
            "handle": "winter-hat",
            "title": "Winter Hat",
            "updatedAt": "2026-05-20T11:00:00Z",
            "variants": {
              "nodes": [
                { "id": "gid://shopify/ProductVariant/111", "sku": "HAT-GREY", "barcode": "8901234567890" }
              ]
            }
          }
        }
      ],
      "pageInfo": { "hasNextPage": true, "endCursor": "eyJsYXN0X2lkIjoxMjN9" }
    }
  },
  "extensions": {
    "cost": {
      "requestedQueryCost": 105,
      "actualQueryCost": 12,
      "throttleStatus": { "maximumAvailable": 2000, "currentlyAvailable": 1988, "restoreRate": 100 }
    }
  }
}
```

**Performance tip from Shopify**: when filtering on a field, sort by the same key. `query: "updated_at:>=..."` pairs with `sortKey: UPDATED_AT`.

### `productByIdentifier(identifier: ProductIdentifierInput!): Product`

Introduced **2026-04**. The successor to `productByHandle`.

```graphql
input ProductIdentifierInput {
  id: ID
  handle: String
  customId: UniqueMetafieldValueInput
}

input UniqueMetafieldValueInput {
  namespace: String!
  key: String!
  value: String!
}
```

Exactly one of `id`, `handle`, `customId` must be present.

```graphql
query GetByIdentifier($identifier: ProductIdentifierInput!) {
  productByIdentifier(identifier: $identifier) {
    id
    handle
    title
    status
  }
}
```

Variables:

```json
{ "identifier": { "handle": "boots" } }
```

or:

```json
{
  "identifier": {
    "customId": {
      "namespace": "syncapp",
      "key": "external_id",
      "value": "EXT-123"
    }
  }
}
```

### `productByHandle(handle: String!): Product` (deprecated)

Replaced by `productByIdentifier`. Still works:

```graphql
query { productByHandle(handle: "ipod-nano") { id title status } }
```

### `productVariant(id: ID!): ProductVariant`

```graphql
query GetVariant($id: ID!) {
  productVariant(id: $id) {
    id
    title
    sku
    barcode
    price
    compareAtPrice
    availableForSale
    inventoryPolicy
    inventoryQuantity
    product { id handle }
    inventoryItem {
      id
      tracked
      requiresShipping
      inventoryLevels(first: 10) {
        nodes {
          location { id name }
          quantities(names: ["available", "on_hand"]) { name quantity }
        }
      }
    }
  }
}
```

### `productVariants(...): ProductVariantConnection!`

```
productVariants(
  first: Int
  last: Int
  after: String
  before: String
  reverse: Boolean = false
  sortKey: ProductVariantSortKeys = ID
  query: String
  savedSearchId: ID
): ProductVariantConnection!
```

Variant search fields:

```
barcode:'8901234567890'
sku:HAT-GREY
sku:HAT-*            (prefix)
title:Small
inventory_quantity:0
inventory_quantity:>10
inventory_management:shopify   (or 'none' / fulfillment-service handle)
product_id:123456
product_ids:123,456,789
product_status:ACTIVE
product_type:Snowboards
location_id:74055680
vendor:Nike
taxable:true
gift_card:true
updated_at:>2024-01-01
created_at:>2024-01-01
option1:Red option2:Large       (literal option values)
published_status:published
```

For SyncApp: looking up a variant in a remote shop by barcode is a one-liner:

```graphql
query FindByBarcode($barcode: String!) {
  productVariants(first: 50, query: $barcode) {
    nodes {
      id
      sku
      barcode
      product { id handle title }
      inventoryItem { id }
    }
  }
}
```

Note the `query` variable should be `"barcode:'8901234567890'"`, not the bare barcode (otherwise it matches title/sku too).

### `productTags(first: Int!): StringConnection!`

Page size cap **5000**. Returns just strings, no objects.

```graphql
query { productTags(first: 250) { nodes pageInfo { hasNextPage endCursor } } }
```

### `productTypes(first: Int!): StringConnection!`

Page size cap **1000**.

```graphql
query { productTypes(first: 250) { nodes } }
```

### `productVendors(first: Int!): StringConnection!`

Page size cap **1000**.

```graphql
query { productVendors(first: 250) { nodes } }
```

### `collections(...): CollectionConnection!`

```
collections(
  first/last/after/before/reverse: ...
  sortKey: CollectionSortKeys = ID
  query: String
  savedSearchId: ID
): CollectionConnection!
```

Collection search fields:

```
title:All*
handle:summer-sale
collection_type:custom      (or smart)
id:>=1234
product_id:123456789       (collections that contain this product)
updated_at:>2024-01-01
published_status:published
```

```graphql
query {
  collections(first: 50, query: "collection_type:custom") {
    nodes { id title handle sortOrder updatedAt }
    pageInfo { hasNextPage endCursor }
  }
}
```

---

## Mutations

### `productCreate(product: ProductCreateInput!, media: [CreateMediaInput!]): ProductCreatePayload`

Creates a product. **Variants are created separately** via `productVariantsBulkCreate` — the input no longer accepts a `variants` array (this changed in 2024-10). A new product is created with a single default variant; subsequent `productVariantsBulkCreate` can replace or augment it.

`@idempotent` is not strictly required for `productCreate` (it's safe to retry by handle uniqueness), but supported via the directive.

**Rate limit**: ~10 points. **Plan cap**: after 50,000 product variants, max 1,000 new variants/day (this affects the variant create that follows).

```graphql
mutation CreateProduct($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
  productCreate(product: $product, media: $media) {
    product {
      id
      handle
      title
      status
      options { id name values }
      variants(first: 1) { nodes { id sku barcode } }
    }
    userErrors { field message }
  }
}
```

Variables:

```json
{
  "product": {
    "title": "Eco Water Bottle",
    "handle": "eco-water-bottle",
    "descriptionHtml": "<p>Insulated stainless steel.</p>",
    "productType": "Drinkware",
    "vendor": "EcoLifestyle",
    "status": "ACTIVE",
    "tags": ["eco-friendly", "sports"],
    "seo": { "title": "Eco Water Bottle | EcoLifestyle", "description": "Premium." },
    "productOptions": [
      { "name": "Size", "values": [{ "name": "16oz" }, { "name": "32oz" }] }
    ]
  },
  "media": [
    {
      "alt": "Bottle on white",
      "mediaContentType": "IMAGE",
      "originalSource": "https://cdn.example.com/bottle.jpg"
    }
  ]
}
```

Return shape:

```json
{
  "data": {
    "productCreate": {
      "product": {
        "id": "gid://shopify/Product/9876",
        "handle": "eco-water-bottle",
        "title": "Eco Water Bottle",
        "status": "ACTIVE",
        "options": [{ "id": "gid://shopify/ProductOption/55", "name": "Size", "values": ["16oz", "32oz"] }],
        "variants": { "nodes": [{ "id": "gid://shopify/ProductVariant/444", "sku": null, "barcode": null }] }
      },
      "userErrors": []
    }
  }
}
```

### `productUpdate(product: ProductUpdateInput!, media: [CreateMediaInput!]): ProductUpdatePayload`

Updates an existing product. Does **not** update variants — use `productVariantsBulkUpdate`. The `tags` list is **replaced**, not merged (be defensive: re-fetch, splice, send back).

`@idempotent` supported, recommended.

```graphql
mutation UpdateProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id title tags status updatedAt }
    userErrors { field message }
  }
}
```

```json
{ "product": { "id": "gid://shopify/Product/9876", "tags": ["eco", "sports", "summer"] } }
```

### `productDelete(input: ProductDeleteInput!, synchronous: Boolean): ProductDeletePayload`

**Irreversible**. Deletes the product, all variants, media (unless reused), publications, and the underlying inventory items.

```
input ProductDeleteInput { id: ID! }
```

`synchronous: false` returns a `ProductDeleteOperation` for the caller to poll; useful for products with thousands of variants/locations.

```graphql
mutation DeleteProduct($input: ProductDeleteInput!) {
  productDelete(input: $input, synchronous: false) {
    deletedProductId
    productDeleteOperation { id status }
    userErrors { field message }
  }
}
```

### `productDuplicate(...): ProductDuplicatePayload`

```
productDuplicate(
  productId: ID!
  newTitle: String!
  newStatus: ProductStatus
  includeImages: Boolean = false
  includeTranslations: Boolean = false
  synchronous: Boolean = true   # 2024-10+ for async
): ProductDuplicatePayload
```

Metafields with unique-value constraints are skipped. Returns a `Job` for async image duplication.

```graphql
mutation Duplicate {
  productDuplicate(
    productId: "gid://shopify/Product/123"
    newTitle: "Product Copy"
    newStatus: DRAFT
    includeImages: true
  ) {
    newProduct { id title status }
    imageJob { id done }
    userErrors { field message }
  }
}
```

### `productSet(synchronous: Boolean, identifier: ProductSetIdentifiers, input: ProductSetInput!): ProductSetPayload`

The **upsert** mutation. Single GraphQL call can create-or-update a product including its options and all variants. Used by sync workflows that have the full desired state.

**Critical behavior**: list fields (`variants`, `collections`, `metafields`, `files`) are **replaced wholesale**. Anything not in the input is deleted from the product. Scalar fields are merge-updated.

`@idempotent` recommended for retry safety. **High cost** — typically 10+ points, scales with variant count (~0.2 per variant) and media (~0.6 per file).

`synchronous: false` returns a `ProductSetOperation` for polling; required for large variant counts to avoid timeouts.

```graphql
mutation ProductSet($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product {
      id
      title
      options { id name values }
      variants(first: 100) { nodes { id sku barcode price } }
    }
    productSetOperation { id status }
    userErrors { field message code }
  }
}
```

Variables (create path):

```json
{
  "input": {
    "title": "Winter Hat",
    "handle": "winter-hat",
    "status": "ACTIVE",
    "productOptions": [
      {
        "name": "Color",
        "position": 1,
        "values": [{ "name": "Grey" }, { "name": "Black" }]
      }
    ],
    "variants": [
      {
        "optionValues": [{ "optionName": "Color", "name": "Grey" }],
        "price": "79.99",
        "sku": "HAT-GREY",
        "barcode": "8901234567001"
      },
      {
        "optionValues": [{ "optionName": "Color", "name": "Black" }],
        "price": "69.99",
        "sku": "HAT-BLACK",
        "barcode": "8901234567002"
      }
    ]
  }
}
```

Update path: include `id` in the input or pass `identifier: { handle: "winter-hat" }`.

### `productVariantsBulkCreate(...): ProductVariantsBulkCreatePayload`

```
productVariantsBulkCreate(
  productId: ID!
  variants: [ProductVariantsBulkInput!]!     # max 100 per call
  media: [CreateMediaInput!]
  strategy: ProductVariantsBulkCreateStrategy = DEFAULT
): ProductVariantsBulkCreatePayload
```

`strategy`: `DEFAULT` keeps the synthetic default variant; `REMOVE_STANDALONE_VARIANT` drops it (use when adding the first "real" variant on a product that only has the default).

`@idempotent` supported. Plan caps (50k variants ⇒ 1k/day) apply.

```graphql
mutation BulkCreate(
  $productId: ID!
  $variants: [ProductVariantsBulkInput!]!
  $strategy: ProductVariantsBulkCreateStrategy
) {
  productVariantsBulkCreate(productId: $productId, variants: $variants, strategy: $strategy) {
    product { id variantsCount { count } }
    productVariants {
      id
      title
      sku
      barcode
      inventoryItem { id }
    }
    userErrors { field message code }
  }
}
```

Variables:

```json
{
  "productId": "gid://shopify/Product/9876",
  "strategy": "REMOVE_STANDALONE_VARIANT",
  "variants": [
    {
      "optionValues": [{ "optionName": "Size", "name": "16oz" }],
      "price": "24.99",
      "sku": "BOTTLE-16",
      "barcode": "8901234567001",
      "inventoryPolicy": "DENY",
      "inventoryItem": {
        "tracked": true,
        "requiresShipping": true,
        "cost": "8.50",
        "measurement": { "weight": { "value": 0.35, "unit": "KILOGRAMS" } },
        "harmonizedSystemCode": "392310",
        "countryCodeOfOrigin": "RO"
      }
    }
  ]
}
```

Response includes the new variants with their `inventoryItem.id` — capture this for the inventory chain.

### `productVariantsBulkUpdate(...): ProductVariantsBulkUpdatePayload`

```
productVariantsBulkUpdate(
  productId: ID!
  variants: [ProductVariantsBulkInput!]!    # max 100 per call, each must have `id`
  media: [CreateMediaInput!]
  allowPartialUpdates: Boolean = false
): ProductVariantsBulkUpdatePayload
```

**Cannot** update `inventoryQuantities` — that fails with `NO_INVENTORY_QUANTITIES_ON_VARIANTS_UPDATE`. For stock changes use `inventorySetOnHandQuantities` / `inventorySetQuantities` / `inventoryAdjustQuantities`. You *can* update `inventoryItem` sub-fields (cost, tracked, measurement, HS code, country) here, because those live on the inventory item not on a level.

`allowPartialUpdates: true` makes Shopify apply the valid entries and return `userErrors` for the failed ones; with the default `false`, the whole mutation rolls back on any error.

`@idempotent` recommended.

**SyncApp bulk barcode update** (the canonical use of this mutation in this app):

```graphql
mutation BulkUpdateBarcodes(
  $productId: ID!
  $variants: [ProductVariantsBulkInput!]!
) {
  productVariantsBulkUpdate(
    productId: $productId
    variants: $variants
    allowPartialUpdates: true
  ) {
    productVariants {
      id
      sku
      barcode
      updatedAt
    }
    userErrors {
      field
      message
      code
    }
  }
}
```

Variables:

```json
{
  "productId": "gid://shopify/Product/9876",
  "variants": [
    { "id": "gid://shopify/ProductVariant/111", "barcode": "8901234567001" },
    { "id": "gid://shopify/ProductVariant/112", "barcode": "8901234567002" },
    { "id": "gid://shopify/ProductVariant/113", "barcode": "8901234567003" }
  ]
}
```

**Response**:

```json
{
  "data": {
    "productVariantsBulkUpdate": {
      "productVariants": [
        { "id": "gid://shopify/ProductVariant/111", "sku": "HAT-GREY", "barcode": "8901234567001", "updatedAt": "2026-05-24T10:00:00Z" },
        { "id": "gid://shopify/ProductVariant/112", "sku": "HAT-BLACK", "barcode": "8901234567002", "updatedAt": "2026-05-24T10:00:00Z" }
      ],
      "userErrors": [
        {
          "field": ["variants", "2", "barcode"],
          "message": "Variant does not exist",
          "code": "PRODUCT_VARIANT_DOES_NOT_EXIST"
        }
      ]
    }
  }
}
```

### `productVariantsBulkDelete(productId: ID!, variantsIds: [ID!]!): ProductVariantsBulkDeletePayload`

Note the field name is `variantsIds` (plural with extra s), not `variantIds`. Bites everyone once.

```graphql
mutation BulkDelete($productId: ID!, $variantsIds: [ID!]!) {
  productVariantsBulkDelete(productId: $productId, variantsIds: $variantsIds) {
    product { id variantsCount { count } }
    userErrors { field message }
  }
}
```

Errors out if you'd delete every variant — a product must have at least one. Use `productDelete` instead.

### `productVariantsBulkReorder(productId: ID!, positions: [ProductVariantPositionInput!]!): ProductVariantsBulkReorderPayload`

```
input ProductVariantPositionInput { id: ID!, position: Int! }
```

Positions must be a complete, sequential reordering — no gaps.

```graphql
mutation Reorder {
  productVariantsBulkReorder(
    productId: "gid://shopify/Product/9876"
    positions: [
      { id: "gid://shopify/ProductVariant/112", position: 1 }
      { id: "gid://shopify/ProductVariant/111", position: 2 }
    ]
  ) {
    product { id variants(first: 10) { nodes { id position } } }
    userErrors { field message }
  }
}
```

### `productOptionsCreate(productId: ID!, options: [OptionCreateInput!]!, variantStrategy: ProductOptionCreateVariantStrategy): ProductOptionsCreatePayload`

Adds options to an existing product. `variantStrategy`:
- `LEAVE_AS_IS` (default) — existing variants get the new option's first value implicitly.
- `CREATE` — Shopify creates Cartesian-product variants for every new combination.

Max 3 options per product is enforced at this layer.

```graphql
mutation AddOption($productId: ID!, $options: [OptionCreateInput!]!) {
  productOptionsCreate(
    productId: $productId
    options: $options
    variantStrategy: CREATE
  ) {
    product {
      id
      options { id name values position }
      variantsCount { count }
    }
    userErrors { field message code }
  }
}
```

### `productOptionsDelete(productId: ID!, options: [ID!]!, strategy: ProductOptionDeleteStrategy): ProductOptionsDeletePayload`

`ProductOptionDeleteStrategy` values: `DEFAULT`, `POSITION`, `NON_DESTRUCTIVE`. Used to handle the case where deleting an option would collapse multiple variants into duplicates — `POSITION` keeps the variant at position 1 of the deleted option's values, others are dropped.

All option positions on the product must remain sequential after deletion.

### `productOptionUpdate(productId, option, optionValuesToAdd, optionValuesToUpdate, optionValuesToDelete, variantStrategy): ProductOptionUpdatePayload`

```graphql
mutation UpdateOption {
  productOptionUpdate(
    productId: "gid://shopify/Product/9876"
    option: { id: "gid://shopify/ProductOption/55", name: "Tint", position: 1 }
    optionValuesToAdd: [{ name: "Brown" }]
    optionValuesToUpdate: [{ id: "gid://shopify/ProductOptionValue/777", name: "Charcoal" }]
    optionValuesToDelete: ["gid://shopify/ProductOptionValue/778"]
    variantStrategy: MANAGE
  ) {
    product { options { id name values } }
    userErrors { field message code }
  }
}
```

### `productCreateMedia(productId: ID!, media: [CreateMediaInput!]!): ProductCreateMediaPayload` (deprecated)

Use `productUpdate` with a `media` argument or `productSet` with `files` for new code. Still works:

```graphql
mutation AddMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media {
      id
      alt
      mediaContentType
      status
      ... on MediaImage { image { url } }
    }
    mediaUserErrors { field message code }
    product { id mediaCount { count } }
  }
}
```

`MediaUserErrorCode` includes: `INVALID_MEDIA_TYPE`, `INVALID_IMAGE_FILE_SIZE`, `INVALID_IMAGE_ASPECT_RATIO`, `PROCESSING_FAILED`, `VIDEO_VALIDATION_ERROR`, `FILE_STORAGE_LIMIT_EXCEEDED`, `MODEL3D_VALIDATION_ERROR`, `EXTERNAL_VIDEO_NOT_FOUND`, `MEDIA_DOES_NOT_EXIST`, etc.

### `productDeleteMedia(productId: ID!, mediaIds: [ID!]!): ProductDeleteMediaPayload`

```graphql
mutation DeleteMedia($productId: ID!, $mediaIds: [ID!]!) {
  productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
    deletedMediaIds
    deletedProductImageIds
    mediaUserErrors { field message code }
    product { id mediaCount { count } }
  }
}
```

Atomic — if any media id is invalid, the entire mutation fails and nothing is deleted.

### `productImageUpdate(productId: ID!, image: ImageInput!): ProductImageUpdatePayload` (deprecated)

Replaced by `fileUpdate`. For the rare case of just renaming alt text on an image:

```graphql
mutation UpdateImage($productId: ID!, $image: ImageInput!) {
  productImageUpdate(productId: $productId, image: $image) {
    image { id altText url }
    userErrors { field message }
  }
}
```

```graphql
input ImageInput { id: ID, altText: String, src: URL }
```

### `productPublish(input: ProductPublishInput!): ProductPublishPayload` (deprecated)

Use `publishablePublish` from the publication API instead. Old usage:

```graphql
mutation Publish($input: ProductPublishInput!) {
  productPublish(input: $input) {
    product { id }
    productPublications { publication { id name } }
    userErrors { field message }
  }
}
```

```graphql
input ProductPublishInput {
  id: ID!
  productPublications: [ProductPublicationInput!]!
}

input ProductPublicationInput {
  publicationId: ID
  channelHandle: String   # deprecated
  publishDate: DateTime
}
```

### `productUnpublish(input: ProductUnpublishInput!): ProductUnpublishPayload` (deprecated)

Use `publishableUnpublish` instead.

---

## Input types reference

### `ProductCreateInput`

| Field | Type | Notes |
|---|---|---|
| `title` | `String!` | Required for create |
| `handle` | `String` | Auto-derived from title if omitted |
| `descriptionHtml` | `String` | |
| `productType` | `String` | |
| `vendor` | `String` | |
| `tags` | `[String!]` | Replaces all tags |
| `status` | `ProductStatus` | Defaults to `ACTIVE` for create |
| `seo` | `SEOInput` | `{ title, description }` |
| `productOptions` | `[OptionCreateInput!]` | Up to 3; create-time only — modify later via `productOptions*` mutations |
| `metafields` | `[MetafieldInput!]` | Per-field upsert |
| `collectionsToJoin` | `[ID!]` | |
| `giftCard` | `Boolean` | Create-time only |
| `giftCardTemplateSuffix` | `String` | |
| `requiresSellingPlan` | `Boolean` | |
| `templateSuffix` | `String` | |
| `category` | `ID` | Shopify Standard Product Taxonomy id |
| `combinedListingRole` | `CombinedListingsRole` | `PARENT` / `CHILD` — create-time only |
| `claimOwnership` | `ProductClaimOwnershipInput` | App-managed bundle / subscription provisioning |
| `redirectNewHandle` | `Boolean` | Auto-create a 301 from the old handle (rarely relevant on create) |

Removed/never present: `variants` (use `productVariantsBulkCreate`), `productPublications`, `published`, `publishDate`, `publishOn`.

### `ProductUpdateInput`

Same shape as `ProductCreateInput` plus:

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | Required |
| `collectionsToLeave` | `[ID!]` | |
| `redirectNewHandle` | `Boolean` | Auto-create 301 from old handle to new |

`productOptions` cannot be updated via `productUpdate` — use the option mutations.

### `ProductSetInput`

| Field | Type | Notes |
|---|---|---|
| `id` | `ID` | Optional — populated on update path. Mutually exclusive with `identifier`. |
| `handle` | `String` | |
| `title` | `String` | |
| `descriptionHtml` | `String` | |
| `productType`, `vendor`, `tags`, `status` | various | |
| `seo` | `SEOInput` | |
| `productOptions` | `[OptionSetInput!]` | Drives wholesale option replacement |
| `variants` | `[ProductVariantSetInput!]` | **Replaces the variant list.** Variants absent here are deleted. |
| `files` | `[FileSetInput!]` | Replaces the media list |
| `metafields` | `[MetafieldInput!]` | |
| `category` | `ID` | |
| `collections` | `[ID!]` | Replaces collection memberships |
| `combinedListingRole` | `CombinedListingsRole` | create-only |
| `claimOwnership` | `ProductClaimOwnershipInput` | |
| `giftCard`, `giftCardTemplateSuffix`, `templateSuffix`, `requiresSellingPlan`, `redirectNewHandle` | misc | |

### `ProductSetIdentifiers`

```graphql
input ProductSetIdentifiers {
  customId: UniqueMetafieldValueInput
  handle: String
}
```

### `ProductVariantsBulkInput` (used by `productVariantsBulkCreate` and `productVariantsBulkUpdate`)

| Field | Type | Notes |
|---|---|---|
| `id` | `ID` | Required on update; must be absent on create |
| `barcode` | `String` | |
| `compareAtPrice` | `Money` | |
| `inventoryItem` | `InventoryItemInput` | See below — sets cost, tracked, weight, HS code, country of origin |
| `inventoryPolicy` | `ProductVariantInventoryPolicy` | `DENY` (default) / `CONTINUE` |
| `inventoryQuantities` | `[InventoryLevelInput!]` | **Create-only**. Per-location starting quantities. Capped 50k units across mutation; max 10 locations. |
| `mediaId` | `ID` | Attach existing product media to variant |
| `mediaSrc` | `[String!]` | URLs to upload and attach in this same mutation |
| `metafields` | `[MetafieldInput!]` | |
| `optionValues` | `[VariantOptionValueInput!]` | One entry per product option |
| `price` | `Money` | |
| `quantityAdjustments` | `[InventoryAdjustmentInput!]` | Delta-based starting inventory (alternative to `inventoryQuantities`) |
| `requiresComponents` | `Boolean` | |
| `showUnitPrice` | `Boolean` | |
| `sku` | `String` | |
| `taxCode` | `String` | |
| `taxable` | `Boolean` | |
| `unitPriceMeasurement` | `UnitPriceMeasurementInput` | |

### `VariantOptionValueInput`

Identify the value-to-set on an option for the variant. Two strategies — name-based or ID-based:

```graphql
input VariantOptionValueInput {
  id: ID                  # ProductOptionValue GID; if present, fully-qualified
  name: String            # value name (e.g. "Red")
  optionId: ID            # ProductOption GID
  optionName: String      # option name (e.g. "Color")
  linkedMetafieldValue: String
}
```

Patterns:
- `{ optionName: "Color", name: "Red" }` — name lookup (most common in SyncApp).
- `{ optionId: "gid://...", name: "Red" }` — disambiguates if two options share a name (rare).
- `{ id: "gid://shopify/ProductOptionValue/777" }` — direct reference; safe against renames.

### `InventoryItemInput`

| Field | Type | Notes |
|---|---|---|
| `cost` | `Decimal` | Per-unit cost in shop's default currency |
| `tracked` | `Boolean` | Toggles Shopify's stock tracking on/off for this item |
| `requiresShipping` | `Boolean` | |
| `measurement` | `InventoryItemMeasurementInput` | `{ weight: { value: Float!, unit: WeightUnit! } }` — unit one of `GRAMS`, `KILOGRAMS`, `OUNCES`, `POUNDS` |
| `harmonizedSystemCode` | `String` | 6–13 digits |
| `countryCodeOfOrigin` | `CountryCode` | |
| `provinceCodeOfOrigin` | `String` | |
| `sku` | `String` | Mirrors variant SKU |
| `countryHarmonizedSystemCodes` | `[CountryHarmonizedSystemCodeInput!]` | Per-destination overrides |

### `CreateMediaInput`

```graphql
input CreateMediaInput {
  originalSource: String!        # URL — staged upload URL or any reachable URL
  alt: String
  mediaContentType: MediaContentType!  # IMAGE | VIDEO | EXTERNAL_VIDEO | MODEL_3D
}
```

Used by `productCreate`, `productUpdate`, `productCreateMedia`, `productVariantsBulkCreate`, `productVariantsBulkUpdate`. For uploaded files (not URL hot-link), first call `stagedUploadsCreate` to get a temporary upload target.

### `OptionCreateInput`

```graphql
input OptionCreateInput {
  name: String!
  position: Int
  values: [OptionValueCreateInput!]!
  linkedMetafield: LinkedMetafieldCreateInput
}

input OptionValueCreateInput {
  name: String!
  linkedMetafieldValue: String
}

input LinkedMetafieldCreateInput {
  namespace: String!
  key: String!
  values: [String!]    # the metafield's allowed values that become option values
}
```

### `OptionSetInput` (used by `productSet`)

```graphql
input OptionSetInput {
  id: ID                # present for updates
  name: String
  position: Int
  values: [OptionValueSetInput!]
  linkedMetafield: LinkedMetafieldCreateInput
}

input OptionValueSetInput {
  id: ID
  name: String
  linkedMetafieldValue: String
}
```

### `ProductVariantSetInput` (used by `productSet`)

| Field | Type | Notes |
|---|---|---|
| `id` | `ID` | Present for update of an existing variant in the list |
| `optionValues` | `[VariantOptionValueInput!]` | One per product option |
| `price` | `Money` | |
| `compareAtPrice` | `Money` | |
| `sku` | `String` | |
| `barcode` | `String` | |
| `taxable` | `Boolean` | |
| `taxCode` | `String` | |
| `position` | `Int` | |
| `inventoryPolicy` | `ProductVariantInventoryPolicy` | |
| `inventoryQuantities` | `[ProductSetInventoryInput!]` | Per-location quantities; capped 50k units total |
| `inventoryItem` | `InventoryItemInput` | |
| `metafields` | `[MetafieldInput!]` | |
| `file` | `FileSetInput` | Single variant image/video |
| `requiresComponents` | `Boolean` | |
| `unitPriceMeasurement` | `UnitPriceMeasurementInput` | |

Complexity cost: ~0.2 per variant + 0.4 per metafield + 0.6 per file. A `productSet` with 100 variants × 5 metafields × 1 image runs ~120 cost. Watch the bucket.

---

## Search syntax (`query:` parameter)

Shopify search syntax is shared across most list queries (products, productVariants, collections, orders, draftOrders, customers, files, ...).

### Operators

| Operator | Example | Notes |
|---|---|---|
| `AND` | `status:ACTIVE AND vendor:Nike` | Implicit between terms |
| `OR` | `vendor:Nike OR vendor:Adidas` | Caps required |
| `NOT` | `NOT status:archived` | Caps required |
| `-` | `-status:archived` | Equivalent to `NOT` on a single term |
| `:` | `status:ACTIVE` | Equality |
| `:>` | `inventory_total:>10` | Greater than |
| `:<` | `price:<50` | Less than |
| `:>=` | `created_at:>=2024-01-01` | GTE |
| `:<=` | `created_at:<=2024-12-31` | LTE |
| `*` (suffix) | `title:head*` | Prefix wildcard. `*head` is **not** supported |
| `*` (alone) | `barcode:*` | "Field has a non-null value" |
| `-field:*` | `-barcode:*` | "Field is null" |
| `"…"` / `'…'` | `tag:'on sale'` | Phrase / value with spaces |
| `(…)` | `(vendor:Nike OR vendor:Adidas) AND status:ACTIVE` | Grouping |
| `\` | `title:Why\?` | Escape `:`, `(`, `)`, `\` |

Date/time: ISO 8601. Single-quoting required if the value has special chars: `created_at:>'2020-10-21T23:39:20Z'`.

### Caveats

- Range queries on large collections are slow or time out. Pair filtering with the matching `sortKey` to keep them fast.
- Search is eventually-consistent — a freshly-updated product can take several seconds to appear in results.
- Query string length is implementation-limited; keep below ~1 KB.
- The GraphQL response includes `extensions.warnings` when the query is malformed but still parseable — log them.

### Recipes for SyncApp

- **All variants with a given barcode anywhere on the shop**: `productVariants(first: 50, query: "barcode:'8901234567890'")`. Most often returns 0 or 1, sometimes more if the merchant has duplicates (in which case SyncApp's barcode group has multiple local members and needs to fan out).
- **Recently changed products since last sync**: `products(first: 100, sortKey: UPDATED_AT, reverse: true, query: "updated_at:>=2026-05-24T08:00:00Z")`.
- **All active, tracked products that are out of stock somewhere**: `products(first: 100, query: "status:ACTIVE tracks_inventory:true out_of_stock_somewhere:true")`. Useful for the velocity / reports dashboards.

---

## Pagination model

Cursor-based, GraphQL-Relay-flavored. Every connection (`ProductConnection`, `ProductVariantConnection`, `MediaConnection`, etc.) exposes:

```graphql
connection {
  edges {
    cursor       # opaque base64 string
    node { ... } # the actual object
  }
  nodes { ... }  # shortcut — same objects, no cursors
  pageInfo {
    hasNextPage
    hasPreviousPage   # only meaningful when paginating with `before`
    startCursor
    endCursor
  }
}
```

Arguments:

- **Forward**: `first: 50, after: <cursor>`. Walks the list in natural order.
- **Backward**: `last: 50, before: <cursor>`.
- **Sort**: `sortKey: ProductSortKeys, reverse: Boolean`.
- **Filter**: `query: String`.

Rules:
- Cannot mix `first` and `last` in the same request.
- Page size cap depends on the type (most connections: 250 max, `productTags`: 5000).
- Cursors are opaque — never construct them; pass back exactly what Shopify returned.
- Cursors are valid for ~24h; after that they may be invalidated.

Canonical pagination loop:

```ts
async function* allProducts(client) {
  let cursor: string | null = null;
  while (true) {
    const resp = await client.query({
      data: { query: PRODUCTS_QUERY, variables: { cursor } },
    });
    const conn = resp.body.data.products;
    for (const edge of conn.edges) yield edge.node;
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
}
```

SyncApp does this in `app/graphql/queries.ts` + service layer.

---

## Rate limits and query cost

GraphQL Admin API uses a **calculated cost** model, not request count.

Each query has a cost in points:

- Scalar/enum fields: 0
- Object fields: 1
- Connections: cost ~= sum of (1 + child cost) × `first`/`last`
- Mutations: 10 (default), some higher (`productSet` ~10+ scaling, bulk mutations scale with variant count)

The response carries cost info:

```json
"extensions": {
  "cost": {
    "requestedQueryCost": 105,
    "actualQueryCost": 12,
    "throttleStatus": {
      "maximumAvailable": 2000.0,
      "currentlyAvailable": 1988.0,
      "restoreRate": 100.0
    }
  }
}
```

| Plan | Bucket capacity | Restore rate |
|---|---|---|
| Standard / Basic | 1000 | 50/sec |
| Shopify | 1000 | 50/sec |
| Advanced Shopify | 2000 | 100/sec |
| Shopify Plus | 2000 | 100/sec (with higher dynamic caps) |
| Plus Plus / Enterprise | up to 10,000 | up to 200/sec |

**SyncApp's `rateLimitedShopifyFetch`** (in `app/services/rate-limiter.server.ts`) reads `extensions.cost.throttleStatus` and gates outgoing calls per shop. Never bypass it — direct `admin.graphql` calls don't get rate-limit accounting.

Throttling errors:

```json
{
  "errors": [{
    "message": "Throttled",
    "extensions": {
      "code": "THROTTLED",
      "cost": { "requestedQueryCost": 105, "actualQueryCost": null, "throttleStatus": {...} }
    }
  }]
}
```

Strategy: retry after `(requestedQueryCost - currentlyAvailable) / restoreRate` seconds (rounded up), with jitter. SyncApp's circuit breaker tracks consecutive throttles and opens after 3.

### The 50,000 variants rule

When a shop crosses 50,000 product variants total, the following are limited to **1,000 new variants per day**:

- `productCreate` (the single default variant it creates counts)
- `productUpdate`
- `productVariantCreate` (legacy)
- `productVariantsBulkCreate`

This does **not** apply to Shopify Plus stores.

`productSet` is also limited above 50k variants — even though it's an upsert, every variant in its `variants` array counts when one is newly created.

---

## `@idempotent` directive

Some mutations are **required** to be called with `@idempotent` in API 2026-04+ (inventory writes especially). Even where it's optional, applying it makes retries server-side-safe — Shopify dedupes by the `key` argument for ~24h.

Placement is at the **field** level (the mutation root call), not the operation level:

```graphql
mutation Foo($input: FooInput!, $idemKey: String!) {
  someMutation(input: $input) @idempotent(key: $idemKey) {
    ...
  }
}
```

Required on (Products/Variants/Media scope):
- `productCreate` — supported, not required.
- `productUpdate` — supported, recommended.
- `productSet` — supported, recommended (a duplicate retry can otherwise wipe variants twice).
- `productVariantsBulkCreate` — supported, recommended.
- `productVariantsBulkUpdate` — supported, recommended.
- `productVariantsBulkDelete` — supported, safe without it.
- `productCreateMedia` / `productDeleteMedia` — supported, recommended.
- `productOptionsCreate` / `productOptionUpdate` / `productOptionsDelete` — supported.

**Required** on (sibling skill, but relevant):
- `inventorySetQuantities`, `inventorySetOnHandQuantities`, `inventoryAdjustQuantities` — API will refuse the mutation without `@idempotent(key: ...)`.

Key generation: any non-empty string ≤ 255 chars. SyncApp uses `<prefix>-<uuid v4>`; see `newIdempotencyKey()` in `app/graphql/mutations.ts`.

A duplicate retry with the same key:
- If the original succeeded: replays the same response.
- If the original failed: replays the same failure.
- If the original is still in flight: returns a deterministic "already processing" error.

Different logical attempts must use **different keys** (don't reuse the key across distinct intents).

---

## SyncApp angles

This section is the bridge between the raw Shopify API and how SyncApp uses it. Cross-reference with `MASTERFILE.md` for module layout and `PRODUCTION-AUDIT.md` for known issues.

### 1. Barcode is the join key

The single most important invariant: **a non-empty `variant.barcode` is what joins variants across shops into a `BarcodeGroup`**. Empty / null barcode means the variant is ungrouped and sync skips it entirely.

Reading: pull `variant.barcode` on every variant query. SyncApp's `PRODUCTS_QUERY` already does.

Writing: never write a barcode without going through SyncApp's group-aware path. A direct `productVariantsBulkUpdate` with a barcode change *will* land in Shopify but the local DB's `BarcodeGroup.barcodeKey` and `barcode_groups_membership` rows won't update until the `PRODUCTS_UPDATE` webhook reconciles. There is a window of inconsistency.

### 2. Handling barcode changes via `PRODUCTS_UPDATE`

The `PRODUCTS_UPDATE` webhook fires when *any* product field changes, including any variant's `barcode`. The payload includes the **full product with all variants**. SyncApp's normalize worker has to:

1. Pull the prior local snapshot of every variant on this product.
2. Diff `variant.barcode` per variant.
3. For every variant where `barcode` changed:
   - If the new barcode is empty: remove the variant from its `BarcodeGroup`. If the group ends up with < 2 members, mark it inactive (no point allocating across one shop).
   - If the new barcode is non-empty and matches an existing group's `barcodeKey`: move the variant into that group, recompute the group's `CentralStockPool` (sum of `on_hand` across all members), and `markDirty(workspaceId, groupId, "barcode-changed")`.
   - If the new barcode is non-empty and doesn't match any existing group: create a new `BarcodeGroup` keyed on the new barcode (still in "pending" state until a second variant from another shop joins).
4. If the **old** barcode left a group with < 2 members, mark that group inactive too — but **don't delete it** because the variant might bounce back.

Edge case: a single `PRODUCTS_UPDATE` can re-barcode multiple variants on the same product. Treat each variant independently.

Edge case: the same barcode can appear on two variants of the same product (Shopify allows it). SyncApp treats both as members of the same group on this shop; the allocation engine still has only one pool to draw from.

Self-push: if the barcode change was caused by SyncApp itself pushing to Shopify, the `INVENTORY_LEVELS_UPDATE` echo is what we suppress — *not* `PRODUCTS_UPDATE`. SyncApp doesn't currently write barcodes from sync flows, so this is a future concern.

### 3. Handling variant deletions

A variant disappears in two ways:

1. **`productVariantsBulkDelete` direct call** — fires a `PRODUCTS_UPDATE` webhook for the parent product. The variant_id will be absent from `variants[]`.
2. **`productDelete`** — fires a `PRODUCTS_DELETE` webhook with just the product id. All variants of that product are now gone.

Detection in normalize worker:
- For `PRODUCTS_UPDATE`: compare incoming `variants[].id` set with the local snapshot. Any local variant id not in the incoming payload was deleted.
- For `PRODUCTS_DELETE`: list all local variants belonging to that product and mark them deleted.

Group bookkeeping on variant deletion:
- Soft-delete the local `Variant` row (set `deleted_at`). Don't hard-delete — historical orders, snapshots, and reports reference it.
- Remove the variant from its `BarcodeGroup`. Recompute pool quantity.
- If the group drops below 2 members, mark it inactive (preserves the row so a future variant with the same barcode can rejoin).
- If the group's last member was the deleted variant, mark the group `DELETED` after a grace period (e.g. 30 days) so it can be GC'd.

Detection bug to avoid: the variant might still appear in old `PRODUCTS_UPDATE` payloads if a webhook is retried out of order. Always check `webhook_event.eventKey` and the variant's local `updated_at` before applying changes.

### 4. Extracting `inventory_item_id` when importing variants

The minimum chain for a write-capable variant import:

```graphql
variant {
  id                  # ProductVariant GID
  sku
  barcode
  inventoryItem {
    id                # InventoryItem GID — what every inventory mutation takes
    tracked           # if false, inventory write will silently no-op
    requiresShipping  # affects shipping calculations downstream
  }
}
```

Store `inventoryItem.id` (or the numeric `legacyResourceId`) on the local `InventoryItem` row keyed by `(workspaceId, shopId, variantId)`. Every later inventory mutation needs this id.

If `inventoryItem.tracked: false`, SyncApp marks the variant as un-trackable in the local DB and excludes it from allocation. To enable tracking you'd have to call `inventoryItemUpdate(id, input: { tracked: true })` first — usually a merchant decision, not the app's.

For multi-location: also fetch `inventoryItem.inventoryLevels(first: N)` to populate `InventoryLevel` rows per `(InventoryItem, Location)`. Each `InventoryLevel` is itself addressable by GID for finer-grained operations, but the SET/ADJUST mutations key on `(inventoryItemId, locationId)` pair anyway.

### 5. Pagination + sort key pairing for "since" syncs

For incremental sync (the reconciliation worker's fast path), always:

```graphql
products(
  first: 100
  sortKey: UPDATED_AT
  reverse: true
  query: "updated_at:>=<last_sync_iso>"
)
```

The `sortKey` matching the `query:` filter is critical for performance. Without it, Shopify scans the whole product table.

Watch for clock skew: subtract 30 seconds from `<last_sync_iso>` to avoid missing edits that landed during the previous sync's runtime.

### 6. Tags are a list — mutations replace

`productUpdate(product: { id, tags: ["new"] })` **deletes all other tags**. Always read-merge-write:

```ts
const existing = await getProduct(id);
const newTags = Array.from(new Set([...existing.tags, "added-by-syncapp"]));
await updateProduct({ id, tags: newTags });
```

Same goes for `collectionsToJoin` vs `collectionsToLeave` — explicit add/remove deltas, no list replacement.

### 7. `productSet` is destructive — only use when you have the full state

`productSet` will delete variants not in the input. SyncApp uses it sparingly — only when bootstrapping a fresh shop or migrating a product wholesale. The default path is `productVariantsBulkCreate` / `productVariantsBulkUpdate` / `productVariantsBulkDelete` because these are surgical.

### 8. Variant max is 2048; product options max 3

Hardcoded in the API. SyncApp's import path should refuse to mirror a product that would push the target shop past either cap, and surface this in the UI as a sync error. The error code from Shopify will be a generic `SUBSCRIPTION_VIOLATION` if SKU plan limits are hit, but the 2048/product limit shows up as an `INVALID_INPUT` with a message about "variant limit exceeded".

### 9. Webhooks vs polling for variant changes

`INVENTORY_LEVELS_UPDATE` fires for every quantity change at the location level. `PRODUCTS_UPDATE` fires for any product/variant field change *and includes the current inventory_total*, but `inventory_total` lags by seconds. For accurate stock, **always read via `inventoryLevels.quantities(names: [...])`** rather than trusting `product.totalInventory` or `variant.inventoryQuantity` from a webhook payload.

SyncApp's reconciliation worker uses this asymmetry: webhooks trigger fast sync, but the 10-minute fast-recon and 6-hour full-recon GraphQL-poll authoritative inventory levels.

### 10. Idempotency in retries (web ↔ worker boundary)

When the web process enqueues a sync job and the worker retries it, the **same idempotency key** must be reused across attempts of the same logical operation. The key should be derived from `(workspaceId, barcodeGroupId, shopId, attempt-cycle-id)`, not regenerated per HTTP call. Shopify's 24h dedup means the second attempt sees the same result as the first.

Don't reuse keys *across* logical attempts (e.g. don't use the same key on Monday's nightly sync and Tuesday's). New intent ⇒ new key.

### 11. The `extensions.cost` is your friend

Always log `extensions.cost.actualQueryCost` (and the throttle status) at debug level. This is the only data Shopify gives you to tune query budgets, and a 10× drop after a refactor is the single best signal that your sync is healthier.

---

## Quick reference cheat sheet

```graphql
# Find a variant by barcode (cross-shop join key)
query { productVariants(first: 5, query: "barcode:'8901234567890'") {
  nodes { id sku barcode product { id handle } inventoryItem { id } }
}}

# Get a variant's per-location stock in one shot
query { productVariant(id: "gid://shopify/ProductVariant/N") {
  id sku barcode inventoryPolicy
  inventoryItem {
    id tracked requiresShipping
    inventoryLevels(first: 50) { nodes {
      location { id name }
      quantities(names: ["available","on_hand","committed"]) { name quantity }
    }}
  }
}}

# Bulk barcode rewrite (SyncApp's most common variant mutation)
mutation($pid: ID!, $vs: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $pid, variants: $vs, allowPartialUpdates: true) {
    productVariants { id sku barcode }
    userErrors { field message code }
  }
}

# Incremental product sync since last run
query($cursor: String, $since: String) {
  products(first: 100, sortKey: UPDATED_AT, reverse: true,
           query: $since, after: $cursor) {
    edges { cursor node {
      id handle title updatedAt
      variants(first: 100) { nodes { id sku barcode inventoryItem { id tracked } } }
    }}
    pageInfo { hasNextPage endCursor }
  }
}
```
