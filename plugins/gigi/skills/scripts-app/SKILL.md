---
name: scripts-app
description: Operează TOT dashboard-ul intern Scripturi (https://scripts.arona.ro) din terminal — al 5-lea app intern al echipei, după metrics-app / bi-grandia / tom / scentum. API FastAPI cu ~146 de mutații + 107 citiri pe 24 de arii — Purchase Orders (PO → TOM/WMS), Profitability (run/remap/COGS/transport/marketing overrides/status), Trendyol (split/AWB/mapare/profit/returns/questions), DPD (nomenclator/analiză/reclamații/dimensiuni), etichete perfumuri + push-to-stores, eMAG labels, e-Transport review (ANAF, match OpenAI), Marketing (sync FB/TikTok, reguli de mapare), Perfumes + stoc + OCR, Daily-perf (tokens/rates/brand-map/sync), Product Analytics, Employee store, Customer Service tags, Users (admin). Introspecție + dispatcher generic; dry-run implicit; high-risk cere confirmare. Use pentru "fă un PO în scripts", "rulează profitabilitatea pe luna X", "split Trendyol", "trimite PO în TOM", "generează etichete perfum", "sync marketing FB/TikTok", "scripts.arona.ro", "dashboard-ul intern", "vreau să fac X în scripturi (app-ul web)".
argument-hint: "areas | endpoints <area> [--mutations] | sig <METHOD> <path> | call <METHOD> <path> [--json '{...}'] [--query k=v] [--apply] [--confirm]"
---

# scripts-app
> Author: **Gigi**. Operează `scripts.arona.ro` (dashboard-ul intern Scripturi) din CLI — **orice mutație**.

## Ce e
Al **5-lea app intern** al echipei (după `gigi:metrics-app`, `gigi:bi-grandia`, `gigi:tom`,
`gigi:scentum`). E backend-ul **FastAPI** din `/root/Scripturi` (VPS), servit la
**https://scripts.arona.ro**. Acoperă operațiuni pe care restul skill-urilor NU le ating:
**Purchase Orders** (creare → trimitere în TOM/WMS), **Profitability** (rulează engine-ul, remap
status, COGS/transport/marketing overrides), **Trendyol** (split colete, AWB, mapare, profit,
returns, questions), **DPD** (nomenclator, analiză facturi, reclamații), **etichete** perfumuri +
push în Shopify, **eMAG labels**, **e-Transport** review (ANAF + match OpenAI), **Marketing**
(sync FB/TikTok, reguli de mapare), **Perfumes** (certificate, nomenclator, stoc, OCR),
**Daily-perf**, **Product Analytics**, **Employee store**, **CS tags**, **Users** (admin).

> Total suprafață: **146 mutații ✏️ + 107 citiri 📖** pe **24 de arii** (42 high-risk 🔴).

## Autentificare — token emis din secretul echipei (NU parolă stocată)
API-ul e FastAPI cu JWT: middleware-ul verifică doar **semnătura** tokenului cu `SECRET_KEY`,
iar rolul (admin/user) e citit din payload. CLI-ul **emite** un token admin semnat cu **exact acel
secret** din KB (`JWT_SECRET_KEY`) — la fel cum `scentum` folosește rolul RW. Fără parole pe disc.
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export JWT_SECRET_KEY="$(uv run "$KB" secret-get JWT_SECRET_KEY)"   # nu se printează
export SCRIPTS_USER="emailul.tau@…"        # opțional: apare în audit ca `sub` (default claude-automation)
```
> ⚠️ Tokenul e **admin** → ocolește permisiunile per-user din app. E pentru operatori de încredere
> (același model ca celelalte app-uri interne). Nu-l folosi ca să dai acces cuiva care n-ar trebui.

## Comenzi
```bash
CLI="uv run scripts/scripts_cli.py"
$CLI areas                                   # ariile + nr. de endpointuri (✏️/📖/🔴)
$CLI endpoints purchase_orders               # endpointurile unei arii
$CLI endpoints --mutations                   # doar mutațiile, toate ariile
$CLI sig POST /api/purchase-orders           # ce cere: path-params + câmpurile de body (din manifest)
$CLI call GET  /api/profitability/settings   # CITIRE — se execută direct
$CLI call POST /api/profitability/run --json '{"month":"2026-06"}'            # DRY-RUN (nu trimite)
$CLI call POST /api/profitability/run --json '{"month":"2026-06"}' --apply    # execută
```
- **Citirile** (GET) rulează direct. **Mutațiile sunt DRY-RUN implicit** → trimit doar cu `--apply`.
- **High-risk** 🔴 (DELETE, `clear`, `cancel`, `send-to-tom`, `push-to-stores`, `download`,
  `remap`, `execute`/`split`, `reject`, `reset`) cer **și** `--confirm` pe lângă `--apply`.
- Path-params: pune-le direct în URL (ex. `call POST /api/purchase-orders/PO-123/approve --apply`).
- Query-params: `--query month=2026-06` (repetabil).

## Reguli de aur
1. Rulează întâi `sig` → vezi ce JSON cere. Multe endpointuri **nu validează cu Pydantic** peste tot
   (unele iau `dict`/`Body`), deci un body greșit dă 500, nu un mesaj frumos.
2. Apoi `call` fără `--apply` (dry-run) → vezi exact ce s-ar trimite. Abia apoi `--apply`.
3. La 🔴 gândește-te de două ori: `send-to-tom` creează PO real în WMS, `push-to-stores` scrie în
   Shopify, `download` scoate etichete din coada de print, `clear` șterge datele de profit ale lunii.

## Regenerarea manifestului (când se schimbă app-ul)
`endpoints.json` e generat din sursă (`/openapi.json` e stricat — dă 500). Când cineva cu repo-ul
(`~/Downloads/Scripturi`) modifică rutele, regenerează + republică:
```bash
uv run scripts/gen_manifest.py scripts/endpoints.json   # reparsează api/*.py
```

## Notes / capcane
- **Nu există dry-run pe server** (FastAPI n-are `validate_only`) → „dry-run" = CLI-ul printează ce
  AR trimite și nu trimite. `--apply` execută pe bune, pe producție.
- Familia app-urilor interne: [[contact546-app-cli-skills]] · pattern-ul de mutații: [[scentum-erp-cli]].
- Secret unic: KB `JWT_SECRET_KEY`. Nu-l printa. Baza de date live e pe VPS (`/root/Scripturi/data`).
