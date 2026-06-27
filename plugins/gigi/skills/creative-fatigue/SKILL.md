---
name: creative-fatigue
description: "Creative / audience fatigue detector for Meta + TikTok ad accounts — compares a recent window vs a baseline and flags accounts where frequency is climbing AND CTR is falling and/or CPA is rising, the classic audience-saturation signal that means 'refresh the creatives / widen the audience'. Account-level from the warehouse (per-creative drill-down via meta-ads/tiktok-ads). Use when the user asks about creative fatigue, ad fatigue, audience saturation, frequency too high, CTR dropping, CPA rising, creatives getting stale, or when to refresh ads."
user-invokable: true
---

> **ARONA (gigi) — ce e și ce NU e.** Agenția rulează Meta+TikTok; skill-ul ăsta e semnalul de
> **accountability**: pe care CONT s-a saturat audiența (frecvență ↑ + CTR ↓ / CPA ↑) → spune-le să
> reîmprospăteze creative-urile sau să lărgească targetarea. Date la **nivel de cont/zi** din
> `metrics.{meta,tiktok}_ad_insights_daily` — **NU per-creativ** (tabela nu are ad_id). Pt a vedea CARE
> creativ moare pe un cont flagat → drill cu [[meta-tiktok-ads-skills]] (`gigi:meta-ads` / `gigi:tiktok-ads`,
> raport la nivel de ad, live API). Complementar lui [[competitor-ads-skill]] (creative-urile COMPETIȚIEI)
> și [[agency-audit]] (dacă agenția cheltuie bine).
>
> ⚠️ Tabelele insight îngheață odată cu tokenul Meta ([[meta-token-single-point-failure]]) — skill-ul
> raportează relativ la ULTIMA zi cu date și avertizează dacă sunt vechi.

# creative-fatigue — saturație de audiență pe conturi Meta + TikTok

## Când o folosești
„Pe ce s-a saturat audiența?", „unde a crescut frecvența?", „de ce scade CTR-ul / crește CPA?",
„ce conturi au nevoie de creative noi?".

## Cum rulezi
```bash
cd plugins/gigi/skills/creative-fatigue/scripts
export DATABASE_URL_METRICS="$(uv run ../../../../core/scripts/kb.py secret-get DATABASE_URL_METRICS)"

uv run creative_fatigue.py                              # ambele platforme, doar conturi flagate
uv run creative_fatigue.py --platform meta --recent 7 --baseline 21
uv run creative_fatigue.py --all                        # arată și conturile sănătoase
uv run creative_fatigue.py --min-spend 500             # ignoră conturi mici
```

## Ce calculează
Per cont (mapat la brand prin `brand_{meta,tiktok}_ad_accounts`), compară fereastra **recentă**
(`--recent`, default 7z) vs **baseline** (`--baseline`, default 21z dinainte), relativ la ultima zi cu date:
- **Δfreq** (afișări/persoană), **ΔCTR**, **ΔCPM**, **ΔCPA**, + `freq` și `ROAS` curent.
- 🔥 **fatigue** = `Δfreq > +15%` ȘI (`ΔCTR < −10%` SAU `ΔCPA > +20%`).

## Cum citești
- 🔥 = audiența vede aceleași reclame prea des și răspunde tot mai prost → **refresh creative / lărgește audiența**
  (mesaj concret pt agenție). Drill pe contul flagat cu `gigi:meta-ads`/`gigi:tiktok-ads` (raport per-ad)
  ca să vezi exact ce creativ să oprești.
- `freq` mare în absolut (Meta >3-4, TikTok >5) = semnal de saturație chiar dacă Δ e mic.
- `--all` arată tot tabloul (util să vezi conturile sănătoase, ex. TikTok cu freq 2-3 + ROAS bun).

## Capcane
- **Account-level, nu creative-level** — spune CARE cont, nu CARE reclamă. Pentru reclama exactă = drill live.
- Date stale când pipeline-ul ads e oprit (token Meta) → verifică linia „ultima zi cu date".
- Conturi partajate TikTok (un cont, mai multe branduri pe token din numele campaniei) → eticheta de brand
  poate fi cea a contului, nu a sub-brandului ([[mapping-tiktok-attribution]]).
- Prag `--min-spend` (default 300 RON recent) ca să nu raportezi conturi neglijabile.
