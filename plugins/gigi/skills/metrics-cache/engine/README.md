# Profit ENGINE — mirror canonic (version-controlled)

`profitability.py` = **oglinda byte-identică** a engine-ului de profitabilitate care rulează în
aplicația FastAPI **Scripturi** de pe VPS: `/root/Scripturi/api/profitability.py`.
Din 3-oct-2026 importă `core.greu` → oglindit aici ca **`greu.py`** (`/root/Scripturi/core/greu.py`).

E **sursa unică** a P&L-ului canonic per brand: `get_report(...)` →
`cache.brand_pnl_monthly` (consumat de `gigi:multi-brand-pnl`, `daily-ops-briefing`,
`agency-audit`, `product-matrix`, `ha-grandia-pnl` — toate Tier 2 moștenesc de aici).

## Fișierele și gemenii lor pe VPS
| În git (`engine/`) | Geamănul de pe VPS | Ce e |
|---|---|---|
| `profitability.py` | `/root/Scripturi/api/profitability.py` | engine-ul P&L (router FastAPI `/api/profitability/*`) |
| `greu.py` | `/root/Scripturi/core/greu.py` | decoratorul `@greu`: rutele grele rulează în threadpool, câte UNA |
| `trendyol_profitability.py` | `/root/Scripturi/trendyol_profitability.py` | P&L Trendyol |
| `trendyol_split.py` | `/root/Scripturi/trendyol_split.py` | split colete Trendyol (⚠ vezi mai jos) |
| `trendyol_get_token.py` | `/root/Scripturi/trendyol_get_token.py` | token Trendyol via playwright |
| `product_profit_calculator.py` | `/root/Scripturi/product_profit_calculator.py` | simulator prag/CPA pre-lansare |

⚠ Fișiere cu ACELAȘI nume care NU sunt gemenii de mai sus: `/root/Scripturi/api/trendyol_split.py` (alt modul —
routerul FastAPI din `app.py`), `/root/Scripturi/product_analytics.py` (copie veche din iunie, n-o importă nimeni;
cea vie e `api/product_analytics.py`) și `/opt/apps/scripturi-dashboard/` (deploy-ul din aprilie; excepție:
`trendyol_split.py` de acolo e din 5-aug-2026, mai nou decât geamănul din `/root` — vezi mai jos).

## De ce e aici
Până acum engine-ul trăia DOAR pe VPS (editat live, cu backup-uri). Acum e în git ca să fie
**versionat, review-abil și recuperabil** — la fel ca `profit_core.py` / `profit_by_sku.py`.

## ⚠️ NU e runnable standalone
Importă modulele aplicației Scripturi (`core.config`, `core.stores`, `core.greu`, `profit_core` etc.) — aici e ca
**referință**, nu de rulat. Din `../scripts/profit_core.py` importă doar `prefix_brandid` și `PREFIX_AWB_DOMAIN`;
regulile lui de TVA, COGS, transport și marketing sunt scrise în `profitability.py` și nu mai coincid peste tot cu
`profit_core.py` / `profit_by_sku.py`.

## Reguli de lucru (ca să NU divergă de VPS)
1. **Editezi AICI (git)**, apoi deployezi pe VPS — niciodată invers.
2. Deploy (întâi `greu.py`, pe care îl importă engine-ul):
   ```bash
   scp engine/greu.py           $VPS:/root/Scripturi/core/greu.py
   scp engine/profitability.py  $VPS:/root/Scripturi/api/profitability.py
   ssh $VPS 'systemctl restart scripturi-dashboard.service'   # ca să servească live noua logică
   ssh $VPS 'bash /root/Scripturi/run_cache.sh --table brand_pnl_real --apply'   # re-materializează brand_pnl
   ```
3. **Drift check** (mirror == VPS), pe TOATE perechile din tabel — md5-urile trebuie să coincidă:
   ```bash
   ssh $VPS 'md5sum /root/Scripturi/api/profitability.py /root/Scripturi/core/greu.py \
     /root/Scripturi/trendyol_profitability.py /root/Scripturi/trendyol_split.py \
     /root/Scripturi/trendyol_get_token.py /root/Scripturi/product_profit_calculator.py'
   md5sum engine/*.py        # macOS: md5 -r engine/*.py
   ```
   Dacă diferă, cineva a editat VPS-ul direct → reconciliază (VPS-ul ține `.bak-*` la fiecare editare).
