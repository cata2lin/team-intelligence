---
name: product-video
description: "Turn a product PHOTO into a short VIDEO clip for social reels / PMax (image-to-video), plus the vetted catalog of open AI video models for the team — image-to-video product ads, talking-head/singing avatars (kids channel, UGC lip-sync), and video background-removal/matting. Runs on GPU CLOUD (Replicate/fal) — none of these run on a Mac. The image-to-video path is WIRED and TESTED (Wan 2.2, Apache 2.0, EU-safe); it just needs Replicate billing credit. Triggers: 'video din poza asta', 'animate the product photo', 'image to video', 'product ad video', 'sticla care se roteste', 'reel din foto', 'video generat AI', 'avatar care canta/vorbeste', 'lip sync', 'talking head', 'remove video background', 'video matting', 'ce modele video open sunt bune', 'genereaza video produs'."
argument-hint: "i2v --image <foto> --prompt <miscare>  (needs REPLICATE_API_TOKEN + credit)"
---

# product-video — foto de produs → clip AI (+ catalog modele video)

Golul din stack-ul nostru: aveam image-gen (foto statică) dar **nu** video generat din foto. Ăsta îl acoperă,
prin **GPU cloud** (Replicate/fal — niciun model video open NU rulează practic pe Mac). Ruta image-to-video
e **cablată și testată** (auth OK, apel corect); așteaptă doar **credit Replicate**.

## Ready-to-run: image-to-video (DOI provideri — Replicate ȘI fal.ai)

```bash
# Replicate (Wan 2.2 fast), token KB REPLICATE_API_TOKEN:
uv run scripts/i2v.py --image "~/Downloads/esteban negru 1.png" \
  --prompt "the perfume bottle slowly rotates 360 degrees, elegant studio lighting, premium fragrance commercial, glass reflections, shallow depth of field"

# fal.ai (Wan 2.2 A14B), token KB FAL_KEY — are adesea credit gratuit de start:
uv run scripts/i2v.py --provider fal --image "~/Downloads/esteban negru 1.png" --prompt "..."
# → *_i2v.mp4  (81 cadre / 16fps ≈ 5s, 480p)
```

- **Replicate**: implicit `wan-video/wan-2.2-i2v-fast` (~$0,05–0,10/clip). Token KB `REPLICATE_API_TOKEN` (cont `gbeschea`). ⚠️ necesită credit (billing) — altfel 402.
- **fal.ai**: implicit `fal-ai/wan/v2.2-a14b/image-to-video`; viteză: `--model fal-ai/ltxv-13b-098-distilled/image-to-video`. Token KB `FAL_KEY` (de la fal.ai/dashboard/keys).
- Ambele: mesaj CLAR dacă lipsește cheia/creditul (nu crapă urât). Calitate mai mare: `--resolution 720p`.
- Cea mai bună intrare = foto de produs cu **fundal curat, sticlă/obiect frontal 3D** (nu etichetă plată).

## Catalogul de modele (cercetat 2026-07-24, doar ce e curat legal + relevant)

### 1. Image-to-Video (reclame de produs) — ✅ implementat mai sus
| Model (HF/Replicate) | Licență | Note |
|---|---|---|
| **Wan-AI/Wan2.2-TI2V-5B** / `wan-video/wan-2.2-i2v-fast` | Apache 2.0 (UE OK) | sweet-spot calitate/viteză, I2V+T2V |
| **Lightricks/LTX-2** | Community (<$10M ARR) | cel mai rapid, audio nativ — pt volum |
| ⛔ **tencent/HunyuanVideo** | licența **EXCLUDE UE/UK/Coreea** | **NU folosi** (ARONA = RO) |

### 2. Talking-head / avatar care CÂNTĂ (canal copii, UGC) — roadmap, experimental
| Model | Licență | Note |
|---|---|---|
| **tencent/HunyuanVideo-Avatar** | Community (sub praguri) | foto+audio → vorbit/**cântat**, exact canalul de copii; lent/imatur |
| **ByteDance/LatentSync-1.6** | OpenRAIL++ (comercial OK) | corecție gură pe UGC (video existent + audio), cea mai bună sincronizare |
| ⚠️ lip-sync open din foto = fragil pe fețe non-frontale + română |

### 3. Video matting / background removal video — roadmap
| Model | Licență | Note |
|---|---|---|
| **PramaLLC/BEN2** | **MIT** (comercial) | cutout produs în clip, ușor — default |
| **facebook/sam2** | Apache 2.0 | tracking obiect (mască dură; combină cu matting) |
| ⛔ **MatAnyone** | **NON-comercial** | calitate top dar NU în reclame |

## Onest (citește înainte să scalezi)
- **Nimic nu e plug-and-play pe Mac** — toate cer GPU cloud. Pilotăm pe Replicate/fal per-clip înainte de infra proprie.
- Ordinea de adopție: (1) I2V produs (gata), (2) BEN2 matting (ieftin, MIT), (3) avatar care cântă (experimental, pilot separat).
- Editare AI (VACE) + upscaling (SeedVR2) = reale dar complexitate mare → faza 2.
- Vezi memoriile [[kids-youtube-channel-research]] (avatarul servește canalul de copii) + [[ai-music-production-stack]].
