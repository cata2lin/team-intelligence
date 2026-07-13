#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# ///
"""Idempotent hub-and-spoke cross-linker for the Claude .claude memory.

Adds ONLY real category relations (spoke -> hub), so recall surfaces the right
neighboring notes and the Obsidian graph clusters by topic. NEVER cosmetic density.

- Auto-detects the most-recently-used ~/.claude/projects/*/memory (or --memory-dir).
- Reads clusters.json next to this script: ordered [{"hub","match"}]; first regex that
  matches a note's filename wins -> that note gets one `**Related:** [[hub]]` line,
  unless it already contains `[[hub]]` (idempotent -> safe to re-run any time).

Usage:
  uv run --no-project link_memory.py                      # dry-run
  uv run --no-project link_memory.py --apply              # write
  uv run --no-project link_memory.py --memory-dir DIR --apply
"""
import os, re, sys, json, glob

def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

def find_memory_dir():
    explicit = arg("--memory-dir")
    if explicit:
        return os.path.expanduser(explicit)
    cands = glob.glob(os.path.expanduser("~/.claude/projects/*/memory/MEMORY.md"))
    if not cands:
        sys.exit("No memory dir found under ~/.claude/projects/*/memory — pass --memory-dir")
    cands.sort(key=os.path.getmtime, reverse=True)
    return os.path.dirname(cands[0])

def main():
    apply = "--apply" in sys.argv
    mem = find_memory_dir()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    clusters = json.load(open(os.path.join(script_dir, "clusters.json"), encoding="utf-8"))
    hubs = {c["hub"] for c in clusters}

    files = [f for f in os.listdir(mem) if f.endswith(".md") and f != "MEMORY.md"]
    present = {f[:-3] for f in files}
    for h in hubs:
        if h not in present:
            print(f"!! hub note missing (won't link to it): {h}.md")

    added, skipped, nomatch = {}, 0, []
    for f in sorted(files):
        slug = f[:-3]
        if slug in hubs:
            continue
        hub = next((c["hub"] for c in clusters if re.search(c["match"], slug, re.I)), None)
        if not hub:
            nomatch.append(slug); continue
        path = os.path.join(mem, f)
        body = open(path, encoding="utf-8").read()
        if f"[[{hub}]]" in body:
            skipped += 1; continue
        added.setdefault(hub, []).append(slug)
        if apply:
            open(path, "w", encoding="utf-8").write(body.rstrip() + f"\n\n**Related:** [[{hub}]]\n")

    print(f"\nmemory: {mem}")
    print(f"{'APPLIED' if apply else 'DRY-RUN'} — new links per hub:")
    for c in clusters:
        print(f"  {c['hub']:38} +{len(added.get(c['hub'], []))}")
    total = sum(len(v) for v in added.values())
    print(f"\nTOTAL new links: {total}   |   already linked (skip): {skipped}   |   no hub: {len(nomatch)}")
    if nomatch:
        print("NO HUB (add a cluster in clusters.json if these are a real topic):")
        print("  " + ", ".join(nomatch))
    if not apply and total:
        print("\nre-run with --apply to write.")

if __name__ == "__main__":
    main()
