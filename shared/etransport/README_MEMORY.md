# e-Transport Memory & History System

## Prezentare

Sistemul de memorie e-Transport oferă:

1. **Import automat istoric** din XML-uri e-Transport vechi
2. **Memorie transportatori** — salvare automată, fără confirmare
3. **Memorie TARIC** — doar cu confirmare explicită
4. **Matching history-first** — caută întâi în istoric, apoi în catalogul SmartBill

## Importul Istoricului XML

```bash
# Default: ~/Downloads/Etransport/
python -m etransport.utils_import_etransport_history

# Cu folder specific:
python -m etransport.utils_import_etransport_history --folder /path/to/xmls
```

### Ce importă
- Doar fișiere `.xml` cu `e-transport` (case-insensitive) în nume
- Scanare recursivă
- Deduplicare prin SHA-256 hash

### Ce extrage
- Document complet (UIT, tip operațiune, dată transport, furnizor, transportator)
- Linii de produs (cod tarifar, denumire, cantitate, UM, greutăți, valoare)
- Transportatori (salvați automat în `carrier_history`)

## Diferența memorie transportator vs TARIC

| Aspect | Transportator | TARIC |
| --- | --- | --- |
| Salvare | **Automată** | **Doar cu confirmare explicită** |
| La import XML | Se salvează automat | Se salvează ca `suggested` |
| La generare nouă | Se salvează automat | NU se memorează automat |
| Override manual | Posibil (scrie alt transport nou) | Posibil (confirmare/respingere) |
| Status-uri | N/A | `suggested`, `confirmed`, `rejected` |

## Ordinea de matching (history-first)

1. **Memorie TARIC confirmată** — `product_tariff_memory` (status=`confirmed`)
2. **Override manual** — `product_tariff_overrides` (legacy)
3. **Istoric XML: hs_code + product_name** — match exact pe ambele
4. **Istoric XML: product_name** — match exact pe nume normalizat
5. **Istoric XML: hs_code** — match exact pe cod HS
6. **Catalog SmartBill** — `tariff_codes` (exact pe NC8 sau HS8)
7. **Fallback NC8** — codul brut NC de 8 cifre
8. **needs_review** — nu a găsit nimic

## Tabele DB (data/etransport.db)

- `tariff_codes` — catalog SmartBill local
- `product_tariff_overrides` — override-uri manuale (legacy)
- `etransport_documents` — documente istorice importate
- `etransport_product_history` — linii produs din istoric
- `carrier_history` — transportatori memorați automat
- `product_tariff_memory` — mapări TARIC cu status (suggested/confirmed/rejected)

## API Endpoints

### Carrier
- `GET /api/carriers/search?q=ANDI` — caută transportator
- `GET /api/carriers/all` — toți transportatorii, ordonați după frecvență

### TARIC
- `POST /api/tariff/confirm` — confirmă o mapare (status → confirmed)
- `POST /api/tariff/reject` — respinge o mapare (status → rejected)
- `GET /api/tariff/pending` — toate sugestiile neconfirmate

## Module

| Fișier | Rol |
| --- | --- |
| `etransport/db.py` | Inițializare schema DB |
| `etransport/services/carrier_history_service.py` | CRUD transportatori |
| `etransport/services/tariff_memory_service.py` | CRUD mapări TARIC cu status |
| `etransport/services/historical_tariff_matcher.py` | Matcher history-first |
| `etransport/utils_import_etransport_history.py` | Import XML-uri istorice |
| `etransport/utils_import_smartbill_codes.py` | Import catalog SmartBill |
