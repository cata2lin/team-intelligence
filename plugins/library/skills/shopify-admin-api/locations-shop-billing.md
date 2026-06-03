# Shopify Admin GraphQL — Locations, Shop, App Installation, Billing, Markets & Publications

Reference for SyncApp. Built against Shopify Admin GraphQL API version **2026-04**. Sources are the live `shopify.dev/docs/api/admin-graphql/latest/...` pages fetched 2026-05-24.

This document covers the surfaces SyncApp uses for:

- **Workspace bootstrapping** — discovering shop currency/timezone and the location set per store.
- **Inventory routing** — the Location object (especially the FS-managed edge case that recurs as a SyncApp bug).
- **Plan tiering** — AppSubscription + APP_SUBSCRIPTIONS_UPDATE webhook.
- **Metered upsell** — appUsageRecordCreate, used for overage charges on Growth/Business tiers.
- **Awareness only** — Markets, Channels/Publications (skim).

---

## Table of Contents

1. [Location object](#1-location-object)
2. [Legacy / Fulfillment-service locations (SyncApp gotcha)](#2-legacy--fulfillment-service-locations)
3. [Location mutations](#3-location-mutations)
4. [Shop object](#4-shop-object)
5. [CurrentAppInstallation query](#5-currentappinstallation-query)
6. [AppSubscription object](#6-appsubscription-object)
7. [Billing mutations](#7-billing-mutations)
8. [Plan replacement behavior](#8-plan-replacement-behavior)
9. [Test mode billing](#9-test-mode-billing)
10. [Trial behavior](#10-trial-behavior)
11. [Subscription state transitions](#11-subscription-state-transitions)
12. [Markets](#12-markets-overview-skim)
13. [Channels / Publications](#13-channels--publications-skim)
14. [SyncApp-specific patterns](#14-syncapp-specific-patterns)

---

## 1. Location object

A `Location` represents a physical or virtual place where the merchant stocks inventory and from which it fulfills orders. SyncApp pulls one `Location` row per merchant location into `Location` (Prisma model) and joins it against `InventoryLevel` to know how much pool inventory to allocate per store + location.

### Full field list

#### Identity

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | Globally-unique GID, e.g. `gid://shopify/Location/12345`. |
| `legacyResourceId` | `UnsignedInt64!` | The numeric REST resource ID. SyncApp does NOT use this — store the GID. |
| `name` | `String!` | Merchant-visible name. |

#### Lifecycle state

| Field | Type | Description |
|---|---|---|
| `isActive` | `Boolean!` | True if the location can stock inventory and fulfill orders. |
| `activatable` | `Boolean!` | True if a deactivated location can be reactivated. |
| `deactivatable` | `Boolean!` | True if an active location can be deactivated (no outstanding fulfillments / orders blocking it). |
| `deletable` | `Boolean!` | True if the location can be deleted outright. |
| `deactivatedAt` | `String` | ISO 8601 timestamp of last deactivation. Nullable. |
| `createdAt` | `DateTime!` | When this location was first created. |
| `updatedAt` | `DateTime!` | Last update timestamp. |

Note that the type of `deactivatedAt` is `String` in the schema, not `DateTime`. It is still ISO-8601 formatted but parsed as a plain string by the SDK. Wrap it in `new Date(...)` if you need to compare.

#### Fulfillment / inventory capabilities

| Field | Type | Description |
|---|---|---|
| `fulfillsOnlineOrders` | `Boolean!` | Whether this location can fulfill online orders. Used as the routing input for online-channel checkout. |
| `isFulfillmentService` | `Boolean!` | **Critical for SyncApp.** True if this location is owned/managed by an external Fulfillment Service app (FBA, ShipBob, etc). FS-managed locations cannot accept direct inventory writes from third parties (see Section 2). |
| `fulfillmentService` | `FulfillmentService` | Non-null when `isFulfillmentService=true`. The FS object with `handle`, `type`, `callbackUrl`, etc. |
| `shipsInventory` | `Boolean!` | Legacy "this location ships inventory" flag. Prefer `fulfillsOnlineOrders` for new logic. |
| `hasActiveInventory` | `Boolean!` | Whether this location currently has any `InventoryLevel` with a non-zero quantity. Used by `locationDeactivate` to decide whether a `destinationLocationId` is required. |
| `hasUnfulfilledOrders` | `Boolean!` | Whether this location has open fulfillment orders. Also blocks deactivation. |

#### Pickup / address

| Field | Type | Description |
|---|---|---|
| `address` | `LocationAddress!` | Full address object (see below). |
| `addressVerified` | `Boolean!` | Whether Shopify has geocode-verified the address. |
| `suggestedAddresses` | `[LocationSuggestedAddress!]!` | List of address corrections Shopify recommends (e.g. ZIP+4 expansion). |
| `localPickupSettingsV2` | `DeliveryLocalPickupSettings` | Local pickup configuration, e.g. pickup instructions, time windows. Null if local pickup is disabled at this location. |

#### Custom data

| Field | Type | Description |
|---|---|---|
| `metafield(namespace, key)` | `Metafield` | Single custom field on this location. |
| `metafields(first, ...)` | `MetafieldConnection!` | Paginated list of all metafields visible to the current app. |

#### Inventory access

| Field | Type | Description |
|---|---|---|
| `inventoryLevel(inventoryItemId: ID!)` | `InventoryLevel` | Pull a specific InventoryItem's level at this location. Returns null if the item isn't stocked here. |
| `inventoryLevels(first, ...)` | `InventoryLevelConnection!` | Paginated list of every InventoryLevel at this location. Useful for full reconciliation runs. |

#### Deprecated — do not use

| Field | Reason |
|---|---|
| `isPrimary` | Shopify deprecated the concept of a primary location. Multi-location is the only model now. SyncApp must NOT branch on `isPrimary`. |
| `metafieldDefinitions` | Replaced by `metafields` filtering. |

### LocationAddress fields

```graphql
type LocationAddress {
  address1: String
  address2: String
  city: String
  country: String
  countryCode: String
  formatted: [String!]!     # Non-null. Multi-line formatted string array.
  latitude: Float
  longitude: Float
  phone: String
  province: String
  provinceCode: String
  zip: String
}
```

All fields except `formatted` are nullable. `formatted` returns a list of pre-rendered lines suitable for display.

### Example: SyncApp's location-import query

```graphql
query SyncAppLocations($cursor: String) {
  locations(
    first: 50
    after: $cursor
    includeLegacy: true          # MUST be true — see Section 2
    includeInactive: true        # We track deactivated ones too for audit history
    sortKey: NAME
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      isActive
      isFulfillmentService
      fulfillmentService { handle type }
      fulfillsOnlineOrders
      shipsInventory
      hasActiveInventory
      address {
        countryCode
        provinceCode
        city
        zip
      }
      createdAt
      updatedAt
    }
  }
}
```

### Sort keys

`LocationSortKeys` has three values:

- `ID` — sort by GID.
- `NAME` — sort alphabetically by display name. **This is the default** when no `sortKey` is provided.
- `RELEVANCE` — search relevance score. Only meaningful when `query:` is also supplied.

### Single-location query

```graphql
query OneLocation($id: ID!) {
  location(id: $id) {
    id
    name
    isActive
  }
}
```

If `id` is omitted, Shopify returns the primary location (legacy behaviour — but `isPrimary` is deprecated, so behaviour here is unstable across shops). SyncApp always passes the `id` explicitly.

### LocationsAvailableForDeliveryProfiles

```graphql
query DeliveryProfileEligibleLocations {
  locationsAvailableForDeliveryProfilesConnection(first: 100) {
    nodes {
      id
      name
    }
  }
}
```

Returns locations that are valid as origins for a Delivery Profile (shipping zones). SyncApp does not currently use this — relevant only if/when we expose per-store shipping settings.

---

## 2. Legacy / Fulfillment-service locations

> **This is a recurring SyncApp bug class.** Read this section even if you're not actively touching location code.

### What "FS-managed" means

A Location with `isFulfillmentService: true` is owned by a Fulfillment Service app — usually a 3PL like Amazon FBA, ShipBob, ShipHero, or Deliverr. The FS app:

- Holds the source of truth for inventory at that location.
- Reports back to Shopify via a `/fetch_inventory` callback (if `inventoryManagement: true`).
- Owns fulfillment of orders routed to that location.

The `FulfillmentService` object on the Location exposes:

```graphql
type FulfillmentService {
  id: ID!
  handle: String!                    # e.g. "shipbob"
  serviceName: String!               # Merchant-visible name
  type: FulfillmentServiceType!      # See enum below
  callbackUrl: URL                   # Where Shopify sends FS callbacks
  inventoryManagement: Boolean!      # If true, FS tracks inventory and updates Shopify
  trackingSupport: Boolean!          # Whether FS reports tracking numbers
  requiresShippingMethod: Boolean!   # Whether physical shipping is required
  location: Location                 # Inverse pointer back to its Location
  fulfillmentOrdersOptIn: Boolean!   # Deprecated
}
```

### FulfillmentServiceType enum

- `GIFT_CARD` — Gift card fulfillment. Treated by Shopify as automatically fulfilled.
- `MANUAL` — Merchant fulfills by hand. Most merchant-managed locations have type `MANUAL`.
- `THIRD_PARTY` — A third-party fulfillment service app. **This is what SyncApp must NOT write to.**

### Why writes to FS locations fail

`inventorySetQuantities`, `inventorySetOnHandQuantities`, `inventoryAdjustQuantities`, and `inventorySetScheduledChanges` will all return userErrors when targeting an FS-managed location's `inventoryItemId + locationId` pair. The error code varies; typical messages are:

- `"Location is managed by a fulfillment service and inventory cannot be set directly"`
- `"The inventory item cannot be stocked at the location"`

The FS owns inventory at its location. The only way for inventory at that location to change is through the FS app's own pipeline (it pushes updates to Shopify via its inventory feed).

### `includeLegacy: true` on the locations query

By default, `locations(first: N)` **excludes** FS-managed locations. They are considered "legacy" in this query argument's terminology — not because they're deprecated, but because they predate the modern multi-location model and are managed via the old FulfillmentService API rather than the native location surface.

**Always pass `includeLegacy: true` when SyncApp imports locations.** Otherwise the merchant's FBA / ShipBob locations are silently missing from our `Location` table, and:

- Webhooks (`INVENTORY_LEVELS_UPDATE`) reference a `locationId` we don't know about → we drop the event.
- Pool snapshots are wrong because we never read inventory from those locations.
- Allocation math is off by however many units sit in the FS warehouse.

This was the root cause of a SyncApp bug where merchants with FBA reported "my Amazon inventory isn't syncing". Fix: `includeLegacy: true` on import + skip-write logic on push.

### SyncApp's `Location.isFsLocation` field

The Prisma `Location` model has a derived boolean `isFsLocation` (true when `isFulfillmentService=true` OR the location's `fulfillmentService.type === 'THIRD_PARTY'`). The allocation engine and sync orchestrator both check this flag:

- **Reads (pool snapshot):** include FS locations.
- **Writes (push allocation back to Shopify):** skip FS locations. The merchant's 3PL stays the source of truth there; we don't try to overwrite it.

If you add new write code paths against `InventoryLevel`, mirror this check.

### `includeInactive: true`

Separately from `includeLegacy`, `includeInactive: true` includes locations whose `isActive: false`. SyncApp imports inactive ones for audit purposes (so we can render history correctly when a merchant deactivates a location mid-month) but never allocates to them.

---

## 3. Location mutations

All four mutations require the `write_locations` access scope. The two destructive ones (`locationActivate`, `locationDeactivate`) require the `@idempotent` directive with an idempotency key as of API version 2026-04.

### 3.1 `locationAdd`

Creates a new merchant-managed location.

```graphql
mutation LocationAdd($input: LocationAddInput!) {
  locationAdd(input: $input) {
    location {
      id
      name
      isActive
      address { formatted }
    }
    userErrors {
      field
      message
      code
    }
  }
}
```

#### `LocationAddInput`

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | `String!` | Yes | Display name. |
| `address` | `LocationAddAddressInput!` | Yes | See below. |
| `fulfillsOnlineOrders` | `Boolean` | No | Defaults to `true`. |
| `metafields` | `[MetafieldInput!]` | No | Custom fields, e.g. `{namespace, key, type, value}`. |

#### `LocationAddAddressInput`

| Field | Type | Required | Notes |
|---|---|---|---|
| `address1` | `String!` | Yes | Street. |
| `address2` | `String` | No | Apt/suite. |
| `city` | `String!` | Yes | |
| `provinceCode` | `String!` | Yes | E.g. `CA`, `NY`. |
| `countryCode` | `CountryCode!` | Yes | ISO 3166-1 alpha-2. |
| `zip` | `String` | No | Postal code. |
| `phone` | `String` | No | |

#### Example

```graphql
mutation {
  locationAdd(input: {
    name: "Brooklyn Warehouse"
    address: {
      address1: "123 Atlantic Ave"
      city: "Brooklyn"
      provinceCode: "NY"
      countryCode: US
      zip: "11201"
    }
    fulfillsOnlineOrders: true
  }) {
    location { id name }
    userErrors { field message code }
  }
}
```

SyncApp does NOT create locations on behalf of merchants. We only read existing ones. This mutation is here for completeness.

### 3.2 `locationEdit`

Updates an existing location.

```graphql
mutation LocationEdit($id: ID!, $input: LocationEditInput!) {
  locationEdit(id: $id, input: $input) {
    location {
      id
      name
      fulfillsOnlineOrders
      address { formatted }
    }
    userErrors {
      field
      message
      code
    }
  }
}
```

#### `LocationEditInput`

| Field | Type | Notes |
|---|---|---|
| `name` | `String` | New display name. |
| `address` | `LocationEditAddressInput` | Same shape as `LocationAddAddressInput` but all fields optional. |
| `fulfillsOnlineOrders` | `Boolean` | **Constraint:** Cannot be set to `false` for fulfillment service locations. The FS owns whether it fulfills online orders. |
| `metafields` | `[MetafieldInput!]` | Custom fields to upsert. |

#### Access scope nuance

- `write_locations` for merchant-managed locations.
- `write_fulfillments` for editing fulfillment-service locations.

SyncApp does not call `locationEdit`. If we ever need to (e.g. to write a metafield like `syncapp.barcode_group_default`), the call site must respect the FS constraint above.

### 3.3 `locationActivate`

Activates a deactivated location.

```graphql
mutation LocationActivate($locationId: ID!) @idempotent(key: $key) {
  locationActivate(locationId: $locationId) {
    location {
      id
      isActive
    }
    locationActivateUserErrors {
      field
      message
      code
    }
  }
}
```

- Argument: `locationId: ID!`
- Errors live in `locationActivateUserErrors` (not the generic `userErrors`).
- Only works when the target's `activatable: true`. Re-activating an already-active location returns a userError.
- **`@idempotent` directive is required as of 2026-04.** Generate a key like `loc-activate-${locationId}-${nonce}`.

### 3.4 `locationDeactivate`

Deactivates a location, optionally relocating inventory and pending fulfillment orders to another location.

```graphql
mutation LocationDeactivate(
  $locationId: ID!
  $destinationLocationId: ID
) @idempotent(key: $key) {
  locationDeactivate(
    locationId: $locationId
    destinationLocationId: $destinationLocationId
  ) {
    location {
      id
      isActive
    }
    locationDeactivateUserErrors {
      field
      message
      code
    }
  }
}
```

#### Required-destination rules

If the location has `hasActiveInventory: true` OR `hasUnfulfilledOrders: true`, you MUST pass `destinationLocationId`. Otherwise the mutation returns:

- `HAS_ACTIVE_INVENTORY_ERROR` — inventory exists but no destination given.
- `HAS_OPEN_PURCHASE_ORDERS_ERROR` — open fulfillment orders but no destination given.

#### Idempotency

The `@idempotent` directive is **required** as of 2026-04 (was optional from 2026-01). Without it, the mutation rejects.

SyncApp does not deactivate locations. If a merchant deactivates one via Shopify admin, we hear about it through the `LOCATIONS_DEACTIVATE` webhook (Section 14) and re-import to update `Location.isActive` in our database.

### Common location userError codes

Both `LocationActivateUserError` and `LocationDeactivateUserError` define `code` fields. Notable codes:

- `LOCATION_NOT_FOUND`
- `CANNOT_DISABLE_ONLINE_ORDER_FULFILLMENT`
- `HAS_ACTIVE_INVENTORY_ERROR`
- `HAS_OPEN_PURCHASE_ORDERS_ERROR`
- `LOCATION_IS_NOT_ACTIVATABLE` / `LOCATION_IS_NOT_DEACTIVATABLE`
- `GENERIC_ERROR`

Always inspect `code`, not just `message`. Messages are localized and unstable.

---

## 4. Shop object

`Shop` is the per-store root object. SyncApp queries it on OAuth installation to cache currency, timezone, and plan, and re-queries on `SHOP_UPDATE` webhooks.

### Full field list

#### Identity

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | Globally-unique. |
| `name` | `String!` | Display name. |
| `myshopifyDomain` | `String!` | `*.myshopify.com` canonical domain. SyncApp uses this as the natural primary key for the `Shop` row. |
| `primaryDomain` | `Domain!` | The merchant's online store custom domain (e.g. `shop.example.com`). |
| `url` | `URL!` | Online storefront URL. |
| `createdAt` | `DateTime!` | When the shop was created. |
| `updatedAt` | `DateTime!` | Last modification timestamp. |

#### Time zone

| Field | Type | Notes |
|---|---|---|
| `ianaTimezone` | `String!` | IANA name, e.g. `America/New_York`. **Use this** for any date math (cron schedules, reconciliation windows). |
| `timezoneAbbreviation` | `String!` | E.g. `EDT`. |
| `timezoneOffset` | `String!` | E.g. `-04:00`. |
| `timezoneOffsetMinutes` | `Int!` | E.g. `-240`. |

SyncApp's daily-metrics worker runs reconciliation windows in the shop's local time, derived from `ianaTimezone`.

#### Currency / measurement

| Field | Type | Notes |
|---|---|---|
| `currencyCode` | `CurrencyCode!` | E.g. `USD`. The shop's primary settlement currency. |
| `currencyFormats` | `CurrencyFormats!` | Templates for rendering money (e.g. `${{amount}}`). |
| `enabledPresentmentCurrencies` | `[CurrencyCode!]!` | All currencies the storefront accepts at checkout. |
| `currencySettings(first, ...)` | `CurrencySettingConnection!` | Paginated per-presentment-currency settings (rate, last update). |
| `weightUnit` | `WeightUnit!` | E.g. `GRAMS`, `POUNDS`. |
| `unitSystem` | `UnitSystem!` | `METRIC_SYSTEM` or `IMPERIAL_SYSTEM`. |

#### Plan (load-bearing for SyncApp billing)

```graphql
type ShopPlan {
  publicDisplayName: String!   # "Basic", "Plus", "Development", "Trial", "Advanced", "Agentic", ...
  partnerDevelopment: Boolean! # True if this is a partner dev store
  shopifyPlus: Boolean!        # True for Plus shops
  displayName: String!         # DEPRECATED — use publicDisplayName
}
```

**Detecting shop types from SyncApp:**

```typescript
const isDevStore = shop.plan.partnerDevelopment;
const isPlus = shop.plan.shopifyPlus || shop.plan.publicDisplayName === "Plus";
const isTrial = shop.plan.publicDisplayName === "Trial" || shop.plan.publicDisplayName === "Plus Trial";
```

SyncApp does NOT charge dev stores. The billing service checks `shop.plan.partnerDevelopment` and skips `appSubscriptionCreate` entirely. This is in addition to the `test: true` flag, which we still pass on dev-store subscriptions in case the dev-store check is later revoked or moved.

#### Contact / billing

| Field | Type | Notes |
|---|---|---|
| `email` | `String!` | Shop owner's email. Shopify uses this for system notifications. |
| `contactEmail` | `String!` | Customer-facing contact email. Different from `email`. |
| `shopAddress` | `ShopAddress!` | Address shown to buyers. |
| `shopOwnerName` | `String!` | Account owner full name. |
| `accountOwner` | `StaffMember!` | Full staff record for the account owner. |

#### Customer settings

| Field | Type | Notes |
|---|---|---|
| `customerAccounts` | `ShopCustomerAccountsSetting!` | Legacy customer-account requirement enum. |
| `customerAccountsV2` | `CustomerAccountsV2!` | New customer-account configuration object. Prefer this. |
| `customerTags(first, ...)` | `StringConnection!` | All tags applied to customer records. |
| `marketingSmsConsentEnabledAtCheckout` | `Boolean!` | Whether SMS opt-in is shown at checkout. |

#### Policies / features

| Field | Type | Notes |
|---|---|---|
| `description` | `String` | Storefront meta description. |
| `shopPolicies` | `[ShopPolicy!]!` | Refund, privacy, TOS, shipping, contact policies. |
| `features` | `ShopFeatures!` | Feature flags (storefront, branding, etc.). |
| `checkoutApiSupported` | `Boolean!` | Whether the shop supports the Checkout API (legacy). |
| `setupRequired` | `Boolean!` | Whether the shop has outstanding setup steps. |

#### Fulfillment / operations

| Field | Type | Notes |
|---|---|---|
| `fulfillmentServices` | `[FulfillmentService!]!` | Every FS app installed on this shop. |
| `paymentSettings` | `PaymentSettings!` | Accepted payment methods, etc. |
| `resourceLimits` | `ShopResourceLimits!` | Per-shop quotas (max variants, locations, etc.). |
| `shipsToCountries` | `[CountryCode!]!` | Countries the shop ships to. |
| `countriesInShippingZones` | `CountriesInShippingZones!` | Detailed shipping-zone country breakdown. |

#### Tax

| Field | Type | Notes |
|---|---|---|
| `taxesIncluded` | `Boolean!` | Whether prices include tax. |
| `taxShipping` | `Boolean!` | Whether shipping is taxed. |
| `transactionalSmsDisabled` | `Boolean!` | Whether transactional SMS is turned off. |

#### Order formatting

| Field | Type | Notes |
|---|---|---|
| `orderNumberFormatPrefix` | `String!` | Prefix before the order number. |
| `orderNumberFormatSuffix` | `String!` | Suffix after the order number. |
| `orderTags(first, ...)` | `StringConnection!` | All tags applied to orders. |
| `draftOrderTags(first, ...)` | `StringConnection!` | All tags applied to draft orders. |

#### Metafields

| Field | Type | Notes |
|---|---|---|
| `metafield(namespace, key)` | `Metafield` | Single shop-level custom field. |
| `metafields(first, ...)` | `MetafieldConnection!` | Paginated shop metafields visible to this app. |

#### Misc

| Field | Type | Notes |
|---|---|---|
| `storefrontAccessTokens(first, ...)` | `StorefrontAccessTokenConnection!` | Public Storefront API tokens. |
| `alerts` | `[ShopAlert!]!` | Active admin alert banners. |
| `allProductCategoriesList` | `[TaxonomyCategory!]!` | Up to 1000 standard product categories. |
| `entitlements` | `EntitlementsType!` | Plan-derived feature entitlements. |
| `merchantApprovalSignals` | `MerchantApprovalSignals` | Channel-app onboarding gates. |
| `navigationSettings` | `[NavigationItem!]!` | Admin nav config. |
| `search(...)` | `SearchResultConnection!` | Generic admin search. |
| `searchFilters` | `SearchFilterOptions!` | Filter options for the admin search. |
| `channelDefinitionsForInstalledChannels` | `[AvailableChannelDefinitionsByChannel!]!` | Per-channel channel definitions. |
| `availableChannelApps(first, ...)` | `AppConnection!` | Uninstalled sales channels available to install. |
| `translations` | `[Translation!]!` | Published shop-level translations. |
| `richTextEditorUrl` | `URL!` | Mobile rich-text editor URL. |

### Query

```graphql
query CurrentShop {
  shop {
    id
    name
    myshopifyDomain
    primaryDomain { url }
    ianaTimezone
    currencyCode
    weightUnit
    plan {
      publicDisplayName
      partnerDevelopment
      shopifyPlus
    }
    enabledPresentmentCurrencies
    contactEmail
    email
  }
}
```

`shop` takes no arguments — Shopify always returns the shop tied to the access token in the request.

---

## 5. CurrentAppInstallation query

The `currentAppInstallation` query returns the `AppInstallation` for the currently-authenticated app on the current shop. This is the gateway for:

- Listing **granted access scopes** (verify we have what we think we have).
- Listing **active app subscriptions** (verify plan tier).
- Reading/writing **app-owned metafields** (where SyncApp stores per-shop config the merchant shouldn't see, e.g. internal feature flags).
- Reading **one-time purchases** (we don't use these but they live here).

### Query

```graphql
query CurrentAppInstallation {
  currentAppInstallation {
    id
    launchUrl
    uninstallUrl
    accessScopes { handle }
    activeSubscriptions {
      id
      name
      status
      trialDays
      createdAt
      currentPeriodEnd
      test
      returnUrl
      lineItems {
        id
        plan {
          pricingDetails {
            __typename
            ... on AppRecurringPricing {
              price { amount currencyCode }
              interval
              discount {
                priceAfterDiscount { amount currencyCode }
                remainingDurationInIntervals
              }
            }
            ... on AppUsagePricing {
              terms
              cappedAmount { amount currencyCode }
              balanceUsed { amount currencyCode }
              interval
            }
          }
        }
      }
    }
    app {
      id
      title
      apiKey
    }
  }
}
```

### `AppInstallation` object fields

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | Installation GID. |
| `app` | `App!` | The App being installed (see Section 5.1). |
| `launchUrl` | `URL!` | URL Shopify hits to open the app. |
| `uninstallUrl` | `URL` | URL Shopify hits on uninstall. |
| `accessScopes` | `[AccessScope!]!` | Permissions granted by the merchant during install. Each has a `handle` string (e.g. `read_products`). |
| `activeSubscriptions` | `[AppSubscription!]!` | All recurring subscriptions in `ACTIVE` (or pre-active) state. **SyncApp uses this on every loader to determine plan tier.** |
| `allSubscriptions(first, ...)` | `AppSubscriptionConnection!` | Paginated history of every subscription this shop has ever created for this app. |
| `oneTimePurchases(first, ...)` | `AppPurchaseOneTimeConnection!` | One-time charges (we don't use these). |
| `credits(first, ...)` | `AppCreditConnection!` | Partner-issued credits applicable to future charges. |
| `metafield(namespace, key)` | `Metafield` | Single app-owned metafield. **Only this app can read these.** |
| `metafields(first, ...)` | `MetafieldConnection!` | Paginated app-owned metafields. |
| `revenueAttributionRecords(first, ...)` | `AppRevenueAttributionRecordConnection!` | External revenue tracking (mostly used for non-billing-API monetization). |

#### Deprecated AppInstallation fields

- `channel` (replaced by `publication` / channel app definitions)
- `publication` (still exists at the channel level; deprecated here on AppInstallation)
- `subscriptions` (use `activeSubscriptions` or `allSubscriptions`)

### What "uninstalled state" looks like

When the merchant uninstalls SyncApp, Shopify revokes our access token. Calls to `currentAppInstallation` then fail with 401 / `ShopifyAuthError`. Catch this in middleware and treat it as "session is dead, redirect to OAuth start". The actual cleanup (delete Workspace data, etc.) is triggered by the `APP_UNINSTALLED` webhook, not by polling.

### App-owned metafields pattern

The `AppInstallation.metafields` connection is the canonical place to store per-shop configuration that the merchant should not be able to edit through Shopify admin (only your app can read or write them). SyncApp uses this for:

- Feature gates (e.g. `syncapp.flag.enable_new_allocation_v2 = "true"`).
- Migration markers (e.g. `syncapp.schema_version = "2026-05-19"`).

Set with `metafieldsSet`:

```graphql
mutation SetAppMetafield {
  metafieldsSet(metafields: [{
    ownerId: "gid://shopify/AppInstallation/123"
    namespace: "syncapp"
    key: "schema_version"
    type: "single_line_text_field"
    value: "2026-05-19"
  }]) {
    metafields { id key value }
    userErrors { field message code }
  }
}
```

### 5.1 `App` object fields (reference)

The `App` object that hangs off `AppInstallation.app`:

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | App GID. |
| `apiKey` | `String!` | Public API key (= Shopify Client ID). |
| `handle` | `String` | Short ID. |
| `title` | `String!` | Display title. |
| `description` | `String` | App description. |
| `icon` | `Image!` | Icon image. |
| `banner` | `Image!` | App store banner. |
| `screenshots` | `[Image!]!` | App store screenshots. |
| `embedded` | `Boolean!` | Uses Embedded App SDK / App Bridge. |
| `published` | `Boolean!` | Published to the App Store. |
| `shopifyDeveloped` | `Boolean!` | First-party Shopify app. |
| `developerName` | `String` | Developer's name. |
| `developerType` | `AppDeveloperType!` | Developer type enum. |
| `publicCategory` | `AppPublicCategory!` | App Store category. |
| `features` | `[String!]!` | Feature tag list shown on App Store listing. |
| `requestedAccessScopes` | `[AccessScope!]!` | Scopes the app requests on install. |
| `optionalAccessScopes` | `[AccessScope!]!` | Optional scopes. |
| `availableAccessScopes` | `[AccessScope!]!` | All scopes the app *could* request. |
| `pricingDetails` | `String` | Free-form pricing text. |
| `pricingDetailsSummary` | `String!` | Short pricing summary. |
| `appStoreAppUrl` | `URL` | App Store listing URL. |
| `appStoreDeveloperUrl` | `URL` | Developer's App Store page. |
| `installUrl` | `URL` | Install URL. |
| `privacyPolicyUrl` | `URL` | Privacy policy. |
| `isPostPurchaseAppInUse` | `Boolean!` | Whether this is the post-purchase app on the shop. |
| `previouslyInstalled` | `Boolean!` | Whether the app was installed before. |
| `uninstallMessage` | `String!` | Message shown on uninstall. |
| `webhookApiVersion` | `String!` | API version used for outbound webhooks. |
| `installation` | `AppInstallation` | This app's installation on the current shop (null if not installed). |
| `failedRequirements` | `[FailedRequirement!]!` | Setup steps still pending. |
| `feedback` | `AppFeedback` | App-reported feedback shown to merchants. |
| `channels` | `ChannelConnection!` | Sales channels associated with this app. |

---

## 6. AppSubscription object

The core billing record. SyncApp creates one `AppSubscription` per `Workspace` per plan tier.

### Fields

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | Subscription GID. |
| `name` | `String!` | Plan name. SyncApp uses `"Starter"`, `"Growth"`, `"Business"`, `"Enterprise"` exactly — these match `prisma/seed.ts` plan names. |
| `status` | `AppSubscriptionStatus!` | See enum below. |
| `trialDays` | `Int!` | Free trial days from creation. Billing is delayed until `createdAt + trialDays`. |
| `createdAt` | `DateTime!` | When this subscription was first created. |
| `currentPeriodEnd` | `DateTime` | When the current billing period ends. Null in PENDING state before approval. |
| `lineItems` | `[AppSubscriptionLineItem!]!` | One or two line items (one recurring + optionally one usage). |
| `returnUrl` | `URL!` | Where Shopify sends the merchant after approval. |
| `test` | `Boolean!` | True if this is a test subscription (no actual charges). |

### `AppSubscriptionStatus` enum

| Value | Meaning |
|---|---|
| `PENDING` | Created by `appSubscriptionCreate`, awaiting merchant approval. Merchant has not yet clicked "Approve" on the confirmation URL. |
| `ACCEPTED` | Merchant approved but the subscription has not yet started (transient state, rarely seen by polling). |
| `ACTIVE` | The subscription is live and billing. **This is the state SyncApp gates plan features on.** |
| `DECLINED` | Merchant clicked "Decline" on the confirmation URL. |
| `EXPIRED` | Merchant never approved within Shopify's confirmation window (currently 2 days). Equivalent to a silent decline. |
| `CANCELLED` | Subscription was cancelled by either party (merchant cancellation, app calling `appSubscriptionCancel`, or replacement via `appSubscriptionCreate` with replacement behavior). |
| `FROZEN` | Subscription is paused by Shopify, typically because the merchant's payment method is failing. The merchant retains app access but no charges are processed until resolved. |

### `AppSubscriptionLineItem`

```graphql
type AppSubscriptionLineItem {
  id: ID!
  plan: AppPlanV2!                              # Pricing model
  usageRecords(first, after, ...): AppUsageRecordConnection!
}
```

`plan.pricingDetails` is a union of:

- `AppRecurringPricing` — flat recurring fee.
- `AppUsagePricing` — metered charges with a cap.

A line item can only be one of these (not both simultaneously). A subscription can have at most one of each type, so a typical Growth/Business subscription has 2 line items: a flat $X/month recurring + a usage line for overage charges.

### `AppRecurringPricing`

| Field | Type | Description |
|---|---|---|
| `price` | `MoneyV2!` | E.g. `{amount: "29.00", currencyCode: USD}`. |
| `interval` | `AppPricingInterval!` | `EVERY_30_DAYS` or `ANNUAL`. |
| `discount` | `AppSubscriptionDiscount` | Optional intro discount. |
| `planHandle` | `String` | App Store pricing plan handle if matched. |

#### `AppPricingInterval` enum

- `EVERY_30_DAYS` — monthly billing.
- `ANNUAL` — yearly billing. Shopify gives merchants a discount when switching from monthly to annual.

### `AppUsagePricing`

| Field | Type | Description |
|---|---|---|
| `terms` | `String!` | Merchant-visible description of what triggers usage charges, e.g. `"$0.50 per sync above 1000/month"`. Required, must be approved by merchant on creation. |
| `cappedAmount` | `MoneyV2!` | Max usage charge per billing interval. Hard limit — once hit, further `appUsageRecordCreate` calls reject. |
| `balanceUsed` | `MoneyV2!` | Total usage charges accumulated this interval. Read-only. |
| `interval` | `AppPricingInterval!` | Same enum as recurring. |

#### How usage charges work end-to-end

1. App calls `appSubscriptionCreate` with both an `AppRecurringPricing` line item AND an `AppUsagePricing` line item with `terms` and `cappedAmount`.
2. Merchant approves — both line items become active.
3. As usage accrues, the app calls `appUsageRecordCreate` with a `subscriptionLineItemId` (the usage line, not the recurring one), a `price`, a `description`, and an `idempotencyKey`.
4. Shopify rejects if `balanceUsed + price > cappedAmount`. The app must surface this back to the merchant.
5. To raise the cap, call `appSubscriptionLineItemUpdate` with the new `cappedAmount`. Merchant must approve via a fresh confirmation URL.

---

## 7. Billing mutations

### 7.1 `appSubscriptionCreate`

Creates a new recurring subscription. The subscription starts in `PENDING` until the merchant approves at the returned `confirmationUrl`.

#### Arguments

| Argument | Type | Required | Notes |
|---|---|---|---|
| `name` | `String!` | Yes | Plan name. Be exact — SyncApp uses literal `"Growth"`, `"Business"`, etc. |
| `lineItems` | `[AppSubscriptionLineItemInput!]!` | Yes | At least one line item. Max one recurring + max one usage. |
| `returnUrl` | `URL!` | Yes | Where the merchant goes after approving/declining. SyncApp routes back to `/app/billing/callback?charge_id=...`. |
| `trialDays` | `Int` | No | Free-trial duration. Defaults to 0. |
| `replacementBehavior` | `AppSubscriptionReplacementBehavior` | No | Defaults to `STANDARD`. See Section 8. |
| `test` | `Boolean` | No | Defaults to `false`. See Section 9. |

#### Input shapes

```graphql
input AppSubscriptionLineItemInput {
  plan: AppPlanInput!
}

input AppPlanInput {
  appRecurringPricingDetails: AppRecurringPricingInput
  appUsagePricingDetails: AppUsagePricingInput
}

input AppRecurringPricingInput {
  price: MoneyInput!                                     # {amount, currencyCode}
  interval: AppPricingInterval                           # EVERY_30_DAYS (default) or ANNUAL
  discount: AppSubscriptionDiscountInput                 # Optional
}

input AppUsagePricingInput {
  terms: String!                                         # Required, merchant-visible
  cappedAmount: MoneyInput!                              # {amount, currencyCode}
}

input MoneyInput {
  amount: Decimal!
  currencyCode: CurrencyCode!
}
```

#### Full example — recurring + usage combo

```graphql
mutation AppSubscriptionCreate(
  $name: String!
  $lineItems: [AppSubscriptionLineItemInput!]!
  $returnUrl: URL!
  $trialDays: Int
  $test: Boolean
  $replacementBehavior: AppSubscriptionReplacementBehavior
) {
  appSubscriptionCreate(
    name: $name
    lineItems: $lineItems
    returnUrl: $returnUrl
    trialDays: $trialDays
    test: $test
    replacementBehavior: $replacementBehavior
  ) {
    appSubscription {
      id
      status
      trialDays
      currentPeriodEnd
    }
    confirmationUrl
    userErrors {
      field
      message
    }
  }
}
```

Variables:

```json
{
  "name": "Growth",
  "returnUrl": "https://syncapp.example.com/app/billing/callback",
  "trialDays": 14,
  "test": false,
  "replacementBehavior": "STANDARD",
  "lineItems": [
    {
      "plan": {
        "appRecurringPricingDetails": {
          "price": { "amount": "49.00", "currencyCode": "USD" },
          "interval": "EVERY_30_DAYS"
        }
      }
    },
    {
      "plan": {
        "appUsagePricingDetails": {
          "terms": "$0.005 per sync operation above 5000/month",
          "cappedAmount": { "amount": "100.00", "currencyCode": "USD" }
        }
      }
    }
  ]
}
```

#### Return type — `AppSubscriptionCreatePayload`

| Field | Type | Notes |
|---|---|---|
| `appSubscription` | `AppSubscription` | The created subscription record, in `PENDING` state. |
| `confirmationUrl` | `URL` | **Redirect the merchant here immediately.** This is the Shopify-hosted approval screen. |
| `userErrors` | `[UserError!]!` | Validation errors. |

The merchant must click "Approve" on the confirmation URL before the subscription transitions to `ACTIVE`. SyncApp's billing flow is:

1. Loader checks `currentAppInstallation.activeSubscriptions`. If none matches the target plan, call `appSubscriptionCreate`.
2. Redirect to `confirmationUrl`.
3. On callback (`returnUrl` hit), re-fetch `currentAppInstallation`, confirm status is `ACTIVE`, write plan to `Workspace.planId`.

### 7.2 `appSubscriptionCancel`

Cancels an active subscription.

```graphql
mutation AppSubscriptionCancel($id: ID!, $prorate: Boolean) {
  appSubscriptionCancel(id: $id, prorate: $prorate) {
    appSubscription {
      id
      status      # Will be CANCELLED
    }
    userErrors {
      field
      message
    }
  }
}
```

#### Arguments

- `id: ID!` — subscription GID.
- `prorate: Boolean` — defaults to `false`. If `true`, Shopify issues a prorated credit to the merchant for the unused portion of the current billing period (and the partner is debited proportionally on rev-share).

When SyncApp downgrades a merchant (Growth → Starter), we typically prefer `prorate: false` because the simpler accounting outweighs the small merchant goodwill of a partial refund. Surface "your downgrade takes effect at the end of the current billing period" copy if you go that route.

#### Common scenarios

- Merchant cancels via your app's UI → `appSubscriptionCancel`.
- App-initiated downgrade → cancel old + `appSubscriptionCreate` new (or use a replacement behavior on the create call, which combines these atomically).
- Merchant uninstalls the app → Shopify auto-cancels the subscription. The `APP_UNINSTALLED` webhook fires; you don't need to call cancel.

### 7.3 `appSubscriptionLineItemUpdate`

Updates a usage line item's `cappedAmount`. Required to raise the cap mid-cycle.

```graphql
mutation AppSubscriptionLineItemUpdate(
  $id: ID!
  $cappedAmount: MoneyInput!
) {
  appSubscriptionLineItemUpdate(
    id: $id
    cappedAmount: $cappedAmount
  ) {
    appSubscription {
      id
      lineItems {
        id
        plan {
          pricingDetails {
            ... on AppUsagePricing {
              cappedAmount { amount currencyCode }
              balanceUsed { amount currencyCode }
            }
          }
        }
      }
    }
    confirmationUrl
    userErrors {
      field
      message
    }
  }
}
```

- `id` is the `AppSubscriptionLineItem.id` (the **line item**, not the subscription). Find it via `currentAppInstallation.activeSubscriptions[].lineItems[].id`.
- The mutation returns a fresh `confirmationUrl`. **The new cap is not in effect until the merchant approves.**
- This is also how you switch between usage-pricing tiers (e.g. doubling the cap when a merchant upgrades).

Use cases:

- Raise the cap because the merchant has hit it.
- Lower the cap because the merchant downgraded (rarely done — typically you cancel + recreate).

### 7.4 `appSubscriptionTrialExtend`

Extends an existing subscription's trial. Useful for support/retention.

```graphql
mutation AppSubscriptionTrialExtend($id: ID!, $days: Int!) {
  appSubscriptionTrialExtend(id: $id, days: $days) {
    appSubscription {
      id
      trialDays
      status
    }
    userErrors {
      field
      message
      code
    }
  }
}
```

- `days` must be `> 0` and `<= 1000`.
- The new `trialDays` is **additive** — it extends the existing trial window, not replaces it.
- Works on `PENDING` and `ACTIVE` subscriptions. If the subscription's trial has already ended and billing started, extending the trial creates a credit for the additional days.

### 7.5 `appUsageRecordCreate`

Records a single usage event against a usage line item. Use for overage billing.

```graphql
mutation AppUsageRecordCreate(
  $subscriptionLineItemId: ID!
  $price: MoneyInput!
  $description: String!
  $idempotencyKey: String
) {
  appUsageRecordCreate(
    subscriptionLineItemId: $subscriptionLineItemId
    price: $price
    description: $description
    idempotencyKey: $idempotencyKey
  ) {
    appUsageRecord {
      id
      price { amount currencyCode }
      description
      createdAt
    }
    userErrors {
      field
      message
    }
  }
}
```

#### Arguments

- `subscriptionLineItemId: ID!` — must reference an `AppSubscriptionLineItem` whose plan is `AppUsagePricing`. Recurring-only line items reject.
- `price: MoneyInput!` — the charge amount for this single event. Currency must match the line item's `cappedAmount.currencyCode`.
- `description: String!` — what was billed for. Shown to the merchant on their Shopify invoice.
- `idempotencyKey: String` — optional but **strongly recommended**. Max 255 chars. SyncApp uses `${workspaceId}:${eventType}:${eventId}` to dedupe retries.

#### Rejection cases

- **Cap exceeded** — `balanceUsed + price > cappedAmount`. Error: `"Failed to create usage charge"`. Surface "your usage cap has been reached" to the merchant and offer to raise the cap via `appSubscriptionLineItemUpdate`.
- **Currency mismatch** — line item is USD, price is CAD. Reject.
- **Concurrent same-key request** — `"Another request for the same idempotency key is being processed"`. Retry with backoff.
- **Idempotency key already used** — returns the same record (idempotent semantics — this is the success case for replay).

### 7.6 `appPurchaseOneTimeCreate`

Creates a one-time charge. SyncApp does not use this — included for completeness because workspace operators may eventually want one-time charges (e.g. data import packages).

```graphql
mutation AppPurchaseOneTimeCreate(
  $name: String!
  $price: MoneyInput!
  $returnUrl: URL!
  $test: Boolean
) {
  appPurchaseOneTimeCreate(
    name: $name
    price: $price
    returnUrl: $returnUrl
    test: $test
  ) {
    appPurchaseOneTime {
      id
      name
      price { amount currencyCode }
      status
      createdAt
    }
    confirmationUrl
    userErrors {
      field
      message
    }
  }
}
```

Workflow identical to `appSubscriptionCreate`: returns `confirmationUrl`, redirect merchant, await approval webhook.

#### `AppPurchaseOneTime` object

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | Purchase GID. |
| `name` | `String!` | Display name. |
| `price` | `MoneyV2!` | Charge amount. |
| `status` | `AppPurchaseStatus!` | See enum below. |
| `createdAt` | `DateTime!` | Creation timestamp. |
| `test` | `Boolean!` | Test mode flag. |

#### `AppPurchaseStatus` enum

- `PENDING` — Awaiting merchant approval. Charged only after approval. If payment fails, status stays PENDING.
- `ACTIVE` — Approved and activated. Charged to the merchant, paid to the partner.
- `DECLINED` — Merchant declined.
- `EXPIRED` — Merchant didn't accept within 2 days of creation.
- `ACCEPTED` — Deprecated, no longer used in new flows. Treat as alias of `ACTIVE` if encountered on legacy data.

---

## 8. Plan replacement behavior

`AppSubscriptionReplacementBehavior` controls what happens when `appSubscriptionCreate` runs while another active subscription exists for the same app.

### Enum values

#### `STANDARD` (default)

Smart replacement. Cancels the existing subscription immediately and activates the new one immediately, UNLESS one of three downgrade scenarios applies, in which case the change is deferred to the start of the next billing cycle:

1. Replacing an annual subscription with another annual subscription of **lower** value in the **same currency**.
2. Switching from annual to monthly billing in the same currency.
3. The subscriptions are identical except for discount adjustments.

This is what SyncApp uses by default for upgrades. Upgrade = immediate. Downgrade = deferred to end of period (merchant keeps what they paid for).

#### `APPLY_IMMEDIATELY`

Always replaces immediately, no matter what. Cancels the current subscription and activates the new one right now. Pro-rated credits are issued automatically when the new plan is cheaper.

Use case: you want to enforce an immediate switch (e.g. plan migration during a price restructure, retention save where you offer a downgrade effective immediately).

#### `APPLY_ON_NEXT_BILLING_CYCLE`

Always defers to the start of the next billing cycle. The current subscription continues until its `currentPeriodEnd`, then the new subscription activates.

**Exception:** If the currencies differ between old and new subscriptions, the deferral is bypassed and the new one activates immediately. (Shopify can't proration across currencies cleanly.)

Use case: you explicitly want the merchant to finish out their paid period before the change applies.

### Pro-ration

- `APPLY_IMMEDIATELY` (and `STANDARD` on upgrades) issues a prorated credit for unused time on the cancelled subscription.
- Deferred replacements (any path that waits for `currentPeriodEnd`) do not produce a credit because no unused time was lost.
- The partner is debited for the credit on the rev-share split.

### What fires APP_SUBSCRIPTIONS_UPDATE

- The instant the merchant approves on the confirmation URL.
- When the subscription transitions out of trial into billing.
- When status changes (PENDING → ACTIVE, ACTIVE → CANCELLED, ACTIVE → FROZEN, etc.).
- When `appSubscriptionLineItemUpdate` approval completes and the cap takes effect.

SyncApp's webhook handler should NOT assume the new status — always re-fetch with `currentAppInstallation` to get the fresh state. Webhooks are unordered.

---

## 9. Test mode billing

Pass `test: true` to `appSubscriptionCreate` (or `appPurchaseOneTimeCreate`) to create a real subscription that does NOT charge the merchant.

### What test mode does

- The subscription goes through every state transition normally (PENDING → confirmation URL → ACTIVE).
- All mutations (cancel, line-item update, usage record) work normally.
- All webhooks fire normally (`APP_SUBSCRIPTIONS_UPDATE`).
- **No actual money moves.** The merchant is not charged, the partner is not paid.

### When test mode is required

- **Development stores.** Shopify rejects non-test subscriptions on `partnerDevelopment: true` shops.
- **Test charges on production stores during QA.** A merchant on a live shop will see the subscription on their Billing page but won't be billed.

### SyncApp's pattern

```typescript
async function createSubscription(shop, planConfig) {
  const isTest = shop.plan.partnerDevelopment === true
    || process.env.SHOPIFY_BILLING_TEST_MODE === "true";

  return await admin.graphql(APP_SUBSCRIPTION_CREATE, {
    variables: {
      name: planConfig.name,
      lineItems: planConfig.lineItems,
      returnUrl: planConfig.returnUrl,
      trialDays: planConfig.trialDays,
      test: isTest,
      replacementBehavior: "STANDARD",
    },
  });
}
```

In production, `SHOPIFY_BILLING_TEST_MODE` is unset. In staging and dev, it defaults to `"true"`. Never log the value of this flag with PII — it's not a secret, but it gates billing behavior and surfacing it in support logs has been confusing.

---

## 10. Trial behavior

### When trial counts from

`trialDays` is measured from `AppSubscription.createdAt` — the moment the subscription is created via `appSubscriptionCreate`, not from when the merchant approves.

If a merchant clicks the confirmation URL 8 hours after the API call, they've already burned 8 hours of their trial. In practice this is fine — confirmation URLs are usually clicked immediately and Shopify expires unapproved subscriptions after 2 days anyway.

### When does it transition to ACTIVE

The subscription transitions to `ACTIVE` as soon as the merchant approves on the confirmation URL. During the trial period:

- `status: ACTIVE`
- `trialDays: <original value>`
- `currentPeriodEnd: <createdAt + 30 days>` (the period clock starts immediately; trial just delays the first charge)

### When is the first charge

At `createdAt + trialDays`, Shopify processes the first charge for the recurring line item. Up until that point, the subscription is active but no money has moved.

If the trial extends past `currentPeriodEnd` (e.g. 60-day trial on a monthly plan), the first charge is at trial end and the period clock then resets from that point.

### Trial extension caveats

- `appSubscriptionTrialExtend` adds days to the existing trial. If the trial has already ended and billing has begun, calling extend with N days creates a credit for N days' worth of the recurring price, effectively giving the merchant N free days going forward.
- Trial extension cannot exceed 1000 days total.
- Extensions work in PENDING, ACTIVE, and FROZEN states (not on CANCELLED / DECLINED / EXPIRED).

---

## 11. Subscription state transitions

### State diagram

```
                   appSubscriptionCreate
                          |
                          v
                      [PENDING]
                  /              \
       merchant approves    merchant declines OR
                          2 days elapse with no action
                  |                        |
                  v                        v
        [ACCEPTED -> ACTIVE]         [DECLINED]  or  [EXPIRED]
            (transient,
             rarely seen
             by polling)
                  |
                  |
   +--------------+--------------+
   |              |              |
   v              v              v
appSubscription  payment      appSubscriptionCreate (replacement)
Cancel called    fails       OR merchant uninstalls app
   |              |              |
   v              v              v
[CANCELLED]   [FROZEN]      [CANCELLED]
                  |
                  | merchant updates payment, charge retries succeed
                  v
              [ACTIVE]
```

### What each status means for SyncApp's feature gating

| Status | Treat as paid? | UI hint |
|---|---|---|
| `PENDING` | No | "Approve your subscription to continue." Surface the `confirmationUrl` (we cache this in `Workspace.pendingConfirmationUrl` until the webhook clears it). |
| `ACCEPTED` | Yes (transient) | Same as ACTIVE. Rarely seen — usually polls catch the state as ACTIVE directly. |
| `ACTIVE` | Yes | Full feature access. |
| `DECLINED` | No | "You declined the subscription. Re-subscribe to continue." Show the upgrade CTA. |
| `EXPIRED` | No | "Your subscription request expired. Re-subscribe to continue." Same CTA as DECLINED. |
| `CANCELLED` | No | "Your subscription was cancelled. Re-subscribe to continue." Same CTA. |
| `FROZEN` | **Yes**, in SyncApp's model. | "Your payment method failed. Please update it in Shopify admin to avoid service interruption." We keep the merchant on their plan — the merchant has not chosen to leave, Shopify just can't bill them temporarily. Cutting them off here causes churn. |

### What fires `APP_SUBSCRIPTIONS_UPDATE`

- Merchant approval/decline on a confirmation URL.
- Trial → billing transition.
- Cancellation (by either side).
- Freeze / unfreeze (payment failures and recoveries).
- `appSubscriptionLineItemUpdate` approval.
- Plan replacement (via `appSubscriptionCreate` with replacement behavior).

The webhook payload includes the subscription's new `status` and `admin_graphql_api_id`. SyncApp's handler:

1. Parses the GID.
2. Re-queries `currentAppInstallation` for the canonical state (webhooks can be out of order).
3. Maps the canonical state to a `Workspace.planId` via the plan name match (`name` field on the subscription).
4. Writes the new plan tier to the workspace.
5. Toasts the merchant if a downgrade just took effect.

---

## 12. Markets (overview, skim)

Markets enable merchants to sell internationally with localized pricing, currency, language, and domain configuration per region.

### `Market` object

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | Market GID. |
| `handle` | `String!` | Short, merchant-editable identifier. |
| `name` | `String!` | Internal display name (not customer-visible). |
| `status` | `MarketStatus!` | Active / inactive. **Replaces the deprecated `enabled` field.** |
| `currencySettings` | `MarketCurrencySettings` | Currency display rules. |
| `conditions` | `MarketConditions` | Visitor-matching criteria (country, IP, etc.). |
| `catalogs(first, ...)` | `MarketCatalogConnection!` | Catalogs (products + pricing) assigned to this market. |
| `priceList` | `PriceList` | Deprecated — replaced by catalogs. |
| `primary` | `Boolean!` | Deprecated. |
| `enabled` | `Boolean!` | Deprecated — use `status`. |
| `regions` | `MarketRegionConnection!` | Deprecated — use `conditions`. |
| `webPresence` | `MarketWebPresence` | Deprecated — moved to web-presence-specific objects. |

### `markets` query

```graphql
query Markets {
  markets(first: 10) {
    nodes {
      id
      name
      handle
      status
      currencySettings {
        baseCurrency { currencyCode }
        localCurrencies
      }
    }
  }
}
```

Arguments: `first`, `last`, `after`, `before`, `query` (filter syntax), `type` (MarketType), `sortKey` (`MarketsSortKeys`, default `NAME`), `reverse`.

### SyncApp relevance

Mostly none. Markets affect storefront pricing, not inventory. SyncApp's allocation engine doesn't care about markets — we allocate by store/location and don't model market-level demand.

The one case where Markets matter: if a merchant uses a separate location per market for regional fulfillment (e.g. one location in EU for EUR-priced market, one in US for USD), SyncApp imports both locations and treats them independently. No special Markets-aware logic is needed.

Required scopes: `read_markets` for queries, `write_markets` for mutations.

---

## 13. Channels / Publications (skim)

Shopify deprecated `Channel` in favor of `Publication`. New code should use `Publication`.

### `Publication` object

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | Publication GID. |
| `name` | `String!` | **Deprecated** at this layer. Read from `app.title` if you need a display name. |
| `app` | `App!` | **Deprecated** at this layer. Read via channel definitions. |
| `hasCollection(id: ID!)` | `Boolean!` | Whether a given collection is published. |
| `products(first, ...)` | `ProductConnection!` | Paginated list of products published here. |
| `productPublicationsV3(first, ...)` | `ResourcePublicationConnection!` | Publication records (with publish state, scheduled dates). |
| `supportsFuturePublishing` | `Boolean!` | Whether `publishDate` (future-scheduled publish) is supported. |
| `autoPublish` | `Boolean!` | Whether new products are auto-published to this publication. |
| `catalog` | `Catalog` | Associated catalog (pricing/availability rules). |

### `publications` query

```graphql
query AllPublications {
  publications(first: 25) {
    nodes {
      id
      autoPublish
      supportsFuturePublishing
      catalog {
        id
        ... on AppCatalog {
          status
        }
      }
    }
  }
}
```

Arguments: `first`, `last`, `after`, `before`, `catalogType` (CatalogType filter), `reverse`.

### `publishablePublish` mutation

Publishes a `Publishable` (Product, Collection, etc.) to one or more publications.

```graphql
mutation PublishProduct($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    publishable {
      resourcePublicationsCount { count }
    }
    shop {
      id
    }
    userErrors {
      field
      message
    }
  }
}
```

Variables:

```json
{
  "id": "gid://shopify/Product/123456",
  "input": [
    { "publicationId": "gid://shopify/Publication/111", "publishDate": null },
    { "publicationId": "gid://shopify/Publication/222", "publishDate": "2026-06-01T00:00:00Z" }
  ]
}
```

- `publishDate` is optional. If provided, it must be a future timestamp and the publication's `supportsFuturePublishing` must be `true` (online stores support it; many app channels don't).
- Requires `write_publications` scope.
- Products with `requiresSellingPlan: true` (subscription-only) can only target online stores.

### `Channel` object (deprecated, here for legacy code)

| Field | Type | Notes |
|---|---|---|
| `id` | `ID!` | |
| `name` | `String!` | |
| `handle` | `String!` | |
| `app` | `App!` | The underlying app powering the channel. |
| `supportsFutureMutations` / `supportsFuturePublishing` | `Boolean!` | Whether scheduled mutations are supported. |
| `productPublicationsV3` | `ResourcePublicationConnection!` | **Deprecated** in favor of `Publication.productPublicationsV3`. |

### SyncApp relevance

SyncApp does not publish products and does not query publications. Channels are documented here for awareness because:

- If we ever support per-channel inventory routing (e.g. "only allocate to the Online Store channel, ignore POS"), the Publication object is the entry point.
- The `AppInstallation.channel` / `AppInstallation.publication` deprecated fields are sometimes still surfaced in legacy responses — ignore them.

---

## 14. SyncApp-specific patterns

### Plan tiering via `AppSubscription`

SyncApp has four plan tiers seeded in `prisma/seed.ts`: **Starter**, **Growth**, **Business**, **Enterprise**. The plan name on the subscription MUST exactly match one of these strings.

#### Plan creation flow

1. Merchant clicks "Upgrade to Growth" in `/app/billing`.
2. Loader calls `appSubscriptionCreate` with `name: "Growth"`, the Growth pricing line items (a `$X/month` recurring + a usage line for overage), `trialDays: 14`, `returnUrl` pointing back to `/app/billing/callback`, `test: shop.plan.partnerDevelopment`.
3. Loader caches `confirmationUrl` on `Workspace.pendingConfirmationUrl` and returns a redirect.
4. Merchant approves on Shopify-hosted screen. Shopify redirects to `returnUrl?charge_id=...`.
5. `APP_SUBSCRIPTIONS_UPDATE` webhook fires with the new ACTIVE status.
6. Webhook handler re-queries `currentAppInstallation`, finds the new ACTIVE subscription with `name: "Growth"`, maps to `Plan` row, writes `Workspace.planId`.

#### Plan switching

Use `replacementBehavior: "STANDARD"` on the new `appSubscriptionCreate`. SyncApp does not call `appSubscriptionCancel` directly on the old one — Shopify handles replacement atomically.

#### Plan-tier-dependent features

The webhook handler is the source of truth. All loaders read `Workspace.planId` and join to the `Plan` row for feature flags. Never read `AppSubscription` inline in a UI loader — too slow, and webhooks are guaranteed to flow eventually.

### `APP_SUBSCRIPTIONS_UPDATE` webhook handling

```typescript
// Pseudocode for the normalize worker
async function handleAppSubscriptionsUpdate(workspaceId: string, payload: any) {
  // Re-query for canonical state — webhooks can be out of order
  const installation = await admin.graphql(CURRENT_APP_INSTALLATION_QUERY);
  const active = installation.activeSubscriptions[0]; // SyncApp only allows one active subscription

  if (!active) {
    // No active subscription — downgrade to Starter (free tier)
    await db.workspace.update({
      where: { id: workspaceId },
      data: { planId: STARTER_PLAN_ID, pendingConfirmationUrl: null },
    });
    return;
  }

  const plan = await db.plan.findFirst({ where: { name: active.name } });
  if (!plan) {
    log.error({ workspaceId, subscriptionName: active.name }, "Unknown plan name on subscription");
    return;
  }

  await db.workspace.update({
    where: { id: workspaceId },
    data: { planId: plan.id, pendingConfirmationUrl: null },
  });
}
```

Key points:

- The webhook payload itself is NOT the source of truth. Re-query `currentAppInstallation` to get the canonical, current state.
- Treat FROZEN as paid (Shopify is mid-recovery, the merchant didn't choose to leave).
- Clear `pendingConfirmationUrl` once a subscription is ACTIVE — the UI banner is no longer needed.

### `LOCATIONS_CREATE` / `LOCATIONS_UPDATE` / `LOCATIONS_DEACTIVATE` webhooks

Each fires when a merchant adds, edits, or deactivates a location. SyncApp's handler:

1. Receive webhook with `payload.id` (numeric REST resource ID).
2. Build the GID: `gid://shopify/Location/${payload.id}`.
3. Query the location: `query LocationFresh($id: ID!) { location(id: $id) { ... } }` with `includeLegacy` semantics implicit on a single-record fetch.
4. Upsert into `Location` table, including `isFulfillmentService` and `isActive`.
5. Mark the workspace dirty so the next dirty-flush re-evaluates allocations (some locations participate in pools).

If the location is FS-managed, the upsert still happens but the location is flagged as read-only — sync writes will skip it (see Section 2).

### `includeLegacy: true` is non-negotiable on imports

Every code path that fetches `locations(...)` for a workspace MUST pass `includeLegacy: true`. Code review should flag any `locations(...)` query that doesn't.

Find all such queries with:

```bash
rg --type=ts 'locations\s*\(' app/
```

There should be exactly one query string template in `app/graphql/queries.ts` for the bulk location import, and it must include `includeLegacy: true`.

### Self-push suppression also applies to mutations driven by billing changes

If a plan change triggers a recompute of allocations (because the new plan unlocks more pool participants), the resulting inventory writes still need `markAsSelfPush()` before each `inventorySetQuantities` call. Don't skip this just because the trigger was a billing event.

### Test mode for development stores

Always pass `test: shop.plan.partnerDevelopment` on `appSubscriptionCreate`. Dev stores reject `test: false` subscriptions outright.

### Workspace plan name match is exact

Plans seeded in `prisma/seed.ts`:

- `Starter` (free tier — no `appSubscriptionCreate` needed)
- `Growth`
- `Business`
- `Enterprise`

The `AppSubscription.name` field must match one of `Growth`, `Business`, `Enterprise` exactly (or be absent for the Starter free tier). Typos here silently break plan resolution because the webhook handler's `findFirst({ name })` returns null and the workspace stays on whatever plan it had.

---

## Appendix: Quick reference — which mutation for which task

| Task | Mutation |
|---|---|
| New merchant subscribes to a plan | `appSubscriptionCreate` |
| Merchant upgrades plan | `appSubscriptionCreate` with `replacementBehavior: STANDARD` |
| Merchant downgrades plan | `appSubscriptionCreate` with `replacementBehavior: STANDARD` (downgrade is deferred to end of period) |
| Merchant cancels their plan | `appSubscriptionCancel` |
| Raise/lower usage cap | `appSubscriptionLineItemUpdate` |
| Bill for usage event | `appUsageRecordCreate` |
| Extend a trial | `appSubscriptionTrialExtend` |
| Charge one-time fee | `appPurchaseOneTimeCreate` |
| Add a merchant location | `locationAdd` (SyncApp doesn't do this) |
| Edit a location | `locationEdit` (SyncApp doesn't do this) |
| Activate / deactivate a location | `locationActivate` / `locationDeactivate` (SyncApp doesn't do this; both require `@idempotent`) |
| Publish a product to a channel | `publishablePublish` |

## Appendix: Quick reference — which query for which task

| Task | Query |
|---|---|
| Get shop currency, timezone, plan | `shop { ... }` |
| Get my app's current installation | `currentAppInstallation { ... }` |
| List all locations (including FS) | `locations(includeLegacy: true, includeInactive: true) { ... }` |
| Get one location by ID | `location(id: $id) { ... }` |
| List delivery-profile-eligible locations | `locationsAvailableForDeliveryProfilesConnection { ... }` |
| List shop markets | `markets { ... }` |
| List publications | `publications { ... }` |
