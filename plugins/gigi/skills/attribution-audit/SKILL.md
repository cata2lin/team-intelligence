---
name: attribution-audit
description: Auditează tab-ul „Mapping" (sheet „CPA și financiar") pentru bug-uri de ATRIBUIRE a spend-ului de reclame între branduri — tiparul descoperit la Belasil: un cont TikTok PARTAJAT de mai multe branduri, dar un brand îl revendică cu TOKEN DE CAMPANIE GOL → înghite toate campaniile contului (inclusiv ale altui brand), umflând spend-ul și dublu-numărându-l. Listează conturile partajate, le pune flag (🔴 token gol / 🟠 token duplicat / 🟢 curat) și, cu --live, cuantifică din API spend-ul fantomă (RON) al campaniilor cu tag-ul altui brand. Folosește pentru „verifică maparea/atribuirea spend", „de ce are brandul X spend TikTok pe care nu-l rulează", „audit Mapping", „spend fantomă", „dublu-numărare spend", „verifică toate datele din Raport Zilnic", „cont TikTok partajat", "wrong ad spend attribution", „de ce nu ia bine spend-ul la brand".
argument-hint: [--live]
---

# attribution-audit — prinde spend-ul fantomă din Mapping

Generalizează bug-ul Belasil: un cont TikTok **partajat** între branduri e sigur DOAR dacă fiecare
brand care-l revendică are un **token de campanie** DISTINCT și ne-gol (col „Campanie" din Mapping).
Token gol pe cont partajat = brandul înghite toate campaniile contului → phantom + dublu-numărare.

## Rulare
```bash
KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"
uv run scripts/mapping_audit.py                                  # audit static (o citire Mapping)

export DATABASE_URL_METRICS="$(uv run "$KB" secret-get DATABASE_URL_METRICS)"
uv run scripts/mapping_audit.py --live                           # + cuantifică spend-ul fantomă din API
```

## Ce dă
- **static**: lista conturilor TikTok partajate + brandurile care le revendică + token-ul fiecăruia.
  Flag: 🔴 token GOL (înghite tot), 🟠 token DUPLICAT (nu se separă), 🟢 curat.
- **--live**: pentru brandurile 🔴, rulează `tiktok.py report <brand> --level campaign` și scoate
  campaniile al căror nume conține tag-ul ALTUI brand, cu spend-ul (RON/14z) = phantom confirmat.

## Context (de ce există)
Atât **Raport Zilnic 2** (Apps Script `adaugaRandZilnic2`) cât și skill-urile [[meta-tiktok-ads-skills]]
(brandmap.py) citesc tab-ul Mapping LIVE și atribuie spend-ul pe brand după conturi + token de campanie.
Vezi [[mapping-tiktok-attribution]] pentru cazul Belasil (înghițea campaniile „NEW TIKTOK ESTEBAN").
Coloane Mapping: A Brand · B Facebook · C Tiktok · D Shopify · E Google · **F Campanie(token)** · G Cont multiplu.

## Capcane
- SA-ul Sheets dă „Regional Access Boundary / Precondition check failed" la apeluri în rafală (rate-limit) —
  scriptul face O singură citire; nu-l rula în buclă.
- FB/Google sunt conturi dedicate (nume exact), deci partajarea apare la TikTok — de-aia auditul e pe TikTok.
- Token-ul filtrează pe substring în numele campaniei (ex. „BELASIL" prinde „BLACK FRIDAY BELASIL").
