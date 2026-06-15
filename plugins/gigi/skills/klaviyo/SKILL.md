---
name: klaviyo
description: Klaviyo email/SMS analyst (read-only) — audit which lifecycle email/SMS flows a store has vs the 10 standard ecommerce flows (the GAP = revenue left on the table), list flows + campaigns, and check account setup. Use for "Klaviyo audit", "what email flows are we missing", "email marketing audit", "ce flow-uri de email avem/lipsesc", "audit Klaviyo", "abandoned cart / post-purchase / winback set up?". Per-store key in KB.
argument-hint: "gap --store esteban | flows | campaigns | account"
---

# klaviyo — email/SMS lifecycle analyst
> Author: Gigi.

We run email in-house. This audits what's set up in Klaviyo and — most usefully — **what's missing**: every standard lifecycle flow a store doesn't have is revenue it's not capturing (flows typically drive 30–40%+ of email revenue). Read-only.

```bash
uv run klaviyo.py account   --store esteban     # org / industry / timezone
uv run klaviyo.py flows     --store esteban     # all flows + status (live/draft) + trigger
uv run klaviyo.py gap       --store esteban     # the 10-flow lifecycle GAP audit (what's missing)
uv run klaviyo.py campaigns --store esteban     # recent email campaigns
```

## The 10-flow lifecycle checklist (`gap`)
Welcome · Abandoned Cart · Abandoned Checkout · Browse Abandonment · **Post-purchase** · Winback/Sunset · **Review request** · Replenishment · Birthday · VIP/Loyalty. Matched by EN + RO keywords against live/draft flow names. **Esteban (Jun 2026): 4/10 live; missing browse-abandon, post-purchase, review, replenishment, birthday, VIP** — post-purchase + review are the highest-value gaps for a fragrance brand.

## Credentials
Per-store Klaviyo **Private API Key** in KB: `KLAVIYO_<STORE>_PRIVATE_KEY` (e.g. `KLAVIYO_ESTEBAN_PRIVATE_KEY`). Read scopes only. Klaviyo requires a `revision` header — default `2024-10-15`, override via env `KLAVIYO_REVISION`. Currently only **Esteban** is connected; add other stores' keys to extend.

## How to use
Run `gap` first → it hands you the build list (which flows to create). Then brief whoever builds the flows (content from `core:esteban-articles` brand voice + the marketing-psychology/copy frameworks). Pair with RFM segments from our own order data (`metrics.orders`) for targeting. **Read-only**: this never edits flows or sends — it audits.

## Caveats / v2
- Gap match is keyword-based (EN+RO); a flow with an unusual name may be miscounted — eyeball `flows` output.
- v2: flow/campaign **revenue reporting** (Klaviyo `flow-values-reports` — revenue/open/click per flow, needs the Placed Order metric id) and **RFM tiers** (Active/Warm/At-Risk/Lapsed) computed from `metrics.orders` + DTC benchmark comparison (open 30%+, flow-revenue 30-40%+, unsub <0.3%).
