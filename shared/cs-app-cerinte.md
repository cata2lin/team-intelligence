# Aplicația CS proprie — cerințe (se completează pe măsură ce apar)

> Sursa: discuție cu ownerul, 31-aug-2026. Fiecare cerință e însoțită de ce ȘTIM deja despre ea,
> ca să nu re-descoperim. Faza curentă e DOAR CAPTARE; astea sunt pentru înlocuire.

## Modelul de bază
**Un brand = o pagină Facebook + un magazin Shopify + o adresă de email.**

⚠️ Corecție măsurată: relația NU e unu-la-unu peste tot. 23 de adrese cad pe doar **18 cutii**:
- `contact@trynocturna.eu` ← aliasuri: `bonhaus.hu`, `bonhaus.hr`, `nocturna.pl`
- `contact@casaofertelor.ro` ← alias: `ofertelezilei.ro`
- `facturi@aronagroup.ro` ← alias: `reclamatii@aronagroup.ro` (e cutie de CONTABILITATE, nu CS)

⇒ Atribuirea pe brand se face după **adresa din antet (`Delivered-To`)**, NU după cutie. Altfel
Ofertele Zilei apare ca fiind Casa Ofertelor.
Inaccesibile: `contact@bonhaus.ro`, `contact@bonhaus.sk` (203 tichete).
Invizibile pentru noi: `support@*.customerdesk.io` (5 adrese, 30 tichete) — sunt ale Richpanel,
mailul nu trece prin Gmail-ul nostru.

## Cutiile de email — clasificare CORECTATĂ de owner (31-aug)
**17 cutii fizice** pentru 23 de adrese (4 sunt aliasuri; dovadă dură: bonhaus.hu vs trynocturna.eu =
36/36 mesaje identice, suprapunere 100%).

| Cutie | Rol | Tratare |
|---|---|---|
| `contact@esteban.ro` … 12 cutii de magazin | **CS** | captare completă |
| **`contact@rossinails.ro`** | **CS pentru brandul ROSSI Nails** — activitate mică ACUM | ✅ captare completă. ⚠️ Traficul actual e ~98% notificări DPD (688/776), dar asta e incidental, NU motiv de excludere — brandul e real, doar în pauză. |
| **`facturi@aronagroup.ro`** (alias `reclamatii@`) | facturier automat, 2.348 trimise/7z | ⚠️ captează **DOAR răspunsurile clienților**, nu emisia de facturi. Altfel = ~70k mesaje de zgomot. |
| **cutia de lucru a unei colege** (nu o public aici) | email de muncă, nu adresă de rol | 92 mesaje total, 51 inbox/7z, **0 trimise**; conținut = notificări Facebook Business (20), Google, Meta Ads. **NU e cutie de CS** — de captat doar dacă apar mesaje de la clienți. |
| `contact@bonhaus.ro` → **alias pe `contact@casaofertelor.ro`** | CS, INACTIV din dec-2025 | ✅ deja captat prin cutia-gazdă |
| `contact@bonhaus.sk` → **alias pe `contact@bonhaus.bg`** (~201 mesaje) | CS, INACTIV din dec-2025 | ✅ deja captat prin cutia-gazdă |
| `support@*.customerdesk.io` (5) | infrastructura Richpanel | ❌ invizibile pentru noi |

⚠️ **Un alias poate da `invalid_grant` la impersonare, exact ca o adresă inexistentă.** Nu deduce din
eroare că adresa nu există — caută unde ATERIZEAZĂ mailul (`deliveredto:` / `to:` în toate cutiile
accesibile). Așa s-au găsit bonhaus.ro și bonhaus.sk, pe care le raportasem greșit ca gol de acoperire
de 203 tichete. Aliasuri care SE impersonează (bonhaus.hu→trynocturna.eu) și aliasuri care NU se
impersonează (bonhaus.ro→casaofertelor) dau erori identice.

**ACOPERIRE: 100% din adresele active.** Singurele inaccesibile rămân cele 5 `support@*.customerdesk.io`
(infrastructura Richpanel, 30 tichete total, 2024-2025).

**Volum real de CS pe email:** ~161 mesaje umane/zi inbound, ~96 răspunsuri/zi. 65% din inbox e automat
(Judge.me 23%, curieri 17%).

