---
name: gigi:data-analyst-test
description: "Generate a hiring SCREENING TEST for a Data Analyst from REAL Arona shipping data (AWBprint) — a 30k-AWB anonymized dataset + a requirements brief + a full answer-key/barem + injected AI/data-quality traps that catch a candidate who blindly pastes the CSV into ChatGPT without checking. Builds a deliverability-analysis exercise (overall / by county / by product / by store rates, cost anomalies, avg delivery time, refusal patterns), anonymizes all PII (keeps county only), models delivery dates (real ones aren't stored cleanly in the DB), and plants 6 categories of subtle data-quality traps (duplicate AWBs, spelling variants that split groupby, status casing, logical contradictions, impossible delivery times, mixed decimal formats) with a manifest + naive-vs-correct grading logic. Triggers: 'probă data analyst', 'test de angajare analist date', 'set de date pentru interviu', 'dataset pentru probă practică', 'candidate data test', 'capcane AI în dataset', 'answer key / barem pentru probă'."
argument-hint: "[--size 30000] [--out ~/Downloads/Proba_Data_Analyst] [build|traps|analyze|all]"
---

# data-analyst-test — probă practică de Data Analyst din date reale de livrare

Generează un **test de screening** complet pentru angajarea unui analist de date, folosind AWB-uri REALE
din baza **AWBprint** (sursa de adevăr livrare). Ieșirea: dataset anonimizat + cerințe + barem + **capcane AI**.

> **De ce există:** vrei să vezi cine chiar *analizează* datele vs. cine aruncă CSV-ul în ChatGPT și copiază.
> Capcanele sunt subtile (~0,5% din date) → nu spar erori evidente, dar un răspuns AI naiv le scapă și dă cifre greșite.

## Ce produce (în `--out`, implicit `~/Downloads/Proba_Data_Analyst/`)
| Fișier | Pentru | Conținut |
|---|---|---|
| `dataset_awb_30000.csv` | **aplicant** | 30k AWB anonimizat (doar județ, fără nume/telefon/adresă) + capcane înăuntru |
| `CERINTE_*.md` | **aplicant** | brief: context, dicționar coloane, 7 analize, criterii, avertisment „verifică integritatea" |
| `BAREM_*.md` | **doar tine** | soluția pe 7 dimensiuni + secțiunea de capcane (naiv vs corect) + rubrică 100p |
| `_capcane_AI.json`, `_anomalii_injectate.txt` | **doar tine** | manifeste cu ID-urile capcanelor |

⚠️ **Trimite aplicantului DOAR `CERINTE_*` + `dataset_*.csv`.** Copiază-le într-un folder curat ca să nu scape baremul/manifestele.

## Cele 7 analize cerute (baremul le calculează)
1. Livrabilitate generală (rată+nr) · 2. pe județ · 3. pe produs (SKU) · 4. pe magazin · 5. anomalii de cost ·
6. timp mediu de livrare · 7. tipare de refuz. Definiția livrării: succes = `livrat`+`ridicat_personal`; exclude `in_tranzit` din numitor.

## 🔑 Schema AWBprint (câștigată greu — NU improviza altceva)
- Conexiune: secret KB **`DATABASE_URL_AWBPRINT`** (host DB 38.242.226.83), **read-only**. Tabel `orders` (~672k).
- **NU există coloană `status`.** Statusul de livrare = **`aggregated_status`** (`delivered` / `back_to_sender`=refuz-retur /
  `cancelled` / `in_transit` / `refused` / `lost_in_transit` / `customer_pickup` / …). `shipment_status` e similar.
