---
name: richpanel-export
description: Bulk-export the Richpanel helpdesk history into a local SQLite (the official Richpanel API is disabled on the account — this speaks JSON-RPC directly to the MCP endpoint using RICHPANEL_MCP_TOKEN from the KB). Phase 1 exports conversation summaries (subject, first message, channel, agent, customer, timestamps), auto-detects the STORE and the ORDER NUMBER, and CATEGORIZES every ticket by rules (livrare/WISMO, retur, schimb/swap, anulare, modificare comanda, problema produs, refuz livrare, plata/factura, presale, comanda noua, recenzie, spam-automat, comentariu social). Resumable day-by-day. Use for "export Richpanel history", "categorize CS tickets", "ticket history analysis", "istoricul tichetelor", "categorii tichete CS", "ce fel de tichete primim", and as the data layer for the CS documentation / bad-answer audit project. Read-only on Richpanel.
---

# Richpanel — export istoric + categorisire tichete

API-ul oficial Richpanel e dezactivat pe cont, dar token-ul MCP (KB: `RICHPANEL_MCP_TOKEN`) permite apeluri JSON-RPC directe la `https://mcp.richpanel.com/mcp` → export programatic complet.

## Cum rulezi
```bash
uv run richpanel_export.py pull --from 2026-05-12 --to 2026-06-11   # export interval (resumabil)
uv run richpanel_export.py stats                                    # ce avem: categorii/canale/magazine
uv run richpanel_export.py categorize                               # re-rulează regulile pe ce e în DB
uv run richpanel_link.py                                            # LEAGĂ tichetele de clienți Shopify + rezolvă magazinul
```
DB local: `Scripturi/data/richpanel_tickets.db` (tabel `tickets` + `pull_log`). Backfill mare → rulează în fundal; zilele complete se sar la re-rulare.

