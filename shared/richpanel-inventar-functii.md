# Richpanel — inventar de funcționalități (parcurs ecran cu ecran, 31-aug-2026)

> Logat cu contul Cristina Sava (rol `TENANT_ADMIN`).
> Scop: să știm exact ce replicăm. Marcaj: **[E]** esențial · **[U]** util · **[D]** decor.
> Nu s-a modificat nimic — doar citire. (Singura încercare de scriere, „Create Key", a fost
> refuzată de server; dialogul a fost închis.)

## 1. ECRANUL DE TICHET — suprafața de lucru a agentului

### Firul conversației
- **[E]** Mesajele clientului + răspunsurile agentului, cu autor și oră.
- **[E]** **Postarea-sursă afișată inline**: la comentariile de pe reclame se vede CREATIVUL
  („PACHET: 10L DETERGENT… 99 Lei", 12 reacții, 2 comentarii) și **care** comentariu e selectat.
  Fără asta agentul răspunde orb. Noi avem legătura prin `story_id` (vezi [[fb-comment-webhook-state]]).
- **[E]** Eticheta de canal pe fiecare mesaj (Facebook comment / Facebook DM / Email).
- **[U]** **Traducere** RO ↔ EN, un click. Contează pentru Bonhaus CZ/PL/BG și Nocturna BG.
- **[U]** Jurnal de activitate: „Task Bot Rule Lisa was triggered on…" + „Show 2 other activity messages".
- **[U]** Navigare Prev / Next fără întoarcere în listă.

### Compunerea răspunsului
- **[E]** Taburi: **canal public** vs **Private Note** (nota internă nu ajunge la client).
- **[E]** „Replying to X via Facebook Comment" — spune explicit pe ce canal pleacă.
- **[E]** **Send** și **Send and Close** (închiderea la trimitere = fluxul CS real, vezi [[cs-map-and-windows-encoding]]).
- **[U]** Macro-uri inserabile (43, vezi §3).

### AI (Sidekick)
- **[U]** **Suggest a reply** · **Summarize** · **Prompt** liber.
  Noi avem deja echivalentul, cu date mai bune: `gigi:cs-draft-reply` completează comanda/AWB reale.

### Bara laterală de client — DOUĂ taburi: `Timeline` și `Customer Profile`
- **[E]** Câmpuri client: Email, Nume, Prenume, Telefon, Last Seen, +15 câmpuri ascunse. Editabile.
- **[E]** **Other Conversations** — istoricul clientului cu noi (cine a răspuns, când, ce status).
- **[E]** **Orders** / **Draft Orders** — comenzile clientului, cu buton *Create order*.
  ⚠️ Pe tichetul verificat scria **„No orders found"** deși clientul cumpărase (2 bidoane, spune el).
  Se leagă de bug-ul deja măsurat: fișa de comandă din Richpanel e incompletă/greșită
  (tracking gol, status blocat pe „fulfilled") — vezi [[richpanel-connector-write-api]].
- **[U]** **Agent Forms** — formulare structurate completate de agent. Există „Formular De Retur"
  cu câmpurile **IBAN**, **Nume complet**, **Motivul anulării**. E captare de date, nu doar text.
- **[U]** **Knowledge Base** în bara laterală, pe limbi, cu articole inserabile („Use").
- **[U]** **Block** (blocare client) · **Notes** · **Subscriptions** (nefolosit).
- **[D]** „Edit widgets" — rearanjarea panourilor.

## 2. INBOX
- **[E]** Vizualizări **pe agent → brand** (exact modelul ownerului):
  `Raluca`: Esteban 124 · Lab Noir 65 · Grandia 21 · George Talent 18 · Nubra 14 · ROSSI 2
  `Lisa`: Genti 67 · Reduceri bune 60 · Casa Ofertelor 49 · MagDeal 25 · Belasil 17 · Ofertele Zilei 10 · Covoare 6
  `Martina`: Bonhaus EU (CZ+PL) 403 · Bonhaus/Nocturna BG 65
- **[E]** Vizualizări implicite: My Inbox, Unassigned, Drafts, Mentions, Outbox, All.
- **[E]** Filtre pe status (Open 1,5k / Snoozed 0 / Closed **285,8k**) + Add Filter + sortare.
- **[U]** Vizualizări partajate: `AI`, `Reclamaţii DPD`, `Awaiting Reply` (1,45k), `Nocturna BG`, `Trash` (10,56k).
- **[U]** Contoare pe canal: Emails 764 · Facebook 281 · Instagram 16 · restul 0.
- **[D]** Tasks (All/My Tasks) — 0 folosite.

## 3. MACRO-URI — 43 active
Extrase integral din API-ul intern (`/tenant/macros`) → `data/rp_macros_clean.json`.
**Toate au o singură acțiune: `ADD_REPLY_TEXT`.** Text fix, fără condiții, fără ramificații,
fără variabile dinamice. Toate pe „All Brands" (deci NU sunt per-brand, deși câmpul există).
Acoperă: retur (proceduri de 1.100-1.200 car.), schimb, produs greșit, comandă întârziată,
anulare imposibilă, rambursare, client agresiv, client neidentificat, ramburs, indisponibil,
detalii produs, sigiliu parfumuri, solicitare date.
⇒ **De replicat: o listă de texte, nu un motor.** `gigi:cs-draft-reply` face deja mai mult
(completează `{comanda}/{awb}/{magazin}` cu date reale). Vezi [[cs-macros-clickup]].

## 4. AUTOMATIZĂRI — 17 reguli, doar 3 ACTIVE
```
LIVE:    CZ Marta → Martina K · Lisa → Monica Dan · Raluca → Cristina Sava
         (declanșator identic: „A customer starts a new conversation")
implicit: Irina Oprea
PAUZATE (14): WISMO ×3 (cu/fără tracking, </>3 zile) · anulare ×2 · schimbare adresă ×2 ·
         produse lipsă · produse deteriorate · mesaj sărbători · problemă tehnică ×3 · autoresponder
```
⇒ **Automatizarea reală = rutare pe agent.** Toate răspunsurile automate au fost încercate
și oprite. De replicat: 3 reguli, nu 17.

## 5. RESTUL SETĂRILOR (existente, de inventariat la nevoie)
Canale (14, active doar Email + FB/IG) · Assignment · Task Bots · Spam Filtering Bots ·
Follow Up On Snooze · Sidekick (AI) · Users / Teams / Agent Shifts / Permissions / Audit Logs ·
**Brands** (concept nativ, cu widget separat pe brand: limbă, culori, mesaj de întâmpinare) ·
SLAs · Business Hours · Satisfaction Survey · Conversation Fields · Customer Fields ·
Agent Forms · Custom Objects · CSV Import · Helpdesk Importer.

## 5b. RESTUL ECRANELOR — parcurse

### Brands — ⚠️ LIMITATE DE PLAN (constrângere reală, nu alegere)
**10 configurate:** Esteban.ro · Belasil · Casa Ofertelor · Apreciat · Gento · Carpetto ·
Nocturna.bg · Bonhaus BG · **Call Center** · **Reclamatii ARONA** (ultimele două sunt funcții, nu branduri).
Ownerul confirmă: **Richpanel plafonează numărul de branduri** — de-aia sunt 10 la ~20 de magazine.
⇒ **Cerință dură pentru sistemul propriu: branduri NELIMITATE, unul per magazin.**
Brandul e cheia pentru: widget separat (limbă, culori, mesaj), **SLA per brand**, **program de lucru
per brand**. Cu doar 10 branduri, jumătate din magazine n-au SLA și program propriu.

### Billing — costul crește cu echipa
`Helpdesk Pro $89/lună × 4 seats = $356/lună`, contract lunar, următoarea factură **8-sep-2026**.
⇒ **Un agent nou = +$89/lună.** Sunt 6 utilizatori dar 4 seats (Irina Oprea = *Pending Invitation*).
Cu ReplyZen ($149) → **$505/lună = $6.060/an**.

### Assignment — mai bogat decât regulile
Metode: **Manually** · **Balanced** (după încărcare) · **Round Robin** (recomandat de ei).
Opțiuni: *Re-assign on reply* (dacă agentul e offline) · *Assign on First Message* · *Notify all agents*.
**Agent Capacity**: plafon de conversații per agent, SEPARAT pe „chat-like" (Live Chat, FB Message,
IG DM, WhatsApp) vs „ticket channels" (Email, FB Comment, IG Comment, SMS, call…).
Canalele se pot muta între cele două categorii (Live Chat NU se poate muta).
⇒ De replicat: rutare pe brand→agent + plafon de capacitate. Restul e decor la 3 agenți.

### Users — 6, dintre care 1 neactivat
Mariana Popescu · Cristina Sava (=Raluca) · Diana Popa · **Irina Oprea (Pending Invitation)** ·
Martina K · Monica Dan (=Lisa). Filtrare pe status/rol, **Export**.
⚠️ Reconfirmat: numele afișat ≠ persoana reală (identificarea se face pe email).

### Customer Fields — 73 de câmpuri
Includ exact schema decodată empiric prin connector: `lastOrderAmount`, `lastOrderTaxAmount`,
`lastOrderShippingAmount`, `appClientIdList`, `email`, `firstName`, `lastName`, `uid`.
⚠️ Există câmpuri DUPLICATE făcute de om: „Telefon"/`telefon` și „Telefon"/`nrtelefon`.
Tipuri: Single-line Text, Number, List.

### Agent Forms — unul singur
„Formular De Retur" — *„Vă rugăm completați toate datele din formularul de mai jos pentru
procesarea returului"*, cu IBAN / Nume complet / Motivul anulării. Puțin folosit, dar e singurul
mecanism de **captare structurată** din tot helpdesk-ul.

### Permissions — grosier
Doar 4 comutatoare, pe roluri: *Download reports* (Admin) · *Trashing Conversation* ·
*Editing Views* · **Allow refunds** (agenții pot face refund din helpdesk).
⇒ Nu există permisiuni pe brand. Un agent vede tot.

### SLAs & Business hours — per BRAND
Ambele se configurează alegând întâi brandul ⇒ limitate de aceleași 10 branduri.

## 6. CE NU SE POATE LUA PRIN API
`Create Key` → **403** „API access is not enabled on your account". Blocaj de BACKEND, nu de UI.
Rămâne MCP + `RICHPANEL_MCP_TOKEN`. Detalii în [[richpanel-tickets-access]].
