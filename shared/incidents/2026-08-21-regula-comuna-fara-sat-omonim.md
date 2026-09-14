# SPEC pentru agentul de OH — regulă nouă de adresă: „comună fără sat cu același nume"

> Scris 21-aug-2026 după incidentul Siriu. **NU e implementată** — asta e specificația.
> Fișierul de reguli: `services/address_cleanup.py` (acolo stau R1…R21). Politici: `services/nomenclator/policy.py`.

## Problema, pe un caz real
4 colete pentru **Siriu, jud. Buzău** au plecat toate în **Constanța**:

| AWB | comandă | ce scria clientul |
|---|---|---|
| 81345425122 | EST239434 | „Cașoca, Nr.87" · oraș `siriu` · jud. Buzău · 127580 |
| 81345492627 | EST239681 | „Com siriu judetul Bz sat coltu pietrii nr .60" · oraș `Siriu` |
| 81347120523 | EST241526 | „Strada Gheorghe Briciu, Nr 26" · oraș `Siriu` |
| 81344769928 | NUBRA12985 | „Sat Coltu Pietrii Nr 113" · oraș `Siriu` |

Județul era CORECT peste tot. Localitatea e problema.

**Cauza, din nomenclatorul DPD** (`POST https://api.dpd.ro/v1/location/site`, `{userName, password,
language:"EN", countryId:642, name:"Siriu"}`) — două rezultate:

| siteId | ce e | județ | cod poștal |
|---|---|---|---|
| **642161611** | **s. SIRIU, com. CRUCEA** | **CONSTANȚA** | 907099 |
| 642149527 | s. GURA SIRIULUI, com. SIRIU | BUZĂU | 127583 |

**În Buzău nu există niciun sat livrabil numit „Siriu"** — acolo Siriu e doar COMUNĂ (SIRUTA 49484,
`niv=2`). Singurul „SIRIU" exact din țară e satul din com. Crucea, Constanța. Curierul potrivește pe
NUME, deci trimite acolo. **Codul poștal nu salvează nimic**: 127580 e codul generic al COMUNEI, nu
al vreunui sat, iar DPD nu-l folosește la dezambiguizare.

Satele reale ale comunei Siriu (BZ), toate în nomenclatorul DPD:

| sat | cod poștal | DPD siteId | SIRUTA |
|---|---|---|---|
| Lunca Jariştei *(reședința comunei)* | 127585 | 642149493 | 49493 |
| Colţu Pietrii | 127582 | 642149509 | 49509 |
| Caşoca | 127581 | 642149518 | 49518 |
| Gura Siriului | 127583 | 642149527 | 49527 |
| Muşceluşa | 127584 | 642149536 | 49536 |

## Nu e un caz izolat — sunt 33 de comune în țară
Interogare pe `romania_siruta`: comune al căror nume **nu** există ca sat în județul lor, **dar**
există ca sat în alt județ (SQL-ul complet mai jos). Rezultat: **33**. Cele cu volum la noi:

| comună | județul comunei | sat omonim în | comenzi | livrate | returnate |
|---|---|---|--:|--:|--:|
| Brebu | Prahova | Buzău, Caraș-Severin, Dâmbovița | 59 | 47 | 7 |
| **Siriu** | **Buzău** | **Constanța** | **52** | 36 | 7 |
| Baleni | Dâmbovița | Bihor, Galați | 39 | 31 | 5 |
| Alexandru cel Bun | Neamț | Iași | 38 | 32 | 5 |
| Crangurile | Dâmbovița | Prahova | 38 | 30 | 7 |
| Cornu | Prahova | Alba, Dolj | 31 | 25 | 3 |
| Brazi | Prahova | Hunedoara | 26 | 20 | 3 |
| Moara | Suceava | Prahova | 22 | 19 | 2 |
| … încă 21 de comune | | | | | |

**Total: 457 de comenzi**, din care 337 livrate și **78 întoarse la expeditor**. Rata de retur pe
aceste adrese: **18,8%** vs **15,3%** media noastră pe România (622.507 comenzi). Diferența e reală
dar modestă — semnalul TARE nu e statistica, ci cele 4 colete confirmate acum în alt județ. O parte
din „livrate" au ajuns pentru că le-a rezolvat sortarea manuală a curierului, nu pentru că adresa
era bună.

## Regula cerută (propun `R22 — comună fără sat omonim`)

**Declanșare** (toate trei):
1. `judet` e completat și valid;
2. `city` normalizat = numele unei **comune** din acel județ (`romania_siruta`, `niv = 2`);
3. în acel județ **nu** există niciun sat (`niv = 3`) cu același nume.

