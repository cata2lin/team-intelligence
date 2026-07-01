---
name: metricool-social
description: >-
  Post organic content to ALL networks (TikTok + Instagram + Facebook + YouTube) for every ARONA
  brand through the Metricool API — one reel published to all platforms at once, sourced automatically
  from the team's Google Drive creative libraries, Gemini-vetted (quality + on-brand + no foreign
  watermark), captioned in RO, deduped, and scheduled. Includes a stock-verified HA-deals pipeline
  (only post products that are ACTIVE + in stock in the store), a persistent posting routine (launchd),
  a content library / vetting cache in the KB, and a performance "recipe" that learns the best posting
  hour / day / content / caption from Metricool analytics so posting gets better over time. Use whenever
  the user wants to post to social, fill a brand profile, schedule reels, source creative, or analyze
  what content performs.
---

# metricool-social — organic posting on all networks for all ARONA brands

Everything organic goes through **Metricool** (one flat-rate account, already paid). One post → TikTok +
Instagram + Facebook + YouTube. Replaces the old Meta-app path. Content is sourced from Drive, vetted by
Gemini, and deduped so refilling the queue never reposts.

## Runtime location
Live scripts + data run from **`~/Downloads/Scripturi/social-queue/`** (queue.json, posted_registry.json,
mc_brands.json, poster.log). This skill folder holds the canonical copies of the code.

## Secrets (KB, never echo values)
- `METRICOOL_API_TOKEN` — Metricool REST token (header `X-Mc-Auth`). userId ARONA = **3986721**.
- `GOOGLE_SA_LOOKER_SHEETS_JSON` — SA for Drive (drive.readonly, `.with_subject("gheorghe.beschea@overheat.agency")`).
- `GEMINI_API_KEY` — video vetting. `SHOPIFY_STORES_CSV` — deals stock check.

## Post to a brand (all networks)
```
uv run scripts/mc_post.py brands                 # list 14 brands + connected networks + blogId
uv run scripts/mc_post.py post --brand "Lab Noir" --media <blob_url> --text "<caption>" --publish
# default --network tiktok,instagram,facebook,youtube ; --when "YYYY-MM-DDTHH:MM:00" (default +20min);
# omit --publish for a DRAFT; --dry prints the payload with no write.
```
Metricool API: `POST /v2/scheduler/posts?userId&blogId`, one post with N `providers`. TikTok caption has
NO line breaks (auto-flattened); YouTube needs a `title` (first caption line); returns status only (no
permalink — link the profile via `tiktokUserProfileUrl`).

## Source content (Drive → Gemini vet → Blob → queue)
```
uv run scripts/pick_drive_brand.py "Nubra" "Lab Noir" --per 6      # per-brand CREATIVE folders
uv run scripts/pick_ha_deals.py --per 2                            # deals: only ACTIVE+in-stock HA SKUs
```
Drive libraries: **CREATIVE** `1pjDE3spDnpRuLUtTUzNUPx9XRyPA_gBP` (subfolder per brand; prefer the edited
`CREATIVE` subfolder, `MATERIALE BRUTE` = raw). **HA-1** `1CdUfqKisb22urOr8seDxik4wvEAXJQLw` + **HA-2**
`1z8kFoaV6NFcuR-THt_S5jqVGpcuauuvR` (one folder per `HA-####`, ready reels in `CREATIVE DENISA`).
Vetting keeps `ok_de_postat && pe_brand`; burned brand-own text is fine, only FOREIGN watermarks are rejected.
⚠️ **HA rule: verify the SKU is active + in stock in the store every time** (pick_ha_deals does this via Shopify).
⚠️ `--per N` value must not leak in as a brand (fixed) — and NEVER run a picker (writes queue.json) concurrently
with a posting script (race clobbers posted flags; reconcile from posted_registry.json if it happens).

## Routine + dedup
`social_queue_poster.py` drains the queue round-robin (launchd daily 10:00), all networks via Metricool.
Dedup = `posted_registry.json` (per brand+src) + content library in KB `files` table (`vetting_store.py`,
category='reel'). The registry is the source of truth if queue.json flags get clobbered.

## Recipe (learn what works → get better)
`recipe.py` pulls per-post performance and learns best hour/day/duration/content:
- TikTok: `GET /v2/analytics/posts/tiktok` (viewCount, engagement, **fullVideoWatchedRate/averageTimeWatched**, ...)
- Instagram: `GET /v2/analytics/reels/instagram` (views, engagement, reach, saved)
- ⚠️ params are `from`/`to` with a **timezone offset** (e.g. `+03:00`), NOT `start`/`end`.
First run (Jul-2026, 9.4k TikTok posts): **Saturday** best day (2.6×), 13-15h, **offer+urgency content wins**
("2+1 gratis", "reduceri 50%", "ultimele bucăți"). Feed this back into scheduling + selection.

## Brands (Metricool blogId)
Esteban 5123830 · George Talent 5123983 · Gento 5123995 · Nocturna 5124047 · Belasil 5124078 · Nubra 6077816 ·
Lab Noir 6490308 · ROSSI Nails 6490391 · Grandia 6490489 · Carpetto 6490523 · Ofertele Zilei 6490618 ·
Magdeal 6490623 · Reduceri bune 6490624 · Casa Ofertelor 6490626.

See memory `metricool-posting-system` for full context. The internal team also posts manually in Metricool —
coordinate (they should stop) so we don't double-post.
