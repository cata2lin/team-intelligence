---
name: internal-app-operator
description: Playbook repetabil pentru „vreau să pot face ORICE dintr-un app intern al echipei din Claude" — transformă toate mutațiile unui app intern (dashboard FastAPI/Express pe HTTP, sau Next.js cu server actions) într-un skill CLI operator, cu introspecție + dispatcher generic + dry-run implicit + gardă pe operațiunile high-risk, apoi îl publică pe GitHub + Second Brain. Acoperă reconul (găsește app-ul, enumeră mutațiile POST/PUT/DELETE sau server-actions), cele 4 tipare de autentificare (JWT emis din secretul echipei, cookie de sesiune, HMAC semnat, shim-uri de server-action cu identitate reală pt audit), forma canonică de CLI (areas/endpoints/sig/call), regulile de siguranță și fluxul de publicare. Implementări de referință: gigi:scripts-app (FastAPI), gigi:scentum (Next server actions), gigi:tom (HMAC+DB), gigi:metrics-app / gigi:bi-grandia (cookie). Use pentru „fă un skill care operează app-ul intern X", „vreau să fac orice din app-ul Y din Claude", „transformă mutațiile app-ului în CLI", „operator CLI pentru un dashboard intern", „reverse-engineer app intern → skill".
argument-hint: "descrie app-ul intern (repo local + URL) → recon → CLI operator → publish"
---

# internal-app-operator
> Author: **Gigi**. Rețeta prin care un app intern devine operabil integral din Claude.

Am aplicat-o deja la **scentum.arona.ro** (Next server actions) și **scripts.arona.ro** (FastAPI).
Rezultatul de fiecare dată: un skill CLI care expune **toate** mutațiile app-ului, cu dry-run implicit.
Familia curentă: `gigi:metrics-app`, `gigi:bi-grandia`, `gigi:tom`, `gigi:scentum`, `gigi:scripts-app`.

## Pasul 0 — Recon (nu construi înainte să înțelegi)
1. **Găsește app-ul:** repo local (adesea în `~/Downloads/…`), URL-ul live, și **cum se autentifică**
   (citește middleware-ul / `app.py` / `middleware.ts` / `auth`).
2. **Stabilește ARHITECTURA mutațiilor** — asta decide tot restul:
   | Semn în cod | Arhitectură | Cum apelezi |
   |---|---|---|
   | `@router.post/put/delete` (FastAPI), `app.post` (Express) | **HTTP API** | request HTTP cu token/cookie |
   | `"use server"` + acțiuni în `src/app/actions/*` chemate din UI | **Server actions** | **importă & rulează în repo** (nu-s pe HTTP) |
   | `/api/v1` + semnătură | **HMAC** | semnezi requestul |
3. **Enumeră TOATE mutațiile** (nu doar câteva) — asta e „orice din Claude":
   ```bash
   # HTTP (FastAPI): metodă + path + funcție, per router
   grep -rhoE '@router\.(post|put|delete|patch)\("[^"]*"' api/*.py | sort | uniq -c
   # Next server actions: funcțiile exportate din fiecare fișier de acțiuni
   grep -rhoE 'export (async )?function \w+' src/app/actions/*.ts
   ```
   Numără-le. Dacă CLI-ul tău acoperă 10% din suprafață, **nu** e „orice din Claude" — vezi mai jos
   de ce un dispatcher pe SERVICII ratează ~40% (multe module scriu direct, fără service layer).

## Pasul 1 — Autentificare (cel mai subtil pas; alege tiparul după app)
> **Secretele vin DOAR din KB** (`kb.py secret-get`), env-first, niciodată printate/comise.

- **JWT + secret partajat** (ex. `gigi:scripts-app`): dacă middleware-ul verifică DOAR semnătura
  (`jwt.decode(token, SECRET_KEY)`) și rolul e în payload → **emiți local un token** semnat cu secretul
  echipei din KB. Fără parolă stocată. Verifică pe un endpoint admin-only că e acceptat.
- **Cookie de sesiune** (ex. `gigi:metrics-app`, `gigi:bi-grandia`): `login` cu creds din KB →
  cache cookie în `~/.config/<app>/cookie` → refolosești.
- **HMAC semnat** (ex. `gigi:tom`, `gigi:awb-tom-po`): semnezi fiecare request cu key_id/secret din KB.
- **Server actions (Next)** (ex. `gigi:scentum`): acțiunile nu-s pe HTTP → **rulezi în repo** cu
  `tsx --tsconfig tsconfig.cli.json`, mapând pe **shim-uri** (DOAR pt CLI, nu în `tsconfig.json` —
  strici build-ul Next): `next/cache`→no-op, `next/navigation`→throw, `server-only`/`client-only`→gol,
  **`@/lib/auth*`→shim care întoarce un user REAL din tabela `users`** (env `<APP>_USER`) ca audit-ul
  (`session.user.id`) să rămână corect. NU ocoli auth-ul — dă-i o identitate reală.
