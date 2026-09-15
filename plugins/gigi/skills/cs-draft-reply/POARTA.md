# Poarta de pornire — motorul de răspuns CS

> Ce rulezi **înainte** să pornești cronul, și după **fiecare** schimbare:
> ```
> uv run plugins/gigi/skills/cs-draft-reply/poarta_pornire.py --vps root@84.46.242.181
> ```
> Cod de ieșire `0` = 🟢 VERDE, `1` = 🔴 ROȘU cu motive. Poarta nu spune că răspunsurile sunt
> bune de trimis — spune că motorul se poartă așa cum a fost măsurat. Pornirea rămâne decizie
> de owner (cronul e **PAUZAT din 29-iun-2026**, vezi `shared/cs-ai-raspuns-documentatie.md §1`).

---

## 1. De ce există: reparațiile nu ajung singure unde rulează codul

Măsurat pe **15-sep-2026**, aceeași zi în care s-au făcut reparațiile rundelor 1-2:

| Copie | Cale | Linii | md5 |
|---|---|--:|---|
| **repo** (arborele de lucru) | `team-intelligence/plugins/gigi/skills/cs-draft-reply/cs_auto_draft.py` | 2352 | `ba902e61…` |
| **plugin** (ce execută skill-ul pe mașina ta) | `~/.claude/plugins/marketplaces/team-intelligence/…/cs_auto_draft.py` | 1342 | `f0494cfb…` |
| **VPS** (ce execută cronul) | `root@84.46.242.181:/root/Scripturi/cs_auto_draft.py` | 1352 | `eb5bc957…` |
| repo · cs_photo.py | `…/cs-photo/cs_photo.py` | 686 | `29c786b8…` |
| plugin · cs_photo.py | `~/.claude/plugins/…/cs-photo/cs_photo.py` | 571 | `6352e102…` |
| VPS · cs_photo.py | `/root/Scripturi/cs_photo.py` | 571 | `6352e102…` |

Trei versiuni diferite, niciuna la fel. Consecințe, în clar:

- un test „pe cod" nu spune nimic despre ce rulează în realitate — **suitele testează repo-ul**;
- cine pornește cronul azi pornește versiunea **de dinainte de runda 1** (fără gărzile multilingve,
  fără registrul de politețe, cu telefoanele vechi, greșite);
- `gigi:cs-draft-reply` invocat ca skill rulează copia **plugin-ului**, adică altă versiune decât
  cea pe care ai citit-o și ai reparat-o.

De-aia secțiunea **E** a porții compară md5-urile și pică pe ROȘU la orice divergență.

## 2. Ce am mai măsurat pe linia de cron (nu era cum se credea)

`/root/Scripturi/cs_backlog.sh` (VPS, modificat 15-sep-2026 17:36) **nu mai conține `--no-comments`**:
buclează explicit pe toate cele 7 canale, **inclusiv `facebook_feed_comment` și `instagram_comment`**,
cu `--auto-hide`. Deci presupunerea „cronul rulează cu `--no-comments`, deci scurgerile pe canal
public nu se declanșează" **nu mai e adevărată** pentru scriptul care ar porni.

Linia din crontab e comentată (`# PAUZAT 2026-06-29 0 9-21/3 …`), deci azi nu rulează nimic —
dar o repornire ar activa direct canalele publice.

**Ce s-a schimbat în cod** (vezi §5): canalul public a devenit **opt-in**. Adică
**desfășurarea ESTE remedierea**: cu codul rundei 4 pe VPS, `cs_backlog.sh` nemodificat
intră pe comentarii în modul „doar-moderare" (ascunde spamul, FĂRĂ draft public).
Ordinea e singurul lucru care contează — vezi §3.1: **cronul se decomentează ULTIMUL**,
după desfășurare și după o poartă VERDE rulată pe copia de pe VPS. Gardă gata scrisă
(neinstalată): `desfasurare/cs_backlog_garda.sh`.

## 3. Procedura de desfășurare (arbore de lucru → git → plugin → VPS)

