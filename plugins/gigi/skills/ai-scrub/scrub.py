# /// script
# requires-python = ">=3.9"
# ///
"""
De-AI-ing pre-publish gate for content (RO-first). Strips the invisible
Unicode watermarks LLMs leave, flags AI-tell phrasing (Romanian blocklist),
and reports over-used em-dashes. Pure stdlib, no keys.

Use as a gate before publishing blog articles (core:*-articles) or any copy.

Usage:
    uv run scrub.py --file articol.md            # report AI-tells
    uv run scrub.py --file articol.md --fix       # also write a cleaned copy (.clean)
    echo "text..." | uv run scrub.py              # from stdin
"""
import argparse, re, sys, unicodedata

# invisible / formatting chars LLMs and copy-paste leave behind
INVIS = {
    "​": "ZERO WIDTH SPACE", "‌": "ZWNJ", "‍": "ZWJ", "⁠": "WORD JOINER",
    "﻿": "BOM/ZWNBSP", "­": "SOFT HYPHEN", " ": "NARROW NBSP", " ": "THIN SPACE",
    "‎": "LTR MARK", "‏": "RTL MARK", "⁡": "FUNCTION APP", " ": "LINE SEP",
}
# RO AI-tell phrases (equivalents of delve/pivotal/landscape/unlock/leverage/in conclusion...)
RO_TELLS = [
    "în concluzie", "în era digitală", "în era modernă", "în lumea de azi", "în peisajul actual",
    "peisajul", "merită menționat", "este important de reținut", "este important de notat",
    "este esențial de", "să ne scufundăm", "să aprofundăm", "navighează prin", "navighează în",
    "deblochează", "deblocați", "valorifică", "valorificați", "joacă un rol crucial",
    "joacă un rol esențial", "joacă un rol vital", "joacă un rol cheie", "o gamă variată",
    "revoluționează", "transformă modul în care", "fără efort", "soluție de top", "nu doar",
    "în cele din urmă", "în acest sens", "demn de remarcat", "o adevărată artă", "o mărturie a",
    "în concluzie,", "pe scurt,", "în esență", "într-o lume în care", "deopotrivă",
]

# length-preserving RO de-accent so matching catches both "menționat" and "mentionat"
_RO = str.maketrans("ăâĂÂîÎșşȘŞțţȚŢ", "aaAAiIssSSttTT")
def _deacc(s): return s.translate(_RO).lower()

def analyze(text):
    inv = {}
    for ch, name in INVIS.items():
        n = text.count(ch)
        if n: inv[name] = n
    # also any other Cf-category char
    other_cf = sum(1 for ch in text if unicodedata.category(ch) == "Cf" and ch not in INVIS)
    emdash = text.count("—")
    low = _deacc(text)                      # diacritic-insensitive, same length as text
    hits = []
    for p in RO_TELLS:
        pd = _deacc(p)
        for m in re.finditer(re.escape(pd), low):
            s = max(0, m.start() - 25); e = min(len(text), m.end() + 25)
            hits.append((p, text[s:e].replace("\n", " ")))
    return inv, other_cf, emdash, hits

def clean(text, fix_emdash):
    for ch in INVIS:
        text = text.replace(ch, "" if ch != " " and ch != " " else " ")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    if fix_emdash:
        text = re.sub(r"\s+—\s+", ", ", text)   # spaced em-dash → comma
        text = text.replace("—", "-")
    return text

def main():
    ap = argparse.ArgumentParser(description="De-AI-ing content gate (RO).")
    ap.add_argument("--file"); ap.add_argument("--text"); ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    if args.file:
        text = open(args.file, encoding="utf-8").read()
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    inv, other_cf, emdash, hits = analyze(text)
    wc = len(re.findall(r"\w+", text))
    print(f"De-AI scan — {wc} cuvinte")
    print(f"  invisible/watermark chars: {sum(inv.values()) + other_cf}" + (f"  {inv}" if inv else "") + (f" +{other_cf} alt Cf" if other_cf else ""))
    print(f"  em-dash (—): {emdash}" + ("  ⚠️ semn tipic LLM dacă e des" if emdash > 3 else ""))
    print(f"  fraze AI-tell (RO): {len(hits)}")
    for p, ctx in hits[:25]:
        print(f"    «{p}»  …{ctx}…")
    score = max(0, 100 - sum(inv.values())*5 - other_cf*5 - max(0, emdash-2)*3 - len(hits)*4)
    print(f"  scor curățenie: {score}/100  ({'OK de publicat' if score >= 80 else 'rescrie zonele de mai sus'})")
    if args.fix:
        out = (args.file or "stdin") + ".clean"
        cleaned = clean(text, fix_emdash=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"  → scris curat (invizibile eliminate, em-dash normalizat): {out}")
        print(f"    (frazele AI-tell NU se rescriu automat — corectează-le manual din listă)")

if __name__ == "__main__":
    main()
