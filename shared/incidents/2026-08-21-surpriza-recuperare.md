# Recuperarea comenzilor fără parfum surpriză (incident cutover OH, 20–21 aug 2026)

## Ce s-a întâmplat
La cutover-ul de AWB pe toată flota (20-aug) cronul xConnector a fost oprit, iar modulul de surpriză
din OH (`services/cron_parity/surprise.py`) nu adăuga efectiv nimic. Rezultat: comenzi eligibile au
plecat fără cadoul la care clientul avea dreptul. Depozitul a semnalat 39 de comenzi „cu număr ciudat
de parfumuri"; scanarea completă a găsit **52**.

Acoperirea, pe zi (comenzi CLIENT eligibile care au primit surpriza):

| zi | Esteban | George Talent |
|---|---:|---:|
| 10–19 aug | 99–100% | 100% |
| 20 aug | 51% | 47% |
| 21 aug | 100% de la 05:03 | 0% (bug separat, vezi `2026-08-21-surpriza-gt-sku.md`) |

## Regula aplicată
`1 surpriză când (nr. parfumuri) % 3 == 2` → 2, 5, 8, 11, 14. Mereu cel mult una. Se numără
CANTITATEA, nu liniile. Cutia cadou nu e condiție și nu se numără. Excluse: comenzi din DRAFT
(`sourceName = shopify_draft_order`), tag `farasurpriza`, cele care au deja surpriză.
Owner (21-aug): „te iei după nr de parfumuri, și să nu fie draft".

## Ce NU era greșit (verificat, ca să nu se „repare" degeaba)
- **Comenzile cu `cutie + 3 parfumuri + surpriză`** (EST244671, 244453, 244110, 244104, 244037,
  243775, 243648, 243450, 243348, 243259, 243232) sunt CORECTE: la toate discountul 2+1 nu s-a
  aplicat deloc (clientul a plătit 3×45, `discountApplications` gol) fiindcă Script-ul de checkout
  omoară 2+1 când e `cutie-cadou` în coș. Surpriza de acolo e compensația.
- **Drafturile CS** (EST244285, EST243275, EST243211, EST244296, GT57305, GT57297) — tag
  `farasurpriza`, flux separat, CS-ul a ales deliberat conținutul.
- **EST244663** — depozitul a notat „1x33, 1x96", dar comanda are în Shopify 69/71/73 cu 2+1
  aplicat, `ON_HOLD`, fără AWB. Nu e caz de surpriză; de verificat de unde vine nepotrivirea.

## Cum s-a reparat
Toate cele 52 de colete erau ÎNCĂ ÎN DEPOZIT — verificat live la DPD: „Shipment data received",
niciun scan de preluare. Deci s-au putut reface, nu doar compensa.

Per comandă: **void AWB la DPD → `fulfillmentCancel` în Shopify → order edit (+1 `parfum-surpriza`,
0 lei) → AWB nou**. Scriptul: `tools/fix_surprise.py` (rulat în containerul `orderhub-web` cu
`/opt/venv/bin/python`). E idempotent și reluabil — starea se citește din Shopify, nu din OH.

Verificarea EFECTULUI (nu a codului): comanda reparată apare în coada de print a depozitului cu
AWB-ul nou **și cu surpriza pe listă** — `EST244418 → skus ["14","surpriza-EST 115","71"],
total_items 3`. Asta dovedește că depozitul chiar o va pune în colet.

## Capcane întâlnite (toate reale, toate au costat timp)
1. **`account_key = "dpdromania"` nu există ca `CourierAccount`.** Sunt ~45.000 de shipments cu cheia
   asta — e eticheta backfill-ului din istoricul xConnector. `_create_one` cu ea dă
   `404: Courier account 'dpdromania' not found`. Contul LIVE per magazin e `xconnector-<prefix>`.
