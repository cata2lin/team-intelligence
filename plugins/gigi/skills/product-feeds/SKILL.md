---
name: product-feeds
description: "Generează și AUTO-GĂZDUIEȘTE feed-uri de produse pentru orice magazin ARONA Shopify — Favi / Google Shopping / Compari.ro — DIRECT din Shopify Admin API, FĂRĂ app plătit (înlocuiește Omega/FeedNexa). Servite la feed.<domeniu>/<canal>.xml pe VPS-ul Scripturi (nginx + Let's Encrypt + cron), reîmprospătate zilnic. Use când: 'feed produse', 'feed Favi/Compari/Google', 'renunțăm la Omega/FeedNexa', 'nu mai vreau să plătesc app-ul de feed', 'feed de comparare preturi', 'datafeed magazin'."
user-invocable: true
argument-hint: "[--store GRAN] [--channel favi|compari|google]"
license: MIT
metadata:
  author: gigi
  version: "1.0.0"
  category: seo
---

# product-feeds — feed-uri de produse self-hosted (fără app plătit)

Generează feed-uri de produse **direct din Shopify Admin API** și le **găzduiește singur** pe
VPS-ul Scripturi, ca să NU mai plătim un app de feed (Omega/FeedNexa etc.). Un singur generator
scoate mai multe formate; nginx le servește la `feed.<domeniu>/<canal>.xml`, un cron le
reîmprospătează zilnic. **Grandia = LIVE** (7-iul-2026), înlocuind Omega.

## De ce (nu app plătit)
- App-urile de feed (Omega/FeedNexa/Simprosys) costă lunar și feed-ul **moare dacă renunți la app** (URL-ul lor).
- **Favi acceptă formatul Google Shopping** (același pe care-l trimitea FeedNexa) → nu trebuie format special pt Favi.
- **Compari.ro** are format propriu (`<products><product>`), PPC (afișare doar cu buget de clicuri) + link **nofollow** — deci e canal de shopping, nu „listare free de autoritate".

## LIVE acum — Grandia
Toate generate din Shopify, HTTPS (Let's Encrypt), cron `0 6,15 * * *`, **fără Omega**:
| Canal | URL | Note |
|---|---|---|
| **Favi** | `https://feed.grandia.ro/favi.xml` | format Google Shopping (476 prod) — ce dai la Favi |
| Google Merchant | `https://feed.grandia.ro/google.xml` | identic favi (pt GMC/PMax) |
| Compari | `https://feed.grandia.ro/compari.xml` | format Compari, doar produse pe stoc (345); **validează pe Compari** (formatul e best-effort) |

## Arhitectura (unde stă ce)
- **Generator**: `/root/Scripturi/feedgen/grandia_feed.py` (stdlib pur; = `scripts/grandia_feed.py` din skill). Paginează `products(status:active)` via GraphQL 2026-01, emite:
  - `google_feed()` → RSS `<rss xmlns:g>` cu `g:id/title/description/link/image_link/price/sale_price/availability/brand/mpn/gtin/product_type` (Favi + Google).
  - `compari_feed()` → `<products><product>` cu `identifier/name/manufacturer/category/producturl/imageurl/price(gross)/description/delivery_time/ean`, doar produse pe stoc.
- **Wrapper + cron**: `/root/Scripturi/feedgen/run.sh` (ia tokenul GRAN din `/root/Scripturi/stores.csv`, rulează generatorul → `/var/www/feeds/`). Cron: `0 6,15 * * * flock … run.sh`.
- **Serving**: nginx `sites-available/feed-grandia` (`server_name feed.grandia.ro; root /var/www/feeds;`) + Let's Encrypt (`certbot --nginx -d feed.grandia.ro`).
- **DNS**: `feed.grandia.ro` A → 84.46.242.181 (VPS Scripturi), **DNS-only / grey** (ca să meargă certbot HTTP-01 direct) — via `gigi:cloudflare` (`cf.py dns-create grandia.ro --type A --name feed --content <ip> --apply`).
- **VPS**: Scripturi = `PROFIT_SSH_HOST/USER/PASS` din KB (84.46.242.181, Debian, nginx+certbot). Acces: paramiko/ssh.

## Cum adaugi un feed NOU (alt magazin sau alt canal)
1. **Adaptează generatorul**: `SHOPIFY_GRAN_SHOP/TOKEN` → magazinul dorit (prefix din `stores.csv`). Fișele feed-ului se scriu în `feeds={...}` din `__main__` (adaugă un `<nume>.xml`).
2. **DNS** (o dată/domeniu): `cf.py dns-create <domeniu> --type A --name feed --content 84.46.242.181 --apply` (grey).
3. **nginx**: server block nou `server_name feed.<domeniu>; root /var/www/feeds-<store>;` → `certbot --nginx -d feed.<domeniu>`.
4. **cron**: adaugă linia în crontab (append, NU suprascrie: `( crontab -l; echo "<linie>" ) | crontab -`).
5. **Test**: `curl -sI https://feed.<domeniu>/<canal>.xml`.

## Capcane (empiric)
- **Token cu `\r`**: `stores.csv` are line-endings Windows → curăță (`gsub(/\r/,"")` în awk / `tr -d '\r'`), altfel „Invalid header value".
- **`weight`/`weightUnit`** nu mai există pe `ProductVariant` în 2026-01 (mutate pe inventoryItem.measurement) — nu le cere.
- **DNS grey (DNS-only)** pt certbot HTTP-01 direct; dacă e proxied, certbot trebuie să treacă prin Cloudflare (mai fragil) sau folosește DNS-01.
- **Cron**: mereu **append** la crontab-ul existent (`crontab -l` are deja joburi de profit/tiktok/social) — nu-l suprascrie.
- **Favi = Google format**; **Compari = format propriu** (`<products><product>`, preț gross fără „RON", `delivery_time` = nr zile, doar stoc). Specul oficial Compari e bot-blocked/JS → formatul Compari e best-effort, validează-l pe Compari (procesare 3-7 zile, arată erori).
- **Compari e PPC + nofollow** — canal de shopping plătit, nu SEO. Favi = home vertical, potrivit Grandia.

Companion: `gigi:shopify-stores` (token/GraphQL), `gigi:cloudflare` (DNS), `gigi:merchant-center-feed` (health feed Google).
