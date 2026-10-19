---
name: gigi:order-hub
description: Modificări pe comenzi PRIN ORDER HUB, pe toate magazinele ARONA (inclusiv ORC, SK, HU, BUC, MD, DUP, LAB) — anulează comanda, oprește sau reface AWB-ul, AWB nou, schimbă adresa, scoate/adaugă produse, eliberează din hold, rezolvă tichetul CS, cererea de anulare a clientului, comandă nouă COD, swap, resend gratuit, înlocuire, factură, storno, rambursare, marcare ramburs plătit, retur DPD, cerere la curier (reprogramare), punct de ridicare, taguri, e-mail comandă, plus fișa comenzii din Order Hub. Order Hub ține tokenurile magazinelor, gărzile și auditul; fiecare acțiune are PROBĂ (nu scrie) și EXECUȚIE (cu confirmarea din probă). Use pentru „anulează comanda X", „oprește AWB-ul", „fă AWB", „refă AWB cu 2 colete", „schimbă adresa", „swap pe altă mărime", „retrimite gratis", „emite factura", „storno", „ramburs", „retur", „ce status are comanda în Order Hub".
category: gigi
version: 1.0.1
---

# gigi:order-hub — acțiunile CS prin Order Hub

Order Hub (OH) face scrierea; skill-ul doar trimite cererea. Merge pe toate magazinele organizației, inclusiv cele
cu token emis (ORC, SK, HU, BUC, MD, DUP, LAB). Statusurile se citesc DOAR din OH (`comanda`).

## MCP `arona-orderhub` (preferă-l, mai puțini tokeni)
`mcp_server.py` din folderul ăsta = wrapper subțire peste `order_hub.py` + `scripts/oh_delivery_rate.py`:
`oh_comanda`, `oh_actiuni`, `oh_operatie`, `oh_rata_livrare` (citiri) · `oh_proba` (nu scrie) ·
`oh_executa` (doar după OK-ul explicit al omului pe planul probei). Parametrii acțiunii = `params_json`, ex.
`{"colete": 2}`. Înregistrare: `claude mcp add --scope user arona-orderhub -- uv run <abs>/order-hub/mcp_server.py`.
Rata de livrare: `uv run scripts/oh_delivery_rate.py --store grandia --age 14` (ssh în containerul OH, doar SELECT).

## Rețeta (mereu în ordinea asta)
1. `comanda --comanda <X>` — fișa: status, etichete, hold-uri, tichet, facturi, ce se poate face acum.
2. Proba: `<actiune> --comanda <X> --utilizator <omul> [parametrii]`. Nu scrie nimic. Întoarce `plan`, `blocaje`
   (fiecare cu `clasa`: `imposibil` sau `politica`), `peste_necesar` și `confirmare` (valabilă 15 minute).
3. Arată omului planul și blocajele, pe scurt. Cere-i acordul explicit. Serverul NU mai cere aprobare: tu ești poarta.
4. Execuția: `<actiune>-apply` cu ACEIAȘI parametri, `--motiv`, `--confirmare <din probă>` și, dacă proba a dat
   `peste_necesar`, `--peste` cu exact codurile acelea (separate prin virgulă). Un blocaj `imposibil` nu se trece.

## Acțiuni (fiecare are proba `<nume>` și execuția `<nume>-apply`)
| Familie | Acțiuni |
|---|---|
| AWB | `awb-nou`, `refacere` (cu `--colete N`), `oprire`, `eliberare` (scoate din hold) |
| Comandă | `anulare`, `adresa`, `produse`, `nota`, `tichet`, `cerere-anulare` |
| Comenzi noi | `comanda-noua` (pe `--magazin`), `schimb` (swap), `retrimitere` (resend gratuit), `inlocuire` |
| Bani | `factura`, `factura-storno`, `rambursare`, `ramburs-platit` |
| Altele | `retur`, `retur-curier`, `retur-anulare`, `cerere-curier`, `punct-ridicare`, `taguri`, `email-comanda` |
| Citiri | `actiuni` (parametrii exacți), `comanda` (fișa), `operatie --id N`, `neplecate --sku <SKU/cod de bare> [--magazin <nume exact>] [--limita N] [--json]` (comenzile neplecate pe SKU, pe magazin — filtrul „Neplecate” din OH; MCP: `oh_neplecate_pe_sku`) |

Blocajele pe care nici un om nu le poate trece în Order Hub (ex. pauza de AWB a magazinului, comandă rambursată,
arhivată sau expediată în Shopify) sunt `imposibil` și aici.

