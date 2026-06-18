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
gen.py — general image generation from a PROMPT, with optional reference photos.

Two engines, selectable at runtime (--engine):
  • gemini  : Gemini "nano-banana" (gemini-2.5-flash-image) or pro
              (gemini-3-pro-image-preview with --pro). Best at preserving an
              EXACT subject from reference photos. Uses GEMINI_API_KEY.
  • openai  : gpt-image-1. images.generate (no refs) or images.edit (with refs).
              Uses OPENAI_API_KEY.

Keys are pulled from the team KB (SharedClaude secrets) automatically, with an
env-var override. Nothing is printed that reveals a secret value.

Output: PNG files written locally (--out, default ./generated-images) and,
with --nas, also copied to $NAS_ROOT/exports/generated-images/<date>/. A sidecar
.json records the prompt, engine, model and reference images for each render.

Examples
--------
  # plain text-to-image (pick engine)
  gen.py --prompt "a cozy scandinavian living room, morning light, photoreal" --engine gemini
  gen.py --prompt "minimal product hero on seamless beige, soft shadow" --engine openai -n 3

  # guided by reference photos (keeps the exact subject)
  gen.py --prompt "Using the reference, place THIS EXACT lamp in a modern bedroom" \
         --engine gemini --ref ./lamp1.jpg ./lamp2.jpg

  # importable: from gen import generate
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import mimetypes
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent
KB_PY = HERE.parent.parent.parent.parent / "core" / "scripts" / "kb.py"  # plugins/core/scripts/kb.py

GEMINI_FLASH = "gemini-2.5-flash-image"        # Nano Banana — fast, faithful to refs
GEMINI_PRO = "gemini-3-pro-image-preview"      # Nano Banana Pro — higher quality
OPENAI_IMAGE_MODEL = "gpt-image-1"

GEMINI_ASPECTS = {"1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "5:4", "4:5", "3:2", "2:3"}

# gpt-image-1 only supports a few fixed sizes; map an aspect onto the nearest one.
OPENAI_SIZE_FOR_ASPECT = {
    "1:1": "1024x1024",
    "4:5": "1024x1536", "3:4": "1024x1536", "9:16": "1024x1536", "2:3": "1024x1536",
    "5:4": "1536x1024", "4:3": "1536x1024", "16:9": "1536x1024", "3:2": "1536x1024",
}


# ─────────────────────────────────────────────────────────────────────
# Secrets (team KB) — never print the value
# ─────────────────────────────────────────────────────────────────────

def kb_secret(key: str) -> str | None:
    """env var wins; otherwise `uv run kb.py secret-get KEY`."""
    val = os.environ.get(key)
    if val:
        return val.strip()
    if not KB_PY.exists():
        return None
    try:
        out = subprocess.run(
            ["uv", "run", str(KB_PY), "secret-get", key],
            capture_output=True, text=True, timeout=60,
        )
        v = (out.stdout or "").strip()
        return v or None
    except Exception:
        return None


def gemini_key() -> str:
    k = kb_secret("GEMINI_API_KEY") or kb_secret("GOOGLE_AI_API_KEY")
    if not k:
        sys.exit("No Gemini key. Put GEMINI_API_KEY (or GOOGLE_AI_API_KEY) in the KB "
                 "(kb.py secret-set GEMINI_API_KEY …) or export it.")
    return k


def openai_key() -> str:
    k = kb_secret("OPENAI_API_KEY")
    if not k:
        sys.exit("No OpenAI key. Put OPENAI_API_KEY in the KB "
                 "(kb.py secret-set OPENAI_API_KEY …) or export it.")
    return k


# ─────────────────────────────────────────────────────────────────────
# Reference image loading (local path or URL → bytes + mime)
# ─────────────────────────────────────────────────────────────────────

class RefImage:
    __slots__ = ("data", "mime", "source")

    def __init__(self, data: bytes, mime: str, source: str):
        self.data = data
        self.mime = mime
        self.source = source


def load_reference(src: str) -> RefImage:
    """src is a local file path or an http(s) URL."""
    if src.startswith("http://") or src.startswith("https://"):
        import requests
        url = src
        # Shrink huge Shopify CDN PNGs so the upload is fast and small.
        if "cdn.shopify.com" in url or "/cdn/shop/" in url:
            base = url.split("?", 1)[0]
            url = f"{base}?width=1024&format=jpg"
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        if not r.content:
            raise ValueError(f"empty body from {src}")
        mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0]
        return RefImage(r.content, mime, src)
    p = Path(src).expanduser()
    if not p.exists():
        raise FileNotFoundError(src)
    mime = mimetypes.guess_type(str(p))[0] or "image/jpeg"
    return RefImage(p.read_bytes(), mime, str(p))


