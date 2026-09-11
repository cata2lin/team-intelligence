---
name: barcode-nou
description: Genereaza un EAN-13 nou GARANTAT liber pe TOATE magazinele Arona (nu se ciocneste cu niciun barcode existent). Trage LIVE toate barcode-urile de pe cele ~21 magazine Shopify (sursa de adevar - warehouse-ul metrics acopera doar 14 branduri si-i lipseste Magdeal, master-ul de sync HA, deci NU e de incredere), apoi continua seria interna 200xxxxxxxxxx (gama pe care GS1 o rezerva pt uz intern, deci nu se ciocneste cu GTIN-uri reale de furnizor). Poate scoate mai multe deodata, randa PNG de eticheta si verifica un EAN dat. Foloseste cand ai nevoie de "un barcode nou care nu e pe niciun magazin", "un EAN liber", "cod de bare unic pt un produs nou".
---

# barcode-nou

> Autor: **Anne**. Scoate un cod de bare (EAN-13) nou care **nu exista pe niciun
> magazin Arona**. Construit iul-2026 dupa ce am avut nevoie de un barcode unic
> pt un produs si warehouse-ul s-a dovedit incomplet.

## De ce nu ajunge warehouse-ul (capcana centrala)

Tentatia e sa cauti barcode-urile in `metrics.variants` (Postgres, instant). **NU
te baza pe el pentru unicitate:** acopera doar **14 din 21 de branduri** si tocmai
ii lipsesc magazinele "deals" cu **0 variante** in warehouse — inclusiv **Magdeal**,
care e **master-ul de sync barcode HA** (barcode-urile se copiaza de pe Magdeal pe
casa/reduceri/oferte). Adica exact unde e riscul de coliziune. Deci **verifica LIVE
din Shopify pe toate magazinele**, nu din warehouse.

## Ce face scriptul

1. Ia lista de magazine din secretul KB `SHOPIFY_STORES_CSV` (nu printa tokenii).
2. Pagineaza `productVariants` pe fiecare din cele ~21 magazine si aduna toate
   barcode-urile completate (~3500 aparitii → ~1630 distincte; multe se repeta
   fiindca magazinele deals partajeaza acelasi EAN prin sync).
3. Continua **seria interna monoton**: prefix `200` + contor, dupa cel mai mare
   numar deja folosit (nu umple goluri vechi → nu reutilizeaza un barcode retras),
   cu cifra de control EAN-13 corecta. Prefix `200-299` = gama rezervata de GS1
   pentru **uz intern/in-store**, deci nu se suprapune niciodata cu un GTIN real de
   furnizor.

## Cum rulezi

Din `scripts/` (copiaza-l in scratchpad daca vrei, e PEP723 → `uv run`):

```bash
uv run next_barcode.py                     # 1 barcode liber
uv run next_barcode.py --count 5           # 5 libere consecutive
uv run next_barcode.py --image --out .     # + PNG de eticheta (barcode_<ean>.png)
uv run next_barcode.py --check 2000000000527   # e liber DA/NU + pe ce magazine apare
uv run next_barcode.py --quiet             # fara logul per-magazin
```

## Reguli de aur

- **Verifica din nou CHIAR inainte sa-l scrii pe produs.** Verificarea reflecta
  starea de acum; daca intre timp cineva seteaza acelasi numar, se dubleaza. (Chiar
  s-a intamplat la constructie: un numar recomandat a aparut pe Grandia peste cateva
  minute — foloseste `--check <ean>` inainte de a-l salva.)
- **Daca vreun magazin ESUEAZA** (apare in "MAGAZINE ESUATE"), unicitatea **nu e
  garantata** pana nu reusesc si alea — de obicei un token OAuth expirat (ex. Nubra
  ruleaza pe VPS, vezi `gigi:shopify-stores` §3). Nu da barcode-ul mai departe pana
  nu-s toate `ok`.
- **Nu printa tokenii Shopify.** Scriptul ii ia din KB si-i tine in proces.
- **Windows:** scriptul are deja `sys.stdout.reconfigure(encoding="utf-8")` (altfel
  crapa pe consola cp1252).

## Ca sa-l pui efectiv pe produs

Scriptul doar GENEREAZA numarul. Ca sa-l setezi pe o varianta Shopify foloseste
`gigi:shopify-stores` (`productVariantsBulkUpdate`, campul `barcode`) — verifica
`userErrors` dupa mutatie. Spune magazinul + SKU-ul si se poate face din acelasi
flux.

## Dependinte

- `requests` (API Shopify), `python-barcode` + `pillow` (PNG) — declarate inline
  (PEP723), se instaleaza singure la `uv run`.
- Secret KB: `SHOPIFY_STORES_CSV`. Acces: `core:knowledge-base` / `core:fetch-secret`.
- Inrudit: `gigi:shopify-stores` (acces + mutatii), `anne:stoc-arona` (foloseste
  aceleasi magazine si `productVariants`).
