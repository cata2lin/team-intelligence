# PLAN DE CAPTARE READ-ONLY — faza „oglindă"

**Obiectiv:** să avem local tot ce are Richpanel (tichete + fir complet de conversație, inclusiv răspunsurile agenților) și, unde se poate, materia primă de la sursă (Meta). **Zero scrieri**: fără reply, fără draft, fără notă privată, fără tag, fără hide/delete în Meta.

**Premisa care schimbă design-ul** (confirmată de verificări, contrar primei intuiții): sursa de adevăr pentru *conținut CS* este **Richpanel**, nu Graph API. Graph e complement, nu înlocuitor. Motivul e măsurat: pe 44 de comentarii unde Facebook arată că pagina a răspuns, Richpanel avea răspunsul în **44/44** cazuri, cu numele real al agentului, iar potrivirea de text a fost **40/40 = 100%**. Invers nu e adevărat: Graph nu dă niciodată identitatea comentatorului (**0 din 24.110** comentarii au `from` de persoană fizică).

---

## 1. CE SE POATE ACUM — per canal, cu dovadă

Mix real de volum (Richpanel, de la 1-iun-2026, total 49.497 tichete): `facebook_feed_comment` 28.143 (56,9%) · `email` 14.239 (28,8%) · `facebook_message` 3.593 (7,3%) · `messenger` 1.410 (2,8%) · `email_from_widget` 1.114 (2,3%) · `instagram_comment` 701 (1,4%) · `instagram_message` 297 (0,6%). Istoric mai există `aircall` (7.888 tichete, ~3%).

### 1.1 Richpanel MCP — coloana vertebrală, acoperă 100% din canale
- `get_conversation` cu `mode=audit`, `max_messages=50`, `max_message_chars=6000`, `include_private_notes=true` întoarce firul complet: mesaje client + **răspunsuri agent cu nume real** (`author_is_workspace_agent=true`, `author.name` = Cristina Sava / Monica Dan / Diana Popa / Irina Oprea / Martina K / Karina Urzica) + note private + atașamente ca URL S3.
- Testat la scară, nu pe caz norocos: eșantion stratificat **95 conversații, 0 erori**; structură identică pe toate; paginare dusă până la capăt pe o conversație de **875 mesaje** (18 pagini, 875 unice, 0 goluri, 33s).
- Lookup direct pe `ticket.id` merge pe **14/14** teste, pe 7 canale (inclusiv `facebook_feed_comment`, `instagram_comment`, `aircall`). Deci harvestul poate fi condus din id-urile pe care le avem deja în SQLite, fără re-enumerare.
- Debit real măsurat luni, în plină zi de lucru, cu rezervă lăsată CS-ului live: **52,3 conversații/min, 180 conversații, 0× 429, 0 erori**, ~2,5 KB/conversație.
- Rate-limit: 60 req/min pe token, cu headere **care există** (`Retry-After: 60`, `x-ratelimit-limit/remaining/reset`) — raportul inițial spunea greșit că nu există. Se pilotează pe `x-ratelimit-remaining`.

### 1.2 Comentarii Facebook (57% din volum) — se pot citi de la sursă, ieftin
- Calea validă: `GET /{page_id}_{post_id}/comments?filter=stream&order=reverse_chronological` **cu page access token**. Cu system token direct pe `/{comment_id}` → `(#200) Missing Permissions`.
- **Costul de cotă e ~zero pe această cale**: 563 postări citite în 47,3s cu `x-app-usage` neschimbat (147→147); 120 postări cu paginare = 8.090 comentarii în 18,9s (428 comentarii/s), 0 erori — în același moment în care `/me/accounts` (user-scoped) returna `(#4)`. Throttle-ul e selectiv pe tip de endpoint, nu global.
- Acoperire, cu numitorul corect (excluzând tichetele unde Richpanel însuși arată `This message was deleted`): **1.218/1.607 = 75,8%** din ce încă există (Reduceri Bune 82,6% · MagDeal 88,6% · Casa Ofertelor 87,2% · Esteban 55,6%). Cifra de 23-43% din raportul inițial era calculată cu tichetele șterse în numitor.
- Câmpuri utile pe care Richpanel **nu** le are: `is_hidden`, `permalink_url`, `parent{id}` (firul de reply), `like_count`, `attachment.media.image.src` (poza trimisă de client), plus legătura cu **campania/creativul** prin `story_id`.
- Descoperirea postărilor: reuniunea a trei surse. `/act_X/adcreatives` dă de 7-11× mai multe story-uri decât `/ads` (GT 1.252 vs 109; Esteban 1.425 vs 197), iar lista derivată din id-urile tichetelor Richpanel (`{page}_{post}_{post}_{comment}`, consistentă **17.387/17.387**) a adus **+31%/+34%** peste sweep-ul pe ad creatives. Niciuna singură nu e suficientă.
- Răspunsurile agenților SUNT în Graph (3.513 reply-uri de pagină pe Esteban) — dar fără să știi *care* agent.