def load_references(srcs: list[str] | None) -> list[RefImage]:
    refs: list[RefImage] = []
    for s in (srcs or []):
        try:
            refs.append(load_reference(s))
        except Exception as e:
            print(f"  ⚠ reference skipped ({s}): {e}", file=sys.stderr)
    return refs


# ─────────────────────────────────────────────────────────────────────
# Engines → return list of (png_bytes, mime)
# ─────────────────────────────────────────────────────────────────────

def _gemini_generate(prompt: str, refs: list[RefImage], n: int, aspect: str,
                     pro: bool) -> list[tuple[bytes, str]]:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("google-genai not available. Run via `uv run` (deps are declared "
                 "inline) or `pip install google-genai`.")

    client = genai.Client(api_key=gemini_key())
    model = GEMINI_PRO if pro else GEMINI_FLASH
    ratio = aspect if aspect in GEMINI_ASPECTS else "1:1"

    parts: list = []
    for ref in refs:
        parts.append(types.Part.from_bytes(data=ref.data, mime_type=ref.mime))
    parts.append(prompt)

    cfg = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        image_config=types.ImageConfig(aspect_ratio=ratio),
    )

    out: list[tuple[bytes, str]] = []
    # Gemini returns one image per call → loop for N variants.
    for i in range(n):
        resp = client.models.generate_content(model=model, contents=parts, config=cfg)
        got = None
        cands = resp.candidates or []
        for part in (cands[0].content.parts if cands and cands[0].content else []):
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None) and \
               (inline.mime_type or "").startswith("image/"):
                got = (inline.data, inline.mime_type or "image/png")
                break
        if got is None:
            txt = ""
            try:
                txt = resp.text or ""
            except Exception:
                pass
            raise RuntimeError(
                f"Gemini returned no image (variant {i+1}). "
                + (f"Model said: {txt[:200]}" if txt else "Often a safety refusal — edit the prompt.")
            )
        out.append(got)
    return out


def _openai_generate(prompt: str, refs: list[RefImage], n: int, aspect: str,
                     quality: str) -> list[tuple[bytes, str]]:
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("openai not available. Run via `uv run` or `pip install openai`.")

    client = OpenAI(api_key=openai_key(), timeout=300)
    size = OPENAI_SIZE_FOR_ASPECT.get(aspect, "1024x1024")

    if refs:
        files = [(f"ref{i}{mimetypes.guess_extension(r.mime) or '.png'}", io.BytesIO(r.data), r.mime)
                 for i, r in enumerate(refs)]
        resp = client.images.edit(
            model=OPENAI_IMAGE_MODEL,
            image=[f for f in files],
            prompt=prompt,
            n=n,
            size=size,
            quality=quality,
        )
    else:
        resp = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            n=n,
            size=size,
            quality=quality,
        )

    out: list[tuple[bytes, str]] = []
    for d in resp.data:
        if getattr(d, "b64_json", None):
            out.append((base64.b64decode(d.b64_json), "image/png"))
        elif getattr(d, "url", None):
            import requests
            rr = requests.get(d.url, timeout=60)
            rr.raise_for_status()
            out.append((rr.content, "image/png"))
    if not out:
        raise RuntimeError("OpenAI returned no image data.")
    return out


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────

