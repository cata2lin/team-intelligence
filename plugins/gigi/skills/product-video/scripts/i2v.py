# /// script
# requires-python = ">=3.10"
# dependencies = ["replicate>=1.0", "fal-client>=0.5", "requests>=2.31"]
# ///
"""i2v.py — foto de produs → clip video (image-to-video) prin GPU cloud. Doi provideri: Replicate ȘI fal.ai.

Niciun model video open NU rulează pe Mac → cloud. Token din KB, niciodată printat. ~$0,05–0,15/clip.

  # Replicate (Wan 2.2 fast), token KB REPLICATE_API_TOKEN:
  i2v.py --image "sticla.png" --prompt "sticla se rotește 360°, lumină de studio, reclamă premium"
  # fal.ai (Wan 2.2 A14B), token KB FAL_KEY (are adesea credit gratuit de start):
  i2v.py --provider fal --image "sticla.png" --prompt "..."
  # alegere model explicit:
  i2v.py --provider fal --model fal-ai/ltxv-13b-098-distilled/image-to-video --image X --prompt Y
"""
import os, sys, time, argparse, subprocess

DEFAULT_MODEL = {"replicate": "wan-video/wan-2.2-i2v-fast",
                 "fal": "fal-ai/wan/v2.2-a14b/image-to-video"}


def _kb():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "core", "scripts", "kb.py"))
    for p in (cand, os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")):
        if os.path.exists(p):
            return p
    sys.exit("kb.py negăsit")


def secret(k):
    return subprocess.run(["/bin/zsh", "-lc", f"uv run '{_kb()}' secret-get {k}"],
                          capture_output=True, text=True).stdout.strip()


def _download(url, out):
    import requests
    r = requests.get(url, timeout=300); r.raise_for_status()
    open(out, "wb").write(r.content)


def run_replicate(model, image, prompt, frames, fps, resolution, out):
    tok = secret("REPLICATE_API_TOKEN")
    if not tok:
        sys.exit("lipsește REPLICATE_API_TOKEN din KB")
    os.environ["REPLICATE_API_TOKEN"] = tok
    import replicate
    img_in = image if image.startswith("http") else open(image, "rb")   # URL sau fișier
    try:
        res = replicate.run(model, input={
            "image": img_in, "prompt": prompt,
            "num_frames": frames, "frames_per_second": fps, "resolution": resolution, "go_fast": True})
    except Exception as e:
        if "402" in str(e) or "Insufficient credit" in str(e):
            sys.exit("⚠️ Replicate FĂRĂ CREDIT (402) — replicate.com/account/billing, apoi re-rulează.")
        raise
    item = res[0] if isinstance(res, list) else res
    if hasattr(item, "read"):
        open(out, "wb").write(item.read())
    else:
        _download(str(item), out)


def run_fal(model, image, prompt, frames, fps, resolution, out):
    key = secret("FAL_KEY")
    if not key:
        sys.exit("lipsește FAL_KEY din KB (kb.py secret-set FAL_KEY <key_id:key_secret>) — ia de la fal.ai/dashboard/keys")
    os.environ["FAL_KEY"] = key
    import fal_client
    # URL public (ex. Shopify CDN) → direct, fără upload (upload-ul fal poate da 403 pe unele chei)
    url = image if image.startswith("http") else fal_client.upload_file(image)
    try:
        res = fal_client.subscribe(model, arguments={
            "image_url": url, "prompt": prompt,
            "num_frames": frames, "frames_per_second": fps, "resolution": resolution}, with_logs=False)
    except Exception as e:
        msg = str(e)
        if "402" in msg or "credit" in msg.lower() or "balance" in msg.lower() or "exhaust" in msg.lower():
            sys.exit("⚠️ fal.ai FĂRĂ CREDIT — fal.ai/dashboard/billing, apoi re-rulează.")
        sys.exit(f"eroare fal: {msg[:200]}")
    vid = res.get("video") or res.get("videos", [{}])[0]
    vurl = vid.get("url") if isinstance(vid, dict) else vid
    if not vurl:
        sys.exit(f"fal: fără video în răspuns: {repr(res)[:200]}")
    _download(vurl, out)


def main():
    ap = argparse.ArgumentParser(description="Image-to-video de produs (Replicate sau fal.ai)")
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--provider", default="replicate", choices=["replicate", "fal"])
    ap.add_argument("--model", help="override slug (implicit per provider)")
    ap.add_argument("--out")
    ap.add_argument("--frames", type=int, default=81)
    ap.add_argument("--fps", type=int, default=16)
    ap.add_argument("--resolution", default="480p", choices=["480p", "720p"])
    a = ap.parse_args()

    if a.image.startswith("http"):
        image = a.image                                  # URL public (Shopify CDN etc.)
    elif os.path.exists(os.path.expanduser(a.image)):
        image = os.path.expanduser(a.image)
    else:
        sys.exit(f"imagine inexistentă: {a.image}")
    model = a.model or DEFAULT_MODEL[a.provider]
    out = os.path.expanduser(a.out) if a.out else os.path.splitext(image)[0] + "_i2v.mp4"

    t0 = time.time()
    print(f"→ [{a.provider}] {model}  ({a.frames}/{a.fps}fps ≈ {a.frames/a.fps:.0f}s, {a.resolution})", flush=True)
    (run_fal if a.provider == "fal" else run_replicate)(model, image, a.prompt, a.frames, a.fps, a.resolution, out)
    print(f"✓ {out}  ({os.path.getsize(out)/1e6:.1f} MB, {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