## Reguli
- `utilizator` = numele omului de la CS, cum îl spune el. Nu-l alege tu. Ajunge în istoricul comenzii.
- O comandă pe apel. Parametrii exact cum îi arată `actiuni` / `skill_info`; nu ghici tipuri.
- `plan_schimbat` = comanda s-a schimbat între probă și execuție: rulează proba din nou.
- `expirata` = au trecut 15 minute: proba din nou.
- Banii (factură, storno, rambursare, ramburs plătit) sunt ireversibili: factura pleacă în e-Factura, rambursarea
  la client. Verifică de două ori suma și factura numită în plan. `--suma` cu cel mult 2 zecimale, fără separator de
  mii (`1000`, `12.50`). Anularea pe card, cererea clientului pe card și înlocuirea cu rambursare ating și banii:
  `bani_oprit` / `fara_drept_bani` = alternativa din blocaj (ex. `rambursare: nu`).
- Câmpurile JSON (listele de obiecte, `reducere`, `adresa`) au tipurile în descriere: `procent|suma` = una din
  valori, `număr` / `întreg` = număr JSON, nu text.

## Coduri de ieșire (ultima linie: `OH_REZULTAT rezultat=… scris=… iesire=…`)
| Cod | Înseamnă | Ce faci |
|---|---|---|
| 0 | făcut, sau proba fără blocaje | răspunzi omului |
| 2 | nimic scris (cerere greșită, cheie, comandă necunoscută, plan schimbat, expirat, oprit, comanda ocupată: `in_lucru` cu `scris=nimic`) | corectezi / proba din nou |
| 3 | refuz Order Hub, nimic scris (și `plafon`: prea multe acțiuni pe oră / pe comandă) | spui omului de ce (blocajul `imposibil` / plafonul); NU reîncerci singur |
| 4 | scris PARȚIAL | NU repeta; deschide fișa (`comanda`) și spune ce a rămas |
| 5 | necunoscut: cererea a plecat, răspunsul nu, execuția e încă „în lucru" (`scris=necunoscut`) sau Order Hub se oprește (`indisponibil`) | repetă IDENTIC (aceeași confirmare) sau `operatie --id N`; NU face probă nouă |

`operatie --id N` dă aceleași coduri: 0 = făcut, 4 = parțial, 5 = încă în lucru / necunoscut. O probă nouă pe o
comandă cu o operație fără rezultat sigur cere `operatie_neincheiata` în `peste`: verifică întâi (`operatie --id`,
fișa), apoi treci peste doar cu acordul omului.

O repetare identică e sigură: Order Hub o recunoaște după confirmare și întoarce rezultatul deja obținut.

## Ce NU face (nici prin alt skill)
Marcare „livrat", AWB pe un cont ales de mână, mesaje către client, comasarea comenzilor, blocklist. Etichetele
Frisbo de la MD le face depozitul: OH nu le poate anula sau reface.

## Capcane (oct-2026)
- **`financial_status` / `mapped_payment` NU e metoda de plată.** Descriu REZULTATUL: un ramburs devine `paid` (și `mapped_payment='card'`) exact când e livrat și încasat; refuzatul rămâne `pending`/`voided`. Metoda reală = `payment_gateway_names` (`Cash on Delivery (COD)` vs `shopify_payments`/`manual`). Grandia sep-oct 2026: ~87% ramburs, ~13% card. Orice „card vs ramburs" pe `financial_status` iese 0% vs 80% refuz din aritmetică — tautologie. Contează la `ramburs-platit`, `rambursare` și la orice raport din fișă.
- **Belasil (BELA, store 34) e STANDALONE din 10/11-oct-2026** — în organizația proprie (org 7 „Belasil"), nu în ARONA (org 4): curier propriu `dpd-belasil-86ae` (profil 10), facturare pe firma lui (VISO CATERING, serie BLS); pe ARONA doar agentic (`scripts/factura_pe_firma.py --store 34 --org 4`). **14-oct: Belasil e vândut** — lipsa lui din rapoartele ARONA e corectă. Acțiunile de aici „pe toate magazinele organizației" nu-l mai acoperă implicit (⚠️ neverificat ce răspunde OH la o acțiune pe o comandă BELA cu cheia ARONA).
- **Deploy OH (pentru cine modifică codul):** imaginea curentă e **`orderhub:latest`**, NU `orderhub-web:latest`. Tag-ul de rollback = `docker tag orderhub:latest orderhub-web:rollback-X` **FĂRĂ `2>/dev/null`**, apoi verifică `docker images | grep rollback-X` înainte să-l raportezi (15-oct: 8 rollback-uri anunțate nu existau).

## Tehnic
`order_hub.py` (doar stdlib), cheia `OH_CS_SB_TOKEN` din env (Second Brain) sau KB. API: `POST /api/cs/<actiune>`,
`GET /api/cs/actiuni`, `GET /api/cs/comanda`, `GET /api/cs/operatie/{id}` pe https://orderhub.arona.ro. Manifestul se
generează din schema OH cu `gen_manifest.py`, deci numele și tipurile parametrilor vin mereu din Order Hub. Înainte de
publicare: `uv run gen_manifest.py --verifica <schema>.json <manifest>.json` pe fiecare manifest (cod 1 = vechi:
regenerează-l cu `uv run gen_manifest.py <schema>.json > <manifest>.json`).
