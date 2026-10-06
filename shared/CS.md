# 🎧 HARTĂ CS (Customer Service) — PRIMUL fișier pe care Claude îl citește pentru ORICE task CS

> **Regula de aur CS:** NU improviza query-uri raw (în AWBprint / Shopify / metrics). Pentru fiecare
> intenție CS există un **SKILL dedicat** — caută intenția aici, folosește skill-ul, dă-i exemplul de mai jos.
> Toate merg pe DB / xConnector / Richpanel, **fără să consume rația API Shopify**.
>
> ⚠️ Greșeala tipică (de evitat): „ce comandă are telefonul 07…?" → NU căuta raw în AWBprint.
> → **`gigi:cs-360 customer --phone 07…`** (normalizează formatul singur).
>
> 🪟 **Mașinile CS + depozitul sunt pe WINDOWS** (consolă cp1252): skill-urile forțează UTF-8 la output, deci
> NU mai crapă pe diacritice. Dacă scrii un script nou care printează ț/ș/ă → pune din prima
> `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`, altfel dă „eroare la caracter".

---
## 1. 🔎 CAUT o comandă / un client
| Am … | Tool | Exemplu |
|---|---|---|
| **telefon** client | `gigi:cs-360 customer` | `cs360.py customer --phone 0700000000` → toate comenzile lui, LTV, refuzuri. **Merge și `40748…` / `+40748…`** (ultimele 9 cifre). |
| **nume** client | `gigi:cs-360 customer` | `cs360.py customer --name "Rebeca Kiss"` |
| **email** client | `gigi:cs-360 customer` | `cs360.py customer --email ana@gmail.com` |
| **nr comandă** (GT123) | `gigi:order-hub comanda` · `gigi:xconnector links` | `order-hub comanda` (`comanda: GT000001`) → fișa din Order Hub: status, etichete, hold-uri, tichet, facturi, ce se poate face acum · `xconnector.py links --order GT000001` → linkuri Shopify/xConnector/tracking |
| **AWB / tracking** | `gigi:xconnector links` | `xconnector.py links --awb 00000000001` |
| „**unde e comanda**" (WISMO) | `gigi:cs-360 wismo` | după order# / telefon / AWB → order + fulfillment + tracking |
| identitate **cross-platform** (Shopify ↔ Richpanel) | `gigi:customer-identity` | leagă email/telefon/FB/IG de comenzi și tichete |

