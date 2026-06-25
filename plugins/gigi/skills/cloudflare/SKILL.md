---
name: cloudflare
description: Operate Cloudflare for ALL Arona domains via the API — DNS management (read AND edit records on every zone) and R2 object storage. One team API token from the secret store (CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID); never printed. DNS = list zones, list/get/create/update/delete records (A, AAAA, CNAME, MX, TXT, NS, SRV…) on the ~35 zones (esteban.ro, grandia.ro, nubra.ro, george-talent.ro, belasil.ro, bonhaus.*, nocturna.*, casaofertelor.ro, magdeal.ro, gento.ro, carpetto.ro, covoria.ro, apreciat.ro, reduceribune.ro, arona.ro …). R2 = list buckets/objects, upload/download (S3-compatible; only once R2 is enabled on the account). Use for "add/change a DNS record", "set a TXT/SPF/DMARC/verification record", "point a subdomain (CNAME/A)", "Cloudflare zone", "domain verification (Google/Klaviyo/Shopify/Meta)", "what DNS records does X have", "store files on R2/Cloudflare object storage". Triggers: cloudflare, dns, dns record, txt record, cname, a record, mx, spf, dmarc, domain verification, zone, nameserver, r2, object storage, bucket.
argument-hint: "verify | zones | dns-list <domain> | dns-create/update/delete <domain> ... --apply | r2-*"
---

# cloudflare

> Author: **Gigi**. Shared with the whole team via the `gigi` plugin.

One token, every Arona domain. Operates **DNS** (the live production zones) and
**R2** (object storage) through the Cloudflare API. The token lives only in the
SharedClaude secret store and is never printed.

## Auth (already set up)
Secrets in the KB (`core:fetch-secret` / `kb.py secret-get`):
- `CLOUDFLARE_API_TOKEN` — has **Zone:Read** on all zones + **DNS records Read/Edit**.
- `CLOUDFLARE_ACCOUNT_ID`.
- `CLOUDFLARE_R2_*` (access key id / secret / endpoint) — for R2 once it's enabled.

`cf.py` loads them from the KB automatically (env first, then `kb.py secret-get`).
Nothing to configure.

## Safety — DNS is production
These are **live store domains**. Every write (`dns-create`, `dns-update`,
`dns-delete`, `r2-put`) is **dry-run by default** and only executes with
`--apply`. Always run without `--apply` first, eyeball the diff, then re-run with it.
Call out cross-store effects before changing anything shared (MX, SPF/DMARC, NS).

## Use it

```bash
CF="${CLAUDE_PLUGIN_ROOT}/skills/cloudflare/cf.py"

uv run "$CF" verify                       # token ok? what can it do?
uv run "$CF" zones --filter nocturna      # domain -> zone_id
uv run "$CF" dns-list esteban.ro          # all records
uv run "$CF" dns-list esteban.ro --type MX
uv run "$CF" dns-get  grandia.ro --name www

# create / update / delete (DRY-RUN unless --apply):
uv run "$CF" dns-create grandia.ro --type TXT --name _verif --content '"token=abc"' --apply
uv run "$CF" dns-update esteban.ro --name www --type CNAME --content shops.myshopify.com --apply
uv run "$CF" dns-delete nubra.ro --id <record_id> --apply

# R2 (only once enabled on the account):
uv run "$CF" r2-buckets
uv run "$CF" r2-ls <bucket> --prefix img/ --max 50
uv run "$CF" r2-put <bucket> path/in/bucket.jpg ./local.jpg --apply
uv run "$CF" r2-get <bucket> path/in/bucket.jpg ./local.jpg
```

`--name` accepts a short label (`www`, `_dmarc`, `@` for root) or a full FQDN —
it's normalised to `<name>.<domain>` automatically. Record lookups for
update/delete take either `--id` (from `dns-list`) or `--name`+`--type` when that
pair is unique.

## Notes / gotchas
- **`proxied`** only applies to A/AAAA/CNAME. Use `--proxied` to turn the orange
  cloud on; on update use `--no-proxied` to turn it off. DNS-only records (MX, TXT,
  NS, mail A records) must stay unproxied.
- **TXT content** usually needs the quotes inside the value — pass it quoted, e.g.
  `--content '"v=spf1 include:_spf.google.com -all"'`.
- **`ttl=1`** means "Auto" in Cloudflare. Proxied records are forced to Auto.
- **R2 not enabled yet** (as of 2026-06): the per-account `*.r2.cloudflarestorage.com`
  endpoint refuses TLS until R2 is turned on in the dashboard (R2 → Enable). `cf.py`
  detects this and tells you. DNS works regardless — R2 is unrelated.
- The token is **R2-data-plane + Zone/DNS**; it can't use the R2 *management* API
  (`/accounts/.../r2/buckets` returns "enable R2"). Bucket data ops go through the
  S3 endpoint (boto3), which `cf.py` uses.
- Adding a new permission to the token takes ~1 min to propagate (a fresh grant can
  return `code 10000 Authentication error` briefly).

## Common tasks
- **Domain verification** (Google/Klaviyo/Shopify/Meta): `dns-create <domain> --type TXT --name @ --content '"...=..."' --apply`.
- **Point a subdomain**: `dns-create <domain> --type CNAME --name sub --content target.example.com --apply`.
- **Email**: SPF/DKIM/DMARC are TXT/CNAME — change with `dns-update`; MX with care (affects mail for the whole domain).
