# /// script
# requires-python = ">=3.10"
# ///
"""
compact_catalog.py — scurtează descrierile skill-urilor din catalogul CLAUDE.team.md.

DE CE: catalogul se încarcă în CONTEXT la fiecare tură, în fiecare sesiune, la toți. Descrierile
lungi acolo costă ~3,3k tokeni/tură team-wide degeaba — rutarea reală se face cu `find_skills` pe DB
(care păstrează descrierile complete). Ține DB-ul complet, ține catalogul RANDAT scurt.

E un POST-PROCESOR format-agnostic și IDEMPOTENT: rulezi generatorul tău de catalog CUM VREI, apoi
treci fișierul prin ăsta ca pas final. Scurtează doar liniile de skill din secțiunea „# Skills catalog";
regulile (înainte de catalog) NU se ating. O linie deja scurtă se sare (idempotent).

  uv run compact_catalog.py shared/CLAUDE.team.md            # DRY-RUN: arată economia
  uv run compact_catalog.py shared/CLAUDE.team.md --apply    # scrie în loc
  uv run compact_catalog.py shared/CLAUDE.team.md --words 8 --apply

Măsurat 2026-07-15: 30.8KB → 17.4KB (−44%, ~3,3k tok/tură). Vezi CONTRIBUTING.md (nota de rendering).
"""
import argparse, re, sys

CATALOG_MARK = "# Skills catalog"
LINE = re.compile(r'^(- \*\*[^*]+\*\*)\s*[—-]\s*(.+)$')


def compact(text, words):
    i = text.find(CATALOG_MARK)
    if i == -1:
        sys.exit(f"nu găsesc secțiunea '{CATALOG_MARK}' — nimic de compactat")
    head, catalog = text[:i], text[i:]
    out = []
    for ln in catalog.splitlines(keepends=False):
        m = LINE.match(ln)
        if not m:
            out.append(ln); continue
        name, desc = m.group(1), re.sub(r'\s+', ' ', m.group(2)).strip().rstrip('…').strip()
        ws = desc.split()
        short = " ".join(ws[:words]) + ("…" if len(ws) > words else "")
        out.append(f"{name} — {short}")
    return head + "\n".join(out) + ("\n" if catalog.endswith("\n") else "")


def main():
    ap = argparse.ArgumentParser(description="Compactează descrierile din catalogul CLAUDE.team.md.")
    ap.add_argument("file")
    ap.add_argument("--words", type=int, default=10, help="cuvinte păstrate din fiecare descriere (10)")
    ap.add_argument("--apply", action="store_true", help="scrie în fișier (altfel doar arată economia)")
    a = ap.parse_args()
    orig = open(a.file, encoding="utf-8").read()
    new = compact(orig, a.words)
    tok = lambda s: len(s) // 4
    saved = tok(orig) - tok(new)
    print(f"{a.file}: {len(orig)} → {len(new)} chars  (~{tok(orig)} → ~{tok(new)} tok, "
          f"−{saved} = {saved * 100 // max(tok(orig),1)}%)")
    if not a.apply:
        print("DRY-RUN. Adaugă --apply ca să scriu."); return
    if new == orig:
        print("deja compact — nimic de scris (idempotent)."); return
    open(a.file, "w", encoding="utf-8").write(new)
    print("✅ scris.")


if __name__ == "__main__":
    main()
