# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""
suggest_links.py — auto-linker cu POARTĂ DE PRECIZIE pentru graful de memorie .claude.

De ce poartă de precizie: research 2026 (fuzionat Claude+Gemini+ChatGPT) arată că similaritatea
semantică PURĂ e abia peste random la calitatea legăturilor (CausalRAG2: semantic 24,9% F1 vs random
23,9%; cu o a doua poartă → 31,6%). Deci: cosine găsește CANDIDAȚII (recall ieftin), dar legătura se
adaugă doar după o A DOUA poartă (verificare LLM „relație reală, specifică") — altfel = densitate
cosmetică = strică recall-ul. Vezi [[efficiency-skills-adoption]] / memory-graph philosophy.

Flux (2 pași, ca aprobarea să fie deliberată):
  1) uv run suggest_links.py suggest --out candidates.json [--min 0.80 --topk 6]
       embed name+description (OpenAI) → candidați = perechi cosine≥min, NElegate deja. NU scrie nimic.
  2) (poarta LLM: un subagent verifică perechile → vetted.json = doar cele REALE, cu direcție + motiv)
  3) uv run suggest_links.py apply --from vetted.json [--apply]
       adaugă `[[target]]` în „Related" (idempotent). Dry-run fără --apply.

Secret: OPENAI_API_KEY din KB (export înainte). Nu se printează.
"""
import argparse, glob, json, os, re, sys, urllib.request

def memdir():
    base = os.path.expanduser("~/.claude/projects")
    cands = [d for d in glob.glob(os.path.join(base, "*", "memory")) if os.path.isdir(d)]
    if not cands: sys.exit("nu găsesc ~/.claude/projects/*/memory")
    return max(cands, key=os.path.getmtime)

def notes(md):
    out = {}
    for f in glob.glob(os.path.join(md, "*.md")):
        s = os.path.basename(f)[:-3]
        if s == "MEMORY": continue
        t = open(f, encoding="utf-8").read()
        desc = ""
        m = re.search(r'^description:\s*(.+?)(?:\n[a-zA-Z_]+:|\n---)', t, re.S | re.M)
        if m: desc = re.sub(r'\s+', ' ', m.group(1)).strip().strip('"\'')
        links = set(re.findall(r'\[\[([^\]]+)\]\]', t))
        out[s] = {"path": f, "text": f"{s.replace('-',' ')}. {desc}"[:600], "desc": desc, "links": links, "raw": t}
    return out

def _post(url, body, headers, tries=4):
    import time
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
            return json.loads(urllib.request.urlopen(req, timeout=120).read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and k < tries - 1:
                time.sleep(2 ** k); continue
            raise

def embed(texts, provider=None):
    import numpy as np
    provider = provider or ("gemini" if os.environ.get("GEMINI_API_KEY") else "openai")
    vecs = []
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY") or sys.exit("GEMINI_API_KEY lipsește.")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={key}"
        for n, t in enumerate(texts):
            r = _post(url, {"content": {"parts": [{"text": t}]}, "outputDimensionality": 768},
                      {"Content-Type": "application/json"})
            vecs.append(r["embedding"]["values"])
            if (n + 1) % 40 == 0: print(f"  …{n+1}/{len(texts)}", file=sys.stderr)
    else:
        key = os.environ.get("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY lipsește.")
        for i in range(0, len(texts), 128):
            r = _post("https://api.openai.com/v1/embeddings",
                      {"model": "text-embedding-3-small", "input": texts[i:i+128]},
                      {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
            vecs.extend(d["embedding"] for d in r["data"])
    a = np.array(vecs, dtype="float32")
    return a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)

def cmd_suggest(a):
    import numpy as np
    md = a.memory_dir or memdir()
    N = notes(md)
    slugs = sorted(N)
    print(f"memorie: {md}\nnote: {len(slugs)} — embed name+description…", file=sys.stderr)
    E = embed([N[s]["text"] for s in slugs])
    sim = E @ E.T
    seen, cands = set(), []
    for i, s in enumerate(slugs):
        order = np.argsort(-sim[i])
        picked = 0
        for j in order:
            if j == i or picked >= a.topk: continue
            t = slugs[j]; c = float(sim[i][j])
            if c < a.min or c >= 0.985: continue
            if t in N[s]["links"] or s in N[t]["links"]: continue   # deja legate
            key = tuple(sorted((s, t)))
            if key in seen: continue
            seen.add(key); picked += 1
            cands.append({"a": s, "b": t, "cos": round(c, 3),
                          "desc_a": N[s]["desc"][:260], "desc_b": N[t]["desc"][:260]})
    cands.sort(key=lambda x: -x["cos"])
    json.dump(cands, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(f"{len(cands)} candidați (cosine≥{a.min}, nelegate) → {a.out}")
    print("Următor: poarta LLM (subagent) verifică perechile → vetted.json, apoi `apply`.")

def cmd_apply(a):
    md = a.memory_dir or memdir()
    vetted = json.load(open(a.getattr_from))
    N = notes(md)
    applied = 0
    for p in vetted:
        s, t = p["a"], p["b"]
        if s not in N or t not in N: continue
        raw = N[s]["raw"]
        if f"[[{t}]]" in raw: continue
        note = p.get("why", "")
        add = f" · [[{t}]]" + (f" ({note})" if note else "")
        if "**Related:**" in raw:
            raw2 = re.sub(r'(\*\*Related:\*\*[^\n]*)', lambda m: m.group(1) + add, raw, count=1)
        else:
            raw2 = raw.rstrip() + f"\n\n**Related:** [[{t}]]" + (f" ({note})" if note else "") + "\n"
        if a.apply:
            open(N[s]["path"], "w", encoding="utf-8").write(raw2)
            N[s]["raw"] = raw2
        applied += 1
        print(f"  {'✅' if a.apply else '·'} {s} → [[{t}]]  {('— '+note) if note else ''}")
    print(f"\n{'APLICAT' if a.apply else 'DRY-RUN'}: {applied} legături." + ("" if a.apply else "  Adaugă --apply."))

def main():
    ap = argparse.ArgumentParser(description="Auto-linker cu poartă de precizie pt memoria .claude.")
    ap.add_argument("--memory-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("suggest"); g.add_argument("--out", default="candidates.json")
    g.add_argument("--min", type=float, default=0.80); g.add_argument("--topk", type=int, default=6)
    g = sub.add_parser("apply"); g.add_argument("--from", dest="getattr_from", required=True)
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    (cmd_suggest if a.cmd == "suggest" else cmd_apply)(a)

if __name__ == "__main__":
    main()
