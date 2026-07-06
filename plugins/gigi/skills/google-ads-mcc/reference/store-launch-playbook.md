# Playbook: lansare Google Ads pe un magazin ARONA NOU (end-to-end)

> Secvența canonică de lansare, dovedită pe **Bonhaus PL / Gento / Carpetto / Bonhaus CZ**. Toate uneltele trăiesc în acest skill (`gads.py`, `cod_tracking.py`, `fix_conversion_goals.py`, `brandref.py`) + `esteban-creatives/_link_mcc.py`. Toate scripturile cer `export DATABASE_URL_METRICS="$(uv run <core>/scripts/kb.py secret-get DATABASE_URL_METRICS)"`.

## 0. RECON ÎNTÂI (nu lansa orb) — 3 gate-uri care AU oprit lansări
- **Cerere:** magazinul vinde ACUM? (AWBprint / pixel social). NU lansa pe un magazin dormant. *(Nocturna: dormant din aprilie + „cererea TikTok" era MagDeal/Esteban atribuiți greșit pe conturi shell → NU s-a lansat.)*
- **Stoc:** hero-ul e ÎN STOC pe mărimile/variantele care se vând? NU lansa pe winner epuizat. *(Nocturna: setul roșu M/L/XL = 0 → trafic pe OOS = bani arși.)*
- **Economie:** marjă, AOV, **breakeven CPA/ROAS** (moneda magazinului). Target = sub breakeven; **Google ≈ 65% din Social** (vezi [[target-cpa-per-store]]). Ex Bonhaus PL: marjă 65%, AOV ~55 PLN, breakeven CPA ~14-16, target Google 8.
- **Checkout:** COD form (Releasit/EasySell) SAU native? (decide tracking-ul, pasul 3).
- **Feed:** e canalul Google & YouTube instalat? Merchant ID? (verifică `gigi:merchant-center-feed`).

## 1. Creează contul (owner, în Google Ads)
Monedă = **moneda magazinului** (PLN/CZK/RON), geo = piața, limbă = a pieței. ⚠️ **Timezone nu se mai schimbă** după creare (ARONA folosește de regulă Europe/Bucharest, consistent).

## 2. Link MCC
`esteban-creatives/_link_mcc.py` cu `CLIENTS=["<cid>"]` (copiază în scratchpad, editează CLIENTS) → `customerClientLinks:mutate` status PENDING (dry-run fără `--apply`). **Owner acceptă** în contul nou: **Admin → Access & security → Managers → accept NOVOS DIGITAL (746-711-0480)**. Verifică: `gads.py report --customer <cid> --query "SELECT customer.id,customer.currency_code FROM customer"` → **403 = încă nelinkat**.

## 3. Conversii
- **COD form** (Releasit/EasySell): `cod_tracking.py --cid <cid> --ga4 <store> --url https://<domain> --apply` → creează **„COD Purchase"** + printează **Tag ID (AW-…)** + **Purchase Label** + **send_to** + GA4 → **owner le pune în tab-ul „Conversion tracking" al Releasit**. ⚠️ `--ga4` poate pica la rezolvare → dă G-id-ul manual (de pe site). AW-id-ul e adesea deja pe site din canalul Google&YouTube (ACELAȘI cont, nu „stray").
- **Native checkout** (Nubra/GT/Esteban): „Google Shopping App Purchase" declanșează nativ — fără workaround COD.
- Apoi **`CIDARG=<cid> fix_conversion_goals.py --apply`** (citește customer din env `CIDARG`, NU `--customer`) → **PURCHASE-only biddable** (oprește DEFAULT/PAGE_VIEW/ADD_TO_CART/BEGIN_CHECKOUT).

## 4. Campanii (mirror Bonhaus CZ)
- **Brand Search** — kw brand, `MAXIMIZE_CONVERSIONS` fără tCPA (cold start), geo+limbă, buget mic. **Creează pe PAUSE** (enable abia după ce conversia e confirmată — altfel Max Conv optimizează orb).
- **DSA — de regulă SARI-L.** (DSA Bonhaus CZ scurgea ROAS 0.6, CPA 633 → l-am strâns.) PMax e motorul.
- **PMax Shopping-led** = motorul real pt deals. Construiește-l **când feed-ul Merchant iese din review-ul inițial** (`pending_initial_policy_review`, ~3 zile, se curăță singur). Brand Guidelines: **BUSINESS_NAME + LOGO la campaignAssets ÎNTÂI**, apoi restul (vezi [[gento-carpetto-launch]]).

## 5. Cablare „CPA și financiar" (`MAPPING_SHEET_ID 1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo`)
- **Mapping** tab: coloana **E „Conturi Google"** = **numele exact al contului** pe rândul brandului (ex E7="Bonhaus PL"; E6="Bonhaus CZ" precedent).
- **Curs valutar** tab (dacă non-RON): rând brand cu **Moneda** + **Curs** (`=GOOGLEFINANCE("CURRENCY:<X>RON")`) + **Include Shopify=DA**. Cursul se aplică **generic după numele brandului** pe `gCost` — **NU există coloană/toggle Google** (D/E/F = FB/TikTok/Shopify). Deci dacă rândul + rata există, conversia PLN→RON e automată.
- Adaugă brandul în `core/brands.py` + în array-ul `BRANDS` din scripturile Raport. Apoi `meta-ads/brandmap.py sync`.
- ⚠️ **Sheets WRITE** cu SA looker-sheets: scope `spreadsheets` → `unauthorized_client`; folosește scope **`drive`** (DWD-autorizat), impersonând gheorghe.beschea@overheat.agency. Reads = `spreadsheets.readonly`.

## 6. Enable + scalare
Enable Brand Search **DUPĂ** ce conversia e confirmată salvată. După **15-30 conversii** → `set-tcpa` la target (deals ~8-20, parfumuri ~15). NU umbla zilnic (fiecare schimbare de bidding resetează learning 1-2 săpt).

## ⚠️ Gotcha-uri v21 / interfață
- Campania cere `containsEuPoliticalAdvertising = DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING` (altfel reject).
- `businessName` **NU** e câmp valid pe `responsiveSearchAd` (dropează-l).
- `gads.py set-budget --daily N` (NU `--budget`); micros currency-agnostic (valoare×1e6 în moneda contului).
- `gads.py set-status --customer <cid> --campaign <id> --status ENABLED [--apply]`; `set-troas` pe Shopping = versiunea patch (PR #352).
- **Releasit test „couldn't detect the event"** = adaugă **`admin.shopify.com`** în **Traffic permissions** ale Google tag-ului (UI: Google tag → Admin → Traffic permissions); + ad-blocker taie testul din browser (producția prin storefront merge oricum).
