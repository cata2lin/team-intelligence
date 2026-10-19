---
name: gigi:cs-international
description: Playbook-ul Customer Service pentru piețele INTERNAȚIONALE ARONA (Bulgaria, Ungaria, Cehia, Slovacia, Polonia, Grecia, Moldova; Duppo EU/BG/HU/CZ/SK/MD, Bonhaus CZ/BG, Trendyol BG/GR, eMAG BG/HU) pentru agenții umani — pe piață: limba și adresarea, curierul și cum verifici statusul (DPD 2212/Speedy, Packeta, FAN Courier Moldova), politica de retur și rambursare cu termenele, plata (ramburs/card), cutia de suport și vânzătorul, autoritatea de protecția consumatorului (КЗП, ČOI, SOI, UOKiK, consumator.gov.md), formulările validate în bg/hu/cz/sk/pl, cuvintele de escaladare și ce acțiuni Order Hub merg. Plus unelte doar-citire - tichetele deschise neresponse pe piață din Richpanel, tracking FAN Moldova + multi-curier, politica de retur pe piață. Use pentru „CS internațional", „tichete Bulgaria/Ungaria/Cehia neresponse", „cum răspund unui client bulgar/ceh/maghiar/polonez", „politica de retur Duppo BG/CZ/HU/MD", „unde e coletul FAN Moldova", „AWB FAN…", „ce curier are Bonhaus CZ", „client amenință cu КЗП/ČOI/UOKiK", „macro în bulgară/cehă/poloneză", „avem CS pe internațional".
category: customer-service
version: 1.0.0
---

# CS internațional — playbook pe piață + unelte

Din oct-2026 agenți umani răspund pe BG, HU, CZ, SK, PL, GR și MD. Hărțile CS existente (`shared/CS.md`, `gigi:cs-draft-reply`,
`gigi:cs-tickets`) sunt făcute pentru România. Skill-ul ăsta adună pentru fiecare piață: magazinele, limba, curierul, politica de
retur verificată pe site, autoritatea și formulările deja validate de botul CS. Mai are trei unelte care doar citesc:

| Unealtă | CLI (`cs_intl.py`) | MCP (`arona-cs-inbox`) |
|---|---|---|
| Tichete deschise la care ultimul mesaj e al clientului, pe piață | `tichete [--piata BG] [--toate] [--json]` | `cs_intl_tichete(piata)` |
| Tracking FAN Courier Moldova + DPD/Sameday/Econt/Packeta | `tracking --awb FAN30411342,809…` | `tracking_intl(awb)` |
| Politica de retur, livrare și contact pe piață | `politica [--piata MD]` | `politica_retur(piata)` |

## When to use
- „ce tichete avem neresponse pe Bulgaria / Cehia / Ungaria?”, „câte tichete internaționale așteaptă răspuns?”
- un agent răspunde unui client din BG/HU/CZ/SK/PL/GR/MD și are nevoie de limbă, ton, politică, curier sau de un șablon
- „unde e coletul din Moldova / AWB-ul FAN…”, „coletul DPD spre Cehia/Bulgaria”
- „care e politica de retur pe Duppo CZ / Moldova?”, „clientul vrea banii înapoi, cât avem?”
- clientul amenință cu КЗП / ČOI / SOI / UOKiK / fogyasztóvédelem / consumator.gov.md

## Steps
1. **Află piața și magazinul.** Nr. comenzii arată magazinul: `DUP`/`DUPBG` = Duppo EU (BG+CZ+HU+SK), `BONBG` = Bonhaus BG,
   `CZ` = Bonhaus CZ, `PL` = Bonhaus PL, `SK` = Bonhaus SK, `HU` = Bonhaus HU, `MD` = Duppo Moldova. Pe Duppo EU, piața o dă
   limba clientului. Fără nr. de comandă, folosește formularea `ASK_IDENT` din `references/piete.md`.
