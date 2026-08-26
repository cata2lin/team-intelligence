---
name: awb-track
description: Live multi-courier AWB status tracker — paste one or many AWB numbers and get the current status across DPD, Sameday, Econt, Packeta and Dragon Star (DSC), with auto-detection of which courier an AWB belongs to. Returns delivered / in-transit / returned / refused / CANCELED / CLOSED per parcel, and — since the 2026-08-26 fix — reports an unverifiable AWB as EROARE / NEGASIT / NECUNOSCUT instead of silently calling it "in transit". Use for "track this AWB", "status colet", "unde e coletul", "is this delivered", "check these tracking numbers", "bulk AWB status", "ce colete sunt returnate". Read-only.
---

# AWB tracker live (DPD / Sameday / Econt / Packeta / Dragon Star)

Tracking live multi-curier. Lipești unul sau mai multe AWB-uri și primești statusul curent normalizat, cu auto-detecția curierului după pattern-ul AWB-ului.

> ⛔ **REGULA CENTRALĂ (fix 2026-08-26): un AWB fără răspuns VERIFICAT de la curier nu primește niciun status.**
> Înainte, orice text pe care skill-ul nu-l recunoștea — eroare de rate-limit, credențială moartă, AWB
> inexistent, placeholder intern — cădea pe fallback-ul implicit `in_transit`. Rezultatul era un raport
> **indistinctibil de unul real**, cu „✓ Niciun colet cu probleme" și EXIT=0.
>
> **Măsurat pe DPD LIVE, 945 AWB reale din Shopify (Belasil/Esteban/GT), 3 rulări CONCURENTE
> (= CS + cron + al doilea operator pe același cont DPD):**
> | | înainte | după |
> |---|--:|--:|
> | statusuri FABRICATE | **221 / 945 (23,4 %)**, toate „IN TRANZIT" | **0 / 945** |
> | retururi reale ascunse ca „pe drum" | **32** | **0** |
> | `Administrative Closure` | „IN TRANZIT" | `INCHIS ADMIN` (problemă) |
> | credențială DPD moartă | 3× „IN TRANZIT" + „✓ Niciun colet cu probleme", EXIT=0 | 3× `EROARE`, EXIT=1 |
> | lot cu 1 AWB inexistent (mapare pozițională) | 4 / 22 greșite, în AMBELE direcții | 0 / 22 |
> | Econt / Packeta / Dragon Star, AWB inexistent | „In Transit" / „AWB Generat" / „IN TRANZIT" | `NEGASIT` la toate |

## Cum rulezi
```bash
uv run awb_track.py --awb 81298289998,81299189040        # mai multe AWB-uri, auto-detect curier
uv run awb_track.py --awb-file awbs.txt                    # un AWB pe linie
uv run awb_track.py --awb 12345 --courier dpd              # forțează curierul
uv run awb_track.py --awb ... --problems                   # doar coletele cu probleme
uv run awb_track.py --awb ... --json                       # output JSON (stdout curat; avertismentele pe stderr)
uv run awb_track.py --awb-file awbs.txt --allow-partial    # EXIT 0 chiar dacă rămân AWB neverificate
uv run test_awb_track.py                                   # test de regresie, pe fixture, fără rețea
```

## Categorii
`LIVRAT` · `IN TRANZIT` · `RETURNAT` · `REFUZAT` · `ANULAT` (void) · `AWB GENERAT` ·
**`INCHIS ADMIN`** · **`OPRIT DE EXP`** · **`EROARE`** · **`NEGASIT`** · **`NECUNOSCUT`** · `AWB INVALID`

- **`INCHIS ADMIN`** (DPD `Administrative Closure`, cod 129) = stare **TERMINALĂ**: coletul e închis în
  sistemul curierului (de regulă pierdut / casat), nu circulă și nu se va livra. Intră în `--problems`
  fiindcă înseamnă bani neîncasați și cere decizie umană (despăgubire / retrimitere). Nu e nici tranzit,
  nici livrat, nici retur. La fel `OPRIT DE EXP` (cod 121, expeditorul l-a oprit din drum).
- **`EROARE` / `NEGASIT` / `NECUNOSCUT`** = **nu știm** statusul (API căzut, rate-limit epuizat,
  credențială moartă; AWB pe care curierul nu-l cunoaște; text nou, nemapat). Intră toate în `--problems`.
- **`ANULAT`** NU intră în `--problems` — void-ul e flux normal (CS reface eticheta cu alt nr de colete) —
  dar apare cu ⊘ în tabel și numărat separat în rezumat.

## Ce s-a schimbat pentru cine consumă skill-ul (cs-360 WISMO, MCP `arona-fulfillment`, awb-statii)
1. **`--problems` va afișa rânduri care înainte lipseau** (`EROARE`/`NEGASIT`/`NECUNOSCUT`/`INCHIS ADMIN`).
   **Nu e o regresie — ăsta E fixul**: alea erau coletele raportate fals ca fiind în tranzit.