**Starea măsurată 15-sep-2026, înainte de runda 4:**

| copie | cale | `cs_auto_draft.py` | `cs_photo.py` | ce e |
|---|---|--:|--:|---|
| arbore de lucru | `~/Downloads/Scripturi/team-intelligence/` | **2839** | **686** | reparațiile rundelor 1-4, **NECOMISE** |
| clona plugin-ului | `~/.claude/plugins/marketplaces/team-intelligence/` | 1342 | 571 | cod de **dinainte de runda 1** |
| VPS | `/root/Scripturi/` (fișiere flat) | 1352 | 571 | cod de **dinainte de runda 1** |

Cele două clone git sunt pe **același commit** (`a2b2e54`). Diferența nu e un commit lipsă, ci
**modificări necomise** în arborele de lucru. Calea pe care o rulează efectiv skill-ul
`gigi:cs-draft-reply` e cea a **plugin-ului** — deci azi, orice ai repara în arborele de lucru,
skill-ul rulează codul vechi.

⚠️ `deploy.sh` / `deploy_parity.py` **NU acoperă** cele două fișiere: `SCAN_DIRS` conține doar
`plugins/gigi/skills/metrics-cache/scripts`, `shared/scripturi-tools` și `plugins/core/scripts`.
`cs_auto_draft.py` și `cs_photo.py` stau în `plugins/gigi/skills/…`, deci nicio gardă de paritate
nu le-a văzut driftând. De asta pasul 4 e explicit și se verifică cu poarta.

### Pașii, în ordine (îi execută OWNERUL; niciun agent nu comite, nu face push, nu pornește cronul)

```bash
cd ~/Downloads/Scripturi/team-intelligence
SK=plugins/gigi/skills

# ── 0. POARTA pe arborele de lucru. Fără VERDE nu pleacă nimic mai departe.
uv run $SK/cs-draft-reply/poarta_pornire.py

# ── 1. commit, pe o ramură (NU pe main). Un singur commit logic pt motorul CS.
git checkout -b fix/cs-runda-4            # dacă nu ești deja pe o ramură de lucru
git add $SK/cs-draft-reply/ $SK/cs-photo/
git status --short                        # verifică: NIMIC din afara celor două skill-uri
git commit -m "fix(cs): rundele 1-4 — gărzi PII publice, registru incidente, prefixe, poartă cap-la-cap"

# ── 2. push + PR + merge (fluxul obișnuit de skill-uri)
#    gigi:publish-skill   → înregistrează în KB, deschide PR, merge, sincronizează
#    (sau manual: git push -u origin fix/cs-runda-4 && gh pr create && gh pr merge --squash)

# ── 3. clona plugin-ului ia commit-ul (asta e copia pe care o rulează skill-ul)
git -C ~/.claude/plugins/marketplaces/team-intelligence pull --ff-only
uv run $SK/cs-draft-reply/poarta_pornire.py      # secțiunea E: „plugin == certificat" trebuie ✔

# ── 4. POARTA PE COPIA PLUGIN-ULUI (nu doar comparație de md5 — chiar rulează pe ea)
uv run $SK/cs-draft-reply/poarta_pornire.py \
    --root ~/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills

# ── 5. VPS — DOAR după VERDE la pașii 0 și 4. Cele două fișiere merg în ACELAȘI folder
#       (`import cs_photo` le cere vecine). Ia-le din clona plugin-ului, nu din arborele de lucru:
#       aia e versiunea care a trecut poarta.
P=~/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills
scp $P/cs-draft-reply/cs_auto_draft.py   root@84.46.242.181:/root/Scripturi/
scp $P/cs-photo/cs_photo.py              root@84.46.242.181:/root/Scripturi/
scp $P/cs-draft-reply/poarta_pornire.py  root@84.46.242.181:/root/Scripturi/

# ── 6. VERIFICAREA PARITĂȚII (citește doar; nu scrie nimic pe VPS)
uv run $SK/cs-draft-reply/poarta_pornire.py --vps root@84.46.242.181
#    cele trei md5-uri (certificat / plugin / VPS) trebuie să fie IDENTICE pe ambele fișiere

# ── 7. POARTA PE VPS, pe copia desfășurată (rulată ACOLO, nu de la distanță)
ssh root@84.46.242.181 'cd /root/Scripturi && uv run poarta_pornire.py --root /root/Scripturi/skills'
#    Pe VPS fișierele sunt FLAT, nu în arbore de skill-uri. Dacă nu vrei un arbore acolo,
#    fă-l o dată, simbolic, ca poarta să aibă ce certifica:
#      mkdir -p /root/Scripturi/skills/cs-draft-reply /root/Scripturi/skills/cs-photo
#      ln -sf /root/Scripturi/cs_auto_draft.py /root/Scripturi/skills/cs-draft-reply/
#      ln -sf /root/Scripturi/cs_photo.py      /root/Scripturi/skills/cs-photo/

# ── 8. abia ACUM se discută cronul — și e o decizie de OWNER, nu un pas tehnic. Vezi §3.1.
```

