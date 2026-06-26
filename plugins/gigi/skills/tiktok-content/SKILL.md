---
name: tiktok-content
description: >-
  ORGANIC TikTok content engine for the ARONA brands (Romanian DTC, mostly
  COD/ramburs) — strategy, hook/script writing, trend + competitor research,
  posting automation, and organic analytics. Use for "tiktok content", "tiktok
  script", "tiktok hook", "tiktok trend", "post to tiktok", "tiktok organic",
  "tiktok strategy", "fă-mi un script de tiktok", "ce postăm pe tiktok", "idei
  de tiktok pentru <brand>", "hook pentru tiktok", "calendar de conținut
  tiktok", "ce trenduri sunt pe tiktok acum", "ce postează competiția pe
  tiktok", "programează postare tiktok", "analiză organic tiktok", "ce a
  performat pe tiktok". This is the ORGANIC counterpart to gigi:tiktok-ads
  (which is PAID Ads API only). Covers Maison d'Esteban / GT Parfumuri / Nubra
  (parfumuri), Grandia / Bonhaus / Ofertele Zilei / Magdeal (home-deals),
  Carpetto (covoare), Gento (genți), Belasil (curățenie rufe), Rossi Nails
  (unghii DIY). Does NOT duplicate gigi:tiktok-ads (paid), gigi:competitor-ads
  (paid creative intel), or gigi:social-listening (mention search) — it reuses
  them.
tools: Read, Write, Bash, WebSearch, Edit, Grep, Glob, Agent
---

# gigi:tiktok-content — ORGANIC TikTok orchestrator

This skill runs the **ORGANIC** side of TikTok for every ARONA brand: it writes
the strategy, the hooks and the full RO scripts, researches live trends and what
the competition posts organically, **publishes** the videos through the TikTok
Content Posting API, and reports **organic** performance (views, watch-time,
follows, profile-visits) from the Display API.

It is an **orchestrator**. Each capability lives in its own reference file or
execution script — read the one the task needs, don't reimplement it inline.

> **Scope boundary — read this first.** This skill is *organic content + posting
> + organic analytics*. It is NOT paid media and it does NOT re-implement skills
> we already have. Before doing anything, check the reuse table below.

## What this is NOT (reuse, don't duplicate)

| If the ask is really about… | Use this existing skill, not me |
|---|---|
| **Paid** TikTok — spend, ROAS, CPA, campaigns, ad-level reports, mutations | **`gigi:tiktok-ads`** (TikTok **Ads** API, read + operate) |
| What **ads** the competition runs + best-creative ranking + vision analysis | **`gigi:competitor-ads`** |
| Brand **mentions / buzz** across RO web & social | **`gigi:social-listening`** |
| **Generating** a video creative (cinematic / montage) | **`gigi:pmax-video`** |
| **Generating** a still image / product shot | **`gigi:image-gen`** |
| Moving **UGC** files from Cristina's Drive into the media-buying sheet | **`core:ugc-cristina-to-mediabuyer`** |

My job starts where those stop: turning a product + a goal into a **native,
organic TikTok** that a human (or our UGC creators, managed by Cristina) can film
or that we assemble from existing UGC, then scheduling it and measuring the
**organic** result. Paid amplification of a winning organic post = hand the
post/ad code to `gigi:tiktok-ads`.

## The 4 capability areas → where they live

| # | Capability | Reference / script | Use when |
|---|---|---|---|
| 1 | **Strategy + scripts** — content pillars, posting cadence, calendar, hook + full RO script writing per brand | `references/content-strategy.md`, `references/hooks-scripts.md` | "ce postăm", "calendar", "script", "hook", "idei de conținut" |
| 2 | **Trend + competitor research** — live trending sounds/formats/hashtags, what competitors post organically, adapt to our products | `references/trend-research.md` (+ `WebSearch`, reuse `gigi:competitor-ads` for paid) | "ce trenduri", "ce postează competiția", "ce sunet folosesc", "format viral" |
| 3 | **Posting automation** — publish / schedule a video to a brand TikTok via the Content Posting API | `execution/tiktok_post.py` (+ `references/posting-analytics.md`) | "postează", "programează", "urcă pe tiktok" |
| 4 | **Organic analytics** — per-video and account organic metrics (views, watch-time, completion, follows, profile visits), what performed | `execution/tiktok_organic.py` (+ `references/posting-analytics.md`) | "analiză organic", "ce a performat", "câte views", "raport organic" |

