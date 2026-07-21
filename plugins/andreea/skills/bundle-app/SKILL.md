---
name: bundle-app
description: "Maintain the Esteban (Maison d'Esteban) Set Cadou gift-bundle catalog — find bundles whose component perfume is running low on stock (<100), recommend replacements that are WEAK-SELLING but HIGH-STOCK (>400, same gender, scent-family match) to move dead stock, and swap the storefront `inspired_list` display cards to the replacement's ORIGINAL designer-bottle photo. Prepares the manual component swap (the components are app-owned and can't be swapped via API). Use for: 'bundle-uri cu stoc mic', 'schimba parfumurile din seturile cadou', 'ce parfum pun in loc in bundle', 'inlocuieste componentele fara stoc', 'swap inspired_list photos', 'move dead stock through gift sets', 'refresh Set Cadou'. Triggers: set cadou, bundle, gift set, inspired_list, stoc mic bundle, inlocuire parfum set, componenta epuizata."
argument-hint: "[--low 100] [--pool-stock 400] [--apply-display] [--verify]"
---

# bundle-app
> Autor: **Andreea**. Disponibil pentru toată echipa prin plugin-ul `andreea`.

Întreținere pentru **bundle-urile „Set Cadou" de pe Esteban** (esteban.ro): găsește seturile
a căror componentă-parfum a rămas cu **stoc mic**, propune înlocuitori **cu vânzări slabe dar
stoc mare** (ca să miște stocul mort), potrivit pe **gen** și familie olfactivă, și schimbă
**pozele de „inspired by" din storefront** (`custom.inspired_list`) cu poza **sticlei originale**
a înlocuitorului.

## Ce face
1. **Citește** tot catalogul Esteban din Shopify (produse, stoc live, componente bundle).
2. **Identifică** bundle-urile cu ≥1 componentă sub prag (`--low`, implicit 100).
3. **Recomandă** pentru fiecare componentă slabă un înlocuitor din pool = `stoc > --pool-stock`
   (implicit 400) + **cele mai slabe vânzări** (units 12 luni din metrics), **gen potrivit**
   (tema setului = genul majoritar al componentelor), familie olfactivă apropiată, rotit ca să
   împrăștie stocul mort. Scoate lista **SCOATE No.X → PUNE No.Y**.
4. Cu `--apply-display`: **schimbă `inspired_list`** — creează/reutilizează metaobiectul `perfume`
   {name, image=sticla originală} și rescrie lista cardurilor. Cu `--verify`: re-citește și
   confirmă că fiecare card rezolvă o imagine.

## Usage
```bash
uv run scripts/bundle_app.py                        # raport read-only (SCOATE/PUNE)
uv run scripts/bundle_app.py --low 100 --pool-stock 400 --months 12
uv run scripts/bundle_app.py --apply-display        # SCRIE pozele inspired_list
uv run scripts/bundle_app.py --apply-display --verify
```

## ⚠️ Componenta reală = MANUALĂ (app-owned)
Componentele bundle-ului („Bundled products") sunt **deținute de bundle app**, NU de API-ul
Shopify. `productVariantRelationshipBulkUpdate` eșuează cu
`PRODUCT_EXPANDER_APP_OWNERSHIP_ALREADY_EXISTS`. Deci **swap-ul de componentă se face MANUAL** în
bundle app (pagina produsului → cardul „Bundled products" → edit). Skill-ul pregătește tot în jur:
lista SCOATE/PUNE + pozele inspired_list. **Ordine corectă:** fă întâi swap-ul manual de componentă,
apoi (sau înainte) `--apply-display`; altfel storefront-ul afișează parfumurile noi peste
componentele vechi (setul rămâne 0-stoc până la swap-ul manual). După swap actualizează și
SKU-ul variantei (`cadou-a-b-c`).

## Surse de date / shared libs
- **Shopify Admin API** (versiune 2026-01): stoc, componente, `inspired_list`, `inspired_by_photo`,
  metaobiecte `perfume`. Store rezolvat din secretul KB `SHOPIFY_STORES_CSV` (tokenul nu se
  printează niciodată).
- **metrics Postgres** `order_line_items` (units vândute / N luni) prin **`core/scripts/arona_pg.py`**
  (`connect("DATABASE_URL_METRICS")`, read-only) — nu reimplementa DSN/secret.
- **`data/gender.json`**: SKU → gen (M/W/U). Magazinul n-are câmp de gen, deci genul e hardcodat pe
  parfumul-sursă. **Adaugă parfumuri noi acolo** ca un set bărbătesc să nu primească un parfum de damă.
  SKU necunoscut ⇒ U (compatibil cu orice temă) și e tratat ca unisex.

## Gotchas (cost-de-timp real)
- **`custom.inspired_list`** = `list.metaobject_reference`; fiecare card = metaobiect tip `perfume`
  cu câmpuri `name` (single_line_text) + `image` (file_reference → MediaImage). Poza corectă a
  sticlei originale = `custom.inspired_by_photo` de pe produsul standalone (NU pozele din galerie,
  alea sunt sticla neagră generică d'Esteban).
- Editarea `inspired_list` prin `metafieldsSet` **merge și pe bundle-uri ACTIVE** — schimbă DOAR
  afișarea; componenta app-owned rămâne neatinsă, deci NU învie setul (rămâne 0-stoc până la swap manual).
- **Potrivește cardul de SCOS după NUME, nu după gid-ul imaginii** — un card poate avea un MediaImage
  diferit de `inspired_by_photo` al produsului (upload-uri duplicate ale aceleiași sticle).
- Un ref **duplicat** în listă e respins cu eroare înșelătoare (`Value must belong to the specified
  metaobject definition…`) — cauza reală = înlocuitorul e deja în listă. Skill-ul verifică și sare peste.
- `inspired_list` poate să **NU conțină toate componentele** (o componentă moartă poate lipsi din
  afișare) — atunci se adaugă un card nou pentru înlocuitor.
- Parintele de bundle n-are legitim unitCost/barcode/SKU (cost stă pe componente, `requiresComponents:true`).

## Extensie
Merge pe orice magazin clone-perfume cu aceeași structură (GT/Nubra/Lab Noir) via `--prefix` +
`--brand-slug`, dacă au metaobiecte `perfume` + `inspired_list` + `inspired_by_photo`. Testat pe **EST**.
Companion: `gigi:inspired-audit` (audit metafield-uri inspired_by / rehost fragrantica), `gigi:multi-brand-pnl`.
