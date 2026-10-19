# Playbook pe piață — CS internațional

> Verificat 19-oct-2026. Politicile de retur/livrare sunt citite de pe paginile publice ale magazinelor
> (`politici.json`, `cs_intl.py politica`). Formulările în limba locală sunt cele VALIDATE ale botului CS
> (`/root/Scripturi/cs_auto_draft.py` pe VPS: `SAFE_BODY`, `SAFE_PUBLIC`, `ASK_IDENT`, `SIGN_OFF`, gărzile).
> „neverificat" = nu am găsit o sursă sigură.

## Reguli comune (toate piețele)

- **Răspunzi în limba în care a scris clientul**, nu în limba magazinului (un ceh pe pagina slovacă primește cehă).
  Moldova scrie românește, dar piața e MD (politici, curier și firmă diferite).
- **Statusul comenzii vine DOAR din Order Hub** (`oh_comanda`). AWBprint are statusurile internaționale înghețate din 27-sep-2026
  (CZ/PL/SK/HU stau pe `waiting_for_courier`). MD: OH ia statusul din pagina FAN Moldova (`tracking_fan_md=fan`).
- **Etichetele internaționale se fac din Order Hub** pe contul DPD `dpd-ro-arona`, serviciul 2212 (DPD Regional CEE).
  Tracking DPD: `https://tracking.dpd.ro/?shipmentNumber=<AWB>` (merge și pentru coletele 2212).
- **Coada CS din Order Hub sare peste magazinele străine** (`cs_queue.enqueue_order`): „trimite la CS" nu le duce nicăieri.
  Lucrul pe internațional pornește din Richpanel (`cs_intl.py tichete`) și din cutiile de e-mail.
- **Retur**: nu încurajăm returul (ramburs = transport dus-întors pierdut). Întâi motivul + o alternativă; dacă clientul insistă,
  îi spui pașii din politica pieței. Parfum/produs de igienă DESIGILAT = nu se primește înapoi (politicos).
- **Interzis, în orice limbă**: status/AWB/dată/preț inventate; „am verificat" fără să fi verificat; afirmații despre naționalitatea
  sau sediul firmei (risc КЗП/UOKiK/ANPC: răspunde doar cu datele vânzătorului din politică); „produse originale" (sunt parfumuri
  INSPIRATE / imitații, piele ecologică); markdown; datele clientului (nr. comandă, AWB, telefon, adresă, nume) pe un comentariu PUBLIC;
  scuze false de tipul „ne pare rău că aveți impresia asta" / „nu v-a corespuns așteptărilor".
