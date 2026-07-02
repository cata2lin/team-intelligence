---
name: markitdown
description: "Convert ANY document/file/URL to compact Markdown (Microsoft markitdown) so it can be fed to the LLM as TEXT instead of as an image or heavy binary — big token/credit saver. Handles PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx)/CSV, HTML, images (OCR + EXIF), audio (transcription), JSON/XML, ZIP, EPub, and YouTube URLs. Use whenever you need to READ or SUMMARIZE a PDF/Office/HTML file, extract a table from a spreadsheet, get text out of a scanned doc, or turn a webpage/YouTube link into text — instead of opening the raw file (which costs far more tokens). Triggers: 'citeste PDF-ul', 'extrage textul din', 'convert to markdown', 'markitdown', 'transforma docx/pptx/xlsx in text', 'ce scrie in fisierul asta', 'summarize this document', 'OCR', 'transcrie'."
argument-hint: "<fisier|URL> [--stdout] [--out DIR] [--head N]"
---

# markitdown — orice fișier → Markdown (economie de tokeni)

Wrapper peste **[Microsoft markitdown](https://github.com/microsoft/markitdown)**. Scoate textul curat (Markdown structurat: titluri, tabele, liste) din aproape orice, ca să-l dai LLM-ului ca **text**, nu ca imagine/PDF brut → **mult mai puțini tokeni** (un PDF de 1 pagină ≈ 300-400 tokeni text vs mii de tokeni ca imagine).

```bash
uv run scripts/to_markdown.py raport.pdf --stdout          # printeaza Markdown-ul (citește-l direct)
uv run scripts/to_markdown.py factura.pdf oferta.docx      # scrie .md langa fiecare fisier
uv run scripts/to_markdown.py date.xlsx --stdout           # tabelele Excel -> Markdown
uv run scripts/to_markdown.py https://exemplu.ro/pagina    # pagina web -> text
uv run scripts/to_markdown.py https://youtu.be/XXXX --stdout   # transcriere YouTube
uv run scripts/to_markdown.py scan.jpg --stdout            # OCR imagine
uv run scripts/to_markdown.py contract.pdf --head 2000     # doar preview ieftin
```

## Formate suportate
PDF · Word (.docx) · PowerPoint (.pptx) · Excel (.xlsx)/CSV · HTML/.mhtml · imagini (OCR + metadate) · audio (.wav/.mp3, transcriere) · JSON · XML · ZIP (extrage+convertește conținutul) · EPub · YouTube URL · text simplu. `markitdown[all]` (declarat inline PEP723) aduce toate extra-urile; `uv run` le instalează singur.

## Când îl folosești (regula de aur pt economie)
- **ÎNAINTE** de a „citi" un PDF/Office/HTML mare cu tool-ul de fișiere → convertește-l aici întâi și citește Markdown-ul. Diferența de cost e uriașă pe documente lungi/scanate.
- Pentru **tabele** (Excel/CSV/PDF cu tabel) → markitdown le dă direct ca tabel Markdown, gata de parsat.
- Pentru **pagini web / YouTube** → text în loc de HTML brut.
- Pentru **loturi** (multe facturi/AWB-uri/oferte) → dă toate căile odată, scrie `.md`-uri, apoi citești/parsezi textul.

## Note
- Dependențe inline (PEP723) — rulează cu `uv run`, fără setup. Prima rulare instalează `markitdown[all]` (~1-2 min).
- OCR-ul pe imagini și transcrierea audio depind de motoarele din markitdown; pt PDF scanat greu, calitatea variază — verifică output-ul.
- Nu înlocuiește citirea vizuală când chiar ai nevoie de LAYOUT/imagine (ex. verificare design UI). Pentru CONȚINUT (text, tabele, date) → markitdown e mereu mai ieftin.
- Companion: `gigi:doc-deck-factory` (produce docx/pptx), `gigi:pdf` (operații pe PDF). Acesta e strict extragere→Markdown pt consum LLM.
