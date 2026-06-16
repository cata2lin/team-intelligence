---
name: ad-banners
description: Produce premium static ad banners (Google Ads PMax / Performance, Meta, TikTok) from a brand's real product photos — background removal (rembg cutout), a dark "cutout + warm glow" layout template, and per-element capture at exact native size. Pulls source photos from the NAS and brand styling (colour, wordmark) from BRAND_REFERENCE in the KB; knows the Google Ads image dimension / field-type rules and the 20-image-per-asset-group cap. Use whenever you need on-brand banner creatives instead of stock-looking boxes.
---

# Ad banners — premium creatives from real product photos

Turn a brand's existing product photography into clean, on-brand ad banners. The
house style is **dark background + a background-removed product cutout + a warm
radial glow + text on the left** — it reads premium and avoids the "white product
box next to a colour block" stock look.

Pairs with **`gigi:google-ads-mcc`** (which uploads + attaches the finished PNGs to
PMax asset groups) and reads/writes **`BRAND_REFERENCE`** in the KB.

## When to use
- "improve the ad assets / make the asset group EXCELLENT" and images are the gap.
- "pull creatives from the NAS / use real product photos in the ads."
- "remove the background from the photo" (cutout for a banner or a transparent hero).
- Any time an asset group is short on **landscape (1.91:1)** or **square (1:1)** images.

## Pipeline

### 1. Get source photos (NAS)
Brand photos live on the NAS under `~/nas/Projects/<BRAND>/` (e.g. `ESTEBAN`,
`BELASIL`). Good source folders: `03_GRAPHICS_PRINT` (renders, listing cutouts,
`poze_produs_plastic`), `01_EDITS`/`02_EDITS` (edited statics), `04_UGC`,
`_lifestyle`. Copy a handful of candidates locally and build a contact sheet to
pick the hero (PIL montage — see the recon snippet below).

> **Pick a hero that survives a dark background.** A matte-black bottle disappears
> on a dark banner. Prefer an **amber/coloured/glass** bottle, a render with a
> light label panel, or rely on the glow.

> **Verify the LABEL ERA before you commit — brands rebrand.** Old NAS shoots often
> show retired packaging. Check the brand's `label_current` in `BRAND_REFERENCE` and
> glance at the **live site** to confirm what the current bottle label reads, then
> Read candidate photos at full resolution to confirm the label matches. (Esteban
> rebranded to **"Maison d'Esteban"**: only `01_EDITS/2025 12 04 - Colorate`
> [DSC01490/487 single, DSC01516/523 trio] shows the new label — every other folder,
> and even esteban.ro's product photos, still show the old "Esteban / Essential"
> label. Picking an old-label hero meant redoing the whole set.) The bottle label and
> the banner wordmark must agree. Flat-lay (lying-down) single-bottle shots are fine —
> `cutout.py` callers can auto-upright them (cv2 `minAreaRect` deskew + cap-up by
> narrower-end heuristic).

### 2. Remove the background → transparent cutout
One-time env setup (rembg won't `pip install` cleanly on Python 3.13 because
`pymatting`→`numba`→`llvmlite` fails to build; `setup_env.sh` installs rembg
without it and stubs the unused import):

```bash
bash scripts/setup_env.sh /path/to/.venv          # once per machine/venv
/path/to/.venv/bin/python scripts/cutout.py --dir nas_src/ --out-dir cuts/
```

`cutout.py` removes the background (rembg `isnet-general-use`), trims to a tight
bbox, and saves a transparent PNG with its aspect ratio printed. Copy the chosen
cutouts next to `banners.html` and note each `width/height` for the CONFIG.

> This is **not** `remove_watermark.py` (repo root) — that only erases the Gemini ✦
> corner mark via inpainting. Background removal is a different job → use `cutout.py`.

### 3. Fill the template CONFIG
Copy `scripts/banners.html` into your working dir and edit the `CFG` block:
- `accent` ← `brand_color`, `wordmark` ← `shop_name` (uppercased) — pull both from
  `BRAND_REFERENCE` (`brandref.py get <brand>` in the google-ads-mcc skill).
