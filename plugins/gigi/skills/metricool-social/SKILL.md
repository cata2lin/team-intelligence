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

## Runtime = FULL VPS (no Mac dependency)
Everything runs on the **VPS `84.46.242.181` at `/root/social-queue/`** — poster AND pickers. There is NO
NAS dependency (content = Google Drive, a cloud API reachable from the VPS), so nothing needs the Mac.
- **Poster cron** (root crontab): `0 9 * * * /usr/bin/flock -n /tmp/social_poster.lock /root/social-queue/run.sh`
  → **09:00 Berlin = 10:00 RO** (VPS is on Berlin = RO−1h, constant offset all year; Debian cron has no reliable CRON_TZ).
- **`run.sh`** wrapper (the 3 gotchas that MUST be right, else it silently fails):
  1. `export PATH=/root/.local/bin:$PATH` — else the poster's `subprocess.run(["uv","run","mc_post.py"...])`
     dies with `FileNotFoundError: 'uv'` (cron's PATH lacks `/root/.local/bin`).
  2. `set -a; source /root/.kb_env; set +a` — exports `KB_DATABASE_URL` to child processes (token fetch).
  3. `export TZ=Europe/Bucharest` (belt; the scripts also use ZoneInfo, see below).
- **`/root/.kb_env`** (chmod 600) holds `KB_DATABASE_URL` + `EMPLOYEE_HANDLE=gigi` (transferred from Mac; VPS had no KB).
- **Deploy from Mac** with `vps.py` (paramiko, SSH pass from KB `PROFIT_SSH_HOST/USER/PASS`): `uv run vps.py put <local> <remote>` / `get` / `run "<cmd>"`.
- **Mac launchd is DISABLED** (`~/Library/LaunchAgents/com.arona.social-poster.plist.disabled-moved-to-vps`) —
  macOS TCC blocks scheduled jobs from `~/Downloads` (`operation not permitted: poster.log`), which is why it moved to VPS.

## Portability + timezone gotchas (hard-won)
- **`lib.py`** = portable `secret(key)` (direct `SELECT value FROM secrets` via `KB_DATABASE_URL`, plaintext) +
  `blob_upload(path)` (Vercel Blob). Pickers + mc_post import this — NO hardcoded Mac `kb.py`/`social_post` paths.
- **⚠️ TIMEZONE**: VPS clock = Berlin (RO−1h). `datetime.now()` labeled `Europe/Bucharest` = **1h in the past** →
  Metricool 400 `"Given datetime cannot be in the past"` for near-future slots. FIX: mc_post + poster use
  `datetime.now(ZoneInfo("Europe/Bucharest"))` for `when`/slots (independent of system TZ).
- **Poster resilience**: if the all-4-networks post fails, it retries WITHOUT youtube (a reel YT-Shorts rejects
  won't kill TikTok/IG/FB). Deals brands auto-skip youtube (not connected).

## Refill the queue — AUTOMAT (cron), plus manual la nevoie
**Coada se umple singură** — reumplerea a fost ultimul pas manual din pipeline, acum e automatizată.
`refill_queue.py` (wrapper `refill.sh`) rulează din crontab root:
`30 4 * * * /usr/bin/flock -n /tmp/social_poster.lock /root/social-queue/refill.sh` →
**04:30 Berlin = 05:30 RO**, cu **ACELAȘI lock ca posterul** (10:00 RO), ca să nu scrie niciodată
simultan în `queue.json`. Pentru fiecare brand din rotație cu **sub `--min` (3)** posturi nepostate
cheamă pickerul potrivit (Drive pt branduri, HA pt deals) și-l duce la **`--target` (5)**.
Log: `/root/social-queue/refill.log`. Test sigur oricând: `uv run refill_queue.py --dry`.
Un brand fără conținut în folder = **no-op ieftin** (0 candidați, zero cost Gemini) → se auto-repară
singur când apare conținut, fără intervenție.

Manual (top-up punctual):
`uv run vps.py run '... cd /root/social-queue; uv run pick_drive_brand.py "Nubra" "Esteban" --per 4'` — the
picker appends straight to the VPS `queue.json`. (Mac refill still works via **pull → pick → push**: `vps.py get`
queue.json → run picker locally → `vps.py put` back, so VPS posted-flags aren't clobbered.)

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
**Sursă explicită per brand:** `BRAND_FOLDERS` din `pick_drive_brand.py` mapează brandul → folder Drive —
obligatoriu când numele din rotație nu se potrivește cu folderul: **`GT`** → „5. GEORGE TALENT" (altfel
găsea 0 la infinit), **`Nocturna`** și **`Rossi`** (n-au folder sub CREATIVE), **`Lab Noir`** → **„UGC Cristina"**
(UGC de la creatoare, cu subfoldere pe luni — se intră recursiv). Altfel se caută după nume sub CREATIVE.
Ad-hoc: `--folder <ID>`. Când folderul e explicit (curatoriat de om), gate-ul `pe_brand` nu mai blochează.

⚠️ **FĂRĂ BARE NEGRE — 2 straturi** (înainte treceau clipuri care ieșeau cu bare):
1. **filtru dur pe metadata Drive** — doar vertical real (`h > w` ȘI `w/h ≤ 0.65`, adică 9:16); respinge
   4:5 / pătrat / 16:9, cărora platformele le pun bare. (La cenzus: Belasil 23, Nocturna 22, Gento 5 astfel.)
2. **verificare vizuală** — Gemini întoarce `bare_negre` (letterbox/pillarbox **ars în imagine**, ex. un 16:9
   pus pe pânză verticală) → clipul e respins. Ăsta e stratul care contează când formatul e deja 9:16.

Plus **dedupe** pe nume normalizat + durată (același clip urcat de 8 ori cu nume ușor diferite).
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
