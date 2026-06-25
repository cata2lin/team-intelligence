---
name: xconnector
description: Punte spre xConnector (curierat) pt magazinele ARONA, pe TOATE cele 19 magazine. CITEȘTE comenzile fără AWB cu adresă WRONG/UNKNOWN + adresa curentă + sugestia validatorului, le CORECTEAZĂ automat conservator (ai-correct-address) pe cele sigure (cron `correct`), ȘI operează AWB direct prin API: `awb-make` (creează AWB cu parcelCount/curier), `awb-void` (anulează), `awb-regen` (anulează+refă cu alt nr de colete/curier), `awb-label` (link etichetă), `connectors` (listă curieri/facturare). Use pt „corectează adresele proaste", „xconnector address issues", „fă AWB / anulează AWB / regenerează AWB cu 2 colete prin xconnector", „comenzi fără awb cu adresă greșită". Scrierile AWB sunt dry-run by default (POST real doar cu --apply).
---

# /xconnector

Punte spre **API-ul xConnector** (cheie API per magazin) pt fluxul de adrese al ARONA. Model actual
(order-created): comanda nouă → Shopify Flow creează AWB; cele cu **tag „duplicata"** sau **adresă proastă**
rămân **unfulfilled**. Skill-ul ăsta trece prin cele unfulfilled fără AWB, **corectează** adresele sigure
(→ devin VALID → gata de AWB) și **triază** restul pt CS.

## Comenzi
```
uv run xconnector.py summary                                  # per magazin: câte fără AWB, pe ce status
uv run xconnector.py address-issues [--shop <domain>] [--days 60] [--json]
uv run xconnector.py recheck [--order GT1,GT2] [--days 30]    # care s-au auto-validat (VALID/PERFECT)
uv run xconnector.py correct [--shop <domain>] [--days 60] [--min-age-hours N] [--exclude d1,d2] [--apply]  # CRON
uv run xconnector.py connectors [--shop <domain>]            # curieri + facturare per magazin (id/type)
uv run xconnector.py orders [--shop d] [--sku A] [--total-items 1] [--line-items 1] [--sort fulfillmentDate] [--sort-dir asc]  # filtrează/sortează comenzi
uv run xconnector.py links  --order GT123 | --awb <tracking> [--open]    # CS: ce comandă + status + linkuri Shopify/xConnector/tracking
uv run xconnector.py print-batch [--shop a,b] [--sku HA-0002] [--total-items 1] [--from 2026-06-10 --to 2026-06-14] [--limit N] [--test] [--apply]  # PRINT depozit
uv run xconnector.py awb-make  --order GT123 [--shop d] [--connector ID] [--parcels N] [--type PARCEL] [--notify] [--apply]
uv run xconnector.py awb-void  --order GT123 [--shop d] [--connector ID] [--apply]      # anulează AWB
uv run xconnector.py awb-regen --order GT123 --parcels N [--connector ID] [--apply]     # anulează + refă cu alte condiții
uv run xconnector.py awb-label --order GT123 [--shop d]                                  # link etichetă PDF
uv run xconnector.py order-cancel --order GT123 [--shop d] [--force] [--apply]           # anulează AWB (dacă neplecat) + comanda
uv run xconnector.py inv-make  --order GT123 [--connector ID] [--lang ro] [--apply]      # creează factură (SMART_BILL default)
uv run xconnector.py inv-cancel | inv-storno | inv-regen --order GT123 [--apply]         # anulează / storno(revert) / regenerează
uv run xconnector.py inv-doc   --order GT123                                             # link PDF factură
uv run xconnector.py addr-set  --order GT123 --city "…" --zip "…" [--address1 …] [--province …] [--make-awb] [--apply]
```

