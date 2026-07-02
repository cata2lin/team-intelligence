# -*- coding: utf-8 -*-
"""Randează un .docx în PNG-uri pentru verificare vizuală (Read pe imagini).
docx -> pdf (prin Microsoft Word / docx2pdf pe Mac) -> png (prin PyMuPDF).

Utilizare:
    python3 render_preview.py Portofoliu.docx [dpi]
Scoate: /tmp/preview.pdf și /tmp/preview_pNN.png ; afișează numărul de pagini.
Necesită: pip install docx2pdf PyMuPDF  (+ Microsoft Word instalat).
Fallback LibreOffice: soffice --headless --convert-to pdf <docx> --outdir /tmp
"""
import sys
import os
import fitz


def main(docx_path, dpi=100):
    pdf = "/tmp/preview.pdf"
    try:
        from docx2pdf import convert
        convert(docx_path, pdf)
    except Exception as e:
        # fallback LibreOffice
        os.system(f'soffice --headless --convert-to pdf "{docx_path}" --outdir /tmp')
        base = os.path.splitext(os.path.basename(docx_path))[0] + ".pdf"
        cand = os.path.join("/tmp", base)
        if os.path.exists(cand):
            pdf = cand
        else:
            print("Conversie eșuată:", e); sys.exit(1)
    doc = fitz.open(pdf)
    print("TOTAL PAGES:", doc.page_count)
    for i in range(doc.page_count):
        out = f"/tmp/preview_p{i+1:02d}.png"
        doc[i].get_pixmap(dpi=dpi).save(out)
    print(f"Rendered {doc.page_count} pages -> /tmp/preview_pNN.png (dpi={dpi})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Utilizare: python3 render_preview.py <fisier.docx> [dpi]")
        sys.exit(1)
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 100)
