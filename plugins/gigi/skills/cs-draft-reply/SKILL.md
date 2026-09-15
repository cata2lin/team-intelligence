---
name: cs-draft-reply
description: Generates a ready-to-review DRAFT reply for a Richpanel ticket using an LLM, grounded in ARONA's real CS procedures + the customer's actual data (orders, delivery status, AWB tracking link, products). Reads the conversation, resolves the customer via gigi:customer-identity (orders + deliverability + AWB), and writes a polite reply in the customer's language following the team playbook (WISMO→DPD tracking link, retur→return form + 14-day refund, broken perfume→free resend+gift, etc.). ⚠️ DRAFT ONLY — never sends a live message to the customer; with --create-draft it saves the draft in Richpanel for an agent to review/edit/send manually. Uses ANTHROPIC_API_KEY (Claude) if present in the KB, else OPENAI_API_KEY. Use for "draft reply", "raspunde la tichet", "genereaza un raspuns", "draft CS", "suggested reply", "schiteaza un raspuns pentru clientul". Live-send exists (--send / --apply-send) but is HARD-GATED: it refuses to run unless CS_TRIMITERE_LIVE=DA or the confirmation file ~/.arona/cs_trimitere_live.ok is present, so a command-line slip cannot message customers. Public FB/IG comment channels are opt-in (--comments). Run poarta_pornire.py before starting the cron.
---

# cs-draft-reply — schiță de răspuns la tichet (LLM + date reale, DOAR DRAFT)

⚠️ **Implicit nu pleacă niciun mesaj la client.** Afișează sau (cu `--create-draft`) salvează un DRAFT în
Richpanel pe care agentul îl verifică și trimite manual. Căile de trimitere LIVE (`--send`, `--apply-send`
în `cs_auto_draft.py`) există, dar din **15-sep-2026** sunt **blocate de o gardă**: fără `CS_TRIMITERE_LIVE=DA`
sau fișierul `~/.arona/cs_trimitere_live.ok`, procesul se oprește cu cod `2` înainte de orice apel de rețea.
„Draft-only" nu mai e o convenție care ține doar cât timp nimeni nu scrie flagul.

> 🚦 **Înainte de orice pornire de cron: `uv run poarta_pornire.py`** (vezi **[POARTA.md](POARTA.md)**) —
> rulează suitele rundelor 1-3, măsoară indicatorii pe date REALE și compară copia din repo cu cea a
> plugin-ului și cu cea de pe VPS. Măsurat 15-sep-2026: **repo 2352 linii · plugin 1342 · VPS 1352** —
> cine pornește cronul azi pornește versiunea de dinainte de runda 1 de reparații.

## Cum rulezi
```bash
uv run cs_draft_reply.py --conv 265761                 # afișează draftul propus (cu precedent din tichete similare)
uv run cs_draft_reply.py --conv 265761 --create-draft  # + îl salvează ca DRAFT în Richpanel (NU trimite)
uv run cs_draft_reply.py --conv 265761 --similar 0      # fără precedent (doar playbook + date client)
uv run cs_draft_reply.py --conv 265761 --similar-api    # precedent cu embeddings API (ranking mai bun)
```

## Ce face
1. `get_conversation` → citește mesajele clientului.
2. `gigi:customer-identity --conv` → comenzile lui + livrabilitate + **AWB** + produse.
3. **PRECEDENT (nou)** — `cs_ticket_index.py` găsește **semantic** top-N tichete REZOLVATE similare și aduce **replica agentului REAL** din fiecare (via MCP), ca ghid de ton + conținut. Vezi mai jos.
4. LLM cu playbook-ul CS ARONA (proceduri reale per categorie) + datele clientului + precedentul → **draft în limba clientului**, cu link de tracking/formular real, ton politicos, semnătură.
5. Opțional, salvează draftul în Richpanel (`create_draft`).

