---
name: skill-creator
description: How to add a team skill the RIGHT way — first decide EXTEND-an-existing vs CREATE-NEW (scan the catalog for overlap), pick the correct category, reuse shared libs instead of duplicating, scaffold a conventional SKILL.md, then register in the KB and publish. Use when someone says "make a skill for X", "create/add a skill", "should this be a new skill", "how do I ship a skill to the team".
argument-hint: "--check --name <name> --desc '...'   |   --create --name <name> --category <c> --desc '...'"
---

# skill-creator — decide, scaffold, register, publish
> Author: **Gigi**. The front door for new team capabilities. Pairs with `shared/skills-audit.md` (overlap map) and `gigi:publish-skill` (shipping).

## Step 0 — EXTEND or NEW? (do this first, always)
The team already has overlap (93+ skills). A new skill is the last resort, not the default.
```bash
uv run scripts/new_skill.py --check --name "refused orders queue" --desc "list COD orders that came back, by product"
```
It prints the most similar existing skills + an EXTEND recommendation when overlap is high.
**Prefer extending** an existing skill (add a subcommand/mode) when the data + audience overlap.
Recent consolidations that prove the pattern (see audit): the Google-Ads sub-skills → one suite;
the 4 `*-articles` → one `articles --store`; `cro`+`landing-audit`; the CS profile/refusal/watchdog families.

## Categories (where a new skill falls)
| key | domain |
|---|---|
| `cs` | Customer Service / Richpanel — tickets, 360 profiles, refusal-prevention, watchdogs |
| `ads` | Paid ads — Google / Meta / TikTok (operate + analyze) |
| `pnl` | P&L / profitability — revenue − COGS − transport − adspend |
| `shopify` | Store ops & catalog — Admin API, products, stock, orders, Knowledge Base/FAQ |
| `seo` | SEO / AEO / content — analytics, GEO, articles, CRO |
| `fulfillment` | Couriers / AWB / returns / RMA — DPD/Sameday/Econt/Packeta, Frisbo, xConnector |
| `reporting` | BI / dashboards / morning briefings / data-integrity |
| `creative` | Design / banners / slides / brand assets |
| `infra` | Shared libs, reference docs, KB, files/exports, scaffolding |

## Conventions (what a good skill looks like)
- **Folder:** `plugins/<author>/skills/<name>/` with `SKILL.md` + `scripts/`.
- **SKILL.md frontmatter:** `name`, `description` (pack it with real trigger phrases — that's how it's matched), optional `argument-hint`.
- **Scripts:** `uv run` self-contained (`# /// script` header with deps). No committed `.venv`.
- **Reuse shared libs — never duplicate** (the audit found `_clean_dsn` copied in ~40 files):
  **`core/scripts/arona_pg.py`** is the canonical Postgres/secret helper — `secret()` (env-first + KB),
  `clean_dsn()`, `connect(key, readonly=True)`, `query()`. Import it instead of re-inlining DSN/secret code.
  Also reuse: `shopify_lib.Store` (shopify-seo), `gads_client` (`gads.py`), `awb_lib` (awb-track),
  `richpanel_client`, `ro_text` (ai-scrub), and read `cache.*` (gigi:metrics-cache) / `metrics.fx_rates`
  instead of recomputing.
- **Efficiency:** read precomputed `cache.*` tables (`gigi:metrics-cache`) instead of recomputing heavy aggregates live; if you need a new precompute, add a table there + cron, don't bake a slow query into every run.
- **Safety:** Postgres read-only by default; any write is `--apply` after a dry-run that prints the SELECT + row counts + explicit confirmation; no destructive SQL without the matching SELECT shown.

## Save in the Knowledge Base (always)
- **Register:** `kb.py skill-register --plugin <a> --name <n> --author <a> --path plugins/<a>/skills/<n>`
- **Log** creation/changes and usage: `kb.py log --type skill --action created|modified|used --name <a>:<n> --summary "…"`
- **Secrets** go in the KB `secrets` table only: `kb.py secret-set KEY VALUE` / fetch with `secret-get` — never in code or chat.
- **Files** created on the NAS: `kb.py file-add --location nas --path "$NAS_ROOT/…" --action created`.
- **Reference links/docs** (a runbook, dashboard, API doc): `kb.py resource-add --category doc --label "…" --value "…"`.
- **Guardrails** if the skill enables something risky team-wide: `kb.py guard-add ask|deny <regex> --reason "…"`.

## Scaffold + ship
```bash
uv run scripts/new_skill.py --create --name my-skill --category cs --author gigi --desc "…trigger phrases…"
# implement scripts/ + flesh out SKILL.md, then:
/gigi:publish-skill        # registers, branches, commits, PRs, merges to main, syncs (needs push confirmation)
```

## Checklist
1. `--check` overlap → EXTEND if a close match exists.  2. Pick category.  3. Reuse shared libs + `cache.*`.
4. Scaffold.  5. Read-only/dry-run safety.  6. `skill-register` + `log`.  7. Secrets→KB.  8. `publish-skill`.
