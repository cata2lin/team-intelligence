---
name: image-gen
description: Generate ANY image from a text prompt — with optional reference photos to keep an exact subject — using two interchangeable engines (Gemini "nano-banana" gemini-2.5-flash-image / pro, or OpenAI gpt-image-1, chosen with --engine). Keys come from the team KB (GEMINI_API_KEY/GOOGLE_AI_API_KEY, OPENAI_API_KEY). Outputs PNGs locally and optionally to the NAS, with a JSON sidecar of the prompt. Includes a product-photo mode where GPT-4o auto-writes 4 ecommerce prompts (lifestyle/application/detail/context) from a product's real photos. Use whenever you need to create images from a prompt — product shots, scenes, concepts, hero/background art, anything — not just products.
---

# image-gen — create any image from a prompt

A general image generator. Give it a **prompt** and it renders one or more images;
give it **reference photos** too and the model keeps the *exact* subject from them
(a product, an object, a person's item) instead of inventing a new one.

Two engines, picked at runtime with `--engine`:

| `--engine` | model | best at | key (KB) |
|-----------|-------|---------|----------|
| `gemini` (default) | `gemini-2.5-flash-image` (Nano Banana), `--pro` → `gemini-3-pro-image-preview` | **faithfully preserving a subject from reference photos**; fast; cheap | `GEMINI_API_KEY` / `GOOGLE_AI_API_KEY` |
| `openai` | `gpt-image-1` | strong text-to-image; clean studio looks; native `n` variants | `OPENAI_API_KEY` |

> Both keys already live in the **team KB** (SharedClaude `secrets`). The scripts
> fetch them with `kb.py secret-get` automatically (env var overrides). Never paste
> a key into a file or chat. The OpenAI key is the same one the Scripturi e‑transport
> autopilot uses — kept in the KB so the whole team shares one source of truth.

## When to use
- "generate an image of …", "fă o poză cu …", "creează un vizual pentru …" — anything.
- Put a **specific product/object** (from real photos) into a new scene → use `--ref`.
- A batch of professional **product photos** for a store listing → `product_shots.py`.
- Concept art, hero/section backgrounds, mockups, social visuals, illustrations.

This is **not** `gigi:ad-banners` — that composites a background-removed cutout into a
fixed dark "glow" banner template. This skill *generates new pixels* from a prompt
(optionally guided by references). Use ad-banners for on-brand ad creatives from an
existing cutout; use image-gen to invent a new image.

## Setup
Nothing to install. Run the scripts with **`uv run`** — dependencies
(`google-genai`, `openai`, `pillow`, `requests`) are declared inline (PEP 723) and
uv provisions them in an isolated env on first run.

## 1) General generation — `scripts/gen.py`

```bash
# text → image, choose the engine
uv run scripts/gen.py --prompt "cozy scandinavian living room, morning light, photoreal" --engine gemini
uv run scripts/gen.py --prompt "minimal product hero on seamless beige, soft shadow" --engine openai -n 3

# keep an EXACT subject from reference photos (local paths or URLs)
uv run scripts/gen.py --engine gemini \
  --prompt "Using the reference, place THIS EXACT lamp on a walnut nightstand, warm bedroom, photoreal" \
  --ref ./lamp_front.jpg https://cdn.shopify.com/.../lamp_side.jpg

# higher quality / different shape / save to NAS
uv run scripts/gen.py --prompt "editorial perfume still life, dark marble" --engine gemini --pro --aspect 4:5 --nas
```

Key flags: `--engine gemini|openai` · `--ref <path|url> …` · `-n/--count N` ·
`--aspect 1:1|16:9|9:16|4:5|3:4|…` · `--pro` (Gemini quality) ·
`--quality low|medium|high|auto` (OpenAI) · `--out DIR` · `--name SLUG` · `--nas`.

Output: `DIR/<name>_<timestamp>.png` (+ `_2`, `_3` for variants) and a
`<name>_<timestamp>.json` sidecar with the prompt, engine, model and references.
With `--nas` the files are also copied to `$NAS_ROOT/exports/generated-images/<date>/`.

## 2) Product photos — `scripts/product_shots.py`

The flow extracted from grandia‑inventory's catalog‑quality "generate‑images",
generalised to either engine. GPT‑4o directs **4 prompts** — `lifestyle`,
`application`, `detail`, `context` — each told to keep the EXACT product from the
reference photos, then the chosen engine renders one image per prompt.

```bash
uv run scripts/product_shots.py \
  --title "Baterie cădiță retro bronz" --type "Baterii baie" --vendor "Grandia" \
  --notes "alamă masivă finisaj bronz periat, două robinete cu cap ceramic" \
  --ref https://cdn.shopify.com/.../baterie1.jpg ./baterie2.jpg \
  --engine gemini --nas

# just see the 4 prompts, render nothing
uv run scripts/product_shots.py --title "Lampă LED birou" --ref ./lamp.jpg --prompts-only
```

Always pass **1–3 real `--ref` photos** for product mode — without them the product
won't be faithful. Only the first 3 references are used (more add noise + cost).

## Notes & gotchas
- **Reference fidelity:** `gemini` is the better choice when the output must match a
  real product/object closely. `openai` is great for free text‑to‑image and clean
  studio renders but is looser on exact geometry.
- **No image returned:** Gemini occasionally returns only text (a safety refusal) —
  the script reports the model's message; soften/rephrase the prompt and retry.
- **Shopify CDN refs** are auto‑shrunk to `?width=1024&format=jpg` for a fast upload.
- **Cost:** OpenAI `gpt-image-1` at `--quality high` ≈ a few cents/image; Gemini Flash
  is cheaper. Use `--quality medium` / Flash (no `--pro`) for drafts.
- **Aspect ratios:** Gemini honours many ratios; gpt‑image‑1 snaps to the nearest of
  1024×1024 / 1024×1536 / 1536×1024.
- Pushing the result into a Shopify product gallery is intentionally **not** done here
  (review first). Use `gigi:shopify-stores` (staged upload → `productCreateMedia`) when
  you want to publish a chosen image.
