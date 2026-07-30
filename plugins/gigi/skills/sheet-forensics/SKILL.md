---
name: gigi:sheet-forensics
description: Află DE CE un Google Sheet arată cifra aia — citește formulele (nu valorile) și scoate la iveală ce nu se vede în UI: constante hardcodate (cursuri, TVA, taxe), celule „editabile" pe care nicio formulă nu le referă, literali căutați de SUMIFS/FILTER care NU există în tabul sursă (rezultat 0 fără nicio eroare), și taburi care se contrazic pe aceeași cheie. Use când: „de ce e 0 în raport", „de ce nu se actualizează", „ce curs folosește sheet-ul", „brandul X n-are cifre azi", „care tab e sursa de adevăr", „de ce nu se potrivește cu Shopify", „am schimbat cursul și nu s-a schimbat nimic".
---

# gigi:sheet-forensics — de ce arată sheet-ul cifra ASTA

Un sheet minte tăcut în trei feluri, și **niciunul nu se vede în interfață**: constante lipite în
formule, comenzi false, și potriviri pe text care nu potrivesc. Cheia e să citești `valueRenderOption=FORMULA`,
nu valorile.

```bash
uv run scripts/sheet_forensics.py tabs     <SHEET_ID>
uv run scripts/sheet_forensics.py consts   <SHEET_ID> --tab "COGS 2026"
uv run scripts/sheet_forensics.py deadrefs <SHEET_ID> --tab "Raport azi"
uv run scripts/sheet_forensics.py lookups  <SHEET_ID> --tab "Raport azi"
uv run scripts/sheet_forensics.py formulas <SHEET_ID> --tab "Raport azi" --row 21
uv run scripts/sheet_forensics.py compare  <SHEET_ID> --key SKU --col COST --tabs "COGS,1 iulie,COGS 2026"
```

## Cele 4 minciuni și comanda care le prinde

**1. `consts` — constanta lipită în 178 de celule.**
Scoate numerele hardcodate din formule, ordonate după frecvență. Așa a ieșit la iveală formula
canonică de COGS a firmei: `4.46` (curs), `0.1` (vamă), `0.21` (TVA) — fiecare în **178 de celule**.
Un curs repetat în sute de celule **nu se poate schimba dintr-un loc**, oricât ar sugera antetul.

**2. `deadrefs` — comanda falsă.**
Găsește celule etichetate ca parametru („curs", „editabil", „modifică") pe care **nicio formulă nu
le referă**. Caz real: antetul zicea *„Curs BNR RON/USD: 4.5824 ← editabil, tot sheetul se
recalculează"*. Fals — toate formulele aveau `=4.58*…` hardcodat. Editai celula, nu se schimba nimic.

**3. `lookups` — potrivirea care nu potrivește.** ← *cea mai păguboasă*
Extrage literalii căutați de `SUMIFS`/`FILTER`/`VLOOKUP` și verifică dacă **există în tabul sursă**.
Un `❌ LIPSEȘTE` = formula nu potrivește nimic → **0, fără eroare, fără `#N/A`, fără nimic roșu**.
Caz real: raportul zilnic căuta magazinul `"Grandia.ro"`, dar în feed-ul Shopify se numea `Grandia`
→ comenzi 0 și vânzări 0 pe un brand care făcuse 102 comenzi și 19.414 lei în ziua aia.
Coloana Google mergea (căuta corect `"Grandia"`), deci raportul arăta spend fără vânzări — și nimeni
nu se uita la asta ca la o eroare.
> La prima rulare pe raportul echipei a mai găsit unul necăutat: `"Casa ofertelor"` lipsea din
> „Google Ads azi".

**4. `compare` — taburi care se contrazic.**
Aceeași cheie, valori diferite în taburi diferite. Dacă două taburi nu sunt de acord, **niciunul nu e
sursă de adevăr**. Caz real: același SKU avea 32.88 / 11.04 / 33.43 / 43.33 în patru locuri.

## Metoda (ordinea în care le rulezi)
1. `tabs` — vezi structura. ⚠️ **Caută tabul după NUME, nu după `gid` din URL** — gid-urile din
   linkuri vechi pot să nu mai existe (tab șters/redenumit).
2. `lookups` — cel mai rapid câștig dacă „brandul X arată 0".
3. `consts` + `deadrefs` — dacă întrebarea e „ce curs/TVA folosește" sau „am editat și nu se schimbă".
4. `compare` — dacă ai mai multe surse și nu știi pe care să te bazezi.
5. `formulas --row N` — citirea fină a unui rând, cu formula ȘI valoarea rezultată alături.

## Capcane
- **Nu presupune că un tab „azi" e proaspăt** — poate fi alimentat de un conector blocat pe
  „Se încarcă… (Loading data...)". `read` pe el ți-o arată.
- **Istoricul și „azi" pot avea surse DIFERITE** — la noi „Raport Zilnic 2" e scris de un script pe
  VPS, iar „Raport azi" de formule. De-aia un bug de formulă se vede **doar pe ziua curentă**.
- **Numele magazinului e o cheie fragilă.** Redenumești magazinul în Shopify → se rupe orice
  `SUMIFS` care-l caută pe nume. Verifică cu `lookups` DUPĂ orice redenumire.
- Credențiale: service account (`google_credentials.json`, scope `spreadsheets.readonly`) — `--creds`
  ca să-l schimbi. Doar citire, nu scrie nimic.

Înrudite: [[cogs-ron-formula-shopify]] · [[cpa-financiar-live-report]] · `core:export-to-google-sheet` · `gigi:attribution-audit`
