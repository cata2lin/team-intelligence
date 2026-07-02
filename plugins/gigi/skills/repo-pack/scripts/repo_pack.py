# /// script
# requires-python = ">=3.10"
# dependencies = ["pathspec"]
# ///
"""Impacheteaza un director de cod intr-UN singur fisier Markdown compact (arbore + continut),
ca sa-l dai LLM-ului o data, in loc sa deschizi zeci de fisiere. Respecta .gitignore, sare
node_modules/.venv/.git/binare, si estimeaza tokenii. Analog cu 'repomix', pur Python.

  uv run repo_pack.py <dir> [--out FILE] [--stdout] [--include "*.py,*.ts"] [--exclude "test/*"]
       [--max-file-kb 200] [--max-total-tok 120000] [--no-tree]
"""
import os, sys, argparse, fnmatch
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build",
             ".turbo", ".cache", "coverage", ".pytest_cache", ".mypy_cache", "target", ".idea",
             ".vscode", "vendor", ".terraform", "site-packages", ".gradle"}
BIN_EXT = {".png",".jpg",".jpeg",".gif",".webp",".ico",".pdf",".zip",".gz",".tar",".mp4",".mov",
           ".mp3",".wav",".woff",".woff2",".ttf",".eot",".onnx",".bin",".so",".dylib",".dll",
           ".pyc",".class",".jar",".db",".sqlite",".parquet",".xlsx",".docx",".pptx",".lock"}
LANG = {".py":"python",".js":"javascript",".ts":"typescript",".tsx":"tsx",".jsx":"jsx",".json":"json",
        ".md":"markdown",".sh":"bash",".yml":"yaml",".yaml":"yaml",".toml":"toml",".sql":"sql",
        ".html":"html",".css":"css",".go":"go",".rs":"rust",".rb":"ruby",".java":"java"}

def load_ignore(root):
    try:
        import pathspec
    except Exception:
        return None
    lines = []
    gi = os.path.join(root, ".gitignore")
    if os.path.exists(gi):
        lines = open(gi, encoding="utf-8", errors="replace").read().splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines) if lines else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out", default=None)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--include", default="")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--max-file-kb", type=int, default=200)
    ap.add_argument("--max-total-tok", type=int, default=120000)
    ap.add_argument("--no-tree", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.dir)
    inc = [g.strip() for g in a.include.split(",") if g.strip()]
    exc = [g.strip() for g in a.exclude.split(",") if g.strip()]
    spec = load_ignore(root)
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(fns):
            full = os.path.join(dp, fn); rel = os.path.relpath(full, root)
            if os.path.splitext(fn)[1].lower() in BIN_EXT: continue
            if spec and spec.match_file(rel): continue
            if inc and not any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(fn, g) for g in inc): continue
            if exc and any(fnmatch.fnmatch(rel, g) for g in exc): continue
            try:
                if os.path.getsize(full) > a.max_file_kb * 1024: continue
            except OSError:
                continue
            files.append((rel, full))
    out = []
    if not a.no_tree:
        out.append("# Repo: " + root + "\n\n## Arbore\n```")
        out += [rel for rel, _ in files]
        out.append("```\n")
    tok = sum(len(x) for x in out) // 4
    skipped = 0
    for rel, full in files:
        try:
            txt = open(full, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if "\x00" in txt[:2000]:  # binary sniff
            continue
        block = f"\n## `{rel}`\n```{LANG.get(os.path.splitext(rel)[1].lower(),'')}\n{txt}\n```\n"
        if tok + len(block)//4 > a.max_total_tok:
            skipped += 1; continue
        out.append(block); tok += len(block)//4
    text = "\n".join(out)
    if skipped:
        text += f"\n\n> ⚠️ {skipped} fisiere OMISE (depasit --max-total-tok={a.max_total_tok}). Restrange cu --include."
    if a.stdout:
        print(text)
    else:
        dest = a.out or os.path.join(os.path.dirname(root), os.path.basename(root) + "_packed.md")
        open(dest, "w", encoding="utf-8").write(text)
        print(f"{len(files)} fisiere -> {dest}  (~{tok} tokeni{', '+str(skipped)+' omise' if skipped else ''})")

if __name__ == "__main__":
    main()
