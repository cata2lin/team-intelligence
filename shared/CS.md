# 🎧 HARTĂ CS (Customer Service) — PRIMUL fișier pe care Claude îl citește pentru ORICE task CS

> **Regula de aur CS:** NU improviza query-uri raw (în AWBprint / Shopify / metrics). Pentru fiecare
> intenție CS există un **SKILL dedicat** — caută intenția aici, folosește skill-ul, dă-i exemplul de mai jos.
> Toate merg pe DB / xConnector / Richpanel, **fără să consume rația API Shopify**.
>
> ⚠️ Greșeala tipică (de evitat): „ce comandă are telefonul 07…?" → NU căuta raw în AWBprint.
> → **`gigi:cs-customer-360 --phone 07…`** (normalizează formatul singur).
>
> 🪟 **Mașinile CS + depozitul sunt pe WINDOWS** (consolă cp1252): skill-urile forțează UTF-8 la output, deci
> NU mai crapă pe diacritice. Dacă scrii un script nou care printează ț/ș/ă → pune din prima
> `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`, altfel dă „eroare la caracter".

---
## 1. 🔎 CAUT o comandă / un client
| Am … | Tool | Exemplu |
|---|---|---|
| **telefon** client | `gigi:cs-customer-360` | `cs_customer_360.py --phone 0748620192` → toate comenzile lui, LTV, refuzuri. **Merge și `40748…` / `+40748…`** (ultimele 9 cifre). |
| **nume** client | `gigi:cs-customer-360` | `cs_customer_360.py --name "Rebeca Kiss"` |
| **email** client | `gigi:cs-customer-360` | `cs_customer_360.py --email ana@gmail.com` |
| **nr comandă** (GT123) | `gigi:xconnector links` | `xconnector.py links --order GT45911` → status + linkuri Shopify/xConnector/tracking |
| **AWB / tracking** | `gigi:xconnector links` | `xconnector.py links --awb 81313116658` |
| „**unde e comanda**" (WISMO) | `gigi:cs-order-status` | după order# / telefon / AWB → order + fulfillment + tracking |
| identitate **cross-platform** (Shopify ↔ Richpanel) | `gigi:customer-identity` | leagă email/telefon/FB/IG de comenzi și tichete |

> ⚠️ xConnector API **NU** caută după telefon/nume (doar order# / AWB / SKU / dată). Telefon/nume = DOAR `cs-customer-360`.

## 2. 🧩 PROFIL 360 („spune-mi tot despre comanda/clientul X") — ORCHESTRARE (combini, nu un singur tool)
1. `gigi:xconnector links --order GT123` → comanda + status + linkuri
2. `gigi:cs-customer-360 --phone <al lui>` → alte comenzi + profil + refuzuri
3. `gigi:cs-tickets` / Richpanel → tichetele clientului
4. (opțional) `gigi:cs-conversation-profile` / `gigi:cs-profile` → profil 360 pe o conversație Richpanel

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
| **anulez** o comandă (sigur, cu gardă „plecată") | `order-cancel` | `xconnector.py order-cancel --order GT44004 --apply` (refuză dacă a plecat; `--force` forțează) |
| **modific adresa** (la o valoare dată) | `addr-set` | `xconnector.py addr-set --order EST123 --city "Cluj" --zip 400001 --address1 "…" --make-awb --apply` |
| **schimb conținutul** (COD/Releaseit, line items blocate) | cancel + replace | `order-cancel … --apply` apoi `gigi:cs-actions place` (comandă nouă COD) → AWB din cron |
| **fac AWB** | `awb-make` | `xconnector.py awb-make --order GT123 --apply` (nr. colete AUTO din metafield) |
| **refac AWB cu N colete** | `awb-regen` | `xconnector.py awb-regen --order GRAND16613 --parcels 3 --apply` |
| **anulez AWB** | `awb-void` | `xconnector.py awb-void --order GT123 --apply` |
| **factură** (creez/anulez/storno/regen) | `inv-make / inv-cancel / inv-storno / inv-regen` | `xconnector.py inv-make --order GT123 --apply` |
| comandă nouă COD / swap / resend gratis | `gigi:cs-actions` | rezolvă clientul + plasează/înlocuiește |

## 5. 🖨️ PRINT etichete în depozit (Windows + Chrome)
`gigi:xconnector print-batch` — descarcă etichetele NEdescărcate, grupate pe **produs** (`--sku`) + **cantitate** (`--total-items`) + **dată** (`--from/--to`), **cross-magazin** (`--shop a,b,c`), → batch PDF + log → deschide în Chrome (Ctrl+P).
**Exemple:**
- HA-0002 de pe toate magazinele deals, la un loc: `print-batch --sku HA-0002 --shop covoareauto-ro,bonhaus,audusp-rf,ofertelezilei --apply`
- doar 10-14 iunie, max 100: `print-batch --from 2026-06-10 --to 2026-06-14 --limit 100 --apply`
- test sigur (etichete deja descărcate, zero impact pe coadă): `print-batch --test --limit 5`
> ⚠️ `--apply` marchează etichetele `downloaded` → ies din coada de print. Dry-run by default.

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
1. **Telefon negăsit** = format. Caută după ultimele **9 cifre** (`cs-customer-360` o face). Nu scrie `phone = '07…'` exact.
2. **NU căuta raw în AWBprint** pt client/telefon — sursă greșită. `cs-customer-360` = `metrics.orders`.
3. **„Eroare la caracter românesc" pe Windows** = lipsește `sys.stdout.reconfigure(encoding="utf-8")`. Skill-urile CS îl au.
4. **Rația Shopify**: lookup-urile CS NU lovesc Shopify live (DB/xConnector/Richpanel).
5. **Print**: descărcarea unei etichete o scoate din coada de print — fă-o DOAR la print real (`--apply`).

*Hand-maintained. Adaugi/extinzi o capabilitate CS → trece-o și aici. Detalii non-CS (profitabilitate, DB, scripturi) → `shared/HARTA.md`.*
