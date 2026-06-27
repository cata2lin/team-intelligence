---
name: cro
description: On-site CRO (conversion-rate optimization) auditor for Shopify store pages — scores the conversion blockers on a product/collection/home page and gives prioritized, Romanian + COD-aware fixes. The thing nobody checks while we pour organic + paid traffic onto the site. Use for "audit conversion", "why isn't this page converting", "CRO audit", "optimize landing/product page", "ce frânează conversia". Pure offline (no keys).
argument-hint: "audit --url <page>"
---

# cro — on-site conversion auditor (RO/COD)
> Author: Gigi.

We control the site; the agency runs paid social. This scores whether the pages actually **convert** the traffic we get — the on-site lever that's fully ours. Romanian e-commerce, COD-heavy, so "plata la livrare/ramburs" and "livrare gratuită" are weighted as major trust levers.

```bash
uv run cro.py audit --url https://esteban.ro/products/<handle>
uv run cro.py audit --url https://esteban.ro/collections/dama
```

## Signals (weighted → /100), strongest first
add-to-cart/CTA present · **social proof** (reviews/rating) · **COD trust** (plata la livrare/ramburs) · price visible · free shipping · returns/guarantee · urgency/scarcity · product images ≥3 · description depth · email capture (newsletter/popup) · mobile viewport. Output = score + prioritized fix list.

## How to use
Run on the top-traffic product & collection pages (use `gigi:analytics gsc.py` / GA4 landing pages to find them). Fix the flagged items via `gigi:shopify-seo` (Admin API) where possible — meta/trust blocks — or theme edits. Real example: esteban.ro/collections/dama scored 64/100, missing **COD mention, returns/guarantee, and email capture** — three quick conversion wins.

## Caveats
- Heuristic + RO-tuned; it checks for the *presence* of conversion elements, not their visual prominence (a button can exist but be below the fold). Treat as a checklist, confirm visually for top pages.
- Pairs with `gigi:shopify-geo` (AEO), `gigi:analytics` (where the traffic lands), `gigi:shopify-seo` (apply fixes).

## Unghiuri noi (skilluri adoptate MIT)
- **gigi:ab-testing** — design A/B cu motor de statistică (sample size, durată, semnificație) — testează fix-urile CRO riguros.
- **gigi:offers** — design-ul OFERTEI (value equation Hormozi, garanții, scarcity) = cel mai mare lever de conversie.
- **gigi:popups** + **gigi:signup** — lead-capture on-site. **gigi:marketing-psychology** — persuasiune. **gigi:customer-research** — VOC pt obiecțiile reale de eliminat.
