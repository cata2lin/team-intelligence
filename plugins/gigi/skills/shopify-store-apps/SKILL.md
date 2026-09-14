---
name: shopify-store-apps
description: Acces READ+WRITE la 8 magazine Shopify servite de 2 app-uri custom `client_credentials` (NU token static — se emite la cerere, ~24h). App „ro-deals" = Orice Redus (oriceredus.ro), Ofertele Zilei, Rossi Nails. App „bonhaus-intl" = Bonhaus HU/SK/CZ/BG + Casa Ofertelor. Credențialele + maparea magazin→app stau în secretul KB SHOPIFY_READ_APPS. Emite tokenul (POST /admin/oauth/access_token cu client_credentials), apoi query/mutation prin Admin GraphQL. Folosește pentru „ia date din oriceredus/ofertele/rossi/bonhaus/casa ofertelor", „acces API la magazinele astea noi", „token pentru bonhaus.hu", „scrie/citește produse/comenzi pe Orice Redus", „documentație pt dev pe magazinele astea". NU e pentru magazinele ARONA vechi (→ gigi:shopify-stores / SHOPIFY_STORES_CSV) și nici pt ARONA Assistant (→ [[shopify-arona-assistant-mint]]).
category: shopify-operations
version: 1.0.0
---

# shopify-store-apps — cele 2 app-uri client_credentials

8 magazine, 2 app-uri custom care emit token la cerere prin **`client_credentials`** (nu există
token static de căutat). Acum **READ+WRITE** (83 scopes fiecare). Credențialele + maparea stau în
secretul KB **`SHOPIFY_READ_APPS`**.

## When to use
- „Ia produsele/comenzile din **oriceredus / ofertele zilei / rossi**" (app `ro-deals`).
- „Ia/scrie date pe **bonhaus.hu / .sk / .cz / .bg / casa ofertelor**" (app `bonhaus-intl`).
- „Dă-mi un token pentru magazinul X" / „ce scopes are app-ul ăsta".
- Documentație pt un developer extern (vezi `~/Downloads/Shopify-api-acces-dev.md`).
- NU pt: magazinele ARONA din `stores.csv` (→ `gigi:shopify-stores`), ARONA Assistant mint
  (→ memoria [[shopify-arona-assistant-mint]]).

## Magazine → app
| App (`SHOPIFY_READ_APPS`) | Magazine | Monede |
|---|---|---|
| **ro-deals** | oriceredus (oriceredus.ro), ofertele-zilei, rossi (rossinails.ro) | RON |
| **bonhaus-intl** | bonhaus-hu (HUF), -sk (EUR), -cz (CZK), -bg (EUR), casa-ofertelor (RON) | mixt |

Fiecare app merge DOAR pe magazinele ei → altfel `400 app_not_installed`.

## Steps
```bash
cd plugins/gigi/skills/shopify-store-apps/scripts
uv run shopify_apps.py stores                                   # listă magazine + app + scope
uv run shopify_apps.py token  --store oriceredus                # emite token (arată doar …last4)
uv run shopify_apps.py scopes --store rossi [--verbose]         # câte scopes / read vs write
uv run shopify_apps.py query  --store bonhaus-hu --gql '{ shop { name currencyCode } }'
uv run shopify_apps.py query  --store casa-ofertelor --file q.graphql --vars '{"after":null}'
```
Argument `--store` acceptă numele scurt (`oriceredus`, `bonhaus-cz`) SAU domeniul myshopify.

## Mecanism (identic cu ARONA mint, dar app-uri separate)
```
POST https://{shop}/admin/oauth/access_token
  {client_id, client_secret, grant_type:"client_credentials"} → {access_token, expires_in:86400}
```
apoi `X-Shopify-Access-Token` pe `/admin/api/{api_version}/graphql.json`. Token ~24h — **emite o
dată per magazin, ține în cache, refolosește** (Shopify limitează și emiterea).

## Notes
- **Secret**: `SHOPIFY_READ_APPS` (KB, service `shopify-read`) = JSON `{api_version, apps[]}` cu
  `client_id`/`client_secret`/`scope`/`stores{}` per app. Sursa originală = fișierele din
  `~/Downloads/credentials/` (`orice redus.txt`, `bonhaus hu si sk.txt`). ⚠️ Numele secretului zice
  „READ" din motive istorice — **acum e READ+WRITE** (83 scopes, activate 25-iul-2026).
- **READ+WRITE**: poți face mutații (productUpdate, orderCancel, etc.). Verifică ÎNTOTDEAUNA
  `userErrors` pe mutații (200 cu userErrors = n-a făcut nimic). 31 write scopes incl. write_products,
  write_orders, write_inventory, write_draft_orders, write_content, write_themes, write_translations…
- **Scope-uri se schimbă în 2 pași**: (1) editezi scopes-urile în configul app-ului, (2) **confirmi
  din app, în store** (re-acordare) — altfel tokenul emis rămâne cu scopes-urile vechi. Verifică cu
  `scopes --store X` (emite token + citește `/admin/oauth/access_scopes.json`). Capcană dovedită
  25-iul: după „Save" pe scopes, tokenul avea încă 0 write până la confirmarea din store.
- **Capcane**: `400 app_not_installed` = credențiale greșite pt magazinul ăla (ro-deals vs
  bonhaus-intl); `401` = token expirat (>24h) → re-emite; `403 access denied` pe mutație = scope
  `write_*` neacordat încă; rate-limit cost-based → backoff; ID-uri = GID; prețuri ca string + monedă
  per magazin.
- **Windows**: scriptul forțează UTF-8 la output.
- Related: [[shopify-arona-assistant-mint]], [[store-domain-map]], [[shopify-read-apps]],
  [[gigi:shopify-stores]].
