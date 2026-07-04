# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["fastembed", "numpy", "psycopg2-binary", "requests"]
# ///
"""
semsearch.py — căutare SEMANTICĂ (după înțeles, nu cuvinte) peste memoria echipei:
  • KB SharedClaude: `resources` (lecții/IP-uri/URL-uri/docuri) + `skills` (registrul de skill-uri)
  • memoriile locale (~/.claude/projects/*/memory/*.md)

Indexul (embeddings) se ține LOCAL (cache), se reconstruiește când vrei. Două backend-uri:
  • local (default) — fastembed / ONNX, RULEAZĂ PE CPU (merge și pe Intel Mac), gratis, privat.
  • --api — OpenAI text-embedding-3-small (cheie din KB) — calitate mai bună, ~$0.02/1M tok.

  uv run semsearch.py build [--api] [--source all|kb|memory]
  uv run semsearch.py search "de unde iau cursul valutar" [--k 8] [--api] [--json]

Auth: KB_DATABASE_URL din env (bootstrap). Pt --api: OPENAI_API_KEY din KB (kb.py secret-get).
Secretele NU se printează.
"""
import os, sys, glob, re, json, time, argparse, subprocess
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
CACHE = os.path.expanduser("~/.cache/arona-semsearch")
LOCAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def secret(key):
    v = os.environ.get(key)
    if v:
        return v.strip()
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", key],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


# ---------------- corpus ----------------
def _kb_rows():
    out = []
    url = os.environ.get("KB_DATABASE_URL")
    if not url:
        sys.stderr.write("[semsearch] KB_DATABASE_URL lipsă — sar peste KB, folosesc doar memoriile.\n")
        return out
    try:
        import psycopg2
        with psycopg2.connect(url, connect_timeout=12) as c, c.cursor() as cur:
            cur.execute("SELECT COALESCE(category,''),COALESCE(label,''),COALESCE(value,''),COALESCE(description,'') FROM resources")
            for cat, label, val, desc in cur.fetchall():
                txt = f"{label}. {desc} {val}".strip()
                out.append({"id": f"kb-res:{label[:40]}", "source": "kb:resource",
                            "title": f"{cat}/{label}"[:50], "text": txt[:1200]})
            cur.execute("SELECT plugin,name,COALESCE(description,'') FROM skills")
            for plugin, name, desc in cur.fetchall():
                out.append({"id": f"skill:{plugin}:{name}", "source": "kb:skill",
                            "title": f"{plugin}:{name}", "text": f"{plugin}:{name}. {desc}"[:1200]})
    except Exception as e:
        sys.stderr.write(f"[semsearch] KB inaccesibil ({str(e)[:80]}) — doar memoriile.\n")
    return out


def _memory_rows():
    out = []
    for f in glob.glob(os.path.expanduser("~/.claude/projects/*/memory/*.md")):
        if f.endswith("MEMORY.md"):
            continue
        t = open(f, errors="ignore").read()
        name = os.path.basename(f)[:-3]
        m = re.search(r"description:\s*(.+)", t)
        desc = m.group(1).strip().strip('"') if m else ""
        body = re.sub(r"^---.*?---", "", t, flags=re.S).strip()
        out.append({"id": f"mem:{name}", "source": "memory",
                    "title": name, "text": f"{name}. {desc} {body}"[:1200]})
    return out


def gather(source):
    rows = []
    if source in ("all", "kb"):
        rows += _kb_rows()
    if source in ("all", "memory"):
        rows += _memory_rows()
    return rows


# ---------------- embeddings ----------------
def embed_local(texts):
    from fastembed import TextEmbedding
    emb = TextEmbedding(model_name=LOCAL_MODEL)
    v = np.array(list(emb.embed(texts)), dtype=np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def _openai(texts):
    import requests
    key = secret("OPENAI_API_KEY")
    if not key:
        return None
    out = []
    for i in range(0, len(texts), 256):
        chunk = texts[i:i+256]
        for attempt in range(3):
            r = requests.post("https://api.openai.com/v1/embeddings",
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": "text-embedding-3-small", "input": chunk}, timeout=90)
            if r.status_code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code != 200:
                return None
            out += [d["embedding"] for d in r.json()["data"]]; break
    return out


def _gemini(texts):
    import requests
    key = secret("GEMINI_API_KEY") or secret("GOOGLE_AI_API_KEY")
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents?key={key}"
    out = []
    for i in range(0, len(texts), 100):
        chunk = texts[i:i+100]
        body = {"requests": [{"model": "models/gemini-embedding-001",
                              "content": {"parts": [{"text": t}]}} for t in chunk]}
        r = requests.post(url, json=body, timeout=90)
        if r.status_code != 200:
            return None
        out += [e["values"] for e in r.json()["embeddings"]]
    return out


def embed_api(texts):
    out = _openai(texts)
    if out is None:
        sys.stderr.write("[semsearch] OpenAI indisponibil (quota/429) — trec pe Gemini.\n")
        out = _gemini(texts)
    if out is None:
        sys.exit("API embeddings indisponibil (nici OpenAI, nici Gemini). Folosește backend-ul local (fără --api).")
    v = np.array(out, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)


def embed(texts, api):
    return embed_api(texts) if api else embed_local(texts)


# ---------------- index i/o ----------------
def paths(api):
    tag = "api" if api else "local"
    return os.path.join(CACHE, f"{tag}.npz"), os.path.join(CACHE, f"{tag}.jsonl")


def cmd_build(a):
    os.makedirs(CACHE, exist_ok=True)
    rows = gather(a.source)
    if not rows:
        sys.exit("corpus gol.")
    print(f"corpus: {len(rows)} intrări ({a.source}) — embedding {'API' if a.api else 'local'}…")
    t0 = time.perf_counter()
    vecs = embed([r["text"] for r in rows], a.api)
    npz, meta = paths(a.api)
    np.savez_compressed(npz, vecs=vecs)
    with open(meta, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"✅ index construit: {len(rows)} vectori în {time.perf_counter()-t0:.1f}s → {CACHE}")


def load(api):
    npz, meta = paths(api)
    if not (os.path.exists(npz) and os.path.exists(meta)):
        return None, None
    vecs = np.load(npz)["vecs"]
    rows = [json.loads(l) for l in open(meta)]
    return vecs, rows


def cmd_search(a):
    vecs, rows = load(a.api)
    if vecs is None:
        print("(index lipsă — îl construiesc întâi)", file=sys.stderr)
        cmd_build(argparse.Namespace(source="all", api=a.api))
        vecs, rows = load(a.api)
    qv = embed([a.query], a.api)[0]
    sims = vecs @ qv
    idx = np.argsort(-sims)[:a.k]
    res = [{"score": round(float(sims[i]), 3), "source": rows[i]["source"],
            "title": rows[i]["title"], "id": rows[i]["id"],
            "snippet": rows[i]["text"][:140]} for i in idx]
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2)); return
    print(f'„{a.query}"  [{"API" if a.api else "local"}]')
    for r in res:
        print(f"  {r['score']:.3f}  [{r['source']:11}] {r['title'][:40]:40} — {r['snippet'][:70]}")


def main():
    p = argparse.ArgumentParser(description="Căutare semantică peste KB + memorii")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--api", action="store_true"); b.add_argument("--source", default="all", choices=["all", "kb", "memory"]); b.set_defaults(fn=cmd_build)
    s = sub.add_parser("search"); s.add_argument("query"); s.add_argument("--k", type=int, default=8); s.add_argument("--api", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_search)
    a = p.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