- **Rol de DB cu drepturi minime** când scrii direct în DB: rol dedicat CRUD fără superuser/DDL
  (ex. `DATABASE_URL_SCENTUM_RW`), nu DSN-ul de superuser. MCP-urile `postgres-*` sunt read-only.

## Pasul 2 — CLI-ul (forma canonică, dry-run implicit)
Aceeași formă la toate (vezi `templates/operator_cli.py` = punct de plecare pt HTTP):
```
areas                         # ariile funcționale + nr. endpointuri (✏️ mutație / 📖 citire / 🔴 high-risk)
endpoints [area] [--mutations]
sig  <ținta>                  # ce cere (path-params + câmpurile de body, din manifest/sursă)
call <ținta> [--json '{...}'] [--query k=v] [--apply] [--confirm]
```
- **Citirile rulează direct. Mutațiile = DRY-RUN implicit** → scriu doar cu `--apply` (printează ce
  AR trimite fără el). **High-risk** (DELETE, clear, cancel, send-to-*, push-to-stores, download,
  execute…) cer **și `--confirm`** — clasifică-le în manifest.
- **Introspecția e obligatorie** — echipa trebuie să vadă ce există și ce cere fiecare, fără repo.
  Bagă manifestul (endpoints + câmpuri de body + risc) **în skill**, generat din sursă:
  `templates/gen_manifest_fastapi.py` (AST). ⚠️ Nu te baza pe `/openapi.json` — la noi dă **500**.
- **Dispatcher pe ACȚIUNI/rute, nu pe servicii** (Next): multe module scriu direct cu Prisma → un
  dispatcher pe `src/lib/services/*` ratează suprafața. Mergi pe `src/app/actions/*`.

## Pasul 3 — Verifică pe VIU (nu doar compilează)
Dovedește: (1) o citire reală, (2) un dry-run corect, (3) garda high-risk blochează fără `--confirm`,
(4) **o scriere reală reversibilă** (toggle + toggle înapoi, sau un no-op citit→scris identic).
⚠️ La no-op, trimite structura EXACTĂ pe care o cere endpointul — capcană dovedită: pe un POST de
settings am trimis tot răspunsul GET în loc de dict-ul interior → `INSERT OR REPLACE` a adăugat chei
gunoi (reparat prin SSH pe DB-ul live). De-aia `sig` există: rulează-l ÎNAINTE de `call`.

## Pasul 4 — Publică (echipa să-l aibă)
1. **GitHub** (team-intelligence): `gigi:publish-skill` → PR + merge. Livrează CLI-ul rulabil.
2. **Second Brain**: publisher-ul SB (`second-brain-skill`) cu frontmatter `name: <plugin>:<slug>` +
   `category: dev-tools` + `version`. Oglindește `gigi:tom` — operator de app intern publicat ca
   **knowledge skill** (SKILL.md + scripts), **fără manifest exec** (dispatcher-ul generic cu
   path/JSON arbitrar nu intră în modelul de param-uri whitelisted al SB; se rulează din CLI-ul livrat
   prin GitHub).
3. **KB + memorie**: `kb.py skill-register` + log; scrie o memorie cu tiparul de auth + capcanele.

## Reguli de aur (nenegociabile)
- Secretele DOAR din KB, env-first, niciodată vizibile. DB read-only by default; scrieri cu rol minim.
- **Dry-run implicit pe orice mutație**; high-risk cere confirmare în plus. Fără dry-run pe server
  (n-avem `validate_only`) → „dry-run" = CLI-ul nu trimite; `--apply` execută pe PRODUCȚIE.
- Raportează cinstit: dacă strici ceva la test, repară-l și spune (vezi Pasul 3).

## Referințe (copiază de la cea mai apropiată)
- **HTTP/FastAPI + JWT** → `gigi:scripts-app` (146 mutații, token emis din `JWT_SECRET_KEY`).
- **Next server actions** → `gigi:scentum` (shim-uri + `SCENTUM_USER` + rol RW).
- **HMAC + DB reads** → `gigi:tom`. **Cookie sesiune** → `gigi:metrics-app`, `gigi:bi-grandia`.
- Familia + capcanele: [[contact546-app-cli-skills]], [[scentum-erp-cli]], [[scripts-app-cli]].
