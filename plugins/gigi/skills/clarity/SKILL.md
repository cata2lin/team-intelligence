---
name: clarity
description: "Implement & manage Microsoft Clarity (free heatmaps + session recordings) across the team's Shopify stores — create one Clarity project per store, inject the tracking snippet into each theme, invite team members, and verify the tag is live. Use for 'install Clarity', 'add Clarity / heatmaps / session recordings on all sites', 'pune Clarity pe magazine', 'da acces cuiva la Clarity', 'invite X to Clarity', 'remove Clarity'."
argument-hint: "[deploy|remove|invite <email> [admin]|verify]"
---

# clarity — Microsoft Clarity pe magazinele ARONA

Microsoft Clarity = heatmaps + session recordings + insights, **gratis, trafic nelimitat, proiecte nelimitate**.
Cont curent: **Google `gheorghe.beschea@overheat.agency`** (clarity.microsoft.com). **Un proiect per magazin** (industrie Retail).
Project ID-urile (per magazin) = `scripts/clarity_ids.json` + readable `~/Downloads/clarity_project_ids.txt`. Vezi memoria [[microsoft-clarity-all-stores]].

> Project ID-ul NU e secret (apare în sursa paginii). Snippet-ul e injectat în `layout/theme.liquid` (înainte de `</head>`), per magazin, cu tokenul Shopify Admin al fiecăruia.

## 1. Injectare / scoatere snippet în teme (programatic, toate magazinele)
```
uv run scripts/clarity_deploy.py            # DRY-RUN: ce ar injecta (verifică </head>, deja-Clarity, accesibilitate)
uv run scripts/clarity_deploy.py --apply    # injectează snippet-ul (idempotent: sare dacă există clarity.ms/tag)
uv run scripts/clarity_deploy.py --apply --only EST,GT        # doar anumite magazine
uv run scripts/clarity_deploy.py --apply --remove            # SCOATE blocul Clarity de pe toate (reversibil)
```
- Snippet pus între `<!-- Microsoft Clarity --> … <!-- End Microsoft Clarity -->` → ușor de găsit/scos.
- Tokenuri Shopify din `load_shopify_tokens()` (xconnector / SHOPIFY_ADMIN_TOKENS + stores.csv). NU folosim GTM (niciun magazin n-are tag manager în temă) → injectare directă per temă.
- Maparea prefix→ProjectID e în `scripts/clarity_ids.json`. La magazine noi: creează proiectul (pasul 2), adaugă ID-ul în json, rulează `--apply --only <PREFIX>`.

## 2. Creare proiecte + invitare membri — prin Chrome logat (NU există API public de creare)
clarity.microsoft.com n-are API public, dar UI-ul lovește **`POST https://clarity.microsoft.com/api/v2`** (GraphQL).
Userul se loghează în Chrome (chrome-devtools MCP) → apoi rulezi mutațiile cu `evaluate_script` (fetch în pagină, sesiunea + header-ele lui).

**Header-e necesare** (ia-le dintr-un request real cu `get_network_request`): `csrf-token` (ROTEȘTE per page-load — ia-l proaspăt!), `x-clarity-version`, `content-type: application/json`. Cookie-urile se atașează automat (same-origin).

**Creare proiect** (operationName `addProject`):
```graphql
mutation addProject($hostname:String,$friendlyName:String!,$monthlyPageviews:Int!,$referrer:String!,$isAppProject:Boolean!,$industry:String,$additionalTermsAccepted:Boolean){
  createNewProjectV2(hostname:$hostname,friendlyName:$friendlyName,monthlyPageviews:$monthlyPageviews,referrer:$referrer,isAppProject:$isAppProject,industry:$industry,additionalTermsAccepted:$additionalTermsAccepted){ id hostname friendlyName }
}
# variables: {hostname:"https://esteban.ro", friendlyName:"Maison d'Esteban", monthlyPageviews:10000, referrer:"claritySite", isAppProject:false, industry:"Retail", additionalTermsAccepted:false}
```

**Invitare membru** (operationName `inviteUser`) — Settings → Team → Add team member:
```graphql
mutation inviteUser($projectId:String!,$email:String!,$role:UserRoleType!){
  inviteUserToProject(email:$email,role:$role,projectId:$projectId){ email role inviteCode }
}
# variables: {projectId:"<id>", email:"contact@heyads.ro", role:"Member"}   # role = "Member" (vede+analizează) sau "Admin" (gestionează+șterge)
```
Loop peste toate ID-urile din `clarity_ids.json` → invită pe toate magazinele dintr-un singur `evaluate_script`.
Lista proiectelor + membrii: query `getAccountStatus` (membershipInfo.projects) / `getTeamInfo`.

## 3. Verificare (tag live)
Navighează pe site (chrome-devtools) și `evaluate_script`:
```js
() => ({ hasClarity: typeof window.clarity === "function",
         projectId: ([...document.scripts].map(s=>s.src).find(s=>s.includes("clarity.ms/tag"))||"").match(/tag\/([a-z0-9]+)/)?.[1] })
```
Așteptat: `hasClarity:true` + `projectId` = ID-ul corect al magazinului. Datele apar în dashboard în ~2h de la primul trafic.

## Note
- Roluri: **Admin** = full (setări, invită, șterge); **Member** = vede heatmaps/recordings/setări, fără gestionare echipă/ștergere. Default pt agenții externe (ex HeyAds) = **Member**.
- Reversibil 100%: `--remove` scoate snippet-ul; proiectele se șterg din Settings → Overview → "Delete this project" (sau mutația de delete).
- Confidențialitate: Clarity maschează automat input-uri sensibile; configurabil din Settings → Masking. GDPR: bannerul de cookie-uri al magazinului ar trebui să acopere analytics.
