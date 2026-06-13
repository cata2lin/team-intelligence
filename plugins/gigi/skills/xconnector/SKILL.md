---
name: xconnector
description: Punte READ-ONLY spre xConnector (curierat) pt magazinele ARONA — scoate comenzile NEPORNITE (fără AWB) cu adresă WRONG/UNKNOWN validată de xConnector, cu adresa curentă + sugestia validatorului + verdict auto/manual, ca să confirmi/corectezi adresa ÎNAINTE să se printeze AWB-ul (prevenție refuzuri). Pereche cu gigi:cs-address-guard (detecție din DB) și cu skill-ul xConnector aac (/agentic-address-correction) pt corecția propriu-zisă. Use pt „ce comenzi au adresă proastă la xconnector", „adrese de confirmat înainte de awb", „xconnector address issues", „comenzi nepornite cu adresă greșită george talent". Read-only — nu creează AWB, nu dispecerează, nu scrie nimic.
---

# /xconnector

Citește din **API-ul xConnector** (cheie API per magazin) comenzile cu adresă problemă care **n-au încă AWB**
(= nepornite) — exact cele unde merită confirmat/corectat adresa înainte ca depozitul să plătească eticheta.

## Comenzi
```
uv run xconnector.py summary                                  # per magazin: câte fără AWB, pe ce status
uv run xconnector.py address-issues [--shop <domain>] [--days 60] [--json]
```
- `summary` — per magazin: total comenzi în fereastră, câte FĂRĂ AWB, distribuție status, câte de confirmat.
- `address-issues` — lista comenzilor nepornite cu adresă `WRONG`/`UNKNOWN`: order, status, **adresa curentă**,
  **sugestia validatorului** (candidat + locality + zip) și **verdict** (✅ auto-corectabil dacă există UN
  singur candidat cu toate scorurile ≥0.95; altfel `manual`). `--json` pt dialer/Sheet/altă automatizare.

## Auth (cheie API xConnector, per magazin)
Sursă, în ordine: secret KB **`XCONNECTOR_SHOPS`** (JSON `[{ "shopDomain":"…","apiKey":"…" }]`), altfel
`~/.aac/input.json`. Cheia o generezi în xConnector (Profil → API Keys). **Nu se printează niciodată.**
Cheia costă ~$30/magazin — momentan e activat doar **George Talent** (`ix5bxc-hr.myshopify.com`).

## Ce poate / ce NU poate (important)
- ✅ **Citire** (durabil, prin cheia API): ce comenzi au adresă WRONG/UNKNOWN, care n-au AWB, connectors, documente.
- ✅ **Corecția de adrese** o face skill-ul oficial xConnector **`/agentic-address-correction`** (aac, supervised,
  dry-run→`--apply`, porți de siguranță). Acest skill îți dă SEMNALUL + triajul; corecția fină → aac.
- ❌ **Creare AWB / dispatch / facturi** — NU prin cheia API: trăiesc pe dashboard-ul xConnector (cookie+CSRF),
  cheia API primește 403. Când xConnector le expune în API (sau activează `/mcp`), se adaugă aici.

## Flux ARONA
`comandă nouă → adresă posibil greșită → (xConnector validează) → confirmă/corectează ÎNAINTE de AWB → AWB`.
Folosește-l ca semnal zilnic alături de [gigi:cs-address-guard] (care detectează din DB-ul intern) — xConnector
aduce validarea reală pe baza de adrese a curierului. Read-only; nu atinge comenzile expediate.
