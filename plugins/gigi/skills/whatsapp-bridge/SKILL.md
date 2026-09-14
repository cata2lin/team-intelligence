---
name: gigi:whatsapp-bridge
description: "Puntea WhatsApp a firmei (Baileys, pe VPS) — trimite mesaje și PDF-uri pe grupuri, ascultă un grup, intră pe un grup nou fără repornire. O SINGURĂ ușă de intrare: `wa.sh`. Folosește pentru: „trimite pe WhatsApp", „pune AWB-urile pe grupul AWB", „conectează-te pe grupul X", „citește ce s-a scris pe grup", „răspunde pe grup". ⚠️ NU deschide niciodată socket propriu — vezi regula sesiunii unice."
category: fulfillment-logistics
version: 2.0.0
---

# Puntea WhatsApp

`/root/wa-bridge` pe VPS, device linked pe numărul firmei. Contul e în ~298 de grupuri.

## ⛔ REGULA CARE EXPLICĂ TOT RESTUL: o singură sesiune
WhatsApp multi-device permite **un singur socket pe set de credențiale**. Al doilea proces care
deschide sesiune **o dă afară pe prima** (`CLOSED sc=440`), iar trimiterile în curs cad cu
„Connection Closed".

S-a întâmplat în producție (4-sep-2026): patru bucle de trimis PDF-uri pornite simultan, plus
listener-ul, însemnau că **nu pleca nimic** — și fiecare buclă raporta doar „Connection Closed",
nu „mă bat cu altcineva pe sesiune".

**Deci: nu scrie script propriu care deschide socket. Totul trece prin `wa.sh`**, care verifică
întâi dacă există deja o sesiune și se așază la coadă în loc să concureze.

## ⛔ NU TESTA PE GRUPURI REALE
Ținta de test e **`40746661159`** — contul punții e chiar contul ownerului, deci ăla e chat-ul
„Message yourself" și nu-l vede nimeni. Pe grupuri reale: doar acțiuni cerute, niciodată probe.
Un grup de lucru nu e banc de test — un „test 2" șters după 5 secunde tot a fost citit de 6 oameni.
Vezi [[no-testing-on-real-chats]].

## Comenzi
```bash
cd /root/wa-bridge
./wa.sh status                      # e vie o sesiune? pe ce grupuri ascultă?
./wa.sh find "customer service"     # caută grupuri după nume (din catalog, fără socket nou)
./wa.sh join "Apple Computers"      # intră pe un grup — FĂRĂ repornire, sesiunea rămâne
./wa.sh leave "Apple Computers"
./wa.sh listen 60                   # pornește ascultarea (0 = fără termen)
./wa.sh stop
./wa.sh say "Operational + CS" "mesaj"
./wa.sh doc "AWB" /cale/eticheta.pdf "caption"
./wa.sh read 30                     # ultimele mesaje, fiecare cu #numar

# pe un mesaj anume (numarul din `read`)
./wa.sh react 389 👍                # emoji gol = scoate reactia
./wa.sh edit  389 "text nou"        # DOAR mesajele noastre, ~15 min
./wa.sh del   389                   # pt toata lumea; ale altora doar daca botul e admin
./wa.sh reply 389 "..."             # raspuns CITAND mesajul
./wa.sh fwd   389 "Apple Computers" # trimite mai departe
./wa.sh get   389 [folder]          # descarca media (poza/video/voice/document)
./wa.sh seen  389                   # marcheaza citit

./wa.sh poll "AWB" "Cine preia?" "eu" "tu" "nimeni"
./wa.sh typing "AWB" on|off         # indicatorul „scrie..."
./wa.sh members "AWB"

# ⚠️ admin pe grup — DRY-RUN implicit, cer --apply
./wa.sh add|remove|promote|demote <grup> <numar> --apply
./wa.sh subject <grup> "nume nou" --apply
```

**Conversațiile private merg identic cu grupurile** — dă un număr în loc de nume:
`./wa.sh say 0746661159 "salut"`, `./wa.sh join 0746661159`. Numărul se normalizează singur
(`0746…` → `40746…@s.whatsapp.net`).

**Fișierele pleacă în formatul lor** — tipul se ia din extensie. Înainte orice fișier era marcat
`application/pdf`, deci o poză ajungea în chat ca document ilizibil.
Numele se rezolvă din **etichetele tale** (`groups.json`) ȘI din catalogul real
(`groups_cache.json`, scris de listener la conectare). Potrivire pe substring, fără diacritice;
dacă e ambiguu îți listează variantele în loc să ghicească.

