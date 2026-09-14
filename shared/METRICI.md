# METRICI — dicționarul unic

> **Regula:** o metrică are UN loc unde e definită și UN loc de unde se citește. Dacă un
> raport dă alt număr decât ce scrie aici, raportul e greșit, nu dicționarul.
> Auditat integral **30 iulie 2026** (reconciliere + watchdog rulate, rezultate mai jos).

---

## 0. Formula-mamă

```
Contribuție = Venit − COGS − Transport − Marketing
```

- **ex-TVA** pe venit / COGS / transport (TVA e deductibil)
- **marketing NET** (fără TVA de adăugat)
- **doar comenzile LIVRATE** intră în venit

Totul e implementat într-un singur fișier: **`profit_core.py`**. Orice motor nou de profit
îl **importă**, nu rescrie formulele.

---

## 1. Metricile, una câte una

| Metrică | Definiție | Sursa de adevăr | Cum o iei |
|---|---|---|---|
| **Contribuție / profit net per brand** | venit − COGS − transport − marketing, ex-TVA, doar livrate, lunar | `cache.brand_pnl_monthly` | `gigi:multi-brand-pnl` |
| **Venit** | `total_price` al comenzilor livrate ÷ TVA, minus retururi Shopify | AWBprint `orders` | via engine |
| **COGS** | Shopify `unitCost` × buc ÷ 1,21. **Costul e RON pe TOATE magazinele** — nu-l converti cu cursul | Shopify | `profit_core.cogs_ron()` |
| **Transport** | `orders.transport_cost` ÷ 1,21 — **UN AWB principal per comandă**, autoritativ | AWBprint `orders.transport_cost` | `profit_core.parcel_transport()` |
| **Marketing (brand)** | spend Meta+TikTok+Google, RON | `cache.daily_ad_spend_ron` <- `AWBprint.marketing_daily_costs` <- sheet Raport Zilnic 2 | engine |
| **Marketing (per SKU)** | de la cutover **19-06-2026**: sheet WMS per-ad; înainte + Google: `cache.product_ad_spend` | `wms_ad_spend_sync.py` | `wms_marketing.py` |
| **Profit per SKU / categorie** | venit RON ex-TVA reconciliat cu engine, COGS+override, transport real, marketing pe comenzi | `profit_orders` + `profit_order_lines` | `profit_by_sku.py YYYY-MM` |
| **Bucăți vândute per produs** | qty pe linie | Shopify / AWBprint | `gigi:product-sales` |
| **Livrabilitate / rată refuz** | livrate ÷ expediate | AWBprint `aggregated_status` | `gigi:fulfillment-analytics` |
| **Breakeven CPA / ROAS** | model de PLANIFICARE (COGS% + transport median), NU actuals | `brandref` | `breakeven.py` |
| **Cost transport real per comandă** | vezi Transport | AWBprint | ⚠️ **NU** suma `order_awbs` — rândurile sunt duplicate |

**Cota TVA pe țară** (`profit_core.VAT_BY_COUNTRY`): RO 21 · BG 20 · CZ 21 · PL 23 · HU 27 · SK 23 · HR 25.

---

## 2. Capcana #1: „livrat" are DOUĂ vocabulare

Ăsta e cel mai frecvent mod de a obține numere greșite. Sunt două tabele, în două limbi:

| Sursă | Coloană | Valoarea pentru „livrat" | Câte valori distincte |
|---|---|---|---|
| **AWBprint** (Postgres) | `orders.aggregated_status` | **`delivered`** (engleză, snake_case) | 19 |
| **engine** (SQLite `profitability.db`) | `profit_orders.status_category` | **`Livrata`** (română) | 7 |

Un `WHERE aggregated_status='Livrata'` întoarce **zero rânduri** și pare că brandul n-a livrat nimic.