### Modificare conținut comandă COD / Releaseit (cancel + replace)
Comenzile din app-ul **COD Form (Releaseit)** au **line items BLOCATE** (nu se pot edita). Doar **adresa** se modifică
(via `addr-set`). Dacă clientul cere schimbat CONȚINUTUL → procedura e **cancel + replace** (orchestrare, nu cod nou):
1. **`order-cancel --order X --apply`** — anulează AWB-ul (dacă neplecat) + comanda veche.
2. **`gigi:cs-actions` `place`** — plasează o comandă NOUĂ COD cu produsele corecte (tag agent CS).
3. AWB-ul comenzii noi → automat din **cron-ul `fulfill`** (sau `awb-make`). Noua e tag-uită CS → `fulfill` o lasă fără dedup, dar îi face AWB.
(NU se face order-edit pe Releaseit — line items blocate. Identifici Releaseit după `sourceName`/app.)

### Setare adresă (COD: adresa SE poate modifica; line items NU → ăla e cancel+replace)
- **`addr-set`** — setează adresa de livrare în Shopify (`orderUpdate.shippingAddress`, confirmat suportat în 2026-04) la câmpurile
  date, păstrând restul (firstName/lastName/company/countryCode). Cu **`--make-awb`** face **poll pe xConnector** până confirmă
  resync-ul adresei noi, ABIA APOI face AWB-ul (ca să nu folosească adresa veche); dacă nu se sincronizează în ~30s → NU face AWB
  (rulezi `awb-make` mai târziu). Dry-run by default. Merge pe toate magazinele (inclusiv Nubra — token CSV valid).
- `summary` — per magazin: total în fereastră, câte FĂRĂ AWB, distribuție status.
- `address-issues` — lista comenzilor nepornite cu adresă `WRONG`/`UNKNOWN` + adresa curentă + sugestia
  validatorului + verdict. `--json` pt automatizări.
- **`recheck`** — re-verifică statusul CURENT al adreselor: care s-au auto-validat (`VALID`/`PERFECT`) vs
  încă `WRONG`/`UNKNOWN`. Cu `--order GT1,GT2` verifici o listă; fără, ia coada curentă. Read-only. Util
  fiindcă validarea xConnector e async/batch — multe comenzi flagate se vindecă singure în câteva ore.
