---
name: shopify-product-images
description: "Optimize PRODUCT PHOTOS on any Shopify store — (1) add SEO alt text, (2) rename image files per SKU, (3) NORMALIZE the first-image framing so the product sits at a consistent size/position across the whole collection (the thing that makes a collection grid look uneven). Backs up every original first, is SHARED-MEDIA aware (many stores reuse one photo across many products), and applies changes losslessly where possible. Use for 'optimizeaza pozele de produs', 'alt text pe poze', 'redenumeste pozele dupa SKU', 'incadrarea nu e uniforma in colectie', 'pozele sunt mai mici/mai mari', 'normalize product image framing', 'consistent product thumbnails', 'sync product images'. Companion to gigi:shopify-seo (broader on-page SEO) and gigi:image (generation)."
argument-hint: "--domain <shop> [backup|alt-rename|reframe] [--apply] [--target 0.86] [--fix 2,64,176]"
---

# shopify-product-images — optimizator poze de produs (alt · redenumire SKU · încadrare)

Trei lucruri, în ordinea sigură, pe orice magazin Shopify:
1. **Backup** — descarcă TOATE originalele + manifest (produs ↔ SKU ↔ media id ↔ alt). **Mereu primul** — încadrarea reîncarcă pixeli.
2. **Alt text + redenumire după SKU** — metadata, **lossless** (`fileUpdate` schimbă alt + `filename` fără re-upload).
3. **Re-încadrare prima poză** — aduce produsul la aceeași **mărime/poziție** în cadru pe toată colecția (rembg detectează produsul → crop/scale la calitate maximă). **SAMPLE + aprobare înainte de batch.**

```bash
# 0. token per magazin: statice (SHOPIFY_ADMIN_TOKENS/CSV) SAU app OAuth ARONA (Lab Noir/Esteban/GT/Nubra – client_credentials)
uv run scripts/product_images.py --domain 31k0py-bi.myshopify.com backup
uv run scripts/product_images.py --domain 31k0py-bi.myshopify.com alt-rename            # dry-run
uv run scripts/product_images.py --domain 31k0py-bi.myshopify.com alt-rename --apply
uv run --python 3.11 scripts/product_images.py --domain 31k0py-bi.myshopify.com reframe --sample   # face contact-sheet before/after
uv run --python 3.11 scripts/product_images.py --domain 31k0py-bi.myshopify.com reframe --apply     # dupa aprobare
uv run --python 3.11 scripts/product_images.py --domain 31k0py-bi.myshopify.com reframe --apply --fix 2,64,176  # doar anumite produse (cazuri grele)
```

## ⚠️ Capcane dovedite (Lab Noir, iul-2026 — citește ÎNAINTE)
1. **POZE PARTAJATE.** Multe magazine refolosesc UN fișier media pe zeci de produse (Lab Noir: 72 unice pe 54 produse, un fișier pe 22!). Un `fileUpdate` pe media id partajat **schimbă alt/nume pe TOATE** → per-SKU e imposibil pe partajate. Regula: **partajate → alt + nume GENERIC** (`lab-noir-eau-de-parfum-N`); **unice → per-SKU** (`lab-noir-<sku>-N`). Numără aparițiile media id per produs ca să știi care-i care.
2. **`productDeleteMedia` DOAR DETAȘEAZĂ** de la produsul dat — NU șterge global. Co-partajorii rămân intacți (verificat). Deci replace pe prima poză (upload nou + reorder poz.0 + delete vechea) e sigur și pe media partajat.
3. **rembg nu se instalează pe Python 3.13** (numba/llvmlite build fail). Rulează cu **`uv run --python 3.11`** + pinuri `llvmlite==0.42.0 numba==0.59.1` (au wheels). PEP723 din script cere deja `==3.11.*`.
4. **Detecția pe sticlă transparentă/fundal gri** e capricioasă cu metode simple (prind eticheta/capacul). Folosește **rembg u2net** (model complet, nu u2netp) + izolează **componenta cea mai ÎNALTĂ** (= produsul, ignoră capacul de lângă). Centrare orizontală pe **etichetă** (crem, R−B>15) — mai stabilă decât bbox când capacul e lângă.
5. **Capac tăiat sus** = vârful pulverizatorului iese peste bbox → adaugă **buffer sus ~3.5%** la bbox top înainte de crop. **Reflexia de jos** umflă înălțimea → produsul supra-scalat.
6. **Poze compuse** (sticlă+cutie, set 2 sticle) NU se normalizează ca sticla simplă — arată diferit de restul. Caută printre pozele produsului o **sticlă simplă** și pune-o pe aia prima (normalizată), demotează poza cu cutia la secundară.
7. **„fără compresie"** = păstrează 2000px + JPEG **quality 95, subsampling=0**. Re-încadrarea E un re-encode (crop), dar la calitate maximă.
8. **Colizii de nume**: la replace pe produsele cu prima poză UNICĂ, noul upload cu același nume primește sufix → pass final `fileUpdate filename=<vrut>` după ce vechea e ștearsă.

## Cum se face încadrarea (algoritm)
- rembg u2net → mască alfa → `scipy.ndimage.label` → componenta cu **cea mai mare înălțime** = produsul → bbox.
- `y0 -= 0.035*H` (buffer capac). Scale `s = TARGET_BH*H / bbox_h` (TARGET_BH=0.86 = mediana Lab Noir → schimbare minimă). Baseline jos la 0.90-0.92.
- Centrare orizontală pe **eticheta** (crem) dacă e detectată, altfel pe centrul bbox.
- Crop o fereastră cu **ACELAȘI raport** ca poza (păstrează raportul, cerință frecventă), pad cu `mode='edge'` unde depășește, resize LANCZOS la dimensiunea originală, salvează q95.
- **Verifică vizual un contact-sheet** (extreme: cele mai mici + cele mai mari sticle) înainte de a atinge magazinul.

## Auth (token per magazin)
- **Magazine cu token static** (cele 19 din `gigi:xconnector`): `load_shopify_tokens()` → `SHOPIFY_ADMIN_TOKENS`/`SHOPIFY_STORES_CSV` din KB.
- **Magazine pe app-ul OAuth ARONA** (Lab Noir `31k0py-bi`, + Esteban/GT/Nubra): mint token cu **client_credentials** — `POST https://{domain}/admin/oauth/access_token {client_id, client_secret, grant_type:client_credentials}` din secretele KB `SHOPIFY_ARONA_CLIENT_ID/SECRET` + `SHOPIFY_ARONA_<BRAND>_DOMAIN`. App-ul are scopes `read/write_products` + `read/write_files`.
- Tokenul **nu se printează** niciodată.

## Reversibilitate
Backup-ul (originale + manifest) rămâne local. Orice poză se repune: `fileUpdate` pentru alt/nume; upload din backup + reorder pentru pixeli. Nimic nu se șterge din backup.
