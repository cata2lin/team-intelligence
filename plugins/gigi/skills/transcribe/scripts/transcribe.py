# /// script
# requires-python = ">=3.10"
# dependencies = ["faster-whisper"]
# ///
"""Transcrie audio/video în text, LOCAL (faster-whisper / CTranslate2) — fără API plătit.
Pt UGC, YouTube descărcat, înregistrări call-uri. Prima rulare descarcă modelul (base ~150MB).

  uv run transcribe.py <fisier.mp3|mp4|wav|...> [--model base] [--lang ro] [--srt] [--stdout] [--out DIR]
    --model  tiny|base|small|medium|large-v3 (default base; small = mai bun RO, mai lent)
    --lang   cod limba (ro/en/...) sau gol = auto-detect
    --srt    scoate .srt cu timestamps (subtitrare) in loc de text simplu
"""
import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from faster_whisper import WhisperModel

def ts(t):
    h=int(t//3600); m=int((t%3600)//60); s=t%60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--model", default="base")
    ap.add_argument("--lang", default=None)
    ap.add_argument("--srt", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.out: os.makedirs(a.out, exist_ok=True)
    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    for src in a.inputs:
        segs, info = model.transcribe(src, language=a.lang, vad_filter=True)
        lines = []
        for i, s in enumerate(segs, 1):
            if a.srt:
                lines.append(f"{i}\n{ts(s.start)} --> {ts(s.end)}\n{s.text.strip()}\n")
            else:
                lines.append(s.text.strip())
        text = "\n".join(lines) if a.srt else " ".join(lines)
        head = f"[{os.path.basename(src)} · limba={info.language} · {info.duration:.0f}s]"
        if a.stdout:
            print(f"\n===== {head} =====\n{text}")
        else:
            ext = ".srt" if a.srt else ".txt"
            base = os.path.basename(src).rsplit(".", 1)[0]
            dest = os.path.join(a.out or (os.path.dirname(os.path.abspath(src))), base + ext)
            open(dest, "w", encoding="utf-8").write(text)
            print(f"{head} -> {dest}  ({len(text)} char ~ {len(text)//4} tok)")

if __name__ == "__main__":
    main()
