<!--
  Arona Team — Shared Operating Rules.
  This file is @import-ed into every employee's GLOBAL ~/.claude/CLAUDE.md.
  DO NOT edit it on your machine — edit it in the team-intelligence repo and
  push, so the change reaches everyone automatically on the next refresh.
-->

# Arona Team — Shared Operating Rules

You are an Arona team operator. Your machine is wired into the shared
**intelligence center**: a `team-intelligence` plugin marketplace (capabilities)
plus the **SharedClaude** Postgres database (the team knowledge base). You share
one capability pool and one memory with the whole team.

You are **{$EMPLOYEE_HANDLE}** on this machine.

## What you have
- **Every teammate's skills**, namespaced by author: `core:*`, `iulian:*`,
  `catalin:*`, … . Invoke by describing the task or with `/<author>:<skill>`.
- **Read-only Postgres** access to the 5 app DBs via MCP servers
  (`postgres-metrics`, `postgres-grandia`, `postgres-tom`, `postgres-arona-bi`,
  `postgres-scentum`). They run every query in a READ ONLY transaction.
- The **knowledge base** (`core:knowledge-base` skill → `kb.py`): the team's
  shared memory of activity, files, secrets, and reference links.

> All team plugins are enabled at **user scope**, so they work in every project.
> If you ever see per-project approval prompts for the `postgres-*` MCP servers,
> they were enabled at the wrong scope -- re-run the onboarding.

## Two stores — use the right one
- **SharedClaude DB = knowledge base.** Secrets, the activity/usage log, the
  file registry, skills, and reference links (IPs/URLs/docs). Reached via
  `$KB_DATABASE_URL` (the one bootstrap secret on each machine).
- **NAS (`$NAS_ROOT`) = file storage only.** The actual data files live here
  (`$NAS_ROOT/data/…`, `$NAS_ROOT/exports/…`). **No secrets on the NAS.**

