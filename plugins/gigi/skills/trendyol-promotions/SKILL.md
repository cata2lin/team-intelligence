---
name: trendyol-promotions
description: "Enroll (or remove) Trendyol products into the platform's discount CAMPAIGNS / PROMOTIONS in bulk via the Partner panel's INTERNAL API — the thing the official Trendyol integration API does NOT expose. List available campaigns, see eligible products, enroll ALL eligible (or specific listingIds) at max price (minimum seller discount, Trendyol co-funds the rest), check what's enrolled, and bulk-remove as undo. Auth = a SHORT-LIVED panel session token (login has reCAPTCHA, so grab the token from the panel once and store it). Triggers: 'bag produsele in campanii Trendyol', 'inscrie produse in promotii Trendyol', 'Trendyol campaigns/promotions enroll', 'add products to Trendyol discount campaign', 'promotii Trendyol prin API', 'scoate produsele din campania Trendyol', 'ce campanii Trendyol sunt disponibile'."
argument-hint: "campaigns | eligible --campaign ID | enroll --campaign ID --all|--listing h,h | enrolled --campaign ID | remove --campaign ID --status APPROVED --apply"
---

# trendyol-promotions — produse în promoțiile/campaniile Trendyol (API intern panel)

Înscrierea produselor în „Campanii disponibile" (promoții cu reducere, Trendyol co-finanțează) **NU e în API-ul
oficial de integrare** — se face prin **API-ul INTERN al panelului partener**. Capturat + verificat live prin
chrome-devtools. Vezi [[trendyol-promotions-internal-api]]. E separat de comenzi/AWB (vezi `gigi:trendyol-awb`).

## Auth (token scurt din sesiune — capcana principală)
API-ul intern cere un **JWT scurt (~15-20 min)** din sesiunea panelului. Login-ul are **reCAPTCHA** → nu se ia
100% headless. Fluxul: loghează-te pe `partner.trendyol.com` (profil RO) → DevTools → Network → orice request
către `apigw.trendyol.com` → copiază header-ul **`authorization`** (fără `Bearer `) → pune-l:
```
kb.py secret-set TRENDYOL_PANEL_TOKEN '<jwt>'
```
(sau `export TRENDYOL_PANEL_TOKEN=...`). Când pică (401), iei altul. StoreFront implicit 29=RO (`TRENDYOL_STOREFRONT_ID`).

## Comenzi
```bash
uv run scripts/trendyol_promotions.py campaigns                     # campaniile disponibile + eligibile + acoperire Trendyol
uv run scripts/trendyol_promotions.py eligible --campaign 371508    # câte/ce produse eligibile
uv run scripts/trendyol_promotions.py enroll   --campaign 371508 --all         # înscrie TOATE eligibile
uv run scripts/trendyol_promotions.py enroll   --campaign 371508 --listing h1,h2   # doar anumite listingId
uv run scripts/trendyol_promotions.py enrolled --campaign 371508    # ce e înscris + status (Aprobat/Pending)
uv run scripts/trendyol_promotions.py remove   --campaign 371508 --status APPROVED,PENDING --apply   # UNDO
```

## Cum funcționează (contract verificat)
Bază `apigw.trendyol.com/partner/sellereng-campaign-scw-campaign-bff/`. Headere pe toate: `Authorization: Bearer`,
`x-sc-store-front-id`, `x-sc-country`, `x-sc-language`.
- **enroll** = `POST /campaign-products/approvable/max-price/batch` cu `{"campaignId","listingIds":[...]}`.
  **`listingIds:[]` GOL = TOATE eligibile** (varianta „max-price" aplică auto prețul maxim admis = discount minim;
  restul reducerii îl acoperă Trendyol). Procesare **ASYNC** → verifică cu `enrolled` (status Aprobat).
  Se face automat și `POST /campaigns/{id}/suppliers` (join) înainte.
- **remove (undo)** = `DELETE /campaign-products/bulk` cu `{"campaignId","reason":"WRONG_JOIN","statuses":[...]}`
  — șterge TOATE produsele cu statusele date. `--apply` obligatoriu (dry-run implicit).

## ⚠️ Reguli
- Înscrierea = **decizie COMERCIALĂ** (reducere reală, preț BLOCAT pe durata campaniei) — NU o rula fără OK explicit.
- E API intern/nedocumentat → se poate schimba; recapturează cu chrome-devtools dacă pică (capcană baklava:
  snapshot `verbose` → click real pe uid).
- Pilot verificat 27-iul: înscris toate eligibile în campania **371508** (Reducere 30%, 20% Trendyol) → Aprobat.

Related: `gigi:trendyol-awb` · [[trendyol-promotions-internal-api]] · [[trendyol-awb-download]]
