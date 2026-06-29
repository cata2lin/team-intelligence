---
name: cs-draft-reply
description: Generates a ready-to-review DRAFT reply for a Richpanel ticket using an LLM, grounded in ARONA's real CS procedures + the customer's actual data (orders, delivery status, AWB tracking link, products). Reads the conversation, resolves the customer via gigi:customer-identity (orders + deliverability + AWB), and writes a polite reply in the customer's language following the team playbook (WISMO→DPD tracking link, retur→return form + 14-day refund, broken perfume→free resend+gift, etc.). ⚠️ DRAFT ONLY — never sends a live message to the customer; with --create-draft it saves the draft in Richpanel for an agent to review/edit/send manually. Uses ANTHROPIC_API_KEY (Claude) if present in the KB, else OPENAI_API_KEY. Use for "draft reply", "raspunde la tichet", "genereaza un raspuns", "draft CS", "suggested reply", "schiteaza un raspuns pentru clientul". The live-send is intentionally disabled (team rule).
---

# cs-draft-reply — schiță de răspuns la tichet (LLM + date reale, DOAR DRAFT)

⚠️ **Nu trimite niciodată mesaj live la client.** Doar afișează sau (cu `--create-draft`) salvează un DRAFT în Richpanel pe care agentul îl verifică și trimite manual. Regula echipei: trimiterea automată e dezactivată; acest skill respectă asta (folosește `create_draft`, niciodată `send_message`).

## Cum rulezi
```bash
uv run cs_draft_reply.py --conv 265761                 # afișează draftul propus
uv run cs_draft_reply.py --conv 265761 --create-draft  # + îl salvează ca DRAFT în Richpanel (NU trimite)
```

## Ce face
1. `get_conversation` → citește mesajele clientului.
2. `gigi:customer-identity --conv` → comenzile lui + livrabilitate + **AWB** + produse.
3. LLM cu playbook-ul CS ARONA (proceduri reale per categorie) + datele clientului → **draft în limba clientului**, cu link de tracking/formular real, ton politicos, semnătură.
4. Opțional, salvează draftul în Richpanel (`create_draft`).

## LLM
- `ANTHROPIC_API_KEY` (Claude) dacă există în KB, altfel `OPENAI_API_KEY`. Model: env `DRAFT_MODEL` (default `gpt-4o-mini`).
- Instruit să **NU inventeze** AWB/date/prețuri — folosește doar datele primite; dacă lipsesc, cere-le politicos.

## Exemplu real (conv #265761)
Client întreabă de ridicare personală → draftul refuză politicos, citează comanda reală EST182490 (status Netrimis) + linkul de tracking DPD cu AWB-ul real. Tot draft, neтrimis.

## Note
- Playbook-ul (proceduri per categorie) e în skill — sincron cu „Documentația CS" din ClickUp. Actualizează-l când se schimbă procedurile.
- Necesită `RICHPANEL_MCP_TOKEN`, o cheie LLM, și (prin customer-identity) `DATABASE_URL_METRICS` + SSH la Scripturi.

---

# cs_auto_draft.py — FLOW peste toată coada (triaj + draft + escaladare + acțiuni + hide/unhide)

Varianta **batch** a lui cs_draft_reply: parcurge tichetele OPEN care **așteaptă răspuns de la noi**
(`last_message_sender_type=='customer'`) și, pentru fiecare, face un pipeline complet. **Tot DRAFT /
propune-aprobă** — niciun mesaj nu pleacă la client, nicio mutație fără `--approve`.

```bash
uv run cs_auto_draft.py                     # DRY-RUN: identifică + draft + escaladări + propuneri (nimic scris)
uv run cs_auto_draft.py --channel email --limit 8
uv run cs_auto_draft.py --actions modify,cancel   # ce acțiuni sunt ACTIVE (restul rămân doar draft); `none` = niciuna
uv run cs_auto_draft.py --create-draft      # salvează DRAFTURILE + rutează escaladările (NU trimite, NU aplică acțiuni)
uv run cs_auto_draft.py --create-draft --tag ai-live   # tag personalizat pe tichetele tratate (default ai-draft)
uv run cs_auto_draft.py --only 273383,274159 --create-draft   # procesează DOAR aceste tichete (regenerare țintită, după număr)
uv run cs_auto_draft.py --approve 273812 --agent Oana   # aplică acțiunea/hide propusă la un tichet + salvează draftul
uv run cs_auto_draft.py --send 273383       # ⚠️ TRIMITE LIVE la client (send_message) — customer-facing, IREVERSIBIL
```

