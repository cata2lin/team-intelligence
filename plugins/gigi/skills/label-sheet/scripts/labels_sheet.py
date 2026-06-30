#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["reportlab"]
# ///
"""etichete_cuvinte_a4.py — pune cuvinte pe A4 in etichete EGALE de dimensiune data (default 3x2 cm),
text centrat + auto-incadrat (micsoreaza fontul / trece pe 2 randuri pt cuvinte lungi), contur de taiere.

Utilizare:
  uv run etichete_cuvinte_a4.py                          # lista default (note parfum) -> PDF
  uv run etichete_cuvinte_a4.py --w 30 --h 20            # alta dimensiune eticheta (mm)
  uv run etichete_cuvinte_a4.py --words-file lista.txt   # cuvinte din fisier (unul/rand)
  uv run etichete_cuvinte_a4.py --out /cale/fisier.pdf
Print la 100% / "Actual size" (NU "fit to page"), altfel dimensiunea nu mai e 3x2cm.
"""
import argparse
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

DEFAULT_WORDS = ["Bergamot","Lavender","Rose","Jasmine","Vanilla","Musk","Sandalwood","Cedarwood",
"Patchouli","Amber","Vetiver","Iris","Violet","Neroli","Orange Blossom","Lemon","Mandarin","Grapefruit",
"Cinnamon","Cardamom","Clove","Pepper","Ginger","Tonka Bean","Benzoin","Frankincense","Myrrh","Oud",
"Leather","Tobacco","Honey","Coconut","Almond","Peach","Pear","Apple","Raspberry","Blackcurrant","Fig",
"Gardenia","Tuberose","Ylang-Ylang","Magnolia","Lily","Peony","Mint","Basil","Sage","Rosemary","Oakmoss"]

FONT = "Helvetica"

def fit(word, maxw, maxh, fmax=14, fmin=6):
    """Intoarce (linii, font_size) care incap in latimea maxw SI inaltimea maxh.
    Alege fontul cel mai MARE; daca wrap-ul pe 2 randuri permite font mai mare (etichete inguste-inalte),
    prefera 2 randuri. Spargere pe spatiu sau cratima."""
    LINE = 1.2  # factor inaltime linie

    def best_one(w):
        for fs in range(fmax, fmin - 1, -1):
            if stringWidth(w, FONT, fs) <= maxw and fs * LINE <= maxh:
                return fs
        return None

    cands = []
    f1 = best_one(word)
    if f1:
        cands.append(([word], f1))
    sep = " " if " " in word else ("-" if "-" in word else None)
    if sep:
        toks = word.split(sep)
        best = (None, 1e9)
        for k in range(1, len(toks)):
            a = sep.join(toks[:k]) + ("-" if sep == "-" else "")
            b = sep.join(toks[k:])
            d = abs(len(a) - len(b))
            if d < best[1]:
                best = ((a, b), d)
        a, b = best[0]
        for fs in range(fmax, fmin - 1, -1):
            if stringWidth(a, FONT, fs) <= maxw and stringWidth(b, FONT, fs) <= maxw and fs * LINE * 2 <= maxh:
                cands.append(([a, b], fs)); break
    if not cands:
        return [word], fmin
    # font mai mare castiga; la egalitate, mai multe randuri (umple mai frumos eticheta inalta)
    cands.sort(key=lambda c: (c[1], len(c[0])), reverse=True)
    return cands[0]