> Pe VPS cheile vin din `/root/Scripturi/.env` (root-600), nu din KB — cronul n-are mediul KB.
> Poarta citește oglinda CS (`data/cs_mirror.db`) și suitele (`~/.arona/cs-suite`); pe VPS ele
> lipsesc, deci secțiunile A/B/C1/C3 vor ieși „NEMĂSURAT". Pe VPS poarta certifică **gărzile
> (D) + paritatea (E) + corpusul de termen (C2)**; măsurătorile pe date reale rămân pe mașina care
> are oglinda. Asta e motivul pentru care pasul 4 (poarta pe copia plugin-ului) NU e opțional.

### 3.1 Cronul: ce se decomentează, CÂND, și de ce nu înainte

`/root/Scripturi/cs_backlog.sh` (versionat în `shared/scripturi-tools/cs_backlog.sh`) buclează pe
**toate cele 7 canale, inclusiv `facebook_feed_comment` și `instagram_comment`**, cu `--auto-hide`.
Linia lui din crontab e comentată (`# PAUZAT 2026-06-29`), deci azi **nu rulează nimic**.

Ce se întâmplă la decomentare, în funcție de codul desfășurat:

| cod pe VPS | ce face scriptul pe canalul public |
|---|---|
| **azi** (pre-runda-1, 1352 linii) | **draftează PUBLIC** + ascunde comentarii |
| **după desfășurare** (runda 4) | `draftam_public(comments=False, no_comments=False, auto_hide=True)` = **„doar-moderare"**: ascunde spamul, **fără draft public** |

Adică **desfășurarea ESTE remedierea** — `cs_backlog.sh` nu trebuie editat ca să nu mai drafteze
public. Dar ordinea contează, și e singurul lucru care contează:

> **Decomentarea liniei de cron vine DUPĂ desfășurare ȘI DUPĂ o poartă VERDE rulată pe copia de
> pe VPS.** Invers, o singură tastă pornește drafturi publice cu cod de dinainte de runda 1.

Ca ordinea asta să nu depindă de memoria cuiva, în repo există
**`cs-draft-reply/desfasurare/cs_backlog_garda.sh`** — o gardă care rulează poarta pe copia
desfășurată și **refuză să pornească backlogul dacă iese ROȘU** (marcaj cu amprentă md5, deci se
re-rulează singură când codul se schimbă). E **scrisă, NU instalată**: instrucțiunile de instalare
sunt în capul fișierului și sunt un pas de OWNER, după desfășurare.

## 4. Cum rulezi poarta

```bash
uv run poarta_pornire.py                          # certifică arborele din repo
uv run poarta_pornire.py --vps root@84.46.242.181 # + compară copia cronului (doar citește)
uv run poarta_pornire.py --root /alt/arbore       # certifică o copie patch-uită
uv run poarta_pornire.py --json                   # ultima linie: @@JSON@@{…} pentru automatizare
uv run poarta_pornire.py --fara-suite             # rapid, dar iese ROȘU: nu certifică nimic
```

