---
name: shopify-facturi
description: Descarcă facturile Shopify (bills — abonament + aplicații + fee threshold) ca PDF pentru TOATE magazinele Arona, pe o lună dată, într-un folder pe Desktop (subfolder per magazin + folder TOATE cu tot la un loc + recap.csv). Merge prin Chrome-ul logat cu contul upstream (CDP), fiindcă facturile NU sunt expuse de Admin API. Acoperă facturare per magazin, organizații vechi (listă de magazine) și organizații noi cu facturi consolidate (ex. HEYADS/MagDeal). Use pentru „scoate facturile Shopify din <lună>", „facturile de abonament Shopify", „bills Shopify pe toate magazinele", „facturi Shopify pentru contabilitate", „export bill PDF Shopify". Triggers: facturi shopify, shopify bills, factura abonament shopify, export bill, facturile din august, billing shopify toate magazinele.
argument-hint: <YYYY-MM> [--stores EST,GT] [--out folder] [--port 9223]
---

# shopify-facturi — facturile Shopify ale tuturor magazinelor, ca PDF

> Autor: **anne**. Făcut pe 2026-09-09 după ce am scos facturile din august 2026 (37 PDF, 26 magazine).

## Ce face
Pentru fiecare magazin din `SHOPIFY_STORES_CSV` (KB) intră în admin → Settings → Billing, ia toate
facturile **emise în luna cerută** și le exportă PDF (butonul „Export bill" → PDF). Scrie:
- `<out>/<Magazin>/<data>_<nrfactura>_<suma>.pdf` — per magazin
- `<out>/TOATE/<Magazin>_<data>_<nrfactura>_<suma>.pdf` — toate la un loc (ce vrea contabilitatea)
- `<out>/recap.csv` — magazin, factură, dată, motiv, sumă, status, fișier

Implicit `<out>` = `Desktop/facturi shopify <YYYY-MM>`.

## Cum se rulează (2 pași)
1. **Chrome logat, cu port de control — fereastră SEPARATĂ.** Nu închide/reporni Chrome-ul principal
   al utilizatorului (lecție dureroasă). Pornește o instanță nouă cu profil temporar:
   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9223 --user-data-dir="$env:LOCALAPPDATA\Temp\claude-chrome-profile" --no-first-run https://admin.shopify.com/
   ```
   Utilizatorul se loghează acolo cu contul **upstream** (contact@upstreamtradellc.com — are toate
   magazinele). Cookie-urile din profilul principal NU se pot copia (app-bound encryption), iar
   Chrome-ul pornit de MCP-ul `chrome-devtools` are flag-uri de automatizare care strică login-ul
   (hCaptcha) — de aceea instanța separată, normală.
2. ```bash
   uv run scripts/shopify_facturi.py 2026-08
   uv run scripts/shopify_facturi.py 2026-08 --stores EST,GT,NUB     # doar câteva
   ```
   Re-rularea e idempotentă (sare peste facturile deja în `recap.csv`).

## Cum e facturarea în Shopify la noi (ca să știi ce să aștepți)
| Tip pagină | Magazine (sep-2026) | Cum arată |
|---|---|---|
| A. `/settings/billing` — facturare proprie | Bonhaus.pl, Grandia, Belasil, Bucsa, Artevita (nu e în stores.csv → adăugat manual în script) | tabel Polaris, link `/invoice/<id>` |
| B. `/settings/organization-billing` cu listă de magazine | org „Parfumuri" (Esteban, GT, Nubra, Lab Noir, Duppo BG/CZ/HU/MD), org Gento+Apreciat, org Nocturna+Lux, org Rossi+Ofertele+OriceRedus, org Bonhaus bg/cz/hu/sk+CasaOfertelor, org Reduceri+Carpetto+Covoria | click pe magazin → același tabel ca A |
| C. `/settings/organization-billing` IndexTable consolidat | HEYADS TECHNOLOGY SRL (MagDeal; „11 stores" dar factura acoperă 1 magazin) | `<tr id=<invoice>>`, fără link; export = download direct `pdf_download.pdf` |

Cazuri fără facturi: **Duppo CZ/HU** (0 lei), **Esteban Bulgaria** (fără pagină de billing), **CePatAi**
(contul upstream nu mai are acces). Datele apar ca „Aug 16, 2026" / „Yesterday at…"; se filtrează pe luna emiterii.

## Capcane tehnice (rezolvate în script, nu le redescoperi)
- Tabelul de facturi e din `div.Polaris-Table-TableRow`, nu `<tr>` (tipul C e `tr.Polaris-IndexTable__TableRow`).
- Butonul modal „Export bill" e într-un `s-internal-button` cu shadow DOM; click-ul merge fiabil doar după
  ce evaluezi ceva pe fiecare buton (touch) și dai click pe `nth(0)` în `expect_download` (care expiră — normal).
- PDF-ul vine fie ca URL semnat GCS din răspunsul GraphQL `BillPdfUrl` (tip A/B → fetch cu `context.request`),
  fie ca download Chrome `…/invoices/<id>/pdf_download.pdf` (tip C → `Browser.setDownloadBehavior` în `_dl`).
- URL-urile directe `.pdf` din admin dau 403; nu există API pentru bills.

## Logare KB
După rulare: `kb.py log --type skill --action used --name anne:shopify-facturi --summary "facturi <lună>: N PDF, M magazine"`.