def layout_at(word, fs, maxw):
    """Liniile unui cuvant la font FIX fs: 1 rand daca incape in latime, altfel sparge pe 2 (spatiu/cratima)."""
    if stringWidth(word, FONT, fs) <= maxw:
        return [word]
    sep = " " if " " in word else ("-" if "-" in word else None)
    if sep:
        toks = word.split(sep)
        best = (None, 1e9)
        for k in range(1, len(toks)):
            a = sep.join(toks[:k]) + ("-" if sep == "-" else "")
            b = sep.join(toks[k:])
            d = abs(len(a) - len(b))
            if d < best[1]:
                best = ((a, b), d)
        return list(best[0])
    return [word]  # cratima/spatiu lipsa: cel mai mic font

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--w", type=float, default=30.0, help="latime eticheta (mm)")
    ap.add_argument("--h", type=float, default=20.0, help="inaltime eticheta (mm)")
    ap.add_argument("--words-file", default="")
    ap.add_argument("--copies", type=int, default=1, help="cate exemplare din FIECARE cuvant (grupate)")
    ap.add_argument("--fill", action="store_true", help="repeta toata lista pana umple pagina/paginile complete")
    ap.add_argument("--one-page", action="store_true", help="micsoreaza inaltimea cat sa incapa TOATE etichetele pe O pagina")
    ap.add_argument("--per-label", action="store_true", help="font diferit per eticheta (implicit: font UNIFORM pe toate)")
    ap.add_argument("--landscape", action="store_true", help="pagina A4 pe lung (landscape, 297x210)")
    ap.add_argument("--out", default="/Users/gheorghebeschea/Downloads/etichete_note_parfum_A4.pdf")
    a = ap.parse_args()

    base = DEFAULT_WORDS
    if a.words_file:
        base = [l.strip() for l in open(a.words_file, encoding="utf-8") if l.strip()]
    # --copies: N bucati din fiecare cuvant, grupate (NN x w1, NN x w2, ...)
    words = [w for w in base for _ in range(max(1, a.copies))]

    import math
    LW, LH = a.w * mm, a.h * mm
    PAGESIZE = landscape(A4) if a.landscape else A4
    PW, PH = PAGESIZE
    if a.one_page and words:
        # pastrez latimea, reduc INALTIMEA cat sa intre TOATE pe o pagina (cu ~3mm margine sus/jos)
        cols0 = max(1, int(PW // LW))
        rows_needed = math.ceil(len(words) / cols0)
        LH = min(LH, (PH - 6 * mm) / rows_needed)
    cols = int(PW // LW)
    rows = int(PH // LH)
    per_page = cols * rows
    if a.fill and words:
        # repeta lista ciclic pana la pagini COMPLETE (fara ultima pagina incompleta)
        import math
        target = max(per_page, math.ceil(len(words) / per_page) * per_page)
        words = [words[k % len(words)] for k in range(target)]
    mx = (PW - cols * LW) / 2.0           # centrez grila pe orizontala
    my = (PH - rows * LH) / 2.0           # ... si verticala
    pad = 1.5 * mm
    maxw = LW - 2 * pad
    maxh = LH - 2 * pad

    # FONT UNIFORM (implicit): cel mai mare font la care INTRA TOATE cuvintele -> aceeasi marime peste tot
    uni = None
    if not a.per_label and words:
        uni = min(fit(w, maxw, maxh)[1] for w in words)

    c = canvas.Canvas(a.out, pagesize=PAGESIZE)
    for i, word in enumerate(words):
        page_i, idx = divmod(i, per_page)
        if i > 0 and idx == 0:
            c.showPage()
        col = idx % cols
        row = idx // cols
        x = mx + col * LW
        y = PH - my - (row + 1) * LH      # de sus in jos
        # contur de taiere (gri subtire)
        c.setStrokeColorRGB(0.7, 0.7, 0.7); c.setLineWidth(0.4)
        c.rect(x, y, LW, LH, stroke=1, fill=0)
        # text centrat (1 sau 2 randuri), centrat vertical
        if uni is None:
            lines, fs = fit(word, maxw, maxh)
        else:
            fs = uni
            lines = layout_at(word, fs, maxw)
        c.setFillColorRGB(0, 0, 0); c.setFont(FONT, fs)
        lh = fs * 1.15
        total_h = lh * len(lines)
        y_start = y + LH / 2 + total_h / 2 - lh + (lh - fs) / 2
        for j, ln in enumerate(lines):
            c.drawCentredString(x + LW / 2, y_start - j * lh, ln)
    c.showPage(); c.save()

    n = len(words)
    pages = (n + per_page - 1) // per_page
    print("Eticheta: %.1f x %.1f mm%s" % (LW / mm, LH / mm, " (redusa pt o pagina)" if (a.one_page and abs(LH - a.h * mm) > 0.1) else ""))
    print("Font: %s" % ("UNIFORM %dpt pe toate" % uni if uni else "auto-fit per eticheta"))
    print("Pe A4 incap: %d coloane x %d randuri = %d etichete/pagina" % (cols, rows, per_page))
    print("Cuvinte: %d -> %d pagina/pagini" % (n, pages))
    print("PDF: %s" % a.out)

if __name__ == "__main__":
    main()