### 1.3 Mesaje private Facebook (7,3%) — merg complet
- `GET /{page}/conversations` merge pe **20/20** pagini testate care generează tichete DM (2.669 mesaje, 35,4% de la pagină). Recensământ verificat exact pe 4 pagini (54/54, 65/65, 11/11, 121/121).
- **Fără fereastră de retenție**: istoric până în 2022-09 pe unele pagini. Fără trunchiere de fir: 12 thread-uri cu >25 mesaje, `message_count == fetched` în **12/12** cazuri.
- Test decisiv de paritate cu Richpanel pe aceeași conversație (RP #312780 ↔ Meta `t_1568293114781914`): **17 vs 17 mesaje**, potrivire 1-la-1 pe timestamp + text, inclusiv atașamentul audio.
- Filtru nativ pe client: `GET /{page}/conversations?user_id={PSID}` — PSID-ul e exact valoarea din `from.id` a tichetului Richpanel.
- Debit: 800 conversații + 4.704 mesaje în 44,7s (17,9 conv/s), 0 erori.

### 1.4 Instagram — doar comentarii organice
- `GET /{ig_id}/media` → `/{media}/comments` întoarce comentarii **cu `username`** (identitate!) și `replies` cu răspunsurile noastre. Dar volumul organic e ~0 (ultimele 10 postări GT: 0,0,0,0,0,0,3,0,0,0).
- Cele 701 tichete `instagram_comment` sunt comentarii la **reclame** (`to.id` = page id). Calea prin `effective_object_story_id` pentru IG **nu a fost testată** (vezi §6, pasul 3).

### 1.5 Email — zero de la sursă azi
- Toate cutiile `contact@*` sunt conturi Google reale, într-un tenant Workspace diferit: impersonarea dă `unauthorized_client` pe **7/7** cutii, iar controlul negativ pe același domeniu (`zzz-fake-777@apreciat.ro`) dă `invalid_grant`. Diferența dintre cele două erori e dovada că cutiile există și că nouă nu ni s-a acordat DWD.
- Mecanismul ar funcționa: IMAP `EXAMINE` + `BODY.PEEK` verificat non-mutant pe o cutie ARONA reală (UNSEEN 1529 înainte și după). Dar credențiala funcțională e a unei cutii de **facturi**, nu de CS.
- Cheia de join e sigură dacă vom avea vreodată acces: `ticket.id` = Message-ID RFC822 pe **14.202/14.239 = 99,74%** din emailuri; căutare IMAP `HEADER Message-ID` → exact 1 uid.
- **Concluzie operațională pentru faza asta: emailul se ia exclusiv prin Richpanel.**

---

## 2. CE NU SE POATE — și de ce

| Gol | Mărime măsurată | Cauză | Se repară? |
|---|---|---|---|
| **Identitatea comentatorului în Graph** | 0 persoane fizice din 24.110 comentarii (și 0/8.090 pe re-test) | `from` e omis tăcut de API pe comentarii de persoane; datele circulă doar prin webhook | Nu prin API de citire. Doar webhook (netestat, vezi §6) |
| **Comentarii șterse** | 0/1.429 recuperate din Graph; 46-61% din tichetele FB din august au `This message was deleted` în Richpanel | ștergere la sursă; `GET /{comment_id}` → `(#100) does not exist` | Nu. Se previne doar prin captură rapidă (mediana ștergerii: Esteban 1,4h, Reduceri Bune 11,5h, MagDeal 16,2h) |
| **Backfill istoric FB comments** | Graph acoperă 20-33%/lună din ce a primit CS în lunile trecute | ștergerile s-au produs deja | Nu. Orice backfill produce un dataset sistematic biasat (lipsesc tocmai comentariile negative/spam) |
| **DM Instagram** | 0 pe toate cele 17 conturi IG (297 tichete/perioadă) | pe paginile cu volum IG real: HTTP **200 + `{"data":[]}`** (eșec tăcut); `subcode 2534041` apare doar pe pagini cu 0 tichete IG | Cauza reală **nu e cunoscută**; fixul „toggle din app-ul Instagram" e infirmat |
| **Pagini fără token** | 12% din volumul social (19.378 tichete istoric). Cea mai mare: `364899953373966` = 7.723 tichete, 2.188 comentarii de la 1-iul, 386 `facebook_message` | `(#10)` cu **toate** cele 3 tokenuri → nu e problemă de token | Da, dar **administrativ**: adăugarea paginii pentru system user `122121862052787611` în Business Manager |
| **Email de la sursă** | 28,8% din tichete (din care doar ~18,4% umane) | fără DWD pe tenantul ARONA; în plus grantul existent are doar `gmail.modify` (**scriere**) | Da, administrativ, cu scope explicit `gmail.readonly`. Chiar și atunci: oglinda ar prinde **doar inbound** — Richpanel trimite prin SES/`customerdesk.io`, deci 55,3% din mesaje (18.965) rămân invizibile |
| **`messenger` + `email_from_widget`** | 2.524 tichete (5,1%) | widget de chat pe site; nu există în Graph sub nicio formă; fără Message-ID | Nu. Doar Richpanel |
| **`aircall`** | 7.888 tichete istoric | mesaje de tip `VoiceComment`, fără text; 0 mesaje de agent în 9/9 tichete | Nu (ar trebui transcriere audio, alt proiect) |
| **Identitatea agentului în Meta** | — | Meta arată doar `from = page` | Nu. Doar Richpanel dă numele agentului |
| **Enumerare din `list_conversations`** | offset plafonat la pagina 50 (~2.500 înregistrări/filtru); `has_more` a raportat `False` cu 14 pagini rămase (744 rânduri) | limitare API | Se ocolește: feliere pe **zile**, paginare până când `returned < per_page`, niciodată pe `has_more` |
| **Rație Meta** | app-ul `1268707461439970` stă cronic la 100-230% din `x-app-usage`, din pipeline-ul de reclame; apelurile eșuate **incrementează** contorul; deblocare ~75 min | cotă per-app, partajată cu producția (`meta_sync.py`, `cs_auto_draft.py`, webhook-ul planificat) | Se ocolește: **numai** endpointuri page-scoped. Interzis: `/{comment_id}`, Batch API (50 subrequests = 50 apeluri, 85/200 au picat), `/me/accounts` mai des de 1×/zi |
| **Rație Richpanel** | 60/min partajat cu CS-ul live — am luat 429 la primul apel propriu, cu 0 apeluri proprii în fereastră | un singur token pentru toate skill-urile CS | Se gestionează: rezervă de ≥12 req/min, cap 30/min în orele de program |

Corecții de așteptări față de rapoartele inițiale, care contează la dimensionare:
- Doar **36,9%** din tichete (97.955/265.195) au ≥2 mesaje. Restul nu conțin niciun răspuns de agent — 265k apeluri produc conținut CS în ~37% din cazuri.
- **45%** din mesajele marcate „agent" au `author.id = "operator"` = automatisme canned ale widgetului („Sunteți deja client?"). Se filtrează, altfel statistica „ce răspunde CS" e poluată masiv.
- Câmpul `is_ai` e mort în workspace (0/87), nu se folosește ca detector de bot.
- Din 14.239 emailuri, **5.144 sunt automate** (3.973 Judge.me, 744 Shopify, 427 klaviyo/curieri). Emailul „uman" e 18,4% din CS, nu 28,5%.

