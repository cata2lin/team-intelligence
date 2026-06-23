# Provisioning — what's still needed for every skill to run "from the get-go"

> **Purpose.** The repo + the SharedClaude DB already make the whole marketplace self-installing
> (`git clone` → `./install.ps1`/`.sh` installs uv/node/gh, enables all plugins + the 6 MCP servers,
> pulls every secret from the DB). This file lists the **handful of things that are NOT in the repo**
> and still have to be provided once (by a skill's owner or the admin) before *every* skill is usable
> by *anyone*. Secret **values never live here or in git** — only key names.
>
> Audited 2026-06-22 against all **105 skills**. Status: **~99 usable today**, **1 needs a secret**,
> **5 blocked on owner-supplied files**. Companion detail: `c:/tmp/skill_runnability_report.md`.

---

## 0. TL;DR — the only open items
| # | Item | Owner | Unblocks |
|---|---|---|---|
| 1 | Commit the `blog-rollout/*` pipeline + `articles/{esteban,gt,nubra}.json`; de-hardcode the Mac `BASE` path | **Gigi** | `core:esteban-articles`, `core:gt-articles`, `core:nubra-articles` |
| 2 | Port the claudekit design skills into the marketplace convention (or commit the missing sibling skills) | **Catalin** | `library:banner-design`, `library:design` |
| 3 | `kb.py secret-set SMARTBILL_STORES <value>` | **Gigi** | `gigi:cs-actions` (invoice subcommand) |
| 4 | Decide VPS access: reuse `PROFIT_SSH_*` (in DB) like `ha-grandia-pnl`, **or** ship the SSH key | **Gigi/admin** | ~10 CS/profitability skills |
| 5 | One-time Google + Microsoft OAuth consents (tokens then shared via DB) | **Catalin/admin** | 3 Google-Sheet/Drive + 1 OneDrive skill |

Everything below expands these. Nothing else is required — all other secrets, DSNs, NAS and Sheets are already in the DB.

---

## 1. Files missing from the repo (BLOCKED skills)

### 1a. Blog article pipeline → `core:esteban-articles`, `core:gt-articles`, `core:nubra-articles`
These three crash: the build pipeline and the article inputs are not committed, and the publish
script reads a hardcoded path on Gigi's Mac.
**Gigi must commit** (today only at `/Users/gheorghebeschea/Downloads/Scripturi/blog-rollout`):
- `blog-rollout/build_index.py`, `build_catalog.py`, `process_results.py`, `process_seo.py`,
  `articles_workflow.js`, `seo_workflow.js`
- `articles/esteban.json`, `articles/gt.json`, `articles/nubra.json`
- **and** replace `BASE = /Users/gheorghebeschea/Downloads/Scripturi/blog-rollout` with a repo-relative
  path (or `$NAS_ROOT`).
> `core:labnoir-articles` already works (articles embedded in the script) — same pattern fixes the other three.

### 1b. Library design skills → `library:banner-design`, `library:design`
Imported from claudekit and never fully ported to the marketplace convention.
- `banner-design` calls sibling skills that don't exist here: `ai-artist`, `ai-multimodal`,
  `frontend-design` (+ scripts `gemini_batch_process.py`, `screenshot.js`, `inject-brand-context.cjs`).
- `design` invokes `python3 ~/.claude/skills/design/...` (a path that doesn't exist in the plugin
  layout) with a manual `pip install google-genai pillow`, instead of `${CLAUDE_PLUGIN_ROOT}` + `uv` + PEP 723.
**Fix (Catalin):** port to `${CLAUDE_PLUGIN_ROOT}` paths + `uv run` + inline deps, or commit the missing
sibling skills. The underlying scripts are fine; only the wiring is wrong. *(Offered — can be done on request.)*

### 1c. Minor (non-blocking)
- `core:grandia-pnl` ships 2 optional diagnostic scripts (`diag_shopify_orders.py`, `_diag_quick.py`)
  that `import requests` with no PEP 723 block — they crash only if run directly; the main P&L path is fine.

---

## 2. Credentials still needed in the DB (`kb.py secret-set KEY VALUE`)

### Required (1)
| Key | Unlocks |
|---|---|
| `SMARTBILL_STORES` | `gigi:cs-actions` invoice subcommand (all other cs-actions ops already run) |

### Optional (skills already work via a fallback — add only for the upgrade)
| Key | Effect | Skills |
|---|---|---|
| `ANTHROPIC_API_KEY` | use Claude instead of the present OpenAI fallback | `cs-procedures`, `cs-sentiment`, `cs-conversation-profile`, `cs-draft-reply` |
| `YOUTUBE_API_KEY` | adds the YouTube listening source (else falls back to the present ads key) | `gigi:social-listening` |
| `REDDIT_CLIENT_ID/SECRET`, `IG_*` | adds Reddit / Instagram listening (else those channels show n/a) | `gigi:social-listening` |
| `PSI_API_KEY` | dedicated PageSpeed key (stops borrowing the ads-API quota) | `gigi:landing-audit` |

> Every other secret a skill references is already present (134 keys in the DB).

---

## 3. Access & one-time authentication (cannot live in git)

### 3a. SSH to the VPS `root@84.46.242.181` — ~10 skills
These read live data over key-based SSH with **no stored credential**, so a teammate without the key
can't run them: `cod-confirmation`, `cs-address-guard`, `cs-ghost-shipments`, `cs-order-status`,
`cs-proactive-delays`, `cs-profile`, `cs-refused-recovery`, `cs-stock-answer`, `customer-identity`,
`metrics-cache` (ETL). **Pick one (Gigi/admin):**
- **Preferred / zero-touch:** convert these to SSH via paramiko using the `PROFIT_SSH_HOST/USER/PASS`
  creds **already in the DB** (exactly how `gigi:ha-grandia-pnl` works) — no per-machine key.
- Or distribute the VPS SSH private key to each teammate and document it in onboarding.

### 3b. Google OAuth Desktop (per-user) — 2 skills
`core:export-to-google-sheet` and `core:ugc-cristina-to-mediabuyer` use a per-user OAuth Desktop file
at `~/.config/gcp/oauth-client.json` + a one-time browser consent (the token = that person's Google
account). **Provision once:** drop the shared OAuth client + complete consent; the token can then be
stored in the DB and reused. *(Most other Google-Sheet/Drive skills already use the service account
`GA4_SA_JSON` / `GOOGLE_OAUTH_TOKEN_JSON` from the DB and need nothing.)*

### 3c. Microsoft / OneDrive (one-time) — 1 skill
`core:placing-ugc-orders` reads Cristina's OneDrive "Comenzi" sheet via MSAL. Run
`microsoft_auth.py --login` once; the token is cached in the DB key `MS_MSAL_CACHE` and shared.

---

## 4. Accessible data resources (all provisioned — reference map)

### 4a. Databases
| Database | DSN secret (in DB) | MCP server | What it holds | Used by |
|---|---|---|---|---|
| metrics | `DATABASE_URL_METRICS` | ✅ `postgres-metrics` | BI warehouse: ad spend, orders, `cache.*` | most CS/BI/ads skills |
| grandia | `DATABASE_URL_GRANDIA` | ✅ `postgres-grandia` | Grandia store, RMA, profitability | grandia-*, returns/rma, product-quality |
| tom_wms | `DATABASE_URL_TOM` | ✅ `postgres-tom` | warehouse / WMS | tom, stock |
| arona-bi | `DATABASE_URL_ARONA_BI` | ✅ `postgres-arona-bi` | competitive intel (50+ sites, `mv_best_sellers_ranked`, 213k products) | `sourcing-radar`, pricewatch |
| scentum | `DATABASE_URL_SCENTUM` | ✅ `postgres-scentum` | Scentum app | scentum-specific |
| AWBprint | `DATABASE_URL_AWBPRINT` | ❌ DSN only | **delivery/transport source of truth** (Frisbo/AWB) | fulfillment-analytics, deliverability, cross-sell, product-sales, awb-track |
| inventorysync | `DATABASE_URL_INVENTORYSYNC` | ❌ DSN only | inventory-sync app | sync tooling |
| mattermost | `DATABASE_URL_MATTERMOST` | ❌ DSN only | Mattermost chat | chat integration |
| trendyol | `DATABASE_URL_TRENDYOL` | ❌ DSN only | Trendyol marketplace | trendyol profitability (VPS) |
| profitability.db | via SSH `PROFIT_SSH_*` | — SQLite (VPS) | `profit_orders` engine (`/root/Scripturi/data/`) | ha-grandia-pnl + CS profit skills |
| product_analytics.db | via SSH | — SQLite (VPS) | live stock | cs-stock-answer |
| richpanel_tickets.db | local (from `richpanel-export`) | — SQLite | Richpanel history export | cs-comment-intelligence, richpanel-* |

> **Optional enhancement:** AWBprint/inventorysync/mattermost/trendyol have no MCP server — skills reach
> them via DSN directly (works), but there's no ad-hoc MCP querying. Add a `postgres-awbprint` MCP server
> to `core/plugin.json` if you want AWBprint queryable interactively. Not required for any skill.

### 4b. NAS  (`\\$NAS_HOST\$NAS_SHARE\$NAS_BASE\<handle>`, keys `NAS_HOST/SHARE/BASE` in DB)
Per-user folder `ClaudeShared/<you>/{data,exports}` + team-wide `_shared`. Auto-connected by the
SessionStart hook. Used by `ad-banners`, `image-gen --nas`, `meta-ads` (nas_creatives), all exports.
> Caveat: a few scripts read env `NAS_ROOT` while the DB key is `NAS_BASE` — onboarding sets `NAS_ROOT`,
> so it's fine on an onboarded machine; the NAS copy step skips gracefully if unset.

### 4c. Google Drive
| Drive resource | Access | Used by |
|---|---|---|
| Brand video shared drive (`drive/folders/0AKkB0AV7_E-bUk9PVA`) | `GOOGLE_OAUTH_TOKEN_JSON` / `GA4_SA_JSON` (DB) | brand video sourcing |
| Cristina's UGC brand folders | per-user `~/.config/gcp` combined sheets+drive token (see 3b) | `core:ugc-cristina-to-mediabuyer` |

### 4d. Spreadsheets (Google Sheets unless noted)
| Sheet | ID / location | Auth | Used by |
|---|---|---|---|
| Mapping / "CPA și financiar" | `MAPPING_SHEET_ID` (DB) | SA / OAuth token (DB) | `attribution-audit` |
| DPD nomenclator | `NOMENCLATOR_SHEET_ID` (DB) | DB | `meta-ads`, `tiktok-ads`, `metrics-cache` |
| Google Ads sheet | `GADS_SHEET_ID` / `GADS_SHEET_NAME` (DB) | `GOOGLE_SA_LOOKER_SHEETS_JSON` | ads reporting |
| "Raport Zilnic" daily report | bound Apps Script | looker-sheets SA + DWD | `apps-script-deploy` |
| "Containere care vin" (Tom recepții) | `docs.google.com/…/1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU` | DB token | container intake |
| "Valori inventar lunar / magazin" | `docs.google.com/…/1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0` | DB token | inventory valuation |
| "Media Buying" (Alex) | resolved in skill | DB token | `core:ugc-cristina-to-mediabuyer` |
| "Comenzi" (Cristina) | **OneDrive**, not Google | `MS_MSAL_CACHE` (DB, see 3c) | `core:placing-ugc-orders` |

---

## 5. Plugin / MCP gaps (not blockers)
- **Empty plugins:** `iulian`, `adriana`, `andreea` ship only a `plugin.json` (no skills). Enabling them
  does nothing — add a skill each, or hold them out of the marketplace until they have one.
- **MCP servers:** all 6 are auto-configured (5 read-only Postgres in `core` + `chrome-devtools` in
  `library`). No skill needs an MCP server that isn't already shipped. (See 4a for the optional AWBprint MCP.)

---

## 6. From-the-get-go checklist
**Already automatic on `git clone` + `./install` (no action):** uv/node/gh install · marketplace + all 8
plugins enabled · 6 MCP servers · every secret/DSN/Sheet/NAS pulled from the DB · team rules + 105-skill
catalog `@import`ed.

**Still owner-supplied (this file):** §1 article files + design-skill port · §2 `SMARTBILL_STORES` ·
§3 VPS-SSH decision + the two OAuth/MS consents.

Once §1–§3 are done, **all 105 skills are usable by anyone straight after onboarding.**