def generate(prompt: str, *, engine: str = "gemini", refs: list[str] | None = None,
             n: int = 1, aspect: str = "1:1", pro: bool = False,
             quality: str = "high") -> list[tuple[bytes, str]]:
    """Generate `n` images for `prompt`. Returns list of (png_bytes, mime)."""
    ref_imgs = load_references(refs)
    if engine == "gemini":
        return _gemini_generate(prompt, ref_imgs, n, aspect, pro)
    if engine == "openai":
        return _openai_generate(prompt, ref_imgs, n, aspect, quality)
    raise ValueError(f"unknown engine: {engine!r} (use 'gemini' or 'openai')")


def save_images(images: list[tuple[bytes, str]], out_dir: Path, name: str,
                meta: dict, nas: bool = False) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved: list[Path] = []
    for i, (data, _mime) in enumerate(images, 1):
        suffix = f"_{i}" if len(images) > 1 else ""
        path = out_dir / f"{name}_{stamp}{suffix}.png"
        path.write_bytes(data)
        saved.append(path)
    sidecar = out_dir / f"{name}_{stamp}.json"
    sidecar.write_text(json.dumps({**meta, "files": [p.name for p in saved]},
                                  ensure_ascii=False, indent=2))

    if nas:
        nas_root = os.environ.get("NAS_ROOT")
        if nas_root:
            nas_dir = Path(nas_root) / "exports" / "generated-images" / datetime.now().strftime("%Y-%m-%d")
            try:
                nas_dir.mkdir(parents=True, exist_ok=True)
                for p in saved + [sidecar]:
                    (nas_dir / p.name).write_bytes(p.read_bytes())
                print(f"  ↳ copied {len(saved)} file(s) to NAS: {nas_dir}")
            except Exception as e:
                print(f"  ⚠ NAS copy failed: {e}", file=sys.stderr)
        else:
            print("  ⚠ --nas set but $NAS_ROOT is empty; skipped NAS copy.", file=sys.stderr)
    return saved


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Generate images from a prompt (Gemini or OpenAI).")
    ap.add_argument("--prompt", required=True, help="Text prompt describing the image.")
    ap.add_argument("--engine", choices=["gemini", "openai"], default="gemini",
                    help="Pixel engine (default: gemini / nano-banana).")
    ap.add_argument("--ref", nargs="*", default=[],
                    help="Reference image(s): local paths or URLs (subject to preserve).")
    ap.add_argument("-n", "--count", type=int, default=1, help="Number of variants (default 1).")
    ap.add_argument("--aspect", default="1:1", help="Aspect ratio, e.g. 1:1, 16:9, 4:5 (default 1:1).")
    ap.add_argument("--pro", action="store_true",
                    help="Gemini only: use Nano Banana Pro (gemini-3-pro-image-preview).")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high", "auto"],
                    help="OpenAI only: image quality (default high).")
    ap.add_argument("--out", default="./generated-images", help="Output directory.")
    ap.add_argument("--name", default="image", help="Filename slug (default 'image').")
    ap.add_argument("--nas", action="store_true", help="Also copy outputs to $NAS_ROOT/exports/generated-images.")
    args = ap.parse_args()

    refs = args.ref or []
    print(f"Engine: {args.engine}"
          + (f" (pro)" if args.pro and args.engine == "gemini" else "")
          + f" · variants: {args.count} · aspect: {args.aspect}"
          + (f" · refs: {len(refs)}" if refs else " · no refs"))
    try:
        images = generate(args.prompt, engine=args.engine, refs=refs, n=args.count,
                          aspect=args.aspect, pro=args.pro, quality=args.quality)
    except SystemExit:
        raise
    except Exception as e:
        print(f"✗ generation failed: {e}", file=sys.stderr)
        return 1

    meta = {
        "prompt": args.prompt,
        "engine": args.engine,
        "model": (GEMINI_PRO if args.pro else GEMINI_FLASH) if args.engine == "gemini" else OPENAI_IMAGE_MODEL,
        "aspect": args.aspect,
        "references": refs,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    saved = save_images(images, Path(args.out).expanduser(), args.name, meta, nas=args.nas)
    print(f"✓ saved {len(saved)} image(s):")
    for p in saved:
        print(f"   {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
