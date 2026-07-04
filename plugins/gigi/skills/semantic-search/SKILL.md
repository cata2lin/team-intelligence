---
name: semantic-search
description: Semantic (meaning-based) search over the team's own knowledge — the SharedClaude KB (`resources` = lessons/IPs/URLs/docs + the `skills` registry) plus local memories (~/.claude/.../memory/*.md). Finds the right lesson / skill / memory even when the wording differs (e.g. "shared ad account attribution" surfaces gigi:attribution-audit and the mapping-tiktok-attribution memory), where plain grep drowns in noise. Embeddings run LOCALLY (fastembed/ONNX, CPU-only — works even on Intel Macs, no GPU, no torch), free and private; optional --api uses a hosted embedding model (OpenAI text-embedding-3-small, falls back to Gemini gemini-embedding-001) for higher ranking quality. Use for "search the KB / my memories by meaning", "which skill/lesson is about X", "cauta semantic in KB", "ce stim despre …", "find related memories", "what have we learned about …", "recall from the knowledge base". Companion to core:knowledge-base (which stores/logs) — this RETRIEVES by meaning.
argument-hint: "search \"<question>\" [--k 8] [--api]  |  build [--api] [--source all|kb|memory]"
---

# semantic-search

> Author: **Gigi**. Shared with the whole team via the `gigi` plugin.

Search the team's accumulated knowledge by **meaning**, not keywords. Indexes:
- **KB `resources`** — the team lessons / reference links / docs,
- **KB `skills`** — the whole skill registry (name + description),
- **local memories** — `~/.claude/projects/*/memory/*.md`.

The embeddings index is cached locally and rebuilt on demand. Nothing leaves the
machine on the default (local) backend.

## Use it

```bash
S="${CLAUDE_PLUGIN_ROOT}/skills/semantic-search/semsearch.py"

uv run "$S" search "cum impart spend-ul cand un cont e partajat"   # top-8, local
uv run "$S" search "profit pe fereastra de zile" --k 5
uv run "$S" search "unde tin exporturile mari" --api               # hosted embeddings (better ranking)
uv run "$S" build            # (re)build the local index — after new memories/lessons
uv run "$S" build --api      # build the higher-quality (hosted) index
uv run "$S" search "..." --json   # machine-readable (for other skills to consume)
```

`search` auto-builds the index the first time. Results show a cosine score, the
source (`kb:resource` / `kb:skill` / `memory`), the title, and a snippet.

## Two backends
- **local (default)** — `fastembed` + `paraphrase-multilingual-MiniLM-L12-v2` (~0.22 GB),
  ONNX on CPU. Runs on any machine incl. Intel Macs (no GPU/torch). ~15–20s to embed
  a few hundred items, queries in milliseconds. Free, private, offline.
- **`--api`** — hosted embeddings for sharper ranking: OpenAI `text-embedding-3-small`,
  auto-falls-back to Gemini `gemini-embedding-001` if OpenAI is rate-limited. Cheap
  (~$0.02/1M tokens). The small local model is good; the hosted one ranks the single
  best hit higher (e.g. it pulls `mapping-tiktok-attribution` to the top where the local
  model ranks it lower).

Local and `--api` indexes are cached separately (`~/.cache/arona-semsearch/`), so you
can keep both.

## Auth
- `KB_DATABASE_URL` (already in env — the bootstrap) for the KB corpus.
- `--api`: `OPENAI_API_KEY` / `GEMINI_API_KEY` from the secret store (never printed).
- No KB reachable → it still works over local memories alone.

## Notes / gotchas
- **Python 3.12** — `fastembed`/onnxruntime has no wheels for CPython 3.13 on Intel
  macOS yet, so the PEP-723 header pins `<3.13`; `uv` picks 3.12 automatically.
- **Rebuild after adding knowledge** — the index is a snapshot; run `build` to refresh
  after new memories or `kb.py resource-add`/`skill-register`.
- **When grep is still fine**: rare, exact terms (a specific SKU, an IP). Semantic wins
  when the wording varies or the terms are generic.
- Complements `core:knowledge-base` (`kb.py recent` / `resource-list` = store & log);
  this is the *retrieval-by-meaning* layer on top.
