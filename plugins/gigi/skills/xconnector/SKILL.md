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
uv run xconnector.py print-batch [--shop a,b] [--sku HA-0002] [--total-items 1] [--from .. --to ..] [--sort sku|totalItemsCount] [--limit 250] [--printed] [--test] [--apply]  # PRINT depozit
uv run xconnector.py awb-make  --order GT123 [--shop d] [--connector ID] [--parcels N] [--type PARCEL] [--notify] [--apply]
uv run xconnector.py awb-void  --order GT123 [--shop d] [--connector ID] [--apply]      # anulează AWB
uv run xconnector.py awb-regen --order GT123 --parcels N [--connector ID] [--apply]     # anulează + refă cu alte condiții
uv run xconnector.py awb-label --order GT123 [--shop d]                                  # link etichetă PDF
uv run xconnector.py order-cancel --order GT123 [--shop d] [--force] [--apply]           # anulează AWB (dacă neplecat) + comanda
uv run xconnector.py inv-make  --order GT123 [--connector ID] [--lang ro] [--apply]      # creează factură (SMART_BILL default)
uv run xconnector.py capture   [--shop GT|all] [--days 60] [--limit N] [--apply]   # COD: livrat→mark paid · refuzat→tag 'refuzata' · în curs→verifică DPD
uv run xconnector.py inv-bulk  [--shop GT|all] [--days 60] [--connector ID] [--lang ro] [--limit N] [--apply]  # FACTUREAZĂ ÎN MASĂ comenzile plătite fără factură
uv run xconnector.py inv-cancel | inv-storno | inv-regen --order GT123 [--apply]         # anulează / storno(revert) / regenerează
uv run xconnector.py inv-doc   --order GT123                                             # link PDF factură
uv run xconnector.py addr-set  --order GT123 --city "…" --zip "…" [--address1 …] [--province …] [--make-awb] [--apply]
```

### Modificare conținut comandă COD / Releaseit (cancel + replace)
Comenzile din app-ul **COD Form (Releaseit)** au **line items BLOCATE** (nu se pot edita). Doar **adresa** se modifică
(via `addr-set`). Dacă clientul cere schimbat CONȚINUTUL → procedura e **cancel + replace** (orchestrare, nu cod nou):
1. **`order-cancel --order X --apply`** — anulează AWB-ul (dacă neplecat) + comanda veche.
2. **`gigi:cs-actions` `replace --from-order X`** — re-plasează COD cu produsele corecte (copiază adresa din comanda veche, tag `replasata-cs` — **NU `swap`**; swap-ul e DOAR pt schimbarea produsului/mărimii). Replasează la **aceeași valoare** (vezi promo COD [[releaseit-cod-promo-model]]).
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
- **RETURURI (BI Grandia → xConnector):** un retur/redirect creat în BI Grandia **NU e o comandă separată** — apare ca **etichetă SHIPPING_LABEL suplimentară pe comanda-părinte** (același `merchantOrderId`). Regula fiabilă: **o expediere = o singură etichetă** (2 colete = tot 1 AWB/etichetă), deci **prima** SHIPPING_LABEL = **DUS** (outbound), **oricare de după = RETUR/REDIRECT**. `links` le listează pe toate cu marcaj DUS/RETUR + status descărcat; dacă `--awb` e un AWB de retur, te duce pe comanda-părinte și te avertizează. **DA, retururile Grandia ajung în xConnector și se pot descărca** (verificat: GRAND18363 → dus `81326488944` + retururi `81326489142`/`81326489296`, toate DPD, toate descărcate). ⚠️ În DTO **NU există flag de retur** — singurul semnal e „a doua etichetă+"; AWBprint nu le distinge (DPD-urile de retur nici nu ajung în AWBprint, doar AWB-ul Frisbo de dus). Vezi [[bi-grandia-returns-xconnector]].

**CS order-360 = ORCHESTRARE (nu duplic):** când CS întreabă despre o comandă, combină `links` (comandă+status+linkuri, xConnector+AWBprint) **+** `gigi:cs-360 customer` (alte comenzi ale clientului, LTV, refuzuri — din DB) **+** `gigi:cs-tickets`/Richpanel (tichetele clientului). Toate **fără Shopify live** (DB/Richpanel/xConnector). Căutare CS după **nume/telefon** → `gigi:cs-360 customer` (xConnector n-are filtru pe nume/telefon; doar order#/AWB).

### `print-batch` — [⚠️ DEPRECAT → `depozit:print-queue`] PRINT în depozit (descarcă etichetele nedescărcate, grupate pe produs/cantitate/dată)
> **Tool-ul canonic de print depozit e acum `depozit:print-queue`** (per-stație: `pull`→`plan`→`open --machine depozit|uzina2`). `print-batch` rămâne funcțional (fără rutare pe stație) doar pt compatibilitate — nu-l folosi pt print nou. Secțiunea de mai jos = referință istorică.
`uv run xconnector.py print-batch [--shop a,b] [--sku HA-0002] [--total-items 1] [--from <d> --to <d>] [--sort sku] [--limit N] [--apply]`.
Selectează etichetele **nedescărcate** (`downloaded=false` = coada de print), le **descarcă** (PDF), le pune într-un **batch PDF merged** în ordinea grupată, scrie un **log CSV cu `downloaded_at`** (audit „când s-a printat"), apoi **trimite direct în coada imprimantei**. Rulează **LOCAL** (mașina cu imprimanta — are uv + acces la secrete).
- 🔁 **DOAR eticheta de DUS, NICIODATĂ retururile.** `print-batch` și `not-downloaded` folosesc `awb_doc()` = **prima** SHIPPING_LABEL (outbound); etichetele de RETUR/REDIRECT (a doua+, `return_labels()`) **nu intră în coada de print** — ele **pleacă de la client**, nu se printează în depozit. (Nu descărca returul odată cu restul AWB-urilor la print.)
- **Imprimantă „aleasă o dată, ținută minte"**: la **prima** rulare cu `--apply` te întreabă imprimanta (listă din sistem), o **salvează** în `~/.arona_printbatch.json`, iar de la a doua rulare merge **DIRECT în coadă** — fără Chrome, fără Ctrl+P. Trimite **toate** batch-urile (mono+multi), nu doar primul. `--choose-printer` = re-alege · `--printer "Nume"` = o setezi din comandă (+ o reține) · `--print-dialog` = revii o dată la dialogul vechi.
- **Cum trimite silent pe Windows (fără instalări obligatorii)**, în cascadă: **SumatraPDF** `-print-to` dacă e (cel mai curat) → verbul shell **`printto`** (Edge/Adobe, oricare imprimantă) → verbul **`print`** pe imprimanta **default** (zero-install). Dacă niciuna nu merge sau nu e TTY → cade pe dialog: **Chrome → Ctrl+P** (cum deschidea xConnector), macOS Preview+Cmd⌘P, Linux xdg-open.
- **Grupare**: implicit `sort=sku` → toate „1×SKU1" împreună, apoi „1×SKU2"… Filtre: `--sku` (produs, potrivire exactă), `--total-items` (cantitate), `--from/--to` (interval, yyyy-MM-dd sau DD/MM/YYYY).
- **Cross-magazin**: `--shop` acceptă **listă** (`--shop covoareauto-ro,bonhaus,audusp-rf,ofertelezilei`) sau prefix → același SKU (ex HA-0002) de pe mai multe magazine, la un loc. Fără `--shop` = toate.
- **Batch max 250 AWB-uri** (default `--limit 250`): dacă-s mai multe, printează 250 + zice câte rămân („rulează iar" pt batch-ul următor). `--limit N` schimbă plafonul.
- **`--printed`** = RE-PRINT pe AWB-uri DEJA printate (downloaded=true) — re-printezi etichete deja descărcate, fără să afectezi coada de nedescărcate.
- **Alegi ce printezi**: `--sku` (produs), `--total-items` (cantitate), `--from/--to` (interval), `--shop` (magazine), **`--sort`** (`sku` grupat pe produs = default; `totalItemsCount` = cele mai multe bucăți primele). **`--no-print`** = doar salvează (fără dialog).
- **`--by-sku`** = NU printează, arată **coada grupată pe SKU** (câte etichete are fiecare produs în coadă, cele mai multe primele) — ca să vezi ce produs are cele mai multe comenzi de expediat și să-l printezi pe ăla (`--sku <top> --apply`). (SKU-ul nu e în DTO-ul xConnector → se ia din Shopify line items, doar pt comenzile pending.)
- ⚠️ **`--apply` DESCARCĂ → flip `downloaded`** (etichetele ies din coada de print). Dry-run by default (listează, NU descarcă).
- **`--test`** = rulează pe etichete **DEJA descărcate** (`downloaded=true`) → ZERO impact pe coada reală (pt verificare). Cu `--test`, `--apply` e sigur.

### `capture` — COD: mark-paid pe livrare (fluxul pending → livrat → paid → facturat)
Pipeline-ul complet pt COD: comenzile sunt **PENDING** până le încasează curierul. `capture` rezolvă fiecare comandă PENDING
din ultimele `--days` zile, după **statusul REAL de livrare**:
- sursă status = **AWBprint** `aggregated_status` (batch, o conexiune); pt cele **în curs** (in_transit / unsuccessful_delivery /
  redirected / customer_pickup / fără status) face **cross-check LIVE pe API-ul DPD** (`api.dpd.ro/v1/track`, creds `DPD_RO_*` din KB);
- **LIVRAT** (`delivered`, sau DPD „Delivered/Collected") → **`orderMarkAsPaid`** în Shopify (= capture COD);
- **REFUZAT / întors la expeditor** (`back_to_sender`/`returning_to_sender`/`refused`/`lost`, sau DPD „Return to Sender/Refused") → **tag `refuzata`**;
- **ÎN CURS / nesigur** (DPD „Returned to Office", „Prepared for Self-collecting", in-transit) → **lăsat** (re-verificat la rularea următoare; NU marchez/tag prematur);
- `incorrect_address`/`cancelled` → lăsate (CS / deja anulate).
CONSERVATOR by design: `customer_pickup` = pregătit la locker, NU încă ridicat → NU se marchează paid; „Returned to Office" ≠ refuz.
`--shop` prefix/domeniu/CSV/`all`; `--limit N`; **dry-run by default** (listează acțiunile), scrie în Shopify DOAR cu `--apply`.
**Apoi** `inv-bulk` facturează cele devenite PAID. Flux uzual: `capture --shop X --apply` → `inv-bulk --shop X --apply`.

### Facturi prin API (mirror AWB)
Connector de facturare = tip **SMART_BILL** (ales automat dacă e unul singur; altfel `--connector <id>`). Dry-run by default.
- **`inv-make`** — creează factura (`create-invoice`). Refuză dacă există deja factură → folosește `inv-regen`. `--lang ro/en`.
- **`inv-cancel`** / **`inv-storno`** — anulează (`cancel-invoice`) / stornează (`revert-invoice`; `--refund-id` pt storno parțial pe un refund).
- **`inv-regen`** — anulează + creează din nou (create gardat pe succesul anulării).
- **`inv-doc`** — link-ul PDF al facturii (din documentul `INVOICE` al comenzii).
- **`inv-bulk`** — **facturare ÎN MASĂ** a comenzilor plătite fără factură pe `--days` zile (ex YTD: `--days 180`),
  pe `--shop` (prefix `GT`/domeniu/CSV/`all`) cu `--exclude d1,d2` opțional. Criterii: **payment=PAID**, neanulate,
  fără refund, **total>0**, **fără factură**. Shipping **inclus automat** de SmartBill; **data facturii = azi**;
  toate pe **seria ARONA** (connectorul SMART_BILL **activ** per magazin — fiecare are exact unul; „PX"/„JG" inactive = alte serii).
  **Dry-run by default**; emite cu `--apply`; `--limit N` plafonează emiterile/rulare.
  Guard: `--connector` nebilling → abort. Guard anti-dublură: dacă <10% din comenzile dintr-un magazin au factură ÎN xConnector → 🚩 SKIP (facturează probabil altundeva) decât cu `--force`.
  - **Flux TARGETAT (minim Shopify):** ia întâi comenzile **fără factură** din xConnector (`documents`, ZERO Shopify),
    apoi verifică plata **DOAR pt ele**, după ID (`nodes(ids:…)`, în loturi) — NU mai scanează TOATE comenzile plătite
    (≈80% mai puține apeluri pe rația Shopify, partajată cu celelalte app-uri ARONA). Toate apelurile Shopify sunt **politicoase**
    (lasă ≥50% din bucket-ul GraphQL liber + back-off pe THROTTLED) și **robuste la scară** (`shopify_status_by_ids` reîncearcă
    ID-urile lipsă din loturi throttlate — altfel subnumără plătiții, ex Ofertele 48 în loc de ~400).
  - **Plafon xConnector `getOrders` ≈10000 comenzi/cerere** → `_scan_all_orders` **bisectează fereastra pe dată** la **≥9500**
    (uneori întoarce 9999 = 10000 minus duplicate; un prag de 10000 ratează exact cazul ăsta — ex Esteban), recursiv până sub plafon.
    Altfel magazinele mari (Ofertele, Reduceri Bune… zeci de mii de comenzi) pierd comenzile mai vechi de ultimele ~10000.
  - **`getOrders` REÎNCEARCĂ paginile picate** (throttle/eroare) și **ridică** dacă tot pică, în loc să întoarcă tăcut o scanare
    PARȚIALĂ (altfel un blip pe o pagină = magazin masiv subnumărat, ex Ofertele 2600 în loc de ~13000). inv-bulk **sare magazinul**
    pe scanare eșuată (îl reia la rularea următoare — idempotent), NU sub-facturează.
  - **Internațional:** factura iese în **moneda comenzii** (CZ→CZK, PL→PLN, BG→EUR de la trecerea Bulgariei la euro) +
    echivalentul **RON** pt ANAF — automat, per comandă (verificat pe PDF-urile reale).

### ⚠️ SmartBill: rate-limit + erori de business (lecție 2026-06-28 — CITEȘTE înainte de bulk)
- **Rate-limit REAL ≈30 requesturi/fereastră** (header `X-RateLimit-Limit: 30`). La depășire → **HTTP 422** cu mesaj
  ROMÂNESC „Ai depasit limita maxima de requesturi admisa. Vei putea executa alte requesturi **dupa N min**" =
  **penalizare lipicioasă ~10 min** (NU se ridică cu pauze scurte; fiecare re-încercare ÎN timpul blocării **resetează** timerul).
  De aceea `inv-bulk` **pasează adaptiv ~24/min (2.5s/factură)**, SUB plafon, cu headroom pt fluxul normal de facturare
  al xConnector (consumă din ACELAȘI bucket), și **parsează cooldown-ul exact** din mesaj („dupa 10 min" → așteaptă 630s fără re-atac).
- **422 = ȘI rate-limit ȘI erori de business** — se distinge după **MESAJ**, nu după status code. `inv-bulk` tratează 422 ca
  rate-limit DOAR dacă mesajul confirmă; altfel = eroare reală (NU retry — altfel pierde ~12 min/comandă) + o raportează la final.
- **Erori de CONFIG frecvente (blochează facturarea — fix în xConnector/SmartBill, NU prin retry):**
  „Produsul **LIVRARE EXPRESS** nu are codul specificat" / „Produsul **PROTECTIE COLET** nu are codul specificat" —
  linia de serviciu (livrare expres / protecție colet) n-are **cod de produs** în SmartBill. Apare pe multe magazine
  (Belasil, deals…) și e **și motivul pt care fluxul normal le-a lăsat nefacturate**. Fix = dă cod produselor de serviciu
  (config xConnector/SmartBill); apoi re-rulezi `inv-bulk`. SmartBill API n-are endpoint de produse (doar `GET /stocks`) → fix din UI/config.

## Auth (cheie API xConnector + token Shopify Admin, per magazin)
- xConnector: secret KB **`XCONNECTOR_SHOPS`** (JSON `[{shopDomain,apiKey}]`), altfel `~/.aac/input.json`.
- Shopify (pt tagul „duplicata"): secret KB **`SHOPIFY_ADMIN_TOKENS`** (JSON `[{prefix,shopDomain,adminToken}]`).
- Cheile **nu se printează niciodată**. Din **2026-06-24** avem chei pe **toate cele 19 magazine active**
  (toate cu `ROLE_AUTOMATION` + 17 permisiuni, expiră 22-sep-2026), nu doar George Talent.

## Magazine EXTERNE (CZ/PL/BG/HU/SK) — nomenclator național + HERE Geocoding, curier DPD Romania
Validatorul de adrese xConnector e **centrat pe România** → magazinele externe (**Bonhaus CZ `vthuzq-7j`,
PL `f0yrmh-ia`, BG `ux1x6n-n2`, HU `63e901-2f`, SK `16w7xv-0w`**) primesc `WRONG`/`UNKNOWN` în masă (false-positive, BG ~98%). KPI-ul nostru e
**AWB făcut**, deci pe externe `fulfill` NU folosește validatorul RO, ci **întâi nomenclatorul național** (`intl_nomen`
dispatcher: CZE→`cz_addresses`, POL→`pl_addresses`, BGR→`bg_localities`, **HUN/SVK→`geonames_localities`+`geonames_streets`**),
apoi **HERE Geocoding** ca fallback (`here_validate`, cheie KB `HERE_API_KEY`): geocodează adresa în `countryCode` și dacă
`queryScore ≥ 0.9` (`HERE_MIN_SCORE`) → face AWB; sub prag (sau eroare HERE) → **fail-closed** = lasă la CS, nu face AWB.
Curier = **DPD Romania** (livrează cross-border, ca toate). Externele **NU intră** în corecția de text RO (`ai-correct-address`).
Test CZ (dry-run): din 52 unfulfilled, 31 validate HERE → AWB, 21 chiar proaste → CS. Cheile lor rămân utile și pt AWB/facturi.

**HU/SK nomenclator (`geonames_nomenclator.py`, 2026-07-27)** — sursă **GeoNames postal** (`geonames_localities`: HU 3571 + SK 5233)
+ **street-level din OSM** (`geonames_streets`: HU 43.762 + SK 27.799 străzi, din Geofabrik `.pbf` via `geonames_streets_build.py`).
`gn_validate_and_correct` (generic per țară): (1) localitate reală → valid, zip lipsă → completează **din STRADĂ** (cod
poștal stradă-specific, `find_street` fuzzy ≥0.86) sau din localitate; (2) zip→oraș (`locality_for_pc`, **space-agnostic** —
SK stochează „974 01", clientul dă „97401"); (3) typo localitate fuzzy ≥0.9; (4) → HERE. **De ce street-level:** la orașe
mari zip-ul „central" al localității e greșit — Budapest+„Váci utca" → **1052** (District V), NU zip generic Budapest;
Bratislava+„Hlavná" → **83101**. Verificat pe **comenzi reale**: Bonhaus HU 800 → **100% rescue** (414 străzi confirmate în OSM),
SK 800 → **99% rescue** (412 confirmate; 3 reziduu = garbage real: adresă PL pe magazin SK, zip invalid+typo). Rebuild nomenclator:
`uv run geonames_streets_build.py HU hungary.osm.pbf` (idempotent, DELETE per-country + COPY). Tabelele-s în `metrics.public` (central → cronul VPS le citește).

## Erori de CURIER care NU-s de adresă (le repară cronul singur)
Trei clase de eșec 422 la `create-shipping-label` arată ca „adresă proastă", dar n-au nicio legătură cu adresa.
Le rezolvă `fulfill` automat, fiecare O SINGURĂ dată per comandă (marker în `.name_fixed` / `.ro_phone_fixed` /
`.intl_email_synthed`), apoi reîncearcă AWB-ul pe loc; dacă xConnector n-a resincronizat încă → tura următoare.

| Eroare DPD | Cauză reală | Ce face cronul |
|---|---|---|
| `receiver.name.client_name_validator.invalid-name` („cel puțin 2 cuvinte") | clientul a scris **un singur nume** în formularul COD (`Kaló`, `Edit`, `Csabi`), al doilea câmp rămâne `-` | **REGULA (owner, 2026-08-18): dacă există UN SINGUR nume, pune-l de DOUĂ ORI** — `Kaló` → `Kaló Kaló`. `dup_single_name()` + event `name-dup`. NU inventăm un nume de familie inexistent; coletul pleacă, curierul sună oricum pe telefon.<br>**Nume FĂRĂ nicio literă** (clientul a scris TELEFONUL în câmpul de nume, ex. `0752 109 578`) → dublarea n-ar ajuta (tot cifre), deci se pune placeholder neutru **`Client <Oraș>`** (fallback `Client Nou` fără oraș). Nu inventăm identitatea nimănui: eticheta poartă un nume evident generic, iar alternativa e ca vânzarea să stea blocată la infinit (măsurat: ~8 comenzi/90 zile pe magazinele RO, descoperite abia când a întrebat un om).<br>⚠️ **Capcană verificată înainte de a automatiza:** pe Bonhaus BG, AWBprint arată 876 de comenzi al căror `customer_name` e numai cifre — dar în xConnector numele reale sunt chirilice (`Марияна Цветанова`). E artefact de mapare în AWBprint, NU clienți care scriu telefonul. Regula testează prezența LITERELOR, deci nu atinge numele chirilice/grecești — altfel ar fi corupt 876 de nume reale. |
| `receiver.email.not-empty` (uneori confuz `recipient.id_or_client_name`) | comandă intl fără email; WPO cere email obligatoriu | sintetizează `awb-<comanda>@arona.ro` pe `order.email` (event `intl-email-synth`), doar dacă nu există email real |
| telefon RO malformat (`751842097`, `+400…`) | format greșit din formular | `ro_phone_fix` → normalizează la `07xxxxxxxx` (event `ro-phone-fix`) |

**Limite HARD ale curierului (NU se repară din cod — cer schimbare de configurare):**
- `sla.insurance.insBaseAmount.lesser-or-equal` — valoarea declarată depășește maximul asigurabil al serviciului
  (**HU: max 26.250 HUF**). O comandă de 31.660 HUF NU poate primi AWB până nu se scoate/plafonează „extinderea de
  răspundere" pe connectorul DPD din xConnector (sau se activează un al doilea curier — pe HU e inactiv `PACKETERY
  HU Home Delivery HD [20320]`). Măsurat 2026-08-18: HU1924/HU1937/HU1939 blocate din cauza asta.
- `content.parcelsCount.parcel-count-out-of-range` — serviciul acceptă **[1, 1]** colete (măsurat pe SK). Comandă cu
  3 produse → `parcelCount` auto = 3 → respinsă. Se face cu `awb-make --parcels 1`.
- `receiver.address.addressLine2.max-length` — max **35 caractere** pe adresa 2 (SK2315: stradă de 37 → prescurtat
  `českého`→`čes.` și mutat în `address1`).

## Magazine FĂRĂ Customer Service — cronul rezolvă TOT, zero HOLD
`NO_CS_DOMAINS` = **SK `16w7xv-0w`, HU `63e901-2f`, Orice Redus `oriceredus`** (decizie owner, 2026-08-18).
Pe magazinele cu CS, o comandă tag-uită `duplicata` al cărei conținut DIFERĂ de „geamăna" ei se pune pe **HOLD**
ca s-o judece un om. Pe magazinele de mai sus **nu lucrează nimeni coada**, deci HOLD-ul = comandă moartă pe veci:
măsurat **SK2287 (84,37 €) blocată din 16-aug**, SK2270 (26,98 €) din 14-aug — ambele aveau ALTE produse decât
comanda „geamănă", deci erau comenzi REALE, nu dubluri. → pe `NO_CS_DOMAINS`, `resolve_duplicate` întoarce **`ship`**
(fă AWB) în loc de `held`. **Anularea automată rămâne NESCHIMBATĂ peste tot**: doar dublura TEHNICĂ (aceleași SKU-uri
ȘI aceeași sumă) se anulează. Magazinele cu CS (Esteban etc.) păstrează HOLD-ul — verificat A/B că nu s-a schimbat nimic.

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
- **tag `influencer`** (cadou UGC, 100% discount, flux separat) → **NU se face AWB** (skip ÎNAINTE de logica draft/team, fiindcă multe sunt draft orders). La fel `awb-make` manual îl refuză (cu `--force` se poate forța).
- **BLOCKLIST client** (serial-refuseri/fraudă) → **NU se face AWB + se ANULEAZĂ comanda** (ca duplicat: reason OTHER, restock, protecție livrare — nu anulez ce a plecat). Cheia = **customer GID per magazin** din KB `XCONNECTOR_CUSTOMER_BLOCKLIST` (JSON `{shopDomain:[gid,...]}`, editabil fără redeploy). De ce GID și NU email/telefon: magazinele-s pe plan **Basic → PII (email/telefon) e BLOCAT** pt app-token; customer GID e non-PII + stabil per magazin. Adaugi un client: ia-i GID-ul (`orders(query:"name:X"){...customer{id}}`) pe fiecare magazin unde comandă și pune-l în secret. Vezi [[serial-refuser-blocklist]].
- **tag de duplicat** (`duplicata`/`duplicata3`/`duplicat4`) → regula Flow-urilor: **păstrează cea mai NOUĂ** comandă a clientului
  (7 zile) → îi fac AWB; **cele VECHI** → le **anulez** (reason OTHER, fără refund/restock/notify, **protecție livrare**: nu anulez
  ce a plecat). **CS-placed / draft order** (tag agent CS sau `sourceName=shopify_draft_order`) → **NU se dedup-ează, dar PRIMESC AWB**.
  Fără client / status incert → NU expediez, NU anulez (conservator — erorile API cad pe „skip").
- **`t` în NOTE = CS a corectat manual → DE TRIMIS** (cerut de owner). CS pune marker-ul `t` în nota comenzii
  după ce corectează adresa. `fulfill` îl detectează (`t` ca token de sine stătător, case-insensitive: „t"/„T"/„corectat t"
  → da; „trimite"/„test" → nu) și **forțează AWB**, sărind ruta WRONG→CS. ⚠️ Aplică TOTUȘI corecția automată
  (nomenclator RO / intl) înainte — „poate CS n-a verificat exact zip-ul". Dar **`t` NU face o adresă genuin greșită să
  devină bună**: dacă rămâne `WRONG`, DPD tot respinge AWB-ul (→ eșec/retry) — atunci CS trebuie să repare **adresa
  reală** în Shopify, nu doar să pună `t`. Dedup rămâne activ (rulează înaintea deciziei de adresă).
- **`fără xc` → tag `i` AUTOMAT.** Comenzile unfulfilled+open care NU apar în xConnector (nesincronizate) nu pot primi
  AWB; `fulfill --apply` le pune tag-ul **`i`** în Shopify (idempotent) ca să fie găsibile (filtru pe tag). Cerut de owner:
  „când nu apar, pus tag".
- **DEJA EXPEDIAT (fulfillment anulat) → NU re-expedia** (gardă anti-dublă-expediere). O comandă LIVRATĂ căreia i s-a
  ANULAT fulfillment-ul reapare în Shopify ca `unfulfilled+open` (deși are tracking). `shopify_unfulfilled` citește
  `fulfillments.trackingInfo`; dacă are ORICE tracking → `shipped=True` → `fulfill` o **sare** (contor
  `deja-expediat-fulfillment-anulat`). Fără asta, cronul i-ar face AWB NOU = coletul pleacă de 2 ori (bani pierduți).
  **Fix-ul pt astea = re-pune fulfillment-ul** cu tracking-ul original (`fulfillmentCreateV2`, `notifyCustomer:false`) +
  marchează paid dacă nu e. Incident 25-iul: 7 comenzi (5 CZ Packeta + Lux + Nubra), toate PAID+livrate, fulfillment
  anulat → re-fulfilled. Vezi [[fulfillment-cancelled-reship-guard]].
- **ZIP LIPSĂ RO → cod GENERAL al orașului** (`ro_city_general_zip`, în `nomenclator_correct`), cu GARDĂ pe
  mărimea localității. Când zip-ul lipsește și strada e REALĂ dar neindexată în nomenclator/HERE (owner: „zip-ul
  e pentru străzile care nu-s indexate altfel"), iar ORAȘUL e valid → `MIN(cod_postal)` pe localitate din
  `romania_addresses` (codul general, ex. Buzău→120001, Constanța→900003, Oradea→410001). Data-driven, orice oraș.
  **Gardă (owner):**
  - **Oraș MARE** (`ro_city_is_big` = >25 coduri poștale distincte pe localitate) → codul general se aplică DOAR
    dacă adresa are **stradă+număr real**, căutat în TOATE câmpurile (a1/a2/oraș/județ — „adresa poate fi oriunde";
    `_addr_has_street_and_number`). Fără stradă+număr (doar oraș / „undefined" / nume firmă) → **CS**.
  - **RURAL/sat mic** → merge și FĂRĂ număr (numele localității ajunge curierului); dacă n-are stradă deloc →
    **„Strada Principală"** (+ nr dacă e undeva; `_rural_street`).
  - ⚠️ Străzile INDEXATE (ex. **Bulevardul Mamaia** Constanța, cu intervale de numere: nr.1→900697, nr.20→900673,
    nr.100→900527) sunt rezolvate SPECIFIC de nomenclator (conștient de `numar` = interval) — NU cad pe codul general.
    Abrevierile („bvd."/„b-dul"/„bd") se expandează la „Bulevardul" în pre-clean, deci se leagă de nomenclator.
  NU suprascrie un zip existent. Deblochează cat. „zip lipsă" (~25% din reziduul CS). Vezi [[address-correction-rules]].
- **Field-agnostic (owner „orice câmp de adresă poate fi în ORICE câmp")** — set de reguli în pre-clean-ul
  `nomenclator_correct`, din analiza cozii CS (`cs_queue.db`): **(a)** ZIP din orice câmp (`_zip_from_fields` +
  gardă `_zip_matches_city` să nu contrazică orașul — ex. nu suprascrie Buzău cu 905800=Constanța); **(b)** city
  cu „Județ X" (inclusiv diacritice Ț/ț: „ZalĂu JudeȚ SĂlaj"→„Zalău", apoi județ-din-oraș→Sălaj); **(c)** termeni
  MAGHIARI (`_translate_hungarian`: „Palás köz 7 szám"→„Aleea Palás nr 7"; univoc: utca/tér/köz/körút/szám/megye,
  nu „ter"/„ut" simple); **(d)** typo „Stada"→„Strada". ⚠️ Nomenclatorul PRINDE DEJA: sat/comună din a1
  (Fulga/Parța/Chirileu), zip-in-city-field („305600"→Sânnicolau Mare), Sector→București, deglue — reziduul era
  stuck pe APLICARE (cache `.here_ok`, reparat), nu pe reguli lipsă.
- **JUDEȚ din ORAȘ** (`ro_judet_from_city` în pre-clean-ul `nomenclator_correct`). Owner: „orice câmp poate fi
  în orice câmp" — câmpul `province` e des defaultat GREȘIT (Slatina/Constanța/Voluntari/Câmpina puse ca
  „București"; sectoare Buc puse ca „Ilfov"). Dacă orașul e NE-ambiguu în nomenclator (sau municipiu dominant la
  omonime: Slatina→Olt, nu satele omonime) și județul diferă → OVERRIDE province cu județul orașului, ÎNAINTE de
  lookup (scopează corect + fixează bug-ul „Slatina→a matchuit o stradă în București"). Sub-tipar dominant în
  reziduul CS: **București↔Ilfov** (Voluntari/Chiajna/Mogoșoaia = Ilfov). Măsurat ~30 din reziduu. Vezi [[address-correction-rules]].
- **Fallback DPD general-zip la AWB-make** (`ro_genzip_fallback` în `_do_awb`, RO-only, o dată/comandă via
  `.ro_genzip_fixed`). Owner: „dacă nu merge [zip-ul specific] să faci AWB la DPD, pune codul general". Când DPD
  respinge PERMANENT (non-tranzitoriu — ex. `receiver.address.siteId.valid-locality-id`, NU „was not created")
  un zip specific → pune codul GENERAL al orașului → reîncearcă O DATĂ. Rulează DUPĂ `dpd_fix_locality` (findSite pe
  zip) + phone-fix, ÎNAINTE de escaladarea agentică. (Un cod poștal specific descoperit se poate salva permanent în
  nomenclator, ex. Șoseaua Nordului Buzău→120320: `INSERT` în `romania_addresses`, rolul `scraper` are drept.)
- **SNAP la strada CURIERULUI** (`courier_street_snap` în `nomenclator_correct`). Owner „dacă mai igienizăm":
  când nomenclatorul RO zice adresa validă DAR curierul (xConnector) o ține **WRONG/UNKNOWN** fiindcă strada nu-i
  în baza LUI, validatorul curierului sugerează strada apropiată (`addressMatchers[].streetName`). Dacă **scor≥0.80
  ȘI string-similară** cu ce a scris clientul → folosim sugestia (păstrând nr casă): „Bulevardul.Tineretului"→
  „tineretului" (0.87)→VALID · „Leghes"→„leghesului" · „Mureșului"→„mureș". **GARDĂ obligatorie de similaritate**:
  „Minovici"→„caloian vasile" (scor 0.80 dar stradă COMPLET diferită) e RESPINS (difflib+prefix). Ce nu are sugestie
  bună (Arțarului 0.41) → CS. Dovedit: EST221376 a devenit VALID.
- **Recuperare la fails==2** (`_do_awb`): când `correct_address` (xc.match, conservator) dă „manual", cheamă și
  `nomenclator_correct` (bogat: pre-clean/deglue/fix-JUDEȚ/snap-curier) — prinde comenzi altfel pierdute via-HERE.
- ⚠️ **Citirea adreselor în masă: RETRY pe `by_id`.** La colectare paralelă (>~8 workeri), `xcl.by_id(orderId)` poate
  pica pe rate-limit și întoarce gol → **NU trata „adresă goală" ca reală** (poate fi doar fetch eșuat). Fă retry
  (3-4×, backoff) + concurență mică. `by_id` e METODĂ pe instanța XC (`xcl.by_id`), NU pe modul. Câmpul `shippingAddress`
  (+ `originalCustomerAddress`, `latestAddressValidation.addressMatchers` = motivul WRONG pe componente zip/oraș/stradă).
- **Grandia auto-rutează** (voluminos → Dragon Star, restul DPD) — nu mai trebuie `--exclude`. Sare automat magazinele cu AWB deja făcut.
- **Dry-run by default.** Sursa „plecat" = AWBprint. Consistent cu cele 2 Shopify Flow-uri de duplicate (NU le înlocuiește — le completează).
- **Stare/cache** (`.cron_giveup`/`.here_ro_nogo`/`.held_sweep`/`.awb_failcount`/`.dpd_corrected`/`.intl_sanitized`/`.ro_phone_fixed`/`.ro_genzip_fixed`):
  fac cronul să SARĂ backlog-ul deja încercat. Ca să re-încerci TOT de la zero (ex. după ce CS a corectat în masă),
  **șterge-le** (păstrează dedup-ul — ăla e pe tag-uri Shopify, nu pe fișiere). `--held-sweep-hours 0` forțează hold-urile.

## Cron (VPS)
`correct --apply` rulează periodic pe VPS (flock + log, `0 8-20 * * *`): corectează automat ce e sigur, sare
duplicatele și comenzile proaspete (`--min-age-hours`), scoate triajul CS. Vezi `gigi:xconnector` în KB pt detalii
deploy. Pereche cu [gigi:cs-address-guard].
