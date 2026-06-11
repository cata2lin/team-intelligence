---
name: reviews-manager
description: Product reviews coverage & management across brands via the Judge.me API. Reports per-store review coverage (products with 0 reviews, average rating, total count, rating distribution), recent reviews, and low-star reviews needing a reply, and finds best-selling products missing reviews (joined to metrics order_line_items). Use for "review coverage", "which products have no reviews", "average rating", "reply to bad reviews", "low star reviews", and the recurring "add reviews / recenzii" catalog tasks. Triggers: reviews, recenzii, Judge.me, ratings, star rating, social proof, testimonials, review coverage, no reviews.
---

# Reviews manager (Judge.me, per brand)

Acoperirea de recenzii pe magazin + ce produse n-au recenzii + recenzii cu notă mică de răspuns. Servește taskurile recurente „de adăugat recenzii".

## Cum rulezi
```bash
uv run reviews_manager.py coverage    --brand Esteban   # total, rating mediu, produse cu 0 recenzii, distributie note
uv run reviews_manager.py recent      --brand Grandia   # recenziile recente
uv run reviews_manager.py low         --brand GT        # recenzii nota <=3 (de răspuns)
uv run reviews_manager.py bestsellers --brand Nubra     # best-sellers FĂRĂ recenzii (prioritate de cerut review)
# --limit N
```
Branduri cu token în KB: Esteban, Grandia, GT, Bonhaus PL, Gento, Redbune (Reduceri bune), Rossi. (`--brand all` le ia pe toate.)

## Cum se calculează
- Judge.me REST API v1 (`JUDGEME_<BRAND>_PRIVATE_TOKEN` din KB + shop_domain). Counts exacte din `/reviews/count` + `/products/count`.
- Best-sellers fără recenzii: join cu `metrics.order_line_items` (top vândute) ∩ produse fără recenzie. Read-only.

## Limitări
- Pe magazine mari (ex. Esteban ~11.500 recenzii) `coverage` citește un eșantion (max ~2.500 recenzii) pt rating mediu/distribuție — totalele rămân exacte din count. Mărește `max_pages` în cod dacă vrei agregare completă.
- Doar READ. Trimiterea de cereri de review / publicarea recenziilor (write) e de adăugat ulterior, confirm-gated.
