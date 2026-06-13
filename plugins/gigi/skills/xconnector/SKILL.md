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
uv run xconnector.py correct [--shop <domain>] [--days 60] [--apply]    # CRON
```
- `summary` — per magazin: total în fereastră, câte FĂRĂ AWB, distribuție status.
- `address-issues` — lista comenzilor nepornite cu adresă `WRONG`/`UNKNOWN` + adresa curentă + sugestia
  validatorului + verdict. `--json` pt automatizări.
- **`correct`** (cron-ul) — pt fiecare comandă fără AWB cu adresă `WRONG`/`UNKNOWN`:
  - tag **„duplicata"** (Shopify) → **skip** (nu corectez, nu trimit la AWB — se anulează separat);
  - **corectabilă** (gate aac: UN candidat cu zip/oraș/județ ≥0.95 + stradă ≥0.90 + `/zip-code` confirmă +
    număr casă păstrat) → `ai-correct-address` (cu `--apply`) → adresa devine VALID → gata de AWB;
  - **grea** (rural fără stradă / fără număr / garbage / ambiguu) → **triaj CS** (cu motiv).
  Fără `--apply` = **dry-run** (arată ce ar face). Corecția face adresa VALID în xConnector; AWB-ul se
  (re)creează separat (bulk în dashboard / al doilea Flow) — volumul corectabil e mic.

## Auth (cheie API xConnector + token Shopify Admin, per magazin)
- xConnector: secret KB **`XCONNECTOR_SHOPS`** (JSON `[{shopDomain,apiKey}]`), altfel `~/.aac/input.json`.
- Shopify (pt tagul „duplicata"): secret KB **`SHOPIFY_ADMIN_TOKENS`** (JSON `[{prefix,shopDomain,adminToken}]`).
- Cheile **nu se printează niciodată**. Cheia xConnector costă ~$30/magazin — momentan doar **George Talent**
  (`ix5bxc-hr.myshopify.com`).

## Siguranță (corecția de adrese)
Corecția urmează porțile skill-ului oficial xConnector **aac** (`/agentic-address-correction`), conservator:
**un singur candidat** (fără competitor) + scoruri pe câmpuri (zip/oraș/județ ≥0.95, stradă ≥0.90) +
`/zip-code` confirmă + **numărul casei păstrat** + nume/telefon/`address2` păstrate. Regula de aur: *un zip
greșit pe etichetă e mai rău decât nicio corecție* → incert = lasă la CS. Plasă suplimentară: flow-ul ARONA
care contactează client+curier dacă o adresă invalidă ajunge la preluare. Cele grele (rural/garbage/ambiguu)
NU se ating — merg la CS.

## Ce NU poate
❌ Creare AWB / dispatch / facturi prin cheia API — alea-s pe dashboard-ul xConnector (cookie+CSRF), cheia API
dă 403. AWB-ul se face prin Shopify Flow (acțiunea xConnector „Create shipping label"). Skill-ul ăsta doar
pregătește adresa (corecție → VALID) ca AWB-ul să reușească.

## Cron (VPS)
`correct --apply` rulează periodic pe VPS (flock + log): corectează automat ce e sigur, sare duplicatele,
scoate triajul CS. Vezi `gigi:xconnector` în KB pt detalii deploy. Pereche cu [gigi:cs-address-guard].
