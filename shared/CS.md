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
| **nr comandă** (GT123) | `gigi:xconnector links` | `xconnector.py links --order GT45911` → status + linkuri Shopify/xConnector/tracking |
| **AWB / tracking** | `gigi:xconnector links` | `xconnector.py links --awb 81313116658` |
| „**unde e comanda**" (WISMO) | `gigi:cs-360 wismo` | după order# / telefon / AWB → order + fulfillment + tracking |
| identitate **cross-platform** (Shopify ↔ Richpanel) | `gigi:customer-identity` | leagă email/telefon/FB/IG de comenzi și tichete |

> ⚠️ xConnector API **NU** caută după telefon/nume (doar order# / AWB / SKU / dată). Telefon/nume = DOAR `cs-360`.

## 2. 🧩 PROFIL 360 („spune-mi tot despre comanda/clientul X") — ORCHESTRARE (combini, nu un singur tool)
1. `gigi:xconnector links --order GT123` → comanda + status + linkuri
2. `gigi:cs-360 customer --phone <al lui>` → alte comenzi + profil + refuzuri
3. `gigi:cs-tickets` / Richpanel → tichetele clientului
4. (opțional) `gigi:cs-360 conversation --llm` / `gigi:cs-360 conversation` → profil 360 pe o conversație Richpanel

**Exemplu** — CS: „cine e clientul de la GT45911 și ce mai are?"
→ `links --order GT45911` (afli telefonul + statusul) → `cs-customer-360 --phone <telefon>` (istoricul) → `cs-tickets` (tichete).

## 3. 📦 STATUS / livrare / AWB
| Vreau … | Tool | Exemplu |
|---|---|---|
| status comandă + tracking | `gigi:xconnector links` | `links --order GT123` (livrare reală din AWBprint) |
| tracking multi-curier dintr-un AWB | `gigi:awb-track` | lipești AWB-ul → status DPD/Sameday/Econt/Packeta |
| livrabilitate / refuzuri / COD-risk | `gigi:deliverability-monitor` / `gigi:fulfillment-analytics` | rapoarte pe magazin |

## 4. 🛠️ ACȚIUNI (modific / anulez / refac) — `gigi:cs-actions` sau `gigi:xconnector`
| Vreau să … | Comandă (xConnector) | Exemplu |
|---|---|---|
| **anulez** o comandă | `order-cancel` | `xconnector.py order-cancel --order GT123 --agent Raluca --motiv "client s-a răzgândit"` = probă; apoi același rând cu `--apply`. Prin Order Hub: eticheta întâi, apoi comanda; refuză dacă a plecat |
| **modific adresa** (la o valoare dată) | `addr-set` | `xconnector.py addr-set --order EST123 --city "Cluj" --zip 400001 --address1 "…" --apply`. Dacă comanda are deja AWB, eticheta rămâne cu adresa veche → după 1–2 minute, `awb-regen` |
| **schimb conținutul** (COD/Releaseit, line items blocate) | cancel + replace | `order-cancel … --apply` apoi `gigi:cs-actions place` (comandă nouă COD) → AWB-ul îl face Order Hub |
| **fac AWB** | Order Hub | AWB-ul îl face Order Hub (singur, sau „Ship now” din Order Hub). `xconnector.py awb-make` doar când Order Hub cere eticheta făcută manual în xConnector: se refuză pe o comandă care are deja AWB și nu eliberează hold-urile puse de Order Hub |
| **refac AWB** (alt nr. de colete, adresă schimbată) | `awb-regen` | probă: `xconnector.py awb-regen --order GRAND123 --parcels 3`; execuție: rândul „→ execuție” afișat de probă (`… --parcels 3 --awb <eticheta> --apply`) |
| **opresc** o comandă (anulez AWB, rămâne pe hold) | `awb-void` | `xconnector.py awb-void --order GT123 --apply`. NU e pasul întâi din „anulez și fac alt AWB” (acela e `awb-regen`); hold-ul se eliberează din Order Hub |
| **factură** (creez/anulez/storno/regen) | `inv-make / inv-cancel / inv-storno / inv-regen` | `xconnector.py inv-make --order GT123 --apply` |
| comandă nouă COD / swap / resend gratis | `gigi:cs-actions` | rezolvă clientul + plasează/înlocuiește |

> 🔴 **Din 2-oct-2026 acțiunile pe comandă și AWB întreabă ÎNTÂI Order Hub** (`order-cancel`, `awb-void`, `awb-regen`,
> `awb-make`, `addr-set --make-awb`, `gigi:cs-actions cancel`). Order Hub face etichetele pe toate magazinele — direct la
> curier, unde xConnector nu le vede, sau prin xConnector — și ține hold-urile (dublură, blocklist, „de confirmat”). O
> anulare prin xConnector anula comanda în Shopify și lăsa eticheta vie la curier (cinci comenzi, 29-sep). Acum hotărăște
> Order Hub pe orice comandă pe care o cunoaște: anularea, oprirea și refacerea trec prin xConnector doar când Order Hub
> răspunde că n-o cunoaște, iar un AWB nou (`awb-make`) doar pe o comandă fără AWB viu în Order Hub.
> - **Întâi proba** (fără `--apply`): arată planul Order Hub — ce AWB anulează, ce se întâmplă cu stocul și cu banii.
>   `--agent <Nume>` ajunge în istoricul comenzii, `--motiv "…"` în nota ei (la anulare și la oprire).
> - **Bani:** o comandă plătită cu cardul e rambursată și i se stornează factura automat (în plan: „Bani: …”). `--refund`,
>   `--notify` și `--force` nu se aplică. **Stoc:** se repune; `--no-restock` ca să nu.
> - **Refacerea** (`awb-regen --apply`) cere `--parcels` și `--awb` (eticheta din probă): repetată pe aceeași etichetă,
>   Order Hub o refuză, deci nu iese a treia etichetă. Reface pe același curier; alt curier → din Order Hub. După o
>   schimbare de adresă, refă eticheta abia după 1–2 minute (adresa nouă trebuie să fi ajuns peste tot).
> - **Coduri de ieșire cu `--apply`:** 3 = refuz (plecat, etichetă pe care Order Hub n-o cunoaște, Shopify a refuzat,
>   hold pus de Order Hub); 2 = nimic scris prin xConnector: fără un răspuns valid de la Order Hub (cheia `OH_CS_TOKEN`
>   lipsă, eroare, timeout) sau comanda nu s-a putut citi din Shopify — fă acțiunea din https://orderhub.arona.ro/app/orders.
>   Excepție: la `awb-regen`, un 2 cu „xConnector are altă adresă decât Shopify” înseamnă că adresa nouă n-a ajuns încă
>   în xConnector — reîncearcă peste 1–2 minute (din Order Hub doar dacă adresa din xConnector e cea bună).
> - **„Cererea a plecat, dar răspunsul n-a venit”** (tot cod 2): poate să fi fost executată în Order Hub. Rulează proba
>   înainte de a repeta.
> - **`shopify_refuz`:** eticheta e anulată la curier, comanda rămâne deschisă în Shopify, Order Hub deschide tichet CS
>   (azi: Belasil, până se redeschide aplicația Order Hub în admin).
> - **Ramburs plătit apoi cu cardul:** eticheta fără ramburs o reface Order Hub. Emailul „[COD dublu]” doar raportează
>   comenzile lui; nu le mai reface de aici.
> - Facturile (`inv-*`, cu garda OH) și cronul `fulfill` nu trec prin Order Hub.

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
