# TikTok Posting + Organic Analytics — API & token reality (ARONA)

Ghidul TEHNIC pentru postarea organică + analitica organică TikTok prin **API-urile
oficiale TikTok** (NU un SaaS plătit ca upload-post). Scripturile: `execution/tiktok_post.py`
(postare) și `execution/tiktok_organic.py` (analytics).

## ⚠️ Realitatea token-urilor (citește înainte de orice)
- Token-urile noastre din `metrics.tiktok_access_tokens` sunt **Business/Ads API**
  (scope-uri NUMERICE: `15000000`, `2`, `3`, `150`, `152`…) → ele operează **conturi de
  reclame** (gigi:tiktok-ads). **NU au `video.publish` / `video.list`.**
- Postarea organică + citirea statisticilor organice cer un **app TikTok for Developers**
  (Login Kit + Content Posting API + Display API) cu scope-urile string `video.publish`,
  `video.upload`, `video.list`, `user.info.basic` — **plus OAuth per cont de brand**.
  Token-ul rezultat se salvează în KB ca **`TIKTOK_CONTENT_<BRAND>_TOKEN`**.
  (Exact ca re-auth-ul de management de la YouTube — vezi [[youtube-skill-optimization]].)
- **AUDIT**: un app NEAUDITAT poate posta DOAR `SELF_ONLY` (privat, doar tu vezi). Pentru
  postare **publică** (`PUBLIC_TO_EVERYONE`), app-ul trebuie trecut prin „URL prefix /
  domain verification + content review" la TikTok. Până atunci: postezi `SELF_ONLY`, verifici
  în app, apoi publici manual; SAU folosești drafturile (apar în inbox-ul TikTok al brandului).

## 1. Content Posting API (postare) — `tiktok_post.py`
Base: `https://open.tiktokapis.com/v2`. Flux (Direct Post):
1. `POST /post/publish/creator_info/query/` → confirmă creatorul + limitele (max durată,
   privacy options permise). Bun pt `--check`.
2. `POST /post/publish/video/init/` cu:
   - `post_info`: `{title, privacy_level, disable_comment, disable_duet, disable_stitch}`
   - `source_info`: fie **`PULL_FROM_URL`** `{source, video_url}` (simplu — TikTok trage
     fișierul de la un URL public, ex. link Shopify/NAS), fie **`FILE_UPLOAD`**
     `{source, video_size, chunk_size, total_chunk_count}` (upload chunked).
   → întoarce `publish_id` (+ `upload_url` la FILE_UPLOAD).
3. La FILE_UPLOAD: `PUT upload_url` cu `Content-Range: bytes 0-(N-1)/N`.
4. Poll `POST /post/publish/status/fetch/` cu `publish_id` până la `PUBLISH_COMPLETE`
   (sau `FAILED`).
- `PULL_FROM_URL` cere ca domeniul URL-ului să fie **verificat** în app (URL ownership).
- Rate limit: ~6 postări / minut / user; quotă zilnică pe app.

`uv run tiktok_post.py --brand NUBRA --video-url <url> --title "..." --privacy SELF_ONLY`
(dry: `--check`). PULL_FROM_URL = recomandat (fără chunked upload).

## 2. Display API (analytics organic) — `tiktok_organic.py`
- `POST /v2/video/list/?fields=id,title,view_count,like_count,comment_count,share_count,create_time`
  cu body `{max_count, cursor}` → paginabil (`has_more` + `cursor`). Scope `video.list`.
- `POST /v2/video/query/` (cu lista de `video_id`) → statistici la cerere pe videouri știute.
- `POST /v2/user/info/?fields=follower_count,likes_count,video_count` → profilul brandului.
- Dă views / likes / comments / shares per video → vezi ce **format organic** funcționează
  (completează gigi:tiktok-ads care e DOAR plătit). Watch-time/retenție NU sunt în Display API
  public (alea-s în TikTok Studio / Research API gated).

`uv run tiktok_organic.py --brand NUBRA --top 20`.

## 3. Setup OAuth (one-time per app + per brand) — ce rămâne de făcut
1. Creează un app pe **developers.tiktok.com** (Login Kit + Content Posting API + Display API).
2. Adaugă produsele + scope-urile `video.publish video.upload video.list user.info.basic`.
3. Verifică domeniul (pt PULL_FROM_URL) + cere content review (pt postare publică).
4. OAuth per cont de brand → `access_token` + `refresh_token` →
   `kb.py secret-set TIKTOK_CONTENT_<BRAND>_TOKEN <token>` (+ refresh la nevoie).
Până atunci, scripturile rulează dar întorc clar „lipsește scope" — vezi mesajele lor.

> Token-urile = DOAR în KB (`kb.py`), niciodată hardcodate. Brand → uppercase
> (`TIKTOK_CONTENT_NUBRA_TOKEN`). Legături: [[youtube-skill-optimization]] (același tipar de re-auth).