- `bottles` ← your cutout filenames + their `ar` (width/height from step 2).
- `concepts` ← short RO `[headline, subtitle]` pairs (keep headline ≤ ~22 chars so
  the square doesn't wrap badly).
- `render` ← which banners to emit. **Always produce both** 1200×628 (landscape)
  **and** 1200×1200 (square) — those are the two ratios PMax asset groups are
  usually short on.

### 4. Render + capture at native size (chrome-devtools MCP)
```
navigate_page  file:///…/banners.html
take_snapshot                         # gives a uid per banner (image "est_ls_…")
take_screenshot  uid=<each>  filePath=…/est_ls_feminin.png   # one per banner
```
Element screenshots come out at exact CSS size (1200×628 / 1200×1200) at DPR 1.
**Never `sips`-pad them** — 1200×628 is already a valid 1.91:1 (above Google's
600×314 minimum). Padding just adds dark borders.

### 5. Upload + attach (hand off to google-ads-mcc)
Use `gigi:google-ads-mcc/add_pmax_images.py` (env `CIDARG`, `AGARG`, `DIRARG`,
`IMGSARG`). See the cap gotcha below.

## Google Ads PMax image cheat-sheet
| Field type | Ratio | Use our size | Min |
|---|---|---|---|
| `MARKETING_IMAGE` | 1.91:1 | 1200×628 | 600×314 |
| `SQUARE_MARKETING_IMAGE` | 1:1 | 1200×1200 | 300×300 |
| `PORTRAIT_MARKETING_IMAGE` | 4:5 | 1080×1350 | 480×600 |
| `LOGO` | 1:1 | 1200×1200 | 128×128 |
| `LANDSCAPE_LOGO` | 4:1 | 1200×300 | 512×128 |

- **Max 20 images TOTAL per asset group** (landscape + square + portrait combined).
  Ad strength rewards having several of **each** ratio; aim for a balance like
  **5 / 5 / 10**, not 3 / 3 / 14.
- File < 5 MB. Brand assets (BUSINESS_NAME + LOGO) can live at the campaign level
  and cover every group — don't re-add them per group.

### The 20-image cap gotcha (important)
If a group is already at 20 images, `assetGroupAssets:mutate` create ops fail with
`resourceCountLimitExceededError` (`ENABLED_IMAGE`). The image *assets* still get
created (they just sit unlinked in the library). Fix: in **one atomic mutate**,
`remove` N redundant links (usually surplus portraits) **then** `create` the N new
links — net stays ≤ 20. Find the existing asset resource_names with a GAQL report
on `asset_group_asset` filtered by `field_type`, and reuse the already-created
image assets (look them up by `asset.name`) instead of creating duplicates.

## KB integration (read + write)
- **Read** brand styling so you don't re-derive it: `brandref.py get esteban`
  → `brand_color`, `shop_name`, `domain`, `gads_cid`, `gads_pmax`.
- **Write** back what you learn: after a successful upload, record the asset-group
  composition / which cutouts you used under the brand's `creatives` field
  (`brandref.py set <brand> creatives '<json>'`) so the next run starts from state.

## Recon snippet (contact sheet to pick a hero)
```python
from PIL import Image, ImageDraw; import glob, os
files=sorted(glob.glob("nas_src/*.jpg")); cell=380; cols=3
rows=(len(files)+cols-1)//cols; pad=12; lab=20
sheet=Image.new("RGB",(cols*cell+(cols+1)*pad, rows*(cell+lab)+(rows+1)*pad),(40,40,46))
d=ImageDraw.Draw(sheet)
for i,f in enumerate(files):
    im=Image.open(f).convert("RGB"); im.thumbnail((cell,cell)); r,c=divmod(i,cols)
    x=pad+c*(cell+pad)+(cell-im.width)//2; y=pad+r*(cell+lab+pad)
    sheet.paste(im,(x,y)); d.text((pad+c*(cell+pad),y+cell+4), os.path.basename(f), fill=(230,230,230))
sheet.save("_contact.png")
```

## Gotchas
- **Dark product on dark bg = invisible.** Use a coloured/amber hero or lean on the glow.
- **Glass is translucent.** rembg cuts the silhouette cleanly; opaque/amber liquid
  reads best on the new background. Inspect the cutout on a dark panel before using.
- **Capture per element**, not full-page — full-page screenshots are scaled down.
- **No sips padding.** Native element size is already the right ratio.
- Keep headlines short for the square; the cutout takes ~60–70% of the width.
