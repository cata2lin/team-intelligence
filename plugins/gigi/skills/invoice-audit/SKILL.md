---
name: invoice-audit
description: Audit de corectitudine pe facturile SmartBill emise automat pentru comenzile Arona (prin xConnector) — găsește facturile unde TOTALUL FACTURAT ≠ CÂT A PLĂTIT CLIENTUL, în special transportul nefacturat (venit subdeclarat = risc ANAF/e-Factura). Mapare EXACTĂ comandă↔factură din xConnector (fără potriviri pe nume), PDF-uri descărcate din xConnector (NU din SmartBill → nu ne blochează rate-limit-ul). Use for "facturile nu includ transportul", "verifică facturile", "audit facturi SmartBill", "cât transport nu s-a facturat", "factura nu corespunde cu comanda", "venit subdeclarat", "invoice audit", "missing shipping on invoice".
argument-hint: "--days 7 | --days 30"
---

# invoice-audit — facturile SmartBill chiar corespund cu ce a plătit clientul?
> Author: Gigi. Născut din întrebarea „facturile automate nu includ transportul?" (14-iul-2026).

Facturile Arona se emit **automat prin xConnector** (`/api/actions/create-invoice`) → **SmartBill**, seria principală **`ARONA`** (magazinele RO + majoritatea; `ROSSI` are serie proprie; există și serii `PA-*` pe geo). Comenzile **taxează transport** (19-24 lei; 0 peste pragul de livrare gratuită). Dacă transportul nu ajunge pe factură → **venit subdeclarat**.

```bash
uv run audit_invoices.py --days 7      # fereastra de audit
uv run audit_invoices.py --days 30
```

## 🔑 Metoda CORECTĂ (și de ce oricare alta te minte)
Testul e **UN SINGUR NUMĂR: `total factură` vs `total plătit de client`.** NU căuta cuvântul „transport" pe factură.

1. **Mapare comandă↔factură = din xConnector**, nu ghicită. `xc.orders(from,to)` → fiecare comandă are `documents[]`; cel cu `documentType == "INVOICE"` conține `name: "ARONA-549771.pdf"` + un **`url` de descărcare directă** (`xconnector.app/download/invoice?c=..&s=ARONA&n=549771&h=..`).
2. **PDF-urile se descarcă de la xConnector, NU de la SmartBill.** SmartBill te **blochează** după câteva zeci de cereri (`invoice/pdf`) — xConnector nu. Bonus: URL-ul are hash, nu-ți trebuie credențiale SmartBill.
3. **Cât a plătit clientul = Shopify** (`totalPriceSet` / `subtotalPriceSet` / `totalShippingPriceSet`).
4. **Filtrează pe comenzile care CHIAR au avut transport taxat** (`totalShipping > 0`) — altfel raportezi fals-pozitive pe comenzile cu livrare gratuită (unde lipsa liniei de transport e CORECTĂ).
5. Verdict: `factură ≈ total plătit` = ✅ · `factură ≈ subtotal (produse) < plătit` = 🔴 **transport nefacturat** (suma lipsă = `totalShipping`).
6. Sari peste **storno/anulate** (`TOTAL PLATA` negativ, sau textul `A N U L A T A` în PDF).

## ⚠️ Cele 3 capcane care mi-au dat DE DOUĂ ORI un bug fals (verificat: nu existau)
1. **NU căuta „transport" în textul PDF-ului.** Extragerea pypdf ratează linia → am raportat „14 din 15 facturi fără transport" când de fapt **10 din 11 erau perfect corecte**. Factura ARONA 555438 (GT) chiar avea linia: `transport · buc · 1 · 16,53 + 3,47 TVA = 20 lei`. **Extrage doar `TOTAL PLATA` (ăla se citește fiabil) și compară-l cu ce a plătit clientul.**
2. **NU potrivi factura cu comanda după NUMELE clientului.** Am „găsit" un bug pe LUX33976 (lipsesc 24 lei) — era o comandă din **martie**, alt client, potrivită doar după numele de familie „Dinca". Mapping-ul EXACT există în xConnector — folosește-l.
3. **NU lovi SmartBill în paralel.** 8 fire × 45 PDF-uri → throttled ore bune. Dacă chiar ai nevoie de SmartBill: secvențial + pauze; sau folosește URL-urile xConnector (recomandat).

## SmartBill — referință (când chiar îți trebuie direct)
- Auth: `HTTPBasicAuth(SMARTBILL_EMAIL, SMARTBILL_TOKEN)` + `cif=SMARTBILL_CIF` (toate în KB). Base: `https://ws.smartbill.ro/SBORO/api`.
- `GET /series?cif=&type=f` → seriile + `nextNumber` (ARONA e cea mare; nextNumber ≈ 555.440 la 14-iul-2026).
- `GET /invoice/pdf?cif=&seriesname=&number=` → PDF. **Rate-limited agresiv.**
- `GET /invoice/paymentstatus?cif=&seriesname=&number=` → JSON cu `invoiceTotalAmount` (mai ieftin decât PDF-ul, dacă îți trebuie doar totalul).

## Ce faci cu rezultatul
Scriptul scoate: nr. facturi corecte · nr. **fără transport** · **suma totală nefacturată** · defalcare pe magazin · lista comenzilor. Dacă apar cazuri reale → **storno + refacturare** (`gigi:xconnector inv-storno` / `inv-regen`) și verifică maparea transportului în configul xConnector al magazinului afectat (tiparul e per-magazin/flux, nu aleatoriu).

Legături: [[gigi:xconnector]] (emitere/storno facturi), [[gigi:cs-actions]].