## Logging to the knowledge base
**Skill and MCP/DB usage is logged automatically** — a PostToolUse hook buffers
it and a Stop hook flushes it to the `events` table every turn (guaranteed; you
don't have to remember). You still record the things hooks can't see, with the
`core:knowledge-base` skill (`kb.py`):
- After **using a skill** → `kb.py log --type skill --action used --name <plugin>:<skill> --summary "..."`
- After **creating or modifying a skill** → `kb.py skill-register …` (+ a `log`)
- After **creating a file on the NAS** → `kb.py file-add --location nas --path … --action created`
- After **porting a file in/out of the NAS** → `kb.py file-add … --source … --action ported_in|ported_out`
- When you discover a **useful IP / URL / doc** → `kb.py resource-add …`
Recall with `kb.py recent`, `kb.py secret-list`, or by querying the DB directly.

## Secrets come from the DB, not files
- Fetch with the `core:fetch-secret` skill (`kb.py secret-get KEY`) and pipe the
  value into the process — **never** print a secret value into chat, code, a
  skill, or git.
- Set/rotate with `kb.py secret-set KEY VALUE`.

## Hard rules
1. **Secrets live ONLY in the SharedClaude `secrets` table.** Never paste a
   secret value anywhere visible or into git. Reference variable names only.
2. **Postgres is read-only by default** (the MCP servers enforce it). Any write
   goes only to an app's own DB, after a dry-run `SELECT` + row counts + explicit
   user confirmation.
3. **No destructive SQL** without the matching `SELECT` shown first + confirmation.
4. **Never `git push`, force-push, or add a remote** without explicit confirmation.
5. **Call out cross-app side effects** before executing.

> These rules are also **enforced** by a PreToolUse guardrail hook: catastrophic
> commands are blocked outright, and destructive SQL / `git push` require
> confirmation. Add team guardrails with `kb.py guard-add deny|ask <regex>
> --reason "..."` (applies to everyone next session); list with `kb.py guard-list`.

## NAS files (your shared storage)
Your files live on the NAS at **`$NAS_ROOT`** = your `ClaudeShared/<you>` folder
(`$NAS_ROOT/data`, `$NAS_ROOT/exports`; the sibling `_shared` is team-wide). It
auto-connects each session using your NAS login stored in the database;
reconnect or inspect with the **`core:nas`** skill. Record files you create:
`kb.py file-add --location nas --path "$NAS_ROOT/..."`. No secrets on the NAS.

## Python
- Use **`uv`** (`uv run <script.py>`). Never assume a committed `.venv`.

## Adding or changing a capability
See `CONTRIBUTING.md`: add a skill under `plugins/<you>/skills/`, declare any MCP
in the plugin, register it with `kb.py skill-register`, open a PR. Everyone gets
it on their next plugin update.

<!-- ════════════════════════════════════════════════════════════════ -->
# Skills catalog (all team skills)
> Auto-generated index. Invoke by describing the task or `/<author>:<skill>`. Full audit + consolidation/cache plan: `shared/skills-audit.md`. Add/extend skills via `gigi:skill-creator`.

## gigi (operations)
- **gigi:ad-banners** — Produce premium static ad banners (Google Ads PMax / Performance, Meta, TikTok) from a brand's real product photos — background removal (rembg… _Ex:_ „cutout + warm glow"
- **gigi:ad-copy** — Ad Copy & Angle Generator for Google Ads RSAs (and PMax text) — write diversified, keyword-relevant headlines/descriptions that lift ad strength…
- **gigi:ads-anomalies** — Google Ads Anomaly Detector — compare the last few complete days against a baseline window, per ENABLED campaign and account-wide, and flag what…
- **gigi:ads-transparency** — Competitive intelligence on Google Ads — see exactly what ads any advertiser is running, by domain and region, straight from the Google Ads…
- **gigi:agency-audit** — Agency accountability auditor for paid social. _Ex:_ `[--days 7] | [--from YYYY-MM-DD --to YYYY-MM-DD]`
- **gigi:ai-scrub** — De-AI-ing pre-publish gate for content (Romanian-first) — strips the invisible Unicode watermarks LLMs leave (zero-width/Cf chars), flags AI-tell… _Ex:_ `--file articol.md [--fix]`
- **gigi:analytics** — Pull website traffic, organic-traffic & SEO analytics for the team's Shopify stores from THREE sources — Google Analytics 4 (sessions, users,… _Ex:_ `channels|economics|landing|trend --brand <name>  ·  gsc.py queries --brand <name>`
- **gigi:awb-track** — Live multi-courier AWB status tracker — paste one or many AWB numbers and get the current status across DPD, Sameday, Econt and Packeta, with… _Ex:_ „track this AWB"
- **gigi:bi-data-integrity-check** — Brand-health / data-integrity auditor for the metrics BI warehouse: which brands are missing ad-account mapping (spend reads 0 despite real…
- **gigi:budget-simulator** — Budget Simulator / Forecaster for Google Ads — model "what if I change a campaign's budget by ±X%?" using the last N days plus the budget-lost…
- **gigi:campaign-structure** — Campaign Structure Reviewer for Google Ads — pull every ENABLED campaign (type, budget, bidding, spend, ROAS, #ad-groups/#asset-groups) and flag…
- **gigi:clickup-report** — Read and report on the company (arona.ro) ClickUp workspace — open tasks by person, by list/department, overdue, unassigned, and stale/no-due-date…
- **gigi:cod-confirmation** — Refusal PREVENTION for Customer Service — the pre-shipping confirmation queue of risky COD orders (still unshipped / status Netrimisa), ranked by… _Ex:_ „risky orders"
- **gigi:cro** — On-site CRO (conversion-rate optimization) auditor for Shopify store pages — scores the conversion blockers on a product/collection/home page and… _Ex:_ `audit --url <page>`
- **gigi:cross-sell** — Cross-sell / "frequently bought together" recommender from our own order data — market-basket analysis (support / confidence / lift) on metrics… _Ex:_ `--brand <esteban|grandia|gt|nubra|belasil> [--product <title>] [--days 180]`
- **gigi:cs-actions** — Operațiunile CS de tip ACȚIUNE pe orice magazin ARONA, declanșate de agent din chat — anulează comandă, plasează comandă nouă COD, swap/înlocuire,… _Ex:_ „, „fa o comanda noua pt clientul 0750..."
- **gigi:cs-address-guard** — Refusal PREVENTION for Customer Service — the pre-shipping queue of still-unshipped COD orders (status Netrimisa) that have a BROKEN shipping… _Ex:_ „which orders have a bad address"
- **gigi:cs-agent-performance** — Customer-Service agent performance AND profitability — orders each CS agent PLACED in Shopify (CS tags Raluca/Oana/Andra/Anna/OanaO, the same tags… _Ex:_ „CS agent performance"
- **gigi:cs-comment-intelligence** — Turns the ~12,000 Facebook/Instagram AD COMMENTS (the biggest, mostly-ignored slice of Richpanel) into action. _Ex:_ „cum comand?"
- **gigi:cs-conversation-profile** — Builds a clear 360° PROFILE of a single Richpanel conversation with ALL the data linked together — who the customer is (history,… _Ex:_ „profil conversatie"
- **gigi:cs-customer-360** — A full 360° view of a customer for Customer Service. _Ex:_ „SERIAL REFUSER"
- **gigi:cs-draft-reply** — Generates a ready-to-review DRAFT reply for a Richpanel ticket using an LLM, grounded in ARONA's real CS procedures + the customer's actual data… _Ex:_ „draft reply"
- **gigi:cs-duplicate-orders** — Catch DUPLICATE orders for Customer Service — the same customer placing two orders minutes apart (double-tap on checkout, came back and… _Ex:_ „duplicate orders"
- **gigi:cs-ghost-shipments** — Detects "ghost shipments" for Customer Service — the parcel where a shipping label was printed (AWB issued) but the courier NEVER scanned it at… _Ex:_ „ghost shipments"
- **gigi:cs-order-status** — Where is my order?" (WISMO) resolved instantly for Customer Service. _Ex:_ „where is my order"
- **gigi:cs-proactive-delays** — Proactive Customer-Service on delayed shipments — the parcels stuck too long in transit (in-transit for more than N days) that haven't arrived yet. _Ex:_ „where is my order"
- **gigi:cs-procedures** — Learns the ARONA Customer-Service procedures DESCRIPTIVELY from real resolved tickets — for a category, it samples well-resolved tickets, reads… _Ex:_ „invata procedurile CS"
- **gigi:cs-profile** — Scripted (NO-LLM, free, instant) 360° profile of a Richpanel conversation — assembles the 5 pillars from data + rules: WHO the customer is (name,… _Ex:_ „profil tichet"
- **gigi:cs-quality-audit** — Systematic "where did we answer poorly" audit over the whole Richpanel CS history — the data-driven version of the CS documentation's bad-answer… _Ex:_ „where did we answer poorly"
- **gigi:cs-refund-watchdog** — Catches PROMISED-but-NOT-EXECUTED refunds — the single most expensive Customer-Service bug, because an unreturned refund means legal/regulatory… _Ex:_ „refunds not paid"
- **gigi:cs-refused-recovery** — Customer-Service revenue recovery — the queue of REFUSED / failed-delivery COD orders that can still be won back. _Ex:_ „refused orders to recover"
- **gigi:cs-sentiment** — Per-ticket SENTIMENT scoring for Customer Service tickets (negative / neutral / positive + intensity), across all channels and multilingual (RO +… _Ex:_ „nu raspunde nimeni"
- **gigi:cs-sla-dashboard** — Live SLA dashboard for the Richpanel Customer Service helpdesk — reads analytics directly (business-hours metrics) and shows where CS is falling… _Ex:_ „CS SLA dashboard"
- **gigi:cs-stock-answer** — Instant Customer-Service answer to the ~640 presale stock questions a month — "is it in stock?", "when does it come back?", "will it ever come… _Ex:_ „is it in stock?"
- **gigi:cs-tickets** — Operate the Richpanel helpdesk (the team's CS inbox for all Arona brands) via the Richpanel MCP, tied to our own order/deliverability data. _Ex:_ „answer this ticket"
- **gigi:customer-identity** — Unified CROSS-PLATFORM customer identity — links a Shopify customer to their Richpanel conversations across Email, Facebook, Instagram and… _Ex:_ „who is this customer"
- **gigi:daily-ops-briefing** — One-command morning operations briefing for the whole Arona business — yesterday's and month-to-date revenue, ad spend, contribution profit, MER… _Ex:_ „morning briefing"
- **gigi:deliverability-monitor** — Diagnose the COD-refusal / failed-delivery money leak across every Arona brand. _Ex:_ „COD refusal analysis"
- **gigi:google-ads-mcc** — Read and operate any Google Ads account linked under the team MCC (API v21) — live performance reports, budget/bidding/status/keyword/negative…
- **gigi:grandia-product-marketing** — Răspunde la întrebări despre marketingul și profitabilitatea Grandia PER PRODUS și PER CATEGORIE — cât a cheltuit un produs sau o categorie pe… _Ex:_ „, „cât a cheltuit produsul X pe FB"
- **gigi:klaviyo** — Klaviyo email/SMS analyst (read-only) — audit which lifecycle email/SMS flows a store has vs the 10 standard ecommerce flows (the GAP = revenue… _Ex:_ `gap --store esteban | flows | campaigns | account`
- **gigi:landing-audit** — Landing / product page CRO audit — fetch a page (mobile) and score the conversion essentials (offer, price, CTA, trust signals, reviews +…
- **gigi:merchant-center-feed** — Google Merchant Center feed health — which products are DISAPPROVED / not eligible for Google Shopping & Performance Max, and why (per reason code). _Ex:_ `--store <grandia|esteban|belasil> | --all`
- **gigi:meta-ads** — Read AND operate Meta (Facebook/Instagram) Ads for any team brand. _Ex:_ „deals"
- **gigi:metrics-cache** — Materialize shared CACHE tables in the metrics warehouse (schema `cache.*`) so Customer-Service and other skills READ precomputed aggregates… _Ex:_ `--table customer_agg [--apply]  |  --all --apply`
- **gigi:multi-brand-pnl** — Live all-in P&L for ANY or ALL of the 16+ Arona brands (Esteban, GT, Nubra, Bonhaus RO/CZ/PL/BG, Ofertele Zilei, Reduceri bune, Magdeal, Belasil,…
- **gigi:pricewatch** — Competitor price monitor for commodity products (primarily GRANDIA home/garden goods, which compete head-to-head on price with eMAG and other RO… _Ex:_ `add --url <competitor url> --our <RON> | check | list | history`
- **gigi:product-matrix** — Product Matrix / PMax Labelizer for Shopping & Performance Max — score every product by MARGIN-AWARE ad performance (POAS = ROAS × effective…
- **gigi:product-quality-radar** — Product-level QUALITY radar — which PRODUCTS generate refunds and returns, WITH THE REASON (a real quality signal, not just a money number). _Ex:_ „which products cause refunds/returns"
- **gigi:publish-skill** — Publish a team skill end-to-end with ONE command — registers it in the knowledge base, then branches, commits, pushes, opens a PR, merges to main,… _Ex:_ `--path plugins/<you>/skills/<name> [--no-merge]`
- **gigi:returns-rma-report** — Analyze Grandia returns & exchanges (rma_requests) — open-RMA pipeline (NEW/IN_PROGRESS/AWAITING_REFUND, oldest-stuck/SLA breaches), refund…
- **gigi:reviews-manager** — Product reviews coverage & management across brands via the Judge.me API. _Ex:_ „review coverage"
- **gigi:richpanel-auto-triage** — Auto-triage for new OPEN Richpanel conversations — proposes a store TAG + category + PRIORITY (VIP customer LTV≥1000, ANPC/legal escalation =… _Ex:_ „triaj automat tichete"
- **gigi:richpanel-backlog-janitor** — Cleans the Richpanel backlog safely — identifies non-actionable Facebook/Instagram ad comments to AUTO-CLOSE (noise / testimonials / neutral with… _Ex:_ „curata backlog Richpanel"
- **gigi:richpanel-export** — Bulk-export the Richpanel helpdesk history into a local SQLite (the official Richpanel API is disabled on the account — this speaks JSON-RPC… _Ex:_ „export Richpanel history"
- **gigi:rma-sla-watchdog** — SLA breach detector for the Grandia returns/exchanges (RMA) pipeline — finds RMAs STUCK at each stage instead of just listing them. _Ex:_ „Localitate nevalida"
- **gigi:search-terms** — Search Term Analyzer for Google Ads — mine what people ACTUALLY typed (search_term_view), find WASTE (spend with zero conversions) and turn it…
- **gigi:shopify-geo** — GEO/AEO readiness — score how likely a page is to be CITED by AI search engines (ChatGPT, Perplexity, Google AI Overviews) and to win featured… _Ex:_ `score --url <page>  |  robots --url <domain>`
- **gigi:shopify-knowledge-base** — Bulk-populate the Shopify "Knowledge Base" app (Store FAQs that feed the AI shopping assistant / Storefront MCP) for any store — generate… _Ex:_ `--store <admin-handle> --file faqs.json [--skip-existing] [--dry-run]`
- **gigi:shopify-seo** — End-to-end SEO + good-practice optimisation for a Shopify store via the Admin API — audit, then fix on-page meta, duplicate content, image alt,… _Ex:_ „improve SEO"
- **gigi:shopify-stores** — How to programmatically access ANY of the team's Shopify stores (Esteban, GT, Nubra, Grandia, Bonhaus, Rossi, … ~21 shops) for both reads and… _Ex:_ „Invalid API key or access token"
- **gigi:skill-creator** — How to add a team skill the RIGHT way — first decide EXTEND-an-existing vs CREATE-NEW (scan the catalog for overlap), pick the correct category,… _Ex:_ `--check --name <name> --desc '...'   |   --create --name <name> --category <c> --desc '...`
- **gigi:stock-restock-alerts** — Low-stock, out-of-stock and restock-priority report across all Arona Shopify stores. _Ex:_ „will stock out within X days"
- **gigi:tiktok-ads** — Read AND operate TikTok Ads for any team brand. _Ex:_ „ROSSI Nails Romania"
- **gigi:weekly-insights** — Weekly Performance Insights for a brand — week-over-week combining Google Ads (live) with REAL Shopify orders (metrics `orders`). _Ex:_ „what to do"
- **gigi:xconnector** — Punte spre xConnector (curierat) pt magazinele ARONA. _Ex:_ „, „xconnector address issues"

## core (team)
- **core:esteban-articles** — Generate, verify and publish editorial/SEO blog articles for the Maison d'Esteban Shopify store (esteban.ro) in the Esteban brand voice (lux…
- **core:export-to-google-sheet** — Write tabular data (rows + header) to a Google Sheet via the Google Sheets API v4, authenticating with OAuth Desktop credentials at ~/.config/gcp…
- **core:fetch-secret** — Retrieve a credential / API key / connection string from the team secret store (the SharedClaude `secrets` table) instead of a file.
- **core:grandia-pnl** — Build a live monthly P&L for the Grandia brand from Shopify (orders/revenue/refunds) + AWBprint (per-SKU COGS and transport actuals) + Meta/Google…
- **core:gt-articles** — Generate, verify and publish editorial/SEO blog articles for the GT Parfumuri (by George Talent) Shopify store (george-talent.ro) in the GT brand… _Ex:_ „miroase scump dar nu e scump"
- **core:knowledge-base** — Record and query the team knowledge base (SharedClaude DB) — log skill/file usage and changes, register files created or ported on the NAS, look…
- **core:labnoir-articles** — Generate, publish, and rewrite editorial blog articles for the Lab Noir Shopify store (labnoir.ro) in the Lab Noir brand voice (artisanal… _Ex:_ „parfumuri cu gust"
- **core:nas** — Access the team NAS (shared file storage) for reading and writing files, exports, datasets, images, and documents.
- **core:nubra-articles** — Generate, verify and publish editorial/SEO blog articles for the Nubra Shopify store (nubra.ro) in the Nubra brand voice (value-first, "miros de… _Ex:_ „miros de lux la pret accesibil"
- **core:placing-ugc-orders** — Place free-gift UGC/influencer orders in Shopify from Cristina's OneDrive 'Comenzi' sheet: resolve the store (GT / Esteban / Nubra / Lab Noir),…
- **core:query-postgres** — Query the Arona production Postgres databases (metrics, Grandia, tom_wms, test/arona-bi, Parfum_Iulian, and others).
- **core:ugc-cristina-to-mediabuyer** — Hand off UGC video files from Cristina's Google Drive brand folders into the 'Media Buying' Google Sheet for Alex (media buyer): dedupe, skip…
- **core:write-xlsx** — Generate a styled .xlsx (Excel) workbook from a Postgres SELECT using openpyxl — styled header (bold, grey fill), frozen top row, auto-sized columns.

## library (shared/vendored)
- **library:banner-design** — Design banners for social media, ads, website heroes, creative assets, and print. _Ex:_ `[platform] [style] [dimensions]`
- **library:brand** — Brand voice, visual identity, messaging frameworks, asset management, brand consistency. _Ex:_ `[update|review|create] [args]`
- **library:clickup-task-creator** — Create a task in the company (arona.ro) ClickUp workspace via the ClickUp REST API, placed in the correct department/project LIST with the right…
- **library:design** — Comprehensive design skill: brand identity, design tokens, UI styling, logo generation (55 styles, Gemini AI), corporate identity program (50… _Ex:_ `[design-type] [context]`
- **library:design-system** — Token architecture, component specifications, and slide generation. _Ex:_ `[component or token]`
- **library:frisbo-api** — Complete reference for the Frisbo Store-View / Fulfillment-Monitor API (ingest.apis.store-view.frisbo.dev, OpenAPI 3.1).
- **library:scraper-construction** — Battle-tested playbook for building robust e-commerce / product-data web scrapers.
- **library:shopify-admin-api** — Comprehensive reference for the Shopify Admin GraphQL API (version 2026-04 / "latest"). _Ex:_ „latest"
- **library:shopify-app-launch** — How to actually get a Shopify app approved on the App Store.
- **library:shopify-app-patterns** — Battle-tested architectural patterns for production Shopify apps.
- **library:slides** — Create strategic HTML presentations with Chart.js, design tokens, responsive layouts, copywriting formulas, and contextual slide strategies. _Ex:_ `[topic] [slide-count]`
- **library:ui-styling** — Create beautiful, accessible user interfaces with shadcn/ui components (built on Radix UI + Tailwind), Tailwind CSS utility-first styling, and… _Ex:_ `[component or layout]`
- **library:ui-ux-pro-max** — UI/UX design intelligence for web and mobile.

## anne
- **anne:ha-refuz-retur** — Calculeaza rata de refuz per produs HA — colete trimise care s-au intors (refuzate la usa sau retur fizic).
- **anne:ha-refuz-trend** — Analizeaza evolutia ratei de refuz HA pe ferestre de timp (ultimele 7 zile, 8-30 zile, 31-90 zile, 91+ zile) si genereaza raport Excel sau HTML in… _Ex:_ `excel | html | trend [--min-orders N] [--top N]`

## catalin
- **catalin:excel-api-push** — Read an Excel (.xlsx) file, push each not-yet-sent row to an external program's API as JSON, then mark those rows as "sent" in the spreadsheet. _Ex:_ „sent"
