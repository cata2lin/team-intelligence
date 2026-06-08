# Shopify — ARONA Assistant (custom app)

A Shopify **custom app** (full Admin API permissions) used by this assistant
control plane to query/mutate Shopify directly, **independent** of the
per-brand custom apps tracked in `metrics.shopify_stores`.

## Why a separate app?

- The per-brand apps in `metrics.shopify_stores` are scoped narrowly for the
  metrics ingestion pipeline.
- ARONA Assistant is broader: it can be installed on any of our stores when we
  need ad-hoc reads/writes (catalog edits, draft orders, inventory pokes,
  one-off audits) without touching the metrics OAuth grants.

## Currently installed on

| Store | myshopify domain | Public domain | Currency | Notes |
|---|---|---|---|---|
| **Lab Noir** | `31k0py-bi.myshopify.com` | `labnoir.ro` | RON | Initial install |
| **Esteban** | `6f9e22-9d.myshopify.com` | `esteban.ro` | RON | Used by [skills/placing-ugc-orders.md](../skills/placing-ugc-orders.md) |
| **George Talent** | `ix5bxc-hr.myshopify.com` | `georgetalent.ro` | RON | Used by [skills/placing-ugc-orders.md](../skills/placing-ugc-orders.md) |
| **Nubra** | `bmuwvv-jy.myshopify.com` | `nubra.ro` | RON | Used by [skills/placing-ugc-orders.md](../skills/placing-ugc-orders.md) |

> Add a row above each time the app is installed on a new store. The
> client_id/secret are app-level (single pair); only the domain changes.

## Credentials

Stored in `secrets/credentials.env` only:

```
SHOPIFY_ARONA_CLIENT_ID=...
SHOPIFY_ARONA_CLIENT_SECRET=shpss_...
SHOPIFY_ARONA_API_VERSION=2026-04
SHOPIFY_ARONA_LABNOIR_DOMAIN=31k0py-bi.myshopify.com
SHOPIFY_ARONA_ESTEBAN_DOMAIN=6f9e22-9d.myshopify.com
SHOPIFY_ARONA_GT_DOMAIN=ix5bxc-hr.myshopify.com
SHOPIFY_ARONA_NUBRA_DOMAIN=bmuwvv-jy.myshopify.com
```

This is a **Shopify-managed custom app** (the new model, not legacy
“Develop apps”), so it accepts the OAuth `client_credentials` grant on
`/admin/oauth/access_token`. The minted Admin API token is short-lived
(`expires_in: 86399` = ~24 h, prefix `shpat_...`); mint it per run, don’t
persist it.

## Mint an Admin API access token

```bash
set -a; source secrets/credentials.env; set +a
curl -s -X POST "https://${SHOPIFY_ARONA_LABNOIR_DOMAIN}/admin/oauth/access_token" \
  -H 'Content-Type: application/json' \
  -d "{\"client_id\":\"${SHOPIFY_ARONA_CLIENT_ID}\",\"client_secret\":\"${SHOPIFY_ARONA_CLIENT_SECRET}\",\"grant_type\":\"client_credentials\"}"
```

Returns `{"access_token":"shpat_...","scope":"...","expires_in":86399}`.
The scope string includes the full Admin write set (orders, products,
inventory, discounts, fulfillments, themes, etc.) plus storefront-unauth
and customer-account scopes — i.e. effectively full permissions.

## Call the Admin API

```bash
curl -s -X POST \
  "https://${SHOPIFY_ARONA_LABNOIR_DOMAIN}/admin/api/${SHOPIFY_ARONA_API_VERSION}/graphql.json" \
  -H "X-Shopify-Access-Token: <shpat_...>" \
  -H 'Content-Type: application/json' \
  -d '{"query":"{ shop { name myshopifyDomain primaryDomain { url } currencyCode ianaTimezone } }"}'
```

Verified response (2026-06-02): `Lab Noir` / `labnoir.ro` / `RON` /
`Europe/Bucharest`. Order numbering uses `LAB####` (latest seen `LAB1019`).

See [`shopify.md`](shopify.md) for query/mutation patterns.

## Python helper pattern

```python
import os, requests
from pathlib import Path

for line in Path("secrets/credentials.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

domain = os.environ["SHOPIFY_ARONA_LABNOIR_DOMAIN"]
ver    = os.environ["SHOPIFY_ARONA_API_VERSION"]
tok = requests.post(
    f"https://{domain}/admin/oauth/access_token",
    json={
        "client_id":     os.environ["SHOPIFY_ARONA_CLIENT_ID"],
        "client_secret": os.environ["SHOPIFY_ARONA_CLIENT_SECRET"],
        "grant_type":    "client_credentials",
    }, timeout=20,
).json()["access_token"]

resp = requests.post(
    f"https://{domain}/admin/api/{ver}/graphql.json",
    headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
    json={"query": "{ shop { name myshopifyDomain primaryDomain { url } } }"},
    timeout=20,
).json()
print(resp)
```

## Hard rules

- This app has **full Admin API permissions**. Any **write** (product update,
  inventory adjust, order edit, draft order create, etc.) must be confirmed by
  the user before execution — same rule as Postgres writes.
- Do **not** mint and persist the access token anywhere on disk; mint per-run.
- Do **not** put the secrets into MD files / scripts you commit. They live in
  `secrets/credentials.env` only.

## See also
- [`shopify.md`](shopify.md) — Admin API mechanics
- [`shopify-stores.md`](shopify-stores.md) — per-brand metrics apps (different from this)