Reference files are cross-linked: strategy points to hooks, hooks point to
trends, trends point back to strategy, and both posting + analytics share
`references/posting-analytics.md` (the API + token reality lives there).

## Command router

| Intent (what the user said) | Do this |
|---|---|
| "fă-mi un script / hook de tiktok pentru <brand>" | Gather context (below) → read `references/hooks-scripts.md` + the brand block in `references/content-strategy.md` → write hook variants + full RO shot-list script |
| "ce postăm pe tiktok / calendar / strategie <brand>" | Read `references/content-strategy.md` → produce pillars + weekly cadence + a 2-week calendar with concrete post ideas |
| "ce trenduri sunt pe tiktok acum" / "ce sunet/format viral" | Read `references/trend-research.md` → `WebSearch` for current RO TikTok trends → map each to a concrete ARONA post |
| "ce postează competiția pe tiktok (organic)" | Read `references/trend-research.md` → identify competitor handles → research their organic posts; for their **ads** defer to `gigi:competitor-ads` |
| "postează / programează pe tiktok" | Read `references/posting-analytics.md` → run `execution/tiktok_post.py` (dry-run first, then `--apply`) |
| "analiză / raport organic tiktok" / "ce a performat" | Read `references/posting-analytics.md` → run `execution/tiktok_organic.py` for the brand + window |
| "amplificăm postarea asta cu buget" | This is PAID → hand off to `gigi:tiktok-ads` |
| "generează video / imagine pentru postare" | Defer to `gigi:pmax-video` (video) / `gigi:image-gen` (image), then come back to post |

## Context-gathering protocol (ALWAYS before producing content)

Never write a script or a calendar blind. Resolve three things first — ask only
what you can't infer:

1. **Which brand?** This sets voice, vertical and offer mechanics. If the user
   named a product, infer the brand from it. The brand map:

   | Brand | Domain | Vertical | Organic voice (TikTok) |
   |---|---|---|---|
   | Maison d'Esteban | esteban.ro | Parfumuri | lux accesibil — "experiență de designer la o fracțiune din cost", elegant, aspirațional dar prietenos |
   | GT Parfumuri (by George Talent) | george-talent.ro | Parfumuri | influencer energy — "miroase scump dar nu e scump", George ca față, GRWM/POV, energic |
   | Nubra | nubra.ro | Parfumuri | value-first — "miros de lux la preț accesibil", "plătești pentru esență nu pentru ambalaj", direct |
   | Grandia | grandia.ro | Home/deals | produs util la preț mic, demo "uite ce face", satisfying/oddly-satisfying |
   | Bonhaus / Casa Ofertelor | casaofertelor.ro | Home/deals | ofertă, "merită banii", before/after casnic |
   | Ofertele Zilei | — | Home/deals | urgență de ofertă, "azi la preț de…" |
   | Magdeal | magdeal.ro | Home/deals | gadget util, demo rapid |
   | Carpetto | — | Covoare | transformare cameră, "covorul care schimbă tot", satisfying la curățat |
   | Gento | — | Genți | fashion haul, "geanta care merge cu tot", styling |
   | Belasil | — | Curățenie rufe (detergent/balsam) | rezultat vizibil, miros, "rufe ca la hotel", satisfying laundry |
   | Rossi Nails | — | Unghii (polygel + pudră) | DIY acasă, tutorial pas-cu-pas, "unghii de salon acasă", before/after |

2. **Goal?** Pick the lens:
   - **Awareness / reach** → trend-led, hook-first, broad, sound-driven (read `references/trend-research.md`).
   - **Sales / conversie** → demo + offer + COD reassurance ("plată ramburs, livrare rapidă"), clear CTA, comment-pinned offer.
   - **UGC / creator brief** → output a brief Cristina's creators can film (props, shots, lines), not a finished edit.

3. **Format?** Confirm or choose: GRWM, POV, demo/unboxing, before/after,
   tutorial, "things I wish I knew", green-screen reaction, satisfying/ASMR,
   haul, day-in-the-life. The format dictates the script template in
   `references/hooks-scripts.md`.

If brand + goal + format are clear, proceed without asking. If a script needs
real data (best-sellers, price, COGS for margin-safe discounting, real reviews
for a "what people say" hook), pull it: `gigi:product-sales` for units,
`gigi:shopify-stores` for live price/variants, `gigi:reviews-manager` for review
quotes.

