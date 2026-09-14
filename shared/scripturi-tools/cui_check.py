#!/usr/bin/env python3
"""Validator de CUI romanesc (cifra de control) + extractor din fisiere."""
import re, sys, json

KEY = "753217532"

def valid_cui(cui: str):
    d = re.sub(r"\D", "", str(cui))
    if not (2 <= len(d) <= 10):
        return None, "lungime invalida"
    body, check = d[:-1], int(d[-1])
    k = KEY[-len(body):] if len(body) <= len(KEY) else KEY.rjust(len(body), "0")
    s = sum(int(a) * int(b) for a, b in zip(body, k))
    c = (s * 10) % 11
    if c == 10:
        c = 0
    return (c == check), f"asteptat {c}, gasit {check}"

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--scan":
        import pathlib
        found = {}
        for p in sys.argv[2:]:
            txt = pathlib.Path(p).read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"(?:CUI|C\.U\.I\.|RO)\s*:?\s*(RO)?\s*(\d{2,10})\b", txt, re.I):
                cui = m.group(2)
                ctx = txt[max(0, m.start()-90):m.start()].replace("\n", " ")[-90:]
                found.setdefault(cui, {"contexte": set(), "fisiere": set()})
                found[cui]["contexte"].add(ctx.strip())
                found[cui]["fisiere"].add(pathlib.Path(p).name)
        out = []
        for cui, info in sorted(found.items()):
            ok, why = valid_cui(cui)
            out.append({"cui": cui, "valid": ok, "motiv": why,
                        "fisiere": sorted(info["fisiere"]),
                        "context": sorted(info["contexte"])[0][:90]})
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for a in sys.argv[1:]:
            print(a, valid_cui(a))
