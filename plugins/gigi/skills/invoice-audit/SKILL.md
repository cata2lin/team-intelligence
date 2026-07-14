---
name: invoice-audit
description: Audit de corectitudine pe facturile SmartBill emise automat pentru comenzile Arona (prin xConnector + cronul xc_invoice) — găsește facturile unde TOTALUL FACTURAT ≠ CÂT A PLĂTIT CLIENTUL, tipic transportul nefacturat (venit subdeclarat + TVA necolectat = risc ANAF/e-Factura/D394). Citește PDF-urile cu markitdown (păstrează tabelul de linii), leagă factura de comandă din câmpul `Order:` de pe factură, și cuantifică expunerea pe magazin/lună. Descoperit cu el: GT + ROSSI facturau 100% FĂRĂ transport (37.205 facturi, 716.610 lei). Use for "facturile nu includ transportul", "verifică facturile", "audit facturi SmartBill", "cât transport nu s-a facturat", "factura nu corespunde cu comanda", "venit subdeclarat", "invoice audit", "missing shipping on invoice".
argument-hint: "--days 7 | --days 30 | --store GT"
---

# invoice-audit — facturile SmartBill chiar corespund cu ce a plătit clientul?
> Author: Gigi. Născut din „facturile automate nu includ transportul?" (14-iul-2026) — și **avea dreptate**.

## Cum funcționează facturarea la noi (contextul, ca să nu-l re-descoperi)
- Cron **săptămânal** pe VPS: `0 2 * * 0` → `/root/Scripturi/xc_invoice.sh` → `xconnector.py capture --days 30` + **`inv-bulk --days 30 --apply`** (facturează comenzile **PAID fără factură**; ultima rulare: 2.247 facturi).
- **Noi NU construim factura.** Payload-ul trimis e doar `{"orderId": ..., "connectorId": ...}` → **xConnector o construiește** din datele comenzii, prin connectorul **SMART_BILL** al magazinului.
- ⇒ **Dacă factura e greșită, vina e în CONFIGUL connectorului (per magazin), nu în cronul/codul nostru.** Connectori: GT `17542`, ROSSI `10376`, NOC `13889`, LUX `14225`.
- Serii SmartBill: **`ARONA`** (magazinele RO, seria mare) · `ROSSI` · `PA-*` (geo). Facturile poartă `Order: <nume comandă>` în subsol.

## 🔑 Metoda CORECTĂ
```bash
uv run audit_invoices.py --days 7
```
1. **Citește PDF-ul cu `gigi:markitdown`**, NU cu pypdf brut. markitdown **păstrează tabelul de linii**; pypdf turtește textul și **pierde linia de transport** → am dat de două ori alarme false cu el.
2. **Legătura factură↔comandă e PE FACTURĂ**: câmpul `Order: GT49192`. Nu mai potrivi după nume/sume (vezi capcanele).
3. **Verdictul se dă pe UN SINGUR NUMĂR: `TOTAL PLATA` (factură) vs `totalPrice` (Shopify).** Nu căuta cuvântul „transport" — la GT linia se numește **„Livrare prin DPD"**, la altele „transport". Keyword-ul e fragil; totalul nu minte.
4. **Filtrează pe comenzile cu transport TAXAT** (`totalShippingPriceSet > 0`) — la livrare gratuită lipsa liniei e CORECTĂ.
5. Verdict: `factură = plătit` ✅ · `factură = subtotal (produse) < plătit` 🔴 **transport nefacturat** (lipsa = `totalShipping`).
6. Sari peste **storno/anulate** (`TOTAL PLATA` negativ / `A N U L A T A`).

## ⚠️ RATE-LIMIT — citește înainte să pornești orice scan
- **SmartBill blochează** după câteva zeci de `invoice/pdf` (l-am prins cu 8 fire × 45 → throttled ore).
- ⚠️ **URL-ul `xconnector.app/download/invoice` NU e o portiță — proxy-ează tot SmartBill.** „Am ocolit rate-limit-ul" e fals; doar îl ascunzi. Am oprit un audit de 5.474 facturi exact din motivul ăsta.
- ✅ **Regula: nu citi facturi în masă.** Ia un **eșantion** (300-500, secvențial, cu pauze) → tiparul e **determinist per magazin** → apoi **calculează expunerea din comenzile Shopify** (gratis, fără rate-limit). Nu trebuie să citești 37.000 de facturi ca să afli suma.

## 🩸 Cele 3 capcane care mi-au dat bug-uri FALSE (verificate: nu existau)
1. **Keyword „transport" în textul PDF** → „14 din 15 facturi rupte" când de fapt **10 din 11 erau corecte**. Cauze: pypdf turtește tabelul + linia se poate numi „Livrare prin DPD". **Judecă pe TOTAL.**
2. **Potrivire factură↔comandă după numele clientului** → „bug pe LUX33976, lipsesc 24 lei" = comandă din **martie**, alt client, potrivit doar după „Dinca". **Folosește `Order:` de pe factură.**
3. **A crede că e temporal când e per-magazin** (sau invers). Test: compară **intervalele de numere de factură** ale grupului corect vs rupt. Dacă se **suprapun** → e **pe magazin** (config), nu temporal.

## 🔴 Ce a găsit (14-iul-2026) — cazul de referință
- **GT: 428/428 facturi verificate = 100% FĂRĂ transport.** **ROSSI: 22/22.** EST/LUX/NOC: **0 rupte** (același interval de facturi ⇒ **per-magazin, nu temporal**). Vechi cel puțin din **nov-2025**.
- Tipar: client plătește 150 (130 produse + **20 transport**) → **factura = 130**.
- **Expunere: 37.205 facturi · 716.610 lei** (cu TVA) · ~124.370 lei TVA necolectat. GT 634.080 / ROSSI 82.530. ⚠️ TVA-ul a fost **19% până în aug-2025** → split-ul pe perioade îl face contabilul.
- **Cauza:** connectorii SmartBill **GT `17542`** și **ROSSI `10376`** nu mapau linia de transport (bifă în UI-ul xConnector).

## ✅ Reparație — ORDINEA CONTEAZĂ
1. **Întâi repară connectorul** (bifa de transport în xConnector, per magazin). **Dacă faci storno/refacturare ÎNAINTE, noile facturi ies la fel de greșite** — xConnector le reconstruiește cu același config rupt.
2. **Verifică pe o factură NOUĂ**, nu pe una veche (alea rămân rupte oricum): `xconnector.py inv-make --order <X> --apply` pe o comandă PAID fără factură (cronul oricum o facturează) → citește-o → trebuie să apară linia + `TOTAL = cât a plătit clientul`. *Dovadă: ARONA 555441 → „Livrare prin DPD 16,53 + 3,47 TVA", total 150 = plătit 150.* ✅
3. **Trecutul (storno + refacturare) = decizia CONTABILULUI**, nu a agentului: zeci de mii de facturi, declarații depuse, D394, e-Factura. Tu livrezi cifrele + lista comenzilor.

Legături: [[gigi:xconnector]] (inv-make/storno/regen), [[gigi:markitdown]], [[gigi:cs-actions]].
