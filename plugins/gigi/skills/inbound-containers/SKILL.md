---
name: inbound-containers
description: "Ce marfa vine din China, IN CE CONTAINER si in ce cantitati — si daca a mai fost comandata. Combina TOM Arona (purchase_orders: ce s-a cerut / anulat / expediat) cu FISIERUL DE CONTAINERE din KDocs/WPS (packing list real pe container: SKU, comandat vs sosit, cutii, CBM, pret). Use when the user asks: cand vin X, in ce container vine, ce e in containerul 43, cate bucati vin, a mai fost comandat, s-a comandat restock, ce contine urmatorul container, packing list, purchase order TOM, TOM-014, sau intreaba de marfa pe drum / inbound stock."
user-invokable: true
---

> **ARONA (gigi).** Raspunde la „**cand vine X si in ce container?**". Complementar cu
> [[reorder-planner-skill]] (CAT si CAND sa comanzi) si `gigi:stock-restock-alerts` (ce e pe terminate).
> Astea doua se uita la ce AI; asta se uita la ce **e pe drum**.

# inbound-containers — ce vine, in ce container, cate bucati

## 🔄 PRIMUL PAS OBLIGATORIU — verifica prospetimea (nu raspunde din memorie)
La ORICE intrebare despre containere / marfa pe drum, INAINTE de a raspunde verifica **3 lucruri**
(userul a cerut explicit — 15-iul-2026, vezi [[container-check-freshness]]):
1. **A mai SOSIT un container?** `tom_po.py containers` → cel mai mare grup (ex. „Container 57-58-59").
   Daca apare unul peste ce stiai (60+) = ceva nou → scaneaza-l.
2. **A mai fost PUBLICAT unul?** In KDocs, `window.__sheets()` → daca e o **foaie noua** peste ultima
   stiuta (ex. #60), s-a publicat un container nou → scaneaz-o vizual.
3. **Ce STATUS au produsele comandate in TOM?** `tom_po.py search --q <produs>` → statusul pe LINIE
   (`NEW`/`ORDERED`/`RECEIVED`/`SHIPPED`/`CANCELLED`) + statusul PO-ului. Include-l in raspuns
   („comandat, inca neexpediat" vs „receptionat" vs „anulat in TOM dar produs pe container").
4. **Foile KDocs se COMPLETEAZA in timp** — un container poate fi PARTIAL cand te uiti (se mai adauga
   SKU/cantitati). NU trata o foaie deja scanata ca finala: re-verific-o si **semnaleaza in raspuns
   daca pare in lucru** (randuri goale, col „实到 Actual" necompletata, foaie abia inceputa).
Doar dupa ce ai confirmat ca nu e nimic nou/necompletat (sau ai scanat ce e nou) → raspunzi, mentionand statusul TOM.

## ⚠️ Regula de aur (lectia care schimba raspunsul)
**TOM Arona NU e sursa de adevar pentru CE E in container.** TOM tine ce s-a *cerut* la furnizor.
Cand aprovizionarea trece pe **tabele pe marimi**, liniile din TOM sunt **CANCELLED** (nota
`"Use of tables in multiple sizes"`) si evidenta reala se muta in **fisierul de containere KDocs**.

TOM **stie** grupurile de containere (`shipments` = „Container 43-44-45", „46-47-48", …) si ce PO-items
sunt atasate — dar sunt toate **`DRAFT`, fara `departedAt`/`arrivedAt`**, si **liniile anulate nu-s
atasate niciunui container**, desi marfa exista fizic. Deci TOM iti da *scheletul*, KDocs *continutul*.

| Intrebare | Sursa |
|---|---|
| „a mai fost comandat X?" / „ce s-a cerut la furnizor?" | **TOM** (`tom_po.py search`) |
| „ce containere sunt planificate / cum sunt grupate?" | **TOM** (`tom_po.py containers`) |
| „**ce e in containerul 43**? cate bucati/cutii/CBM? ce SKU?" | **KDocs** (fisierul de containere) |
| „cat stoc am acum / cat vand" | `gigi:fulfillment-analytics`, `gigi:stock-restock-alerts` |

Un item **CANCELLED in TOM poate fi totusi produs si incarcat in container** — verifica intotdeauna
ambele inainte sa spui „nu mai vine".

---
## 1. TOM Arona — ce s-a comandat / anulat (scriptat, instant)
```bash
cd plugins/gigi/skills/inbound-containers/scripts
export DATABASE_URL_TOM="$(uv run ../../../../core/scripts/kb.py secret-get DATABASE_URL_TOM)"

uv run tom_po.py search --q pijam          # toate liniile (si anulate), cu motivul anularii
uv run tom_po.py search --q satin --open   # doar liniile ACTIVE (necanulate)
uv run tom_po.py po --tom TOM-014          # detaliul unui PO
uv run tom_po.py recent --limit 20         # ultimele PO-uri (vezi daca exista un restock nou)
uv run tom_po.py containers                # grupurile de containere + ce PO-uri contin
uv run tom_po.py containers --name 43      # ce e atasat containerului 43 (dupa TOM)
uv run tom_po.py shipments                 # loturile TOM, brut (status/carrier/date)
```
**Capcane SQL in TOM** (te blocheaza din prima daca nu le stii):
- coloanele sunt **camelCase → obligatoriu ghilimele**: `poi."externalSku"`, `po."tomNumber"`, `"createdAt"`.
- FK-ul e **`purchase_order_items."poId"` → `purchase_orders.id`** (NU `purchase_order_id`).
- statusul liniei (`poi.status`) ≠ statusul PO-ului (`po.status`): un PO `PARTIALLY_SHIPPED` poate avea
  toate liniile de interes `CANCELLED`. **Judeca pe linie, nu pe PO.**
- titlurile-s mixte RO/EN → cauta pe mai multe chei (`pijam`, `satin`, `pajam`, `睡衣`).

## 2. Fisierul de containere (KDocs / WPS) — packing list-ul real
Link-ul e un **share view-only**: fara copy, fara download, fara Ctrl+F. Se citeste prin
**chrome-devtools MCP** (`new_page` → `evaluate_script` → `take_screenshot`).

**O foaie = un container**, denumita `#9`, `#10`, … `#59`. Structura foii:

| col | continut |
|---|---|
| A | 商品编码 Code (barcode / cod produs; uneori SKU-ul HA-####) |
| B | 产品图片 Picture (**poza — asa recunosti produsul la zoom mic**) |
| C | 采购 order QLT (**comandat**) |
| D | 实到 Actual QLT (**sosit efectiv** — compara-l cu C, asa vezi lipsurile) |
| E | 实到箱数 BOX (cutii) |
| F | 实到体积 Volume (**CBM**, adesea *merged* pe tot blocul de produs) |
| G | 标签 label (小/大) |
| H | 规格 (**spec = SKU-ul de varianta**, ex `rosu-XS`, `albastru-4XL`, + `50pcs/box`) |
| I | 品名 (nume produs, chinezeste — ex 睡衣 = pijamale, 吸顶灯 = plafoniera) |
| J | 材质 (material) · K price (USD/buc) · L/M total |

### Fluxul (verificat)
```
1) mcp chrome-devtools new_page <link KDocs>          # se deschide read-only, e ok
2) evaluate_script: lipesti scripts/kdocs_nav.js      # defineste __sheets/__go/__wheel
3) evaluate_script: window.__sheets()                 # lista containerelor ["#9"..."#59"]
4) pentru fiecare container tinta:
     evaluate_script: await window.__go('#43', 25)    # zoom 25 = scanare (~18 randuri/ecran)
     take_screenshot                                  # RECUNOSTI produsul DUPA POZA (col B)
     evaluate_script: await window.__wheel(2, 1)      # scroll jos; (n, -1) = sus
   daca gasesti produsul:
     evaluate_script: await window.__go('#43', 90)    # zoom 90 = citesti H (SKU) + C/D/E/F
     take_screenshot                                  # noteaza SKU, comandat, sosit, cutii, CBM
```

### ⚠️ Ce NU merge (nu pierde timp)
- **Valorile celulelor nu sunt in JS** — traiesc intr-un core WASM. `range.Text`, `.Value2`,
  `getValue2()`, `queryRangeValues()` → **toate `undefined`**. Nu exista extragere programatica;
  **citesti din screenshot**. (De aia zoom 25 pt scanat + zoom 90 pt citit.)
- `onScrollToCellLeftTop()`, `setScrollPos()`, `Ctrl+Home`/`Ctrl+End` → **nu misca** foaia.
  Singurul scroll care merge = eveniment `wheel` pe canvas (`__wheel`).
- `s.activate()` fara `await s.loadSheetData()` + ~900ms pauza → foaia apare **goala**.
- NU pune filtre / nu sorta pe foaie: e documentul echipei, orice editare se propaga.

## 3. Raspunsul pe care il dai
Per container: **SKU (spec), comandat, sosit, cutii, CBM, pret** + total pe container. Spune explicit
**care e urmatorul container care soseste** (intreaba userul daca nu stii — nu se deduce din fisier)
si daca `实到 Actual` = `采购 order` peste tot (adica **nu lipseste nimic** fata de comanda).
Inchei cu verdictul din TOM: *„si nu a mai fost comandat / a fost recomandat in TOM-0XX"*.

## Exemplu real (14-iul-2026, „cand vin pijamalele? in ce container?")
- TOM: toate cele 36 de linii de pijamale = **TOM-014**, toate **CANCELLED** (15-mai, „Use of tables in
  multiple sizes"); niciun PO ulterior nu le contine; **0 linii de pijamale atasate vreunui container**
  in `shipments` → daca te opreai aici, spuneai gresit *„nu mai vin"*.
- KDocs: pijamalele sunt totusi **produse si incarcate** — containerele **#43** (1.984 seturi, 40 cutii,
  ~5 CBM), **#48** (2.348), **#52** (2.996), **#58** (1.750) = **9.078 seturi**, 6,2–7,3 USD/set;
  peste tot `实到 Actual` = `采购 order` (nu lipseste nimic). Restul containerelor (#44–47, #49–51,
  #53–57, #59) — fara pijamale. Vezi memoria [[container-pipeline-kdocs]].

## Siguranta
Read-only peste tot: TOM = `SELECT`; KDocs = doar navigare/zoom/screenshot (fara editare, fara filtre).
Secretele se iau din KB (`kb.py secret-get DATABASE_URL_TOM`) — nu se scriu niciodata in cod sau chat.
