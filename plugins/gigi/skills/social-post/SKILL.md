---
name: social-post
description: Postează pe Facebook Page + Instagram pentru orice brand ARONA (Gento, Nubra, GT, Grandia, MagDeal, Apreciat, Bonhaus, Belasil… ~22 pagini), direct din chat, cu tokenul The Wow Grid SU (system user, nu expiră; scopes pages_manage_posts + instagram_content_publish). Suportă text / poză / link / postare PROGRAMATĂ pe FB și poză+caption pe Instagram (flux container→publish, cu hosting automat de imagine pe Vercel Blob fiindcă IG cere URL public). DRY-RUN by default — postarea pe pagini publice e ireversibilă, deci cere --apply. Use pentru „postează pe Facebook/Instagram", „pune un post pe pagina X", „programează un post", „publică poza asta pe IG-ul brandului", „dă un anunț pe social". NU postează pe TikTok (API de content posting separat, neaprobat).
---

# social-post — postează pe FB Page + Instagram (branduri ARONA)

Postez eu direct pe paginile de Facebook și pe conturile de Instagram ale brandurilor ARONA, cu tokenul
**The Wow Grid SU** (system user, nu expiră). **DRY-RUN implicit** — arăt ce s-ar posta; postarea reală cere `--apply`
(paginile sunt publice, postarea e ireversibilă → confirmă cu userul înainte de `--apply`).

## Cum rulezi
```bash
uv run social_post.py list                                              # brandurile + paginile (FB + IG legat)
uv run social_post.py post --brand gento --text "..."                   # DRY-RUN
uv run social_post.py post --brand gento --text "..." --apply           # postează FB + IG
uv run social_post.py post --brand nubra --image poza.jpg --text "Caption" --to both --apply
uv run social_post.py post --brand gt   --link https://george-talent.ro/... --text "..." --to fb --apply
uv run social_post.py post --brand grandia --text "..." --schedule "2026-07-02 10:00" --to fb --apply
```

- `--brand` — potrivire fuzzy pe numele paginii (gento, nubra, gt, grandia, magdeal, apreciat, bonhaus…). Rulează `list` să vezi exact.
- `--to fb|ig|both` (default both). IG e sărit automat dacă brandul n-are IG legat sau dacă postezi doar text/link.
- `--image` — cale locală (urcată automat pe Vercel Blob pt IG) SAU URL public. FB acceptă și fișier direct.
- `--link` — post FB cu preview de link (doar FB).
- `--schedule "YYYY-MM-DD HH:MM"` — postare programată (DOAR FB; între 10 min și 6 luni în viitor).
- `--apply` — postează REAL. Fără el = dry-run.

## Cum funcționează
- **Token:** The Wow Grid SU din `meta_access_tokens` (sau env `FB_SYSTEM_TOKEN`). Nu expiră.
- **Pagini + IG:** `GET /me/accounts?fields=name,access_token,instagram_business_account`. Postarea folosește **page token**-ul (nu SU direct).
- **FB:** poză → `POST /{page}/photos` (fișier `source` sau `url`); link → `/{page}/feed` cu `link`; text → `/{page}/feed` cu `message`; programat → `published=false` + `scheduled_publish_time`.
- **IG (2 pași):** `POST /{ig}/media` (image_url public + caption) → poll `status_code=FINISHED` → `POST /{ig}/media_publish`. Imagine locală → urcată pe **Vercel Blob** (`BLOB_READ_WRITE_TOKEN_*`) pt URL public.

## Capcane
- **IG cere IMAGINE** (poză + caption); nu poate posta doar text. IG nu suportă programare via API (postează acum).
- **IG cere URL PUBLIC** de imagine — de-asta hosting-ul pe Blob. FB nu (acceptă bytes).
- **Postare = publică + ireversibilă.** Rulează întâi fără `--apply`, arată userului, apoi `--apply`.
- Doar 8 din 22 pagini au IG legat (vezi `list`). Restul → doar FB.
- TikTok = neacoperit (Content Posting API separat, cere aprobare Meta/TikTok).
