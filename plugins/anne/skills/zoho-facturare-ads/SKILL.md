---
name: zoho-facturare-ads
description: Facturarea LUNARĂ a spend-ului Meta per cont de reclame — THEWGRID LLC → ARONA SRL — în Zoho Invoice (invoice.zoho.eu, org 20099310317), ca DRAFT. Din sheet-ul cu „cont / ad_account_id / sumă USD" face planul de împărțire în facturi ≤ $31k (sparge doar conturile mari), creează drafturile (prima prin formular, restul prin API-ul intern), apoi reconciliază per cont cu sheet-ul. NU trimite facturi — Send dă doar Anne. Use pentru „facturăm în Zoho", „facturile THEWGRID pe <lună>", „facturi ads către ARONA", „împarte spend-ul Meta în facturi", „reconciliază facturile Zoho cu sheet-ul". Triggers: zoho, zoho invoice, thewgrid, facturare ads, INV-000, facturi meta arona.
argument-hint: plan spend.csv <YYYY-MM> | create plan.json | verify spend.csv <YYYY-MM>
---

# zoho-facturare-ads — facturile THEWGRID → ARONA pentru spend-ul Meta

> Autor: **anne**. Făcut pe 2026-09-10 după august 2026: 24 conturi, $336.199 → INV-000148…158 (11 drafturi).
> Reconcilierea e descrisă în memoria [[ad-spend-invoicing-reconcile]].

## Regula de aur
**Doar DRAFT.** Toate facturile din Zoho sunt și rămân Draft (inclusiv cele vechi); Anne dă Send / PDF.
Skill-ul nu are nicio cale de „Send"/„Mark as sent".

## Fluxul (3 comenzi)
1. **Sursa** = sheet-ul Annei cu spend-ul lunar per cont (coloane: lună, cont, ad_account_id, sumă, monedă, echivalent
   USD pentru RON). Îl primești ca screenshot sau link → scrii `spend.csv` cu `cont,ad_account_id,suma_usd`
   (conturile RON — ex. DUPPO BG, Nubra — cu echivalentul USD din sheet, NU suma în RON).
2. ```bash
   uv run scripts/zoho_facturare.py plan spend.csv 2026-08        # afișează împărțirea + total; scrie plan.json
   ```
   Arată-i planul Annei (tabel: factură, total, conținut). Ea a acceptat „cum o fi"; dacă vrea altă împărțire, editezi plan.json.
3. **Chrome separat** (NU cel principal): `chrome.exe --remote-debugging-port=9223 --user-data-dir=%LOCALAPPDATA%\Temp\claude-chrome-profile https://invoice.zoho.eu/` → Anne se loghează (Google + 2FA pe telefon).
   ```bash
   uv run scripts/zoho_facturare.py create plan.json               # drafturi; idempotent (plan.done.json)
   uv run scripts/zoho_facturare.py verify spend.csv 2026-08       # sumă per cont din Zoho vs sheet + total
   ```
   Raportezi lista INV cu totaluri + „diferențe per cont: niciuna".

## Ce știe scriptul (nu redescoperi)
- Client **ARONA SRL** = customer_id `602270000000050151`, USD (`602270000000000059`), template Standard `602270000000000103`.
- Date: Invoice Date = **1 a lunii următoare**, Terms **Custom**, Due = **2 a lunii peste două** (iul → 01 Aug / 02 Oct; aug → 01 Sep / 02 Nov).
- Linie: `Nume cont (ad_account_id)` + newline + `1-31 Aug`, qty 1, rate = suma, fără taxe. Notes/Terms = textul standard „Facebook Ads … hello@thewowgrid.com".
- Împărțire: tranșe ≤ $31.000, bin-pack best-fit; se sparg doar conturile care nu încap (Esteban, Esteban 2, Esteban 3). Sume identice pe 2 facturi = split intenționat.
- **Auth**: GET-urile merg cu `X-ZCSRF-TOKEN: csrfp=<cookie CSRF_TOKEN>`, dar **POST/PUT dau 401** cu el. Aplicația trimite
  `x-zcsrf-token: zbcsparam=<hash>` + `x-zb-source: zbclient`. De aceea prima factură se salvează prin UI (Save as Draft) cu
  `page.on("request")` care capturează header-ul, apoi restul merg prin `context.request.post(/api/v3/invoices, multipart JSONString)`.
- Formular: dropdown-ul Terms nu se selectează fiabil → după Save se corectează cu PUT (`payment_terms 62 / Custom / due_date`).

## Capcane
- Nu confunda facturile PRIMITE de la THEWGRID (în `FACTURI ARONA\<lună>\the w grid`) cu cele EMISE aici — sunt aceleași documente, văzute din partea cealaltă.
- Numerotarea INV e continuă; `verify` filtrează pe Invoice Date, deci nu porni luna nouă cu aceeași dată ca una veche.
- Nu închide/reporni Chrome-ul principal al Annei (vezi [[feedback-nu-inchide-chrome]]).
