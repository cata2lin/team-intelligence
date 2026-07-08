---
name: shopify-kit-landing
description: Build a custom "kit + free bundled items" conversion landing page on ANY Shopify store — as a Shopify PAGE (not a product template) — with a bundle selector (pick a kit tier, then pick N items), the bundled items added to cart FREE via an automatic BXGY discount (real line items, so they show on the AWB/packing slip), and cart-linking so removing the kit auto-removes the items and the items can't be removed individually. Also covers: in-stock filtering from live product data, real reviews via the Judge.me API (Judge.me widgets DO NOT render on a Page — must fetch + render static), colour/variant swatch photos sourced by SKU code from local files/Drive/priced-twins + uploaded to Shopify Files, a lite-YouTube video facade (loads only on click), the Page section+template deploy pattern with 1-file rollback, and the Shopify/Cloudflare cache gotcha. Reference build = ROSSI Nails dip-powder kit (/pages/kit-pudra-unghii). Use for "kit/bundle landing page", "pick N colours free with a kit", "buy X get Y free bundle on a landing", "custom product selector on a Shopify page", "free items linked to a kit".
argument-hint: "(playbook — adapt scripts/ to your store: kit product ids, powder collection id, variant ids)"
---

# shopify-kit-landing — custom "kit + free bundled items" landing on Shopify
> Author: Gigi. Reference build: **ROSSI dip kit** (`rossinails.ro/pages/kit-pudra-unghii`), see [[rossi-dip-landing]].

Builds a high-conversion landing where the customer picks a **kit tier** (e.g. 1/3/6 items) then picks that many **items** (colours), which are added to cart **free** and **bundled** to the kit. Real line items (on the AWB), not properties.

## Arhitectura care MERGE (dovedită, după multe iterații)
1. **Page nou (NU product template)**: secțiune Liquid `sections/<name>.liquid` (namespace CSS, HTML/CSS/JS self-contained, wrap `{% raw %}` + `{% schema %}`) + template `templates/page.<suffix>.json` (doar secțiunea = full-width, fără titlu de temă). Page = shell cu `template_suffix`. **Rollback = șterge templates/page.<suffix>.json**. Vezi `scripts/deploy.py`.
2. **Selector custom** (JS în secțiune): tier chooser → grilă de item-uri (poze reale) → pick exact N (max forțat) → `POST /cart/add.js` cu **kit + variantele item-urilor** (proprietăți: pe kit „Alegere 1..N", pe item „Inclus în kit"). Vezi `scripts/landing.html`.
3. **Item-urile GRATIS = discount automat BXGY**: „Buy 1 [kit tier], get N [din colecția de item-uri] 100% off". Câte un BXGY per tier (1/3/6). Item-urile rămân **line-items reale** (pe AWB) dar apar 0,00 (~~preț~~ + „-preț"). Vezi `scripts/bxgy_discounts.py`. ⚠️ BXGY se aplică doar când sunt exact N item-uri — selectorul forțează N, deci OK.
4. **Legare kit↔item-uri în tema** (`assets/custom.js`, 2 blocuri): (a) **watcher** pe `cart:updated` — dacă nu mai e niciun kit (`product_id ∈ KIT_IDS`) → scoate liniile cu proprietatea „Inclus în kit" (`cart/update.js`). Scoți kitul → item-urile dispar. (b) **lock** — liniile cu „Inclus în kit" → ascunde `.cart__remove`+qty + tag „🎁 Inclus gratis". Nu poți scoate un item singur. (Impulse theme; adaptează selectorii `.cart__item`/`.cart__remove`.)

## Capcane REZOLVATE (citește înainte)
- ⚠️ **Judge.me widgets NU se hidratează pe un Page** (nici product widget, nici carousel — rămân goale/spinner). → **trage recenzii reale prin Judge.me API** (`JUDGEME_<BRAND>_PRIVATE_TOKEN`, shop_domain=myshopify) + randează STATIC. Media reală din distribuție (`reviews/count?rating=N`), nu inventa 4.9.
- ⚠️ **CACHE Shopify/Cloudflare**: după deploy, pagina se poate servi stale în browser câteva min. Cloudflare o servește `DYNAMIC` (fresh) — verifică **ASSET-ul via Admin API** (`themes/../assets.json`), NU pagina în browserul tău. Userul: **hard-refresh (Cmd+Shift+R)**. Deploy-uri rapide succesive = cache confuz → nu diagnostica „nu merge" din browser fără hard-refresh.
- ⚠️ **Font heading al temei** pe `<h4>`/heading-urile tale = uppercase + letter-spacing → taie cuvinte lungi + diacritice urâte. Fix: `.wrap h1,h2,h3,h4{font-family:Poppins,-apple-system,... !important;text-transform:none !important;letter-spacing:-.2px !important}`.
- ⚠️ **Tabele pe mobil**: dă clasă + media query (`font-size:12px;padding:8px 4px`) ca să nu se taie coloane.
- **Poze swatch prin COD SKU**: dacă item-urile n-au poza „de rezultat", caută-le local/Drive după codul SKU (partea după prefix, ex `R203-<COD>`) ȘI după denumire normalizată (ex „feelincozy"), prefer versiunea „...2" (a 2-a poză). Upload în Shopify Files (staged FILE→IMAGE). Vezi `scripts/downloads_photos.py` + `scripts/drive_fix_photos.py`. Priced-twin (produsul full-price al aceleiași culori) are deja poza-unghie #2.
- **Full-price + BXGY vs zero-price**: produsele full-price au deja poza #2 → folosește-le în selector + BXGY le face gratis. (Alternativ: produse zero-price numite exact ca item-ul + scriptul „Sky remove bundle" existent al temei.)
- **In-stock filter**: `avail` per item în datele injectate + `drawGrid` filtrează `c.avail`. Snapshot — reface când se schimbă stocul (sau live-fetch `/products.json`).
- **Familii de culoare (filtru chips)**: numele nu spun culoarea → clasifică prin **vision** (Gemini `gemini-2.5-flash`, batch de imagini → familie din taxonomie fixă).
- **Video rapid**: **facade lite-YouTube** — poster + play, `<iframe youtube/embed?autoplay=1>` injectat DOAR la click. Zero impact pe viteză. (Videoul e deja pe canalul de YouTube al brandului — caută-l, nu-l reurca.)
- **PageSpeed API (`pagespeedonline/v5`) e blocat pe cheia GOOGLE_AI** (403). Folosește **Lighthouse via chrome-devtools** (`lighthouse_audit` = SEO/a11y/BP; `performance_start_trace` = perf) + Performance API direct (load/TTFB/transferSize/resurse).

## Cum se folosește (adaptare la alt magazin/produs)
1. `scripts/deploy.py` — setează `HANDLE`, `SUFFIX`, `HERO`, tema live (role main). Injectează `colors.json`/`reviews.json` în `landing.html`.
2. `scripts/landing.html` — tokeni `__COLORS__`/`__REVIEWS__`/`__HERO__`; `TIERS` (variante kit + N + preț); namespace CSS.
3. `scripts/bxgy_discounts.py` — setează `COLL` (colecția item-urilor) + `KITS` (id produs kit + N per tier).
4. Adaugă blocurile watcher+lock în `assets/custom.js` (KIT_IDS + proprietatea „Inclus în kit").
5. Reviews: Judge.me API. Poze: după cod SKU. Familii: vision. Video: facade.

Store access via `gigi:shopify-stores` (token din `SHOPIFY_STORES_CSV`). Secrete via `arona_pg.secret`/kb.
