# Privacy, GDPR & Protected Customer Data

This is the part of compliance that has the most teeth: rejection AND a separate "data protection review" if you get it wrong.

## The three mandatory GDPR webhooks

Every published Shopify app, regardless of whether it touches customer data, must implement HMAC-verified handlers for these three topics and return 200 OK:

| Topic | Trigger | What it means | Required action |
|---|---|---|---|
| `customers/data_request` | Customer files a GDPR data-access request | "Tell me what data you have about me" | Within 30 days, email the merchant a packaged copy of any data you hold about that customer. Even if you hold nothing, acknowledge the request. |
| `customers/redact` | 10 days after a customer is deleted in Shopify Admin | "Delete this customer's data" | Erase any data tied to that customer's `customer_id` from your DB. If you hold nothing, no-op + return 200. |
| `shop/redact` | 48 hours after a shop is uninstalled | "Delete EVERYTHING for this shop" | Cascade-delete all rows scoped to that `shop_id`. Logs, sync history, snapshots, tokens — everything. |

### Implementation pattern (from `app/routes/webhooks.tsx`)

```ts
const { topic, shop, payload } = await authenticate.webhook(request);

switch (topic) {
  case "CUSTOMERS_DATA_REQUEST": {
    log.info({ topic, shop }, "GDPR customers/data_request received");
    // If you store customer data, package + email merchant within 30 days.
    // If not (you'd be at PCD Level 0), no action required besides acknowledging.
    return new Response(null, { status: 200 });
  }

  case "CUSTOMERS_REDACT": {
    const customerId = payload?.customer?.id;
    log.info({ topic, shop, customerId }, "GDPR customers/redact received");
    // Delete any rows scoped to this customer_id. If you don't store any, no-op.
    return new Response(null, { status: 200 });
  }

  case "SHOP_REDACT": {
    log.info({ topic, shop }, "GDPR shop/redact received — full data deletion");
    // Cascade-delete EVERYTHING for this shop. Use Prisma cascading FKs
    // or an explicit transaction across every shop-scoped table.
    const shopRow = await db.shop.findUnique({ where: { myshopifyDomain: shop } });
    if (shopRow) {
      await db.shop.delete({ where: { id: shopRow.id } });
      // FK cascades handle the rest if schema has onDelete: Cascade on every relation.
    }
    return new Response(null, { status: 200 });
  }
}
```

### TOML configuration

```toml
[webhooks.privacy_compliance]
customer_data_request_url = "/webhooks"
customer_deletion_url = "/webhooks"
shop_deletion_url = "/webhooks"
```

The `[webhooks.privacy_compliance]` block in `shopify.app.toml` registers the URLs with Shopify. Without this, your app fails review even if the handler code exists.

## Protected customer data Level classification

Shopify classifies apps based on what customer data they ACCESS (not just what they use):

| Level | Trigger | Review impact |
|---|---|---|
| **Level 0** | No customer data accessed at all | No additional review |
| **Level 1** | Access to customer-adjacent data EXCLUDING name, address, phone, email | Public apps require review; custom apps don't |
| **Level 2** | Access to customer name, address, phone, OR email fields | Public apps require review **plus** data protection review |

### Why this is subtle

The classification is based on what your **scopes** could access, AND what your code does with the payload.

- `read_orders` scope delivers webhook payloads that include `customer.email`, `customer.first_name`, `shipping_address.*`, `billing_address.*` etc.
- If your code reads those fields → Level 2
- If your code discards those fields and only persists `total_price`, `subtotal_price`, `financial_status`, line item GIDs → **Level 0** (no customer fields ever touch your DB)

### Where to declare

Partner Dashboard → App distribution settings → "Protected customer data access" → declare level and provide justification text.

### Justification text examples that work

**Level 0**: *"App processes order metadata (totals, line item GIDs, fulfillment status) for inventory and velocity analytics. No customer fields (name, email, address, phone) are persisted or processed. Order webhook payloads are filtered before storage."*

**Level 1 (orders + cart, no PII)**: *"App computes sales velocity and restock forecasts using order line items, quantities, prices, and timestamps. Customer-level joins are not performed."*

**Level 2 (CRM apps, marketing apps)**: *"App emails customers on the merchant's behalf for low-stock notifications. Customer email addresses are stored encrypted at rest and deleted on customer/redact webhook."*

### Level 1 requirements (must implement if Level 1)

1. **Data minimization** — store only what's needed for stated functionality
2. **Merchant transparency** — disclose in privacy policy what you process and why
3. **Purpose limitation** — only use data for stated purposes
4. **Consent respect** — honor customer consent decisions
5. **Opt-out support** — honor opt-outs where legally required
6. **Automated decision opt-out** — for algorithmic decisions with legal/significant effects
7. **Data agreements** — privacy/DPA with merchants
8. **Retention policies** — bounded retention windows
9. **Encryption at rest + in transit** — AES-256 / TLS 1.2+

### Level 2 requirements (Level 1 + these)

10. **Backup encryption** — backups themselves encrypted
11. **Environment separation** — test/prod data isolated
12. **Data loss prevention** — technical controls against extraction
13. **Access limitation** — staff access restricted
14. **Strong authentication** — staff accounts use strong passwords + ideally MFA
15. **Access logging** — audit log of who accessed what when
16. **Incident response policy** — documented breach response

## Privacy policy requirements

Every public app must have a privacy policy linked from the App Store listing. Required disclosures:

- Data collection methods (which Shopify APIs you call)
- Direct merchant data collected (e.g., app settings, configurations)
- Direct customer data collected (cookies, tracking — usually "none")
- Purpose of each data use beyond core functionality
- Retention periods
- Sub-processors (hosting, email, analytics, error tracking)
- Whether you're EU-established (affects GDPR controller/processor status)
- Contact method for inquiries

### Sub-processor table — the part that gets missed

Privacy policies must list every third party that touches the data, with purpose and scope. Common entries:

| Sub-processor | Purpose | Data shared |
|---|---|---|
| Your cloud / VDS host (AWS, GCP, Contabo, etc.) | Hosting + database + Redis | All workspace data (encrypted at rest) |
| Email provider (SendGrid, Postmark, your own SMTP) | Transactional alerts | Recipient email + alert content |
| TLS provider (Let's Encrypt / cert provider) | Cert issuance | Domain name only |
| Error tracking (Sentry, etc.) | Error reporting | Error context, sanitized |
| Webhook receiver integrations (Slack, Teams, Discord, custom) | Alert delivery to merchant's channels | Alert content, at merchant's direction (these are integrations not sub-processors) |

Distinguish carefully: a Slack webhook URL the merchant configured is THEIR integration, not your sub-processor. Document it as such.

## Customer data retention

If you store any customer-adjacent data:

- Define a retention window in your privacy policy (e.g., "sync logs 30 days, raw webhook events 7 days")
- Implement automatic deletion via scheduled jobs
- On `shop/redact`, delete everything within the 48-hour Shopify SLA (we delete within minutes via the webhook)
- On `customers/redact`, delete that customer's records within 30 days (we'd prefer near-immediate)