## Cum decide `wa_send.js` pe unde trimite
```
listener viu?  ──DA──►  pune treaba în multi_out/ ; listener-ul o trimite ; aștept ack (max 180s)
      │
      NU
      ▼
ia lock-ul .send.lock (așteaptă până la 4 min dacă altcineva trimite) ──► deschide socket ──► trimite
```
Așa, **oricâți agenți** cer trimiteri simultan, tot o singură sesiune există la un moment dat.

## Garanții (fiecare a fost un bug real)
| garanție | ce era înainte |
|---|---|
| Fișierul din outbox se șterge **doar după** ce mesajul a plecat | se ștergea ÎNAINTE ⇒ un eșec pierdea mesajul definitiv |
| Se reîncearcă (20 de ture în pompă, 3 în trimiterea directă) | ieșea la prima eroare cu `exit(2)` |
| Eșecul definitiv **mută** fișierul în `multi_out/esuate/` | dispărea fără urmă |
| Trimite către **orice** jid | arunca cu „SKIP (jid necunoscut)" orice grup neascultat |
| Un PDF nu se retrimite (`sent_docs.log`, cheia `jid\|nume\|mărime`; `--force` trece peste) | se putea dubla eticheta la depozit |
| Grupurile ascultate se schimbă **la cald**, din `groups.json`, recitit la 5s | trebuia repornit procesul, deci se rupea sesiunea |

⚠️ Contractul de ieșire e neschimbat — `SENT doc -> <jid>` — fiindcă `awb_zilnic.py` și
`awb_station.py` caută exact șirul ăsta ca să marcheze o comandă ca trimisă. **Output gol ≠ trimis.**

## Capcane măsurate
1. **`pkill -f multi_bot.js` prin ssh își omoară propriul shell** (comanda remote conține șirul).
   Folosește `wa.sh stop`, care merge pe PID.
2. **`kill -9` nu lasă procesul să-și șteargă pid-ul.** De aceea `viu()` verifică și
   `/proc/<pid>/cmdline`, nu doar că numărul există — altfel `listen` refuză să pornească
   invocând un proces pe care tocmai l-ai omorât.
3. **O comandă RESPINSĂ în Claude Code poate să fi pornit deja procesul pe VPS.** Verifică `ps`.
4. **`shouldIgnoreJid` e ce ține telefonul liniștit.** Fără el: 88 de scrieri/min în `auth/`,
   notificări „syncing" în buclă pe telefonul ownerului. Cu el: 0 scrieri/45s.
5. Mesajele proprii ale ownerului vin `fromMe` — se sar doar ID-urile pe care le-am trimis noi,
   altfel nu vezi deloc ce scrie el de pe telefon.

## Reguli de conținut (owner)
- nu da mesaje pe grup până nu ești întrebat
- nu răspunde la lucruri confidențiale sau personale
- nu povesti în grup cum lucrăm — vezi [[wa-group-no-confidential-replies]]
- tonul casei: un rând, fără diacritice, tough love · vezi [[arona-voice-and-tone]]

## Ce trebuie salvat ca să poți ținti un mesaj
| ce salvez | pentru ce ajunge |
|---|---|
| **cheia** (`remoteJid`+`id`+`participant`) în `multi_in.jsonl` | react · edit · delete · seen |
| **mesajul întreg** în `multi_raw/<n>.json` | reply-citat · forward · descărcare media |

Fiecare mesaj primește un **#număr** = rândul lui din `multi_in.jsonl`, inclusiv cele trimise de bot
(altfel nu-mi pot cita sau șterge propriile mesaje). Mesajele de dinainte de upgrade n-au cheie și
apar cu `#?` — nu pot fi țintite.

⚠️ Tipurile de mesaj necunoscute **își spun numele** (`[sondaj]`, `[document]`, `[locatie]`…).
Înainte ajungeau text gol, adică invizibile: vedeai o linie fără conținut și răspundeai pe lângă.

## Permanență
`systemd: wa-listener.service`, `Restart=always`, pornit cu termen **0** (fără expirare).
Grupurile se schimbă la cald din `groups.json`, deci **nu reporni ca să intri pe un grup nou** —
repornirea rupe sesiunea, exact ce vrei să eviți.

⚠️ Bugetul de reconectări era pe TOATĂ VIAȚA procesului (5) și nu se reseta niciodată: la a cincea
reconectare — fie ea peste trei zile — listener-ul se oprea definitiv, tăcut. Acum se resetează la
fiecare conectare reușită.

Fișiere: `wa.sh`, `multi_bot.js` (listener + pompă), `wa_send.js` (trimitere), `wa-listener.service`,
`wa_find.js` (căutare când nu există catalog), `wa_dm.js` (mesaj individual).