2. **Exit code 1 când au rămas AWB neverificate.** Cronurile/scripturile care presupun exit 0 folosesc
   `--allow-partial` (comportamentul de dinainte). Un cron care se uită doar la exit code nu mai poate
   confunda un raport fabricat cu unul real.
3. **JSON: cheile vechi rămân** (`awb`, `courier`, `status`, `status_ro`, `problem`, `status_raw`), se
   **ADAUGĂ** `ok` (bool — răspuns verificat de la curier), `err`, `source`, `op_code` (codul DPD),
   `attention` (în tranzit dar cu livrare eșuată/întârziere), `unrecognized` (text nou de la curier).
   👉 **Consumatorii trebuie să înceapă să citească `ok`**: cât timp se uită doar la `status`, fixul e
   cosmetic pentru ei. Deocamdată niciunul nu-l citește.
4. **Numărul de retururi raportat SCADE** față de rapoartele vechi: DPD cod 38 `Returned to Office` e o
   operație **INTERMEDIARĂ** (coletul e înapoi în depozit și se REÎNCEARCĂ livrarea), nu un retur — dar
   textul conținea „Return" și cădea pe lista de retururi. **424 apariții** în corpusul de 33.170 de
   operații. Cine compară cu un raport vechi va crede că „s-au pierdut retururi": e corecția unui fals pozitiv.

## Cum funcționează
- Curier auto-detectat din pattern-ul AWB (DPD `8...`, Packeta `Z...`, Sameday `1O...`, Econt `10...`,
  **Dragon Star `9xxxxxxx` 8 cifre**), apoi se interoghează sursa live: DPD `api.dpd.ro/v1/track`,
  Sameday `api.sameday.ro`, Econt `ee.econt.com`, Packeta (XML), **Dragon Star `dragonstarcurier.ro/tracking-awb`**.
- **DPD se clasifică pe `operationCode`** (câmp stabil), nu pe text liber — tabel construit din 33.170 de
  operații reale. Cod nevăzut (colete internaționale / servicii noi) → potrivire pe text; text nepotrivit
  → `NECUNOSCUT`, **niciodată** `IN TRANZIT`.
- **Mapare pe `parcelId`, nu pozițional.** DPD **omite tăcut** coletele pe care nu le cunoaște (fără eroare,
  fără element gol), deci vechiul `zip(batch, parcels)` decala statusurile pe alte AWB-uri de la primul
  colet lipsă încolo. AWB cerut și absent din răspuns → `NEGASIT`.
- **Throttling.** Limita DPD e **5 CERERI/secundă pe CONT**, iar contul e **PARTAJAT cu producția**
  (VPS/xConnector) — nu e o limită de colete: 200 de colete într-o cerere trec. Deci: **lot MARE**
  (`--dpd-batch`, implicit 50), **ritm limitat global** (`--dpd-rps`, implicit 2 cereri/s), DPD strict
  secvențial, **retry cu backoff** pe rate-limit (4 încercări). Loturile mici înrăutățesc situația —
  generează mai multe cereri. După retry-uri epuizate → `EROARE` pe coletele acelei cereri, nu status.
  Când contul a fost efectiv limitat, se tipărește linia `↻ DPD rate-limit: …` (câte cereri limitate,
  câte loturi recuperate, câte pierdute) — fără ea nu poți deosebi o rulare corectă de una norocoasă.
- Credențiale din KB (`COURIER_CREDS_JSON`, fallback `DPD_RO_USERNAME/PASSWORD`). Dragon Star NU cere credentiale.
- Read-only (doar tracking).

## Limitări
- **Rate-limitul e intermitent** (măsurat: 15,1 % din cereri într-o rulare, 0 % în alte cinci — contul e
  partajat). O singură rulare „curată" NU dovedește nimic; verificarea se face pe scenariul CONCURENT.
- Tabelul de `operationCode` e construit din codurile văzute pe 6.525 colete **RO**. Coletele
  internaționale sau serviciile noi pot avea coduri nevăzute → cad pe text, apoi pe `NECUNOSCUT`
  (numărat explicit în rezumat), niciodată pe `IN TRANZIT`.
- **Dragon Star (DSC)** = curier nou, doar pe Grandia (connector xConnector 24257). NU se sincronizează în
  AWBprint/Frisbo (`shipment_status` rămâne null) — de-aceea acest tracker live e singura sursă de status
  real pentru DSC. AWB-urile sunt 8 cifre (`94xxxxxx`).
- Econt/Packeta/Sameday: comportamentul pe **AWB inexistent** e verificat live; **nu** au putut fi testate
  pe colete reale în circulație (din 3.982 de expediții extrase din 14 magazine, 3.976 sunt DPD).
