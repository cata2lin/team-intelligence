# AI-ul care răspunde la mesaje — documentație de predare

> **Sistemul:** `cs_auto_draft.py` — generează răspunsuri (drafturi) la tichetele de Customer
> Service din Richpanel: email, Messenger, Instagram, comentarii Facebook.
> **Stare: OPRIT** din 29 iunie 2026. Ultima scriere reală în Richpanel: **3 iulie 2026, 11:41**.
> **Documentat:** 15 septembrie 2026, pentru predarea dezvoltării.
>
> Documentul e produsul a patru investigații independente, fiecare verificată de un sceptic pus
> să o dărâme. **Toate patru au fost corectate** — greșelile lor sunt în §9, fiindcă sunt instructive.
> Unde scrie o cifră, e măsurată. Unde nu se știe, scrie „nu știu".

---

## 1. Ce trebuie să știi în primele cinci minute

**Sistemul a scris 2.642 de drafturi în Richpanel și niciunul n-a fost folosit.**

Asta e măsurătoarea de efect, făcută pentru prima dată acum, la predare: din 40 de tichete cu draft
verificate, **0 au fost acceptate**. 38 din 40 au fost închise de agenți umani **fără niciun răspuns
trimis**, la o mediană de **12 zile** după ce draftul fusese scris.

Sistemul a funcționat tehnic. Nu a produs niciun răspuns către un client.

**Cele trei cauze, măsurate:**

| Cauză | Dovada |
|---|---|
| **31% din drafturi conțin afirmații fabricate** | 360 din 1.162 drafturi „normale". Dominant: *„nu am găsit nicio comandă în sistemul nostru"* — 341 de cazuri, în care sistemul **nu făcuse niciun lookup** |
| **Vocea și procedurile învățate n-au ajuns niciodată în producție** | `.learned_playbook.md` e în `.gitignore`, deci nu ajunge pe VPS prin git, și nu a fost copiat niciodată manual. `load_playbook()` returnează `{}` tăcut → blocul „PROCEDURA ÎNVĂȚATĂ + VOCEA AGENȚILOR REALI" a fost **gol la toate cele 2.642 de drafturi** |
| **Nimeni n-a văzut că nu merge** | 24 de rulări consecutive, 5 zile, ~39.400 tichete procesate, **zero drafturi scrise** — și cron verde tot timpul. Pe VPS sunt 27 de joburi cu heartbeat; **niciunul nu e ăsta** |

**Un al patrulea lucru, descoperit abia la verificarea adversarială:** rularea din 2 iulie a scris
**1.217 drafturi pe comentarii publice de Facebook**, deși decizia de proces era că la comentarii
**nu** se draftează. Un al doilea wrapper, `cs_draft_all.sh.bak-0703`, bucla pe 7 canale în loc de 5.
Nimeni nu știa că există al doilea wrapper.

### Vestea bună

Blocajul principal din iunie **s-a deschis între timp**, fără ca cineva să observe:

- **Contextul reclamei e acum accesibil.** În iunie, AI-ul răspundea la un comentariu „Care e prețul?"
  fără să știe despre ce produs e vorba. Testat live azi, pe 6 tichete reale: **5 din 6 pagini** întorc
  textul reclamei cu tot cu preț — *„Set 5 Pijamale din Satin … 99 Lei"*, *„Cumperi 2, primești 1 GRATIS"*.
  `META_SYSTEM_TOKEN` are acum 29 de pagini, toate cu `MODERATE`.
- **Cheia Claude e validă** (verificat: HTTP 200, 11 modele). Codul o preferă automat când e în env —
  dar wrapperul de cron nu o exportă, deci a rulat tot timpul pe OpenAI.

---

## 2. Unde e codul — și care copie contează

**Există PATRU copii ale aceluiași fișier.** Asta a produs deja confuzie; e primul lucru de clarificat.

| Copie | Cale | Ce e |
|---|---|---|
| **VPS — cea care a rulat** | `/root/Scripturi/cs_auto_draft.py` | 93.425 o., mtime 3-iul 11:27. `sha` `b5f4742…` |
| git (repo) | `team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | `sha` `57631ae…` |
| **marketplace — cea pe care o execută plugin-ul** | `~/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | identică cu git |
| clona de pe VPS | `/root/Scripturi/team-intelligence/plugins/…` | identică cu git |

Diferența VPS ↔ git e de **o singură linie**: o pagină „Lab Noir" în `PAGE_STORE`, prezentă în git,
absentă pe VPS. Pagina are **zero tichete** în tot warehouse-ul, deci diferența e inofensivă —
dar a stat nedetectată 74 de zile, fiindcă `deploy_parity.py` **nu scanează** directorul
`cs-draft-reply`.

Plus **6 backup-uri** pe VPS (29-iun și 3-iul), inclusiv `.bak-efficiency-0703`.

---


---

## 3. Arhitectura și fluxul

> Sistemul e un pipeline batch peste coada deschisă din Richpanel: alege tichetele în care ultimul mesaj e al clientului, le trece printr-un triaj LLM care întoarce JSON, construiește un context (identitate, comenzi, poze, reclama pe care s-a comentat), generează un răspuns cu un al doilea apel LLM, îl trece prin post-filtre deterministe și îl salvează ca DRAFT — niciodată trimis, în configurația implicită. Motorul e `cs_auto_draft.py`, 1243 de linii pe VPS; versiunea din git e identică în afară de o singură linie (o pagină Facebook în plus, Lab Noir). Are 20 de flag-uri, 4 prompturi de sistem și 10 unelte MCP Richpanel. Cronul e oprit din 29-iun-2026 și ultima rulare, 2-iul, a eșuat integral pe rate-limit OpenAI (429 pe toate cele 464 de tichete) — garda anti-gunoi a prins totul, deci nu s-a scris nimic greșit, dar nici nu s-a produs nimic. Peste tot jurnalul: 1.336 drafturi salvate, 5.151 tichete excluse ca spam, 6.015 drafturi invalide blocate. Două lucruri de știut înainte de orice: pe VPS lipsesc `.learned_playbook.md` (deci vocea învățată din tichete reale nu s-a aplicat niciodată) și scripturile `cs_actions.py`/`customer_identity.py` (deci calea de acțiuni reale, `--approve`, e moartă acolo).


### 0. Orientare rapidă — ce e, unde e, în ce stare

## Ce face, în trei propoziții

Parcurge tichetele **OPEN** din Richpanel în care **ultimul mesaj e al clientului** (adică așteaptă răspuns de la noi), le triază cu un apel LLM care întoarce JSON, construiește un context cu tot ce știm despre client și comanda lui, apoi generează un răspuns cu un al doilea apel LLM. Răspunsul se salvează ca **DRAFT** în conversație — agentul îl deschide, îl retușează și îl trimite el. Există și căi de trimitere live și de execuție de acțiuni pe comenzi, dar sunt **opt-in explicit** și nu sunt în configurația de cron.

## Harta fișierelor

| Unde | Fișier | Ce e |
|---|---|---|
| git | `plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | motorul de backlog — **ăsta e sistemul** |
| git | `plugins/gigi/skills/cs-draft-reply/cs_draft_reply.py` | varianta pe o singură conversație (`--conv`), cu precedent semantic din tichete rezolvate |
| git | `plugins/gigi/skills/cs-draft-reply/grammar_audit.py` | poarta de limbă/gramatică peste ieșirea `--json` |
| git | `plugins/gigi/skills/cs-draft-reply/cs_ticket_index.py` | index semantic de tichete rezolvate (folosit doar de `cs_draft_reply.py`) |
| git | `plugins/gigi/skills/cs-draft-reply/mcp_server.py` | serverul MCP `arona-cs-inbox` care expune fluxul ca unealtă |
| git | `plugins/gigi/skills/cs-photo/cs_photo.py` | modulul canonic de „vedere” a pozelor — importat de motor |
| VPS | `/root/Scripturi/cs_auto_draft.py` | **versiunea care a rulat** (93.425 octeți, 3-iul-2026, 1243 linii) |
| VPS | `/root/Scripturi/cs_photo.py` | identic cu git (sha256 `4fb6079e…` pe ambele) |
| VPS | `/root/Scripturi/cs_backlog.sh` | wrapperul de cron (910 octeți, 2-iul) |
| VPS | `/root/Scripturi/data/cs_backlog.log` | jurnalul rulărilor (12 MB, 137.095 linii) |
| VPS | `/root/Scripturi/.auto_draft_proposals.json` | coada de propuneri (7,3 MB, 3.156 intrări) |
| VPS | `/root/Scripturi/.env` | secretele (root-600) |

## VPS vs git — diferența exactă

Task-ul avertizează că versiunile diferă. **Am făcut `diff` complet.** Diferența e **o singură linie**:

```diff
@@ -64,6 +64,7 @@
     "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
+    "61586834387211": "Lab Noir",
 }
```

Git are o intrare în plus în `PAGE_STORE` (pagina Facebook a magazinului Lab Noir), adăugată pe 8-iul, după ultimul deploy pe VPS (3-iul). Nimic altceva nu diferă: nici prompturi, nici gărzi, nici flag-uri.

- sha256 VPS: `b5f474232b0ad203c2a006f3f5b1beaf70a993c52796d3f57b21c1a270ce2f25`
- sha256 git: `57631aebe2f27af13b246c592c33e8d561554a5c2eb8c2ce1edd82d4500de439`

> **Convenție pentru toate numerele de linie din acest document:** se referă la **versiunea de pe VPS** (1243 linii), cea care a rulat. Ca să le mapezi pe git: identice până la linia 66, iar de la 67 încolo **linia din git = linia din VPS + 1**.

## Starea

Cronul e **oprit**. În `crontab -l`, linia 45:

```
# PAUZAT 2026-06-29 0 9-21/3 * * * /usr/bin/flock -n /tmp/cs_backlog.lock /root/Scripturi/cs_backlog.sh >> /root/Scripturi/data/cs_backlog.log 2>&1
```

Au mai existat 5 copii de siguranță din 29-iun și una din 3-iul (`cs_auto_draft.py.bak-*`). Evoluția lor arată ordinea în care s-au adăugat capabilitățile:

| Backup | Linii | Are |
|---|--:|---|
| `.bak-20260629-162212` | 1072 | ground, apply-send, send, close-spam |
| `.bak-20260629-163827` | 1185 | + **photos** |
| `.bak-20260629-164236` | 1196 | idem |
| `.bak-20260629-165430` | 1197 | idem |
| `.bak-20260629-170703` | 1233 | idem |
| `.bak-efficiency-0703` | 1235 | idem |
| **`cs_auto_draft.py` (live)** | **1243** | + **fast-triage** |

Ultima rulare din jurnal e **2026-07-02 10:00:06**, adică versiunea actuală (3-iul, cu `--fast-triage` și integrarea `cs-photo`) **nu a rulat niciodată** în producție.

### 1. Fluxul complet, pas cu pas — funcția și linia

Tot fluxul principal e în `main()` (linia **846**). Îl parcurg în ordinea execuției.

---

### Pas 0 — bootstrap (846-878)

| Ce | Linii |
|---|---|
| Parsează cele 20 de flag-uri | 849-868 |
| `AI_TAG = a.tag` — setează globala folosită de tot restul | 869-870 |
| `ALLOWED` = mulțimea acțiunilor active din `--actions` (`none` → mulțime goală) | 871 |
| `MCP(secret("RICHPANEL_MCP_TOKEN"))` — handshake JSON-RPC `initialize` | 872 |
| Tabelul de curieri pentru afișare | 873 |
| **Scurtcircuit**: `--approve` → `do_approve()` și ieșire | 875-876 |
| **Scurtcircuit**: `--send` → `do_send()` și ieșire | 877-878 |

Clasa `MCP` (240-274) vorbește direct cu `https://mcp.richpanel.com/mcp` prin JSON-RPC, cu header `Authorization: Bearer <token>` și `Accept: application/json, text/event-stream`. Răspunsul e SSE — se ia **ultima linie `data:`** (linii 253-254). `_post` reîncearcă de 6 ori pe 429/500/502/503/504 și pe timeout/URLError, cu `Retry-After` dacă serverul îl dă, altfel backoff exponențial plafonat la 60s (248-266). `call()` (267-274) nu ridică niciodată excepție — întoarce `{"_error": …}`, ceea ce e important: **toate verificările de succes din cod testează `res.get("_error")`**.

---

### Pas 1 — SELECȚIA tichetelor (880-904)

Două căi.

**A. Țintit (`--only`)** — liniile 880-884. Se creează stub-uri `{"conversation_no": x, "_stub": True}`, fără niciun fetch în avans. Tichetul se ia individual în buclă (915-920), cu `get_conversation` în `mode="compact"`. Motivul: evită ráfala de citiri și e robust la paginare.

**B. Scanarea cozii** — liniile 885-904:

```python
args = {"status": "open", "page": page, "per_page": 50,
        "sortKey": "last_message_at", "order": "desc"}
if a.channel: args["channel"] = a.channel
r = mcp.call("list_conversations", args)
```

**Criteriul unic de selecție**, linia 898:

```python
if (t.get("last_message_sender_type") or "").lower() != "customer": continue
```

Asta e definiția lui „tichet care așteaptă răspuns de la noi”. Nimic altceva nu filtrează aici.

Paginarea (901-902) **nu se bazează pe `has_more`** — API-ul Richpanel nu îl setează fiabil. Se paginează cât timp apar tichete **noi** (`new_this_page`), cu `time.sleep(0.15)` între pagini. Oprire: `len(picked) >= --limit` **sau** `len(seen) >= --scan`.

---

### Pas 2 — pregătirea contextului, per tichet (914-1025)

| Subpas | Ce se întâmplă | Linii |
|---|---|---|
| Fetch stub | doar pe `--only`: `get_conversation` mode=compact; dacă nu-l găsește, sare | 915-920 |
| Canal + stil | `PLATFORM.get(channel, …)` → etichetă + regulă de stil; fallback generic | 922-923 |
| `is_public` | doar `facebook_feed_comment` și `instagram_comment` | 924 |
| `--no-comments` | sare complet canalele publice | 925-926 |
| Client | nume, email (din `customer.email` sau `from.email`), telefon | 928-932 |
| **Transcript** | dacă e comentariu public cu ≤1 mesaj → doar mesajul; altfel `get_conversation` **mode=`audit`, max_messages=20**, se iau ultimele 12 mesaje ne-private | 938-954 |
| Etichetare | `[AI]` / `[AGENT]` (`author_is_workspace_agent`) / `[CLIENT]`, trunchiat la 400 car. | 950-951 |
| `last_cust` | **ultimul** mesaj al clientului din fir | 952-953 |
| Poze client | `_csp.client_photos_block(msgs, …)` sau fallback local `describe_photos` | 955-959 |
| **Marcaj** | se adaugă la final: `>>> ULTIMUL MESAJ AL CLIENTULUI (răspunde la ACESTA; restul firului = context)` | 960-961 |
| `--skip-tagged` | dacă tag-ul `AI_TAG` e deja pe tichet → sare | 964-965 |
| Reclama | doar pe public: `fb_post_text()` (Graph, are nevoie de token de pagină) + `_csp.ad_block()` (og:image, **fără** token) | 966-987 |
| Sentiment | euristic pe cuvinte (31 negative / 16 pozitive), semne de exclamare, majuscule | 989, 210-223 |
| Brand | `PAGE_STORE[to.id]`, apoi `brand_from_email(to.email)`, apoi brandul primei comenzi | 990-993, 1003-1004 |

Despre **telefon**, linia 932: se acceptă doar dacă `raw_phone.isdigit()` și lungimea e 9-13. Un `+40…` cade și câmpul rămâne gol — vezi capitolul de îmbunătățiri.

**Marcajul de la 960-961 e cel mai important fix de comportament din tot fișierul.** Fără el, modelul răspundea la PRIMUL mesaj din fir: un client care întreba „unde e coletul”, primea auto-responder, apoi scria „Foarte bune, mulțumesc!” primea un draft de tip WISMO în loc de o mulțumire.

---

### Pas 2b — contextul de comenzi: trei regimuri (995-1025)

Ordinea de evaluare, exact cum e în cod:

```python
if a.ground and (email or phone or ORDER_RE.search(_gtxt) or AWB_RE.search(_gtxt)):
    orders = lookup_orders(email, phone, _onames, _awbs)      # 998-1005
elif a.lean:
    elsewhere = "(lean — fără context 360)"                    # 1006-1007
elif (not a.lean) and (email or phone):
    ci = customer_ident(no)                                    # 1008-1018
```

**`--ground` are prioritate față de `--lean`** dacă le dai pe amândouă. Căutarea se face în `_gtxt = subj + " " + tr` — adică în **subiect plus tot firul, inclusiv citatele** — nu doar în ultimul mesaj (linia 997). Asta prinde numărul comenzii din semnătura unui email citat.

Rezultatul se formatează în `od` (1020-1023):

```
    • EST000001 (EST): status=AWB creat, curier=DPD, AWB=0000000000001, produse=EST-0042
```

sau `"    (nicio comandă găsită)"` — string-ul ăsta e verificat mai târziu de `has_order_data()`.

---

### Pas 3 — IDENTIFY, triajul LLM (1027-1045)

Se pornește de la un dicționar implicit în care categoria vine din regexul `categorize_hint()` (201-208, 12 reguli la 172-185). Regexul e **doar hint** — greșea sistematic (recenzii→spam, adresă→factură).

Promptul de utilizator (1031-1032):

```
PLATFORMĂ: <etichetă>
MESAJ CLIENT:
<transcript, cu marcajul ULTIMUL MESAJ, plus pozele și reclama dacă există>

COMENZILE LUI:
<od>

A MAI SCRIS PE: <elsewhere> | nr alte tichete: <n>
SENTIMENT euristic: <lab>/<int>
HINT categorie: <cat regex>
```

> Atenție: aici se trimite `od` **ne-redactat**, chiar și pe canale publice. Corect — triajul e intern. Redactarea se aplică abia la promptul de draft (`od_ctx`, linia 1132).

Apelul: `llm(IDENTIFY_SYS, ident_user, js=True)` (1038). Parsarea taie între prima `{` și ultima `}` (1039). Pe excepție se scrie un avertisment pe stderr și se continuă cu euristicile (1043-1044) — **eșecul de triaj nu oprește tichetul**.

`--fast-triage` (1034-1035) sare complet acest apel dacă hint-ul regex e o categorie sigură (≠ `altele`, ≠ `spam_automat`). Trece de la 2 apeluri LLM/tichet la 1. Pierde extracția fină (adresă nouă, `items`), care oricum cere `--approve`.

Linia 1045 forțează categoria la `str` — un output LLM malformat nu mai crapă căutarea în `LEARNED`.

---

### Pas 4 — filtrul de SPAM și non-clienți (1047-1071)

Șase semnale, **cinci deterministe**, verificate în paralel:

| Variabilă | Sursa | Definiție |
|---|---|---|
| `idn["spam"]` / `cat == "spam_automat"` | LLM | judecata triajului |
| `_non_customer` | `NON_CUSTOMER_SENDER_RE` (76-79) | domenii de curier (dpd, sameday, econt, packeta, cargus, fancourier, posta-romana, gls, nemo-express) + aplicații Shopify (omegatheme, consentik, mailchimp, klaviyomail, sendgrid) |
| `_judgeme` | `JUDGEME_NOTIF_RE` (81) | subiect „left a N star review” — dar **nu** replica reală a clientului „Re: ⭐ …” |
| `_saas` | `SAAS_NOISE_SUBJ_RE` (83-86) | rapoarte săptămânale, „mentioned you on”, „new follower” etc. |
| `_bounce` | `BOUNCE_RE` (88-90) | mailer-daemon, „address not found”, „nu a putut fi livrat” |
| `_padded` | `_padded_noise()` (93-98) | >55% din corp sunt spații sau caractere Unicode invizibile (categoria `Cf`) — tiparul newsletterelor |

Dacă oricare e adevărat: tichetul e **EXCLUS complet** (short-circuit la 1071, `continue`) — nu se generează draft, nu se cheltuie apelul LLM de draft. Cu `--close-spam` se pune tag `spam` și se face `update_conversation_status → CLOSED` (1063-1067).

---

### Pas 5 — decizia de escaladare (1073-1081)

```python
_real_esc = _real_escalation(blob + " " + tr)
_llm_esc  = bool(idn.get("escalate")) or str(idn.get("severity")).upper() in ("HIGH", "URGENT")
_ord_cat  = cat in (11 categorii obișnuite)
is_esc    = _real_esc or (_llm_esc and not _ord_cat)
level     = "URGENT" if (_real_esc and (ESCAL.search(...) or severity == "URGENT")) else "HIGH"
```

Detaliile în capitolul 7.

---

### Pas 6 — comanda-țintă (1083-1087)

`resolve_target_order(blob + " " + tr, orders)` (definită la 460-484). Vezi capitolul 4, garda 5.

---

### Pas 7 — propunerea de acțiune (1089-1119)

Se intră aici **doar** dacă nu e escaladare, categoria e în `ACTION_CATS` (`modificare_comanda, anulare, schimb_swap, problema_produs, refuz_livrare`, linia 187) și canalul **nu e public** (1094).

Porțile, în ordine:

1. comandă ambiguă sau lipsă → `❓ Comandă neclară` și **nicio acțiune** (1095-1096);
2. acțiunea nu e în `--actions` → `🔕 dezactivată`, `act = "none"` (1099-1100);
3. `modify`/`cancel` pe o comandă în stare `post` → `🚫 PRE-FULFILLMENT NU` (1103-1104);
4. `confidence >= 0.55` (prin `_f()`, float defensiv) și avem prefix de magazin → `build_action_cmd()` (639-654) construiește comanda, apoi `run_cs_action(cmd, apply=False)` o rulează în **DRY-RUN** (1105-1113);
5. date insuficiente → draftul cere completarea (1116-1119).

**Nimic nu se execută aici.** Propunerea se salvează în coadă și se aplică doar cu `--approve`.

> Notă de cod: `action_note` e inițializat `""` la linia 1089 și **nu e setat niciodată** — la linia 1150 e folosit, dar concatenarea e mereu goală. Deliberat: propunerea nu trebuie să ajungă în promptul de draft, ca draftul să nu confirme o acțiune care încă n-a rulat.

---

### Pas 8 — moderarea comentariului (1121-1127)

Vezi capitolul 8.

---

### Pas 9 — generarea DRAFTULUI (1129-1154)

```python
sys_prompt = HOLDING if is_esc else SYSTEM                                    # 1130
od_ctx     = od if not is_public else "(comenzi ascunse — canal public…)"     # 1132
phone_ctx  = (phone or "—") if not is_public else "—"                        # 1133
email_ctx  = (email or "—") if not is_public else "—"                        # 1134
learned    = LEARNED.get(cat, "")                                            # 1135
lang       = detect_lang(...) or STORE_LANG.get(store_name) or idn["language"] or "ro"   # 1138
tel_blk    = "\nTELEFON_COMANDĂ: %s" % phone_order  (condiționat)            # 1140-1142
```

Contextul final (1143-1150) are forma:

```
PLATFORMĂ: <etichetă> — STIL: <regula de platformă>
MAGAZIN/BRAND: <store_name>
CLIENT: <nume> | email=<…> | tel=<…>
PROBLEMA IDENTIFICATĂ: <din triaj>
PRODUS: <din triaj>
CATEGORIE: <cat> | LIMBA: <lang> | SENTIMENT: <lab>/<int>
<TELEFON_COMANDĂ dacă e cazul>

CONVERSAȚIA:
<transcript + marcaj ultimul mesaj + poze + reclamă>

COMENZILE CLIENTULUI:
<od_ctx — redactat pe public>

A MAI SCRIS PE: <elsewhere>
ALTE TICHETE:
<hist_txt>
<PROCEDURA INVATATA + VOCEA AGENTILOR REALI pt '<cat>'… — max 1800 car.>
SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL… (orientativ: limba≈<lang>)
```

Apoi `draft, engine = llm(sys_prompt, ctx)` (1152). Pe excepție, draftul devine literal `"(eroare LLM: …)"` (1154) — string pe care garda de la 1212 îl prinde înainte de scriere.

---

### Pas 10 — post-filtrul anti-halucinație (1156-1172)

Condiția de activare (1157) — cinci ȘI-uri:

```python
if (not is_esc and not draft.startswith("(eroare")
    and not has_order_data(od_ctx) and not photo_blk
    and not _ad_has_catalog and HALLU.search(draft)):
```

Adică: **numai când chiar n-avem date**. Dacă avem comenzi, sau am văzut o poză, sau reclama a adus preț real din catalog, filtrul nu se aplică — pentru că atunci cifrele din draft sunt legitime.

Pe potrivire: o singură regenerare cu un prompt corectiv (1158-1160). Dacă și a doua încercare conține tipare de fabricare, se cade pe **șablon sigur** (1167-1172), diferit pe categorie:

- `presale_intrebare` / `comanda_noua`: „Vă revin cu detaliile exacte cât mai curând; între timp puteți vedea informațiile actualizate și pe site.”
- restul: „Ca să verific exact comanda dumneavoastră, îmi puteți spune numărul comenzii sau un număr de telefon asociat?”

---

### Pas 11 — consola (1174-1196)

Formatul unei intrări, exact cum arată în jurnal:

```
────────────────────────────────────────────
  [4/464] #278807 · Esteban · Email · livrare_wismo · sent=pozitiv/puternic ⛳HIGH 🔧🙈
  client: Roxana Manea | comenzi: 1 | a mai scris: grounded — 1 comenzi găsite în DB
  📷 2 poză(e) văzută(e) → folosite în draft
  problemă: Am lansat o comanda acum 3 zile. Nu am primit nici o notificare…
  🔧 PROPUNERE MODIFY pe EST000001 (necesită aprobare): …
  ┌─ DRAFT (openai/gpt) ────────────
  │ Bună ziua, …
  └──────────────────────────────────
  → aprobă:  uv run cs_auto_draft.py --approve 278807 --agent <Nume>
```

Apoi se scrie intrarea în coadă (1188-1190) și rândul pentru `--json` (1191-1194).

---

### Pas 12 — SCRIERILE în Richpanel (1198-1232)

**Totul e sub `if a.create_draft and cid:`** (1199). Fără flag, niciun octet nu pleacă spre Richpanel.

Ordinea internă:

1. **Rutare escaladare** (1201-1208) — doar dacă `is_esc and not a.lean`;
2. **hide propus** → nu se salvează draft (1210-1211);
3. **Garda anti-gunoi** (1212-1214) → sare tichetul;
4. **`--apply-send`** (1215-1225) → trimite live + închide, doar pe ne-escaladat și ne-public;
5. **altfel `create_draft`** + tag (1226-1231).

La final, `time.sleep(a.sleep)` (1232).

---

### Pas 13 — încheiere (1234-1239)

`save_queue(queue)` scrie `.auto_draft_proposals.json`. Se printează sumarul de spam. Cu `--json`, ultima linie e `@@JSON@@` urmat de JSON-ul rândurilor. Fără `--create-draft`, un reminder că a fost dry-run.

### 2. Flag-urile CLI — toate 20, cu combinațiile care contează

Definite la liniile **849-868**.

## Selecția tichetelor

| Flag | Implicit | Ce face | Linia |
|---|---|---|---|
| `--limit N` | `15` | câte tichete procesează | 849 |
| `--scan N` | `150` | câte tichete **citește** din listare până renunță (plafon de paginare) | 854 |
| `--channel X` | toate | filtrează pe canal Richpanel (`email`, `facebook_message`, `messenger`, `email_from_widget`, `instagram_message`, `facebook_feed_comment`, `instagram_comment`) | 850 |
| `--only 1,2,3` | — | **doar** aceste numere de conversație; ignoră complet listarea, ia fiecare tichet incremental | 866 |
| `--no-comments` | oprit | exclude canalele de comentarii publice | 859 |
| `--skip-tagged` | oprit | sare tichetele care au deja tag-ul din `--tag` → **idempotență** | 858 |

> `--limit` și `--scan` interacționează: bucla se oprește la primul dintre ele. Dacă pui `--limit 3000` dar lași `--scan 150`, iei maximum ~150 de tichete. De asta wrapperul de cron are `--limit 3000 --scan 6000`.

## Cât de mult context adună

| Flag | Implicit | Ce face | Linia |
|---|---|---|---|
| `--lean` | oprit | fără context 360, fără rutare de escaladare. Doar transcript → draft → `create_draft` | 856 |
| `--ground` | oprit | caută comenzile real, în proces, din metrics + `profitability.db`. Fără SSH, fără `uv` | 857 |
| `--photos` / `--no-photos` | **pornit** | vede pozele clientului și reclama comentată | 861 |
| `--fast-triage` | oprit | sare apelul LLM de triaj când categoria regex e sigură → 1 apel/tichet în loc de 2 | 860 |

### `--lean` vs `--ground` — diferența reală

Ambele ocolesc `customer_identity.py` (care are nevoie de SSH și `uv`, deci nu merge din cron). Diferă în ce pun în loc:

| | `--lean` | `--ground` | implicit (niciunul) |
|---|---|---|---|
| Comenzi în context | **niciuna** | din metrics + profitability.db | din `customer_identity.py` (SSH) |
| „Unde a mai scris” | `(lean — fără context 360)` | `grounded — N comenzi găsite în DB` | listă pe canale |
| Rutare escaladare | **dezactivată** | activă | activă |
| Post-filtrul anti-halucinație | **activ** (n-are date) | inactiv dacă a găsit comenzi | inactiv dacă a găsit comenzi |
| Viteză | ~10s/tichet | mai lent | cel mai lent, **rupt pe VPS** |

Dacă le dai pe amândouă, **`--ground` câștigă** (linia 998 e verificată prima).

`--ground` se activează doar dacă există **email, telefon, un număr de comandă sau un AWB** în text (linia 998). Altfel nu face niciun lookup și contextul rămâne gol.

## Ce scrie înapoi

| Flag | Implicit | Ce face | Linia |
|---|---|---|---|
| `--create-draft` | oprit | **poarta unică de scriere.** Salvează drafturi + rutează escaladări | 851 |
| `--tag X` | `ai-draft` | tag-ul pus pe tichetele tratate. `--tag ""` = fără tag | 865 |
| `--close-spam` | oprit | închide (CLOSED) + tag `spam` pe tichetele detectate ca spam | 864 |
| `--apply-send` | oprit | ⚠️ **TRIMITE LIVE** în buclă + închide tichetul. Necesită `--create-draft` | 862 |
| `--send <conv>` | — | ⚠️ trimite live **un singur** răspuns din coadă | 867 |
| `--approve <conv>` | — | aplică acțiunea/hide propusă pentru un tichet | 852 |
| `--agent Nume` | env `CS_AGENT` | agentul în numele căruia se execută acțiunea (Raluca/Oana/Andra/Anna/OanaO) | 853 |
| `--actions lista` | `modify,cancel,swap,resend` | ce acțiuni sunt **active**. `none` = niciuna | 854 |

### Ierarhia scrierilor — citește asta înainte de orice rulare

```
nimic          → DRY-RUN. Zero scrieri. DAR consumă apeluri LLM plătite.
--create-draft → scrie DRAFT + tag; la escaladări: priority HIGH + tag + notă privată.
                 NU trimite. NU execută acțiuni.
--approve N    → execută acțiunea pe comandă (cs-actions --apply) sau hide-ul FB.
                 Mod separat: iese imediat, nu parcurge coada.
--send N       → trimite LIVE un răspuns din coadă + închide tichetul.
                 Mod separat. Refuză escaladări, hide, retrimitere.
--apply-send   → trimite LIVE în BUCLĂ, pe tot lotul. Cel mai periculos flag din fișier.
```

`--apply-send` are trei porți proprii (1215): `a.apply_send and not is_esc and not is_public`. Escaladările și comentariile publice rămân drafturi.

## Operaționale

| Flag | Implicit | Ce face | Linia |
|---|---|---|---|
| `--sleep S` | `0.2` | pauză între tichete | 855 |
| `--json` | oprit | ultima linie = `@@JSON@@` + JSON-ul drafturilor, pentru `grammar_audit.py` | 863 |

Câmpurile din `--json` (1191-1194): `no, store, channel, cat, escalate, language, cust_msg, subject, orders, comment_action, draft`.

---

## Exemple reale de rulare

**Cronul, așa cum e în `/root/Scripturi/cs_backlog.sh`:**

```bash
for ch in email facebook_message messenger email_from_widget instagram_message; do
  .venv/bin/python3 cs_auto_draft.py --channel "$ch" --limit 3000 --scan 6000 \
      --create-draft --ground --no-comments --skip-tagged --tag ai-draft --sleep 0.5
done
```

**Inspecție sigură, înainte de a reporni ceva** (nu scrie nimic, dar costă apeluri LLM):

```bash
cd /root/Scripturi && set -a && . .env && set +a
.venv/bin/python3 cs_auto_draft.py --channel email --limit 5 --ground
```

> Atenție: dacă sursezi `.env` complet, `ANTHROPIC_API_KEY` intră în env și `llm()` comută pe Claude (linia 296). Wrapperul de cron NU-l exportă, deci cronul merge pe OpenAI.

**Regenerare țintită pe câteva tichete, cu tag distinct** (ca să identifici lotul în UI):

```bash
uv run cs_auto_draft.py --only 273383,274159 --create-draft --tag ai-draft-v3
```

**Audit de limbă și gramatică pe ce a ieșit:**

```bash
uv run cs_auto_draft.py --limit 20 --json 2>/dev/null \
  | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/cs_drafts.json
uv run grammar_audit.py --file /tmp/cs_drafts.json
```

**Aplicarea unei acțiuni propuse** (doar de pe stație, nu de pe VPS):

```bash
uv run cs_auto_draft.py --approve 273812 --agent Oana
```

**Combinația de evitat:**

```bash
# NU: re-rulare pe aceleași tichete fără --skip-tagged
uv run cs_auto_draft.py --channel email --create-draft --limit 500
```

`create_draft` **adaugă**, nu suprascrie, și **nu există API de ștergere a drafturilor** — curățarea e manuală în UI. `--skip-tagged` e singura protecție. În jurnal, 28.207 tichete au fost sărite exact așa.

### 3. Prompturile — unde sunt, ce impun, regulile anti-halucinație

Patru prompturi de sistem, plus două texte injectate condiționat.

| Prompt | Linii | Folosit la |
|---|---|---|
| `IDENTIFY_SYS` | 391-416 | triajul LLM (pasul 3) |
| `SYSTEM` | 419-444 | generarea draftului normal |
| `HOLDING` | 446 | draftul de așteptare la escaladare |
| `VISION_SYS` | 325-327 | descrierea pozelor (fallback local) |
| prompt corectiv anti-halucinație | 1158-1160 | a doua încercare, în post-filtru |
| șabloane sigure | 1169, 1171 | a treia încercare eșuată |

---

## `IDENTIFY_SYS` — triajul (391-416)

Cere **strict JSON** cu 16 câmpuri (394-408):

```
{"problem", "category", "language", "severity", "escalate", "escalation_reason",
 "suggested_action", "action", "order", "new_address", "new_city", "new_zip",
 "new_phone", "items", "product", "comment_action", "spam", "confidence", "missing"}
```

Cele 14 categorii posibile: `livrare_wismo | retur | schimb_swap | anulare | modificare_comanda | problema_produs | refuz_livrare | plata_factura | presale_intrebare | comanda_noua | recenzie_feedback | comentariu_social | spam_automat | altele`.

**Regula de ultim mesaj** (392) — fixul care a rezolvat bug-ul #273383:

> „răspundem la ULTIMUL mesaj al clientului (marcat cu «>>> ULTIMUL MESAJ AL CLIENTULUI» în conversație). Firul de dinainte = DOAR context. Dacă ultimul mesaj e mulțumire / feedback pozitiv / «a ajuns» / «sunt foarte bune», atunci `category`=recenzie_feedback și NU mai e WISMO/problemă — chiar dacă firul a ÎNCEPUT cu o întrebare de livrare.”

**Regula de sarcasm** (393):

> „un comentariu aparent neutru/pozitiv dar critic (ex. persistență mică «au persistat 4 ore 😅» la un parfum reclamat 12h, «super... 🙄», emoji 😅😂🙄 + reproș) NU e `recenzie_feedback` — e NEMULȚUMIRE”

**Calibrarea escaladării** (410) — cea mai lungă regulă din tot fișierul. Esența:

> „o reclamație de produs OBIȘNUITĂ (nu funcționează bine, nu e ca în reclamă, defect minor, întrebare de calitate, «cârpa e proastă», SAU **parfum/produs spart/deteriorat la livrare** — se rezolvă prin retrimitere gratuită, procedură standard) FĂRĂ furie explicită … și FĂRĂ ANPC/juridic = `problema_produs`/`schimb_swap` rezolvată DIRECT, NU HIGH.”

și

> „o simplă întrebare de status (WISMO) politicoasă NU se escaladează — chiar dacă clientul are nr. comandă, multe comenzi sau istoric de tichete (volumul/«a mai scris de N ori» în istoric NU e, singur, motiv de escaladare).”

A doua propoziție e fixul direct pentru tiparul „escaladare prea agresivă” găsit la revizia din 23-iun.

**Anti-halucinație în triaj**, ultima frază (416):

> „Dacă nu e clar ce comandă sau lipsesc date → action="none" + missing. NU inventa nimic. Răspunde DOAR JSON.”

---

## `SYSTEM` — draftul (419-444)

Începe cu identitatea: „Scrii ca un agent REAL (Cristina/Diana/Irina/Martina/Alexandra) — cald, politicos, natural, cu diacritice, fără limbaj robotic.”

### Regula #1, anti-halucinație (420-426)

E marcată explicit ca fiind deasupra tuturor:

> „⛔ ANTI-HALUCINARE — REGULA #1, MAI PRESUS DE ORICE: NU ai făcut niciun lookup live. Folosește DOAR informația care apare EXPLICIT în context … Dacă o informație NU e în context, NU o INVENTA și NU pretinde că o știi/ai verificat-o.”

Cinci interdicții numite (421-425):

1. „să spui «am verificat / am căutat / am găsit / NU am găsit comanda / nu există nicio comandă» — NU cauți tu, nu ai cum să știi”
2. „să afirmi un STATUS de comandă/livrare … un AWB, o dată sau un termen de livrare în zile — dacă nu e în context”
3. „să INVENTEZI specificații de produs: dimensiuni (cm), preț (lei), culoare, material, disponibilitate/stoc”
4. „să INVENTEZI un număr de telefon — folosește DOAR `TELEFON_COMANDĂ` dacă apare în context”
5. „să confirmi capabilități nesigure (ex. livrare internațională) fără bază”

Și alternativa impusă (426): „cere-i POLITICOS clientului ce-ți lipsește — **numărul comenzii SAU un număr de telefon** … sau spune ONEST «verificăm și revenim cât mai curând»”.

### Excepția pentru poze (427)

> „📷 POZE CLIENT — EXCEPȚIE de la anti-halucinare: dacă în context apare secțiunea «POZE TRIMISE DE CLIENT (conținutul REAL al imaginilor…)», acela e conținutul pozelor pe care le-am VĂZUT efectiv → e informație REALĂ, folosește-o ca DOVADĂ. … NU cere clientului o poză dacă deja a trimis una.”

Excepția e implementată și în cod, nu doar în prompt: `not photo_blk` în condiția de la 1157.

### Procedurile de business (430-437)

| Categorie | Ce impune promptul |
|---|---|
| WISMO | status real **doar** dacă ai AWB+curier confirmat, cu linkul potrivit curierului (DPD / Sameday / Packeta / Econt, toate patru în prompt). Fără comandă → cere nr. comandă sau telefon, **nu** promite termen |
| Retur | „ARONA e COD și NU încurajează returul” → întreabă motivul, oferă alternativă; dacă insistă și e eligibil → `https://bi.grandia.ro/returns?order=<nr>&email=<email>` + „maximum 14 zile”. Parfum/igienă **desigilat** → refuz politicos |
| Produs spart (parfum) | **NU refund** → retrimitere gratuită + parfum cadou |
| Defect casă | cere poză (dacă nu e deja descrisă); pe stoc → retrimitere/schimb; altfel retur+refund |
| Pre-vânzare | răspuns cald care **confirmă și încurajează** comanda; spune cum comandă. „NU deflecta seac cu «dacă aveți întrebări scrieți-ne»” |
| Descriere produs | pe categorie: parfumuri → miros/persistență/preț, **nu** „aspect plăcut”; genți → piele ecologică; casă → calitate/utilitate |

### Onestitatea despre produse (437)

> „multe produse ARONA sunt REPLICI/imitații, NU originale. Parfumurile sunt INSPIRATE din branduri cunoscute … Genți/accesorii «din piele» sunt de regulă PIELE ECOLOGICĂ … La întrebări de tip «e original?» → răspunde ONEST și pozitiv”

### Regulile de comentariu public (438-439)

Cele mai dese corecții s-au acumulat aici:

- **fără salut de deschidere**: „NU începe cu «Bună ziua!» / «Bună!» / «Salut» … Salut + semnătură DOAR pe email”
- **fără numele clientului** (442): „Pe COMENTARII PUBLICE (FB/IG) NU folosi numele clientului (nici prenume, nici nume de familie, ex «doamnă Nechita») — e expunere de date personale într-un spațiu public”
- **nu deflecta întrebările simple** (439): „«câte bucăți la X lei?» → spune oferta; «dați-mi numărul de telefon (ca să comand)» → DĂ numărul TELEFON_COMANDĂ direct (NU «e pe site»)”. „«Scrieți-ne în privat» se folosește DOAR când e nevoie de date personale”
- **nu re-oferi canalul reclamat** (443): „dacă clientul spune explicit că un canal NU funcționează (ex. «sun de zile și nu răspunde nimeni») → NU-l trimite înapoi la acel canal”
- **nu pretinde DM**: „NU spune «v-am scris în privat» / «ți-am trimis detalii» — NOI nu trimitem DM; clientul ne contactează”

### Regula de acțiune (440) — guardrail-ul cheie

> „REGULA DE ACȚIUNE: dacă în context apare `ACTIUNE_APLICATA: …` → confirmă acțiunea ca FĂCUTĂ. Dacă NU → nu spune niciodată că ai modificat/anulat ceva; confirmă că ai PRELUAT solicitarea sau cere datele lipsă.”

Marcajul `ACTIUNE_APLICATA` se injectează într-un **singur** loc în tot fișierul: linia **783**, în `do_approve()`, și **numai după** ce acțiunea a trecut testul de succes.

### Semnătura (444)

> „Email → salut + semnătură «Cu drag, Echipa \<Magazin\>»; dacă magazinul e necunoscut/generic, semnează «Cu drag, echipa noastră» (NU «Echipa magazinul nostru»).”

---

## `HOLDING` — escaladarea (446)

Un singur paragraf. Impune: mesaj **scurt de așteptare**, în limba clientului, care confirmă preluarea sesizării și spune că revine un coleg. „NU promite soluții concrete, NU da detalii de comandă pe canal public.” Aceleași reguli de registru și semnătură ca `SYSTEM`. Pe comentariu public: 1-2 fraze + invitație în privat, fără salut de deschidere.

---

## `VISION_SYS` — pozele (325-327)

> „Descrie pe SCURT (1-2 fraze, factual, în română) ce arată poza trimisă de client: produs defect/spart/deteriorat (zi exact ce e rupt/lipsă/greșit), dovadă de livrare (AWB, SMS/email curier + ce status), etichetă/colet, captură de ecran … Dacă e relevant pentru o reclamație, spune clar ce DOVEDEȘTE. **Fără speculații.**”

E folosit de `describe_photos` (fallback local). În producție, pozele trec prin `cs_photo.py`, care are prompturile lui.

---

## Promptul corectiv (1158-1160)

Se atașează la sfârșitul contextului la a doua încercare:

> „⛔ Răspunsul tău anterior CONȚINEA INFORMAȚIE INVENTATĂ (lookup/«am verificat/nu am găsit», status comandă, preț, dimensiune sau telefon pe care NU le ai în context). Rescrie complet, FĂRĂ să inventezi NIMIC și FĂRĂ să spui că ai căutat/găsit/verificat ceva.”

---

## Playbook-ul învățat — injectat condiționat (1135-1136)

```python
learned = LEARNED.get(cat, "")
learned_blk = ("\nPROCEDURA INVATATA + VOCEA AGENTILOR REALI pt '%s' "
               "(urmeaza procedura; imita tonul/structura replicilor; "
               "NU copia datele din exemple):\n%s\n" % (cat, learned[:1800])) if learned else ""
```

`LEARNED` se încarcă o dată, la import (linia 636), din `.learned_playbook.md` prin `load_playbook()` (615-634), care parsează pe titluri `## CATEGORIE`.

> ⚠️ **Pe VPS fișierul nu există.** Am verificat direct. Deci `LEARNED = {}`, `learned_blk` e mereu `""` și blocul nu a intrat niciodată într-un prompt din producție. În git există (17.884 octeți, 9 categorii: LIVRARE_WISMO, RETUR, ANULARE, PROBLEMA_PRODUS, MODIFICARE_COMANDA, SCHIMB_SWAP, PRESALE_INTREBARE, PLATA_FACTURA, REFUZ_LIVRARE), dar e în `.gitignore` — se regenerează cu `gigi:cs-procedures`.

## Model și caching

`llm()` (295-314) alege în ordine:

1. `ANTHROPIC_API_KEY` → Claude, model `ANTHROPIC_MODEL` (implicit `claude-sonnet-4-6`), `max_tokens=900`. Promptul de sistem e marcat `cache_control: ephemeral` (301) — e identic pe toate tichetele, deci după primul apel se taxează la 0,1×. Comentariul de la 299 notează pragul: Sonnet 2048 tokeni (SYSTEM ~2,6k prinde), Haiku 4096 (nu prinde).
2. altfel `OPENAI_API_KEY` → model `DRAFT_MODEL` (implicit `gpt-4o`, cronul setează `gpt-4o-mini`), `temperature=0.2`, cu `response_format: json_object` când `js=True`.
3. altfel `SystemExit`.

> ⚠️ `js=True` nu are efect pe Claude — de asta triajul are nevoie de parsarea tolerantă de la 1039 și de coerciile defensive (`_f()`, `str(cat)`).

### 4. Toate gărzile, și ce bug a reparat fiecare

Douăzeci de mecanisme de protecție. Le iau în ordinea gravității bug-ului reparat.

---

### 1. Guardrail `ACTIUNE_APLICATA` — regula de acțiune

**Unde:** `SYSTEM` linia **440** (regula), `do_approve` linia **783** (singura injecție), comentariul explicativ la **1114-1115**.

**Bug-ul:** drafturile spuneau „am actualizat adresa dumneavoastră” deși nu se executase nimic — propunerea era doar propunere. Clientul primea o confirmare falsă.

**Cum e reparat:** marcajul `ACTIUNE_APLICATA: <descriere> la comanda <nr>` se injectează într-un singur punct din tot fișierul, la linia 783, **după** ce acțiunea a trecut testul de succes. În pasul de propunere (1114) e explicit notat că nu se injectează. Fără marcaj, promptul impune formularea „am preluat solicitarea”.

---

### 2. Post-filtrul determinist `HALLU`

**Unde:** regexul la **189-196**, helperul `has_order_data()` la **197-200**, aplicarea la **1156-1172**.

**Bug-ul:** auditul adversarial pe 75 de drafturi reale (29-iun) a găsit **71% cu probleme, 18 grave**. În modul `--lean` modelul n-avea date și inventa: dimensiuni, prețuri, numere de telefon, statusuri de comandă, plus fraze de tip „am verificat și nu am găsit comanda” — când nu căutase nimic.

**Ce prinde regexul:**

```python
HALLU = re.compile(
    r"am verificat|am c[ăa]utat|nu am g[ăa]sit|n-?am g[ăa]sit|nu (am )?identificat|am identificat comanda|"
    r"nu exist[ăa] (nicio|o) comand|comanda (dumneavoastr[ăa]|nr|#)?\s*[A-Z]{2,5}\d+ (este|a fost|nu)|"
    r"este în procesare|a fost predat|nu a fost predat[ăa]|urmeaz[ăa] s[ăa] fie preluat|"
    r"livrare[a]? (se face |va fi |în )?\b\d+\s?(-\s?\d+\s?)?zile|în \d+ zile lucr|"
    r"\b\d{1,3}\s?x\s?\d{1,3}\b|\b\d{2,3}\s?cm\b|"
    r"\b\d{2,4}\s*(de\s+)?(lei|ron)\b", re.I)
```

Șase familii: pretenția de lookup, afirmarea statusului, termene de livrare în zile, dimensiuni `NxM`, dimensiuni în cm, orice preț în lei.

**Trei trepte:** regenerare corectivă (1 dată) → dacă tot fabrică, șablon sigur pe categorie → engine marcat `șablon-sigur`.

**Când NU se aplică** (1157): dacă avem comenzi (`has_order_data`), dacă am văzut o poză (`photo_blk`), sau dacă reclama a adus preț din catalog (`_ad_has_catalog`). Altfel filtrul ar cenzura cifre legitime.

> Măsurat: în jurnal, **0** corectări și **0** șabloane sigure. Nu s-a declanșat niciodată — pentru că rulările de după implementare au fost sufocate de 429-uri, iar filtrul cere `not draft.startswith("(eroare")`.

---

### 3. Garda anti-gunoi pe `(eroare LLM`

**Unde:** **1212-1214**.

```python
elif draft.strip().startswith("(eroare") or "(eroare LLM" in draft or len(draft.strip()) < 5:
    print("  ⛔ draft invalid (eroare LLM / gol) → NU salvez (sar tichetul).")
```

**Bug-ul:** la paralelizarea pe 8 workeri (24-iun), 16 cereri OpenAI simultane au produs 429-uri în lanț. Retry-ul se epuiza, `llm()` arunca, iar linia 1154 punea în `draft` literalmente `"(eroare LLM: HTTP Error 429…)"` — și asta **se scria în Richpanel** ca draft. ~13 drafturi-gunoi scrise, imposibil de șters prin API.

**Cum e reparat:** tichetul se **sare**, nu se scrie. La rularea următoare e reluat (tag-ul `ai-draft` nu s-a pus, deci `--skip-tagged` nu-l ocolește).

> Măsurat: **6.015** drafturi invalide oprite, **0** apeluri `create_draft` eșuate. Garda a funcționat perfect. Doar în ultima rulare (2-iul), toate cele 464 de tichete au picat pe 429.

---

### 4. `fulfillment_state` fail-safe

**Unde:** **487-496**, aplicat la **1101-1104**.

```python
PRE_STATES = {"netrimisa", "comanda noua", "noua", "plasata", "in asteptare", "draft",
              "unfulfilled", "open", "neexpediat", "de expediat", "nefinalizata"}
def fulfillment_state(order):
    awb = (order or {}).get("awb") or ""
    deliv = deacc((order or {}).get("deliv") or "").strip()
    if not awb and deliv in PRE_STATES:
        return "pre"
    return "post"
```

**Bug-ul:** un status necunoscut, gol sau `"?"` era tratat ca „probabil neexpediat” → sistemul ar fi modificat sau anulat un colet care plecase deja.

**Cum e reparat:** `pre` **doar** dacă e dovedit — fără AWB **și** status explicit în lista de 11 stări. Orice altceva, inclusiv necunoscutul, întoarce `post` și blochează `modify`/`cancel`. Fail-safe în direcția sigură.

---

### 5. `resolve_target_order`

**Unde:** **460-484**, apelat la **1084**.

**Bug-ul:** dacă clientul scria un număr de comandă care nu se potrivea cu niciuna dintre comenzile lui, dar avea exact o comandă în cont, sistemul **substituia tăcut** acea comandă și acționa pe ea.

**Cum e reparat** — patru verdicte, unul singur permite acțiune:

| Situație | Verdict | Acțiune |
|---|---|---|
| exact o comandă referită și confirmată | `(obj, name, False, "comandă referită explicit")` | **permisă** |
| mai multe comenzi referite | `(None, "", True, "clientul a referit mai multe comenzi")` | blocată |
| referință care nu se potrivește | `(None, "", True, "o singură comandă dar referința nu se potrivește")` | **blocată** — fixul |
| nicio referință, dar o singură comandă în cont | `(obj, name, False, "o singură comandă în cont")` | permisă |
| nicio referință, mai multe comenzi | `(None, "", True, "mai multe comenzi, niciuna referită clar")` | blocată |

La blocare (1095-1096), draftul cere clientului să confirme comanda.

> Măsurat: **422** cazuri de „Comandă neclară” în jurnal. Nicio acțiune propusă (0 `PROPUNERE MODIFY/CANCEL/SWAP/RESEND`).

---

### 6. Redactarea PII pe canale publice — structurală

**Unde:** **1131-1134**.

```python
od_ctx    = od if not is_public else "(comenzi ascunse — canal public, NU expune date personale)"
phone_ctx = (phone or "—") if not is_public else "—"
email_ctx = (email or "—") if not is_public else "—"
```

**Bug-ul:** protecția era doar în prompt („nu scrie date personale”). Un model care greșea putea scrie numărul comenzii sau telefonul într-un comentariu **public** pe Facebook.

**Cum e reparat:** datele **nu ajung** în promptul de draft pe canale publice. Modelul nu are ce scurge. Completat de regula 442 din `SYSTEM`, care interzice până și prenumele pe comentarii.

> Notă: promptul de **triaj** primește `od` ne-redactat (1031). Corect — triajul e intern și are nevoie de context ca să decidă.

---

### 7. Draft-only ca implicit

**Unde:** toată scrierea e sub `if a.create_draft and cid:` (**1199**). Trimiterea live are trei căi separate, toate opt-in: `--send` (mod dedicat, 803-830), `--apply-send` (1215) și metoda MCP `send_message`.

**Contextul:** `send_message` **există** pe serverul MCP Richpanel (21 de unelte în `tools/list`), dar e scos din registrul de unelte al Claude ca să forțeze draft-only. Scriptul îl poate chema prin JSON-RPC direct. Decizia echipei (23-iun-2026) rămâne: **draft-only**.

`do_send` (803-830) refuză în trei cazuri: tichet deja trimis (810-811), escaladare (812-813), hide propus (814-815).

---

### 8. Filtrarea deterministă de spam

**Unde:** 5 regexuri + un detector de padding la **76-98**, aplicate la **1047-1071**.

**Bug-ul:** `gpt-4o-mini` ratează spam pe care `gpt-4o` îl prinde. Filtrarea care depinde doar de LLM e instabilă la schimbarea modelului.

**Cum e reparat:** cinci semnale care nu depind deloc de LLM. Un email de la `backline-tichet@dpd.ro` sau o notificare judge.me se exclud indiferent ce spune modelul.

> Măsurat pe jurnal: 5.151 excluderi, dintre care **5.147 de la LLM** și doar 4 de la filtrele deterministe (2 judge.me, 1 SaaS, 1 padding, 0 non-client, 0 bounce). Filtrele deterministe sunt o plasă de siguranță, nu motorul principal.

---

### 9. Gate-ul anti-supra-escaladare

**Unde:** `ANGER_RE` (101), `_real_escalation()` (104-110), decizia la **1073-1081**.

**Bug-ul:** `gpt-4o-mini` escalada aproape orice reclamație. Un WISMO politicos cu număr de comandă ajungea prioritate HIGH, cu notă internă — zgomot pentru CS în loc de răspuns.

**Cum e reparat:** pe cele 11 categorii obișnuite, verdictul LLM e ignorat; contează doar semnalul determinist:

```python
def _real_escalation(text):
    dt = deacc(text or "")
    if ESCAL.search(dt) or ANGER_RE.search(dt):
        return True
    letters = [c for c in (text or "") if c.isalpha()]
    return len(letters) >= 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7
```

Trei declanșatoare: `ESCAL` (anpc, protectia consumator, dau in judecata, instanta, avocat, denunt, reclamatie, chargeback, politie), `ANGER_RE` (escroc, hoti, teapa, inselat, bataie de joc, rusine, jignit, inadmisibil, nesimtit, incompetent, scandalos, dezgustator, sau `!!!`), sau peste 70% majuscule.

---

### 10. Poarta de succes din `do_approve`

**Unde:** **776-778**.

```python
if rc != 0 or "✅" not in out or "⚠" in out:
    print("⚠️ Acțiunea a EȘUAT / e incompletă — NU salvez draftul de confirmare (nu mint clientul).")
    return
```

**Bug-ul:** succesul se ghicea căutând cuvântul „error”/„eroare” în output. Un eșec parțial trecea drept succes și draftul confirma o acțiune neexecutată.

**Cum e reparat:** trei condiții simultan — cod de ieșire 0, marcajul ✅ prezent, **și** niciun ⚠. `run_cs_action` (657-668) întoarce explicit `(returncode, output)`.

---

### 11. Consumarea intrării după aplicare

**Unde:** **768-769** (verificarea) și **798-800** (marcarea).

**Bug-ul:** un al doilea `--approve` pe același tichet **reaplica** acțiunea — o retrimitere sau un swap dublu, marfă plecată de două ori.

**Cum e reparat:** după aplicare reușită, `cmd` și `hide` se golesc și se pune `applied: True`. Un al doilea apel iese cu „a fost deja aplicat”. Același tipar pentru `--send`, cu flag-ul `sent` (810-811).

---

### 12. `hide` nu salvează draft

**Unde:** **790-792** (în `do_approve`) și **1209-1211** (în buclă).

**Bug-ul:** la o propunere de `hide` se salva și un draft de răspuns. Comentariul urma să fie ascuns — un răspuns public la un comentariu ascuns e un foot-gun.

---

### 13. `tag_id()` — nume → UUID

**Unde:** **579-599**.

**Bug-ul:** `add_tags_to_conversation` din MCP acceptă **doar UUID-uri de tag**, nu nume, și **nu creează** tag-uri noi. Pasarea de nume eșua **tăcut** — tag-ul pur și simplu nu se atașa. Afecta și rutarea escaladărilor și, mai grav, `--skip-tagged`: fără tag, tichetele se re-draftau la fiecare rulare.

**Cum e reparat:** `tag_id()` caută prin `list_tags`, creează prin `create_tag` dacă nu găsește, și memorează în `_TAG_CACHE`. `add_tags()` (595-599) rezolvă lista de nume în ID-uri.

Tag-uri create în workspace: `ai-draft`, `ai-live`, `ai-sent`, `escaladare`, `esc-high`, `esc-urgent`, `de-sunat`, `spam`.

> Numele nu conțin `:` — `esc:high` a fost redenumit `esc-high`.

---

### 14. `_f()` — float defensiv

**Unde:** **150-155**, folosit la **1105**.

**Bug-ul:** dacă LLM-ul întorcea `confidence` ca string non-numeric, `float()` arunca și **tot lotul** crăpa, nu doar tichetul.

---

### 15. `secret()` rezistent la lipsa lui `uv`

**Unde:** **225-238**.

**Bug-ul — cel mai costisitor din istoric:** `secret()` făcea `subprocess.run(["uv", "run", KB, …])` pentru `ANTHROPIC_API_KEY`. Pe cron, `uv` nu e în PATH → excepție → **fiecare apel LLM cădea**. Toate drafturile din acea perioadă au ieșit `(eroare LLM ...'uv')`.

**Cum e reparat:** env-ul se citește primul; `subprocess` e într-un `try/except` care întoarce `""` în loc să arunce; rezultatul se memorează în `_SECRET_CACHE`. Comentariul din cod (236) e explicit: „uv negăsit / KB inaccesibil → gol, NU excepție (altfel llm() iese «(eroare LLM ...'uv')»)”.

---

### 16. Retry cu backoff — MCP și LLM

**Unde:** `MCP._post` **245-266**, `_llm_http` **276-293**.

Ambele: 6 încercări, pe 429/500/502/503/504 **și** pe `URLError`/`TimeoutError`/`ConnectionError`. Respectă `Retry-After` când e numeric, altfel backoff exponențial (plafon 60s la MCP, 90s la LLM).

---

### 17. Paginarea fără `has_more`

**Unde:** **901-902**. API-ul Richpanel nu setează `has_more` fiabil — codul paginează cât timp apar tichete **noi** (`new_this_page`), cu dedup pe `seen`.

---

### 18. Idempotența prin `--skip-tagged`

**Unde:** **964-965**. Singura protecție împotriva stivuirii, pentru că `create_draft` **adaugă** și nu există API de ștergere.

> Măsurat: **28.207** tichete sărite astfel.

---

### 19. Excepțiile de la anti-halucinație — poze și catalog

**Unde:** condiția de la **1157** (`not photo_blk and not _ad_has_catalog`), setarea la **980**.

O poză văzută efectiv sau un preț din catalogul metrics sunt **date reale**. Fără excepții, filtrul ar fi cenzurat răspunsuri corecte.

---

### 20. `fb_page_token` nu minte callerul

**Unde:** **671-692**. Iterează șase chei de token și întoarce un token de pagină **doar dacă Graph confirmă accesul**; altfel `None`, memorat în cache. Un token care nu vede pagina nu e raportat ca succes.

### 5. Limba și registrul — precedența exactă

## Limba

O singură linie decide, **1138**:

```python
lang = detect_lang(blob + " " + tr) or STORE_LANG.get(store_name) or idn.get("language") or "ro"
```

Precedența, în ordine strictă:

### 1. `detect_lang()` — scriptul și diacriticele (157-170)

Cel mai tare semnal, pentru că e determinist:

| Semnal | Limba |
|---|---|
| orice caracter chirilic (`[Ѐ-ӿ]`) | `bg` |
| `ł ą ę ż ś ć ź ń` | `pl` |
| `ř ů ě` | `cz` |
| `ă â î ș ț ş ţ` | `ro` |
| doar ASCII | **`None`** → trece mai departe |

Ordinea din funcție contează: chirilic → polonez → ceh → român. Un text ASCII curat (engleză, sau română fără diacritice) nu decide nimic aici.

### 2. `STORE_LANG` — piața brandului (112)

```python
STORE_LANG = {"Bonhaus CZ": "cz", "Bonhaus PL": "pl", "Bonhaus BG": "bg"}
```

Trei intrări. Se aplică doar dacă `store_name` a fost rezolvat (din `PAGE_STORE[to.id]`, din domeniul emailului, sau din brandul primei comenzi).

### 3. `idn["language"]` — verdictul LLM

Câmpul din triaj. Ultimul semnal înainte de implicit.

### 4. `"ro"` — implicitul

---

### De ce în ordinea asta

Motivul e scris în memoria de proiect: **un client pe un brand CZ poate scrie în engleză**. Dacă brandul ar fi avut prioritate, i-ai fi răspuns în cehă. De asta detecția pe textul real bate piața brandului.

Instrucțiunea către model dublează asta în context (1146):

> „SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL în conversația de mai sus (orientativ: limba≈%s; ro/cz/pl/bg/en). Brandurile pe Cehia/Polonia/Bulgaria (Bonhaus CZ/PL/BG) răspund de regulă în limba pieței, **DAR dacă clientul a scris clar în altă limbă (ex. engleză), răspunde în limba LUI**. Exemplele de procedură/voce pot fi în română — folosește-le DOAR pentru pași+ton, NU pentru limbă.”

Ultima frază e importantă: playbook-ul învățat e în română, iar fără avertisment modelul îl imita și lingvistic.

### Riscul cunoscut

`detect_lang` primește `blob + " " + tr` — adică subiectul plus **tot firul**, inclusiv replicile agenților și textul reclamei. Un agent român care a scris cu diacritice într-un fir cehesc ar putea vira detecția pe `ro`. Nu am un caz măsurat; îl trec la incertitudini.

### Verificarea

`grammar_audit.py` e multilingv și compară limba **răspunsului** cu `cust_msg` (mesajul real al clientului), raportând `limba_gresita` la nepotrivire. Promptul lui (linia 30):

> „Dacă răspunsul e în altă limbă decât a scris clientul (ex. răspuns în ROMÂNĂ la un mesaj în cehă/poloneză/bulgară/engleză) → eroare GRAVĂ, tip «limba_gresita».”

> Capcană documentată: auditul a dat odată 4 fals-pozitive citind cuvinte din `cust_msg` în loc de `draft`. Dacă vezi ceva ciudat, verifică întâi asta.

---

## Registrul

**Un singur registru, pe toate canalele: FORMAL, la plural.** Nu depinde de canal, de categorie sau de sentiment.

`SYSTEM` linia 429:

> „REGISTRU (important): FORMAL, la PLURAL, pe TOATE canalele (email, DM, chat, comentariu) — în română «dumneavoastră/vă/-ți» (NICIODATĂ «tu/ție/te/-i»); în alte limbi registrul politicos echivalent. Așa scriu agenții ARONA reali («Vă rugăm», «Vă informăm», «Vă mulțumim»).”

Întărit în trei locuri:

- `HOLDING` (446): aceeași formulare pentru mesajele de așteptare;
- regulile de platformă din `PLATFORM` (57-58), pentru comentarii: „POLITICOS la PLURAL (dumneavoastra/va, NU la 'tu')”;
- regula generală (444): „gramatică corectă («ți-am scris/v-am scris», nu «te-am scris»)”.

**De ce:** auditul de gramatică din iunie a găsit că răspunsurile ieșeau la „tu” — pe toate canalele, nu doar pe comentarii. Agenții ARONA reali scriu formal. Fixul a fost global, în `SYSTEM` și `HOLDING` simultan.

### Ce variază, totuși, cu canalul

Registrul nu, dar **forma** da — prin `PLATFORM` (51-59) și prin reguli dedicate:

| Canal | Formă |
|---|---|
| Email | răspuns complet, salut + semnătură „Cu drag, Echipa \<Magazin\>”; date de comandă OK |
| Chat / Messenger / IG DM | conversațional, scurt; date de comandă OK |
| SMS | foarte scurt, fără semnătură lungă |
| Comentariu FB/IG | **1-2 fraze**, 1-2 emoji, **fără salut de deschidere**, **fără numele clientului**, fără date personale |

Regula de salut, din `SYSTEM` (438):

> „REGULĂ CS FERMĂ pt TOATE răspunsurile la comentarii: NU începe cu «Bună ziua!» / «Bună!» / «Salut» / niciun salut de deschidere — intră DIRECT în mesaj. … Salut + semnătură DOAR pe email, niciodată pe comentarii.”

### Adresarea pe nume (442)

- **canale private**: doar prenumele, dacă e curat. Dacă numele pare concatenat (majusculă în interior, fără spațiu — exemplul din cod e „GheorghesiGerda”) → adresare neutră;
- **comentarii publice**: **niciun** nume, nici prenume, nici de familie. E expunere de date personale într-un spațiu public.

### Calibrarea pe sentiment (441)

> „negativ → scuze sincere + asumare + soluție; pozitiv → cald; neutru → la obiect.”

Sentimentul vine din euristica de la 216-223 (31 de cuvinte negative, 16 pozitive, semne de exclamare, majuscule), nu din LLM. Produce o etichetă (`negativ`/`pozitiv`/`neutru`) și o intensitate (`puternic`/`mediu`/`slab`).

### 6. Grounding-ul (--ground) — ce baze, ce chei, ce se întâmplă la ratare

Funcția e `lookup_orders()`, liniile **512-574**. E singura cale de date reale care funcționează din cron.

## De ce există

Calea „clasică”, `customer_ident()` (499-504), rulează `uv run customer_identity.py --conv N --json` ca subproces, iar acela face **SSH către VPS-ul de profitabilitate**. Din cron nu merge: `uv` nu e în PATH și autentificarea SSH nu e disponibilă. `--ground` face aceleași lookup-uri **în proces**, prin `pg8000` și `sqlite3`.

## Condiția de activare (linia 998)

```python
if a.ground and (email or phone or ORDER_RE.search(_gtxt) or AWB_RE.search(_gtxt)):
```

unde `_gtxt = subj + " " + tr` (linia 997) — **subiectul plus tot firul, inclusiv citatele**. Comentariul din cod e explicit: „caută comanda/AWB în SUBIECT + TOT firul (inclusiv citate), nu doar ultimul mesaj”. Asta prinde numărul comenzii din semnătura unui email citat, sau AWB-ul dintr-un mesaj mai vechi.

Dacă niciunul dintre cele patru semnale nu există, **nu se face niciun lookup**.

## Extragerea cheilor (999-1001)

```python
_onames = ["".join(m.group(0).split()).replace("-", "").upper() for m in ORDER_RE.finditer(_gtxt)]
_awbs   = AWB_RE.findall(_gtxt)
orders  = lookup_orders(email, phone, _onames, _awbs)
```

**`ORDER_RE`** (linia 72) — 18 prefixe de magazin urmate de 4-7 cifre, cu spațiu sau cratimă opțională:

```python
ORDER_RE = re.compile(r"\b(EST|GT|NUB|GRAND|GRAN|MAG|OFER|RED|BONBG|BON|CZ|PL|BELA|GEN|CARP|COV|APR|ROSSI)[ -]?(\d{4,7})\b", re.I)
```

Normalizarea scoate spațiile și cratimele și pune totul cu majuscule: `EST 000001` și `est-193486` devin ambele `EST000001`.

**`AWB_RE`** (linia 73) — `\b\d{10,16}\b`. Orice grup de 10-16 cifre. Deliberat lat: acoperă Sameday (13), DPD, Econt, Packeta.

## Baza 1 — `metrics`, tabelul `orders` (521-547)

Conexiune `pg8000.dbapi` cu `ssl_context=True`, din `DATABASE_URL_METRICS` (parsat cu `urlparse` + `unquote` pe user și parolă, ca să suporte caractere speciale).

Coloanele citite: `name, "totalPrice", "financialStatus", "shopifyCreatedAt"`.

Trei interogări, care se **cumulează** (nu se exclud):

| Cheie | SQL | Limită |
|---|---|---|
| email | `WHERE lower(email) = lower(%s) ORDER BY "shopifyCreatedAt" DESC` | 12 |
| telefon | `WHERE phone LIKE %s OR "shippingPhone" LIKE %s` cu pattern `"%" + ultimele_9_cifre` | 12 |
| nr. comandă | `WHERE name IN (…)` | — |

**Telefonul trece prin `norm_phone()`** (507-509), care păstrează doar cifrele și ia **ultimele 9**. Asta rezolvă problema clasică din CS: `0748123456`, `40748123456` și `+40 748 123 456` se potrivesc toate. Regula e aceeași ca în `gigi:cs-360`.

Rezultatul se indexează pe numele comenzii în `byname` (543-545), cu `deliv="?"`, `awb=""`, `courier=""` — urmează să fie îmbogățite.

## Baza 2 — `profitability.db`, tabelul `profit_orders` (548-573)

SQLite **local pe VPS**, calea din `PROFIT_DB` (511), implicit `/root/Scripturi/data/profitability.db`.

```sql
SELECT order_name, status_category, skus, awb, courier_key FROM profit_orders WHERE …
```

Două interogări, cu roluri diferite:

**A. Îmbogățire, pe nume** (562-566) — pentru comenzile deja găsite în metrics, aduce statusul real de livrare, SKU-urile, AWB-ul și curierul. `_apply(r)` fără `create`.

**B. Căutare după AWB** (567-570) — `WHERE awb IN (…)`, cu `_apply(r, create=True)`. Asta **creează** o intrare nouă. E cazul WISMO în care clientul dă doar AWB-ul, fără email cunoscut și fără număr de comandă. Validat pe VPS: AWB `0000000000001` → `BONBG00001`, status „Livrata”, curier econt.

**Dimensiunea bazei, măsurată azi (15-sep-2026):** 843.459 de comenzi, 804.057 cu AWB (95,3%), 722 MB. E semnificativ mai mare decât cifrele din memorie (294k / 278k în iunie).

Coloanele disponibile în `profit_orders`: `id, month, prefix, shop, order_name, created_at, revenue, currency, cogs, cogs_missing, cogs_missing_skus, payment_status, fulfillment_status, awb, courier_key, courier_status, status_category, tags, skus, shopify_delivery_status`.

## Ce intră în context

Formatarea, liniile 1020-1023, maximum 6 comenzi:

```
    • EST000001 (EST): status=Livrata, curier=DPD, AWB=0000000000001, produse=EST-0042,EST-0091
```

Curierul trece prin tabelul de traducere `COURIER` (linia 873): `dpd-ro`/`dpd` → DPD, `sameday` → Sameday, `packeta` → Packeta, `econt` → Econt.

Brandul se poate deduce din prima comandă, dacă nu s-a rezolvat altfel (1003-1004).

## Când nu găsește

Trei consecințe, în lanț:

1. `od` devine literalmente `"    (nicio comandă găsită)"` (1023);
2. `elsewhere` devine `"grounded — nicio comandă găsită în DB"` (1005);
3. `has_order_data(od_ctx)` întoarce **False** (197-200, caută subșirul `"nicio comandă"`) → **post-filtrul anti-halucinație se activează**.

Deci absența datelor nu e o gaură — e semnalul care aprinde protecția. Draftul va cere numărul comenzii sau un telefon, nu va inventa un status.

## Problema: eșecul e indistingibil de ratare

Ambele ramuri au `except Exception: pass` (546-547 și 572-573). Dacă `DATABASE_URL_METRICS` lipsește, dacă Postgres e căzut, sau dacă `pg8000` nu e instalat (517-518 întoarce `[]` direct), funcția întoarce `[]` — **exact** ca pentru un client fără comenzi. În consolă scrie aceeași frază. Nu ai cum să distingi o pană de un miss legitim. E propunerea #8 din capitolul de îmbunătățiri.

## Dependența de rulare

Antetul PEP 723 al scriptului (liniile 1-4) declară:

```python
# dependencies = ["pg8000"]
```

Pe VPS scriptul nu rulează prin `uv`, ci prin `.venv/bin/python3` — deci `pg8000` trebuie să fie în acel venv. **Verificat: e acolo, versiunea 1.31.5, pe Python 3.11.2.** Dacă lipsea, grounding-ul ar fi picat tăcut.

## Randamentul măsurat

În tot jurnalul, `--ground` a apucat să ruleze pe **21 de tichete**: 13 cu comandă găsită, 8 fără. Restul rulărilor (7.345 de tichete) au fost pe `--lean`. Wrapperul a fost comutat de pe `--lean` pe `--ground` pe 29-iun, exact când cronul a fost pus pe pauză — deci **grounding-ul nu a fost niciodată exersat la scară**. Validările punctuale din iunie au dat ~10 din 13 pe email-uri.

### 7. Escaladarea — ce o declanșează și ce face concret

## Decizia (1073-1081)

```python
_real_esc = _real_escalation(blob + " " + tr)
_llm_esc  = bool(idn.get("escalate")) or str(idn.get("severity")).upper() in ("HIGH", "URGENT")
_ord_cat  = cat in ("problema_produs", "livrare_wismo", "retur", "schimb_swap", "plata_factura",
                    "modificare_comanda", "anulare", "comanda_noua", "presale_intrebare",
                    "recenzie_feedback", "altele")
is_esc    = _real_esc or (_llm_esc and not _ord_cat)
level     = "URGENT" if (_real_esc and (ESCAL.search(deacc(blob + " " + tr))
                                        or str(idn.get("severity")).upper() == "URGENT")) else "HIGH"
```

De citit cu atenție: `_ord_cat` acoperă **11 din cele 14** categorii. Singurele în care verdictul LLM mai contează sunt `refuz_livrare`, `comentariu_social` și `spam_automat`. Pe absolut tot restul — reclamații de produs, WISMO, retur, anulare — **decide doar regexul determinist**.

Asta e intenționat (comentariul din cod, 1074-1075: „gpt-4o-mini escaladează aproape orice reclamație/WISMO”), dar e neevident la citire. Merită un comentariu mai apăsat.

## Cele trei declanșatoare deterministe (104-110)

**1. `ESCAL`** (186) — juridic și autorități:

```python
ESCAL = re.compile(r"anpc|protectia consumator|dau in judecat|instanta|avocat|denunt|reclamatie|chargeback|politi[ae]", re.I)
```

**2. `ANGER_RE`** (101) — furie explicită:

```python
ANGER_RE = re.compile(r"!!!|\b(escroc|hoti|hotie|hotilor|teap[ăa]|tepui|inselat|inselaciune|"
                      r"bataie de joc|rusine|jignit|inadmisibil|nesimti|incompeten|scandalos|dezgustator)\b", re.I)
```

**3. Majuscule** — cel puțin 12 litere și peste 70% majuscule.

Toate rulează pe text **dezacentuat** (`deacc()`, 148-149) — deci „țeapă” și „teapa” se prind la fel.

## Nivelul

- **URGENT**: semnal real **și** (`ESCAL` se potrivește **sau** LLM-ul a zis `severity: URGENT`). Adică: ANPC, instanță, avocat, chargeback, poliție.
- **HIGH**: orice alt semnal real (furie, majuscule).

## Ce face, concret, în Richpanel (1201-1208)

**Condiția:** `a.create_draft and cid` **și** `is_esc and not a.lean`.

> Deci în `--lean` escaladarea e **doar cosmetică** — apare flag-ul ⛳ în consolă și se folosește promptul `HOLDING`, dar nu se scrie nimic intern în Richpanel.

Patru scrieri:

### 1. Prioritate HIGH

```python
mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
```

Schema MCP acceptă **doar `LOW` și `HIGH`** — nu există `URGENT`. De asta urgența se codifică în tag. A fost o decizie conștientă, nu o scăpare.

### 2. Tag-uri

```python
tags = ([AI_TAG] if AI_TAG else []) + ["escaladare", "esc-%s" % level.lower()] + (["de-sunat"] if phone else [])
add_tags(mcp, cid, tags)
```

Rezultă, tipic: `ai-draft`, `escaladare`, `esc-urgent`, `de-sunat`.

`de-sunat` apare **doar dacă avem telefon** — și aici mușcă filtrul de la linia 932: un telefon cu `+40` a fost golit, deci tag-ul nu se pune.

### 3. Notă privată — brief-ul pentru agent

Generată de `escalation_note()` (833-843):

```
⚠️ ESCALADARE [URGENT] — client amenință cu ANPC pentru refund neefectuat
Problemă: Clientul a primit promisiunea unui refund acum 3 săptămâni, nu a primit banii.
Client: Maria Ionescu | tel: 0748123456 | email: maria@example.com
Comandă: EST000001 (EST) status=Livrata AWB=0000000000001
A mai scris pe: Email×3, FB mesaj×1
Sentiment: negativ/puternic
→ ACȚIUNE SUGERATĂ: verifică refundul în Shopify și sună clientul azi
(brief auto cs_auto_draft — verifică înainte de a acționa)
```

Nouă câmpuri: nivel, motiv, problemă, nume, telefon, email, linia comenzii (din `target_line`, 1087), unde a mai scris, sentiment, acțiune sugerată. Ultimul rând e un avertisment explicit către agent.

### 4. Draft de AȘTEPTARE

Nu `SYSTEM`, ci **`HOLDING`** (linia 1130). Un mesaj scurt care confirmă preluarea și spune că revine un coleg. Fără soluții, fără promisiuni, fără detalii de comandă pe canal public.

## Ce NU face

- **nu trimite** nimic clientului — doar draft;
- **nu atribuie** tichetul unui agent anume (`assign_conversation` există în MCP, dar nu e folosit);
- **nu execută** nicio acțiune pe comandă — ramura de acțiuni e `elif` după `if is_esc` (1091 vs 1094), deci escaladarea o exclude;
- `--send` și `--apply-send` **refuză** escaladările (812-813, 1215). Un caz escaladat se închide numai de un om.

## Măsurat

| | |
|---|--:|
| tichete flagate ⛳ în jurnal | 394 |
| rutări efective în Richpanel („⛳ Rutat”) | **0** |
| intrări `escalate: true` în coada de pe VPS | 328 |

Zero rutări pentru că toate cele 394 au căzut în rulări cu `--lean`. **Calea de rutare a escaladării nu a fost niciodată exersată în producție pe VPS.** E de validat pe un lot mic înainte de repornire.

### 8. Moderarea comentariilor FB/IG

## Contextul

Mecanismul corectează `replyzen.ai` — auto-responderul care punea tag-ul `auto-reply-sent` și răspundea la „tu”. Tiparul real al agenților ARONA, extras din tichete închise, e: **pozitiv → răspuns public foarte scurt** (deseori doar „❤️”), **negativ → șters**, **niciun DM**.

**Decizia ownerului, 23-iun-2026: FĂRĂ DM-uri.** Răspundem public, scurt; dacă e nevoie de rezolvare, **invităm** clientul să scrie în privat sau să sune.

## Cele trei verdicte

Din triaj, câmpul `comment_action` (definit la 406, explicat la 411-414):

| Verdict | Definiția din prompt | Ce face codul |
|---|---|---|
| `hide` | „DOAR spam/troll/abuz/vulgaritate/ofense/reclamă străină” | propune ascunderea (1125-1127); se aplică la `--approve` |
| `public` | „orice comentariu care merită un răspuns public scurt — laudă/recenzie, întrebare presale, reclamație ușoară” | draft normal, salvat; agentul îl postează |
| `none` | „comentariu pur zgomot (tag de prieten fără conținut, off-topic)” | **nimic — nu e implementat** |

> **Gaură confirmată:** `cact` e citit o singură dată, la linia 1125, doar pentru `== "hide"`. Nu există nicio ramură pentru `none`. Un comentariu clasificat ca zgomot pur primește totuși draft generat și salvat. Vezi îmbunătățirea #7.

## Calea `hide`

**Propunerea** (1125-1127):

```python
if is_public and cact == "hide":
    hide_obj = {"comment_id": cid, "page_id": page_id, "mode": "hide"}
```

`page_id` vine din `t["to"]["id"]` (1124) — adică **pagina pe care a venit comentariul**, nu brandul din Richpanel (care e stale: org-ul e `nocturna954` pe orice tichet). `PAGE_STORE` (60-67) mapează 18 id-uri de pagină la branduri.

**Aplicarea** — `fb_hide_comment()` (712-733), doar la `--approve`:

```python
u = "https://graph.facebook.com/v19.0/%s?is_hidden=true&access_token=%s"
```

Id-ul comentariului se derivă din id-ul tichetului, care are forma `pageid_postid_postid_commentid`. Codul încearcă **trei variante** (717-721): ultimele două segmente unite cu `_`, ultimul segment singur, și id-ul întreg. Comentariul din cod recunoaște că formatul nu e confirmat.

La `hide` **nu se salvează draft** (790-792, 1209-1211) — ar fi absurd un răspuns public la un comentariu ascuns.

## Tokenul

`fb_page_token()` (671-692) încearcă, în ordine:

```python
for key in ("META_PAGES_TOKEN", "META_SYSTEM_TOKEN_3", "META_SYSTEM_TOKEN",
            "META_SYSTEM_TOKEN_2", "META_SYSTEM_TOKEN_4", "META_USER_TOKEN"):
```

Pentru fiecare cheie, cheamă `GET /{page_id}?fields=access_token` și acceptă doar dacă Graph întoarce un token real. Sare peste tokenurile care încep cu `REVOKED` (681). Rezultatul se memorează, inclusiv `None`.

**Starea reală a tokenurilor:**

| Token | Stare |
|---|---|
| `META_USER_TOKEN` | REVOCAT |
| `META_SYSTEM_TOKEN` | token de ADS — **0 pagini** |
| `META_SYSTEM_TOKEN_3` | **2 pagini**: Nubra + Covoria |
| restul | nedocumentate |

> ⚠️ **Pe VPS, niciun `META_*` nu e în `/root/Scripturi/.env`.** Am verificat lista de chei. `secret()` ar cădea pe `uv run kb.py`, care pe VPS nu merge (calea `KB` se rezolvă la `/root/../../core/scripts/kb.py`, inexistentă). Deci pe VPS `fb_page_token` întoarce **întotdeauna `None`** → `fb_hide_comment` răspunde „(fără token Meta în KB)” și `fb_post_text` întoarce `""`.

## Ce merge și ce nu

| Capabilitate | Necesită | Merge azi |
|---|---|---|
| Draft de răspuns public | nimic în plus | **DA** |
| Propunere de `hide` | nimic (doar propunere) | **DA** |
| Ascundere efectivă | Page Access Token + `pages_manage_engagement` | doar Nubra/Covoria, **și doar de pe stație** |
| Textul postării comentate | Page Access Token | doar Nubra/Covoria, **nu de pe VPS** |
| **Poza reclamei** | **nimic** — `og:image` cu UA de crawler | **DA, peste tot** |
| Postare publică efectivă | Page Access Token | **NU** — agentul postează manual |
| DM privat | `pages_messaging` + App Review | **NU** — și e decizie de business să nu-l folosim |

## Cele două surse de context pentru un comentariu (966-987)

MCP-ul Richpanel întoarce **doar textul comentariului** („Care este pretul”), nu și postarea la care s-a comentat. Fără context, modelul nu știe produsul. Două remedii, independente:

**A. `fb_post_text(post_id, page_id)`** (695-709) — Graph, `fields=message,story`. Are nevoie de token de pagină. Pe VPS: indisponibil.

**B. `cs_photo.ad_block(ticket)`** — ia poza reclamei prin **`og:image`**, cu user-agent de crawler, **fără niciun token**, și o descrie vizual. Apoi `catalog_match()` (cs_photo.py, 412-456) caută produsul în catalogul metrics:

```sql
SELECT p.title, v.price, v."inventoryQuantity", v.sku, (<scor pe cuvinte>) AS sc
FROM products p JOIN variants v ON v."productId"=p.id LEFT JOIN brands b ON b.id=p."brandId"
WHERE lower(b.name)=lower(%s) AND (<OR pe cuvinte-cheie>)
ORDER BY sc DESC, v.price::numeric
```

Dacă găsește, blocul injectat conține literalmente `"PRODUS în CATALOG (preț/stoc REALE — folosește-le)"`. Codul detectează asta (linia 980, `_ad_has_catalog`) și **dezactivează post-filtrul anti-halucinație** pentru tichet — prețul e real, nu inventat.

Există un registru SQLite (`fb_post_registry.sqlite`) care ține `post_id → produs`, ca aceeași reclamă să nu fie re-descrisă. Pe VPS are 311 KB (mai mare decât cel din git, 40 KB) — deci s-a folosit.

## Cod mort, păstrat deliberat

`fb_private_reply()` (736-760) implementează Graph `/private_replies` pentru DM ca răspuns la un comentariu. **Nu e apelată nicăieri.** A fost scoasă din flux prin decizia din 23-iun, dar e păstrată pentru momentul în care echipa obține un token de pagină cu `pages_messaging`. Constrângerile FB, notate în docstring: un singur private reply per comentariu, în fereastra de 7 zile.

## Excluderea comentariilor din cron

Wrapperul rulează cu `--no-comments` (linia 925-926 sare complet canalele publice). Decizia ownerului: comentariile rămân pentru CS — hide-ul de spam și lead-urile de comandă se tratează separat. În coada de pe VPS, `comentariu_social` e totuși a doua categorie ca volum (819 din 3.156), din rulările manuale de dinainte.

### 9. Dependențele — tot ce-i trebuie ca să ruleze

## A. Secrete

Toate prin `secret()` (225-238): **env mai întâi**, apoi `uv run kb.py secret-get`, apoi `""`.

| Cheie | La ce | Obligatorie | Pe VPS |
|---|---|---|---|
| `RICHPANEL_MCP_TOKEN` | tot accesul la Richpanel (linia 872) | **DA** | în `.env` |
| `OPENAI_API_KEY` | LLM (306) și vizual (340) | DA (sau Anthropic) | în `.env`, exportată de wrapper |
| `ANTHROPIC_API_KEY` | LLM preferat (296) și vizual (331) | alternativă | **în `.env`, dar wrapperul NU o exportă** |
| `DATABASE_URL_METRICS` | grounding (521) + catalog produse | doar `--ground` | în `.env`, exportată |
| `META_PAGES_TOKEN` / `META_SYSTEM_TOKEN` / `_2` / `_3` / `_4` / `META_USER_TOKEN` | token de pagină pentru hide și text postare (679) | doar moderare FB | **NICIUNA** |

> ⚠️ Consecința primei linii îngroșate: dacă cineva rulează manual cu `set -a && . .env`, `ANTHROPIC_API_KEY` intră în env și **motorul comută pe Claude**, cu alt model și alt cost. Cronul merge pe OpenAI. E o diferență ușor de ratat.

`.env` mai conține și `DRAFT_OPENAI_API_KEY`, care **nu e folosită** de acest flux.

## B. Variabile de mediu (nu secrete)

| Variabilă | Implicit | Efect | Linia |
|---|---|---|---|
| `DRAFT_MODEL` | `gpt-4o` | modelul OpenAI. Cronul: `gpt-4o-mini` | 308 |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | modelul Claude | 300 |
| `VISION_MODEL` | `gpt-4o-mini` | modelul vizual OpenAI | 342 |
| `PROFIT_DB` | `/root/Scripturi/data/profitability.db` | calea SQLite pentru grounding | 511 |
| `CS_AGENT` | — | implicitul pentru `--agent` | 852 |
| `PYTHONUNBUFFERED` | — | **obligatoriu** pe cron, altfel jurnalul se tamponează | wrapper |
| `FB_POST_DB` | lângă `cs_photo.py` | registrul de reclame | cs_photo 38 |

## C. Fișiere

| Fișier | Rol | Git | VPS |
|---|---|---|---|
| `cs_auto_draft.py` | motorul | ✅ 1244 linii | ✅ 1243 linii |
| `cs_photo.py` | vederea pozelor | ✅ | ✅ **identic** (sha `4fb6079e…`) |
| `.learned_playbook.md` | proceduri + voce învățate | ✅ 17.884 oct., 9 categorii | ❌ **LIPSEȘTE** |
| `.auto_draft_proposals.json` | coada de propuneri | (gitignored) 516 intrări | ✅ 3.156 intrări, 7,3 MB |
| `fb_post_registry.sqlite` | cache reclame | ✅ 40 KB | ✅ 311 KB |
| `cs_backlog.sh` | wrapperul de cron | ❌ | ✅ |
| `data/cs_backlog.log` | jurnalul | ❌ | ✅ 12 MB |
| `.voice_pack.json` | generat de `build_voice_pack.py` | (gitignored, gol) | ❌ |

`.gitignore` al skill-ului exclude deliberat `.auto_draft_proposals.json` (conține PII — drafturi și context de comandă), `.voice_pack.json` și `.learned_playbook.md`.

## D. Scripturi surori — și starea lor reală

Calculate relativ la `HERE` (36-48):

| Constantă | Cale calculată | La ce | Există pe VPS |
|---|---|---|---|
| `KB` (37) | `HERE/../../../core/scripts/kb.py` | fallback pentru secrete | ❌ |
| `CI` (46) | `HERE/../customer-identity/customer_identity.py` | context 360 (mod implicit) | ❌ |
| `CSA` (47) | `HERE/../cs-actions/scripts/cs_actions.py` | execuția acțiunilor | ❌ |
| `cs_photo` (41-45) | `HERE/../cs-photo/` **plus `sys.path[0]`** | vederea pozelor | ✅ |

> **De ce `cs_photo` merge și celelalte nu:** Python pune automat directorul scriptului în `sys.path[0]`. Pe VPS, `cs_photo.py` e **lângă** `cs_auto_draft.py`, deci `import cs_photo` reușește chiar dacă `HERE/../cs-photo/` nu există. Celelalte două sunt chemate prin `subprocess` cu cale absolută — și acolo nu există remediu accidental.

**Consecințe practice pe VPS:**

- `secret()` cade pe env și doar pe env — orice cheie care nu e în `.env` e goală;
- `customer_ident()` întoarce `{}` → modul implicit (fără `--lean`, fără `--ground`) nu produce niciun context de comandă;
- `run_cs_action()` întoarce `(1, "(cs_actions.py negăsit la …)")` → **`--approve` nu poate aplica nimic**.

## E. Baze de date

| Bază | Acces | Ce ia | Unde |
|---|---|---|---|
| `metrics` (Postgres) | `pg8000`, `DATABASE_URL_METRICS` | `orders` (nume, total, status financiar, dată); prin `cs_photo`: `products`/`variants`/`brands` pentru preț și stoc | 521-547; cs_photo 412-456 |
| `profitability.db` (SQLite) | fișier local | `profit_orders`: status livrare, SKU-uri, AWB, curier. **843.459 rânduri, 804.057 cu AWB** | 548-573 |
| `fb_post_registry.sqlite` | fișier local | cache `post_id → produs` | cs_photo 289-336 |
| `richpanel_tickets.db` | **nu de acest script** | e sursa lui `gigi:cs-procedures`, care produce `.learned_playbook.md` | — |

## F. Unelte MCP Richpanel — toate 10

| Unealtă | Unde | Scrie? |
|---|---|---|
| `list_conversations` | 890 | nu |
| `get_conversation` | 916, 941 | nu |
| `list_tags` | 584 | nu |
| `create_tag` | 589 | **DA** (creează tag în workspace) |
| `add_tags_to_conversation` | 599 | **DA** |
| `create_draft` | 793, 1227 | **DA** |
| `add_private_note` | 1207 | **DA** |
| `update_conversation` | 1205 | **DA** (prioritate) |
| `update_conversation_status` | 826, 1065, 1222 | **DA** (închide) |
| `send_message` | 820, 1218 | **DA — către client** |

> `mcp__richpanel__send_message` e în `permissions.deny` în `~/.claude/settings.json` pentru agenți. Scriptul îl cheamă prin JSON-RPC direct, deci acel deny nu-l acoperă — protecția reală e că flag-urile sunt opt-in.
>
> **`create_draft` ADAUGĂ, nu suprascrie.** Nu există `delete_draft` și nici `delete_note` — verificate toate cele 21 de metode ale serverului. Curățarea e manuală, în UI.

## G. Skill-uri conexe

| Skill | Relația |
|---|---|
| `gigi:cs-procedures` | **hrănește** — produce `.learned_playbook.md` din tichete reale. Rulează: `PROC_MODEL=gpt-4o cs_procedures.py --category all --out .learned_playbook.md` |
| `gigi:cs-photo` | **importat direct** ca modul. Pozele clientului + poza reclamei + potrivirea în catalog |
| `gigi:customer-identity` | context 360 în modul implicit (SSH; nu merge din cron) |
| `gigi:cs-actions` | execută modify/cancel/swap/resend la `--approve`. Agenți valizi: Raluca, Oana, Andra, Anna, OanaO |
| `gigi:cs-360` | echivalentul manual — client după telefon/nume/email |
| `gigi:cs-sentiment` | scor de sentiment per tichet, separat |
| `gigi:cs-quality-audit` | „unde am răspuns prost”, retrospectiv |
| `gigi:richpanel-auto-triage` | triaj de magazin/categorie/prioritate. **Avea același bug de tag-uri cu nume în loc de UUID** — de verificat dacă s-a reparat |
| `gigi:cs-comment-intelligence` | lead-uri și reclamații din comentarii |
| ClickUp doc `2kyqg8j1-3895` | macro-urile CS oficiale. **Nu sunt citite de acest script** — `cs_draft_reply.py` le folosește; `cs_auto_draft.py` merge pe `SYSTEM` + `.learned_playbook.md` |

## H. Runtime

- Python ≥ 3.10 (antet PEP 723). Pe VPS: **3.11.2** în `/root/Scripturi/.venv`.
- Singura dependență externă: **`pg8000`** (1.31.5 pe VPS). Restul e bibliotecă standard — `urllib`, `json`, `sqlite3`, `subprocess`, `argparse`, `unicodedata`, `base64`.
- Pe stație: `uv run cs_auto_draft.py` rezolvă dependența singur. Pe VPS: `.venv/bin/python3`, deci `pg8000` trebuie să fie instalat în venv.

### 10. Starea măsurată la predare — ce spune jurnalul

Totul din `/root/Scripturi/data/cs_backlog.log`, 137.095 de linii, **24-iun-2026 15:39 → 02-iul-2026 10:00**. 25 de markere `RUN`, 24 `DONE`.

## Volumele

| Ce | Cât |
|---|--:|
| Drafturi salvate în Richpanel | **1.336** |
| Tichete sărite (aveau deja tag-ul) | 28.207 |
| Tichete excluse ca spam/zgomot | 5.151 |
| Drafturi invalide oprite de gardă | **6.015** |
| Eșecuri de triaj LLM (429) | **10.960** |
| Apeluri `create_draft` eșuate | **0** |
| Tichete flagate ⛳ escaladare | 394 |
| Rutări efective de escaladare | **0** |
| „Comandă neclară → nicio acțiune” | 422 |
| Propuneri de acțiune (modify/cancel/swap/resend) | **0** |
| Propuneri de hide | **0** |
| Tichete cu `--ground` | 21 (13 cu comandă, 8 fără) |
| Tichete cu `--lean` | 7.345 |
| Poze văzute | 2 |
| Corectări anti-halucinație | **0** |
| Șabloane sigure | **0** |

## Cum se citesc cifrele astea

**Garda anti-gunoi a fost cea mai activă componentă.** 6.015 drafturi invalide blocate, 0 scrieri eșuate. Fără ea, în Richpanel ar fi ajuns mii de drafturi cu textul „(eroare LLM: HTTP Error 429)” — imposibil de șters prin API.

**Gâtul de sticlă e rata OpenAI, nu Richpanel.** 10.960 de eșecuri de triaj, toate `429 Too Many Requests` de la OpenAI. Rata Richpanel s-a manifestat doar ca backoff, niciodată ca eroare vizibilă.

**Ultima rulare a eșuat integral.** Extras din 2026-07-02 10:00:06, canalul `email`, 464 de tichete selectate:

```
  ⚠️ triaj LLM eșuat (#276536) → fallback pe regex/euristici: HTTP Error 429: Too Many Requests
  [1/464] #276536 · Esteban · Email · altele · sent=pozitiv/puternic 
  client: Paula Anghele | comenzi: 1 | a mai scris: grounded — 1 comenzi găsite în DB
  ┌─ DRAFT (—) ────────────
  │ (eroare LLM: HTTP Error 429: Too Many Requests)
  └──────────────────────────────────────────
  ⛔ draft invalid (eroare LLM / gol) → NU salvez (sar tichetul).
```

Același tipar la toate. Observă însă că **grounding-ul a funcționat** — „1 comenzi găsite în DB”. Doar LLM-ul a căzut.

În mijlocul rulării apare un marker inserat manual, `===== RELANSAT DEDICAT $(date +%H:%M:%S) =====`, cu variabila neexpandată — semn de intervenție ad-hoc. Probabil explică cele 25 de `RUN` față de 24 `DONE`.

**Filtrarea de spam e făcută în proporție de 99,9% de LLM.** Din 5.151 de excluderi: 5.147 de la verdictul LLM, 2 judge.me, 1 raport SaaS, 1 padding, 0 expeditori non-clienți, 0 bounce-uri. Filtrele deterministe sunt o plasă de siguranță, nu motorul. Ceea ce înseamnă că la un model mai slab, filtrarea se degradează.

**Nicio cale de mutație nu a fost exersată.** 0 propuneri de acțiune, 0 propuneri de hide, 0 rutări de escaladare, 0 aplicări, 0 trimiteri. Toate cauzate de configurația de cron (`--lean` la început, `--ground` fără acțiuni active pe VPS, plus scripturile surori lipsă).

**Canalele rulate:** `email` (25 de treceri), `email_from_widget`, `facebook_message`, `instagram_message`, `messenger` (câte 24). Comentariile publice — niciodată, prin `--no-comments`.

## Coada de propuneri (VPS, 7,3 MB, 3.156 intrări)

| | |
|---|--:|
| cu comandă de acțiune (`cmd`) | 9 |
| cu obiect `hide` | 31 |
| escaladate | 328 |
| aplicate (`applied`) | **0** |
| trimise (`sent`) | **0** |

Categorii:

| Categorie | Nr. |
|---|--:|
| `altele` | 1.119 |
| `comentariu_social` | 819 |
| `presale_intrebare` | 302 |
| `recenzie_feedback` | 234 |
| `livrare_wismo` | 195 |
| `problema_produs` | 133 |
| `retur` | 114 |
| `anulare` | 105 |
| `comanda_noua` | 60 |
| `modificare_comanda` | 37 |
| `schimb_swap` | 17 |
| `plata_factura` | 15 |
| `refuz_livrare` | 5 |
| `anulare\|altele` (malformat) | 1 |

> `altele` la 35% e semnalul cel mai clar că triajul are loc de îmbunătățire. Iar intrarea `anulare|altele` arată că modelul a întors o dată o categorie compusă — coerciția `str(cat)` de la linia 1045 a prevenit crash-ul, dar căutarea în `LEARNED` a ratat.

Magazine, top 8: Esteban 665, **`magazinul nostru` 554**, Magdeal 366, Ofertele Zilei 292, Reduceri bune 206, Bonhaus 142, Nubra 135, Casa Ofertelor 116.

> Cele 554 de tichete cu brand nerezolvat (17,5%) s-au semnat „echipa noastră” în loc de „Echipa \<Magazin\>” și n-au primit numărul de telefon din `STORE_PHONE`. E consecința combinată a `PAGE_STORE` incomplet, a `EMAIL_BRAND` incomplet și a lipsei comenzilor în `--lean`.

### 11. Runbook — cum lucrezi cu el fără să strici nimic

## Regulile care nu se negociază

1. **`create_draft` adaugă și nu se poate șterge prin API.** Fiecare rulare cu `--create-draft` lasă un draft NOU. Nu re-rula pe aceleași tichete fără `--skip-tagged`. Curățarea se face manual, în UI Richpanel.
2. **Nicio iterație de prompt cu `--create-draft`.** Fiecare fix face drafturile deja salvate stale, iar noile se stivuiesc peste. Finalizează promptul în dry-run, apoi fă **o singură** scriere curată.
3. **Rata Richpanel e partajată cu CS-ul live** (~60 cereri/minut). Un singur worker, secvențial. Paralelismul e exclus — 429 apare inclusiv la simpla listare.
4. **Rata OpenAI e plafonul real de debit.** Configurația curată confirmată: 2 workeri (4 cereri simultane), `--lean`, `--sleep 0.8` → zero erori. 3 workeri pornesc curat dar degradează; de la 4 în sus, furtună de 429.
5. **Dry-run nu e gratis.** Fără `--create-draft` nu scrie nimic, dar tot face 2 apeluri LLM pe tichet.

## Ordinea de verificare înainte de a reporni cronul

### Pas 1 — repară ce lipsește pe VPS

```bash
# playbook-ul învățat (vocea agenților reali) — lipsește
scp plugins/gigi/skills/cs-draft-reply/.learned_playbook.md root@84.46.242.181:/root/Scripturi/

# paritate cu git (linia Lab Noir) — prin deploy git-driven, nu scp manual
ssh root@84.46.242.181 'bash /root/Scripturi/deploy.sh --apply'
```

### Pas 2 — testează rata LLM pe un lot minuscul

```bash
ssh root@84.46.242.181
cd /root/Scripturi
export RICHPANEL_MCP_TOKEN="$(grep -m1 ^RICHPANEL_MCP_TOKEN= .env | cut -d= -f2-)"
export OPENAI_API_KEY="$(grep -m1 ^OPENAI_API_KEY= .env | cut -d= -f2-)"
export DATABASE_URL_METRICS="$(grep -m1 ^DATABASE_URL_METRICS= .env | cut -d= -f2-)"
export DRAFT_MODEL=gpt-4o-mini PYTHONUNBUFFERED=1
.venv/bin/python3 cs_auto_draft.py --channel email --limit 5 --ground
```

**Criteriul de trecere:** 5 din 5 drafturi fără „(eroare LLM”. Dacă apar 429, nu merge mai departe — ori urci tier-ul OpenAI, ori exporți și `ANTHROPIC_API_KEY` (comută automat pe Claude).

### Pas 3 — validează calea de escaladare, care n-a rulat niciodată

Găsește un tichet cu semnal clar (ANPC sau furie) și rulează țintit:

```bash
.venv/bin/python3 cs_auto_draft.py --only <nr> --create-draft --ground --tag ai-test
```

Apoi verifică **în UI Richpanel**: prioritate HIGH, tag-urile `ai-test`/`escaladare`/`esc-high|esc-urgent`/`de-sunat`, și nota privată cu brief-ul.

### Pas 4 — poarta de limbă și gramatică

Obligatorie **după orice modificare de prompt**:

```bash
.venv/bin/python3 cs_auto_draft.py --limit 20 --json --ground 2>/dev/null \
  | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/cs_drafts.json
uv run grammar_audit.py --file /tmp/cs_drafts.json
```

Iterează audit → fix → re-audit până la **0 greșeli**. În iunie a convers la 0.

### Pas 5 — repornirea

Scoate diezul din crontab, linia 45:

```
0 9-21/3 * * * /usr/bin/flock -n /tmp/cs_backlog.lock /root/Scripturi/cs_backlog.sh >> /root/Scripturi/data/cs_backlog.log 2>&1
```

La fiecare 3 ore, 9:00-21:00, sub `flock` (nu se suprapun rulările). Prima rulare va fi lungă (drafturează backlog-ul existent o dată), apoi devine incrementală prin `--skip-tagged`.

## Comenzi de diagnostic

```bash
# ce s-a întâmplat la ultima rulare
grep -n "^===== RUN" /root/Scripturi/data/cs_backlog.log | tail -3

# sănătatea: drafturi salvate vs blocate
grep -c '✅ DRAFT salvat'   /root/Scripturi/data/cs_backlog.log
grep -c '⛔ draft invalid'   /root/Scripturi/data/cs_backlog.log
grep -c 'triaj LLM eșuat'    /root/Scripturi/data/cs_backlog.log

# ce mai e în coadă neaplicat
python3 -c "import json;d=json.load(open('/root/Scripturi/.auto_draft_proposals.json'));\
print('cmd:',sum(1 for v in d.values() if v.get('cmd')),\
'hide:',sum(1 for v in d.values() if v.get('hide')),\
'applied:',sum(1 for v in d.values() if v.get('applied')))"

# paritate VPS ↔ git
ssh root@84.46.242.181 'sha256sum /root/Scripturi/cs_auto_draft.py'
shasum -a 256 plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py
```

## Harta simptom → cauză

| Simptom | Cauză probabilă | Unde te uiți |
|---|---|---|
| Toate drafturile ies `(eroare LLM 429)` | rata OpenAI | urcă tier-ul sau exportă `ANTHROPIC_API_KEY` |
| Drafturile ies `(eroare LLM ...'uv')` | `secret()` cade pe KB și `uv` nu e în PATH | pune cheia în `.env` și exportă-o în wrapper |
| Aceleași tichete se draftează la fiecare rulare | tag-ul nu se atașează | `tag_id()` / `list_tags` — verifică tag-urile în UI |
| Draftul se semnează „echipa noastră” | brand nerezolvat | `PAGE_STORE`, `EMAIL_BRAND`, sau lipsa comenzilor în `--lean` |
| Draftul inventează prețuri sau statusuri | grounding fără rezultat + post-filtru netrecut | rulează cu `--ground` și verifică linia „grounded — N comenzi” |
| Jurnalul e gol în timpul rulării | lipsește `PYTHONUNBUFFERED=1` | wrapper |
| Escaladările nu apar în Richpanel | `--lean` dezactivează rutarea | scoate `--lean`, folosește `--ground` |
| `--approve` zice „cs_actions.py negăsit” | scriptul nu e pe VPS | rulează `--approve` de pe stație |
| Drafturi duplicate pe același tichet | s-a re-rulat fără `--skip-tagged` | curățare manuală în UI; nu există API |

## Ce să NU faci niciodată

- `--apply-send` pe un lot, fără să fi validat drafturile una câte una. E singurul flag care trimite **în buclă** către clienți reali.
- Să paralelizezi pe mai mult de 2 workeri.
- Să repari promptul și să re-scrii în Richpanel la fiecare iterație.
- Să faci `scp` manual pe VPS în loc de `deploy.sh --apply` — e cauza documentată a divergențelor git↔VPS.
- Să publici skill-ul cu `gigi:publish-skill` având modificări nesalvate: face `git checkout main` și **aruncă working tree-ul**. S-au pierdut deja editări așa, o dată.

**Ce nu se știe (explicit):**

- Nu știu dacă cele 1.336 de drafturi scrise între 24-iun și 2-iul mai sunt în Richpanel sau au fost curățate manual. Nu am atins Richpanel în această sesiune (regulă de sarcină). `create_draft` ADAUGĂ și nu există API de ștergere — deci dacă n-au fost curățate manual din UI, sunt încă acolo.
- Nu știu dacă `fb_hide_comment` (liniile 712-733) funcționează. Nu a fost apelat niciodată: 0 „PROPUNERE HIDE” în jurnal și 0 intrări `applied` în coadă (deși 31 de intrări au un obiect `hide` pregătit). Formatul exact al comment-id derivat din id-ul tichetului rămâne nevalidat, iar pe VPS niciun token META_* nu e în `.env`, deci `fb_page_token` ar întoarce `None` oricum.
- Nu știu dacă `--apply-send` (trimitere live în buclă) a fost rulat vreodată. 0 apariții de „TRIMIS LIVE” în jurnal și 0 intrări `sent` în coadă, dar coada de pe VPS acoperă doar rulările care au ajuns până la scrierea ei.
- Nu știu de ce jurnalul are 25 de markere RUN și doar 24 DONE. Cel mai probabil ultima rulare (2-iul) a fost întreruptă manual — în interiorul ei apare și un marker inserat de mână, „===== RELANSAT DEDICAT $(date +%H:%M:%S) =====”, cu variabila neexpandată, semn de intervenție ad-hoc.
- Nu știu dacă `DRAFT_OPENAI_API_KEY` din `/root/Scripturi/.env` e o cheie separată, dedicată acestui flux, sau un rest. Wrapperul exportă `OPENAI_API_KEY`, nu pe aceasta. Dacă e o cheie cu cotă proprie, mutarea fluxului pe ea ar putea rezolva 429-urile — de verificat cu ownerul, nu am testat-o.
- Nu am putut măsura câte din cele 2.421+ tichete deschise din iunie mai sunt deschise azi. Cifra e din memoria de proiect (24-iun-2026), nu din Richpanel live.
- Precedența de limbă rulează pe `blob + tr`, adică pe TOT firul, inclusiv replicile agenților și textul reclamei — nu doar pe mesajul clientului. Teoretic un fir cehesc în care un agent român a scris o replică cu diacritice ar putea vira detecția pe `ro`. Nu am un caz măsurat care să confirme sau să infirme.

---

## 4. Ce a rulat de fapt — criminalistică pe jurnale

> Sistemul de auto-draft CS (cs_auto_draft.py) e oprit din 29-iunie-2026 si asa trebuie sa ramana pana cand cineva il repune constient in functiune. Versiunea de pe VPS nu e o bifurcatie: e byte-identica cu commitul git fe410875 (PR #367, 4-iul) si difera de git HEAD printr-o singura linie de 34 de octeti, intrarea Lab Noir din PAGE_STORE, deci un deploy al git peste VPS nu pierde absolut nimic, ci doar adauga. Lucrul de eficienta din 3-iul (prompt caching Anthropic plus flagul --fast-triage) e deja in git, iar cele sase backup-uri de pe VPS reconstituie complet ziua de 29-iun, de la vederea pozelor pana la garda determinista anti-supra-escaladare. Ce s-a pierdut cu adevarat e altceva si e mai vechi: pe 26-iun, la 4 minute dupa ce a fost comis, un raspuns canned pentru sesizarile ANPC/juridic (18 linii, text legal necompus de LLM) a fost sters de un git checkout main al fluxului de publicare si nu s-a mai intors niciodata, nici in git, nici pe VPS. Jurnalul spune povestea reala a productiei: din 25 de rulari, una singura a produs drafturi (1.336 pe 24-iun), 23 de rulari de cron consecutive au produs zero fiindca secret() chema uv, care nu e in PATH-ul cronului, iar cea de-a 25-a a murit pe 429 de la OpenAI; separat, un al doilea wrapper nedocumentat, cs_draft_all.sh, a mai scris 1.306 drafturi pe 2-3 iul, dintre care 1.217 pe comentarii publice Facebook, contrar deciziei de proces. In total 2.642 de tichete au primit o schita scrisa in Richpanel, 2.289 sunt inca deschise azi, si Richpanel nu are API de stergere a schitelor.


### 1. Harta sistemului: ce ruleaza, unde, si cine cheama pe cine

### Cele trei copii ale aceluiasi fisier

| Unde | Cale exacta | sha256 (16) | Octeti | Linii | mtime |
|---|---|---|---|---|---|
| Mac, git working tree | `/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | `57631aebe2f27af1` | 93.459 | 1.244 | 2026-07-08 14:14 |
| VPS, clona git (auto-pull) | `/root/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | `57631aebe2f27af1` | 93.459 | 1.244 | 2026-07-30 21:31 |
| VPS, copia PLATA pe care o ruleaza cronul | `/root/Scripturi/cs_auto_draft.py` | `b5f474232b0ad203` | 93.425 | 1.243 | 2026-07-03 11:27:55 |

Clona git de pe VPS se actualizeaza singura: `run_cs_pipeline.py` (cron la 30 de minute, orele 8-16) face `git pull --ff-only` pe `/root/Scripturi/team-intelligence` la fiecare rulare. Deci **versiunea din git este deja pe VPS, langa cea rulata**. Copia plata e o copie facuta manual cu scp, ramasa in urma cu un commit.

### Lantul de executie

```
crontab linia 45  (COMENTATA din 29-iun)
   -> /usr/bin/flock -n /tmp/cs_backlog.lock
      -> /root/Scripturi/cs_backlog.sh        (wrapper, 910 octeti, 2-iul 10:20)
         sursaza din /root/Scripturi/.env (root, 600):
             RICHPANEL_MCP_TOKEN, OPENAI_API_KEY, DATABASE_URL_METRICS
         exporta DRAFT_MODEL=gpt-4o-mini, PYTHONUNBUFFERED=1
         bucla peste 5 canale: email, facebook_message, messenger,
                               email_from_widget, instagram_message
         -> .venv/bin/python3 cs_auto_draft.py --channel <ch> --limit 3000
              --scan 6000 --create-draft --ground --no-comments
              --skip-tagged --tag ai-draft --sleep 0.5
      -> jurnal: /root/Scripturi/data/cs_backlog.log
```

Exista si un **al doilea wrapper, rulat doar manual, care NU e in cron si NU e in SKILL.md**: `/root/Scripturi/cs_draft_all.sh` (plus `cs_draft_all.sh.bak-0703`), cu jurnalul lui `/root/Scripturi/data/cs_draft_all.log`. Detalii in sectiunea 6.

### Ce cheama cs_auto_draft mai departe

| Dependenta | Cale pe VPS | Stare azi |
|---|---|---|
| `cs_photo` (vedere poze client + poza reclamei) | `/root/Scripturi/cs_photo.py` | PREZENT, sha identic cu `plugins/gigi/skills/cs-photo/cs_photo.py` din git |
| `profitability.db` (status livrare, AWB, curier) | `/root/Scripturi/data/profitability.db` | PREZENT, 722 MB, reimprospatat azi 12:00 |
| DB metrics (comenzi Shopify dupa email/telefon/nr) | `DATABASE_URL_METRICS`, `scraper@38.242.226.83/metrics` | CONECTEAZA OK |
| `.learned_playbook.md` (procedurile si vocea invatate) | `/root/Scripturi/.learned_playbook.md` | **LIPSESTE** |
| `cs_procedures.py` (il regenereaza) | `/root/Scripturi/cs_procedures.py` | **LIPSESTE** |
| `cs_actions.py` (executa actiunile la `--approve`) | `/root/Scripturi/cs_actions.py` | **LIPSESTE** |
| KB (`kb.py`, pentru secrete) | se rezolva la `/core/scripts/kb.py` | **NU EXISTA** - vezi capcana 4 |

### Nota importanta despre KB pe VPS

`HERE = os.path.dirname(os.path.abspath(__file__))` si `KB = HERE/../../../core/scripts/kb.py`. In layout-ul de skill asta rezolva corect. Pe VPS, unde fisierul sta plat in `/root/Scripturi/`, rezolva la `/core/scripts/kb.py`, care nu exista. Consecinta: `secret()` nu poate lua niciodata nimic din KB pe VPS, **toate cheile trebuie sa fie in `.env`**. Pentru `ANTHROPIC_API_KEY` asta e de fapt o plasa de siguranta: cheia exista in `.env` (108 caractere) dar `cs_backlog.sh` NU o exporta, deci cronul ramane pe OpenAI. Daca cineva ruleaza manual cu `set -a; . .env`, se muta tacut pe Claude Sonnet.

### 2. Diff VPS vs git, functie cu functie

### Rezultatul, scurt: o singura linie

```diff
--- git HEAD  plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py
+++ VPS       /root/Scripturi/cs_auto_draft.py
@@ -64,7 +64,6 @@
     "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
-    "61586834387211": "Lab Noir",
 }
```

Asta e tot. `diff -u` intre cele doua fisiere produce exact 3 linii cu `+`/`-` (una de continut, doua antete). Diferenta de octeti e 93.459 - 93.425 = **34**, adica exact lungimea liniei sterse cu indentare si newline. Lista de functii si de constante de nivel superior e identica: 78 de intrari de fiecare parte, `diff` intre ele returneaza gol.

### Care e mai noua

**Git.** Am calculat sha256-ul blobului la fiecare commit care atinge fisierul:

| Commit | sha256 (16) | Linii | Octeti | Data | PR |
|---|---|---|---|---|---|
| `31d43aa6` (HEAD) | `57631aebe2f27af1` | 1.244 | 93.459 | 2026-07-08 14:14 | #397 |
| `fe410875` | **`b5f474232b0ad203`** | 1.243 | 93.425 | 2026-07-04 09:14 | #367 |
| `2438f2ff` | `e4d8eb7c5d3bc083` | 1.235 | 92.350 | 2026-06-29 17:10 | #340 |
| `20832dee` | `8782c0be1eb581ad` | 1.178 | 85.651 | 2026-06-29 15:37 | #335 |
| `c7ff4cab` | `7152d684b24e0d4b` | 1.158 | 84.684 | 2026-06-29 14:53 | #333 |
| `33152e95` | **`13d1862ff9a84a58`** | 1.072 | 78.354 | 2026-06-29 14:27 | #330 |
| `ae2db653` | `f57ce56f1ec0cc91` | 1.055 | 77.112 | 2026-06-29 14:13 | #328 |
| `5fbfa5d8` | `2f8e581d8c21f9b8` | 991 | 73.347 | 2026-06-29 13:50 | #327 |
| `ac01991b` | **`d61a24aa0d47434e`** | 929 | 67.513 | 2026-06-26 10:24 | #277 |
| `f564d3f8` | `029e50cb88ec92df` | 947 | 69.150 | 2026-06-26 10:20 | #276 |
| `ecc5789d` | **`d61a24aa0d47434e`** | 929 | 67.513 | 2026-06-24 16:48 | #232 |
| `8947d711` | `549306ca419683bf` | 853 | 62.027 | 2026-06-24 12:58 | #228 |
| `c1794027` | `7461f26782c52661` | 852 | 60.837 | 2026-06-23 23:37 | #223 |
| `e0e0b31d` | `5d377575c24c8d98` | 748 | 51.349 | 2026-06-23 12:38 | #221 |

`b5f474232b0ad203` = exact commitul `fe410875`. **Fisierul de pe VPS nu e o bifurcatie, e un commit din istoria git**, unul in urma. Git HEAD e cu 4 zile mai nou.

### Ce s-ar pierde deployand git peste VPS

**Nimic.** Git este un superset strict al VPS-ului: aceleasi 1.243 de linii plus una. Ai castiga maparea paginii Facebook `61586834387211` la brandul Lab Noir - fara ea, orice tichet venit de pe acea pagina cade pe brand necunoscut si draftul se semneaza `echipa noastra` in loc de `Echipa Lab Noir`.

### Ce s-a lucrat pe 3-iul: diferenta `.bak-efficiency-0703` -> fisierul care ruleaza

Backupul `.bak-efficiency-0703` (`e4d8eb7c5d3bc083`) este byte-identic cu commitul `2438f2ff`/PR #340. Deci lucrul de eficienta din 3-iul e exact diff-ul #340 -> #367, si el e **deja in git**. Trei schimbari, +18/-10 linii:

**(a) Prompt caching Anthropic**, in `llm()`, git liniile 299-302:
```diff
-        "system": system, "messages": [{"role": "user", "content": user}]}
+        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
+        "messages": [{"role": "user", "content": user}]}
```
Comentariul din cod noteaza pragul minim de cache: Haiku 4.5 = 4096 tokeni, Sonnet 4.6 = 2048; SYSTEM are ~2,6k si IDENTIFY ~1,3k, deci prinde pe Sonnet, nu pe Haiku.

**(b) Flag nou `--fast-triage`**, git linia 861: sare apelul LLM de triaj cand categoria data de regex e sigura (diferita de `altele` si `spam_automat`), deci circa 1 apel LLM pe tichet in loc de 2.

**(c) Bucla principala devine conditionata**, git liniile 1033-1044: blocul `try: llm(IDENTIFY_SYS, ...)` intra pe ramura `else` a lui `if a.fast_triage and _hint not in ("altele", "spam_automat")`. Garzile de spam si de escaladare raman deterministe si active in ambele cazuri.

### Capcana publish-skill: s-a mai intamplat, si se poate MASURA

Da, si o singura data in cele 14 commituri. Blobul `d61a24aa0d47434e` apare de doua ori in istorie, la `ecc5789d` (#232, 24-iun 16:48) si la `ac01991b` (#277, 26-iun 10:24). Intre ele, `f564d3f8` (#276, 26-iun 10:20:42) adaugase 18 linii; patru minute mai tarziu, #277 a readus fisierul byte-identic la starea de la #232.

Ce a fost sters:
```python
# detectie specifica ANPC/OPC/juridic (subset al ESCAL) - pt raspunsul CANNED legal
ANPC_RE = re.compile(r"\banpc\b|\bopc\b|protectia consumator|dau in judecat|...", re.I)
# raspuns CANNED pentru sesizari ANPC/juridic (verbatim, NU generat de LLM - text legal)
ANPC_REPLY = ("Buna ziua%s. Am preluat solicitarea dumneavoastra ...")
...
if is_esc and ANPC_RE.search(deacc(blob + " " + last_cust + " " + (idn.get("escalation_reason") or ""))):
    draft, engine = (ANPC_REPLY % ((", " + name) if name else "")), "canned-anpc"
```

`grep -c 'ANPC_REPLY\|ANPC_RE'` returneaza **0** si in git HEAD, si pe VPS. Blocul nu s-a mai intors niciodata. Era singurul loc din sistem in care un raspuns juridic pleca verbatim, fara LLM - adica singurul raspuns garantat necompus de model pe categoria cu cel mai mare risc legal.

Editurile de AWB din 29-iun despre care vorbeste memoria au fost **re-aplicate cu succes**: `AWB_RE` si parametrul `awbs=` sunt prezente si in HEAD, si pe VPS.

**Astazi nu exista pierdere in curs**: branch `main`, `git status --short` pe directorul skillului returneaza gol, working tree identic cu HEAD.

### 3. Cele 6 backup-uri: cronologie reconstituita

Sunt **sase** fisiere `.bak`, nu cinci: cinci din 29-iun si unul din 3-iul.

| # | Fisier | Octeti | Linii | mtime VPS (+0200) | sha256 (16) | Corespondent git |
|---|---|---|---|---|---|---|
| B1 | `cs_auto_draft.py.bak-20260629-162212` | 78.354 | 1.072 | 29-iun 15:22:12 | `13d1862ff9a84a58` | `33152e95` / PR #330 |
| B2 | `cs_auto_draft.py.bak-20260629-163827` | 87.439 | 1.185 | 29-iun 15:38:27 | `dd18f316f288f6c2` | intermediar, necomis |
| B3 | `cs_auto_draft.py.bak-20260629-164236` | 88.998 | 1.196 | 29-iun 15:42:36 | `0394059081e5d431` | intermediar, necomis |
| B4 | `cs_auto_draft.py.bak-20260629-165430` | 89.159 | 1.197 | 29-iun 15:54:31 | `9c98d4c3050a8bf8` | intermediar, necomis |
| B5 | `cs_auto_draft.py.bak-20260629-170703` | 92.134 | 1.233 | 29-iun 16:07:03 | `45425ba65e441143` | intermediar, necomis |
| B6 | `cs_auto_draft.py.bak-efficiency-0703` | 92.350 | 1.235 | 3-iul 11:27:55 | `e4d8eb7c5d3bc083` | `2438f2ff` / PR #340 |
| — | `cs_auto_draft.py` (viu) | 93.425 | 1.243 | 3-iul 11:27:55 | `b5f474232b0ad203` | `fe410875` / PR #367 |

Nota de fus orar: mtime-urile sunt in ora VPS (Europe/Berlin, +0200), stampilele din nume sunt in +0300 (ora Bucurestiului). Acelasi moment fizic. B6 si fisierul viu au acelasi mtime la 0,45 secunde diferenta: `cp` pentru backup, imediat urmat de `scp` pentru noua versiune.

### Ce s-a schimbat la fiecare pas

**B1 -> B2** (+120/-11 linii). *Vederea pozelor.* Cel mai mare pas al zilei.
- functii noi: `_enc_url()` (percent-encode pe URL - pozele WhatsApp au spatii in nume si urllib crapa), `_vision_describe()` (Anthropic sau OpenAI, model vizual), `describe_photos()` (extrage atasamentele CLIENTULUI, dedup pe nume de fisier, sare imaginile sub 12.000 octeti ca sa nu descrie logo-uri si pixeli de tracking)
- `import cs_photo` din skillul frate `cs-photo`, cu `_csp = None` si cadere pe logica locala daca nu e pe path
- flag `--photos`, implicit PORNIT (`BooleanOptionalAction`)
- pozele sunt **exceptate de la filtrul anti-halucinare**: daca am vazut poza, ce spune draftul despre ea e informatie reala
- `NON_CUSTOMER_SENDER_RE`: expeditorii de tip curier (dpd, sameday, econt, packeta, cargus, fancourier, posta-romana, gls, nemo-express) sunt exclusi ca spam - clientii nu scriu de pe @dpd.ro
- cautarea comenzii se largeste de la ultimul mesaj la subiect plus tot firul: `_gtxt = subj + " " + tr`
- garda anti-gunoi se largeste de la `(eroare LLM` la orice `(eroare`
- la comentarii, textul postarii/reclamei se prepend-uieste in transcript

**B2 -> B3** (+17/-6). *Filtre de zgomot.*
- `NON_CUSTOMER_SENDER_RE` primeste aplicatiile Shopify: omegatheme, consentik, mailchimp, klaviyomail, sendgrid
- `JUDGEME_NOTIF_RE` = `left (a|the following) \d+ star review` - notificarea automata judge.me, dar NU si replica reala a clientului la `Re: ... cum ti s-a parut`
- `SAAS_NOISE_SUBJ_RE` = subiecte-sablon de raport SaaS (weekly report, performance report is ready, consent banner performed...)
- regula de prompt: **parfum sau produs spart la livrare = procedura standard de retrimitere gratuita, NU escaladare**, decat daca clientul e explicit furios, e a doua oara pe aceeasi comanda, sau invoca ANPC

**B3 -> B4** (+1/-0). Un singur rand, in randul de `--json`: se adauga `subject` si `orders` in obiectul exportat, ca auditul sa poata deosebi o comanda reala de una inventata.

**B4 -> B5** (+38/-8). *Garda determinista anti-supra-escaladare.* Al doilea pas important al zilei.
- `import unicodedata`
- `BOUNCE_RE` (mailer-daemon, address not found, delivery status notification, nu a putut fi livrat)
- `_padded_noise(s)`: mesaj in care peste 55% din caractere sunt spatii albe sau caractere invizibile de categorie Unicode `Cf` - tiparul newsletterelor si al notificarilor SaaS
- `ANGER_RE`: escroc, hoti, teapa, inselat, bataie de joc, rusine, jignit, inadmisibil, nesimti, incompeten, scandalos, dezgustator, plus `!!!`
- `_real_escalation(text)`: adevarat doar la ANPC/juridic (`ESCAL`), SAU insulte (`ANGER_RE`), SAU mesaj cu peste 70% majuscule din minimum 12 litere
- poarta se schimba din:
  ```python
  is_esc = bool(idn.get("escalate")) or severity in ("HIGH","URGENT") or ESCAL.search(...)
  ```
  in:
  ```python
  _ord_cat = cat in ("problema_produs","livrare_wismo","retur","schimb_swap","plata_factura",
                     "modificare_comanda","anulare","comanda_noua","presale_intrebare",
                     "recenzie_feedback","altele")
  is_esc = _real_esc or (_llm_esc and not _ord_cat)
  ```
  Comentariul din cod spune de ce, negru pe alb: *"gpt-4o-mini escaladeaza aproape orice reclamatie sau WISMO"*. Pe categoriile obisnuite, LLM-ul singur nu mai poate escalada.
- regula de prompt: nu deflecta in privat o intrebare publica simpla la care se poate raspunde (cate bucati la X lei, ce pret are, dati-mi numarul de telefon, cum comand)

**B5 -> B6** (+4/-2).
- antetul PEP 723 devine `dependencies = ["pg8000"]` (fara el, grounding-ul cade tacut cand se ruleaza cu `uv run`)
- `_ad_has_catalog`: daca blocul reclamei a adus pret sau stoc REAL din catalog, draftul e exceptat de la filtrul `HALLU` - altfel filtrul rescria drafturi care citau corect pretul din reclama

**B6 -> fisierul viu** (+17/-9): lucrul de eficienta din 3-iul, descris in sectiunea 2.

### Verdict pe backup-uri

Lantul e **continuu si complet recuperat in git**. B2, B3, B4 si B5 nu au corespondent direct in git, dar sunt stari intermediare ale aceleiasi zile, iar descendentul lor imediat (B6 = PR #340) e in git cu tot continutul lor. Nimic din cele sase fisiere nu contine cod care sa lipseasca azi din HEAD. Se pot arhiva sau sterge fara pierdere - dar abia dupa ce copia plata e adusa la nivelul git.

### 4. Cronul: linia exacta, starea, si daca ar mai merge azi

### Linia, verbatim, din `crontab -l` al lui root, linia 45

```
# CS backlog auto-draft email+DM (comentarii excluse, skip-tagged) - gigi
# PAUZAT 2026-06-29 0 9-21/3 * * * /usr/bin/flock -n /tmp/cs_backlog.lock /root/Scripturi/cs_backlog.sh >> /root/Scripturi/data/cs_backlog.log 2>&1
```

Comentata din **29-iunie-2026**. Ca sa o reactivezi, stergi prefixul `# PAUZAT 2026-06-29 ` si ramane `0 9-21/3 * * * ...`.

Program: minutul 0, orele 9, 12, 15, 18, 21, **ora VPS = Europe/Berlin**, adica 10, 13, 16, 19, 22 ora Romaniei. Cinci rulari pe zi, serializate cu `flock -n` (o rulare care depaseste 3 ore sare urmatorul slot in loc sa se suprapuna).

Ultima rulare de cron din jurnal: **29-iun 12:00:01, terminata 12:36:51**.

### Verificarea de prerechizite, punct cu punct (masurat azi, 15-sep-2026)

| Verificare | Rezultat |
|---|---|
| `cs_backlog.sh` exista | DA, 910 octeti, `-rwxr-xr-x`, mtime 2026-07-02 10:20:24 |
| `RICHPANEL_MCP_TOKEN` in `.env` | PREZENT, 16 caractere |
| `OPENAI_API_KEY` in `.env` | PREZENT, 164 caractere |
| `DATABASE_URL_METRICS` in `.env` | PREZENT, 75 caractere |
| `.env` protejat | `-rw-------`, root, 6.132 octeti |
| `.venv/bin/python3` | simlink la `/usr/bin/python3`, Python **3.11.2** |
| `pg8000` (cerut de `--ground`) | **1.31.5**, importa OK |
| `sqlite3` | OK, SQLite 3.40.1 |
| `data/profitability.db` | 722 MB, reimprospatat azi 12:00 |
| `profit_orders` | **843.459** randuri, **804.057** cu AWB |
| conexiune `DATABASE_URL_METRICS` | **OK** (`scraper@38.242.226.83/metrics`) |
| conexiune `DATABASE_URL_AWBPRINT` | **OK** (acelasi host) |
| `import cs_photo` | rezolva la `/root/Scripturi/cs_photo.py` (directorul scriptului e `sys.path[0]`), `client_photos_block` si `ad_block` prezente, sha identic cu git |
| `cs_auto_draft.py --help` | ruleaza curat, toate flagurile listate, zero eroare de import sau sintaxa |
| model `gpt-4o-mini` | **EXISTA** (`GET /v1/models/gpt-4o-mini` -> HTTP 200) |
| model `gpt-4o` | EXISTA |
| model `claude-sonnet-4-6` | EXISTA (in lista de 11 modele a contului) |
| model `claude-haiku-4-5` | **NU EXISTA** ca alias; exista doar `claude-haiku-4-5-20251001` |
| `.learned_playbook.md` | **LIPSESTE** |
| `cs_procedures.py` | **LIPSESTE** |
| `cs_actions.py` | **LIPSESTE** (deci `--approve` nu poate executa nimic) |
| tokenul Richpanel valid live | **NEVERIFICAT, deliberat** (60 cereri/min partajate cu CS-ul care lucreaza) |

### Dry-run real al grounding-ului, ZERO apeluri LLM si ZERO Richpanel

Am importat modulul cu `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` si `RICHPANEL_MCP_TOKEN` scoase din mediu, si am chemat direct `lookup_orders()`:

```
modul incarcat OK; PROFIT_DB = /root/Scripturi/data/profitability.db | exists: True

-- dupa NUMAR DE COMANDA --
[{'o':'EST000002','total':110.0,'fin':'PAID','date':'2026-08-31','brand':'EST',
  'deliv':'Livrata','awb':'00000000001','courier':'dpd-ro','skus':'168; 96; 73'}]

-- dupa AWB (WISMO fara numar de comanda) --
[{'o':'EST000002','deliv':'Livrata','awb':'00000000001','courier':'dpd-ro','skus':'168; 96; 73'}]

-- fara nicio ancora --
[]
```

**Grounding-ul functioneaza azi, cap-coada.** Ambele cai (nume de comanda si AWB) rezolva comanda reala cu status de livrare si curier. Fara ancora returneaza gol curat, nu crapa.

Atentie la un detaliu care m-a pacalit pe mine intai: parola din `DATABASE_URL_METRICS` e percent-encodata. Codul face `urllib.parse.unquote()` pe user si parola (`lookup_orders`, in jurul liniei 640). Un test scris fara `unquote` da `password authentication failed for user "scraper"` si te face sa crezi gresit ca a murit credentialul.

### Raspunsul la intrebare

**Da, ar rula.** Fisierul porneste, toate cheile sunt la locul lor, modelul cerut de `cs_backlog.sh` exista, ambele baze de date raspund, grounding-ul e verificat cu date reale. Trei rezerve:

1. Ar rula **fara playbook-ul invatat** (`.learned_playbook.md` lipseste, `load_playbook()` returneaza `{}` tacut). Asta a fost adevarat si pentru toate cele 2.642 de drafturi scrise pana acum.
2. Versiunea aflata pe VPS a rulat in productie **86 de secunde in toata viata ei** (4 drafturi, 3-iul 11:41:28 - 11:42:54). `--fast-triage` nu e validat la volum.
3. Tokenul Richpanel nu a fost testat live.

### 5. Ce a produs ultima oara: jurnalul cs_backlog.log

Fisier: `/root/Scripturi/data/cs_backlog.log`, **12.084.334 octeti**, ultima scriere **2026-07-02 11:36:02**.

25 de marcaje `RUN`, 24 cu `DONE`. Prima: 24-iun 15:39:59. Ultima: 2-iul 10:00:06, fara `DONE`.

### Tabelul complet, rulare cu rulare

| Start | End | Drafturi | Invalide | Triaj esuat | Sarite | Spam | Escaladate |
|---|---|---:|---:|---:|---:|---:|---:|
| 24-iun 15:39:59 | 24-iun 18:42:19 | **1.336** | 0 | 1 | 2 | 222 | 174 |
| 24-iun 21:00:01 | 24-iun 21:43:03 | 0 | 120 | 311 | 1.336 | 191 | 0 |
| 25-iun 09:00:01 | 25-iun 09:41:24 | 0 | 138 | 331 | 1.317 | 193 | 0 |
| 25-iun 12:00:01 | 25-iun 12:37:40 | 0 | 129 | 323 | 1.083 | 194 | 0 |
| 25-iun 15:00:01 | 25-iun 15:44:07 | 0 | 183 | 381 | 1.317 | 198 | 0 |
| 25-iun 18:00:01 | 25-iun 18:45:09 | 0 | 174 | 360 | 1.316 | 186 | 1 |
| 25-iun 21:00:01 | 25-iun 21:43:42 | 0 | 207 | 411 | 1.316 | 204 | 1 |
| 26-iun 09:00:01 | 26-iun 09:45:04 | 0 | 228 | 435 | 1.303 | 207 | 1 |
| 26-iun 12:00:01 | 26-iun 12:44:30 | 0 | 246 | 460 | 1.303 | 214 | 1 |
| 26-iun 15:00:01 | 26-iun 15:47:02 | 0 | 267 | 483 | 1.300 | 215 | 1 |
| 26-iun 18:00:01 | 26-iun 18:46:57 | 0 | 276 | 490 | 1.293 | 213 | 1 |
| 26-iun 21:00:01 | 26-iun 21:46:40 | 0 | 284 | 500 | 1.293 | 215 | 1 |
| 27-iun 09:00:02 | 27-iun 09:45:34 | 0 | 291 | 510 | 1.271 | 218 | 1 |
| 27-iun 12:00:01 | 27-iun 12:46:36 | 0 | 293 | 513 | 1.271 | 219 | 1 |
| 27-iun 15:00:01 | 27-iun 15:43:50 | 0 | 296 | 515 | 1.240 | 218 | 1 |
| 27-iun 18:00:01 | 27-iun 18:45:48 | 0 | 299 | 520 | 1.241 | 220 | 1 |
| 27-iun 21:00:01 | 27-iun 21:43:50 | 0 | 300 | 523 | 1.234 | 222 | 1 |
| 28-iun 09:00:01 | 28-iun 09:42:07 | 0 | 309 | 528 | 1.160 | 218 | 1 |
| 28-iun 12:00:01 | 28-iun 12:43:55 | 0 | 317 | 539 | 1.161 | 221 | 1 |
| 28-iun 15:00:01 | 28-iun 15:44:46 | 0 | 325 | 550 | 1.161 | 224 | 1 |
| 28-iun 18:00:01 | 28-iun 18:44:52 | 0 | 334 | 563 | 1.161 | 228 | 2 |
| 28-iun 21:00:01 | 28-iun 21:45:35 | 0 | 338 | 569 | 1.161 | 230 | 2 |
| 29-iun 09:00:01 | 29-iun 09:43:15 | 0 | 350 | 587 | 1.143 | 236 | 2 |
| 29-iun 12:00:01 | 29-iun 12:36:51 | 0 | 290 | 531 | 804 | 240 | 2 |
| 2-iul 10:00:06 | **NICIODATA** | 0 | 21 | 26 | 20 | 5 | 0 |
| **TOTAL** | | **1.336** | **6.015** | **10.960** | **28.207** | **5.151** | **197** |

### Ce spune tabelul

**O singura rulare a produs vreodata drafturi prin acest jurnal.** Prima, din 24-iun 15:39:59, pornita manual, cu `--lean` (contextul din jurnal spune `(lean - fara context 360)`): 1.336 drafturi, 174 escaladari, 121 de cazuri de `NICIO actiune` pe comanda ambigua, exact **1** esec de triaj.

**Urmatoarele 23 de rulari, toate de cron, au produs zero.** Cauza e una singura si e in jurnal, textual:

```
(eroare LLM: [Errno 2] No such file or directory: 'uv')     6.009 aparitii
triaj LLM esuat ... : 'uv'                                 10.933 aparitii
```

`secret()` facea `subprocess.run(["uv", "run", KB, "secret-get", k])` pentru `ANTHROPIC_API_KEY`. `uv` e la `/root/.local/bin/uv`, care **nu e in PATH-ul cronului**. Rularea 1 a mers pentru ca a fost pornita dintr-un shell interactiv, care il are. Din momentul in care a trecut pe cron, fiecare apel LLM a crapat.

**Garda anti-gunoi si-a facut treaba impecabil.** Cele 6.015 drafturi invalide au fost **respinse, nu scrise**: `"draft invalid (eroare LLM / gol) -> NU salvez (sar tichetul)"`. Am verificat explicit - **zero** drafturi care incep cu `(eroare LLM` au fost salvate in Richpanel din acest jurnal. Fara garda, ar fi fost 6.015 mesaje de gunoi in inboxul CS.

**Rularea 25 (2-iul 10:00:06)** e prima cu `--ground` (jurnalul arata `grounded - N comenzi gasite in DB`). Eroarea `uv` a disparut complet. In locul ei: **21 de `HTTP Error 429: Too Many Requests`** de la OpenAI plus 26 de esecuri de triaj tot pe 429. Rularea a murit la tichetul 46 din 464 pe canalul email, jurnalul se opreste la 11:36:02, fara `DONE`.

**Cele 5.151 de tichete de spam nu au fost inchise niciodata.** `cs_backlog.sh` nu foloseste `--close-spam`, deci jurnalul repeta de 5.151 de ori `"ar inchide+arhiva (ruleaza cu --close-spam ca sa le inchizi)"`. Sunt doar excluse din draft, la fiecare rulare.

### Drafturi pe canal (rularea 1, singura productiva)

| Canal | Drafturi |
|---|---:|
| email | 612 |
| messenger | 449 |
| facebook_message | 233 |
| email_from_widget | 42 |
| instagram_message | 0 |

Debit: 5 canale in 3 ore si 2 minute pentru 1.336 drafturi, aproximativ **7,5 secunde per draft** pe gpt-4o-mini cu `--sleep 0.5`.

### De ce s-a oprit

Cronul a fost pus pe pauza pe 29-iun dupa auditul adversarial pe 75 de drafturi reale (71% cu probleme, 18 de severitate mare). Dar jurnalul insusi arata un al doilea motiv, independent, pe care nimeni nu l-a semnalat la timp: **sistemul rulase deja 5 zile producand exclusiv erori**, si nimic nu s-a plans. Nu exista linie de rezumat la finalul rularii, nu exista heartbeat, si `data_health.py` nu urmareste acest job.

### 6. Al doilea wrapper, nedocumentat: cs_draft_all.sh

Asta nu e in brief si nu e in SKILL.md, dar a scris **jumatate din tot ce exista in Richpanel de la sistemul asta**.

Fisiere: `/root/Scripturi/cs_draft_all.sh` si `/root/Scripturi/cs_draft_all.sh.bak-0703`, ambele mtime 3-iul 11:27:55. Jurnal: `/root/Scripturi/data/cs_draft_all.log`, 1.994.232 octeti, ultima scriere **2026-07-03 11:42:54**. `crontab -l | grep -c cs_draft_all` = **0**, deci pornit exclusiv manual.

### Continutul actual

```bash
#!/bin/bash
cd /root/Scripturi || exit 1
export RICHPANEL_MCP_TOKEN="$(grep -m1 ^RICHPANEL_MCP_TOKEN= .env | cut -d= -f2-)"
export OPENAI_API_KEY="$(grep -m1 ^OPENAI_API_KEY= .env | cut -d= -f2-)"
export DATABASE_URL_METRICS="$(grep -m1 ^DATABASE_URL_METRICS= .env | cut -d= -f2-)"
export ANTHROPIC_API_KEY="$(grep -m1 ^ANTHROPIC_API_KEY= .env | cut -d= -f2-)"
export ANTHROPIC_MODEL=claude-haiku-4-5 DRAFT_MODEL=gpt-4o-mini PYTHONUNBUFFERED=1
for ch in email facebook_message messenger email_from_widget instagram_message; do
  .venv/bin/python3 cs_auto_draft.py --channel "$ch" --limit 5000 --scan 9000 \
      --create-draft --ground --skip-tagged --no-comments --fast-triage --tag ai-draft --sleep 0.3
done
```

Diferenta fata de `.bak-0703`: varianta veche NU seta `ANTHROPIC_MODEL` (deci cadea pe `claude-sonnet-4-6`), **avea `facebook_feed_comment` si `instagram_comment` in bucla**, nu avea `--no-comments` si nici `--fast-triage`, si folosea `--sleep 0.5`.

Diferenta critica fata de `cs_backlog.sh`: **acesta exporta `ANTHROPIC_API_KEY`**. In `llm()`, prima verificare e `ak = secret("ANTHROPIC_API_KEY")`; daca exista, se merge pe Claude si `DRAFT_MODEL=gpt-4o-mini` nu mai conteaza deloc.

### Ce a produs

| Pas | Pornit | Terminat | Drafturi | Invalide | Triaj esuat | Escaladari |
|---|---|---|---:|---:|---:|---:|
| `DRAFT CLAUDE` | 2-iul 11:36:58 | niciodata | 0 | 0 | 0 | 0 |
| `DRAFT ALL-OPEN` | 2-iul 11:36:58 | niciodata | **1.302** | 321 | 357 | 154 |
| `DRAFT REAL-SUPPORT (Haiku + fast-triage)` | 3-iul 11:41:28 | niciodata | **4** | 0 | 0 | 0 |

**Pasul `DRAFT ALL-OPEN`, pe canale:** email 85, **facebook_feed_comment 1.217**.

**Motoare folosite:** `claude` 1.327, `sablon-sigur` 9, `claude+corectat` 5, esuate 322.

**Erori:** `HTTP Error 400: Bad Request` de 321 de ori, `HTTP Error 529` o data.

### Trei lucruri de retinut de aici

**(1) 1.217 drafturi sunt pe comentarii publice Facebook.** Decizia de proces e ca la comentarii NU se drafteaza. Varianta `.bak-0703` a wrapperului avea totusi canalele de comentarii in bucla. Richpanel nu are API de stergere a schitelor, deci sunt acolo pana le sterge cineva manual din interfata.

**(2) Cele 321 de 400 Bad Request s-au pierdut definitiv, fara nicio reincercare.** `_llm_http` reincearca doar pe 429, 500, 502, 503 si 504. Un 400 se ridica imediat. Am cautat un semnal de continut in tichetele cazute si **nu l-am gasit**: nu difera semnificativ de cele reusite dupa `(no message)`, poze sau problema goala. Cad intr-o rafala continua incepand cu tichetul `[324/457]`, ceea ce seamana mai degraba a conditie de server sau depasire de context decat a proprietate de tichet. Nu stiu cauza. Nici `529` (Anthropic overloaded) nu e in lista de reincercari.

**(3) Versiunea aflata acum pe VPS a rulat 86 de secunde.** Pasul `DRAFT REAL-SUPPORT` din 3-iul 11:41:28 e singura rulare a fisierului `b5f4742` in productie: 4 drafturi, si jurnalul se opreste la 11:42:54. Cine reporneste sistemul reporneste **cod nevalidat la volum**.

**Bonus, verificat azi:** `claude-haiku-4-5` (aliasul) nu mai e in lista de modele a contului. Exista doar `claude-haiku-4-5-20251001`. Wrapperul asta, rulat azi, ar cadea pe fiecare apel Anthropic.

### 7. Ce a ramas scris in Richpanel

Masurat exclusiv din oglinzi LOCALE, zero apeluri API.

### Ce NU pot masura oglinzile, si de ce

**`/root/Scripturi/data/cs_mirror.db`** (47,6 MB, reimprospatat azi la 03:00). Tabelul `rp_ticket` are intr-adevar `tag_names` cu **nume**, dar are 12.234 randuri si `created_at` intre **2026-08-27 si 2026-09-15**. E o fereastra rulanta de trei saptamani. Numaratoare pe jeton exact:

| Tag | Aparitii |
|---|---:|
| ai-draft | 0 |
| ai-live | 0 |
| ai-draft-v2 | 0 |
| ai-sent | 0 |
| escaladare | 0 |
| esc-high | 0 |
| esc-urgent | 0 |
| de-sunat | 0 |
| spam | 0 |

Asta demonstreaza doar ca niciun tichet creat dupa 27-aug nu poarta tag AI - ceea ce e de asteptat, sistemul e oprit. **Nu spune nimic despre iunie si iulie.** Capcana: `WHERE tag_names LIKE '%escaladare%'` da 4, dar acelea sunt `flag-escaladare`, scrise de pipeline-ul CS care ruleaza azi (`run_cs_pipeline.py`), nu de sistemul de auto-draft.

**`/root/Scripturi/data/richpanel_tickets.db`** (478 MB, actualizat azi 12:05). 255.767 tichete, 2024-10-15 pana azi. Dar coloana `tags` contine **UUID-uri de tag, nu nume**, si e un instantaneu de la momentul pull-ului.

### Ce se poate masura: numerele de tichet din jurnale

Am extras din cele doua jurnale numerele de conversatie unde apare `DRAFT salvat` si le-am cautat in oglinda istorica:

| Lot | Tichete cu draft scris | Regasite | OPEN azi | CLOSED azi |
|---|---:|---:|---:|---:|
| 24-iun (`cs_backlog.log`) | 1.336 | 1.335 | **1.001** | 334 |
| 2-3 iul (`cs_draft_all.log`) | 1.306 | 1.306 | **1.288** | 18 |
| **Total unic** | **2.642** | 2.641 | **2.289** | **352** |

Suprapunerea intre cele doua loturi e **zero** - `--skip-tagged` a functionat corect.

Canalele lotului din 24-iun: email 611, messenger 449, facebook_message 233, email_from_widget 42.

### Cate mai poarta tagul ai-draft

UUID-ul `ea60dd56-5fd9-4952-85ae-c3ea5f3b33ce` apare pe **979** din tichetele lotului 24-iun si pe **418** din lotul de iulie, in total **1.397**, din **1.418** aparitii ale sale in intreg istoricul de 255.767 tichete. Concentratie **98,5%** pe tichetele noastre.

Asta e aproape sigur tagul `ai-draft`. Precizare onesta: e o **deductie prin concentratie**, nu o cautare pe nume. Am construit o harta UUID->nume din suprapunerea celor doua oglinzi (12.217 tichete comune) si am reusit sa denumesc 11 taguri, dar acest UUID nu apare deloc in fereastra august-septembrie, deci nu are cum sa fie in harta. Se confirma cu un singur `list_tags` live.

**Deci: cel putin 1.397 de tichete mai poarta tagul, din 2.642 scrise.** E o limita inferioara - instantaneul nu avea niciun tag inregistrat pentru 342 din lotul de iunie si 667 din cel de iulie.

### Drafturi de eroare si dubluri

In **ambele** jurnale de pe VPS, numarul de drafturi care incep cu `(eroare LLM` si au fost totusi **salvate** este **0**. Garda le-a respins pe toate 6.015 plus 321.

Cele aproximativ 13 drafturi `(eroare LLM)` si dublurile din memorie vin din **experimentele cu executie paralela din 24-iun**, care au rulat inainte de wrapper si nu au lasat jurnal pe VPS. Nu le pot cuantifica din date locale. Se confirma doar deschizand tichetele in interfata Richpanel.

Richpanel **nu are `delete_draft` si nu are `delete_note`** (toate cele 21 de metode ale serverului au fost verificate prin `tools/list`, conform memoriei). Iar `create_draft` **adauga**, nu suprascrie - doua rulari pe acelasi tichet lasa doua schite. Curatarea e exclusiv manuala, din interfata.

### Scrieri care schimba starea tichetului, nu doar adauga o schita

**328 de tichete au fost escaladate**: 174 in 24-iun, 154 in 2-iul. La fiecare, sistemul a facut trei lucruri ireversibile prin API: a setat `priority = HIGH`, a atasat tagurile `escaladare` plus `esc-high` sau `esc-urgent`, si a adaugat o **nota privata** cu brieful (problema, client si telefon, comanda si AWB, unde a mai scris, sentiment, actiune sugerata). Notele nu se pot sterge prin API.

### Context: cat de mare e coada azi

Din `cs_mirror.db`, fereastra 27-aug - 15-sep: **1.248 tichete OPEN** in total.

| Canal | OPEN | CLOSED |
|---|---:|---:|
| facebook_feed_comment | 492 | 6.650 |
| email | 323 | 3.127 |
| messenger | 221 | 81 |
| facebook_message | 133 | 711 |
| email_from_widget | 51 | 172 |
| instagram_comment | 23 | 157 |
| instagram_message | 0 | 88 |

### 8. Fisierul .auto_draft_proposals.json

### Pe VPS

`/root/Scripturi/.auto_draft_proposals.json`, **7.286.865 octeti**, mtime **2026-07-03 11:28:45**. Scris de rularea `DRAFT ALL-OPEN` din 2-iul (fisierul se rescrie pe masura ce coada creste).

Forma: un dictionar cu cheia = numarul conversatiei. **3.156 intrari**, numere de la **254121** la **280172**. Fiecare intrare:

```json
{
  "cid": "<id-ul conversatiei in Richpanel>",
  "draft": "textul integral al schitei",
  "cmd": null,              // comanda cs-actions, daca exista o actiune executabila
  "hide": null,             // propunerea de moderare pe comentariu (mode: hide)
  "ctx": "contextul complet trimis LLM-ului",
  "action_desc": "...",
  "cat": "problema_produs",
  "order": "...",
  "store": "Casa Ofertelor",
  "escalate": true
}
```

### Ce contine, numarat

| Masura | Valoare |
|---|---:|
| Intrari | 3.156 |
| Cu `applied = true` | **0** |
| Cu `cmd` nenul (actiune chiar executabila) | **9** |
| Cu propunere `hide` | **31** |
| Marcate `escalate` | 328 |

Pe categorie: altele 1.119, comentariu_social 819, presale_intrebare 302, recenzie_feedback 234, livrare_wismo 195, problema_produs 133, retur 114, anulare 105, comanda_noua 60, modificare_comanda 37, schimb_swap 17, plata_factura 15, refuz_livrare 5.

Pe magazin: Esteban 665, **`magazinul nostru` 554**, Magdeal 366, Ofertele Zilei 292, Reduceri bune 206, Bonhaus 142, Nubra 135, Casa Ofertelor 116, Grandia 113, Belasil 98, Bonhaus CZ 93, George Talent 91.

Cele 554 de `magazinul nostru` sunt **17,6% din coada** unde brandul nu s-a rezolvat - exact gaura pe care `brand_from_email()` trebuia sa o acopere. Acele drafturi s-au semnat `echipa noastra` in loc de numele magazinului.

### Copia de pe Mac e alta, si mai veche

`plugins/gigi/skills/cs-draft-reply/.auto_draft_proposals.json`, **1.518.500 octeti**, mtime **2026-06-29 17:06:35**, **516 intrari**, 1 cu `cmd`, si moduri de `hide` care nu mai exista in cod: `public_and_private` 47, `unhide` 5, `hide` 4, `private_reply` 2. Precede decizia din 23-iun de a renunta complet la DM-uri.

### Mai sunt valabile?

**Nu.** Trei motive:

1. **Varsta.** 74 de zile. Din tichetele pe care le-am putut verifica, 352 sunt deja CLOSED - clientul a fost servit intre timp de un om.
2. **Contextul e inghetat.** Campul `ctx` contine statusuri de comanda si de livrare din inceputul lui iulie. Un draft care spune `comanda e in tranzit` descrie o realitate de acum doua luni si jumatate.
3. **Nu pot fi executate oricum.** Singurul lucru replayabil sunt cele 9 intrari cu `cmd`, prin `--approve`. Dar `cs_actions.py` **nu exista pe VPS**, deci calea de aplicare e rupta acolo.

### Ce ramane util

Fisierul e un **artefact de audit excelent**: 3.156 de perechi context-plus-draft reale, cu categorie, magazin si marcaj de escaladare. E cea mai buna baza de regresie pe care o are sistemul, daca cineva vrea sa masoare efectul unei schimbari de prompt fara sa cheltuie bani pe LLM si fara sa atinga Richpanel.

**Atentie la PII**: contine drafturi complete plus email, telefon si context de comanda pentru mii de clienti. E deliberat in `.gitignore`-ul skillului, langa `.voice_pack.json` si `.learned_playbook.md`. Nu urca nicaieri, nu-l pune pe NAS.

### 9. RUNBOOK: daca vrei sa repornesti sistemul

Ordinea conteaza. Pasii 1-4 nu ating nimic; primul pas care scrie in Richpanel e 7.

---

### Pas 0. Decide ce faci cu cele 2.642 de schite deja scrise

Inainte de orice cod. 2.289 de tichete sunt inca OPEN si au o schita AI neatinsa, dintre care **1.217 pe comentarii publice Facebook**, scrise contrar deciziei de proces. Cu ownerul, alege: se sterg manual din interfata, sau se lasa si agentii sunt instruiti sa le ignore. **Nu exista API de stergere.** Daca repornesti fara sa decizi, adaugi peste ele.

### Pas 1. Adu copia plata la nivelul git

Ambele fisiere sunt deja pe aceeasi masina.

```bash
ssh root@84.46.242.181
cd /root/Scripturi
# verifica intai ca clona e curata si actuala
git -C team-intelligence pull --ff-only
sha256sum team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py
# trebuie sa fie 57631aebe2f27af13b246c592c33e8d561554a5c2eb8c2ce1edd82d4500de439

cp cs_auto_draft.py cs_auto_draft.py.bak-$(date +%Y%m%d-%H%M%S)
cp team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py cs_auto_draft.py

# verifica: singura schimbare trebuie sa fie linia Lab Noir
grep -c 'Lab Noir' cs_auto_draft.py     # 3
.venv/bin/python3 cs_auto_draft.py --help >/dev/null && echo OK
```

Nu pierzi nimic - git e superset strict. Copiaza si `cs_photo.py` daca s-a schimbat (azi e identic).

### Pas 2. Pune playbook-ul invatat

Fara el, drafturile ies fara procedurile si fara vocea invatate din tichetele reale. Genereaza local cu `gigi:cs-procedures`:

```bash
# pe Mac, in folderul skillului
PROC_MODEL=gpt-4o uv run cs_procedures.py --category all --out .learned_playbook.md
scp .learned_playbook.md root@84.46.242.181:/root/Scripturi/
```

Verifica pe VPS ca se incarca:
```bash
.venv/bin/python3 -c "import sys; sys.path.insert(0,'/root/Scripturi'); \
import cs_auto_draft as m; p=m.load_playbook(); print('categorii:', len(p), list(p)[:5])"
```
Daca da `categorii: 0`, fisierul nu e unde trebuie sau nu are antete `## CATEGORIE`.

### Pas 3. Verifica prerechizitele (toate au trecut azi, 15-sep)

```bash
cd /root/Scripturi
for k in RICHPANEL_MCP_TOKEN OPENAI_API_KEY DATABASE_URL_METRICS; do
  v=$(grep -m1 "^$k=" .env | cut -d= -f2-); [ -n "$v" ] && echo "$k OK (${#v})" || echo "$k LIPSA"
done
.venv/bin/python3 -c "import pg8000, sqlite3; print('deps OK')"
ls -la data/profitability.db
```

Dry-run de grounding, fara LLM si fara Richpanel:
```bash
set -a; . ./.env; set +a
unset ANTHROPIC_API_KEY OPENAI_API_KEY RICHPANEL_MCP_TOKEN
.venv/bin/python3 -c "
import sys; sys.path.insert(0,'/root/Scripturi')
import cs_auto_draft as m
print(m.lookup_orders(None, None, order_names=['EST000002']))"
```
Trebuie sa intoarca o comanda cu `deliv`, `awb` si `courier`. Daca intoarce `[]`, grounding-ul e mort si **NU ai voie sa repornesti** - asta e exact conditia care a produs halucinarile din iunie.

### Pas 4. Verifica modelul

```bash
set -a; . ./.env; set +a
curl -s -o /dev/null -w "%{http_code}\n" https://api.openai.com/v1/models/gpt-4o-mini \
  -H "Authorization: Bearer $OPENAI_API_KEY"     # trebuie 200
```
Daca vrei sa mergi pe Claude, verifica intai ca aliasul exista in cont: `claude-haiku-4-5` **nu mai exista**, doar `claude-haiku-4-5-20251001`.

### Pas 5. O rulare de proba, FARA scriere

Alege 20 de tichete si ruleaza **fara** `--create-draft`. Drafturile se genereaza si se afiseaza, dar nu ajung nicaieri.

```bash
cd /root/Scripturi
export RICHPANEL_MCP_TOKEN="$(grep -m1 ^RICHPANEL_MCP_TOKEN= .env | cut -d= -f2-)"
export OPENAI_API_KEY="$(grep -m1 ^OPENAI_API_KEY= .env | cut -d= -f2-)"
export DATABASE_URL_METRICS="$(grep -m1 ^DATABASE_URL_METRICS= .env | cut -d= -f2-)"
export DRAFT_MODEL=gpt-4o-mini PYTHONUNBUFFERED=1
.venv/bin/python3 cs_auto_draft.py --channel email --limit 20 --scan 200 \
    --ground --no-comments --fast-triage --json --sleep 0.8 2>&1 | tee /tmp/proba.txt
```

Asta costa bani reali (circa 40 de apeluri LLM) dar **nu scrie nimic in Richpanel**. E singura forma de test onest.

### Pas 6. Treci drafturile prin poarta de gramatica

```bash
# ia ultima linie de dupa marcajul @@JSON@@
sed -n 's/.*@@JSON@@//p' /tmp/proba.txt > /tmp/proba.json
uv run grammar_audit.py --file /tmp/proba.json
```
Auditul e multilingv si compara limba raspunsului cu mesajul real al clientului. Verifica manual, in plus:
- niciun draft nu afirma un status de comanda pe care nu il are in context
- niciun draft nu confirma o actiune ca facuta fara `ACTIUNE_APLICATA` in context
- pe comentariile publice, niciun nume de client
- registrul e formal, la `dumneavoastra`
- semnatura poarta numele magazinului, nu `echipa noastra`

**Daca ceva nu e curat, opreste-te aici.** Fiecare corectie de prompt face stale schitele deja salvate, iar `create_draft` adauga in loc sa suprascrie. Finalizeaza promptul intai, scrie o singura data.

### Pas 7. Prima scriere reala, pe un lot mic

```bash
.venv/bin/python3 cs_auto_draft.py --channel email --limit 20 --scan 200 \
    --create-draft --ground --no-comments --fast-triage --skip-tagged \
    --tag ai-draft --sleep 0.8
```
Deschide 3-4 tichete in interfata Richpanel si uita-te la schite cu ochii tai.

### Pas 8. Abia acum, cronul

```bash
crontab -e
# la linia 45, sterge prefixul "# PAUZAT 2026-06-29 " si lasa:
# 0 9-21/3 * * * /usr/bin/flock -n /tmp/cs_backlog.lock /root/Scripturi/cs_backlog.sh >> /root/Scripturi/data/cs_backlog.log 2>&1
```
Orele sunt in ora VPS (Berlin) = 10, 13, 16, 19, 22 ora Romaniei.

### Pas 9. Supravegheaza prima rulare de cron

Asta e pasul care a lipsit in iunie si a costat 5 zile.

```bash
tail -f /root/Scripturi/data/cs_backlog.log
```
Dupa prima rulare completa:
```bash
tail -20000 /root/Scripturi/data/cs_backlog.log | grep -c 'DRAFT salvat'
tail -20000 /root/Scripturi/data/cs_backlog.log | grep -o '(eroare LLM: [^)]*)' | sort | uniq -c
```
**Daca numarul de drafturi salvate e 0, opreste imediat cronul.** Exact asta a rulat 23 de ori la rand fara ca nimeni sa observe.

### 10. Capcanele, in ordinea pagubelor pe care le-au facut

### 1. `create_draft` ADAUGA, si nu exista stergere

Richpanel nu are `delete_draft` si nu are `delete_note` (toate cele 21 de metode ale serverului verificate prin `tools/list`). A doua rulare pe acelasi tichet lasa **doua** schite. `--skip-tagged` e singura protectie, si depinde de tagul din `--tag`: daca schimbi tagul, `--skip-tagged` nu mai recunoaste vechile tichete si le drafteaza din nou. Asa au aparut dublurile. Curatarea e exclusiv manuala, din interfata.

**Regula:** cand iterezi pe prompt, ruleaza **fara** `--create-draft`. Scrie o singura data, la final.

### 2. `uv` nu e in PATH-ul cronului

Cauza celor 23 de rulari sterile. `secret()` facea `subprocess.run(["uv", ...])`; `uv` e la `/root/.local/bin/uv`, absent din PATH-ul minimal al cronului. Reparat pe 29-iun cu `try/except` plus cache, dar tiparul se repeta: orice binar chemat din cron are nevoie de cale absoluta. Pe VPS mai exista cinci cronuri care au murit exact asa (`uv: not found`, tacut, din 14-aug).

**Corolar pe care trebuie sa-l stii:** `KB` se rezolva pe VPS la `/core/scripts/kb.py`, care nu exista. Deci `secret()` nu poate lua **niciodata** nimic din KB acolo. Toate cheile trebuie sa fie in `/root/Scripturi/.env`.

### 3. `--lean` inseamna fara date, si fara date modelul inventeaza

Toate cele 1.336 de drafturi din 24-iun au fost `--lean`, adica fara context 360. Auditul adversarial de pe 29-iun a gasit 71% cu probleme si 18 grave, majoritatea halucinari: dimensiuni, preturi, telefoane si statusuri inventate, plus `am verificat, nu am gasit` fara sa fi cautat nimic. Raspunsul a fost dublu: regula anti-halucinare explicita in prompt, plus post-filtrul determinist `HALLU` care regenereaza o data cu corectiv si, daca modelul tot fabrica, pune un sablon sigur pe categorie. Filtrul e activ **doar** cand `not has_order_data(od_ctx)`.

**Nu reporni pe `--lean`.** Foloseste `--ground`, si verifica intai cu dry-run-ul de la pasul 3 ca chiar gaseste comenzi.

### 4. `publish-skill` face `git checkout main` si arunca working tree-ul

S-a intamplat, o data, si se vede in istoria git: PR #276 (26-iun 10:20:42) a adaugat 18 linii, PR #277 (26-iun 10:24:54) a readus fisierul byte-identic la starea de la PR #232. Blocul `ANPC_RE` plus `ANPC_REPLY` - raspunsul juridic verbatim la sesizarile ANPC/OPC - **nu s-a mai intors niciodata**, nici in HEAD, nici pe VPS.

**Regula:** commit inainte de orice scp pe VPS, si un `grep` de verificare dupa publicare.

### 5. Limita de rata a OpenAI a fost gatul, nu Richpanel

Pe 2-iul, un singur worker cu `--sleep 0.5` a luat 21 de 429-uri si a murit la tichetul 46 din 464. Memoria noteaza configuratia curata gasita empiric: **2 workeri (4 cereri OpenAI simultane), lean, fara tag, `--sleep 0.8`** = 0 erori, 0 gunoi, 0 tichete sarite. 3 workeri pornesc curat dar degradeaza. 4 sau mai multi = furtuna de 429 si tichete sarite.

Richpanel e si el strans: da 429 chiar si la **listarea** paginilor, nu doar la scriere, deci paralelizarea pe mai multi workeri e exclusa din start.

### 6. `_llm_http` nu reincearca pe 400 si nici pe 529

Reincearca doar 429, 500, 502, 503, 504. Un 400 se ridica instant. Asa s-au pierdut 321 de drafturi consecutive pe 2-iul, intr-o singura rafala.

### 7. `--tag` gol rupe `--skip-tagged`

`add_tags` sare cand tagul e gol. Daca rulezi cu `--tag ""` pentru volum, pierzi idempotenta si urmatoarea rulare redrafteaza tot.

### 8. Tagurile Richpanel se ataseaza doar prin UUID, nu prin nume

`add_tags_to_conversation` accepta **doar ID-uri de tag**, nu nume, si nu creeaza taguri noi. Trecerea unui nume esueaza **tacut**. Helperul `tag_id()` din cs_auto_draft rezolva nume la ID prin `list_tags`, creeaza prin `create_tag`, si pune in cache. Inainte de acest fix, escaladarile nu se rutau deloc pentru ca tagurile nu se atasau. `gigi:richpanel-auto-triage` are probabil **acelasi bug** si nu a fost verificat.

### 9. Escaladarea pe LLM mic supra-declanseaza

Comentariul din cod e explicit: gpt-4o-mini escaladeaza aproape orice reclamatie sau WISMO. De aceea exista poarta determinista `_real_escalation()` adaugata in B5. Daca schimbi modelul in ceva mai slab, verifica raportul de escaladari inainte si dupa. Reper: rularea din 24-iun a escaladat 174 din 1.336, adica 13%.

### 10. Driftul git-VPS e invizibil pentru watchdog

`deploy_parity.py check` a raportat azi 10 divergente, niciuna in cs-draft-reply: `discover()` scaneaza doar `plugins/gigi/skills/metrics-cache/scripts/` si `shared/scripturi-tools/`. Driftul de 74 de zile a stat nedetectat exact din acest motiv.

### 11. Jurnalul nu are rezumat, deci esecul e tacut

12 MB de jurnal si nicio linie de totaluri. Ca sa afli ca 23 de rulari au produs zero trebuie sa scrii un parser. `data_health.py` nu urmareste acest job, nu exista heartbeat, nu exista alerta. Singurul motiv pentru care s-a observat e ca cineva a citit drafturile.

### 12. Doua wrappere, doua modele, doua comportamente

`cs_backlog.sh` **nu** exporta `ANTHROPIC_API_KEY` si ramane pe OpenAI. `cs_draft_all.sh` **o exporta** si merge pe Claude, indiferent ce spune `DRAFT_MODEL`. In `llm()`, prima verificare e pe cheia Anthropic. Daca rulezi manual cu `set -a; . .env; set +a`, te muti tacut pe Claude Sonnet, cu alt cost si alt comportament decat cronul.

### 11. Referinta rapida: fisiere, comenzi, flaguri

### Toate fisierele care conteaza

**Git (Mac), `/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/`:**

| Fisier | Octeti | Rol |
|---|---:|---|
| `cs_auto_draft.py` | 93.459 | motorul de backlog, cel important |
| `cs_draft_reply.py` | 13.079 | o singura conversatie |
| `cs_ticket_index.py` | 5.420 | index de precedente din tichete rezolvate |
| `grammar_audit.py` | 5.833 | poarta de limba si gramatica, multilingva |
| `build_voice_pack.py` | 6.300 | construieste `.voice_pack.json` |
| `mcp_server.py` | 3.347 | expune skillul ca server MCP |
| `SKILL.md` | 15.392 | documentatia, la nivelul PR #367 |
| `DEMO.md` | 3.456 | |
| `.learned_playbook.md` | 17.884 | generat de `gigi:cs-procedures`, gitignorat |
| `.auto_draft_proposals.json` | 1.518.500 | 516 propuneri din 29-iun, PII, gitignorat |
| `.voice_pack.json` | 2 | gol |

Skill frate: `plugins/gigi/skills/cs-photo/cs_photo.py` (30.859 octeti, sha `4fb6079ef80ac4e4...`).

**VPS `root@84.46.242.181`, `/root/Scripturi/`:**

| Fisier | Octeti | mtime |
|---|---:|---|
| `cs_auto_draft.py` | 93.425 | 3-iul 11:27:55 |
| `cs_auto_draft.py.bak-20260629-162212` | 78.354 | 29-iun 15:22:12 |
| `cs_auto_draft.py.bak-20260629-163827` | 87.439 | 29-iun 15:38:27 |
| `cs_auto_draft.py.bak-20260629-164236` | 88.998 | 29-iun 15:42:36 |
| `cs_auto_draft.py.bak-20260629-165430` | 89.159 | 29-iun 15:54:31 |
| `cs_auto_draft.py.bak-20260629-170703` | 92.134 | 29-iun 16:07:03 |
| `cs_auto_draft.py.bak-efficiency-0703` | 92.350 | 3-iul 11:27:55 |
| `cs_photo.py` | 30.859 | 29-iun 16:07:04 |
| `cs_backlog.sh` | 910 | 2-iul 10:20:24 |
| `cs_draft_all.sh` | ~1.100 | 3-iul 11:27:55 |
| `cs_draft_all.sh.bak-0703` | ~1.000 | 3-iul 11:27:55 |
| `.auto_draft_proposals.json` | 7.286.865 | 3-iul 11:28:45 |
| `.env` | 6.132 | 28-aug 16:23, `-rw-------` |
| `data/cs_backlog.log` | 12.084.334 | 2-iul 11:36:02 |
| `data/cs_draft_all.log` | 1.994.232 | 3-iul 11:42:54 |
| `data/profitability.db` | 722.767.872 | azi 12:00 |
| `data/cs_mirror.db` | 47.636.480 | azi 03:00 |
| `data/richpanel_tickets.db` | 478.224.384 | azi 12:05 |
| `team-intelligence/` | — | clona git, `git pull --ff-only` la 30 min |

Lipsesc de pe VPS: `.learned_playbook.md`, `.voice_pack.json`, `cs_procedures.py`, `cs_actions.py`, `cs_draft_reply.py`, `grammar_audit.py`.

### Toate flagurile lui cs_auto_draft.py

| Flag | Ce face |
|---|---|
| `--limit N` | cate tichete proceseaza |
| `--channel X` | email, facebook_message, messenger, email_from_widget, instagram_message, facebook_feed_comment, instagram_comment |
| `--scan N` | cate tichete scaneaza ca sa gaseasca `--limit` candidati |
| `--create-draft` | **SCRIE** schita in Richpanel plus ruteaza escaladarile. Fara el = doar afisare |
| `--approve <conv>` | aplica actiunea propusa. Necesita `cs_actions.py`, **absent pe VPS** |
| `--agent <Nume>` | numele agentului pentru `--approve` |
| `--actions a,b` | ce actiuni sunt active; `none` le dezactiveaza pe toate |
| `--sleep S` | pauza intre tichete |
| `--lean` | proces redus, **fara context 360 si fara rutare de escaladare**. Sursa halucinarilor din iunie |
| `--ground` | grounding self-contained: DB metrics plus `profitability.db`, fara SSH si fara uv. **Asta se foloseste** |
| `--skip-tagged` | sare tichetele care au deja tagul din `--tag`. Idempotenta |
| `--no-comments` | exclude complet canalele de comentarii |
| `--fast-triage` | sare apelul LLM de triaj cand regexul e sigur. 1 apel in loc de 2 |
| `--photos` / `--no-photos` | vede pozele clientului. Implicit PORNIT |
| `--apply-send` | **LIVE, IREVERSIBIL**: trimite la client plus inchide tichetul. Nu il folosi |
| `--json` | scoate drafturile structurat, dupa marcajul `@@JSON@@` |
| `--close-jspam` | (`--close-spam`) inchide plus tag `spam` pe tichetele de spam. **Nu e in `cs_backlog.sh`** |
| `--tag T` | tagul pus pe tichetele tratate. Implicit `ai-draft` |
| `--only N,N` | procesare tintita pe numere de conversatie |
| `--send <conv>` | trimite live un singur tichet. Refuza escaladarile si comentariile |

### Variabile de mediu

| Variabila | Efect |
|---|---|
| `RICHPANEL_MCP_TOKEN` | obligatorie. Token de 16 caractere, fara expirare |
| `OPENAI_API_KEY` | folosita daca `ANTHROPIC_API_KEY` lipseste |
| `ANTHROPIC_API_KEY` | **daca exista, are prioritate** si comuta pe Claude |
| `DATABASE_URL_METRICS` | comenzile Shopify pentru `--ground`. Parola e percent-encodata |
| `DRAFT_MODEL` | model OpenAI. Implicit `gpt-4o` in cod, `gpt-4o-mini` in wrappere |
| `ANTHROPIC_MODEL` | model Claude. Implicit `claude-sonnet-4-6` |
| `VISION_MODEL` | model vizual OpenAI. Implicit `gpt-4o-mini` |
| `PYTHONUNBUFFERED=1` | **obligatorie in cron**, altfel jurnalul se scrie tamponat |

### Comenzi de diagnostic, toate read-only

```bash
# paritate git vs flat
ssh root@84.46.242.181 'cd /root/Scripturi && \
  sha256sum cs_auto_draft.py team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py'

# starea cronului
ssh root@84.46.242.181 'crontab -l | grep -n cs_backlog'

# ultima rulare
ssh root@84.46.242.181 'grep -n "^===== " /root/Scripturi/data/cs_backlog.log | tail -5'

# cate drafturi in ultima rulare
ssh root@84.46.242.181 'tail -20000 /root/Scripturi/data/cs_backlog.log | grep -c "DRAFT salvat"'

# erori, pe tip
ssh root@84.46.242.181 "grep -o '(eroare LLM: [^)]*)' /root/Scripturi/data/cs_backlog.log \
  | sort | uniq -c | sort -rn"

# coada de azi, fara niciun apel API
ssh root@84.46.242.181 "python3 -c \"import sqlite3; \
c=sqlite3.connect('file:/root/Scripturi/data/cs_mirror.db?mode=ro',uri=True); \
print(c.execute('select status,channel,count(*) from rp_ticket group by 1,2').fetchall())\""
```

### Skilluri conexe

| Skill | Rol fata de sistem |
|---|---|
| `gigi:cs-procedures` | **hraneste**: genereaza `.learned_playbook.md`, procedura de-facto plus replicile reale ale agentilor, per categorie. Are nevoie de `data/richpanel_tickets.db` |
| `gigi:cs-photo` | **modul importat**: `client_photos_block()` si `ad_block()`. Vede poza clientului si poza reclamei |
| `gigi:cs-360` | context client, comenzi, refuzuri. Calea clasica, prin SSH, ocolita de `--ground` |
| `gigi:cs-actions` | **executa** actiunile la `--approve`. Absent pe VPS |
| `gigi:cs-sentiment` | scor de sentiment per tichet |
| `gigi:cs-quality-audit` | unde s-a raspuns prost, per agent si magazin |
| `gigi:richpanel-auto-triage` | triaj cu tag, categorie, prioritate. Ruleaza azi prin `run_cs_pipeline.py`. **Probabil are acelasi bug de tag prin nume** |
| `gigi:cs-comment-intelligence` | lead-uri si reclamatii din comentariile FB/IG |
| ClickUp doc `2kyqg8j1-3895` | macro-urile CS oficiale, formatul si expresiile canonice. Variabile: `{client}`, `{comanda}`, `{awb}`, `{magazin}`, `{link_retur}` |

**Ce nu se știe (explicit):**

- Numele tagului din spatele UUID-ului ea60dd56-5fd9-4952-85ae-c3ea5f3b33ce. Nu am putut face maparea UUID->nume pentru el: harta pe care am construit-o din suprapunerea celor doua oglinzi acopera doar tagurile folosite intre 27-aug si 15-sep, iar acest tag nu mai e folosit din iulie. Deduc ca e ai-draft din concentrare (1.397 din cele 1.418 aparitii ale sale in intreg istoricul cad pe tichetele in care jurnalele spun ca am scris draft), nu dintr-o cautare pe nume. Se confirma cu un singur list_tags live.
- Cauza exacta a celor 321 de HTTP 400 Bad Request din rularea de pe 2-iul cu claude-sonnet-4-6. Nu am gasit un semnal de continut: tichetele cazute nu difera de cele reusite pe (no message), poze sau problema goala, iar 227 din 378 aveau comenzi: 0 fata de 1.229 din 1.306 la cele reusite. Sunt intr-o rafala continua de la tichetul [324/457], ceea ce sugereaza o conditie de server sau o depasire de context, nu o proprietate a tichetului. Nu stiu.
- Cate drafturi (eroare LLM) si cate dubluri au ramas efectiv in Richpanel din experimentele paralele de pe 24-iun. Memoria vorbeste de ~13 plus dubluri. In cele doua jurnale de pe VPS numarul e 0 (garda anti-gunoi a respins 6.015 si nu a salvat niciunul), fiindca acele experimente au rulat inainte de wrapper si nu au lasat jurnal pe VPS. Nu am de unde sa le numar local.
- Daca tokenul RICHPANEL_MCP_TOKEN (16 caractere, prezent in .env) mai e valid. Nu l-am testat deliberat: limita Richpanel e de 60 de cereri pe minut si e partajata cu CS-ul care lucreaza acum. Se verifica cu un singur list_tags.
- Costul real per rulare. Jurnalele nu inregistreaza tokeni sau bani, iar eu nu am facut niciun apel de generare. Singurul reper: rularea din 24-iun a procesat 5 canale in 3 ore si 2 minute pentru 1.336 drafturi pe gpt-4o-mini, adica circa 7,5 secunde per draft.
- Daca limita OpenAI care a dat 21 de 429-uri pe 2-iul mai e in vigoare. Contul a urcat probabil de tier intre timp, dar nu se poate sti fara o rulare reala.
- De ce cs_auto_draft.py de pe VPS are mtime 3-iul 11:27:55, cu o zi inainte de commitul git care ii contine continutul (fe410875, 4-iul 09:14). Interpretarea mea e ca fisierul a fost scris local, urcat cu scp pe 3-iul si comis abia a doua zi, dar nu am dovada directa a ordinii.
- Daca cele 2.642 de tichete cu draft au fost intre timp atinse manual de agenti (draft sters, retusat, trimis). Instantaneul local imi da doar statusul (2.289 inca OPEN, 352 CLOSED), nu si ce s-a intamplat cu schita.

---

## 5. Calitatea răspunsurilor — ce s-a măsurat

> Sistemul de auto-draft CS a rulat 8 zile (24-iun → 2-iul-2026) si a scris 1.336 de drafturi in Richpanel, TOATE intr-o singura rulare manuala pe 24-iun; cele 24 de rulari de cron care au urmat au produs exact 0 drafturi, fiindca `uv` nu era in PATH-ul cronului (6.009 esecuri identice, tacute). Calitatea a fost auditata in 4 runde in iunie, dar mereu pe esantioane mici de TEXT — nimeni nu a masurat vreodata ce a facut CS-ul cu drafturile. Am masurat azi ambele: pe toata populatia de 1.336, filtrul anti-halucinare din codul de azi prinde 31,0% din drafturile normale (dominant „nu am gasit nicio comanda in sistemul nostru\", o afirmatie fabricata, fiindca rula in `--lean`, fara niciun lookup); iar pe un esantion live de 40 de tichete, 40/40 sunt inchise azi, 38/40 (95%) au fost inchise de agenti umani FARA niciun raspuns, si 0/40 au primit un raspuns care sa semene cu draftul — rata de acceptare masurata este zero. Codul de pe VPS difera de git printr-o SINGURA linie (o pagina FB Lab Noir), deci nu e o divergenta functionala, dar pe VPS lipsesc 5 dependinte: playbook-ul invatat (0 categorii incarcate), cs-actions, customer-identity, kb.py si grammar_audit.py — adica procedurile+vocea invatate din tichete reale nu au ajuns NICIODATA intr-un draft de productie, iar gate-ul de limba nu a fost rulat niciodata pe iesirea reala. Inainte de repornire, cele doua lucruri obligatorii sunt: curatarea drafturilor vechi gresite (nu se pot sterge prin API) si o poarta de EFECT pe un lot mic, nu inca o runda de audit pe text.


### 0. Starea sistemului azi — verificata, nu presupusa

Toate cifrele de mai jos sunt verificate pe 15-sep-2026, prin citire directa.

## Codul: VPS vs git — divergenta e de UN RAND

Briefingul semnala sha-uri diferite (VPS `b5f474232b0ad203`, git `57631aebe2f27af1`). Am adus fisierul si l-am comparat:

```
diff /root/Scripturi/cs_auto_draft.py  <git>/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py
→ 2 linii de diff, adica UN rand adaugat in git:
+    "61586834387211": "Lab Noir",       # in dict-ul PAGE_STORE
```

**Concluzie: nu exista divergenta functionala.** VPS = 1.243 linii, git = 1.244. Git e inainte cu o intrare de pagina Facebook. Singurul efect practic: pe VPS un comentariu de pe pagina Lab Noir ar cadea pe „magazin necunoscut".

⚠️ Dar **numerele de linie difera cu 1** dupa linia 66. Tot ce citez mai jos e **numerotarea de pe VPS**; in git adauga +1 pentru orice linie ≥67.

## Fisierele

| Cale | Ce e | Stare |
|---|---|---|
| `/root/Scripturi/cs_auto_draft.py` | motorul care A RULAT | 93.425 b, 3-iul 11:27 |
| `/root/Scripturi/cs_auto_draft.py.bak-efficiency-0703` | inainte de pachetul de eficienta | 92.350 b |
| `/root/Scripturi/cs_auto_draft.py.bak-20260629-*` | 5 backup-uri din 29-iun (78k → 92k) | urma reparatiilor anti-halucinare |
| `/root/Scripturi/cs_backlog.sh` | wrapperul de cron PAUZAT | 2-iul 10:20, `--ground` |
| `/root/Scripturi/cs_draft_all.sh` | wrapperul NOU (eficienta 3-iul) | Haiku 4.5 + `--fast-triage` |
| `/root/Scripturi/data/cs_backlog.log` | jurnalul complet | 12,08 MB, 137.095 linii |
| `/root/Scripturi/.auto_draft_proposals.json` | coada de propuneri | 7,29 MB, 3.156 intrari |
| `<git>/.../grammar_audit.py` | poarta de limba | **NU exista pe VPS** |

## Cronul

```
# PAUZAT 2026-06-29 0 9-21/3 * * * /usr/bin/flock -n /tmp/cs_backlog.lock /root/Scripturi/cs_backlog.sh
```

Linia e comentata. Separat, pipeline-ul de triaj (`run_cs_pipeline.py`) **ruleaza** (`*/30 8-16` + `0 2`) — dar fara `--llm`, deci nu costa credite si nu atinge drafturile.

## Cinci dependinte care lipsesc de pe VPS (verificat prin import real)

```python
>>> import cs_auto_draft as m   # pe VPS
LEARNED playbook incarcat: 0 categorii
CSA (cs-actions) exista: False
CI (customer-identity) exista: False
KB exista: False
```

Motivul: pe VPS `HERE = /root/Scripturi` (deploy plat), iar caile din cod sunt relative la structura de skill (`../../../core/scripts/kb.py`). Consecintele **masurate**:

- **`LEARNED` = 0 categorii** → blocul `learned_blk` (linia 1135) a fost **mereu gol**. Procedurile de-facto si vocea agentilor reali, invatate de `cs-procedures` din tichete inchise, **nu au ajuns niciodata intr-un draft de productie**. Toate cele 1.336 de drafturi masurate aici s-au bazat DOAR pe playbook-ul scris de mana din `SYSTEM`.
- **`CSA` lipseste** → `--approve` nu poate executa nicio actiune pe VPS.
- **`KB` lipseste** → `secret()` cade pe env. Asta e OK azi (are try/except), dar exact asta a fost bomba din 24-iun.
- **`grammar_audit.py` lipseste** → poarta de limba nu a fost rulata niciodata pe iesirea de productie.

### 1. Cele 4 runde de audit — reconstituite, cu ce s-a masurat exact

Reconstituit din `cs-auto-draft-flow.md` (istoricul deciziilor) incrucisat cu logul de pe VPS si cu codul.

| # | Data | Esantion | Metoda | Rezultat |
|---|---|---|---|---|
| **0** | ~22-23 iun | dry-run, cateva tichete | rulare uscata + citire de om (EST comentariu, Bonhaus CZ, reclamatie ANPC) | 0 greseli de gramatica; a confirmat multilingv (cehă) si escaladarea URGENT |
| **0b** | 23 iun | toate drafturile unei rulari | **`grammar_audit.py`** — corector RO/CZ/PL/BG/EN strict (gpt-4o, JSON), iterativ audit→fix→re-audit | a convers la **0 greseli**. A scos 3 tipare: registru („tu"→„dumneavoastră"), limba raspunsului ≠ limba clientului, semnatura „Echipa magazinul nostru" |
| **1** | 23 iun | **19 drafturi** din 20 scrise LIVE (tag `ai-live`) | workflow adversarial: **36 de agenti**, verificare pe 7 dimensiuni → refutare → sinteza | **14/19 curate, 5 cu probleme** (7 constatari, toate med/low). Plus: **4 false-pozitive** de la gate-ul de gramatica (citea `cust_msg`, nu `draft`) |
| **2** | 23 iun | aceleasi drafturi, citire tinta | QA manual pe fir + 2 sonde pe API-ul MCP | **1 bug major**: raspundea la PRIMUL mesaj, nu la ULTIMUL (#273383). 2 descoperiri MCP: `create_draft` ADAUGA si nu se poate sterge; `send_message` EXISTA pe server |
| **3** | 23 iun | **19 drafturi** regenerate v2 (tag `ai-draft-v2`) | acelasi workflow de 36 de agenti, pe promptul reparat | **14/19 curate, 6 probleme**, din care **1 HIGH** = supra-corectia mea la WISMO (#274123 inventa „e in procesare + 1-2 zile + tracking" fara AWB) |
| **4** | 29 iun | **75 de drafturi** parsate din `cs_backlog.log` | audit adversarial pe text | **71% probleme, 18 HIGH.** Verdict: NU sigure de auto-send → **cron pus pe pauza** |
| **NOU A** | 15 sep | **toate cele 1.336** de drafturi salvate | recensamant determinist: regexul `HALLU` din codul de azi + 10 sonde de tipar, rulat pe populatia intreaga | **31,0%** din cele 1.162 normale contin fabricatie. Detaliu in sectiunea 3 |
| **NOU B** | 15 sep | **40 de tichete** trase aleator (seed 20260915) | **masuratoare de EFECT**: citire live Richpanel — a raspuns un agent dupa draft? seamana raspunsul cu draftul? | **0/40 acceptare.** 38/40 inchise fara niciun raspuns. Detaliu in sectiunea 6 |

## Ce au in comun rundele 1-4

Toate au masurat **TEXTUL**. Niciuna nu a masurat **ce a facut CS-ul** cu drafturile. Regula echipei — „verifica EFECTUL, nu codul" — nu a fost aplicata acestui sistem pana azi.

## Nota metodologica importanta despre esantionul de 75 din 29-iun

Logul din care a fost tras contine **7.366 de treceri prin tichete**, din care:
- **1.336** cu text real de draft (toate dintr-o singura rulare),
- **6.015** cu „(eroare LLM)" — **care NU au fost salvate in Richpanel** (garda anti-gunoi le-a sarit).

Daca cele 75 au fost trase uniform din log, ~82% dintre ele erau drafturi-eroare pe care CS-ul nu le-a vazut niciodata. Memoria chiar mentioneaza „drafturi-eroare '(eroare LLM ...'uv')'" printre constatari. **Deci „71% probleme" amesteca doua populatii diferite** si e probabil pesimist fata de ce a ajuns efectiv in inbox. Nu am gasit fisierul cu cele 75 ca sa verific — vezi incertitudini.

### 2. Ce a rulat de fapt: 25 de rulari, 1 singura care a produs ceva

Parsat integral din `/root/Scripturi/data/cs_backlog.log` (137.095 linii).

| Rulare | Tichete | **Salvate** | Sarite (garda) | Escaladari rutate | Erori LLM |
|---|--:|--:|--:|--:|--:|
| 2026-06-24 15:39 *(manuala)* | 1336 | **1336** | 0 | 0 | 0 |
| 2026-06-24 21:00 *(primul cron)* | 120 | **0** | 120 | 0 | 120 |
| 2026-06-25 09:00 | 138 | **0** | 138 | 0 | 138 |
| … *(inca 20 de rulari identice)* | … | **0** | … | 0 | … |
| 2026-06-29 12:00 | 291 | **0** | 290 | 0 | 291 |
| 2026-07-02 10:00 | 21 | **0** | 21 | 0 | 21 |
| **TOTAL** | **7366** | **1336** | **6015** | **0** | **6030** |

Tichete unice atinse: **1.713**.

## Cauza: `uv` nu e in PATH-ul cronului

```
6009  (eroare LLM: [Errno 2] No such file or directory: 'uv')
  21  (eroare LLM: HTTP Error 429: Too Many Requests)     ← doar rularea din 2-iul
```

Si la triaj, acelasi lucru: **10.933** esecuri `'uv'` + 26 × 429 + 1 × 520.

Mecanismul: `secret()` chema `subprocess.run(["uv", "run", kb.py, "secret-get", "ANTHROPIC_API_KEY"])`. In cron, PATH-ul e minimal → `uv` nu exista → exceptie → **toate** apelurile LLM cadeau. Reparat pe 29-iun, prezent azi la **liniile 226-238**:

```python
def secret(k):
    v = os.environ.get(k)
    if v: return v
    if k in _SECRET_CACHE: return _SECRET_CACHE[k]
    try:
        v = subprocess.run(["uv", "run", KB, "secret-get", k], ..., timeout=30).stdout.strip()
    except Exception:
        v = ""   # uv negasit → gol, NU exceptie
```

## Lectia care conteaza pentru Sonia

**Cronul a raportat ✅ verde la fiecare rulare, 5 zile, producand zero.** Garda anti-gunoi facea exact ce trebuia (sarea tichetul in loc sa scrie gunoi in Richpanel) — dar o facea **tacut**. Nimeni nu a observat pana la auditul din 29-iun.

Celelalte 9 cronuri de pipeline de pe acest VPS au `heartbeat.py` si sunt vazute de `data_health.py`. Acesta nu are. Vezi propunerea de imbunatatire.

## Distributia a ce s-a procesat

| Canal | Procesate | Salvate |
|---|--:|--:|
| email | 3471 | 612 |
| messenger | 2354 | 449 |
| facebook_message | 1339 | 233 |
| email_from_widget | 156 | 42 |
| instagram_message | 46 | 0 |

Comentariile FB/IG sunt **excluse deliberat** (`--no-comments`) — decizie de owner, raman la CS.

Categorii: `altele` 5838 (79%), retur 410, livrare_wismo 362, anulare 252, problema_produs 147. **Ponderea uriasa de „altele" e ea insasi un semnal** — in `--lean`/`--fast-triage`, cand triajul LLM pica, hintul regex cade pe „altele", iar draftul se genereaza fara categorie → fara procedura.

Magazine: `magazinul nostru` 2510 (34%) — adica brandul **nu a fost identificat** pe o treime din tichete, deci semnatura a iesit generica („echipa noastra"). Asta e capcana `--lean` documentata: brandul venea din comenzi, iar lean sarea peste ele. `brand_from_email()` (linia 132) o mitigheaza doar la email.

### 3. Verdictul din 29-iun disecat + recensamant NOU pe toata populatia

## Ce spunea verdictul

> „Audit adversarial pe 75 drafturi reale (din logul VPS cs_backlog.log, parsat): **71% probleme, 18 HIGH** — halucinari (dimensiuni/pret/telefon/status inventate, „am verificat/nu am gasit" fara lookup) + drafturi-eroare „(eroare LLM ...'uv')". Verdict: NU sigure de auto-send."

Esantion: 75 de drafturi parsate din log. Vezi nota metodologica din sectiunea 1 — esantionul amesteca drafturi salvate cu drafturi-eroare nesalvate.

## Masuratoare NOUA: acelasi lucru, dar pe populatia INTREAGA

Am extras toate cele **1.336 de drafturi cu text** din rularea din 24-iun si le-am trecut prin **regexul `HALLU` importat direct din codul de pe VPS de azi** — adica exact filtrul scris ca sa repare aceasta problema. Intrebarea: *cat din ce s-a scris atunci ar fi prins filtrul de azi?*

```
Drafturi salvate 24-iun:                1336
  din care de ASTEPTARE (escaladare):    174   ← filtrul nu se aplica pe ele
  drafturi normale:                     1162

HALLU prinde pe cele 1162 normale:       360   (31,0%)
```

**Defalcare pe tipar** (din cele 1.162):

| Tipar | Nr | % | Verdict |
|---|--:|--:|---|
| **lookup fabricat** („am verificat / am cautat / nu am gasit") | **341** | **29,3%** | dominant, cu mult |
| pret concret inventat (N lei) | 18 | 1,5% | |
| dimensiune inventata (N cm / NxM) | 2 | 0,2% | |
| status de comanda inventat | 1 | 0,1% | |
| termen de livrare in zile | 0 | 0,0% | |

**Deci „halucinarea" nu era un evantai de probleme — era, in 95% din cazuri, UNA singura:** draftul spunea clientului ca i-am cautat comanda si n-am gasit-o. Sistemul rula `--lean`: nu facuse **niciun** lookup. Exemple reale, toate salvate in Richpanel:

> **#272838** (Grandia, anulare): „Din pacate, **nu am gasit nicio comanda in sistemul nostru**. Va rugam sa ne oferiti detalii suplimentare…"
>
> **#273288** (Esteban, WISMO): „Din pacate, **nu am gasit nicio comanda inregistrata pe numele dumneavoastra**. Este posibil sa fi folosit un alt nume sau o alta adresa de email…"
>
> **#273410** (Esteban, WISMO): „In acest moment, **nu am gasit informatii referitoare la comanda dumneavoastra, ceea ce ne ingrijoreaza**."

Ultimul e cel mai rau: fabrica si o emotie („ceea ce ne ingrijoreaza") pe baza unei cautari inexistente.

## Ce NU era stricat — masurat pe aceleasi 1.336

Astea sunt vestile bune, si sunt la fel de masurate:

| Sonda | Nr | % | Interpretare |
|---|--:|--:|---|
| tutuire („tu/tie/te-am") | 1 | 0,1% | **fixul de registru din runda 0b a tinut** |
| „Echipa magazinul nostru" | 0 | 0,0% | **fixul de semnatura a tinut** |
| confirmare falsa de actiune („am modificat/am anulat") | 0 | 0,0% | **garda `ACTIUNE_APLICATA` a tinut perfect** |
| „(eroare LLM" intr-un draft SALVAT | 0 | 0,0% | **garda anti-gunoi a tinut perfect** |

Ultimele doua sunt importante: cele doua garzi deterministe (nu de prompt) au avut **rata de scapare zero** pe 1.336 de cazuri. Garzile scrise in cod au tinut; cele scrise in prompt au avut 31% scapari.

**Asta e lectia de arhitectura pentru Sonia: ce poti verifica in cod, verifica in cod. Promptul e o rugaminte, codul e o poarta.**

## Un non-rezultat, ca sa nu-l raportezi gresit

Sonda „salut de deschidere" a dat 718 (53,7%). **Nu e o greseala.** Regula „fara «Buna ziua»" se aplica DOAR comentariilor publice, iar rularea a fost `--no-comments` — deci toate sunt email/DM, unde salutul e corect. Am lasat-o aici exact ca sa nu fie renumarata ca defect.

### 4. Poarta de limba si gramatica (grammar_audit.py) — capcana NU e reparata

Fisier: `<git>/plugins/gigi/skills/cs-draft-reply/grammar_audit.py`, 111 linii. **Nu exista pe VPS.**

## Ce verifica

Promptul `AUDIT_SYS` (liniile 27-35) cere unui gpt-4o (temperature 0, `response_format: json_object`) doua lucruri:

1. **LIMBA** — raspunsul trebuie sa fie in aceeasi limba in care a scris clientul (`mesaj_client`). Daca nu → eroare **GRAVA**, tip `limba_gresita`. `lang` e doar indicatie; textul clientului primeaza.
2. **GRAMATICA in limba textului** — acord, cazuri, prepozitii, diacritice, punctuatie. Pentru romana, explicit: „ți-am scris/v-am scris" NU „te-am scris"; comentariu public = PLURAL politicos.

Tipuri raportate: `limba_gresita | gramatica | acord | caz | prepozitie | registru | diacritice | punctuatie | naturalete`.

Este **multilingv** (ro/cz/pl/bg/en) si acopera **toate** tipurile de raspuns: comentariu public, DM, email si mesajul de escaladare.

## Cum se ruleaza

```bash
# 1) scoate drafturile structurat (ultima linie, dupa marcajul @@JSON@@)
uv run cs_auto_draft.py --limit 20 --json 2>/dev/null \
  | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/cs_drafts.json

# 2) treci-le prin corector
uv run grammar_audit.py --file /tmp/cs_drafts.json
```

Fiecare rand din JSON duce `draft` + `private_msg` ca texte separate, plus `language`, `cust_msg` (ultimul mesaj real al clientului, 240 car.), `channel`, `escalate`, `orders`. Iesirea: doar greselile, cu corectura, plus un rezumat cu tipare recurente.

⚠️ **Regula de proces:** se ruleaza dupa ORICE schimbare de prompt. In practica nu s-a intamplat — vezi mai jos.

## Ce a gasit (runda 0b, 23-iun)

Iterativ audit→fix→re-audit, pana la **0 greseli**. Trei tipare sistemice:

1. **Registru** — raspunsurile erau la „tu" pe toate canalele; agentii ARONA reali scriu FORMAL, la „dumneavoastra". Fix global in `SYSTEM` + `HOLDING`.
2. **Multilingv** — raspunsul trebuie sa fie in limba in care a scris CLIENTUL, nu in limba pietei brandului. A dus la lantul de precedenta din `detect_lang` (linia 158).
3. **Semnatura la magazin necunoscut** — „Echipa magazinul nostru" (concatenare bruta) → „echipa noastra".

Toate trei sunt in codul de azi si **au tinut**: 0,1% tutuire si 0,0% semnatura gresita pe 1.336 de drafturi.

## Capcana documentata — si NU, nu e reparata

> „**CAPCANA gate gramatica**: `grammar_audit.py` a dat **4 false-pozitive** citind cuvinte din `cust_msg` (mesajul clientului), nu din `draft` — de intarit promptul AUDIT sa auditeze DOAR raspunsul." *(23-iun)*

**Verificat azi — nereparata. Trei dovezi:**

1. **Istoricul git**: `git log -- grammar_audit.py` → **un singur commit**, `e0e0b31` din 2026-06-23. Niciun commit dupa descoperirea capcanei.
2. **mtime**: 23-iun 12:38, neatins de atunci.
3. **Codul**: payload-ul (liniile 73-75) inca trimite ambele texte in acelasi bloc —
   ```python
   "id=%d | lang=%s | tip=%s | tichet #%s\n  mesaj_client: %s\n  RASPUNS: %s\n"
   ```
   iar `AUDIT_SYS` **nu contine nicaieri** o instructiune de tipul „auditezi DOAR campul RASPUNS". Instructiunea 2 zice „GRAMATICA IN LIMBA TEXTULUI" — iar in payload sunt doua texte. Ambiguitatea care a produs cele 4 false-pozitive e intacta.

**Fixul (o linie, netestat de mine — NU l-am aplicat, task read-only):** in `AUDIT_SYS`, inainte de punctul 1:

> „Auditezi EXCLUSIV textul de dupa `RASPUNS:`. `mesaj_client` e DOAR context, ca sa stii in ce limba a scris clientul — greselile DIN `mesaj_client` NU se raporteaza niciodata."

## Doua gauri de proces

- **Poarta nu e pe VPS** → nu a fost rulata niciodata pe iesirea de productie. Cele 1.336 de drafturi reale nu au trecut prin ea.
- **Poarta nu e in SKILL.md** → 15 sectiuni, zero mentiuni despre `grammar_audit`, audit sau masurarea calitatii. Cine preia sistemul nu are de unde sa afle ca exista.

### 5. Tiparele sistemice reparate in prompt — fiecare verificat in codul de pe VPS

Enumerate toate, cu tichetul care le-a scos la iveala si cu **verificarea in cod** (`grep` pe `/root/Scripturi/cs_auto_draft.py`, numerotare VPS; in git +1 dupa linia 66).

## A. Reparatii de CONTINUT (prompt)

| # | Tipar | Tichetul declansator | In cod AZI? | Unde |
|---|---|---|---|---|
| 1 | **Raspundea la PRIMUL mesaj, nu la ULTIMUL** — client intreba WISMO, apoi scria „Foarte bune, multumesc!"; draftul iesea WISMO | **#273383** | ✅ in 3 locuri | `IDENTIFY_SYS` L392, `SYSTEM` L428, marcajul in transcript L961 (`>>> ULTIMUL MESAJ AL CLIENTULUI`) |
| 2 | **Escaladare prea agresiva** — volumul de tichete / „a mai scris de N ori" trecea drept motiv de escaladare | **#274123** (WISMO politicos escaladat) | ✅ | L410: „volumul/«a mai scris de N ori» in istoric NU e, singur, motiv de escaladare" |
| 3 | **WISMO pasiv** — cerea date pe care le avea deja | #274123 | ✅ | L432, proceduri LIVRARE/WISMO |
| 4 | **WISMO care halucina statusul** (supra-corectia de la #2) — „e in procesare + 1-2 zile + tracking" fara AWB | **#274123**, runda 3 | ✅ | L432: „NU afirma statusul… NU promite termen/tracking" |
| 5 | **Salut din nume neformatat** — „doamna GheorghesiGerda" (prenume+nume lipite) | — | ✅ | L442 `SALUT PE NUME`, cu exemplul literal „GheorghesiGerda" |
| 6 | **Nume de client pe comentariu public** = expunere de PII („doamna Nechita") | runda 3 | ✅ | L442, a doua jumatate |
| 7 | **Re-oferea canalul reclamat** — „sun de 4 zile si nu raspunde nimeni" → draftul il trimitea sa sune | **#274212** | ✅ | L443 `CANAL RECLAMAT` |
| 8 | **Deflecta in privat o intrebare simpla** (pret/dimensiune) | runda 3 | ✅ | L439, blocul ⚠️ |
| 9 | **Nu incuraja intentia de cumparare** — „vreau si eu" primea deflectare seaca | — | ✅ | L436 PRE-VANZARE |
| 10 | **Descriere generica de produs** — „aspect placut" la un parfum | — | ✅ | L435, pe categorie: parfum→miros/persistenta |
| 11 | **Escalada nemultumirea de produs pe comentariu public** fara ANPC | **#274207** (de-escaladat manual) | ✅ | L410, sectiunea COMENTARII PUBLICE |
| 12 | **Salut de deschidere pe comentarii** („Buna ziua!" pe FB) | — | ✅ | L438, „REGULA CS FERMA" |
| 13 | **Sarcasm citit ca lauda** — „au persistat 4 ore 😅" la un parfum dat ca 12h | — | ✅ | L393 (`IDENTIFY_SYS`) + L438 (`SYSTEM`) |
| 14 | **Registru „tu"** pe toate canalele | runda 0b | ✅ | L429 `REGISTRU (important)` |
| 15 | **Semnatura „Echipa magazinul nostru"** | runda 0b | ✅ | L444 + `HOLDING` L446 |
| 16 | **Pretindea originalitate** la replici/piele ecologica | — | ✅ | L437 `PRODUSE — ONESTITATE` |
| 17 | **⛔ ANTI-HALUCINARE #1** (29-iun, pachetul mare) | esantionul de 75 | ✅ | L420-425, primul lucru din `SYSTEM`, marcat „MAI PRESUS DE ORICE" |

## B. Reparatii in COD (garzi deterministe — astea chiar au tinut)

| # | Garda | In cod AZI? | Unde | Masurat |
|---|---|---|---|---|
| 18 | **Post-filtru `HALLU`** — regenereaza 1× cu corectiv, apoi cade pe sablon sigur | ✅ | regex L189-196, aplicare L1156-1173 | prinde 31,0% din 24-iun |
| 19 | **`ACTIUNE_APLICATA`** — draftul confirma „facut" doar daca actiunea chiar s-a aplicat | ✅ | L440 + injectare doar in `do_approve` L783 | **0 scapari / 1.336** |
| 20 | **Garda anti-gunoi** — nu salva draft care incepe cu „(eroare" sau <5 caractere | ✅ | L1213-1215 | **0 scapari / 1.336**; a sarit 6.015 |
| 21 | **`secret()` cu try/except + cache** — fixul bugului `uv` | ✅ | L226-238 | vezi sectiunea 2 |
| 22 | **`_llm_http` retry+backoff** pe 429/5xx | ✅ | L276-294 | a rezistat pana la 429-storm real |
| 23 | **`MCP._post` retry** pe 429/5xx/timeout | ✅ | L245-266 | |
| 24 | **`tag_id()`** — rezolva nume→UUID (MCP-ul accepta DOAR UUID, esua TACUT pe nume) | ✅ | L579 | |
| 25 | **`fulfillment_state` FAIL-SAFE** — necunoscut → „post", blocheaza modificarea | ✅ | L489-498 | |
| 26 | **`resolve_target_order`** — nu substituie tacit o comanda; ambiguu → nicio actiune | ✅ | L460 | |
| 27 | **`do_approve` consuma intrarea** (flag `applied`) → fara resend/swap dublu | ✅ | L768-770 | 0 aplicari vreodata |
| 28 | **`_f()`** float defensiv pe `confidence` | ✅ | L150 | |
| 29 | **Redactare PII STRUCTURALA** pe canale publice (nu doar prin prompt) | ✅ | L1131-1133 | |
| 30 | **`brand_from_email`** — deduce brandul din domeniu in lean | ✅ | L132 | insuficient: 34% au ramas „magazinul nostru" |
| 31 | **`--ground`** — lookup self-contained (metrics + profitability.db + AWB), fara SSH/uv | ✅ | `lookup_orders` L512, flag L857 | **0 drafturi in productie** |

**Toate cele 31 sunt prezente in codul de pe VPS de azi.** Niciuna nu s-a pierdut la deploy — desi capcana era reala (memoria documenteaza ca `gigi:publish-skill` face `git checkout main` si arunca working tree-ul, ceea ce a distrus o data edit-urile de AWB).

### 6. MASURATOAREA DE EFECT — prima din istoria sistemului

Regula echipei: *verifica EFECTUL, nu codul. Pentru un sistem care scrie drafturi, efectul = ce a facut CS-ul cu ele.*

## Ce n-a mers (si de ce, ca sa nu pierzi timpul acolo)

Sugestia din brief era `cs_mirror.db`. Am verificat — **nu poate raspunde**:

```
cs_mirror.db  rp_ticket: 12.234 randuri
  created_at: 2026-08-27 .. 2026-09-15
  tag_names LIKE '%ai-%': 0
  overlap cu cele 1.434 de tichete ai-*: 0
```

Oglinda incepe pe **27-aug**; tichetele cu draft sunt din **iunie**. Zero suprapunere. Ideea e buna, dar fereastra e gresita.

## Populatia reala

Din `richpanel_tickets.db` (255.767 tichete, `tag_names` in JSON-ul `raw`):

| Tag | Tichete |
|---|--:|
| `ai-draft` | 1418 |
| `ai-draft-v2` | 19 |
| `ai-live` | 18 |
| `ai-draft-v3` | 3 |
| **`ai-sent`** | **0** |

**`ai-sent` = 0 in tot istoricul.** Calea `--send` (trimitere live + inchidere, implementata in runda 3, `do_send` L803) **nu a fost folosita niciodata in productie.** Draft-only nu a fost doar o regula — a fost si un fapt.

Si din coada de propuneri (`.auto_draft_proposals.json`, 3.156 intrari): **`applied: 0`**. Nicio actiune (modify/cancel/swap/resend) nu a fost vreodata aplicata prin `--approve`. 9 propuneri de comanda, 31 de hide, 328 de escaladari — toate neatinse.

## Masuratoarea: 40 de tichete, citite live azi

Esantion aleator (seed 20260915) din cele **1.162 de drafturi normale** salvate pe 24-iun. Pentru fiecare: exista vreun mesaj de agent dupa 24-iun 13:30 UTC (pornirea rularii)? Seamana cu draftul nostru?

**41 de apeluri `get_conversation`, 5 s intre ele (~12/min), strict citiri.** Plus 2 apeluri pentru `list_users`. Total **43**, sub plafonul de 60.

```
===== REZULTAT (n=40) =====
  INCHISE azi:                      40/40  (100%)
  cu RASPUNS DE AGENT dupa draft:    2/40  (5%)
  INCHISE FARA niciun raspuns:      38/40  (95%)
  inca OPEN:                            0
  client a rescris dupa draft:          4
  similaritate raspuns-agent vs draft: min=0.30  median=0.33  max=0.33
  >=0.60 (draft folosit ~ca atare):     0
  >=0.35 (retusat vizibil):             0
```

## Rata de acceptare masurata: 0/40

Cele doua tichete care au primit un raspuns l-au primit pe **6-iul, la 12 zile dupa draft**, si raspunsul nu era draftul — era un macro generic de scuze:

> #270834 (sim 0,33): „Bună ziua! Ne cerem scuze pentru răspunsul întârziat. Acesta se datoreaza unei erori în sistem. Vă rugăm să ne s…"
>
> #270703 (sim 0,30): „Bună ziua! Ne cerem scuze pentru răspunsul întârziat. Acesta se datoreaza unei erori în sistem. Am observat poza…"

Acelasi text pe ambele → sablon de curatenie de backlog, scris de om, nu draftul nostru.

**0/40 cu 95% incredere inseamna o rata reala sub ~7,4%** (regula lui trei).

## Cine le-a inchis: oameni, nu un bot

```
closed_by:  20458195… → Cristina Sava (<email agent — vezi `rp.py agents`>, TENANT_AGENT_FULL)  20
            ecd1325c… → Diana Popa    (contact@nocturna.ro,        TENANT_AGENT_FULL)  19
            2bcd5152… → Monica Dan    (TENANT_ADMIN)                                    1
```

Decalaj draft → inchidere: **min 4 zile, mediana 12, max 17.** Concentrat pe 30-iun si 6-9 iul.

Citit cu regula casei „REPLY = CLOSE" (CS-ul inchide cand raspunde), tiparul e clar: **agentii au inchis in masa un backlog vechi, fara sa raspunda, si fara sa atinga drafturile.**

## Verificare incrucisata pe populatia mare (fara API)

Din snapshot-ul inghetat la 2-3 iul (`richpanel_tickets.db` re-trage doar ultimele 2 zile, deci zilele din iunie au inghetat la backfill-ul din 2-iul):

| | tichete | `last_message_sender_type = operator` |
|---|--:|--:|
| **cu tag ai-*** (email+DM) | 986 | **46 (4,7%)** |
| control, netagate, aceeasi fereastra | 3686 | 1951 (52,9%) |

⚠️ **Aceasta comparatie e confundata si NU trebuie citata ca dovada de dauna.** Grupul AI a fost ales tocmai pentru ca era deschis si neraspuns pe 24-iun — adica coada grea si veche. O rata de raspuns mai mica e de asteptat din selectie, nu din cauza AI-ului.

Ce e **nefalsificabil** e numarul absolut: **4,7% pe populatia mare si 5% pe esantionul live, masurate independent, la 9 zile respectiv 83 de zile distanta.** Doua metode, acelasi raspuns.

## Escaladarile: identificate, niciodata rutate

```
⛳ ESCALADARE (identificate):  197   (173 HIGH + 24 URGENT)
⛳ Rutat: (scris in Richpanel):  0
DRAFT ASTEPTARE (generate):    197
din cele 174 salvate, cu tag escaladare/esc-*:  3
```

**Cele 24 de cazuri URGENT — adica ANPC/juridic/amenintare/chargeback — au primit un „revenim cat mai curand" si nimic altceva.** Fara prioritate HIGH, fara tag, fara nota interna de brief pentru coleg.

Cauza, in cod la **linia 1201**:
```python
if is_esc and not a.lean:      # rutarea se face DOAR in modul complet
```
Cronul din iunie rula `--lean`. Wrapperul de azi (`cs_backlog.sh`, `cs_draft_all.sh`) ruleaza `--ground`, deci rutarea **ar** functiona la repornire — dar nu a fost niciodata dovedita pe un tichet real. **Verific-o prin efect inainte sa te bazezi pe ea.**

### 7. Ce NU s-a masurat NICIODATA — lista explicita

| # | Ce | Stare | Nota |
|---|---|---|---|
| 1 | **Rata de acceptare a drafturilor de catre agenti** | ~~niciodata~~ → **masurat azi: 0/40** | prima data in viata sistemului |
| 2 | **Cat de des retuseaza CS-ul un draft** | **niciodata, si nici nu se poate inca** | ca sa masori retusul iti trebuie macar un draft trimis. Sunt 0. Observatii utile: **zero** |
| 3 | **A fost vreodata trimis efectiv un draft?** | **NU** | `ai-sent` = 0 in 255.767 de tichete; `--send` nefolosit in productie |
| 4 | **A fost vreodata aplicata o actiune propusa?** | **NU** | `applied: 0` din 3.156 de propuneri; `--approve` + `cs_actions --apply` + `fb_hide_comment` = cai nevalidate live |
| 5 | **CSAT / satisfactie dupa un draft** | niciodata | CSAT nu e setat deloc in Richpanel (=0) |
| 6 | **Rata de redeschidere** dupa un raspuns bazat pe draft | niciodata | nu exista raspunsuri bazate pe draft |
| 7 | **Efectul pe FRT** (timp de prim raspuns) | niciodata | linie de baza ~5h in Richpanel; nicio comparatie |
| 8 | **Cost per draft, masurat** | niciodata | doar estimari (~$22 pe backlog de 3,6k cu Haiku, ~$220 cu Sonnet). Facturi reale: neverificate |
| 9 | **Calitatea pe calea `--ground`** | **0 drafturi in productie** | validata doar manual (~5/7 si ~10/13 hit-rate). O singura rulare de cron, 21 tichete, toate picate pe 429 |
| 10 | **Calitatea pe Haiku 4.5** (configuratia de AZI) | **niciun lot** | doar un smoke-test, in care Haiku a supra-afirmat o actiune. **Cei 31,0% masurati aici sunt pe gpt-4o-mini si NU se transfera** |
| 11 | **`--fast-triage`** (1 apel LLM in loc de 2) | niciun lot | adaugat 3-iul, nerulat niciodata |
| 12 | **Calitatea pe canalul de comentarii FB/IG** | exclusa din cron | `--no-comments`; masurata doar in cele 19-20 de drafturi din 23-iun |
| 13 | **Calitate non-RO in productie** (cz/pl/bg/en) | niciodata | poarta de limba nu e pe VPS; un singur caz confirmat manual (Bonhaus CZ) |
| 14 | **Poarta de gramatica pe iesirea de productie** | niciodata | `grammar_audit.py` nu exista pe VPS |
| 15 | **Efectul playbook-ului invatat** asupra calitatii | imposibil de masurat | `LEARNED` = 0 pe VPS; n-a fost injectat niciodata |
| 16 | **Cate drafturi vechi mai stau azi in Richpanel** | necunoscut | `get_conversation` nu returneaza drafturi; nu exista delete-draft |
| 17 | **Cate drafturi duplicate** din experimentele cu workeri paraleli | estimat „~13+ si dubluri", nemasurat | `create_draft` ADAUGA |
| 18 | **Acuratetea triajului** (categorie/limba/severitate) fata de un adevar de referinta | niciodata | 79% au iesit „altele" — puternic suspect, niciodata investigat |
| 19 | **Acuratetea detectiei de spam** (5.146 excluderi) | niciodata | nici precizie, nici acoperire |

## Verdictul onest asupra a ce s-a masurat pana acum

Patru runde de audit, ~113 drafturi citite in total (19+19+75), toate pe **text**. Niciuna pe **efect**.

Iar cele 4 runde au fost facute pe drafturi din **23-24 iun** — dar fixurile mari (anti-halucinare + grounding) au venit pe **29-iun**, iar pachetul de eficienta (Haiku + fast-triage) pe **3-iul**. **Configuratia actuala a sistemului nu a fost auditata niciodata pe niciun lot de drafturi.** Cifra de 31,0% e o linie de baza istorica, nu o masura a ce ar produce sistemul daca l-ai porni maine.

### 8. Runbook: cum reproduci fiecare cifra de aici

Toate scripturile de mai jos sunt **strict de citire**. Niciunul nu scrie in Richpanel, in git sau pe VPS.

Regula de rulare pe VPS (heredoc-urile inline se strica):
```bash
# scrie scriptul local, apoi:
ssh root@84.46.242.181 "bash -s" < /cale/locala/script.sh
```

## 1. Diferenta VPS ↔ git
```bash
scp root@84.46.242.181:/root/Scripturi/cs_auto_draft.py /tmp/vps.py
diff -u /tmp/vps.py <git>/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py
shasum -a 256 /tmp/vps.py | cut -c1-16     # → b5f474232b0ad203
```

## 2. Statistici pe rulari (din log, fara API)
Parseaza `/root/Scripturi/data/cs_backlog.log` dupa marcajele:
- `^===== RUN (.+) =====` — inceput de rulare
- `^--- channel: (\S+) ---` — canal
- `\[\d+/\d+\] #(\d+) · (magazin) · (platforma) · (categorie) ·` — antet de tichet
- `✅ DRAFT salvat` — draft chiar scris in Richpanel
- `⛔ draft invalid` — sarit de garda
- `⛳ Rutat:` — escaladare chiar rutata

## 3. Extrage textul drafturilor
Blocul e delimitat de `┌─ DRAFT (engine) ───` … `└───`, cu fiecare rand prefixat `│`. **Doar rularea 1 (liniile 1-17967) contine text real** — restul sunt „(eroare LLM)".

## 4. Recensamant de halucinare cu filtrul de AZI
```python
import importlib.util, sys
spec = importlib.util.spec_from_file_location("cad", "/root/Scripturi/cs_auto_draft.py")
m = importlib.util.module_from_spec(spec); sys.modules["cad"] = m
try: spec.loader.exec_module(m)
except SystemExit: pass          # main() iese pe lipsa de argumente — normal
hits = [k for k, v in drafts.items() if m.HALLU.search(v["draft"]) and not v["esc"]]
```
Importa regexul **din codul de productie**, nu o copie — altfel masori altceva decat ruleaza.

## 5. Verifica ce e efectiv incarcat pe VPS
```python
print("LEARNED:", len(m.LEARNED))                 # → 0
import os
for n in ("CSA", "CI", "KB"): print(n, os.path.exists(getattr(m, n)))   # → False, False, False
```

## 6. Masuratoarea de efect (consuma cota Richpanel)
Bugetul: **1 init + N × `get_conversation`**, cu `time.sleep(5)` intre apeluri (~12/min, sub plafonul de 15). Foloseste clasa `MCP` din `cs_auto_draft.py` — are deja retry cu backoff pe 429.
```python
cv = mcp.call("get_conversation", {"conversation_number": str(no), "mode": "audit", "max_messages": 40})
t, msgs = cv["ticket"], cv["messages"]
cust = t["customer"]["id"]
after = [x for x in msgs if x["created_at"] > "2026-06-24T13:30:00"]     # pornirea rularii, UTC
agent = [x for x in after if not x["is_private"] and x["author_id"] != cust]
```
Similaritate: `difflib.SequenceMatcher` pe text normalizat (fara diacritice, fara punctuatie). Praguri folosite: **≥0,60 = draft folosit aproape ca atare**, **0,35-0,60 = retusat vizibil**.

⚠️ Tokenul (`RICHPANEL_MCP_TOKEN`) se ia **pe VPS** din `/root/Scripturi/.env` si nu paraseste cutia:
```bash
export RICHPANEL_MCP_TOKEN="$(grep -m1 ^RICHPANEL_MCP_TOKEN= .env | cut -d= -f2-)"
```

## 7. Ce sa NU faci
- ❌ `--create-draft` / `--send` / `--approve` — scriu la client
- ❌ re-rulat pe aceleasi tichete — `create_draft` **ADAUGA**, nu suprascrie, si **nu exista delete prin API** (verificat: 21 de unelte pe server)
- ❌ paralelizat — 429 si pe citiri (Richpanel), si pe LLM (OpenAI). Configuratia curata dovedita: **2 workeri, `--sleep 0.8`**; 3 degradeaza, ≥4 = furtuna de 429
- ❌ `scp` manual pe VPS pentru deploy → foloseste `deploy.sh --apply` (scp-ul manual e cauza documentata a divergentelor git↔VPS pe acest repo)

## Fisiere intermediare produse azi (pe VPS, /tmp — efemere)
`/tmp/run1_drafts.json` (cele 1.336 de drafturi cu metadate) · `/tmp/ai_tickets.tsv` (cele 1.434 tagate) · `/tmp/live_sample.json` (esantionul live de 40)

**Ce nu se știe (explicit):**

- Nu stiu daca cele ~1.162 drafturi vechi mai sunt efectiv in Richpanel azi. `get_conversation` NU returneaza drafturile (verificat pe #272838: comment_count 3, doar mesaje de client), iar MCP-ul nu are delete-draft. Deci nu pot nici confirma, nici infirma prin API — se poate verifica doar deschizand cateva conversatii in UI.
- Nu stiu compozitia exacta a esantionului de 75 de drafturi din auditul 29-iun. Memoria spune „parsat din logul VPS". Logul contine 7.366 treceri, din care doar 1.336 au produs text real de draft si 6.015 au produs „(eroare LLM)". Daca esantionul a fost tras uniform din log, o parte din cele 71% „probleme" erau drafturi-eroare care oricum NU au fost salvate in Richpanel (garda le-a sarit) — deci verdictul de 71% ar fi pesimist fata de ce a vazut CS-ul. Nu am gasit fisierul cu cele 75 ca sa verific.
- Nu stiu daca cele 18 HIGH din 29-iun sunt un subset al celor 360 prinse de HALLU azi sau alta taxonomie. Recensamantul meu e determinist (regex), al lor era adversarial (LLM+agenti), deci numaratoarea nu e direct comparabila.
- Nu pot explica complet decalajul de tag-uri: 1.336 drafturi salvate in RUN1, dar doar 979 (73,3%) poarta azi tag ai-*, iar 439 de tichete tagate ai-draft NU apar in log. Ipoteza cea mai probabila: rularile manuale/paralele din 23-24 iun (experimentele cu 8 workeri, documentate in memorie) au scris in afara wrapperului de cron, deci nu-s in cs_backlog.log. Nu am dovada.
- Esantionul de efect e n=40, tras aleator din cele 1.162 drafturi normale. 0/40 acceptare inseamna ca rata reala e sub ~7,4% cu 95% incredere — dar nu pot exclude ca exista un buzunar de tichete (alt canal, alt magazin) unde drafturile chiar au fost folosite. Nu am esantionat stratificat pe canal.
- Nu stiu daca cei 2 agenti care au inchis 39 din 40 de tichete (Cristina Sava, Diana Popa) le-au inchis ca actiune de curatenie de backlog sau caz cu caz. Decalajul (mediana 12 zile, concentrat pe 30-iun / 6-9 iul) si faptul ca 38/40 n-au primit niciun raspuns sugereaza curatenie in masa, dar e inferenta, nu masuratoare.
- Masuratoarea mea de similaritate draft-vs-raspuns-agent foloseste difflib pe text normalizat. Un agent care ar fi rescris ideea draftului cu alte cuvinte ar iesi cu scor mic si ar aparea fals ca „nu a folosit draftul". Pe cele 2 cazuri reale am citit textul si chiar nu era draftul (era un macro generic de scuze), dar metoda in sine nu ar distinge parafraza.
- Nu stiu de ce rularea din 2-iul a picat pe OpenAI 429. Memoria documenteaza contentia pe aceeasi cheie OpenAI cu pipeline-ul de triaj, dar nu am verificat ce rula la 10:00 in acea zi.
- Calitatea pe Haiku 4.5 (modelul din wrapperul de azi, cs_draft_all.sh) nu e masurata pe niciun lot. Toate cele 1.336 de drafturi masurate aici au fost produse cu openai/gpt-4o-mini. Memoria semnaleaza un smoke-test in care Haiku a supra-afirmat o actiune — deci cifra de 31,0% halucinare NU se poate transfera pe configuratia actuala.

---

## 6. Starea la predare și analiza de îmbunătățire

> Sistemul de auto-draft CS (`cs_auto_draft.py`, 1.244 linii) e OPRIT din 29-iun-2026 și versiunea de pe VPS e practic identică cu git — singura diferență e o linie (pagina Lab Noir în `PAGE_STORE`), deci alarma „VPS diferă de git" e benignă. Jurnalul arată însă altceva, mult mai grav: din 25 de rulări de cron, DOAR PRIMA a produs drafturi (1.336); următoarele 24 au procesat ~39.400 tichete și au scris ZERO, cinci zile la rând, fără ca nimeni să afle — pentru că nu există niciun watchdog, niciun heartbeat și niciun tichet marcat la eșec, deci același tichet eșuat reintră la fiecare rulare. Trei blocaje din memorie sunt REZOLVATE azi și nimeni nu le-a actualizat: `META_SYSTEM_TOKEN` are 29 de pagini cu task MODERATE și 31 de scope-uri (am citit LIVE textul reclamei cu preț pe 5 din 6 pagini FB testate, plus `is_hidden` pe comentarii), iar `ANTHROPIC_API_KEY` există și e validă în seif (11 modele vizibile) — SKILL.md încă scrie că tokenurile Meta sunt „de ADS, 0 pagini", ceea ce blochează decizii pe informație falsă. Am măsurat consumul real cu `count_tokens`: 6.908 tokeni input/tichet email; cu Claude Sonnet 5 + prompt caching + `--fast-triage` costul e 4,33 $/1.000 tichete, adică ~28 $ pentru tot backlogul de 6.513 tichete email+DM deschise azi — și, contraintuitiv, Sonnet 5 cu cache (7,25 $/1k) e mai IEFTIN decât Haiku 4.5 (8,91 $/1k), fiindcă Haiku are prag de cache 4.096 tokeni și prompturile noastre (1.910 și 3.960) nu-l ating. Două lucruri lipsesc tăcut din producție: `.learned_playbook.md` (vocea agenților) e în `.gitignore`, NU există pe VPS, deci injecția de voce e no-op de la prima zi; iar pe configurația actuală (`--ground`) istoricul cross-canal al clientului e mereu gol, deși oglinda CS locală (`cs_mirror.db`, 12.234 tichete, 24.656 mesaje, 2.179 replici reale de agent) stă pe același server și l-ar rezolva fără SSH.


### 0. Ce e sistemul, în 90 de secunde

**Ce face:** ia din Richpanel tichetele OPEN unde ultimul mesaj e de la client, le triază cu un LLM, construiește context (client, comenzi, poze, reclama de pe FB), scrie un **DRAFT** în Richpanel și rutează escaladările. **Nu trimite** implicit — agentul uman apasă Send.

**Fluxul pe un tichet** (`cs_auto_draft.py`, funcția `main()`, liniile 847-1244):

1. `list_conversations` (status=open, per_page=50, paginare cât apar tichete noi) → filtrează `last_message_sender_type == "customer"`
2. `--skip-tagged` → sare tichetele care au deja tagul AI (linia 965)
3. `get_conversation` (mode=audit, max 20 mesaje) → transcript, marchează explicit **ULTIMUL mesaj al clientului**
4. **Poze** (`--photos`, implicit PORNIT) → `cs_photo.py` descarcă atașamentele S3 + descrie vizual; la comentarii ia și poza/copy-ul reclamei
5. **Grounding** (`--ground`) → `lookup_orders()` (linia 513): comenzi din `metrics.orders` după email/telefon/nr-comandă + status/AWB/curier/SKU din `profitability.db` local, inclusiv **căutare după AWB** extras din mesaj
6. **Triaj LLM** (`IDENTIFY_SYS`) → JSON: problemă, categorie, limbă, severitate, `escalate`, `action`, `comment_action`, `spam`
7. **Gărzi deterministe** (independente de model): `NON_CUSTOMER_SENDER_RE` (curieri/app-uri), `JUDGEME_NOTIF_RE`, `SAAS_NOISE_SUBJ_RE`, `BOUNCE_RE`, `_padded_noise()` (spam); `_real_escalation()` (anti-supra-escaladare)
8. **Draft LLM** (`SYSTEM`, sau `HOLDING` la escaladare) → text în limba clientului
9. **Post-filtru anti-halucinare** (`HALLU`, linia 1158): dacă nu avem date de comandă și draftul afirmă status/preț/dimensiune/„am verificat" → regenerează 1× cu corectiv → dacă tot fabrică, șablon sigur
10. **Scriere**: `create_draft` + tag; la escaladare în plus `priority=HIGH` + taguri + `add_private_note` cu brief

**Fișierele (căi EXACTE):**

| Fișier | Linii | Rol |
|---|--:|---|
| `/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | 1.244 | motorul de backlog — cel important |
| `.../cs-draft-reply/cs_draft_reply.py` | 218 | o singură conversație, manual |
| `.../cs-draft-reply/grammar_audit.py` | 111 | gate de limbă/gramatică, offline |
| `.../cs-draft-reply/cs_ticket_index.py` | 136 | index semantic pe tichete rezolvate (folosit doar de `cs_draft_reply`) |
| `.../cs-draft-reply/build_voice_pack.py` | 134 | **MORT** — scrie `.voice_pack.json`, pe care nu-l citește nimeni |
| `.../cs-draft-reply/mcp_server.py` | 58 | server MCP `arona-cs-inbox` (strat subțire peste CLI) |
| `.../cs-draft-reply/SKILL.md` | 155 | documentația — **conține afirmații false azi**, vezi §5 |
| `.../cs-photo/cs_photo.py` | — | importat de `cs_auto_draft`; **identic** git↔VPS (`4fb6079ef80ac4e4`) |

**Pe VPS** (`root@84.46.242.181`, `/root/Scripturi/`): `cs_auto_draft.py`, `cs_photo.py`, `cs_backlog.sh` (wrapperul de cron, 2-iul), `cs_draft_all.sh` (varianta „eficiență" Haiku, 3-iul, **niciodată pusă în cron**), `data/cs_backlog.log`, `.auto_draft_proposals.json`.

### 1. Starea LIVE, verificată azi (15-sep-2026)

| Ce | Stare măsurată azi | Dovada |
|---|---|---|
| Cronul de draft | **PAUZAT din 29-iun-2026** | crontab linia 45: `# PAUZAT 2026-06-29 0 9-21/3 * * * ... cs_backlog.sh` |
| Cronul de triaj (`run_cs_pipeline`) | **ACTIV**, `*/30 8-16` + `0 2`, dar **fără `--llm`** (gratis) | crontab liniile 24, 26 |
| Oglinda CS (`run_cs_mirror.sh`) | **ACTIVĂ**, `0 2 * * *`, doar citire | crontab linia 201; ultima rulare 15-sep 03:00 |
| VPS vs git | **1 singură linie diferență** | `diff` = 2 linii; git are în plus `"61586834387211": "Lab Noir"` în `PAGE_STORE` |
| `ANTHROPIC_API_KEY` în seif | **DA, validă** (HTTP 200, 11 modele vizibile) | `GET /v1/models` cu cheia din KB |
| `OPENAI_API_KEY` pe VPS | **validă** (HTTP 200, 130 modele, `gpt-4o-mini` prezent) | `GET /v1/models` din `.env` |
| `META_SYSTEM_TOKEN` | **29 pagini, 31 scope-uri, nu expiră** | `debug_token` + `/me/accounts` |
| `profitability.db` (grounding) | **PROASPĂTĂ** — `max(created_at)=2026-09-15T00:29Z`, 843.459 comenzi, 804.057 cu AWB | interogare sqlite RO |
| `metrics.orders` | **PROASPĂTĂ** — 368.231 comenzi, max 15-sep 07:00Z | MCP postgres-metrics |
| `.learned_playbook.md` pe VPS | **NU EXISTĂ** (`find /` = 0 rezultate) | vezi §8 |
| Backlog Richpanel OPEN | **12.499** (5.874 comentarii FB, 3.035 email, 1.411 FB msg, 1.385 messenger, 598 widget, 112 IG cmt, 84 IG msg) | `metrics.richpanel_tickets` |

**Notă importantă despre „VPS diferă de git":** hash-urile chiar diferă (`b5f474232b0ad203` vs `57631aebe2f27af1`), dar diferența reală e **o singură linie** — maparea paginii Lab Noir. Nu e drift periculos; e o publicare (PR #397, 8-iul) care n-a fost redeployată. Se rezolvă cu `deploy.sh --apply`, **nu** cu `scp` (scp-ul manual e cauza istorică a divergențelor git↔VPS).

### 2. Ce a rulat de fapt — jurnalul, măsurat

`/root/Scripturi/data/cs_backlog.log` = 12 MB, 137.095 linii, **25 de rulări** între 24-iun 15:39 și 2-iul 11:36.

```
RULARE               tichete  drafturi  invalide
2026-06-24 15:39:59    1560      1336        0     <-- SINGURA care a produs ceva
2026-06-24 21:00:01    1647         0      120
2026-06-25 09:00:01    1648         0      138
...  (20 de rulări identice, 0 drafturi) ...
2026-06-29 12:00:01    1335         0      290
2026-07-02 10:00:06      46         0       21
```

**Totaluri:** 40.724 tichete procesate · **1.336 drafturi** (3,3%) · 6.015 „draft invalid → sar tichetul" · 12.045 linii „eroare LLM".

**Distribuția erorilor — aici e povestea:**

| Eroare | Nr. | Cauză |
|---|--:|---|
| `[Errno 2] No such file or directory: 'uv'` (triaj) | 10.933 | `secret()` chema `uv run kb.py`; `uv` nu e în PATH-ul cron |
| `[Errno 2] ... 'uv'` (draft) | 6.009 | idem |
| `HTTP Error 429` | 21+26 | contenție OpenAI cu `richpanel_llm --llm` (2-iul) |
| `HTTP Error 520` | 1 | tranzitoriu |

**Ce trebuie să reții:** bugul `uv` e **reparat** (linia 227, `secret()` are try/except + cache). Dar defectul de sistem NU e reparat:

1. **Nimic nu a observat 5 zile de output zero.** 24 de rulări × ~45 min, ✅ verde în cron, 0 drafturi.
2. **Tichetul eșuat nu e marcat.** Garda (liniile 1213-1215) refuză corect să scrie gunoi în Richpanel — dar nu pune tag, nu incrementează un contor. Deci la următoarea rulare `--skip-tagged` îl ia din nou. Rezultat: **39.388 cicluri irosite**, iar contorul „invalide" crește monoton de la 120 la 350 pe rulare = aceleași tichete otrăvite, la nesfârșit.
3. **Coada de propuneri nu se curăță niciodată.** `/root/Scripturi/.auto_draft_proposals.json` = 7,3 MB, 3.156 intrări, 0 `applied`, 0 `sent`, 328 escaladate, fiecare cu draft + context de comandă = **PII**, din iunie.

### 3. Meta: ce se poate face AZI (testat live, doar citire)

Memoria zicea: „`META_USER_TOKEN`=REVOCAT; `META_SYSTEM_TOKEN`=ADS, 0 pagini; doar `_3` are 2 pagini". **Fals azi.** Măsurat pe 15-sep:

| Secret | Valid | Tip | Expiră | Scope-uri | Pagini |
|---|---|---|---|--:|--:|
| `META_SYSTEM_TOKEN` | DA | SYSTEM_USER | **niciodată** (`expires_at=0`) | **31** | **29** |
| `META_SYSTEM_USER_TOKEN` | DA | SYSTEM_USER | niciodată | 31 | 29 |
| `META_SYSTEM_TOKEN_2` | DA | SYSTEM_USER | niciodată | 4 | 0 |
| `META_SYSTEM_TOKEN_3` | DA | SYSTEM_USER | niciodată | 9 | 2 |
| `META_SYSTEM_TOKEN_4` | DA | SYSTEM_USER | niciodată | 3 | 0 |
| `META_USER_TOKEN` | **NU** | — | — | 0 | 0 (valoarea e literal `REVOKED_pasted_by_mistake_2026-06`) |

`META_SYSTEM_TOKEN` are, pe **toate** cele 29 de pagini, `tasks = CREATE_CONTENT, MODERATE, MESSAGING, ADVERTISE, ANALYZE` și scope-urile relevante: `pages_manage_engagement`, `pages_read_user_content`, `pages_messaging`, `pages_manage_posts`, `instagram_manage_comments`, `instagram_manage_messages`. (`META_SYSTEM_TOKEN` și `META_SYSTEM_USER_TOKEN` par a fi **același token salvat de două ori** — același app `1268707461439970`, aceleași 29 de pagini, aceleași 31 de scope-uri.)

### Verdictul pe cele TREI lucruri din memorie

| Capabilitate | Stare azi | Dovadă |
|---|---|---|
| **Context postare** (AI-ul să știe produsul+prețul din reclamă) | ✅ **MERGE ACUM**, pe FB | 5 din 6 pagini FB testate au întors textul reclamei |
| **Hide comentariu** | ✅ **preconditii îndeplinite** — NEtestat (regulă: doar citire) | task `MODERATE` + `pages_manage_engagement` + am citit `is_hidden` pe comentarii reale |
| **Send live pe comentariu / DM** | tehnic posibil, **decis NU** (draft-only, decizie user 23-iun) | scope `pages_messaging` prezent, dar decizia rămâne |

### Ce am citit efectiv (tichete reale din oglindă, doar GET pe Graph)

```
#333841 Reduceri bune  -> POSTARE: "🔪🥕 Gătește curat... Tocătorul din bambus cu tavă din oțel..."
                          COMENTARIU: is_hidden=True | "Argus nu aduce coletul..."
#333832 Bonhaus CZ     -> POSTARE: "😲 Odstraní prach, nečistoty a oleje..."
                          COMENTARIU: is_hidden=False | "Právě jsem objednala tak jsem zvědavá"
#333827 Esteban        -> POSTARE: "💖 Cumperi 2, primești 1 GRATIS ... Livrare GRATUITĂ peste 150 Lei"
#333835 Nocturna Lux   -> POSTARE: "😍 Set 5 Pijamale din Satin Cele mai Mici prețuri - 99 Lei"
#333834 Ofertele Zilei -> FĂRĂ TOKEN (pagina 364899953373966 nu e în cele 29)
#333801 Esteban (IG)   -> POSTARE GOALĂ (derivarea post_id nu merge pe IG)
#333720 Nubra (IG)     -> POSTARE GOALĂ (idem)
```

**Deci:** pe comentariile FB, AI-ul poate ști acum **produsul și prețul din reclamă** — exact contextul care lipsea și care făcea draftul să întrebe „despre ce produs e vorba?".

### Trei defecte concrete descoperite aici

**a) Ordinea tokenurilor în `fb_page_token` (linia 681) alege tokenul SLAB.**
```python
for key in ("META_PAGES_TOKEN", "META_SYSTEM_TOKEN_3", "META_SYSTEM_TOKEN", ...)
```
`META_SYSTEM_TOKEN_3` e încercat ÎNAINTEA celui bun. Pe pagina Nubra (`582569158278392`), `_3` are task **doar `ADVERTISE`** — deci întoarce un token de pagină care NU poate modera. Măsurat în test: Nubra a primit tokenul via `_3`. `META_SYSTEM_TOKEN` (29 pagini, MODERATE) trebuie să fie **primul**. `META_PAGES_TOKEN` nici nu există în seif — un apel Graph irosit pe fiecare pagină necunoscută.

**b) 7 pagini cu trafic real nu sunt acoperite de niciun token — 18,2% din volumul social.**

| page_id | tichete/19z | în cod? | în token? |
|---|--:|---|---|
| `364899953373966` (Ofertele Zilei, a 2-a pagină) | 962 | DA | **NU** |
| `814175968452902` | 189 | **NU** | **NU** |
| `898588036681214` | 129 | **NU** | **NU** |
| `336777842857489` | 76 | **NU** | **NU** |
| `995674783622734` / `516792924847762` / `425006144024872` | 66 | **NU** | **NU** |

În plus, **9 pagini cu trafic lipsesc din `PAGE_STORE`** (liniile 60-68) = 671 tichete/19z (8,6%) care nu primesc brandul corect. Iar `PAGE_STORE` conține 4 pagini pe care tokenul nu le vede deloc: `364899953373966`, `1678573069021466` (Orașul Verde), `1805415543098993` (Rossi Nails, id vechi — cel nou e `122095975544011424`), `61586834387211` (Lab Noir).

**c) Instagram: derivarea `post_id` e greșită.** Codul ia `segs[-2]` din id-ul tichetului. Pe FB id-ul e `{page}_{post}_{post}_{comment}` și merge. Pe IG e `{media}_{comment}` (ex. `17942163597036761_18111903014089292`) → `segs[-2]` = media id, iar `fb_post_text` încearcă `{page}_{media}` → gol. **0 din 2 teste IG au reușit.** IG = 292 tichete de comentariu/19z.

### 4. Modelul și costul — măsurat, nu estimat

Am măsurat consumul real cu endpointul **gratuit** `count_tokens` (nu generează tokeni, nu costă):

| Bloc | Tokeni (măsurat) |
|---|--:|
| `IDENTIFY_SYS` (system triaj) | **1.910** |
| `SYSTEM` (system draft) | **3.960** |
| `HOLDING` (escaladare) | 284 |
| Apel TRIAJ pe un email real (system+context) | **2.429** |
| Apel DRAFT pe același email | **4.479** |
| Total input / tichet email (2 apeluri) | **6.908** |
| Playbook injectat (când există) | **+636** |
| Total input / comentariu | **6.414** |

(Output estimat: ~150 tok triaj + ~250 tok draft = ~400/tichet email, ~220/comentariu. `max_tokens=900`.)

### Costul, cu prețurile curente

**$ / 1.000 tichete email+DM** (2 apeluri):

| Model | fără cache | **cu cache** | prag cache |
|---|--:|--:|--:|
| `gpt-4o-mini` (azi, pe cron) | **1,28** | — (codul nu face caching pe OpenAI) | — |
| `claude-haiku-4-5` | 8,91 | **8,91** (nu se cache-uiește!) | 4.096 tok |
| `claude-sonnet-5` | 17,82 | **7,25** | 1.024 tok |
| `claude-sonnet-4-6` (default în cod) | 26,72 | **10,88** | 1.024 tok |
| `claude-opus-5` | 44,54 | **18,13** | 512 tok |
| `gpt-4o` | 21,27 | — | — |

### Descoperirea contraintuitivă

**Sonnet 5 cu caching (7,25 $/1k) e mai IEFTIN decât Haiku 4.5 (8,91 $/1k).** Motivul: prompturile noastre au 1.910 și 3.960 tokeni, iar Haiku 4.5 are prag minim de cache **4.096** — deci NICIUNUL dintre cele două system-prompturi nu se cache-uiește pe Haiku. Pe Sonnet 5 (prag 1.024) se cache-uiesc **amândouă**, iar citirea din cache costă 0,1×.

> ⚠️ **Comentariul din cod (linia 300) e greșit pe două puncte, verificabile:** zice „Sonnet 4.6 = 2048 tok" (real: **1.024**) și „SYSTEM~2.6k / IDENTIFY~1.3k" (real: **3.960** și **1.910**). Concluzia lui — „se cache-uiește doar SYSTEM pe Sonnet" — e falsă: pe Sonnet 4.6/5 se cache-uiesc ambele.

Deci decizia din 3-iul (Haiku 4.5 „3× mai ieftin ca Sonnet") era corectă față de **Sonnet 4.6 fără cache**, dar e **inversă** față de Sonnet 5 cu cache.

### Scenarii pe volumul REAL

Backlog OPEN azi: **6.513** email+DM · **5.986** comentarii. Flux nou: **258/zi** email+DM · **376/zi** comentarii.

| Configurație | Backlog email+DM (6.513) | 30 zile incremental |
|---|--:|--:|
| `gpt-4o-mini` (azi) | 8,31 $ | 9,88 $ |
| `claude-sonnet-5` + cache | 47,22 $ | 56,12 $ |
| `claude-sonnet-5` + cache + `--fast-triage` | **~28 $** | **~34 $** |
| `claude-haiku-4-5` | 58,02 $ | 68,95 $ |
| `claude-sonnet-4-6` + cache (default în cod) | 70,83 $ | 84,17 $ |
| `claude-opus-5` + cache | 118,05 $ | 140,29 $ |

\+ comentariile, pe Sonnet 5 cu cache: 26,71 $ backlog / 50,33 $ pe lună.

**Ce se câștigă cu Claude:** (a) respectarea regulilor anti-supra-afirmare — problema măsurată pe Haiku la smoke-test (3-iul: „am trecut solicitarea de anulare", „returnat pe metoda de plată" la o comandă COD); (b) **cheie separată de OpenAI** → dispare contenția 429 cu `richpanel_llm --llm` care a omorât rularea din 2-iul; (c) prompt caching, care nu există pe calea OpenAI din cod.

### Capcane de model, concrete

- `cs_auto_draft.py` liniile 301 și 334: default `ANTHROPIC_MODEL = "claude-sonnet-4-6"` — de mutat pe `claude-sonnet-5` (mai nou, mai ieftin: 2/10 vs 3/15 $/MTok).
- **`llm()` preferă Claude dacă găsește cheia.** Cheia e acum în KB, iar `secret()` cade pe `uv run kb.py`. Deci **rularea LOCALĂ pe Mac comută tăcut de pe `gpt-4o-mini` pe `claude-sonnet-4-6`** — alt model, alt cost, alt comportament, fără niciun flag. Pe VPS `cs_backlog.sh` NU exportă `ANTHROPIC_API_KEY`, iar `uv` nu e în PATH-ul cron → rămâne pe OpenAI. Cele două medii nu rulează același model.
- **BUG în `cs_draft_reply.py:83`:** ramura Anthropic citește `DRAFT_MODEL` (nu `ANTHROPIC_MODEL`). Wrapperele exportă `DRAFT_MODEL=gpt-4o-mini` → dacă rulezi `cs_draft_reply.py` cu acele variabile și cheia Claude accesibilă, trimite `model="gpt-4o-mini"` la `api.anthropic.com` → 404 → `SystemExit`. `cs_draft_reply.py` nici nu are retry/backoff (doar `cs_auto_draft._llm_http` are).
- `grammar_audit.py:39` merge **doar pe OpenAI** (`AUDIT_MODEL`, default `gpt-4o`) — nu are ramură Claude. Dacă muți motorul pe Claude, gate-ul de gramatică rămâne pe OpenAI.

### 5. Throughput și rate-limit — ce s-a schimbat

Concluzia veche (24-iun): *„config curat = 2 workeri lean, `--sleep 0.8`, gâtul e OpenAI nu Richpanel"*. **Ambele jumătăți s-au schimbat.**

### Richpanel — măsurat azi
Oglinda CS a rulat pe 15-sep 03:00: **208 apeluri în 271,6 s = 48 cereri/min, 0× HTTP 429**. Wrapperul folosește deliberat `--max-rpm 45` „ca să lase rezervă CS-ului de gardă". Deci Richpanel susține ~48/min noaptea, cu zero refuzuri — **NU e limita de 429 pe READ pe care o descria memoria din iunie** (aceea era paginare în rafală, fără throttle).

### Bugetul de apeluri al motorului de draft
Per tichet: 1 `get_conversation` + 1 `create_draft` + 1-2 `add_tags` (+ `list_conversations` la 50 tichete) ≈ **3-4 apeluri**.

Viteza măsurată din jurnal: rularea de 24-iun a procesat 1.560 tichete în 3h02m20s = **7,0 s/tichet** = 8,56 tichete/min pe **un** worker → **~30 apeluri Richpanel/min**.

| Workeri | Tichete/min | Apeluri Richpanel/min | Verdict |
|--:|--:|--:|---|
| 1 | 8,6 | ~30 | sigur, lasă rezervă CS-ului |
| 2 | 17 | ~60 | **atinge plafonul de 60/min, partajat cu CS-ul live** |
| 3+ | 26+ | 90+ | 429 pe Richpanel |

**Deci gâtul s-a MUTAT: nu mai e OpenAI, e Richpanel.** Cele 429-uri din jurnal (21+26) au apărut pe 2-iul, când `richpanel_llm --llm` recupera backfill-ul pe **aceeași cheie OpenAI** — iar acel cron rulează azi **fără `--llm`** (verificat în crontab), deci contenția nu mai există. Cu cheie Claude (cotă separată), dispare și teoretic.

### Recomandare de rulare
- **Backlog (o dată):** 6.513 tichete ÷ 8,56/min = **12,7 h pe un worker**. Rulează-l **noaptea** (fereastra 22:00-07:00), 1-2 workeri, `--sleep 0.3`. La 2 workeri: ~6,3 h, dar doar în afara programului CS.
- **Incremental (zilnic):** 258 tichete/zi = **30 min/zi pe un worker**. Un worker e suficient cu marjă.
- `--fast-triage` taie **40%** din cost (1 apel în loc de 2) și ~35% din timp, fără să atingă gărzile deterministe de spam/escaladare.

### 6. Grounding — ce vede AI-ul azi și ce ratează

### Ce vede azi (`--ground`, `lookup_orders()` linia 513)

| Sursă | Ce dă | Stare |
|---|---|---|
| `metrics.orders` (Postgres) | comenzi după **email / telefon / nr-comandă**: name, totalPrice, financialStatus, dată | 368.231 rânduri, la zi (15-sep 07:00Z) |
| `profitability.db` (SQLite LOCAL pe VPS) | `status_category`, `skus`, `awb`, `courier_key`; **căutare și DUPĂ AWB** din mesaj | 843.459 comenzi, 804.057 cu AWB, la zi (15-sep 00:29Z) |

**AWBprint NU e folosit** de `--ground`, deci avertismentul „AWBprint e STALE post-cutover" **nu-l atinge**. Calea de grounding e sănătoasă azi.

### Ce ratează — trei goluri măsurabile

**a) Order Hub nu e folosit deloc, deși oglinda lui e pe ACELAȘI server.**
`/root/Scripturi/data/oh_mirror.db` (33 MB, actualizat azi la **12:08**) conține:
- `oh_orders`: 57.171 comenzi — `order_name, store, status, courier, cod_value, cod_currency, cancelled, **lines_json**`
- `oh_awbs`: 55.762 AWB-uri — `awb, order_name, courier, items_json`

Statusurile OH sunt cele **operaționale reale**: `✅ Livrată` (27.566), `🚚 În curs de livrare` (7.585), `✈️ Procesată` (6.170), `❌ Refuzată` (4.348), `❌ Anulată` (1.182), `⏰ Netrimisă (Alertă)` (424), `🚦 On Hold` (198).

Ce ar câștiga draftul, concret:
1. **Produsul din comandă**, nu doar SKU-urile: `lines_json` = `[{"sku":"DUP-175","title":"№ 175 — Lay It On","qty":1}, ...]`. Azi draftul primește `skus` trunchiat la 40 de caractere.
2. **Statusuri pe care `profit_orders` nu le are**: `On Hold`, `Netrimisă (Alertă)`, `Anulată` — exact cazurile unde un WISMO generic e greșit.
3. **Contradicția cu clientul**: „am refuzat coletul" vs `❌ Refuzată` — se poate confirma, nu presupune.

**b) Istoricul cross-canal e MEREU GOL pe configurația de cron.** La `--ground` (liniile 999-1006), `other = []` rămâne gol, deci:
- `A MAI SCRIS PE:` = literal `"grounded — 3 comenzi găsite în DB"` — text fără sens, care ajunge **și în nota de escaladare** citită de agentul CS (`escalation_note`, linia 834)
- `ALTE TICHETE:` = `"(fără alte tichete)"` **întotdeauna**

Calea care chiar aduce istoricul (`customer_ident()`, linia 500) face `subprocess uv run` + SSH → nu merge pe cron, de-aia a fost ocolită. **Dar `cs_mirror.db` e local și rezolvă asta fără SSH:** 12.234 tichete, acoperire `customer_phone` 67,5% / `customer_email` 32,3% / `customer_id` 100%, cu indecși pe canal și dată. În 19 zile există **934 de grupuri de tichete cu același telefon** și **262 cu același email** — clienți care au scris de mai multe ori și pe care AI-ul nu-i vede deloc.

**c) `metrics.orders` (368k) e un subset față de `profit_orders` (843k)**, iar `profit_orders` **nu are email/telefon**. Deci un client fără nr. de comandă și fără AWB în mesaj, a cărui comandă e doar în `profit_orders`, rămâne negăsit.

### 7. Vocea: `.learned_playbook.md` — funcția e no-op în producție

**Verdict scurt: fișierul există doar pe Mac, nu e în git, nu e pe VPS, deci injecția de voce n-a rulat NICIODATĂ pe cron.**

| Întrebare | Răspuns măsurat |
|---|---|
| Există local? | DA — `.../cs-draft-reply/.learned_playbook.md`, 17.884 octeți |
| Cât de vechi? | **23-iun-2026 = 84 de zile** |
| E în git? | **NU** — `.gitignore` linia 6: `.learned_playbook.md` |
| E pe VPS? | **NU** — `find / -name ".learned_playbook.md"` = 0 rezultate |
| E folosit? | `load_playbook()` (linia 616) citește `os.path.join(HERE, ...)`; pe VPS `HERE=/root/Scripturi` → fișier lipsă → `LEARNED = {}` → la linia 1136 `learned = ""` → **blocul nu se injectează** |
| Cât de bogat e? | 9 categorii, fiecare **din 8-10 tichete**. Total: **82 de tichete** |

Categoriile: `LIVRARE_WISMO`, `RETUR`, `ANULARE`, `PROBLEMA_PRODUS`, `MODIFICARE_COMANDA`, `SCHIMB_SWAP`, `PRESALE_INTREBARE` (doar 4 tichete), `PLATA_FACTURA`, `REFUZ_LIVRARE`.

### Materia primă pentru o versiune mult mai bună există deja, locală

`cs_mirror.db` conține **2.179 de replici reale de agent** de 60-900 caractere, din ultimele 19 zile:

| Canal | Replici agent |
|---|--:|
| email | 761 |
| facebook_feed_comment | 649 |
| facebook_message | 646 |
| email_from_widget | 84 |
| instagram_message | 31 |

Exemple reale (vocea autentică, cu tot cu diacritice lipsă și fraze scurte):
> „Buna ziua, Aveti o comanda EST000003 plasata ieri dar nu e platita ci cu plata ramburs la cureir. Livrarea se face in 2 zile lucratoare. Si mai e una din 19 august platita si livrata. Multumim"

> „Dobrý den, mockrát se omlouváme, ale Vaši objednávku se nám již bohužel nepodařilo zrušit včas..." *(cehă — few-shot multilingv, gratis)*

> „Buna ziua, Comanda dv a fost plasata pt o adresa din Sibiu. Doriti sa corectam adresa? Nu intelegem la ce adresa va referiti."

**Costul injectării:** +636 tokeni/tichet măsurat = pe Sonnet 5 cu cache, 7,25 → 8,52 $/1k (+17%).

**Atenție la politică:** playbook-ul trebuie să codifice regulile REALE ARONA, nu presupuneri — vezi memoria `cs-procedures-learn-not-assume`: returul NU se încurajează (mai ales igienă/parfum desigilat); refund = **doar valoarea produselor**, transportul NU se returnează; la anulare se anulează ÎNTÂI AWB-ul în xConnector, apoi comanda în Shopify.

### 8. Trimitere live: ce EXISTĂ deja și ce lipsește ca gate

### Ce există deja în cod (contrar impresiei „nu putem trimite")

| Mecanism | Unde | Ce face |
|---|---|---|
| `--send <conv>` | `do_send()`, linia 804 | trimite UN tichet, refuză dacă `sent`/`escalate`/`hide`, apoi **închide** tichetul (CLOSED) |
| `--apply-send` | argument linia 862, execuție 1216-1226 | **trimite ÎN MASĂ** în bucla principală + închide tichetele |
| `send_message` | metodă MCP Richpanel | **există pe server** (21 unelte), e doar ascunsă din registry-ul Claude; apelabilă prin JSON-RPC |

**Deci mecanismul e complet construit.** Nu lipsește codul de trimitere — lipsesc gărzile.

### Gate-urile care EXISTĂ azi pe `--apply-send`
1. `not is_esc` — nu trimite escaladări (`_real_escalation` determinist + verdictul LLM)
2. `not is_public` — nu postează pe comentarii
3. draftul nu începe cu `(eroare` și are ≥5 caractere
4. spam short-circuit mai devreme în buclă

### Gate-urile care LIPSESC (fiecare e o cale prin care un mesaj greșit ajunge la client)
1. **Niciun prag de încredere.** `idn["confidence"]` se calculează dar nu blochează nimic.
2. **Niciun gate de grounding.** Un draft fără date de comandă poate pleca. Minim: `has_order_data(od_ctx) == True` pentru orice răspuns care afirmă ceva despre o comandă.
3. **Niciun gate de limbă/gramatică inline.** `grammar_audit.py` e offline, pe alt model, rulat manual.
4. **Nicio listă albă de categorii.** `recenzie_feedback`, `presale_intrebare`, `livrare_wismo` cu status confirmat sunt sigure; `retur`, `refund`, `problema_produs`, `anulare` nu sunt.
5. **Niciun plafon de rată** („maxim N trimiteri/oră"). Un bug scrie la 6.513 clienți.
6. **Niciun buton de oprire.** Nu există fișier-kill-switch verificat în buclă.
7. **Niciun audit per mesaj trimis.** Doar `print` în log + tagul `ai-sent`.
8. **Niciun rollback — și nici nu poate exista.** `send_message` e ireversibil, iar MCP-ul **nu are delete-draft și nu are delete-note** (verificate toate cele 21 de metode). Un draft greșit nu se poate șterge prin API; curățarea e MANUALĂ din UI.

### Ce ar schimba decizia (draft-only → send)
Ordinea sănătoasă: **măsurare întâi, trimitere după.** Ai nevoie de o cifră pe care azi nu o ai: **ce procent din drafturi trimit agenții NEMODIFICAT**. Oglinda CS o poate produce — compari draftul salvat cu mesajul de agent trimis ulterior pe același tichet. Dacă rata de „trimis identic" depășește un prag agreat (ex. 85%) pe o categorie, acea categorie devine candidată la auto-send; sub prag, rămâne draft. Asta transformă decizia dintr-o presupunere într-o măsurătoare.

### 9. Măsurarea efectului — azi e ZERO

**Nu există niciun watchdog, niciun heartbeat, nicio metrică pe acest sistem.** Dovada: pe VPS sunt **27 de joburi cablate pe `heartbeat.py`** (`profit_sync`, `build_cache`, `fx_sync`, `token_expiry`, `reconcile`, …) și **niciunul** nu e `cs_auto_draft`/`cs_backlog`. `data_health.py` (427 linii) are 6 verificări — `check_metrics`, `check_awbprint`, `check_awb_output`, `check_profitdb`, `check_heartbeats`, `check_gads_feed` — niciuna pe draft.

De-aia 5 zile de output zero au trecut neobservate.

### Ce trebuie măsurat, în ordinea utilității

**Nivel 1 — „rulează?" (prinde exact incidentul din iunie)**

| Metrică | Prag de alarmă | Sursa |
|---|---|---|
| heartbeat `cs_draft` la fiecare rulare reușită | lipsă >6 h în fereastra activă | `heartbeat.py` |
| drafturi salvate / rulare | **= 0 la o rulare cu >50 tichete → ROȘU** | parsare log |
| rată de eșec LLM | >5% din tichete | parsare log |
| tichete sărite ca „draft invalid" | >2% sau în creștere 3 rulări la rând | parsare log |
| vechimea `.learned_playbook.md` | >30 zile | `stat` |

**Nivel 2 — „e bun?" (asta decide dacă se poate trimite vreodată)**

| Metrică | Cum se măsoară |
|---|---|
| **rată de acceptare** = % drafturi trimise NEMODIFICAT | `cs_mirror.db`: compari draftul cu mesajul de agent trimis ulterior pe același `ticket_id` |
| **rată de editare** = distanță de editare draft ↔ mesaj trimis | idem, Levenshtein normalizat |
| **rată de abandon** = drafturi pe tichete închise fără răspuns | `rp_ticket.closed_at` + absența mesajului de agent |
| **timp până la primul răspuns (FRT)** cu AI vs fără | `rp_ticket.first_responded_at`, split pe tagul `ai-draft` |
| **rată de redeschidere** a tichetelor cu draft AI | tichet CLOSED care primește mesaj nou de client |
| **halucinații prinse de post-filtru** | contor pe `HALLU.search()` + regenerări |
| **acoperire grounding** = % tichete cu ≥1 comandă găsită | contor `len(orders)>0` |

**Nivel 3 — bani**

Loghează `usage` din răspunsul API (`input_tokens`, `output_tokens`, `cache_read_input_tokens`) per tichet. Pe Claude, dacă `cache_read_input_tokens` e 0 la rulări repetate, caching-ul nu prinde — și plătești 2,5× degeaba, tăcut.

**Unde se pun:** un tabel `cache.cs_draft_runs` în metrics (rulare, canal, tichete, drafturi, erori, tokeni, cost) + un check în `data_health.py` + heartbeat în wrapper. Costă o zi de lucru și elimină întreaga clasă de eșec tăcut.

### 10. Cod mort, datorie tehnică, capcane de proces

### Cod mort (măsurat prin numărare de referințe)

| Ce | Unde | Dovadă |
|---|---|---|
| `fb_private_reply()` | `cs_auto_draft.py:737-763` (27 linii) | **1 singură apariție** a numelui în tot fișierul = doar definiția. Scoasă când s-a decis „fără DM-uri" (23-iun), lăsată „pentru când avem token de pagină". **Tokenul există acum** — deci ori se reactivează deliberat, ori se șterge. Nu o lăsa în limbo. |
| `build_voice_pack.py` + `.voice_pack.json` | 134 linii + fișier de **2 octeți** (`{}`) | `grep voice_pack *.py` → referințe doar în propriul fișier. Nimeni nu-l citește. Suprapus funcțional peste `.learned_playbook.md`. |
| `META_PAGES_TOKEN` | `fb_page_token`, linia 681 | secret inexistent în KB; un apel Graph irosit pe fiecare pagină nouă |
| `META_SYSTEM_USER_TOKEN` | KB | duplicat al lui `META_SYSTEM_TOKEN` (același app, aceleași 29 pagini, aceleași 31 scope-uri) și nici nu e în lista încercată de `fb_page_token` |

**TODO/FIXME în cod: ZERO.** (Căutat în toate fișierele skill-ului.)

### Datorie tehnică

1. **`.auto_draft_proposals.json` crește la infinit** — 7,3 MB / 3.156 intrări pe VPS, 1,5 MB local, zero curățare, conține drafturi + context de comandă = **PII**. `save_queue()` rescrie tot fișierul la fiecare tichet: O(n²) pe rulări mari.
2. **`--apply-send` nu e documentat în SKILL.md** — un flag care trimite în masă la clienți, absent din documentație. `--send` (per tichet) e documentat.
3. **SKILL.md conține afirmații FALSE azi** (§ Pipeline per tichet, pasul 7): *„tokenurile actuale sunt de ADS (0 pagini, exceptând Nubra+Covoria prin `META_SYSTEM_TOKEN_3`)"* și *„Apply-paths... de validat live o dată"*. Cineva care citește asta concluzionează greșit că moderarea e blocată.
4. **Zgomotul Meta primește în continuare draft** — filtrele `SAAS_NOISE_SUBJ_RE` prind „mentioned you on", „liked your", dar notificările Meta Business trec (măsurat în memorie; regula e după subiect, nu după expeditor `@facebookmail.com`).
5. **Eticheta de canal „?×N"** — `CH_LABEL` (linia 145) n-are toate canalele; dar problema reală e că pe `--ground` blocul nici nu se construiește (§6b).
6. **`messenger` are `to_id` GOL** pe toate cele 302 tichete măsurate → `PAGE_STORE` nu poate rezolva magazinul; se cade pe fallback.

### Capcane de proces — citește-le înainte să atingi ceva

1. 🔴 **`create_draft` ADAUGĂ, nu suprascrie**, și **NU există delete-draft prin API**. O re-rulare pe aceleași tichete **stivuiește** drafturi. Curățarea = manuală, din UI Richpanel. În iunie s-au stivuit 13+ drafturi „(eroare LLM)" exact așa.
2. 🔴 **Orice schimbare de prompt face STALE drafturile deja scrise.** Finalizează promptul, validează în dry-run, apoi **o singură** scriere curată. Pentru demo folosește dry-run.
3. 🔴 **`gigi:publish-skill` face `git checkout main` și ARUNCĂ working tree-ul nesalvat.** A distrus o dată editările de căutare-după-AWB, local ȘI pe VPS. **Commit înainte de publish**, și `grep` de verificare după.
4. 🔴 **Deploy pe VPS = `deploy.sh --apply`**, NU `scp`. Scp-ul manual e cauza istorică a divergențelor git↔VPS.
5. 🟡 **Richpanel: 60 cereri/min PARTAJATE cu CS-ul live.** Nu paraleliza peste 2 workeri; preferă noaptea.
6. 🟡 **Stațiile CS + depozitul sunt pe Windows (cp1252)** — orice script nou care printează diacritice are nevoie de `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`.
7. 🟡 **`add_tags_to_conversation` acceptă DOAR id-uri de tag (UUID), nu nume**, și eșuează TĂCUT pe nume. Helper-ul `tag_id()` (linia 580) rezolvă asta — nu-l ocoli. Înrudit: `list_tags` fără `query` întoarce doar primele 25 din ~1.900 → caută mereu cu `query`.
8. 🟡 **Nu rula `cs_auto_draft` cu `--create-draft`/`--send`/`--approve` ca test.** Fiecare rulare scrie ireversibil în Richpanel. Dry-run e implicit — păstrează-l așa.

### 11. Runbook: comenzile exacte

```bash
# ---------- LOCAL (Mac) ----------
cd /Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply

# DRY-RUN (nu scrie nimic) — ATENȚIE: pe Mac cheia Claude e în KB → folosește claude-sonnet-4-6, NU gpt-4o-mini
uv run cs_auto_draft.py --limit 5 --channel email --ground

# forțează motorul explicit, ca să știi ce plătești
ANTHROPIC_MODEL=claude-sonnet-5 uv run cs_auto_draft.py --limit 5 --ground
OPENAI_API_KEY=... DRAFT_MODEL=gpt-4o-mini uv run cs_auto_draft.py --limit 5 --ground   # forțează OpenAI

# audit de limbă/gramatică pe drafturi (offline, fără scriere)
uv run cs_auto_draft.py --limit 20 --json 2>/dev/null | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/d.json
uv run grammar_audit.py --file /tmp/d.json

# regenerează playbook-ul învățat (are nevoie de data/richpanel_tickets.db)
PROC_MODEL=gpt-4o uv run ../cs-procedures/cs_procedures.py --category all --out .learned_playbook.md

# ---------- VPS (root@84.46.242.181) ----------
# scripturile se rulează așa (heredoc-urile inline se strică):
ssh root@84.46.242.181 "bash -s" < /tmp/script_local.sh

# repornirea cronului = decomentează linia 45 din crontab:
#   0 9-21/3 * * * /usr/bin/flock -n /tmp/cs_backlog.lock /root/Scripturi/cs_backlog.sh >> /root/Scripturi/data/cs_backlog.log 2>&1
# NU o reporni înainte de watchdog + dead-letter (§9, §2).
```

### Toate flag-urile (`cs_auto_draft.py`)

| Flag | Implicit | Ce face |
|---|---|---|
| `--limit N` | 15 | câte tichete procesează |
| `--scan N` | 150 | câte tichete scanează la listare |
| `--channel X` | toate | `email`, `facebook_message`, `messenger`, `email_from_widget`, `instagram_message`, `facebook_feed_comment`, `instagram_comment` |
| `--create-draft` | off | **SCRIE** drafturi + rutare escaladare |
| `--ground` | off | grounding self-contained (metrics + profitability.db), fără SSH/uv — **config de cron** |
| `--lean` | off | fără 360, fără rutare escaladare (mai rapid, mai sărac) |
| `--fast-triage` | off | sare triajul LLM când regex-ul e sigur → 1 apel în loc de 2 (**−40% cost**) |
| `--skip-tagged` | off | sare tichetele care au deja tagul → idempotent |
| `--no-comments` | off | exclude comentariile FB/IG |
| `--photos` / `--no-photos` | **PORNIT** | vede pozele clientului + reclama |
| `--tag X` | `ai-draft` | tagul pus; `--tag ""` = fără tag |
| `--sleep S` | 0.2 | pauză între tichete |
| `--only 1,2,3` | — | regenerare țintită |
| `--json` | off | drafturi structurate după marcajul `@@JSON@@` |
| `--close-spam` | off | închide + taghează spamul |
| `--actions ...` | `modify,cancel,swap,resend` | ce acțiuni se propun |
| `--approve N --agent X` | — | ⚠️ **APLICĂ** acțiunea propusă (mutație reală pe comandă) |
| `--send N` | — | 🔴 **TRIMITE LIVE** un tichet + îl închide |
| `--apply-send` | — | 🔴 **TRIMITE LIVE ÎN MASĂ** + închide (nedocumentat în SKILL.md) |

### Configurația de cron (`cs_backlog.sh`, pauzată)
```bash
export DRAFT_MODEL=gpt-4o-mini PYTHONUNBUFFERED=1
for ch in email facebook_message messenger email_from_widget instagram_message; do
  .venv/bin/python3 cs_auto_draft.py --channel "$ch" --limit 3000 --scan 6000 \
      --create-draft --ground --no-comments --skip-tagged --tag ai-draft --sleep 0.5
done
```
`cs_draft_all.sh` (3-iul) e varianta „eficiență" (Haiku + `--fast-triage` + `--sleep 0.3`) — **niciodată pusă în cron**.

**Ce nu se știe (explicit):**

- Tokenii de OUTPUT (~400/tichet email, ~220/comentariu) sunt ESTIMAȚI, nu măsurați — `count_tokens` măsoară doar inputul, iar eu n-am făcut apeluri de generare (regula „fără apeluri LLM prin cs_auto_draft"). Costurile de output sunt 25-55% din total, deci o eroare de ±50% aici mută costul total cu ±15-25%. Prima rulare reală trebuie să logheze `usage.output_tokens` ca să fixeze cifra.
- Prețurile OpenAI (gpt-4o-mini la 0,15/0,60 $ per MTok, gpt-4o la 2,50/10,00) sunt din listarea publică, NU le-am verificat azi. Prețurile Claude vin din tabelul curent al skill-ului claude-api (cache 2026-06-24) — acelea le consider solide.
- `claude-haiku-4-5` apare în `/v1/models` pe cheia noastră doar ca `claude-haiku-4-5-20251001` (datat), iar `cs_draft_all.sh` setează aliasul nedatat `claude-haiku-4-5`. Aliasul rezolvă de regulă, dar n-am confirmat cu un apel — dacă cineva repornește cs_draft_all.sh fără să verifice, poate primi 404. Un apel de 1 token lămurește.
- Nu știu de ce 3 din 6 comentarii FB n-au putut fi citite prin Graph (#333838, #333827, #333835) deși tokenul are acces la pagină și postarea s-a citit. Cea mai probabilă explicație e că acele comentarii au fost șterse între timp (memoria notează că 57% din comentariile FB dispar în 3 zile), dar nu am verificat — nu exclud o a doua problemă de format al id-ului, ca la Instagram.
- Nu știu dacă `META_SYSTEM_TOKEN` și `META_SYSTEM_USER_TOKEN` sunt literalmente același șir (n-am comparat valorile, ca să nu manipulez secrete în clar) — știu doar că au același app, aceleași 29 de pagini și aceleași 31 de scope-uri, ceea ce e un indiciu puternic de duplicat.
- Nu am testat NICIO cale de scriere: nici hide de comentariu, nici `cs-actions --apply`, nici create_draft, nici send. Verdictul „hide ar trebui să meargă" e dedus din preconditii (task MODERATE + pages_manage_engagement + id de comentariu care se rezolvă la citire), nu dovedit prin efect.
- Rata de acceptare a celor 1.336 de drafturi scrise în iunie e NECUNOSCUTĂ — nimeni nu a măsurat câte au fost trimise nemodificat. Fără ea, orice afirmație despre calitatea sistemului (a mea inclusă) e o presupunere. Datele există (drafturi în .auto_draft_proposals.json + mesaje de agent în cs_mirror.db), doar că nimeni nu le-a pus față în față.
- `oh_mirror.db` are `updated_at` doar pe ultimele ~24 h (14-sep 13:40 → 15-sep 13:08) pentru toate cele 57.171 de comenzi. Pare o oglindă completă reîmprospătată continuu, dar nu am verificat cât de adânc în trecut ține comenzile — dacă fereastra e scurtă, grounding-ul pe OH ar acoperi doar tichetele recente.
- Plafonul Richpanel de 60 cereri/minut e din instrucțiunile primite, nu măsurat de mine. Ce am măsurat e o rulare reală la 48/min cu 0× 429 (oglinda CS, 15-sep 03:00). Nu știu dacă limita e per token, per cont sau per endpoint, și nici cât consumă CS-ul live în orele de vârf — de-aia recomand rularea de noapte.
- Estimarea de 12,7 h pentru backlog pe un worker extrapolează viteza din rularea de 24-iun (7,0 s/tichet), care rula cu `--lean` și `--sleep 0.5`. Configurația actuală (`--ground` + `--photos`) face mai multă muncă per tichet — probabil mai lentă, dar nu am o măsurătoare curată a configurației `--ground`, fiindcă toate rulările cu `--ground` au eșuat pe bugul `uv`.

---

## 7. Foaia de parcurs — 52 de îmbunătățiri, ordonate

Ordonate după impact, apoi după efort. Fiecare cu dovada care o susține.


#### impact mare · efort mediu


**1. Rezolva cele 1.217 drafturi pe comentarii publice Facebook inainte de a reporni orice**

- *De ce:* Decizia de proces e ca la comentarii NU se dra fteaza (raman pentru CS, hide spam manual). Dar rularea manuala din 2-iul 11:36 a folosit cs_draft_all.sh.bak-0703, care avea facebook_feed_comment si instagram_comment in bucla: 1.217 din cele 1.302 drafturi scrise atunci sunt pe comentarii publice. Richpanel nu are delete-draft prin API.
- *Primul pas:* Decide cu ownerul: se sterg manual din UI, sau se lasa ca schite pe care agentii le ignora. Pana atunci, orice repornire trebuie sa foloseasca --no-comments (cs_backlog.sh il are deja).

**2. Fa jurnalul sa aiba un rezumat masinal la finalul fiecarei rulari**

- *De ce:* cs_backlog.log are 12 MB si nu contine nicio linie de totaluri. Ca sa aflu ca 23 din 25 de rulari au produs zero drafturi a trebuit sa scriu un parser peste marcaje decorative. Un watchdog nu putea vedea esecul, si de asta a mers 5 zile scriind doar erori.
- *Primul pas:* La finalul main(), printeaza o linie JSON cu {drafturi, invalide, triaj_esuat, sarite, spam, escaladate} si leag-o de heartbeat.py, cum fac celelalte 9 cronuri pipeline

**3. Fă playbook-ul învățat să existe efectiv în producție și regenerează-l din oglinda CS**

- *De ce:* .learned_playbook.md e în .gitignore (linia 6), NU e în git, NU există pe VPS (`find /` = 0 rezultate) → load_playbook() (linia 616) întoarce {} → injecția de la linia 1136 e no-op de la prima rulare. Local e din 23-iun (84 de zile) și e construit din doar 82 de tichete (8-10 per categorie, presale doar 4). Între timp cs_mirror.db are 2.179 de replici reale de agent (60-900 car.) din 19 zile, inclusiv în cehă.
- *Primul pas:* Pas 1 (o oră): regenerează local cu `cs_procedures.py --category all --out .learned_playbook.md` și copiază-l explicit în /root/Scripturi/ ca parte din deploy (rămâne gitignored pt. PII, dar adaugă-l în deploy.sh). Pas 2: rescrie sursa lui pe cs_mirror.db (rp_message where is_agent=1) în loc de richpanel_tickets.db, grupat pe categorie, ca să se auto-împrospăteze. Costul injecției: +636 tok/tichet = +17%.

**4. Adaugă oh_mirror.db la --ground: status operațional real + liniile comenzii**

- *De ce:* /root/Scripturi/data/oh_mirror.db e pe ACELAȘI server, actualizat azi la 12:08, cu 57.171 comenzi și 55.762 AWB-uri. Are statusuri pe care profit_orders nu le are: 🚦 On Hold (198), ⏰ Netrimisă/Alertă (424), ❌ Refuzată (4.348), ❌ Anulată (1.182) — exact cazurile unde un WISMO generic e greșit. Și are `lines_json` cu sku+title+qty, în timp ce azi draftul primește doar `skus` trunchiat la 40 de caractere.
- *Primul pas:* În `lookup_orders()` (cs_auto_draft.py:513), după pasul cu profit_orders, adaugă un pas 3: `SELECT status, courier, cod_value, lines_json FROM oh_orders WHERE order_name IN (...)` pe /root/Scripturi/data/oh_mirror.db și suprascrie `deliv` cu statusul OH (mai proaspăt) + adaugă un câmp `produse` din titlurile din lines_json. Fail-safe: try/except, ca restul funcției.

**5. Construiește metrica de acceptare a draftului — cifra care decide dacă se poate trimite vreodată**

- *De ce:* Nu există nicio măsurătoare a calității. 1.336 de drafturi au fost scrise în Richpanel în iunie și nimeni nu știe câte au fost trimise, câte editate, câte abandonate. Fără cifra asta, „trecem pe send live" e o presupunere, nu o decizie. Auditul adversarial din 29-iun a găsit 71% probleme și 18 HIGH pe 75 de drafturi — dar pe un eșantion manual, nu pe o metrică continuă.
- *Primul pas:* Scrie un script care, pentru fiecare tichet cu tagul `ai-draft`, ia draftul din .auto_draft_proposals.json și primul mesaj de agent de după el din cs_mirror.db (rp_message, is_agent=1) și calculează distanța de editare normalizată. Raportează pe categorie: % trimis identic, % editat ușor (<20%), % rescris, % abandonat. Rulează-l pe cele 1.336 de drafturi existente ca linie de bază.

**6. Gate-uri pentru --apply-send, înainte ca cineva să-l descopere din întâmplare**

- *De ce:* Flagul EXISTĂ (linia 862, execuție 1216-1226), trimite ÎN MASĂ + închide tichetele, nu e documentat în SKILL.md, iar singurele gărzi sunt: nu-e-escaladat, nu-e-comentariu, draftul nu începe cu „(eroare". Lipsesc: prag de încredere (idn['confidence'] se calculează dar nu blochează), cerință de grounding, verificare de limbă, listă albă de categorii, plafon de rată, kill-switch, audit per mesaj. Și nu există rollback: send_message e ireversibil, iar MCP-ul n-are delete-draft/delete-note (toate 21 de metode verificate).
- *Primul pas:* Adaugă, în ordine: (1) listă albă de categorii prin env (`SEND_CATS=recenzie_feedback,presale_intrebare`); (2) `has_order_data(od_ctx)` obligatoriu pentru orice categorie care afirmă ceva despre o comandă; (3) `idn['confidence'] >= 0.8`; (4) plafon `SEND_MAX_PER_RUN` (implicit 20); (5) kill-switch — dacă există /root/Scripturi/.cs_send_stop, refuză. Abia după ce metrica de acceptare arată >85% pe o categorie.

**7. Sterge sau marcheaza cele ~1.162 drafturi vechi ramase in Richpanel inainte de orice repornire**

- *De ce:* Recensamant pe populatia intreaga: 360 din 1.162 drafturi normale (31,0%) contin afirmatii fabricate, dominant „nu am gasit nicio comanda in sistemul nostru" (341, 29,3%) — desi sistemul nu facuse niciun lookup. `create_draft` ADAUGA si nu exista delete prin API (verificat, 21 unelte pe server). Esantionul live de azi confirma ca tichetele s-au inchis, dar drafturile nu se pot verifica prin API — deci presupunerea sigura e ca sunt inca acolo.
- *Primul pas:* Ia lista celor 1.336 din /tmp/run1_drafts.json (regenerabil din log cu parserul din sectiunea Runbook), filtreaza cu HALLU si da-i CS-ului lista de conversatii de curatat manual in UI. Fara asta, orice repornire pune drafturi noi peste drafturi vechi gresite.

#### impact mare · efort mic


**8. Repornirea cronului e blocată de rata OpenAI, nu de cod — ultima rulare a eșuat 100%**

- *De ce:* În jurnal (`/root/Scripturi/data/cs_backlog.log`) ultima rulare, 2026-07-02 10:00:06, are 464 tichete și FIECARE apel LLM a întors `HTTP Error 429: Too Many Requests`. Pe tot jurnalul: 10.960 eșecuri de triaj și 6.015 drafturi invalide oprite de gardă, față de 1.336 drafturi salvate. Garda a funcționat (0 `create_draft` eșuate, 0 gunoi scris), dar randamentul a fost aproape zero.
- *Primul pas:* Înainte de a scoate diezul din crontab: rulează o singură dată `--limit 5 --channel email` FĂRĂ `--create-draft` și numără câte drafturi ies fără „(eroare LLM”. Dacă apar 429, urcă tier-ul OpenAI sau comută pe `ANTHROPIC_API_KEY` (deja în `/root/Scripturi/.env`, doar că wrapperul nu-l exportă) — `llm()` îl preferă automat când e în env.

**9. `.learned_playbook.md` lipsește de pe VPS — vocea și procedurile învățate nu s-au aplicat niciodată în producție**

- *De ce:* `load_playbook()` (linia 615) caută `HERE/.learned_playbook.md`, adică `/root/Scripturi/.learned_playbook.md`. Verificat pe VPS: fișierul nu există. Deci `LEARNED = {}` și `learned_blk` (linia 1136) e mereu gol — blocul „PROCEDURA INVATATA + VOCEA AGENTILOR REALI” nu a intrat în niciun prompt. Local, în git, fișierul există (17.884 octeți, 9 categorii).
- *Primul pas:* `scp` fișierul local `plugins/gigi/skills/cs-draft-reply/.learned_playbook.md` în `/root/Scripturi/`, apoi confirmă cu o rulare dry-run că apare „PROCEDURA INVATATA” în context. Regenerarea periodică: `PROC_MODEL=gpt-4o cs_procedures.py --category all --out .learned_playbook.md`.

**10. Pune .learned_playbook.md pe VPS sau fa lipsa lui zgomotoasa**

- *De ce:* load_playbook() deschide HERE/.learned_playbook.md si la exceptie returneaza {} tacut. Pe VPS fisierul NU exista (verificat: ls: cannot access). Deci toate cele 2.642 de drafturi scrise de pe VPS au fost generate FARA procedurile si vocea invatate din tichete reale - exact ingredientul pentru care s-a construit gigi:cs-procedures. Nici cs_procedures.py nu e pe VPS, deci nu se poate regenera acolo.
- *Primul pas:* Genereaza local (PROC_MODEL=gpt-4o cs_procedures.py --category all --out .learned_playbook.md), scp in /root/Scripturi/, si adauga in load_playbook un print pe stderr cand fisierul lipseste

**11. Documenteaza in SKILL.md al doilea wrapper, --fast-triage, --apply-send si prompt caching-ul**

- *De ce:* grep in SKILL.md: 0 aparitii pentru fast-triage, 0 pentru apply-send, 0 pentru caching/cache_control, 0 pentru cs_draft_all. SKILL.md e la nivelul PR #367 (mtime 4-iul 09:14) si descrie doar cs_backlog.sh. Sonia ar prelua un sistem in care jumatate din ce s-a scris efectiv in Richpanel (1.306 drafturi din 2.642) a venit pe o cale nedocumentata.
- *Primul pas:* Adauga o sectiune "Wrappere pe VPS" cu cele doua scripturi, modelele lor si istoricul de rulare, plus flagurile noi in lista de optiuni

**12. Nu reporni cronul fara o rulare de proba pe un lot mic**

- *De ce:* Versiunea aflata acum pe VPS (b5f4742, cu --fast-triage si prompt caching) a rulat in productie 86 de secunde in toata viata ei: pasul "DRAFT REAL-SUPPORT" din 3-iul 11:41:28 a produs 4 drafturi si jurnalul se opreste la 11:42:54. Nu exista nicio dovada ca --fast-triage se comporta bine la volum.
- *Primul pas:* Ruleaza intai cu --limit 20 --only <lista> fara --create-draft, citeste cele 20 de drafturi, treci-le prin grammar_audit.py --file, si abia apoi decomenteaza cronul

**13. Watchdog + heartbeat pe motorul de draft (blocant înainte de orice repornire)**

- *De ce:* 24 de rulări consecutive (24-iun 21:00 → 2-iul) au procesat ~39.400 tichete și au scris ZERO drafturi, timp de 5 zile, cu ✅ verde în cron. Pe VPS sunt 27 de joburi cablate pe heartbeat.py și NICIUNUL nu e cs_auto_draft; data_health.py (427 linii, 6 verificări) nu-l menționează.
- *Primul pas:* Adaugă `&& .venv/bin/python heartbeat.py cs_draft` la finalul liniei din crontab, apoi un `check_cs_draft(rows, ctx)` în /root/Scripturi/data_health.py care parsează ultima rulare din data/cs_backlog.log și dă ROȘU dacă: heartbeat lipsă >6h SAU drafturi_salvate==0 la o rulare cu >50 tichete SAU rata de eroare LLM >5%.

**14. Dead-letter: marchează tichetul care eșuează, nu-l reîncerca la infinit**

- *De ce:* Garda de la liniile 1213-1215 refuză corect să scrie gunoi, dar nu pune tag și nu numără. Măsurat: contorul „invalide" crește monoton 120 → 350 pe rulare = aceleași tichete otrăvite reprocesate la fiecare rulare. Total: 39.388 cicluri irosite.
- *Primul pas:* În ramura `draft invalid` (cs_auto_draft.py:1213), incrementează un contor `fails` în `.auto_draft_proposals.json` pentru conv_no; la `fails >= 3` pune tagul `ai-failed` și sări tichetul la rulările următoare (verifică-l lângă `--skip-tagged`, linia 965). Raportează numărul în rezumatul final.

**15. Comută pe Claude Sonnet 5 cu prompt caching + --fast-triage**

- *De ce:* Măsurat cu count_tokens: 6.908 tok input/tichet email. Costurile pe 1.000 tichete: sonnet-5 cu cache 7,25 $ (cu --fast-triage 4,33 $) vs haiku-4-5 8,91 $ vs sonnet-4-6 (default în cod) 10,88 $. Haiku e mai SCUMP fiindcă pragul lui de cache e 4.096 tok, iar prompturile noastre au 1.910 și 3.960 — nu se cache-uiesc deloc pe Haiku. Cheia ANTHROPIC_API_KEY există și e validă (HTTP 200, 11 modele).
- *Primul pas:* În cs_auto_draft.py liniile 301 și 334 schimbă default-ul `claude-sonnet-4-6` → `claude-sonnet-5`; în cs_backlog.sh exportă `ANTHROPIC_API_KEY` din .env + `ANTHROPIC_MODEL=claude-sonnet-5` și adaugă `--fast-triage`. Corectează comentariul greșit de la linia 300 (pragul Sonnet 4.6 e 1.024, nu 2.048; SYSTEM are 3.960 tok, nu 2.6k). Verifică `usage.cache_read_input_tokens != 0` la a doua cerere.

**16. Activează contextul postării pe comentarii FB (deblocat azi de token)**

- *De ce:* Testat live pe 6 tichete reale: 5 din 6 pagini FB au întors textul reclamei cu tot cu preț — „Set 5 Pijamale din Satin ... 99 Lei", „Cumperi 2, primești 1 GRATIS ... Livrare GRATUITĂ peste 150 Lei", „Odstraní prach..." (CZ). Fără el, draftul la un comentariu „Care e prețul?" nu are ce răspunde. Volum: 5.874 comentarii FB OPEN + 376 comentarii noi/zi.
- *Primul pas:* Codul `fb_post_text()` (linia 696) e deja corect și deja apelat în buclă pe `is_public`. După fix-ul de ordine a tokenurilor (punctul anterior), rulează `cs_auto_draft.py --channel facebook_feed_comment --limit 10` în DRY-RUN și verifică în log că postarea apare prepend-uită în transcript.

**17. Nu reporni cronul fara o poarta de EFECT (masurare de acceptare) pe un lot mic**

- *De ce:* Masuratoarea de azi: 0/40 drafturi acceptate; 38/40 tichete inchise de agenti umani FARA niciun raspuns, la 12 zile mediana dupa draft. Sistemul a scris 1.336 de drafturi si nu a produs niciun raspuns trimis. Repornirea fara masurare repeta exact acelasi rezultat, doar mai scump.
- *Primul pas:* Ruleaza `cs_draft_all.sh` pe --limit 30 pe un singur canal, apoi peste 72h ruleaza scriptul de efect (live.sh din scratchpad) pe acele 30 si raporteaza: % cu raspuns de agent dupa draft si similaritate. Poarta de trecere: >=30% acceptare. Sub asta, sistemul nu merita creditele.

**18. Muta cele 5 dependinte lipsa pe VPS sau accepta explicit ca acele functii sunt moarte**

- *De ce:* Verificat prin import real pe VPS: `LEARNED` = 0 categorii (lipseste .learned_playbook.md), `CSA` (cs-actions) nu exista, `CI` (customer-identity) nu exista, `KB` nu exista, `grammar_audit.py` nu exista. Deci in productie: procedurile+vocea invatate din tichete reale NU au fost injectate NICIODATA in vreun draft, `--approve` nu poate rula, si gate-ul de limba nu a fost rulat niciodata pe iesirea de productie.
- *Primul pas:* `scp` .learned_playbook.md si grammar_audit.py in /root/Scripturi/, apoi reruleaza importul de verificare (`LEARNED` trebuie sa dea >0 categorii). Pentru cs-actions si customer-identity: ori cloneaza skill-urile pe VPS, ori scoate `--approve` din documentatie ca fiind indisponibil pe VPS.

**19. Adauga heartbeat + alerta pe cs_backlog.sh inainte de repornire**

- *De ce:* Cronul a rulat 24 de ori in 8 zile producand ZERO drafturi si nimeni nu a observat 5 zile. Cauza: `secret()` chema `uv run kb.py`, iar `uv` nu e in PATH-ul cronului — 6.009 esecuri identice, toate tacute (garda anti-gunoi facea exact ce trebuia: sarea tichetul, dar in liniste). Logul arata ✅ la finalul fiecarei rulari.
- *Primul pas:* In wrapper, dupa bucla: numara `✅ DRAFT salvat` din iesire; daca e 0 si au fost tichete de procesat, iesi cu cod !=0 si cheama `heartbeat.py` DOAR pe succes real, ca la celelalte 9 cronuri din shared/scripturi-tools. Apoi `data_health.py` il vede rosu.

**20. Ruleaza calea --ground pe un lot real — nu a produs niciodata un draft in productie**

- *De ce:* Grounding-ul (cautare comanda reala in metrics + profitability.db, inclusiv dupa AWB) e singura reparatie structurala a halucinarii si a fost validata DOAR manual (~5/7 si ~10/13 hit-rate, dupa memorie). In cron a rulat o singura data, 2-iul, pe 21 de tichete, si toate 21 au picat pe OpenAI 429 → 0 drafturi salvate. Deci in productie calea grounded are exact 0 drafturi.
- *Primul pas:* `cs_draft_all.sh` (are deja --ground) pe --limit 30, canal email, cand pipeline-ul de triaj nu ruleaza (evita contentia pe aceeasi cheie OpenAI). Apoi treci cele 30 prin HALLU si compara cu 31,0% de referinta din 24-iun.

**21. Repara sursa reala a escaladarilor nerutate si verific-o prin efect**

- *De ce:* 197 de escaladari identificate (173 HIGH, 24 URGENT — adica ANPC/juridic/chargeback) au primit doar un draft de asteptare; 0 au fost rutate in Richpanel (0 linii „⛳ Rutat"), si doar 3 din cele 174 salvate poarta azi tag escaladare/esc-*. Cauza: blocul de rutare e la linia 1201 sub `if is_esc and not a.lean:`, iar cronul din iunie rula `--lean`. Wrapperul de azi foloseste `--ground`, deci rutarea AR functiona — dar nu a fost niciodata dovedita in productie.
- *Primul pas:* La primul lot de test, verifica pe un tichet escaladat ca priority=HIGH, tag-urile `escaladare`+`esc-high|esc-urgent` si nota privata chiar exista in Richpanel (get_conversation cu include_private_notes). Fara dovada pe tichet real, nu declara rutarea functionala.

#### impact mediu · efort mediu


**22. Uneltele de acțiune (`--approve`) sunt moarte pe VPS: `cs_actions.py` și `customer_identity.py` nu există la căile calculate**

- *De ce:* `CSA` (linia 47) se rezolvă la `/root/cs-actions/scripts/cs_actions.py` și `CI` (linia 46) la `/root/customer-identity/customer_identity.py`. Verificat: ambele LIPSESC. Deci `run_cs_action` întoarce mereu `(1, "(cs_actions.py negăsit…)")` și `customer_ident()` întoarce `{}`. În jurnal: 0 propuneri de acțiune, 0 aplicări; în coadă, 9 intrări cu `cmd` și 0 cu `applied`.
- *Primul pas:* Ori copiezi cele două scripturi pe VPS și corectezi `CI`/`CSA` să le găsească (ca la `cs_photo.py`, care e lângă script și se importă corect), ori documentezi explicit că `--approve` se rulează DOAR de pe stația de lucru, unde arborele de plugin-uri e complet.

**23. Escaladarea din LLM e practic dezactivată pe cazurile obișnuite, și nu se vede din cod că e intenționat**

- *De ce:* La linia 1080, `is_esc = _real_esc or (_llm_esc and not _ord_cat)`. `_ord_cat` (1078-1079) enumeră 11 din cele 14 categorii posibile. Singurele categorii pe care verdictul LLM mai contează sunt `refuz_livrare`, `comentariu_social` și `spam_automat`. În rest decide doar regexul determinist `_real_escalation` (104-110). Efect măsurat: 394 tichete flagate ⛳, dar nicio rutare în Richpanel (0 apariții „⛳ Rutat”) — pentru că rulările cu escaladări au fost cele cu `--lean` (7.345 tichete), iar `--lean` sare rutarea (linia 1201).
- *Primul pas:* Lasă logica dar scrie comentariul explicit deasupra liniei 1078 („LLM-ul decide DOAR pe cele 3 categorii de mai jos”) și rulează o dată cu `--ground` (fără `--lean`) pe un lot mic cu `--create-draft`, ca să validezi că rutarea HIGH+notă chiar scrie în Richpanel — nu s-a întâmplat niciodată.

**24. Repară „a mai scris pe" — pe configurația de cron e mereu gol și ajunge așa în nota de escaladare**

- *De ce:* La `--ground` (liniile 999-1006) `other` rămâne [], deci `A MAI SCRIS PE:` = literal „grounded — 3 comenzi găsite în DB" și `ALTE TICHETE:` = „(fără alte tichete)" ÎNTOTDEAUNA. Textul acesta fără sens ajunge și în `escalation_note` (linia 834) pe care o citește agentul CS. Calea care chiar aduce istoricul (`customer_ident`, linia 500) face subprocess uv + SSH și nu merge pe cron. Măsurat în cs_mirror: 934 de grupuri cu același telefon și 262 cu același email au ≥2 tichete în 19 zile.
- *Primul pas:* Scrie un `lookup_convos(email, phone)` care interoghează /root/Scripturi/data/cs_mirror.db (`rp_ticket` are indecși pe canal și dată; acoperire telefon 67,5%, email 32,3%) și întoarce aceeași formă ca `ci['convos']`; apelează-l în ramura `--ground` și construiește `elsewhere`/`hist_txt` cu CH_LABEL, ca în ramura non-lean.

**25. Few-shot cu replici reale de agent, per categorie, din oglindă**

- *De ce:* cs_mirror.db conține 2.179 de replici de agent de 60-900 caractere din ultimele 19 zile (761 email, 649 comentarii FB, 646 FB mesaj), cu vocea autentică — fraze scurte, fără diacritice, direct la subiect („Buna ziua, Aveti o comanda EST000003 plasata ieri dar nu e platita ci cu plata ramburs la cureir."). Playbook-ul actual are 82 de tichete și 84 de zile. Multilingv gratis: replici în cehă.
- *Primul pas:* Extinde build-ul playbook-ului cu 3-5 replici reale per categorie selectate din cs_mirror (filtrează pe canal + lungime + prezența unui răspuns de agent), injectate ca exemple de TON, cu instrucțiunea existentă „NU copia datele din exemple". Măsoară delta de tokeni înainte de a o porni pe tot volumul.

#### impact mediu · efort mic


**26. Unealta MCP `arona-cs-inbox → cs_draft` cheamă un flag inexistent și eșuează întotdeauna**

- *De ce:* `plugins/gigi/skills/cs-draft-reply/mcp_server.py` linia 33 face `_run(DRAFT, ["--conv", conv] …)` unde `DRAFT = cs_auto_draft.py`. Dar `cs_auto_draft.py` nu are `--conv` (verificat: cele 20 de flag-uri sunt la liniile 849-868; singura apariție a textului `--conv` e la linia 502, în apelul către `customer_identity.py`). Argparse iese cu cod 2.
- *Primul pas:* Schimbă `DRAFT` să pointeze pe `cs_draft_reply.py` (care ARE `--conv`, linia 160) sau înlocuiește `--conv` cu `--only` în apel.

**27. Telefonul clientului se pierde tăcut dacă e stocat cu prefix internațional**

- *De ce:* Linia 932: `phone = raw_phone if (raw_phone.isdigit() and 9 <= len(raw_phone) <= 13) else ""`. Un `+40700000000` sau `0040 700 000 000` pică testul `isdigit()` → `phone=""` → `--ground` nu mai caută după telefon (linia 998 și 534-537), iar tag-ul `de-sunat` nu se mai pune la escaladare (linia 1202). Funcția `norm_phone()` (507-509) face exact normalizarea corectă, dar e chemată abia DUPĂ filtrul care a golit câmpul.
- *Primul pas:* Înlocuiește filtrul de la 932 cu `phone = norm_phone(raw_phone) and raw_phone` (sau pasează direct `raw_phone` la `lookup_orders`, care normalizează singur).

**28. Trei din cele cinci canale pe care le rulează cronul n-au stil de platformă definit**

- *De ce:* `PLATFORM` (liniile 51-59) are 7 chei. Cronul (`cs_backlog.sh`) buclează pe `email, facebook_message, messenger, email_from_widget, instagram_message`. Dintre ele, `facebook_message`, `email_from_widget` și `instagram_message` NU sunt în `PLATFORM` → cad pe fallback-ul generic de la linia 923 („ton prietenos, la obiect.”). `messenger` și `instagram_dm` există, dar Richpanel trimite `instagram_message`, nu `instagram_dm`.
- *Primul pas:* Adaugă în `PLATFORM` alias pentru `facebook_message` (= regula `messenger`), `instagram_message` (= `instagram_dm`) și `email_from_widget` (= `email`). Trei linii.

**29. `comment_action: "none"` nu e onorat — zgomotul pur de pe comentarii primește totuși draft**

- *De ce:* Promptul IDENTIFY (linia 414) definește `none` = „comentariu pur zgomot … se lasă cum e”, dar în cod `cact` e citit o singură dată, la linia 1125, doar pentru `== "hide"`. Nu există nicio ramură care să sară generarea/salvarea draftului la `none`. În coadă, `comentariu_social` e a doua categorie ca volum (819 din 3.156).
- *Primul pas:* După linia 1125, adaugă `if is_public and cact == "none": continue` (înainte de pasul 3, ca să nu mai cheltui apelul LLM de draft).

**30. Grounding-ul eșuat arată identic cu „clientul n-are comenzi”**

- *De ce:* `lookup_orders` (512-574) înghite toate excepțiile cu `except Exception: pass` — pe ambele ramuri (metrics la 546-547, sqlite la 572-573). Dacă `DATABASE_URL_METRICS` lipsește sau Postgres e căzut, funcția întoarce `[]`, exact ca pentru un client necunoscut, iar în consolă scrie „grounded — nicio comandă găsită în DB”. Nu ai cum să distingi o pană de un miss legitim.
- *Primul pas:* Întoarce `(orders, errors)` sau setează un flag pe care linia 1005 să-l scrie ca „grounding INDISPONIBIL (DB)” — altă frază decât „nicio comandă găsită”.

**31. Post-filtrul anti-halucinație nu s-a declanșat niciodată în producție — e cod nevalidat**

- *De ce:* În jurnalul de 137.095 de linii: 0 apariții „+corectat” și 0 „șablon-sigur”. Cauza probabilă e că el rulează doar dacă draftul a ieșit corect (linia 1157 cere `not draft.startswith("(eroare")`), iar majoritatea rulărilor au produs erori 429. Deci regexul `HALLU` (189-196) și ramura de rescriere (1158-1172) n-au fost puse la încercare la scară.
- *Primul pas:* După ce rezolvi rata LLM, rulează 30 de tichete `--lean` (fără date de comandă, exact cazul în care filtrul e activ) în dry-run și numără câte ies „+corectat”/„șablon-sigur”. Abia atunci ai o măsurătoare.

**32. Baga cs-draft-reply in deploy_parity.py, ca driftul git-flat sa nu mai fie invizibil**

- *De ce:* Am rulat deploy_parity.py check pe VPS: raporteaza 10 divergente, dar NICIUNA nu e cs_auto_draft.py. discover() scaneaza doar plugins/gigi/skills/metrics-cache/scripts/ si shared/scripturi-tools/. Driftul de 74 de zile intre flat si git a stat nedetectat exact fiindca garda nu se uita acolo.
- *Primul pas:* Adauga plugins/gigi/skills/cs-draft-reply/ (cs_auto_draft.py, cs_photo.py, grammar_audit.py) in lista de directoare din discover(), /root/Scripturi/deploy_parity.py:64

**33. Extinde retry-ul din _llm_http peste 400 si 529**

- *De ce:* _llm_http (cs_auto_draft.py:~270) reincearca doar pe 429/500/502/503/504. In cs_draft_all.log, 321 de drafturi consecutive s-au pierdut pe HTTP 400 Bad Request, intr-o rafala continua incepand cu tichetul [324/457], plus 1 pe HTTP 529 (Anthropic overloaded). Un 400 omoara tichetul instant, fara nicio reincercare.
- *Primul pas:* Adauga 529 in lista de coduri reincercabile si, pentru 400, o singura reincercare cu promptul trunchiat (transcript taiat la jumatate) inainte de a renunta

**34. Repara sau sterge cs_draft_all.sh - modelul pe care il cere nu mai exista**

- *De ce:* Wrapperul exporta ANTHROPIC_MODEL=claude-haiku-4-5. Am interogat GET /v1/models cu cheia din .env: contul are 11 modele, iar aliasul claude-haiku-4-5 NU e printre ele (exista doar claude-haiku-4-5-20251001). claude-sonnet-4-6 exista. Deci rulat azi, wrapperul ar pica pe fiecare apel Anthropic.
- *Primul pas:* Schimba in ANTHROPIC_MODEL=claude-haiku-4-5-20251001 (sau sterge linia ca sa cada pe claude-sonnet-4-6), in /root/Scripturi/cs_draft_all.sh linia 8

**35. Confirma live cate tichete mai poarta tagul ai-draft, inainte de a decide curatarea**

- *De ce:* Oglinzile locale nu pot raspunde: cs_mirror.db e o fereastra rulanta 27-aug..15-sep (0 taguri AI, dar nu vede iunie/iulie), iar richpanel_tickets.db tine UUID-uri de tag, nu nume, si sunt un instantaneu de la momentul pull-ului. Am ajuns la >=1.397 prin concentrare statistica (UUID ea60dd56-5fd9-4952-85ae-c3ea5f3b33ce apare pe 1.397 din 1.418 aparitii ale sale in tot istoricul de 255.767 tichete, toate pe tichetele noastre), nu printr-o cautare pe nume.
- *Primul pas:* O singura citire Richpanel: list_tags ca sa mapezi numele la UUID, apoi list_conversations filtrat pe tagul ai-draft. Sub 15 cereri/minut, doar citiri, si doar daca esti agentul desemnat.

**36. Actualizează SKILL.md — conține afirmații demonstrat false care blochează decizii**

- *De ce:* SKILL.md scrie „tokenurile actuale sunt de ADS (0 pagini, exceptând Nubra+Covoria prin META_SYSTEM_TOKEN_3)". Măsurat azi: META_SYSTEM_TOKEN are 29 de pagini cu task MODERATE și 31 de scope-uri, iar am citit LIVE textul reclamei pe 5 din 6 pagini FB. În plus, `--apply-send` (trimite ÎN MASĂ la clienți) nu e documentat deloc.
- *Primul pas:* Rescrie secțiunile „Moderare comentarii" și „Necesită" cu starea măsurată (29 pagini, MODERATE, pages_manage_engagement), documentează `--apply-send` cu avertisment roșu, și scoate nota „apply-paths de validat live" înlocuind-o cu ce s-a validat efectiv (citire) vs ce nu (scriere/hide).

**37. Repară coliziunea DRAFT_MODEL din cs_draft_reply.py + lipsa de retry**

- *De ce:* cs_draft_reply.py:83 folosește `DRAFT_MODEL` pe ramura ANTHROPIC, iar wrapperele exportă `DRAFT_MODEL=gpt-4o-mini` → trimite `model="gpt-4o-mini"` la api.anthropic.com → 404 → SystemExit. cs_auto_draft.py folosește corect `ANTHROPIC_MODEL` (linia 301). În plus cs_draft_reply n-are backoff, spre deosebire de `_llm_http` (linia 277).
- *Primul pas:* În cs_draft_reply.py:83 schimbă `os.environ.get("DRAFT_MODEL", ...)` → `os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")` și importă/copiază `_llm_http` din cs_auto_draft pentru retry pe 429/5xx.

**38. Pune META_SYSTEM_TOKEN primul în fb_page_token — azi codul alege tokenul mai slab**

- *De ce:* Ordinea de la linia 681 e `META_PAGES_TOKEN, META_SYSTEM_TOKEN_3, META_SYSTEM_TOKEN, ...`. META_PAGES_TOKEN nu există în seif (apel Graph irosit), iar META_SYSTEM_TOKEN_3 are pe Nubra doar `tasks=ADVERTISE` (nu MODERATE) — măsurat în test: pagina Nubra a primit tokenul via _3, deci nu ar putea ascunde comentarii. META_SYSTEM_TOKEN are MODERATE pe toate cele 29 de pagini.
- *Primul pas:* Schimbă tuplul de la linia 681 în `("META_SYSTEM_TOKEN", "META_SYSTEM_TOKEN_3", "META_SYSTEM_TOKEN_2", "META_SYSTEM_TOKEN_4")` — scoate META_PAGES_TOKEN (inexistent) și META_USER_TOKEN (valoarea e literal `REVOKED_pasted_by_mistake_2026-06`).

**39. Curăță și plafonează coada de propuneri (PII + O(n²))**

- *De ce:* /root/Scripturi/.auto_draft_proposals.json = 7,3 MB, 3.156 intrări din iunie, 0 `applied`, 0 `sent`, 328 escaladate — fiecare cu draft + context de comandă, adică PII. `save_queue()` rescrie tot fișierul după FIECARE tichet → cost pătratic pe rulări de mii de tichete.
- *Primul pas:* În `save_queue()` (linia 609) taie intrările mai vechi de 14 zile (adaugă `ts` la salvare) și scrie coada o singură dată la finalul buclei, nu per tichet. Arhivează/șterge fișierul actual înainte de repornire.

**40. Completează PAGE_STORE cu cele 9 pagini lipsă și cere acces la cele 7 fără token**

- *De ce:* Măsurat pe volumul social din oglindă (7.803 tichete/19z): 9 pagini cu trafic lipsesc din PAGE_STORE = 671 tichete (8,6%) fără brand corect; 7 pagini nu sunt în cele 29 ale tokenului = 1.422 tichete (18,2%) fără context de reclamă și fără moderare. Cea mai mare: 364899953373966 (a doua pagină Ofertele Zilei), 962 tichete în 19 zile. În plus PAGE_STORE conține 4 pagini pe care tokenul nu le vede (Orașul Verde, Rossi id vechi, Lab Noir, Ofertele Zilei-2).
- *Primul pas:* Rulează interogarea de distribuție `to_id` pe cs_mirror.db, identifică etichetele pentru cele 6 pagini necunoscute (814175968452902, 898588036681214, 336777842857489, 995674783622734, 516792924847762, 425006144024872) și completează PAGE_STORE. Separat, cere în Business Manager „The Wow Grid" asignarea acestor pagini la system user-ul care emite META_SYSTEM_TOKEN.

**41. Validează o singură dată căile care SCRIU: hide comentariu + cs-actions --apply**

- *De ce:* Punct rămas din memorie „de rezolvat înainte de scalare". Azi preconditiile sunt demonstrat îndeplinite pentru hide: task MODERATE pe toate cele 29 de pagini, scope pages_manage_engagement + pages_read_user_content, iar id-ul de comentariu se rezolvă (am citit `is_hidden=True` pe #333841 și `is_hidden=False` pe #333832). Dar scrierea n-a fost testată niciodată, iar `fb_hide_comment` (linia 713) încearcă 3 formate de id la nimereală.
- *Primul pas:* Alege UN comentariu de spam evident pe o pagină cu volum mic, rulează `--approve <conv>` cu un om lângă, verifică în UI-ul Facebook că e ascuns, apoi dezascunde. Separat, `cs-actions --apply` pe o comandă de test pe un magazin de test (precedentul GT000001 e documentat în memoria cs-actions-skill).

**42. Repara capcana din grammar_audit.py (auditeaza mesajul clientului in loc de draft)**

- *De ce:* Documentata pe 23-iun ca 4 false-pozitive, marcata „de intarit promptul". Verificat azi: fisierul are un singur commit in istoric (e0e0b31, 2026-06-23) si mtime 23-iun 12:38 — NU a fost atins de atunci. Promptul AUDIT_SYS (liniile 27-35) inca primeste `mesaj_client` alaturi de `RASPUNS` in payload (linia 74) si nu contine nicio instructiune care sa spuna ca se auditeaza DOAR campul RASPUNS.
- *Primul pas:* In AUDIT_SYS adauga o linie 0: „Auditezi EXCLUSIV textul de dupa `RASPUNS:`. `mesaj_client` e DOAR context pentru limba — greselile din el NU se raporteaza." Apoi reruleaza pe acelasi JSON si verifica: cele 4 false-pozitive trebuie sa dispara.

**43. Documenteaza in SKILL.md gate-ul de calitate (nu exista deloc acolo)**

- *De ce:* SKILL.md are 15 sectiuni si zero mentiuni despre grammar_audit.py sau despre cum se masoara calitatea. Cine preia sistemul nu are de unde sa afle ca gate-ul exista, cum se ruleaza si ce capcana are.
- *Primul pas:* Adauga o sectiune „Poarta de calitate" cu cele doua comenzi (--json | @@JSON@@ -> grammar_audit.py --file) + recensamantul determinist HALLU + regula: se ruleaza dupa ORICE schimbare de prompt.

#### impact mic · efort mediu


**44. Repară derivarea post_id pe Instagram (azi 0 din 2 teste reușesc)**

- *De ce:* Codul ia `segs[-2]` din id-ul tichetului, ceea ce merge pe FB (`{page}_{post}_{post}_{comment}`) dar nu pe IG, unde formatul e `{media}_{comment}` (ex. 17942163597036761_18111903014089292) — `fb_post_text` cere `{page}_{media}` și primește gol. Volum afectat: 292 tichete de comentariu IG în 19 zile.
- *Primul pas:* Detectează formatul: dacă id-ul are exact 2 segmente și ambele încep cu 17/18, e IG → media id = segs[0], și cere-l pe Graph ca `/{media_id}?fields=caption,media_url` cu tokenul de pagină (necesită scope instagram_basic, prezent). Testează pe #333801 (Esteban) și #333720 (Nubra).

#### impact mic · efort mic


**45. VPS-ul e cu o linie în urma git-ului: pagina Lab Noir nu se mapează la brand**

- *De ce:* `diff` complet între `/root/Scripturi/cs_auto_draft.py` și versiunea din git = O SINGURĂ linie: git are în plus, în `PAGE_STORE`, `"61586834387211": "Lab Noir"` (după linia 66). Efect: pe VPS un tichet venit de pe pagina Lab Noir cade pe `store_name = "magazinul nostru"` → se semnează „echipa noastră”, iar `STORE_PHONE` nu găsește numărul.
- *Primul pas:* Deployează cu `deploy.sh --apply` (git-driven), nu cu `scp` manual — vezi `shared/HARTA.md`, secțiunea de monitorizare. Nimic altceva nu diferă.

**46. Nu există mod de inspecție cu cost zero: chiar și dry-run consumă apeluri LLM plătite**

- *De ce:* Fără `--create-draft` scriptul nu scrie nimic în Richpanel, dar tot face 2 apeluri LLM per tichet (triaj la linia 1038, draft la 1152) — 1 cu `--fast-triage`, plus 1 vizual pe tichetele cu poze. Un `--limit 3000` în dry-run costă exact cât unul real.
- *Primul pas:* Adaugă un `--no-llm` care sare ambele apeluri și afișează doar selecția + triajul regex + contextul construit. Util pentru a verifica selecția tichetelor și grounding-ul fără factură.

**47. Adu copia plata /root/Scripturi/cs_auto_draft.py la nivelul git inainte de orice repornire**

- *De ce:* VPS = b5f474232b0ad203 (1243 linii) = commitul fe410875/PR #367. Git HEAD = 57631aebe2f27af1 (1244 linii) = 31d43aa6/PR #397. Singura diferenta: linia 67, PAGE_STORE["61586834387211"]="Lab Noir". Fara ea, orice tichet de pe pagina Lab Noir primeste brand necunoscut si se semneaza "echipa noastra". Clona git exista deja pe VPS si e identica cu HEAD.
- *Primul pas:* cp /root/Scripturi/cs_auto_draft.py /root/Scripturi/cs_auto_draft.py.bak-$(date +%Y%m%d-%H%M%S) && cp /root/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py /root/Scripturi/cs_auto_draft.py

**48. Sincronizează VPS cu git (o singură linie) prin deploy.sh, nu prin scp**

- *De ce:* Diferența reală VPS↔git e UN rând: git are `"61586834387211": "Lab Noir"` în PAGE_STORE (liniile 60-68), VPS nu. Restul fișierului e identic (diff = 2 linii). cs_photo.py e deja identic (4fb6079ef80ac4e4). Alarma „versiunile diferă" e benignă, dar lasă tichetele Lab Noir fără brand.
- *Primul pas:* `ssh root@84.46.242.181 'bash /root/Scripturi/deploy.sh --apply'` și verifică cu `grep 61586834387211 /root/Scripturi/cs_auto_draft.py`. NU scp — scp-ul manual e cauza istorică a divergențelor git↔VPS.

**49. Dă-i lui grammar_audit o ramură Claude și repară falsul-pozitiv pe mesajul clientului**

- *De ce:* grammar_audit.py:39 merge exclusiv pe OpenAI (AUDIT_MODEL, default gpt-4o) — dacă motorul trece pe Claude, gate-ul de calitate rămâne pe alt furnizor și altă cotă. În plus, memoria consemnează 4 fals-pozitive fiindcă auditorul citea cuvinte din `cust_msg` (mesajul clientului), nu din `draft`.
- *Primul pas:* Copiază ramura Anthropic din cs_auto_draft.llm() în grammar_audit.llm_json() (cu `output_config.format` pentru JSON garantat) și întărește AUDIT_SYS cu „auditezi DOAR câmpul `draft`; `mesaj_client` e context pentru limbă, nu obiect al corecturii".

**50. Decide soarta codului mort: fb_private_reply și build_voice_pack**

- *De ce:* `fb_private_reply()` (cs_auto_draft.py:737-763, 27 linii) are exact o apariție a numelui în fișier = doar definiția; a fost lăsată „pentru când avem token de pagină" — tokenul există acum (scope pages_messaging prezent), deci e ori reactivare deliberată, ori ștergere. `build_voice_pack.py` (134 linii) scrie `.voice_pack.json`, care pe disc e literalmente `{}` și pe care nu-l citește nimeni; funcția lui e dublată de .learned_playbook.md.
- *Primul pas:* Șterge build_voice_pack.py + .voice_pack.json (funcția e acoperită de playbook). Pentru fb_private_reply: confirmă cu ownerul că decizia „fără DM-uri" din 23-iun rămâne în picioare acum că tokenul permite DM — dacă da, șterge funcția și scrie motivul în SKILL.md ca să nu fie rescrisă.

**51. Filtrează notificările Meta Business după expeditor, nu doar după subiect**

- *De ce:* Punct rămas din memorie: „noise (notificări Meta) tot primește draft". Filtrele existente (SAAS_NOISE_SUBJ_RE, liniile 84-88) prind „mentioned you on"/„liked your" dar sunt bazate pe subiect; NON_CUSTOMER_SENDER_RE (liniile 77-80) listează curieri și app-uri Shopify, dar nu domeniile Meta. Fiecare notificare care trece costă 2 apeluri LLM și poate produce un draft absurd.
- *Primul pas:* Adaugă `facebookmail\.com|meta\.com|business\.facebook\.com|mail\.instagram\.com` în NON_CUSTOMER_SENDER_RE (linia 77) și verifică pe un dry-run de 50 de tichete email câte se mai strecoară.

**52. Sincronizeaza VPS-ul cu git-ul (o singura linie diferenta)**

- *De ce:* Masurat: diff-ul complet intre /root/Scripturi/cs_auto_draft.py (b5f474232b0ad203) si versiunea din git (57631aebe2f27af1) e de 2 linii de diff, adica UN rand adaugat in git: `"61586834387211": "Lab Noir"` in PAGE_STORE. Restul e identic. Deci divergenta nu e functionala — dar pe VPS un comentariu FB de pe pagina Lab Noir ar cadea pe magazin necunoscut.
- *Primul pas:* `deploy.sh --apply` (git-driven), NU scp manual — scp-ul manual e cauza documentata a divergentelor git↔VPS pe acest repo.

---

## 8. Ce a corectat verificarea adversarială

Toate cele patru documente inițiale au fost corectate. Le păstrez fiindcă greșelile arată exact
unde e ușor să te înșeli pe sistemul ăsta.


### Verificarea 1

**Motiv:** Am re-măsurat fiecare cifră pe VPS și am recitit codul independent. Structura și majoritatea cifrelor rezistă (1243/1244 linii, diff-ul de o linie „Lab Noir", 20 flaguri, 4 prompturi, 10 unelte MCP, 1336 drafturi, 6015 blocate, 5151 spam, 422 comandă neclară, coada 3156/9 cmd/31 hide/328 escalate/0 applied, 21 grounded, lipsa .learned_playbook.md + cs_actions.py + customer_identity.py) — dar trei afirmații-cheie sunt FALSE și una dintre ele e chiar teza documentului. (1) „464 tichete au picat pe 429" — rularea din 2-iul a atins 21 de tichete, nu 464; 464 era doar selecția. (2) „triaj_LLM_esuat_429: 10960" — 10.933 din ele sunt `[Errno 2] No such file or directory: 'uv'` (bug REPARAT din 29-iun), doar 26 sunt 429. Deci recomandarea #1 („blocajul e rata OpenAI") e construită pe o măsurătoare greșită. (3) „escaladari_flagate: 394" — sunt 197; 394 = linii care conțin ⛳, iar fiecare escaladare printează emoji-ul de două ori. În plus, capitolul 5 („Limba") descrie o precedență care nu există: `idn["language"]` nu e populat NICIODATĂ (cheia lipsește din dict-ul inițializat la 1028-1030, iar `idn.update({k: ... for k in idn})` copiază doar cheile existente) — l-am reprodus: un client care scrie engleză pe Esteban primește lang="ro". Numitorul e greșit peste tot: toate agregatele „peste tot jurnalul" amestecă 24 de rulări `--lean` (cod pre-fix) cu O SINGURĂ rulare `--ground`, iar 1.448 din cele 3.156 intrări ale cozii nu apar deloc în jurnal. Omisiunea cea mai gravă: Sonia este user `sonia`, grup `csapp`, iar `/root` e `drwx------ root:root` — nu poate CITI niciun fișier din harta de fișiere a documentului. Am găsit și 4 defecte vii, nedocumentate, în codul care va rula la repornire.

**Greșeli găsite:**

- FALS — „ultima rulare, 2-iul, a eșuat integral pe rate-limit OpenAI (429 pe toate cele 464 de tichete)”. Blocul ultimei rulări din `/root/Scripturi/data/cs_backlog.log` are 269 de linii (de la 136827 la 137095) și conține: 21 × „grounded — ”, 21 × „⛔ draft invalid”, 26 × „triaj LLM eșuat”, 47 de apariții ale lui „429”. Rularea a atins 21 de tichete, apoi a fost întreruptă manual (nu are marcaj DONE, ultima linie e în mijlocul unui tichet). 464 e doar numărul din antet („464 tichete care așteaptă răspuns”) = selecția, nu ce s-a procesat. Un factor de 22× între ce scrie documentul și ce s-a întâmplat.
- FALS — „triaj_LLM_esuat_429: 10960”. Am desfăcut mesajele: `grep "⚠️ triaj LLM eșuat" cs_backlog.log | sed -E 's/.*euristici: //' | sort | uniq -c` dă 10.933 × `[Errno 2] No such file or directory: 'uv'`, 26 × `HTTP Error 429: Too Many Requests`, 1 × `HTTP Error 520`. Doar 0,24% sunt 429. Cauza reală e bug-ul `uv` din `secret()` — REPARAT pe 29-iun (liniile 233-237 din versiunea de pe VPS au acum try/except). Documentul atribuie unui blocaj încă deschis (rata OpenAI) o defecțiune deja închisă, și pe asta își construiește recomandarea #1.
- FALS — „escaladari_flagate: 394”. `grep -cF '⛳ ESCALADARE'` = 197. 394 = `grep -cF '⛳'`, iar fiecare tichet escaladat printează emoji-ul de DOUĂ ori: o dată în `proposal_line` (linia 1092, „⛳ ESCALADARE %s: …”) și o dată în antetul tichetului (linia 1176, `flag = "⛳%s " % level`). Verificare pe componente: `⛳HIGH ` = 173, `⛳URGENT ` = 24, 173+24 = 197 = exact numărul de escaladări; 197+197 = 394. Cifra e dublată.
- FALS — capitolul 5, „Limba și registrul — precedența exactă”, pasul 3: „`idn["language"]` — verdictul LLM. Câmpul din triaj. Ultimul semnal înainte de implicit.” Câmpul NU ajunge niciodată în `idn`. Dict-ul inițializat la liniile 1028-1030 are 13 chei (problem, category, severity, escalate, escalation_reason, suggested_action, action, order, comment_action, product, spam, confidence, missing) — fără `language`. Linia 1040 e `idn.update({k: got.get(k, idn.get(k)) for k in idn})`, deci copiază DOAR cheile deja prezente; liniile 1041-1042 adaugă explicit doar new_address/new_city/new_zip/new_phone/items. Am rulat reproducerea cu un răspuns LLM care conține `"language":"en"`: `idn.get('language')` → `None`, iar linia 1138 pe un client care scrie engleză pe Esteban dă `lang = 'ro'`. Precedența reală are 3 trepte (detect_lang → STORE_LANG → "ro"), nu 4.
- FALS — „Cere strict JSON cu 16 câmpuri (394-408)”. Sunt 19 câmpuri, iar documentul le listează el însuși pe toate 19 în blocul de cod imediat următor (problem, category, language, severity, escalate, escalation_reason, suggested_action, action, order, new_address, new_city, new_zip, new_phone, items, product, comment_action, spam, confidence, missing). Contradicție internă în același paragraf. (Detaliu util: din cele 19 cerute, doar 18 sunt folosite — `language` se aruncă, vezi mai sus.)
- GREȘIT — tabelul de flaguri, primele două rânduri. `--limit N` e dat ca linia 849 și `--channel X` ca linia 850. Pe VPS `--limit` e la 848 și `--channel` la 849; în git sunt 849 și 850. Nicio numerotare nu le face pe amândouă corecte simultan — tabelul amestecă cele două fișiere. Restul rândurilor verificate (--scan 854, --lean 856, --ground 857, --skip-tagged 858, --no-comments 859, --photos 861, --only 866) sunt corecte în numerotarea VPS. La fel, antetul „Definite la liniile 849-868” e greșit: flagurile ocupă 848-867, iar 868 e `a = ap.parse_args()`.
- NENUMEROTAT CORECT (sistemic) — TOATE numerele de linie din document sunt din fișierul de pe VPS, dar harta de fișiere îi spune Soniei că „ăsta e sistemul” arătând spre git (`plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py`). Fiindcă git are o linie în plus la 67 (pagina Lab Noir), fiecare referință de după linia 67 e cu 1 mai mică decât în fișierul pe care îl va deschide Sonia: main() e la 847 în git (nu 846), SYSTEM la 420-445 (nu 419-444), HALLU la 190-197 (nu 189-196), do_approve/ACTIUNE_APLICATA la 784 (nu 783), load_playbook la 616 (nu 615), post-filtrul la 1157-1173 (nu 1156-1172). Documentul nu spune nicăieri care numerotare folosește.
- IMPRECIS — „`_post` … backoff exponențial plafonat la 60s (248-266)”. Plafonul de 60s e doar pe ramura HTTPError (linia 258, `min(60, 2 ** attempt * 2)`). Ramura URLError/TimeoutError/ConnectionError (liniile 262-265) plafonează la 30s (`min(30, 2 ** attempt * 2)`). Tot așa, în `_llm_http` plafonul e 90s pe HTTP (linia 285) și 30s pe rețea (linia 291) — trei plafoane diferite, nu unul.
- MIS-ÎNCADRAT — „tichete_procesate_cu_lean: 7345” apare alături de „tichete_procesate_cu_ground: 21” ca și cum ar fi două moduri ale aceleiași configurații. Wrapperul de azi (`/root/Scripturi/cs_backlog.sh`, 2-iul 10:20) NU pasează `--lean` deloc. Cele 7.345 vin din primele 24 de rulări (24-iun → 29-iun), când wrapperul pasa `--lean`; cele 21 vin exclusiv din singura rulare de 2-iul, cu wrapperul actual. Sunt două sisteme diferite puse în același tabel, fără să se spună.
- MIS-ÎNCADRAT — „drafturi_salvate_in_Richpanel: 1336” prezentat ca bilanț „peste tot jurnalul”. Toate cele 1.336 provin dintr-o SINGURĂ rulare, prima, manuală, 2026-06-24 15:39:59. Am construit registrul pe rulare: rulările 2-24 (toate cronurile, 24-iun 21:00 → 29-iun 12:00) au salvat 0 drafturi fiecare, iar rularea 25 (2-iul) tot 0. Deci CRONUL nu a produs niciodată niciun draft — afirmație absentă din document și esențială pentru cine decide repornirea.

**Omisiuni:**

- ACCES — Sonia nu poate citi niciun fișier din harta de fișiere. Pe 84.46.242.181: `stat -c '%A %U:%G' /root` → `drwx------ root:root`. `getent group csapp` → `csapp:x:1003:sonia`. Toate cele 6 căi VPS din document (cs_auto_draft.py, backupurile, cs_backlog.sh, cs_backlog.log, .auto_draft_proposals.json) sunt sub /root. Precedentul e deja pe aceeași cutie: `/opt/orqestra-tickets` e `drwxrwsr-x ... csapp` + `/usr/local/sbin/cs-ctl` (8-sep-2026), făcut exact ca „să poată prelua Sonia proiectul”. Documentul o trimite într-un director închis.
- CONTEXT DE SUPRAVIEȚUIRE — Richpanel are un înlocuitor în lucru pe ACEEAȘI cutie: motorul de tichete Orqestra, preview pe https://tichete.arona.ro, mutat în /opt/orqestra-tickets pe 8-sep, cu cron activ `12 * * * *` (`/opt/orqestra-tickets/sync.sh`). Un document de predare despre un sistem 100% legat de Richpanel trebuie să spună că helpdesk-ul însuși e pe masă.
- VECINI VII — pe aceeași cutie rulează ACUM 5 cronuri CS care ating Richpanel, niciunul menționat: `*/30 8-16` `run_cs_pipeline.py --recent 1 --push` (scrie taguri + note private), `0 2` `run_cs_pipeline.py --recent 3 --push`, `50 9` `cs_queue_sync.sh`, `12 * * * *` sync Orqestra, `0 2` `run_cs_mirror.sh`. Programul propus pentru cs_backlog (`0 9-21/3`) se suprapune la 09:00/12:00/15:00 peste fereastra pipeline-ului, iar lock-urile sunt DIFERITE (`cs_backlog.lock` vs `cs_pipeline.lock` vs `orq_tickets_sync.lock`), deci nimic nu previne concurența pe același buget de 60 cereri/min.
- DEFECT VIU #1 — brandul devine PREFIX în modul `--ground` (configurația de azi). `lookup_orders` pune `"brand": store_prefix(r[0])` (liniile 545 și 557), iar `store_prefix` întoarce prefixul brut („EST”, „CZ”, „GRAN”, „BONBG”), NU numele magazinului. Linia 1004 face `store_name = orders[0].get("brand")`. Consecințe reproduse: semnătura devine „Cu drag, Echipa EST”; `STORE_LANG.get("CZ")` → None (semnalul de limbă pentru Bonhaus CZ moare, și e singurul rămas după ce `idn["language"]` e mort); `STORE_PHONE.get("EST")` → "" (fără TELEFON_COMANDĂ pe presale/comandă nouă). Tabelul de corecție `ORDER_PFX` (linia 68) există și mapează EST→Esteban, dar e folosit doar invers, la linia 454.
- DEFECT VIU #2 — garda anti-gunoi lipsește din `--approve` și `--send`. Verificarea „draftul începe cu (eroare” e DOAR în bucla principală, linia 1212. `do_approve` (linia 792: `if p.get("draft") and _cmode != "hide"`) și `do_send` (linia 816-820) nu o au. În `/root/Scripturi/.auto_draft_proposals.json` sunt 459 de intrări al căror `draft` începe cu „(eroare” — un `--send <conv>` pe oricare dintre ele trimite LIVE textul „(eroare LLM: …)” la client ȘI închide tichetul (linia 826).
- DEFECT VIU #3 — `--close-spam` scrie în Richpanel chiar și fără `--create-draft`. Liniile 1063-1065 (`add_tags(mcp, cid, ["spam"])` + `update_conversation_status` CLOSED) nu sunt sub garda `if a.create_draft and cid`. În același timp linia 906 printează „DRY-RUN — nimic scris”, iar docstring-ul modulului spune „DRY-RUN: … nimic scris”. `tag_id()` poate și CREA taguri (linia 589). Deci `--close-spam` fără `--create-draft` = închideri reale de tichete sub eticheta „dry-run”.
- DEFECT VIU #4 — unealta MCP `cs_draft` din `mcp_server.py` e ruptă. `DRAFT=os.path.join(HERE,"cs_auto_draft.py")` iar `cs_draft()` apelează `_run(DRAFT,["--conv",conv])`. `cs_auto_draft.py` NU are flag `--conv` (îl are `cs_draft_reply.py`, linia 160). Rulat local: `rc=2 … error: unrecognized arguments: --conv 274155`. `mcp_server.py` e cel mai recent fișier din skill (20-aug), iar serverul `arona-cs-inbox` e înregistrat la user scope — deci unealta e vizibilă și nefuncțională.
- DIVERGENȚA MANUAL-vs-CRON pe model și cost. `uv` există la `/root/.local/bin/uv`, care e în PATH-ul interactiv dar NU în PATH-ul cron (crontab n-are linie `PATH=`, deci cron folosește `/usr/bin:/bin`). `llm()` cere întâi `secret("ANTHROPIC_API_KEY")`, iar `secret()` cade pe `uv run KB`. Deci același wrapper rulat de mână vs din cron poate lua căi diferite. Bonus: `KB` se rezolvă pe VPS la `/core/scripts/kb.py` (HERE=/root/Scripturi, minus 3 niveluri) — fișier care NU există; verificat. Deci pe VPS seiful e structural inaccesibil, singura sursă de secrete e `.env`, iar `ANTHROPIC_API_KEY` chiar e acolo (confirmat, `.env` root-600, 49 chei) dar wrapperul nu-l exportă. Există și un `DRAFT_OPENAI_API_KEY` separat în `.env`, pe care nimic din cs_auto_draft nu-l citește.
- REGISTRUL RULĂRILOR lipsește complet — fără el nu se poate decide repornirea. Vezi tabelul din de_adaugat_md: rulare 1 (manuală) = 1.336 drafturi, rulările 2-25 = 0 drafturi și 6.015 blocate. Lipsește și reconcilierea completă a pâlniei, care iese exactă: 7.345 (lean) + 21 (ground) = 7.366 tichete au ajuns la generare; 6.030 au primit „(eroare LLM…)” (12.045 apariții în jurnal ÷ 2, minus 15 fără cid); 7.366 − 6.030 = 1.336 = exact drafturile salvate. Zero gunoi scris.
- JURNALUL NU E ISTORIA — 1.448 din cele 3.156 intrări ale cozii nu apar deloc în cs_backlog.log (verificat prin căutarea fiecărui `#<conv_no>` în text). Ultima scriere reală a sistemului e 3-iul 11:28:45 (mtime coadă), la un minut după ultima editare de cod (3-iul 11:27:55) — o rulare care NU a lăsat nicio linie de jurnal. Documentul dă „log_interval … → 2026-07-02 10:00” ca sfârșit al poveștii.
- CE E DE FAPT ÎN CELE 5.147 EXCLUDERI „SPAM/automat” — documentul le numește „excluse_de_LLM_spam_automat”. În perioada respectivă triajul LLM a crăpat de 10.933 ori din ~12.506 apeluri (≈87%), deci majoritatea au fost clasificate de regexul `categorize_hint` (`RULES`, regula `spam_automat`), nu de LLM. Ramura de print de la 1053 e comună pentru `idn["spam"]` și `cat == "spam_automat"`, deci jurnalul nu le distinge — atribuirea către LLM e nesusținută.
- PLAYBOOK-UL, ACOPERIREA REALĂ — recomandarea „scp .learned_playbook.md pe VPS” nu spune ce acoperă. Fișierul (17.884 octeți) are 9 categorii: livrare_wismo, retur, anulare, problema_produs, modificare_comanda, schimb_swap, presale_intrebare, plata_factura, refuz_livrare — exact `CATS` din `cs_procedures.py` (linia ~31), deci lista e închisă prin cod. Lipsesc `altele`, `comentariu_social`, `recenzie_feedback`, `comanda_noua`. În coada de pe VPS, `altele` (1.119) + `comentariu_social` (819) = 1.938 din 3.156 = 61% din tichete — care nu vor primi niciodată blocul „PROCEDURA INVATATA”, oricât ai copia fișierul.
- CONTRADICȚIE ASCUNSĂ — documentul spune (corect) „escaladari_rutate_in_Richpanel: 0” dar nu explică de ce, deși codul pare să rute. Cauza: linia 1201 e `if is_esc and not a.lean`, iar toate cele 197 escaladări s-au produs în rulări `--lean`. Consecința operațională, absentă: la repornirea cronului cu wrapperul de AZI (`--ground`, fără `--lean`), rutarea se va activa PRIMA DATĂ în producție — priority HIGH + taguri `escaladare`/`esc-high`/`esc-urgent`/`de-sunat` + notă privată, scrise în tichete reale, comportament nevalidat niciodată live.
- GARDA DE ECHIPĂ E OCOLITĂ — `~/.claude/settings.json` are `permissions.deny: ["mcp__richpanel__send_message"]` (verificat azi). `cs_auto_draft.py` nu folosește uneltele MCP ale lui Claude Code: are propria clasă `MCP` care lovește `https://mcp.richpanel.com/mcp` prin JSON-RPC brut (liniile 240-274) și cheamă `send_message` direct (liniile 820 și 1218). Deci `--send` și `--apply-send` trec pe lângă regula de deny a echipei. Asta trebuie scris explicit într-un document de predare.
- ARHEOLOGIA BACKUPURILOR — sunt 6 backupuri, nu 5, și toate sunt DEJA post-fix (fiecare are `--ground`, `HALLU`, `AWB_RE` și try/except în `secret()`). Build-ul care a produs cele 10.933 crash-uri `uv` nu mai există pe disc — supraviețuiește doar în jurnal și în istoricul git. Al 6-lea, `.bak-efficiency-0703` (1235 linii, 19 flaguri), arată exact ce a adus editarea din 3-iul: `--fast-triage` + prompt caching Anthropic (`cache_control: ephemeral`) — adică lucru pe COST, ceea ce leagă oprirea de motivul real („credite LLM”, pauza CS din 3-iul).
- PROSPEȚIMEA SURSELOR PENTRU `--ground`, nemăsurată în document. Azi, 15-sep: `metrics.orders` = 368.231 rânduri, `MAX("shopifyCreatedAt")` = 2026-09-15 10:00:52 (proaspăt, cu toate cele 7 coloane cerute de `lookup_orders` prezente); `/root/Scripturi/data/profitability.db` = 722 MB, scris azi 12:10, `profit_orders` = 843.459 comenzi, 804.057 cu AWB (față de 294k/278k în memoria din iunie). Ambele surse sunt vii. Dar `metrics.orders` (368k) e mult mai mic decât `profit_orders` (843k) — deci pasul 1 al grounding-ului (căutarea după email/telefon) are acoperire structural mai slabă decât pasul 2 (după AWB/nume).
- MODELELE ȘI COSTUL, absente. `llm()` (295-314): Anthropic `ANTHROPIC_MODEL` implicit `claude-sonnet-4-6`, max_tokens 900, cu prompt caching; OpenAI `DRAFT_MODEL` implicit `gpt-4o` (wrapperul îl forțează la `gpt-4o-mini`), temperature 0.2, `response_format=json_object` doar pe triaj. Vederea pozelor (`_vision_describe`, 329-350) are propriul model: `VISION_MODEL` implicit `gpt-4o-mini` pe OpenAI. 2 apeluri LLM/tichet (1 cu `--fast-triage`), plus 1 vizual per poză.
- INTERACȚIUNEA `--fast-triage` × comentarii, nedocumentată. `categorize_hint` (201-208) întoarce `comentariu_social` pentru canalele de comentarii. Cu `--fast-triage`, gate-ul de la 1034 sare triajul LLM pentru orice hint diferit de `altele`/`spam_automat` — deci pe comentarii publice triajul nu mai rulează niciodată, iar `comment_action` rămâne `none` (hide nu se mai propune vreodată) și `idn["spam"]` rămâne False. Flagul introdus pe 3-iul dezactivează tăcut moderarea comentariilor.
- SECRETELE COMPLETE — „secrete_necesare_minim: 3” e corect ca minim de cron, dar lista completă a cheilor pe care le cere codul e: RICHPANEL_MCP_TOKEN (linia 872), OPENAI_API_KEY / ANTHROPIC_API_KEY (296, 306, 331, 340), DATABASE_URL_METRICS (521), plus 6 chei Meta încercate în ordine de `fb_page_token` (linia 679): META_PAGES_TOKEN, META_SYSTEM_TOKEN_3, META_SYSTEM_TOKEN, META_SYSTEM_TOKEN_2, META_SYSTEM_TOKEN_4, META_USER_TOKEN. Pe VPS: `grep -cE '^META_' /root/Scripturi/.env` = 0, iar KB e inaccesibil (calea ruptă) → `fb_page_token` întoarce mereu None.
- FRAGILITATEA TOKENULUI RICHPANEL, nemenționată. `RICHPANEL_MCP_TOKEN` e cheia de 16 caractere generată de fluxul OAuth al MCP-ului, fără expirare și fără refresh; API-ul oficial Richpanel e BLOCAT pe cont (403 „API access is not enabled”, verificat live 31-aug ca TENANT_ADMIN). Dacă cineva șterge tokenul din pagina Richpanel API Keys, moare tot sistemul, iar regenerarea cere re-auth OAuth interactiv — nu se poate face din cron.

### Verificarea 2

**Motiv:** Partea de criminalistică pe fișiere e solidă și am reprodus-o cifră cu cifră (toate cele 8 hash-uri de commit, cele 6 backup-uri, diff-ul de o linie, 1.336 / 1.306 / 2.642 drafturi distincte cu intersecție 0, 2.289 OPEN / 352 CLOSED / 1.397 tag, 843.459 profit_orders, lipsa .learned_playbook.md pe VPS). Dar documentul cade pe trei clase de probleme, toate verificabile: (1) cifre corecte ca număr, greșite ca ÎNȚELES — „328 escaladări scrise în Richpanel" sunt de fapt 154, fiindcă rularea din 24-iun a mers pe `--lean`, iar linia 1202 (`if is_esc and not a.lean:`) sare rutarea; „5.151 tichete spam" sunt 5.151 de LINII de log peste 25 de rescanări ale aceluiași backlog, tichetele distincte fiind 366; „2.289 încă deschise AZI" e starea înghețată la 3-iul (max(updated_at) în oglindă = 2026-07-03T08:44, `--recent 1/3` nu mai revizitează iulie). (2) fapte pur și simplu false, cu consecință în „cum": `cs_procedures.py` ESTE pe VPS (în clona git), `.learned_playbook.md` există deja local și lipsește de pe VPS fiindcă e în `.gitignore`, iar `deploy_parity.py:64` e `def discover()` — lista de directoare e `SCAN_DIRS`, linia 30. (3) omisiuni care contează mai mult decât drift-ul: flagul `--apply-send` (trimite LIVE la client + închide tichetul, în masă — nedocumentat nici în SKILL.md), serverul MCP `arona-cs-inbox` din 20-aug care e punctul de intrare de AZI și e RUPT (cheamă `--conv`, argument inexistent → rc=2), a patra copie a fișierului (cea din marketplace, pe care o execută MCP-ul), ce pierde de fapt `--fast-triage` (sare garda LLM de spam), și faptul că „eficiența" din 3-iul a produs 4 drafturi, nu 1.306.

**Greșeli găsite:**

- „escaladari_scrise_in_richpanel: 328" — FALS, realul e 154. Rularea din 24-iun a mers cu `--lean` (dovadă: logul tipărește „(lean — fără context 360)", string emis exclusiv la cs_auto_draft.py:1008, sub `elif a.lean:` de la 1007). Iar rutarea escaladării e închisă la cs_auto_draft.py:1202 — `if is_esc and not a.lean:` — deci priority HIGH + tag `escaladare` + `add_private_note` NU s-au scris deloc în acea rulare. Măsurat: linii `⛳ Rutat:` (singura dovadă de scriere, tipărită la linia 1209) = 0 în cs_backlog.log, 154 în cs_draft_all.log. Cele 174 din tabelul agentului sunt linii `⛳ ESCALADARE …` = DECIZII, nu scrieri. Consecință reală, mai gravă decât cifra: 174 de clienți au primit un draft de AȘTEPTARE generat cu promptul HOLDING (linia 447: „un coleg revine cât mai curând") în spatele căruia nu există nicio escaladare în Richpanel.
- „tichete_spam_excluse: 5151" — e un număr de LINII de log, nu de tichete. Cele 25 de rulări au rescanat același backlog, deci același tichet apare de ~16 ori. Măsurat cu parser pe antetul `[n/N] #<conv>`: tichete DISTINCTE excluse ca spam = 293 în cs_backlog.log + 84 în cs_draft_all.log = 366 în uniune. Aceeași eroare de numitor la „drafturi_respinse_de_garda_uv: 6015" (tichete distincte lovite de gardă: 378) și la „esecuri_triaj_LLM_uv: 10933" (număr de evenimente peste rescanări, nu de tichete). Prin contrast, 2.642 ESTE corect ca tichete distincte — am verificat: uniune = 2.642, intersecție între cele două jurnale = 0.
- „tichete_cu_draft_inca_OPEN_azi: 2289" — cifra se reproduce exact (2.289 OPEN / 352 CLOSED din 2.641 regăsite în oglindă), dar „azi" e fals. `SELECT max(updated_at) FROM tickets JOIN <cele 2.642>` în /root/Scripturi/data/richpanel_tickets.db = 2026-07-03T08:44:16Z, iar distribuția pe luni e 2026-05: 99, 2026-06: 1.781, 2026-07: 761 — niciun rând după 3-iul. Oglinda e hrănită de `run_cs_pipeline.py --recent 1` (crontab linia 24, la 30 min) și `--recent 3` (linia 26), care trag doar ultimele zile; tichetele din iulie nu mai sunt niciodată revizitate. Deci 2.289 e starea de acum 74 de zile, nu de azi.
- „Nici cs_procedures.py nu e pe VPS, deci nu se poate regenera acolo" — FALS. Există la /root/Scripturi/team-intelligence/plugins/gigi/skills/cs-procedures/cs_procedures.py (7.757 octeți), adus de auto-pull-ul git. Iar intrarea lui e tot pe VPS și e VIE: /root/Scripturi/data/richpanel_tickets.db, 478 MB, 255.767 tichete, max(updated_at)=2026-09-15T09:59:58Z. `cs_procedures.py:23` citește `RICHPANEL_DB` din env, deci regenerarea pe VPS merge cu o singură comandă. Agentul a verificat doar calea PLATĂ /root/Scripturi/cs_procedures.py.
- „Genereaza local (PROC_MODEL=gpt-4o cs_procedures.py --category all --out .learned_playbook.md), scp in /root/Scripturi/" — premisa e greșită: fișierul EXISTĂ deja local, la /Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/.learned_playbook.md, 17.884 octeți, 23-iun, 9 categorii care se parsează curat cu `load_playbook()` (livrare_wismo 1.766 car., retur 2.117, anulare 1.538, problema_produs 1.746, modificare_comanda 1.740, schimb_swap 1.928, presale_intrebare 2.064, plata_factura 2.040, refuz_livrare 1.719). Cauza-rădăcină a absenței de pe VPS nu e „nu l-a generat nimeni", ci `.gitignore` linia 6 din chiar acel folder, care îl exclude DELIBERAT („PII sau se regenerează") — deci niciun deploy prin git nu-l va duce vreodată acolo.
- „Adauga ... in lista de directoare din discover(), /root/Scripturi/deploy_parity.py:64" — linia greșită. La 64 e `def discover():`. Lista de directoare e constanta `SCAN_DIRS`, la linia 30, și conține exact trei intrări: `plugins/gigi/skills/metrics-cache/scripts`, `shared/scripturi-tools`, `plugins/core/scripts`. (Concluzia că cs-draft-reply nu e acoperit de paritate e corectă — doar adresa e greșită.)
- „Cele trei copii ale aceluiasi fisier" — sunt PATRU. Lipsește /Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py (sha256 57631aebe2f27af1 = git HEAD, 93.459 octeți, 4-sep-2026). E copia pe care o execută efectiv serverul MCP `arona-cs-inbox`, înregistrat user-scope în ~/.claude.json cu `uv run <acea cale>/mcp_server.py`. Adică e singura copie care „rulează" astăzi — și nici ea n-are `.learned_playbook.md`.
- „drafturi_scrise_2_3_iul: 1306" — suma e corectă, dar ascunde exact lucrul care contează. Împărțit pe cele două anteturi din cs_draft_all.log: 2-iul (`DRAFT ALL-OPEN`, claude-sonnet-4-6) = 1.302 drafturi, din care 85 pe email și 1.217 pe facebook_feed_comment; 3-iul (`DRAFT REAL-SUPPORT`, Haiku + --fast-triage) = 8 tichete văzute, 4 drafturi. Deci munca de „eficiență" din 3-iul, prezentată ca realizarea zilei, a fost exercitată pe 4 drafturi — practic netestată.
- Atribuirea celor 1.217 drafturi pe comentarii publice: agentul citează conținutul ACTUAL al `cs_draft_all.sh`, care are `--no-comments` și 5 canale — deci textul citat nu putea produce acel rezultat. Vinovatul e `cs_draft_all.sh.bak-0703` (827 octeți): buclă pe 7 canale, incluzând `facebook_feed_comment` și `instagram_comment`, FĂRĂ `--no-comments` și fără `ANTHROPIC_MODEL` (deci default `claude-sonnet-4-6`). Fișierul curent (1.182 octeți) e REPARAȚIA, nu cauza.
- „_llm_http (cs_auto_draft.py:~270) reincearca doar pe 429/500/502/503/504" — incomplet, iar linia e 277 exact. Blocul reîncearcă și pe `urllib.error.URLError`, `TimeoutError`, `ConnectionError` (liniile 288-291). Observația de fond (400 și 529 nu se reîncearcă) rămâne corectă și e utilă.
- Lab Noir: „Fara ea, orice tichet de pe pagina Lab Noir primeste brand necunoscut si se semneaza «echipa noastra»" — supralicitat. `store_name` are trei trepte (cs_auto_draft.py:991-1013): PAGE_STORE → `brand_from_email` → `orders[0]["brand"]`. Iar `EMAIL_BRAND` (linia 125) conține `"labnoir.ro": "Lab Noir"`, deci pe EMAIL brandul iese corect și pe copia VPS, fără linia 67. Gaura reală e îngustă: tichete Lab Noir de pe pagina FB/IG (id 61586834387211) care în plus nu găsesc nicio comandă la `--ground`. Fix-ul propus rămâne bun, dar impactul declarat e mai mare decât realitatea.

**Omisiuni:**

- `--apply-send` — flagul care transformă sistemul din „draft" în „AI care răspunde clientului". argparse la linia 863, implementare la 1216-1222: pentru FIECARE tichet ne-escaladat și ne-public dintr-o rulare cheamă `send_message`, pune tag `ai-sent` și `update_conversation_status=CLOSED`. Nu apare nici în documentul agentului, nici în SKILL.md (care descrie doar `--send <conv>`, per tichet, la liniile 146-148). Pentru o predare despre „AI-ul care răspunde la mesajele clienților", ăsta e cel mai periculos lucru din fișier. Măsurat că nu s-a folosit niciodată: 0 apariții de „TRIMIS LIVE" și 0 de „ai-sent" în ambele jurnale.
- Serverul MCP `arona-cs-inbox` (mcp_server.py, 3.347 octeți, 20-aug-2026) — punctul de intrare de ASTĂZI, înregistrat user-scope în ~/.claude.json. ȘI E RUPT: linia 34 cheamă `_run(DRAFT, ["--conv", conv] + …)` cu DRAFT = cs_auto_draft.py (linia 22), dar cs_auto_draft.py nu are `--conv` (are `--only`). Dovadă rulată: `python3 cs_auto_draft.py --conv 274123` → `error: unrecognized arguments: --conv 274123`, rc=2. `_run` nu prinde nimic, deci unealta întoarce tăcut „(fără output) [stderr] …". `--conv` există doar în cs_draft_reply.py:160.
- Ce pierde de fapt `--fast-triage` (linia 1035: `if a.fast_triage and _hint and _hint not in ("altele","spam_automat"): pass`). Când sare apelul de triaj, `idn` rămâne dicționarul-implicit de la 1029: `spam=False`, `escalate=False`, `problem=""`, `product=""`, `action="none"`, `comment_action="none"`, `confidence=0.0`. Deci: garda LLM de spam e ocolită complet (rămân doar cele 5 regexuri deterministe), promptul de draft primește „PROBLEMA IDENTIFICATĂ: (neclar)" și „PRODUS: —", și nu se mai propune NICIO acțiune. Asta e configurația din `cs_draft_all.sh` de azi. Ordin de mărime: cu triaj LLM, 293 de tichete au fost excluse ca spam; fără el, cea mai mare parte ar primi draft.
- Prompt caching-ul e INERT în configurația reală. Comentariul din cod (linia 300) spune el însuși: pragul de cache e 4.096 token pe Haiku 4.5, iar SYSTEM are ~2.6k — deci nu prinde. `cs_draft_all.sh` linia 8 setează exact `ANTHROPIC_MODEL=claude-haiku-4-5`. Câștigul din 3-iul vine din `--fast-triage`, nu din caching.
- Lanțul ANTI-HALUCINARE, inima calității: regexul `HALLU` (liniile 190-197 — prinde „am verificat/nu am găsit", status de comandă, „N zile lucrătoare", dimensiuni `NNxNN`/`NN cm`, orice preț „N lei") + post-filtrul de la 1158-1176 care regenerează O DATĂ cu un corectiv și, dacă tot fabrică, cade pe unul din două șabloane sigure hard-codate. Și, esențial: se DEZACTIVEAZĂ când `has_order_data(od_ctx)` sau `photo_blk` sau `_ad_has_catalog` sunt adevărate.
- Telefonul se pierde tăcut: liniile 932-933 — `phone = raw_phone if (raw_phone.isdigit() and 9 <= len(raw_phone) <= 13) else ""`. Orice număr stocat ca `+40…`, cu spații sau cu prefix internațional devine `""` → nu se mai pune tagul `de-sunat` (linia 1203) și nu se mai caută comanda după telefon în `lookup_orders`. Ironic, `norm_phone()` (linia 508) ar fi știut să-l normalizeze — dar filtrul e ÎNAINTEA ei.
- Gardele de spam citesc `subj` + `first` (PRIMUL mesaj), nu ultimul — liniile 1049-1054. Rateu documentat în producție, în chiar jurnalul VPS: `#279169 · Grandia · email · 🚫 SPAM/automat → EXCLUS`, motiv „Clientul a primit drona defectă (o elice nu funcționează) … ultimul mesaj este o notificare automată de la Yahoo Mail". Un client cu produs defect a fost aruncat ca spam.
- Acoperirea reală a playbook-ului: cele 9 categorii pe care le are (livrare_wismo, retur, anulare, problema_produs, modificare_comanda, schimb_swap, presale_intrebare, plata_factura, refuz_livrare) NU includ `comentariu_social`, `recenzie_feedback`, `comanda_noua`, `altele` — adică exact canalul pe care s-au scris 1.217 drafturi. Și e tăiat la `learned[:1800]` (linia 1137), ceea ce trunchiază retur (2.117), presale_intrebare (2.064), plata_factura (2.040) și schimb_swap (1.928).
- Poarta de limbă și gramatică `grammar_audit.py` — cum se rulează (`--json` → marcajul `@@JSON@@` pe ultima linie → `--file`), că e MULTILINGVĂ și compară limba răspunsului cu `mesaj_client`, că modelul e `AUDIT_MODEL` (default gpt-4o, temperature 0, response_format json_object), și capcana ei cunoscută din memorie: a dat 4 fals-pozitive citind cuvinte din `mesaj_client` în loc de `draft`.
- Restul setului de fișiere, nicăieri în document: `cs_draft_reply.py` (o singură conversație, `--conv`, `--similar N`, `--similar-api`), `cs_ticket_index.py` (precedent semantic peste subiectele tichetelor REZOLVATE, cere `richpanel_tickets.db` + `gigi:semantic-search`), `build_voice_pack.py` + `.voice_pack.json` — care are 2 octeți, adică `{}`, deci n-a fost construit niciodată, `DEMO.md`. Pe VPS: `cs_photo.py` (30.859 o.) plus încă PATRU `.bak`-uri ale lui din 29-iun (care ridică numărul real de backup-uri ale zilei de la 6 la 10), `cs_detail.py` (25-iul) și `.auto_draft_proposals.json` (7.286.865 o., 3-iul — conține PII: drafturi + context de comandă).
- Mediul CS de AZI, pe care Sonia îl moștenește odată cu drafterul oprit și care e singurul lucru viu din zonă: `run_cs_mirror.sh` (crontab 201, 02:00) → `cs_mirror.db` 47 MB (rp_ticket 12.234, rp_message 24.656, gm_message 7.554) + gmail_sync + parity_check, cu regula măsurată „zilnic, nu săptămânal, fiindcă la 3 zile 57,1% din comentariile FB sunt deja șterse"; `run_cs_pipeline.py` (crontab 24 și 26) → `richpanel_tickets.db` 478 MB / 255.767 tichete, proaspăt azi, care face ȘI `git pull --ff-only` pe clona VPS (verificat: „git pull: Already up to date." în cs_intraday.log); `cs_queue_sync.sh` (crontab 109, 09:50). `richpanel_tickets.db` e exact intrarea de care are nevoie `cs_procedures.py` ca să regenereze playbook-ul lipsă, direct pe VPS.
- Lista de prerechizite pentru repornire ratează faptul principal: bugul `uv` care a omorât 23 de rulări de cron E DEJA REPARAT în copia deployată. `secret()` (liniile 226-239) învelește subprocesul `uv` în try/except cu cache și întoarce `""` în loc să arunce. Reparația a intrat pe 29-iun în PR #327, iar copia plată de pe VPS (fe410875, 3-iul) o are. Deci repornirea cronului nu va repeta acel eșec. Ce rămâne de spus: `cs_backlog.sh` NU exportă `ANTHROPIC_API_KEY` (doar `cs_draft_all.sh` o face), deci calea de cron va rula pe `gpt-4o-mini` prin OPENAI_API_KEY.
- Cele 321 de HTTP 400 sunt lăsate ca „nu știu", dar se poate spune ceva util și verificabil: corpul erorii a fost DISTRUS de cod, nu lipsea din date. `_llm_http` (linia 281) lasă `urllib.error.HTTPError` să se propage, iar `str(e)` e doar „HTTP Error 400: Bad Request" — corpul JSON cu `invalid_request_error` n-a fost citit niciodată. Asta explică de ce nu se poate diagnostica azi și dă și fix-ul (un `e.read()` în mesaj). Tiparul — rafală continuă de la [324/457] care nu se mai reface, fără corelație de conținut — se potrivește cu o condiție de cont, nu de tichet; Anthropic întoarce 400 (nu 402) pentru sold insuficient. Rămâne ipoteză, dar e testabilă și merită scrisă ca atare.

### Verificarea 3

**Motiv:** Am reprodus exact recensământul lor pe populația din 24-iun (1.336 salvate / 174 așteptare / 1.162 normale / 360 HALLU = 31,0% / lookup 341 = 29,3% / preț 18 / dimensiune 2 / status 1) — deci partea măsurată e corectă. DAR premisa centrală e falsă: ei au analizat UN SINGUR log. Pe VPS mai există `/root/Scripturi/data/cs_draft_all.log` (1.994.232 b, 3-iul 11:42) cu încă **1.306 drafturi scrise în Richpanel** pe 2-3 iul, pe Claude + `--ground`, plus **154 escaladări rutate efectiv** (HIGH + tag + notă privată). Asta răstoarnă: totalul de curățat (2.642, nu 1.336), afirmația „TOATE într-o singură rulare pe 24-iun", „escaladări rutate = 0", fereastra de rulare (ultima scriere = 3-iul, nu 2-iul) și mai ales verdictul de calitate — pe ultima campanie de producție HALLU prinde **2,6%**, nu 31,0%. Recomandarea „nu reporni, calitatea e zero" e construită pe cohorta cea mai proastă (lean, fără lookup, gpt-4o-mini), ignorând cohorta care arată de 12× mai bine. În plus: 4 din cele 5 „dependințe lipsă" EXISTĂ pe VPS (în clona git auto-actualizată), iar grounding-ul — reparația-cheie din 29-iun — e orb azi pe două branduri întregi (Nubra, Lab Noir) și pe Magdeal, lucru pe care l-am dovedit rulând `lookup_orders` pe comenzi reale.

**Greșeli găsite:**

- FALS: «a scris 1.336 de drafturi in Richpanel, TOATE intr-o singura rulare manuala pe 24-iun» și «drafturi_salvate_in_richpanel: 1336». Dovadă: pe VPS sunt DOUĂ loguri cu drafturi salvate — `grep -rl "DRAFT salvat" /root/Scripturi/data/` → cs_backlog.log (1336) ȘI cs_draft_all.log (1306). Total real = **2.642 evenimente de salvare**, pe **2.638 tichete distincte** (suprapunere între cele două populații = 0, verificat pe numerele de conversație). Consecință: lista de curățat din DE IMBUNATATIT #1 („cele ~1.162 drafturi vechi") subestimează de peste 2×.
- FALS: «escaladari_rutate_efectiv_in_richpanel: 0». Dovadă: `grep -c "⛳ Rutat" /root/Scripturi/data/cs_draft_all.log` = **154** (145 HIGH + 9 URGENT, numărate din flagul ⛳ de pe linia de tichet). Adică 154 de tichete au primit REAL, în Richpanel, `priority=HIGH` + tag-urile `escaladare`/`esc-high`/`esc-urgent`/`de-sunat` + o notă privată de brief. Ele sunt tot acolo și nu se pot șterge prin API — e o a doua populație de curățat, pe care documentul nu o menționează deloc.
- FALS ca verdict de calitate: «filtrul anti-halucinare din codul de azi prinde 31,0% din drafturile normale». Cifra e corectă DOAR pentru cohorta lean din 24-iun (o pot reproduce: 360/1162). Pe ULTIMA campanie de producție (2-iul, Claude + `--ground`) același regex `HALLU`, importat din același fișier de pe VPS, prinde **30 din 1.159 = 2,6%** — de 12× mai puțin. Defalcat: lookup fabricat 14 (1,2%) vs 341 (29,3%). În plus, în acea rulare post-filtrul a intervenit REAL în producție: 5 drafturi `claude+corectat` + 9 `șablon-sigur` (din `grep -oE "┌─ DRAFT.*\(...\)" cs_draft_all.log`). Afirmația din rezumat că «gate-ul nu a fost rulat niciodată pe ieșirea reală» e adevărată doar pentru grammar_audit.py, nu pentru filtrul HALLU.
- FALS: «pe VPS lipsesc 5 dependinte: playbook-ul invatat, cs-actions, customer-identity, kb.py si grammar_audit.py». Doar UNA lipsește efectiv de pe mașină. Celelalte patru EXISTĂ, în clona git de la `/root/Scripturi/team-intelligence` (HEAD 15f1b4b, `git pull --ff-only` la fiecare 30 min prin cronul `run_cs_pipeline.py`): verificat cu `[ -f ]` → OK plugins/gigi/skills/cs-actions/scripts/cs_actions.py, OK plugins/gigi/skills/customer-identity/customer_identity.py, OK plugins/core/scripts/kb.py, OK plugins/gigi/skills/cs-draft-reply/grammar_audit.py (și OK cs-photo/cs_photo.py, OK cs-procedures/cs_procedures.py). Ce e adevărat: **copia PLATĂ din /root/Scripturi nu le vede**, fiindcă `HERE=/root/Scripturi` iar căile calculate ies în afara arborelui (`/root/cs-actions/...`, `/core/scripts/kb.py`). Deci diagnosticul e corect, dar concluzia („nu există pe VPS") și remediul („scp" / „clonează skill-urile") sunt greșite — clona e deja acolo și e auto-actualizată.
- FALS: «`scp` .learned_playbook.md ... in /root/Scripturi/» ca remediu. `.learned_playbook.md` e în `.gitignore`-ul skill-ului (linia 3 din `plugins/gigi/skills/cs-draft-reply/.gitignore`), deci nu ajunge NICIODATĂ pe VPS prin git — corect. Dar singura copie existentă e cea de pe Mac, din **23-iun-2026**, iar sursa din care se regenerează (`/root/Scripturi/data/richpanel_tickets.db`, 478.224.384 b) e pe VPS și e actualizată **azi, 15-sep 12:05**. A copia un playbook de acum 3 luni în loc de a-l regenera din datele live e remediul greșit.
- FALS ca descriere a fișierului: «Fiecare rand din JSON duce `draft` + `private_msg` ca texte separate». `private_msg` NU apare NICIODATĂ în `cs_auto_draft.py` (`grep -n private_msg` → zero apariții; rândurile `--json` sunt construite la L1191-1194 VPS și conțin doar no/store/channel/cat/escalate/language/cust_msg/subject/orders/comment_action/draft). Ramura din grammar_audit.py L70-71 e cod mort din 23-iun, când s-a scos mecanismul de DM.
- INEXACT (off-by-one la citare): rândurile 3 și 4 din tabelul „Reparații de CONȚINUT" trimit la «L432, proceduri LIVRARE/WISMO». În numerotarea VPS, L432 e `- RETUR:`; procedura **LIVRARE/WISMO este la L431**. (Restul citărilor lor rezistă: secret() L226-238 ✓, IDENTIFY_SYS L392 ✓, ESCALADARE L410 ✓, regula „ultimul mesaj" din SYSTEM L428 ✓, marcajul din transcript L961 ✓, HALLU L189-195 ✓.)
- INEXACT: «treceri_tichete_procesate: 7366». 7.366 = numărul de BLOCURI de draft generate (`grep -c "┌─ DRAFT"`), nu treceri de tichet. Trecerile reale de tichet în cs_backlog.log sunt **40.724** (linii `[i/N] #nr`), din care 5.151 excluse ca spam și 28.207 sărite de `--skip-tagged`. Tot de aici vine și eroarea din tabel: rândul „2026-06-24 15:39 | Tichete 1336" — rularea a atins de fapt **1.560 tichete** (1.336 draftate + 222 spam + 2 sărite), cifra fiind confirmată de anteturile rulării (834+234+450+42+0).
- INEXACT ca atribuire: «escaladari_HIGH: 173, escaladari_URGENT: 24» sunt corecte ca total pe tot logul, dar lasă impresia că au fost identificate împreună. În rularea care a și salvat ceva (24-iun) sunt **173 HIGH + 1 URGENT**; celelalte **23 URGENT** apar exclusiv în rulările de cron care au eșuat integral — adică exact cazurile ANPC/juridic/amenințare n-au produs NIMIC: nici draft, nici prioritate, nici notă.
- INCOMPLET/înșelător: «perioada_rulare: 2026-06-24 15:39 -> 2026-07-02 10:00» și «rulari_cron_total: 25». Ultima scriere în Richpanel e din **2026-07-03 11:41** (antetul `===== DRAFT REAL-SUPPORT (Haiku + fast-triage, FARA comentarii) 2026-07-03T11:41:28 =====` din cs_draft_all.log). Documentul listează `cs_draft_all.sh` în tabelul de fișiere ca „wrapperul NOU", fără să spună că a și rulat — a rulat, dar a produs doar 4 drafturi (restul erau deja tag-uite).
- OMIS din tabloul de rulări, deși schimbă interpretarea: rularea din 2-iul a draftat **1.217 comentarii publice Facebook** (din 1.302 drafturi atribuite; restul 85 = email). Documentul repetă regula „comentariile sunt EXCLUSE, rămân pentru CS" (adevărată pentru `cs_backlog.sh`, care are `--no-comments`), dar rularea manuală din 2-iul a fost `ALL-OPEN`, fără `--no-comments`. Deci în Richpanel există 1.217 drafturi pe comentarii publice pe care nimeni nu le-a inventariat.

**Omisiuni:**

- Lipsește complet campania din 2-3 iul: al doilea log de producție (`/root/Scripturi/data/cs_draft_all.log`, 18.482 linii), cele 1.306 drafturi, cele 154 escaladări rutate, trecerea pe Claude și rata de halucinare de 2,6%. E cea mai recentă dovadă despre ce face sistemul și e singura care arată comportamentul post-reparații.
- Lipsește MECANISMUL pentru «0 escaladări rutate»: gate-ul `if is_esc and not a.lean:` (L1201 VPS). Rutarea escaladărilor e dezactivată tăcut de `--lean`. Wrapper-ul de cron de azi (`cs_backlog.sh`) folosește `--ground`, NU `--lean` → la o repornire rutarea SE VA APRINDE și va scrie prioritate HIGH, tag-uri și note private în Richpanel. Documentul recomandă o repornire-pilot fără să avertizeze că suprafața de scriere se schimbă.
- Lipsește cea mai importantă capcană de repornire: `cs_backlog.sh` exportă doar RICHPANEL_MCP_TOKEN, OPENAI_API_KEY, DATABASE_URL_METRICS și `DRAFT_MODEL=gpt-4o-mini` — NU exportă `ANTHROPIC_API_KEY` (care EXISTĂ în /root/Scripturi/.env). Cum `secret()` citește întâi env-ul și pe cron `uv` nu e în PATH (fallback la string gol), repornirea cronului pauzat readuce **gpt-4o-mini**, adică exact motorul cohortei cu 31% halucinare, aruncând configurația Claude (`cs_draft_all.sh`) care a produs cohorta cu 2,6%.
- Lipsește starea REALĂ a grounding-ului (`--ground`) azi — reparația-cheie din 29-iun. Am dovedit-o rulând `lookup_orders` pe VPS: EST000001 → 1 comandă cu status „Livrata" + AWB; **MAG000001 → 0 comenzi**, deși comanda EXISTĂ în profit_orders (aceeași comandă e găsită dacă se citează AWB-ul 00000000002). Cauză: pasul 1 interoghează `metrics.orders`, care are **0 rânduri** pentru MAG / PL / LUX / NOC (verificat prin `count(*)`), iar pasul 2 (profit_orders) se execută doar dacă pasul 1 a găsit ceva (`if byname:`). Magdeal singur = 14.989 comenzi în ultimele 90 de zile.
- Lipsește un defect măsurabil al regexului de comenzi: `ORDER_RE` (L72 VPS) nu recunoaște formatele reale `NUBRA####`, `LAB####`, `NOC####`, `LUX####`, `MD###`, `HU###`, `SK###`, `DUPBG###`, `ORC###`. Testat pe 216.496 comenzi reale din ultimele 90 de zile: **16.616 (7,7%) nu sunt recunoscute**, din care Nubra 9.161 și Lab Noir 4.257 — două branduri ÎNTREGI. Efect direct pe calitate: clientul dă numărul comenzii, sistemul nu-l extrage, `has_order_data` e False, filtrul HALLU intervine și forțează șablonul „îmi puteți spune numărul comenzii?" — adică exact ce clientul tocmai a scris.
- Lipsește motivul mecanic pentru care draftul zicea „nu am găsit nicio comandă": nu e halucinare liberă, e promptul care i-a dat modelului o premisă falsă. În `--lean`, câmpul de context `od` este literalmente `"    (nicio comandă găsită)"` (L1021-1025), indistinctibil de un lookup negativ real. Modelul a raportat fidel ce i s-a spus. Fixul nu e „mai mult prompt anti-halucinare", ci un text de context diferit pentru „nu s-a căutat" vs „s-a căutat și nu s-a găsit".
- Lipsește acoperirea reală a playbook-ului învățat, chiar dacă l-ai deploya: `.learned_playbook.md` parsează în **9 categorii** (livrare_wismo, retur, anulare, problema_produs, modificare_comanda, schimb_swap, presale_intrebare, plata_factura, refuz_livrare), dar `IDENTIFY_SYS` are 14. Pe populația din 24-iun, doar **460/1.336 = 34,4%** din drafturi cad într-o categorie acoperită; **65,6% nu pot folosi playbook-ul niciodată** (altele 745, recenzie_feedback 60, comanda_noua 56, comentariu_social 14). Documentul prezintă playbook-ul ca o dependință care ar repara lucrurile dacă ar fi copiată.
- Lipsesc trei defecte măsurate pe populația de 1.336, toate cu impact direct pe calitatea textului: (a) **491 drafturi (36,8%) au brandul „magazinul nostru"** → semnătură generică „echipa noastră"; (b) **745 (55,8%) au categoria `altele`** → fără procedură, fără voce, fără playbook; (c) **275 (20,6%) pe canale absente din dicționarul PLATFORM** (`facebook_message` 233, `email_from_widget` 42) → `PLATFORM.get(channel, ...)` cade pe stilul generic „ton prietenos, la obiect", deci emailurile din widget au pierdut regula de salut+semnătură. Există și o categorie malformată scăpată de LLM: `anulare|altele` (1), care ratează orice lookup în LEARNED.
- Lipsește un risc de limbă latent, deși limba a ieșit bine în practică: `brand_from_email` colapsează `bonhaus.cz` / `bonhaus.pl` / `bonhaus.bg` → „Bonhaus" (fără țară), fiindcă EMAIL_BRAND (L124-131) nu are intrări pentru ele. Consecință: `STORE_LANG.get("Bonhaus")` → None și `STORE_PHONE.get("Bonhaus")` → "". În cele 1.336, 101 drafturi Bonhaus internaționale au ieșit corect (82 cz, 18 pl, 1 bg) — DAR exclusiv pentru că `detect_lang` a prins diacriticele din mesajul clientului. Un client ceh care scrie fără diacritice cade pe română. Merită spus și partea bună, pe care documentul o omite: pe toate cele 1.336, limba detectată în draft e ro 1.232 / cz 83 / pl 19 / bg 1 — pista multilingvă a funcționat.
- Lipsește explicația pentru incertitudinea pe care singuri o declară („1.336 salvate dar doar 979 poartă tag ai-*"). Cauza e în cod: `add_tags` (L595-599) apelează `mcp.call(...)` și **aruncă rezultatul**, iar `MCP.call` (L267-274) înghite orice excepție într-un `{"_error": ...}`; `tag_id` (L579-593) întoarce None tăcut dacă `list_tags`/`create_tag` eșuează. Mesajul din log „✅ DRAFT salvat + tag ai-draft" e tipărit necondiționat după `create_draft`, deci confirmă doar draftul, nu tag-ul. Un 429 pe add_tags = tag pierdut, tăcut — și `--skip-tagged` devine ne-idempotent exact pe acele tichete.
- Lipsesc trei defecte în `grammar_audit.py` care fac gate-ul să raporteze fals „0 greșeli" — relevant fiindcă tot documentul se sprijină pe „a convers la 0": (a) **tot lotul intră într-UN SINGUR apel LLM** (L73-76, fără batching, fără --limit) — la 20 de drafturi merge, la 1.336 nu; (b) **dacă modelul nu întoarce rezultat pentru un id, textul e numărat drept CURAT** (L90-94: `r = by_id.get(i) or {}` → `iss` gol → `clean += 1`), deci o ieșire trunchiată se raportează ca perfecțiune; (c) `secret()` (L24-25) NU are try/except — pe VPS, unde `uv` lipsește din PATH, gate-ul crapă, spre deosebire de `secret()`-ul reparat din cs_auto_draft.
- Lipsește starea cozii de acțiuni, singura dovadă despre calea „propune + aprobă": `/root/Scripturi/.auto_draft_proposals.json` (7.286.865 b, 3-iul 11:28) conține **3.156 intrări, din care 9 cu acțiune propusă (`cmd`), 328 escaladate și 0 cu `applied=True`**. Adică în toată istoria sistemului s-au propus 9 acțiuni executabile și nu s-a aplicat niciuna.
- Lipsește verificarea de prospețime a surselor de grounding, obligatorie după schimbările din septembrie (Order Hub, AWBprint stale): `profitability.db` = 722.767.872 b, modificat **15-sep 12:10**, 843.459 comenzi (804.057 cu AWB), `max(created_at)=2026-09-15T00:29:27Z`; `metrics.orders` = 368.231 rânduri, `max(shopifyCreatedAt)=2026-09-15T07:00:52Z`. Ambele sunt VII azi — deci `--ground` nu e blocat de date moarte, ci de acoperire (punctele de mai sus). În iunie profit_orders avea 294k comenzi; azi are 843k.
- Lipsește drift-ul din SKILL.md, care e primul lucru pe care îl va citi Sonia: SKILL.md descrie cronul ca ACTIV (`0 9-21/3`, linia 83) fără să menționeze pauza din 29-iun, documentează `--lean` ca mod de backlog deși wrapperul folosește `--ground` din 29-iun, și nu pomenește `--fast-triage`, `--apply-send` sau caching-ul de prompt Anthropic. Un handover pe SKILL.md ar duce la repornirea configurației greșite.
- Lipsește contextul „ce rulează totuși azi" pe aceeași mașină, util ca să nu se creadă că CS-ul e oprit: `run_cs_pipeline.py` (la 30 min, 8-16, + 2:00), `cs_queue_sync.sh` (9:50) și `run_cs_mirror.sh` (2:00, `cs_mirror.db` 47 MB actualizat azi 03:00). Oglinda CS notează măsurat de ce rulează zilnic: comentariile FB trase la 3 zile sunt 57,1% „This message was deleted", trase în aceeași zi 14,4%.

### Verificarea 4

**Motiv:** Coloana vertebrală e corectă și am confirmat-o: sistemul e oprit, VPS diferă de git printr-o singură linie (am reprodus diff-ul), nu există watchdog, .learned_playbook.md lipsește de pe VPS, tokenul Meta chiar are 29 de pagini toate cu MODERATE (verificat live), cheia Anthropic e validă (HTTP 200, 11 modele), backlogul OPEN e exact 12.499 / 6.513 pe cele 5 canale ale wrapperului, iar tabelul de costuri e aritmetic CORECT în toate cele 8 celule (l-am rederivat cu prețurile din skill-ul claude-api). Dar documentul nu rezistă intact pe patru puncte măsurabile: (1) cifra-titlu de cost e greșită cu 1,6x — fast-triage sare triajul pe 18,2% din tichete, nu pe toate (măsurat cu categorize_hint pe toate cele 6.513 tichete OPEN ale wrapperului: 71,9% cad pe altele), deci ~44 $, nu ~28 $; (2) 39.388 cicluri irosite e aritmetică fabricată — skip-tagged a funcționat (28.207 sărituri logate), iar risipa reală e 6.015 reîncercări pe 378 tichete distincte; (3) recomandarea de sincronizare git-VPS împinge o pagină Lab Noir (61586834387211) care are ZERO tichete în tot warehouse-ul de 255.767 rânduri, în timp ce pagina reală de Lab Noir (898588036681214, 243 tichete din 15-aug) lipsește din PAGE_STORE în ambele copii; (4) ratează un pericol customer-facing viu: 449 de intrări din coada de pe VPS au draft (eroare LLM...) și sunt eligibile pentru --send/--approve, care NU au garda de la linia 1213. În plus, rezultatul de business lipsește complet: 1.001 din cele 1.336 tichete cu draft (74,9%) sunt ÎNCĂ deschise azi — drafturile n-au fost folosite niciodată.

**Greșeli găsite:**

- RULARI: „25 de rulari de cron intre 24-iun 15:39 si 2-iul 11:36”. Masurat pe /root/Scripturi/data/cs_backlog.log (137.095 linii): 25 de markere RUN dar doar 24 DONE. Cronul e `0 9-21/3` (doar la fix), deci nici prima rulare (24-iun 15:39:59) nici ultima (2-iul 10:00:06) nu sunt rulari de cron — iar pe 2-iul linia era deja comentata din 29-iun. Deci 23 cron + 2 MANUALE, iar ultima n-a terminat niciodata (fara DONE, log oprit la tichetul [46/464], mtime 2-iul 11:36). Consecinta pe care documentul o rateaza: SINGURA rulare care a produs drafturi a fost una MANUALA, dintr-un shell interactiv unde `uv` era in PATH; fiecare rulare de cron a murit pe `uv`.
- CICLURI IROSITE: „cicluri_irosite: 39388” si „acelasi tichet esuat reintra la fiecare rulare”. Fals ca marime. `--skip-tagged` a FUNCTIONAT: 28.207 linii „deja ai-draft -> sarit” in log, iar rularea 2 a sarit exact 1.336 tichete (= cele draftate in rularea 1). Risipa reala, masurata: 6.015 evenimente „draft invalid” pe doar 378 tichete DISTINCTE = 15,9 reincercari/tichet (maxim 350 intr-o rulare). Dead-letter-ul ramane o problema reala, dar magnitudinea corecta e 6.015/378, nu 39.388.
- TICHETE PROCESATE: „tichete_procesate_total_in_jurnal: 40724”. Masurat: suma celor 120 de anteturi „| N tichete care asteapta raspuns” = 41.509 (24 rulari x 5 canale = 120 anteturi; rularea din 29-iun 12:00 a pierdut antetul pe email_from_widget, rularea 25 a avut doar email).
- ESCALADARI: documentul spune la §0 ca sub `--create-draft` „ruteaza escaladarile” si citeaza 328 escaladari in coada, fara sa verifice ce s-a executat. Masurat: ZERO linii „⛳ Rutat” in tot logul, desi 176 de tichete distincte au fost marcate ⛳ (394 linii cu ⛳). Cauza: wrapperul de cron din iunie rula `--lean`, iar rutarea e blocata de `if is_esc and not a.lean` (cs_auto_draft.py:1206). Deci 176 de cazuri ANPC/furie au primit un draft de ASTEPTARE fara prioritate HIGH, fara tagul `escaladare` si fara nota privata — CS n-a avut niciun semnal.
- GROUNDING: documentul trateaza `--ground` ca fiind configuratia curenta si-i judeca istoricul cross-canal. Masurat: sirul „grounded” apare de 21 de ori in tot logul de 12 MB, TOATE in rularea neterminata din 2-iul. Deci `--ground` (tot PR #327/#328/#330, inclusiv cautarea dupa AWB) a atins 21 de tichete in toata viata lui si a produs 0 drafturi salvate — toate cele 21 au picat pe 429. La fel: post-filtrul anti-halucinare n-a rulat niciodata (0 aparitii „+corectat”, 0 „sablon-sigur”), iar `--photos` a atins 2 tichete.
- COST: „cu Claude Sonnet 5 + prompt caching + --fast-triage costul e 4,33 $/1.000 tichete, adica ~28 $ pentru tot backlogul de 6.513”. Numitorul lipseste. Masurat cu `categorize_hint` din cod, rulat pe TOATE cele 6.513 tichete OPEN de pe cele 5 canale ale wrapperului (din /root/Scripturi/data/richpanel_tickets.db): 71,9% dau `altele` si 9,8% `spam_automat`, deci --fast-triage sare apelul de triaj pe doar 18,2%. Cost real amestecat = 0,818 x 7,25 + 0,182 x 4,33 = 6,72 $/1k -> ~44 $, nu 28 $. (Verificat pe doua esantioane independente: 400 tichete aleatorii din warehouse = 17,2%; tot backlogul = 18,2%.)
- MODEL: planul „trecem pe Sonnet 5 cu caching (7,25 $/1k)” nu se poate executa cu wrapperul actual. `llm()` (cs_auto_draft.py:296-321) citeste `ANTHROPIC_MODEL` pe ramura Claude, cu implicit `claude-sonnet-4-6`; `DRAFT_MODEL=gpt-4o-mini` pe care-l seteaza cs_backlog.sh nu are NICIUN efect acolo. Fara `ANTHROPIC_MODEL=claude-sonnet-5` se plateste 10,88 $/1k (tabelul lor propriu), adica +50% fata de plan.
- PRAG DE CACHE: documentul verifica pragurile pentru Sonnet 5 (1.024) si Haiku 4.5 (4.096) — corect, confirmat — dar nu si pentru modelul pe care codul il foloseste de fapt. Pragul real pentru `claude-sonnet-4-6` e 1.024, NU 2.048 cum scrie comentariul din cod (cs_auto_draft.py:300). Cu 1.910 si 3.960 de tokeni, AMBELE prompturi de sistem se cache-uiesc deja pe modelul implicit — comentariul din cod spune contrariul si trebuie corectat odata cu SKILL.md.
- LAB NOIR: „git are 61586834387211: Lab Noir in PAGE_STORE ... alarma e benigna, sincronizeaza prin deploy.sh”. Diff-ul e corect (l-am reprodus: exact 1 linie), dar recomandarea tinteste o pagina moarta: `61586834387211` are ZERO tichete in tot warehouse-ul (255.767 randuri, orice status). Traficul real de Lab Noir e pe pagina `898588036681214` — 243 tichete din 15-aug-2026, 91 OPEN azi — care LIPSESTE din PAGE_STORE si in git, si pe VPS. Sincronizarea propusa nu repara nimic.
- ACOPERIRE META/PAGE_STORE: „7 pagini cu trafic fara acces token = 18,2% din volumul social” si „9 pagini lipsa din PAGE_STORE = 8,6%” nu se reproduc si n-au numitor declarat. Masurat pe tichetele social OPEN (n=8.866 = 5.874 cmt FB + 1.411 fb_msg + 1.385 messenger + 598 widget... corect: 5.874+112+1.411+1.385+84): 14 pagini fara acces token = 2.140 tichete = 24,1%; 17 pagini fara mapare in PAGE_STORE = 1.434 tichete = 16,2%. Cele doua gauri mari: Covoria (638338549359389, 898 OPEN — nici mapata, nici pe token) si Ofertele Zilei (364899953373966, 875 OPEN — mapata, dar token-ul NU are acces). Deci „context postare ✅ MERGE ACUM pe FB” tine pe un esantion de 6 pagini, dar nu ponderat cu traficul.
- VERSIUNEA CARE A RULAT: documentul (si briefing-ul) trateaza fisierul de pe VPS ca „versiunea care A RULAT” si numara 5 backupuri. Sunt 6 backupuri, iar `cs_auto_draft.py` de pe VPS (93.425 B) are mtime 3-iul 11:27 — A DOUA ZI dupa ultima rulare logata (2-iul 10:00-11:36). Niciuna dintre cele 25 de rulari n-a executat fisierul aflat azi pe VPS; ultima versiune care chiar a rulat e pastrata ca `cs_auto_draft.py.bak-efficiency-0703` (92.350 B, 1.235 linii). Corolar direct: `--fast-triage` exista DOAR in fisierul curent, deci n-a rulat niciodata nici macar o data — iar cifra-titlu de cost se sprijina pe el.
- PLAYBOOK: corect ca `.learned_playbook.md` lipseste de pe VPS si ca e in .gitignore, dar concluzia implicita („trebuie re-rulat cs-procedures”) e gresita. Copia exista local: /Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/.learned_playbook.md (17.884 B, 23-iun-2026). Reparatia e un scp de un fisier, nu o regenerare LLM.

**Omisiuni:**

- REZULTATUL DE BUSINESS lipseste complet. Am extras din log cele 1.336 numere de conversatie care au primit „✅ DRAFT salvat” si le-am interogat in metrics.richpanel_tickets azi: 1.001 din 1.335 gasite (74,9%) sunt INCA OPEN, la 78 de zile dupa ce li s-a scris draftul. Pe cele 378 de tichete otravite: 300 (79,4%) inca OPEN. Pe canalele DM situatia e totala — din 186 de tichete DM otravite, 185 sunt inca deschise (1 singur inchis). Adica: drafturile n-au fost folosite, bucla „AI scrie draft -> agentul trimite” nu s-a inchis niciodata.
- PERICOL CUSTOMER-FACING NEDOCUMENTAT: garda anti-gunoi de la cs_auto_draft.py:1213 exista DOAR pe calea din bucla. `do_send` (liniile 804-832) si `do_approve` (764-802) iau `p["draft"]` din coada si-l trimit/salveaza fara nicio verificare de validitate. Masurat in /root/Scripturi/.auto_draft_proposals.json: 459 intrari au draft care incepe cu „(eroare”, din care 449 sunt ne-escaladate si ne-hide = eligibile pentru `--send`. Un `--send <nr>` pe oricare din cele 449 ar posta LIVE textul „(eroare LLM: [Errno 2] No such file or directory: 'uv')” la client SI ar inchide tichetul. `do_send` nici nu stie daca tichetul e un comentariu public (coada nu salveaza `channel`), spre deosebire de `--apply-send` care exclude `is_public`.
- MCP-ul e RUPT si nimeni n-a observat: mcp_server.py:33 expune unealta `cs_draft` care cheama `cs_auto_draft.py --conv <nr>`. `--conv` NU e un argument argparse in cs_auto_draft.py — apare o singura data, la linia 502, ca parametru pentru customer-identity. Rezultat: argparse iese cu cod 2 la orice apel. Unealta trebuie sa cheme `cs_draft_reply.py` (care chiar are `--conv`, linia 160).
- FISIERE DIN SKILL NEMENTIONATE DELOC: cs_ticket_index.py (136 linii — retrieval semantic peste tichete rezolvate, importat de cs_draft_reply), build_voice_pack.py (134 linii — COD MORT: produce `.voice_pack.json`, pe care nu-l citeste NIMIC; grep pe cs_auto_draft.py si cs_draft_reply.py = 0 rezultate, iar fisierul e literalmente `{}`), mcp_server.py (58 linii), DEMO.md (51 linii).
- DEPLOY INCOMPLET: din cele 7 fisiere ale skillului, doar 2 sunt pe VPS (cs_auto_draft.py + cs_photo.py). Lipsesc cs_draft_reply.py, grammar_audit.py, cs_ticket_index.py, build_voice_pack.py, mcp_server.py. Consecinta operationala: gate-ul de calitate (grammar_audit.py) NU poate rula pe iesirea de productie, si e OpenAI-only oricum (AUDIT_MODEL, implicit gpt-4o, hardcodat pe api.openai.com) — deci nu urmeaza comutarea pe Claude.
- COMUTARE TACITA DE PROVIDER: `ANTHROPIC_API_KEY` e DEJA in /root/Scripturi/.env (root-600). `cs_backlog.sh` nu-l exporta, iar `secret()` verifica env-ul intai — de asta cade pe OpenAI azi. Dar `llm()` verifica Anthropic PRIMUL: in clipa in care cineva adauga `export ANTHROPIC_API_KEY` in wrapper (exact miscarea pe care o recomanda foaia de parcurs), motorul comuta TACIT pe `claude-sonnet-4-6`, fara flag, fara log, fara avertisment — de la 1,28 $/1k la 26,72 $/1k (sau 10,88 cu cache). Bonus: `DRAFT_OPENAI_API_KEY` exista in .env si nu-l foloseste NIMIC (grep pe tot /root/Scripturi) — cheia separata care ar fi rezolvat contentia 429 din 2-iul nu e cablata.
- RUTARE DE PROVIDER SCHIZOFRENICA PE POZE: `cs_auto_draft._vision_describe` (liniile 330-350) verifica Anthropic PRIMUL; `cs_photo.vision()` (cs-photo/cs_photo.py:128-150) — modulul canonic `_csp`, cel chiar deployat pe VPS — verifica OpenAI PRIMUL. Deci cu ambele chei prezente, textul pleaca pe Claude iar pozele raman pe gpt-4o-mini. In plus `VISION_MODEL` e onorat DOAR pe ramura OpenAI: pe Anthropic pozele merg pe `ANTHROPIC_MODEL`, adica pe modelul scump de text.
- `js=True` E IGNORAT PE ANTHROPIC (llm(), liniile 296-321): triajul cere JSON (`llm(IDENTIFY_SYS, ident_user, js=True)`, linia 1039) dar pe ramura Claude nu se trimite nimic care sa-l impuna — `response_format` exista doar pe ramura OpenAI. Memoria propunea „prefill «{» cand pornim pe Claude”: AZI ASTA DA HTTP 400 — prefill-ul de mesaj assistant e eliminat pe Sonnet 4.6 si Sonnet 5. Solutia curenta corecta e `output_config: {format: ...}` (structured outputs). Fara asta, comutarea pe Claude degradeaza tacit triajul.
- `usage` E ARUNCAT: `_llm_http` (liniile 277-295) intoarce JSON-ul complet dar nimeni nu citeste `usage.output_tokens`, `usage.cache_read_input_tokens` sau `cache_creation_input_tokens`. De asta tokenii de output raman estimati pentru totdeauna SI nu exista nicio dovada ca prompt-caching-ul prinde efectiv (daca `cache_read_input_tokens` e 0, un invalidator tacit lucreaza si costul e cel „fara cache”).
- BUGETUL DE APELURI RICHPANEL OMITE LISTAREA: cu `--limit 3000 --scan 6000` din wrapper, bucla de paginare (liniile 886-903) face pana la 120 de apeluri `list_conversations` PE CANAL (6000/50), x 5 canale = pana la 600 de apeluri de listare pe rulare, independent de cate tichete se drafteaza. Modelul „3-4 apeluri/tichet” nu le include.
- `--skip-tagged` NU FUNCTIONEAZA PE COMENTARII CU UN SINGUR MESAJ: la liniile 940-947, daca `comment_count <= 1` si tichetul e public, `get_conversation` nu se apeleaza deloc, iar `cur_tags` ramane cel din lista de sumar — despre care codul insusi noteaza la linia 949 ca „lista summary nu le are”. Cronul scapa azi doar pentru ca ruleaza `--no-comments`. In ziua in care comentariile se activeaza, `create_draft` (care ADAUGA, nu suprascrie, si n-are API de stergere) va stivui un draft nou la fiecare rulare, pe fiecare comentariu.
- FLAGUL `escalate` AL LLM-ULUI E PRACTIC MORT PE EMAIL/DM: la liniile 1078-1081, `_ord_cat` include toate categoriile de comanda PLUS `altele`, iar `is_esc = _real_esc or (_llm_esc and not _ord_cat)`. Cum `altele` acopera 71,9% din tichete si restul sunt categorii de comanda, expresia se reduce la `_real_esc` — regexul determinist (ANPC/juridic, ANGER_RE, >70% MAJUSCULE) — pentru orice tichet de email/DM. Flagul LLM conteaza doar pe `spam_automat` si `comentariu_social`. Nicio documentatie nu spune asta, iar cine tuneaza IDENTIFY_SYS pentru escaladari pierde timpul.
- REZUMATUL FINAL NU NUMARA NIMIC (linia 1237): singurul contor tiparit la sfarsit e cel de spam. Nu exista contor de drafturi salvate, de drafturi invalide, de escaladari sau de tichete sarite. Exact de asta 5 zile cu output zero au aratat ✅ verde — informatia nu era agregata nicaieri, doar imprastiata linie cu linie in 137.095 de linii de log.
- SPAM: 5.146 de excluderi de spam sunt logate (suma rezumatelor per canal, ~222/rulare la inceput, ~240 la final). Capitolul despre jurnal nu le mentioneaza deloc, desi sunt ~12% din volumul procesat si sunt singurul lucru pe care sistemul l-a facut consecvent si corect in toate cele 25 de rulari.
- CORPUSUL REAL LIPSESTE DIN DOCUMENT: pe VPS exista /root/Scripturi/data/richpanel_tickets.db = 478 MB, 255.767 tichete + 255.767 randuri `customer_identity`, actualizat AZI la 12:05. Asta e sursa pentru cs-procedures (playbook) si cs_ticket_index (precedent semantic). Documentul pomeneste doar `cs_mirror.db` (12.234 tichete). In plus: `metrics.richpanel_tickets.tags` tine UUID-uri, nu nume de taguri, deci acoperirea AI NU se poate audita din warehouse fara maparea id->nume.

---

## 9. Completările scepticilor

Markdown produs de verificatori pentru ce lipsea. Păstrat integral.


### Completare 1

## 0.b Numerotarea liniilor din acest document

**Toate numerele de linie sunt din fișierul de pe VPS** (`/root/Scripturi/cs_auto_draft.py`, 1243 linii, sha `b5f474232b0ad203`). Fișierul din git are o linie în plus la 67 (pagina Facebook „Lab Noir”), deci **orice referință de după linia 67 e cu +1 în git**: `main()` = 846 VPS / **847 git**, `SYSTEM` = 419-444 / **420-445**, `HALLU` = 189-196 / **190-197**, `do_approve`+`ACTIUNE_APLICATA` = 783 / **784**, `load_playbook` = 615 / **616**, post-filtrul anti-halucinare = 1156-1172 / **1157-1173**. Liniile ≤ 67 (`KB`=37, `CI`=46, `CSA`=47, `QUEUE`=48) sunt identice în ambele.

---

## 0.c ⛔ ÎNAINTE DE ORICE: Sonia nu poate citi fișierele din harta de mai sus

Măsurat pe `84.46.242.181`, 15-sep-2026:

```
drwx------ root:root   /root
drwxr-xr-x UNKNOWN:staff /root/Scripturi
drwxrwsr-x UNKNOWN:csapp /opt/orqestra-tickets
csapp:x:1003:sonia
-rwxr-xr-x root root 819 Sep  8 14:36 /usr/local/sbin/cs-ctl
```

`/root` e `0700`. Sonia e în grupul `csapp`, nu e root. **Nu poate nici măcar lista `/root`**, darămite să citească `cs_auto_draft.py`, jurnalul sau coada. Toate cele 6 căi VPS din harta de fișiere sunt inaccesibile pentru ea.

Rețeta e deja rezolvată pe aceeași cutie, pe 8-sep-2026, **exact ca să poată prelua Sonia proiectul CS**: motorul de tichete Orqestra a fost mutat din `/root` în `/opt/orqestra-tickets`, cu grup `csapp` + setgid, bare repo `/opt/git/orqestra-tickets.git` și wrapper `cs-ctl` + `/etc/sudoers.d/cs-dev`.

> **Primul pas al preluării nu e citirea codului, e mutarea lui**: `cs_auto_draft.py`, `cs_photo.py`, `cs_backlog.sh`, jurnalul și coada trebuie scoase din `/root/Scripturi` în ceva ca `/opt/cs-autodraft` (grup `csapp`), altfel documentația asta nu se poate urma.
>
> ⚠️ `/root/Scripturi/.env` **rămâne unde e** — acolo stau 49 de chei, inclusiv `KB_DATABASE_URL` (secretul-rădăcină al seifului) și `ANTHROPIC_API_KEY`. Cine citește fișierul ăla deține toată infrastructura. Wrapperul continuă să-l sursifice ca root; codul se mută, secretele nu.
>
> ⚠️ Lecția din mutarea Orqestra: mutarea a omorât tăcut harvest-ul care rula (`attempt to write a readonly database`, cale sqlite veche). **Oprește întâi ce scrie, apoi mută.**

---

## 0.d Sistemul pe care îl predăm are un potențial înlocuitor

Richpanel nu e o constantă. Pe aceeași cutie rulează **„Motorul de tichete Orqestra”**, preview pe `https://tichete.arona.ro`, declarat explicit „posibilul înlocuitor de Richpanel”, cu cron activ orar (`12 * * * * /opt/orqestra-tickets/sync.sh`) care aspiră tichetele. `cs_auto_draft` e legat 100% de Richpanel (10 unelte MCP, `create_draft`, `send_message`). Orice investiție în el trebuie cântărită față de decizia asta.

---

## 7. Registrul rulărilor — ce a rulat de fapt (măsurat, per rulare)

Documentul dă agregate „peste tot jurnalul”. Agregatele mint, pentru că jurnalul conține **două sisteme diferite**:

* **rulările 1-24** (24-iun 15:39 → 29-iun 12:00): wrapper cu **`--lean`**, cod **pre-fix `uv`**;
* **rularea 25** (2-iul 10:00): wrapper cu **`--ground`** (cel de azi), cod post-fix.

| # | început | selectate | ✅ salvate | ⛔ blocate | lean | ground | spam | sărite | ⛳ esc | rutate | erori `uv` | linii „429” |
|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | 06-24 15:39 **(manual)** | 1560 | **1336** | 0 | 1336 | 0 | 222 | 2 | 174 | 0 | 0 | 8 |
| 2 | 06-24 21:00 | 1647 | 0 | 120 | 120 | 0 | 191 | 1336 | 0 | 0 | 431 | 8 |
| 3 | 06-25 09:00 | 1648 | 0 | 138 | 138 | 0 | 193 | 1317 | 0 | 0 | 469 | 9 |
| … | *(rulările 4-23, toate la fel: 0 salvate)* | | **0** | 129→350 | | 0 | ~190-240 | ~1100-1320 | 0-2 | 0 | 452→938 | 9 |
| 24 | 06-29 12:00 | 1702 | 0 | 290 | 291 | 0 | 240 | 804 | 2 | 0 | 822 | 5 |
| 25 | 07-02 10:00 **(manual)** | 464 | **0** | 21 | 0 | **21** | 5 | 20 | 0 | 0 | 0 | **47** |
| | **TOTAL** | 41509 | **1336** | **6015** | 7345 | 21 | 5151 | 28207 | **197** | **0** | 16942 | 254 |

**Ce înseamnă tabelul:**

1. **Cronul nu a produs niciodată niciun draft.** Toate cele 1.336 vin din rularea 1, pornită de mână pe 24-iun la 15:39. Rulările 2-25 au produs 0.
2. **Cauza nu a fost 429, a fost `uv`.** Desfăcut pe mesaje, cele 10.960 de eșecuri de triaj sunt **10.933 × `[Errno 2] No such file or directory: 'uv'`**, 26 × HTTP 429, 1 × HTTP 520. `uv` e instalat la `/root/.local/bin/uv`, care e în PATH-ul interactiv dar **nu** în PATH-ul cron (crontab n-are linie `PATH=`, deci cron are doar `/usr/bin:/bin`). Rularea manuală a mers; toate cronurile au crăpat. **Bug-ul e REPARAT** din 29-iun (try/except + cache în `secret()`, liniile 233-237).
3. **Rularea din 2-iul nu a „eșuat pe 464 de tichete”** — a atins 21 și a fost întreruptă manual: blocul ei are 269 de linii, n-are marcaj `DONE`, iar ultima linie din jurnal e în mijlocul unui tichet. 464 era doar antetul selecției. Înăuntru sunt două marcaje puse de mână: `===== RELANSAT DEDICAT $(date +%H:%M:%S) =====` (variabilă neexpandată) și `===== DRAFT MAIN-KEY 2026-07-02T10:36:41 =====` — cineva încerca chei diferite.
4. **Escaladările sunt 197, nu 394.** Fiecare tichet escaladat printează „⛳” de două ori: în `proposal_line` (1092) și în antet (1176, `⛳HIGH `=173 + `⛳URGENT `=24 = 197).

### Pâlnia se reconciliază exact

```
7.345 (lean) + 21 (ground)         = 7.366 tichete au ajuns la generare de draft
12.045 apariții „(eroare LLM” ÷ 2  = 6.030 drafturi-eroare (2 linii fiecare: corpul + mesajul gărzii)
7.366 − 6.030                      = 1.336 = exact drafturile salvate
```

Din cele 6.030 drafturi-eroare, garda a oprit 6.015; celelalte 15 n-au ajuns la scriere (fără `conversation_id`, linia 1199). **`create_draft` eșuate: 0. Gunoi scris în Richpanel: 0.** Garda a funcționat perfect. Randamentul a fost însă zero: 6.030 din 7.366 = 82% apeluri LLM ratate.

### Jurnalul nu e istoria completă

`/root/Scripturi/.auto_draft_proposals.json` are 3.156 intrări; **1.448 dintre ele nu apar deloc în `cs_backlog.log`** (verificat căutând fiecare `#<conv_no>`). Ultima scriere reală a sistemului e **3-iul 11:28:45** (mtime coadă), la un minut după ultima editare de cod (3-iul 11:27:55) — o rulare fără jurnal. Contextul: pe 3-iul-2026 toată stiva CS pe LLM a fost pusă pe pauză manual, motiv declarat „credite LLM” (cronurile pipeline-ului au stat până pe 17-aug). Asta, nu 429-ul, e sfârșitul poveștii.

---

## 8. Patru defecte VII, în codul care va rula la repornire

Niciunul nu e în memorie și niciunul nu a fost observat vreodată în producție, fiindcă rularea `--ground` a murit după 21 de tichete.

### 8.1 Brandul devine prefix brut — „Cu drag, Echipa EST”

`lookup_orders` umple `o["brand"]` cu `store_prefix(...)` (liniile **545** și **557**), iar `store_prefix` (449-457) întoarce **prefixul de comandă**, nu numele magazinului. Linia **1004** îl promovează în `store_name`:

```python
if (store_name == "magazinul nostru") and orders:
    store_name = orders[0].get("brand") or store_name     # 1004 → "EST", nu "Esteban"
```

Reprodus:

| comandă | `brand` rezultat | `STORE_LANG` | `STORE_PHONE` | semnătura generată |
|---|---|---|---|---|
| EST000001 | `EST` | None | `""` | „Cu drag, Echipa **EST**” |
| CZ80817 | `CZ` | **None** | `""` | „Cu drag, Echipa **CZ**” |
| GRAND00001 | `GRAN` | None | `""` | „Cu drag, Echipa **GRAN**” |
| BONBG00001 | `BONBG` | **None** | `""` | „Cu drag, Echipa **BONBG**” |

Trei efecte, nu unul:
* semnătură ruptă către client;
* **`STORE_LANG.get("CZ")` → None** — cade singurul semnal de limbă rămas pentru Bonhaus CZ/PL/BG (vezi 8.2);
* **`STORE_PHONE.get("EST")` → ""** — dispare `TELEFON_COMANDĂ` de pe presale/comandă nouă, exact unde procedura cere „sunați la…”.

Tabelul de corecție **există deja** — `ORDER_PFX` (linia 68) mapează `EST→Esteban` — dar e folosit doar în sens invers, la linia 454. Fix de o linie: `"brand": ORDER_PFX.get(store_prefix(r[0]) or "", store_prefix(r[0])) or "?"`.

### 8.2 `idn["language"]` nu e populat niciodată — precedența limbii are 3 trepte, nu 4

Dict-ul inițializat la **1028-1030** are 13 chei și **nu conține `language`**. Linia **1040** e:

```python
idn.update({k: got.get(k, idn.get(k)) for k in idn})   # copiază DOAR cheile deja existente
for k in ("new_address", "new_city", "new_zip", "new_phone", "items"):   # 1041-1042
    idn[k] = got.get(k, "")
```

Deci `language` — cerut explicit în `IDENTIFY_SYS` la linia 397 — se aruncă. La linia **1138**, `idn.get("language")` întoarce mereu `None`. Reprodus cu un răspuns LLM care conține `"language":"en"`, pe un client care scrie engleză pe Esteban:

```
idn.get('language') -> None
lang -> 'ro'
```

**Precedența REALĂ:** `detect_lang()` (script/diacritice) → `STORE_LANG` (doar 3 branduri, și acelea rupte de 8.1) → `"ro"`. Un client care scrie engleză, sau cehă fără `ř/ů/ě`, e etichetat român.

*Mitigare parțială:* promptul de la linia 1146 îi spune modelului „SCRIE ÎN LIMBA ÎN CARE A SCRIS CLIENTUL … (orientativ: limba≈%s)”, deci modelul poate corecta singur. Dar `lang` intră și în ieșirea `--json` (linia 1192), pe care `grammar_audit.py` o folosește ca să compare limba răspunsului cu a clientului — **deci gate-ul de gramatică auditează pe o etichetă greșită.**

### 8.3 Garda anti-gunoi lipsește din `--approve` și `--send`

Verificarea „draftul începe cu `(eroare`” e **doar** în bucla principală, linia **1212**. Nu e în:

* `do_approve`, linia **792**: `if p.get("draft") and _cmode != "hide": mcp.call("create_draft", …)`
* `do_send`, liniile **816-820**: singurele verificări sunt `sent` / `escalate` / `hide` / draft gol.

În coada de pe VPS, **459 din 3.156 intrări au un `draft` care începe cu „(eroare”**. Un `--send <conv>` pe oricare dintre ele trimite LIVE textul „(eroare LLM: HTTP Error 429…)” la client **și închide tichetul** (linia 826). Coada e din 29-iun/3-iul — **orice draft de acolo e vechi de 2 luni și jumătate**, generat de un build anterior, cu prompturi de atunci.

> **Regulă pentru Sonia:** coada `.auto_draft_proposals.json` nu e o listă de lucru. E un artefact istoric. Nu rula `--send` sau `--approve` pe intrări din ea fără să regenerezi întâi.

### 8.4 Unealta MCP `cs_draft` e ruptă

`mcp_server.py` (cel mai recent fișier din skill, 20-aug) definește `DRAFT = os.path.join(HERE,"cs_auto_draft.py")` și apelează `_run(DRAFT, ["--conv", conv])`. Dar **`cs_auto_draft.py` nu are flag `--conv`** — îl are `cs_draft_reply.py` (linia 160). Rulat:

```
$ python3 cs_auto_draft.py --conv 274155
rc=2
cs_auto_draft.py: error: unrecognized arguments: --conv 274155
```

Serverul `arona-cs-inbox` e înregistrat la user scope, deci unealta `cs_draft` e vizibilă și cade la fiecare apel. Fix: `DRAFT = os.path.join(HERE, "cs_draft_reply.py")`.

---

## 9. Ce NU e dry-run

Docstring-ul modulului spune „DRY-RUN: identifică+draft+escaladări+propuneri, **nimic scris**”, iar linia 906 printează antetul „DRY-RUN — nimic scris”. **E fals când pui `--close-spam`.**

Toate scrierile în Richpanel și garda lor:

| linie | apel | gardat de |
|--:|---|---|
| 1064-1065 | `add_tags(["spam"])` + `update_conversation_status` **CLOSED** | **doar `--close-spam` — NU `--create-draft`** |
| 1205-1207 | `update_conversation(priority HIGH)` + `add_tags` + `add_private_note` | `--create-draft` **și** `not --lean` **și** `is_esc` |
| 1218-1222 | `send_message` + `add_tags` + **CLOSED** | `--create-draft` **și** `--apply-send` |
| 1227-1230 | `create_draft` + `add_tags` | `--create-draft` |
| 793-797 | `create_draft` + `add_tags` | `--approve` |
| 820-826 | `send_message` + `add_tags` + **CLOSED** | `--send` |

În plus, `tag_id()` (579-593) **creează taguri noi** în workspace dacă nu există (`create_tag`, linia 589) — deci și rezolvarea de taguri e o scriere.

> `cs_auto_draft.py --close-spam` fără `--create-draft` **închide tichete reale** în timp ce afișează „DRY-RUN — nimic scris”.

---

## 9.b Garda de echipă nu acoperă acest script

`~/.claude/settings.json` are `permissions.deny: ["mcp__richpanel__send_message"]`. Regula aceea protejează uneltele MCP ale lui Claude Code. **`cs_auto_draft.py` nu le folosește** — are propria clasă `MCP` (240-274) care vorbește direct cu `https://mcp.richpanel.com/mcp` prin JSON-RPC și cheamă `send_message` la liniile **820** (`--send`) și **1218** (`--apply-send`). Deny-ul nu se aplică. Singura barieră reală e că nimeni nu pasează flagurile.

---

## 10. Playbook-ul învățat: ce acoperă și ce nu va acoperi niciodată

`.learned_playbook.md` lipsește de pe VPS (confirmat), deci blocul „PROCEDURA INVATATA + VOCEA AGENTILOR REALI” (linia 1136) n-a intrat niciodată într-un prompt de producție. Dar copierea lui rezolvă mai puțin decât pare.

Fișierul local (17.884 octeți) are **9 secțiuni**, exact lista `CATS` codificată în `cs_procedures.py`:

`livrare_wismo · retur · anulare · problema_produs · modificare_comanda · schimb_swap · presale_intrebare · plata_factura · refuz_livrare`

`IDENTIFY_SYS` poate întoarce însă **14 categorii**. Cele 5 fără acoperire posibilă sunt `altele`, `comentariu_social`, `recenzie_feedback`, `comanda_noua`, `spam_automat`. În coada reală de pe VPS:

| categorie | tichete | are playbook? |
|---|--:|:-:|
| altele | 1.119 | **nu** |
| comentariu_social | 819 | **nu** |
| presale_intrebare | 302 | da |
| recenzie_feedback | 234 | **nu** |
| livrare_wismo | 195 | da |
| problema_produs | 133 | da |
| retur | 114 | da |
| anulare | 105 | da |

**61% din tichete (1.938 din 3.156) nu vor primi niciodată blocul învățat**, oricât ai copia fișierul. Extinderea lui `CATS` în `cs_procedures.py` e o precondiție, nu un detaliu.

Sursa pentru regenerare e vie: `/Users/gheorghebeschea/Downloads/Scripturi/data/richpanel_tickets.db`, 473 MB, **265.195 tichete**, actualizată 17-aug-2026.

---

## 11. Arheologie: cele 6 backupuri

```
1072 linii  13d1862ff9a84a58  .bak-20260629-162212       18 flaguri
1185 linii  dd18f316f288f6c2  .bak-20260629-163827       19 flaguri  (+ --photos)
1196 linii  0394059081e5d431  .bak-20260629-164236       19
1197 linii  9c98d4c3050a8bf8  .bak-20260629-165430       19
1233 linii  45425ba65e441143  .bak-20260629-170703       19
1235 linii  e4d8eb7c5d3bc083  .bak-efficiency-0703       19
1243 linii  b5f474232b0ad203  cs_auto_draft.py (activ)   20 flaguri
```

Două lucruri de reținut:

1. **Toate cele 6 backupuri sunt deja post-fix** (au `--ground`, `HALLU`, `AWB_RE` și try/except în `secret()`). Build-ul care a generat cele 10.933 de crash-uri `uv` **nu mai există pe disc** — supraviețuiește doar în jurnal și în istoricul git.
2. **Ce a adus editarea din 3-iul** (`.bak-efficiency-0703` → activ), singura modificare de după publicarea în git a lunii iunie:
   * **`--fast-triage`** (linia 860) — sare apelul LLM de triaj când hintul regex e sigur: ~1 apel/tichet în loc de 2;
   * **prompt caching Anthropic** (liniile 298-302): `system` devine bloc cu `cache_control: {"type":"ephemeral"}`, taxat 0,1× după primul apel, TTL 5 min.

   Ambele sunt lucru pe **cost**, ceea ce confirmă motivul real al opririi. ⚠️ Capcană notată în cod: pragul minim de cache e 2048 token pe Sonnet și 4096 pe Haiku; `SYSTEM` ≈ 2,6k și `IDENTIFY` ≈ 1,3k → doar `SYSTEM` prinde cache-ul, și doar pe Sonnet.

   ⚠️ **`--fast-triage` dezactivează tăcut moderarea comentariilor.** `categorize_hint` (201-208) întoarce `comentariu_social` pentru canalele de comentarii, iar gate-ul de la 1034 sare triajul LLM pentru orice hint ≠ `altele`/`spam_automat`. Pe comentarii publice, `comment_action` rămâne `"none"` (hide nu se mai propune niciodată) și `idn["spam"]` rămâne `False`.

---

## 12. Secrete, modele, și divergența manual-vs-cron

### Toate cheile pe care le cere codul

| cheie | linie | rol |
|---|--:|---|
| `RICHPANEL_MCP_TOKEN` | 872 | obligatorie — fără ea nu pornește |
| `ANTHROPIC_API_KEY` | 296, 331 | preferată de `llm()` și de vedere |
| `OPENAI_API_KEY` | 306, 340 | fallback |
| `DATABASE_URL_METRICS` | 521 | doar pentru `--ground` |
| `META_PAGES_TOKEN`, `META_SYSTEM_TOKEN_3`, `META_SYSTEM_TOKEN`, `META_SYSTEM_TOKEN_2`, `META_SYSTEM_TOKEN_4`, `META_USER_TOKEN` | 679 | încercate în ordine de `fb_page_token` |

Pe VPS: `grep -cE '^META_' /root/Scripturi/.env` = **0**. Deci `fb_page_token` → `None` mereu → `fb_post_text` → `""` (comentariile nu văd reclama) și `fb_hide_comment` → `"(fără token Meta în KB)"`.

### Seiful e structural inaccesibil pe VPS

`KB = os.path.join(HERE, "..","..","..","core","scripts","kb.py")` (linia 37). Cu `HERE=/root/Scripturi`, asta se rezolvă la **`/core/scripts/kb.py`** — fișier care nu există (verificat). Calea presupune că scriptul stă în arborele de plugin-uri; pe VPS stă plat. **Pe VPS, singura sursă de secrete e `.env`.** Aceeași problemă la `CI`→`/root/customer-identity/...` și `CSA`→`/root/cs-actions/scripts/...`.

> Excepție care merge din noroc: `import cs_photo`. `sys.path.insert(0, HERE + "/../cs-photo")` arată spre `/root/cs-photo`, inexistent — dar Python găsește `cs_photo.py` fiindcă e sibling în directorul scriptului. **Deci pozele FUNCȚIONEAZĂ pe VPS** (`_csp` nu e `None`). Se rupe doar dacă cineva separă cele două fișiere.

### Același wrapper, două comportamente

`uv` e la `/root/.local/bin/uv` — în PATH-ul interactiv, **nu** în PATH-ul cron (crontab n-are linie `PATH=`, deci cron folosește `/usr/bin:/bin`). `llm()` cere întâi `ANTHROPIC_API_KEY`, iar `secret()` cade pe `uv run KB`. Pornit de mână vs din cron, scriptul urmează căi diferite. E cauza exactă a lui 8.-„rularea 1 a mers, toate cronurile au crăpat”.

### Modelele

| apel | variabilă | implicit | forțat de wrapper |
|---|---|---|---|
| triaj + draft, Anthropic | `ANTHROPIC_MODEL` | `claude-sonnet-4-6` (max_tokens 900, prompt caching) | — |
| triaj + draft, OpenAI | `DRAFT_MODEL` | `gpt-4o` (temp 0.2) | **`gpt-4o-mini`** |
| vedere poze | `VISION_MODEL` | `gpt-4o-mini` | — |

`ANTHROPIC_API_KEY` **există** în `/root/Scripturi/.env` (root-600, 49 chei) dar wrapperul **nu-l exportă** — exportă doar `RICHPANEL_MCP_TOKEN`, `OPENAI_API_KEY`, `DATABASE_URL_METRICS`. În `.env` mai stă și un `DRAFT_OPENAI_API_KEY` separat, pe care **nimic din `cs_auto_draft.py` nu-l citește**.

---

## 13. Vecinii: cine mai scrie în Richpanel de pe aceeași cutie

Documentul tratează `cs_auto_draft` ca singurul locatar. Nu e. Cronuri **active azi** pe `84.46.242.181`:

| program | job | ce face în Richpanel |
|---|---|---|
| `*/30 8-16` | `run_cs_pipeline.py --recent 1 --push` | **scrie** taguri `magazin-*`/`cat-*`/`sentiment-*`/`flag-*` + notă privată |
| `0 2` | `run_cs_pipeline.py --recent 3 --push` | idem |
| `12 * * * *` | `/opt/orqestra-tickets/sync.sh` | citește tot (harvest pentru înlocuitor) |
| `50 9` | `cs_queue_sync.sh` | coada CS |
| `0 2` | `run_cs_mirror.sh` | oglinda de email |

Programul propus pentru cs_backlog, `0 9-21/3`, lovește la **09:00, 12:00, 15:00** exact în fereastra pipeline-ului (`*/30 8-16`), plus minutul `:12` al sync-ului Orqestra. Lock-urile sunt **diferite** (`cs_backlog.lock` vs `cs_pipeline.lock` vs `orq_tickets_sync.lock`), deci nimic nu previne concurența. Bugetul Richpanel e **60 cereri/minut, partajat și cu CS-ul live**, iar 429 apare deja pe simpla LISTARE.

> Dacă repornești cronul, **mută-l în afara ferestrei 8-16** (ex. `0 17,20 * * *`) sau pune-l pe același `flock` cu pipeline-ul.

---

## 14. Checklist de repornire — corectat

Ordinea din documentul inițial pornește de la o premisă greșită (blocajul = rata OpenAI). Blocajul istoric era `uv`, și e deja reparat. Ordinea reală:

1. **Mută codul din `/root`** (secțiunea 0.c) — altfel Sonia nu poate lucra deloc. Oprește ce scrie înainte de mutare.
2. **Repară cele 4 defecte vii** din secțiunea 8. Fără 8.1 și 8.2, drafturile ies semnate „Echipa EST” și în limba greșită. Fără 8.3, coada veche e o mină.
3. **Decide soarta celor 3.156 de intrări din coadă** — 459 sunt drafturi-eroare, toate sunt din iunie/iulie. Cel mai curat: arhivează fișierul și pornește cu coadă goală.
4. **Tratează `.learned_playbook.md` ca pe două lucrări**, nu una: copiază fișierul **și** extinde `CATS` în `cs_procedures.py`, altfel 61% din tichete rămân descoperite (secțiunea 10). Sursa e proaspătă (265.195 tichete, 17-aug).
5. **Abia acum, o rulare de probă** — și cu garda corectă:
   ```bash
   # citește-l ca pe un test de INFRASTRUCTURĂ, nu de calitate
   cd <noua cale> && ./cs_backlog-test.sh   # --limit 5 --channel email, FĂRĂ --create-draft, FĂRĂ --close-spam
   ```
   * ⚠️ **fără `--close-spam`** — acela scrie chiar și în „dry-run” (secțiunea 9);
   * ⚠️ rularea tot **suprascrie coada** (`save_queue`, linia 1234) și tot **costă bani** (2 apeluri LLM/tichet);
   * **criteriul de trecere:** zero apariții de „(eroare LLM” în ieșire. Dacă apar 429 → exportă `ANTHROPIC_API_KEY` din `.env` în wrapper (`llm()` îl preferă automat când e în env) sau urcă tierul OpenAI. Dacă apare `'uv'` → PATH-ul cron, nu rata.
6. **Re-programează cronul în afara ferestrei 8-16** (secțiunea 13).
7. **Ai grijă ce se aprinde prima dată:** cu wrapperul de azi (`--ground`, fără `--lean`), **rutarea escaladărilor se activează pentru prima oară în producție** — linia 1201 e `if is_esc and not a.lean`, iar toate cele 197 de escaladări de până acum au fost în rulări `--lean`, deci nerutate. La repornire, `priority=HIGH` + tagurile `escaladare`/`esc-high`/`esc-urgent`/`de-sunat` + nota privată încep să se scrie în tichete reale. Comportament **nevalidat vreodată live**. Testează-l pe `--only <un tichet>` înainte de lot.
8. **Verifică ce e încă în Richpanel din iunie.** Cele 1.336 de drafturi au fost scrise de build-ul `--lean` pre-fix — exact configurația pe care auditul din 29-iun a găsit-o „71% cu probleme, 18 grave”. `create_draft` **adaugă** și **nu există API de ștergere** (doar UI). Dacă n-au fost curățate manual, sunt încă acolo, atașate tichetelor.

---

## 15. Fragilitatea tokenului Richpanel

`RICHPANEL_MCP_TOKEN` e cheia de **16 caractere** produsă de fluxul OAuth al serverului MCP: **fără expirare, fără refresh**. API-ul oficial Richpanel e **blocat pe cont** — butonul „Create Key” există în UI, dar `POST ws-prod.richpanel.com/tenant/manage-token` întoarce 403 „API access is not enabled on your account” (verificat live pe 31-aug-2026, logat ca `TENANT_ADMIN`; blocajul e pe backend, Developer API Access = doar plan Enterprise).

**Dacă cineva șterge tokenul din pagina Richpanel → API Keys, moare tot sistemul**, iar regenerarea cere re-autentificare OAuth interactivă — imposibil din cron. Punct unic de cedare, fără alertă.

---

## 16. Prospețimea surselor pentru `--ground` (măsurat azi, 15-sep-2026)

| sursă | stare |
|---|---|
| `metrics.orders` (pg8000) | 368.231 rânduri, `MAX("shopifyCreatedAt")` = **2026-09-15 10:00:52**. Toate cele 7 coloane cerute de `lookup_orders` sunt prezente (`email`, `phone`, `shippingPhone`, `name`, `totalPrice`, `financialStatus`, `shopifyCreatedAt`). |
| `/root/Scripturi/data/profitability.db` | 722 MB, scris **azi 12:10**. `profit_orders` = **843.459** comenzi, **804.057** cu AWB. Statusuri: Livrata 658.435 · Refuzata 115.518 · Anulata 37.160 · In curs 18.240 · Netrimisa 11.333 · Lipsa awb 2.739 · UNMAPPED 34. |

Ambele sunt vii — grounding-ul nu e blocat de date. Dar reține asimetria: pasul 1 (căutarea după email/telefon în `metrics.orders`, 368k) are acoperire structural mai slabă decât pasul 2 (căutarea după AWB/nume în `profit_orders`, 843k). Memoria din iunie dădea 294k/278k pentru `profit_orders` — baza a crescut de ~2,9×, deci hit-rate-ul din iunie (~5/7, ~10/13) e o subestimare pentru azi.

### Completare 2

## A. Copiile fișierului: sunt PATRU, nu trei

| Unde | Cale exactă | sha256 (16) | Octeți | Linii | mtime | Cine o rulează |
|---|---|---|---|---|---|---|
| Mac, arbore git | `/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | `57631aebe2f27af1` | 93.459 | 1.244 | 2026-07-08 14:14 | sursa de adevăr |
| **Mac, clona de plugin (marketplace)** | `/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | `57631aebe2f27af1` | 93.459 | 1.244 | 2026-09-04 14:17 | **serverul MCP `arona-cs-inbox` — singurul lucru care cheamă codul ăsta AZI** |
| VPS, clona git (auto-pull) | `/root/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | `57631aebe2f27af1` | 93.459 | 1.244 | 2026-07-30 21:31 | nimeni (doar stă) |
| VPS, copia PLATĂ | `/root/Scripturi/cs_auto_draft.py` | `b5f474232b0ad203` | 93.425 | 1.243 | 2026-07-03 11:27:55 | cronul pauzat + `cs_draft_all.sh` |

Copia de marketplace e înregistrată user-scope în `~/.claude.json`:
`"arona-cs-inbox": {"command":"uv","args":["run",".../cs-draft-reply/mcp_server.py"]}`.
Nici ea **nu** are `.learned_playbook.md` (e în `.gitignore`), deci și calea „vie" rulează fără proceduri învățate.

---

## B. ⚠️ Serverul MCP `arona-cs-inbox` e RUPT (verificat azi)

`mcp_server.py` (3.347 o., 20-aug-2026) expune 5 unelte: `cs_draft`, `richpanel_triage`, `richpanel_janitor`, `cs_sentiment`, `cs_sla`.

`cs_draft` nu poate funcționa. Linia 22 leagă `DRAFT = cs_auto_draft.py`, iar linia 34 îl cheamă cu `--conv`:

```python
DRAFT = os.path.join(HERE, "cs_auto_draft.py")          # mcp_server.py:22
return _run(DRAFT, ["--conv", conv] + (["--create-draft"] if create_draft else []))   # :34
```

Dar `cs_auto_draft.py` **nu are `--conv`**. Reprodus:

```
$ python3 cs_auto_draft.py --conv 274123
cs_auto_draft.py: error: unrecognized arguments: --conv 274123      # rc=2
```

`--conv` există doar în `cs_draft_reply.py:160`. `_run()` nu prinde excepția și nici nu verifică `returncode` decât ca să lipească stderr, deci unealta întoarce tăcut `(fără output) [stderr] …`.

**Două fix-uri posibile, alege unul:**
- `DRAFT = os.path.join(HERE, "cs_draft_reply.py")` — dacă intenția era o singură conversație (asta sugerează și textul uneltei);
- `["--only", conv, "--create-draft"]` — dacă intenția era motorul de backlog țintit pe un tichet.

---

## C. `--apply-send`: trimiterea LIVE în MASĂ (nedocumentată nicăieri)

SKILL.md descrie doar `--send <conv>` (un tichet, explicit). În cod există și un al doilea drum, **pe lot**:

```python
ap.add_argument("--apply-send", ...)                                    # :863
...
elif a.apply_send and not is_esc and not is_public:                     # :1216
    res = mcp.call("send_message", {"conversation_id": cid, "body": draft.strip()})   # :1219
    if ok:
        add_tags(mcp, cid, [t for t in (AI_TAG, "ai-sent") if t])
        mcp.call("update_conversation_status", {"conversation_id": cid, "status": "CLOSED"})   # :1222
        print("  📤 TRIMIS LIVE la client + tichet ÎNCHIS.")
```

Pentru FIECARE tichet ne-escaladat și ne-public dintr-o rulare: trimite răspunsul la client și închide tichetul. Ireversibil (Richpanel n-are API de retragere).

Ce-l protejează: e în interiorul `if a.create_draft and cid:`, sare escaladările (`not is_esc`) și comentariile publice (`not is_public`), și garda anti-gunoi de la 1214 se aplică ÎNAINTE.

**Nu s-a folosit niciodată.** Măsurat pe ambele jurnale de pe VPS: 0 apariții de `TRIMIS LIVE`, 0 de `ai-sent`. Niciun client n-a primit vreodată un mesaj trimis de sistemul ăsta.

> 🔴 Regula de predare: `--apply-send` rămâne interzis până când există o poartă de review uman pe eșantion. Dacă cineva îl adaugă într-un wrapper, sistemul încetează să fie „draft-only" fără ca nimic din SKILL.md să se schimbe.

---

## D. Ce face de fapt `--fast-triage` (și ce sacrifică)

```python
if a.fast_triage and _hint and _hint not in ("altele", "spam_automat"):
    pass   # cs_auto_draft.py:1035 — triaj LLM SĂRIT
```

Când sare, `idn` rămâne dicționarul-implicit de la linia 1029:
`spam=False`, `escalate=False`, `problem=""`, `product=""`, `action="none"`, `comment_action="none"`, `confidence=0.0`.

Consecințe, în ordinea gravității:

1. **Garda LLM de spam e ocolită.** Condiția de la 1054 începe cu `bool(idn.get("spam"))`, care e mereu `False`. Rămân doar cele 5 porți deterministe: `NON_CUSTOMER_SENDER_RE`, `JUDGEME_NOTIF_RE`, `SAAS_NOISE_SUBJ_RE`, `BOUNCE_RE`, `_padded_noise`. Ordin de mărime: cu triaj LLM activ, 293 de tichete distincte au fost excluse ca spam în `cs_backlog.log`.
2. **Promptul de draft rămâne orb** — primește `PROBLEMA IDENTIFICATĂ: (neclar)` și `PRODUS: —` (linia 1148).
3. **Nicio acțiune nu mai e propusă** — `idn["action"]` e „none", deci blocul `cat in ACTION_CATS` (1080-1110) nu produce nimic.
4. Escaladarea rămâne pe `_real_escalation()` (regex ANPC/furie/MAJUSCULE) — asta e chiar dorit, e garda anti-supra-escaladare.

Asta e configurația din `cs_draft_all.sh` de azi. **A fost exercitată pe 8 tichete și 4 drafturi** (rularea din 3-iul), deci e practic netestată.

---

## E. Prompt caching-ul e inert în configurația reală

Codul își spune singur problema, la linia 300:

> `NB prag minim de cache: Haiku 4.5 = 4096 tok, Sonnet 4.6 = 2048 tok. SYSTEM~2.6k / IDENTIFY~1.3k → se cache-uiește pe SONNET (SYSTEM), NU pe Haiku (sub prag).`

Iar `cs_draft_all.sh:8` setează exact `ANTHROPIC_MODEL=claude-haiku-4-5`. Deci pe configurația din 3-iul, `cache_control` nu prinde niciodată. Economia reală vine din `--fast-triage` (un apel LLM/tichet în loc de două), nu din caching. Dacă vrei caching real: rulează pe Sonnet, sau umflă SYSTEM peste 4.096 token.

---

## F. Ce a produs de fapt fiecare rulare (tichete DISTINCTE, nu linii de log)

Jurnalele sunt rescanări repetate ale aceluiași backlog, deci numărul de linii ≠ numărul de tichete. Parsate pe antetul `[n/N] #<conv>`:

| | `cs_backlog.log` (24-iun → 2-iul, 25 rulări) | `cs_draft_all.log` (2-3 iul, manual) |
|---|---:|---:|
| tichete distincte văzute | 1.976 | 2.647 |
| **drafturi scrise** | **1.336** | **1.306** |
| excluse ca spam (distincte) | 293 | 84 |
| escaladări **scrise** (`⛳ Rutat:`) | **0** | **154** |
| drafturi respinse de gardă (distincte) | 378 | 321 |

- Uniunea drafturilor = **2.642**, intersecția = **0** → `--skip-tagged` a funcționat, nicio dublură între cele două wrappere.
- Numerele mari din jurnal sunt **evenimente peste rescanări**, nu tichete: 5.151 linii de spam ↔ 366 tichete distincte; 6.015 linii „draft invalid" ↔ 699 tichete distincte; 10.960 linii „triaj LLM eșuat" (din care 10.933 din cauza `uv`, 26 din 429, 1 din 520).

**Defalcarea celor 1.306 din iulie** (două rulări diferite în același jurnal):

| Antet | Model | Canale | Drafturi |
|---|---|---|---|
| `DRAFT ALL-OPEN` 2026-07-02T11:36:58 | claude-sonnet-4-6 | 7, **inclusiv comentarii** | **1.302** (85 email + **1.217 facebook_feed_comment**) |
| `DRAFT REAL-SUPPORT (Haiku + fast-triage)` 2026-07-03T11:41:28 | claude-haiku-4-5 | 5, fără comentarii | **4** |

Cele 1.217 drafturi pe comentarii publice **nu vin din `cs_draft_all.sh` de azi** (care are `--no-comments`), ci din `cs_draft_all.sh.bak-0703` (827 o.): buclă pe 7 canale incluzând `facebook_feed_comment`/`instagram_comment`, fără `--no-comments`, fără `ANTHROPIC_MODEL` (deci Sonnet implicit). Fișierul curent e REPARAȚIA.

---

## G. 🔴 Cele 174 de „escaladări" din 24-iun n-au fost scrise niciodată

Rularea din 24-iun a mers pe `--lean` — dovada e în jurnal, `(lean — fără context 360)`, string emis exclusiv la `cs_auto_draft.py:1008`. Iar rutarea e închisă explicit:

```python
if is_esc and not a.lean:                                    # :1202
    mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
    add_tags(mcp, cid, tags)                                 # escaladare / esc-high / de-sunat
    mcp.call("add_private_note", {"conversation_id": cid, "body": note})
    print("  ⛳ Rutat: HIGH + %s + notă-brief." % ...)        # :1209
```

Măsurat: `⛳ Rutat:` = **0** în `cs_backlog.log`, **154** în `cs_draft_all.log`. Cele 197 de linii `⛳ ESCALADARE …` din primul jurnal sunt DECIZII, nu scrieri.

**Consecința operațională, care contează mai mult decât cifra:** pe acele tichete `is_esc` a comutat totuși promptul pe `HOLDING` (linia 447), deci clientul a primit un draft care spune *„Am preluat sesizarea și am escaladat-o către colegii noștri"* — fără prioritate HIGH, fără tag `escaladare`, fără notă-brief în Richpanel. Draftul promite o escaladare care nu există în unealtă.

> Regulă: `--lean` și escaladarea se exclud. Dacă rulezi lean pe o coadă care poate conține ANPC/juridic, ori treci pe `--ground`, ori scoți promptul HOLDING din calea lean.

---

## H. Starea din Richpanel a celor 2.642 de tichete: 3-iul, NU „azi"

Din `/root/Scripturi/data/richpanel_tickets.db` (oglinda, 478 MB, 255.767 tichete):

| | |
|---|---:|
| regăsite în oglindă | 2.641 / 2.642 |
| **OPEN** | **2.289** |
| **CLOSED** | **352** |
| poartă tagul `ea60dd56-5fd9-4952-85ae-c3ea5f3b33ce` | 1.397 |

⚠️ **Aceste cifre sunt înghețate la 3-iul-2026, nu sunt de azi.** `max(updated_at)` peste cele 2.642 = `2026-07-03T08:44:16Z`; distribuția pe luni e 2026-05: 99, 2026-06: 1.781, 2026-07: 761 — **niciun rând mai nou**. Oglinda e hrănită de `run_cs_pipeline.py --recent 1` (crontab 24, la 30 min, 8-16) și `--recent 3` (crontab 26, 02:00), care trag doar ultimele zile; tichetele din iulie nu mai sunt revizitate niciodată. Starea de AZI nu se poate afla din oglindă — cere un `list_conversations` live, cu rație.

Despre UUID-ul `ea60dd56…`: în TOT istoricul oglinzii apare pe 1.418 tichete, **toate create în 2026-06 (998) și 2026-07 (420)**, niciunul după. 1.397 din 1.418 (98,5%) cad pe tichetele pe care jurnalele spun că am scris draft. Coincide cu `ai-draft`, dar rămâne o deducție — se confirmă definitiv cu un singur `list_tags` live.

---

## I. `.learned_playbook.md`: există, dar prin construcție nu ajunge niciodată pe VPS

`load_playbook()` (linia 616) deschide `HERE/.learned_playbook.md` și la orice excepție întoarce `{}` **tăcut**. Pe VPS fișierul nu există (`ls: cannot access`). Deci toate cele 2.642 de drafturi s-au generat fără proceduri și fără vocea agenților.

**Cauza nu e că nu l-a generat nimeni.** Fișierul există local — `.../cs-draft-reply/.learned_playbook.md`, 17.884 octeți, 23-iun — și se parsează curat în 9 categorii:

| categorie | caractere | | categorie | caractere |
|---|---:|---|---|---:|
| retur | 2.117 | | modificare_comanda | 1.740 |
| presale_intrebare | 2.064 | | livrare_wismo | 1.766 |
| plata_factura | 2.040 | | problema_produs | 1.746 |
| schimb_swap | 1.928 | | refuz_livrare | 1.719 |
| anulare | 1.538 | | | |

Cauza e `.gitignore`-ul din chiar acel folder:

```
# .learned_playbook.md = generat de gigi:cs-procedures
.auto_draft_proposals.json
.voice_pack.json
.learned_playbook.md
```

E exclus **deliberat** (PII + regenerabil), deci niciun deploy prin git nu-l va duce vreodată nici pe VPS, nici în clona de marketplace pe care o rulează MCP-ul.

**Două limite pe care le are chiar și când e prezent:**
- nu acoperă `comentariu_social`, `recenzie_feedback`, `comanda_noua`, `altele` — adică exact categoria pe care s-au scris 1.217 drafturi;
- e tăiat la `learned[:1800]` (linia 1137), ceea ce trunchiază retur, presale_intrebare, plata_factura și schimb_swap.

**Regenerarea SE POATE face direct pe VPS** (contrar a ce s-a scris): `cs_procedures.py` e acolo, în clona git, și intrarea lui e proaspătă.

```bash
# pe VPS — cs_procedures.py:23 citește RICHPANEL_DB din env
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/cs-procedures
RICHPANEL_DB=/root/Scripturi/data/richpanel_tickets.db \
PROC_MODEL=gpt-4o \
  uv run cs_procedures.py --category all --out /root/Scripturi/.learned_playbook.md
```

| verificat azi | |
|---|---|
| `/root/Scripturi/team-intelligence/plugins/gigi/skills/cs-procedures/cs_procedures.py` | 7.757 o. |
| `/root/Scripturi/data/richpanel_tickets.db` | 478 MB, 255.767 tichete, `max(updated_at)=2026-09-15T09:59:58Z` |

Și fă lipsa zgomotoasă — în `load_playbook`, un `print(..., file=sys.stderr)` pe `except`, ca să nu mai treacă neobservată 74 de zile.

---

## J. Defecte de cod găsite la re-citire (toate verificate, niciunul reparat)

**1. Telefonul se pierde tăcut** — `cs_auto_draft.py:932-933`

```python
raw_phone = str(cust.get("phone") or "")
phone = raw_phone if (raw_phone.isdigit() and 9 <= len(raw_phone) <= 13) else ""
```

Orice număr stocat ca `+40712345678`, `0712 345 678` sau `0040…` pică pe `.isdigit()` → devine `""`. Efecte: tagul `de-sunat` nu se mai pune (1203), iar `lookup_orders` nu mai caută după telefon. Ironia: `norm_phone()` (linia 508) exact asta face — curăță și ia ultimele 9 cifre — dar filtrul e **înaintea** ei. Fix: `phone = norm_phone(raw_phone)`.

**2. Porțile de spam citesc PRIMUL mesaj, nu ultimul** — `1049-1054`. `_saas`, `_bounce`, `_padded` se uită în `subj` și `first`, deși restul fluxului are deja `last_cust`. Rateu real din producție:

```
[4/457] #279169 · Grandia · email · 🚫 SPAM/automat → EXCLUS (fără draft)
  motiv: Clientul a primit drona defectă (o elice nu funcționează) și întreabă ce trebuie
         să facă; ultimul mesaj este o notificare automată de la Yahoo Mail (promo/spam).
```

Un client cu produs defect, aruncat ca spam.

**3. Corpul erorii HTTP e distrus înainte să fie citit** — `_llm_http:281`. `urllib.error.HTTPError` se propagă, iar `str(e)` e doar `"HTTP Error 400: Bad Request"`. De-asta cele 321 de eșecuri din 2-iul nu se mai pot diagnostica: **dovada n-a lipsit din date, a fost aruncată de cod.** Fix minim:

```python
except urllib.error.HTTPError as e:
    detail = ""
    try: detail = e.read().decode()[:400]
    except Exception: pass
    ...
    raise RuntimeError("HTTP %s: %s" % (e.code, detail)) from e
```

Tiparul celor 321 — rafală continuă de la `[324/457]` care nu se mai reface, fără corelație de conținut — seamănă cu o condiție de CONT, nu de tichet. Anthropic întoarce **400**, nu 402, pentru sold insuficient. Rămâne ipoteză până citim corpul; instrumentarea de mai sus o rezolvă la prima repetare.

**4. `_llm_http` nu reîncearcă pe 400 și 529.** Lista e `(429, 500, 502, 503, 504)` la linia 283 (plus, corect, `URLError`/`TimeoutError`/`ConnectionError` la 288-291). Un 400 omoară tichetul instant. 529 = „overloaded" la Anthropic și e clar tranzitoriu → adaugă-l. Pentru 400, adaugă o singură reîncercare DOAR după ce corpul e citit și se poate distinge „cerere invalidă" de „cont".

---

## K. Ce NU e acoperit de garda de paritate

`deploy_parity.py` scanează exact trei directoare, în constanta `SCAN_DIRS`, **linia 30**:

```python
SCAN_DIRS = [
    "plugins/gigi/skills/metrics-cache/scripts",
    "shared/scripturi-tools",
    "plugins/core/scripts",
]
```

`cs-draft-reply` nu e acolo — de-asta driftul de 74 de zile între copia plată și git n-a fost semnalat niciodată. Fix: adaugă `"plugins/gigi/skills/cs-draft-reply"` și `"plugins/gigi/skills/cs-photo"` (gemenii plați `cs_auto_draft.py`, `cs_photo.py`, `grammar_audit.py` există deja în `/root/Scripturi/`).

---

## L. Ce mai trăiește în jurul drafterului oprit (și e sănătos)

Drafterul e pauzat, dar zona CS **nu** e moartă. Ce rulează azi pe VPS și ce moștenești odată cu el:

| Cron | Ce face | Ieșire |
|---|---|---|
| `*/30 8-16` (linia 24) + `0 2` (linia 26) | `run_cs_pipeline.py --recent 1/3 --push`; face ȘI `git pull --ff-only` pe clona VPS | `richpanel_tickets.db` — 478 MB, 255.767 tichete, proaspăt azi |
| `0 2` (linia 201) | `run_cs_mirror.sh` → `rp_sync.py --recent 8 --max-rpm 45`, `gmail_sync.py`, `parity_check.py` | `cs_mirror.db` — 47 MB (rp_ticket 12.234, rp_message 24.656, rp_attachment 1.822, gm_message 7.554) |
| `50 9` (linia 109) | `cs_queue_sync.sh` → `cs_queue_sync.py --days 14` (xconnector) | `cs_queue.db` |
| `0 9-21/3` (**linia 45, COMENTATĂ din 29-iun**) | `cs_backlog.sh` — drafterul | `cs_backlog.log` |

Auto-pull-ul e confirmat viu: `cs_intraday.log` conține `git pull: Already up to date.` la fiecare rulare de azi.

Două lucruri de reținut din `run_cs_mirror.sh`, ambele măsurate și scrise în comentariile lui:
- oglinda merge **zilnic, nu săptămânal**: la 3 zile, 57,1% din comentariile FB sunt deja „This message was deleted"; în aceeași zi, 14,4%;
- fereastra e **8 zile, nu 3**: filtrează pe data CREĂRII, dar răspunsurile agenților vin peste zile (măsurat 2-sep: 28 de răspunsuri pierdute cu fereastra mică).

`richpanel_tickets.db` e și intrarea pentru `cs_procedures.py` (secțiunea I) și pentru `cs_ticket_index.py`.

---

## M. Restul setului de fișiere (nedocumentat până acum)

**În git / marketplace:**

| Fișier | Octeți | Ce e |
|---|---:|---|
| `cs_draft_reply.py` | 13.079 | o singură conversație: `--conv <nr>`, `--create-draft`, `--similar N` (precedent din tichete rezolvate), `--similar-api` |
| `cs_ticket_index.py` | 5.420 | index SEMANTIC peste SUBIECTELE tichetelor CLOSED (fastembed local sau `--api`), prin `gigi:semantic-search`; importat de `cs_draft_reply.py` ca `retrieve` |
| `grammar_audit.py` | 5.833 | poarta de limbă+gramatică (vezi mai jos) |
| `mcp_server.py` | 3.347 | serverul MCP `arona-cs-inbox` (vezi secțiunea B) |
| `build_voice_pack.py` | 6.300 | generează `.voice_pack.json` — **care are 2 octeți, adică `{}`: nu s-a construit niciodată** |
| `DEMO.md` | 3.456 | demo |

**Pe VPS, plat, nedocumentat:**

| Fișier | Octeți | mtime |
|---|---:|---|
| `cs_photo.py` | 30.859 | 29-iun 16:07 |
| `cs_photo.py.bak-20260629-{163827,164236,165430,170703}` | 27.466 fiecare | 29-iun |
| `cs_detail.py` | 2.188 | 25-iul |
| `.auto_draft_proposals.json` | 7.286.865 | 3-iul — **conține PII** (drafturi + context de comandă) |

Deci ziua de 29-iun a lăsat **10** backup-uri, nu 6: 6 pe `cs_auto_draft.py` + 4 pe `cs_photo.py`.

**De ce `import cs_photo` merge pe VPS, deși pare că n-ar trebui:** codul face `sys.path.insert(0, HERE/"../cs-photo")`, ceea ce pe copia plată dă `/root/cs-photo` — inexistent. Merge totuși fiindcă Python pune directorul scriptului (`/root/Scripturi`) în `sys.path`, iar `cs_photo.py` e chiar acolo. E o potrivire fericită, nu un design: dacă cineva mută copia plată în alt folder fără `cs_photo.py`, `_csp` devine `None` și se cade tăcut pe `describe_photos()`.

---

## N. Poarta de limbă și gramatică — `grammar_audit.py`

Se rulează DUPĂ orice schimbare de prompt, pe drafturi reale, fără să scrie nimic în Richpanel:

```bash
uv run cs_auto_draft.py --limit 20 --json 2>/dev/null | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/cs_drafts.json
uv run grammar_audit.py --file /tmp/cs_drafts.json
```

- `cs_auto_draft.py --json` emite `@@JSON@@{…}` pe ULTIMA linie (linia 1238), cu `draft`, `private_msg`, `lang`, `mesaj_client`.
- Corectorul e MULTILINGV (ro/cz/pl/bg/en) și compară limba RĂSPUNSULUI cu `mesaj_client` real → tipul de eroare `limba_gresita`.
- Model: `AUDIT_MODEL`, default `gpt-4o`, `temperature=0`, `response_format=json_object`.
- **Capcană cunoscută:** a dat 4 fals-pozitive citind cuvinte din `mesaj_client` ca și cum ar fi fost din `draft`. Când vezi o „greșeală" care nu apare în răspuns, e ea.

---

## O. Lanțul anti-halucinare (inima calității draftului)

Trei straturi, în ordine:

1. **Prompt** — regula ⛔ANTI-HALUCINARE din `SYSTEM` (linia 445): doar datele din context, fără AWB/prețuri/numere inventate.
2. **Post-filtru determinist** — regexul `HALLU` (liniile 190-197) prinde: „am verificat / am căutat / nu am găsit", afirmații de status pe o comandă, „livrare în N zile", dimensiuni `NNxNN` sau `NN cm`, și **orice preț concret** (`N lei/ron`). La detecție (linia 1158) regenerează **o dată** cu un corectiv explicit.
3. **Șablon sigur** — dacă tot fabrică (1168-1174), înlocuiește complet cu unul din două texte hard-codate: unul pentru `presale_intrebare`/`comanda_noua`, unul pentru rest („îmi puteți spune numărul comenzii sau un număr de telefon?"). `engine` devine `"șablon-sigur"`.

**Se dezactivează** — atenție, asta e partea pe care o ratează toată lumea — când oricare e adevărat: `has_order_data(od_ctx)` (avem comenzi reale), `photo_blk` (am văzut poza clientului), `_ad_has_catalog` (reclama a adus preț/stoc REAL din catalog). Logica: acolo cifrele NU sunt inventate, sunt din sursă.

Sub `--ground` fără potrivire, `od` devine `"(nicio comandă găsită)"`, iar `has_order_data` întoarce `False` → filtrul rămâne **activ**. Corect.

---

## P. Checklist de repornire (ce e deja reparat și ce nu)

**Bugul `uv` care a omorât 23 de rulări de cron E DEJA REPARAT** în copia deployată — nu-l mai căuta. `secret()` (liniile 226-239) învelește subprocesul în try/except cu cache:

```python
try:
    v = subprocess.run(["uv", "run", KB, "secret-get", k], ...).stdout.strip()
except Exception:
    v = ""   # uv negăsit / KB inaccesibil → gol, NU excepție
```

A intrat pe 29-iun (PR #327) și e prezent atât în git HEAD, cât și în copia plată de pe VPS (`fe410875`, 3-iul). Repornirea cronului **nu** va repeta acel eșec.

Ce rămâne de știut înainte de repornire:

- `cs_backlog.sh` **nu** exportă `ANTHROPIC_API_KEY` (doar `cs_draft_all.sh` o face) → calea de cron rulează pe `gpt-4o-mini` prin `OPENAI_API_KEY`. Dacă vrei Claude pe cron, adaugă exportul; dacă nu, e o alegere validă (TPM mai mare, mai ieftin).
- Adu întâi copia plată la nivelul git (diferența e linia 67, `PAGE_STORE["61586834387211"] = "Lab Noir"`) — vezi mai jos nuanța de impact.
- Pune `.learned_playbook.md` (secțiunea I) ÎNAINTE de prima rulare, altfel repeți exact greșeala celor 2.642 de drafturi.
- Cronul e la ora VPS, **Europe/Berlin (+0200)**: `0 9-21/3` = 09/12/15/18/21 la Berlin = **10/13/16/19/22 ora României**.

**Nuanță pe Lab Noir:** `store_name` are trei trepte (`991-1013`) — `PAGE_STORE` → `brand_from_email` → `orders[0]["brand"]`. Iar `EMAIL_BRAND` (linia 125) conține deja `"labnoir.ro": "Lab Noir"`. Deci pe EMAIL brandul iese corect și fără linia 67. Gaura reală e îngustă: tichete Lab Noir venite pe pagina FB/IG (`to.id = 61586834387211`) care în plus nu găsesc nicio comandă la `--ground`. Fix-ul rămâne bun și e de 30 de secunde — doar nu e catastrofa pe care ai crede-o din descriere.

```bash
cp /root/Scripturi/cs_auto_draft.py /root/Scripturi/cs_auto_draft.py.bak-$(date +%Y%m%d-%H%M%S) \
 && cp /root/Scripturi/team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py \
       /root/Scripturi/cs_auto_draft.py
```


### Completare 3

## ⚠️ CORECȚIE MAJORĂ — au fost DOUĂ campanii de producție, nu una

Tot raportul de mai sus se sprijină pe `cs_backlog.log`. Pe VPS mai există un al doilea jurnal de producție, cu drafturi scrise real în Richpanel:

```bash
grep -rl "DRAFT salvat" /root/Scripturi/data/
# /root/Scripturi/data/cs_backlog.log      → 1336
# /root/Scripturi/data/cs_draft_all.log    → 1306   ← LIPSEA DIN ANALIZĂ
```

`/root/Scripturi/data/cs_draft_all.log` — 1.994.232 b, 18.482 linii, 3-iul 11:42. Conține două segmente:

| Segment | Antet din log | Config | Tichete cu linie | **Drafturi salvate** | Escaladări **rutate** | Motor |
|---|---|---|---:|---:|---:|---|
| **A** | `===== DRAFT CLAUDE / DRAFT ALL-OPEN 2026-07-02T11:36:58 =====` | Claude + `--ground`, **fără** `--no-comments` | 1.659 | **1.306** (1.302 atribuite pe tichet) | **154** (145 HIGH + 9 URGENT) | claude 1.288 · claude+corectat 5 · șablon-sigur 9 |
| **B** | `===== DRAFT REAL-SUPPORT (Haiku + fast-triage, FARA comentarii) 2026-07-03T11:41:28 =====` | `cs_draft_all.sh` | 4 | 4 | 0 | claude 4 |

**Totaluri reale, corectate:**

| Mărime | În raportul anterior | **Real (măsurat azi)** |
|---|---:|---:|
| Drafturi scrise în Richpanel | 1.336 | **2.642** |
| Tichete distincte cu draft | 1.336 | **2.638** (suprapunere între cele două populații = **0**) |
| Escaladări rutate efectiv (HIGH + tag + notă privată) | 0 | **154** |
| Ultima scriere în Richpanel | 2026-07-02 10:00 | **2026-07-03 11:41** |

> Consecință operațională: populația de curățat înainte de orice repornire nu e „~1.162 drafturi", ci **2.642 drafturi + 154 tichete cu prioritate HIGH, tag-uri de escaladare și notă privată** scrise de mașină. Niciunele nu se pot șterge prin API.

---

## Recensământ comparativ — cohorta lean (24-iun) vs cohorta grounded (2-iul)

Același regex `HALLU`, importat din același `/root/Scripturi/cs_auto_draft.py`, aplicat pe ambele populații:

| Măsură | 24-iun (`--lean`, gpt-4o-mini) | **2-iul (`--ground`, Claude)** |
|---|---:|---:|
| Drafturi salvate | 1.336 | 1.306 |
| din care de așteptare (escaladare) | 174 | 143 |
| Drafturi normale (numitorul) | 1.162 | 1.159 |
| **HALLU prinde** | **360 = 31,0%** | **30 = 2,6%** |
| — lookup fabricat | 341 (29,3%) | 14 (1,2%) |
| — preț inventat | 18 (1,5%) | 13 (1,1%) |
| — dimensiune inventată | 2 (0,2%) | 7 (0,6%) |
| — status inventat | 1 (0,1%) | 1 (0,1%) |
| Brand necunoscut („magazinul nostru") | 491 = **36,8%** | 5 = **0,4%** |
| Categoria `altele` | 745 = **55,8%** | 6 = **0,5%** |
| Post-filtrul a intervenit în producție | 0 | **14** (5 rescrieri + 9 șabloane sigure) |
| Canale | Email 612 · Messenger 449 · facebook_message 233 · email_from_widget 42 | **Comentariu public FB 1.217** · Email 85 |

**Citirea corectă:** cele 31% nu descriu „sistemul", ci **cea mai proastă configurație pe care a avut-o vreodată** (fără lookup, model ieftin, fără rutare). Ultima configurație rulată în producție măsoară **2,6%** — de 12× mai bine — și e cea pe care o va moșteni orice repornire… *dacă* e repornită corect (vezi secțiunea următoare).

Exemple, ca să se vadă diferența de natură:

- **24-iun (lean)** — #272181, Esteban/anulare: *„…din păcate, **nu am găsit nicio comandă** asociată cu adresa dumneavoastră de email în sistemul nostru."* → afirmație fabricată: nu se făcuse niciun lookup.
- **2-iul (ground)** — #275253, Nubra/recenzie_feedback: *„…am plasat comanda cu numărul **NUBRA00001**…"* → tiparul rămas e altul: confirmarea unei acțiuni, nu inventarea unui lookup.

---

## Capcana de repornire #1 — repornirea cronului readuce motorul PROST

`/root/Scripturi/cs_backlog.sh` (wrapperul pauzat) exportă:

```bash
RICHPANEL_MCP_TOKEN, OPENAI_API_KEY, DATABASE_URL_METRICS, DRAFT_MODEL=gpt-4o-mini
```

**NU** exportă `ANTHROPIC_API_KEY` — deși cheia EXISTĂ în `/root/Scripturi/.env` (verificat: cheia e prezentă; valoarea nu se citește). `llm()` (L295-314 VPS) încearcă întâi `secret("ANTHROPIC_API_KEY")`; `secret()` (L226-238) citește env-ul, apoi `uv run kb.py`, care pe cron eșuează tăcut → string gol → **cade pe OpenAI, `gpt-4o-mini`**.

`/root/Scripturi/cs_draft_all.sh` exportă `ANTHROPIC_API_KEY` + `ANTHROPIC_MODEL=claude-haiku-4-5` → Claude.

> **Regulă pentru Sonia:** dacă reporniți `cs_backlog.sh` ca atare, sistemul se întoarce la configurația cu 31% halucinare. Orice repornire trebuie să pornească de la `cs_draft_all.sh` (Claude), nu de la wrapperul pauzat.

## Capcana de repornire #2 — rutarea escaladărilor se aprinde singură

L1201 (VPS):

```python
if is_esc and not a.lean:
    mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
    add_tags(mcp, cid, tags)                 # escaladare, esc-high|esc-urgent, de-sunat
    mcp.call("add_private_note", {...})      # notă-brief
```

`--lean` dezactivează TĂCUT rutarea — de aceea campania din 24-iun a rutat 0. Ambele wrappere de azi (`cs_backlog.sh`, `cs_draft_all.sh`) folosesc `--ground`, **nu** `--lean` → rutarea **va scrie** prioritate, tag-uri și note private. Asta e o suprafață de scriere în plus față de „doar drafturi", și e ireversibilă prin API.

Nuanță pe escaladări, pierdută în agregare: cele 173 HIGH sunt toate din 24-iun (salvate, dar nerutate); din cele 24 URGENT, **doar 1** a ajuns vreodată draft — restul de 23 apar exclusiv în rulările de cron care au eșuat integral. Adică exact cazurile ANPC / juridic / amenințare n-au primit nimic: nici draft, nici prioritate, nici notă.

---

## Grounding-ul (`--ground`) e orb pe două branduri întregi și pe Magdeal — dovedit azi

`lookup_orders` (L512-575) lucrează în doi pași: (1) `metrics.orders` după email / telefon / nr-comandă; (2) `profit_orders` din `profitability.db` — **dar numai dacă pasul 1 a găsit ceva** (`if byname:`) sau dacă mesajul conține un AWB.

Test rulat azi pe VPS (doar SELECT-uri):

```
EST — comanda din metrics            onames=['EST000001'] -> 1 comandă  ('EST000001','Livrata','00000000003')
MAG — există în profit_orders        onames=['MAG000001']  -> 0 comenzi
MAG — aceeași comandă, prin AWB      awbs=['00000000002'] -> 1 comandă  ('MAG000001','Netrimisa',…)
NUBRA00002 — ORDER_RE nici n-o extrage                     -> 0 comenzi
LAB000001   — ORDER_RE nici n-o extrage                     -> 0 comenzi
```

### Cauza 1 — `metrics.orders` nu conține magazinele

`SELECT count(*) FROM orders WHERE name LIKE 'MAG%'` → **0**. La fel PL, LUX, NOC. În `profit_orders` aceleași prefixe au: MAG 36.620 · PL 28.711 · LUX 18.471 · NOC 12.481. Pe ultimele 90 de zile, **19.477 din 216.496 comenzi (9,0%)** aparțin unor prefixe cu zero rânduri în `metrics.orders`.

### Cauza 2 — `ORDER_RE` (L72 VPS) nu recunoaște formatele reale

```python
ORDER_RE = re.compile(r"\b(EST|GT|NUB|GRAND|GRAN|MAG|OFER|RED|BONBG|BON|CZ|PL|BELA|GEN|CARP|COV|APR|ROSSI)[ -]?(\d{4,7})\b", re.I)
```

Testat pe cele 216.496 de nume de comenzi reale din ultimele 90 de zile: **16.616 (7,7%) NU se potrivesc**.

| Prefix nepotrivit | Comenzi 90z | Format real |
|---|---:|---|
| NUB (Nubra) | 9.161 | `NUBRA00002` — „NUB" prinde, dar urmează „RA", nu cifre |
| LAB (Lab Noir) | 4.257 | `LAB000001` — prefixul lipsește din regex |
| LUX | 1.036 | `LUX17150` |
| NOC (Nocturna) | 543 | `NOC000001` |
| DUPBG / SK / ORC / HU / MD | 514 / 383 / 301 / 271 / 150 | lipsesc din regex |

**Efectul pe calitate, în lanț:** clientul dă numărul comenzii → nu e extras → `orders=[]` → contextul devine `(nicio comandă găsită)` → `has_order_data()` False → `HALLU` intervine → draftul iese cu șablonul sigur *„Ca să verific exact comanda dumneavoastră, îmi puteți spune numărul comenzii sau un număr de telefon asociat?"* — adică exact informația pe care clientul tocmai a dat-o. Pentru Nubra și Lab Noir, asta e comportamentul implicit pe TOATE tichetele cu nr. de comandă.

### Fix minim (ordinea contează)

1. `ORDER_RE`: adaugă `NUBRA|LAB|NOC|LUX|DUPBG|ORC|SK|HU|MD` **înaintea** alternativelor mai scurte (`NUBRA` înainte de `NUB`).
2. `lookup_orders`: interoghează `profit_orders` **direct pe `order_names`**, nu doar pe `byname.keys()` — profit_orders are comenzile pe care metrics nu le are.
3. Re-rulează testul de mai sus și cere ≥1 comandă pe `MAG000001`, `NUBRA00002`, `LAB000001`.

---

## Contextul minte modelul: `(nicio comandă găsită)` ≠ „nu s-a căutat"

În `--lean`, blocul de comenzi din prompt (L1021-1025) este literal:

```
    (nicio comandă găsită)
```

identic cu ce se pune când `--ground` chiar a căutat și n-a găsit. Modelul n-a halucinat liber: a raportat fidel o premisă falsă pe care i-am dat-o noi. De aceea tiparul dominant din 24-iun (341 din 360) e „nu am găsit nicio comandă", cu 286 de apariții ale formulării exacte *„nu am găsit nicio comandă"*.

Fixul nu e mai mult prompt anti-halucinare, ci **două stringuri diferite**: `NECUNOSCUT — nu s-a făcut niciun lookup` vs `LOOKUP EXECUTAT — 0 rezultate`. Și, în modul `--ground`, când lookup-ul chiar a rulat pe un email/telefon cunoscut, forțarea șablonului „dați-mi numărul comenzii" e comportament greșit — sistemul cere o dată pe care o are deja.

---

## Dependințele „lipsă" de pe VPS: 4 din 5 EXISTĂ — problema e CALEA, nu mașina

`HERE = os.path.dirname(os.path.abspath(__file__))` (L36). Cronul rulează copia PLATĂ, deci `HERE=/root/Scripturi`, iar căile ies în afara arborelui:

| Ce caută codul | Calea calculată | Pe disc acolo? | **Unde e de fapt** |
|---|---|---|---|
| playbook învățat | `/root/Scripturi/.learned_playbook.md` | ❌ | nicăieri pe VPS (e în `.gitignore`) |
| `grammar_audit.py` | `/root/Scripturi/grammar_audit.py` | ❌ | ✅ `…/team-intelligence/plugins/gigi/skills/cs-draft-reply/grammar_audit.py` |
| `CSA` (cs-actions) | `/root/cs-actions/scripts/cs_actions.py` | ❌ | ✅ `…/plugins/gigi/skills/cs-actions/scripts/cs_actions.py` |
| `CI` (customer-identity) | `/root/customer-identity/customer_identity.py` | ❌ | ✅ `…/plugins/gigi/skills/customer-identity/customer_identity.py` |
| `KB` (kb.py) | `/core/scripts/kb.py` | ❌ | ✅ `…/plugins/core/scripts/kb.py` |
| `cs_photo` | `/root/cs-photo/cs_photo.py` | ❌ | ✅ **și** `/root/Scripturi/cs_photo.py` (30.859 b) → importul MERGE |

Clona `/root/Scripturi/team-intelligence` (HEAD `15f1b4b`) e actualizată automat: `run_cs_pipeline.py` face `git pull --ff-only` la fiecare 30 de minute (cron `*/30 8-16`). `cs_auto_draft.py` din clonă are sha `57631aebe2f27af1` = **exact versiunea din git**, inclusiv linia Lab Noir care lipsește din copia plată.

> **Remediul corect nu e `scp`.** E să rulezi din clonă (`cd …/plugins/gigi/skills/cs-draft-reply && python3 cs_auto_draft.py …`), unde toate căile-surori se rezolvă singure și codul se actualizează singur la fiecare `git pull`. Copia plată din `/root/Scripturi` e o furculiță de deploy manual care a rămas în urmă cu un commit.

### Singura dependință real absentă: `.learned_playbook.md`

E în `.gitignore` (linia 3 din `plugins/gigi/skills/cs-draft-reply/.gitignore`) → **nu va ajunge niciodată pe VPS prin git**. Unica copie e pe Mac, din **23-iun-2026** (17.884 b). Sursa din care se regenerează e însă pe VPS și e vie: `/root/Scripturi/data/richpanel_tickets.db`, 478.224.384 b, modificat **15-sep 12:05**.

```bash
# corect: regenerează pe VPS din tichetele de AZI, nu copia fișierul din iunie
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/cs-procedures
PROC_MODEL=gpt-4o python3 cs_procedures.py --category all \
  --out ../cs-draft-reply/.learned_playbook.md
```

### …dar playbook-ul acoperă doar o treime din drafturi

Parsat (`load_playbook`, L615-634, heading-uri `## CATEGORIE`), fișierul dă **9 categorii**: `livrare_wismo, retur, anulare, problema_produs, modificare_comanda, schimb_swap, presale_intrebare, plata_factura, refuz_livrare` — fiecare învățată din 4-10 tichete. `IDENTIFY_SYS` (L393-404) declară **14**.

Pe populația din 24-iun: **acoperite 460 / 1.336 = 34,4%**; **neacoperite 876 = 65,6%** (`altele` 745, `recenzie_feedback` 60, `comanda_noua` 56, `comentariu_social` 14, `spam_automat` 0). Deci chiar deployat, playbook-ul nu ar fi atins două treimi din drafturi. `LEARNED.get(cat)` nu are nici fallback pe `altele`.

---

## Alte defecte măsurate pe cohorta din 24-iun (neraportate)

| Defect | Măsură | Cauză în cod |
|---|---:|---|
| Brand necunoscut → semnătură „echipa noastră" | **491 / 1.336 = 36,8%** | `PAGE_STORE` gol pe email + `brand_from_email` nu prinde domeniul |
| Categoria `altele` → fără procedură, fără voce | **745 / 1.336 = 55,8%** | triaj slab pe gpt-4o-mini |
| Canale fără stil dedicat | **275 / 1.336 = 20,6%** | `PLATFORM` (L51-64) n-are chei `facebook_message` (233) și `email_from_widget` (42) → `PLATFORM.get(channel, (channel, "ton prietenos, la obiect."))` la L923. Emailurile din widget pierd regula „răspuns COMPLET cu salut + semnătură" |
| Categorie malformată de la LLM | `anulare\|altele` (1) | nu se normalizează → ratează `LEARNED` și `ACTION_CATS` |
| Bonhaus fără țară | 115 drafturi | `EMAIL_BRAND` (L124-131) n-are `bonhaus.cz/.pl/.bg` → `brand_from_email("info@bonhaus.cz")` → `"Bonhaus"` → `STORE_LANG.get("Bonhaus")=None`, `STORE_PHONE.get("Bonhaus")=""` |

**Partea care a mers și merită spusă:** limba a fost corectă. Pe toate cele 1.336 de drafturi, limba detectată e **ro 1.232 · cz 83 · pl 19 · bg 1 · ASCII 1**; cele 101 drafturi Bonhaus internaționale au ieșit în cz/pl/bg. Dar au ieșit așa **exclusiv** prin `detect_lang` pe diacriticele din mesajul clientului, nu prin `STORE_LANG` (care era rupt, vezi tabelul). Un client ceh care scrie fără diacritice cade pe română.

---

## De ce 1.336 drafturi salvate, dar doar 979 tag-uri `ai-*` — rezolvat

Nu e un mister, e cod:

```python
# L595-599
def add_tags(mcp, cid, names):
    ids = [i for i in (tag_id(mcp, n) for n in names) if i]
    if ids and cid:
        mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": ids})   # rezultatul e ARUNCAT

# L267-274
def call(self, name, args):
    try:  ...
    except Exception as e:
        return {"_error": str(e)}          # orice eșec devine o valoare, nu o excepție
```

`tag_id` (L579-593) întoarce `None` tăcut dacă `list_tags` sau `create_tag` eșuează; `add_tags` nu verifică nimic. Iar mesajul din log:

```python
print(("  ✅ DRAFT salvat%s (NU trimis)." % (" + tag %s" % AI_TAG if AI_TAG else "")) if ok else …)
```

este condiționat DOAR de `create_draft` — `"+ tag ai-draft"` e o **promisiune tipărită, nu o confirmare**. Un 429 pe `add_tags_to_conversation` = tag pierdut, tăcut.

**Consecință gravă:** `--skip-tagged` se sprijină pe exact acele tag-uri. Pe tichetele unde tag-ul s-a pierdut, idempotența dispare → o repornire va **stivui un al doilea draft** peste cel vechi (`create_draft` adaugă, nu suprascrie, și nu există delete prin API).

*Fix minim:* verifică rezultatul lui `add_tags` și, la eșec, nu raporta „+ tag"; ideal, retry pe 429 (același `_llm_http`-style backoff pe care îl are deja calea LLM, dar nu și calea MCP).

---

## `grammar_audit.py` — trei defecte care fac gate-ul să raporteze fals „0 greșeli"

Fișier: `plugins/gigi/skills/cs-draft-reply/grammar_audit.py`, 111 linii. Peste capcana deja cunoscută (auditează și `mesaj_client`, nu doar `RASPUNS`):

1. **Un singur apel LLM pentru TOT lotul** (L73-76). Nu există batching, `--limit` sau paginare. A mers pe 19-20 de drafturi în iunie; pe 1.336 nu are cum.
2. **Rezultatele lipsă se numără drept CURATE** (L89-94):
   ```python
   r = by_id.get(i) or {}
   iss = r.get("issues") or []
   if not iss:
       clean += 1
       continue
   ```
   Dacă modelul întoarce rezultate pentru 40 din 200 de id-uri, celelalte 160 se raportează ca perfecte. **„A convers la 0 greșeli" poate să însemne „modelul n-a răspuns".** Cere întotdeauna numitorul: `len(res)` vs `len(texts)`.
3. **`secret()` fără try/except** (L24-25) — spre deosebire de cel reparat din `cs_auto_draft.py`. Pe VPS, unde `uv` nu e în PATH-ul cronului, gate-ul crapă cu `FileNotFoundError` în loc să cadă pe env.

Plus: **ramura `private_msg` (L70-71) e cod mort.** `grep -n private_msg /root/Scripturi/cs_auto_draft.py` → zero apariții; rândurile `--json` (L1191-1194) conțin doar `no, store, channel, cat, escalate, language, cust_msg, subject, orders, comment_action, draft`. Mecanismul de DM a fost scos pe 23-iun.

*Fixuri, în ordine:* (a) verifică `len(res) == len(texts)` și **crapă** dacă nu, nu raporta „curat"; (b) batch-uri de 20-25 de texte; (c) try/except pe `secret()`; (d) în prompt, „auditează DOAR linia `RASPUNS:`; `mesaj_client` e strict context".

---

## Coada de acțiuni — „propune + aprobă" n-a executat niciodată nimic

`/root/Scripturi/.auto_draft_proposals.json` (7.286.865 b, 3-iul 11:28):

```
intrări:            3.156
cu acțiune (cmd):       9
escalate:             328
applied=True:           0
```

În toată istoria sistemului s-au propus **9** acțiuni executabile (modify/cancel/swap/resend) și s-a aplicat **0**. Calea `--approve` e, practic, netestată în producție — și oricum e inoperantă din copia plată, unde `CSA` nu se rezolvă.

---

## Sursele de grounding, verificate AZI (15-sep-2026)

Necesar, fiindcă împrejurimile s-au schimbat în septembrie (Order Hub, AWBprint stale). Ambele surse sunt VII:

| Sursă | Stare azi | Notă |
|---|---|---|
| `metrics.orders` (pas 1) | `max(shopifyCreatedAt) = 2026-09-15T07:00:52Z`, **368.231** rânduri | 273.551 cu email (74,3%), 368.172 cu `shippingPhone` (99,98%) — dar **lipsesc integral** MAG/PL/LUX/NOC |
| `/root/Scripturi/data/profitability.db` (pas 2) | 722.767.872 b, modificat **15-sep 12:10**; **843.459** comenzi, **804.057** cu AWB; `max(created_at)=2026-09-15T00:29:27Z` | în iunie avea 294k/278k — a crescut de ~2,9× |
| `pg8000` în `/root/Scripturi/.venv` | ✅ importă | fără el grounding-ul pică tăcut |
| `uv` în PATH-ul cronului | ❌ (binarul există la `/root/.local/bin/uv`) | degradează grațios din 29-iun (`secret()` try/except), nu mai crapă |

Deci `--ground` nu e blocat de date moarte. E limitat de acoperire (secțiunea „grounding orb").

---

## SKILL.md e stale pentru handover

`plugins/gigi/skills/cs-draft-reply/SKILL.md` (155 linii) descrie o realitate din iunie:

- **linia 83**: „Cron (VPS): `/root/Scripturi/cs_backlog.sh` … rulat la 3h (`0 9-21/3`)" — **nu spune că e PAUZAT din 29-iun**. Cine citește SKILL.md crede că sistemul e live.
- **liniile 74-77**: documentează `--lean` ca mod de backlog; wrapperul folosește `--ground` din 29-iun.
- nu pomenește `--fast-triage`, `--apply-send`, caching-ul de prompt Anthropic (L298-302) sau `--photos`.

De actualizat înainte de predare, altfel Sonia repornește configurația greșită.

---

## Ce mai rulează pe aceeași mașină (ca să nu se creadă că CS-ul e oprit)

| Cron | Ce face | Stare |
|---|---|---|
| `*/30 8-16` + `0 2` `run_cs_pipeline.py` | `git pull` pe clona team-intelligence + `richpanel_pipeline.py` | ✅ activ, `cs_intraday.log` 15-sep 12:05 |
| `0 2` `run_cs_mirror.sh` | oglinda CS — captează firul complet din Richpanel, DOAR CITIRE | ✅ activ, `cs_mirror.db` 47 MB, 15-sep 03:00 |
| `50 9` `cs_queue_sync.sh` | coada CS din xConnector/AWBprint | ✅ activ |
| `0 9-21/3` `cs_backlog.sh` | **auto-draft** | ⏸️ `# PAUZAT 2026-06-29` |

Notă din `run_cs_mirror.sh`, utilă pentru orice lucru pe comentarii: comentariile FB trase la 3 zile sunt **57,1%** „This message was deleted"; trase în aceeași zi, **14,4%**. 43 de puncte procentuale de conținut mor între ziua 0 și ziua 3.

---

## Corecții de citare (numerotare VPS; în git +1 după linia 66)

- procedura **LIVRARE/WISMO** e la **L431**, nu L432 (L432 = `- RETUR:`).
- `SYSTEM` se declară la **L419**; regula „răspunzi la ULTIMUL mesaj" din interiorul lui e la **L428**.
- corecte, verificate: `HALLU` L189-195 · `has_order_data` L197-200 · `secret()` L226-238 · `MCP.call` L267-274 · `llm()` L295-314 · `store_prefix` L449-457 · `lookup_orders` L512-575 · `tag_id`/`add_tags` L579-599 · `load_playbook` L615-634 · `IDENTIFY_SYS` L391 (regula „ultimul mesaj" L392) · `ESCALADARE` L410 · `HOLDING` L446 · `PLATFORM.get` L923 · marcajul din transcript L961 · post-filtrul L1157-1171 · rutarea escaladării L1201-1208 · garda anti-gunoi L1214 · `create_draft` L1227.

---

## Runbook — reproducerea tuturor cifrelor de mai sus

```bash
# 1) codul rulat vs git
scp root@84.46.242.181:/root/Scripturi/cs_auto_draft.py /tmp/vps.py
diff /tmp/vps.py <git>/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py   # 1 rând: Lab Noir

# 2) AMBELE loguri de producție
ssh root@84.46.242.181 'grep -rl "DRAFT salvat" /root/Scripturi/data/'
ssh root@84.46.242.181 'for f in /root/Scripturi/data/cs_backlog.log /root/Scripturi/data/cs_draft_all.log; do
  echo "$f"; for m in "✅ DRAFT salvat" "⛳ Rutat" "┌─ DRAFT AȘTEPTARE" "⛔ draft invalid"; do
  printf "  %-24s %s\n" "$m" "$(grep -c "$m" $f)"; done; done'

# 3) recensământ HALLU pe orice log (regexul se importă din codul VIU, nu se copiază)
#    blocurile de draft sunt „  ┌─ DRAFT (motor) …" / „  │ text" / „  └───"
#    numitorul corect = drafturile SALVATE și NE-de-așteptare (is_esc le scutește de filtru)

# 4) grounding, test end-to-end (doar SELECT)
ssh root@84.46.242.181 'cd /root/Scripturi &&
  export DATABASE_URL_METRICS="$(grep -m1 ^DATABASE_URL_METRICS= .env | cut -d= -f2-)" &&
  .venv/bin/python3 -c "
src=open(\"cs_auto_draft.py\",encoding=\"utf-8\").read()
ns={\"__name__\":\"m\",\"__file__\":\"/root/Scripturi/cs_auto_draft.py\"}
exec(compile(src,\"c\",\"exec\"),ns)
print(ns[\"lookup_orders\"](\"\",\"\",[\"EST000001\"],[]))
print(ns[\"lookup_orders\"](\"\",\"\",[\"MAG000001\"],[]))     # 0 — defectul
print(ns[\"lookup_orders\"](\"\",\"\",[],[\"00000000002\"]))  # aceeași comandă, găsită prin AWB
"'

# 5) acoperirea ORDER_RE pe comenzi reale (90 de zile, din profitability.db)
```

**Poarta de repornire, reformulată:** nu „încă un audit pe text" (avem deja recensământ determinist pe ambele populații), ci, în ordine: (1) repară `ORDER_RE` + pasul 2 din `lookup_orders` și dovedește-l cu testul de la punctul 4; (2) rulează din **clonă**, nu din copia plată; (3) regenerează playbook-ul pe VPS; (4) pornește cu **Claude**, nu cu `cs_backlog.sh`; (5) abia apoi lot mic + măsurătoarea de EFECT la 72h.

### Completare 4

## A. Ce a produs de fapt sistemul — rezultatul de business (măsurat 15-sep-2026)

Cele 1.336 de drafturi scrise pe 24-iun nu au fost folosite. Am extras din `cs_backlog.log` numerele de conversație care au primit `✅ DRAFT salvat` și le-am interogat azi în `metrics.richpanel_tickets`:

| Lot | Tichete | OPEN azi | CLOSED azi | % încă deschise |
|---|--:|--:|--:|--:|
| Cu draft scris (`✅ DRAFT salvat`) | 1.336 | **1.001** | 334 | **74,9%** |
| Otrăvite (`⛔ draft invalid`, distincte) | 378 | **300** | 78 | **79,4%** |

Defalcat pe canal, pe lotul otrăvit: email 107 OPEN / 77 CLOSED, **messenger 97 OPEN / 0 CLOSED**, **facebook_message 86 OPEN / 1 CLOSED**, widget 8 OPEN, IG msg 2 OPEN. Adică **185 din 186 de tichete DM sunt și azi neatinse** — canalul DM nu e doar nedraftat, e nelucrat deloc.

> **Concluzia pentru Sonia:** bucla „AI scrie draft → agentul apasă Send" nu s-a închis niciodată. Înainte de orice repornire tehnică, trebuie stabilit cu CS **cine** consumă drafturile și **cum** se măsoară că le-a consumat. Altfel repornirea produce alte 1.336 de drafturi necitite.
>
> *Avertisment de numitor:* warehouse-ul are doar OPEN/CLOSED; un tichet amânat (snoozed) apare probabil ca OPEN, iar unul închis și redeschis apare OPEN. Cifra e o limită superioară a „neatinselor", nu o dovadă că nimeni nu s-a uitat.

---

## B. ⛔ PERICOL VIU: `--send` și `--approve` pot trimite „(eroare LLM…)" la client

Garda anti-gunoi (`cs_auto_draft.py:1213-1215`) protejează **doar** calea din bucla principală. Coada `.auto_draft_proposals.json` primește draftul **înainte** de gardă (linia ~1194), iar cele două căi manuale îl citesc fără nicio verificare:

- `do_send()` — liniile **804-832**: refuză escaladări, `hide` și retrimiterea, dar **nu** verifică dacă draftul începe cu `(eroare` sau e gol. Trimite `send_message` LIVE + `update_conversation_status=CLOSED`.
- `do_approve()` — liniile **764-802**: salvează `p["draft"]` prin `create_draft` fără verificare.

**Măsurat pe `/root/Scripturi/.auto_draft_proposals.json` (3.156 intrări, 7,3 MB):**

| | Nr. |
|---|--:|
| intrări cu draft care începe cu `(eroare` | **459** |
| din care ne-escaladate ȘI ne-`hide` = **eligibile pentru `--send`** | **449** |
| intrări cu acțiune propusă (`cmd`) | 9 |
| intrări cu `hide` propus | 31 |
| `applied` / `sent` | 0 / 0 |

Un singur `cs_auto_draft.py --send 272547` ar posta la client textul `(eroare LLM: [Errno 2] No such file or directory: 'uv')` și ar închide tichetul. `do_send` nici nu poate distinge un comentariu public de un email — coada nu salvează `channel` (cheile sunt: `action_desc, cat, cid, cmd, ctx, draft, escalate, hide, order, store`), deci îi lipsește garda `is_public` pe care `--apply-send` o are.

**Reparație minimă, înainte de orice altceva:**
1. În `do_send` și `do_approve`, aceeași gardă ca la 1213: `if d.startswith("(eroare") or "(eroare LLM" in d or len(d) < 5: refuză`.
2. Adaugă `channel` / `is_public` în intrarea de coadă și refuză `send_message` pe canale publice.
3. Curăță o dată coada: elimină cele 459 de intrări otrăvite (fișierul e doar cache local, nu sursă de adevăr).

---

## C. Ce nu a rulat NICIODATĂ (fii sceptică la orice capitol care descrie o funcție ca „existentă")

Din 137.095 de linii de jurnal și 25 de rulări:

| Funcție | Măsurat în jurnal | Notă |
|---|---|---|
| Rutarea escaladărilor (HIGH + tag + notă privată) | **0** linii `⛳ Rutat`, deși **176** tichete distincte marcate `⛳` | blocată de `if is_esc and not a.lean` (**linia 1206**); cronul rula `--lean` |
| `--ground` (grounding + căutare după AWB) | **21** tichete, toate în rularea neterminată din 2-iul, **0 drafturi** | tot PR #327/#328/#330 e practic netestat în producție |
| Post-filtru anti-halucinare (`HALLU`) | **0** regenerări (`+corectat`), **0** șabloane sigure | n-a existat în versiunea care a rulat |
| `--photos` | **2** tichete | |
| `--fast-triage` | **0** — flagul există doar în fișierul instalat pe 3-iul, după ultima rulare | |
| `--approve` / `--send` / `--apply-send` | 0 aplicate, 0 trimise | |
| `fb_hide_comment` | 31 propuneri, **0** aplicate | |
| `.learned_playbook.md` (injecția de voce) | absent pe VPS → `LEARNED = {}` → `learned_blk` gol | no-op de la prima zi |
| `.voice_pack.json` | `{}`, și **nu-l citește nimeni** | `build_voice_pack.py` = cod mort |

**Ce a funcționat consecvent:** filtrul de spam — **5.146 de excluderi** logate în cele 25 de rulări (≈222/rulare la început, ≈240 la final), ~12% din volum. E singura componentă cu istoric curat.

---

## D. Versiunea care a rulat NU e versiunea de pe VPS

| Fișier | Octeți | Linii | Data | A rulat? |
|---|--:|--:|---|---|
| `/root/Scripturi/cs_auto_draft.py` | 93.425 | 1.243 | **3-iul 11:27** | **NU** — instalat a doua zi după ultima rulare |
| `…/cs_auto_draft.py.bak-efficiency-0703` | 92.350 | 1.235 | 3-iul 11:27 | DA — rularea din 2-iul |
| `…/cs_auto_draft.py.bak-20260629-170703` | 92.134 | 1.233 | 29-iun 16:07 | — |
| `…/cs_auto_draft.py.bak-20260629-165430` | 89.159 | 1.197 | 29-iun 15:54 | — |
| `…/cs_auto_draft.py.bak-20260629-164236` | 88.998 | 1.196 | 29-iun 15:42 | — |
| `…/cs_auto_draft.py.bak-20260629-163827` | 87.439 | 1.185 | 29-iun 15:38 | — |
| `…/cs_auto_draft.py.bak-20260629-162212` | 78.354 | 1.072 | 29-iun 15:22 | DA — cele 24 de rulări din 24→29-iun |
| git `plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | 93.459 | 1.244 | 8-iul 14:14 | — |

Sunt **6** backupuri, nu 5. Diferența git↔VPS e într-adevăr o singură linie (reprodusă cu `diff -u`), dar corolarul important e altul: **`--fast-triage` a fost adăugat abia pe 3-iul și n-a rulat niciodată**, iar orice cifră de cost care se sprijină pe el e o proiecție, nu o măsurătoare.

---

## E. Costul, cu numitorul pus la loc

Tabelul de preț pe model din document e **corect** — l-am rederivat celulă cu celulă cu prețurile curente (Sonnet 5 $2/$10, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5, Opus 5 $5/$25 per MTok). Pragurile de cache sunt și ele corecte: Sonnet 5 = 1.024, Haiku 4.5 = 4.096. **Cifra-titlu e însă greșită**, fiindcă presupune că `--fast-triage` prinde pe toate tichetele.

**Măsurat** — `categorize_hint()` din cod, rulat pe **toate cele 6.513 tichete OPEN de pe cele 5 canale ale wrapperului** (`richpanel_tickets.db`, azi):

| Hint regex | Tichete | % |
|---|--:|--:|
| `altele` | 4.686 | **71,9%** |
| `spam_automat` | 639 | 9,8% |
| `retur` | 369 | 5,7% |
| `livrare_wismo` | 242 | 3,7% |
| `anulare` | 207 | 3,2% |
| restul (problema_produs, presale, recenzie, plata, modificare, comanda_noua, swap, refuz) | 370 | 5,7% |

→ **`--fast-triage` sare apelul de triaj pe doar 18,2%** (1.188 / 6.513). Verificat independent pe un eșantion aleator de 400 de tichete din warehouse: 17,2%.

| Scenariu (Sonnet 5 + caching) | $/1.000 tichete | 6.513 tichete |
|---|--:|--:|
| fără `--fast-triage` | 7,25 | 47,2 $ |
| **cu `--fast-triage`, la 18,2% real** | **6,72** | **43,8 $** |
| cu `--fast-triage` la 100% (ipoteza din document) | 4,33 | 28,2 $ |

Costuri **neincluse în niciun scenariu** (toate împing în sus):
- **al treilea apel LLM** — regenerarea anti-halucinare (liniile 1159-1176) refolosește `SYSTEM` întreg (~4.479 tok input) când nu există date de comandă; ~+4,3 $/1k la 100% declanșare;
- **apelurile de viziune** — `--photos` e implicit PORNIT;
- **tokenii de output** rămân estimați (~400/tichet) fiindcă nimeni nu citește `usage` (vezi §F).

> ⚠️ **Nu poți alege Sonnet 5 cu wrapperul actual.** `llm()` citește `ANTHROPIC_MODEL` (implicit **`claude-sonnet-4-6`**) pe ramura Claude; `DRAFT_MODEL=gpt-4o-mini` pe care-l setează `cs_backlog.sh` nu are niciun efect acolo. Fără `export ANTHROPIC_MODEL=claude-sonnet-5` plătești 10,88 $/1k.
>
> ⚠️ **Comentariul din cod e greșit.** Linia 300 spune „Sonnet 4.6 = 2048" — pragul real e **1.024**, deci pe modelul implicit **ambele** prompturi de sistem (1.910 și 3.960 tokeni) se cache-uiesc deja. Corectează comentariul odată cu SKILL.md.

---

## F. Comutarea tăcută pe Claude — capcana de 20×

`ANTHROPIC_API_KEY` **este deja** în `/root/Scripturi/.env` (root-600). Azi nu se folosește doar fiindcă `cs_backlog.sh` nu o exportă și `secret()` verifică env-ul întâi. Dar `llm()` verifică **Anthropic PRIMUL**: în clipa în care cineva adaugă `export ANTHROPIC_API_KEY` în wrapper — exact mișcarea recomandată de orice plan de „trecem pe Claude" — motorul comută pe `claude-sonnet-4-6` **fără flag, fără log, fără avertisment**, de la 1,28 $/1k la 26,72 $/1k (10,88 cu cache).

Trei efecte secundare care nu se văd nicăieri:

1. **`js=True` e ignorat pe Anthropic** (liniile 296-321): triajul cere JSON (`llm(IDENTIFY_SYS, …, js=True)`, linia 1039) dar `response_format` există doar pe ramura OpenAI. Memoria propunea „prefill «{» când pornim pe Claude" — **azi asta returnează HTTP 400**, prefill-ul e eliminat pe Sonnet 4.6/5. Soluția curentă: `output_config: {format: {…}}` (structured outputs).
2. **Pozele rămân pe OpenAI.** `cs_auto_draft._vision_describe` (330-350) preferă Anthropic, dar modulul canonic — `cs_photo.vision()`, singurul deployat lângă script pe VPS — preferă **OpenAI** (`cs-photo/cs_photo.py:128-150`). Deci textul pleacă pe Claude și pozele rămân pe `gpt-4o-mini`. În plus `VISION_MODEL` e onorat **doar** pe ramura OpenAI.
3. **Gate-ul de gramatică nu urmează.** `grammar_audit.py` e OpenAI-only (`AUDIT_MODEL`, implicit `gpt-4o`, hardcodat pe `api.openai.com`) — și oricum **nu e pe VPS**.

Notă înrudită: `DRAFT_OPENAI_API_KEY` există în `.env` și **nu-l folosește niciun script** (grep pe tot `/root/Scripturi`). Cheia separată care ar fi rezolvat contenția 429 din 2-iul nu e cablată nicăieri.

---

## G. Ce e deployat de fapt pe VPS

| Fișier al skillului | Pe VPS? |
|---|---|
| `cs_auto_draft.py` | ✅ |
| `cs_photo.py` | ✅ (hash identic cu git: `4fb6079ef80ac4e4`) |
| `cs_draft_reply.py` | ❌ |
| `grammar_audit.py` (gate de limbă/gramatică) | ❌ |
| `cs_ticket_index.py` (precedent semantic) | ❌ |
| `build_voice_pack.py` | ❌ |
| `mcp_server.py` | ❌ |
| `.learned_playbook.md` | ❌ (există local: 17.884 B, 23-iun — **e un scp, nu o regenerare**) |
| `.voice_pack.json` | ❌ (local e `{}` și nu-l citește nimeni) |

Dependențe verificate pe VPS: `pg8000 1.31.5` în `.venv` ✅; `fb_post_registry.sqlite` (311 KB, 3-iul) ✅.

---

## H. Restul skillului — fișiere pe care documentația le trece sub tăcere

| Fișier | Linii | Ce e | Stare |
|---|--:|---|---|
| `cs_ticket_index.py` | 136 | index semantic (fastembed) peste SUBIECTELE tichetelor CLOSED cu agent real; `retrieve()` importat de `cs_draft_reply.py` pentru „cum am rezolvat cazuri similare" | funcțional, dar **nu e pe VPS**; are nevoie de `data/richpanel_tickets.db` |
| `build_voice_pack.py` | 134 | extrage replici reale ale agenților → `.voice_pack.json` | **COD MORT** — `grep voice_pack` în `cs_auto_draft.py` + `cs_draft_reply.py` = 0 rezultate |
| `mcp_server.py` | 58 | expune skillul ca MCP `arona-cs-inbox` (`cs_draft`, `richpanel_triage`, `richpanel_janitor`, `cs_sentiment`, `cs_sla`) | **RUPT**, vezi mai jos |
| `DEMO.md` | 51 | rezumatul pentru echipă (pipeline + principiul draft-only) | ok |

### 🐞 `mcp_server.py` — unealta `cs_draft` nu poate funcționa

```python
# mcp_server.py:33
return _run(DRAFT, ["--conv", conv] + (["--create-draft"] if create_draft else []))
#            ^^^^^ = cs_auto_draft.py
```

`cs_auto_draft.py` **nu are argumentul `--conv`**. Singura apariție a șirului în tot fișierul e la **linia 502**, ca parametru pasat lui `customer_identity`. Orice apel al uneltei iese cu `argparse` cod 2. Reparația: `DRAFT` trebuie să pointeze la `cs_draft_reply.py` (care chiar are `--conv`, linia 160), sau flagul trebuie schimbat în `--only`. Celelalte 4 unelte pointează la scripturi care există.

---

## I. Corpusuri și acoperire — cifrele reale

### Bazele de tichete disponibile azi (VPS)

| Fișier | Mărime | Conținut | Prospețime |
|---|--:|---|---|
| `/root/Scripturi/data/richpanel_tickets.db` | 478 MB | **255.767 tichete** + 255.767 `customer_identity` + `pull_log` | **azi 12:05** |
| `/root/Scripturi/data/cs_mirror.db` | 47,6 MB | 12.234 `rp_ticket`, 24.656 `rp_message`, 1.822 atașamente, 7.554 `gm_message` | azi 03:00 |

Pentru playbook (`cs-procedures`) și precedent (`cs_ticket_index`) sursa e **`richpanel_tickets.db`**, nu oglinda.

> ⚠️ `metrics.richpanel_tickets.tags` ține **UUID-uri de tag, nu nume** (230.358 din 255.767 sunt `[]`). Acoperirea AI **nu** se poate audita din warehouse fără maparea id→nume; `--skip-tagged` lucrează cu `tag_names` din API-ul live.

### Pagini Facebook/Instagram — unde e gaura, ponderat cu traficul

`PAGE_STORE` are **19** intrări (git) / 18 (VPS). Tokenul `META_SYSTEM_TOKEN` vede **29** de pagini, toate cu `CREATE_CONTENT, MODERATE, MESSAGING, ADVERTISE, ANALYZE, VIEW_MONETIZATION_INSIGHTS` (verificat live azi).

Măsurat pe tichetele social OPEN (n = 8.866: 5.874 cmt FB + 1.411 fb_msg + 1.385 messenger + 112 cmt IG + 84 IG msg):

| Gaură | Pagini | Tichete | % din social |
|---|--:|--:|--:|
| pagini cu trafic **fără acces token** | 14 | 2.140 | **24,1%** |
| pagini cu trafic **nemapate în `PAGE_STORE`** | 17 | 1.434 | **16,2%** |
| tichete fără `to.id` deloc (tot canalul `messenger`) | — | 1.385 | 15,6% |

Cele două găuri care contează:

| Page ID | Magazin | OPEN | În `PAGE_STORE`? | Pe token? |
|---|---|--:|---|---|
| `638338549359389` | **Covoria** | 898 | ❌ | ❌ |
| `364899953373966` | **Ofertele Zilei** | 875 | ✅ | ❌ |
| `898588036681214` | **Lab Noir** | 91 | ❌ | ❌ |
| `814175968452902` | (Bonhaus BG parțial) | 151 | ❌ | ❌ |

Deci „contextul postării merge acum pe FB" e adevărat pe un eșantion de 6 pagini, dar **~1 din 4 tichete social nu are token de pagină**, iar pe cele două pagini cu cel mai mare backlog (1.773 tichete cumulat) nu poate funcționa deloc.

> 🐞 **Linia Lab Noir din git nu repară nimic.** Singura diferență git↔VPS este `"61586834387211": "Lab Noir"`. Pagina `61586834387211` are **ZERO tichete în tot warehouse-ul** (255.767 rânduri, orice status). Traficul real de Lab Noir e pe **`898588036681214`** — 243 tichete din 15-aug-2026, 91 OPEN azi — care lipsește din `PAGE_STORE` **și în git, și pe VPS**.
>
> **Reparația corectă** (nu un simplu `deploy.sh`):
> ```python
> "898588036681214": "Lab Noir",        # pagina REALĂ, 243 tichete din 15-aug
> "638338549359389": "Covoria",         # 898 OPEN, cea mai mare gaură
> "814175968452902": "Bonhaus BG",
> ```
> Un tichet fără mapare primește `store_name = "magazinul nostru"` și se semnează „echipa noastră". În coada de propuneri asta s-a întâmplat pe **554 din 3.156 de intrări = 17,6%**.

---

## J. Capcane de cod pe care le vei lovi sigur

### 1. `escalate` de la LLM e mort pe email și DM
```python
# liniile 1078-1081
_llm_esc = bool(idn.get("escalate")) or str(idn.get("severity")).upper() in ("HIGH","URGENT")
_ord_cat = cat in ("problema_produs","livrare_wismo","retur","schimb_swap","plata_factura",
                   "modificare_comanda","anulare","comanda_noua","presale_intrebare",
                   "recenzie_feedback","altele")          # <-- include si "altele"
is_esc   = _real_esc or (_llm_esc and not _ord_cat)
```
`_ord_cat` acoperă toate categoriile în afară de `spam_automat` și `comentariu_social`. Cum `altele` e 71,9% din volum, expresia se reduce la **`_real_esc`** — regexul determinist (ANPC/juridic, `ANGER_RE`, >70% MAJUSCULE) — pentru orice email/DM. **Nu pierde timp tunând `IDENTIFY_SYS` pentru escaladări pe email**; efectul e zero. Dacă vrei ca modelul să conteze, scoate `"altele"` din `_ord_cat`.

### 2. `--skip-tagged` nu ține pe comentariile cu un singur mesaj
La liniile 940-947, dacă `comment_count <= 1` și tichetul e public, `get_conversation` **nu se apelează**, deci `cur_tags` rămâne cel din lista de sumar — despre care codul însuși notează la linia 949 că „lista summary nu le are". Cronul scapă azi doar prin `--no-comments`. În ziua în care comentariile se activează, `create_draft` (care **ADAUGĂ**, nu suprascrie, și **nu are API de ștergere**) va stivui un draft nou pe fiecare comentariu, la fiecare rulare.

### 3. Bugetul de apeluri Richpanel omite listarea
Cu `--limit 3000 --scan 6000` (wrapperul actual), bucla de paginare (liniile 886-903) face până la **120 apeluri `list_conversations` pe canal** (6000/50), × 5 canale = **până la 600 de apeluri de listare pe rulare**, independent de câte tichete se draftează. Modelul „3-4 apeluri/tichet" nu le include. `--scan` e plafonul real, nu `--limit`.

### 4. `usage` e aruncat — nu poți dovedi nici costul, nici cache-ul
`_llm_http` (277-295) întoarce JSON-ul complet, dar nimeni nu citește `usage.output_tokens`, `usage.cache_read_input_tokens` sau `cache_creation_input_tokens`. Consecințe: tokenii de output rămân estimați pentru totdeauna, iar dacă un invalidator tăcut strică prefixul, plătești costul „fără cache" fără să afli. **Primul fix de logging, înainte de orice repornire:**
```python
u = r.get("usage") or {}
print("  💤 in=%s cache_r=%s cache_w=%s out=%s" % (
    u.get("input_tokens"), u.get("cache_read_input_tokens"),
    u.get("cache_creation_input_tokens"), u.get("output_tokens")), file=sys.stderr)
```

### 5. Rezumatul final nu numără nimic
Linia 1237 tipărește **doar** contorul de spam. Nu există contor de drafturi salvate / invalide / escaladate / sărite. Exact de asta cinci zile de output zero au arătat ✅ verde: informația nu era agregată nicăieri, era împrăștiată în 137.095 de linii. Orice watchdog trebuie să aibă mai întâi ce citi.

---

## K. Flaguri implementate dar NEdocumentate în `SKILL.md`

| Flag | În cod | În SKILL.md | Ce face |
|---|:--:|:--:|---|
| `--fast-triage` | ✅ | ❌ | sare triajul LLM când hintul regex e sigur (**doar 18,2% din cazuri**, §E) |
| `--ground` | ✅ | ❌ | grounding self-contained (metrics + `profitability.db`); **e ce rulează wrapperul de pe VPS**, dar SKILL.md documentează cronul cu `--lean` |
| `--apply-send` | ✅ | ❌ | ⚠️ **TRIMITE ÎN MASĂ la clienți** + închide tichetele, pe tot lotul, ne-escaladate și ne-comentariu |
| `--close-spam` | ✅ | ❌ | închide (CLOSED) + tag `spam` |
| `--json` | ✅ | ❌ | emite drafturile după marcajul `@@JSON@@` (intrarea pentru `grammar_audit.py`) |

`SKILL.md` mai conține trei afirmații demonstrat false azi: „tokenurile actuale sunt de ADS (0 pagini…)" (sunt 29 de pagini cu MODERATE), tagul `esc:<lvl>` (codul folosește `esc-%s`, fără `:`), și „Richpanel ~4 req/sec" (= 240/min, contra plafonului real de 60/min partajat cu CS-ul live).

---

## L. Ordinea corectă de repornire (blocante întâi)

1. **Garda pe `do_send`/`do_approve`** + curățarea celor 449 de intrări otrăvite din coadă (§B). *Fără asta nimeni nu are voie să atingă `--send`.*
2. **Logarea `usage`** (§J.4) și **contoarele în rezumatul final** (§J.5) — altfel watchdogul n-are ce citi.
3. **Watchdog + heartbeat** — `&& .venv/bin/python heartbeat.py cs_draft` pe linia de crontab (27 de joburi îl au deja, `cs_auto_draft` nu) + `check_cs_draft()` în `data_health.py` (427 linii, 6 verificări: `check_metrics`, `check_awbprint`, `check_awb_output`, `check_profitdb`, `check_heartbeats`, `check_gads_feed` — niciuna nu-l menționează).
4. **Dead-letter** — contor `fails` per conversație în coadă; la `fails >= 3`, tag `ai-failed` verificat lângă `--skip-tagged` (linia 965). Ținta măsurată: cele 378 de tichete care au consumat 6.015 încercări.
5. **`.learned_playbook.md` pe VPS** — un `scp` din git working dir (fișierul local există, 17.884 B).
6. **`PAGE_STORE`** — cele 3 pagini din §I (Lab Noir real, Covoria, Bonhaus BG), **nu** linia moartă din git.
7. **Rutarea escaladărilor** — dacă rămâi pe `--ground` (nu `--lean`), rutarea se activează singură; validează pe UN tichet că apar HIGH + tag + notă, fiindcă n-a rulat niciodată.
8. Abia apoi: alegerea modelului (`ANTHROPIC_MODEL=claude-sonnet-5`), structured outputs în locul lui `js=True`, `--fast-triage`.

---

## M. Incertitudini care rămân după verificare

- **Tokenii de output** (~400/tichet email) rămân estimați. Lungimile de prompt sunt însă consistente și credibile: `IDENTIFY_SYS` 5.078 caractere / 1.910 tokeni și `SYSTEM` 10.499 / 3.960 dau același raport, 2,65 car/token (rezonabil pentru română cu diacritice). Prima rulare reală, cu logarea din §J.4, fixează cifra.
- **`claude-haiku-4-5`**: în `/v1/models` pe cheia noastră apare doar ca `claude-haiku-4-5-20251001`. Aliasul nedatat e forma canonică și se rezolvă, dar nu l-am confirmat cu un apel. Risc mic — oricum Haiku e cea mai scumpă opțiune aici, fiindcă pragul lui de cache (4.096) e peste ambele prompturi.
- **Rularea din 2-iul** a fost oprită la tichetul 46/464 (fără `DONE`, log oprit la 11:36). Nu știu dacă a fost oprită manual sau a picat; toate cele 46 de tichete au lovit 429 pe OpenAI, pe ambele apeluri.
- **De ce 3 din 6 comentarii FB n-au putut fi citite prin Graph** rămâne neexplicat și în verificarea mea.

---

## 10. Accesul — ce are Sonia și ce-i trebuie

### Are deja

| Ce | Unde | Cum |
|---|---|---|
| **Codul** | `team-intelligence/plugins/gigi/skills/cs-draft-reply/` | repo **public** — îl citește fără să-i dea nimeni nimic. Ca să scrie: `gh repo fork` + PR |
| **Repo-ul aplicației CS** | `github.com/gbeschea/arona-cs` | privat, e **admin** |
| **Toate cele 6 secrete** | Second Brain, API | cont `member` → `POST /api/auth/login` → `GET /api/secrets/<KEY>/value` |
| **VPS** | `sonia@84.46.242.181` | gazdele `server` + `vps` acordate în SB; cheia se rotește cu `POST /api/ssh/my-key` |
| **Oglinda CS** | `sudo mirror-ctl snapshot` → `/srv/cs-share/` | grup `csmirror` |

Secretele necesare, toate confirmate prezente în Second Brain (373 în total):

```
RICHPANEL_MCP_TOKEN    OPENAI_API_KEY       ANTHROPIC_API_KEY
DATABASE_URL_METRICS   CLICKUP_API_TOKEN    META_SYSTEM_TOKEN
```

### Ce NU are încă

`mirror-ctl` acoperă doar **oglinda CS**, nu motorul de draft. Pentru a rula `cs_auto_draft.py` pe
VPS îi trebuie ori acces direct la `/root/Scripturi/` (are nevoie de root), ori un punct de intrare
controlat separat, pe modelul `mirror-ctl`.

⚠️ **De ce nu l-am construit deja:** versiunea de pe VPS diferă de git, iar `--apply-send` trimite
în masă către clienți. Un tool de acces trebuie construit **după** ce se decide care versiune e
canonică și ce gate-uri are trimiterea — altfel îngheață configurația greșită.

### Ordinea recomandată

1. Sonia își rotește cheia SSH și confirmă că intră pe VPS.
2. Decidem care copie a `cs_auto_draft.py` e canonică (§2) și o aducem în git.
3. Abia apoi construim `draft-ctl`, cu dry-run implicit.

---

## 11. Dacă ar fi să aleg trei lucruri

Din cele 52, astea trei schimbă cel mai mult, în ordinea asta:

**1. Măsoară acceptarea înainte de orice repornire.** Metrica de efect (§1) există acum pentru prima
dată și spune `0/40`. Orice repornire fără ea repetă exact același rezultat, la scară mai mare.

**2. Pune playbook-ul pe VPS.** Un `scp` de un fișier face ca vocea și procedurile învățate din
tichetele reale să intre efectiv în prompt — lucru care nu s-a întâmplat niciodată în producție.
Efort: minute. E singura reparație cu raport efort/impact atât de bun.

**3. Watchdog + heartbeat.** 5 zile de rulări goale cu cron verde nu trebuie să se mai poată întâmpla.
Restul infrastructurii are deja tiparul — 27 de joburi cablate pe `heartbeat.py`.

Curățarea celor 2.642 de drafturi vechi rămâne o decizie de owner: `create_draft` adaugă și **nu
există delete prin API**, deci ștergerea e manuală în UI.