## C1 — Conturi de agent
Trebuie să putem **crea conturi de agenți** din aplicație.
Stare actuală în Richpanel: 6 utilizatori (Cristina Sava, Mariana Popescu, Monica Dan,
Irina Oprea, Martina K, Diana Popa).
⚠️ Numele afișat NU e de încredere ca identitate — emailurile nu se potrivesc cu numele
(ex. „Cristina Sava" e un Gmail personal cu alt nume). Identificarea se face după EMAIL —
citește-le cu `uv run rp.py agents`, nu din documentație (repo-ul e public).

## C2 — Asignare de branduri la agenți
Fiecare agent se ocupă de anumite branduri. **Măsurat pe tichete reale (iun–aug 2026):**
| Agent | Branduri | Volum |
|---|---|---|
| Cristina Sava | Esteban, George Talent, Nubra, Grandia | 2.282 / 617 / 474 / 411 |
| Diana Popa | Ofertele Zilei, Reduceri Bune, Magdeal, Casa Ofertelor | 1.106 / 501 / 403 / 217 |
| Martina K | Bonhaus CZ, Bonhaus PL, Belasil | 276 / 233 / 136 |
Specializare clară: parfumuri / deals / internațional. Aplicația trebuie să reproducă rutarea asta,
nu doar s-o permită.

## C3 — Comenzi istorice vizibile
Agentul trebuie să vadă **istoricul de comenzi** al clientului.
✅ Aici stăm mai bine decât Richpanel, nu mai prost — datele există deja:
- AWBprint = sursa autoritativă de livrare (status real, AWB, curier, cost transport)
- `metrics.orders` + `shopifyNumericId` = link direct în adminul Shopify
- `gigi:cs-360` face deja profilul 360 (telefon/email/nume → toate comenzile, LTV, refuzuri)
⚠️ Richpanel are datele astea GREȘITE: tracking gol și status blocat pe „fulfilled" chiar și pe
comenzi livrate de DPD (măsurat, vezi [[richpanel-connector-write-api]]).

## C4 — Macro-uri de răspuns
Agenții răspund din macro-uri, nu de la zero.
Ce știm deja: textele oficiale CS sunt ținute în **ClickUp**, doc `2kyqg8j1-3895` (v3 docs API +
`CLICKUP_API_TOKEN`) — vezi [[cs-macros-clickup]]. `gigi:cs-draft-reply` le folosește deja:
alege macro-ul pe categorie+limbă și completează `{client}/{comanda}/{awb}/{magazin}/{link_retur}`.
De verificat în UI: câte macro-uri sunt configurate ÎN Richpanel, dacă diferă de cele din ClickUp,
și dacă au variabile/condiții pe care ClickUp nu le are.
Ton: scurt, formal („dumneavoastră"), trimite la site, fără răspunsuri lungi care deschid
conversație. Vezi [[arona-voice-and-tone]].

## Inventarul funcționalităților Richpanel (DE FĂCUT)
Trebuie parcurs ecran cu ecran, ca să știm ce replicăm. Ce se poate afla prin API vs doar din UI:

**Prin API (am deja):** utilizatori (6), echipe (1: Belasil), taguri, canale, metrici de raport
(new/closed conversations, backlog, FRT, CSAT), conversații + fir complet + note private.

**DOAR din UI (nu există în API):** macro-uri și variabilele lor · reguli de automatizare/rutare ·
SLA și programul de lucru · vizualizări și filtre salvate · setări per canal · formularul de
contact al widgetului · semnături · integrarea Shopify (ce câmpuri trage) · roluri și permisiuni ·
rapoarte predefinite · setări de escaladare.

⚠️ Ca să pot face inventarul îmi trebuie o sesiune Richpanel LOGATĂ în browserul de automatizare
(Chrome pe :9222 sau :9223). Nu am nevoie de parolă — e suficient ca ownerul să se logheze acolo o
dată. Login-ul automatizat pică oricum pe protecțiile anti-bot (dovedit la Cloudflare, 31-aug).

## Ce mai lipsește pentru înlocuire (necerut încă, dar necesar)
- Trimitere outbound (email + Messenger) — acum avem doar `gmail.readonly`
- Interfață de agent (inbox, atribuire, macro-uri, SLA)
- Istoricul trimis de Richpanel până acum a plecat prin infrastructura LUI ⇒ 55,3% din mesaje
  se pot lua DOAR din Richpanel cât timp abonamentul e activ. Nu opri imediat după cutover.
