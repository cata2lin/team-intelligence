---
name: xconnector
description: Punte spre xConnector (curierat) pt magazinele ARONA. CITEȘTE comenzile fără AWB cu adresă WRONG/UNKNOWN (validate de xConnector) + adresa curentă + sugestia validatorului, ȘI le CORECTEAZĂ automat conservator (aac ai-correct-address) pe cele sigure, sărind comenzile cu tag „duplicata". Comanda `correct` e cron-ul fluxului order-created: adresă proastă rămasă unfulfilled → corecție → VALID → gata de AWB; cele grele → triaj CS. Use pt „corectează adresele proaste george talent", „xconnector address issues", „cron corecție adrese awb", „comenzi fără awb cu adresă greșită". Scrie DOAR corecții de adresă (gate dur), niciodată AWB/dispatch.
---

# /xconnector

Punte spre **API-ul xConnector** (cheie API per magazin) pt fluxul de adrese al ARONA. Model actual
(order-created): comanda nouă → Shopify Flow creează AWB; cele cu **tag „duplicata"** sau **adresă proastă**
rămân **unfulfilled**. Skill-ul ăsta trece prin cele unfulfilled fără AWB, **corectează** adresele sigure
(→ devin VALID → gata de AWB) și **triază** restul pt CS.

## Comenzi
```
uv run xconnector.py summary                                  # per magazin: câte fără AWB, pe ce status
uv run xconnector.py address-issues [--shop <domain>] [--days 60] [--json]
uv run xconnector.py recheck [--order GT1,GT2] [--days 30]    # care s-au auto-validat (VALID/PERFECT)
uv run xconnector.py correct [--shop <domain>] [--days 60] [--min-age-hours N] [--exclude d1,d2] [--apply]  # CRON
```
- `summary` — per magazin: total în fereastră, câte FĂRĂ AWB, distribuție status.
- `address-issues` — lista comenzilor nepornite cu adresă `WRONG`/`UNKNOWN` + adresa curentă + sugestia
  validatorului + verdict. `--json` pt automatizări.
- **`recheck`** — re-verifică statusul CURENT al adreselor: care s-au auto-validat (`VALID`/`PERFECT`) vs
  încă `WRONG`/`UNKNOWN`. Cu `--order GT1,GT2` verifici o listă; fără, ia coada curentă. Read-only. Util
  fiindcă validarea xConnector e async/batch — multe comenzi flagate se vindecă singure în câteva ore.