## Precedent din tichete rezolvate (`cs_ticket_index.py`)
Ancorează draftul în **cum a rezolvat echipa cazuri REALE similare** (retrieval semantic), nu doar în playbook.
Reutilizează embeddings-urile din `gigi:semantic-search` (fastembed local pe CPU, sau `--api`).
```bash
# o dată (și după ce se adună tichete noi) — construiește indexul de subiecte de tichete rezolvate:
uv run cs_ticket_index.py build --limit 8000 --days 180        # local (CPU); sau --api pt calitate
uv run cs_ticket_index.py similar "vreau sa returnez produsul" --k 5   # test standalone
```
- Indexăm **doar `subject`** (întrebarea) al tichetelor CLOSED cu agent real, din `data/richpanel_tickets.db`
  (export `gigi:richpanel-export`). Replica agentului **NU** e în DB → se aduce LIVE prin MCP doar pt top-K.
- `agent_reply()` extrage doar mesajele **`author_is_workspace_agent`**, taie thread-ul de email citat + boilerplate-ul Richpanel (auto-form).
- `--similar N` (default **3**, `0`=off) în `cs_draft_reply.py`. Dacă indexul lipsește → se sare elegant (draftul merge fără precedent).

## LLM
- `ANTHROPIC_API_KEY` (Claude) dacă există în KB, altfel `OPENAI_API_KEY`. Model: env `DRAFT_MODEL` (default **`claude-sonnet-4-6`**; vechiul `claude-3-5-sonnet-20241022` a fost RETRAS → 404).
- Instruit să **NU inventeze** AWB/date/prețuri — folosește doar datele primite; iar din precedent ia DOAR tonul/conținutul, NU AWB/nume/date.

## Exemplu real (conv #265761)
Client întreabă de ridicare personală → draftul refuză politicos, citează comanda reală EST000001 (status Netrimis) + linkul de tracking DPD cu AWB-ul real. Tot draft, neтrimis.

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
CS_TRIMITERE_LIVE=DA uv run cs_auto_draft.py --send 273383   # ⚠️ TRIMITE LIVE (fără variabilă: refuz + cod 2)
```

> ⚠️ **Draft vs. send.** `--create-draft` scrie DOAR un draft (Richpanel nu-l trimite). `--send <conv>` trimite răspunsul LIVE la client prin metoda de server **`send_message`** (există pe serverul MCP Richpanel, deși nu e în lista implicită de unelte — apelată prin JSON-RPC). `--send` e per-tichet, explicit, REFUZĂ escaladările/hide/retrimiterea **și cere garda armată** (`CS_TRIMITERE_LIVE=DA` sau `~/.arona/cs_trimitere_live.ok`); la fel `--apply-send` (lot). **Default-ul echipei rămâne draft-only.**
> ⚠️ **`create_draft` ADAUGĂ, nu suprascrie** — fiecare rulare lasă un draft NOU pe conversație; **nu există API de ștergere a drafturilor** (curățarea = manual în UI Richpanel). Folosește `--only` + un `--tag` distinct ca să identifici lotul corect; nu re-rula la nesfârșit pe aceleași tichete.

## Backlog masiv / cron (volum mare)
Pentru a draftui TOT backlog-ul deschis, throttle-uit și fără dubluri:
```bash
uv run cs_auto_draft.py --channel email --create-draft --lean \
    --skip-tagged --tag ai-draft --limit 3000 --scan 6000 --sleep 0.5
