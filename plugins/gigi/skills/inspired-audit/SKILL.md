---
name: inspired-audit
description: "Audit and fix the 'inspired by' metafields on ARONA clone-perfume stores (Lab Noir, Esteban, GT, Nubra) — the fields that tell each dupe which luxury fragrance it imitates (custom.inspired_by text + custom.inspired_by_photo image). Flags products MISSING the text or photo, TWO products showing the SAME inspired-by photo (copy-paste bug — the thing that makes '183 & 184 show the same perfume'), duplicate inspired-by text, and fragile fragrantica (fimgs.net) HOTLINKS that can break into a placeholder. rehost downloads those hotlinked images and re-hosts them on Shopify + repoints the metafield. Use for 'verifica inspired by', 'doua produse au aceeasi poza inspired by', 'lipsesc poze inspired by', 'reincarca pozele fragrantica pe shopify', 'audit clone perfume metafields'. Read-only by default. Companion to gigi:shopify-product-images."
argument-hint: "audit --all | audit --brand NUBRA | rehost --brand NUBRA [--apply]"
---

# inspired-audit — audit „inspired by" pe magazinele de parfum-clonă

Fiecare parfum-clonă ARONA are metafield-urile care spun ce parfum de lux imită:
- `custom.inspired_by` — text (ex „Tom Ford — Fucking Fabulous")
- `custom.inspired_by_photo` — url poza parfumului original

```bash
uv run scripts/inspired_audit.py audit --all              # toate magazinele de parfum
uv run scripts/inspired_audit.py audit --brand NUBRA      # un magazin
uv run scripts/inspired_audit.py rehost --brand NUBRA     # DRY: ce poze fragrantica ar re-găzdui
uv run scripts/inspired_audit.py rehost --brand NUBRA --apply   # descarcă + urcă pe Shopify + update metafield
```

## Ce prinde `audit`
- **fără text / fără poză** inspired_by (produs incomplet). Bundle-urile/„Parfum surpriză"/trio-urile n-au sursă unică → normal să apară aici, ignoră-le.
- **POZE DUPLICATE** — același URL de poză pe **>1 produs** = **copy-paste** (ex „183 și 184 arată același parfum inspired-by"). Bug real de reparat manual (pune poza corectă pe al doilea). Grupează + arată produsele afectate.
- **text inspired_by duplicat** — doi clone care numesc același parfum-sursă; poate fi legitim (concentrații diferite) sau copy-paste → **ochi uman**.
- **hotlink fragrantica** (`fimgs.net`/fragrantica) — poza NU e pe Shopify CDN, e împrumutată de pe fragrantica → **se poate rupe** (devine placeholder). `rehost` o repară.

## Ce face `rehost`
Pentru fiecare poză inspired_by pe hotlink fragrantica: **descarcă** (cu Referer fragrantica — altfel 403), **urcă pe Shopify Files** (`stagedUploadsCreate`+`fileCreate`, așteaptă `READY`), apoi **repointează** `custom.inspired_by_photo` la URL-ul Shopify stabil (`metafieldsSet`). Nume fișier `inspired-by-<blend>.jpg`. Dry-run by default; scrie DOAR cu `--apply`.

## Note
- fragrantica servește poza doar cu header **`Referer: fragrantica.com`** (altfel 403) — scriptul îl trimite. ID-ul din URL (`o.<ID>.jpg`) e id-ul parfumului pe fragrantica.
- Auth: app-ul OAuth ARONA (`SHOPIFY_ARONA_<BRAND>_DOMAIN` + `CLIENT_ID/SECRET` din KB), scopes `read/write_products` + `read/write_files`. Tokenul nu se printează.
- Stare iul-2026 la creare: Lab Noir = 0 dupe / 0 hotlink (reparat 03-iul, vezi [[labnoir-theme-fixes]]); GT = 0 dupe (2 reparate); **Nubra = 2 hotlink fragrantica** de re-găzduit. Companion: `gigi:shopify-product-images`.