Secțiuni: **A** prerechizite · **B** suitele rundelor 1-3 · **C** indicatorii măsurați pe date
reale · **D** gărzile de siguranță · **E** desfășurarea.

### Indicatorii și pragurile (fiecare cu povestea lui)

| Indicator | Sursa datelor | Prag | Măsurat 15-sep (runda 4) |
|---|---|---|---|
| **B. fals-pozitiv față de POLITICA NOASTRĂ** | `data/cs_mirror.db` — 137 răspunsuri REALE de agent | ≤ 3% | **0,7%** (1 din 137) |
| **A. divergență față de ce a scris AGENTUL** | aceleași 137 | ≤ 25% (derivă) | **19,7%** (27 din 137) |
| promisiuni de termen VERBATIM de pe piață | `corpus/termen_definitie.json` | prinse TOATE | **8/8** (bg, cz, hu, pl, sk, hr) |
| promisiuni în ÎNCADRAREA NOASTRĂ | același corpus, ramă scrisă de noi | scăpările să nu crească | **0/1** (1 scăpare, înghețată) |
| scurgeri de date pe canal public | 4 drafturi OSTILE, cap-la-cap prin `main()` | 0 clase rămase | **3 cazuri din 4 scurg** `email`, `adresă`, `nume` |
| acoperire prefixe de comandă | `ORDER_PFX` × `STORE_URL`/`STORE_PHONE` | toate | **24/24** (atâtea intrări are `ORDER_PFX`) |

#### A vs B: de ce două populații și nu una

