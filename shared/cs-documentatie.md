# Sistemul CS Richpanel — documentație completă și runbook de predare

> **Pentru cine preia munca.** Conține tot ce trebuie ca să continui fără să mă întrebi: unde stă
> codul, cum se rulează, ce înseamnă fiecare cifră, ce e stricat și de ce, și ce am încercat deja
> și n-a mers.
>
> **Actualizat:** 9 sep 2026 · **Faza:** captare (zero scrieri către client) ·
> **VPS:** `root@84.46.242.181` · **Tenant Richpanel:** `nocturna954`
>
> Fiecare cifră e măsurată, nu estimată. Unde nu știu, scrie „nu știu".

## Cuprins

1. [Ce e sistemul și de ce există](#1-ce-e-sistemul-și-de-ce-există)
2. [Runbook — cum rulezi tot](#2-runbook--cum-rulezi-tot)
3. [Componentele, una câte una](#3-componentele-una-câte-una)
4. [Ce am găsit parcurgând Richpanel](#4-ce-am-găsit-parcurgând-richpanel)
5. [Limite măsurate — ce nu se poate și de ce](#5-limite-măsurate--ce-nu-se-poate-și-de-ce)
6. [Defecte deschise](#6-defecte-deschise)
7. [Erată — ce am afirmat greșit](#7-erată--ce-am-afirmat-greșit)
8. [Ce urmează](#8-ce-urmează)
9. [Anexe: schema, secrete, glosar](#9-anexe)

**Documente-sursă:** [`cs-mirror-plan.md`](cs-mirror-plan.md) (planul tehnic detaliat, cu dovezile) ·
[`richpanel-inventar-functii.md`](richpanel-inventar-functii.md) (inventarul ecran cu ecran) ·
[`cs-app-cerinte.md`](cs-app-cerinte.md) (cerințele aplicației proprii) ·
[`../plugins/gigi/skills/richpanel-export/SKILL.md`](../plugins/gigi/skills/richpanel-export/SKILL.md)

---

## 1. Ce e sistemul și de ce există

Helpdesk-ul costă **6.060 $/an** și crește cu fiecare agent nou. Din 27 aug 2026 captăm local tot ce
trece prin el — inclusiv răspunsurile agenților — fără să scriem nimic înapoi și fără tokeni LLM.

| | Sumă | Detaliu |
|---|--:|---|
| Richpanel | **356 $/lună** | Helpdesk Pro 89 $ × 4 locuri; contract lunar; factura pe 8 ale lunii |
| ReplyZen | **149 $/lună** | moderare comentarii FB/IG |
| **Total** | **505 $/lună = 6.060 $/an** | **+89 $/lună per agent nou** |
| Oglinda | **0 $** | VPS existent, tokeni existenți, zero LLM |

Faza curentă e strict **oglindă**: citim, nu răspundem. Asta **nu reduce factura** și nu e proiectată
s-o reducă. Cumpără trei lucruri:

1. Istoricul complet al firelor, **cu numele agentului** (exportul vechi avea doar `first_message`,
   trunchiat la 301 caractere).
2. Textul comentariilor care se șterg de pe Facebook înainte să apuce cineva să le citească — pe care
   le pierd *ambele* sisteme.
3. Independență de interfața Richpanel pentru orice analiză.

### Starea componentelor

| Componentă | Stare | Ritm | Ce produce |
|---|---|---|---|
| Oglinda CS (`rp_sync.py`) | **LIVE** | zilnic 03:00 BUC | fir complet: mesaje client, răspunsuri agent, note private, atașamente |
| Captare email (`gmail_sync.py`) | **LIVE** | zilnic 03:00 | 17 cutii Google, doar citire |
| Paritate (`parity_check.py`) | **LIVE** | zilnic 03:00 | numitorul luat LIVE din Richpanel |
| Pipeline îmbogățire (`run_cs_pipeline.py`) | **LIVE** | 30 min + nightly | taguri + o notă internă; 251.407 tichete în Postgres |
| Webhook FB/IG (Cloudflare Worker) | **DRAFT** | timp real | 29 pagini abonate; ascunde live, răspunsurile în coadă |
| Punte AWBprint → fișa CS (`rp.py push`) | **OPRIT** | — | rupea linkul către comandă (§7, erata 03–04) |

### Paritatea, măsurată 9 sep 2026 (2–8 sep)

| Indicator | Rezultat | Citire |
|---|---|---|
| **M1** — acoperire pe tichete, pe zi și canal | **TRECE — 100,00%** | 4.362 tichete, **zero id-uri lipsă**, toate cele 7 zile × 7 canale |
| **M3** — răspunsurile agenților | **36 / 1.205** | **35 = decalaj**, nu pierdere (răspunsuri sosite după rularea de la 03:00). **1 = defect real**, vezi §6.1 |

---

## 2. Runbook — cum rulezi tot

### 2.1 Unde stă codul

| | Cale |
|---|---|
| Cod oglindă | `team-intelligence/plugins/gigi/skills/richpanel-export/` |
| Webhook | `team-intelligence/shared/scripturi-tools/fb-comment-webhook/` |
| Pe VPS (clonă git) | `/root/Scripturi/team-intelligence/…` (același drum) |
| Wrapper de rulare | `/root/Scripturi/run_cs_mirror.sh` |
| Baza oglindă | `/root/Scripturi/data/cs_mirror.db` (SQLite WAL, 30 MB) |
| Istoric îmbogățit | `/root/Scripturi/data/richpanel_tickets.db` (466 MB) → `metrics.richpanel_tickets` |
| Jurnale | `/root/Scripturi/data/cs_mirror.log` |

> **✅ Codul E în git (din 9 sep 2026).** Branch **`feat/cs-mirror-oglinda`** → **PR #576**
> (ramificat din `origin/main`, ca să nu amestece PR #574 — webhook-ul). Conține `cs_mirror.py`, `gmail_sync.py`,
> `parity_check.py`, `rp.py`, `rp_sync.py`, `richpanel_apply.py`, `SKILL.md` + cele 4 documente CS.
> Înainte de commit am verificat prin **hash SHA-256** că fișierele locale sunt **identice** cu cele
> care rulează pe VPS — altfel s-ar fi versionat o copie veche.
>
> **⚠️ Două fișiere sunt IGNORATE deliberat** (`.gitignore`), nu uitate:
> - `push_log.json` — jurnalul de semnături al `rp.py push`; **stare de rulare**, se reface singur.
> - `connector_keys.json` — cheile Custom Connector. Sunt **publice** (stau în sursa fiecărui magazin),
>   dar sunt **credențiale de SCRIERE** în fișa clientului: connectorul n-are altă autentificare
>   (§3.6). Regula echipei e categorică — credențialele nu intră în git — iar fișierul se re-culege
>   în câteva secunde: `uv run rp.py keys --refresh`.
>
> **Cele 4 wrappere care CONDUC producția sunt acum în git** (PR #587, 14 sep):
> `run_cs_mirror.sh`, `run_cs_pipeline.py`, `run_rp_push.sh`, `run_apply.sh`, în
> `shared/scripturi-tools/`. Nu erau nicăieri, și erau **structural invizibile** pentru
> `deploy_parity.py` (el compară doar fișiere care există deja în git).
> ⚠️ `run_cs_pipeline.py` face el însuși `git pull --ff-only` la fiecare 30 de minute — codul CS se
> auto-actualizează nesupravegheat, iar un fișier untracked poate bloca tăcut acel pull.
>
> **Restul repo-ului are drift** (≈37 fișiere modificate necommitate, în afara CS). Nu ține de
> sistemul ăsta, dar e bine de știut înainte de un `deploy.sh --apply`.
>
> ⚠️ **`deploy.sh --apply` NU e sigur orb.** Dry-run-ul din 14 sep arată **11 fișiere flat
> divergente**, iar scriptul însuși avertizează că divergența poate fi în orice direcție. Exact așa
> era `richpanel_export.py`: serverul avea fixul notelor duplicate, git-ul nu — un `--apply` l-ar fi
> reînviat pe cel vechi. **Verifică direcția per fișier înainte.**

> **🌍 REPO-UL E PUBLIC — `cata2lin/team-intelligence`, `visibility=public`.**
> Verificat anonim, fără token: PR-ul și fișierele se citesc de oricine.
> **Consecința pentru orice scrii aici:** fără adrese de email personale ale colegilor, fără valori
> de secrete, fără chei. Identitățile agenților se citesc la nevoie cu `uv run rp.py agents`, din
> Richpanel — nu din fișiere versionate.
>
> Primul commit al acestui branch (9 sep) conținea două adrese personale; le-am redactat și am
> rescris branch-ul cu `--force-with-lease`. ⚠️ **Commiturile vechi rămân accesibile pe GitHub după
> SHA** (`8384201`, `757fcb7`) până când GitHub le colectează — ștergerea completă cere fie o cerere
> la GitHub Support, fie trecerea repo-ului pe privat. **Decizie de owner, nedeschisă încă.**
>
> Ce era deja public înainte (nu de la commitul ăsta): IP-ul VPS-ului (23 fișiere), `nocturna954`,
> și un cont de agent.

### 2.2 Mediu

Toate scripturile sunt PEP-723 (dependențe declarate inline) și se rulează cu **`uv run`**.
Nu presupune un `.venv` commitat.

```bash
cd /root/Scripturi/team-intelligence/plugins/gigi/skills/richpanel-export
export PATH=/root/.local/bin:$PATH                       # uv
export RICHPANEL_MCP_TOKEN="$(grep -m1 '^RICHPANEL_MCP_TOKEN=' /root/Scripturi/.env | cut -d= -f2-)"
export CS_MIRROR_DB=/root/Scripturi/data/cs_mirror.db
export KB_DATABASE_URL="$(grep -m1 '^KB_DATABASE_URL=' /root/Scripturi/.env | cut -d= -f2-)"
```

> **⚠️ NU face `. /root/Scripturi/.env`** — o linie din el conține un caracter care strică
> bash-source. Extrage doar variabila de care ai nevoie, cu `grep`+`cut`, exact ca mai sus.
> `run_cs_pipeline.py` face același lucru, dar parsând în Python.

### 2.3 Comenzile

**`rp_sync.py` — captarea firelor din Richpanel** (READ-ONLY)

```bash
uv run rp_sync.py --recent 8 --max-rpm 45          # așa rulează cronul
uv run rp_sync.py --from 2026-08-28 --to 2026-08-31 --max-rpm 45   # --to e EXCLUSIV
uv run rp_sync.py --stats                          # doar raportează oglinda, zero apeluri
uv run rp_sync.py --recent 1 --limit 20            # test scurt
```

| Opțiune | Implicit | Note |
|---|---|---|
| `--from` / `--to` | — | `--to` e **EXCLUSIV**, ca `endDate` din API |
| `--recent N` | — | ultimele N zile, **inclusiv azi** (vezi capcana de mai jos) |
| `--max-rpm` | 30 ziua / 50 noaptea | plafon cereri/minut |
| `--reserve` | 12 | cereri/min lăsate CS-ului live |
| `--statuses` | `OPEN,CLOSED` | `status=all` **nu merge**, cere-le separat |
| `--limit` | — | oprește după N fire trase |

> **⚠️ De ce fereastra e 8, nu 3.** Fereastra filtrează pe **data creării** tichetului, dar
> răspunsurile agenților vin peste zile. În plus `--recent N` numără **inclusiv ziua curentă**, care
> la 02:00 e goală ⇒ „3" însemna de fapt 2 zile utile. Măsurat 2 sep: **28 de răspunsuri pierdute**,
> toate pe tichete create pe 30 aug și răspunse pe 1 sep.
> Costul lărgirii e mic: firele nemodificate se sar — **223 din 601** într-o zi.

**`gmail_sync.py` — captarea cutiilor Google** (READ-ONLY, forțat în cod)

```bash
uv run gmail_sync.py --recent 3                              # toate cutiile CS
uv run gmail_sync.py --mailbox contact@esteban.ro --recent 1
uv run gmail_sync.py --since 2026-08-27 --until 2026-08-30   # --until EXCLUSIV
uv run gmail_sync.py --stats
uv run gmail_sync.py --reconcile --days 7                    # ce e în cutie dar NU în Richpanel
```

Opțiuni utile: `--rediscover` (re-descoperă cutiile), `--all-boxes`, `--full` (ignoră cursorul de
istoric), `--rps 20` (cereri/s), `--workers 4`.

**`parity_check.py` — verificarea** (READ-ONLY)

```bash
uv run parity_check.py --days 7            # așa rulează cronul
uv run parity_check.py --from 2026-08-28 --to 2026-08-31    # --to INCLUSIV aici
uv run parity_check.py --days 7 --json     # pentru dashboard/cron
uv run parity_check.py --selftest          # dovada logicii, ZERO apeluri API
```

Ieșire: cod 0 = trece, non-zero = pică. Scrie și în tabelul `parity_daily`.

**`rp.py` — CLI-ul de operare** (citiri + acțiuni; **dry-run implicit**, cere `--apply`)

```bash
uv run rp.py find --email x@y.ro / --phone 07...
uv run rp.py conv 324855                   # o conversație
uv run rp.py list --status OPEN --channel email --limit 25
uv run rp.py agents | tags | teams
uv run rp.py tag|close|reopen|assign|note|snooze|draft <ref> [--apply]
uv run rp.py keys --refresh                # re-culege cheile publice de widget
uv run rp.py verify --email x@y.ro         # citește fișa clientului din Richpanel
uv run rp.py push --since ... [--apply]    # ⛔ OPRIT — vezi §3.6, NU reporni fără să citești
```

`send_message` e **blocat deliberat în cod** — nu se trimite niciodată mesaj la client.

**`cs_mirror.py`** e biblioteca (schema, regula de îngheț, `MirrorMCP`, `RateLimiter`).
`uv run cs_mirror.py selftest` verifică logica fără API.

### 2.4 Cron (VPS, fus Europe/Berlin = BUC −1h)

```cron
# Oglinda CS Richpanel (captare fir complet + paritate). ZILNIC 03:00 Bucuresti = 02:00 Berlin.
0 2 * * * /usr/bin/flock -n /tmp/cs_mirror.lock /root/Scripturi/run_cs_mirror.sh \
          >> /root/Scripturi/data/cs_mirror.log 2>&1

# Pipeline de imbogatire (taguri + nota) — intraday + nightly
*/30 8-16 * * * … run_cs_pipeline.py --recent 1 --push
0 2 * * *       … run_cs_pipeline.py --recent 3 --push

# OPRIT 2026-08-19 (sourceId): reimprospatare fisa CS din AWBprint
# 30 5,17 * * * … run_rp_push.sh
```

`run_cs_mirror.sh` face, în ordine: `rp_sync --recent 8 --max-rpm 45` → `gmail_sync --recent 3` →
`gmail_sync --reconcile --days 3` → `parity_check --days 7`, și întoarce codul de la paritate.

### 2.5 Deploy

**Corect = git-driven**, nu scp:

```bash
ssh root@84.46.242.181 'bash /root/Scripturi/deploy.sh --apply'
```

`deploy.sh` face `git fetch` + sync al fișierelor flat (cu `.bak`) + `git pull --ff-only`.
`run_cs_pipeline.py` face el însuși `git pull` înainte de fiecare rulare.
**scp-ul manual e cauza divergențelor git↔VPS** — de aceea cele 5 fișiere untracked sunt un risc.

### 2.6 Prima verificare când preiei

```bash
# 1. A rulat cronul azi? Ce a produs?
ssh root@84.46.242.181 'tail -60 /root/Scripturi/data/cs_mirror.log'

# 2. Cât avem în oglindă?
cd …/richpanel-export && uv run rp_sync.py --stats

# 3. Suntem la paritate?
uv run parity_check.py --days 7

# 4. Rulează ceva acum?
ssh root@84.46.242.181 'ps aux | grep -E "rp_sync|gmail_sync|parity" | grep -v grep'
```

> **⚠️ Capcană de diagnostic dovedită:** o rulare pornită *înainte* de o pauzare se termină *după* și
> contaminează testul — primul meu test de regresie a ieșit fals-negativ exact așa. **Verifică `ps`
> întâi.**

---

## 3. Componentele, una câte una

### 3.1 Oglinda de conversații — `rp_sync.py` → `cs_mirror.db` · LIVE

Enumerează tichetele feliat **pe zile**, apoi trage firul complet cu `get_conversation`
(`mode=audit`, `max_messages=50`, `max_message_chars=6000`, `include_private_notes=true`) — singura
combinație care întoarce firul COMPLET. Paginează pe `messages_page.next_cursor` până la capăt
(o conversație a avut **875 de mesaje**, 18 pagini, 0 goluri, 33s).

**Conținut captat (27 aug → 9 sep):**

| | Cantitate |
|---|--:|
| Tichete | 8.052 |
| Mesaje | 16.219 |
| — de la agenți reali | 2.788 |
| — de la boți canned (widget) | 2.636 |
| — note private | 1.978 |
| Atașamente (doar URL) | 1.211 |

Debit măsurat: **44,4 apeluri/min** (plafon 45), **zero 429**.

**Mixul de canale:**

| Canal | Tichete | % |
|---|--:|--:|
| `facebook_feed_comment` | 4.718 | 58,6% |
| `email` | 2.338 | 29,0% |
| `facebook_message` | 506 | 6,3% |
| `messenger` | 167 | 2,1% |
| `email_from_widget` | 137 | 1,7% |
| `instagram_comment` | 116 | 1,4% |
| `instagram_message` | 70 | 0,9% |

> **🧊 REGULA DE ÎNGHEȚ — miezul proiectului.**
> Un text captat nu se suprascrie **niciodată** cu gol, cu `This message was deleted` (tombstone),
> sau cu un text **mai scurt**. Dacă un comentariu dispare, se scrie doar `disappeared_at`.
> Singura excepție voită: dacă ce aveam era chiar un tombstone și acum vine text real, îl luăm.
> Predicatul rulează **în ACELAȘI statement SQL** cu insertul (atomic), nu pe o citire de dinainte.
> E singurul lucru pe care oglinda îl face și pe care nici Richpanel, nici Facebook nu-l mai pot face.

**De ce zilnic, nu săptămânal** (măsurat 31 aug):

| Când tragem | Comentarii deja șterse |
|---|--:|
| în aceeași zi | **14,4%** |
| la 3 zile | **57,1%** |

43 de puncte procentuale de conținut mor între ziua 0 și ziua 3. La o rulare săptămânală, regula de
îngheț n-ar mai avea ce îngheța. Mediana ștergerii: Esteban 1,4h · Reduceri Bune 11,5h · MagDeal 16,2h.

**Logica de sărire (`plan_day`):** un fir se sare doar dacă `updated_at` din oglindă e identic cu cel
proaspăt **ȘI** `msg_total_at_fetch >= comment_count` (poartă de completitudine). Planificarea se face
**înainte** de a scrie sumarele — altfel upsert-ul ar face `updated_at` identic și ar sări tot.

### 3.2 Captarea pe email — `gmail_sync.py` · LIVE

Service account cu **delegare de domeniu** (`GOOGLE_SA_LOOKER_SHEETS_JSON` din KB), **scope unic
`gmail.readonly`**, acordat 31 aug 2026. Codul respinge **în rulare** orice metodă Gmail care nu e de
citire (`assert_read_only()`) și orice scope care nu e readonly (`assert_scopes()`) — faza e doar
captare, iar asta e verificat de mașină, nu promis în README.

> **🔑 Capcana centrală: 23 de adrese = doar 17 cutii fizice.**
> Dovadă dură: dump 7 zile pe `bonhaus.hu` vs `trynocturna.eu` = 36/36 mesaje identice, 100% suprapunere.

| Alias | Cutia-gazdă |
|---|---|
| `contact@bonhaus.hu` · `.hr` · `contact@nocturna.pl` | `contact@trynocturna.eu` |
| `contact@ofertelezilei.ro` | `contact@casaofertelor.ro` |
| `reclamatii@aronagroup.ro` | `facturi@aronagroup.ro` |
| `contact@bonhaus.ro` (inactiv din dec-2025) | `contact@casaofertelor.ro` |
| `contact@bonhaus.sk` (~201 mesaje, inactiv) | `contact@bonhaus.bg` |

> **⚠️ Două feluri de aliasuri, aceeași eroare.** Unele SE impersonează și `users.getProfile()`
> întoarce cutia-gazdă. Altele NU se impersonează și dau **`invalid_grant` — identic cu o adresă
> inexistentă**. Din cauza asta am raportat un gol fals de 203 tichete.
> **Metoda corectă:** caută unde *aterizează* mailul — `deliveredto:<adresa>` / `to:<adresa>` în
> toate cutiile accesibile. Deduplică cutiile prin `getProfile().emailAddress`, altfel tragi aceeași
> cutie de 4 ori.

**Clasificarea cutiilor** (confirmată de owner):

| Cutie | Rol | Tratare |
|---|---|---|
| 13 cutii de magazin | CS | captare completă |
| `contact@rossinails.ro` | **CS pentru ROSSI Nails**, activitate mică ACUM | captare completă. Traficul e ~98% notificări DPD (688/776) — **incidental, NU motiv de excludere**; brandul e real, doar în pauză |
| `facturi@aronagroup.ro` | facturier automat, 2.348 trimise/7z | doar **răspunsurile clienților**; altfel ~70k mesaje de zgomot |
| cutia de lucru a unei colege (nu o public aici) | email de muncă, nu adresă de rol | 92 mesaje, 0 trimise; conținut = notificări Meta/Google. **Nu e CS** |
| 5 × `support@*.customerdesk.io` | infrastructura Richpanel | invizibile pentru noi (30 tichete total) |

**Acoperire: 100% din adresele active.**

**Volume:** ~161 emailuri umane/zi inbound · ~96 răspunsuri/zi pe 13 cutii. **65,4%** din inbox e
automat (Judge.me 23%, curieri 17%). Istoric total în cele 17 cutii: **306.242 mesaje**.
Captate în oglindă până acum: 4.820 mesaje, 321 atașamente.

**Detalii de implementare care contează:**
- Incremental **real** pe `users.history.list` (`gm_state.history_id`, luat ÎNAINTE de enumerare și
  salvat doar dacă trecerea a mers). Cursor prea vechi → 404 → cade automat pe interogarea după dată.
  ⚠️ **404 nu are voie să fie tratat global ca „nimic nou"** — ar pierde tăcut tot.
- `TRASH = 0` pe toate cele 17 cutii ⇒ se parcurg doar `INBOX/SENT/SPAM`.
- Expeditorul cunoscut se verifică **ÎNAINTEA** anteturilor generice — altfel `List-Unsubscribe`
  înghite proveniența și Judge.me apare ca „listă" (măsurat: raporta 0).
- Atașamentele: **doar metadate** (nume/mime/mărime/`attachment_id`), conținutul nu se descarcă.

> **🔥 Richpanel trimite PRIN Gmail, nu prin SES-ul lui.** M-am înșelat de două ori aici: întâi
> „cutia are doar inbound", apoi „agenții răspund din Gmail pe lângă Richpanel". **Ambele false.**
> Răspunsul agentului pleacă prin Gmail ⇒ e în `SENT`, cu Message-ID `@mail.gmail.com` (168/168 pe
> esteban). Cele 18 mesaje SENT din 28 aug → **18/18 regăsite în Richpanel**. Nu există canal paralel.
> Invers, **Gmail e superset strict**: 778/778 și 90/90 tichete Richpanel regăsite în cutie.

**Cheia de legătură:** `rp_ticket.id` **este** Message-ID-ul RFC822 pe **99,74%** din emailuri
(14.202/14.239). Din 5.563 mesaje: 0 fără Message-ID, 0 duplicate.

**`--reconcile` — cele trei trepte de verdict.** Fără distincția asta ai raporta drept „pierderi"
zilele pe care pur și simplu nu le-ai tras din Richpanel:

| verdict | ce înseamnă |
|---|---|
| `lipsa_in_rp` | ziua are în oglindă id-uri **per mesaj** → absența e **DOVEDITĂ** |
| `lipsa_in_rp_probabil` | ziua e acoperită doar de exportul vechi (id-uri de **conversație**) → probabil |
| *(nescris în `gm_gap`)* | ziua n-are NICIO acoperire RP → **nejudecabil**, nu pierdere |

**Ce chiar NU ajunge în Richpanel** — câștigul real al sursei a doua:

| Sursă | Ajunge în RP | Volum |
|---|--:|---|
| `no-reply@dpd.ro` | **0%** | ~21/zi doar pe Esteban, 688/7z pe rossinails — **exact semnalul de livrare** |
| Klaviyo | **0%** | — |
| Spam | **~0%** | ~200/7z pe 13 cutii; 0 din 90 verificate existau în RP |
| `mailer@shopify.com` | 40% | — |

### 3.3 Verificarea de paritate — `parity_check.py` · LIVE

Nu întreabă „a rulat jobul", ci *câte tichete îndeplineau condiția și pe câte s-a produs efectul*
(regula de cutover a echipei). **Numitorul se ia LIVE din Richpanel** — altfel verificăm oglinda cu
oglinda, capcana în care watchdogul de adrese a raportat „mort" un modul viu.

| Metrică | Ce măsoară | Prag |
|---|---|---|
| **M1** | paritate de tichete, per canal și magazin. Raportează **lista de id-uri lipsă**, nu procentul | 100% |
| **M2** | paritate de mesaje: `total_available` la fetch vs rânduri; re-fetch 30 tichete/zi cu diff pe număr și hash | 99,5% |
| **M3** | tichete cu `first_responded_at` non-null dar **0 mesaje de agent uman** în oglindă | ≈0 |
| **M4** | comentarii unde noi avem textul iar RP arată `deleted` — **metrica ce justifică proiectul** | >0, altfel se oprește componenta |
| **M5** | taxonomia golului invers: pagină fără token / story nedescoperit / șters înainte de poll / **necunoscut** ← alarma reală | (d) = 0 |
| **M7** | cote: `x-app-usage` înainte/după, `x-ratelimit-remaining` minim, număr de 429 | remaining ≥ 12 |

> **De ce M3 contează separat de M1.** Pe 2 sep, M1 arăta **100% pe toate zilele și toate canalele**.
> M3 a găsit **28 de răspunsuri de agent lipsă**. Un singur indicator ar fi declarat „paritate
> perfectă". Fereastra e acum de 8 zile, zilele pierdute au fost recuperate (1.652 tichete, 1.161 fire,
> 458 mesaje de agent, zero 429), iar la rularea din 9 sep M3 nu mai găsește nicio zi pierdută.

**Alertare:** email **doar** pe roșu sau pe drift NOU (tiparul `reconcile_sources.py`).
**Confirmare umană lunară:** 5 tichete la întâmplare, deschise în UI-ul Richpanel de un om din CS,
comparate cu oglinda. Nu doar din DB — lecția din incidentul OH.

**Gardă de read-only, în cod, nu în intenție:**
1. Wrapper peste `rp.MCP.call` cu **allowlist explicit**: `get_conversation`, `list_conversations`,
   `list_users`, `list_tags`, `list_teams`, `search_conversations_by_customer`, `query_analytics`.
   Orice altceva → excepție.
2. Client HTTP Meta care refuză orice metodă ≠ GET.
3. **Tokenurile nu ating discul** — se iau la runtime, rămân în memoria procesului.

### 3.4 Pipeline-ul de îmbogățire — `run_cs_pipeline.py` → `metrics.richpanel_tickets` · LIVE

Mai vechi decât oglinda și cu **alt rol**: categorisește tichetele pe reguli, le leagă de clientul
Shopify și de comandă, și scrie înapoi în Richpanel **taguri** plus **o singură notă internă**.
Zero LLM — pasul cu Gemini (`--llm`) e singurul care costă și **nu e în niciun cron**.

- **251.407 tichete**, 15 oct 2024 → azi.
- Pași (~2 min): PULL → CATEGORIZE → LINK → DEEP → SENTIMENT → COMMENT → QUALITY → SYNC→PG → `--push`.
- **Categorii:** `livrare_wismo` · `retur` · `schimb_swap` · `anulare` · `modificare_comanda` ·
  `problema_produs` · `refuz_livrare` · `plata_factura` · `presale_intrebare` · `comanda_noua` ·
  `recenzie_feedback` · `spam_automat` · `comentariu_social` · `altele`.
- **Nota se scrie O SINGURĂ DATĂ** pe conversație (`--note-mode once`, implicit). E un **instantaneu**
  de la primul contact, nu un flux — statusul din ea îmbătrânește. Statusul live se ia din
  `gigi:cs-360` / `gigi:xconnector links`.
- Două gărzi contra dublurilor: coloana `applied_note_sig` în SQLite **și** o întrebare live în
  Richpanel (`get_conversation` + `include_private_notes`) înainte de fiecare scriere.

**Linking — regula corectă:** comanda se alege după **magazinul TICHETULUI** (domeniul din `to`) +
**data tichetului** (cea mai apropiată comandă). Înainte lua `matched[0]` + magazinul majoritar și
greșea la clienții multi-magazin. `nocturna.ro`/`trynocturna` = **inbox partajat, nu magazin**.

**⚠️ Notele NU se pot șterge prin MCP** — doar manual din UI.

### 3.5 Webhook-ul FB/IG — `fb-comment-webhook/` · DRAFT

Worker Cloudflare care **înlocuiește ReplyZen**. La fiecare comentariu nou: ascunde răul (live,
reversibil), trimite la aprobare răspunsurile către lead-uri și pozitivi, escaladează plângerile la CS.
Scrie evenimentul brut în D1 (`cs-raw-events`) **înainte de orice filtrare**, ca să nu pierdem materie
primă din cauza unei erori de clasificare.

- Callback activ: `https://fb-comment-webhook.arona-ops.workers.dev/` pe `page/feed` ȘI
  `instagram/comments`; app „Api export" `1268707461439970`.
- **29 din 29 de pagini abonate** pe `feed`.
- `REPLY_MODE=draft` — dar **⚠️ ascunderea e LIVE în ambele moduri**.
- HMAC validat cu **`META_APIEXPORT_APP_SECRET`** (NU `META_APP_SECRET` — ăla e alt app).
- Deploy prin endpointul **`/content`**, care păstrează bindings/secrete (PUT pe script le-ar șterge).
- Tokenul Cloudflare din seif e **read-only** pe Workers; cel cu drepturi e
  **`CLOUDFLARE_API_TOKEN_WORKERS`**.

> **De ce webhook și nu interogare.** E singura cale care poartă **identitatea comentatorului**:
> prin API-ul de citire, Facebook a returnat autorul pentru **0 din 24.110** comentarii. Și e singura
> care ajunge **înaintea moderării** — orice interogare ajunge după ce ReplyZen a șters deja.

**Reparat 31 aug:** `maps()` chema `/me/accounts` la FIECARE eveniment (cel mai scump endpoint ca
și cotă). La saturare `j.data` lipsea, `(j.data||[])` producea harta **goală**, fiecare comentariu era
sărit tăcut (`if(!pt)continue`) iar Workerul răspundea `200 EVENT_RECEIVED`. Fix: cache 5 min + la
eroare ultima hartă bună, iar dacă n-are niciuna **aruncă** (500 ⇒ Meta reia livrarea).
Observability a fost **activat** (era `{}`, de-aia nu exista istoric de loguri).

**Capcane de măsurare (m-au păcălit pe rând):**
1. **KV e eventual-consistent** — un element apare după ~8s. Cu `sleep(4)` am conchis greșit „live".
   Așteaptă ≥30s înainte de a declara ceva.
2. **Tail-ul își vede propriul control** — clasifică după `user-agent`; Facebook nu trimite
   `python-requests`. Prima rulare a raportat „Meta livrează" numărând propriul meu eveniment.
3. **Deploy-ul invalidează sesiunile de tail** — repornește ascultarea DUPĂ deploy.
4. Diferența de bytes cod deployat vs git poate fi doar **învelișul multipart** al răspunsului API.
5. **Coada goală ≠ „nu ajung evenimente"** — majoritatea comentariilor se clasifică `keep`/`hide`, iar
   alea nu intră niciodată în coada de aprobare. Livrare confirmată: 3 evenimente reale cu
   `user-agent: facebookexternalua` în 40 min de ascultare.
6. Scanarea `/{page}/feed` arată 0 comentarii recente pentru că vede **doar postările organice**;
   evenimentele vin de la reclame (dark posts).

> ### 🔴 MĂSURAT 14 sep: webhook-ul NU captează nimic de pe Facebook
>
> Șapte zile de loguri Workers, cu păstrare 100%:
>
> ```
> invocări totale       9
> object=page           0     ← comentarii Facebook
> object=instagram      6     ← DM-uri
> ```
>
> **Zero.** În aceleași 7 zile Richpanel a primit ~3.000 de comentarii Facebook. Iar D1
> (`cs-raw-events`) are **6 rânduri în total**, toate `instagram/messaging`, niciunul Facebook.
>
> **Nu e greșeală de configurare** — am verificat tot lanțul: abonamentul app-ului `page/feed` e
> **activ**, **29 din 29 de pagini** sunt abonate pe `feed`, `rawSave` tratează corect ambele forme
> de payload și scrie înainte de orice filtrare, iar workerul e viu (cele 6 evenimente IG au trecut
> prin el).
>
> **Cauza rădăcină:** app-ul „Api export" (`1268707461439970`) are aprobate exact **două** permisiuni:
>
> ```
> email           live
> public_profile  live
> ```
>
> Îi lipsesc `pages_read_engagement`, `pages_manage_engagement`, `pages_read_user_content`.
> Fără ele Meta **nu livrează** evenimente `feed` pentru comentariile oamenilor reali — exact
> același zid ca la DM-urile Instagram (§5): **Standard Access**, date doar pentru cine are rol în app.
>
> ⇒ **ReplyZen și Instagram sunt ACELAȘI blocaj**, nu două. Ambele se deschid cu o singură
> submisie de App Review pe app-ul „Api export", pentru: `pages_read_engagement`,
> `pages_manage_engagement`, `instagram_manage_comments`, `instagram_manage_messages`.
>
> ⚠️ Deci criteriul din README — „3–5 zile de rulare în paralel" — **nu se poate îndeplini azi**.
> Validarea ar fi picat. **ReplyZen rămâne pornit**, dar nu din prudență: din necesitate.

### 3.6 Puntea AWBprint → fișa clientului — `rp.py push` · ⛔ OPRIT

Custom Connector-ul Richpanel e un **canal de scriere gratuit** în fișa CS — protocol nedocumentat,
decodat empiric din JS-ul widgetului (`api.richpanel.com/v2/j/<API_KEY>?version=2.0.0`):

```
POST https://api.richpanel.com/v3/t
body: {"h": base64(json(data))}
data = {event, properties, userProperties:{uid,email,name,phone},
        appClientId:<API_KEY>, did, sid, time:{sentAt}, version:"2.0.0.js", eventId, context}
```

Evenimente: `identify`, `order`, `page_view`, + orice custom. **Fără auth — cheia E autentificarea.**

**Schema `order` — numele de câmp care FUNCȚIONEAZĂ** (decodate cu valori distincte per candidat):

| Câmp în fișa CS | Trimiți | Note |
|---|---|---|
| `lastOrderNumber` + `orderIds` | `order_name` | ⚠️ NU `order_number`/`orderNumber`/`name` |
| `lastOrderAmount` + `ltv` + `aov` | `amount` | ⚠️ NU `total_price`/`totalPrice` |
| `lastOrderDeliveryStatus` | `status` | statusul REAL de livrare; valoare **ENGLEZĂ** (`fulfilled`), nu tradusă |
| `lastOrderStatusUrl` | `status_url` | ⭐ singurul link apăsabil. **NU pune tracking aici** — vezi erata 03 |
| care e „ultima comandă" | `created_at` (ms epoch) | ⚠️ **OBLIGATORIU**. RP alege lastOrder după DATA trimisă, nu după ordinea de push |
| `lastOrderTrackingNumber/Company/Url` | `fulfillments:[{tracking_number,tracking_company,tracking_url}]` | ⚠️ DOAR imbricat; forma plată e ignorată tăcut |
| `lastOrderShippingAddress` | `shipping_address` | ⚠️ **STRING**; obiect → `"[object Object]"` |
| `lastOrderId` | `id` | vezi regula de mai jos |

N-au prins: `financial_status`, `fulfillment_status`, `currency`, `line_items`.

**Cele două reguli care evită dublarea:**
1. `id` = **partea numerică** a numărului (`NUBRA11203` → `11203`) — exact ce scrie integrarea
   Shopify. Cu numărul complet ⇒ **comandă NOUĂ** ⇒ `ordersCount`/`ltv` **dublate**.
2. Push-ul e **idempotent** pe același `id` (re-trimis = update).

**🔑 Cheile tuturor magazinelor sunt PUBLICE** — stau în query string-ul scriptului de widget din
pagina publică: `cdn.richpanel.com/js/richpanel_shopify_script.js?appClientId=<KEY>&tenantId=…`.
`rp.py keys --refresh` culege 20/25 automat (acoperă toate magazinele active).
⚠️ Unele teme pun tag-ul într-un blob JSON ⇒ URL escapat (`\/`, `&amp;`) — **normalizează
escape-urile înainte de regex**, altfel 12 magazine par „fără widget".
⚠️ Numele magazinului în AWBprint ≠ domeniul din widget. Potrivirea are **3 nivele**: exact → fără
cratime → trunchi fără TLD **dar numai dacă trunchiul e unic** (altfel bonhaus.cz/.pl/.bg colapsează
într-o singură cheie). Fără nivelul 3, cele 8.781 de comenzi Nubra ar fi fost sărite tăcut.

**Golul e sistematic:** integrarea Shopify lasă `lastOrderTrackingNumber/Company` **goale** și statusul
blocat pe „fulfilled" chiar și pe comenzi livrate de DPD.

> **⛔ DE CE E OPRIT (19 aug).** Richpanel construiește linkul „deschide comanda în Shopify admin" din
> câmpul **`sourceId`** al clientului. Evenimentul `order` prin connector face din comanda noastră
> „ultima comandă" **fără `sourceId`** ⇒ linkul cade pe `/orders/` fără id. **`sourceId` NU se poate
> seta prin connector** — testat cu 8 nume de candidați cu valori distincte (`source_id`, `sourceId`,
> `shopify_order_id`, `shopifyOrderId`, `external_id`, `order_id`, `gid`, `admin_graphql_api_id`):
> niciunul nu-l populează. Îl scrie EXCLUSIV integrarea Shopify, și se reface doar parțial.
> **Raport cost/beneficiu:** câștigi AWB+status în sidebar, pierzi saltul în comandă — pe care agenții
> îl folosesc constant. AWB-ul ajunge oricum la agent prin **nota internă** scrisă de pipeline, care
> nu atinge fișa clientului. **Nu reporni fără să rezolvi `sourceId`.**

**Capcane ale connectorului:**
1. **`200` ≠ „a intrat"** — ingestul e un firehose Kinesis care acceptă ORICE (testat cu `appClientId`
   inventat → tot 200 + SequenceNumber). Validarea e în aval. Verifică prin MCP
   (`get_customer_by_email_or_phone`).
2. **Latență ~5 min** până apare fișa. Nu e potrivit pentru citire sincronă.
3. **Merge pe email/uid** → un push greșit poate SUPRASCRIE datele din integrarea Shopify.
4. **Nu există ștergere.** Profile de test rămase în producție (inofensive):
   `arona-api-probe@`, `arona-probe-a/b/c/d@example.com`.
5. **AWB anulat — nu filtra pe `tracking_number IS NOT NULL`.** Când AWB-ul e anulat, AWBprint îi
   șterge tracking-ul, deci comanda ar ieși din lot și în fișa CS ar rămâne **un AWB mort pe care
   agentul îl citește ca valid**. Trimite-o fără `fulfillments` ⇒ RP golește corect.

---

## 4. Ce am găsit parcurgând Richpanel

Logat ca `TENANT_ADMIN` (contul Cristina Sava), 31 aug, fără nicio modificare.
**Concluzia scurtă: helpdesk-ul e mult mai simplu decât pare, iar jumătate din ce e configurat nu se
folosește.**

### 4.1 Cele patru descoperiri care schimbă calculul

| Ce am găsit | Cifra | Ce înseamnă |
|---|--:|---|
| **Brandurile sunt plafonate de plan** | **10** | Configurate 10 la ~20 de magazine — și două („Call Center", „Reclamatii ARONA") nici nu sunt branduri, sunt funcții. Brandul e cheia pentru widget separat (limbă, culori, mesaj), **SLA** și **program de lucru**. Cu 10 branduri, jumătate din magazine n-au niciunul. ⇒ **Cerință dură: branduri NELIMITATE, unul per magazin.** |
| **Macro-urile n-au motor** | **43** | Toate au o singură acțiune: `ADD_REPLY_TEXT`. Text fix, fără condiții, fără variabile dinamice, fără ramificații. Toate pe „All Brands" (deși câmpul per-brand există). Acoperă retur (proceduri de 1.100–1.200 caractere), schimb, produs greșit, întârziere, anulare, rambursare, client agresiv, ramburs, indisponibil, sigiliu parfumuri. ⇒ **De replicat e o listă de texte, nu un motor.** |
| **Automatizările sunt doar rutare** | **3 din 17** | Active: CZ Marta→Martina K · Lisa→Monica Dan · Raluca→Cristina Sava (declanșator identic: „A customer starts a new conversation"). Pauzate: WISMO ×3, anulare ×2, schimbare adresă ×2, produse lipsă/deteriorate, sărbători, problemă tehnică ×3, autoresponder. **Toate răspunsurile automate au fost încercate și oprite.** |
| **Permisiunile sunt grosiere** | **4** | *Download reports* · *Trashing Conversation* · *Editing Views* · **Allow refunds**. **Nu există permisiuni pe brand** — un agent vede tot. |

Branduri configurate: Esteban.ro · Belasil · Casa Ofertelor · Apreciat · Gento · Carpetto ·
Nocturna.bg · Bonhaus BG · Call Center · Reclamatii ARONA.

Macro-urile extrase integral din API-ul intern (`/tenant/macros`) → `data/rp_macros_clean.json`.

### 4.2 Ecranul de tichet — ce trebuie neapărat replicat

Marcaj: **[E]** esențial · **[U]** util · **[D]** decor.

- **[E] Postarea-sursă afișată inline.** La comentariile de pe reclame se vede CREATIVUL
  („PACHET: 10L DETERGENT… 99 Lei", 12 reacții) și **care** comentariu e selectat. Fără asta agentul
  răspunde orb. Noi avem legătura prin `story_id`.
- **[E] Eticheta de canal pe fiecare mesaj** + „Replying to X via Facebook Comment".
- **[E] Notă privată vs canal public**, ca taburi separate la compunere.
- **[E] Send** și **Send and Close**. Închiderea la trimitere e fluxul CS real
  (**REPLY = CLOSE**, inclusiv escaladările ANPC/OPC; RP redeschide automat dacă clientul scrie iar).
- **[E] Other Conversations** — istoricul clientului cu noi: cine a răspuns, când, cu ce status.
- **[E] Câmpuri client** editabile: Email, Nume, Prenume, Telefon, Last Seen + 15 ascunse.
- **[U] Traducere RO ↔ EN** într-un click. Contează pentru Bonhaus CZ/PL/BG și Nocturna BG.
- **[U] Agent Forms** — „Formular De Retur" cu **IBAN**, Nume complet, Motivul anulării.
- **[U] Knowledge Base** în bara laterală, pe limbi, cu articole inserabile.
- **[U] AI (Sidekick)** — Suggest a reply · Summarize · Prompt liber. Avem echivalentul cu date mai bune.
- **[U]** Jurnal de activitate · navigare Prev/Next · `Block` · `Notes`.
- **[D]** „Edit widgets" (rearanjarea panourilor) · Subscriptions (nefolosit) · Tasks (0 folosite).

> **⚠️ Bara de comenzi a Richpanel e GREȘITĂ, nu doar săracă.**
> Pe tichetul verificat scria **„No orders found"** deși clientul cumpărase (2 bidoane, spune el).
> Se leagă de golul deja măsurat: tracking gol și status blocat pe „fulfilled" chiar și pe comenzi
> livrate de DPD. **Aici stăm mai bine decât helpdesk-ul, nu mai prost** — AWBprint e sursa
> autoritativă de livrare, iar `gigi:cs-360` face deja profilul complet.

### 4.3 Inboxul — modelul de lucru real

Vizualizările sunt organizate **pe agent → brand**, exact modelul pe care sistemul propriu trebuie
să-l reproducă. Specializare clară: parfumuri / deals / internațional.

| Agent | Branduri | Volum tichete (iun–aug) |
|---|---|--:|
| Cristina Sava | Esteban · George Talent · Nubra · Grandia | 2.282 / 617 / 474 / 411 |
| Diana Popa | Ofertele Zilei · Reduceri Bune · MagDeal · Casa Ofertelor | 1.106 / 501 / 403 / 217 |
| Martina K | Bonhaus CZ · Bonhaus PL · Belasil | 276 / 233 / 136 |

- Status: **Open 1,5k** · Snoozed 0 · **Closed 285,8k** · **Trash 10,56k**.
- Vizualizări implicite: My Inbox, Unassigned, Drafts, Mentions, Outbox, All.
- Vizualizări partajate: `AI`, `Reclamaţii DPD`, `Awaiting Reply` (1,45k), `Nocturna BG`.
- Contoare pe canal: Emails 764 · Facebook 281 · Instagram 16 · restul 0.

> **⚠️ Identitatea agenților.** Numele afișat **nu** e persoana reală: „Cristina Sava" =
> un cont Gmail personal cu alt nume, iar „Monica Dan" la fel. Adresele reale se citesc cu
> `uv run rp.py agents` — nu le pun în documentație, fiindcă repo-ul e public.
> **Orice atribuire pe agent se face după EMAIL**, niciodată după numele din interfață.

**Users:** 6 — Mariana Popescu · Cristina Sava · Diana Popa · Irina Oprea *(Pending Invitation)* ·
Martina K · Monica Dan. Plătim **4 locuri**.
⚠️ `list_users` întoarce 6, nu 7 — un agent lipsește sau e dezactivat; atribuirea pe istoric poate
avea găuri. **Nu știu care.**

### 4.4 Restul ecranelor

- **Assignment** — Manually / Balanced / Round Robin (recomandat de ei). Opțiuni: *Re-assign on reply*,
  *Assign on First Message*, *Notify all agents*. **Agent Capacity**: plafon de conversații per agent,
  SEPARAT pe canale „chat-like" (Live Chat, FB Message, IG DM, WhatsApp) vs „ticket channels" (Email,
  FB Comment, IG Comment, SMS, call). Canalele se pot muta între categorii (Live Chat nu).
  ⇒ De replicat: rutare brand→agent + plafon. Restul e decor la 3 agenți.
- **Customer Fields — 73.** Includ exact schema decodată empiric prin connector (`lastOrderAmount`,
  `lastOrderTaxAmount`, `lastOrderShippingAmount`, `appClientIdList`, `email`, `firstName`…).
  ⚠️ Câmpuri **duplicate făcute de om**: „Telefon"/`telefon` și „Telefon"/`nrtelefon`.
- **Agent Forms — unul singur**, „Formular De Retur". Puțin folosit, dar e **singurul mecanism de
  captare structurată** din tot helpdesk-ul.
- **SLAs & Business Hours** — se configurează **per brand** ⇒ limitate de aceleași 10 branduri.
- **Canale** — 14 configurate, active doar Email + FB/IG.
- **Billing** — Pro 89 $ × 4 seats, contract lunar, factura 8 sep 2026.
- Neinventariate încă (există, de parcurs la nevoie): Task Bots · Spam Filtering Bots · Follow Up On
  Snooze · Agent Shifts · Audit Logs · Satisfaction Survey · Conversation Fields · Custom Objects ·
  CSV Import · Helpdesk Importer.

### 4.5 Cele trei căi de API (și de ce a treia lipsește)

`Settings → Integrations → API Keys` are buton **Create Key** și un formular funcțional, dar
`POST ws-prod.richpanel.com/tenant/manage-token` răspunde **403**:
`API access is not enabled on your account. Please reach out to Richpanel Support Team`.
**Blocaj de BACKEND, nu de interfață.** Developer API Access = doar plan Enterprise.
Contul E admin (`custom:role: TENANT_ADMIN`) — deci nu e problemă de permisiuni interne.

| Cale | Ce dă | Limite |
|---|---|---|
| **MCP JSON-RPC** — `mcp.richpanel.com/mcp`, `RICHPANEL_MCP_TOKEN` | 21 unelte: conversații, fir complet, note private, taguri, useri, analytics | **60 req/min**, partajate cu CS-ul live. **NU scrie custom fields** — doar taguri, note, draft, priority, subject, status, assign, snooze |
| **Custom Connector** — `api.richpanel.com/v3/t` | scriere în fișa clientului (comandă, AWB, status) | one-way, fără citire, **nu setează `sourceId`** |
| **API intern** — `ws-prod.richpanel.com`, Bearer JWT Cognito + `X-Tenant: nocturna954` | citiri bogate: `/tenant/macros`, `/tenant/brand`, `/tenant/teams`, `/users`, `/tenant/conversation-tag`, `/tenant/views`, `/channel/getall` | JWT expiră în ~24h, se ia din sesiunea browserului. CORS blochează `fetch` din pagină ⇒ se citește prin CDP |

**Cum se obține tokenul MCP:** e creat de fluxul OAuth al MCP-ului („MCP Token (Claude Code)" în
pagina API Keys), stocat de Claude Code în keychain macOS → item `Claude Code-credentials` → JSON →
`mcpOAuth/richpanel|*/accessToken`. **16 caractere, fără expirare, fără refresh.** Salvat în KB ca
`RICHPANEL_MCP_TOKEN`. ⚠️ Dacă cineva îl șterge din pagina Richpanel API Keys, moare calea scriptată
— regenerare prin re-auth MCP (`/mcp`).

### 4.6 Capcane ale API-ului Richpanel (învățate empiric)

- **`endDate` e EXCLUSIV** — pentru o singură zi cere `[zi, zi+1)`. `start=end` → 0 rezultate.
- `sortKey=createdAt` + `order=asc` → **0 rezultate**. Folosește sortul default.
- `status=all` **nu merge** — cere separat `open` + `closed`.
- Max **50/pagină**; paginează cât `len == 50`; pauză ~0,4s între pagini.
- **Offsetul se plafonează la pagina 50** (~2.500 înregistrări/filtru), iar **`has_more` minte** — a
  raportat `False` cu 14 pagini rămase (744 rânduri). ⇒ **feliere pe ZILE**, paginare până când
  `returned < per_page`, **niciodată pe `has_more`**.
- **Emoji din Facebook au surrogates rupte** → sanitizează stringurile înainte de SQLite.
- **`list_tags` fără `query` întoarce DOAR primele 25** dintr-un dicționar de ~1.900 ⇒ tagurile uzuale
  păreau inexistente și `create_tag` umplea workspace-ul de dubluri urât-normalizate.
  `tag_id()` caută întâi pe nume (`query=<tag>`) și creează doar dacă chiar lipsește.
- ⚠️ `sleep(1.1)` naiv **nu** dă 55/min — cu latența p50 de 0,34–0,42s a dat **37,1/min măsurat**.
  Trebuie pacer pe deadline sau pe header (`Retry-After`, `x-ratelimit-remaining`).

---

## 5. Limite măsurate — ce nu se poate și de ce

| Gol | Mărime măsurată | Cauză | Se repară? |
|---|--:|---|---|
| Identitatea comentatorului prin API | **0 / 24.110** (și 0/8.090 la re-test) | FB omite tăcut `from` pe comentariile persoanelor fizice | **Doar prin webhook** — deja pornit |
| Comentarii deja șterse | **0 / 1.429** | ștergere la sursă; `GET /{comment_id}` → `(#100) does not exist` | Nu. Se previne doar prin captură rapidă |
| Backfill istoric comentarii FB | **20–33%/lună** | ștergerile s-au produs deja | Nu — și ar fi sistematic **biasat**: lipsesc tocmai comentariile negative/spam |
| DM Instagram | **0 fire** pe toate cele 17 conturi | Permisiunea e *granted*, dar la **Standard Access**: `subcode 2534048` — Meta întoarce date doar pentru cine are rol în app (2 admini) | Da: App Review pentru Advanced Access. **Amânat de owner** |
| Interogare pentru comentarii IG | **13,2% recall** (152/1149) | 81% din comentarii nu mai există pe IG când ajungem noi; **ReplyZen** le șterge înainte | Nu. Captarea IG **exclusiv la webhook** |
| Pagini fără token | **12% din social** (19.378 tichete istoric); cea mai mare: `364899953373966` = 7.723 tichete | eroarea `(#10)` cu **toate** cele 3 tokenuri ⇒ nu e problemă de token | **Administrativ:** acordarea paginilor către system user `122121862052787611` în Business Manager |
| Istoricul trimis de Richpanel | **55,3% din mesaje** (18.965) | a plecat prin infrastructura lui (SES/`customerdesk.io`) | Doar din Richpanel, cât timp abonamentul e activ. **Nu opri imediat după cutover** |
| Widget chat (`messenger` + `email_from_widget`) | 2.524 tichete (5,1%) | nu există în Graph sub nicio formă, fără Message-ID | Nu. Doar Richpanel |
| `aircall` | 7.888 tichete istoric | `VoiceComment` fără text; 0 mesaje de agent în 9/9 | Nu (ar cere transcriere audio, alt proiect) |

### 5.1 Instagram — ce SE poate (calea exactă)

```
GET /me/adaccounts                                          → 39 conturi
GET /act_{id}/adcreatives?fields=effective_instagram_media_id
GET /{ig_media_id}/comments?fields=id,text,username,timestamp,hidden,replies{...}   ← PAGE token
```

Câmpul cheie e **`effective_instagram_media_id`** — nu trebuie `/{ig-id}/media`, nu trebuie rezolvat
permalink-ul. **0 erori pe 715+ media.** Media sunt `media_product_type=AD` (dark posts).
Legătura cu tichetele e dovedită: `ticket.id = {ig_media_id}_{comment_id}`, iar 97 din 114 media (85%)
apar ca `effective_instagram_media_id` ⇒ tichetele `instagram_comment` **sunt** comentarii la reclame.
Cotă: **~gratis** (466+ apeluri page-scoped, 0 erori; Meta nici nu returnează `x-app-usage` pe ele).

**Identitate: 29%** (145/506 au `username`). Unde vine, e **corect** (34/34 identice cu Richpanel).
Per pagină: GT 100% · MagDeal 100% · Esteban 35% · **Apreciat 3%**. Cauza: username-ul vine doar când
ownerul media e un cont IG legat de o pagină la care avem token ⇒ **fixabil administrativ** (adaugă
acele conturi IG la system userul „The Wow Grid").
Contrast: pe Facebook identitatea e **0 din 24.110** — la IG e prima dată când o avem pe reclame.

**Test instant care confirmă diagnosticul DM înainte de App Review:** adaugi IGSID-ul unui coleg ca
Tester în app, el trimite un DM, apoi `GET /{page}/conversations?platform=instagram` — dacă apare
DOAR acel thread, diagnosticul e confirmat.
⚠️ **De ce e eșec TĂCUT:** endpointul de LISTĂ aplică filtrul și dă `200 {"data":[]}`; eroarea onestă
apare doar când numești un user anume (`&user_id=`).

### 5.2 Facebook — ce SE poate

- **Comentarii:** `GET /{page_id}_{post_id}/comments?filter=stream&order=reverse_chronological` **cu
  page access token**. Cu system token direct pe `/{comment_id}` → `(#200) Missing Permissions`.
  Acoperire, cu numitorul corect (excluzând tichetele unde RP arată `deleted`): **1.218/1.607 = 75,8%**.
- **Descoperirea postărilor = reuniunea a TREI surse.** `/act_X/adcreatives` dă de **7–11×** mai multe
  story-uri decât `/ads` (GT 1.252 vs 109), iar lista derivată din id-urile tichetelor RP
  (`{page}_{post}_{post}_{comment}`, consistentă 17.387/17.387) aduce **+31%/+34%** peste sweep-ul pe
  ad creatives. **Niciuna singură nu e suficientă.**
- **DM-uri:** `GET /{page}/conversations` merge pe **20/20** pagini testate. **Fără fereastră de
  retenție** (istoric până în 2022-09), fără trunchiere de fir (12/12 cu `message_count == fetched`).
  Test de paritate cu RP pe aceeași conversație: **17 vs 17 mesaje**, potrivire 1-la-1 pe timestamp și
  text, inclusiv atașamentul audio. Filtru nativ pe client: `?user_id={PSID}`, unde PSID = `from.id`
  din tichetul RP. Debit: 800 conversații + 4.704 mesaje în 44,7s.
- Câmpuri pe care Richpanel **nu** le are: `is_hidden`, `permalink_url`, `parent{id}`, `like_count`,
  `attachment.media.image.src`, plus legătura cu campania/creativul prin `story_id`.

### 5.3 Cotele — constrângerea de fiecare zi

- **Richpanel: 60 cereri/minut**, partajate cu CS-ul live. Am luat 429 la primul apel propriu, cu zero
  apeluri proprii în fereastră. Headerele **există** (`Retry-After: 60`, `x-ratelimit-limit/remaining/
  reset`) — raportul inițial spunea greșit că nu există. Rulăm cu rezervă ≥12/min, 30/min ziua,
  45–50/min noaptea.
- **Meta: cota e per APLICAȚIE** (`1268707461439970`) și stă cronic la **100–230%** din `x-app-usage`,
  saturată de pipeline-ul de reclame. **Apelurile eșuate incrementează contorul**; deblocare ~75 min.
  Folosim **doar** endpointuri page-scoped, unde costul e ≈0: 563 postări citite cu `x-app-usage`
  neschimbat (147→147); 8.090 comentarii în 18,9s (428/s) — în același moment în care `/me/accounts`
  (user-scoped) returna `(#4)`. **Throttle-ul e selectiv pe tip de endpoint, nu global.**
- **Interzis**, cu buget măsurat: `/{comment_id}` (~330 apeluri/oră disponibile, +61 puncte de cotă la
  200 apeluri) și **Batch API** (50 subrequests = 50 apeluri; 85 din 200 au picat).

### 5.4 Ce contează la dimensionare

- Doar **36,9%** din tichete (97.955/265.195) au ≥2 mesaje. Restul nu conțin niciun răspuns de agent.
- **45%** din mesajele marcate „agent" au `author.id = "operator"` = automatisme canned ale widgetului
  („Sunteți deja client?"). **Fără filtrarea lor, orice statistică despre „ce răspunde CS" e poluată
  masiv.** Boturile NU se aruncă — se marchează (`is_operator_bot`).
- Emailul **uman** e **18,4%** din CS, nu 28,8% — din 14.239 emailuri, **5.144 sunt automate**
  (3.973 Judge.me, 744 Shopify, 427 klaviyo/curieri).
- Câmpul `is_ai` e **mort** în workspace (0/87). Nu se folosește ca detector de bot.
- Istoric complet restrâns la tichetele cu ≥2 mesaje: **~31 h** de rulare la 30/min. Varianta completă
  (265k): ~85 h ≈ 3,5 zile, **fără conținut nou**. Recomandare: doar varianta de 31 h, în weekend.
- Stocare: ~2,5–3 KB JSON/conversație ⇒ **0,8–1,2 GB** brut pentru tot istoricul.
  Atașamentele: doar URL-uri. **Nu știu dacă URL-urile S3 expiră** — dacă expiră, descărcarea devine
  obligatorie și costul de stocare crește cu un ordin de mărime. Test: 10 URL-uri, unul din 2024.

---

## 6. Defecte deschise

### 6.1 Tichete captate FĂRĂ fir — **cauză găsită, fix cunoscut, NEAPLICAT**

**Simptom:** rândul de tichet se scrie, firul rămâne gol (`msg_total_at_fetch = 0`, zero rânduri în
`rp_message`), iar la fiecare rulare se reîncearcă și eșuează la fel. Tichetul **trece de M1** și apare
în oglindă — **doar M3 îl vede**.

**Mărime:** **2 tichete din 8.052 (0,02%)**:

| Tichet | Canal | Id |
|---|---|---|
| `#322801` | `email` | `<CAMxp_TNpjBKGq7dQqkVyMvuXgrFNp8xdH8BwVM=tVaS…` (Message-ID RFC822) |
| `#324855` | `facebook_message` | `m_jTGUpyrNk1NSBgdELBNv9M7nJWYQgIuqaecQnydpo18…` (base64url) |

**Cauza, reprodusă 9 sep:** MCP-ul respinge id-ul și răspunde cu **text**, nu cu obiect:

```
mcp.call("get_conversation", {"conversation_id": "m_jTGUpyrNk…"})
  → 'Error: id contains invalid characters.'        ← un STRING de 38 de caractere
```

Apoi `d.get("messages")` pe un string aruncă `AttributeError: 'str' object has no attribute 'get'`,
prins per-tichet în `sync()` și jurnalizat ca eroare misterioasă. **Mesajul real al serverului era
acolo tot timpul, dar nu ajungea niciodată în jurnal.**

> **⚠️ CORECTAT 14 sep — teoria caracterelor e INFIRMATĂ, și era doar 1 din 8 erori.**
>
> Scrisesem aici că validatorul respinge id-urile cu `<`, `>`, `=`, `+`, `-`, `_`. **Fals**: printre
> id-urile ACCEPTATE, 618 conțin `=`, 648 conțin `+`, iar 579 din cele 770 `m_*` conțin `-`.
> Regula reală a validatorului rămâne **necunoscută**, iar cazurile sunt doar 2 incidente
> independente (ambele de la același client, același fir), nu 3.
>
> Mai important: din cele **8 erori** ale ultimei rulări, **doar 1** e clasa asta. Celelalte **7**
> sunt o clasă nouă, în care **serverul răspunde corect și crapă clientul nostru** — vezi §6.1b.

**Fix verificat:** același tichet, cerut după **număr**, se întoarce complet:

```
mcp.call("get_conversation", {"conversation_number": 324855})
  → dict, 2 mesaje: clientul + răspunsul Monicăi Dan
```

**Ce trebuie făcut (două schimbări în `cs_mirror.py` / `rp_sync.py`):**
1. **`MirrorMCP.call` să ARUNCE când răspunsul nu e dict** — cu textul serverului în excepție.
   Acum îl întoarce ca string și eroarea reală se pierde.
2. **`fetch_thread` să cadă pe `conversation_number`** când `conversation_id` e respins
   (`rp_ticket.conversation_no` e deja în oglindă, deci nu e nevoie de niciun apel în plus).

⚠️ **Fixul ăsta acoperă 1 din 8 erori.** Pentru celelalte 7, vezi §6.1b — sunt cauză diferită și
reparație diferită.

### 6.1b Parserul SSE se rupe la U+2028 — **7 din 8 erori · NEREPARAT**

**Cauza:** parserul din `rp.py` (`for line in txt.splitlines(): if line.startswith("data:")`) taie
răspunsul în două când corpul conține **U+2028 LINE SEPARATOR** — caracter *legal neescapat* în JSON,
dar pe care Python îl tratează ca sfârșit de linie. De aici:

```
JSONDecodeError: Unterminated string starting at: line 1 column 45 (char 44)
                                                              ↑
        exact lungimea prefixului {"result":{"content":[{"type":"text","text":"
```

Serverul răspunde **corect**; noi stricăm răspunsul la parsare.

**Dovada:** din 2.586 id-uri de tichet care există și în cutiile Gmail (sursă independentă de
Richpanel), cele **7 cu U+2028 sunt 7/7 fără fir**; cele 2.579 fără U+2028 dau **unul singur**.

**De unde vine caracterul:** dintr-un singur șablon de email — invitații TestFlight scrise de un
dezvoltator care lipește text dintr-o aplicație Mac ce folosește U+2028 ca rând moale. Din 44 de
tichete „X has invited you to test", pică 7 (15,9%), toate din același lot; 37 de invitații identice
ca șablon, de la alte firme, trec.

**⚠️ Riscul real e pe cealaltă cale.** Același parser e și pe **enumerare**
(`list_conversations`): acolo un U+2028 ar ucide o pagină de 50 și, prin `except`-ul de zi din
`rp_sync.py`, **o zi întreagă** — iar acele tichete n-ar intra niciodată în `rp_ticket`, deci ar fi
invizibile și în numitorul parității. Nu s-a întâmplat încă **doar** fiindcă `first_message` din listă
e trunchiat la 300 de caractere, iar U+2028-ul observat stă la offset 1057.

**Fix:** în `rp.py::_post`, împarte pe `\n` / `\r\n` explicit, nu cu `splitlines()` — sau parsează SSE
pe octeți. Parserul naiv apare în **15 fișiere din 9 skill-uri** (`cs-360`, `cs-draft-reply`,
`cs-photo`, `cs-procedures`, `cs-sla-dashboard`, `customer-identity`, `richpanel-auto-triage`,
`richpanel-backlog-janitor`, `richpanel-export`), deci reparația trebuie făcută în toate.

**Istoric: nu știu.** `richpanel_tickets.db` n-are tabel de mesaje — firele istorice n-au fost trase
niciodată — iar `first_message` e trunchiat la 300 de caractere și supus aceleiași selecții de
supraviețuire. Orice extrapolare ar fi ghicit.

După ambele fixuri, rulează `rp_sync.py --from 2026-08-30 --to 2026-09-14` ca să recuperezi firele,
apoi `parity_check.py --days 7`.

### 6.2 ~~Cinci fișiere untracked în git~~ — REZOLVAT 9 sep 2026

Cele cinci scripturi (`cs_mirror.py`, `gmail_sync.py`, `parity_check.py`, `rp.py`, `rp_sync.py`) plus
`richpanel_apply.py` și `SKILL.md` sunt acum commitate și împinse pe **`feat/cs-mirror-oglinda`**,
cu **PR #576** deschis către `main`.
Verificat prin hash că sunt identice cu producția înainte de commit. Detalii și ce e ignorat
deliberat: §2.1.

**Rămâne de făcut:** merge-ul PR #576 în `main`, apoi `deploy.sh --apply` pe VPS. Cât timp branch-ul
nu e în `main`, un `git pull --ff-only` pe VPS tot nu vede codul — deci riscul de divergență
**nu e închis complet**, doar oprit din a se agrava.

### 6.3 Ce nu știu (explicit)

1. ~~Dacă Richpanel live mai are textul tichetelor marcate șterse.~~ **REZOLVAT 14 sep — răspunsul e DA.**
   Vezi §6.4.
2. De ce Graph ratează ~24% din comentariile care încă există. Nu pot separa „API-ul chiar nu
   returnează" de „scrape-ul a fost trunchiat tăcut de rate-limit". Test: re-paginare exhaustivă,
   single-thread, cu eșec zgomotos, pe story-ul `122185382600525741`.
3. Identitatea paginii `364899953373966`: o verificare a etichetat-o „Ofertele Zilei", alta „prefix
   nocturna9540".
4. Dacă URL-urile S3 ale atașamentelor expiră (schimbă costul de stocare cu un ordin de mărime).
5. De ce `list_users` întoarce 6 agenți, nu 7.
6. Dacă pe VPS există un Postgres scriibil (până atunci, SQLite). Test de 2 minute:
   `psql "$LOCAL_PG" -c 'select 1'` / `systemctl status postgresql`.

---

### 6.4 Textul șters NU se pierde — Richpanel îl ține în `subject` *(fost §6.3.1)*

Întrebarea care bloca decizia pe congelatorul de comentarii. **Răspunsul e DA**, dar în alt câmp
decât cel în care ne uitam.

```
conv 321372   messages[0].text  = "This message was deleted"
              first_message     = "This message was deleted"
              subject           = "Csalók !!!!!!! Nem ezt küldik !!!!!!!"   ← textul real
```

| Măsurătoare | Rezultat |
|---|--:|
| Tichete cu mesaj tombstone care au text real în `subject` (oglindă) | **1.868 / 1.868 = 100%** |
| Idem, pe arhiva de 2 ani `richpanel_tickets.db` | **70.572 / 70.573 = 100%** |
| `subject` chiar E textul comentariului (pe 4.984 comentarii neșterse) | 88,5% identic · 11,5% = primele 100 car + `…` |

⚠️ **Plafon: 100 de caractere.** Ce depășește se taie.

**Ce înseamnă pentru congelator.** Rămâne pornit, dar justificarea lui e cu **un ordin de mărime**
mai mică decât scria aici. Nu salvează „1.928 de texte altfel pierdute". Salvează:
- **193 de comentarii** care depășesc 100 de caractere (~38 tăiate fiecare);
- **60 de mesaje** tombstone care nu sunt primul din fir;
- **60 de URL-uri** de atașament;
- asigurarea că `subject` nu începe și el să fie suprascris.

**Pârghia adevărată e alta, și e ieftină: viteza de captare.** În zilele în care oglinda a citit
repede a prins **381/381** și **450/450** intacte; pe 2026-08-29 a pierdut **66,3%**, în timp ce
arhiva paralelă pierduse 17,7%. Cei „26,3% deja șterse la captare" nu sunt o proprietate a
comentariilor — sunt **decalajul nostru de citire**.

**Și mai există o copie pe același server.** `richpanel_tickets.db` are **284 de texte** pe care
oglinda le are doar ca tombstone. Tabloul complet pe comentarii FB+IG (N=6.851):

| | tichete | % |
|---|--:|--:|
| ambele arhive au textul | 3.888 | 56,8% |
| doar oglinda | 1.096 | 16,0% |
| **doar arhiva veche** | **284** | **4,1%** |
| niciuna | 1.583 | 23,1% |
| **reuniunea** | **5.268** | **76,9%** (vs 72,7% oglinda singură) |

⇒ Trei acțiuni ieftine care recuperează azi text declarat pierdut: **citește `subject`**,
**fuzionează cele două arhive**, **micșorează decalajul de captare**.

### 6.5 Paritatea dovedește mai puțin decât pare *(corectat 14 sep)*

Două nuanțe pe care raportarea „M1 = 100%, M3 = 0" le ascunde:

**M1 dovedește „am captat tot ce a NUMIT `list_conversations`", nu „tot ce are Richpanel".**
Măsurat: **11 din 18 zile au în oglindă mai multe tichete decât a numărat Richpanel** (cu 1–6,
concentrat pe `instagram_comment` și `email`), toate raportate `coverage_pct = 100.0`, `ok=1`.
Niciun rând invers. Garda pentru numitor trunchiat există, dar se declanșează doar la **exact zero** —
divergența parțială trece tăcută.

**M3 e slab exact pe ziua care contează.** Cronul rulează la 02:00 și verifică D-7..D-1, dar zilele
proaspete n-au fost re-trase după ce a răspuns CS-ul. În rularea din 14 sep, **12 și 13 septembrie au
contribuit 0 candidați din 1.230 de tichete** (26% din fereastră) și verdictul a ieșit tot
„PARITATE DOVEDITĂ". Inofensiv acum (CS nu lucrează în weekend), dar puterea lui M3 stă pe D-7..D-3 —
adică **nu** pe ziua în care ar apărea prima o regresie de captare.

**Fixul corect nu e** „leagă `candidați > 0` de `ok`" (ar înnegri fiecare luni), **ci** „cere candidați
pe zilele care au avut timp să fie răspunse, și marchează explicit zilele NEEVALUABILE în
`parity_daily` și în `--json`".

**Cele 2 selftest-uri picate (22/24) sunt teste greșite, nu defecte** — ambele cer contractul de
dinaintea unei reparații. Garda lor e fail-closed: poate produce doar fals-roșu, și n-a declanșat
niciodată în producție (0 zile cu `rp_count=0` din 18).

## 7. Erată — ce am afirmat greșit

Partea cea mai utilă a dosarului. Fiecare rând a costat timp sau, într-un caz, date de producție.
**Tiparul se repetă: am dedus din interfață sau din documentație în loc să măsor efectul.**

| # | Am afirmat | Ce a arătat măsurătoarea |
|--:|---|---|
| 01 | „Credențialele din Downloads sunt cheie de admin API" | Sunt identitatea **widgetului de chat** (Custom Connector). Testate pe toate combinațiile de header și bază: refuzate constant. Dar s-au dovedit altceva valoros — un canal de scriere în fișa clientului |
| 02 | „API-ul oficial nu e blocat — butonul «Create Key» există" | Butonul există, formularul merge, serverul răspunde **403**. Blocajul e pe backend. **Prezența unui control în UI nu dovedește că funcția e disponibilă** |
| 03 | „`status_url` e pagina de mulțumire — punem tracking în loc" | Era **linkul către comanda concretă** din Shopify, pe care agenții îl foloseau ca să sară direct în comandă. L-am suprascris pe **11.650 de comenzi**. Nu s-a auto-reparat. A raportat-o CS-ul. *Costul verificării înainte: 1 minut* |
| 04 | „Am reparat linkul punând `status_url` construit" | Tot câmpul greșit. Butonul se construiește din **`sourceId`**, pe care connectorul nu-l poate seta. Ce a rezolvat: **dump complet** al unei fișe sănătoase vs una stricată, câmp cu câmp. *A treia oară în aceeași zi* |
| 05 | „Webhook-ul e pe `REPLY_MODE = live`" | Era pe `draft`. **KV e eventual-consistent** — un element apare după ~8s, iar eu așteptasem 4 |
| 06 | „Meta nu livrează evenimente către webhook" | Livrează. Scriptul meu de ascultare **își număra propriul eveniment de control**. Clasificarea corectă se face după `user-agent` |
| 07 | „Cutia are doar inbound" → apoi „Agenții răspund din Gmail pe lângă Richpanel" | Ambele false. **Richpanel trimite prin Gmail**. 18/18 mesaje verificate se regăsesc în Richpanel. Nu există canal paralel |
| 08 | „Richpanel pierde 39% din emailuri" | **Zero pierdute.** Richpanel **colapsează pe CLIENT, nu pe firul RFC822**. Cele 82 „lipsă": **82/82 găsite live**. ⇒ *„lipsă din Richpanel" se verifică LIVE înainte de a fi numită pierdere* |
| 09 | „203 tichete inaccesibile — `bonhaus.ro` și `.sk` nu există" | Sunt **aliasuri**, deja captate prin cutia-gazdă. Un alias care nu se impersonează dă exact aceeași eroare ca o adresă inexistentă |
| 10 | „`contact@rossinails.ro` e cutie de reclamații la curier" | E **cutia CS a brandului ROSSI Nails**, doar cu activitate mică. *Corectat de owner* |
| 11 | „Paritate perfectă — 100% pe toate zilele și canalele" | M1 era 100%. Dar **M3 a găsit 28 de răspunsuri lipsă**. *Un singur indicator, oricât de verde, nu dovedește paritatea* |
| 12 | „Pagina DUPPO Moldova nu se conectează — e bug Richpanel" | Nu e bug. Ecranul arată „All your pages are connected", iar Duppo lipsește din lista pe care **Richpanel** o vede — deși tokenul nostru o vede fără probleme (`id 575422458989566`, tasks `CREATE_CONTENT,MODERATE,MESSAGING,ADVERTISE,ANALYZE`, IG `17841437880793429`). **Lipsește acordarea paginii către aplicația Richpanel** în Business Manager. *Colateral: „ROSSI Nails" apare de 5× în lista de pagini conectate* |
| 13 | „Eroarea de parsare e un câmp uneori obiect, uneori text" | Aproape. Era **răspunsul întreg**: MCP-ul întoarce un STRING de eroare (`id contains invalid characters`) în loc de obiect, iar clientul nostru îl pasa mai departe. §6.1 |
| 14 | „Id-urile pică din cauza caracterelor `<`, `>`, `=`, `+`, `-`" | **Infirmat.** Printre id-urile ACCEPTATE: 618 conțin `=`, 648 conțin `+`, 579 din 770 `m_*` conțin `-`. Regula validatorului rămâne necunoscută, iar cazurile sunt 2 incidente, nu 3. §6.1 |
| 15 | „Cele 8 erori sunt toate clasa §6.1" | **Doar 1 din 8.** Celelalte 7: serverul răspunde corect și **crapă clientul nostru** pe U+2028 în parserul SSE. Dovada: din 2.586 id-uri prezente și în Gmail, cele 7 cu U+2028 sunt 7/7 fără fir; cele 2.579 fără dau unul singur. §6.1b |
| 16 | „Nu știm dacă Richpanel mai are textul comentariilor șterse — de asta atârnă tot congelatorul" | **Îl are**, în `ticket.subject`, 1.868/1.868 în oglindă și 70.572/70.573 pe 2 ani. Ne uitasem doar în `messages[].text` și `first_message`. Valoarea congelatorului scade cu un ordin de mărime. §6.4 |
| 17 | „M1 = 100%, zero ID-uri lipsă ⇒ am captat tot" | M1 dovedește „tot ce a **numit** `list_conversations`". **11 din 18 zile au în oglindă mai multe tichete decât a numărat Richpanel**, toate raportate 100%. §6.5 |
| 18 | „ReplyZen așteaptă 3–5 zile de validare în paralel" | **Validarea ar fi picat.** Webhook-ul primește **0 evenimente Facebook în 7 zile**, fiindcă app-ul are aprobate doar `email` + `public_profile`. ReplyZen și Instagram sunt **același** blocaj: o singură submisie de App Review. §3.5 |
| 19 | „Wrapperele sunt versionate odată cu restul" | Cele 4 care **conduc** producția nu erau în git nicăieri și erau invizibile pentru `deploy_parity.py`. Reparat, PR #587. §2.1 |

**Alte bug-uri de producție găsite pe drum:**

- **~6.500 note duplicate în 2 zile** — `INSERT OR REPLACE INTO tickets` **șterge rândul** în SQLite,
  deci coloanele nelistate (`applied_note_sig`, `resolved_store`, `sentiment`, `quality_flags`,
  `match_order`) reveneau la NULL ⇒ writerul credea că n-a scris ⇒ rescria nota la fiecare 30 min.
  Bug VECHI, dormant cât timp cronul a fost pauzat (3 iul → 17 aug); repornirea l-a trezit.
  **Fix:** `INSERT … ON CONFLICT(id) DO UPDATE SET <doar câmpurile din pull>`.
- **„Update-on-change" era spam, nu bug** — statusul livrării trece prin Netrimisă → În curs → Refuzată
  și AWB-ul se reface ⇒ 4 note pe același tichet în 3 zile. Fix: `--note-mode once`.
  Dovada efectului: pe o copie cu 2.472 tichete OPEN cu status schimbat, `on-change` ar fi scris 2.472
  note, `once` **0**.
- **Cronurile nu erau stricate, erau PAUZATE manual** pe 3 iul „credite LLM" — și Postgres a stat
  blocat **45 de zile** fără să se plângă nimeni. ⇒ orice pauză de cron ar trebui să intre în
  `data_health.py`.
- **Contaminare de diagnostic:** o rulare pornită înainte de pauzare se termină după și produce un
  test fals-negativ. Verifică `ps` întâi.
- **`rawSave` plasat după gărzile de căutare în hartă** ⇒ evenimentul IG de test s-a pierdut. Mutat
  înaintea tuturor filtrelor.
- **Payload-ul de mesagerie mapat cu schema de comentarii** ⇒ DM-uri stocate cu autor și text goale.

---

## 8. Ce urmează

| Sarcină | Stare | Ce deblochează |
|---|---|---|
| **Fix `conversation_number` + `MirrorMCP.call` să arunce** (§6.1) | **cauză găsită, fix neaplicat** | 0,02% din tichete, dar tăcut. E cea mai mică sarcină cu cel mai clar câștig |
| ~~Commit în git al celor 5 fișiere untracked~~ → **merge PR #576 în `main`** + `deploy.sh --apply` (§6.2) | commis și împins, **nemergiat** | Până la merge, VPS-ul tot nu ia codul prin `git pull` |
| ~~Scurgerea evenimentelor brute din D1 în `cs_mirror.db`~~ | **fără obiect** | D1 are 6 rânduri, toate IG DM. N-are ce scurge până nu trece App Review-ul |
| **Citește `subject` pentru comentariile șterse** + fuzionează cele două arhive (§6.4) | **de făcut, ieftin** | Recuperează azi text pe care documentul îl declara pierdut definitiv |
| **Repară parserul SSE la U+2028** (§6.1b), în toate cele 15 fișiere | **de făcut** | 7 din 8 erori curente; și riscul tăcut de a pierde o zi întreagă la enumerare |
| **App Review pentru „Api export"** — `pages_read_engagement`, `pages_manage_engagement`, `instagram_manage_comments`, `instagram_manage_messages` | **decizie de owner** | **Deblochează SIMULTAN: ReplyZen (149 $/lună), identitatea comentatorilor FB, și DM-urile Instagram.** Sunt același blocaj, nu trei (§3.5) |
| ~~Validare ReplyZen: 3–5 zile în paralel~~ | **imposibil azi** | Webhook-ul nu primește niciun eveniment Facebook. Validarea ar pica. ReplyZen rămâne pornit |
| Acordarea paginilor lipsă în Business Manager | administrativ | 12% din volumul social + conectarea DUPPO Moldova în Richpanel. *Criteriu de succes = EFECTUL:* `GET /{page}?fields=access_token` → 200 **și** în 24h comentariile apar în oglindă |
| Comentarii TikTok pe grupurile de reclame ACTIVE | de făcut | Canal pe care Richpanel nu-l are deloc; acum se moderează în ReplyZen. Semnătura API e cunoscută (`/open_api/v1.3/comment/list/`) |
| ~~Advanced Access Instagram~~ → inclus în App Review-ul de mai sus | **decizie de owner** | Nu mai e un item separat |
| Testul „RP live mai are textul șters?" (§6.3.1) | de făcut | Decide dacă congelatorul de comentarii merită costul |

### 8.1 Cerințele pentru aplicația proprie

**Modelul de bază: un brand = o pagină Facebook + un magazin Shopify + o adresă de email.**

⚠️ Relația **nu** e unu-la-unu peste tot (vezi §3.2). Atribuirea pe brand se face după **adresa din
antet (`Delivered-To`)**, NU după cutie — altfel Ofertele Zilei apare ca fiind Casa Ofertelor.

| Cerință | Unde stăm |
|---|---|
| **C1** Conturi de agent create din aplicație | De construit. Identificarea se face după **email**, nu după numele afișat |
| **C2** Asignare de branduri la agenți | Rutarea reală e măsurată pe tichete (§4.3). Aplicația trebuie **s-o reproducă**, nu doar s-o permită |
| **C3** Comenzi istorice vizibile | ✅ **Stăm mai bine decât Richpanel.** AWBprint = sursa autoritativă de livrare; `metrics.orders` + `shopifyNumericId` = link direct în adminul Shopify; `gigi:cs-360` face deja profilul 360 |
| **C4** Macro-uri de răspuns | Textele oficiale sunt în **ClickUp** (doc `2kyqg8j1-3895`, v3 docs API + `CLICKUP_API_TOKEN`); `gigi:cs-draft-reply` le completează cu `{client}/{comanda}/{awb}/{magazin}/{link_retur}` reale. Ton: scurt, formal („dumneavoastră"), fără răspunsuri lungi care deschid conversație |
| **C5** Branduri nelimitate, unul per magazin | Cerință **nouă**, ieșită din inventar: cu 10 branduri plafonate, jumătate din magazine n-au SLA și program propriu |

### 8.2 Ce ne mai desparte de înlocuire

Trei lucruri, dintre care unul e deja pornit:

1. **Ingestia prin webhook** — singura care poartă identitatea comentatorului. **Pornită, în draft.**
2. **Trimiterea outbound** (email + Messenger) — acum avem doar `gmail.readonly`.
3. **Interfața de agent** — inbox, atribuire, macro-uri, SLA.

**Orice cifră de economie înainte de acestea ar fi o promisiune, nu o estimare.**

⚠️ Și încă un lucru la cutover: **55,3% din mesajele istorice au plecat prin infrastructura Richpanel**
și se pot lua DOAR de acolo cât timp abonamentul e activ. **Nu opri abonamentul imediat după cutover.**

---

## 9. Anexe

### 9.1 Schema `cs_mirror.db`

```sql
CREATE TABLE rp_ticket(
    id TEXT PRIMARY KEY,
    conversation_no INTEGER, channel TEXT, status TEXT, priority TEXT, assignee_id TEXT,
    to_id TEXT, to_email TEXT, from_id TEXT, from_email TEXT,
    customer_id TEXT, customer_name TEXT, customer_email TEXT, customer_phone TEXT,
    tag_names TEXT, subject TEXT, first_message TEXT, comment_count INTEGER,
    created_at TEXT, updated_at TEXT, closed_at TEXT, first_responded_at TEXT,
    store_resolved TEXT, fetched_at TEXT, msg_total_at_fetch INTEGER);

CREATE TABLE rp_message(
    ticket_id TEXT NOT NULL, msg_id TEXT NOT NULL, idx INTEGER, created_at TEXT,
    type TEXT, is_private INTEGER, is_ai INTEGER,
    author_id TEXT, author_name TEXT, is_agent INTEGER, is_operator_bot INTEGER,
    channel TEXT, text TEXT, text_len INTEGER, truncated INTEGER, fetched_at TEXT,
    PRIMARY KEY (ticket_id, msg_id));

CREATE TABLE rp_attachment(
    ticket_id TEXT NOT NULL, msg_id TEXT NOT NULL, url TEXT NOT NULL, fetched_at TEXT,
    PRIMARY KEY (ticket_id, msg_id, url));

CREATE TABLE gm_message(
    message_id TEXT PRIMARY KEY,
    mailbox TEXT, gmail_id TEXT, thread_id TEXT, direction TEXT,
    from_addr TEXT, to_addr TEXT, subject TEXT, date_utc TEXT,
    in_reply_to TEXT, refs TEXT,
    is_automated INTEGER, auto_reason TEXT,
    body_text TEXT, body_len INTEGER, has_attachments INTEGER,
    labels TEXT, fetched_at TEXT);

CREATE TABLE gm_attachment(
    message_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT,
    size_bytes INTEGER, attachment_id TEXT, fetched_at TEXT,
    PRIMARY KEY (message_id, filename));

CREATE TABLE gm_gap(            -- ce e in cutie dar NU in Richpanel
    message_id TEXT PRIMARY KEY, day TEXT, mailbox TEXT, subject TEXT,
    from_addr TEXT, reason TEXT, found_at TEXT);

CREATE TABLE gm_state(          -- cursorul incremental per cutie
    mailbox TEXT PRIMARY KEY, history_id TEXT, last_sync_at TEXT, last_ok_at TEXT,
    messages_total INTEGER, aliases TEXT, note TEXT);

CREATE TABLE parity_daily(
    day TEXT NOT NULL, channel TEXT NOT NULL, rp_count INTEGER, mirror_count INTEGER,
    matched INTEGER, missing_sample TEXT, coverage_pct REAL, run_at TEXT,
    m3_no_agent INTEGER, m3_sample TEXT,
    PRIMARY KEY (day, channel));

CREATE TABLE sync_run(
    job TEXT, started_at TEXT, ended_at TEXT, ok INTEGER, items INTEGER,
    errors INTEGER, note TEXT);
```

Tabele **planificate, neimplementate încă** (pentru captarea de la sursă Meta): `fb_post`,
`fb_comment`, `fb_thread`, `fb_message`, `ig_comment` — schema în [`cs-mirror-plan.md`](cs-mirror-plan.md) §3.

### 9.2 Secrete (nume, NU valori)

Toate în tabelul `secrets` din SharedClaude. Ia-le cu `kb.py secret-get KEY` și **pipe-uiește valoarea
în proces** — niciodată în chat, cod sau git.

| Cheie | Pentru ce |
|---|---|
| `RICHPANEL_MCP_TOKEN` | MCP JSON-RPC (citiri + taguri/note) |
| `GOOGLE_SA_LOOKER_SHEETS_JSON` | service account cu delegare de domeniu (Gmail readonly) |
| `CLOUDFLARE_API_TOKEN_WORKERS` | deploy Worker + KV + D1. ⚠️ `CLOUDFLARE_API_TOKEN` e **read-only** |
| `META_APIEXPORT_APP_SECRET` | HMAC-ul webhookului (app „Api export"). ⚠️ NU `META_APP_SECRET` |
| `DATABASE_URL_AWBPRINT` | sursa de livrare pentru `rp.py push` |
| `DATABASE_URL_METRICS` | `metrics.richpanel_tickets` |
| `CLICKUP_API_TOKEN` | macro-urile CS oficiale (doc `2kyqg8j1-3895`) |

Pe VPS, aceleași valori sunt în `/root/Scripturi/.env` (⚠️ nu bash-source, vezi §2.2).

⚠️ În sonde s-au găsit **5 fișiere de secrete world-readable (`0644`)** în scratchpad.
Tokenurile nu au voie să atingă discul.

### 9.3 Glosar

| Termen | Ce înseamnă |
|---|---|
| **oglindă** | copia locală read-only a Richpanel (`cs_mirror.db`). Nu răspunde, nu modifică |
| **regula de îngheț** | un text captat nu se suprascrie cu gol/tombstone/mai scurt |
| **tombstone** | `This message was deleted` și variantele lui |
| **operator bot** | mesaj marcat „agent" dar cu `author.id == "operator"` = automatism canned al widgetului |
| **M1…M7** | metricile de paritate (§3.3) |
| **connector** | Custom Connector Richpanel = canalul de scriere în fișa clientului |
| **story_id** | id-ul postării/reclamei pe care s-a comentat; leagă tichetul de creativ |
| **PSID** | id-ul clientului pe Messenger; = `from.id` din tichetul RP |
| **dark post** | reclamă fără postare organică — de aceea `/{page}/feed` arată 0 comentarii |

### 9.4 Skill-uri CS legate

`gigi:richpanel-export` (ăsta) · `gigi:cs-tickets` · `gigi:cs-draft-reply` · `gigi:cs-360` ·
`gigi:cs-sentiment` · `gigi:cs-quality-audit` · `gigi:cs-comment-intelligence` ·
`gigi:richpanel-auto-triage` · `gigi:richpanel-backlog-janitor` · `gigi:cs-sla-dashboard` ·
`gigi:customer-identity` · `gigi:xconnector`.
Harta de rutare pe intenții: [`CS.md`](CS.md).

### 9.5 Memorii legate

`richpanel-tickets-access` · `richpanel-connector-write-api` · `cs-richpanel-pipeline-deploy` ·
`cs-mirror-email-capture` · `fb-comment-webhook-state` · `instagram-capture-limits` ·
`cs-macros-clickup` · `fb-page-store-map` · `cs-map-and-windows-encoding` · `arona-voice-and-tone`
