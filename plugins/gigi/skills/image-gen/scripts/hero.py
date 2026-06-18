# /// script
# requires-python = ">=3.10"
# ///
"""hero.py — generate a CONTEXTUAL blog hero image (16:9) for an article.

Wraps gen.py with blog-hero defaults so articles get a unique, on-topic visual
instead of reusing the same product photo (which makes every article look alike).

Two modes, both via Gemini (best at preserving a real subject from a reference):
  • WITH --ref <product image url/path>  → the REAL product (e.g. the exact GT
    perfume bottle) is placed into a fresh editorial scene. Use this for perfume
    stores so the bottle is authentic, not invented.
  • WITHOUT --ref → a photoreal lifestyle scene illustrating the topic.

Always pass a --scene describing the article's topic so the image is IN CONTEXT
(e.g. "summer fresh aquatic men's perfume, poolside, citrus, water droplets").

Usage:
  uv run hero.py --store GT --title "Cum alegi un parfum de barbati" \
      --scene "masculine premium, dark slate, warm rim light, dried botanicals" \
      --ref https://cdn.shopify.com/.../bottle.jpg --out /tmp/gen --name gt-pillar

Prints the saved PNG path on the last line (SAVED: <path>) for easy capture.
"""
import argparse, os, re, subprocess, sys
from pathlib import Path

# Per-brand visual style so heroes feel on-brand (prefix OR brand name, case-insensitive).
BRAND_STYLE = {
    "GT": "moody masculine premium atmosphere, dark slate surface, warm golden rim light, cinematic",
    "GEORGE-TALENT": "moody masculine premium atmosphere, dark slate surface, warm golden rim light, cinematic",
    "EST": "luxe accessible maison feel, soft elegant editorial light, warm neutral palette",
    "ESTEBAN": "luxe accessible maison feel, soft elegant editorial light, warm neutral palette",
    "NUB": "value-luxe, clean bright editorial, modern minimal",
    "NUBRA": "value-luxe, clean bright editorial, modern minimal",
    "GRAN": "bright modern Romanian home interior, natural daylight, lived-in and inviting",
    "GRANDIA": "bright modern Romanian home interior, natural daylight, lived-in and inviting",
    "BELA": "fresh clean bright home, crisp and airy, hints of laundry freshness",
    "BELASIL": "fresh clean bright home, crisp and airy, hints of laundry freshness",
}
DEFAULT_STYLE = "clean modern editorial photography, natural light"


def build_prompt(scene: str, style: str, has_ref: bool) -> str:
    base = (
        "Wide editorial blog hero photograph, photorealistic, cinematic lighting, "
        "shallow depth of field, magazine quality. "
    )
    subject = (
        "Feature THIS EXACT product from the reference image — keep its shape, cap, "
        "color and label identical, do not invent a different product. "
        if has_ref else ""
    )
    return (
        base + subject +
        f"Scene: {scene}. Style: {style}. "
        "No text, no captions, no watermarks, no added logos. Aspect 16:9."
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="article title (for the output name + alt)")
    ap.add_argument("--scene", required=True, help="topic-specific scene so the hero is IN CONTEXT")
    ap.add_argument("--store", default="", help="brand/prefix for the visual style (GT, Belasil, Grandia, ...)")
    ap.add_argument("--ref", nargs="*", default=[], help="reference product image url(s)/path(s) — keeps the real product")
    ap.add_argument("--engine", default="gemini")
    ap.add_argument("--aspect", default="16:9")
    ap.add_argument("--out", default="./generated-images")
    ap.add_argument("--name", default="")
    ap.add_argument("--pro", action="store_true", help="higher-quality Gemini model")
    a = ap.parse_args()

    style = BRAND_STYLE.get(a.store.strip().upper(), DEFAULT_STYLE)
    prompt = build_prompt(a.scene, style, bool(a.ref))
    name = a.name or re.sub(r"[^a-z0-9]+", "-", a.title.lower()).strip("-")[:50] + "-hero"

    gen = str(Path(__file__).with_name("gen.py"))
    cmd = ["uv", "run", gen, "--engine", a.engine, "--aspect", a.aspect,
           "--out", a.out, "--name", name, "--prompt", prompt]
    if a.pro:
        cmd.append("--pro")
    if a.ref:
        cmd += ["--ref", *a.ref]
    print("PROMPT:", prompt, file=sys.stderr)
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stderr.write(r.stderr)
    out = r.stdout
    print(out)
    m = re.findall(r"(\S+\.png)", out)
    if r.returncode != 0 or not m:
        raise SystemExit(f"hero generation failed (rc={r.returncode})")
    print("SAVED:", m[0])


if __name__ == "__main__":
    main()