## Hard rules for ARONA TikTok content

- **Romanian-first**, native and casual — TikTok rewards authentic over polished.
  Write the way a creator actually talks, not ad copy.
- **COD-aware.** When the goal is sales, surface the real mechanic: **plată
  ramburs (la livrare), livrare rapidă 24–48h**, retur conform politicii. Don't
  promise free returns we don't offer (perfumes desigilate = retur refuzat).
- **No unsubstantiated superlatives** — "cel mai bun din lume", "garantat",
  medical/"vindecă" claims are Google/legal-sensitive and get content pulled.
  Use concrete, defensible phrasing ("miros care ține 12h+", "preț de la X lei").
  Run finished captions/scripts through **`gigi:ai-scrub`** before publishing.
- **Authentic UGC > polished ad.** Prefer a creator filming on a phone over a
  rendered montage. Reserve generated video (`gigi:pmax-video`) for when no
  footage exists.
- **Hook in the first 1–2s or it's dead.** Every script must open with a scroll-
  stopper (see the hook bank in `references/hooks-scripts.md`).

## Infra & API reality (details in `references/posting-analytics.md`)

- **Ads tokens ≠ organic tokens.** `metrics.tiktok_access_tokens` holds the
  **Ads API** tokens (shared business accounts split per brand by campaign
  token) — those drive `gigi:tiktok-ads`. **Organic posting + organic analytics
  use the TikTok Content Posting API + Display API**, which need the
  `video.publish` / `video.upload` / `video.list` scopes — a **separate OAuth /
  re-auth** per brand TikTok account. Secrets land in the SharedClaude KB via
  `kb.py` (`core:fetch-secret` to read, `kb.py secret-set` to store). If the
  organic token/scope is missing, `references/posting-analytics.md` has the
  re-auth runbook; until then, posting falls back to a **scheduled-draft export**
  a human approves in the app.
- **Python via `uv`** with PEP-723 inline deps in every script. **No paid SaaS** —
  scripts call TikTok's own APIs and our own DBs/KB only.
- Log usage to the KB after a run:
  `kb.py log --type skill --action used --name gigi:tiktok-content --summary "..."`.

## Typical flows

- **"Dă-mi 5 idei + un script pentru Nubra, scop vânzări"** → context (Nubra /
  sales / pick format) → `references/content-strategy.md` (Nubra pillars) +
  `references/hooks-scripts.md` (hook bank + sales template) → 5 hook-led ideas,
  1 full RO shot-list script with COD CTA → `gigi:ai-scrub` → done.
- **"Ce trend putem fura azi pentru Grandia"** → `references/trend-research.md`
  + `WebSearch` (current RO trends) → 3 trending sounds/formats mapped to real
  Grandia demo products → one ready script.
- **"Programează clipul ăsta pe TikTok GT pentru mâine 18:00"** →
  `references/posting-analytics.md` → `execution/tiktok_post.py --brand gt
  --video <path> --caption … --schedule "2026-06-27T18:00" ` (dry-run, then
  `--apply`).
- **"Cum a mers organic pe Esteban luna asta"** →
  `execution/tiktok_organic.py --brand esteban --since 2026-06-01` → per-video
  views/watch-time/follows + what to double down on.

## Reference index

- `references/content-strategy.md` — per-brand content pillars, posting cadence,
  calendar templates, account positioning. ↔ links to hooks-scripts & trends.
- `references/hooks-scripts.md` — hook bank (by goal/format) + full RO script
  templates (GRWM, demo, before/after, tutorial, POV…) with ARONA examples. ↔
  links to content-strategy & trend-research.
- `references/trend-research.md` — how to find live RO TikTok trends (sounds,
  formats, hashtags), competitor organic handles per vertical, and how to adapt
  a trend to our products. ↔ defers to `gigi:competitor-ads` for paid creative.
- `references/posting-analytics.md` — TikTok Content Posting API + Display API:
  auth/scopes/re-auth runbook, token reality, endpoints, the post + analytics
  script contracts. Shared by both execution scripts.

## Execution index

- `execution/tiktok_post.py` — publish or schedule a video to a brand's TikTok
  (Content Posting API). Dry-run by default; `--apply` to publish. Falls back to
  scheduled-draft export when organic scope is absent.
- `execution/tiktok_organic.py` — pull organic performance (account + per-video)
  from the Display API for a brand and date window.