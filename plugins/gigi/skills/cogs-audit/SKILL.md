---
name: gigi:cogs-audit
description: Auditează și repară COGS-ul (costPerItem) din Shopify față de formula CANONICĂ ARONA din sheet-ul „Stoc ARONA" → tab „COGS 2026" — (marfă$ + shipping$) × 4.46 × 1.10 vamă × 1.21 TVA. Prinde automat bug-ul cel mai frecvent și mai păgubos (cost oprit la coloana „COGS lei", fără vamă și TVA), placeholderele rotunde și divergențele aceluiași SKU între magazine. Use când: „completează COGS-ul lipsă", „de ce e COGS-ul greșit", „cost fără TVA/vamă", „verifică costurile din Shopify", „unde găsesc costul de achiziție în lei", „aliniază COGS", „missing cost price", „landed cost".
---

# gigi:cogs-audit — COGS-ul corect în lei, verificat la sursă

Răspunde la „ce COGS pun pe produsul X" și la „câte produse au COGS greșit", fără să ghicești.
Sursa de adevăr NU e Shopify — e sheet-ul cu formule al firmei. Shopify e doar destinația.

## Formula canonică (nu o rescrie, e VIE în sheet)
Sheet **„Stoc ARONA"** `1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0` → tab **„COGS 2026"**
(gid `933312595`, ~178 rânduri). Formulele reale din celule:

```
I = G + H              COGS dolari = marfă$ + shipping$
J = 4.46 * I           COGS lei        (curs FIX 4.46, hardcodat manual)
K = 0.1 * J            taxe vamale 10%
L = 0.21 * (J + K)     TVA 21%
N = J + K + L          TOTAL cu TVA   ← asta intră în Shopify `cost`
M = N / 1.21           total fără TVA
```
⇒ `COGS_RON = USD_landed × 5.93626`. Engine-ul de profit face apoi `unitCost / 1.21` → deci
**convenția corectă în Shopify e CU TVA**.

## Rulare
```bash
uv run scripts/cogs_audit.py audit                              # tot, toate magazinele
uv run scripts/cogs_audit.py audit --only-bugs --store RED,OFER
uv run scripts/cogs_audit.py audit --sku asternut --json out.json
uv run scripts/cogs_audit.py fix --sku oglinda                  # dry-run
uv run scripts/cogs_audit.py fix --sku oglinda --apply          # scrie în Shopify
```
`fix` e **dry-run implicit**; repară doar clasele derivabile, sare peste `MISMATCH` (cere `--force`).

## Clasele de bug (ce caută)
| clasă | ce înseamnă | test |
|---|---|---|
| **MISSING_VAT_DUTY** | s-au oprit la coloana „COGS lei" — fără vamă și TVA | `cost ≈ USD_landed × 4.46` |
| MISSING_VAT | are vamă, n-are TVA | `cost ≈ USD_landed × 4.46 × 1.10` |
| MISMATCH | nu derivă din nimic (tipic **placeholder rotund**) | — |
| DIVERGENT | același SKU, costuri diferite între magazine | costul e RON peste tot ⇒ orice diferență e bug |

**Testul rapid de mână:** dacă `cost ÷ 4.46` îți dă fix prețul landed în dolari → cuiva i-au lipsit
vama și TVA. Așa a fost prins `oglinda` (16.50 = 3.70 × 4.46 exact, corect 21.96).

## 🚨 Capcana care a produs o concluzie GREȘITĂ (evit-o)
**Costul din Shopify e în RON pe TOATE magazinele, inclusiv CZ / BG / PL**, deși magazinul are altă
monedă. Consecințe:
- **valori identice între magazine = NORMAL**, nu dovadă de placeholder;
- **NU** compara costul cu prețul în valuta magazinului. Un agent a „dovedit" un placeholder cu
  „cost 20 EUR > preț de vânzare 11.99 EUR pe BG" — **fals**: erau 20 **lei** ≈ 4 EUR.

Singurul test valid rămâne: **derivă din formulă?**

## De unde vine partea în DOLARI (când SKU-ul lipsește din „COGS 2026")
Ordinea (vezi și `gigi:po-cost-usd`): **Sheet13** din „Comenzi Procurement Tom" → **kdocs** (packing
list per container, match pe **barcode**) → TOM.
- ⚠️ **TOM `requestedUnitCost` e DEJA landed** (marfă + shipping). kdocs dă marfă $5.80, TOM dă $6.30
  = +$0.50. Dacă iei TOM și mai adaugi shipping, **dublezi transportul**.
- Shipping lipsă → **$0.50/bucată** (regula de business a userului, 22-iul-2026). Excepție: „frate"
  identic (altă culoare) cu shipping real → ia valoarea lui.

## Limite — citește înainte să generalizezi
- **×5.93626 NU e lege globală.** Pe 121 SKU cu ambele surse, doar ~20% o respectă; familia `HA-####`
  are alt regim (~6.03). De-aia scriptul compară cu **valoarea din sheet**, nu cu multiplicatorul,
  și marchează `NO_REF` ce nu e în „COGS 2026" — nu inventează.
- **4.46 nu e curs BNR** — e o constantă rotundă pusă manual (în `metrics.fx_rates` nu există nicio
  rată în banda 4.4594–4.4601). Nu o „corecta" la cursul zilei fără decizie de business.
- **Surse care NU-s adevăr:** tabul vechi „COGS" (fără formulă, are erori: albastru-160 și -180 ambele
  32.88; HA-0384 = 0) · taburile „1 iulie"/„1 iunie" (se auto-contrazic) · sheet-ul PO „RESTUL"
  (`=4.58*USD` hardcodat, fără vamă/TVA — iar antetul „Curs BNR 4.5824 ← editabil, tot sheetul se
  recalculează" e **fals**, nicio formulă nu referă celula aia).

## Detalii tehnice
- Cost = pe **InventoryItem**, nu pe variantă: `PUT /admin/api/{ver}/inventory_items/{id}.json`
  cu `{"inventory_item":{"id":…,"cost":"21.96"}}`.
- **GraphQL e rupt** pe `core/shopify_client.py` (API 2024-01 întoarce liste goale) → tot REST.
- Credențiale: `stores.csv` (prefix,shop,token) + `google_credentials.json` din folderul Scripturi
  (`SCRIPTURI_DIR` dacă e altundeva). Nu printa tokenuri.
- Output UTF-8 forțat (mașinile din depozit sunt Windows/cp1252).

Înrudite: [[cogs-ron-formula-shopify]] · [[po-cost-usd-sources]] · [[profit-data-sources-truth]]
