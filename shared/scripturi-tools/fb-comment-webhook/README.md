# fb-comment-webhook — înlocuiește Reply Zen (moderare + reply real-time FB/IG)

Webhook Page `feed` → la fiecare comentariu nou: **ascunde** răul (live, reversibil) + **reply/DM** la lead/pozitiv
(draft-cu-aprobare prima săptămână) + **escaladează** plângerile/întrebările complexe la CS. Reguli = cele Reply Zen.

## Arhitectură
FB (comentariu nou) → **Cloudflare Worker** (`worker.js`) → clasifică → acțiune:
- 🔴 negativ/competiție/monedă/CAPS/emoji → `is_hidden=true` (LIVE din start; reversibil; ca Reply Zen)
- 💰 lead → reply public „ți-am scris în privat" + **DM** cu detalii (ramburs/link) → [draft prima săpt.]
- 💚 pozitiv → reply scurt de mulțumire → [draft prima săpt.]
- 🚚🤔 plângere livrare / întrebare complexă → escaladare CS (draft Richpanel), NU răspuns automat
- restul (neutru) → keep

## Ce-mi trebuie de la tine (blocaje deploy)
1. **Hosting** — token-ul Cloudflare actual are DOAR DNS+Zone (nu Workers). Alege:
   - (recomand) adaugă pe token permisiunile **Workers Scripts:Edit + Workers KV:Edit** (Cloudflare dashboard → My Profile → API Tokens) → deployez Worker + KV;
   - SAU confirmă-mi că deployez pe **VPS** + record DNS proxied (token-ul actual poate face DNS-ul).
2. **Meta App Dashboard** (app „Api export", id `1268707461439970`, ai nevoie de admin):
   - Products → **Webhooks** → Page → Callback URL = `https://<worker>/` + **Verify Token** (ți-l dau eu) → Subscribe câmpul **`feed`**.
   - **App Secret** al app-ului (Settings → Basic) → pt validare HMAC (sau confirmă că `META_APP_SECRET` din KB e al acestui app).
3. Restul secretelor le am (FB_SYSTEM_TOKEN = „The Wow Grid SU", ANTHROPIC_API_KEY din KB).

## Ce fac eu după deblocare
1. `wrangler deploy` Worker + KV namespace `REVIEW` + `wrangler secret put` (token/app-secret/verify/LLM).
2. Abonez cele **22 pagini**: `POST /{page-id}/subscribed_apps?subscribed_fields=feed` (prin API, cu page tokens).
3. Test: comentariu de probă → confirm că ascunde / pune în coada `/review`.
4. Pornesc în `REPLY_MODE=draft` (reply-urile aterizează la `/review` pt aprobare; hide-ul e live).

## 🛑 CÂND OPREȘTI REPLY ZEN (criteriu)
**Rulăm în PARALEL** (RZ rămâne plasă de siguranță). Oprești RZ DOAR după ce, timp de **~3-5 zile**:
1. Worker-ul **primește evenimente** pe toate 22 paginile (verificat în loguri).
2. **Auto-hide-ul live** prinde aceleași comentarii ca RZ (0 scăpări vizibile la spot-check).
3. **Reply-urile/DM-urile** din coada `/review` arată bine la aprobare → le trecem pe `REPLY_MODE=live`.
→ Când toate 3 sunt OK, **îți zic „acum oprește Reply Zen"** și treci sistemul pe full-auto.

Până atunci: **NU opri Reply Zen.**
