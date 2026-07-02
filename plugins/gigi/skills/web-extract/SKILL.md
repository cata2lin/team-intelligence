---
name: web-extract
description: "Extract ONLY the main article/content from a web page as clean Markdown (trafilatura) — strips menus, ads, sidebars, footers, cookie banners and raw HTML. Far fewer tokens than fetching the raw page. Use whenever you need to READ or SUMMARIZE a webpage, pull an article/blog post/competitor page/documentation into text, or gather sources for research — instead of loading the raw HTML (which is mostly boilerplate). Triggers: 'citeste pagina', 'extrage articolul', 'ce scrie pe site-ul asta', 'summarize this URL', 'get the article text', 'read this page', 'research on', 'competitor page content', 'clean text from URL'. Complements gigi:markitdown (which also does HTML but trafilatura is better at article boilerplate removal)."
argument-hint: "<URL> [alt URL...] [--stdout] [--out DIR] [--with-links] [--with-meta]"
---

# web-extract — pagină web → articol curat (Markdown)

Wrapper peste **trafilatura**: scoate DOAR conținutul principal al unei pagini (articolul), fără meniuri/reclame/subsol/HTML. Rezultat = Markdown compact, gata de citit/rezumat — **mult mai puțini tokeni** decât pagina brută (care e 80% boilerplate).

```bash
uv run scripts/web_extract.py "https://exemplu.ro/articol" --stdout       # citește direct
uv run scripts/web_extract.py URL1 URL2 URL3 --out ./surse                # scrie .md per pagină
uv run scripts/web_extract.py URL --with-links                            # păstrează linkurile inline
uv run scripts/web_extract.py URL --with-meta                             # + titlu/autor/dată în output
```

## Când îl folosești (regula de aur)
- **Research / SEO / social-listening / competitor analysis** → extrage textul paginilor înainte de a le da LLM-ului; nu încărca HTML brut.
- Articole de blog, documentație, pagini de produs concurente, știri.
- Pentru **loturi de surse** → dă toate URL-urile odată, scrie `.md`-uri, apoi le citești/sintetizezi.

## web-extract vs markitdown
- **web-extract** (trafilatura) = cel mai bun la a scoate ARTICOLUL curat dintr-o pagină „zgomotoasă" (blog/știri/ecommerce) — elimină boilerplate agresiv.
- **`gigi:markitdown`** = universal (PDF/Office/img/audio/YouTube) și convertește și HTML, dar păstrează mai mult din structura paginii. Pt pagini-articol → web-extract; pt fișiere/formate variate → markitdown.

## Note
- Dependență inline `trafilatura`; `uv run`, fără setup.
- `favor_precision=True` (implicit) → mai curat, poate tăia ocazional o secțiune marginală; dacă lipsește conținut, e o pagină JS-heavy (SPA) — atunci folosește Chrome/rendered.
- Nu randează JavaScript (fetch simplu). Pt SPA-uri grele → chrome-devtools MCP.
