---
name: memory-graph
description: Keep your Claude memory a well-linked, self-improving knowledge graph. An IDEMPOTENT hub-and-spoke cross-linker adds ONLY real category relations (spoke → hub) to your live ~/.claude/projects/<slug>/memory notes — never cosmetic density — so future recall surfaces the right neighboring notes and the Obsidian graph clusters by topic. Re-run any time memory grows: it skips notes already linked and adds only the new ones. Ships a one-command Obsidian setup that MIRRORS the vault folder on live memory (symlink, so the graph is never a stale copy) and installs a colored + glowing graph config. Use when your memory graph is sparse/monochrome/grey, notes feel disconnected, the Obsidian graph shows fewer notes than you actually have, or you want the graph to reflect live memory. Clusters are config-driven (clusters.json) so it adapts as your memory topics change.
---

# memory-graph — keep your Claude memory a real knowledge graph

Two jobs, both about your **live memory** at `~/.claude/projects/<project-slug>/memory/`
(the `MEMORY.md` index + one file per fact — the store your recall reads):

1. **Cross-link it well** — an idempotent *hub-and-spoke* linker (`link_memory.py`).
2. **Visualize it truthfully** — mirror an Obsidian vault folder onto live memory and
   make the graph colored + glowing (`setup_obsidian.py`).

## The one rule (read `reference/philosophy.md` first)
**Add only REAL relations. Never cosmetic density.** A link you added just to make the
graph look busy is *noise* — it pollutes the Backlinks panel and makes recall WORSE,
because recall surfaces `[[linked]]` notes as neighboring context. More links ≠ better;
*right* links = better. The classic anti-pattern: linking two unrelated product notes
("raft metalic" ↔ "bibliotecă") just because both are furniture. Don't.

Why links are useful (not decoration):
- **Recall** — when one note is pulled into context, its `[[links]]` surface too, so next
  session Claude sees the correct neighboring facts.
- **Backlinks** — open any note → see everything that references it = real navigation.
- **Orphan detection** — a note with zero links is often mis-filed / forgotten knowledge.

## 1. Cross-link (idempotent, re-runnable)
```bash
cd scripts
uv run --no-project link_memory.py                 # DRY-RUN: shows +N per hub, skips, no-hub
uv run --no-project link_memory.py --apply         # writes "**Related:** [[hub]]" where missing
uv run --no-project link_memory.py --memory-dir /path/to/memory --apply   # explicit
```
- Auto-detects the most-recently-used `~/.claude/projects/*/memory` (or pass `--memory-dir`).
- Reads `scripts/clusters.json` = ordered `[{hub, match}]`; first regex that matches a
  note's filename wins → that note gets one `[[hub]]` link if it doesn't already have it.
- **Idempotent**: a note that already contains `[[hub]]` is skipped. Safe to re-run weekly.
- **Self-improving**: as memory grows, re-run and only the new notes get linked. When you
  add a new topic cluster, add a `{hub, match}` line to `clusters.json`.

Edit `clusters.json` to fit your memory. Each entry: a **hub** note (the index/overview
for a topic) and a filename **match** regex for its spokes. Order = priority.

## 2. Obsidian graph — mirror + glow
```bash
cd scripts
uv run --no-project setup_obsidian.py --vault ~/Documents/YourVault           # DRY-RUN
uv run --no-project setup_obsidian.py --vault ~/Documents/YourVault --apply    # do it
```
It (idempotently):
1. **Backs up** any existing real `Claude Memory` folder in the vault, then **symlinks**
   `Claude Memory` → your live memory dir. The graph now shows *all* live notes and every
   new link automatically — no more stale 147-vs-177 divergence.
2. Installs `assets/graph.json` (5 color groups by topic + dark bg + node-size-by-links)
   and `assets/graph-glow.css` snippet (dark radial bg + canvas saturate/brightness), and
   enables the snippet in `appearance.json`. Existing files are backed up first.
3. Prints the manual finishing steps (fully quit + reopen Obsidian to load; open Graph View).

**Colors** come from the same filename patterns as the clusters (app / google / cs /
profit / seo). **Real per-node glow** (bloom) needs the community plugin *New 3D Graph*
(id `new-3d-graph`) — the 2D graph can only glow the whole canvas, not individual nodes.
`assets/data-3dgraph.json` is a ready neon config for it (copy into the plugin folder,
then restart Obsidian).

## Honest expectations
A few hundred notes will never look like the 8,900-node marketing renders — that density
comes from note **count + real `[[links]]`**, not from settings. This skill maximizes the
*real* structure; it won't fake density.

## Recurring upkeep (optional)
Re-run `link_memory.py --apply` after big memory-growth spurts (or on a weekly cron). It's
idempotent, so it only ever adds new real links. See `reference/philosophy.md` for how to
decide a link is real before adding it by hand.
