---
name: skill-consolidation
description: "Reproducible method to AUDIT the team's ~230 skills for overlap / contradiction / merge candidates, then EXECUTE the consolidation safely (merge script-backed skills via a dispatcher, delete pure duplicates, correct contradictions), AND reorganize the Obsidian memory vault (fix broken [[links]], add MOC hubs, cluster the index). Use when someone says 'audit the skills', 'which skills overlap/contradict', 'combine/merge skills', 'clean up the skill catalog', 'skills that should be together', 'reorganize memory/Obsidian', 'fix the memory index', or after a burst of new skills. Includes the hard-won gotchas (Second Brain has NO delete → tombstone; KB catalog is UPDATE-only; moving a script changes its __file__ depth; never delete a script-backed unique skill)."
argument-hint: "audit | consolidate <cluster> | memory-reorg"
---

# skill-consolidation — audit + consolidate skills, reorganize the memory vault

> Built 2026-07-24 from the real consolidation run (~230→~216 skills, PR #476-480 + memory vault of 314 notes).
> Full findings: `shared/skills-audit.md`. Companion: `gigi:knowledge-ops`, `gigi:skill-creator`.

## When to use
After many skills accrue and start overlapping/contradicting, or when the memory/Obsidian vault gets tangled
(broken links, no entry points). Two independent jobs — do either.

## PART A — Audit the skills (find overlap / contradiction / merge)
1. **Fan out parallel `Explore` agents, one per cluster** (SEO ~33, ADS ~30, CS ~29, Profit/Data ~16, Content, App-factory, Inventory…). Each reads `plugins/<author>/skills/<name>/SKILL.md` frontmatter + `shared/skills-audit.md`, and returns a table `skill | what it does | verdict (KEEP / MERGE-INTO-X / REDUNDANT)`.
2. Ask each agent for **CONTRADICTIONS** explicitly, not just overlaps — skills that give *opposite* answers cost money (real examples found: breakeven ROAS `1/margin` vs `1/(margin×delivery)` for COD; 4-5 divergent P&L "delivered" definitions; fatigue 10% vs 20%; pixel vs token attribution; 4 COGS definitions).
3. Synthesize into `shared/skills-audit.md` (extend, don't overwrite the prior plan).

## PART B — Consolidate safely (by skill TYPE)
Order: **contradictions first** (they produce wrong numbers), then pure duplicates, then big merges.

- **Contradiction (skill stays)** → add a `> ⚠️ CORECȚIE` banner at the top of its SKILL.md pointing to the ARONA-canonical skill. Non-destructive, doc-only.
- **Pure duplicate / fully covered → DELETE** (`git rm`). First `grep -rlE "gigi:<name>"` for references and repoint them to the canonical. Verify no OTHER skill's *code* calls its script.
- **Big merge of N script-backed skills → ONE skill + dispatcher.** Do NOT rewrite their logic. Move the N tested scripts into the new skill dir **at skill-dir depth** (so their `__file__`-relative resolution of shared libs like `rp_db`/`kb.py` is unchanged — moving into a `scripts/` subdir adds a `..` level and BREAKS it), and add a thin `<name>.py` dispatcher that `uv run`s the right one per mode. TEST every mode on real data before deleting anything (e.g. `cs360.py customer --phone <real>`).
- **NEVER delete a skill that has a UNIQUE script** (e.g. `shopify-geo/geo.py` offline scoring) — reframe its banner instead.

**Every deletion propagates to THREE stores:**
1. **git** — `git rm` (the marketplace/plugin catalog; auto-regenerates `CLAUDE.team.md`).
2. **KB catalog** (`SharedClaude.public.skills`) — you have UPDATE not DELETE → `UPDATE public.skills SET status='merged' WHERE name IN (...)` via `KB_DATABASE_URL` (user `gigi`).
3. **Second Brain** — ⛔ **NO hard delete** (API is GET-only; app DB is local on the SB box `m23726`; skills are files in `/opt/second-brain/vault/`). Do a **tombstone**: `publish_skill` the same slug with a `⛔ DEPRECAT/ȘTERS — folosește <canonical>` body. Routing then prefers the canonical (verify with `find_skills`). True deletion needs SSH to the SB server.

⚠️ **Staging trap:** after `git rm` + a reference-rewrite pass, do NOT `git add -u` (it stages unrelated pre-existing working-tree changes). Stage exact paths; verify each modified file's diff is only your rewrite before adding.

Register the new merged skill: `kb.py skill-register --plugin <p> --name <n> --path <dir>` + `publish_skill` to SB.

## PART C — Reorganize the memory / Obsidian vault
Vault = `~/.claude/projects/<proj>/memory/` (frontmatter notes + `[[slug]]` links + `MEMORY.md` index).
1. **Fix broken `[[links]]`** — scan every `[[slug]]` vs existing filenames; classify each broken one: (a) wrong slug → repoint to the real note, (b) it's a SKILL name not a note → convert to `` `plugin:skill` `` backtick, (c) placeholder/garbage → unwrap. Apply with a small Python pass over all `*.md`. Re-scan → 0 broken.
2. **Rewrite `MEMORY.md`** — lead with a **🧭 MOC (Map of Content)** section listing the entry-point notes (highest inbound-link count: profit-data-sources-truth, cs-map, xconnector-integration, store-domain-map, google-ads-launch-playbook, shopify-app-factory…), then ~16 clear clusters. Preserve every existing note link.
3. Back up the vault first (`cp -r`).

## Gotchas (learned the hard way)
- SB `publish_skill` only overwrites skills; there is no delete/disable endpoint. Tombstone is the max.
- Working tree often carries other people's uncommitted changes (VPS-synced repo) — stage precisely, never `-A`/`-u` broadly.
- A merged skill's scripts may import a shared lib from a sibling skill (`rp_db` from `richpanel-export`) resolved via `__file__` — keep the same directory depth.
- Test operational (CS/ops) merges on live data before deleting the originals.
