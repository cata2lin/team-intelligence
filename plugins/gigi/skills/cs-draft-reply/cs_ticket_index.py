# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["fastembed", "numpy", "requests"]
# ///
"""
cs_ticket_index.py — index SEMANTIC peste SUBIECTELE tichetelor REZOLVATE (Richpanel export
SQLite) ca să găsești „cum am rezolvat cazuri similare" pentru a ancora un draft.

Indexăm OFFLINE doar `subject` (întrebarea clientului) al tichetelor CLOSED cu agent real.
Răspunsul agentului (rezoluția) NU e în SQLite → se aduce LIVE prin MCP doar pt top-K la retrieval.

Reutilizează embeddings din skill-ul `gigi:semantic-search` (fastembed local pe CPU, sau --api).

  uv run cs_ticket_index.py build [--api] [--limit 8000] [--days 180]
  uv run cs_ticket_index.py similar "vreau sa anulez comanda" [--k 5] [--category anulare] [--api]

Importat de cs_draft_reply.py: `from cs_ticket_index import retrieve`.
"""
import os, sys, json, re, sqlite3, time, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "semantic-search"))
from semsearch import embed  # noqa: E402  (fastembed local / API, normalizat)

CACHE = os.path.expanduser("~/.cache/arona-semsearch")


def find_db():
    if os.environ.get("RICHPANEL_DB") and os.path.exists(os.environ["RICHPANEL_DB"]):
        return os.environ["RICHPANEL_DB"]
    for c in [os.path.expanduser("~/Downloads/Scripturi/data/richpanel_tickets.db"),
              os.path.expanduser("~/Scripturi/data/richpanel_tickets.db"),
              "/root/Scripturi/data/richpanel_tickets.db",
              os.path.join(HERE, "..", "..", "..", "..", "data", "richpanel_tickets.db")]:
        if os.path.exists(c):
            return c
    return None


def _paths(api):
    tag = "api" if api else "local"
    return os.path.join(CACHE, f"tickets_{tag}.npz"), os.path.join(CACHE, f"tickets_{tag}.jsonl")


def _norm(s):
    s = re.sub(r"\b[A-Z]{2,5}\d{3,}\b", "", s or "")   # scoate nr comenzi (GT43483) ca subiectele să se grupeze
    return re.sub(r"\s+", " ", s).strip().lower()


def cmd_build(a):
    db = find_db()
    if not db:
        sys.exit("richpanel_tickets.db negăsit (setează RICHPANEL_DB).")
    os.makedirs(CACHE, exist_ok=True)
    con = sqlite3.connect(db)
    cutoff = ""
    if a.days:
        cutoff = f"AND updated_at >= datetime('now','-{int(a.days)} days')"
    rows = con.execute(
        f"""SELECT conversation_no, subject, COALESCE(category,''), COALESCE(store,'')
            FROM tickets
            WHERE status='CLOSED' AND assignee_id IS NOT NULL
              AND subject IS NOT NULL AND subject!='' AND subject NOT LIKE '(no %'
              {cutoff}
            ORDER BY updated_at DESC LIMIT ?""", (int(a.limit) * 3,)).fetchall()
    seen, docs = set(), []
    for no, subj, cat, store in rows:
        key = (_norm(subj), cat)
        if key in seen:
            continue
        seen.add(key)
        docs.append({"no": no, "subject": subj[:200], "category": cat, "store": store})
        if len(docs) >= int(a.limit):
            break
    print(f"tichete rezolvate unice: {len(docs)} (din DB {os.path.basename(db)}) — embedding {'API' if a.api else 'local'}…")
    t0 = time.perf_counter()
    vecs = embed([d["subject"] for d in docs], a.api)
    npz, meta = _paths(a.api)
    np.savez_compressed(npz, vecs=vecs)
    with open(meta, "w") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"✅ index tichete: {len(docs)} vectori în {time.perf_counter()-t0:.1f}s → {npz}")


def _load(api):
    npz, meta = _paths(api)
    if not (os.path.exists(npz) and os.path.exists(meta)):
        return None, None
    return np.load(npz)["vecs"], [json.loads(l) for l in open(meta)]


def retrieve(query, k=3, category=None, api=False):
    """Top-K tichete rezolvate similare. [] dacă indexul lipsește (nu crapă apelantul)."""
    vecs, docs = _load(api)
    if vecs is None:
        return []
    try:
        qv = embed([query], api)[0]
    except Exception:
        return []
    sims = vecs @ qv
    order = np.argsort(-sims)
    out = []
    for i in order:
        d = docs[i]
        if category and d.get("category") and d["category"] != category:
            continue
        out.append({**d, "score": round(float(sims[i]), 3)})
        if len(out) >= k:
            break
    return out


def cmd_similar(a):
    res = retrieve(a.query, a.k, a.category, a.api)
    if a.json:
        print(json.dumps(res, ensure_ascii=False)); return
    if not res:
        print("(index lipsă — rulează `build` întâi)"); return
    print(f'„{a.query}"  [{"API" if a.api else "local"}]' + (f" cat={a.category}" if a.category else ""))
    for r in res:
        print(f"  {r['score']:.3f}  #{r['no']:<7} [{r['category']:14}] {r['store'][:12]:12} — {r['subject'][:60]}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--api", action="store_true"); b.add_argument("--limit", default=8000); b.add_argument("--days", default=180); b.set_defaults(fn=cmd_build)
    s = sub.add_parser("similar"); s.add_argument("query"); s.add_argument("--k", type=int, default=5); s.add_argument("--category"); s.add_argument("--api", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_similar)
    a = p.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