```
- **`--lean`** — proces redus (fără 360/SSH, fără rutare escaladare) → mult mai rapid; brandul pe email e derivat din domeniul adresei (`brand_from_email`).
- **`--skip-tagged`** — sare tichetele care au DEJA tag-ul `--tag` → **idempotent**, fără dubluri la rulări repetate (esențial pt cron).
- **canalul PUBLIC e opt-in** (schimbat 15-sep-2026): comentariile FB/IG **nu** se mai draftează implicit —
  se cere **`--comments`**. Cu `--auto-hide` dar fără `--comments`, comentariile intră **doar pentru moderare**
  (spamul tot se ascunde), fără draft public. **`--no-comments`** rămâne acceptat (liniile vechi de cron nu
  crapă) și exclude complet canalul. Raportul final spune câte au fost sărite și câte doar moderate.
  > De ce s-a inversat implicitul: pe canal public o scurgere de date e vizibilă pentru toată lumea, iar
  > `cs_backlog.sh` de pe VPS **nu mai conține `--no-comments`** (buclează pe toate cele 7 canale).
- **`--sleep`** — pauză între tichete (rate-safety).
- **Plafon real = rate-limit-ul OpenAI (TPM) + Richpanel (~4 req/sec)** → un singur worker secvențial, ~0.15-0.5 drafturi/sec. Paralelismul agresiv produce 429 (OpenAI → „(eroare LLM)" prins de gardă; Richpanel → backoff). `DRAFT_MODEL=gpt-4o-mini` are TPM mult mai mare.
- **Reziliență**: retry+backoff pe 429/5xx **și** timeout/URLError (LLM + MCP); **gardă**: NU salvează draft dacă LLM a eșuat (`(eroare LLM`/gol).
- **Cron (VPS)**: `/root/Scripturi/cs_backlog.sh` (sursează cheile din `.env`), `flock`-guarded. ⚠️ Verificat
  15-sep-2026: linia din crontab e **comentată din 29-iun-2026** (`# PAUZAT … 0 9-21/3`), iar scriptul
  **buclează pe toate cele 7 canale, inclusiv comentariile** — nu mai are `--no-comments`. Secretele pe VPS = `/root/Scripturi/.env` (root-600), nu KB (cron-ul n-are env KB). **Deploy**: pune `cs_auto_draft.py` ȘI `cs_photo.py` în `/root/Scripturi/` (același folder = `import cs_photo` merge) — **procedura completă, cu verificare, în [POARTA.md](POARTA.md) §3**. `dependencies=["pg8000"]` (altfel grounding+catalog pică tăcut local).

## Hardening filtrare + escaladare (analiză 40 email + 15 cmt, iun-2026)
- **Spam gating DETERMINIST** (robust indiferent de model — gpt-4o-mini ratează spam ce gpt-4o prinde): `NON_CUSTOMER_SENDER_RE` (curier dpd/sameday/econt + app omegatheme/consentik) + `JUDGEME_NOTIF_RE` (subiect „left a N star review") + `SAAS_NOISE_SUBJ_RE` (rapoarte/mention social) + `BOUNCE_RE` (mailer-daemon) + `_padded_noise` (corp majoritar caractere invizibile) → EXCLUS înainte de draft, fără să depindă de LLM.
- **Gate anti-SUPRA-escaladare** (`_real_escalation`): reclamații obișnuite (parfum spart, WISMO simplu) NU mai escaladează doar pe flag-ul LLM; escaladează DOAR la semnal REAL (ANPC/juridic via `ESCAL`, furie/„țeapă" via `ANGER_RE`, sau >70% MAJUSCULE).
- **Comentarii**: răspuns DIRECT la întrebări simple (preț/ofertă/„cum comand"/„dă-mi numărul") — nu deflecta în privat; privatul doar pt date personale.
- **Preț din catalog**: pe comentarii deals, `cs_photo.ad_block(ticket, store_hint=<brandul REAL al tichetului>)`
  aduce prețul/stocul real (catalog metrics) → draftul răspunde la „ce preț?"; exceptat de la anti-halucinare
  (`_ad_has_catalog`). **`store_hint` e obligatoriu**: fără el brandul se ghicea din `og:title`, iar o piață
  străină fără brand propriu în metrics cădea pe brandul ROMÂNESC (preț și stoc din alt magazin). Potrivirea
  are **prag de încredere** (≥2 cuvinte potrivite ȘI un cuvânt de CONȚINUT, nu doar un atribut ca „electric").

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

## Gărzi de conținut (reparate în rundele 1-2, sep-2026) — ce filtrează efectiv un draft
Toate rulează **după** generare, pe textul FINAL, și pe **toate** limbile în care răspundem
(ro/bg/hu/sk/cz/pl/hr/en) — un filtru scris doar în română e mort pe piețele străine.

| Gardă | Funcții | Ce face |
|---|---|---|
| anti-halucinare | `hallu_hits`, `HALLU`, `term_excused` | prinde lookup („am verificat"), status („predat curierului"), TERMEN („în 3 zile") și dimensiuni **inventate**, în fiecare limbă. Scutiri: termenul de politică (1-3 zile livrare, 14/30 zile retur **în context de retur**), prețul/dimensiunea din catalogul reclamei, pozele văzute efectiv, faptele din registrul de incidente |
| canal public | `public_pii`, `public_pii_leaks`, `redact_public_pii` | scoate din draftul public telefoanele clientului și numerele de comandă; se cheamă pe textul final, deci și pe drafturile regenerate |
| registru de politețe | `register_rule`, `informal_register_hits`, `ctx_lang` | pl/hu **nu** se adresează la plural; draftul informal se REGENEREAZĂ (dacă și regenerarea iese informală, se păstrează originalul — nu ciuntim) |
| emoji | `emoji_allowed`, `strip_emoji` | fără emoji pe escaladare / ton negativ |
| telefon + link | `STORE_PHONE`, `STORE_URL`, `phone_ok`, `our_phone`, `tel_block` | numărul dat clientului e linia PUBLICATĂ a magazinului (reverificată 15-sep-2026 din `tel:` de pe site), validată pe formatul țării; numerele NOASTRE nu sunt tratate ca halucinație. Bonhaus PL n-are linie poloneză reală → deflectare pe Messenger + site, nu un număr pe care clientul nu-l poate forma |
| callback | tag `de-sunat` + `add_private_note` | numărul clientului stă în nota INTERNĂ, nu în draftul public |
| catalogul reclamei | `cs_photo.ad_block(..., store_hint=…)` | prețul/stocul vin de pe brandul REAL al tichetului, cu prag de încredere |
| incidente | `load_incidents`, `incident_facts`, `incident_block`, `unbacked_claims` | vezi mai jos |

### Registrul de incidente (`CS_INCIDENTS_FILE`)
Când avem o problemă reală de la noi (lot greșit, întârziere de depozit), draftul **nu deflectează** —
recunoaște deschis și spune ce facem. Faptele vin dintr-un fișier, nu din LLM:
- schema `cs-incidents/1`, fișier `CS_INCIDENTS_FILE` sau **`~/.arona/cs_incidents.json`**;
- șablon comentat în repo: **`incidents.example.json`** (fișierul real NU e în git — conține numere de comandă);
- **`topic` e OBLIGATORIU ca brațul public să funcționeze** și se scrie **pe limba pieței**:
  `"topic": {"bg": [...], "sk": [...], "*": [...]}` — termenii pe care îi scrie CLIENTUL, verbatim
  (`*` = fără limbă: cifre, „2+1", nume de produs). Se folosesc doar cei pe limba magazinului
  (`STORE_LANG`) plus `*`; o listă simplă se tratează ca `*`. Fără termeni pe limba pieței, motorul
  **strigă la pornire** („⚠️ REGISTRU INCIDENTE…") și nu recunoaște nimic public acolo — descrierea
  românească NU se mai folosește ca fallback, fiindcă nu se potrivește cu un comentariu în bulgară;
- `store` se potrivește **EXACT** (poate fi și listă; plus `store_aliases`): „Duppo" NU mai prinde
  cele șase pagini Duppo, iar „Bonhaus" nu mai anunță incidentul pe BG, CZ și PL deodată;
- pe canal PUBLIC descrierea e curățată (`_safe_desc`): fără e-mail, sume, numere lungi sau numere de comandă,
  iar draftul recunoaște incidentul **fără** să confirme că persoana din comentariu e client
  (magazinul vine DOAR din pagină sau din domeniul cutiei, **niciodată** din comenzile persoanei);
- doar faptele din registru sunt scutite de anti-halucinare (`incident_backed` / `unbacked_claims`).

### Unde stau datele reale (niciunul în git — repo public)
| Ce | Unde |
|---|---|
| oglinda CS (răspunsuri REALE de agent, pt măsurători) | `~/Downloads/Scripturi/data/cs_mirror.db` (`CS_MIRROR_DB`) |
| coada de propuneri (drafturi + acțiuni) | `.auto_draft_proposals.json`, lângă script |
| registrul reclamelor FB/IG | `../cs-photo/fb_post_registry.sqlite` (`FB_POST_DB`) |
| incidente | `~/.arona/cs_incidents.json` |
| suitele de test ale rundelor 1-3 | `~/.arona/cs-suite/` (`CS_SUITE_DIR`) — vezi [POARTA.md](POARTA.md) |
| confirmarea de trimitere live | `~/.arona/cs_trimitere_live.ok` (`CS_TRIMITERE_FILE`) |
| corpus de fraze REALE de piață (în repo: text public) | `corpus/termen_piata.json` |

## Siguranță
- **DRY-RUN implicit**; `--create-draft` scrie doar drafturi + rutare escaladare (intern). Acțiunile pe
  comenzi și hide/unhide **nu** se aplică decât cu `--approve <conv>` (model propune+aprobă).
- **Draft-only e acum o GARDĂ, nu o convenție.** `--send <conv>` (per tichet) și `--apply-send` (lot) trimit
  prin `send_message` și închid tichetul — ireversibil. Din 15-sep-2026 amândouă cer consimțământ explicit
  **în afara liniei de comandă**: `CS_TRIMITERE_LIVE=DA` sau `echo DA > ~/.arona/cs_trimitere_live.ok`.
  Fără el: cod de ieșire `2` și un mesaj care spune de ce, **înainte de orice apel de rețea** (deci o greșeală
  de linie de comandă nu poate ajunge la clienți). Escaladările, comentariile și hide-ul rămân oricum DRAFT.
  Cazurile sensibile (igienă desigilată, ANPC) → escaladare la om, nu auto-trimitere.
- **Canalul public e opt-in** (`--comments`); fără el, comentariile FB/IG nu primesc draft. `--auto-hide`
  continuă să ascundă spamul.
- **Poarta de pornire**: `uv run poarta_pornire.py` (suite + indicatori + gărzi + paritatea copiilor).
  Verdict VERDE/ROȘU, cod de ieșire 0/1. Detalii: [POARTA.md](POARTA.md).
- **Runda 4 — poarta măsoară altfel** (vezi [POARTA.md](POARTA.md) §4):
  - scurgerile pe canal public se măsoară **cap-la-cap prin `main()`**, pe drafturi OSTILE,
    cu clasele definite **în poartă** (`email`, `adresa`, `nume` pe lângă telefon/comandă/AWB).
    Indicatorul vechi era circular — chema detectorul gărzii pe ieșirea redactării gărzii,
    deci ieșea 0 pe orice cod. Azi poarta e **ROȘIE**: 3 din 4 drafturi ostile scurg.
  - fals-pozitivul pe română se raportează în **două populații**: *divergență față de agent*
    (19,7% — informativ) și *fals-pozitiv față de politica noastră* (0,7% — defectul real).
  - termenul se măsoară contra unei **definiții scrise** (`corpus/termen_definitie.json`):
    politică generală = scuzată, promisiune despre coletul ACESTUI client = prinsă.
    Promisiuni VERBATIM de piață: **8/8**. „5/7" era un dezacord de etichetare.
- **Desfășurarea** (arbore de lucru → git → plugin → VPS), pas cu pas, cu poarta rulată pe
  FIECARE copie: [POARTA.md](POARTA.md) §3. ⚠️ `deploy.sh`/`deploy_parity.py` **nu acoperă**
  `plugins/gigi/skills/**`, de asta cele două fișiere au driftat tăcut.
- **Cronul se decomentează ULTIMUL** — după desfășurare și după o poartă VERDE pe copia de pe
  VPS. Gardă gata scrisă, **neinstalată**: `desfasurare/cs_backlog_garda.sh`.

## Necesită
`RICHPANEL_MCP_TOKEN`, cheie LLM, `customer-identity` (→ `DATABASE_URL_METRICS` + SSH), `gigi:cs-actions`
(token-uri `write_orders`), `META_SYSTEM_TOKEN` (pt hide/unhide).

> ⚠️ Apply-paths (`cs-actions --apply`, hide/unhide Graph) sunt codate dar de **validat live o dată**
> (scope token Meta moderare + formatul exact al comment-id din ticket id).