> ⚠️ xConnector API **NU** caută după telefon/nume (doar order# / AWB / SKU / dată). Telefon/nume = DOAR `cs-360`.

## 2. 🧩 PROFIL 360 („spune-mi tot despre comanda/clientul X") — ORCHESTRARE (combini, nu un singur tool)
1. `gigi:order-hub comanda` (`comanda: GT123`) → statusul din Order Hub, etichete, facturi, tichet; linkurile: `gigi:xconnector links --order GT123`
2. `gigi:cs-360 customer --phone <al lui>` → alte comenzi + profil + refuzuri
3. `gigi:cs-tickets` / Richpanel → tichetele clientului
4. (opțional) `gigi:cs-360 conversation --llm` / `gigi:cs-360 conversation` → profil 360 pe o conversație Richpanel

**Exemplu** — CS: „cine e clientul de la GT000001 și ce mai are?"
→ `links --order GT000001` (afli telefonul + statusul) → `cs-customer-360 --phone <telefon>` (istoricul) → `cs-tickets` (tichete).

## 3. 📦 STATUS / livrare / AWB
| Vreau … | Tool | Exemplu |
|---|---|---|
| **status comandă** (sursa de adevăr) | `gigi:order-hub comanda` | fișa din Order Hub (statusurile DOAR de aici, nu din AWBprint) |
| tracking + linkuri | `gigi:xconnector links` | `links --order GT123` |
| tracking multi-curier dintr-un AWB | `gigi:awb-track` | lipești AWB-ul → status DPD/Sameday/Econt/Packeta |
| livrabilitate / refuzuri / COD-risk | `gigi:deliverability-monitor` / `gigi:fulfillment-analytics` | rapoarte pe magazin |

## 4. 🛠️ ACȚIUNI (modific / anulez / refac / bani) — `gigi:order-hub`, pe TOATE magazinele
> 🔴 **Din 6-oct-2026 orice modificare pe o comandă se face prin `gigi:order-hub`** (API-ul Order Hub pentru agenți,
> `/api/cs`). Order Hub ține tokenurile tuturor magazinelor (inclusiv ORC, SK, HU, BUC, MD, DUP, LAB), gărzile, lacătele,
> plafoanele și istoricul. Comenzile de modificare din `gigi:xconnector` (`order-cancel`, `addr-set`, `awb-make`,
> `awb-regen`, `awb-void`, `inv-*`) și `gigi:cs-actions` NU se mai folosesc: n-au gărzile Order Hub.

**Rețeta, pe fiecare acțiune:**
1. `comanda` (`comanda: GT123`) — fișa din Order Hub: status, etichete, hold-uri, tichet, facturi, ce se poate face acum.
2. **Proba** `<acțiune>` — nu scrie nimic. Întoarce planul, blocajele (`imposibil` / `politica`), `peste_necesar` și
   `confirmare` (valabilă 15 minute).
3. Arată omului planul și blocajele, pe scurt, și **cere-i acordul explicit**. Serverul nu mai întreabă: agentul e poarta.
4. **Execuția** `<acțiune>-apply`, cu ACEIAȘI parametri plus `motiv`, `confirmare` (din probă) și `peste` = exact
   `peste_necesar`. Un blocaj `imposibil` nu se trece (pauza de AWB a magazinului, comandă expediată / rambursată /
   arhivată în Shopify, etichetele Frisbo de la MD).

`utilizator` = numele omului de la CS, cum îl spune el: ajunge în istoricul comenzii.

| Vreau să … | Acțiune `gigi:order-hub` | Parametri (exemplu) |
|---|---|---|
| **anulez** o comandă | `anulare` | `comanda: GT123` (`restock`, `rambursare`: da/nu; pe card rambursează și stornează ca butonul din OH) |
| **opresc** o comandă (AWB anulat + hold + tichet) | `oprire` | `comanda: GT123` |
| **scot din hold** | `eliberare` | `comanda: GT123` (doar hold-urile citite în probă, după id) |
| **fac AWB** | `awb-nou` | `comanda: GT123` (`colete: 2` opțional; contul și ruta le alege Order Hub) |
| **refac AWB** (alt nr. de colete, adresă nouă) | `refacere` | `comanda: GRAND123, colete: 3` (+ `awb` = eticheta arătată de probă) |
| **modific adresa** (și refac AWB-ul) | `adresa` | `comanda: EST123, oras: Cluj, cod_postal: 400001, strada: …, reface_awb: da` |
| **scot / adaug produse** | `produse` | `linii: [{"sku": "HA-0002", "cantitate": 0}]` · `adauga: [{"variant_id": "…", "cantitate_totala": 1}]` |
| **notă pe comandă** | `nota` | `text: …` |
| **tichetul CS din Order Hub** (coada CS, nu Richpanel) | `tichet` | `operatie: confirma / inchide / nota / deschide` |
| **cererea de anulare a clientului** | `cerere-anulare` | `mod: executa / anuleaza / pastreaza` |
| **comandă nouă COD** | `comanda-noua` | `magazin: …, produse: [...], adresa: {...}` sau `din_comanda: GT123` |
| **swap** (alte produse, aceeași adresă) | `schimb` | `produse: [{"sku": "…", "cantitate": 1}]` |
| **resend gratuit** | `retrimitere` | `comanda: GT123` |
| **înlocuire** (anulează originalul, apoi comanda nouă) | `inlocuire` | `bani: ramburseaza / pastreaza` |
| **factură / storno** | `factura` / `factura-storno` | storno: `serie`, `numar` (documentul exact) |
| **rambursare** | `rambursare` | `suma: 50` (cel mult 2 zecimale, fără separator de mii) sau `linii` |
| **ramburs încasat** (marchez comanda plătită) | `ramburs-platit` | `comanda: GT123` |
| **retur DPD** / curier pentru retur / anulez returul | `retur` / `retur-curier` / `retur-anulare` | `comanda: GT123` |
| **cerere la curier** (reprogramare, nouă încercare, retur la expeditor) | `cerere-curier` | `actiune: reprogramare` |
| **punct de ridicare** (locker) | `punct-ridicare` | `actiune: seteaza, retea: easybox, punct_id: …` |
| **taguri** / **e-mail comandă** | `taguri` / `email-comanda` | `adauga: [...]` / `email: …` |

Parametrii exacți: `gigi:order-hub actiuni` (sau `skill_info`). Listele de obiecte se dau ca JSON.

> **Coduri de ieșire** (ultima linie: `OH_REZULTAT rezultat=… scris=… iesire=…`):
> - **0** făcut, sau proba fără blocaje;
> - **2** nimic scris (cerere greșită, plan schimbat, confirmare expirată, comanda ocupată): corectezi și faci proba din nou;
> - **3** refuz Order Hub, nimic scris (și plafonul atins): spui omului de ce, nu reîncerci singur;
> - **4** scris PARȚIAL: NU repeta, deschide fișa și spune ce a rămas;
> - **5** necunoscut (cererea a plecat, răspunsul nu, sau „în lucru"): **repetă IDENTIC** (aceeași confirmare) sau
>   `operatie` (`id: N`); NU face probă nouă. O repetare identică e sigură: Order Hub o recunoaște.
>
> **Banii** sunt ireversibili (factura pleacă în e-Factura, rambursarea la client): verifică de două ori suma și documentul
> numit în plan. **Nu face nimeni prin agent:** marcare „livrat", AWB pe un cont ales de mână, mesaje către client,
> comasarea comenzilor, blocklist. `gigi:xconnector links` rămâne pentru linkuri și tracking.

## 5. 🖨️ PRINT etichete în depozit (Windows + Chrome)
> 🔴 **Din 24–25 sep 2026 AWB-urile DPD se fac din ORDER HUB (contul `dpd-ro-arona`), nu din xConnector.** Etichetele astea
> **NU apar în `depozit:print-queue`** — xConnector nu le cunoaște. Se printează DOAR din **Order Hub → Printing**
> (https://orderhub.arona.ro/app/printing), alegând stația (Bartolomeu / Uzina 2 / Parfumuri). Coada din Order Hub le are pe
> TOATE (și pe cele încă făcute în xConnector — Esteban/GT/Nubra/Lab Noir), fără dubluri: ce se descarcă în xConnector e marcat
> printat și în Order Hub în max. 30 de minute. Măsurat pe 27-sep: din 24 de etichete Order Hub căutate în
> xConnector, 0 existau acolo. `depozit:print-queue` rămâne doar ca rezervă pentru etichetele xConnector, până la oprirea
> xConnector (2-oct-2026).

**Rezervă (doar etichete xConnector) = `depozit:print-queue`** (rulează LOCAL pe stație, live din xConnector, per-STAȚIE: fiecare mașină vede DOAR magazinele ei via `--machine depozit|uzina2`). Operatorul vorbește, agentul rulează `pull` (refresh ~15s) + `plan` (instant) → spune NUMĂRUL; la print, `open` descarcă etichetele filtrate + le deschide în Chrome (operatorul apasă Ctrl+P).
**Exemple:**
- câte AWB-uri am de printat: `pull --machine <depozit|uzina2>` apoi `plan` → total + pe magazine
- HA-0002: `pull --machine depozit` apoi `open --sku HA-0002 --machine depozit`
- parfumuri de 3 pe Esteban: `plan --shop esteban --items 3`
> ⚠️ `open` marchează etichetele `downloaded` (ies din coada TUTUROR stațiilor) → DOAR la print real. `pull`/`plan` = zero efecte.
> `gigi:xconnector print-batch` = **DEPRECAT** (fără rutare pe stație) — folosit doar pt compatibilitate; nu-l mai folosi pt print nou.

## 6. 💬 TICHETE (Richpanel)
| Vreau … | Tool |
|---|---|
| draft de răspuns (în vocea CS, cu datele clientului) | `gigi:cs-draft-reply` — **folosește MACRO-urile CS din ClickUp** (formatul/expresiile oficiale): citește doc-ul `2kyqg8j1-3895` (v3 docs API + `CLICKUP_API_TOKEN`), alege macro-ul pe categorie+limbă, completează `{client}/{comanda}/{awb}/{magazin}/{link_retur}` cu date reale → `create_draft` (NICIODATĂ trimitere). Vezi [[cs-macros-clickup]]. |
| triaj automat (tag + categorie + prioritate) | `gigi:richpanel-auto-triage` |
| sentiment per tichet | `gigi:cs-sentiment` |
| dashboard SLA (unde rămânem în urmă) | `gigi:cs-sla-dashboard` |
| curățenie backlog (auto-close zgomot / snooze WISMO) | `gigi:richpanel-backlog-janitor` |
| audit calitate răspunsuri | `gigi:cs-quality-audit` |
| operează inboxul (triaj/răspuns/asignare) | `gigi:cs-tickets` |

> 📌 **REPLY = CLOSE.** Când CS răspunde la un tichet, îl **ÎNCHIDE** — inclusiv escaladările (ANPC/OPC). Richpanel îl **REDESCHIDE automat** dacă clientul scrie iar. Deci nu lăsa tichetul deschis după ce ai răspuns. (Atribuirea magazinului rămâne din `to.id`, vezi mai jos.)

## 7. 🛡️ PREVENȚIE / PROACTIV (oprim pierderi înainte să se întâmple)
| Risc | Tool | Exemplu |
|---|---|---|
| COD riscant nelivrat (serial-refuser etc.) | `gigi:cod-confirmation` | coada de confirmat înainte de expediere |
| adresă greșită la colet neplecat | `gigi:cs-address-guard` | telefonează ÎNAINTE de pickup |
| **comenzi DUBLATE** (același client, 2x) | `gigi:cs-duplicate-orders` | anulează dublura înainte să plece |
| **ghost shipment** (AWB făcut, curier n-a scanat) | `gigi:cs-ghost-shipments` | + `xconnector not-downloaded --min-age-hours 48` |
| întârzieri în tranzit | `gigi:cs-proactive-delays` | contactează clientul proactiv |
| **refund promis dar neexecutat** | `gigi:cs-refund-watchdog` | risc ANPC/chargeback |
| recuperare comenzi refuzate | `gigi:cs-refused-recovery` | re-câștigă COD-uri eșuate |
| întrebări de stoc presale | `gigi:cs-stock-answer` | „e pe stoc? când revine?" |

## 8. 📚 CUM RĂSPUND (procedura, nu doar unde caut)
- Învață procedurile din tichete reale: **`gigi:cs-procedures`**.
- Politica de retur ARONA: **NU** încuraja returul (mai ales igienă / parfumuri desigilate). Memorie: `cs-procedures-learn-not-assume`.
- De-AI pre-publicare (RO): `gigi:ai-scrub` (scoate watermark-uri + fraze AI).

---
### 🏬 Ce MAGAZIN e un tichet Richpanel (NU te lua după brandul din Richpanel — e STALE)
Magazinul unui tichet = **PAGINA pe care a venit** = câmpul **`to.id`** (page id FB/IG) → mapează cu `PAGE_STORE` din `gigi:richpanel-auto-triage` (ex `775068272350568` = MagDeal). **NICIODATĂ** după:
- **brandul / `last_message_sender_id` din Richpanel** — e neactualizat (ex pagina MagDeal e încă etichetată „nocturna9540" → un agent a zis greșit „Nocturna");
- numele/handle-ul clientului.
Fallback dacă n-ai `to.id`: prefixul comenzii din mesaj (EST/GT/MAG…) → magazin. Vezi memoria [[fb-page-store-map]].

## ⚠️ Capcane care au stricat lucruri înainte (citește)
1. **Telefon negăsit** = format. Caută după ultimele **9 cifre** (`cs-360` o face). Nu scrie `phone = '07…'` exact.
2. **NU căuta raw în AWBprint** pt client/telefon — sursă greșită. `cs-360` = `metrics.orders`.
3. **„Eroare la caracter românesc" pe Windows** = lipsește `sys.stdout.reconfigure(encoding="utf-8")`. Skill-urile CS îl au.
4. **Rația Shopify**: lookup-urile CS NU lovesc Shopify live (DB/xConnector/Richpanel).
5. **Print**: descărcarea unei etichete o scoate din coada de print — fă-o DOAR la print real (`--apply`).

*Hand-maintained. Adaugi/extinzi o capabilitate CS → trece-o și aici. Detalii non-CS (profitabilitate, DB, scripturi) → `shared/HARTA.md`.*