---

## 3. ARHITECTURA MINIMĂ

**Unde:** VPS `84.46.242.181`, sub `/root/Scripturi/` (același tipar ca restul: `uv run`, PEP-723, cron + `heartbeat.py`, `deploy.sh --apply` pentru cod).
**Nu știu** dacă pe `84.46.242.181` există un Postgres în care avem drept de scriere (cele 5 MCP-uri Postgres sunt read-only prin construcție, iar HARTA menționează doar SQLite local pe VPS). **Test de 2 minute:** `psql "$LOCAL_PG" -c 'select 1'` / `systemctl status postgresql` pe VPS. Până atunci **default = SQLite WAL**, `/root/Scripturi/data/cs_mirror.db`, pentru că se potrivește cu tooling-ul existent (backup online-API + gzip + rotație 7, exact ca `backup_profitdb.py`).

**Reutilizare, nu fork:** clientul MCP `rp.py` și `richpanel_export.py` există deja în `plugins/gigi/skills/richpanel-export/` și au deja `pull_log(day, n, done_at)`. Schema locală actuală (`tickets`, cu `raw`) **nu are tabel de mesaje** — asta se adaugă, nu se rescrie. Baza locală e stale (ultimul tichet `2026-08-17`, 14 zile oarbe) — primul job o aduce la zi.

