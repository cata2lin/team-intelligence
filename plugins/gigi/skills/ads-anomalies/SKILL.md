---
name: ads-anomalies
description: Google Ads Anomaly Detector — compare the last few complete days against a baseline window, per ENABLED campaign and account-wide, and flag what broke: zero conversions where there were some (tracking/feed), ROAS drop, spend spike/drop, CPA rise, CPC rise, impression collapse. Severity-ranked, read-only, built to run daily (cron). Catches problems the campaign-level dashboard averages away.
---

# Google Ads Anomaly Detector

Yesterday's average hides today's fire. This compares **recent** (last N complete days) vs a
**baseline** (the prior M days), per campaign and for the whole account, and tells you what changed.

## What it flags (and the likely cause → action)
| Alert | Sev | Likely cause | Do |
|---|---|---|---|
| **Conversions 0** (baseline had some) | 🔴 | tracking/pixel broke, feed disapproved, campaign limited | Check conversion tracking + Merchant feed **first** — this is usually a measurement break, not a real sales drop |
| **ROAS −X%** | 🔴 | big budget/bid change reset learning, competitor, promo/feed change, seasonality | Don't pile on budget; check what changed 3–7 days ago (`change_history.py`) |
| **Spend +X%** | 🟠 | budget raised, bid loosened, new auction pressure | Confirm conversions kept pace; if not, it's waste |
| **CPA +X%** | 🟠 | efficiency dropping | Tighten tROAS/tCPA, check search terms for junk |
| **CPC +X%** | 🟡 | auction competition | Watch; raise quality/relevance |
| **Impressions −50%** | 🟡 | budget exhausted early, bid too low, disapproval | Check budget pacing + approvals |

## Run it (daily)
```bash
uv run scripts/anomaly_detector.py --customer 5229815058                 # recent 3d vs baseline 14d
uv run scripts/anomaly_detector.py --customer 7566352958 --recent 1 --baseline 7
```
Tune `--spend-dev` `--roas-drop` `--cpa-rise` `--cpc-rise` `--min-spend` (ignores tiny campaigns).
Loop both accounts in one line; wire to cron for a morning check.

## How to read it
- **"today" is partial** and is excluded automatically — recent = last *complete* days.
- A spend/CPA alert right after **you raised a budget or target** is expected — read with
  `change_history.py` (did we change it, or did the auction?). Real alarms = ROAS drop with no
  change on our side, or conversions going to 0.
- Thresholds are deviations vs baseline daily averages, so a campaign that's simply ramping reads
  as a spend spike — `--min-spend` filters noise from tiny campaigns.
- Pairs with **`gigi:google-ads-mcc`** (`change_history.py` to see who changed what, `gads.py` to act).
