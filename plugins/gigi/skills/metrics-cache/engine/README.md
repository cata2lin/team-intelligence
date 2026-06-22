# Profit ENGINE — mirror canonic (version-controlled)

`profitability.py` = **oglinda byte-identică** a engine-ului de profitabilitate care rulează în
aplicația FastAPI **Scripturi** de pe VPS: `/root/Scripturi/api/profitability.py`.

E **sursa unică** a P&L-ului canonic per brand: `get_report(...)` →
`cache.brand_pnl_monthly` (consumat de `gigi:multi-brand-pnl`, `daily-ops-briefing`,
`agency-audit`, `product-matrix`, `ha-grandia-pnl` — toate Tier 2 moștenesc de aici).

## De ce e aici
Până acum engine-ul trăia DOAR pe VPS (editat live, cu backup-uri). Acum e în git ca să fie
**versionat, review-abil și recuperabil** — la fel ca `profit_core.py` / `profit_by_sku.py`.

## ⚠️ NU e runnable standalone
Importă modulele aplicației Scripturi (`models`, `db`, etc.) — aici e ca **referință**, nu de rulat.
Logica reutilizabilă (vat/cogs/transport/marketing) e în `../scripts/profit_core.py` (acela se importă).

## Reguli de lucru (ca să NU divergă de VPS)
1. **Editezi AICI (git)**, apoi deployezi pe VPS — niciodată invers.
2. Deploy:
   ```bash
   scp engine/profitability.py  $VPS:/root/Scripturi/api/profitability.py
   ssh $VPS 'systemctl restart scripturi-dashboard.service'   # ca să servească live noua logică
   ssh $VPS 'bash /root/Scripturi/run_cache.sh --table brand_pnl_real --apply'   # re-materializează brand_pnl
   ```
3. **Drift check** (mirror == VPS):
   ```bash
   ssh $VPS 'cat /root/Scripturi/api/profitability.py' | diff - engine/profitability.py
   ```
   Trebuie să fie gol. Dacă diferă, cineva a editat VPS-ul direct → reconciliază.

## Convenții cheie (vezi și `shared/HARTA.md`)
- **Transport** = `orders.transport_cost` (AWBprint) — costul AUTORITATIV per comandă, exact cum e
  urcat în AWB Arona: **UN AWB principal/comandă** (gross, TVA transport = RO 21% → `/1.21` = ex-TVA).
  **NU** se sumează `order_awbs` (rândurile multiple sunt în mare duplicate). Fallback:
  `MAX(transport_cost_fara_tva)` (principalul deduplicat) → `cost_per_parcel` flat.
- **COGS** ex-TVA (override + conversie RON), **marketing** din `cache.product_ad_spend` window-aware,
  venit **doar LIVRATE** ex-TVA. Toate VAT-urile per țară din `profit_core.vat_for_*`.

## Alte scripturi de profit VPS (mirror, RUNNABLE pe VPS)
Spre deosebire de `profitability.py` (modul al aplicației), astea sunt scripturi standalone care
rulează din `/root/Scripturi`. Aici sunt ca **mirror byte-identic** (version-controlled, fără secrete):
- **`trendyol_profitability.py`** — P&L pt marketplace-ul **Trendyol** (return-uri scăzute, `net_units`).
- **`trendyol_split.py`** / **`trendyol_get_token.py`** — toolchain Trendyol (split comenzi / token via playwright).
- **`product_profit_calculator.py`** — simulator prag/CPA **pre-lansare** (`--vat-rate` default 21) — NU e profit realizat.

Deploy/drift = la fel ca engine-ul: `scp` din folderul ăsta în `/root/Scripturi/<fișier>.py`; diff cu VPS = gol.
