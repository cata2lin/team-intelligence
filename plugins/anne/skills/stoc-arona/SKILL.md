---
name: stoc-arona
description: Construieste raportul lunar de STOC (cantitati per magazin/SKU, live din Shopify) si VALOAREA de stoc pentru contabila (Cogs din facturile de import in LEI × cantitate) in Google Sheet-ul "1 <luna>". Trei parti - (A) snapshot cantitati pe cele ~8 grupuri de magazine Arona cu regulile de dedup/excludere, (B) pretuire SKU din facturile PDF de import prin sheet-ul de receptii + kdocs master, cu potrivire pe MODEL/NUME/CANTITATE (nu doar pozitie) si fallback din lunile trecute, (C) verificarea majora (vs facturi) + completitudine (produse active lipsa) + tabelul sumar cu formule. Foloseste cand faci "stocul" lunar sau actualizezi valoarea de stoc.
---

# stoc-arona

> Autor: **Anne**. Runbook pentru raportul de stoc + valoare (contabila). Construit + rafinat iul-2026.
> Rulat cap-coada pe `1 iulie`: 818/821 SKU cu Cogs, verificat, aprobat de user. Vezi memoria `project-stoc-valuation`.

Doua livrabile in acelasi Google Sheet (tab `1 <luna>`, ex `1 iulie`,
spreadsheet `1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0`):
- **Coloane A-D = STOC**: `Magazin | Categorie | SKU | Cantitate` (cantitati live din Shopify).
- **Coloane E-F = VALOARE**: `Cogs | Valoare stoc` unde **Cogs = pret import in LEI** (nu COGS-ul de pe site, care e ~3.4× mai mare) si **Valoare = Cantitate × Cogs**.
- **Coloane G-J = SUMAR** (formule): per bloc `SUM(F...)` + TVA/USD + procent (vezi PARTEA C).

Auth Google: OAuth Desktop la `~/.config/gcp/sheets-token.json` (vezi `core:export-to-google-sheet`).
Shopify: helper `shopify_gql.py` din `gigi:shopify-stores` (rezolva tokenul, **nu-l printa**).
Windows: pune mereu `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
Toate scripturile = PEP723 (`uv run`), din folderul `scripts/` (copiaza-le in scratchpad si lucreaza acolo).

---

## PARTEA A — STOCUL (cantitati) → `refresh_all.py`

**Regula de aur:** doar produse **ACTIVE cu stoc pozitiv**; trage prin **`productVariants`**
(NU prin `products` cu variante imbricate — rateaza SKU-uri tacit). Fara dublari: acelasi SKU o SINGURA data.

Magazine mirror (impart ACELASI stoc fizic) — ia de la UNUL singur, **nu aduna**:
- **Pijamale (Nocturna)**: NOC/LUX/BG identice -> ia doar **NOC**. Exclude `surpriza`.
- **Genti (Gento)**: Gento + Apreciat identice -> ia doar **Gento** (GEN). Exclude `surpriza`.
- **Covoare (Covoria)**: Covoria/Carpetto/Reduceri -> ia de pe **Carpetto** (CARP) + adauga `baie-verde`
  daca e activ pe Covoria. Subcat: `baie-*` = **Covorase**, restul = **Covoare**.

Reguli per grup:
- **ROSSI**: dedup pudre (apar 2x: standalone + varianta din kit, ACELASI stoc). Subcat: Pudra / Esentiale /
  Accesorii unghii. **Exclude** kit-urile virtuale (stoc fantoma ~9000): `kitpolygel-3`, `R196-base+top`,
  `kit-culoare`, `kit-3culori`, `kit-6culori`.
- **Casa Ofertelor** (= **BON** / bonhaus = CasaOfertelor.ro): **doar** SKU-urile din lista `1 iunie` (subcat
  `Mixtit`), **fara niciun HA**. Non-HA noi -> merg la Facebook.
- **Facebook** (deals): **toate HA-urile ACTIVE cu stoc** din **union pe 4 magazine** (BON/MAG/OFER/RED)
  **MINUS cele cu tag `test`** (tagul e pe **Magdeal**). Prioritate cantitate: MAG->BON->OFER->RED. PLUS
  non-HA din lista `1 iunie` Facebook (ex `set-8-snur-perdele-auriu`, asternut-*).
- **Grandia** (GRAN): tot activ, **MINUS** covoarele Covoria (cross-listate) si `R203-naildrill` (ROSSI).
  Covorasele proprii Grandia (GD-KID play mats) RAMAN.
- **Parfumuri**: bloc **manual al userului** (sticle/capace/pompite, cost in col E) -> **NU-l atinge NICIODATA**.

Scrie A-D cu **rand gol intre magazine**, header ingrosat+inghetat, **pastreaza Parfumuri** (`write_preserve.py`).

---

## PARTEA B — VALOAREA (Cogs import in LEI)

Contabila are facturile de import (China, Guangzhou Tang Xiaoyang), cu valori **mai mici** decat site-ul.
**Lant:** factura (pret USD, fara SKU) -> receptie (are SKU) -> SKU din stoc -> `Cogs LEI = pret_USD × curs`.

### B0. Sursele de SKU (per container)
1. **Sheet receptii** `1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU`, tab per container `C16`..`C45`
   (unele au SPATIU in nume, ex `' C42 08 Iulie'`). **NU are pret** -> pretul vine din PDF-ul facturii.
   ⚠️ **Header-ele DIFERA de la tab la tab** — nu presupune coloane fixe. Variante intalnite:
   - `# | Descriere | SKU | Cantitate | UM | STATUS` (C23; SKU col2, qty col3)
   - `# | Descriere | (SKU fara header) | Cantitate` (C20; SKU col2)
   - `SKU | Titlu | Cantitate` (C16/C17/C19; SKU col0, titlu col1, qty col2)
   - `... | Quantity(col7) | ... | SKU(col12)` (C18; header EN lung)
   - `# | Descriere | SKU | Cantitate | UM | Pret | Valoare` (C24)
   **Inspecteaza intai** cu un dump al primelor 5 randuri ca sa afli indicii coloanelor (`insp.py`).
