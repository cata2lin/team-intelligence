---
description: Runbook to fix the Grandia BI "Marketing Performance" report so daily ROAS / Contribution Margin reflect real P&L economics — the 5 fixes, target numbers, and verification against the canonical scripts/grandia_pnl.py.
---

# Fix the Grandia BI "Marketing Performance" report

> Author: **Arona core**.

**Goal:** make the daily ROAS / Contribution Margin dashboard reflect *real*
P&L economics, so scaling decisions are based on truth instead of vanity
numbers.

**Source of truth:** `${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py` (ported into
the `core` plugin). That script's logic is the spec — port it 1:1 into the BI
app. Per-field formulas + source DBs are documented in the `grandia-pnl` skill.

All credentials (`DATABASE_URL_*`, Meta / Google Ads / TikTok / DPD tokens) come
from the SharedClaude `secrets` table: `grandia_pnl.py` imports `kb_env` and
calls `load_secrets_into_env()` up front, so the only thing the environment must
provide is `$KB_DATABASE_URL`. No `.env` file, nothing on the NAS.

---

## What's wrong today (May 1–15 2026 audit)

| Metric | BI shows | Reality | Why it's wrong |
|---|---:|---:|---|
| Revenue | 469,100 RON | **407,985 RON** | BI includes **PENDING** orders (COD not yet collected). |
| Orders | 2,068 | 1,922 | Same — PENDING leaks in. |
| Ad spend | 118,549 RON | **136,165 RON** | **TikTok is missing** (10,618 RON); Google is also ~7k short — probably MCC scope. |
| ROAS (gross) | 3.96× | **3.00×** | Inflated by both errors above. |
| Contribution Margin | 158,860 (33.9%) | **34,066 (10.1%)** | BI's formula is `Revenue − COGS − Ads`. Missing: VAT (21%), transport, refunds, TikTok. |
| Gross margin | 59.14% | 60.75% | OK (small drift). |

The CM error is the dangerous one: BI says we have a 4× safety margin we don't have.

---

## Five concrete fixes

### 1. Exclude PENDING and VOIDED orders from revenue

Currently the report counts every order regardless of `financial_status`.

**Use only:** `PAID`, `PARTIALLY_REFUNDED`, `REFUNDED`.

**Exclude:**
- `PENDING` — COD not yet collected. Frisbo doesn't sync back to AWBprint, so most stay PENDING forever in some databases. Treating these as revenue overstates everything.
- `VOIDED` — cancelled, zero revenue anyway, but still inflates the order count.

**Source:** Shopify Admin GraphQL (`orders { financialStatus }`). Do **not**
trust AWBprint's `financial_status` column — it's stale by design.

### 2. Use net-of-VAT amounts everywhere, or label clearly

Romania VAT = 21%. Today BI mixes them:

- Revenue field uses Shopify `totalPrice` (gross, incl. VAT).
- COGS field uses AWBprint `sku_costs.cost` (also gross — purchase invoices include VAT).
- Ad spend is naturally net (reverse-charge, no VAT on invoice).

When you subtract `COGS − Ads` from `Revenue` you're mixing gross apples
with net oranges, and you keep the 21% VAT in "profit" — which is illegal
to spend, it belongs to the state.

**Two acceptable approaches**, pick one and stick with it:

| | Net P&L (recommended) | Gross P&L (labelled clearly) |
|---|---|---|
| Revenue | `currentTotalPrice / 1.21` | `currentTotalPrice` |
| COGS | `sku_costs.cost / 1.21` | `sku_costs.cost` |
| Transport | AWBprint `transport_cost_fara_tva` | `transport_cost` |
| Ad spend | unchanged (already net) | **multiply by 1.21** for fairness |
| Label | "All amounts net of VAT" | "All amounts incl. VAT — ads grossed up" |

The script uses the **net** convention because it's the standard P&L view.

> ⚠️ **Whichever you pick, never compare gross revenue to net costs.** That's
> the current bug.

### 3. Add transport / last-mile delivery as a cost line

Today's "CM" formula has no transport at all. For Grandia (COD-dominant,
DPD-shipped), transport is **10–13% of net revenue** — bigger than your
contribution margin itself.

**Source:** `AWBprint.order_awbs` joined to `orders.order_number = shopify_order.name`.
Use `transport_cost_fara_tva` (net) per AWB.

**Important data lag:** courier costs are populated only after the carrier
invoices back. For the current/partial month, most AWBs will have a 0 or
NULL cost. The script handles this with a **three-stage pipeline**:

1. **measured** — `transport_cost_fara_tva` populated → use as-is.
2. **backfilled** — for missing rows, look up `Grandia.courier_shipments.dpdResponse->'price'->>'total'` (the original DPD create-time price).
3. **estimated** — for still-missing rows, use the same-courier mean cost per AWB for the period.

Code: see `_fetch_awb_rows`, `_backfill_from_grandia`, `_estimate_missing`
in `${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py`.

