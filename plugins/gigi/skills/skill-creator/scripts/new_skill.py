# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
new_skill.py — decide EXTEND-vs-NEW, then scaffold a new team skill correctly.

Before creating anything it scans every existing SKILL.md and shows the most similar
skills, so you EXTEND an existing one instead of adding a near-duplicate (the team
already has overlap — see shared/skills-audit.md). Then it scaffolds a conventional
skill dir + SKILL.md and prints the KB-register + publish steps.

Usage:
  uv run new_skill.py --check  --name "..." --desc "what it does / when used"
  uv run new_skill.py --create --name my-skill --category cs --author gigi \
        --desc "One-line description with trigger phrases."
"""
import argparse, glob, os, re, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]  # …/team-intelligence

CATEGORIES = {
  "cs":          "Customer Service / Richpanel (tickets, profiles, refusals, watchdogs)",
  "ads":         "Paid ads — Google / Meta / TikTok (operate + analyze)",
  "pnl":         "P&L / finance / profitability (revenue−COGS−transport−spend)",
  "shopify":     "Shopify store ops & catalog (Admin API, products, stock, orders, knowledge base)",
  "seo":         "SEO / AEO / content (analytics, GEO, articles, CRO)",
  "fulfillment": "Couriers / AWB / returns / RMA (DPD/Sameday/Frisbo/xConnector)",
  "reporting":   "BI / dashboards / briefings / data-integrity",
  "creative":    "Design / banners / slides / brand assets",
  "infra":       "Shared libs, reference docs, KB, files, exports, scaffolding",
}

# Shared libs to REUSE (don't duplicate) — see shared/skills-audit.md
SHARED = """Reuse, do NOT re-implement (core/scripts or the owning skill):
  core/scripts/arona_pg.py  ← secret() (env-first+KB) + clean_dsn() + connect(readonly) + query()
                              THE shared Postgres/secret helper. Import it; never re-inline _clean_dsn.
  shopify_lib.Store   (shopify-seo: ARONA app token + gql/gql_all + assets)
  gads_client         (google-ads-mcc/gads.py: MCC OAuth + GAQL search/mutate)
  fx_ron              (metrics.fx_rates already exists — read it, don't re-derive FX)
  metrics_db + BRANDS, rma_lib, awb_lib (awb-track), richpanel_client, ro_text (ai-scrub)
  cache.* tables      (gigi:metrics-cache — read precomputed customer_agg / order_outcome)"""

STOP = set("the a an and or of to for in on with from per by is are be your you our this that "
           "use used uses when whoever which what skill store stores team arona via into out".split())

def tokens(s): return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}

def load_skills():
    out = []
    for sk in glob.glob(str(REPO / "plugins/*/skills/*/SKILL.md")):
        p = sk.split("/"); author, name = p[-4], p[-2]
        txt = open(sk, encoding="utf-8", errors="ignore").read()
        m = re.search(r"^---\s*\n(.*?)\n---", txt, re.S | re.M); desc = ""
        if m:
            dm = re.search(r"^description:\s*(.*?)(?:\n[a-zA-Z_]+:|\Z)", m.group(1), re.S | re.M)
            if dm: desc = re.sub(r"\s+", " ", dm.group(1)).strip().strip('"\'')
        out.append((f"{author}:{name}", desc))
    return out

def similar(name, desc, k=6):
    q = tokens(name + " " + desc)
    scored = []
    for full, d in load_skills():
        t = tokens(full.split(":")[1] + " " + d)
        if not t: continue
        j = len(q & t) / len(q | t) if (q | t) else 0
        scored.append((round(j, 3), full, d))
    scored.sort(reverse=True)
    return scored[:k]

TEMPLATE = """---
name: {name}
description: {desc}
argument-hint: "{hint}"
---

# {name}
> Author: **{author}**.

## What it does
{desc}

## Usage
```bash
uv run scripts/{snake}.py --help
```

## Data sources / shared libs
- (reuse core/scripts shared libs — do NOT duplicate DSN/Shopify/GAds/FX helpers)
- read-only by default; any write is `--apply` after a dry-run + row counts + confirmation

## Notes / gotchas
- (record hard-won traps here so the next run is fast)
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--name", required=True)
    ap.add_argument("--desc", default="")
    ap.add_argument("--category", choices=list(CATEGORIES))
    ap.add_argument("--author", default="gigi")
    a = ap.parse_args()

    print("Closest existing skills (EXTEND one of these before creating new):")
    for j, full, d in similar(a.name, a.desc):
        print(f"  [{j:>5}] {full:28} {d[:80]}")
    top = similar(a.name, a.desc)[0]
    if top[0] >= 0.12:
        print(f"\n>>> RECOMMENDATION: likely EXTEND `{top[1]}` (overlap {top[0]}) rather than create new.")
    elif top[0] >= 0.06:
        print(f"\n>>> MAYBE related to `{top[1]}` (overlap {top[0]}) — check it before creating new.")
    else:
        print("\n>>> No strong overlap — a new skill seems justified.")
    if a.category:
        print(f">>> Category: {a.category} — {CATEGORIES[a.category]}")
    print("\n" + SHARED)

    if not a.create:
        print("\n(--check only) Re-run with --create --category <c> to scaffold.")
        return
    if not a.category:
        sys.exit("\n--create needs --category")
    d = REPO / "plugins" / a.author / "skills" / a.name
    if d.exists(): sys.exit(f"\n{d} already exists.")
    (d / "scripts").mkdir(parents=True)
    snake = a.name.replace("-", "_")
    (d / "SKILL.md").write_text(TEMPLATE.format(
        name=a.name, desc=a.desc or "TODO describe + trigger phrases", author=a.author,
        hint="--help", snake=snake), encoding="utf-8")
    (d / "scripts" / f"{snake}.py").write_text(
        "# /// script\n# requires-python = \">=3.10\"\n# dependencies = []\n# ///\n"
        'print("TODO: implement")\n', encoding="utf-8")
    print(f"\nScaffolded {d}")
    print("Next:")
    print("  1) implement scripts/, fill SKILL.md frontmatter (name/description/argument-hint)")
    print(f"  2) register:  uv run core/scripts/kb.py skill-register --plugin {a.author} --name {a.name} "
          f"--author {a.author} --path plugins/{a.author}/skills/{a.name}")
    print("  3) log usage / files (kb.py log, file-add), secrets via kb.py secret-set (never in code)")
    print(f"  4) publish:   /{a.author}:publish-skill  (or uv run publish_skill.py --path plugins/{a.author}/skills/{a.name})")

if __name__ == "__main__":
    main()