**Acțiune:**
1. Ia satele comunei: `select cod_siruta, denumire, cod_postal from romania_siruta where sirsup = <cod_siruta_comuna>`.
2. Caută numele lor în `address1` + `address2` + `city` (fără diacritice, case-insensitive, tolerând
   prefixele „sat/satul/s.”). **Dacă exact unul se potrivește** → `city` = satul, `zip` = `cod_postal`-ul lui.
   Asta rezolvă 3 din cele 4 cazuri de mai sus („Cașoca", „sat coltu pietrii" ×2).
3. **Dacă nu se potrivește niciunul** → NU ghici, NU trimite așa. Trimite la CS cu mesaj explicit:
   „Comuna X (jud. Y) nu are sat cu acest nume; satele sunt: A, B, C… Cere clientului satul."
   Al 4-lea caz („Strada Gheorghe Briciu") ar cădea aici — corect, pentru că strada **nu e în
   nomenclatorul oficial** (0 rezultate în `romania_addresses`, în toată țara).
4. **Dacă se potrivesc mai multe** → tot la CS.

**Ce să NU faci:**
- Să nu te bazezi pe codul poștal al clientului ca dezambiguizator: pe toate 4 era 127580, codul
  generic al comunei, care nu aparține niciunui sat. Verifică întâi dacă zip-ul e `cod_postal`-ul
  vreunui sat al comunei; dacă nu e, ignoră-l.
- Să nu alegi automat reședința comunei ca fallback — pare tentant, dar pe Siriu ar fi trimis 3 din 4
  colete în satul greșit.
- Să nu potrivești pe stradă. Strada „Principală" apare în 2.467 de localități (vezi
  `homonym_guard` din `policy.py`) — zero semnal.

**Îmbunătățire opțională (a doua opinie, ieftină):** înainte de AWB, întreabă nomenclatorul
CURIERULUI, nu pe al nostru. Dacă `location/site` pentru `city` întoarce potriviri **doar** în alte
județe decât al clientului, adresa e sigur greșită — indiferent ce zice validatorul nostru. Ăsta e
exact testul care a găsit cauza aici.

## Cum verifici că merge — pe EFECT, nu pe funcție
Regula de cutover se aplică și aici: nu e destul ca `R22()` să întoarcă valoarea bună pe un mock.
1. **Rulare în gol pe istoric:** pasează cele 457 de comenzi din tabelul de mai sus prin regulă și
   raportează câte s-ar fi corectat automat, câte ar fi mers la CS, câte ar fi rămas neatinse.
   Compară cu ce s-a întâmplat REAL (`aggregated_status`), inclusiv pe cele 78 returnate.
2. **Nicio adresă bună stricată:** rulează pe toate comenzile RO din ultimele 30 de zile și verifică
   să nu atingă adrese care s-au livrat corect. Așteptarea: atinge **doar** cele ~33 de comune.
3. **După live:** peste o săptămână, numără câte comenzi pe cele 33 de comune au plecat cu `city` =
   numele comunei. Trebuie să fie **zero**.

## SQL-ul care generează lista de 33 (rulează-l periodic, SIRUTA se schimbă)
```sql
with com as (select cod_siruta, denumire_norm, jud from romania_siruta where niv = 2),
     sat as (select denumire_norm, jud from romania_siruta where niv = 3)
select c.denumire_norm, c.jud,
       (select string_agg(distinct s2.jud::text, ',') from sat s2
         where s2.denumire_norm = c.denumire_norm and s2.jud <> c.jud) as sat_in_alte_judete
from com c
where not exists (select 1 from sat s where s.denumire_norm = c.denumire_norm and s.jud = c.jud)
  and exists     (select 1 from sat s where s.denumire_norm = c.denumire_norm and s.jud <> c.jud)
order by 1;
```

## Comenzi de reparat manual acum (nu așteaptă regula)
- Cele 4 din tabelul de sus sunt deja `in_transit` spre Constanța → CS trebuie să sune DPD cu
  destinația corectă (vezi tabelul cu sate/siteId).
- **MAG7330** — „Coltu Pietri", Siriu, 127580, încă **fără AWB**. Corectează-i adresa în
  **Colţu Pietrii, 127582** ÎNAINTE să i se facă eticheta, altfel intră în aceeași capcană.

## Legat
Memoria [[siriu-buzau-vs-constanta]] (datele complete + metoda de diagnostic),
[[adrese-blocate-clasificare-si-reguli]] (clasa „județ greșit"),
[[adresa-diacritice-si-localitate-in-adresa]] (R20/R21), [[oh-learned-nomenclator]]
(strada lipsă din nomenclator ≠ adresă greșită — caută în livrările reale).
