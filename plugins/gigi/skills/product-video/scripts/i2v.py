# /// script
# requires-python = ">=3.10"
# dependencies = ["replicate>=1.0"]
# ///
"""i2v.py — foto de produs → clip video (image-to-video) prin Replicate (GPU cloud, NU local pe Mac).

Model implicit: Wan 2.2 I2V fast (Apache 2.0, comercial + UE OK). Tokenul din KB `REPLICATE_API_TOKEN`,
niciodată printat. Cost ~$0,05–0,10/clip; contul Replicate trebuie să aibă credit (altfel 402).

  i2v.py --image "sticla.png" --prompt "sticla se rotește 360°, lumină de studio, reclamă premium"
  i2v.py --image X --prompt Y --model wan-video/wan-2.2-i2v-a14b --resolution 720p --out clip.mp4
"""
import os, sys, time, argparse, subprocess


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


def main():
    ap = argparse.ArgumentParser(description="Image-to-video de produs prin Replicate")
    ap.add_argument("--image", required=True, help="cale foto de produs (sticlă/obiect, fundal curat = cel mai bun)")
    ap.add_argument("--prompt", required=True, help="ce mișcare/atmosferă (RO sau EN)")
    ap.add_argument("--model", default="wan-video/wan-2.2-i2v-fast",
                    help="wan-video/wan-2.2-i2v-fast (implicit) | wan-video/wan-2.2-i2v-a14b (calitate)")
    ap.add_argument("--out", help="mp4 de ieșire (implicit lângă imagine)")
    ap.add_argument("--frames", type=int, default=81)
    ap.add_argument("--fps", type=int, default=16)
    ap.add_argument("--resolution", default="480p", choices=["480p", "720p"])
    a = ap.parse_args()

    if not os.path.exists(a.image):
        sys.exit(f"imagine inexistentă: {a.image}")
    tok = secret("REPLICATE_API_TOKEN")
    if not tok:
        sys.exit("lipsește REPLICATE_API_TOKEN din KB (kb.py secret-set REPLICATE_API_TOKEN r8_...)")
    os.environ["REPLICATE_API_TOKEN"] = tok
    out = a.out or os.path.splitext(a.image)[0] + "_i2v.mp4"

    import replicate
    t0 = time.time()
    print(f"→ {a.model}  ({a.frames} cadre / {a.fps}fps ≈ {a.frames/a.fps:.0f}s, {a.resolution})", flush=True)
    try:
        res = replicate.run(a.model, input={
            "image": open(a.image, "rb"), "prompt": a.prompt,
            "num_frames": a.frames, "frames_per_second": a.fps, "resolution": a.resolution, "go_fast": True,
        })
    except Exception as e:
        msg = str(e)
        if "402" in msg or "Insufficient credit" in msg:
            sys.exit("⚠️ Replicate FĂRĂ CREDIT (402) — adaugă card la replicate.com/account/billing, apoi re-rulează.")
        sys.exit(f"eroare Replicate: {msg[:200]}")

    item = res[0] if isinstance(res, list) else res
    if hasattr(item, "read"):
        open(out, "wb").write(item.read())
    elif isinstance(item, str) and item.startswith("http"):
        import urllib.request; urllib.request.urlretrieve(item, out)
    else:
        sys.exit(f"output neașteptat: {repr(item)[:160]}")
    print(f"✓ {out}  ({os.path.getsize(out)/1e6:.1f} MB, {time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