2. **kdocs master** "New product shipment data" (kdocs.cn, WPS) — foi `#9`..`#57` (= containere C9..C57).
   Structura variaza: `#14` = model(col0) img(col1) qty(col2) pret(col3)...; `#13` = model(col0) img(col1)
   qty(col2) **SKU-complet GD-*(col3)**. **Container 9 = primul cu Grandia.** Se citeste doar prin
   browserul controlat (chrome-devtools) dupa ce userul se logheaza WPS:
   `WPSOpenApi.Application` e **async** (`await`!) — enumera foile via `wb.Sheets`, apoi
   `await (await sheet.Range("A1:F70")).Value2`. Celulele sunt pe canvas, NU in DOM. Pretul din kdocs
   (col E) **NU** e pretul din factura -> foloseste tot factura; kdocs = doar sursa de SKU/model.

### B1. Potrivirea factura ↔ SKU — 3 strategii, in ordine (order-INDEPENDENT)
Receptiile vechi sunt **sortate diferit** de factura, deci pozitionalul pur crapa. Foloseste dupa caz:

- **`process_pos.py <C> <fx> <data> <hrow> <si> <ci>`** — pozitional (two-pointer pe cantitate) cand
  receptia **urmeaza ordinea facturii** (ex C23, C18, C24). Prinde si cantitati care se repeta la preturi
  diferite (ex 3 noptiere 250buc @4/4/6) fiindca merge in ordine.
- **`process_name.py <C> <fx> <data> <hrow> <si> <ti> <ci>`** — **potrivire pe MODEL + NUME** (cea mai
  robusta, order-independent). Ordinea strategiilor per rand receptie:
  1. **numar de model** din SKU/titlu care apare in descrierea facturii (ex "Plafoniere model **12748**"
     ↔ SKU/titlu 12748) — daca da un pret unic -> ala e.
  2. **cuvinte semnificative** din titlu (len≥4, minus stopwords) care se intersecteaza cu descrierea
     facturii, restranse pe cantitate daca e nevoie (ex "dulap"/"mixer"/"aspirator").
  3. **cantitate unica** (fallback).
  Necesita `inv_desc_<C>.json` = `[[qty, usd, "descriere"], ...]` (transcrii descrierile din PDF).
