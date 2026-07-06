---
name: cpa-report
description: "Audit and operate the 'CPA și financiar 2025' daily report (the Google Sheet + Apps Script that shows per-brand spend/orders/revenue/ROAS/CPA/profit for today and history — tabs 'Raport azi' + 'Raport Zilnic 2', fed by a 3rd-party FB/Shopify connector). USE WHENEVER a brand shows 0 or is MISSING from the report, when adding a NEW store/brand to the report, or when data looks wrong. The audit pinpoints the real cause — almost always MISSING DATA IN A SOURCE TAB (a connector feed stopped, a new ad account isn't pulled, a new store missed its first day), NOT a formula bug. Triggers: 'de ce e brandul X pe 0 in raport', 'X nu apare in Raport azi', 'lipseste din CPA si financiar', 'adauga magazin nou in raport', 'Magdeal 2 nu e luat in calcul', 'feed oprit', 'shopify pull zilnic', 'completez zilele lipsa'. Companion to gigi:apps-script-deploy (edits the report's Apps Script) and gigi:multi-brand-pnl (the OTHER, engine-based P&L)."
argument-hint: "audit | audit --brand Covoria | shopify-pull --brand LABNOIR --store 'Lab Noir' --from 2026-07-04 --to 2026-07-06"
---

# cpa-report — audit + operare a raportului „CPA și financiar 2025"

Raportul (sheet `1IVg0fI-...`, [[cpa-financiar-live-report]]) e alimentat de un **conector 3rd-party** care trage FB/Shopify/Google în filele sursă (`Facebook Ads`(+azi), `Shopify`(+azi), `Google Ads azi`, `Tiktok Ads`(+azi)); două scripturi Apps Script (`adaugaRandZilnicAzi` = „Raport azi", `adaugaRandZilnic2` = „Raport Zilnic 2") construiesc rândurile din lista `BRANDS` + `Mapping`. **Când un brand e pe 0 / lipsește, cauza aproape mereu = date lipsă în filele SURSĂ**, nu formula.

```bash
uv run scripts/cpa_report.py audit                 # toate brandurile: feed FB/Shopify/Google, ultima dată, gap
uv run scripts/cpa_report.py audit --brand Covoria # un brand (fiecare cont FB din Mapping verificat SEPARAT)
uv run scripts/cpa_report.py shopify-pull --brand LABNOIR --store "Lab Noir" --from 2026-07-04 --to 2026-07-06
```

## `audit` — de ce e brandul X pe 0 / lipsește
Pt fiecare brand din `Mapping`: sparge conturile FB (col B, comma-separated) și verifică **fiecare cont separat** în `Facebook Ads` + `Facebook Ads azi`; magazinul (col D) în `Shopify` + `Shopify azi`; Google (col E) în `Google Ads azi`. Marchează: `ok` (are azi/ieri), `⚠️ STALE` (feed oprit — ultima dată veche), `❌ LIPSĂ` (zero rânduri). Cazuri reale prinse:
- **feed oprit** (ex Covoria: FB+Shopify STALE la 07-04 → apare 0 în „Raport azi"; slotul refolosit când s-a adăugat alt magazin).
- **cont nou netcontorizat** (ex „Magdeal 2" pus în Mapping dar `❌ NU e în 'Facebook Ads azi'` → conectorul nu-l trage → nu se sumează, deși regex-ul `^(REFLEXINO|MAGDEAL2)$` e corect).
- **Google `⚠️`/absent poate fi NORMAL** — dacă nu rulezi Google pe brandul ăla (nu-l trata ca feed rupt).
> Fix pt ❌/⚠️ = la **CONECTOR** (adaugă contul/magazinul în add-on-ul care alimentează filele), NU în script.

## `shopify-pull` — completează manual zilele lipsă (magazin nou / feed ratat)
Scoate metricile zilnice din **Shopify Admin API** (ARONA OAuth: ESTEBAN/GT/NUBRA/LABNOIR) **în exact formatul filei** `Shopify`: `Day · Store · Orders · TotalSales · Cost(COGS) · Gross · Discounts · Shipping · Taxes`, unde **TotalSales = Gross − Discounts + Shipping + Taxes = SUM(F:I)** (ce citește raportul ca „Vanzari"). tz magazin (RO = +03 vara).
- **Validează calibrând pe o zi cunoscută**: rulează și pt o zi care E deja în filă (ex azi din `Shopify azi`) și compară — dacă se potrivește la cifră, restul zilelor sunt de încredere.
- ⚠️ **Pune rândul ÎN BLOCUL zilei, ALFABETIC pe magazin** (filele sunt pe DATĂ apoi magazin; conectorul e append-only, NU re-sortează). Un rând pus la coadă supraviețuiește dar arată **orfan** → userul crede că lipsește. Vezi [[labnoir-cpa-sheet-add]].
- **NU adăuga ziua CURENTĂ în istoric** dacă niciun magazin n-o are încă (intră overnight pt toți) → dublezi (`Shopify` = SUMIFS fără dedup; `Facebook Ads` are `UNIQUE` care tolerează dubluri cu aceeași valoare, nu parțial-vs-final).

## A adăuga un MAGAZIN NOU în raport (procedură)
1. `Mapping`: rând cu FB/Shopify(/Google) — nume EXACT cum apar în filele sursă. 2. `'Lab Noir'` în `const BRANDS` în AMBELE scripturi (`gigi:apps-script-deploy` push `--as` owner). 3. Istoric în „Raport Zilnic 2" (append-only nu revizitează trecutul) = inserează rânduri în poziția sortată cu formulele scriptului. 4. Golurile din filele sursă (prima zi ratată de conector) = `shopify-pull` + pune-le la loc. 5. „Raport azi" se reconstruiește la rebuild-ul trigger-ului (rar) → poți pune rândul manual până atunci.

Auth: SA `looker-sheets` (`GA4_SA_JSON` din KB, Editor pe sheet) pt citit; ARONA OAuth pt `shopify-pull`. `audit`+`shopify-pull` sunt read-only (nu scriu în sheet). Companion: `gigi:apps-script-deploy`.
