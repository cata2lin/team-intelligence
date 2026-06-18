---
name: social-listening
description: Social listening RO — caută ACTIV mențiuni și buzz despre brandurile Arona (Nubra, Esteban, George Talent, Grandia, Belasil, Gento, Covoria) pe web și social, din surse pe care le avem deja, fără tool plătit de monitoring și fără cod extern neverificat. Combină: mențiuni/profile terțe în Google RO (DataForSEO SERP, localizat — scoate postările de Instagram/Facebook/forumuri care ne pomenesc, inclusiv influenceri/UGC), numărul global de mențiuni + sentiment (DataForSEO Content Analysis), branded search din Search Console (căutări pe numele brandului, săptămâna curentă vs precedentă = semnalul „se vorbește/caută despre noi", cu detecție de spike/uptick), Reddit (best-effort) și Instagram hashtag (best-effort, dacă există token IG). Folosește pentru „social listening", „caută mențiuni despre brandul nostru", „ce se vorbește despre Nubra/Esteban", „ne-a pomenit cineva pe social media", „vreo postare de influencer", „brand mentions", „buzz", „monitorizare social media", „cine ne pomenește", „ce zice lumea despre noi", „branded search a crescut?".
argument-hint: scan <brand> [--days 7] [--only mentions,reddit,gsc,instagram]
---

# social-listening — caută mențiuni & buzz despre brandurile Arona

Răspunde la „ne-a pomenit cineva pe social / s-a întâmplat ceva organic / ce se vorbește despre brand"
folosind **doar surse pe care le avem deja** (DataForSEO, Search Console, Reddit/IG public) — fără
abonament la un tool de mention-monitoring și fără să instalăm skill-uri externe neverificate.

## Rulare
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export DATAFORSEO_LOGIN="$(uv run "$KB" secret-get DATAFORSEO_LOGIN)"
export DATAFORSEO_PASSWORD="$(uv run "$KB" secret-get DATAFORSEO_PASSWORD)"
export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"

uv run scripts/social_listen.py scan nubra --days 7
uv run scripts/social_listen.py scan esteban --days 14 --only mentions,gsc
uv run scripts/social_listen.py brands          # brandurile configurate
```

## Ce face fiecare sondă (toate READ-ONLY, fiecare degradează grațios la `n/a`)
| sondă | sursă | ce dă | fiabilitate |
|---|---|---|---|
| **mentions** | DataForSEO **Google RO SERP** + Content Analysis | profile/postări TERȚE care ne pomenesc (IG/FB/forumuri/marketplace), marcate 📱 când-s social; + nr. global de mențiuni & sentiment | **bună** (SERP RO e localizat și curat); costă câțiva cenți/rulare |
| **gsc** | Google Search Console | **branded search** (căutări pe nume) fereastra curentă vs precedentă, cu `flat / 🔼uptick(+15-30%) / 🔺spike(>+30%)` | **bună**, dacă SA `looker-sheets` e Full user pe proprietate; lag GSC ~3 zile |
| **reddit** | reddit.com search (public) | fire recente care pomenesc brandul | best-effort (Reddit dă 403 de pe IP-uri server; volum RO mic) |
| **instagram** | Graph API `ig_hashtag_search` | postări recente cu #brand | best-effort — cere `IG_GRAPH_TOKEN` + `IG_BUSINESS_ID` în KB (lipsesc acum) |

La final tipărește un **VERDICT** scurt: e doar UGC/seeding plătit sau chiar un puseu organic real?
(combină branded-search trend + nr. de mențiuni terțe/social).

## Capcane (citește înainte să interpretezi)
- **Nume ambigue** (`nubra`, `grandia`, `gento`, și chiar `esteban`): omonime internaționale poluează
  indexul GLOBAL al Content Analysis — „nubra" prinde brandul de sutiene **NuBra** și **valea Nubra**
  (India); „esteban" prinde **Esteban Paris Parfums** (FR). De-aia **SERP-ul Google RO** (localizat) e
  sursa de încredere pentru mențiuni, iar numărul „total indexat" e doar orientativ (e marcat cu ⚠).
- **Branded search ≠ neapărat viral.** O creștere de +15-30% pe impresii e de obicei doar efectul a mai
  multe vânzări/seeding, nu o postare virală. Spike real = >+30%.
- **Atribuire vs organic:** ca să decizi dacă un val e organic, compară cu ad-attribution (vezi
  `gigi:meta-ads` / `gigi:tiktok-ads`): dacă ~100% din comenzi sunt atribuite reclamelor, nu e val organic.

## Config branduri
În `scripts/social_listen.py`, dict `BRANDS` (name, site, terms, context, ambiguous). Adaugă un brand nou
acolo. `OWNED_DOMAINS` ține domeniile noastre, ca să le excludem din „mențiuni terțe".

## Reutilizează (nu duplica)
Aceleași credențiale ca `gigi:analytics` (DataForSEO `DATAFORSEO_*`, GSC via `GA4_SA_JSON`). Pentru
comentariile de pe RECLAMELE noastre (nu mențiuni terțe) folosește `gigi:cs-comment-intelligence`.
Pentru ce ad/creativ trage vânzările (UGC de influenceri) — `gigi:meta-ads` / `gigi:tiktok-ads`.

## De activat pe viitor (best-effort azi)
- **Instagram hashtag/mentions**: pune în KB `IG_GRAPH_TOKEN` (scope `instagram_basic` +
  `instagram_manage_insights`) și `IG_BUSINESS_ID` (contul IG Business) → sonda `instagram` pornește.
- **TikTok/Facebook organic search**: nu există API oficial curat (FB public search e scos din 2018,
  TikTok n-are search pt business) — rămân doar comentariile pe conținutul nostru, via Richpanel.
