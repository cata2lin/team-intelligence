# /// script
# requires-python = ">=3.10"
# dependencies = ["markitdown[all]"]
# ///
"""Converteste ORICE fisier/URL in Markdown compact (Microsoft markitdown), ca sa-l dai LLM-ului
ca TEXT in loc de imagine/binar => mult mai putini tokeni. Formate: PDF, DOCX, PPTX, XLSX/CSV,
HTML, imagini (OCR/EXIF), audio (transcriere), JSON/XML, ZIP, EPub, YouTube URL, si altele.

  uv run to_markdown.py <fisier|URL> [alt fisier...] [--out DIR] [--stdout] [--head N]
    --out DIR    scrie <nume>.md in DIR (default: langa fisier)
    --stdout     printeaza Markdown-ul (nu scrie fisier) -- util ca sa-l citesti direct
    --head N     doar primele N caractere (preview ieftin)
    --quiet      fara sumar de tokeni
"""
import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from markitdown import MarkItDown

def convert_one(md, src):
    # markitdown auto-detecteaza local path / URL / stream
    return md.convert(src).text_content

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="fisiere sau URL-uri")
    ap.add_argument("--out", default=None)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--head", type=int, default=0)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    md = MarkItDown()
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    tot = 0
    for src in a.inputs:
        try:
            text = convert_one(md, src)
        except Exception as e:
            print(f"[EROARE] {src}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if a.head:
            text = text[:a.head]
        tot += len(text)
        if a.stdout:
            print(f"\n===== {os.path.basename(src)} =====")
            print(text)
        else:
            base = os.path.basename(src.rstrip("/")).rsplit(".", 1)[0] or "output"
            dest = os.path.join(a.out or (os.path.dirname(os.path.abspath(src)) if os.path.exists(src) else "."), base + ".md")
            open(dest, "w", encoding="utf-8").write(text)
            if not a.quiet:
                print(f"{src} -> {dest}  ({len(text)} char ~ {len(text)//4} tok)")
    if not a.quiet and not a.stdout:
        print(f"TOTAL ~{tot//4} tokeni Markdown")

if __name__ == "__main__":
    main()
