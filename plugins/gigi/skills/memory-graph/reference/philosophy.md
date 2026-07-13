# How to make memory references (the rule that keeps the graph USEFUL)

The point of linking memory notes is **not** a pretty graph. It's three concrete wins:

1. **Recall.** Memory is what Claude reads next session. When one note is pulled into
   context, the notes it links with `[[...]]` surface as neighbors. Correct links = future
   Claude sees the right adjacent facts. This is the #1 reason to link.
2. **Backlinks.** Open a note in Obsidian → the Backlinks panel shows everything that
   references it = instant "what decisions / scripts / incidents touch this".
3. **Orphan detection.** A note with zero links is usually mis-filed or forgotten
   knowledge. The graph makes it visible so you reconnect or archive it.

## The one rule
**Add a link only when the relation is REAL — one you'd actually traverse.** A link added
just to densify the graph is *noise*: it dilutes the Backlinks panel and makes recall
*worse* (it surfaces irrelevant neighbors). More links ≠ better. *Right* links = better.

### Anti-pattern (this actually happened)
Two unrelated product notes — "raft metalic" (a metal shelf) and "bibliotecă" (a bookcase)
— got cross-linked just because both are furniture. Wrong: they share a category label,
not a relationship. The reader corrected it immediately. **Category ≠ relationship.**

### The safe default: hub-and-spoke
A *spoke → hub* link (a specific note → its topic's index/overview note) is almost always
real, because the hub is the note's conceptual parent (e.g. a Google-Ads-launch note →
`google-ads-launch-playbook`). That's what `link_memory.py` automates. It's safe because
it links to the *parent index*, never sideways between two arbitrary siblings.

### Sideways links: only by hand, only when specific
Note-to-note links between siblings (`meta-token-single-point-failure` →
`profit-data-sources-truth`) are the highest-value links, but also the easiest to get
wrong. Add them by hand when the relation is *specific and causal* ("this incident is why
that rule exists"), not merely topical ("both are about ads").

Litmus test before adding a link, ask: **"Next time I'm reading note A, do I genuinely want
note B in front of me?"** If yes → link. If it's just "they're the same category" → don't.

## Keeping it improving
- Re-run `link_memory.py --apply` after memory grows. Idempotent: only new notes get a hub.
- New topic? Add one `{hub, match}` line to `clusters.json` — the whole cluster links on
  the next run.
- Periodically eyeball the graph for **orphans** (unlinked dots) and **wrong clusters** (a
  note colored/placed where it doesn't belong) — those are the signal to fix by hand.
- MEMORY.md is the master index; it already links most notes (that's the big central hub).
  Per-note `[[...]]` links are what create the *meaningful sub-clusters* beyond the index.