### Tabele

```
rp_ticket(id PK, conversation_no, channel, status, priority, assignee_id,
          to_id, to_email, from_id, from_email, customer_id, customer_name,
          customer_email, customer_phone, tag_names, subject, first_message,
          comment_count, created_at, updated_at, closed_at, first_responded_at,
          store_resolved, fetched_at, msg_total_at_fetch)
rp_message(ticket_id, msg_id, idx, created_at, type, is_private, is_ai,
           author_id, author_name, is_agent, is_operator_bot, channel,
           text, text_len, truncated, fetched_at,  PK(ticket_id,msg_id))
rp_attachment(ticket_id, msg_id, url, http_status, fetched_at)

fb_post(page_id, story_id PK, source, first_seen, last_scanned, last_comment_at)
fb_comment(comment_id PK, story_id, page_id, parent_id, created_at, message,
           is_hidden, like_count, comment_count, permalink_url, attachment_url,
           from_id, from_name, first_seen, last_seen, disappeared_at)
fb_thread(thread_id PK, page_id, psid, participant_name, message_count, updated_time)
fb_message(msg_id PK, thread_id, page_id, from_id, is_page, created_at,
           text, shares_json, story_json, attachments_json, tags)
ig_comment(comment_id PK, media_or_story_id, ig_user_id, username, text,
           created_at, is_reply, parent_id)

parity_daily(day, channel, page_or_store, rp_count, mirror_count, matched,
             missing_sample, deleted_in_rp, frozen_by_us, coverage_pct, run_at)
sync_run(job, started_at, ended_at, ok, items, errors, quota_snapshot)
```

**Regula de îngheț (cea mai importantă):** `fb_comment.message` și `rp_message.text` **nu se suprascriu niciodată** cu o valoare goală, cu `This message was deleted`, sau cu un text mai scurt. La rescan, dacă un comentariu nu mai apare → doar `disappeared_at = now()`. Asta e singurul lucru pe care oglinda îl face și pe care nici Richpanel, nici Graph nu-l mai pot face.

### Joburi (toate READ-ONLY, toate cu heartbeat pe succes)