- **Escaladare** (autoritate de protecția consumatorului, avocat, instanță, poliție, chargeback, furie, „țepari"): răspuns UMAN,
  scurt, fără emoji, fără ofertă de bani nepromisă, și anunți ownerul. Cuvintele-cheie pe limbă sunt mai jos.
- **Telefon**: pe piețele fără linie locală NU scrie „sunați-ne" și NU promite că te sună cineva.
- **Semnătura** (doar pe e-mail; pe comentarii publice fără salut și fără semnătură):

| Limba | Încheiere | Linia echipei |
|---|---|---|
| bg | С уважение, | екипът на {magazin} |
| cz | S pozdravem, | tým {magazin} |
| sk | S pozdravom, | tím {magazin} |
| hu | Üdvözlettel, | a {magazin} csapata |
| pl | Z pozdrowieniami, | Zespół {magazin} |
| ro (MD) | Cu drag, | Echipa {magazin} |
| en | Kind regards, | The {magazin} Team |

- **Registrul de adresare** (botul regenerează dacă e greșit):
  bg/cz/sk = persoana a 2-a plural cu majusculă (Вие/Ви/Ваш, Vy/Vám/Vás); **pl = Pan/Pani + persoana a 3-a singular**
  („Prosimy Pana o numer zamówienia"; NU „wy/używacie"); **hu = Ön + persoana a 3-a singular** („Kérjük, adja meg…");
  ro/MD = dumneavoastră. Fără calcuri din română (pl: „przybory kuchenne", nu „instrument").

### Formulări validate (botul CS, folosite pe tichete reale)

**Salut:** bg „Здравейте!" · hu „Jó napot kívánok!" · sk „Dobrý deň!" · cz „Dobrý den!" · pl „Dzień dobry!"

**Cer identificarea comenzii (ASK_IDENT):**
- bg: „За да проверим бързо, моля, изпратете ни номера на поръчката или телефонния номер, използван при поръчката."
- cz: „Abychom to mohli rychle ověřit, zašlete nám prosím číslo objednávky nebo telefonní číslo použité u objednávky."
- sk: „Aby sme to mohli rýchlo overiť, pošlite nám prosím číslo objednávky alebo telefónne číslo použité pri objednávke."
- pl: „Abyśmy mogli to szybko sprawdzić, prosimy o numer zamówienia lub numer telefonu podany przy zamówieniu."
- hu: „Hogy gyorsan utána tudjunk nézni, kérjük, adja meg a rendelés számát vagy a rendeléskor megadott telefonszámot."

**Plângere pe comentariu PUBLIC (SAFE_PUBLIC) — mută discuția în privat, fără date:**
- bg: „Съжаляваме за случилото се. Моля, пишете ни на лично съобщение (Messenger), за да проверим точно Вашия случай."
- hu: „Sajnáljuk a történteket. Kérjük, írjon nekünk privát üzenetben (Messenger), hogy pontosan utánanézhessünk az Ön ügyének."
- sk: „Mrzí nás vzniknutá situácia. Napíšte nám, prosím, do súkromnej správy (Messenger), aby sme mohli Váš prípad presne preveriť."
- cz: „Mrzí nás vzniklá situace. Napište nám prosím do soukromé zprávy (Messenger), abychom mohli Váš případ přesně prověřit."
- pl: „Przykro nam z powodu tej sytuacji. Prosimy o wiadomość prywatną (Messenger), abyśmy mogli dokładnie sprawdzić Pana/Pani sprawę."

**Întrebare despre comandă (SAFE_BODY[1]):**
- bg: „За да проверя точно Вашата поръчка, бихте ли ми казали номера на поръчката или телефонен номер, свързан с нея? Веднага се връщам с детайлите."
- hu: „Hogy pontosan utána tudjak nézni a rendelésének, megadná a rendelés számát vagy egy hozzá tartozó telefonszámot? Azonnal jelentkezem a részletekkel."
- sk: „Aby sme mohli presne preveriť Vašu objednávku, napíšte nám prosím číslo objednávky alebo telefónne číslo, ktoré je k nej priradené. Odpovieme Vám s detailmi priamo tu."
- cz: „Abychom mohli přesně prověřit Vaši objednávku, napište nám prosím číslo objednávky nebo telefonní číslo, které k ní patří. Ozveme se Vám s detaily co nejdříve."
- pl: „Aby dokładnie sprawdzić Pana/Pani zamówienie, prosimy o numer zamówienia lub powiązany z nim numer telefonu. Wrócimy z odpowiedzią najszybciej, jak to możliwe."

**Presale (SAFE_BODY[0]):**
- bg: „Благодарим Ви за интереса! Ще се върна с точните детайли възможно най-скоро; междувременно можете да видите актуалната информация и на сайта."
- hu: „Köszönjük az érdeklődést! Hamarosan jelentkezem a pontos részletekkel; addig is a weboldalunkon megtalálja a friss információkat."
- sk: „Ďakujeme za záujem! Napíšte nám, prosím, a odpovieme Vám priamo tu s presnými informáciami; medzitým nájdete aktuálne údaje aj na našej stránke."
- cz: „Děkujeme za zájem! Ozveme se Vám s přesnými informacemi co nejdříve; mezitím najdete aktuální údaje i na našich stránkách."
- pl: „Dziękujemy za zainteresowanie! Wrócimy z dokładnymi informacjami najszybciej, jak to możliwe; w międzyczasie aktualne dane znajdzie Pan/Pani na naszej stronie."

> Cu om pe piață, promisiunea „revin cu detalii" e în regulă — doar s-o și ții. Macro-urile complete (WISMO, retur, anulare,
> defect, presale) pentru CZ/PL/BG/EN sunt în `macros-clickup.md`.

**BG — gramatica scuzei:** după „съжаляваме за / извиняваме се за" numești faptul („забавянето", „липсващите бройки",
„грешката при подготовката на поръчката", „причиненото неудобство"). Niciodată „…, което сте изпитали". Privat =
„пишете ни на лично съобщение" (nu „на лични", nu „в частен съобщение").

