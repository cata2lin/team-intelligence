---
name: trendyol-invoicing
description: "Automatically invoice delivered Trendyol orders in SmartBill and push the invoice back to Trendyol. For each Delivered + NotInvoiced order: builds a Romanian fiscal invoice in SmartBill (CIF ARONA RO37247302, series ARONA, VAT by the order's COUNTRY — RO 21% / BG 20% for OSS, prices VAT-inclusive, B2B if the order has a CUI, else B2C), fetches the PDF, hosts it at a public URL (scripts.arona.ro/invoices/<token>.pdf), and calls Trendyol POST seller-invoice-links so the order flips to invoiceStatus=Invoiced. Guards against double-invoicing in SQLite. Trendyol's own API can NOT generate invoices (only sendInvoiceLink/File = receives yours), and for a RO seller the fiscal invoice is a RO document — so we must issue + push. Triggers: 'facturează comenzile Trendyol', 'facturi Trendyol automat', 'push invoice to Trendyol', 'Trendyol invoicing', 'emite facturi Trendyol', 'care comenzi Trendyol n-au factură'."
argument-hint: "status | preview --order N | issue --order N --apply | run --apply [--limit N] [--days 30]"
---

# trendyol-invoicing — facturare automată comenzi Trendyol

**Trendyol NU e în xConnector** (unde se facturează restul magazinelor), deci comenzile lui rămâneau neanfacturate.
API-ul Trendyol **nu generează** factura (doar `sendInvoiceLink`/`sendInvoiceFile` = o primește pe a ta), iar pt
seller RO factura e document RO → **emitem în SmartBill + împingem**. Vezi [[trendyol-invoicing-flow]].

## Comenzi (rulează pe VPS, prin wrapper-ul cu KB+SmartBill)
```bash
ssh <vps> 'bash /root/Scripturi/run_trendyol_invoicing.sh status'                   # câte livrate neanfacturate
ssh <vps> 'bash /root/Scripturi/run_trendyol_invoicing.sh preview --order 11434416890'   # dry-run (nimic emis)
ssh <vps> 'bash /root/Scripturi/run_trendyol_invoicing.sh issue --order N --apply'  # emite 1 comandă REAL
ssh <vps> 'bash /root/Scripturi/run_trendyol_invoicing.sh run --apply'              # toate Delivered neanfacturate (guard)
```

## Ce face `issue`/`run` (verificat live 28-iul, factura ARONA584755 → Trendyol Invoiced)
1. **Fetch** comenzi `Delivered` + `invoiceStatus=NotInvoiced` (Trendyol orders API, ferestre de 14 zile).
2. **SmartBill** `POST /invoice` (CIF `RO37247302`, serie `ARONA`, **TVA pe ȚARĂ**: RO 21% / BG 20% după
   `orderCountryCode` — OSS; `isTaxIncluded=true`; B2B dacă `taxNumber`/`invoiceAddress.company`, altfel B2C).
   ⚠️ **`taxName` = numele cotei OSS din cont** (nu doar procent): RO→`"Normala"`, BG→`"Bulgaria"`, SK→`"Slovacia"`,
   HU→`"Ungaria"`, PL→`"Polonia"` (citite din SmartBill `GET /tax?cif=`). Altfel: „Cota TVA X% nu a fost gasita pe server".
3. **PDF** `GET /invoice/pdf` → salvat în `/var/www/invoices/<order>-<token>.pdf` → **public** pe
   `https://scripts.arona.ro/invoices/...` (nginx `location /invoices/` alias; www-data readable).
4. **Push** `POST https://apigw.trendyol.com/integration/sellers/{sellerId}/seller-invoice-links`
   cu `{invoiceLink, shipmentPackageId, invoiceDateTime, invoiceNumber=<serie+nr>}` → 201 → status `Invoiced`.
5. **Guard**: tabel `trendyol_invoices` în `data/trendyol_orders.db` (order_number PK, `pushed`) → nu re-facturează.

## Config / secrete
- SmartBill creds din KB (`SMARTBILL_EMAIL/TOKEN/CIF`), injectate de wrapper (nu în `.env`). Serie: `TRENDYOL_INVOICE_SERIES=ARONA`.
- `KB_DATABASE_URL` luat din `/root/Scripturi/.env` de wrapper (ca kb.py să meargă pe VPS).
- Trendyol creds = din `.env` app (folosite de `core.trendyol_client`).

## ⚠️ Reguli (FISCAL)
- **Factură = document ANAF e-Factura real** (RO obligatoriu inclusiv B2C). Corectarea = storno. Rulează `preview` întâi.
- **TVA pe țara comenzii** (OSS): RO 21%, BG 20% — NU cota din Trendyol (`vatRate` vine 0, artefact de config).
- Emailul clientului e mascat de Trendyol (`@trendyolmail.com`) → factura NU se trimite pe email; merge în ANAF + atașată la comanda Trendyol.
- Cron (după activare): zilnic `run --apply` prinde comenzile nou-marcate `Delivered`; guard-ul previne dublarea.

Related: `gigi:trendyol-awb` · `gigi:trendyol-promotions` · [[trendyol-invoicing-flow]] · [[smartbill-auto-invoice-email]]
