# Parfumuri expediate pe mai multe colete — cine le-a făcut și de ce NU e OH

> Verificare cerută de owner, 21-aug-2026. Răspuns scurt: **nu OH le-a făcut. Cronul vechi le-a făcut.**

## Ce a semnalat depozitul
LAB3840, LAB3841, LAB3842, EST244275, EST244276, EST244277, EST244284 — „prea multe AWB-uri".
De fapt nu erau AWB-uri multiple: **un singur AWB, dar cu 3 colete** (tracking compus,
`81348967964-81348967960023-81348967960034`). DPD taxează pe colet, deci 3 parfumuri = transport ×3.

## Cine le-a făcut — dovada
Toate apar în jurnalul cronului VECHI de pe VPS-ul Scripturi,
`/root/Scripturi/logs/xc_awb_events.jsonl`, cu tracking-ul compus:

```
2026-08-20T11:45:18 LAB3840   -> 3 colete
2026-08-20T11:49:00 EST244275 -> 3 colete
...
```

Inventarul COMPLET al AWB-urilor de parfum cu >1 colet făcute de cronul vechi: **19**, toate într-o
fereastră de 20 de minute pe 20-aug (11:45–12:04, ora VPS). După ora aia — niciunul.

| magazin | comenzi |
|---|---|
| Lab Noir | LAB3840, LAB3841, LAB3842 |
| Nubra | NUBRA13542 |
| George Talent | GT57289 (3), GT57290 (6), GT57291 (3), GT57292 (6) |
| Esteban | EST244275/276/277/284 (3), EST244285 (2), EST244286 (4), EST244280/281/289/291/292 (2) |

Cauza e cea deja documentată în [[parfum-colete-multiple-cron]]: cronul calcula coletele din
metafield-urile produsului (`custom.nr_cutii`) → 1 colet/bucată pe GT/LAB, `ceil(buc/5)` pe Esteban.

## De ce NU e OH — verificat pe cod ȘI pe efect
- **Cod (în containerul LIVE):** `services/cron_parity/parcels.py` are `SHOPS_FARA_COLETE_MULTIPLE`
  cu toate cele 4 magazine de parfum (`6f9e22-9d`, `ix5bxc-hr`, `31k0py-bi`, `bmuwvv-jy`) → cutia
  default e 0 ⇒ 1 colet/comandă.
- **Efect, pe date reale:** din **1.846 de comenzi** pe cele 4 magazine de la cutover încoace,
  **ZERO** au un AWB activ cu mai mult de un colet. Ultima comandă cu colete multiple a fost creată
  la 09:44 UTC pe 20-aug; tot ce a urmat are 1 colet.

## Reparat
Cele 10 GT/EST din prima tranșă fuseseră refăcute ieri. Restul de **9** — LAB3840/41/42,
NUBRA13542, EST244275/276/277/284/285 — refăcute azi cu 1 colet
(`tools/fix_parcels.py`, aceeași procedură cu TAG ca la surpriză). Verificat: 9 AWB-uri noi active,
9 vechi anulate, niciun colet preluat de curier între timp, iar în coada de print a depozitului
apare exact eticheta nouă.

### Etichetele trimise depozitului
Cele 9 etichete noi (1 colet fiecare) au fost descărcate și trimise pe grupul WhatsApp „AWB" într-un
singur PDF combinat: `AWB-9-comenzi-1colet-21.08.pdf` (9 pagini, 738 KB), fără text în mesaj.

⚠️ **Construiește lista de AWB-uri din starea LIVE, nu din rezultatul rulării tale.** Între reparație
și trimitere, LAB3842 primise deja ALTĂ etichetă (a mea, `81349691634`, anulată la 10:03; alta nouă,
`81349705694`, la 10:19) — un PDF construit pe numerele memorate ar fi trimis depozitului o etichetă
moartă. Pentru comenzile deja descărcate din coadă, `print_queue.py pull --all` le readuce (include
și etichetele marcate `downloaded`).

## ⚠️ Ce am găsit pe drum și NU e închis în cod
`xc_fulfill.sh` are garda `OH_CANARY` și sare corect toate cele 23 de magazine (verificat live:
`AWB=0 · rulate=0/23`). **Dar `xc_backlog_parallel.sh` și `xc_cod_paid.sh` NU au nicio gardă** —
`grep OH_CANARY` întoarce 0 în amândouă. Backlog-ul a rulat la 03:00, 04:00 și 05:00 **azi** și a
făcut 19+2+1 AWB-uri, dintre care **8 pe Esteban și GT** (EST244239/244251/244293/244342/244487,
GT57251/GT57366) — ultimul la 04:02.

Norocul: alea au ieșit cu **1 colet fiecare și fără duplicat** (verificat în Shopify, un singur
fulfillment cu un singur tracking). Liniile de cron ale amândurora au fost comentate azi la **07:02**
(`/var/spool/cron/crontabs/root`, mtime 07:02), deci gaura e închisă *acum*.

**Singura protecție e comentariul din crontab.** O decomentare sau o rulare manuală reface exact
problema, pe toate cele 23 de magazine, cu regula veche de colete. Recomandare: pune garda
`OH_CANARY` (sau un refuz explicit) ÎN scripturi, nu doar în crontab. Aceeași lecție ca
[[oh-cutover-incomplet-cron]]: la cutover nu e destul să oprești jobul — trebuie ca ACȚIUNEA să
devină imposibilă.

## Legat
`2026-08-21-surpriza-recuperare.md` (aceeași procedură de refacere a etichetei),
memoriile [[parfum-colete-multiple-cron]], [[oh-cutover-incomplet-cron]], [[oh-awb-cutover-full-fleet]].
