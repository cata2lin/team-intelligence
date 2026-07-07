---
name: brand-entity
description: "Construiește graful de ENTITATE off-site al unui brand ARONA pentru Google Knowledge Graph + citări în AI-search (GEO): item Wikidata (via API) + wiring în Organization JSON-LD `sameAs` (Wikidata, Trustpilot, LinkedIn, FB/IG/TikTok) + harta fundației de autoritate gratuite (ce e dofollow/nofollow, ce e CAPTCHA-gated). Use când: 'entitate brand', 'Wikidata pt brand', 'sameAs', 'Knowledge Graph', 'listări free de autoritate', 'GBP/Trustpilot/Crunchbase/LinkedIn', 'de ce nu apare brandul în panoul Google'. Grandia = referință completă (Q140455536)."
user-invocable: true
argument-hint: "[--label Nubra --domain nubra.ro ...] (vezi scripts/wikidata_brand.py)"
license: MIT
metadata:
  author: gigi
  version: "1.0.0"
  category: seo
---

# brand-entity — graf de entitate off-site (Wikidata + sameAs + fundație free)

Face un brand să existe ca **ENTITATE** pentru Google (Knowledge Graph / panou de brand) și pentru
motoarele AI (ChatGPT/Perplexity — GEO). Nu dă link-juice de ranking (ăla = dofollow câștigat/plătit,
vezi `gigi:instapress`), ci **semnal de entitate**: cine e brandul, unde e site-ul, ce profiluri are.
**Grandia = referință completă** (7-iul-2026): item Wikidata **Q140455536** + `sameAs` cu 6 profiluri.

## Adevărul dur (ca să nu pierzi timp)
- Listările free = **aproape zero dofollow**. Singurul free-dofollow RO era Crunchbase, dar e **US-centric + Cloudflare „you are blocked"** (browserul automat NU trece). TRUSTED.ro = **plătit** (239€+). Restul = **nofollow / entity-signal**.
- **Toate signup-urile sunt CAPTCHA-gated** (Crunchbase/LinkedIn/Trustpilot/Cylex/Compari/**Wikidata cont**). Browserul chrome-devtools e amprentat ca automation → blocat. Deci **omul face contul** (în browserul LUI), agentul face **restul** (conținut de paste, verificare domeniu DNS/meta, wiring sameAs, Wikidata via API).
- **Wikidata = singurul de valoare pe care-l face agentul singur** (via API, nu browser).

## 1) Item Wikidata — `scripts/wikidata_brand.py` (idempotent)
Login = contul echipei **Aronasrl** (creds `~/Downloads/credentials/wikidata.txt`, format `user pass: parola`; **`clientlogin`**, NU `action=login`). ⚠️ La prima folosire a contului, pune o **disclosure de paid-editing** pe `User:Aronasrl` (ToS Wikimedia — reduce riscul de ștergere ca spam).
```bash
python3 scripts/wikidata_brand.py --label "Nubra" --domain nubra.ro \
  --desc-en "Romanian online perfume store" --desc-ro "magazin online de parfumuri" \
  --p31 Q4382945 --fb nubra.ro --ig nubra.ro --tiktok nubra.ro --linkedin nubra-parfumuri \
  --ref https://<presa-tertiara-despre-brand> --apply    # fara --apply = dry-run
```
- Proprietăți: **P31** instance-of (online shop=`Q4382945`, brand=`Q431289`), **P856** website, **P17** țară (`Q218` RO), **P452** industrie (`Q484847` e-commerce), **P2013** Facebook, **P2003** Instagram, **P7085** TikTok, **P4264** LinkedIn (username/slug, NU URL întreg — sunt external-id). **P854** = referință (URL de presă terță) pe P31, ca să nu fie șters ca auto-promo.
- Grandia (P31=online shop) = Q140455536. Parfum-dupe (Esteban/GT/Nubra) = **risc mai mare de ștergere** + confuzie de entitate (Esteban vs casa franceză) → fă-le **PE RÂND**, la câteva zile după ce Grandia supraviețuiește patrolării (evită pattern mass-creation de pe cont fresh).

## 2) Wiring `sameAs` (după ce profilurile există)
În Organization JSON-LD din temă (`sections/header.liquid` la majoritatea; `header-navigation-plain.liquid` la Esteban), extinde array-ul `sameAs` cu fiecare profil live. Via `gigi:shopify-stores/scripts/shopify_theme.py` (get → edit → put). Grandia `sameAs` = IG, FB, Wikidata Q-URL, Trustpilot, LinkedIn (**URL vanity** `linkedin.com/company/<slug>`, NU `/admin/dashboard/`), TikTok. ⚠️ Asset API GET dă cache STALE ~3-4s după PUT.
- **Buclă bidirecțională**: site → sameAs → Wikidata, ȘI Wikidata → website + socialuri. Ăsta e semnalul puternic.

## 3) Fundația de autoritate free (harta, per brand) — 55 platforme auditate
Checklist + conținut gata de paste: `~/Downloads/free-listings/`. Priorități (toate nofollow/entity, omul face contul):
1. **Trustpilot** (revendică profilul care poate exista deja nerevendicat) · 2. **Cylex** (profil per-brand, scapă de limita 1-CUI) · 3. **LinkedIn** Company Page · 4. **NAP-fill** listafirme/termene/risco (deja auto-listate din ANAF, adaugă website) · 5. **Apple Business Connect** (permite brand fără vitrină). **GBP/Bing** = doar dacă biroul devine punct real de ridicare.
- **De SĂRIT:** Fragrantica/Parfumo (resping dupe-urile + confuzie entitate), Wikipedia (notabilitate — cere presă editorială câștigată, nu plătită), Gravatar/about.me/Linktree/Waze/Sitejabber (nofollow, ~0).
- **✅ Deja OK:** ANPC SAL + EU ODR (footer, toate 6 magazine); auto-listate ANAF (listafirme/termene/risco/firme.info); ScamAdviser per-domeniu.

## Capcane
- **Wikidata login** = `clientlogin` (parola principală), NU `action=login` (ăla cere bot-password). Contul fresh poate crea item-uri.
- **Social props = username/slug**, nu URL (P2013 „grandia.ro", P4264 „grandia-ro").
- **Crunchbase/LinkedIn** = browserul automat e BLOCAT (Cloudflare) → omul, în browserul lui.
- **NAP unic**: ARONA SRL · CUI RO37247302 · J51/151/2017 · Str. Dunărea nr. 9, Călărași 910093 · 2017.

Companion: `gigi:shopify-stores` (sameAs/temă), `gigi:cloudflare` (verificare domeniu DNS), `gigi:analytics` (`dataforseo.py backlinks --list` = follow/nofollow), `gigi:instapress` (dofollow câștigat), `gigi:seo-schema`. Memorii: [[offsite-seo-strategy]], [[grandia-self-hosted-feeds]].