Vechiul indicator („fals-pozitiv anti-halucinare pe română") presupunea că **răspunsul agentului =
adevăr**, deci orice prindere e o greșeală a gărzii. Nu e: **agentul avea datele** (deschisese
comanda), **un draft nu le are**. Când garda prinde „*Va ajunge în 2-3 zile lucrătoare*" scris de un
agent, are **dreptate pentru un draft** — SYSTEM îi interzice explicit unui draft să promită termene
fără date în context.

De asta indicatorul s-a „înrăutățit" de la 16,1% la 19,7%: cele 5 răspunsuri prinse în plus sunt
exact promisiuni de termen, adică **adevărate-pozitive**. Fără despărțire, o îmbunătățire a gărzii
se citește ca o regresie.

- **A = divergență față de agent** (19,7%): informativ. Prag larg (25%), ca alarmă de derivă — tot
  el prinde regresia rundei 1 (27,7%).
- **B = fals-pozitiv față de politica NOASTRĂ** (0,7%): **defectul real**. Prinderea e fals-pozitivă
  doar dacă textul prins e ceva ce un **draft conform** ar avea voie să scrie: un termen scris ca
  politică generală cu cifre-constante, un telefon care e al NOSTRU, un preț/dimensiune care **se
  află în contextul dat**. Singurul caz de azi: „*Livrareas e face in 2-3 zile lucratoare*" — o
  frază de politică generală pe care garda o ratează din cauza unei greșeli de tastare.

Clasificatorul care face despărțirea e **în poartă** (`clasa_hit`, `fals_pozitiv_de_politica`), scris
independent de regexurile motorului.

#### Termenul: definiția, nu intuiția

„5/7" din runda 3 nu era o gardă stricată, ci **două chei cu definiții diferite** ale cuvântului
„promisiune". Definiția e acum scrisă explicit, iar fiecare frază din corpus e etichetată după ea
(`corpus/termen_definitie.json`, cu motivul la fiecare intrare):

- **POLITICĂ GENERALĂ** (scuzată) — fraza e impersonală (fără „coletul dumneavoastră", fără „mâine",
  fără verb la persoana I care angajează o acțiune pentru ACEST client) **ȘI** toate cifrele sunt
  constante ale politicii noastre (1/2/3 zile livrare, 14/30 zile retur).
  Exemple verbatim: `ro` „*Livrarea se va face în 2-3 zile…*" · `cz` „*obvykle do 1–2 pracovních
  dnů*" · `pl` „*Zamówienie jest zwykle wysyłane w ciągu 1–2 dni roboczych*" · `hu` „*jellemzően
  1–2 munkanap alatt*" · `sk` „*najneskôr do 30 dní odo dňa uplatnenia reklamácie*" · `hr` „*u roku
  14 dana od dana kada ste nam izjavili jednostrani raskid*".
- **PROMISIUNE DESPRE COLETUL ACESTUI CLIENT** (prinsă) — ori cade impersonalitatea, ori cifra nu e
  o constantă.
  Exemple verbatim: `sk` „*Vám zásielka nebola doručená do 2 pracovných dní*" · `cz` „*Náš obchodník
  se vám do 3 dnů ozve*" · `bg` „*между 3 и 5 работни дни*" · `pl` „*zazwyczaj 2–5 dni roboczych*" ·
  `hu` „*átlagosan 2-8 munkanapon belül*" · `hr` „*dostavljamo u roku 3-10 radnih dana*".

Cu definiția asta, cele două fraze „ratate" în runda 3 sunt **corect nescuzate ca promisiuni** —
sunt politică. Recall-ul real pe promisiuni **verbatim de piață: 8/8**.

**Două rate, raportate separat.** Corpusul are două provenințe: `verbatim` (propoziția exact cum e
publicată, cu URL) și `incadrare-noastra` (aceeași propoziție verbatim, pusă de NOI într-o ramă
personală — „*Coletul dumneavoastră pleacă azi, iar…*"). Ratele NU se amestecă: pe rama noastră
garda scapă (marcajul de politică din frază învinge posesivul), 0/1. E o scăpare reală, în direcție
NESIGURĂ, dar mică și înghețată: poarta pică doar dacă lista **crește**. Lecția rundelor 2 și 3 e
exact asta — pe fraze scrise de autor rata era 100%, pe fraze verbatim de piață 43-54%.

#### Scurgerile publice: de ce indicatorul vechi era circular

Vechea măsurătoare era `public_pii_leaks(redact_public_pii(text))`. Dar `redact_public_pii` scoate
**exact** ce găsește `public_pii_leaks` — deci rezultatul era **0 prin construcție**, pe orice cod,
inclusiv pe cod care nu redactează nimic. O poartă care se uită doar unde se uită garda **nu
verifică garda**.

Acum clasele se definesc **în poartă** (`clase_pii`: `telefon`, `nr_comanda`, `awb`, `email`,
`adresa`, `nume`), cu tipare proprii, fără să importe nimic din motor, iar măsurătoarea trece
**cap-la-cap prin `main()`** cu MCP și LLM înlocuite, pe **drafturi ostile construite anume** (un
model care repetă tot ce a scris clientul). Fiecare caz izolează alte clase — cazul „toate clasele"
declanșează garda de telefon/comandă și draftul se regenerează, deci **nu dovedește nimic** despre
e-mail/adresă/nume.

Măsurat azi: din 4 cazuri, **3 scurg** în draftul public FINAL clasele `email`, `adresa` și `nume`.
`nume` se numără doar când numele **nu** apare în comentariul public al clientului — adică atunci
când l-am luat din CRM-ul nostru și l-am scris sub o postare publică.

### Unde stau suitele și de ce nu-s în repo

Suitele rundelor 1-3 stau în **`~/.arona/cs-suite/`** (sau `CS_SUITE_DIR`), nu în git: cel puțin una
(`r2_test_scurgere-repo.py`) conține, ca exemple de „ce trebuie blocat", **date reale de client**
(email, telefon, număr de comandă) — exact ce apără. Repo-ul e public.

```bash
mkdir -p ~/.arona/cs-suite
cp <sursa>/test_*.py <sursa>/r2_test_*.py <sursa>/r3_test_*.py ~/.arona/cs-suite/
cp <sursa>/corpus_clasificatoare.py ~/.arona/cs-suite/                 # modul-soră al suitei de clasificatoare
cp plugins/core/scripts/pii_guard.py ~/.arona/cs-suite/               # modul-soră al suitei de scurgeri
```
Fără cele două module-soră, suitele respective pică **din alt motiv decât codul** — poarta le verifică
separat, în secțiunea A, ca să nu confunzi cauzele.

O suită lipsă = **ROȘU**: nu certificăm ce nu putem rula. Trei suite **nu pot fi redirecționate** spre alt
arbore (au ținta fixă în cod): `test_catalog-brand.py`, `r2_test_confidentialitate.py`, `r2_test_scurgere-repo.py`
— cu `--root <altundeva>` ele testează tot copia din repo, iar poarta o spune la „Observat". Tot așa, pe un
arbore de lucru temporar secțiunea **E** rămâne roșie prin construcție: o copie de test nu e desfășurată nicăieri.

Suitele nu au o convenție unică de apel
(unele iau `--root`, altele un director, altele o cale de fișier, altele nimic) — tabelul `SUITE`
din `poarta_pornire.py` o ține pe a fiecăreia; o suită nouă e descoperită automat dacă acceptă
`--root <dir cu skill-urile>`. **Convenția pentru suitele noi: `--root`.**

Corpusul de fraze reale (`corpus/termen_piata.json`) **e** în repo: e text public de pe paginile
de politici ale magazinelor noastre și de pe site-uri de curieri, cu URL-ul sursei la fiecare frază.
Oglinda CS (`data/cs_mirror.db`) rămâne în afara repo-ului — conține conversații de client.

## 5. Gărzile de siguranță (ce s-a schimbat în cod)

**Trimiterea LIVE.** `--apply-send` (în lot) și `--send` (per tichet) trimit răspunsul la client
prin `send_message` și **închid** tichetul — ireversibil. „Draft-only" era o **convenție**: ținea
doar cât timp nimeni nu scria flagul. Acum trimiterea cere un consimțământ explicit, **în afara
liniei de comandă**:

```bash
export CS_TRIMITERE_LIVE=DA                 # varianta de sesiune
echo DA > ~/.arona/cs_trimitere_live.ok     # varianta de fișier (CS_TRIMITERE_FILE îl mută)
```

Fără unul dintre ele, procesul se oprește cu cod `2` **înainte de orice apel de rețea** și spune de
ce. Funcționalitatea rămâne întreagă pentru ziua în care se decide trimiterea.

**Canalul public.** Comentariile Facebook/Instagram nu se mai draftează implicit; se cere `--comments`.
Cu `--auto-hide` dar fără `--comments`, comentariile intră **doar pentru moderare** (spamul tot se
ascunde), fără draft public. `--no-comments` rămâne acceptat (liniile de cron vechi nu crapă).
Raportul final spune câte comentarii au fost sărite și câte doar moderate — nimic tăcut.

## 6. Ce NU acoperă poarta

- **calitatea** răspunsurilor. Măsurat în iunie: din 40 de drafturi scrise, 0 acceptate de CS.
  Poarta verifică gărzile și scurgerile, nu dacă răspunsul e bun.
- **unealta MCP `cs_draft`**: cheamă CLI-ul cu `--conv`, flag care nu există în parser → tool-ul
  iese cu eroare de argumente. Poarta semnalează divergența (secțiunea D), dar nu o repară.
- **calitatea** răspunsurilor pe limbile străine dincolo de corpus: `corpus/termen_definitie.json`
  acoperă acum toate cele 7 limbi cu exemple din AMBELE categorii, dar 1-3 fraze pe limbă
  nu e o măsurătoare de acoperire, ci una de definiție.
- **garda de termen în rama personală**: 0/1 pe încadrarea noastră (scăpare cunoscută,
  înghețată). Poarta o raportează; repararea ei e o decizie separată, nu a rundei 4.
- ce se întâmplă pe VPS între două rulări de poartă: paritatea e o fotografie, nu o gardă
  continuă. `deploy_parity.py` NU acoperă `plugins/gigi/skills/**` (vezi §3).