- **`correct`** (cron-ul) — pt fiecare comandă fără AWB cu adresă `WRONG`/`UNKNOWN`:
  - tag **„duplicata"** (Shopify) → **skip** (nu corectez, nu trimit la AWB — se anulează separat);
  - **corectabilă** (gate aac: UN candidat cu zip/oraș/județ ≥0.95 + stradă ≥0.90 + `/zip-code` confirmă +
    număr casă păstrat) → `ai-correct-address` (cu `--apply`) → adresa devine VALID → gata de AWB;
  - **grea** (rural fără stradă / fără număr / garbage / ambiguu) → **triaj CS** (cu motiv).
  Fără `--apply` = **dry-run** (arată ce ar face). `--min-age-hours N` sare comenzile mai noi de N ore
  (le lasă sweep-ului de validare al xConnector să le rezolve — vezi „Validarea e async" mai jos);
  default 0 = oprit. Corecția face adresa VALID în xConnector; AWB-ul se (re)creează separat.

### AWB direct prin API (operare CS, `/api/actions/*`)
Toate rezolvă comanda după `--order GT###` (caută în `--shop` dacă dat, altfel în toate magazinele) și sunt
**dry-run by default** — POST real DOAR cu `--apply`. `orderId` trimis la xConnector = **Shopify order ID**.
- **`connectors`** — listă connectori per magazin: `id`, tip (`curier` vs `factură`: DPD/SAMEDAY vs SMART_BILL), activ.
- **`awb-make`** — creează AWB: `create-shipping-label` cu `parcelCount` **AUTO din metafield** (vezi mai jos), `parcelType`
  (`--type`, default PARCEL), curier (`--connector ID`; obligatoriu dacă-s mai mulți curieri activi). Sare dacă
  are deja AWB (zice să folosești `awb-regen`). La succes întoarce tracking + URL etichetă + preț.
- **`awb-void`** — anulează AWB-ul (`cancel-shipping-label`, după orderId + connectorId).
- **`awb-regen`** — **anulează + refă** cu alte condiții (alt `--parcels`, `--type`, `--connector`) — ex „de la 1 la 2 colete".
  CS folosește asta când AWB-ul s-a făcut cu nr greșit de colete: **`awb-regen --order X --parcels 3 --apply`** = anulează AWB-ul de 1 colet și-l reface cu 3.

### Nr. de colete (parcelCount) — AUTO din metafield (NU mai punem 1 greșit)
`awb-make`/`awb-regen`/`fulfill` calculează `parcelCount` din Shopify (`order_parcel_count`), ca să nu mai facem
AWB-uri de 1 colet când trebuiau 2-3 (sursă frecventă de eșec/etichetă greșită):
1. order metafield **`xconnector.parcel-count`** setat → ceil(value) (totalul deja calculat de sistemul vostru);
2. altfel **`ceil( Σ produs custom.nr_cutii|nr_produse × quantity )`** (cutii reale; Grandia/Carpetto = **decimal**, 1.5→**2**);
3. altfel **1**. **Parfumurile (GT/Esteban) rămân mereu 1** — `custom.nrproduse` e nr de PRODUSE, nu de cutii, e ignorat.
`--parcels N` **forțează** manual (ocolește metafield-ul). Verificat: GT/Esteban toate 1; Grandia 1/2/3/4; Belasil 1/2/3; Carpetto 2.
- **`awb-label`** — link-ul de descărcare al etichetei (PDF) + tracking-ul, fără să recreeze nimic.
- **`order-cancel`** — anulează o comandă SIGUR: verifică în **AWBprint** (`orders.aggregated_status`) dacă a **PLECAT**
  (preluată de curier: `in_transit`/`delivered`/`back_to_sender`/…) → dacă da, **REFUZ** (cu `--force` încearcă oricum);
  dacă e **neplecată** și are AWB → anulează AWB-ul (xConnector) și **DOAR dacă reușește** → anulează comanda
  (Shopify `orderCancel`); fără AWB → doar comanda. **`refund` OFF by default** (`--refund` doar pt comenzi plătite,
  decizie explicită; `--no-restock` ca să nu repună stocul). Dacă anularea AWB eșuează (colet plecat) → NU anulează
  comanda + mesaj clar „anunță CS, a plecat". Tokenul Shopify e verificat ÎNAINTE de orice scriere (nu rămâne comandă activă cu AWB anulat).

### Curier default + Grandia/Dragon Star (auto-rutat)
`awb-make`/`awb-regen`/`fulfill` aleg implicit **DPD Romania** dacă nu dai `--connector`. **Grandia auto-rutează după
`productType`:** comenzile cu produs voluminos (`Magazii de grădină`, `Lavoare`, `Mese și măsuțe`, `Oglinzi LED`) →
**Dragon Star** [24257]; restul → DPD [20673]. (`route_connector`/`GRANDIA_BULKY_TYPES`, citește line items din Shopify.)
Dacă forțezi `--connector`, rutarea e ignorată. `order-cancel` folosește connectorul cu care s-a emis AWB-ul.

### `not-downloaded` — etichete neprintate / ghost
`uv run xconnector.py not-downloaded [--shop d] [--days 14] [--min-age-hours N]` — comenzi cu AWB a cărui etichetă
**nu a fost descărcată** (`document.downloaded=false`). Read-only. Fără filtru = coada de printat (cele noi); cu
`--min-age-hours 48` → etichete VECHI nedescărcate = **potențial ghost** (AWB făcut acum 2+ zile, label niciodată
printat → coletul probabil n-a plecat). `downloaded` n-are filtru server-side — se calculează client-side; dar **acceptă `--sort fulfillmentDate`** (coadă de print ordonată).

### `orders` — filtrare/sortare server-side (SKU, cantitate, sortare)
xConnector a adăugat (2026-06) filtre pe `getOrders`, expuse prin comanda **`orders`**:
`uv run xconnector.py orders [--shop d] [--days N] [--sku ABC] [--sku-mode ANY|ALL] [--exclude-sku XYZ] [--total-items 1|1,2] [--line-items 1] [--sort sku|totalItemsCount|lineItemsCount|date|fulfillmentDate] [--sort-dir asc|desc]`.
- **`--sku`** potrivire EXACTĂ (repetabil sau CSV; `--sku-mode ALL` = toate, `ANY` = oricare). **`--exclude-sku`** scoate (cere un filtru pozitiv alături).
- **`--total-items`** = nr TOTAL bucăți (`=1` → mono-bucată), **`--line-items`** = nr linii. CSV permis (`--total-items 2,3,4`).
- Ex: `orders --shop ix5bxc-hr --total-items 1 --sort fulfillmentDate` (mono-bucată ordonate), `orders --total-items 2,3,4 --shop n12w89-yy` (multi-bucată Grandia = candidați multi-colet).
- **DTO-ul getOrders întoarce doar** `orderName/addressStatus/dispatched/documents` — cantitatea & SKU-ul sunt **filtre & sortare server-side, NU câmpuri în răspuns** (line items rămân în Shopify). Read-only. Filtrele se pot pasa și la `not-downloaded`.

### `links` — CS „du-mă la / spune-mi despre comanda X" (100% prin xConnector, NU consumă rația Shopify)
`uv run xconnector.py links --order GT123` (după nr comandă) **sau** `--awb <tracking>` (după AWB, via `by-tracking-number`). `--open` deschide linkurile în browser. Întoarce, **fără niciun apel Shopify**:
- **ce comandă** e + **status** (adresă VALID/WRONG · AWB făcut/fără · expediat/neexpediat · **livrare REALĂ** din AWBprint `aggregated_status`: waiting_for_courier/in_transit/delivered/refused…);
- **linkuri**: Shopify admin (`/admin/orders/<orderId>`), **xConnector dashboard** (`/shop/<domain>/order?orderId=<merchantOrderId>` — atenție: dashboard-ul folosește `merchantOrderId`, NU orderId-ul Shopify!), **tracking** curier (`/track?connectorId&trackingNumber`).
- **Mapare ID** (cheie): API `orderId` = ID Shopify; API `merchantOrderId` = ID-ul din URL-ul dashboard xConnector.

**CS order-360 = ORCHESTRARE (nu duplic):** când CS întreabă despre o comandă, combină `links` (comandă+status+linkuri, xConnector+AWBprint) **+** `gigi:cs-customer-360` (alte comenzi ale clientului, LTV, refuzuri — din DB) **+** `gigi:cs-tickets`/Richpanel (tichetele clientului). Toate **fără Shopify live** (DB/Richpanel/xConnector). Căutare CS după **nume/telefon** → `gigi:cs-customer-360` (xConnector n-are filtru pe nume/telefon; doar order#/AWB).

### `print-batch` — PRINT în depozit (descarcă etichetele nedescărcate, grupate pe produs/cantitate/dată)
`uv run xconnector.py print-batch [--shop a,b] [--sku HA-0002] [--total-items 1] [--from <d> --to <d>] [--sort sku] [--limit N] [--apply]`.
Selectează etichetele **nedescărcate** (`downloaded=false` = coada de print), le **descarcă** (PDF), le pune într-un **batch PDF merged** în ordinea grupată, scrie un **log CSV cu `downloaded_at`** (audit „când s-a printat"), apoi **deschide dialogul de print**. Rulează **LOCAL** (mașina cu imprimanta — are uv + acces la secrete). **Depozitul e pe WINDOWS:** dacă e instalat **SumatraPDF** → `-print-dialog` (dialog garantat) sau `--printer "NumeImprimantă"` → printare DIRECTĂ fără dialog (batch rapid); altfel verbul „print" al handler-ului PDF default, altfel deschide PDF-ul în viewer (Ctrl+P). (macOS: Preview+Cmd⌘P; Linux: xdg-open.)
- **Grupare**: implicit `sort=sku` → toate „1×SKU1" împreună, apoi „1×SKU2"… Filtre: `--sku` (produs, potrivire exactă), `--total-items` (cantitate), `--from/--to` (interval, yyyy-MM-dd sau DD/MM/YYYY).
- **Cross-magazin**: `--shop` acceptă **listă** (`--shop covoareauto-ro,bonhaus,audusp-rf,ofertelezilei`) sau prefix → același SKU (ex HA-0002) de pe mai multe magazine, la un loc. Fără `--shop` = toate.
- **`--limit N`** = chunk gestionabil. **`--no-print`** = doar salvează (fără dialog).
- ⚠️ **`--apply` DESCARCĂ → flip `downloaded`** (etichetele ies din coada de print). Dry-run by default (listează, NU descarcă).
- **`--test`** = rulează pe etichete **DEJA descărcate** (`downloaded=true`) → ZERO impact pe coada reală (pt verificare). Cu `--test`, `--apply` e sigur.

### Facturi prin API (mirror AWB)
Connector de facturare = tip **SMART_BILL** (ales automat dacă e unul singur; altfel `--connector <id>`). Dry-run by default.
- **`inv-make`** — creează factura (`create-invoice`). Refuză dacă există deja factură → folosește `inv-regen`. `--lang ro/en`.
- **`inv-cancel`** / **`inv-storno`** — anulează (`cancel-invoice`) / stornează (`revert-invoice`; `--refund-id` pt storno parțial pe un refund).
- **`inv-regen`** — anulează + creează din nou (create gardat pe succesul anulării).
- **`inv-doc`** — link-ul PDF al facturii (din documentul `INVOICE` al comenzii).
Guard: `--connector` nebilling sau `--refund-id` nenumeric → abort (nu trimite orbește pe document financiar).

## Auth (cheie API xConnector + token Shopify Admin, per magazin)
- xConnector: secret KB **`XCONNECTOR_SHOPS`** (JSON `[{shopDomain,apiKey}]`), altfel `~/.aac/input.json`.
- Shopify (pt tagul „duplicata"): secret KB **`SHOPIFY_ADMIN_TOKENS`** (JSON `[{prefix,shopDomain,adminToken}]`).
- Cheile **nu se printează niciodată**. Din **2026-06-24** avem chei pe **toate cele 19 magazine active**
  (toate cu `ROLE_AUTOMATION` + 17 permisiuni, expiră 22-sep-2026), nu doar George Talent.

## Magazine EXTERNE (CZ/PL/BG) — validate cu HERE Geocoding, curier DPD Romania
Validatorul de adrese xConnector e **centrat pe România** → magazinele externe (**Bonhaus CZ `vthuzq-7j`,
PL `f0yrmh-ia`, BG `ux1x6n-n2`**) primesc `WRONG`/`UNKNOWN` în masă (false-positive, BG ~98%). KPI-ul nostru e
**AWB făcut**, deci pe externe `fulfill` NU folosește validatorul RO, ci **HERE Geocoding** (`here_validate`,
cheie KB `HERE_API_KEY`): geocodează adresa în `countryCode` (CZE/POL/BGR) și dacă `queryScore ≥ 0.9` (`HERE_MIN_SCORE`)
→ face AWB; sub prag (sau eroare HERE) → **fail-closed** = lasă la CS, nu face AWB. Curier = **DPD Romania**
(livrează cross-border, ca toate). Externele **NU intră** în corecția de text RO (`ai-correct-address`) — doar HERE da/nu.
Test CZ (dry-run): din 52 unfulfilled, 31 validate HERE → AWB, 21 chiar proaste → CS. Cheile lor rămân utile și pt AWB/facturi.

## Siguranță (corecția de adrese)
Corecția urmează porțile skill-ului oficial xConnector **aac** (`/agentic-address-correction`), conservator:
**un singur candidat** (fără competitor) + scoruri pe câmpuri (zip/oraș/județ ≥0.95, stradă ≥0.90) +
`/zip-code` confirmă + **numărul casei păstrat** + nume/telefon/`address2` păstrate. Regula de aur: *un zip
greșit pe etichetă e mai rău decât nicio corecție* → incert = lasă la CS. Plasă suplimentară: flow-ul ARONA
care contactează client+curier dacă o adresă invalidă ajunge la preluare. Cele grele (rural/garbage/ambiguu)
NU se ating — merg la CS.

## Validarea e ASYNC/BATCH — `WRONG`/`UNKNOWN` supra-flaghează (lecție 2026-06-24)
xConnector validează adresele **asincron, în loturi**: o comandă poate sta `WRONG`/`UNKNOWN` ore→o zi, apoi
un **sweep automat** o trece pe `VALID` **fără editare de text** (în `addressValidationHistory`: `actor:"xConnector"`,
`eventType:VALIDATION`). Pe coada GT analizată, **~16%** din „adrese proaste" s-au auto-vindecat singure. Mai mult,
`WRONG` **nu e predictor de eșec la livrare** — pe un eșantion, 6/8 colete cu adresă `WRONG` s-au livrat OK. →
**nu trata un flag proaspăt ca problemă reală**: rulează `recheck` și `correct --min-age-hours N` înainte de a
deranja CS-ul; nu bloca expedierea doar pe baza lui `WRONG`. Coada „grea" reală e mai mică decât numărul brut.

## Scriere prin API — DEBLOCAT (2026-06-24)
Docs: **https://xconnector.app/api-docs.html** (spec `/api-spec.yaml`). Creare AWB / dispatch / facturi **NU mai
sunt dashboard-only** — sunt expuse sync prin `POST /api/actions/*` (`create-shipping-label`, `cancel-shipping-label`,
`dispatch-order`, `estimate-shipping-price`, `create-invoice` + payment/cancel/revert, `locker-notification`),
`POST /api/v1/picking-lists/add-order`, `GET /api/orders/by-tracking-number`. **Gate:** cer rolul `ROLE_AUTOMATION`
pe merchant + permisiuni per-cheie (`API_CREATE_SHIPPING_LABEL` etc.) — fără ele = 403. Toate cele 19 chei le au
(17 permisiuni, inclusiv `API_ADDRESS_VALIDATE`). Skill-ul **implementează** acțiunile de scriere (`awb-make/void/regen`,
facturi, `order-cancel`, `addr-set`) + cron-ul `fulfill` care face AWB peste/în completarea Shopify Flow.

## `fulfill` — safety-net auto-AWB peste Shopify Flow (cron 15 min)
`uv run xconnector.py fulfill [--max-age-min 15] [--exclude …] [--apply]` — pt comenzile **open + unfulfilled mai vechi
de N min** (Flow a avut timp și n-a făcut AWB):
- **RO**: fără AWB + adresă VALID → fă AWB; **WRONG/UNKNOWN** → corecție conservatoare → dacă devine VALID, AWB; altfel CS.
- **EXTERNE (CZ/PL/BG)**: validare **HERE Geocoding** (≥0.9) în loc de validatorul RO → AWB (DPD Romania); sub prag → CS. (vezi secțiunea EXTERNE)
- **parcelCount AUTO** din metafield per comandă (vezi „Nr. de colete") — Grandia/Belasil/Carpetto pot fi 2-4 colete, parfumurile 1.
- **tag de duplicat** (`duplicata`/`duplicata3`/`duplicat4`) → regula Flow-urilor: **păstrează cea mai NOUĂ** comandă a clientului
  (7 zile) → îi fac AWB; **cele VECHI** → le **anulez** (reason OTHER, fără refund/restock/notify, **protecție livrare**: nu anulez
  ce a plecat). **CS-placed / draft order** (tag agent CS sau `sourceName=shopify_draft_order`) → **NU se dedup-ează, dar PRIMESC AWB**.
  Fără client / status incert → NU expediez, NU anulez (conservator — erorile API cad pe „skip").
- **Grandia auto-rutează** (voluminos → Dragon Star, restul DPD) — nu mai trebuie `--exclude`. Sare automat magazinele cu AWB deja făcut.
- **Dry-run by default.** Sursa „plecat" = AWBprint. Consistent cu cele 2 Shopify Flow-uri de duplicate (NU le înlocuiește — le completează).

## Cron (VPS)
`correct --apply` rulează periodic pe VPS (flock + log, `0 8-20 * * *`): corectează automat ce e sigur, sare
duplicatele și comenzile proaspete (`--min-age-hours`), scoate triajul CS. Vezi `gigi:xconnector` în KB pt detalii
deploy. Pereche cu [gigi:cs-address-guard].
