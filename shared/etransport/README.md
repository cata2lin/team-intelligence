# SmartBill e-Transport Generator 🚛

Aplicație Python pentru generarea automată a fișierului Excel (XLSX) necesar importului de produse în modulul e-Transport din SmartBill. Această soluție preia date din documente vamale, documente comerciale (facturi PDF, packing list-uri) și din informații ad-hoc de transport, unificându-le și aplicând logica de business impusă de ANAF.

## 🚀 Funcționalități Principale

- **Procesare Inteligentă PDF-uri**: Extrage tabelar și textual date din Commercial Invoice-uri și Packing List-uri.
- **Normalizare Coduri NC**: Converteste automat codurile HS (de 10 cifre sau alte variații) în formatul strict de 8 cifre (NC8).
- **Corelare și Matching Logistic**: Conectează datele din Invoice (valori, produse, coduri) cu greutățile din Packing list, recunoscând denumirile produselor chiar dacă sunt diferite (folosind potriviri "Fuzzy" bazate pe text și algoritmi de proximitate).
- **Distribuție Inteligentă de Greutăți**: Acolo unde Packing List-ul dă doar totalul net/brut, aplicația distribuie aceste greutăți proporțional către produsele de pe factură, păstrând sumele totale cu o precizie matematică (zero pierderi din rotunjiri).
- **Conversii Valutare**: Suport nativ pentru transformarea valorilor din valuta de achiziție (USD, EUR, CNY, GBP etc.) conform unui curs preconfigurabil în RON.
- **Export "Audit-Ready"**: În afara fișierului vizat de SmartBill, generează și un format JSON complet transparent cu pașii parcurși (ce reguli au fost aplicate și cum s-au extras datele) - pentru rapoarte financiare clare.
- **Interfață Streamlit**: Aplicație web rapidă pentru operarea de catre non-programatori.

---

## 🛠️ Cum funcționează Arhitectura

Sistemul este împărțit în module decuplate, pentru a fi ușor extins în cazul în care un furnizor nou trimite un tip de PDF complet ieșit din comun:
- `models/`: Definiția Pydantic a datelor. Aici știm constant ce structură avem "în memorie".
- `parsers/`: Conține extractoarele de PDF (`invoice_parser.py`, `packing_list_parser.py`) și extractoare pe bază de RegEx din text liber (`transport_parser.py` extrage inteligent CUI, numere Auto, Telefoane Șofer din "paste-uri" neorganizate).
- `services/`: Piesa de rezistență - unifică datele, aplică logica de agregare și distribuția de greutate. Verifică și integritatea logică (`validation_service`) ca să se asigure că datele nu vor da eroare la încărcarea în SmartBill.
- `exporters/`: Scrie obiectul `Shipment` într-un fișier Excel perfect formatat pentru cerințele platformei externe.
- `utils/`: Soluții tehnice punctuale pentru curățarea string-urilor, unificarea U.M.-urilor și maparea codurilor tarifare.

---

## 💻 Instalare

Această procedură acoperă activarea mediului de lucru din terminal:

```bash
cd etransport
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🎮 Cum se Formulează Testele / Execuția

### 1. Lansarea Interfeței Grafice (UI web)
Cea mai comună formă de a rula codul, vizuală și rapidă:
```bash
streamlit run app.py
```
*(va deschide un tab nou în browser automat)*

### 2. Rularea din Linie de Comandă (CLI)
Foarte utilă pentru testare sau pentru a lega codul mai târziu la alte micro-servicii automate. Outputurile sunt puse automat în folderul `./output`
```bash
python -m etransport.main \
    --invoice test_data/factura.pdf \
    --packing-list test_data/packing_list.pdf \
    --transport "TIIU5478466 CT64ADT/CT01AOT 0761283435 Nichei Pavel" \
    --carrier "ANDI TRANS SRL RO5607012" \
    --operation import \
    --currency USD \
    --exchange-rate 4.65 \
    --customs-office ROCT0900 \
    --dest-county Brasov \
    --dest-city Brasov \
    --dest-street Bazaltului \
    --dest-number 11 \
    --dest-postal 507225
```

---

## 🧩 Adăugarea de noi documente "neprietenoase"

Logica PDF-urilor a fost testată contra majorității machetelor standard (Commercial Invoice Table). Totuși, uneori anumiți producători din afara UE au formate complet debusolate:

**Pentru facturi anormale:** 
Modificați `etransport/parsers/invoice_parser.py`. Fie adăugați Regex-uri mai fine în `_parse_products_from_text`, fie calibrați keywords-urile din `_identify_columns` (unde definește sinonimele fiecărui cap de tabel). 

**Pentru mapări noi de unități (ex: `bags`):**
Cea mai ușoară soluție: deschideți `etransport/config.py` și adăugați unitatea în dicționar `UNIT_MAPPINGS` (nu e nevoie de cod nou, dicționarul din `config.py` e citit de utilitare la runtime).


## Catalog Locatii Start

* **customs_office** = locul formalităților vamale pentru import.
* **ptf** = punctul de trecere a frontierei pentru fluxuri intracomunitare / rutiere.

Exemple de utilizare CLI sau Config:
--customs-office 232901
--customs-office "232901 - BVF Otopeni Calatori (ROBU1030)"

--ptf 37
--ptf "37 - Nadlac 2 - A1 (HU)"

Ambele forme sunt corect decodate și mapate in JSON sub obiectele complete start_customs_office respectiv start_ptf.