- **`process_kdocs.py <C> "#N" <fx> <data>`** — pt containere **fara tab de receptie** (C13/C14): ia
  SKU/model din kdocs (`kdocs_<...>.json` salvat din browser) si potriveste pe **numar de model** cu
  `inv_desc_<C>.json`. Doar ce se leaga sigur (model in factura) primeste pret; generice fara model raman.
- **`process_one.py <C> <fx> <data>`** — varianta veche pozitionala care cere headere fixe
  `SKU`/`Cantitate`/`Categorie`. Foloseste-o doar daca tabul chiar are acele headere.

### B2. Reguli de pret
- **Skip parfum/ambalaj** (sunt in blocul Parfumuri al userului): SKU care contine `esteban`, `-lab`,
  `nubra`, `parfumuri`, `sticla-`, `sticle-`, `capac-`, `dop-`, `pulv-`, `pompite`, `cutii-`, `cosulete`,
  `recipient-inox`, `eticheta`, `cutie-3-est`, `cutie-indiv`. Si liniile de "Cutie de ambalare".
- **UM (buc vs set)**: daca receptia e in `set` iar factura in `buc`, `pret/set = pret/buc × (buc/set)`
  (ex rampe HA-1193: 2000 buc = 1000 set => $2/set).
- **Asternut** = pret pe MARIME: `-140`=1.5$, `-160`=1.8$, `-180`=2$ (nu flat).
- **Acelasi SKU in mai multe containere la pret diferit -> MEDIE** a preturilor reale (userul a cerut medie).
  Containerul se noteaza `Cxx+Cyy` in `prices.json`.
- **Curs USD->RON**: din `metrics.fx_rates` (`fromCurrency='USD'`,`toCurrency='RON'`), la **data vamuirii
  (= data receptiei din numele tabului)**. `fx_rates` incepe **12-apr-2026**; pt date mai vechi foloseste
  **4.334** (proxy). `rateDate` e ziua-1 (BNR publica pt ziua urmatoare).

### B3. Fallback cand NU exista factura (ROSSI/Gento/generice)
- **ROSSI (~85 SKU) si Gento (~38)** vin de la **alti furnizori** (nu chinezul) -> **nu au factura de import**.
  - ROSSI: ia Cogs **din `1 iunie` pe SKU** (tab-ul iunie are `Magazin|_|SKU|Cant|Cogs|Valoare`, col E=Cogs
    in lei). SKU-urile noi lipsa din iunie -> foloseste valoarea de familie (ex pudre R203 = **10.97 lei**).
  - Gento: seturile "6 piese genti" = **2 USD** in factura (C39) -> **9.0 lei** (2×4.5) flat pe toate
    (seturile piele/lux pot diferi, dar fara factura ramane aproximare).
- **Generice fara factura** (Grandia/Facebook care nu apar in nicio factura): **estimare din lunile trecute
  × factor**. Masoara intai tiparul cu `pattern.py`: raport `Cogs_factura / Cogs_iunie` pe SKU-urile care au
  AMANDOUA. Mediana masurata iul-2026: **Facebook ×0.285**, **Grandia ×0.295** (iunie = site-cogs, ~3.4×
  mai mare). Aplica `iunie × factor` (sau `mai/aprilie × factor` daca nu-s in iunie). Marcheaza containerul
  `EST-iunie×0.285` ca sa se stie ca-s aproximari, nu exact din factura.

### B4. Scrie in foaie → `fill_ef.py`
Umple **Cogs (col E, 2 zecimale) + Valoare (col F = Cant×Cogs)** din `prices.json`. Foloseste `cogs`
(override in lei) daca exista, altfel `usd×fx`. **Pastreaza blocul Parfumuri** (col E deja completat de user)
neatins — detecteaza randul "Parfumuri" si nu scrie sub el. Doar SKU-uri care EXISTA in stoc primesc pret.

---

## PARTEA C — VERIFICARI + SUMAR (obligatorii inainte de "gata")

