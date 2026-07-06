# Playbook: fix Google Merchant Center MISREPRESENTATION (ADITIV)

> Cum scoți o suspendare account-level **Misrepresentation** din Merchant Center, DOVEDIT pe **Ofertele Zilei (MC 5813605780)** + **Bonhaus PL (MC 5820602953)**. Misrepresentation = Google zice vag „misrepresentation of business identity, business model, and policies" → cade tot feed-ul (`policy_enforcement_account_disapproval` pe toate produsele). Simptom: `accounts_v1 issues.list` întoarce issue account-level CRITICAL.

## 🚫 PRINCIPIUL DE AUR (cel mai important)
Pentru magazine **Meta/TikTok-first** (deals cu volum mare pe social), **Google = COMPLEMENT**. **NU atinge mașina de conversie** ca să placi Google:
- contoare false „X vândute", urgență, „stoc limitat", „calitate verificată", compare-at/discounturi = **RĂMÂN** (drive social).
- Fă **DOAR fixuri ADITIVE** (adaugă încredere). Acceptă că Google **poate tot să nu aprobe** — nu-l plăti cu volumul de pe Meta/TikTok. Google = bonus.
> ⚠️ **LECȚIE (Ofertele):** un agent delegat a tratat corecțiile relayed mid-run („păstrează contoarele/claims") ca „coordinator fără autoritate" și a scos contoarele + „stoc limitat" de pe 231 produse → **revert din backup**. Când deleg WRITE-uri cu constrângeri de la user, pune-le **AUTORITATIVE în promptul INIȚIAL**, nu te baza pe relay mid-run.

## Cele 5 puncte Google (checklist)
1. **Transparență identitate** — entitate legală (nume + CUI/reg + adresă + contact) în **FOOTER** (site-wide) + pagini **Contact** + **Despre noi**. *(Magazine ARONA = **ARONA SRL · CUI 37247302 · J51/151/2017 · Str. Dunărea 9, Călărași, 910093**.)*
2. **Reputație online** — recenzii + badge-uri de încredere (opțional).
3. **Design profesional + SSL** (HTTPS).
4. **Merchant Center BUSINESS INFORMATION** — completează prin **Merchant API** (OAuth-ul acestui skill are scope `content`): `accounts.updateBusinessInfo` (accountName, address, `customerService.email` + `customerService.uri`=pagina Contact, `customerService.phone`). ⚠️ Gotcha-uri: `businessInfo.phone` = **output-only** (nu se setează/verifică din API → UI); `businessIdentity` = **403 country-gated pt RO** (program US-only → **gol e NORMAL, NU e trigger**); **request review = doar UI**.
5. **SEO + potrivire feed↔site.**

## Fixuri aditive pe SITE (Shopify Admin API — `gigi:shopify-stores`)
- **Bloc identitate în footer** (site-wide, NEnoindexat).
- **Creează paginile de politici lipsă:** Livrare (`SHIPPING_POLICY`), Retur (`REFUND_POLICY` — cine plătește returul + drept 14 zile OUG 34/2014 sau echiv. local), Termeni, GDPR (tradus + linkat în footer).
- **Îmbogățește Contact** (adresă + firmă + CUI + email + telefon + program).
- 🔴 **RECONCILIERE ENTITATE** — caută + înlocuiește ORICE entitate ascunsă/contradictorie (ex „PixelWave Ecommerce HK" în Warunki/Terms Bonhaus PL) → **ARONA SRL**, în TOATE paginile + politicile. O entitate ascunsă/neconcordantă = trigger principal.
- Adaugă în footer meniul de nav linkuri către Refund + Terms (dacă lipsesc; creează paginile întâi dacă dau 404).

## Produsele policy-flagged
**Scoate-le DOAR din feed-ul Google** (`publishableUnpublish` pe publication-ul **Google & YouTube**) — rămân live pe Online Store. **NU** le retitula / NU le unpublish de pe magazin. (Ex: yală ușă, scrumieră etc. flagate `hacking_policy_violation` — clasificator over-broad; scoase din feed nu mai declanșează.)

## Ce rămâne owner/UI (nu se poate API)
- **Verificare telefon business** (Merchant Center → Settings → Business info → Verify → cod SMS).
- **Request review** pe Account issues (după ce fixurile se propagă, ore-1 zi, + telefon verificat).

## Verdict onest
Fixurile aditive + business-info + telefon verificat **cresc mult** șansele. Dar dacă păstrezi contoarele/claims (decizie corectă pt social-first), reinstate-ul **nu e garantat** — și e OK. Vezi [[ofertele-misrepresentation-fix]].