2. **Void-ul unei etichete cu `account_key = xconnector-*` prin serviciul DPD** dă
   `Wrong username format` — `base.get_credentials` are alias de vendor doar pentru chei care încep
   cu `dpd`. Soluția: trimite NUMELE curierului („DPD Romania") ca account_key, care cade pe aliasul
   `dpd-ro`.
3. **xConnector LIMITEAZĂ rafalele de apeluri și întoarce gol** (scrie chiar în
   `couriers/xconnector.py`: „la o tură de 18 comenzi = 36 apeluri în rafală"). Mesajele rezultate
   sunt înșelătoare: „comanda nu există (încă) în xConnector" și „no open fulfillment orders" —
   ambele înseamnă de fapt „rate limited". Fără pauză între comenzi, **jumătate din colete rămân fără
   etichetă**. Pauză ≥8s între comenzi + reîncercare cu 25s.
4. **`processing_status` e `NOT NULL`** în `orders` — setarea lui pe `None` aruncă `IntegrityError`
   DUPĂ ce mutațiile din Shopify s-au făcut deja. Nu-l atinge.
5. **`/tmp` din containerul `orderhub-web` se golește** (containerul e recreat des) — copiază
   scriptul imediat înainte de fiecare rulare, iar lista de comenzi dă-o prin argv, nu prin fișier.
6. **Cronul auto-AWB al OH repară singur** comenzile rămase unfulfilled fără AWB (le-a făcut
   etichete noi în câteva minute). Bun de știut, dar înseamnă și risc de ETICHETĂ DUBLĂ dacă
   scriptul cere una în paralel → scriptul verifică întâi dacă a apărut deja un AWB nou.

## Rezultat (verificat 21-aug, 12:40)
**52/52 reparate, toate `FULFILLED` complet.** Fiecare comandă are: linia `parfum-surpriza` în
Shopify, **exact un fulfillment activ** (cu surpriza inclusă), un AWB nou valid la DPD, iar toate
cele 38 de etichete vechi anulate. Niciun colet n-a fost preluat de curier între timp.

Verificarea EFECTULUI (nu a codului), pe coada REALĂ de print a depozitului: **toate 52 apar cu
EXACT eticheta curentă și cu surpriza în lista de SKU-uri**. Lista comandă → AWB de printat →
nr. bucăți: `2026-08-21-lista-depozit.txt`.

### Curățarea etichetelor vechi din coada depozitului — pas OBLIGATORIU
Anularea unei etichete la DPD **NU o scoate din coada de print** — xConnector o ține în continuare ca
document `SHIPPING_LABEL` nedescărcat, deci depozitul ar fi printat DOUĂ etichete pe același colet,
una moartă. Erau **32 de comenzi cu două etichete în coadă**. Se rezolvă descărcând eticheta veche
(`GET` pe URL-ul ei `…/download/shipping-label?c=…&t=<AWB_vechi>`) — descărcarea o marchează
`downloaded` și iese din coada TUTUROR stațiilor. Script: `tools/burn_old.py`.
⚠️ Descarcă STRICT etichetele al căror `t=` **nu** e AWB-ul curent, și verifică întâi la curier că
sunt anulate. Din 38 de etichete „vechi", **una era încă ACTIVĂ** (EST244475 / 81349577244 — o
făcuse cronul auto-AWB al OH cât comanda era fără etichetă) — aia trebuia ANULATĂ, nu doar scoasă
din coadă, altfel rămânea un al doilea colet plătit.

### Comenzile rămase `PARTIALLY_FULFILLED` — procedura cu TAG
34 din 52 au ieșit inițial `PARTIALLY_FULFILLED`: eticheta era una singură și corectă, dar linia de
surpriză nu apărea ca onorată. Cauza: **xConnector face fulfillment-ul din COPIA LUI a comenzii**, iar
copia aia încă nu avea linia nouă în momentul cererii de etichetă. (Cele 18 ieșite complet
`FULFILLED` din prima sunt exact alea la care trecuseră câteva minute între order edit și etichetă.)

**Procedura care le închide (owner, 21-aug) — `tools/fix_partial.py`:**
1. anulează AWB-ul;
2. anulează fulfillment-ul din Shopify;
3. **~15 secunde după anulare, pune un TAG pe comandă** (`awb-refacut`). Modificarea comenzii e ce
   forțează xConnector să-și RE-SINCRONIZEZE copia — acum cu linia de surpriză **și** cu fulfillment
   order-ele redeschise. Fără tag, xConnector răspunde „no open fulfillment orders";
4. mai aștepți ~25s să ajungă webhook-ul, apoi ceri eticheta din nou.

Rezultat pe comanda de test (EST244519): `PARTIALLY_FULFILLED` → **`FULFILLED`**, cu surpriza inclusă
în fulfillment, un singur AWB.

⚠️ Sincronizarea de fundal a OH **reimportă același AWB pe rânduri multiple** în `shipments` — la void
deduplică, altfel a doua încercare pe același AWB pică pe „deja anulat" și oprește procedura.

## Încă o capcană de verificare
`gigi:awb-track` interogat cu ~50 de AWB-uri deodată a raportat 18 etichete anulate drept
**„IN TRANZIT"**; reinterogate, toate erau `ANULAT`. DPD limitează rafala, iar trackerul cade pe
statusul implicit „în tranzit" în loc să semnaleze eroarea. **Nu trage concluzii dintr-o singură
rulare în masă — reinteroghează înainte să crezi un status neașteptat.**

## Legat
`2026-08-21-surpriza-gt-sku.md` (bug-ul GT, încă deschis), memoriile `oh-surprise-left-in-shadow`,
`surprise-perfume-flow`, `oh-cutover-incomplet-cron`, `awb-fast-mcp-regen`.
