---
name: marketing-access
description: Adaugă (sau listează) un user pe conturile de marketing ale echipei — GA4 (Google Analytics 4) și Google Ads — programatic, dintr-o comandă. Folosește la onboarding de agenție/coleg ("adaugă X pe GA4/Google Ads", "dă-i acces de editor pe Analytics", "invită agenția pe conturile de Ads"). GA4 = acces imediat prin Admin API (accessBindings) + domain-wide delegation; Google Ads = invitație pe email prin REST (CustomerUserAccessInvitation) cu credențialele MCC-ului din DB-ul metrics. Include tool-ul scripts/grant_access.py (dry-run implicit, --apply ca să execute).
---

# Marketing Access — dă unui user acces pe GA4 + Google Ads

Onboarding de acces la conturile de marketing (ex. o agenție) dintr-o comandă, fără click prin UI
pe zeci de conturi. Tool: `scripts/grant_access.py` (dry-run implicit; `--apply` execută).

> **Secretele nu se pun în cod.** DSN-ul metrics vine din KB prin env; cheia SA GA4 e un fișier local;
> tokenele Google Ads se citesc din DB în proces. Nimic sensibil nu se printează.

## Roluri (mapare „editor")
| Vrei | GA4 (`--role`) | Google Ads (`--role`) |
|---|---|---|
| Editează (uzual pt agenție) | `editor` | `STANDARD` |
| Control total (+ gestionează useri) | `admin` | `ADMIN` |
| Doar vizualizare | `viewer` | `READ_ONLY` |
| Doar analiză (GA4) | `analyst` | — |

## A) GA4 — acces IMEDIAT (fără acceptare)
```
uv run scripts/grant_access.py ga4 --email nou@agentie.ro --role editor           # dry-run: listează conturile
uv run scripts/grant_access.py ga4 --email nou@agentie.ro --role editor --apply    # adaugă pe toate
```
- User management = **Admin API** `accessBindings.create` (v1alpha), scope `analytics.manage.users`.
- Conturile = tot ce vede SA-ul `looker-sheets` prin `accountSummaries` (v1beta) — „conturile pe care le folosim".
- SA-ul are doar **Viewer** → nu poate adăuga useri singur. Tool-ul **impersonează owner-ul prin DWD**
  (`--subject`, implicit `gheorghe.beschea@overheat.agency`, Admin pe conturile GA4).
- ⚠️ **Blocaj tipic:** `unauthorized_client` la impersonare = DWD nu autorizează scope-ul. Fix (o dată,
  Workspace admin): **admin.google.com → Security → Access and data control → API controls →
  Domain-wide delegation** → client ID **`105430525977895660493`** → adaugă scope
  `https://www.googleapis.com/auth/analytics.manage.users` → Authorize. Apoi reia cu `--apply`.

## B) Google Ads — prin INVITAȚIE pe email (userul o acceptă!)
```
DATABASE_URL_METRICS="$(uv run <kb.py> secret-get DATABASE_URL_METRICS)" \
  uv run scripts/grant_access.py gads --email nou@agentie.ro --role STANDARD           # dry-run
DATABASE_URL_METRICS=... uv run scripts/grant_access.py gads --email nou@agentie.ro --role STANDARD --apply
```
- User management = **REST** `customers/{cid}/customerUserAccessInvitations:mutate` (API v21).
- Credențiale din DB `metrics`, tabel `google_ads_connections` (`isActive=true`): developerToken /
  loginCustomerId (**MCC 7467110480**, NOVOS DIGITAL) / oauthClientId / oauthClientSecret / refreshToken.
  Refresh-token → access-token la `oauth2.googleapis.com/token`; headers `developer-token` + `login-customer-id`.
- Conturile active = `googleAds:search` pe MCC cu `customer_client` unde `status='ENABLED' AND manager=FALSE`.
- ⚠️ **Google Ads NU dă acces direct** — trimite o invitație; **userul trebuie s-o accepte pe email**.
- ⚠️ **Blocaj tipic:** `EMAIL_DOMAIN_POLICY_VIOLATED` = contul restricționează domeniile de email invitabile.
  Fix (UI, per cont sau la MCC): **Google Ads → Admin → Access and security → tab „Domains"** → adaugă
  domeniul (ex. `heyads.ro`). Nu e expus în API. Apoi re-rulează `--apply` (invitațiile deja trimise apar „deja").

## Capcane & note
- **Dry-run mai întâi** — verifică lista de conturi înainte de `--apply` (mai ales pe Ads = emailuri către agenție).
- **GA4 e imediat, Ads cere acceptare** — nu raporta „gata" pe Ads până userul n-a acceptat.
- Idempotent: re-rulările marchează „avea deja" / „deja invitat", nu dublează.
- Conturi care lipsesc din „active": un cont **suspendat** (ex. Esteban pe Ads) nu e `ENABLED` sub MCC → nu apare;
  adaugă-l separat după reactivare.
- Același tipar de scriere ca restul echipei: **GA4 write = SA + DWD (impersonare owner)** ca la Gmail/Apps Script;
  **Google Ads = REST cu creds din `google_ads_connections`** (vezi `gigi:google-ads-mcc`).
