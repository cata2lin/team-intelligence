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
cea vie e `api/product_analytics.py`) și tot ce e în `/opt/apps/scripturi-dashboard/` (deploy-ul vechi din aprilie).

## De ce e aici
Până acum engine-ul trăia DOAR pe VPS (editat live, cu backup-uri). Acum e în git ca să fie
**versionat, review-abil și recuperabil** — la fel ca `profit_core.py` / `profit_by_sku.py`.

## ⚠️ NU e runnable standalone
Importă modulele aplicației Scripturi (`core.config`, `core.stores`, `core.greu`, `profit_core` etc.) — aici e ca
**referință**, nu de rulat. Logica reutilizabilă (vat/cogs/transport/marketing) e în `../scripts/profit_core.py`
(acela se importă).

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
   `shared/scripturi-tools`, `core/scripts` și mapează pe nume → `/root/Scripturi/<nume>`). NU adăuga `engine/`
   acolo: gemenii trăiesc în `api/` și `core/`, iar maparea pe nume ar nimeri copiile vechi. De aceea oglinda a
   rămas în urmă 24-iul → 3-oct-2026 fără nicio alertă — drift check-ul de mai sus se face de mână.

## Convenții cheie (vezi și `shared/HARTA.md`)
- **Venit** = NET: `currentTotalPriceSet` din Shopify (REFUNDED/VOIDED → 0; COD livrat dar încă necapturat →
  totalul), cu fallback pe `totalPriceSet` (decizia din 8-sep-2026). Doar comenzile **LIVRATE**, ex-TVA.
- **Status livrare**: verdictul curierului are prioritate. `DELIVERED` din Shopify NU e dovadă (DPD închide și un
  retur cu „Delivered Back to Sender"): cu plata `PENDING` sau tag-ul `refuzata` → `Refuzata`. La re-sync, un AWB
  iese din coada de tracking DOAR pe verdict de curier (Livrata/Refuzata/Anulata + `awb invalid`,
  `sameday expirat`); `courier_status` se păstrează la rescrierea lunii dacă AWB-ul a rămas același.
- **Transport** = **SUMA tuturor liniilor de AWB** ale comenzii din AWBprint `order_awbs` (outbound + RETUR +
  colete suplimentare), `COALESCE(transport_cost_fara_tva, transport_cost / 1.21)`; fallback `orders.transport_cost / 1.21`
  când comanda nu are nicio linie costată (din 8-sep-2026 — sigur de când importul CSV de la curier se face din
  `api/courier_import.py`: 0 perechi `(order_id, tracking_number)` duplicate pe iun-aug 2026).
  ⚠ `../scripts/profit_by_sku.py` și `shared/HARTA.md` descriu încă regula veche („UN AWB principal/comandă,
  NU suma `order_awbs`") — engine-ul și per-SKU nu mai calculează transportul la fel.
- **Curs valutar** = **BNR**: `metrics.fx_rates` → XML-ul oficial `curs.bnr.ro` → BGN din peg (1 EUR = 1,95583 BGN).
  Luna deschisă se reîmprospătează, luna închisă rămâne înghețată; fallback-ul hardcodat se folosește doar în
  memorie și NU se mai scrie în `profit_exchange_rates` (frankfurter.app scos — 403 din aug-2026).
- **Marketing**: `profit_marketing_override` (sheet-ul „CPA și financiar", via `sync_raport_zilnic.py`) are
  prioritate, sumat pe lunile selectate și pro-ratat pe fereastră parțială; altfel `cache.daily_ad_spend_ron` per
  brand (window-aware; NU `cache.product_ad_spend`); altfel `daily_perf.db`. `GET /marketing-sync` scrie
  override-ul DOAR cu `persist=1`, pe o singură lună întreagă.
- **COGS** ex-TVA (override + conversie RON). TVA / monedă implicită per țară: RO 21 · CZ 21 · PL 23 · BG 20 ·
  SK 23 · HU 27 · MD 20; prefixe noi ORC, SK, HU, MD, DUPBG (DUPBG vinde în EUR — conversia folosește
  `profit_orders.currency`, nu moneda țării).
- **Comision agenție**: `profit_commission_override` (sumă manuală pe lună) + `profit_commission_rate` (regulă cu
  `from_month`), editate din UI și aplicate în UI (`static/js/profitability.js`), NU în `get_report` →
  `cache.brand_pnl_monthly` nu include comisionul.
- **Denumiri magazine** citite din Shopify (`profit_store_names`; refresh la fiecare run + `POST /refresh-store-names`).
- **`@greu`** (din 3-oct-2026): `report`, `product-stats`, `months`, `commission-overrides`, `commission-rates`
  rulează în threadpool, câte UNA, ca să nu mai blocheze scanerul din depozit și `/api/oh-ingest`. Corpul lor NU
  are voie să conțină `await` (`get_report` ia cursul cu `asyncio.run(...)`). `build_cache.py` cheamă în continuare
  `await get_report(...)` — rularea din 4-oct-2026 05:30 a aplicat `brand_pnl_real` normal.

## Alte scripturi de profit VPS (mirror, RUNNABLE pe VPS)
Spre deosebire de `profitability.py` (modul al aplicației), astea sunt scripturi standalone care
rulează din `/root/Scripturi`. Aici sunt ca **mirror byte-identic** (version-controlled, fără secrete):
- **`trendyol_profitability.py`** — P&L pt marketplace-ul **Trendyol** (return-uri scăzute, `net_units`).
- **`trendyol_split.py`** / **`trendyol_get_token.py`** — toolchain Trendyol (split comenzi / token via playwright).
  ⚠ Cronul `*/5` rulează `/opt/apps/scripturi-dashboard/trendyol_split.py`, NU copia din `/root/Scripturi`: acea
  versiune splitează DOAR detergenții (Belasil). Până se mută cronul pe `/root/Scripturi` (cu `.env`), oglinda de
  aici NU e codul care rulează.
- **`product_profit_calculator.py`** — simulator prag/CPA **pre-lansare** (`--vat-rate` default 21) — NU e profit realizat.

Deploy/drift = la fel ca engine-ul: `scp` din folderul ăsta în `/root/Scripturi/<fișier>.py`; md5 egal cu VPS-ul.