Vocabularul AWBprint, complet: `delivered`, `back_to_sender`, `cancelled`, `not_fulfilled`,
`in_transit`, `fulfilled`, `waiting_for_courier`, `lost_in_transit`, `unsuccessful_delivery`,
`returning_to_sender`, `customer_pickup`, `incorrect_address`, `refused`, `deferred_delivery`,
`redirected`, `awaiting_shipment_generation_initialization`, `on_hold`, `lost`,
`errors_incorrect_shipping_address`.

Vocabularul engine: `Livrata`, `Refuzata`, `Anulata`, `Netrimisa`, `In curs de livrare`,
`Lipsa awb`, `UNMAPPED`.


## 2a. Capcana #2: „bucăți vândute" are TREI înțelesuri, toate corecte

Cea mai frecventă sursă de „skill-urile dau cifre diferite". **Nu dau cifre diferite pentru aceeași
întrebare — răspund la întrebări diferite.** Măsurat pe același produs, aceeași lună completă
(Grandia, „Raft metalic 5 polițe", mai 2026):

| Cifră | Sursă | Ce numără | Când o folosești |
|--:|---|---|---|
| **329** | ERP Grandia (`OrderLineItem.createdAt`) | comenzi **PLASATE** | cerere de piață, eficiența reclamelor (ads produc comenzi, nu livrări) |
| **310** | AWBprint gross | bucăți care au primit **AWB** | ce a ieșit din depozit, planificare transport |
| **268** | AWBprint net | după anulări/refuzuri/retururi | **bani reali** — singura care intră în P&L |

Diferențele nu sunt erori: `329 − 310` = comenzi plasate care n-au plecat încă;
`310 − 268` = ce s-a întors. **Regula:** pentru profit folosește NET; pentru marketing folosește
PLASATE (altfel judeci reclamele după livrabilitate, nu după cât au vândut).

### 🔴 Regula anti-„reconciliere inventată" (incident 18-aug-2026, `GD-BR-19651`)
Un run a comparat **319 bucăți** cu **165 comenzi**, a inventat un raport „~1,9 buc/comandă" ca să
pară că se potrivesc, și a declarat corectă cifra greșită. **Nu reconcilia niciodată două numere
printr-un raport dedus.** Adu-le la aceeași unitate și verifică dacă **totalul se închide**:
`livrate + refuzate + anulate + în curs = total comenzi`. Dacă nu se închide, n-ai reconciliat nimic.

### Rețeta canonică: „câte comenzi LIVRATE / REFUZATE are produsul X"
Trei surse independente dau **exact** aceleași cifre (verificat pe `GD-BR-19651`: 200 / 23 / 12 / 1,
total 239 = totalul din ERP):

| Sursă | Cum |
|---|---|
| **skill** (folosește-l pe ăsta) | `gigi:fulfillment-analytics --report refuse --by product --sku <SKU> --stores <PREFIX>` |
| **Shopify / bi.grandia** | `Fulfillment.deliveryStatus` = `DELIVERED` / `NOT_DELIVERED` / `CANCELED` / `OUT_FOR_DELIVERY` |
| **AWBprint** | `orders.aggregated_status` = `delivered` / `back_to_sender` / `cancelled` |

⚠️ Trei capcane care au produs cifre false pe exact această întrebare:
1. **SKU-ul e IMBRICAT** în AWBprint: `item->'inventory_item'->>'sku'`, nu `item->>'sku'` (varianta
   plată → 0 rânduri → „produsul n-a existat"). Iar `quantity` vine `"4.0"` → cast la `numeric`, nu `int`.
2. **O comandă are mai multe fulfillment-uri** (o refuzată are și unul `CANCELED`) → clasifică per
   comandă cu prioritate `DELIVERED > NOT_DELIVERED > OUT_FOR_DELIVERY > CANCELED`, altfel suma
   depășește totalul.
3. **Engine-ul (`profitability.db`, `profit_orders.status_category`) SUBESTIMEAZĂ** pe istoricul unui
   produs — `profit_order_lines` începe la 2026-01 (a dat 193/18 în loc de 200/23). E bun pe bani/lună,
   nu pe „de când există produsul".

⚠️ **Înainte să compari două cifre, verifică FEREASTRA.** Cel mai des „discrepanța" e că una e
1-28 și cealaltă luna calendaristică. ERP-ul lucrează pe lună întreagă;
`gigi:product-sales` acceptă `--from/--to`. Compară doar ferestre identice.


## 2b. Capcana #3: marketingul din P&L vine din SHEET, nu din warehouse

Lantul complet, descoperit 30-iul-2026:

```
sheet "Raport Zilnic 2" -> AWBprint.marketing_daily_costs -> cache.daily_ad_spend_ron -> engine -> brand_pnl
```

Tabelele Meta din warehouse (`meta_ad_insights_daily`, `meta_campaign_insights_daily`) **NU
alimenteaza P&L-ul** - sunt pentru analiza de ads. Consecinte practice:

- Un brand poate avea spend real in Meta, complet sincronizat in warehouse, si **zero marketing
  in P&L** daca randul lui nu ajunge in `marketing_daily_costs`.
- Cand marketingul unui brand pare gresit, verifica in ordinea asta: **sheet -> marketing_daily_costs
  -> cache.daily_ad_spend_ron**. Nu porni de la tabelele Meta - ele arata alta realitate, corecta
  pentru ads, irelevanta pentru profit.

### Ingestia sheet -> tabela PIERDE branduri (verificat 30-iul-2026)

Sheet-ul "Raport Zilnic 2" are **31 de branduri**; `marketing_daily_costs` are **21 de magazine**.
Ingesterul (in aplicatia AWBprint) mapeaza brand -> `store_name`, si **nu are regula pentru 10
dintre ele**. Spend-ul lor nu ajunge niciodata in P&L:

| Brand in sheet | Spend RON | Perioada | Stare |
|---|--:|---|---|
| **Lab Noir** | **30.093** | 4-29 iul 2026 | 🔴 **ACTIV** |
| Genti Promo | 26.743 | apr-dec 2025 | istoric |
| Nocturna SK | 8.035 | ian-apr 2025 | istoric |
| Nocturna HU | 7.600 | ian-iul 2025 | istoric |
| Esteban Parfum | 6.668 | apr-iul 2025 | istoric |
| Nocturna HR | 5.693 | ian-apr 2025 | istoric |
| Nocturna PL | 2.770 | ian-apr 2025 | istoric |
| Nocturna CZ | 2.687 | ian-apr 2025 | istoric |
| Bonhaus SK | 1.278 | mai-iul 2025 | istoric |
| Super Detergent | 241 | ian-apr 2025 | istoric |

**Total 91.808 RON**, din care doar Lab Noir e curent. Fixul e in ingesterul AWBprint (adauga
maparea `Lab Noir` -> un `store_name`), nu in sheet - sheet-ul e corect.

⚠️ **Numele NU se potrivesc 1:1.** "Bonhaus RO" din sheet = `casaofertelor.ro` in tabela
(verificat: 432 zile, aceeasi perioada, 1.412.091 vs 1.419.686 RON). Orice comparatie automata
pe nume da fals pozitiv aici - confirma pe volum si perioada, nu pe sirul de caractere.



---

## 2c. REGISTRUL UNIC de branduri (consolidare 31-iul-2026)

Aceeasi mapare brand<->magazin<->prefix traia in **~25 de fisiere**: `profit_core`, `core/brands.py`,
`stores.csv`, `BRAND_TO_STORE` (AWBprint), `meta-ads/brand_map.json`, `tiktok-ads/brandmap.py`,
`STORE_OVERRIDES` din build_cache, plus o copie in `scratch/sync_marketing_direct.py`.
Sapte "registre" cu marimi diferite: 31 / 22 / 22 / 22 / 22 / 21 / 20 intrari.

**Acum:** `/root/Scripturi/brands.json` = SURSA UNICA (22 branduri, generat din `metrics.brands` +
maparile existente, zero conflicte). Se citeste cu `brand_registry.py`:

```python
from brand_registry import REGISTRY, brand_of_prefix, prefix_of_brand, check_drift
```

`data_health.check_brand_registry` ruleaza `check_drift()` zilnic si da 🔴 la orice divergenta
intre registru si mapările ramase in cod. Destramarea nu mai poate fi tacuta.

### BUG MAJOR gasit de consolidare: prefixul BG era cross-wired

`PREFIX_BRAND["BG"] = "Bonhaus BG"` dar `PREFIX_AWB_DOMAIN["BG"] = "nocturna.bg"`. Doua prefixe
(`BG` si `BONBG`) indicau spre acelasi brand, desi sunt magazine diferite.

**Efect:** 5.077 comenzi / 3.307 livrate / **130.654 RON venit brut** de la magazinul `nocturna.bg`
au fost raportate ca **Bonhaus BG** timp de 9 luni (oct 2025 - iun 2026). Bonhaus BG aparea umflat
cu 57% (raportat ~360k in loc de 229.897 real), iar **Nocturna BG** aparea cu venit ZERO desi
cheltuise 28k pe marketing in feb-mar - adica pierdere pura, fals.

Reparat: `PREFIX_BRAND["BG"] = "Nocturna BG"`. Dupa rebuild, aprilie 2026 arata corect separate:
BG/Nocturna BG = 5 livrate, BONBG/Bonhaus BG = 2.475.

**Lectia:** doua prefixe care indica spre acelasi brand = aproape sigur un bug. Registrul le prinde
acum automat (un brand are UN prefix).

### Lab Noir - complet in P&L (iulie 2026)

| | |
|---|--:|
| livrate | 1.160 |
| venit ex-TVA | 141.085 |
| COGS | 26.503 |
| transport | 14.300 |
| marketing | 31.045 |
| **contributie** | **69.237 (49,1%)** |


### Motorul extins pe 2025 (31-iul-2026)

P&L-ul acopera acum **2025-04 .. 2026-07** (293 randuri, 22 branduri), fata de 2026-02 inainte.
**Contributie 2025 nou vizibila: 9.408.824 RON** pe 9 luni.

**Cum, si de ce NU dureaza zile.** `profit_orders_sync.py` are doua faze: (1) descarca comenzile
din Shopify + calculeaza COGS si `status_category` din datele de fulfillment, (2) verifica AWB-urile
la curier ca sa rafineze statusul. Pe 2025 faza (2) e inutila si foarte lenta — Sameday sterge
tracking-ul dupa ~45 zile, fisierele bulk DPD nu acopera anul trecut, deci cade pe verificare
una-cate-una (**45.000 de apeluri pe luna**, ore intregi). Masurat: dupa faza (1) statusurile sunt
deja **99,8%** rezolvate (2025-12: 108 nerezolvate din 59.211).

Deci driverul `/root/Scripturi/backfill_2025.py`: porneste sync-ul, il **opreste cand incepe faza
de tracking**, apoi completeaza restul din AWBprint (`aggregated_status`, autoritativ si FINAL
pentru 2025). Rezultat: **50-100 secunde pe luna** in loc de ore. 33.035 statusuri completate.

⚠️ **Fereastra cache-ului.** `build_cache.py` construia `brand_pnl_real` doar pe ultimele **6 luni**
(`last_months(6)` hardcodat) — extinderea motorului nu ajungea niciodata in cache. Acum e
configurabila: `BRAND_PNL_MONTHS=18 run_cache.sh --table brand_pnl_real --apply`. Implicit ramane 6
pentru rularea zilnica.

⚠️ **COGS istoric e aproximativ.** Vine din `unitCost`-ul CURENT din Shopify, nu din costul de
atunci. Pentru 2025 il citesti ca ordin de marime, nu ca cifra contabila.

### Brandurile fara comenzi — spend recuperat, dar NU apar in P&L

Cele 8 branduri oprite in 2025 (Genti Promo, Nocturna HU/PL/SK/CZ/HR, Bonhaus SK, Super Detergent)
au fost create in `metrics.brands` si mapate in ingester. Spend-ul lor — **55.047 RON** — e acum in
`marketing_daily_costs` si `cache.daily_ad_spend_ron`.

**Dar nu apar ca linii in `brand_pnl_monthly`**: motorul e condus de COMENZI, iar ele n-au livrat
nimic prin AWBprint (verificat: niciun brand din P&L nu are 0 livrate si marketing > 0). Deci
totalul de grup pe 2025 supraestimeaza profitul cu ~55k (0,6% din 9,4M). Reprezentarea lor ca linii
ar cere ca motorul sa porneasca de la branduri, nu de la comenzi — alta arhitectura.

**Esteban Parfum NU e brand separat** — a fost numele initial al George Talent. De aceea comenzile
`ESTP` (315, apr-iun 2025) sunt pe magazinul `georgetalent.ro`. Mapat catre `georgetalent.ro`.

**Ingesterul nu mai pierde nimic:** dupa cablare, sync-ul pe 2025-01..2026-07 raporteaza
`10.478 records, 30 stores, branduri nemapate: niciunul`.

### Nocturna BG dupa reparare — era profitabil, nu pierdere

Inainte de fixul prefixului BG aparea cu venit ZERO si 28k spend = pierdere pura. Realitatea:

| Luna | Livrate | Venit ex-TVA | Marketing | Profit |
|---|--:|--:|--:|--:|
| 2026-02 | 760 | 81.398 | 17.977 | **+25.539** |
| 2026-03 | 253 | 27.264 | 10.086 | **+3.923** |
| 2026-04 | 5 | 498 | 0 | +253 |

Venitul asta era creditat lui **Bonhaus BG**, care aparea corespunzator umflat.

---

## 3. Unde e definit codul (și cine e aliniat)

**Sursa unică:** `profit_core.py` — `vat_for_country/prefix`, `cogs_ron`, `parcel_transport`,
`refusal_transport_multiplier`, `is_revenue`, `allocate_marketing_by_orders`, `prefix_brandid`,
`PREFIX_AWB_DOMAIN`, `PREFIX_BRAND`, `PREFIX_COUNTRY`, `REVENUE_STATUSES`.

**Importă `profit_core` (deci sunt aliniate):** `api/profitability.py` (engine-ul canonic),
`profit_by_sku.py`, `sync_raport_zilnic.py`, `trendyol_profitability.py`,
`product_profit_calculator.py`, `cod-product-validator/validate.py`.

**NU importă, dar au constante proprii — verificate, nu diverg:**
- `api/test_products.py` — simulator pre-lansare, are propriile setări (`VAT_RATE`, `USD_RON`). Nu produce raportare.
- `gads_upload_conversions.py` — `--vat` implicit 1,21; **cronul îl cheamă doar cu `--store grandia`** (RO), deci corect.
- Hit-urile de `0.21` din `api/customer_service.py`, `api/daily_perf.py`, `api/product_analytics.py`
  sunt **cursul CZK**, nu TVA. Fals pozitiv la orice grep după TVA.

---

## 4. Cum verifici că datele sunt bune (rulează, nu presupune)

Ambele rulează pe VPS, zilnic prin cron, și trimit email **doar pe roșu**. Fără `--email` nu trimit nimic.

```bash
cd /root/Scripturi && set -a && . ./.env; set +a
.venv/bin/python data_health.py            # prospețimea DATELOR + heartbeat cronuri
.venv/bin/python reconcile_sources.py --months 3 --no-store   # drift între surse independente
```

`data_health` verifică **ieșirea** pipeline-urilor, nu dacă jobul a rulat — un cron poate scrie
în log și când sync-ul eșuează (așa s-au pierdut 11 zile de Meta în iunie).

`reconcile_sources` compară valori între surse independente: livrate engine↔AWBprint,
marketing sheet↔warehouse.

**Citirea corectă a rezultatului:** pe **luna curentă** driftul la livrate e negativ pe toate
brandurile (−1…−9%) și **e normal** — engine-ul se sincronizează o dată pe zi, AWBprint e live.
Semnal real = drift pe o **lună închisă**.

---

## 5. Starea la 30 iulie 2026

**Ce e sănătos:**
- Marketing engine↔warehouse: **0,0% drift pe toate cele 18 branduri**.
- Livrate pe lunile închise (mai, iunie): **reconciliază exact**, cu o singură excepție.
- Toate cele 9 cronuri de pipeline pinguie la timp (heartbeat verde).

**Două găuri reale:**

1. ~~Labnoir lipsește din engine~~ → **CABLAT 30-iul-2026.** Magazinul livra 1.488 comenzi din
   27-mai (527 livrate în iulie = 78.205 RON brut) fără să existe în registru. Acum e conectat:
   `stores.csv` are `LAB,31k0py-bi.myshopify.com`, `profit_core` are `"LAB": "Lab Noir"` +
   `labnoir.ro`, iar tokenul se emite automat.

   **Cum**: Labnoir folosește app-ul „ARONA" cu grant `client_credentials` — nu există token
   static și nici `refresh_token`, se emite la cerere și ține ~24h. `shopify_token_manager` a
   primit o ramură nouă (`_mint_client_credentials`) care emite când nu există rând sau când a
   expirat; rândul din `stores.csv` are tokenul GOL, intenționat, ca să meargă pe calea OAuth.
   Verificat live: shop „Lab Noir" (RON), COGS citit (`unitCost` 8/15 RON pe variante).

   ⚠️ **Marketing-ul lui NU e conectat.** Niciun cont Labnoir nu există în `meta_ad_accounts` /
   `tiktok_ad_accounts`, deși tab-ul „Mapping" listează un cont Facebook „Labnoir". Până se
   conectează, Labnoir va apărea în P&L cu **marketing = 0**, deci cu contribuție supraevaluată.
   De aceea cache-ul **nu a fost reconstruit** — cifra nu s-a schimbat încă nicăieri.

### Marketing Lab Noir — starea exactă (30-iul-2026, seara)

Contul Meta era înregistrat sub numele VECHI **„Favmag"** (`act_1599686260651356`) — fusese
redenumit în Meta în „Labnoir", warehouse-ul rămăsese în urmă. **Nu era legat de niciun brand**,
deci n-a atribuit greșit spend altcuiva. Reparat: brand `Lab Noir` creat, cont redenumit,
legătură cu `campaignFilter=NULL` (corect — cont DEDICAT, nu partajat).

Sync-ul l-a prins (`lastSyncAt` setat) și scrie de acum înainte. Istoricul l-am completat manual
în `meta_ad_insights_daily`: **27 zile, 4-30 iulie, 6.801 USD = 31.187 RON**.

⚠️ **Contul are DOUĂ vieți — nu le aduna.** Sub numele vechi „Favmag" a rulat un TEST în
30 ian – 19 feb (17 zile, 957 USD ≈ 4.163 RON), apoi **pauză de 4,5 luni**, apoi Labnoir din
4 iulie. Prima comandă LAB e din 27 mai, dar contul Meta a pornit pe Labnoir abia în iulie.
Am scris inițial tot istoricul, inclusiv Favmag — **greșit**, șters. Regula: la un cont
redenumit, verifică ruptura de activitate înainte de a moșteni istoricul; numele vechi poate
fi alt produs.

### Capcane descoperite pe drum

- **Redenumirile de conturi în Meta nu se propagă** în warehouse. Un cont poate sta ani cu numele
  vechi și să pară inexistent când îl cauți după numele curent. Caută **după `metaAccountId`**, nu după nume.
- **`meta_campaign_insights_daily` e mort din 19-06-2026** pe TOATE conturile (Apreciat, GT,
  Reduceri — toate se opresc fix acolo). Datele vii sunt în **`meta_ad_insights_daily`**. Orice
  query pe tabelul de campanii întoarce istoric înghețat.
- **Ceasul DB e UTC+3, coloanele sunt UTC.** `WHERE "lastSyncAt" > NOW() - INTERVAL '2 hours'`
  ratează tot ce s-a sincronizat în ultimele 3 ore. Compară cu `NOW() AT TIME ZONE 'UTC'`.
- **Tokenul „OAuth — Sabina Radu" a expirat.** Nu rupe nimic: cele 85 de conturi legate de el au
  toate `lastSyncAt=NULL` (n-au sincronizat vreodată), spend-ul real curge pe tokenul system-user
  no-expire (37 conturi). Dar sunt 85 de conturi marcate `isActive=true` care sunt de fapt moarte.
- **„Nocturna BG"** — brand activ, spend 17.977 (feb) + 10.086 (mar) RON, zero din aprilie și
  **zero comenzi vreodată**. Marketing istoric fără venit atașat.

2. **Sheet-ul WMS e înghețat — redundanța pe marketing per-SKU a căzut.** Nu mai livrează date
   noi: Facebook din **23-iulie**, TikTok din **23-iunie**. Cronul orar rulează și raportează
   succes, dar ingerează la nesfârșit aceleași totaluri (`[fb] 426137 USD`, `[tt] 1545895 USD`,
   identic la fiecare rulare). Cauza e în amonte, la conectorul sheet-ului, nu în cod.

   **Datele NU sunt descoperite**: `cache.product_ad_spend` (meta/tiktok/google) e proaspăt și
   fallback-ul acoperă — `data_health` raportează 🟡, nu 🔴. Problema reală e alta: WMS-ul a fost
   adoptat la cutover-ul din 19-06-2026 tocmai ca să fim **independenți de tokenul Meta**, iar
   acum am revenit tăcut pe calea dependentă de token — exact cea care a picat 11 zile în iunie.
   Deci nu e o gaură de date, e o plasă de siguranță care nu mai există.

   ⚠️ **Capcană la diagnostic:** rulează `data_health.py` DOAR cu `.env` încărcat
   (`set -a && . ./.env; set +a`). Fără el, verificările pe warehouse pică, `cache_fresh` rămâne
   necunoscut, iar WMS apare fals ca 🔴 „marketing per-SKU descoperit".

---

## 6. „Vreau X" → unde mă duc

- **profit per brand / % din profit** → `gigi:multi-brand-pnl`
- **profit per SKU sau categorie** → `profit_by_sku.py` (NU `fulfillment-analytics` — ăla e breakeven)
- **spend ads per SKU** → `cache.product_ad_spend` (⚠️ vezi gaura 2 de mai sus)
- **bucăți vândute** → `gigi:product-sales`
- **livrabilitate / refuz / COD** → `gigi:fulfillment-analytics`, `gigi:deliverability-monitor`
- **segmente clienți / LTV / churn** → `gigi:data-analytics` (pe AWBprint *delivered*, nu pe Shopify brut)
- **cât reaprovizionez** → `gigi:reorder-planner`
- **pacing buget + MER** → `gigi:spend-pacing`

---

## 7. Ce s-a reparat durabil (nu repeta diagnosticul)

Cinci bug-uri reparate la sursă în 24-iulie-2026, toate cu efect mare:
COGS înmulțit cu cursul (CZ/PL/BG ×5 mic) · TikTok phantom pe conturi partajate (Reduceri
72k vs 9k real) · `product_ad_spend` acumulat în loc de upsert · engine-ul citea
`product_ad_spend` în loc de `daily_ad_spend_ron` · Grandia Google nemapat.
Efect: marja grupului a trecut de la 13,5% raportat la **24,7% real**.

**Related:** [[HARTA]] · [[profit-data-sources-truth]] · [[data-health-watchdog]] ·
[[wms-per-sku-marketing]] · [[cogs-ron-formula-shopify]]
