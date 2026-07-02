---
name: transcribe
description: "Transcribe audio/video to text LOCALLY with faster-whisper (CTranslate2) — no paid API, runs on CPU. Turns UGC videos, downloaded YouTube, sales/support call recordings, voice notes into text (or .srt subtitles) so the LLM can read them cheaply instead of you paying a transcription API. Romanian + multilingual, auto language detection. Use for 'transcrie audio/video', 'ce se spune in clipul asta', 'transcribe this recording', 'subtitrare/SRT', 'text din UGC', 'transcribe call'. First run downloads the model (~150MB)."
argument-hint: "<audio|video> [--model base|small] [--lang ro] [--srt] [--stdout] [--out DIR]"
---

# transcribe — audio/video → text (local, gratis)

Wrapper peste **faster-whisper** (Whisper pe CTranslate2, CPU int8). Transcrie local, **fără API plătit** — pt UGC, YouTube descărcat, înregistrări call-uri, note vocale.

```bash
uv run scripts/transcribe.py clip.mp4 --stdout                 # text la stdout
uv run scripts/transcribe.py voce.mp3 --lang ro                # scrie voce.txt (RO)
uv run scripts/transcribe.py interviu.wav --model small --srt  # subtitrare .srt cu timestamps
uv run scripts/transcribe.py *.mp3 --out ./transcrieri         # lot -> .txt per fisier
```

## Model (viteză vs acuratețe)
`tiny` (cel mai rapid) · **`base`** (default, bun raport) · `small` (mai bun pe RO, mai lent) · `medium`/`large-v3` (cel mai bun, lent pe CPU). Prima rulare cu un model îl descarcă o dată (base ~150MB, small ~500MB).

## Când îl folosești (regula de aur)
- **UGC / video / call-uri** → transcrie o dată, apoi LLM-ul citește TEXTUL (ieftin) în loc de a plăti transcriere API sau a te uita la video.
- Combinat cu `gigi:markitdown` (care face și YouTube URL) — dar `transcribe` merge pe FIȘIERE locale + control pe model/limbă/SRT.
- `--srt` pentru subtitrări (video marketing, reels).

## Note
- Dependență inline `faster-whisper` (aduce ctranslate2 + tokenizers); `uv run`, fără setup. Rulează pe CPU (int8) — nu cere GPU.
- Decodează audio DIN video (mp4/mov) via PyAV; dacă un container exotic pică, extrage audio cu ffmpeg întâi.
- `vad_filter=True` sare tăcerile → mai rapid, mai curat. Limba: `--lang ro` forțează, gol = auto-detect.
- Companion audio/video: `gigi:markitdown` (YouTube URL + audio simplu), `gigi:watch-youtube`.
