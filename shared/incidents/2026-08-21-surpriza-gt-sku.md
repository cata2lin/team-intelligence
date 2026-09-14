# BUG DESCHIS în Order Hub — parfumul surpriză nu se adaugă NICIODATĂ pe George Talent

> Pentru agentul care lucrează pe OH. Scris 21-aug-2026, ora 10:40. **Esteban e reparat, GT NU.**

## Simptom măsurat
Rata de acoperire a surprizei (comenzi CLIENT eligibile — `nr_parfumuri % 3 == 2`, fără draft, fără
tag `farasurpriza` — care au primit efectiv linia de surpriză):

| zi | Esteban | George Talent |
|---|---:|---:|
| 10–19 aug (cronul vechi) | 99–100% | 100% |
| 20 aug (ziua cutover-ului) | 51% | 47% |
| **21 aug** | **100% de la 05:03** | **0/3 — încă rupt** |

Ultimele ratări GT: `GT57464` (03:58), `GT57472` (06:04), `GT57484` (07:31) — toate azi, toate cu
2 parfumuri, toate fără surpriză. Pe Esteban ultimele ratări au fost `EST244950` (01:08) și
`EST244956` (02:58), după care mecanismul a pornit corect.

## Cauza — detecția parfumului merge doar pe SKU NUMERIC
`services/cron_parity/surprise.py`, în `analyze()`:

```python
if re.fullmatch(r"\d+", sku):
    perfumes += int(li.quantity or 1)
```

SKU-urile de parfum pe magazine:

| magazin | SKU parfum | numeric? |
|---|---|---|
| Esteban (`6f9e22-9d`) | `33`, `71`, `114` | ✅ da |
| **George Talent (`ix5bxc-hr`)** | **`gt-35`, `zn-71`** | ❌ **NU** |
| Nubra (`bmuwvv-jy`) | `33` | ✅ da |
| Lab Noir (`31k0py-bi`) | `15-50ml`, `81-100ml` | ❌ nu (dar owner-ul l-a exclus deliberat) |

Pe GT `perfumes` rămâne **0** la orice comandă ⇒ `0 % 3 == 0 ≠ 2` ⇒ `analyze()` întoarce mereu 0
⇒ nu se adaugă nimic, tăcut. Dovadă pe date: din 2.674 de comenzi GT (10–21 aug), liniile de
parfum sunt **6.669 × `gt-*` + ~800 × `zn-*`, ZERO SKU-uri numerice**. Singurul SKU numeric-adiacent
e chiar surpriza (`surpriza-158`).

## Ce trebuie schimbat
Detecția „e parfum?" nu poate fi un regex global — SKU-ul diferă pe magazin. Variante, în ordinea
robusteții:

1. **Apartenența la colecția „Toate parfumurile"** (sursa de adevăr a discountului 2+1 însuși —
   pe Esteban colecția are 164 de produse). Cost: un query de colecție per magazin, cache-uit.
2. **Pattern per magazin, configurabil** din `automation_config.params(store, "surprise")` — de ex.
   `sku_pattern: r"^(gt-|zn-)?\d+$"` pe GT, `r"^\d+$"` pe Esteban. Zero query-uri în plus.
3. Excludere în loc de includere: e parfum ORICE linie care nu e `cutie-cadou`, nu e surpriză și nu
   e linie fără SKU. Cel mai simplu, dar prinde și eventualele produse non-parfum de pe magazin.

**Recomandare: (2)** — e cea mai ieftină și nu schimbă comportamentul pe Esteban. Cu default-ul
actual `^\d+$` păstrat pentru magazinele nesetate, GT primește `^(gt|zn)-\d+$`.

⚠️ **Verifică EFECTUL, nu funcția** (regula de cutover): după deploy, rulează pe date reale
„câte comenzi GT eligibile au primit surpriza în ultima oră" — nu „`analyze()` întoarce 1 la un
mock". Bug-ul ăsta a trecut de teste tocmai pentru că funcția era corectă pe intrarea ei numerică.

## Regula corectă (confirmată pe 11.800 de comenzi, 10–21 aug)
`1 surpriză când (nr. parfumuri) % 3 == 2` → la 2, 5, 8, 11, 14. **Mereu cel mult una.**
Se numără CANTITATEA, nu liniile. Cutia cadou NU e condiție și nu se numără.

Dovada, pe Esteban în fereastra în care mecanismul mergea (grupat pe nr. de parfumuri la preț întreg
și pe câte a făcut gratis discountul nativ):

| parfumuri | din care gratis 2+1 | comenzi | au primit surpriză |
|---:|---:|---:|---:|
| 1 | 0 | 267 | 0 |
| **2** | 0 | **599** | **599 (100%)** |
| 3 | 1 | 4.465 | 0 |
| 4 | 1 | 301 | 0 |
| **5** | 1 | **120** | **117 (98%)** |
| 6 | 2 | 1.558 | 0 |
| 7 | 2 | 59 | 0 |
| **8** | 2 | **23** | **21 (91%)** |
| 9 | 3 | 189 | 0 |
| **11** | 3 | **6** | **6 (100%)** |
| **14** | 4 | **5** | **5 (100%)** |

Excepții (nu se adaugă): comandă din DRAFT (`sourceName = shopify_draft_order`), tag `farasurpriza`,
are deja surpriză, are deja AWB.

## Alte două lucruri găsite pe drum (nu blochează, dar sunt reale)
1. **Garda „are deja AWB în xConnector" e moartă.** În `services/couriers/xconnector.py::create_awb`:
   ```python
   o = await self.xc_order_by_shopify_id(creds, order.shopify_order_id)
   if self._doc(o, "SHIPPING_LABEL"): return {"success": False, ...}
   ```
   `/api/orders/by-id?orderId=<shopify_id>` NU întoarce `documents` — răspunsul are doar câmpuri de
   adresă (`addressHash`, `addressStatus`, `shippingAddress`, `originalCustomerAddress`,
   `latestAddressValidation`, …). Verificat live pe EST244418. Deci `_doc()` întoarce mereu `None`
   și garda nu se declanșează niciodată — se poate cere o a doua etichetă peste una existentă.
   `print_queue_central.py` folosește același endpoint dar cu **orderId-ul INTERN xConnector**
   (ex. 71765446), nu cu cel Shopify (13194907320665) — probabil acolo e diferența.
2. **`account_key = "dpdromania"` nu există ca `CourierAccount`** — sunt 44.971 de shipments cu
   cheia asta (backfill-ul din istoricul xConnector). Orice `_create_one` cu ea dă
   `404: Courier account 'dpdromania' not found.` Contul LIVE per magazin e `xconnector-<prefix>`.
   Contează pentru orice cod care refoloseşte `shipment.account_key` ca să refacă o etichetă.

## Legat
Memoriile `oh-surprise-left-in-shadow`, `surprise-perfume-flow`, `oh-cutover-incomplet-cron`.
Reparația comenzilor afectate (52 de comenzi, 20–21 aug) — vezi `2026-08-21-surpriza-recuperare.md`.