- **Județ** = `shipping_address->>'province'` (JSON; extrage DOAR asta — restul e PII). Internațional: filtrează `country_code='RO'`.
- **Produs** = `line_items->0->'inventory_item'->>'sku'` (primul line item). Magazin = join `stores` pe `store_uid` → `stores.name`.
- **Cost transport** = **`orders.transport_cost`** (autoritativ per comandă, gross cu TVA). NU suma `order_awbs` (rânduri duplicate).
- **DATE:** populate real doar `frisbo_created_at` (comandă, 100%) + `fulfilled_at` (expediere, ~99%).
  ⚠️ `shipment_created_at` / `shipment_status_date` sunt **GOALE peste tot**; `package_weight`/`package_count` = **0/NULL**.
  Data reală de livrare NU e stocată curat → **o modelăm** (`fulfilled_at` + zile tranzit pe curier/județ), altfel „timpul de livrare" nu se poate.

## 🪤 Cele 6 capcane AI (injectate de `traps`, documentate în barem)
1. **Dubluri AWB** (45 rânduri cu același `awb_id`) → naiv numără 30.045; corect dedup → 30.000.
2. **Variante scriere județ** (`Cluj`/`cluj`, `Iași`/`Iasi`, `Prahova`/`Prahova␣`) → naiv vede 45 „județe"; corect normalizează → 42.
3. **Status casing** (50× `livrat`→`Livrat`/`livrat␣`) → filtru `=="livrat"` le ratează → livrabilitate subestimată.
4. **Contradicții** (15 refuzate CU dată livrare + 15 livrate FĂRĂ dată) → cross-check status↔dată.
5. **Timp negativ** (12 rânduri `data_livrare`<`data_expediere`) → filtrează `zile<0` înainte de medie.
6. **Format numeric mixt** (6 costuri cu virgulă `12,50`) → coloana devine text; `.mean()` crapă; curăță `str.replace(',', '.')`.

**Semn că a picat = n-a verificat datele** (probabil AI copiat): raportează „30.045 AWB", „45 județe", livrabilitate <84,5% nejustificat, sau media de livrare trasă de negative. Corect după curățare = revine la barem.

## 🕵️ Canary AI în documentul Word (opțional, dar puternic)
Când livrezi cerințele ca **.docx** (nu doar .md), ascunde în el o instrucțiune-capcană de tip *prompt-injection*
pentru modelele AI — **text alb 1pt + „vanish"** (invizibil la citire normală, dar în text-stream). Conține un
**cod-martor** (ex. `RX-4417-VALID`), o **cifră greșită** de livrabilitate (ex. „~90%", real 84,5%) și o **metodă
inventată**. Dacă aplicantul dă documentul pe mână unui ChatGPT/Claude și copiază orbește, unul din ele apare în
răspuns → l-a procesat cu AI fără să citească. Implementare: `run.font.color.rgb=White` + `run.font.size=Pt(1)` +
`w:vanish` în rPr, pus în 2-3 locuri (final + o celulă de tabel). Documentează codul în barem + `_capcane_AI.json`.
NU prinde pe cine folosește AI doar ca asistent de cod dar rulează analiza reală pe CSV (ăla obține cifra corectă).

## Rulare
```bash
cd <acest skill>/scripts
uv run analyst_test.py all --size 30000 --out ~/Downloads/Proba_Data_Analyst   # build → traps → analyze + scrie toate documentele
# sau pe faze:
uv run analyst_test.py build   --size 30000 --out <dir>     # extrage + anonimizează + modelează date livrare
uv run analyst_test.py traps   --out <dir>                  # injectează cele 6 capcane + manifest
uv run analyst_test.py analyze --out <dir>                  # baremul (naiv vs corect) → tipărește + scrie BAREM
```
Rulează din `/Users/gheorghebeschea/Downloads/Scripturi` (ca `kb.py secret-get` să meargă). Totul e read-only pe DB.

## Capcane de implementare
- **Anonimizare obligatorie**: dataset-ul merge la EXTERNI → zero `customer_name`/`email`/`phone`/adresă completă. Doar `province`.
- Windows/consolă: scripturile forțează UTF-8 la output.
- Reproductibil: `random.seed(...)` fix → același set + aceleași ID-uri de capcană la re-rulare.

Related: [[cs-map-and-windows-encoding]] · [[profit-data-sources-truth]] · `core:query-postgres` · `gigi:fulfillment-analytics` (analytics reale, nu test).
