---
name: adaugare-stoc-produse
description: Adauga stoc pentru marfa noua (un container) pe cele 4 magazine "deals" Arona (Magdeal, Casa Ofertelor, Reduceri Bune, Ofertele Zilei) direct prin Shopify Admin API - pune STOCUL doar pe MAGDEAL (master), plus barcode + inventory tracking + policy "nu vinde fara stoc" (DENY) + scoate tag-ul "test", iar app-ul de sync pe barcode copiaza stocul pe celelalte 3. Barcode-urile vin din sheet-ul TOM. Trateaza si produsele dublura (le pune pe DRAFT) si marcheaza randurile rezolvate verde in sheet-ul containerelor. Foloseste cand primesti un container nou si trebuie sa "pui stoc la HA-uri" / sa activezi produsele noi pe magazinele deals.
---

# adaugare-stoc-produse

> Autor: **Anne**. Runbook + tool pentru pus stoc la marfa noua pe magazinele deals.
> Construit iul-2026 pe containerul C41 (31 SKU-uri HA), verificat cap-coada de user.

## Ce face (pe scurt)
Pentru fiecare SKU nou dintr-un container (tab in sheet-ul de receptii, ex `C42`):
- **MAGDEAL (master):** stoc = cantitatea din sheet + **barcode** + **inventory tracking** ON +
  **policy DENY** ("sell when out of stock" DEBIFAT) + **scoate tag `test`**.
- **Casa Ofertelor / Reduceri Bune / Ofertele Zilei (satelite):** DOAR **barcode** + **tracking** ON.
  **NU pui stoc pe ele** — un app de sync pe barcode copiaza stocul de pe Magdeal.
- **Dublurile** (produs duplicat fara barcode si cu stoc 0) → le pune pe **DRAFT**.
- **Marcheaza verde** randul rezolvat in sheet-ul containerului.

## ⚠️ REGULA DE AUR (de ce doar pe Magdeal)
Magazinele deals au un **app de sync pe BARCODE**. Sync-ul e **reactiv si bidirectional**:
copiaza schimbarile de stoc intre magazine, cu intarziere, in val.
- **Pune STOCUL doar pe MAGDEAL.** Daca scrii stoc pe toate 4 magazinele deodata, sync-ul
  amplifica scrierile (delta-uri care se propaga inainte-inapoi) si cifrele **drifteaza**.
  Dovedit: setand toate 4 pe C41, un SKU a sarit de la 500 la **3030**. Pus doar pe Magdeal →
  satelitele au convergit corect.
- **Barcode + tracking + policy + tag** = OK sa le pui pe toate (sync-ul NU se lupta cu ele,
  doar cu cantitatea de stoc).
- Foloseste **`on_hand`** (nu `available`) — e cantitatea fizica primita.

## Setup
- **Auth Google** (sheet-uri): OAuth Desktop la `~/.config/gcp/sheets-token.json`
  (vezi `core:export-to-google-sheet`).
- **Shopify:** tokenii din `SHOPIFY_STORES_CSV` (env cu path/text) sau din KB secret
  (scriptul ii rezolva singur; vezi `gigi:shopify-stores`). **Nu printa tokenul.**
- **Windows:** scriptul forteaza UTF-8 la output (diacritice).
- Rularea: `uv run scripts/add_stock.py <cmd> --container C42` (PEP723, deps inline).

## Magazine (prefix → magazin)
| prefix | magazin | rol |
|---|---|---|
| **MAG** | magdeal.ro | **MASTER** — aici pui stocul |
| BON | casaofertelor.ro | satelit (copiaza prin sync) |
| RED | reduceribune.ro | satelit |
| OFER | ofertelezilei.ro | satelit |

## Sursele de date
- **Sheet containere** (`1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU`): un tab per container
  (`C40`, `C41`, `' C42 08 Iulie'`…). Coloane tipice: `# | Descriere | Categorie | SKU | Cantitate | UM | Status`.
  **Rand ALB = de facut; verde/galben (puse de user) = ignorate.** Scriptul detecteaza singur
  coloanele SKU/Cantitate si randurile albe.
- **Sheet barcode-uri TOM** (`10eSCKItlCHMl8S5A2YGjBZBZwRe506HH0ETpgR7BV7A`): coloane `Sku`→`Barcode`
  in tab-urile `✅ TOM - WINNER_WORK` / `✅ TOM - TO BE VERIFIED_WORK` / `SHOPIFY_VARIANTS_CASA`.
  Daca un SKU **n-are barcode** aici, scriptul se opreste — completeaza barcode-ul in sheet intai
  (fara barcode nu se face sync-ul).