For a quick BI implementation, the **flat fallback** is acceptable:
**18 RON per included order (net)** for partial months — that matches our
historical Grandia/DPD average. Refine once invoices come in.

### 4. Add TikTok ad spend; reconcile Google scope

BI is currently summing only Google + FB. Missing pieces:

- **TikTok** — pull from TikTok Marketing API v1.3. For Grandia we see
  ~2,800 USD / 12,700 RON per half-month — material.
- **Google Ads** — verify the MCC scope. Our script pulls from the full
  MCC tree and gets ~7k more than BI shows. Likely BI is filtering to a
  single sub-account or excluding non-search campaigns.

Reference implementations in `${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py`:
- Meta:       `meta_spend()`
- Google Ads: `google_ads_spend()` (REST v20)
- TikTok:     `tiktok_spend()` (with per-day USD→RON FX from `AWBprint.exchange_rates`)

All credentials come from the SharedClaude `secrets` table via
`kb_env.load_secrets_into_env()` (Meta / Google / TikTok tokens, DPD keys); in
BI they're presumably already wired for Meta + Google — just add TikTok.

### 5. Redefine "Contribution Margin" properly

**Wrong (current BI):**
```
CM = Revenue − COGS − Ads
```

**Right (operational P&L):**
```
Net Revenue        = Shopify currentTotalPrice / 1.21        ← already nets refunds
Net COGS           = sku_costs.cost × units / 1.21
Net Transport      = AWBprint transport_cost_fara_tva (3-stage pipeline)
Ad Spend           = Meta + Google + TikTok (all net)

Gross Margin       = Net Revenue − Net COGS
Contribution Margin = Gross Margin − Net Transport − Ad Spend
CM%                = Contribution Margin / Net Revenue
```

For ROAS, agencies see **gross** (with VAT) and ad spend net, so:
```
ROAS_gross = (Net Revenue × 1.21) / Ad Spend
```

That's the 3.00× number for May 1–15, not 3.96×.

---

## Target numbers (use as test assertions)

If you regenerate May 1–15 2026 from BI after the fixes, these should match
the script's output within 1–2% (some drift is expected from rounding and
courier cost lag):

| Field | Expected | Source |
|---|---:|---|
| Orders (incl) | 1,922 | PAID+REF only |
| Net revenue | 337,178 | gross / 1.21 |
| Gross revenue | 407,985 | for ROAS display |
| Net COGS | 132,351 | gross / 1.21 |
| Gross margin | 204,827 (60.7%) | NR − COGS |
| Net transport | 34,596 | 18 RON × 1,922 (flat fallback) |
| Ad spend total | 136,165 | Meta 38,800 + Google 86,747 + TikTok 10,618 |
| Contribution margin | 34,066 (10.1%) | GM − transport − ads |
| ROAS (gross) | 3.00× | (NR × 1.21) / ads |
| MER (net) | 2.48× | NR / ads |

---

## Order-status logic (copy-paste)

In whatever query layer BI uses for orders:

```sql
WHERE financial_status IN ('PAID', 'PARTIALLY_REFUNDED', 'REFUNDED')
-- explicitly exclude: 'PENDING' (COD not yet collected), 'VOIDED' (cancelled),
-- 'AUTHORIZED' (rare for COD), 'EXPIRED'
```

For an "informational excluded" widget you can still surface:
```
PENDING:  N orders, gross X RON   ← future revenue if collected
VOIDED:   N orders                ← cancelled, no impact
```

---

## VAT divisor (copy-paste constant)

```python
VAT_DIVISOR = 1.21   # Romania VAT 21%, applied to revenue, COGS, transport, refunds
                     # NOT applied to ad spend (reverse-charge, invoices are net)
```

---

## Quick verification command

After BI changes are deployed, compare against the canonical script. It runs
with `uv` — PEP 723 inline deps install automatically, and `kb_env` pulls every
secret from the store (set `$KB_DATABASE_URL` first):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py" \
    --start 2026-05-01 --end 2026-05-15 \
    --transport-flat-per-order 18 \
    --spot-check 0
```

The summary printed to stderr is the ground truth. BI should be ≤ 2% off
on every line (rounding, query-time lag), and 0% off on order counts.

Add `--sheet-id <id> --tab "<tab>"` to also push the result to a Google Sheet
(uses the `export-to-google-sheet` OAuth creds). Always dry-run without
`--sheet-id` first and validate the numbers before pushing.

---

## Files referenced (in the `core` plugin)

- `${CLAUDE_PLUGIN_ROOT}/scripts/grandia_pnl.py` — canonical implementation.
- Skill `grandia-pnl` — per-field formulas + source DBs + frozen identifiers.
- Skill `query-postgres` — the DB list + read-only MCP servers (`metrics`,
  `Grandia`, `AWBprint`, …).
- Skill `fetch-secret` — pulling a credential out of the SharedClaude `secrets`
  store (Shopify Admin GraphQL, TikTok / Google / Meta tokens, DPD keys).
