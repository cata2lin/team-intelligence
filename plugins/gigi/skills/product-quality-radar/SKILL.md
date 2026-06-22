---
name: product-quality-radar
description: Product-level QUALITY radar — which PRODUCTS generate refunds and returns, WITH THE REASON (a real quality signal, not just a money number). Combines two independent circuits: (a) Shopify refunds from metrics (orders.totalRefunded>0 x order_line_items x brands) → refunded orders + RON + units per SKU, all brands; (b) Grandia RMA returns (rma_request_items x rma_requests) → aggregated return REASON per SKU (poor_quality / defective / damaged / not_as_described / wrong_product / missing_parts) + a DEFECT RATE per SKU (returns / units sold). Cross-validates SKUs appearing in BOTH circuits = confirmed problem, and emits a CS/quality recommendation (fix the description, pull from COD, fix the supplier). Use for "which products cause refunds/returns", "why is product X returned", "defect rate per SKU", "worst-quality products", "ce produse se returneaza si de ce", "rata de defect", "produse de scos de pe COD". Read-only.
---

# product-quality-radar

Radar de **calitate per produs**: nu doar *cât* pierdem pe refund-uri/retururi, ci
**ce produse** și **DE CE** — semnalul care îți spune unde e o problemă reală (descriere
greșită, lot defect, ambalare proastă, furnizor de schimbat). Read-only.

## Cum rulezi
```bash
cd plugins/gigi/skills/product-quality-radar
uv run product_quality_radar.py                      # sumar: refund Shopify + retururi RMA cu motiv + cross-validare
uv run product_quality_radar.py --store Esteban      # filtrează refund-urile Shopify pe un brand
uv run product_quality_radar.py --reason poor_quality  # doar SKU-uri returnate cu un motiv anume
uv run product_quality_radar.py --limit 25           # mai multe rânduri per secțiune
uv run product_quality_radar.py --json               # pt automatizare / export
```
`--reason` acceptă: `poor_quality`, `defective`, `damaged`, `not_as_described`,
`wrong_product`, `missing_parts`, `other`.

## Ce scoate
1. **REFUND-uri Shopify** (toate brandurile, sau `--store X`): top SKU-uri după numărul
   de comenzi cu refund, cu buc și RON rambursat (`orders.totalRefunded`). Sursă de
   adevăr financiar — orice brand, nu doar Grandia.
2. **Retururi RMA Grandia — CU MOTIV**: per SKU, numărul de RMA-uri, buc, RON rambursat,
   **mixul de motive** (de ce se returnează), o **notă reprezentativă a clientului**
   (`reasonNote`), **rata de defect** (RMA-uri / comenzi vândute) și o **recomandare**
   automată (fix descriere / fix furnizor / întărește ambalaj / verifică fulfillment /
   scoate de pe COD dacă rata ≥ 15%).
3. **Cross-validare**: SKU-uri care apar în **AMBELE** circuite (refund Shopify ȘI retur
   RMA) = problemă confirmată din două surse independente.

## Cum funcționează
- **metrics** (`DATABASE_URL_METRICS`): `orders` (`"totalRefunded">0`, `name`, `"brandId"`)
  × `order_line_items` (`"orderId"`, `sku`, `quantity`) × `brands` (`id`, `name`).
  `totalRefunded` e o sumă **pe comandă**, deci se pre-agregă pe `(order, brand, sku)` și se
  însumează **o singură dată per comandă** (altfel s-ar dubla pe liniile cu același SKU).
- **grandia** (`DATABASE_URL_GRANDIA`): `rma_request_items` (`sku`, `title`, `quantity`,
  `"requestId"`) × `rma_requests` (`reason`, `reasonNote`, `"refundAmount"`, `type`,
  `"createdAt"`). `rma_request_items` e un tabel **real per-linie** (nu aproximăm prin toate
  liniile comenzii ca în `returns-rma-report`), deci motivul e atribuit exact SKU-ului
  returnat.
- **Numitorul** ratei de defect = comenzi vândute pentru SKU, **din AWBprint** (store `grandia.ro`,
  `line_items.inventory_item.sku`). NU din metrics: metrics.orders pt Grandia e truncat (din ~19-apr-2026),
  pe când AWBprint are istoricul complet (din nov-2025) — altfel numitorul iese mic și defect-rate
  supraestimat ~38% (poate flipa fals pragul de „scoate de pe COD").
- Conexiuni `pg8000` cu SSL; URL-urile vin din env, altfel din KB (`kb.py secret-get`).
  Doar `SELECT`, nu scrie nimic.

## Interpretare (de pe datele reale)
- `not_as_described` dominant → **fix pagina de produs** (foto/specs nu corespund).
- `poor_quality` / `defective` → **problemă de lot / furnizor**.
- `damaged` → **ambalare / curier**, nu neapărat produsul.
- `wrong_product` → **eroare de fulfillment** (mapare SKU în depozit), nu calitate.
- `missing_parts` → **kit incomplet** la furnizor.
- **Rată ≥ 15%** = candidat clar de scos de pe COD / depublicat.

## Limitări
- Motivele de retur (RMA) există **doar pentru Grandia** (singurul magazin cu modulul RMA).
  Pentru celelalte branduri ai doar circuitul de refund Shopify (fără „de ce").
- Rata de defect e calculată doar pentru SKU-urile Grandia (singurul brand cu RMA). Numitorul
  vine acum din AWBprint (istoric complet), deci e mult mai aproape de realitate decât înainte
  (când metrics-ul truncat la apr supraestima rata ~38%).
- Sume brute în RON (cum sunt în DB), fără TVA-adjust sau transport.
- `--reason X` restrânge setul de SKU-uri și cifrele de headline la acel motiv; linia
  „motive:" arată în continuare **mixul complet** al SKU-ului, pentru context.