- **`correct`** (cron-ul) — pt fiecare comandă fără AWB cu adresă `WRONG`/`UNKNOWN`:
  - tag **„duplicata"** (Shopify) → **skip** (nu corectez, nu trimit la AWB — se anulează separat);
  - **corectabilă** (gate aac: UN candidat cu zip/oraș/județ ≥0.95 + stradă ≥0.90 + `/zip-code` confirmă +
    număr casă păstrat) → `ai-correct-address` (cu `--apply`) → adresa devine VALID → gata de AWB;
  - **grea** (rural fără stradă / fără număr / garbage / ambiguu) → **triaj CS** (cu motiv).
  Fără `--apply` = **dry-run** (arată ce ar face). `--min-age-hours N` sare comenzile mai noi de N ore
  (le lasă sweep-ului de validare al xConnector să le rezolve — vezi „Validarea e async" mai jos);
  default 0 = oprit. Corecția face adresa VALID în xConnector; AWB-ul se (re)creează separat.

## Auth (cheie API xConnector + token Shopify Admin, per magazin)
- xConnector: secret KB **`XCONNECTOR_SHOPS`** (JSON `[{shopDomain,apiKey}]`), altfel `~/.aac/input.json`.
- Shopify (pt tagul „duplicata"): secret KB **`SHOPIFY_ADMIN_TOKENS`** (JSON `[{prefix,shopDomain,adminToken}]`).
- Cheile **nu se printează niciodată**. Din **2026-06-24** avem chei pe **toate cele 19 magazine active**
  (toate cu `ROLE_AUTOMATION` + 17 permisiuni, expiră 22-sep-2026), nu doar George Talent.

## Magazine EXTERNE — validatorul e RO-only (`--exclude`)
Validatorul de adrese xConnector e **centrat pe România**. Magazinele externe (**Bonhaus CZ `vthuzq-7j`,
PL `f0yrmh-ia`, BG `ux1x6n-n2`**) primesc `WRONG`/`UNKNOWN` în masă (BG ~98% din comenzi) pentru că nu le
înțelege adresele — iar gate-ul nostru de auto-corecție scorează pe zip/oraș/județ RO, deci NU se declanșează
oricum pe ele. → cron-ul rulează cu **`--exclude vthuzq-7j.myshopify.com,f0yrmh-ia.myshopify.com,ux1x6n-n2.myshopify.com`**
ca să nu irosească apeluri și să nu inunde triajul CS. Cheile lor rămân utile pt AWB/facturi/alte operații.

## Siguranță (corecția de adrese)
Corecția urmează porțile skill-ului oficial xConnector **aac** (`/agentic-address-correction`), conservator:
**un singur candidat** (fără competitor) + scoruri pe câmpuri (zip/oraș/județ ≥0.95, stradă ≥0.90) +
`/zip-code` confirmă + **numărul casei păstrat** + nume/telefon/`address2` păstrate. Regula de aur: *un zip
greșit pe etichetă e mai rău decât nicio corecție* → incert = lasă la CS. Plasă suplimentară: flow-ul ARONA
care contactează client+curier dacă o adresă invalidă ajunge la preluare. Cele grele (rural/garbage/ambiguu)
NU se ating — merg la CS.

## Validarea e ASYNC/BATCH — `WRONG`/`UNKNOWN` supra-flaghează (lecție 2026-06-24)
xConnector validează adresele **asincron, în loturi**: o comandă poate sta `WRONG`/`UNKNOWN` ore→o zi, apoi
un **sweep automat** o trece pe `VALID` **fără editare de text** (în `addressValidationHistory`: `actor:"xConnector"`,
`eventType:VALIDATION`). Pe coada GT analizată, **~16%** din „adrese proaste" s-au auto-vindecat singure. Mai mult,
`WRONG` **nu e predictor de eșec la livrare** — pe un eșantion, 6/8 colete cu adresă `WRONG` s-au livrat OK. →
**nu trata un flag proaspăt ca problemă reală**: rulează `recheck` și `correct --min-age-hours N` înainte de a
deranja CS-ul; nu bloca expedierea doar pe baza lui `WRONG`. Coada „grea" reală e mai mică decât numărul brut.

## Scriere prin API — DEBLOCAT (2026-06-24)
Docs: **https://xconnector.app/api-docs.html** (spec `/api-spec.yaml`). Creare AWB / dispatch / facturi **NU mai
sunt dashboard-only** — sunt expuse sync prin `POST /api/actions/*` (`create-shipping-label`, `cancel-shipping-label`,
`dispatch-order`, `estimate-shipping-price`, `create-invoice` + payment/cancel/revert, `locker-notification`),
`POST /api/v1/picking-lists/add-order`, `GET /api/orders/by-tracking-number`. **Gate:** cer rolul `ROLE_AUTOMATION`
pe merchant + permisiuni per-cheie (`API_CREATE_SHIPPING_LABEL` etc.) — fără ele = 403. Cheia GT actuală le are pe
toate (17 permisiuni, inclusiv `API_ADDRESS_VALIDATE`). Skill-ul **încă nu implementează** acțiunile de scriere
(AWB-ul rămâne pe Shopify Flow) — migrarea fluxului AWB/dispatch/factură de pe Flow pe API e următorul pas.

## Cron (VPS)
`correct --apply` rulează periodic pe VPS (flock + log, `0 8-20 * * *`): corectează automat ce e sigur, sare
duplicatele și comenzile proaspete (`--min-age-hours`), scoate triajul CS. Vezi `gigi:xconnector` în KB pt detalii
deploy. Pereche cu [gigi:cs-address-guard].
