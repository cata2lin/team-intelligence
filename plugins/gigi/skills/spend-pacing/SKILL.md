---
name: spend-pacing
description: "Ad-spend pacing + MER forecaster for the month, per ARONA brand and channel (Google/Meta/TikTok). Shows month-to-date spend, linear run-rate projection to month-end, vs last month, and MER (placed gross revenue / spend) for RON stores. Answers: am I on pace to overspend/underspend this month, which brand is burning budget too fast, what's the blended return. Use when the user asks about budget pacing, spend pacing, month-end spend projection, are we on budget, overspending, MER, blended ROAS, or marketing efficiency this month."
user-invokable: true
---

> **ARONA (gigi) — de ce e fiabil chiar când Meta pică.** Spend-ul vine din
> `metrics.cache.daily_ad_spend_ron` (RON, per magazin/canal/zi), alimentat de pipeline-ul **WMS**
> (FB+TikTok per-ad + Google) — **independent de tokenul Meta OAuth** care e single-point-of-failure
> ([[meta-token-single-point-failure]]). Când tabelele `*_ad_insights_daily` îngheață, cache-ul ăsta
> rămâne fresh. MER = venit din AWBprint (comenzi plasate luna asta). Complementar lui
> [[daily-ops-briefing]] (snapshot zilnic) — ăsta e PACING pe lună + proiecție. Vezi [[cpa-financiar-live-report]]
> pt date pe ziua curentă din sheet.

# spend-pacing — pacing buget ads + MER pe luna curentă

## Când o folosești
„Suntem pe buget luna asta?", „cine cheltuie prea repede?", „cât ajungem la final de lună?",
„care e MER-ul pe brand?", „pe ce canal merge banii la Esteban?".

## Cum rulezi
```bash
cd plugins/gigi/skills/spend-pacing/scripts
export DATABASE_URL_METRICS="$(uv run ../../../../core/scripts/kb.py secret-get DATABASE_URL_METRICS)"
export DATABASE_URL_AWBPRINT="$(uv run ../../../../core/scripts/kb.py secret-get DATABASE_URL_AWBPRINT)"  # pt MER

uv run spend_pacing.py                      # toate magazinele
uv run spend_pacing.py --store esteban.ro   # un magazin + breakdown pe canal
uv run spend_pacing.py --no-mer             # doar spend (nu lovi AWBprint)
```

## Ce calculează
- **spend MTD** per magazin (și per canal cu `--store`), din `cache.daily_ad_spend_ron`.
- **proiecție** = `spend MTD / zile_scurse × zile_în_lună` (run-rate liniar).
- **vs LM** = proiecție vs **luna trecută completă** (același magazin) → +/- %.
- **MER** = venit PLASAT gross (AWBprint, comenzi `frisbo_created_at` în luna curentă) / spend.
  Doar magazine **RON** (non-RON: bonhaus.cz/pl/bg → pacing only, MER „—"). Spend sub 100 RON → MER „—" (irelevant).

## Cum citești
- **vs LM mare +** pe un brand mic = scalare agresivă (verifică dacă MER ține); **vs LM mare −** = canal oprit/buget tăiat.
- **MER scăzut** (ex. Grandia ~1.9) = brand de marjă mică / cont agenție — normal; **MER mare** (Esteban ~5.5) = eficient.
- Breakdown pe canal (`--store`) arată unde se duce banul: Meta vs TikTok vs Google.
- ⚠️ Dacă apare avertismentul de staleness (spend recent vechi >2 zile) → proiecția e sub-estimată, pipeline-ul ads întârzie.

## Capcane
- **MER ≠ profit.** E venit gross / spend — NU scade COGS, transport, refuz COD. Pt contribuție reală →
  [[multi-brand-pnl]] / `profit_by_sku.py` ([[profitability-breakeven-model]]).
- MER pe venit **plasat** (nu livrat) ca să se alinieze temporal cu spend-ul lunii; include deci și comenzi
  care se vor refuza. Pt venit real livrat → [[data-analytics-skill]] / fulfillment.
- Proiecția e liniară (nu prinde weekend/spike-uri de campanie). E pacing, nu forecast fin.
- `store_name` „nubra" (fără .ro) e mapat automat la nubra.ro.