### C1. Verificare vs facturi → `verify_all.py`
Confirma ca fiecare `usd` stocat **exista** in setul de preturi al facturii containerului (`inv_<C>.json` /
`inv_desc_<C>.json`). Flag: mismatch (usd care nu-i in factura) + anomalii (`cogs<=0` sau `>600`). Tinta =
**0 mismatch, 0 anomalii**. (Containerele procesate in alta sesiune fara `inv_*.json` local nu-s verificabile
automat — noteaza-le.)

### C2. Completitudine — NICIUN produs activ cu stoc lipsa → `completeness.py`
**Bug-ul clasic "snururi perdele":** un produs activ cu stoc ramane pe dinafara. Trage LIVE **toate cele 10
magazine** (ROSSI/NOC/GEN/CARP/COV/BON/MAG/OFER/RED/GRAN), aduna SKU-urile active cu `qty>0`, si scade
SKU-urile deja in foaie. Ce ramane = **suspecte**. Clasifica fiecare:
- tag `test` (verifica tagul pe MAG) -> corect exclus;
- kit virtual / `surpriza` -> corect exclus;
- **duplicat** sub alt cod (ex `oglinda` = `oglinda-acrilica` deja in foaie) -> nu-l adauga;
- **produs bagat de user AZI** (adaugat dupa data snapshot-ului) -> corect exclus din `1 <luna>`;
- altfel = **omis real -> adauga-l** si pretuieste-l.
Nu spune "complet" fara verificarea asta la sursa.

### C3. Tabelul sumar (formule) → `summary_formulas.py`
Afla limitele **reale** ale blocurilor (unde se schimba `Magazin` in col A — folosind randurile goale ca
separator) si scrie in **G1:J{n}**:
- G = eticheta bloc (Total + fiecare magazin + Parfumuri)
- H = `=SUM(F{start}:F{end})` (RON) — range-ul REAL al blocului (NU copia range-uri vechi, se decaleaza!)
- I = `=H{r}*0.23` (≈ USD / TVA, cum e formatata coloana la user)
- J = `=H{r}/$H$1` (procent din total)
Rand 1 = Total: `=SUM(F:F)`. Verifica: procentele dau 100%.

---

## Ordinea de lucru (checklist)
1. `refresh_all.py` -> `write_preserve.py` (STOC A-D, pastreaza Parfumuri).
2. Per container, in ordine (nou->vechi): dump receptie (`insp.py`) -> alege `process_pos` / `process_name`
   / `process_kdocs` dupa cum e aliniata receptia -> ruleaza cu cursul containerului.
3. Fallback: ROSSI/Casa/Gento din iunie pe SKU; generice fara factura = iunie×factor (`pattern.py` intai).
4. `fill_ef.py` dupa fiecare container (idempotent).
5. `verify_all.py` (0 mismatch) + `completeness.py` (niciun activ lipsa) + `summary_formulas.py`.
6. `dupecheck.py` (fara dublari intre magazine).

## Capcane (citeste)
1. `productVariants`, NU `products` cu variante — altfel ratezi SKU-uri tacit.
2. Mirror stores: ia de la UNUL, nu aduna. Test = tag pe Magdeal (verifica tagul, nu ghici).
3. **Receptiile au headere DIFERITE** — inspecteaza coloanele, nu presupune. Ordinea receptiei ≠ ordinea
   facturii la containerele vechi -> foloseste `process_name` (model+nume), nu pozitional.
4. Format SKU diferit receptie vs stoc (ex "baie-rosu **pufos**" vs "baie-**pufos**-rosu") -> normalizeaza /
   potriveste pe model, altfel produsul pare "negasit in factura" desi e (ex covorasele pufoase = C28 @2$).
5. Curs pe data VAMII (receptie), per container; proxy 4.334 pre-apr. Parfumuri = manual, nu atinge.
6. kdocs: `WPSOpenApi` e **async** (await!), celule pe canvas; pretul din kdocs NU e pretul facturii.
7. Tot in **LEI** in col E/F. "Exact din factura" = usd×curs (sau medie pe containere); "estimat" = iunie×factor.
8. La summary: range-urile `SUM` se decaleaza intre luni -> recalculeaza limitele reale, nu copia.
9. "Complet" doar dupa `completeness.py` la sursa. Produsele adaugate de user AZI nu intra in snapshot-ul lunii.