## `richpanel_link.py` — identitate + atribuire magazin (pasul de îmbogățire)
După `pull`, leagă fiecare tichet de clientul Shopify și-i atribuie magazinul:
- **Client↔comandă:** email/telefon (din `from` + regex pe `first_message`) → `metrics.orders` → comenzile lui + nume. (~24% din tichete legate la o comandă; restul sunt comentarii/clienți fără comandă.)
- **Magazin (~98%):** din comanda potrivită → din prefixul comenzii → din **pagina FB/IG** (`to.id`, mapare fixă `PAGE_OVERRIDE` de 18 pagini, vezi memoria `fb-page-store-map`) → din subiectul chat-ului („Chat pe X.ro"). Învață și pagină→magazin din voturile sigure pe email.
- **Scrie:** tabel nou `customer_identity` (ticket_id ↔ email/telefon ↔ comenzi ↔ magazin) + coloane pe `tickets`: `resolved_store, contact_email, contact_phone, match_order, link_method`.
- Restul ~1% e ireductibil fără citirea corpului fiecărei conversații (mesaje generice „Chat with us" fără semnal + emailuri de la cine n-a comandat + notificări FB).
- Pt o vedere completă pe UN client (cross-platform, cu livrare/profit/tichete) folosește skill-ul `gigi:customer-identity`.

## `richpanel_apply.py` — scrie enrichment-ul ÎNAPOI în Richpanel (taguri + notă internă)
```bash
uv run richpanel_apply.py                     # DRY-RUN pe OPEN (ce AR scrie)
uv run richpanel_apply.py --recent 1 --apply  # intraday (așa rulează cronul, prin richpanel_pipeline --push)
uv run richpanel_apply.py --note-mode never --apply   # doar taguri, zero note
```
Taguri `magazin-*`/`cat-*`/`sentiment-*`/`flag-*`/lead/reclamatie/awb-trimis + **UN private note intern**
(client, comandă, status, tracking DPD). Niciodată mesaj/draft către client, nu schimbă status/assignee.

⚠️ **Nota se scrie O SINGURĂ DATĂ pe conversație** (`--note-mode once`, implicit) — e un SNAPSHOT de la
primul contact, nu un live feed. Vechiul „update-on-change" re-punea nota la fiecare schimbare de
status/AWB (Netrimisa → In curs de livrare → Refuzata = 4 note pe același tichet în 3 zile), iar notele
**nu se pot șterge prin MCP**. Statusul curent se ia cu `gigi:cs-360` / `gigi:xconnector links`.
Două gărzi împotriva dublurilor: coloana `applied_note_sig` din SQLite **și** o verificare în Richpanel
(`get_conversation` + `include_private_notes`) înainte de fiecare scriere — pentru că semnătura din DB
s-a mai pierdut o dată (pull cu `INSERT OR REPLACE`) și au ieșit ~6.500 note duplicate.

⚠️ **`list_tags` fără `query` întoarce DOAR primele 25** dintr-un dicționar de ~1.900 taguri — de-aia
`tag_id()` caută întâi pe nume (`query=<tag>`) și abia apoi creează. Fără asta, tagurile uzuale
(`magazin-esteban`, `awb-trimis`, `cat-livrare-wismo`) păreau inexistente la fiecare rulare: `create_tag`
umplea workspace-ul de dubluri urât-normalizate (`colaborar`, `flagfrictiune`) și ~7 tichete/rulare
rămâneau needitate, reîncercate la nesfârșit. Reparat 24-aug-2026.

## Ce extrage per tichet
id, nr conversație, subiect, **primul mesaj**, status, canal, agent (assignee), client (nume/email), **magazin** (din emailul destinație contact@<domeniu> sau prefixul comenzii), **nr comandă** (regex EST/GT/GRAND/... din subiect+mesaj), timestamps, + JSON-ul brut.

## Categorii (reguli pe subiect+prim mesaj, fără diacritice)
`livrare_wismo` · `retur` · `schimb_swap` · `anulare` · `modificare_comanda` · `problema_produs` · `refuz_livrare` · `plata_factura` · `presale_intrebare` · `comanda_noua` · `recenzie_feedback` · `spam_automat` (Judge.me etc.) · `comentariu_social` (comentarii FB/IG la reclame) · `altele`.

## Capcane API (învățate empiric)
- **`endDate` e EXCLUSIV** — pt o singură zi cere `[zi, zi+1)`. start=end → 0 rezultate.
- `sortKey=createdAt` + `order=asc` → 0 rezultate (folosește sortul default).
- `status=all` nu merge — cere separat `open` + `closed`.
- Emoji din Facebook au surrogates rupte → sanitizează stringurile înainte de SQLite.
- Max 50/pagină; paginează cât `len==50`; pauză ~0,4s între pagini.

## Faza 2 (de făcut, planul în memorie)
- `get_conversation` (mode=audit) pe eșantioane per categorie → cum s-a răspuns, timpi, calitate → **documentația CS** + raport „unde s-a răspuns prost".
- Mapare pagini Facebook (`to.id`) → magazin pt comentariile social (acum „necunoscut").
- Îmbogățire cu comanda clientului (metrics.orders + profit_orders) și LLM pe categria `altele`.

## `gmail_sync.py` — a doua sursă a oglinzii CS: **cutiile Gmail** (READ-ONLY)
Richpanel nu e singurul loc unde stă Customer Service-ul: emailul intră în cutiile `contact@*`, iar
o parte din răspunsuri pleacă **direct din Gmail**, pe lângă helpdesk. `gmail_sync.py` captează
cutiile în ACEEAȘI bază (`cs_mirror.db`, tabelele `gm_message` / `gm_attachment` / `gm_gap` /
`gm_state`), cu ACEEAȘI regulă de îngheț ca mesajele Richpanel.

```bash
uv run gmail_sync.py --recent 3                      # ultimele N zile, toate cutiile CS
uv run gmail_sync.py --mailbox contact@esteban.ro --recent 1
uv run gmail_sync.py --since 2026-08-27 --until 2026-08-30   # fereastră fixă (--until EXCLUSIV)
uv run gmail_sync.py --stats
uv run gmail_sync.py --reconcile --days 7            # ce e în cutie dar NU în Richpanel
```
Acces: service account `GOOGLE_SA_LOOKER_SHEETS_JSON` din KB + delegare de domeniu, **scope unic
`gmail.readonly`**. `assert_read_only()` respinge orice metodă Gmail care nu e de citire, iar
`assert_scopes()` respinge orice scope care nu e readonly — faza e DOAR CAPTARE.

**Ce trebuie știut (măsurat, nu presupus):**
- **21 de adrese accesibile ≠ 21 de cutii.** 4 sunt ALIASURI (`contact@bonhaus.hu/hr` +
  `contact@nocturna.pl` → `contact@trynocturna.eu`; `contact@ofertelezilei.ro` →
  `contact@casaofertelor.ro`; `reclamatii@aronagroup.ro` → `facturi@aronagroup.ro`). Cutiile se
  dedublează prin `users.getProfile().emailAddress` — altfel tragi aceeași cutie de 4 ori.
  `contact@bonhaus.ro` și `contact@bonhaus.sk` NU există ca utilizatori Google (`invalid_grant`).
- **Cutia NU conține doar inbound.** Măsurat pe 08-28: 110 mesaje `SENT`, toate cu Message-ID
  `@mail.gmail.com` = scrise de om în interfața Gmail, nu de Richpanel (care trimite prin SES).
- **~36% din volum e AUTOMAT** (Judge.me, curieri, Shopify, Klaviyo). NU se aruncă: `is_automated`
  + `auto_reason`. ⚠️ expeditorul cunoscut se verifică ÎNAINTEA anteturilor generice — altfel
  `List-Unsubscribe` înghite proveniența și Judge.me apare ca „listă" (măsurat: raporta 0).
- `TRASH = 0` pe toate cele 17 cutii → se parcurg doar `INBOX/SENT/SPAM`.
- Incremental REAL pe `users.history.list` (`gm_state.history_id`, luat ÎNAINTE de enumerare și
  salvat doar dacă trecerea a mers). Cursor prea vechi → 404 → cade automat pe interogarea după
  dată. ⚠️ 404 nu are voie să fie tratat global ca „nimic nou" — ar pierde tăcut tot.
- Atașamentele: **doar metadate** (nume/mime/mărime/`attachment_id`), conținutul nu se descarcă.

### `--reconcile` = dovada cantitativă a ce pierde Richpanel
Cheia de legătură e Message-ID-ul RFC822 (`rp_ticket.id` **este** Message-ID pe 99,74% din emailuri).
⚠️ Verdictul are trei trepte, fiindcă și dovezile au calități diferite — fără distincția asta ai
raporta drept „pierderi" zilele pe care pur și simplu nu le-ai tras din Richpanel:
| verdict | ce înseamnă |
|---|---|
| `lipsa_in_rp` | ziua are în oglindă id-uri **per mesaj** → absența e DOVEDITĂ |
| `lipsa_in_rp_probabil` | ziua e acoperită doar de exportul vechi (id-uri de **conversație**) → probabil |
| *(nescris în `gm_gap`)* | ziua n-are NICIO acoperire RP → **nejudecabil**, nu pierdere |

Măsurat pe 2026-08-28 (zi cu pull RP complet), 3 cutii: din 229 emailuri, **145 în Richpanel (63,3%)
și 84 lipsă (36,7%)** — 40 notificări de curier, **17 emailuri UMANE de client** (retur, produs spart,
anulare) și **23 de răspunsuri trimise din Gmail**, invizibile în orice raport de CS.