## Fluxul de lucru (comenzi)
Toate primesc `--container C42`. `apply`/`dupes`/`green` scriu doar cu `--apply` (altfel dry-run).

1. **`plan`** — citeste tabul (randuri albe), harta de barcode, si arata pentru fiecare SKU:
   barcode, stocul curent pe Magdeal, si daca are dublura. Semnaleaza barcode lipsa/conflict.
   ```
   uv run scripts/add_stock.py plan --container C42
   ```
2. **`apply --apply`** — pe fiecare SKU alb: **Magdeal** = stoc(on_hand)=cantitate + barcode + DENY +
   tracking + scot `test`; **satelite** = barcode + tracking (fara stoc). Se opreste daca lipsesc barcode-uri.
   ```
   uv run scripts/add_stock.py apply --container C42 --apply
   ```
3. **`dupes --apply`** — trece pe **DRAFT** dublurile (produs cu acelasi SKU pe Magdeal, fara barcode
   si cu stoc 0). Produsul real (cu barcode) ramane ACTIVE. (Gardat: nu atinge daca nu exista un real cu barcode.)
   ```
   uv run scripts/add_stock.py dupes --container C42 --apply
   ```
4. **`verify`** — verificare completa read-only: pe toate 4 magazinele, per SKU: barcode corect,
   policy DENY, tracking ON, `on_hand` = cantitate, produs ACTIVE, fara tag `test` pe Magdeal.
   ⚠️ Ruleaza dupa ce sync-ul a avut timp sa duca stocul pe satelite (secunde-minute). Daca satelitele
   inca arata cifre vechi/gresite, **mai asteapta si re-verifica** — nu re-scrie stoc pe ele.
   ```
   uv run scripts/add_stock.py verify --container C42
   ```
5. **`green --apply`** — marcheaza verde randurile care au trecut `verify` 100%.
   ```
   uv run scripts/add_stock.py green --container C42 --apply
   ```

## Capcane (citeste)
1. **Stoc DOAR pe Magdeal.** Nu scrie stoc pe satelite — sync-ul o face si se incurca daca scrii tu.
   Daca satelitele arata gresit dupa `apply`, e sync-ul care inca lucreaza; asteapta si re-`verify`.
2. **Barcode obligatoriu inainte de stoc.** Fara barcode potrivit pe toate magazinele, sync-ul nu
   copiaza. Daca `plan` zice "BARCODE LIPSA", completeaza in sheet-ul TOM intai.
3. **Barcode duplicat** = sync-ul poate anula stocul (il duce pe 0). Barcode-urile din seria
   `5901234903…` arata a placeholder — daca un SKU cu asa ceva se poarta ciudat, verifica-l manual.
4. **Dubluri de produs pe Magdeal:** unele SKU-uri au 2 produse (unul real cu barcode/stoc, unul
   junk de test fara barcode, stoc 0, tag `test`, dar ACTIV = vandabil in gol). `dupes` le pune pe DRAFT.
   Verifica INTOTDEAUNA ca produsul pe DRAFT e cel fara barcode si stoc 0 (scriptul o gardeaza, dar uita-te).
5. **Alb vs verde/galben:** randurile pe care userul le-a colorat deja (verde=gata, galben=alt scop)
   sunt IGNORATE. Doar albul e de facut. (Bug rezolvat: Google omite canalele de culoare =0, deci
   galben `{red:1,green:1}` NU e alb — defaulteaza canalele la 0, nu la 1.)
6. **`on_hand`, nu `available`.** Cantitatea din sheet = marfa fizica primita → `on_hand`.
7. **REPLY/verificare la sursa:** nu spune "gata" fara `verify` pe toate 4 magazinele. Stocul se
   aseaza cu intarziere (sync) — verifica dupa ce s-a linistit (fara miscari intre 2 citiri la 60s).

## Legatura cu alte skill-uri / memorii
- `gigi:shopify-stores` — rezolvarea magazinului + tokenului, cookbook de mutatii.
- `core:export-to-google-sheet` — auth Google Sheets.
- Memorie: `ha-deals-stock-magdeal-master` (regula "stoc doar pe Magdeal").
- `anne:stoc-arona` — raportul lunar de stoc/valoare (alt scop: citeste stocul, nu-l pune).