### Cuvinte de ESCALADARE pe limbă (din garda botului)

| Limba | Semnale |
|---|---|
| bg | КЗП, КЗК, НАП, „за защита на потребителите", жалба, адвокат, прокуратура, „ще заведа дело", „ще ви съдя", сигнал до…, полиция; furie: измама, крадци, лъжете |
| hu | fogyasztóvédelem, békéltető testület, NFFH/GVH/NAV, ügyvéd, bíróság, beperel, feljelentés, rendőrség, panasz; furie: csalás, átverés, hazugság |
| cz | ČOI („na ČOI"), dTest, obchodní inspekce, advokát, právník, žaloba, stížnost, trestní oznámení, policie; furie: podvod, okradli, lžete |
| sk | SOI („na SOI", soi.sk), obchodná inšpekcia, ochrana spotrebiteľa, advokát, žaloba, sťažnosť, trestné oznámenie, polícia |
| pl | UOKiK, rzecznik konsumentów, inspekcja handlowa, adwokat, prokuratura, pozew, „do sądu", skarga, policja; furie: oszust, złodziej, wstyd |
| ro/MD | ANPC, protecția consumatorului, consumator.gov.md, avocat, instanță, plângere, poliție, chargeback |

Nu sunt escaladare: „reklamace / reklamácia / reklamacja" (reclamație de garanție obișnuită) și „съд" singur (în bg = „vas").
Botul NU are termeni în greacă și nici pentru agenția din Moldova: pe GR/MD citește tu mesajul.

---

## BG — Bulgaria

- **Magazine**: **Duppo EU** (`j7y429-em`, bg.duppo.eu / duppo.bg, EUR, parfumuri 2+1; prefix `DUPBG` până pe 22-sep, apoi `DUP`;
  OH store 25 — din 22-sep același magazin primește și CZ/HU/SK) · **Bonhaus BG** (`ux1x6n-n2`, bonhaus.bg, EUR, `BONBG`, ramburs) ·
  Nocturna BG ÎNCHIS (iun-2026; tichete vechi doar) · **Trendyol BG** (prima comandă 15-oct, OH store 58) · eMAG BG.
- **Limba**: bulgară. Clienții scriu des cu litere latine („poluchih samo edin") — tot bulgară.
- **Contact**: contact@duppo.eu (= bulgaria@duppo.eu, contact@, hungary@, czechia@ sunt ACEEAȘI cutie: verifică magazinul după
  nr. comenzii, nu după adresa pe care a scris) · contact@bonhaus.bg.
- **Curier**: DPD România, livrat în BG prin rețeaua **Speedy** (adresă sau oficiu/locker Speedy). **Econt NU** (oficiile Econt nu
  se pot servi). Regula ownerului: dacă clientul a dat și adresă și oficiu, pleacă la adresă; din 19-oct oficiul se alege direct.
  Comenzi vechi (înainte de 20-iul): Econt `10…` → `awb_track` le știe.
- **Plată**: Duppo card sau ramburs (наложен платеж); Bonhaus ramburs.
- **Retur**: 14 zile de la primire, doar sigilat; 2+1 → și cadoul sigilat sau i se plătește valoarea; cerere pe e-mail cu nr. comenzii.
  Bani în max. 14 zile după verificare, pe aceeași cale (ramburs → IBAN + titular).
- **Livrare**: Duppo 2–3 zile lucrătoare, 3,99 € sub 45 €, gratuit peste · Bonhaus 3–5 zile.
- **Autoritate**: КЗП (Комисия за защита на потребителите).
- **Incident ACTIV — colete incomplete Duppo BG** (`duppo-bg-colete-incomplete-2026-09`): ~145 de comenzi din 29-aug…10-sep au plecat
  cu mai puține flacoane (des lipsea cadoul 2+1). Oferta ownerului (17-sep): **retrimitem flacoanele lipsă, FĂRĂ dată promisă;
  fără bani înapoi, fără reducere, fără voucher, fără alegere între variante**. Pe public: recunoști general, fără să confirmi că e
  client, fără nr./AWB/dată. Lista comenzilor e în `/root/Scripturi/data/cs_incidents.json` (VPS, date reale, nu se copiază).
- **Adrese**: localitatea se scrie cum a mers înainte la curier (chirilic `Хасково`, nu `Xaskovo`); telefon național → +359.
- **Order Hub**: `adresa`, `anulare`, `awb-nou`, `refacere`, `oprire`, `produse`, `retrimitere`, `rambursare`, `nota` merg ca pe RO;
  `retur` / `retur-curier` / `cerere-curier` / `punct-ridicare` (dpd/easybox/fanbox) sunt făcute pentru RO — **neverificat pe BG**:
  fă întâi `oh_proba` și citește blocajele.

## HU — Ungaria

- **Magazine**: comenzile HU intră prin **Duppo EU** (hu.duppo.eu / duppo.hu) din ~20-sep. Duppo HU separat (`cn0wzi-by`, HUF) retras;
  **Bonhaus HU ÎNCHIS 15-oct** (tichete despre comenzi vechi) · eMAG HU.
- **Limba**: maghiară, **Ön + persoana a 3-a singular**.
- **Contact**: contact@duppo.hu e publicat pe site, dar **domeniul nu primește mail**; răspunzi din cutia Duppo EU (hungary@duppo.eu).
  contact@bonhaus.hu ajunge în cutia trynocturna.eu.
- **Curier**: DPD România (2212), max. 3 colete/AWB.
- **Plată**: card sau utánvét (ramburs). Preț fix 3600 Ft/flacon pe piața HU.
- **Retur**: 14 zile, doar sigilat; 2+1 ca la BG; cerere pe e-mail. Bani în max. 14 zile pe aceeași cale.
- **Livrare** (site): 1–2 zile lucrătoare, 1490 Ft sub 10 000 Ft, gratuit peste (termen optimist pentru expediere din RO — neverificat).
- **Autoritate**: fogyasztóvédelmi hatóság (kormányhivatal) / Békéltető Testület — neverificat pe site.
- **Capcană**: valoarea declarată la DPD în HUF a fost asigurată ca RON (3.803 lei extindere de răspundere în aug). Nu umbla la asta din CS.

## CZ — Cehia

- **Magazine**: **Bonhaus CZ** (`vthuzq-7j`, bonhaus.cz, CZK, prefix `CZ`) — cel mai mare magazin străin, DESCHIS ·
  **Duppo EU** (cz.duppo.eu / duppo.cz) — CZ prin Duppo EU din 18-sep.
- **Limba**: cehă (Vy/Vám/Vás). Ceha fără diacritice e detectată des greșit ca română: citește tu.
- **Contact**: contact@bonhaus.cz, telefon +420 724 216 967 (pagina de contact Bonhaus CZ) · contact@duppo.cz (găzduit în afara
  tenantului nostru; czechia@duppo.eu = alias Duppo EU).
- **Curier**: DPD (2212), **un colet pe AWB** (fiecare covor separat). Comenzi vechi: Packeta `Z…` (awb_track le știe).
- **Plată**: card sau dobírka. Duppo: 270 Kč/flacon, transport 89 Kč sub 1000 Kč.
- **Retur**: Duppo 14 zile, doar sigilat, 2+1 ca la BG. **Bonhaus CZ: pagina de retur NU menționează cele 14 zile** (spune doar
  că desfăcutul nu se primește și „analizăm fiecare caz") — aplici totuși dreptul UE de 14 zile; lacună raportată ownerului.
- **Autoritate**: ČOI (Česká obchodní inspekce).
- **Capcană**: DPD CZ/PL respinge eticheta fără e-mail real; OH pune `awb-<comandă>@arona.ro` pe CZ/PL (nu e e-mailul clientului).

## SK — Slovacia

- **Magazine**: Bonhaus SK ÎNCHIS; Duppo SK → prin Duppo EU. Tichetele SK sunt fie despre comenzi vechi Bonhaus, fie Duppo EU.
- **Limba**: slovacă (Vy/Vám/Vás). Model de formulare: varianta sk („odpovieme Vám priamo tu").
- **Contact**: contact@bonhaus.sk inactiv (ajunge în cutia bonhaus.bg); Duppo → contact@duppo.eu.
- **Curier**: DPD 2212, **doar 1 colet pe AWB**.
- **Autoritate**: SOI (Slovenská obchodná inšpekcia).

## PL — Polonia

- **Magazine**: **Bonhaus PL ȘTERS 15-oct** (site 404) — rămân tichete despre comenzi vechi; Nocturna PL: stare neverificată.
- **Limba**: poloneză, **Pan/Pani + persoana a 3-a singular**.
- **Contact**: nu mai există pagini; **NU da numărul 037x de pe vechiul site** (e o linie din România, scoasă intenționat).
- **Curier**: DPD 2212 (cod poștal fără cratimă); istoric Packeta PL (`Z…`) și Sameday (`1ONB…`).
- **Plată**: ramburs (Releasit/EasySell). **Retur**: regula UE 14 zile; desigilat/igienă nu; ramburs → IBAN.
- **Autoritate**: UOKiK / Rzecznik Konsumentów.

## GR — Grecia

- **Singurul canal: Trendyol GR** (profil separat). Nu avem magazin, macro-uri, formulări validate sau gardă în greacă.
  Comunicarea și returul trec prin Trendyol (neverificat). Tot ce e GR = **neverificat**; întreabă ownerul înainte de a răspunde.

## MD — Moldova

- **Magazin**: **Duppo Moldova** (`4fr12y-2s`, duppo.md, MDL, comenzi `MDxxxx`, OH store 14). Vânzător **GOING STELLAR SRL**
  (IDNO 1026023150471, Chișinău) — NU ARONA SRL și nu firma românească cu același nume.
- **Limba**: română (dumneavoastră). Clienți rusofoni posibili: nu avem formulări validate în rusă.
- **Contact**: contact@duppo.md, L–V 9–17 ora Chișinăului. Pagina FB Duppo Moldova NU e conectată în Richpanel.
- **Curier**: **FAN Courier Moldova**, AWB `FAN` + cifre. Coletele pleacă RO→MD consolidat (vamă, proformă `PLMD26-NNN`).
  Status: `cs_intl.py tracking --awb FAN…` / pagina publică `https://app.fancourier.md/fan/Main?tracking=true&awbno=<AWB>`.
  **Decide „Starea actuală", nu ultimul eveniment.** Capcană: după un refuz FAN scrie „Transportul a fost livrat" (scanat înapoi în hub)
  și apoi „returnat" — NU e livrare la client. „Oprit Autorități / Liber de vamă" = vamă, se eliberează în minute.
  „neridicat" (fără scanări) = întrebare pentru FAN, nu colet pierdut.
- **Plată**: ramburs sau card online.
- **Retur**: 14 zile calendaristice de la primire, DOAR parfum sigilat. Clientul anunță pe e-mail (subiect „Retur", nr. comenzii,
  produsele, IBAN dacă a plătit ramburs), așteaptă adresa de la noi, trimite în max. 14 zile de la notificare, **plătește el
  transportul retur**. Rambursăm în max. 14 zile de la notificare (putem aștepta coletul), transportul inițial doar la retur integral,
  2+1 = reducerea împărțită egal pe cele 3 flacoane (ex. 378 lei / 3 = 126 lei pe flacon).
- **Spart / greșit / lipsă**: în 48h cu poze → înlocuim pe cheltuiala noastră (sau bani, dacă preferă). Defect: reclamație rezolvată
  în max. 14 zile.
- **Livrare**: predare în 24h, 1–2 zile lucrătoare, 79 lei sub 756 lei, gratuit peste. Refuz la ramburs → comanda se anulează
  (pe viitor putem cere plata cu cardul); refuz la card = retragere, rambursăm după întoarcerea coletului.
- **Autoritate**: Agenția pentru Protecția Consumatorilor și Supravegherea Pieței (consumator.gov.md).
- **Adrese**: codul poștal din Chișinău depinde de numărul casei; sursa = Poșta Moldovei, nu ce a scris clientul.
- **AWB automat MD e pe pauză** până vine stocul (decizie 19-oct): nu promite expediere.

## Marketplace-uri (Trendyol BG/GR, eMAG BG/HU)

Comenzile Trendyol intră în Order Hub (store 58) cu AWB-ul Trendyol atașat. Mesajele clienților de marketplace vin prin platformă,
nu prin Richpanel (neverificat dacă cineva le citește). Prețul eMAG BG/HU oglindește RO și se schimbă doar din RO.
