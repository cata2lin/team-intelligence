#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "google-genai>=1.0.0",
#   "openai>=1.50.0",
#   "pillow>=10.0.0",
#   "requests>=2.31.0",
# ]
# ///
"""
product_shots.py — ecommerce PRODUCT photography helper, built on top of gen.py.

Given a product (title/type + a few real reference photos), GPT-4o acts as a
product-photography director and writes 4 distinct, detailed prompts — LIFESTYLE,
APPLICATION, DETAIL, CONTEXT — each instructing the image model to keep the EXACT
product from the reference photos. Then the chosen engine (gemini/openai) renders
one image per prompt, guided by those references.

This is the flow extracted from grandia-inventory's catalog-quality "generate-images",
generalised to either engine and decoupled from the audit pipeline.

Examples
--------
  product_shots.py --title "Baterie cădiță retro bronz" --type "Baterii baie" \
      --ref https://cdn.shopify.com/.../baterie1.jpg ./baterie2.jpg --engine gemini

  # just print the 4 prompts, don't render
  product_shots.py --title "Lampă LED birou" --ref ./lamp.jpg --prompts-only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Reuse the core engine + helpers from gen.py (same directory).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen  # noqa: E402

PROMPT_DIRECTOR_SYSTEM = """You are an ecommerce product photography director for a premium online store (home, beauty, fashion or lifestyle products).

You will receive the product's data AND the actual product images will be sent as reference to the image generator. Your prompts MUST instruct the AI to use the EXACT product from the reference images — same shape, proportions, color, finish, material texture, dimensions, design details. The AI must NOT invent a different product.

Generate exactly 4 distinct, detailed image prompts. Each prompt MUST produce a DIFFERENT type of professional ecommerce product image.

THE 4 IMAGE TYPES (one of each, in this order):

1. LIFESTYLE — The EXACT product from the reference images, installed/styled in its natural environment. Show the product as part of a curated, aspirational scene.

2. APPLICATION — The EXACT product from the reference images being actively used or demonstrating its primary function. Show HOW it works, what problem it solves.

3. DETAIL — Extreme close-up (macro) of the EXACT product from the reference images, showcasing real material quality, surface finish, texture, coating, or unique design elements.

4. CONTEXT — Wide shot with the EXACT product from the reference images as part of a complete, designed scene. The product should be visible and recognizable.

CRITICAL PROMPT RULES (each prompt: 120-180 words, in ENGLISH):
- FIRST LINE of every prompt MUST be: "Using the provided reference product images, generate a photorealistic image of THIS EXACT product — preserving its exact shape, color, finish, proportions, and all design details."
- Photography type: photorealistic commercial/editorial photography
- Scene: specific environment, materials, textures, colors of surroundings
- Product placement: exactly how and where the product sits in the frame
- Lighting: specify (natural window light, studio softbox, ambient warm, etc.)
- Camera: angle (eye-level, 45°, overhead, macro), lens type (35mm, 85mm, 100mm macro)
- Explicitly describe the product's REAL features (exact material, color, finish, dimensions if known)
- Background: complementary objects that don't compete with the product
- NO text, NO logos, NO watermarks, NO people's faces, NO hands holding unless the APPLICATION shot needs it
- Aspect ratio: SQUARE (1:1)

Respond STRICTLY as JSON matching the schema."""

PROMPT_SCHEMA = {
    "name": "image_prompts",
    "strict": True,
    "schema": {
        "type": "object",
        "required": ["prompts"],
        "additionalProperties": False,
        "properties": {
            "prompts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["label", "scene_type", "prompt"],
                    "additionalProperties": False,
                    "properties": {
                        "label": {"type": "string", "description": "Short Romanian label"},
                        "scene_type": {"type": "string",
                                       "enum": ["lifestyle", "application", "detail", "context"]},
                        "prompt": {"type": "string", "description": "Full English prompt (120-180 words)"},
                    },
                },
            },
        },
    },
}


def write_prompts(title: str, ptype: str | None, vendor: str | None,
                  extra: str | None) -> list[dict]:
    """Call GPT-4o to produce the 4 ecommerce prompts."""
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("openai not available. Run via `uv run` or `pip install openai`.")

    client = OpenAI(api_key=gen.openai_key(), timeout=120)

    parts = ["## PRODUCT", f"Title: {title}"]
    if ptype:
        parts.append(f"Type: {ptype}")
    if vendor:
        parts.append(f"Brand/Vendor: {vendor}")
    if extra:
        parts += ["", "## EXTRA CONTEXT / NOTES", extra]

    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": PROMPT_DIRECTOR_SYSTEM},
            {"role": "user", "content": "\n".join(parts)},
        ],
        response_format={"type": "json_schema", "json_schema": PROMPT_SCHEMA},
        temperature=0.7,
        max_tokens=4000,
    )
    content = completion.choices[0].message.content
    if not content:
        sys.exit("GPT-4o returned an empty response.")
    data = json.loads(content)
    return data["prompts"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate 4 ecommerce product photos from reference images.")
    ap.add_argument("--title", required=True, help="Product title.")
    ap.add_argument("--type", dest="ptype", default=None, help="Product type/category.")
    ap.add_argument("--vendor", default=None, help="Brand / vendor.")
    ap.add_argument("--notes", default=None, help="Extra context (materials, colour, key specs).")
    ap.add_argument("--ref", nargs="*", default=[],
                    help="Reference product photo(s): local paths or URLs (up to 3 are used).")
    ap.add_argument("--engine", choices=["gemini", "openai"], default="gemini")
    ap.add_argument("--pro", action="store_true", help="Gemini: use Nano Banana Pro.")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"])
    ap.add_argument("--out", default="./generated-images", help="Output directory.")
    ap.add_argument("--nas", action="store_true", help="Also copy outputs to the NAS.")
    ap.add_argument("--prompts-only", action="store_true", help="Only print the 4 prompts; don't render.")
    args = ap.parse_args()

    refs = (args.ref or [])[:3]
    if not args.prompts_only and not refs:
        print("⚠ No reference photos given — the product shots will be far less faithful.\n"
              "  Pass --ref <photo> (or use --prompts-only to just see the prompts).", file=sys.stderr)

    print(f"Directing 4 product prompts for: {args.title!r} …")
    prompts = write_prompts(args.title, args.ptype, args.vendor, args.notes)
    for p in prompts:
        print(f"\n[{p['scene_type'].upper()}] {p['label']}\n{p['prompt']}")

    if args.prompts_only:
        return 0

    slug = "".join(c.lower() if c.isalnum() else "-" for c in args.title).strip("-")[:40] or "product"
    out_dir = Path(args.out).expanduser()
    all_saved: list[Path] = []
    errors: list[str] = []

    for p in prompts:
        print(f"\n→ rendering {p['scene_type']} ({args.engine}) …")
        try:
            imgs = gen.generate(p["prompt"], engine=args.engine, refs=refs, n=1,
                                aspect="1:1", pro=args.pro, quality=args.quality)
            meta = {
                "prompt": p["prompt"], "label": p["label"], "scene_type": p["scene_type"],
                "engine": args.engine, "product_title": args.title,
                "references": refs, "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
            saved = gen.save_images(imgs, out_dir, f"{slug}-{p['scene_type']}", meta, nas=args.nas)
            all_saved += saved
            for s in saved:
                print(f"   ✓ {s}")
        except Exception as e:
            msg = f"{p['scene_type']}: {e}"
            errors.append(msg)
            print(f"   ✗ {msg}", file=sys.stderr)

    print(f"\nDone — {len(all_saved)} image(s) saved, {len(errors)} failed.")
    return 0 if all_saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
