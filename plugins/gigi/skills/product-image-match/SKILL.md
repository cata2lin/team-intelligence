---
name: product-image-match
description: Match products across two catalogs by IMAGE (perceptual hash + CLIP semantic embeddings) plus an attribute gate — when barcodes/EANs are UNRELIABLE for matching (Arona's internal EANs are not real GTINs). Given our products (title+image) and a candidate pool (competitor/marketplace/arona-bi products with title+image+price), it finds the same physical product even when the two sides re-shot/re-hosted the photo. pHash catches near-identical photos (high precision); CLIP (fastembed Qdrant/clip-ViT-B-32-vision, ONNX, CPU, no torch) catches same-product-different-photo (recall); an attribute gate (pack-count, dimensions, wattage) + name-token overlap kills false positives on white-background commodity goods. Built-in mode: Grandia products ↔ arona-bi competitor catalog → optional write into prc_* (competitor price mappings). Use for "match our products to competitor listings", "which competitor product is the same as ours", "catalog dedup by image", "map SKUs across stores", "competitor matching without barcodes", "internal EAN matching", "image similarity product match". Read-only by default; --apply writes the Grandia prc_* mappings.
argument-hint: "--limit N [--clip-min 0.925] [--phash-max 12] [--max-age-days 14] [--apply]"
---

# product-image-match — cross-catalog product matching by image
> Author: Gigi.

**Problema:** EAN-urile Arona sunt **interne** (nu GTIN-uri reale) → nu poți face match pe barcode.
Soluția = match pe **imagine + atribute**, care prinde același produs fizic chiar dacă cele două
cataloage au poze diferite / re-găzduite.

## Cum matchează (precizie + recall)
- **pHash** (perceptual hash, `imagehash`) — Hamming ≤ `--phash-max` (12) = aceeași poză / aproape.
  Precizie MARE, recall MIC (pică dacă cealaltă parte a re-fotografiat produsul).
- **CLIP** (`fastembed`, `Qdrant/clip-ViT-B-32-vision`, ONNX, CPU, **fără torch**) — cosine ≥ `--clip-min`
  (0.925) = același produs semantic, chiar cu poză diferită. Recall MARE.
- **MATCH = (pHash ≤ prag) SAU (CLIP ≥ prag)**, apoi trece printr-un **attribute gate**:
  - pack-count (`set N` / `N buc` / `N plăci`), dimensiuni (NxM cm), watt (N W) — mismatch = respins.
  - match-urile doar-CLIP cer și **overlap de tokeni** în nume (`--clip-min-overlap` 2) → 0 false positives
    pe mărfuri de tip commodity pe fundal alb (unde CLIP dă fals ~0.92 pe produse diferite).

## Rulare (mod built-in: Grandia ↔ arona-bi)
```bash
# dry (nu scrie): matchează produse Grandia active cu catalogul de competiție arona-bi
uv run product_image_match.py --limit 50 --max-age-days 45

# scrie mapările în Grandia prc_competitor_products + prc_competitor_prices
uv run product_image_match.py --max-age-days 45 --apply
```
Creds (via `arona_pg.secret`, env-first + KB): `DATABASE_URL_GRANDIA` (write), `DATABASE_URL_ARONA_BI` (read).

## Parametri de calibrare
| Flag | Default | Efect |
|---|--:|---|
| `--phash-max` | 12 | prag Hamming pHash (mai mic = mai strict) |
| `--clip-min` | 0.925 | prag cosine CLIP (mai mare = mai puține false positives; commodity goods = ține-l sus) |
| `--clip-min-overlap` | 2 | tokeni comuni în nume ceruți pt match doar-CLIP |
| `--max-age-days` | 14 | ignoră prețuri de competitor mai vechi de N zile |
| `--limit` / `--per-product` | — | câte produse sursă / câte match-uri per produs |

## Caveat recall (onest)
Când cataloagele **re-fotografiază** produsele (ex. Grandia vs arona-bi), pHash pică și rămâne doar CLIP →
recall ~15-35% pe commodity hard-goods. E bun ca **supliment** (umple golurile), nu ca sursă unică. Dacă
ambele părți partajează pozele furnizorului, recall-ul urcă mult. **0 false positives** după tuning (prag
CLIP 0.925 + overlap gate) — deci match-urile sunt de încredere chiar dacă nu sunt exhaustive.

## Adaptare la alt brand / catalog
Codul e cablat pe Grandia↔arona-bi (candidate-gen din `products`/`mv_latest_price`, write în `prc_*`).
Pentru alt brand: schimbă query-ul de produse sursă (title+image) + poolul de candidați (title+image+price).
Logica de match (`phash_of`, `clip_embed`, attribute gate) e reutilizabilă ca atare.

> Legat: `gigi:grandia-pricing` (folosește asta ca supliment pt produse fără mapare de competitor),
> `library:scraper-construction`, memoria [[grandia-price-engine-rebuild]].