> ⚠️ **Draft vs. send.** `--create-draft` scrie DOAR un draft (Richpanel nu-l trimite). `--send <conv>` trimite răspunsul LIVE la client prin metoda de server **`send_message`** (există pe serverul MCP Richpanel, deși nu e în lista implicită de unelte — apelată prin JSON-RPC). `--send` e per-tichet, explicit, și REFUZĂ escaladările/hide/retrimiterea. **Default-ul echipei rămâne draft-only.**
> ⚠️ **`create_draft` ADAUGĂ, nu suprascrie** — fiecare rulare lasă un draft NOU pe conversație; **nu există API de ștergere a drafturilor** (curățarea = manual în UI Richpanel). Folosește `--only` + un `--tag` distinct ca să identifici lotul corect; nu re-rula la nesfârșit pe aceleași tichete.

## Backlog masiv / cron (volum mare)
Pentru a draftui TOT backlog-ul deschis, throttle-uit și fără dubluri:
```bash
uv run cs_auto_draft.py --channel email --create-draft --lean --no-comments \
    --skip-tagged --tag ai-draft --limit 3000 --scan 6000 --sleep 0.5
```
- **`--lean`** — proces redus (fără 360/SSH, fără rutare escaladare) → mult mai rapid; brandul pe email e derivat din domeniul adresei (`brand_from_email`).
- **`--skip-tagged`** — sare tichetele care au DEJA tag-ul `--tag` → **idempotent**, fără dubluri la rulări repetate (esențial pt cron).
- **`--no-comments`** — exclude canalele de comentarii (rămân pt CS: hide spam / lead-uri de comandă, NU draft).
- **`--sleep`** — pauză între tichete (rate-safety).
- **Plafon real = rate-limit-ul OpenAI (TPM) + Richpanel (~4 req/sec)** → un singur worker secvențial, ~0.15-0.5 drafturi/sec. Paralelismul agresiv produce 429 (OpenAI → „(eroare LLM)" prins de gardă; Richpanel → backoff). `DRAFT_MODEL=gpt-4o-mini` are TPM mult mai mare.
- **Reziliență**: retry+backoff pe 429/5xx **și** timeout/URLError (LLM + MCP); **gardă**: NU salvează draft dacă LLM a eșuat (`(eroare LLM`/gol).
- **Cron (VPS)**: `/root/Scripturi/cs_backlog.sh` (sursează cheile din `.env`) rulat la 3h (`0 9-21/3`), `flock`-guarded; loops pe email+DM, comentarii excluse. Secretele pe VPS = `/root/Scripturi/.env` (root-600), nu KB (cron-ul n-are env KB).

## 📷 Poze (`--photos`, implicit PORNIT) — prin modulul canonic `gigi:cs-photo`
Flow-ul **REFERENȚIAZĂ** `cs-photo` (`import cs_photo`) ca să „vadă" pozele — nu duplică logica.
Vede **DOUĂ** poze, ambele injectate în context (triaj + draft):
- **(a) poza CLIENTULUI** (atașament): defect/spart, dovadă livrare (AWB/SMS), etichetă, screenshot;
- **(b) poza RECLAMEI** pe care comentează clientul (tichete FB/IG comment): ce PRODUS e în reclamă —
  prin `og:image` (UA crawler, FĂRĂ token de pagină), cu **registru** (post_id→produs) ca să nu re-descrie.
  Ex: la „De ce nu dați dimensiunile?" pe Casa Ofertelor, draftul știe că e o **bancă de hol cu depozitare**.
  > Fallback: dacă `cs_photo` nu e pe path (ex. VPS înainte de deploy), `_csp=None` → pozele clientului merg
  > pe logica locală (`describe_photos`), iar reclama e dezactivată (fără crash). Pe VPS: pune `cs_photo.py`
  > lângă `cs_auto_draft.py` (același folder = importabil) ca să se activeze și reclama.

Despre poza clientului:
MCP-ul taie bytes-ii imaginilor, dar dă URL-ul (bucket public S3 `richpanel-data`) → se descarcă + se descrie
cu un model vizual. Astfel:
- **retur/defect** → draftul confirmă defectul văzut în poză și tratează cazul (parfum spart → retrimitere+cadou; obiect casă defect → retrimitere/schimb/refund), **fără să mai ceară altă poză**;
- **dispută livrare** → ține cont de dovada din poză (SMS/email curier, AWB, „colet blocat la depozit");
- pozele = **dovadă reală** → EXCEPTATE de la filtrul anti-halucinare (le-am văzut efectiv).
Robustețe: **dedup pe nume fișier** (atașamentul se repetă în thread), **skip imagini < 12KB** (logo/semnătură
de email), **URL percent-encodat** (pozele WhatsApp au spații în nume). Plafon 4 poze/tichet (`max_imgs`).
Cost: o cerere vizuală DOAR pe tichetele CU poze (rare). `--no-photos` dezactivează. Indicator în output:
`📷 N poză(e) văzută(e) → folosite în draft`. Engine separat: `VISION_MODEL` (default `gpt-4o-mini`).
> Skill înrudit: **`gigi:cs-photo`** = varianta standalone (dă un tichet → descrie pozele, pt validare manuală retur/defect).

## Pipeline per tichet
1. **IDENTIFICARE (triaj LLM)** — întoarce JSON: problemă concretă, **produs**, categorie, limbă, severitate,
   `escalate`(+motiv), `suggested_action`, `action` executabilă (+params), `comment_action`.
   Regex-ul (`categorize_hint`) e doar hint/fallback (greșea: recenzii→spam, adresă→factură).
2. **Context 360** — platforma (`channel`→stil), identitate + **toate** comenzile, **unde a mai scris**
   (cross-canal `customer-identity.convos`, dedup pe emailuri+telefoane), sentiment, **brand + produs**.
3. **PROCEDURI + VOCE ÎNVĂȚATE** — dacă există `.learned_playbook.md` (generat de `gigi:cs-procedures` din
   tichete REALE: procedura de-facto + replici-șablon reale ale agenților per categorie), se injectează
   secțiunea categoriei în prompt → draftul urmează procedura reală și sună ca agenții. Fallback: playbook din SYSTEM.
4. **DRAFT** adaptat **platformei + brandului + produsului**: email = complet + semnătură; comentariu public = scurt, **fără date personale**.
5. **ESCALADARE** (ANPC/juridic, refund promis-neefectuat, client foarte supărat/repetat, VIP) → NU
   auto-răspunde: draft scurt de **AȘTEPTARE** + (sub `--create-draft`) rutare internă în Richpanel:
   prioritate **HIGH** (enum doar LOW/HIGH; URGENT în tag), tag `escaladare`/`esc:<lvl>`/`de-sunat`,
   + **notă-brief** (`add_private_note`) cu problemă, telefon, comandă+AWB, unde a mai scris, acțiune sugerată.
6. **ACȚIUNE** modify/cancel/swap/resend — **propune+aprobă**: doar pe comanda **referită clar** și
   **pre-fulfillment**, rulează `gigi:cs-actions` în DRY-RUN; aplică doar cu `--approve … --agent`.
   Draftul confirmă acțiunea ca FĂCUTĂ doar dacă a fost aplicată (`ACTIUNE_APLICATA`).
7. **MODERARE comentarii FB/IG** (`comment_action`, corectează **replyzen.ai**) — așa cum răspund
   agenții REAL la comentarii (verificat în istoric: pozitiv→răspuns PUBLIC scurt, deseori doar un emoji;
   negativ→ascuns; **NU se folosesc DM-uri**). **Nu trimitem mesaje private** — răspundem public și, dacă
   e nevoie de rezolvare, **invităm clientul să ne scrie în privat sau să sune** (numărul magazinului din `STORE_PHONE`):
   - `hide` = spam/troll/abuz/reclamă străină (`fb_hide_comment`, Graph cu token de pagină) — propus, aplicat la `--approve`.
   - `public` = orice comentariu care merită răspuns: laudă→mulțumire caldă; întrebare/reclamație→răspuns scurt
     + invitație „scrieți-ne în privat / sunați-ne la <număr>". Draft salvat în Richpanel (agentul îl postează).
   - `none` = zgomot pur (tag de prieten, off-topic) → se lasă.
   > Postarea publică efectivă + hide pe FB necesită un **Page Access Token** Meta (`pages_messaging` n-ar mai
   > trebui — doar `pages_manage_engagement` pt hide + postare); tokenurile actuale sunt de ADS (0 pagini, exceptând
   > Nubra+Covoria prin `META_SYSTEM_TOKEN_3`). Până atunci: comentariile rămân **draft public** + propunere hide.

## Siguranță
- **DRY-RUN implicit**; `--create-draft` scrie doar drafturi + rutare escaladare (intern). Acțiunile pe
  comenzi și hide/unhide **nu** se aplică decât cu `--approve <conv>` (model propune+aprobă).
- **Default = draft-only** (recomandat). Trimiterea LIVE există acum prin `--send <conv>` (metoda de server
  `send_message`), dar e **opt-in explicit, per-tichet**, refuză escaladări/hide, și e customer-facing/ireversibil —
  trimite DOAR răspunsuri verificate. Cazurile sensibile (igienă desigilată, ANPC) → escaladare la om, nu auto-trimitere.

## Necesită
`RICHPANEL_MCP_TOKEN`, cheie LLM, `customer-identity` (→ `DATABASE_URL_METRICS` + SSH), `gigi:cs-actions`
(token-uri `write_orders`), `META_SYSTEM_TOKEN` (pt hide/unhide).

> ⚠️ Apply-paths (`cs-actions --apply`, hide/unhide Graph) sunt codate dar de **validat live o dată**
> (scope token Meta moderare + formatul exact al comment-id din ticket id).