| Job | Interval | Ce face | Buget |
|---|---|---|---|
| `rp_sync.py` | 15 min (fereastră deschisă) + 03:00 re-pull ultimele 3 zile | `list_conversations` feliat pe zi, `sortKey=updatedAt&order=desc`, paginare până la `returned < per_page`; apoi `get_conversation` audit complet + `include_private_notes=true` + cursor pe mesaje | pacer pe `x-ratelimit-remaining`, rezervă 12/min; cap 30/min în 08:00-20:00, 50/min noaptea |
| `fb_comment_poll.py` | 10-15 min | pentru fiecare pagină accesibilă: story-uri active (reuniune: story-uri din tichetele RP din ultimele 48h + `/{page}/feed` + inventar creative) → `/{story}/comments` cu oprire la watermark `created_time` | page-scoped, cost ≈0; toată flota se baleiază în ~70s |
| `fb_dm_poll.py` | 30 min | `/{page}/conversations?limit=100` sortat pe `updated_time`, expandare doar pentru thread-uri modificate; câmpuri **explicit** `message,shares{link,name},story,sticker,attachments{...},tags,from,to` | ~180 apeluri/ciclu |
| `fb_inventory.py` | zilnic 04:00 | `/act_X/adcreatives` pe cele 39 conturi → `fb_post` | contorul `ads_management` e per ad account, nu se cumulează |
| `page_tokens.py` | zilnic 05:00, cu backoff | `/me/accounts?fields=id,name,tasks,access_token` (user-scoped = scump, o dată/zi) | 1 apel + retry |
| `parity_check.py` | zilnic 09:00 | §4 | ~60-120 apeluri RP |
| `backup_cs_mirror.sh` | zilnic 03:30 | SQLite online-backup + gzip + rotație 7 | — |

**Gardă de read-only, în cod, nu în intenție:**
1. wrapper peste `rp.MCP.call` cu **allowlist explicit**: `get_conversation`, `list_conversations`, `list_users`, `list_tags`, `list_teams`, `search_conversations_by_customer`, `query_analytics`. Orice altceva → excepție. (`rp.py` blochează deja `send_message`; se extinde la `create_draft`, `add_private_note`, `update_*`, `assign_*`, `snooze_*`, `*_tags`.)
2. client HTTP Meta care refuză orice metodă ≠ GET.
3. `kb.py guard-add deny` pe pattern-urile de scriere Richpanel/Graph, ca să prindă și un script scris ulterior de altcineva.
4. **Tokenurile nu ating discul.** În sonde s-au găsit 5 fișiere de secrete world-readable (`0644`) în scratchpad. Page tokenurile se iau la runtime prin `kb.py secret-get`/`/me/accounts` și rămân în memoria procesului. Nu se scriu niciodată în `cs_mirror.db`.

---

## 4. VERIFICAREA DE PARITATE — efectul, nu codul

Regula echipei: nu „jobul a rulat", ci *câte tichete îndeplineau condiția și pe câte s-a produs efectul*. `parity_check.py` rulează zilnic la 09:00 pe **D-1** (zi completă) și scrie în `parity_daily`. **Numitorul se ia LIVE din Richpanel**, niciodată din propria noastră copie — altfel verificăm oglinda cu oglinda (capcana din care watchdogul de adrese a raportat „mort" un modul viu, fiindcă se uita în tabelul greșit).

**M1 — paritate de tichete.** Pentru fiecare (canal × magazin): `count(RP live, created_at ∈ D-1)` vs `count(rp_ticket)`. Prag: **100%**. Raportăm *lista de id-uri lipsă*, nu procentul — un procent de 99,4% ascunde exact tichetele pe care le pierdem sistematic.

