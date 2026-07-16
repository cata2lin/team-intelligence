# /// script
# requires-python = ">=3.10"
# ///
"""
graph_health.py — diagnostic pentru graful de memorie .claude (nu tablou, unealtă de igienă).

Răspunde: câte note/legături, câți ORFANI (cunoaștere ruptă), câte CLUSTERE și cum se distribuie,
care sunt HUBURILE reale (ancorele), și dacă există un cluster MONSTRU (prea mare → recall slab).

  uv run graph_health.py                       # auto-găsește ~/.claude/projects/*/memory + clusters.json
  uv run graph_health.py --memory-dir <dir> --clusters <clusters.json>

Fără dependințe. Rulează-l periodic: orfani în creștere / un cluster peste ~20% = de reparat.
"""
import argparse, glob, json, os, re, sys, collections

def find_memory():
    c = [d for d in glob.glob(os.path.expanduser("~/.claude/projects/*/memory")) if os.path.isdir(d)]
    if not c: sys.exit("nu găsesc ~/.claude/projects/*/memory (dă --memory-dir)")
    return max(c, key=os.path.getmtime)

def find_clusters():
    for p in glob.glob(os.path.expanduser(
            "~/.claude/plugins/marketplaces/*/plugins/gigi/skills/memory-graph/scripts/clusters.json")):
        return p
    return None

def main():
    ap = argparse.ArgumentParser(description="Diagnostic al grafului de memorie .claude.")
    ap.add_argument("--memory-dir", default=None)
    ap.add_argument("--clusters", default=None)
    ap.add_argument("--monster-pct", type=float, default=20.0, help="prag alertă cluster (%% din note)")
    a = ap.parse_args()
    md = a.memory_dir or find_memory()
    clp = a.clusters or find_clusters()
    clusters = json.load(open(clp)) if clp and os.path.exists(clp) else []

    notes = [f for f in glob.glob(os.path.join(md, "*.md")) if os.path.basename(f) != "MEMORY.md"]
    slug = lambda f: os.path.basename(f)[:-3]
    text = {slug(f): open(f, encoding="utf-8").read() for f in notes}
    allslugs = set(text)
    out = {s: {l for l in re.findall(r'\[\[([^\]]+)\]\]', t) if l in allslugs} - {s} for s, t in text.items()}
    inb = collections.defaultdict(set)
    for s, ls in out.items():
        for l in ls: inb[l].add(s)
    deg = {s: len(out[s]) + len(inb[s]) for s in allslugs}
    total_links = sum(len(v) for v in out.values())

    def hub(s):
        for c in clusters:
            if re.search(c["match"], s, re.I): return c["hub"]
        return "(fără hub)"
    byhub = collections.Counter(hub(s) for s in allslugs)
    orphans = sorted(s for s in allslugs if deg[s] == 0)
    N = len(allslugs)

    print(f"memorie: {md}")
    print(f"clusters.json: {clp or '(lipsă → totul apare fără hub)'}")
    print(f"\nNOTE {N}  ·  LEGĂTURI interne {total_links}  ·  densitate {total_links/max(N,1):.2f}/notă")
    print(f"\nCLUSTERE ({len([c for c in clusters])} huburi definite):")
    monster = []
    for h, n in byhub.most_common():
        pct = n * 100 // N
        flag = "  🔴 MONSTRU" if (pct >= a.monster_pct and h != "(fără hub)") else ("  ⚠ fără hub" if h == "(fără hub)" else "")
        if flag.strip().endswith("MONSTRU"): monster.append((h, n, pct))
        print(f"   {n:>3} ({pct:>2}%)  {h}{flag}")
    print(f"\nORFANI (0 legături = cunoaștere ruptă): {len(orphans)}")
    for o in orphans[:15]: print(f"   - {o}")
    print(f"\nTOP 10 ANCORE (grad = cele mai importante):")
    for s, d in sorted(deg.items(), key=lambda x: -x[1])[:10]:
        print(f"   {d:>3}  {s}")

    print("\n── VERDICT ──")
    if orphans: print(f"  • {len(orphans)} orfani → leagă-i la un hub sau cu suggest_links (memory-graph).")
    if monster: print(f"  • cluster monstru: {monster[0][0]} = {monster[0][2]}% → sparge-l / adaugă cross-links reale.")
    nohub = byhub.get("(fără hub)", 0)
    if nohub: print(f"  • {nohub} note fără hub → adaugă un cluster în clusters.json dacă e temă reală.")
    if not orphans and not monster and not nohub: print("  • graf sănătos.")

if __name__ == "__main__":
    main()
