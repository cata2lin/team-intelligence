---
name: ad-copy
description: Ad Copy & Angle Generator for Google Ads RSAs (and PMax text) — write diversified, keyword-relevant headlines/descriptions that lift ad strength from AVERAGE to GOOD/EXCELLENT, and apply them with a full-replace tool (char-validated, dry-run by default). Encodes the angle framework (offer, benefit, feature, proof, identity, urgency), angle multiplication, generic-language elimination, and per-ad-group keyword relevance. Use when an RSA is AVERAGE despite 15 headlines, or to generate fresh test angles.
---

# Ad Copy & Angle Generator

An RSA at **AVERAGE with 15 headlines and 0 pins** is not short on quantity — it's short on
**diversity** and **keyword relevance**. This skill fixes both.

## Why ad strength stalls at AVERAGE (diagnose first)
- **Low diversity** — 15 headlines that all say the same thing (3 ways to write "best detergent").
  Google wants distinct *angles*, not synonyms.
- **Low keyword relevance** — headlines don't contain the ad group's keywords. (Real failure mode:
  Belasil ran **3 different ad groups — cantitate / ieftin / gel — with the IDENTICAL copy**. None
  matched its own keywords → all AVERAGE. Differentiating each to its theme is the fix.)
- Over-pinning (pins kill diversity) or too few descriptions (<4) — check those too.

## ⚠ Reality check — ad strength ≠ performance (read before chasing EXCELLENT)
**Ad Strength is a Google *guidance* label, not a performance metric.** An AVERAGE asset group can
deliver **ROAS 37** (real: Esteban Damă is AVERAGE and prints ROAS 37); a GOOD RSA can outconvert an
EXCELLENT one. Don't optimise the label at the expense of money.
- **Maxed ≠ EXCELLENT.** Assets with 15 headlines / 5 long / 5 descriptions / images in all ratios /
  video / extensions, all genuinely diverse, **still sit at AVERAGE** — because EXCELLENT is Google's
  opaque holistic call. Many top accounts run at GOOD. You cannot always reach EXCELLENT, period.
- **Don't churn performing/learning campaigns for the label.** Rewriting copy → ad strength recompute
  (PENDING) AND, on PMax, contributes to learning. Forcing "more diversity" onto a campaign that's
  *working* can reset learning and *lower* real performance for a cosmetic badge. Esteban PMax stayed
  AVERAGE after maxing every lever — correct move was to **leave it and scale budget** (ROAS 37, was
  budget-capped), not to keep rewriting.
- **When it IS worth a diversity pass:** a NEW/underperforming RSA that's AVERAGE with genuinely
  repetitive or off-keyword headlines (e.g. Belasil's 3 Non-Brand ad groups that shared identical
  copy). Fix those. Leave the winners alone.
- **Two different "learnings":** asset-strength recompute (PENDING, from any asset/text change) is
  separate from **bidding learning** (from budget/tROAS changes). A tROAS change reset Belasil PMax's
  *bidding* (ROAS −65% → hold ~2 weeks); a headline swap only re-scores ad strength, not bidding.

## The angle framework (cover 5–6 distinct angles across the 15)
1. **Offer / price** — 2+1 gratis, 0,49 lei/spălare, -36%, 10L la 99 lei
2. **Benefit / outcome** — 200 de spălări, persistă 12h, parfum de durată
3. **Feature / mechanism** — gel concentrat, balsam inclus, bidon 10L
4. **Trust / source** — de la producător, fără intermediari
5. **Proof / social** — 4,7/5 din 1.250+ recenzii, mii de clienți
6. **Identity / category** — "Detergent Rufe Lichid", "Parfumuri Bărbați" (also the keyword anchor)
**Angle multiplication**: take each angle and write 2–3 *genuinely different* lines, then cut
near-duplicates. **Generic-language elimination**: kill "calitate superioară / cel mai bun" filler;
prefer concrete, specific, keyword-bearing claims.

## Keyword relevance (the other half)
Headlines should echo the **ad group's own keywords** (`gigi:search-terms` / `gads.py keywords`).
Lead each ad group with 3–4 headlines that contain its theme terms (e.g. the "5-10L" group →
"Detergent Bidon 10 Litri", "Detergent Lichid 5 Litri"). This is what makes 3 ad groups in one
campaign each earn their own strength instead of sharing one generic ad.

## Apply it
Edit the `RSAS` list in **`scripts/rsa_apply.py`** (cid, ad_id, 15 headlines ≤30, ≤4 descriptions ≤90)
— it validates length + duplicate headlines, then:
```bash
uv run scripts/rsa_apply.py            # dry-run + char/dupe check
uv run scripts/rsa_apply.py --apply    # full-replace headlines + descriptions in one update
```
Full-replace (updateMask `responsive_search_ad.headlines,…descriptions`) so the old similar set is
gone. After applying, ad strength goes **PENDING** then resettles in a few days — don't churn it daily.

## Constraints & gotchas
- Headlines **≤30 chars**, descriptions **≤90** (RO diacritics count as 1). No `~`/odd symbols
  (`policyFindingError: SYMBOLS`). Don't put competitor trademarks in your own copy.
- 15 headlines, 4 descriptions, minimal pinning = the ceiling Google rewards.
- For **PMax** asset groups the same diversity logic applies to HEADLINE/LONG_HEADLINE/DESCRIPTION
  assets — see `gigi:google-ads-mcc` (`add_rsa_headlines.py` / asset-group text).
- For a quality bar, generate angles with a small judge-panel workflow (multiple angles → score for
  diversity + char limits + brand fit) — the methodology from the public `advertising-skills`
  (Schwartz awareness, angle multiplication) folds in here; the **execution** stays in our toolkit.
- Pairs with **`gigi:search-terms`** (keywords + winners) and **`gigi:google-ads-mcc`** (apply/verify).