**M2 — paritate de mesaje.** Pentru fiecare tichet din D-1: `messages_page.total_available` la momentul fetch-ului vs `count(rp_message)`. Plus re-fetch **30 tichete aleatorii/zi** și diff pe număr + hash de text. Asta prinde trunchierea tăcută (un singur apel se oprește la 20 de mesaje și raportează `next_cursor`, iar `total_available` e relativ la filtru — dacă uiți `include_private_notes`, „completitudinea" e o iluzie: pe conv 312926, 5 mesaje fără flag vs **23 cu flag**, din care 17 note).

**M3 — răspunsurile CS chiar sunt captate.** Tichete cu `first_responded_at IS NOT NULL` (câmp de la Richpanel, independent de parserul nostru) dar cu **0 mesaje de agent uman** în oglindă → trebuie ≈0, cu excepția celor unde singurul „agent" e `operator`. Raportăm separat: `agenti_umani`, `operator_bot`, `note_private`. Dacă raportul uman/operator se abate mult de la ~55/45 măsurat, ceva s-a schimbat în parsare sau în workspace.

**M4 — valoarea livrată de îngheț (metrica care justifică proiectul).** Număr de comentarii din D-1 unde **noi avem textul** iar Richpanel arată `This message was deleted`. Se măsoară în bucăți, zilnic. Dacă e 0 timp de o săptămână, faza „oglindă" pe comentarii nu-și merită costul și se oprește. Baseline așteptat: în august, 2.818 din 6.098 tichete FB erau deja marcate șterse.

**M5 — taxonomia golului invers.** `comment_id`-uri prezente în tichetele Richpanel din D-1 dar absente din `fb_comment`, clasificate: (a) pagină fără token, (b) story nedescoperit, (c) șters înainte de poll, (d) necunoscut. Categoria (d) e alarma reală — înseamnă că nu înțelegem mecanismul. Distribuția trebuie să fie stabilă; o creștere = ceva s-a rupt.

**M6 — paritate DM.** Per pagină: tichete `facebook_message` în D-1 vs thread-uri/mesaje noi în oglindă; plus verificare 1-la-1 pe **5 thread-uri aleatorii** (timestamp + text), exact testul care a dat 17/17 pe conv 312780.

**M7 — sănătatea cotelor.** `x-app-usage` înainte/după fiecare job Meta; `x-ratelimit-remaining` minim atins pe fereastră la Richpanel; număr de 429/(#4). Prag roșu: orice job care a scăzut `remaining` sub rezerva de 12, sau orice apariție nouă de `(#4)` în `sync_runs` la pipeline-ul de reclame (semn că am înfometat producția).

**Alertare:** email **doar** pe roșu sau pe drift NOU (tiparul `reconcile_sources.py`). Roșu = M1 < 100% · M2 < 99,5% · orice canal cu 0 rânduri 24h (detector de efect mort) · M5(d) > 0 · M7 în prag.

**Confirmare umană lunară:** 5 tichete alese la întâmplare, deschise în UI-ul Richpanel de un om din CS, comparate cu ce avem în oglindă. Nu doar din DB — exact lecția din incidentul OH.

---

## 5. COSTUL

**Apeluri și timp — Richpanel** (1 apel ≈ 1 conversație; doar 130 din 265.195 tichete depășesc 50 de mesaje):
- Incremental zilnic: ~660 tichete noi + re-pull fereastră 3 zile (~2.000) ≈ **2.700 apeluri/zi** ≈ 54 min la 50/min, ~90 min la 30/min. Se rulează în ferestre de noapte + puls de 15 min ziua.
- Lună curentă (20.000 tichete): **6,4 h/lună**.
- Istoric complet 265k: **~85 h ≈ 3,5 zile** de rulare continuă la 52,3/min. Restrâns la cele 97.955 cu ≥2 mesaje (singurele care conțin răspunsuri CS): **~31 h**. Recomandare: se face doar varianta de 31 h, în weekend, la 30/min.
- ⚠️ `sleep(1.1)` naiv **nu** dă 55/min — cu latența p50 0,34-0,42s a dat 37,1/min măsurat. Trebuie pacer pe deadline sau pe header.

**Apeluri și timp — Meta:**
- Comentarii: cost de cotă ≈ **0** pe calea page-scoped (563 postări, `x-app-usage` neschimbat). ~240 postări/zi primesc comentarii, 847 postări active pe 7 zile → un baleiaj complet în ~70s. Poll la 15 min = ~96 baleiaje/zi, perfect fezabil.
- DM: 17,9 conv/s; backfill complet 17.324 thread-uri ≈ **16 min** (~174 apeluri de listă).
- Interzis, cu buget măsurat: `/{comment_id}` (≈**330 apeluri/oră** disponibile, +61 puncte de cotă la 200 apeluri) și Batch API.

**Stocare:**
- Richpanel: ~2,5-3 KB JSON/conversație → **0,8-1,2 GB** brut pentru tot istoricul; normalizat în SQLite, estimare 0,6-1,5 GB — **nu știu exact**, se măsoară după primele 5.000 de tichete și se extrapolează.
- FB comments: ~54k comentarii/12 luni pe cele 5 pagini mari, ~700 B/rând → zeci de MB/an. FB DM: 17.324 thread-uri, ~20k-60k mesaje → sub 100 MB.
- Atașamente: **doar URL-uri** în faza asta. Nu știu dacă URL-urile S3 Richpanel sunt descărcabile fără autentificare și dacă expiră — test: 10 URL-uri, unul dintr-un tichet din 2024, se notează codurile HTTP. Dacă expiră, descărcarea devine obligatorie și costul de stocare crește cu un ordin de mărime.
- Backup: ×0,2 după gzip, rotație 7 (tiparul `profitability.db`: 333 MB → 60 MB).

**Comparația cu 356 USD/lună — onest:** faza „oglindă" **nu reduce** factura Richpanel și nu e proiectată s-o reducă. Costul ei marginal e ~0 în bani (rulează pe VPS-ul existent, folosește tokenuri existente) și e plătit în **contenție**: partajăm 60 req/min cu CS-ul live și o cotă Meta deja saturată de reclame. Ce cumpără: (a) istoric complet cu răspunsurile agenților, pe care exportul local actual nu-l are (doar `first_message`, trunchiat la 301 caractere); (b) textul comentariilor care se șterg și pe care le pierd *ambele* sisteme; (c) independență de UI-ul Richpanel pentru analiză.

Ca să înlocuiască Richpanel ar trebui trei lucruri pe care **nu le avem și nu le-am testat**: ingestia prin webhook (singura care poartă identitatea comentatorului — Graph nu o dă), trimiterea outbound, și UI-ul de agent + SLA. Orice cifră de economie înainte de a testa webhook-ul ar fi o promisiune, nu o estimare.

---

## 6. PRIMII 3 PAȘI

### Pasul 1 — Coloana Richpanel + harnașamentul de paritate, pe o fereastră de 7 zile
Deploy `rp_sync.py` + `parity_check.py` pe VPS; se aduce baza de la `2026-08-17` la zi (14 zile oarbe) și se rulează paritatea pe D-1…D-7.
**Criteriu de succes (măsurabil):**
- M1 = **100%** pe fiecare din cele 7 zile, per canal și per magazin, cu lista de id-uri lipsă **goală** (nu „99,x%").
- M2: re-fetch pe 30 tichete/zi → **0 diferențe** de număr de mesaje și de hash de text.
- M3: tichete cu `first_responded_at` non-null și 0 mesaje de agent în oglindă → **0** (excluzând `operator`).
- M7: **niciun 429**, `x-ratelimit-remaining` nu a coborât sub 12 în nicio fereastră.
- Se raportează dimensiunea reală pe disc pentru cele 7 zile → extrapolarea de stocare devine măsurătoare.

### Pasul 2 — Congelatorul de comentarii FB pe primele 5 pagini, 72h de rulare
`fb_comment_poll.py` la 15 min pe Esteban, Reduceri Bune, MagDeal, Casa Ofertelor, GT, cu descoperire pe reuniunea celor 3 surse.
**Criteriu de succes:**
- ≥**95%** din `comment_id`-urile pe care Richpanel le primește în fereastră apar în oglindă în ≤30 min (interogarea M5, pe D-1).
- **M4 > 0, cuantificat**: numărul exact de comentarii unde noi avem textul iar Richpanel arată `This message was deleted`. Ăsta e efectul, nu „scriptul rulează". Dacă e 0, componenta se oprește.
- Impact de cotă: delta `x-app-usage` atribuibilă jobului ≤ **5 puncte/oră**, și **zero** eșecuri `(#4)` noi în `sync_runs` la pipeline-ul de reclame în cele 72h.
- Bonus măsurat automat: curba de supraviețuire a comentariilor (din `first_seen` vs `disappeared_at`) → decide dacă intervalul de 15 min e prea rar. Momentan e o ipoteză, nu o valoare optimizată.

### Pasul 3 — Închiderea golurilor: 1 cerere administrativă + 3 teste care decid domeniul de acoperire
(a) **Administrativ:** cerere către admin-ul Business Manager pentru acordarea paginilor lipsă către system user `122121862052787611`, începând cu `364899953373966` (cea mai mare gaură). *Criteriu de succes = efectul, nu confirmarea:* `GET /{page}?fields=access_token` → 200, **și** în 24h comentariile paginii apar în `fb_comment`, iar M5(a) scade cu numărul așteptat (~2.188 comentarii/2 luni pentru pagina asta).
(b) **Testele care lămuresc ce nu știu** (fiecare cu răspuns numeric, nu opinie):
1. *Richpanel live păstrează textul șters?* 50 de tichete al căror `first_message` local e `This message was deleted` → `get_conversation` live. Răspuns binar: dacă textul există, valoarea congelatorului scade dramatic și pasul 2 se reevaluează. **Nu știu răspunsul.**
2. *Comentariile la reclame IG sunt citibile?* 20 de story-uri de reclamă IG prin `effective_object_story_id` → `/comments`. Succes = număr de comentarii > 0 și potrivire cu ≥1 tichet `instagram_comment`. Decide dacă cele 701 tichete IG intră sau nu în oglindă.
3. *Există deja webhook-uri FB pe app-ul nostru?* `GET /{app_id}/subscriptions` cu app token (`META_SYSTEM_TOKEN_4`), plus inspecția `shared/scripturi-tools/fb-comment-webhook`. Dacă `feed`/`messages` sunt deja abonate, **acolo e identitatea comentatorului** — singura cale cunoscută spre ea — și schimbă complet calculul din §5. **Nu știu dacă e abonat.**

---

## Ce nu știu (explicit)

1. Dacă `get_conversation` **live** mai are textul pentru tichetele marcate șterse (test 3b.1). Toată dimensionarea valorii congelatorului atârnă de asta.
2. De ce Graph ratează ~24% din comentariile care încă există. Nu pot separa „API-ul chiar nu returnează" de „scrape-ul a fost trunchiat tăcut de rate-limit" (sondele rulau 6 threaduri cu app-ul la 200%+, iar `fetch()` returna rezultat parțial la epuizarea retry-urilor). **Test:** re-paginare exhaustivă, single-thread, cu eșec zgomotos, pe story-ul `122185382600525741` (Graph a luat 179, Richpanel are 1.449 tichete) — comparație directă.
3. Identitatea paginii `364899953373966`: o verificare a etichetat-o „Ofertele Zilei", alta „prefix nocturna9540". **Test:** prefixele de comandă și subiectele tichetelor cu acel `to.id`, sau confirmare din Business Manager.
4. Cauza reală a DM-urilor IG goale (HTTP 200 + listă goală pe exact paginile cu volum). **Test:** pe Nubra, `/{page}/conversations?platform=instagram` cu page token proaspăt vs Instagram Messaging API pe id-ul IG, același token; dacă ambele sunt goale cu scope-ul acordat, e caz de escaladat la Meta.
5. Dacă pe `84.46.242.181` avem un Postgres scriibil (altfel SQLite).
6. Dacă URL-urile S3 ale atașamentelor Richpanel expiră (schimbă costul de stocare cu un ordin de mărime).
7. De ce `list_users` întoarce 6 agenți, nu 7 — un agent lipsește sau e dezactivat; atribuirea per-agent pe istoric poate avea găuri.

## Observație colaterală, de raportat separat
Connectorul nostru AWBprint→Richpanel re-postează o notă privată identică („🤖 [auto] profil identificat de sistem (intern)") aproximativ din oră în oră — **17 duplicate pe un singur tichet** (312926, 17-19 aug). Sunt scrieri irosite și zgomot în vederea agentului. Nu ține de faza „oglindă", dar e un bug de producție descoperit în timpul sondelor.