4. ⚠ `deploy_parity.py` / `deploy.sh` **NU acoperă** folderul ăsta (scanează doar `metrics-cache/scripts`,
   `shared/scripturi-tools`, `core/scripts` și mapează pe nume → `/root/Scripturi/<nume>`). Adăugat acolo, `engine/`
   n-ar ajuta: maparea pe nume ar sări în tăcere `profitability.py` și `greu.py` (gemenii lor sunt în `api/` și
   `core/`), adică exact fișierele care contează; doar cele 4 scripturi standalone ar fi acoperite. De aceea
   oglinda a rămas în urmă 24-iul → 3-oct-2026 fără nicio alertă — drift check-ul de mai sus se face de mână.

## Convenții cheie (vezi și `shared/HARTA.md`)
- **Venit** = NET: `currentTotalPriceSet` din Shopify (REFUNDED/VOIDED → 0; COD livrat dar încă necapturat →
  totalul), cu fallback pe `totalPriceSet` (decizia din 8-sep-2026). Doar comenzile **LIVRATE**, ex-TVA.
  Se aplică la următorul re-sync al lunii: lunile ≤ 2026-05 au încă venit BRUT (inclusiv REFUNDED/VOIDED).
- **Status livrare**: verdictul curierului are prioritate. `DELIVERED` din Shopify NU e dovadă (DPD închide și un
  retur cu „Delivered Back to Sender"): cu plata `PENDING` sau tag-ul `refuzata` → `Refuzata`. La re-sync-ul de
  noapte (`/run`), un AWB iese din coada de tracking DOAR pe verdict de curier (Livrata/Refuzata/Anulata +
  `awb invalid`, `sameday expirat`); `POST /refresh` (butonul din UI) alege încă după `status_category`.
  `courier_status` se păstrează la rescrierea lunii dacă AWB-ul a rămas același.
- **Transport** (regula din 8-sep-2026, aplicată pe ORICE lună cerută), în ordine:
  1. **SUMA tuturor liniilor de AWB** ale comenzii din AWBprint `order_awbs` (outbound + RETUR + colete
     suplimentare), `COALESCE(transport_cost_fara_tva, transport_cost / 1.21)`;
  2. `orders.transport_cost / 1.21`, doar pentru comenzile care AU linii de AWB, dar fără cost;
  3. flat `cost_per_parcel` din `profit_transport_costs` / 1.21 (implicit 13 RON brut când luna n-are rânduri) —
     ce primesc lunile fără CSV de curier importat (sep–oct 2026: majoritatea comenzilor).
  Însumarea e sigură de când CSV-ul curierului se importă din `api/courier_import.py`: 0 perechi
  `(order_id, tracking_number)` duplicate din apr-2026. Dec-2025 ARE perechi duplicate (~112k RON ex-TVA dublat).
  ⚠ `../scripts/profit_by_sku.py` și `shared/HARTA.md` descriu încă regula veche („UN AWB principal/comandă,
  NU suma `order_awbs`") — engine-ul și per-SKU nu mai calculează transportul la fel.
- **Curs valutar** = **BNR**: `metrics.fx_rates` → XML-ul oficial `curs.bnr.ro` → BGN din peg (1 EUR = 1,95583 BGN).
  Luna deschisă se reîmprospătează, luna închisă rămâne înghețată; fallback-ul hardcodat se folosește doar în
  memorie și NU se mai scrie în `profit_exchange_rates` (frankfurter.app scos — 403 din aug-2026).
- **Marketing**: `profit_marketing_override` are prioritate, sumat pe lunile selectate și pro-ratat pe fereastră
  parțială. Îl scriu (ore = ora serverului, Europe/Berlin): (1) `sync_raport_zilnic.py` (04:00 și 06:20, plus `--today` la
  10 min între 6 și 23, pentru luna curentă):
  întâi daily_perf (sheet-ul „CPA și financiar”), apoi baza Grandia pentru GRAN, apoi totalurile lunare din
  `cache.daily_ad_spend_ron`, care SUPRASCRIU orice lună ≥ 2026-05; (2) `POST /run` (noaptea la 02:30, luna curentă
  și cea precedentă) prin `/marketing-sync` cu `persist=1`. Fără rând de override: `cache.daily_ad_spend_ron` per
  brand (window-aware; NU `cache.product_ad_spend`); `daily_perf.db` doar dacă warehouse-ul nu răspunde deloc.
- **COGS** = `unitCost` din Shopify / override-ul, luate ca RON, FĂRĂ conversie valutară (vezi comentariul de lângă
  `unitCost` din `profitability.py`), doar comenzile livrate; ex-TVA = / (1 + TVA-ul țării magazinului).
- **TVA per țară**: RO 21 · CZ 21 · PL 23 · BG 20 · SK 23 · HU 27 · MD 20; prefixe noi ORC, SK, HU, MD, DUPBG.
  Venitul se convertește în RON după `profit_orders.currency` al fiecărei comenzi (DUPBG are comenzi în EUR, CZK și
  HUF; TVA aplicat = BG 20%). ⚠ Fallback-ul de curs din memorie n-are HUF/MDL: fără curs BNR, venitul HU/MD se
  convertește cu 1,0.
- **Comision agenție**: `profit_commission_override` (sumă manuală pe lună) + `profit_commission_rate` (regulă cu
  `from_month`), editate din UI și aplicate în UI (`static/js/profitability.js`), NU în `get_report` →
  `cache.brand_pnl_monthly` nu include comisionul.
- **Denumiri magazine** citite din Shopify (`profit_store_names`; refresh la fiecare run + `POST /refresh-store-names`).
- **`@greu`** (din 3-oct-2026): GET `report`, `product-stats`, `months`, `commission-overrides`, `commission-rates`
  și, în `api/product_analytics.py`, GET `/api/analytics/products` și `/api/analytics/product-costs` rulează în
  threadpool, cu UN singur semafor pentru toate 7 (câte una), ca să nu mai blocheze scanerul din depozit și
  `/api/oh-ingest`. Corpul lor NU are voie să conțină `await` (`get_report` ia cursul cu `asyncio.run(...)`).
  `build_cache.py` cheamă în continuare `await get_report(...)` — rularea din 4-oct-2026 05:30 a aplicat
  `brand_pnl_real` normal. Cronul rulează copia din checkout-ul de pe VPS (`run_cache.sh`), care diferă de
  `/root/Scripturi/build_cache.py` și de git.

## Alte scripturi de profit VPS (mirror, RUNNABLE pe VPS)
Spre deosebire de `profitability.py` (modul al aplicației), astea sunt scripturi standalone care
rulează din `/root/Scripturi`. Aici sunt ca **mirror byte-identic** (version-controlled, fără secrete):
- **`trendyol_profitability.py`** — P&L pt marketplace-ul **Trendyol** (return-uri scăzute, `net_units`).
- **`trendyol_split.py`** / **`trendyol_get_token.py`** — toolchain Trendyol (split comenzi / token via playwright).
  ⚠ Cronul `*/5` rulează `/opt/apps/scripturi-dashboard/trendyol_split.py` (5-aug-2026): e cea mai nouă versiune și
  singura cu regula „splitez DOAR detergenții”. Geamănul din `/root/Scripturi` (și oglinda de aici) e mai vechi și
  n-o are. Înainte să muți cronul pe `/root/Scripturi`, portezi întâi filtrul, altfel parfumurile cu cantitate > 1
  se împart în N colete.
- **`product_profit_calculator.py`** — simulator prag/CPA **pre-lansare** (`--vat-rate` default 21) — NU e profit realizat.

Deploy/drift = la fel ca engine-ul: `scp` din folderul ăsta în `/root/Scripturi/<fișier>.py`; md5 egal cu VPS-ul.