2. **Citește statusul DOAR din Order Hub**: `oh_comanda` (MCP `arona-orderhub`). Pentru curier: `cs_intl.py tracking --awb …`.
   Pentru FAN Moldova contează „Starea actuală” (vezi capcana de refuz din `piete.md`).
3. **Deschide secțiunea pieței** din `references/piete.md`: limbă, adresare, curier, plată, retur, autoritate, capcane, incidente
   active. De exemplu, la Duppo BG lipsesc flacoane: se retrimit, fără dată promisă și fără bani înapoi.
4. **Scrie răspunsul** în limba clientului, pornind de la formulările validate (`piete.md`) sau de la macro-urile CZ/PL/BG/EN
   (`references/macros-clickup.md`; citește avertismentele din antet). Pune semnătura pieței doar pe e-mail.
5. **Acțiuni pe comandă** (anulare, adresă, AWB, produse, retrimitere, rambursare): prin `gigi:order-hub`, după rețeta
   `comanda` → `oh_proba` → OK-ul omului → `oh_executa`. `retur`, `retur-curier`, `cerere-curier` și `punct-ridicare` sunt
   construite pentru RO: pe internațional fă întâi proba și citește blocajele (neverificat).
6. **Escaladare** (autoritate, avocat, instanță, poliție, chargeback, furie): răspunde un om, scurt, fără emoji și fără bani
   nepromiși, iar ownerul e anunțat. Cuvintele-cheie pe limbă sunt în `piete.md`.

## Notes
- **Nu scrie nimic în Richpanel din skill-ul ăsta.** Uneltele doar citesc. Pentru draft există `gigi:cs-draft-reply`, cu revizuire
  umană; pe piețele străine gărzile botului au fost la început doar în română (20 din 20 de amenințări bulgare ratate), așa că
  verifică fiecare draft.
- `cs_intl_tichete` pune piața după magazin (`core/scripts/richpanel_schema.resolve_store`: pagina FB din prefixul id-ului,
  cutia de e-mail, prefixul comenzii, domeniul din text). Din 5-oct-2026, Richpanel nu mai trimite `to` (pagina/cutia), iar
  `brand_id` e contul (`nocturna9540`), nu magazinul. Unde magazinul tot nu se află, piața e ghicită din limbă (coloana `cum` =
  „din limbă”). Pe 19-oct, din 971 de tichete OPEN, 448 erau internaționale cu ultimul mesaj de la client: BG 318, CZ 106,
  HU 23, PL 1. MD a ieșit 0, pentru că pagina FB MD nu e în Richpanel și românește nu se distinge de RO.
- **Botul CS LIVE** (`gigi:cs-live-piete`) are încă HU/SK/PL în lista de trimitere automată (`CS_DRAFT_STORES` din
  `cs_live_bg.sh`). Din 6-oct n-a mai trimis nimic, pentru că citește încă `to`, care lipsește. Dacă cineva îl repară, ar
  răspunde automat în paralel cu agenții umani. Decizia de a scoate piețele din listă e a ownerului.
- Cutii partajate: contact@, bulgaria@, hungary@ și czechia@duppo.eu sunt o singură cutie. contact@duppo.hu nu primește mail.
  contact@bonhaus.sk ajunge în cutia bonhaus.bg, iar contact@bonhaus.hu și contact@nocturna.pl ajung în cutia trynocturna.eu.
- Politicile din `references/politici.json` au fost citite de pe `/policies/refund-policy` și `/policies/shipping-policy` ale
  fiecărui magazin, pe 19-oct-2026. Le reverifici când se schimbă site-ul. Memoria internă spunea că MD „nu are retur”, dar site-ul
  spune 14 zile pentru parfumuri sigilate. Site-ul câștigă.
- Înrudite: [[gigi:order-hub]], [[gigi:fan-md-tracking]] (audit OH vs FAN), [[gigi:awb-track]], [[gigi:cs-live-piete]],
  [[gigi:cs-draft-reply]], [[gigi:intl-address-nomenclator]], [[gigi:cs-360]] (căutare client după telefon; pe internațional
  numerele sunt +359/+420/+36/+48/+373).
