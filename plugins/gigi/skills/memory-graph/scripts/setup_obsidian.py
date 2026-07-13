#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# ///
"""Mirror an Obsidian vault folder onto your live Claude memory + install a colored/glow graph.

Idempotent. Dry-run by default; --apply to act.

  uv run --no-project setup_obsidian.py --vault ~/Documents/YourVault
  uv run --no-project setup_obsidian.py --vault ~/Documents/YourVault --apply
  uv run --no-project setup_obsidian.py --vault ... --folder "Claude Memory" --memory-dir DIR --apply

What it does:
  1. Backs up any existing REAL <folder> in the vault, then symlinks it -> live memory dir,
     so the graph always shows current notes + every new [[link]] (no stale copy).
  2. Installs assets/graph.json (5 topic color groups + dark bg) and assets/graph-glow.css
     (enabled in appearance.json). Existing files are backed up first.
"""
import os, sys, glob, json, shutil

def arg(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

def find_memory_dir():
    explicit = arg("--memory-dir")
    if explicit:
        return os.path.expanduser(explicit)
    cands = glob.glob(os.path.expanduser("~/.claude/projects/*/memory/MEMORY.md"))
    if not cands:
        sys.exit("No memory dir found — pass --memory-dir")
    cands.sort(key=os.path.getmtime, reverse=True)
    return os.path.dirname(cands[0])

def main():
    apply = "--apply" in sys.argv
    vault = os.path.expanduser(arg("--vault") or sys.exit("--vault is required"))
    folder = arg("--folder", "Claude Memory")
    mem = find_memory_dir()
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
    tag = "backup"

    def act(desc, fn):
        print(("APPLY " if apply else "PLAN  ") + desc)
        if apply:
            fn()

    if not os.path.isdir(vault):
        sys.exit(f"vault not found: {vault}")
    dot = os.path.join(vault, ".obsidian")
    print(f"vault:  {vault}\nmemory: {mem}\n")

    # 1) mirror folder -> memory (symlink)
    target = os.path.join(vault, folder)
    if os.path.islink(target):
        act(f"symlink '{folder}' already exists -> refresh to {mem}",
            lambda: (os.remove(target), os.symlink(mem, target)))
    elif os.path.isdir(target):
        bak = os.path.join(vault, f"{folder} ({tag})")
        act(f"backup real '{folder}' -> '{folder} ({tag})' then symlink -> {mem}",
            lambda: (shutil.move(target, bak), os.symlink(mem, target)))
    else:
        act(f"create symlink '{folder}' -> {mem}", lambda: os.symlink(mem, target))

    # 2) graph.json
    gj_src = os.path.join(assets, "graph.json")
    gj_dst = os.path.join(dot, "graph.json")
    if os.path.exists(gj_src):
        def install_graph():
            os.makedirs(dot, exist_ok=True)
            if os.path.exists(gj_dst):
                shutil.copy(gj_dst, gj_dst + f".{tag}")
            shutil.copy(gj_src, gj_dst)
        act("install assets/graph.json (backup existing)", install_graph)

    # 3) glow snippet + enable
    css_src = os.path.join(assets, "graph-glow.css")
    snip_dir = os.path.join(dot, "snippets")
    css_dst = os.path.join(snip_dir, "graph-glow.css")
    if os.path.exists(css_src):
        def install_css():
            os.makedirs(snip_dir, exist_ok=True)
            shutil.copy(css_src, css_dst)
            appearance = os.path.join(dot, "appearance.json")
            data = {}
            if os.path.exists(appearance):
                try: data = json.load(open(appearance, encoding="utf-8"))
                except Exception: data = {}
            snips = data.get("enabledCssSnippets", [])
            if "graph-glow" not in snips:
                snips.append("graph-glow")
            data["enabledCssSnippets"] = snips
            json.dump(data, open(appearance, "w", encoding="utf-8"), indent=2)
        act("install + enable graph-glow.css snippet", install_css)

    print("\nDONE." if apply else "\n(dry-run) re-run with --apply.")
    print("Finish in Obsidian: fully quit (Cmd+Q) + reopen, then open Graph View.")
    print("For per-node glow: install community plugin 'New 3D Graph' and copy "
          "assets/data-3dgraph.json into .obsidian/plugins/new-3d-graph/data.json, then restart.")

if __name__ == "__main__":
    main()
