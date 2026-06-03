# Section 5: Category-Specific Requirements — Decision Matrix

Section 5 has 11 categories. Each only applies if your app fits that category. Get the category determination right first — getting it wrong is a top reason for review delay.

## Decision matrix

| Category | Apply if your app... | Section to read |
|---|---|---|
| **Online store** (5.1) | Embeds in the storefront theme via theme app blocks/extensions | 5.1.1 – 5.1.5 |
| **Payment** (5.2) | Provides a payment gateway integration via Payments API | 5.2.1 – 5.2.15 |
| **Payment facilitator** (5.3) | Facilitates payments but doesn't process them directly (e.g., onboarding to a gateway) | 5.3.1 – 5.3.3 |
| **Purchase option** (5.4) | Adds subscriptions, pre-orders, deferred payments, deposits | 5.4.1 – 5.4.20 |
| **Product sourcing** (5.5) | Dropshipping, print-on-demand, supplier sourcing | 5.5.1 – 5.5.5 |
| **Checkout customization** (5.6) | Checkout UI extensions (banner, info, recommendation, validation) | 5.6.1 – 5.6.9 |
| **Sales channel** (5.7) | Lists/sells the merchant's products on an external marketplace | 5.7.1 – 5.7.18 |
| **Post purchase** (5.8) | Post-purchase upsell / thank-you page extensions | 5.8.1 – 5.8.10 |
| **Mobile app builders** (5.9) | Generates a native mobile app for the merchant | 5.9.1 – 5.9.3 |
| **Donation** (5.10) | Collects donations from customers at checkout | 5.10.1 – 5.10.7 |
| **Blockchain / NFT** (5.11) | Sells NFTs or interacts with on-chain assets | 5.11.1 – 5.11.13 |

## "Regular app" — none of the above

A utility / inventory / analytics / B2B / wholesale / catalog-management / data-export / shipping-rule app is a **regular app** and Section 5 is N/A entirely. Sections 1–4 still apply.

If you check Section 5 categories that don't apply, the Partner Dashboard form forces extra fields you can't fill — submission fails validation. Pick "Regular app" (technically: don't enable Sales Channel and don't tag with payment/subscription/donation categories).

## Common miscategorization traps

- **"My app updates inventory after a sale, am I a Payment app?"** — No. Payment apps process the actual money at checkout. Webhook listeners that update inventory after order/create are regular apps.
- **"My app shows a banner in checkout"** — Yes, you ARE a Checkout customization app and fall under 5.6.
- **"My app helps the merchant find suppliers"** — Yes, Product sourcing 5.5.
- **"My app provides a checkout-time discount stack"** — Checkout customization 5.6, possibly Purchase option 5.4 if the discount is recurring.
- **"My app shows the order on a Discord server"** — Regular app, not a Sales channel. (Sales channel = the customer can buy through the channel.)

## When you ARE category-specific

Read the section in detail in the source page (don't trust summaries; the category rules are detailed and update faster than other sections). Common patterns within each:

- **5.2 Payment apps**: Must include test mode, must use Polaris, must NOT be embedded, must use single Checkout UI extension with permitted targets. Multi-currency required.
- **5.4 Purchase option / Subscription**: Customer must be able to modify payment method, cancel from customer portal, and see all subscriptions in one place. Selling-plan rules are strict.
- **5.6 Checkout customization**: No countdown timers, no third-party ads, must reflect price changes accurately, must use approved component types.
- **5.7 Sales channel**: Must use ResourceFeedback API, must allow account disconnect, must use Polaris cards in publishing section, must add `read_only_own_orders` scope.
- **5.10 Donation**: Theme app block delivery only. Must provide proof of donation. Charitable status verification required.
- **5.11 Blockchain**: NFT-specific rules are highly restrictive — only primary sales, no fungible token sales, no on-chain PII.

Re-fetch the source page when working on a category-specific app — Shopify updates these sections frequently as they tighten policy.
