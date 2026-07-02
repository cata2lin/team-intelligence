# /// script
# requires-python = ">=3.10"
# dependencies = ["trafilatura"]
# ///
"""Scoate DOAR conținutul principal (articol) dintr-o pagină web, ca Markdown curat — fără
meniuri/reclame/subsol/HTML. Mult mai puțini tokeni decât HTML-ul brut. Bun pt research, SEO,
social-listening, analiză competiție.

  uv run web_extract.py <URL> [alt URL...] [--stdout] [--out DIR] [--with-links] [--with-meta]
"""
import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import trafilatura

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--out", default=None)
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--with-links", action="store_true")
    ap.add_argument("--with-meta", action="store_true")
    a = ap.parse_args()
    if a.out:
        os.makedirs(a.out, exist_ok=True)
    for url in a.urls:
        dl = trafilatura.fetch_url(url)
        if not dl:
            print(f"[EROARE] nu pot descarca {url}", file=sys.stderr); continue
        text = trafilatura.extract(dl, output_format="markdown", include_links=a.with_links,
                                   include_comments=False, with_metadata=a.with_meta,
                                   favor_precision=True) or ""
        if not text.strip():
            print(f"[GOL] nimic extras din {url}", file=sys.stderr); continue
        if a.stdout:
            print(f"\n===== {url} =====\n{text}")
        else:
            import re
            name = re.sub(r'[^a-z0-9]+', '-', url.split("//")[-1].lower())[:60].strip('-') or "page"
            dest = os.path.join(a.out or ".", name + ".md")
            open(dest, "w", encoding="utf-8").write(text)
            print(f"{url} -> {dest}  ({len(text)} char ~ {len(text)//4} tok)")

if __name__ == "__main__":
    main()
