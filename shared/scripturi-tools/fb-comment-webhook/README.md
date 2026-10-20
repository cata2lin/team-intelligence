# fb-comment-webhook (Cloudflare Worker, cont novosdigital `1eb746…`)

Copie a codului LIVE (descărcat prin `/workers/scripts/fb-comment-webhook/content/v2`). Bindings (secrete,
D1 `RAW` = baza `cs-raw-events`, KV `REVIEW`, `REPLY_MODE`) NU sunt în cod — se păstrează la deploy doar prin
endpointul `PUT .../content` (un PUT pe script le-ar șterge). Token: `CLOUDFLARE_API_TOKEN_WORKERS` din KB.

20-oct-2026: reacțiile (like/love…) de pe feed nu se mai scriu în D1 — erau ~95% din scrieri și loveau
plafonul D1 Free de 100k rânduri/zi (fiecare insert = 5 rânduri: tabel + PK + 3 indecși). Comentariile,
inclusiv cele șterse (`verb=remove` pe `item=comment`), se captează în continuare.
